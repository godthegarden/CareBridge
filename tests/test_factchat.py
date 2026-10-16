import json
from copy import deepcopy
from unittest.mock import Mock

import pytest
import requests
from carebridge import factchat as fc


HEALTH = {'examinee_name': '가상성명', 'document_title': '비공개 문서', 'health_examination_date': '2026-01-01',
          'examinee_sex': '남성', 'health_checkup_results': [
              {'test_item_name': 'BMI(kg/m²)', 'result_value': '26.8', 'reference_range': '과체중(25-29.9)',
               'source_excerpt': '비공개 원문 900101-1234567 가상성명'},
              {'test_item_name': '공복혈당(mg/dL)', 'result_value': '108', 'reference_range': '100미만'}]}
MEDS = {'patient_name': '가상성명', 'record_document_title': '비공개 문서', 'medication_instructions': [
    {'medication_name': '가상 약품', 'single_dose': '1정', 'times_per_day': '3', 'days_of_administration': '2',
     'administration_timing': '식후', 'caution_notes': '문서에 기재된 주의', 'source_excerpt': '비공개 원문 가상성명'}]}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('FACTCHAT_API_KEY', 'test-only-secret-never-log')
    monkeypatch.setenv('FACTCHAT_MODEL', 'solar-mini4')
    monkeypatch.setenv('FACTCHAT_MAX_REQUESTS', '1')
    monkeypatch.setattr(fc, '_requests', 0)
    monkeypatch.setattr(fc, '_last_request', None)
    monkeypatch.setattr(fc, 'load_dotenv', lambda *a, **kw: None)
    def block(*args, **kwargs):
        raise AssertionError('No real HTTP request permitted in these tests')
    monkeypatch.setattr(requests.sessions.Session, 'request', block)


def plan(payload):
    facts = payload['facts']
    eligible = [f['id'] for f in facts if f['quiz_allowed']]
    return {'summary': [f['id'] for f in facts[:3]],
            'attention': [f['id'] for f in facts if f['attention']][:2],
            'quiz': eligible[:3] if len(eligible) >= 2 else []}


def response(payload, **kwargs):
    raw = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(plan(payload))}}],
           'usage': {'prompt_tokens': 500, 'completion_tokens': 80, 'total_tokens': 580},
           'billing': {'deducted_credit': 0.0123}}
    raw.update(kwargs)
    return Mock(status_code=200, json=Mock(return_value=raw))


@pytest.mark.parametrize('data', [HEALTH, MEDS])
def test_minimal_payload_preserves_input_and_excludes_identifiers(data):
    original = deepcopy(data)
    payload = fc.build_facts(data)
    serialized = json.dumps(payload, ensure_ascii=False)
    for private in ['가상성명', '비공개 문서', '비공개 원문', '2026-01-01', '900101-1234567', 'source_excerpt', 'examinee_sex']:
        assert private not in serialized
    assert data == original
    assert 2 <= len(fc.validate_plan(plan(payload), payload)['quiz']) <= 3


def test_redaction_of_misplaced_personal_details():
    data = deepcopy(MEDS)
    data['medication_instructions'][0]['caution_notes'] = '가상성명 900101-1234567 010-1234-5678 person@example.com'
    text = json.dumps(fc.build_facts(data), ensure_ascii=False)
    for private in ['가상성명', '900101', '010-1234', 'person@example.com']:
        assert private not in text
    data['medication_instructions'][0]['caution_notes'] = '주소: 비공개 장소'
    assert '비공개 장소' not in json.dumps(fc.build_facts(data), ensure_ascii=False)


def test_unknown_frequency_is_never_interpreted_as_daily():
    facts = fc.build_facts(MEDS)['facts']
    frequency = next(f for f in facts if f['field'] == 'frequency')
    assert frequency['value'] == '3'
    assert '원문에 적힌 횟수: 3' in fc.explanation(frequency)
    assert '하루 기준인지는' in fc.explanation(frequency)
    assert '하루 3회' not in fc.explanation(frequency)


def test_uncertain_and_missing_evidence_not_used_for_questions():
    payload = fc.build_facts({'health_checkup_results': [{'test_item_name': '검사', 'result_value': 'OCR 불확실'}]})
    assert not payload['facts'][0]['quiz_allowed']
    assert fc.validate_plan({'summary': ['0:result'], 'attention': [], 'quiz': []}, payload)
    with pytest.raises(fc.ExplanationError):
        fc.validate_plan({'summary': ['0:result'], 'attention': [], 'quiz': ['0:result']}, payload)


@pytest.mark.parametrize('bad', [
    {'summary': ['made-up'], 'attention': [], 'quiz': []},
    {'summary': [], 'attention': [], 'quiz': []},
    {'summary': ['0:result']*2, 'attention': [], 'quiz': []},
    {'summary': ['0:result'], 'attention': [], 'quiz': [], 'diagnosis': '발명한 진단'},
    {'summary': ['0:result'], 'attention': [], 'quiz': ['0:result']},
    {'summary': ['0:result'], 'attention': [], 'quiz': ['0:result', '0:unit']},
    {'summary': '0:result', 'attention': [], 'quiz': []},
])
def test_unverified_medical_text_or_invalid_evidence_rejected(bad):
    with pytest.raises(fc.ExplanationError):
        fc.validate_plan(bad, fc.build_facts(HEALTH))


