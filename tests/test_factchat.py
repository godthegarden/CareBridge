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
    monkeypatch.setenv('FACTCHAT_RESPONSE_FORMAT', 'json_schema')
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
    if payload.get('report_version'):
        from carebridge.health_report import LIMIT
        summary.append({'text': LIMIT, 'evidence': ids})
        bank = payload['understanding_questions']
        selected = []
        for theme in ('relationship', 'limits', 'meaning'):
            candidate = next((q for q in bank if q['theme']==theme), None)
            if candidate: selected.append({'question_id': candidate['id']})
        if len(selected)<2: selected=[]
        return {'summary': summary, 'details': details, 'lifestyle': ['food','activity'], 'quiz': selected}
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
    generated['quiz'] = [{'question_id':'0:result'}]
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
    assert len([r for r in app.radio if ('무엇이라고 적혀 있나요' in r.label or ('함께 읽을 때' in r.label or '확정할 수 없는' in r.label or '어떤 정보를' in r.label))]) in (2, 3)
    headings = [m.value for m in app.markdown if m.value.startswith('### ')]
    assert headings[0] == ('### AI 건강검진 리포트' if data is HEALTH else '### AI가 풀어쓴 문서 설명')
    assert headings[-1] == '### 문서 이해도 확인'
    if data is HEALTH:
        bank = {q['id']: q for q in payload['understanding_questions']}
        quizzes = plan(payload)['quiz']
        questions = [r for r in app.radio if r.label.startswith(('1. ','2. ','3. '))]
        for radio, item in zip(questions, quizzes):
            radio.set_value(bank[item['question_id']]['answer'])
        app.run()
        assert any(c.value=='정답입니다.' for c in app.caption)
        app.sidebar.radio[0].set_value('대시보드').run()
        app.sidebar.radio[0].set_value(page).run()
        assert not app.exception and fake.call_count==1
        assert any(c.value=='정답입니다.' for c in app.caption)
    else:
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
    assert len(result['plan']['summary']) in ((3,4,5) if data is HEALTH else (2,3))
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
    assert any(generated['summary'][0]['text'] in m.value for m in app.markdown)
    assert any('FactChat 핵심 요약' in c.value for c in app.caption)
    bank = {q['id']: q for q in payload['understanding_questions']}
    questions = [r for r in app.radio if r.label.startswith(('1. ','2. ','3. '))]
    for index, (radio, item) in enumerate(zip(questions, generated['quiz'])):
        quiz = bank[item['question_id']]
        radio.set_value(quiz['answer'] if index==0 else quiz['options'][1])
    app.run()
    assert any(c.value=='정답입니다.' for c in app.caption)
    assert any(c.value.startswith('다시 확인해 보세요.') for c in app.caption)
    assert any(bank[generated['quiz'][0]['question_id']]['explanation'] in m.value for m in app.markdown)
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


def test_health_report_is_short_and_lifestyle_only_uses_reviewed_templates():
    from carebridge.health_report import LIFESTYLE, LIMIT
    payload = fc.build_facts(HEALTH)
    generated = plan(payload)
    assert fc.validate_plan(generated, payload)
    assert 3 <= len(generated['summary']) <= 5
    assert sum(len(p['text']) for p in generated['summary']) <= 650
    assert generated['summary'][-1]['text']==LIMIT
    assert 2 <= len(generated['lifestyle']) <= 3
    assert all(key in LIFESTYLE for key in generated['lifestyle'])
    for invalid in [['food','treatment'], ['food']*2, ['food'], [{'text':'약을 늘리세요'}]]:
        bad = deepcopy(generated)
        bad['lifestyle'] = invalid
        with pytest.raises(fc.ExplanationError):
            fc.validate_plan(bad, payload)
    bad = deepcopy(generated)
    bad['summary'][0]['text'] = '문서의 항목을 함께 확인해 볼 필요가 있습니다. '*15
    with pytest.raises(fc.ExplanationError):
        fc.validate_plan(bad, payload)


