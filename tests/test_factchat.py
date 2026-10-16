import json
import re
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
    eligible = [f for f in facts if f['quiz_allowed']]
    rows = {}
    for fact in facts:
        rows.setdefault(fact['id'].split(':')[0], []).append(fact)
    medication = payload['kind'] == 'medication_guidance_record'
    details = []
    for row in rows.values():
        fields = {f['field']: f['value'] for f in row}
        name = row[0]['item']
        if medication:
            text = name+' 안내에는 '+', '.join(fc.LABELS[f['field']]+'이 '+f['value'] for f in row if f['origin']=='document')+'로 적혀 있습니다.'
            if 'check' in fields:
                text += ' '+fields['check']+'로 안내되어 있습니다.'
        else:
            text = name+'는 '+fields.get('meaning', '문서에서 확인하는 검사입니다.')
            if 'result' in fields: text += ' 측정값 '+fields['result']+'을 '
            if 'reference' in fields: text += '문서 참고기준 '+fields['reference']+'과 비교하면 '
            text += fields['comparison']+'이므로 문서 기준과 함께 확인할 필요가 있습니다.'
        details.append({'text': text, 'evidence': [f['id'] for f in row]})
    ids = [f['id'] for f in facts]
    summary = ([{'text': '문서의 약품별 복용 안내를 확인할 수 있습니다.', 'evidence': ids},
                {'text': '원문 횟수의 기준은 확인 필요로 적혀 있습니다.', 'evidence': ids}]
               if medication else
               [{'text': '이번 문서에서는 '+'와 '.join(row[0]['item'] for row in rows.values())+'의 기준을 눈여겨볼 필요가 있습니다.', 'evidence': ids},
                {'text': '문서 기준과의 비교 결과를 중심으로 확인하고 불명확한 정보는 확인 필요로 표시합니다.', 'evidence': ids}])
    return {'summary': summary, 'details': details,
            'quiz': [{'fact_id': f['id'], 'explanation': {'text': f['item']+'의 '+fc.LABELS[f['field']]+'은 '+f['value']+'로 문서에 적혀 있습니다.',
                      'evidence': [f['id']]}} for f in eligible[:3]] if len(eligible)>=2 else []}


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
    assert any(f['field']=='check' and '횟수의 기준 확인 필요' in f['value'] for f in facts)
    payload = fc.build_facts(MEDS)
    bad = plan(payload)
    bad['details'][0]['text'] += ' 하루 3회 복용합니다.'
    with pytest.raises(fc.ExplanationError, match='시점|횟수'):
        fc.validate_plan(bad, payload)


def test_uncertain_and_missing_evidence_not_used_for_questions():
    payload = fc.build_facts({'health_checkup_results': [{'test_item_name': '검사', 'result_value': 'OCR 불확실'}]})
    assert not payload['facts'][0]['quiz_allowed']
    generated = plan(payload)
    assert fc.validate_plan(generated, payload)['quiz'] == []
    generated['quiz'] = [{'fact_id': '0:result', 'explanation': {'text': '검사의 결과는 OCR 불확실로 표시되어 있습니다.', 'evidence': ['0:result']}}]
    with pytest.raises(fc.ExplanationError):
        fc.validate_plan(generated, payload)


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
    fake = Mock(return_value={'plan': plan(payload), 'usage': {'total_tokens': 1}, 'model': 'solar-mini4', 'source': 'factchat_generated'})
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
    assert len([r for r in app.radio if '무엇이라고 적혀 있나요' in r.label]) in (2, 3)
    headings = [m.value for m in app.markdown if m.value.startswith('### ')]
    assert headings[0] == '### AI가 풀어쓴 문서 설명'
    assert headings[-1] == '### 문서 이해도 확인'
    questions = [r for r in app.radio if '무엇이라고 적혀 있나요' in r.label]
    for radio, quiz in zip(questions, plan(payload)['quiz']):
        fact = next(f for f in payload['facts'] if f['id']==quiz['fact_id'])
        radio.set_value(fact['value'])
    app.run()
    next(b for b in app.button if b.label == '선택한 답 채점하기').click().run()
    assert any('문항 정답' in element.value for element in app.success)
    app.sidebar.radio[0].set_value('대시보드').run()
    app.sidebar.radio[0].set_value(page).run()
    assert not app.exception and fake.call_count == 1
    assert any('문항 정답' in element.value for element in app.success)
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