def test_endpoint_usage_logging_and_no_secret_or_medical_content(caplog):
    payload = fc.build_facts(HEALTH)
    transport = Mock()
    transport.post.return_value = response(payload)
    result = fc.request_plan(payload, consent=True, session=transport)
    args, kwargs = transport.post.call_args
    assert args == (fc.BASE+'/chat/completions/',)
    assert kwargs['allow_redirects'] is False
    assert kwargs['timeout'] == (5, 35)
    assert kwargs['json']['model'] == 'solar-mini4'
    assert kwargs['json']['stream'] is False
    assert result['usage']['deducted_credit'] == 0.0123
    assert 'deducted_credit=0.0123' in caplog.text
    for private in ['test-only-secret', 'BMI', '26.8', '가상성명', '비공개']:
        assert private not in caplog.text
    assert transport.post.call_count == 1


def test_missing_billing_is_unknown_not_zero():
    payload = fc.build_facts(MEDS)
    transport = Mock()
    transport.post.return_value = response(payload, billing=None, usage=None)
    result = fc.request_plan(payload, consent=True, session=transport)
    assert all(v is None for v in result['usage'].values())


@pytest.mark.parametrize('status', [401, 402, 403, 429, 500, 302])
def test_http_failures_no_retry_and_no_response_body(status, caplog):
    transport = Mock()
    transport.post.return_value = Mock(status_code=status, text='PRIVATE BODY')
    with pytest.raises(fc.ExplanationError) as exc:
        fc.request_plan(fc.build_facts(HEALTH), consent=True, session=transport)
    assert 'PRIVATE' not in str(exc.value)+caplog.text
    assert transport.post.call_count == 1
    transport.post.return_value.json.assert_not_called()


@pytest.mark.parametrize('failure', [requests.Timeout('PRIVATE'), requests.ConnectionError('PRIVATE')])
def test_network_failure_does_not_retry_or_leak(failure, caplog):
    transport = Mock()
    transport.post.side_effect = failure
    with pytest.raises(fc.ExplanationError) as exc:
        fc.request_plan(fc.build_facts(HEALTH), consent=True, session=transport)
    assert transport.post.call_count == 1
    assert 'PRIVATE' not in str(exc.value)+caplog.text


def test_consent_and_configuration_gate_all_network(monkeypatch):
    transport = Mock()
    payload = fc.build_facts(HEALTH)
    with pytest.raises(fc.ExplanationError, match='동의'):
        fc.request_plan(payload, session=transport)
    monkeypatch.delenv('FACTCHAT_API_KEY')
    with pytest.raises(fc.ExplanationError, match='설정'):
        fc.request_plan(payload, consent=True, session=transport)
    transport.post.assert_not_called()
    assert fc._requests == 0


def test_server_budget_counts_failures_and_prevents_further_calls():
    payload = fc.build_facts(MEDS)
    transport = Mock()
    transport.post.side_effect = requests.Timeout()
    for _ in range(2):
        with pytest.raises(fc.ExplanationError):
            fc.request_plan(payload, consent=True, session=transport)
    assert transport.post.call_count == 1


def test_process_cooldown_and_disabled_limit(monkeypatch):
    payload = fc.build_facts(MEDS)
    transport = Mock()
    transport.post.return_value = response(payload)
    monkeypatch.setenv('FACTCHAT_MAX_REQUESTS', '3')
    fc.request_plan(payload, consent=True, session=transport)
    with pytest.raises(fc.ExplanationError, match='간격'):
        fc.request_plan(payload, consent=True, session=transport)
    monkeypatch.setenv('FACTCHAT_MAX_REQUESTS', '0')
    with pytest.raises(fc.ExplanationError, match='한도'):
        fc.request_plan(payload, consent=True, session=transport)
    assert transport.post.call_count == 1


@pytest.mark.parametrize('fails', [False, True])
def test_cache_including_failed_request_is_reused(monkeypatch, fails):
    called = Mock(side_effect=fc.ExplanationError('고정 오류') if fails else None)
    called.return_value = {'plan': plan(fc.build_facts(HEALTH)), 'usage': {}}
    monkeypatch.setattr(fc, 'request_plan', called)
    cache = {}
    first = fc.generate_once(cache, 'record-test', HEALTH, consent=True)
    second = fc.generate_once(cache, 'record-test', deepcopy(HEALTH), consent=False)
    assert first is second and called.call_count == 1
    assert first['state'] == ('error' if fails else 'complete')


def test_changed_facts_require_new_consent(monkeypatch):
    monkeypatch.setattr(fc, 'request_plan', lambda *a, **k: {})
    cache = {}
    fc.generate_once(cache, 'test', HEALTH, consent=True)
    changed = deepcopy(HEALTH)
    changed['health_checkup_results'][0]['result_value'] = '20'
    with pytest.raises(fc.ExplanationError, match='동의'):
        fc.generate_once(cache, 'test', changed)