def test_health_understanding_questions_require_concepts_not_number_recall():
    payload = fc.build_facts(HEALTH)
    generated = plan(payload)
    bank = {q['id']: q for q in payload['understanding_questions']}
    selected = [bank[item['question_id']] for item in generated['quiz']]
    assert 2 <= len(selected) <= 3
    assert {q['theme'] for q in selected} >= {'relationship', 'limits'}
    for q in selected:
        assert not re.search(r'몇|측정값은|무엇이라고 적혀', q['question'])
        assert q['answer'] in q['options'] and len(set(q['options']))==3
        assert q['explanation'] and q['evidence']
        assert all(i in {f['id'] for f in payload['facts']} for i in q['evidence'])
    generated['quiz'][0] = {'question_id':'0:result'}
    with pytest.raises(fc.ExplanationError, match='암기|검증되지'):
        fc.validate_plan(generated, payload)


def test_report_conflicting_verdict_requires_review_in_summary_and_details():
    data = {'health_checkup_results':[{'test_item_name':'공복혈당(mg/dL)','result_value':'108','reference_range':'100미만','remark':'정상'}]}
    payload = fc.build_facts(data)
    comparison = next(f for f in payload['facts'] if f['field']=='comparison')
    assert comparison['level']=='unknown' and comparison['conflict']
    generated = plan(payload)
    generated['summary'][0]['text'] = '공복혈당의 원문 판정과 수치 기준이 달라 확인 필요로 남겨 두었습니다.'
    assert fc.validate_plan(generated, payload)
    generated['summary'][0]['text'] = '공복혈당은 정상입니다.'
    with pytest.raises(fc.ExplanationError, match='충돌'):
        fc.validate_plan(generated, payload)