@pytest.mark.parametrize('data', [HEALTH, MEDS])
def test_real_response_text_is_preserved_not_replaced_by_templates(data):
    payload = fc.build_facts(data)
    generated = plan(payload)
    transport = Mock()
    transport.post.return_value = response(payload)
    result = fc.request_plan(payload, consent=True, session=transport)
    assert result['source'] == 'factchat_generated'
    assert result['plan'] == generated
    assert all(isinstance(block['text'], str) for block in result['plan']['summary'])
    assert len(result['plan']['summary']) in (2, 3)
    assert transport.post.call_args.kwargs['json']['max_tokens'] == 4500
    if data is HEALTH:
        prose = ' '.join(block['text'] for block in generated['details'])
        assert '키에 비해 몸무게' in prose and '혈액 속 포도당' in prose
        assert '100미만과 비교하면 문서 기준 초과' in prose


@pytest.mark.parametrize('extra', [
    ' 999mg/dL이므로 확인이 필요합니다.', ' 당뇨병입니다.', ' 운동을 추천합니다.', ' 정상입니다.'
])
def test_health_unsupported_number_diagnosis_or_advice_rejected(extra):
    payload = fc.build_facts(HEALTH)
    generated = plan(payload)
    generated['details'][0]['text'] += extra
    with pytest.raises(fc.ExplanationError, match='검증 실패'):
        fc.validate_plan(generated, payload)


@pytest.mark.parametrize('extra', [
    ' 이부프로펜도 먹습니다.', ' 아세트아미노펜이 들어 있습니다.', ' 어지럼증이 생깁니다.',
    ' 오전 9시 복용합니다.', ' 하루 3회 복용합니다.', ' 두 정 먹습니다.', ' 2정 먹습니다.'
])
def test_medication_unsupported_ingredient_adverse_effect_or_dosing_rejected(extra):
    payload = fc.build_facts(MEDS)
    generated = plan(payload)
    generated['details'][0]['text'] += extra
    with pytest.raises(fc.ExplanationError, match='검증 실패'):
        fc.validate_plan(generated, payload)


def test_missing_medication_timing_requires_explicit_check_and_is_not_quizzed():
    data = deepcopy(MEDS)
    data['medication_instructions'][0]['administration_timing'] = ''
    payload = fc.build_facts(data)
    generated = plan(payload)
    assert fc.validate_plan(generated, payload)
    assert not any(f['id']=='0:timing' for f in payload['facts'])
    assert '복용 시점 확인 필요' in generated['details'][0]['text']
    generated['details'][0]['text'] = generated['details'][0]['text'].replace('확인 필요', '안내')
    with pytest.raises(fc.ExplanationError, match='확인 필요'):
        fc.validate_plan(generated, payload)


def test_unknown_sex_comparison_cannot_be_invented():
    data = {'health_checkup_results': [{'test_item_name': '허리둘레(cm)', 'result_value': '91', 'reference_range': '남 90이상 여 85이상'}]}
    payload = fc.build_facts(data)
    generated = plan(payload)
    assert fc.validate_plan(generated, payload)
    assert next(f for f in payload['facts'] if f['field']=='comparison')['value']=='확인 필요'
    generated['details'][0]['text'] += ' 기준보다 높습니다.'
    with pytest.raises(fc.ExplanationError, match='임의로 비교'):
        fc.validate_plan(generated, payload)


def test_legacy_id_response_is_not_mislabeled_as_generated_prose(caplog):
    payload = fc.build_facts(HEALTH)
    transport = Mock()
    transport.post.return_value = response(payload, choices=[{'finish_reason': 'stop', 'message': {'content':
        json.dumps({'summary': ['0:result'], 'attention': [], 'quiz': ['0:result', '1:result']})}}])
    with pytest.raises(fc.ExplanationError, match='설명 형식'):
        fc.request_plan(payload, consent=True, session=transport)
    assert 'http=200' in caplog.text and 'state=rejected' in caplog.text
    assert 'state=content_validated' not in caplog.text


def test_quiz_correct_answers_and_options_are_not_llm_fabrications():
    from carebridge.explanation_content import quiz_options
    payload = fc.build_facts(MEDS)
    for quiz in plan(payload)['quiz']:
        fact = next(f for f in payload['facts'] if f['id']==quiz['fact_id'])
        options = quiz_options(fact)
        assert len(options)==3 and fact['value'] in options
        assert len(set(options)) == 3
        assert all(option==fact['value'] or not re.search(r'\d', option) for option in options)
    generated = plan(payload)
    generated['quiz'][0]['explanation']['text'] = '가상 약품의 한 번에 복용하는 양은 9정으로 적혀 있습니다.'
    with pytest.raises(fc.ExplanationError):
        fc.validate_plan(generated, payload)