@pytest.mark.parametrize('finish', ['length', 'content_filter', None])
def test_incomplete_response_rejected(finish):
    payload = fc.build_facts(HEALTH)
    transport = Mock()
    transport.post.return_value = response(payload, choices=[{'finish_reason': finish, 'message': {'content': '{}'}}])
    with pytest.raises(fc.ExplanationError, match='완료'):
        fc.request_plan(payload, consent=True, session=transport)


def test_admin_without_key_makes_no_call(monkeypatch, capsys):
    from carebridge import factchat_admin as admin
    monkeypatch.delenv('FACTCHAT_API_KEY')
    monkeypatch.setattr(admin, 'load_dotenv', lambda *a: None)
    monkeypatch.setattr('sys.argv', ['factchat_admin', 'models'])
    assert admin.main() == 1
    assert '요청하지 않았습니다' in capsys.readouterr().err


@pytest.mark.parametrize('data,page', [(HEALTH, '건강검진'), (MEDS, '복약 관리')])
def test_both_document_screens_consent_cache_quiz_and_navigation(monkeypatch, data, page):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from carebridge.domain import normalize
    from carebridge.records import record
    payload = fc.build_facts(data)
    fake = Mock(return_value={'plan': plan(payload), 'usage': {'total_tokens': 1}, 'model': 'solar-mini4'})
    monkeypatch.setattr(fc, 'request_plan', fake)
    kind, clean = normalize(data)
    item = record(kind, clean, 'synthetic.pdf', 'test', kind)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'), default_timeout=15).run()
    app.session_state['records'] = [item]
    app.sidebar.radio[0].set_value(page).run()
    fake.assert_not_called()
    next(b for b in app.button if b.label == '쉬운 설명 보기').click().run()
    generate = next(b for b in app.button if b.label == '동의한 정보로 설명 생성')
    assert generate.disabled
    fake.assert_not_called()
    next(c for c in app.checkbox if 'Gateway 전송에 동의' in c.label).check().run()
    fake.assert_not_called()
    next(b for b in app.button if b.label == '동의한 정보로 설명 생성').click().run()
    assert not app.exception and fake.call_count == 1
    assert len([b for b in app.button if b.label == '정답과 근거 확인']) in (2, 3)
    headings = [m.value for m in app.markdown if m.value.startswith('### ')]
    assert headings[0] == '### 쉽게 읽는 문서 안내'
    assert headings[-1] == '### 문서 이해도 확인'
    answer = next(t for t in app.text_input if t.label == '내가 이해한 내용')
    answer.set_value('내가 확인한 답').run()
    next(b for b in app.button if b.label == '정답과 근거 확인').click().run()
    assert any(t.value.startswith('문서에 적힌 답:') for t in app.text)
    app.sidebar.radio[0].set_value('대시보드').run()
    app.sidebar.radio[0].set_value(page).run()
    assert not app.exception and fake.call_count == 1
    assert next(t for t in app.text_input if t.label == '내가 이해한 내용').value == '내가 확인한 답'
    assert app.session_state['records'][0]['data'] == clean
    assert not app.session_state['schedules']


def test_ui_without_key_disables_only_optional_feature(monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from carebridge.domain import normalize
    from carebridge.records import record
    monkeypatch.delenv('FACTCHAT_API_KEY')
    fake = Mock()
    monkeypatch.setattr(fc, 'request_plan', fake)
    kind, clean = normalize(HEALTH)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'), default_timeout=15).run()
    app.session_state['records'] = [record(kind, clean, 'synthetic.pdf', 'test', kind)]
    app.sidebar.radio[0].set_value('건강검진').run()
    assert not app.exception
    assert next(b for b in app.button if b.label == '쉬운 설명 보기').disabled
    assert any('<table aria-label="건강검진 결과"' in m.value for m in app.markdown)
    fake.assert_not_called()


def test_ui_failure_keeps_rule_results_and_never_retries(monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from carebridge.domain import normalize
    from carebridge.records import record
    fake = Mock(side_effect=fc.ExplanationError('Gateway 크레딧이 부족합니다.'))
    monkeypatch.setattr(fc, 'request_plan', fake)
    kind, clean = normalize(MEDS)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'), default_timeout=15).run()
    app.session_state['records'] = [record(kind, clean, 'synthetic.pdf', 'test', kind)]
    app.sidebar.radio[0].set_value('복약 관리').run()
    next(b for b in app.button if b.label == '쉬운 설명 보기').click().run()
    next(c for c in app.checkbox if 'Gateway 전송에 동의' in c.label).check().run()
    next(b for b in app.button if b.label == '동의한 정보로 설명 생성').click().run()
    app.run()
    assert not app.exception and fake.call_count == 1
    assert any('크레딧' in w.value for w in app.warning)
    assert any('<table aria-label="복약안내문 약품 목록"' in m.value for m in app.markdown)
    app.toggle[0].set_value(True).run()
    assert any(t.label == '확인한 하루 복용 횟수' for t in app.text_input)
    assert fake.call_count == 1