def test_health_report_screen_order_collapsed_details_and_immediate_feedback(monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from carebridge.domain import normalize
    from carebridge.records import record
    payload = fc.build_facts(HEALTH)
    generated = plan(payload)
    fake = Mock(return_value={'plan': generated, 'usage':{}, 'model':'solar-mini4', 'source':'factchat_generated'})
    monkeypatch.setattr(fc, 'request_plan', fake)
    kind, clean = normalize(HEALTH)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'), default_timeout=15).run()
    app.session_state['records']=[record(kind,clean,'synthetic.pdf','test',kind)]
    app.sidebar.radio[0].set_value('건강검진').run()
    next(b for b in app.button if b.label=='쉬운 설명 보기').click().run()
    next(c for c in app.checkbox if 'Gateway 전송에 동의' in c.label).check().run()
    next(b for b in app.button if b.label=='동의한 정보로 설명 생성').click().run()
    assert not app.exception and fake.call_count==1
    headings=[m.value for m in app.markdown if m.value.startswith('### ')]
    assert headings==['### AI 건강검진 리포트','### 생활 속 건강관리 가이드','### 상세 검진 수치','### 문서 이해도 확인']
    expander=next(e for e in app.expander if e.label=='항목별 AI 설명 · 요약 근거')
    assert not expander.proto.expanded
    assert any(generated['details'][0]['text'] in m.value for m in expander.markdown)
    assert any('검진표 원문이 아닌 일반 건강정보' in c.value for c in app.caption)
    assert not any(b.label=='선택한 답 채점하기' for b in app.button)
    bank={q['id']:q for q in payload['understanding_questions']}
    q=bank[generated['quiz'][0]['question_id']]
    radio=next(r for r in app.radio if r.label.startswith('1. '))
    radio.set_value(q['answer']).run()
    assert any(c.value=='정답입니다.' for c in app.caption)
    assert any(q['explanation'] in m.value for m in app.markdown)
    assert fake.call_count==1


@pytest.mark.parametrize('notice', __import__('carebridge.health_report', fromlist=['REPORT_NOTICES']).REPORT_NOTICES)
def test_reviewed_non_diagnostic_notice_does_not_trigger_medical_keyword_false_positive(notice):
    # Validator-only regression: no transport, HTTP mock, or API call involved.
    payload = fc.build_facts(HEALTH)
    generated = plan(payload)
    generated['summary'][-1]['text'] = notice
    assert fc.validate_plan(generated, payload) is generated
    generated['details'][0]['text'] += ' '+notice
    assert fc.validate_plan(generated, payload) is generated


@pytest.mark.parametrize('claim', [
    '당뇨병으로 진단합니다.', '약물 변경을 권장합니다.', '치료가 필요합니다.',
    '혈압약 복용을 중단하세요.', '검사 결과는 정상입니다.',
])
def test_reviewed_notice_never_shields_an_extra_generated_medical_claim(claim):
    from carebridge.health_report import REPORT_NOTICES
    payload = fc.build_facts(HEALTH)
    generated = plan(payload)
    generated['details'][0]['text'] += ' '+REPORT_NOTICES[1]+' '+claim
    with pytest.raises(fc.ExplanationError) as raised:
        fc.validate_plan(generated, payload)
    assert raised.value.validation_rule == 'document_medical_claim'
    assert raised.value.validation_section == 'details'
    assert raised.value.validation_index == 0


@pytest.mark.parametrize('text', [
    '이 결과로 특정 질병을 진단할 수 있습니다.',
    '이 결과만으로 특정 질병을 진단할 수 없습니다. 하지만 당뇨병입니다.',
    '진단이라는 단어는 모두 허용합니다.',
])
def test_unreviewed_or_positive_diagnosis_text_is_still_rejected(text):
    payload = fc.build_facts(HEALTH)
    generated = plan(payload)
    generated['summary'][0]['text'] = text
    with pytest.raises(fc.ExplanationError) as raised:
        fc.validate_plan(generated, payload)
    assert raised.value.validation_rule == 'document_medical_claim'


def test_lifestyle_and_document_prose_are_validated_as_separate_authorities():
    from carebridge.health_report import LIFESTYLE
    payload = fc.build_facts(HEALTH)
    generated = plan(payload)
    generated['lifestyle'] = ['food','activity','sleep']
    assert fc.validate_plan(generated, payload)
    # Reviewed general information is allowed; personal lifestyle prescriptions are not.
    reviewed = deepcopy(generated)
    reviewed['summary'][0]['text'] = LIFESTYLE['food']['text']
    assert fc.validate_plan(reviewed,payload)
    for text in ['걷기 시간을 늘리고 수면 시간을 정해 드립니다.']:
        bad = deepcopy(generated)
        bad['summary'][0]['text'] = text
        with pytest.raises(fc.ExplanationError) as raised:
            fc.validate_plan(bad, payload)
        assert raised.value.validation_rule == 'document_lifestyle_prose'
        assert raised.value.validation_section == 'summary'
    for value in [[{'id':'food','text':'약물을 바꾸세요.'},'activity'], ['food','generated_plan']]:
        bad = deepcopy(generated)
        bad['lifestyle'] = value
        with pytest.raises(fc.ExplanationError) as raised:
            fc.validate_plan(bad, payload)
        assert raised.value.validation_rule == 'lifestyle_template_only'
        assert raised.value.validation_section == 'lifestyle'


def test_api_json_decoding_is_separate_from_medical_validation():
    payload = fc.build_facts(HEALTH)
    unsafe = plan(payload)
    unsafe['summary'][0]['text'] = '당뇨병으로 진단합니다.'
    parsed = fc.parse_generated_content('```json\n'+json.dumps(unsafe,ensure_ascii=False)+'\n```')
    assert parsed == unsafe  # API/JSON success is not content validation success.
    with pytest.raises(fc.ExplanationError) as raised:
        fc.validate_plan(parsed,payload)
    assert raised.value.validation_rule == 'document_medical_claim'
    with pytest.raises(fc.ExplanationError) as invalid_json:
        fc.parse_generated_content('not json: PRIVATE_CONTENT')
    assert invalid_json.value.validation_rule == 'response_json'
    assert 'PRIVATE_CONTENT' not in str(invalid_json.value)


def test_gateway_rejection_logs_only_rule_location_not_claim_or_identity(caplog):
    payload = fc.build_facts(HEALTH)
    unsafe = plan(payload)
    unsafe['summary'][0]['text'] = '당뇨병으로 진단합니다. PRIVATE_PATIENT RAW_RESPONSE_SECRET'
    transport = Mock()
    transport.post.return_value = response(payload, choices=[{'finish_reason':'stop','message':{'content':json.dumps(unsafe,ensure_ascii=False)}}])
    with pytest.raises(fc.ExplanationError):
        fc.request_plan(payload,consent=True,session=transport)
    assert 'http=200' in caplog.text
    assert 'state=rejected rule=document_medical_claim section=summary item=0' in caplog.text
    assert 'state=content_validated' not in caplog.text
    for private in ['당뇨병','PRIVATE_PATIENT','RAW_RESPONSE_SECRET','26.8','test-only-secret']:
        assert private not in caplog.text
    assert transport.post.call_count == 1


def test_gateway_accepts_reviewed_notice_and_template_ids_without_retry(caplog):
    from carebridge.health_report import REPORT_NOTICES
    payload = fc.build_facts(HEALTH)
    generated = plan(payload)
    generated['summary'][-1]['text'] = REPORT_NOTICES[1]
    transport = Mock()
    transport.post.return_value = response(payload,choices=[{'finish_reason':'stop','message':{'content':json.dumps(generated,ensure_ascii=False)}}])
    assert fc.request_plan(payload,consent=True,session=transport)['plan']==generated
    assert 'state=content_validated' in caplog.text
    assert transport.post.call_count == 1


SAFE_CONTEXT_SENTENCES = [
    '이 검진 결과만으로 특정 질환을 진단할 수는 없습니다.',
    '공복혈당은 음식을 먹지 않은 상태에서 혈액 속 포도당의 양을 측정한 값입니다.',
    '균형 잡힌 식습관과 꾸준한 신체활동은 일반적인 건강관리에 도움이 될 수 있습니다.',
]


def contextual_report(payload):
    result = plan(payload)
    ids = [f['id'] for f in payload['facts']]
    result['summary'] = [{'text': text, 'evidence': ids} for text in SAFE_CONTEXT_SENTENCES]
    result['summary'].append({'text':'공복혈당 측정값 108은 문서의 참고기준 100미만과 달라 원문과 함께 확인할 필요가 있습니다.', 'evidence': ids})
    return result


@pytest.mark.parametrize('sentence', SAFE_CONTEXT_SENTENCES+[
    '정상 범위라는 표현은 검진표의 비교 기준을 뜻하며 질환의 확정 진단을 의미하지 않습니다.',
    '가능성이나 의심이라는 표현은 확정 진단과 같은 뜻이 아닙니다.',
    '이 수치만으로 치료가 필요하다고 판단하기는 어렵습니다.',
])
def test_health_context_allows_terms_definitions_limits_and_general_health(sentence):
    payload=fc.build_facts(HEALTH)
    generated=contextual_report(payload)
    generated['summary'][1]['text']=sentence
    assert fc.validate_plan(generated,payload) is generated


@pytest.mark.parametrize('sentence',[
    '당신은 당뇨병이 확실합니다.',
    '복용 중인 약을 지금부터 중단하세요.',
    '원문에 없는 복용량으로 변경해서 복용하세요.',
    '이 검진 결과만으로 진단할 수는 없지만 당신은 당뇨병이 확실합니다.',
    '이 수치로 고혈압의 가능성이 높습니다.',
    '문서에 108이 적혀 있으므로 당신은 당뇨병이 확실합니다.',
])
def test_health_context_blocks_asserted_diagnosis_and_personal_medication_actions(sentence):
    payload=fc.build_facts(HEALTH)
    generated=contextual_report(payload)
    generated['summary'][1]['text']=sentence
    with pytest.raises(fc.ExplanationError) as error:
        fc.validate_plan(generated,payload)
    assert error.value.validation_rule=='document_medical_claim'
    assert error.value.validation_section=='summary' and error.value.validation_index==1


@pytest.mark.parametrize('sentence,rule',[
    ('공복혈당 측정값 109를 확인해 보세요.','document_number'),
    ('공복혈당 측정값 100을 확인해 보세요.','document_result_value'),
    ('공복혈당 참고기준은 108입니다.','document_reference_value'),
    ('공복혈당 참고기준은 100이상입니다.','document_reference_condition'),
    ('공복혈당의 참고기준은 108미만입니다.','document_reference_condition'),
    ('공복혈당 측정값 108g/dL을 확인해 보세요.','document_quantity'),
    ('공복혈당 측정값 108µg/dL을 확인해 보세요.','document_quantity'),
    ('BMI 측정값 26.8mg/dL을 확인해 보세요.','document_quantity'),
    ('BMI의 참고범위는 25~26.8입니다.','document_reference_condition'),
])
def test_health_quantity_and_reference_manipulation_is_not_hidden_by_valid_numbers(sentence,rule):
    payload=fc.build_facts(HEALTH)
    generated=contextual_report(payload)
    generated['summary'][1]['text']=sentence
    with pytest.raises(fc.ExplanationError) as error:
        fc.validate_plan(generated,payload)
    assert error.value.validation_rule==rule


def test_contextual_health_prose_still_requires_actual_evidence_ids():
    payload=fc.build_facts(HEALTH)
    generated=contextual_report(payload)
    generated['summary'][1]['evidence']=['unknown-evidence']
    with pytest.raises(fc.ExplanationError) as error:
        fc.validate_plan(generated,payload)
    assert error.value.validation_rule=='document_evidence'


def test_health_general_information_does_not_relax_medication_rules():
    payload=fc.build_facts(MEDS)
    generated=plan(payload)
    generated['summary'][0]['text']=SAFE_CONTEXT_SENTENCES[2]
    with pytest.raises(fc.ExplanationError):
        fc.validate_plan(generated,payload)


def test_contextual_gateway_response_reaches_report_and_conceptual_quiz_without_live_api(monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from carebridge.domain import normalize
    from carebridge.records import record
    from carebridge.health_safety import AI_NOTICE
    payload=fc.build_facts(HEALTH)
    generated=contextual_report(payload)
    transport=Mock()
    transport.post.return_value=response(payload,choices=[{'finish_reason':'stop','message':{'content':json.dumps(generated,ensure_ascii=False)}}])
    monkeypatch.setattr(fc.requests,'Session',lambda:transport)
    kind,clean=normalize(HEALTH)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=15).run()
    app.session_state['records']=[record(kind,clean,'synthetic.pdf','test',kind)]
    app.sidebar.radio[0].set_value('건강검진').run()
    next(b for b in app.button if b.label=='쉬운 설명 보기').click().run()
    next(c for c in app.checkbox if 'Gateway 전송에 동의' in c.label).check().run()
    assert transport.post.call_count==0
    next(b for b in app.button if b.label=='동의한 정보로 설명 생성').click().run()
    assert not app.exception and not app.warning
    assert any(all(text in m.value for text in SAFE_CONTEXT_SENTENCES) for m in app.markdown)
    assert any('일반 건강정보: '+SAFE_CONTEXT_SENTENCES[2] in m.value for m in app.markdown)
    assert any(c.value==AI_NOTICE for c in app.caption)
    headings=[m.value for m in app.markdown if m.value.startswith('### ')]
    assert headings==['### AI 건강검진 리포트','### 생활 속 건강관리 가이드','### 상세 검진 수치','### 문서 이해도 확인']
    questions=[r for r in app.radio if r.label.startswith(('1. ','2. ','3. '))]
    assert 2<=len(questions)<=3
    bank={q['id']:q for q in payload['understanding_questions']}
    first=bank[generated['quiz'][0]['question_id']]
    questions[0].set_value(first['answer']).run()
    assert any(c.value=='정답입니다.' for c in app.caption)
    app.sidebar.radio[0].set_value('대시보드').run()
    app.sidebar.radio[0].set_value('건강검진').run()
    assert not app.exception and transport.post.call_count==1
    assert any(c.value=='정답입니다.' for c in app.caption)
    assert app.session_state['records'][0]['data']==clean


@pytest.mark.parametrize('sentence',[
    '균형 잡힌 식단은 일반적인 건강관리에 도움이 될 수 있습니다.',
    '일반적인 건강관리를 위해 꾸준한 신체활동을 권장합니다.',
    '이 검진 결과만으로 약물 변경을 지시하는 것이 아닙니다.',
    '이 검진 결과만으로 약을 변경할 수 없습니다.',
])
def test_health_general_context_is_not_an_exact_sentence_allowlist(sentence):
    payload=fc.build_facts(HEALTH)
    generated=contextual_report(payload)
    generated['summary'][1]['text']=sentence
    assert fc.validate_plan(generated,payload)


@pytest.mark.parametrize('sentence',[
    '당신에게 일반적인 건강관리를 위해 걷기 시간을 늘리라고 정해 드립니다.',
    '일반적인 건강관리를 위해 운동을 108분 하세요.',
    '이 결과만으로 약을 변경할 수 없지만 지금부터 중단하세요.',
])
def test_general_or_negative_context_does_not_shield_personal_prescriptions(sentence):
    payload=fc.build_facts(HEALTH)
    generated=contextual_report(payload)
    generated['summary'][1]['text']=sentence
    with pytest.raises(fc.ExplanationError):
        fc.validate_plan(generated,payload)


def test_measurement_label_does_not_allow_borrowing_a_reference_number():
    payload=fc.build_facts(HEALTH)
    generated=contextual_report(payload)
    generated['summary'][1]['text']='공복혈당은 100mg/dL입니다.'
    with pytest.raises(fc.ExplanationError) as error:
        fc.validate_plan(generated,payload)
    assert error.value.validation_rule=='document_result_value'


def test_patient_specific_diagnosis_check_is_not_limited_to_a_diabetes_keyword():
    payload=fc.build_facts(HEALTH)
    generated=contextual_report(payload)
    generated['summary'][1]['text']='당신은 빈혈이 확실합니다.'
    with pytest.raises(fc.ExplanationError) as error:
        fc.validate_plan(generated,payload)
    assert error.value.validation_rule=='document_medical_claim'


@pytest.mark.parametrize('name,value,unit,reference',[
    ('혈색소','15.1','g/dL','남 13-16.5 여 12-15.5'),
    ('신장 검사','90','mL/min/1.73m²','60이상'),
    ('추가 검사','-0.4','mmol/L','-1~1'),
])
def test_valid_units_and_decimal_reference_ranges_are_not_false_positives(name,value,unit,reference):
    raw={'examinee_sex':'남성','health_checkup_results':[{'test_item_name':name,'result_value':value,'result_unit':unit,'reference_range':reference}]}
    payload=fc.build_facts(raw)
    generated=plan(payload)
    assert fc.validate_plan(generated,payload)
    generated['details'][0]['text']+=' '+name+' 측정값 '+value+unit+'을 확인해 주세요.'
    assert fc.validate_plan(generated,payload)


@pytest.mark.parametrize('sentence',[
    '당신은 당뇨병이 확실합니다.',
    '복용 중인 약을 지금부터 중단하세요.',
    '원문에 없는 복용량으로 변경해서 복용하세요.',
])
def test_unsafe_mock_gateway_content_keeps_rule_screen_without_recall(monkeypatch,sentence):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from carebridge.domain import normalize
    from carebridge.records import record
    payload=fc.build_facts(HEALTH)
    generated=contextual_report(payload)
    generated['summary'][1]['text']=sentence
    transport=Mock()
    transport.post.return_value=response(payload,choices=[{'finish_reason':'stop','message':{'content':json.dumps(generated,ensure_ascii=False)}}])
    monkeypatch.setattr(fc.requests,'Session',lambda:transport)
    kind,clean=normalize(HEALTH)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=15).run()
    app.session_state['records']=[record(kind,clean,'synthetic.pdf','test',kind)]
    app.sidebar.radio[0].set_value('건강검진').run()
    next(b for b in app.button if b.label=='쉬운 설명 보기').click().run()
    next(c for c in app.checkbox if 'Gateway 전송에 동의' in c.label).check().run()
    next(b for b in app.button if b.label=='동의한 정보로 설명 생성').click().run()
    app.run()
    assert not app.exception
    assert any('document_medical_claim' in w.value for w in app.warning)
    assert not any('### AI 건강검진 리포트'==m.value for m in app.markdown)
    assert any('<table aria-label="건강검진 결과"' in m.value for m in app.markdown)
    assert transport.post.call_count==1
    assert app.session_state['records'][0]['data']==clean