def test_gateway_parse_to_ui_prose_and_mixed_multiple_choice_grading(monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from carebridge.domain import normalize
    from carebridge.records import record
    payload = fc.build_facts(HEALTH)
    generated = plan(payload)
    transport = Mock()
    transport.post.return_value = response(payload)
    monkeypatch.setattr(fc.requests, 'Session', lambda: transport)
    kind, clean = normalize(HEALTH)
    item = record(kind, clean, 'synthetic.pdf', 'test', kind)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'), default_timeout=15).run()
    app.session_state['records'] = [item]
    app.sidebar.radio[0].set_value('건강검진').run()
    next(b for b in app.button if b.label=='쉬운 설명 보기').click().run()
    next(c for c in app.checkbox if 'Gateway 전송에 동의' in c.label).check().run()
    next(b for b in app.button if b.label=='동의한 정보로 설명 생성').click().run()
    assert not app.exception and transport.post.call_count==1
    assert any(generated['details'][0]['text'] in m.value for m in app.markdown)
    assert any('FactChat 생성 설명' in c.value for c in app.caption)
    assert any('규칙 기반 표시' in c.value for c in app.caption)
    assert next(b for b in app.button if b.label=='선택한 답 채점하기').disabled
    questions = [r for r in app.radio if '무엇이라고 적혀 있나요' in r.label]
    for index, (radio, quiz) in enumerate(zip(questions, generated['quiz'])):
        fact = next(f for f in payload['facts'] if f['id']==quiz['fact_id'])
        radio.set_value(fact['value'] if index==0 else '이 항목은 문서에 기재되지 않았습니다.')
    app.run()
    next(b for b in app.button if b.label=='선택한 답 채점하기').click().run()
    assert any('3문항 중 1문항 정답' in s.value for s in app.success)
    assert any(generated['quiz'][0]['explanation']['text'] in m.value for m in app.markdown)
    first = next(r for r in app.radio if '무엇이라고 적혀 있나요' in r.label)
    first.set_value('이 항목은 문서에 기재되지 않았습니다.').run()
    assert not app.success  # Changing an answer invalidates the previous grade.
    assert transport.post.call_count==1


def test_http_success_but_invalid_content_shows_failure_not_ai_heading(monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from carebridge.domain import normalize
    from carebridge.records import record
    transport = Mock()
    transport.post.return_value = response(fc.build_facts(HEALTH), choices=[{'finish_reason': 'stop', 'message': {'content': '{}'}}])
    monkeypatch.setattr(fc.requests, 'Session', lambda: transport)
    kind, clean = normalize(HEALTH)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'), default_timeout=15).run()
    app.session_state['records'] = [record(kind, clean, 'synthetic.pdf', 'test', kind)]
    app.sidebar.radio[0].set_value('건강검진').run()
    next(b for b in app.button if b.label=='쉬운 설명 보기').click().run()
    next(c for c in app.checkbox if 'Gateway 전송에 동의' in c.label).check().run()
    next(b for b in app.button if b.label=='동의한 정보로 설명 생성').click().run()
    app.run()
    assert not app.exception and transport.post.call_count==1
    assert any('AI 설명 생성 실패' in w.value for w in app.warning)
    assert not any(m.value=='### AI가 풀어쓴 문서 설명' for m in app.markdown)
    assert any('<table aria-label="건강검진 결과"' in m.value for m in app.markdown)
    assert any('AI 생성 설명이 아닌' in c.value for c in app.caption)


def test_natural_medication_prose_is_accepted_with_original_values():
    payload = fc.build_facts(MEDS)
    generated = plan(payload)
    generated['details'][0]['text'] = ('가상 약품 안내에는 한 번에 먹는 양이 1정이고 횟수는 3, 기간은 2로 적혀 있습니다. '
        '복용 시점인 식후는 식사 뒤라는 뜻입니다. 주의사항에는 문서에 기재된 주의라고 적혀 있습니다. '
        '원문 횟수의 기준은 확인 필요합니다.')
    assert fc.validate_plan(generated, payload)['details'][0]['text'] == generated['details'][0]['text']


def test_fenced_json_response_still_displays_real_prose():
    payload = fc.build_facts(HEALTH)
    transport = Mock()
    transport.post.return_value = response(payload, choices=[{'finish_reason': 'stop', 'message': {'content':
        '```json\n'+json.dumps(plan(payload), ensure_ascii=False)+'\n```'}}])
    assert fc.request_plan(payload, consent=True, session=transport)['plan']==plan(payload)


def test_unknown_test_name_cannot_be_added_to_a_grounded_summary():
    payload = fc.build_facts(HEALTH)
    generated = plan(payload)
    generated['summary'][0]['text'] = '총콜레스테롤의 기준을 눈여겨볼 필요가 있습니다.'
    with pytest.raises(fc.ExplanationError, match='검사 용어'):
        fc.validate_plan(generated, payload)