def test_safe_report_does_not_depend_on_an_exact_generated_disclaimer():
    payload=fc.build_facts(HEALTH)
    generated=contextual_report(payload)
    generated['summary'][0]['text']='검진표의 주의 항목과 참고기준을 함께 살펴보는 안내입니다.'
    assert fc.validate_plan(generated,payload)
    generated['summary'][0]['text']='개인별 치료를 받아야 합니다.'
    with pytest.raises(fc.ExplanationError) as error:
        fc.validate_plan(generated,payload)
    assert error.value.validation_rule=='document_medical_claim'


@pytest.mark.parametrize('data',[HEALTH,MEDS])
def test_official_strict_output_contract_and_local_validation(data):
    from carebridge.output_contract import response_format
    payload=fc.build_facts(data)
    contract=response_format(payload)
    assert contract['type']=='json_schema' and contract['json_schema']['strict'] is True
    def check(node):
        if isinstance(node,dict):
            if node.get('type')=='object':
                assert node['additionalProperties'] is False
                assert set(node['required'])==set(node['properties'])
            for value in node.values(): check(value)
        elif isinstance(node,list):
            for value in node: check(value)
    check(contract)
    session=Mock()
    session.post.return_value=response(payload)
    result=fc.request_plan(payload,consent=True,session=session)
    assert session.post.call_args.kwargs['json']['response_format']==contract
    assert result['source']=='factchat_generated' and session.post.call_count==1


def test_prompt_only_is_explicit_and_still_validates(monkeypatch):
    monkeypatch.setenv('FACTCHAT_RESPONSE_FORMAT','prompt_json')
    payload=fc.build_facts(HEALTH)
    generated=plan(payload)
    generated['summary'][0]['text']='당신은 당뇨병이 확실합니다.'
    session=Mock()
    session.post.return_value=response(payload,choices=[{'finish_reason':'stop','message':{'content':json.dumps(generated)}}])
    with pytest.raises(fc.ExplanationError) as error: fc.request_plan(payload,consent=True,session=session)
    assert error.value.validation_rule=='document_medical_claim'
    assert 'response_format' not in session.post.call_args.kwargs['json']
    assert session.post.call_count==1


def test_rejected_format_has_no_automatic_fallback_or_second_request():
    session=Mock()
    session.post.return_value=Mock(status_code=400)
    with pytest.raises(fc.ExplanationError): fc.request_plan(fc.build_facts(HEALTH),consent=True,session=session)
    assert session.post.call_count==1


def test_invalid_format_configuration_does_not_spend_request(monkeypatch):
    monkeypatch.setenv('FACTCHAT_RESPONSE_FORMAT','invalid')
    session=Mock()
    with pytest.raises(fc.ExplanationError): fc.request_plan(fc.build_facts(HEALTH),consent=True,session=session)
    assert fc._requests==0 and not session.post.called


def test_uncertain_medication_fields_not_eligible_for_quiz_or_silent_correction():
    data=deepcopy(MEDS)
    data['medication_instructions'][0].update(single_dose='1~2정',times_per_day='2~3회',days_of_administration='미상',administration_timing='필요시')
    original=deepcopy(data)
    payload=fc.build_facts(data)
    for fact in payload['facts']:
        if fact['field'] in ('dose','frequency','days','timing'): assert not fact['quiz_allowed']
    assert any(f['field']=='check' and '확인 필요' in f['value'] for f in payload['facts'])
    assert data==original
    generated=plan(payload)
    assert fc.validate_plan(generated,payload)
    generated['details'][0]['text']+=' 9정으로 변경하세요.'
    with pytest.raises(fc.ExplanationError): fc.validate_plan(generated,payload)


@pytest.mark.parametrize('kind',['health_checkup_result','medication_guidance_record'])
def test_mock_agent_to_ai_ui_and_uncertain_schedule_gate(monkeypatch,kind):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from carebridge import client
    data=deepcopy(HEALTH if kind=='health_checkup_result' else MEDS)
    if kind=='medication_guidance_record':
        data['medication_instructions'][0].update(single_dose='1~2정',times_per_day='2~3회',days_of_administration='미상',administration_timing='필요시')
    payload=fc.build_facts(data)
    generated=plan(payload)
    transport=Mock()
    transport.post.return_value=response(payload)
    monkeypatch.setattr(fc.requests,'Session',lambda:transport)
    class FakeAgent:
        cleanup_warning=False
        def analyze(self,content,progress):
            return {'id':'mock-demo','status':'completed','output':[
                {'step_type':'document-parse','status':'completed','content':[{'type':'output_text','text':'가상 중간 텍스트: 원문과 대조 필요'}]},
                {'step_type':'document-classify','status':'completed','content':[{'type':'output_text','text':kind}]},
                {'step_type':'information-extract','status':'completed','content':[{'type':'output_text','text':json.dumps(data)}]}]}
    monkeypatch.setattr(client,'AgentClient',FakeAgent)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=15).run()
    app.sidebar.radio[0].set_value('문서 분석').run()
    next(r for r in app.radio if r.label=='문서 선택 방식').set_value('가상 샘플 체험').run()
    app.checkbox[0].check().run()
    next(b for b in app.button if b.label=='AI 분석 시작').click().run()
    assert not app.exception and transport.post.call_count==0
    saved=app.session_state['records'][0]
    assert saved['classification']==kind
    assert app.session_state['stage_reviews'][saved['id']]['parse_text']=='가상 중간 텍스트: 원문과 대조 필요'
    next(b for b in app.button if b.label=='쉬운 설명 보기').click().run()
    next(c for c in app.checkbox if 'Gateway 전송에 동의' in c.label).check().run()
    next(b for b in app.button if b.label=='동의한 정보로 설명 생성').click().run()
    assert not app.exception
    assert transport.post.call_count==1
    assert any(generated['summary'][0]['text'] in m.value for m in app.markdown)
    if kind=='health_checkup_result':
        questions=[r for r in app.radio if r.label.startswith(('1. ','2. ','3. '))]
        bank={q['id']:q for q in payload['understanding_questions']}
        questions[0].set_value(bank[generated['quiz'][0]['question_id']]['answer']).run()
        assert any(c.value=='정답입니다.' for c in app.caption)
    else:
        app.toggle[0].set_value(True).run()
        assert next(b for b in app.button if b.label=='확인한 내용으로 일정 저장').disabled
        next(c for c in app.checkbox if '일정 저장을 승인' in c.label).check().run()
        assert next(b for b in app.button if b.label=='확인한 내용으로 일정 저장').disabled
        assert not app.session_state['schedules']
        assert any('확인 필요' in w.value for w in app.warning)
    next(c for c in app.checkbox if '개인정보가 포함될 수 있는 원본 자료' in c.label).check().run()
    assert any('가상 중간 텍스트' in t.value for t in app.text)
    assert transport.post.call_count==1 and app.session_state['records'][0]['data']==saved['data']
