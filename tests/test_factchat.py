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


def open_explanation(app):
    # Medication keeps its existing opt-in button; health exposes consent directly.
    buttons=[b for b in app.button if b.label=='쉬운 설명 보기' and not b.disabled]
    if buttons: buttons[0].click().run()


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
            if 'comparison' not in fields: continue
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
        return {'summary': summary, 'quiz': selected}
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
    open_explanation(app)
    generate = next(b for b in app.button if b.label == '동의한 정보로 설명 생성')
    assert generate.disabled
    fake.assert_not_called()
    next(c for c in app.checkbox if 'Gateway 전송에 동의' in c.label).check().run()
    fake.assert_not_called()
    next(b for b in app.button if b.label == '동의한 정보로 설명 생성').click().run()
    assert not app.exception and fake.call_count == 1
    assert len([r for r in app.radio if ('무엇이라고 적혀 있나요' in r.label or ('함께 읽을 때' in r.label or '확정할 수 없는' in r.label or '어떤 정보를' in r.label))]) in (2, 3)
    headings = [m.value for m in app.markdown if m.value.startswith('### ')]
    assert headings[0] == ('### AI 핵심 요약' if data is HEALTH else '### AI가 풀어쓴 문서 설명')
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
    open_explanation(app)
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
        assert set(result['plan'])=={'summary','quiz'}



@pytest.mark.parametrize('extra', [
    ' 999mg/dL이므로 확인이 필요합니다.', ' 당뇨병입니다.', ' 운동을 추천합니다.', ' 정상입니다.'
])
def test_health_unsupported_number_diagnosis_or_advice_rejected(extra):
    payload = fc.build_facts(HEALTH)
    generated = plan(payload)
    generated['summary'][0]['text'] += extra
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
    generated['summary'][0]['text'] += ' 기준보다 높습니다.'
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
    open_explanation(app)
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
    open_explanation(app)
    next(c for c in app.checkbox if 'Gateway 전송에 동의' in c.label).check().run()
    next(b for b in app.button if b.label=='동의한 정보로 설명 생성').click().run()
    app.run()
    assert not app.exception and transport.post.call_count==1
    assert any('AI 설명을 생성하지 못해 기본 요약을 표시합니다' in i.value for i in app.info)
    assert not any(m.value=='### AI가 풀어쓴 문서 설명' for m in app.markdown)
    assert any('<table aria-label="건강검진 결과"' in m.value for m in app.markdown)
    assert any('기본 문서 안내' in c.value for c in app.caption)


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
    assert set(generated)=={'summary','quiz'}
    assert set(LIFESTYLE)=={'food','activity'}
    bad = deepcopy(generated)
    bad['summary'][0]['text'] = '문서의 항목을 함께 확인해 볼 필요가 있습니다. '*30
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


def test_report_conflicting_verdict_cannot_be_overridden_in_summary():
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


def test_health_report_screen_order_collapsed_evidence_and_immediate_feedback(monkeypatch):
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
    open_explanation(app)
    next(c for c in app.checkbox if 'Gateway 전송에 동의' in c.label).check().run()
    next(b for b in app.button if b.label=='동의한 정보로 설명 생성').click().run()
    assert not app.exception and fake.call_count==1
    headings=[m.value for m in app.markdown if m.value.startswith('### ')]
    assert headings==['### AI 핵심 요약','### 어려운 검사 용어 설명','### 검사 결과 의미 해설','### 생활습관 정보 제공','### 상세 검진 수치','### 문서 이해도 확인']
    expander=next(e for e in app.expander if e.label=='요약 근거')
    assert not expander.proto.expanded
    assert expander.text
    assert not any(e.label=='항목별 AI 설명 · 요약 근거' for e in app.expander)
    assert any('WHO 자료 기반 일반 건강정보' in c.value for c in app.caption)
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
    generated['summary'][0]['text'] += ' '+notice
    assert fc.validate_plan(generated, payload) is generated


@pytest.mark.parametrize('claim', [
    '당뇨병으로 진단합니다.', '약물 변경을 권장합니다.', '치료가 필요합니다.',
    '혈압약 복용을 중단하세요.', '검사 결과는 정상입니다.',
])
def test_reviewed_notice_never_shields_an_extra_generated_medical_claim(claim):
    from carebridge.health_report import REPORT_NOTICES
    payload = fc.build_facts(HEALTH)
    generated = plan(payload)
    generated['summary'][0]['text'] += ' '+REPORT_NOTICES[1]+' '+claim
    with pytest.raises(fc.ExplanationError) as raised:
        fc.validate_plan(generated, payload)
    assert raised.value.validation_rule == ('document_comparison' if claim=='검사 결과는 정상입니다.' else 'document_medical_claim')
    assert raised.value.validation_section == 'summary'
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
        assert raised.value.validation_rule == 'response_schema'
        assert raised.value.validation_section == 'response'


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
    from carebridge.explanation_ui import HEALTH_NOTICE as AI_NOTICE
    payload=fc.build_facts(HEALTH)
    generated=contextual_report(payload)
    transport=Mock()
    transport.post.return_value=response(payload,choices=[{'finish_reason':'stop','message':{'content':json.dumps(generated,ensure_ascii=False)}}])
    monkeypatch.setattr(fc.requests,'Session',lambda:transport)
    kind,clean=normalize(HEALTH)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=15).run()
    app.session_state['records']=[record(kind,clean,'synthetic.pdf','test',kind)]
    app.sidebar.radio[0].set_value('건강검진').run()
    open_explanation(app)
    next(c for c in app.checkbox if 'Gateway 전송에 동의' in c.label).check().run()
    assert transport.post.call_count==0
    next(b for b in app.button if b.label=='동의한 정보로 설명 생성').click().run()
    assert not app.exception and not app.warning
    assert any(all(text in m.value for text in SAFE_CONTEXT_SENTENCES) for m in app.markdown)
    assert any('일반 건강정보: '+SAFE_CONTEXT_SENTENCES[2] in m.value for m in app.markdown)
    assert any(c.value==AI_NOTICE for c in app.caption)
    headings=[m.value for m in app.markdown if m.value.startswith('### ')]
    assert headings==['### AI 핵심 요약','### 어려운 검사 용어 설명','### 검사 결과 의미 해설','### 생활습관 정보 제공','### 상세 검진 수치','### 문서 이해도 확인']
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
    generated['summary'][0]['text']+=' '+name+' 측정값 '+value+unit+'을 확인해 주세요.'
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
    open_explanation(app)
    next(c for c in app.checkbox if 'Gateway 전송에 동의' in c.label).check().run()
    next(b for b in app.button if b.label=='동의한 정보로 설명 생성').click().run()
    app.run()
    assert not app.exception
    assert any('AI 설명을 생성하지 못해 기본 요약을 표시합니다' in i.value for i in app.info)
    assert not app.warning
    assert not any('### AI 건강검진 해설'==m.value for m in app.markdown)
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
    next(b for b in app.button if b.label=='AI 분석 및 해설 보기').click().run()
    assert not app.exception and transport.post.call_count==0
    saved=app.session_state['records'][0]
    assert saved['classification']==kind
    assert app.session_state['stage_reviews'][saved['id']]['parse_text']=='가상 중간 텍스트: 원문과 대조 필요'
    open_explanation(app)
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


def natural_report(payload):
    """No verbatim comparison labels; the code-owned table supplies exact data."""
    generated=plan(payload)
    generated['summary']=[
        {'text':'공복혈당은 검진표가 제시한 참고범위를 벗어나 있어 수치와 기준을 함께 확인할 항목입니다.', 'evidence':['1:result','1:reference']},
        {'text':'BMI는 문서에 과체중 구간으로 적혀 있어 몸무게와 키의 관계를 이해할 때 참고할 수 있습니다.', 'evidence':['0:result','0:reference']},
        {'text':'이 검진 결과만으로 특정 질환을 진단할 수는 없습니다.', 'evidence':['0:result','1:result']},
    ]
    return generated


def test_scenario_a_paraphrased_summary_only_passes():
    payload=fc.build_facts(HEALTH)
    generated=natural_report(payload)
    comparison_values=[f['value'] for f in payload['facts'] if f['field']=='comparison']
    assert '문서 기준 초과' in comparison_values
    assert '문서 기준 초과' not in str(generated)
    assert fc.validate_plan(generated,payload)==generated
    assert 'details' not in generated and 'lifestyle' not in generated


@pytest.mark.parametrize('sentence',[
    '공복혈당은 음식을 먹지 않은 상태에서 혈액 속 포도당의 양을 측정한 값입니다.',
    '이 결과가 당뇨병으로 진단할 근거가 충분하다는 뜻은 아닙니다.',
    '약물 변경이나 치료가 필요합니다라는 뜻은 아닙니다.',
    '정상입니다라는 표현만으로 전체 건강 상태를 확정할 수는 없습니다.',
])
def test_scenario_b_safe_definitions_and_scoped_limitations(sentence):
    payload=fc.build_facts(HEALTH)
    generated=natural_report(payload)
    generated['summary'][2]={'text':sentence,'evidence':[f['id'] for f in payload['facts']]}
    assert fc.validate_plan(generated,payload)


@pytest.mark.parametrize('sentence',[
    '공복혈당 측정값은 999mg/dL입니다.',
    '공복혈당의 참고기준은 110미만입니다.',
    '공복혈당 참고기준은 100이상입니다.',
    '공복혈당 측정값은 108g/dL입니다.',
    '공복혈당은 26.8로 문서에 기록되어 있습니다.',
])
def test_scenario_c_invented_or_altered_quantities_rejected(sentence):
    payload=fc.build_facts(HEALTH)
    generated=natural_report(payload)
    generated['summary'][0]={'text':sentence,'evidence':[f['id'] for f in payload['facts']]}
    with pytest.raises(fc.ExplanationError): fc.validate_plan(generated,payload)


@pytest.mark.parametrize('sentence',[
    '당신은 당뇨병이 확실합니다.',
    '복용 중인 약을 지금부터 중단하세요.',
    '원문에 없는 복용량으로 변경해서 복용하세요.',
    '이 결과만으로 진단할 수는 없지만 당신은 당뇨병이 확실합니다.',
    '약물 변경을 지시하는 것은 아닙니다. 하지만 약을 중단하세요.',
])
def test_scenario_d_unsafe_assertions_not_excused_by_limitations(sentence):
    payload=fc.build_facts(HEALTH)
    generated=natural_report(payload)
    generated['summary'][0]['text']=sentence
    with pytest.raises(fc.ExplanationError) as error: fc.validate_plan(generated,payload)
    assert error.value.validation_rule=='document_medical_claim'


@pytest.mark.parametrize('data,sentence,rule',[
    (HEALTH,'공복혈당은 참고범위 안에 있습니다.','document_comparison'),
    (HEALTH,'공복혈당은 기준보다 낮습니다.','document_comparison'),
    ({'health_checkup_results':[{'test_item_name':'공복혈당(mg/dL)','result_value':'80','reference_range':'100미만'}]},
     '공복혈당은 문서의 참고범위를 벗어났습니다.','document_comparison'),
    ({'health_checkup_results':[{'test_item_name':'허리둘레(cm)','result_value':'91','reference_range':'남 90이상 여 85이상'}]},
     '허리둘레는 기준보다 높습니다.','unknown_comparison'),
    ({'health_checkup_results':[{'test_item_name':'공복혈당(mg/dL)','result_value':'108','reference_range':'100미만','remark':'정상'}]},
     '공복혈당은 참고범위 안에 있습니다.','document_conflict'),
])
def test_structured_comparison_blocks_only_explicit_contradictions(data,sentence,rule):
    payload=fc.build_facts(data)
    generated=plan(payload)
    generated['summary'][0]={'text':sentence,'evidence':[f['id'] for f in payload['facts']]}
    with pytest.raises(fc.ExplanationError) as error: fc.validate_plan(generated,payload)
    assert error.value.validation_rule==rule


def test_conflict_stays_code_owned_without_mandatory_summary_label():
    data={'health_checkup_results':[{'test_item_name':'공복혈당(mg/dL)','result_value':'108','reference_range':'100미만','remark':'정상'}]}
    payload=fc.build_facts(data)
    generated=plan(payload)
    generated['summary'][0]={'text':'공복혈당의 원문 표기와 수치를 함께 살펴보는 문서 읽기 안내입니다.','evidence':['0:result','0:remark']}
    assert fc.validate_plan(generated,payload)
    assert next(f for f in payload['facts'] if f['field']=='comparison')['conflict']


def test_scenario_e_paraphrased_gateway_response_reaches_final_ui_and_quiz(monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from carebridge.domain import normalize
    from carebridge.records import record
    payload=fc.build_facts(HEALTH)
    generated=natural_report(payload)
    transport=Mock()
    transport.post.return_value=response(payload,choices=[{'finish_reason':'stop','message':{'content':json.dumps(generated,ensure_ascii=False)}}])
    monkeypatch.setattr(fc.requests,'Session',lambda:transport)
    kind,clean=normalize(HEALTH)
    original=deepcopy(clean)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=15).run()
    app.session_state['records']=[record(kind,clean,'synthetic.pdf','test',kind)]
    app.sidebar.radio[0].set_value('건강검진').run()
    open_explanation(app)
    next(c for c in app.checkbox if 'Gateway 전송에 동의' in c.label).check().run()
    assert transport.post.call_count==0
    next(b for b in app.button if b.label=='동의한 정보로 설명 생성').click().run()
    assert not app.exception and not app.warning
    assert any(all(part['text'] in m.value for part in generated['summary']) for m in app.markdown)
    table=next(m.value for m in app.markdown if '<table aria-label="건강검진 결과"' in m.value)
    assert '문서 기준 초과' in table and '108' in table and '100 미만' in table
    questions=[r for r in app.radio if r.label.startswith(('1. ','2. ','3. '))]
    bank={q['id']:q for q in payload['understanding_questions']}
    questions[0].set_value(bank[generated['quiz'][0]['question_id']]['answer']).run()
    assert any(c.value=='정답입니다.' for c in app.caption)
    app.run()
    assert transport.post.call_count==1 and app.session_state['records'][0]['data']==original


def test_health_equivalent_decimal_spelling_is_data_consistent():
    payload=fc.build_facts(HEALTH)
    generated=natural_report(payload)
    generated['summary'][0]['text']='공복혈당 측정값 108.0 mg/dL은 문서 참고기준 100.0 미만과 함께 살펴볼 항목입니다.'
    assert fc.validate_plan(generated,payload)


def test_health_quantities_cannot_be_borrowed_from_another_cited_item():
    payload=fc.build_facts(HEALTH)
    generated=natural_report(payload)
    generated['summary'][0]={'text':'공복혈당 26.8 kg/m²로 기록되어 있습니다.','evidence':[f['id'] for f in payload['facts']]}
    with pytest.raises(fc.ExplanationError) as error: fc.validate_plan(generated,payload)
    assert error.value.validation_rule=='document_quantity'


@pytest.mark.parametrize('sentence',[
    '당신은 당뇨병입니다라는 사실에 의심이 없습니다.',
    '복용 중인 약을 중단하세요라는 지시에 이견이 없습니다.',
])
def test_unrelated_negative_words_do_not_negate_diagnoses_or_directives(sentence):
    payload=fc.build_facts(HEALTH)
    generated=natural_report(payload)
    generated['summary'][0]['text']=sentence
    with pytest.raises(fc.ExplanationError) as error: fc.validate_plan(generated,payload)
    assert error.value.validation_rule=='document_medical_claim'


def test_grounded_coordinated_measurements_are_not_assigned_to_last_named_item():
    payload=fc.build_facts(HEALTH)
    generated=natural_report(payload)
    generated['summary'][0]={'text':'BMI와 공복혈당의 측정값은 각각 26.8 kg/m²와 108 mg/dL로 적혀 있습니다.',
                             'evidence':[f['id'] for f in payload['facts']]}
    assert fc.validate_plan(generated,payload)


@pytest.mark.parametrize('sentence',[
    '공복혈당은 참고기준을 충족한다고 단정할 수 없습니다.',
    '공복혈당이 참고범위 안에 들어가는지 판단하려면 문서 기준을 함께 읽어야 합니다.',
])
def test_comparison_discussion_is_not_an_asserted_opposite_verdict(sentence):
    payload=fc.build_facts(HEALTH)
    generated=natural_report(payload)
    generated['summary'][0]['text']=sentence
    assert fc.validate_plan(generated,payload)


def test_original_overweight_category_cannot_be_replaced_with_underweight():
    payload=fc.build_facts(HEALTH)
    generated=natural_report(payload)
    generated['summary'][1]['text']='BMI는 저체중입니다.'
    with pytest.raises(fc.ExplanationError) as error: fc.validate_plan(generated,payload)
    assert error.value.validation_rule=='document_comparison'
    generated['summary'][1]['text']='BMI는 문서에 기재된 과체중에 해당합니다.'
    assert fc.validate_plan(generated,payload)


def test_named_document_normality_is_not_whole_person_clinical_certainty():
    data={'health_checkup_results':[{'test_item_name':'공복혈당(mg/dL)','result_value':'80','reference_range':'100미만'}]}
    payload=fc.build_facts(data)
    generated=plan(payload)
    generated['summary'][0]={'text':'공복혈당은 문서의 참고기준에서 정상입니다.','evidence':['0:result','0:reference']}
    assert fc.validate_plan(generated,payload)
    for text in ['공복혈당은 정상입니다.','공복혈당의 문서 기준을 보면 당신은 정상입니다.','공복혈당은 비정상입니다.']:
        generated['summary'][0]['text']=text
        with pytest.raises(fc.ExplanationError): fc.validate_plan(generated,payload)


def summary_variant(generated,variant):
    result=deepcopy(generated)
    parts=result['summary']
    ids=list(dict.fromkeys(fid for part in parts for fid in part['evidence']))
    if variant=='one_block':
        result['summary']=[{'text':' '.join(p['text'] for p in parts),'evidence':ids}]
    elif variant=='no_periods':
        result['summary']=[{'text':p['text'].rstrip('.!?'),'evidence':p['evidence']} for p in parts]
    elif variant=='unpunctuated':
        result['summary']=[{'text':' '.join(p['text'].rstrip('.!?') for p in parts),'evidence':ids}]
    elif variant=='paragraph':
        result['summary']=[{'text':'\n'.join(p['text'].rstrip('.!?') for p in parts),'evidence':ids}]
    elif variant=='two_blocks':
        result['summary']=[{'text':' '.join(p['text'] for p in parts[:2]),'evidence':ids},parts[2]]
    return result


@pytest.mark.parametrize('variant',['original','one_block','no_periods','unpunctuated','paragraph','two_blocks'])
def test_flexible_summary_reaches_streamlit_without_punctuation_contract(monkeypatch,variant):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from carebridge.domain import normalize
    from carebridge.records import record
    from carebridge.health_report import LIFESTYLE
    from carebridge.explanation_ui import HEALTH_NOTICE as AI_NOTICE
    payload=fc.build_facts(HEALTH)
    original=natural_report(payload)
    generated=summary_variant(original,variant)
    assert fc.validate_plan(generated,payload)==generated
    transport=Mock()
    transport.post.return_value=response(payload,choices=[{'finish_reason':'stop','message':{'content':json.dumps(generated,ensure_ascii=False)}}])
    monkeypatch.setattr(fc.requests,'Session',lambda:transport)
    kind,clean=normalize(HEALTH)
    snapshot=deepcopy(clean)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=15).run()
    app.session_state['records']=[record(kind,clean,'synthetic.pdf','test',kind)]
    app.sidebar.radio[0].set_value('건강검진').run()
    open_explanation(app)
    next(c for c in app.checkbox if 'Gateway 전송에 동의' in c.label).check().run()
    assert transport.post.call_count==0
    next(b for b in app.button if b.label=='동의한 정보로 설명 생성').click().run()
    assert not app.exception and not app.warning
    body='\n'.join(m.value for m in app.markdown)
    assert all(part['text'].rstrip('.!?') in body for part in original['summary'])
    assert any(c.value==AI_NOTICE for c in app.caption)
    for guide in LIFESTYLE.values():
        assert guide['text'] in body and guide['url'] in body
    assert [m.value for m in app.markdown if m.value.startswith('### ')]==[
        '### AI 핵심 요약','### 어려운 검사 용어 설명','### 검사 결과 의미 해설','### 생활습관 정보 제공','### 상세 검진 수치','### 문서 이해도 확인']
    assert len([r for r in app.radio if r.label.startswith(('1. ','2. ','3. '))])>=2
    app.run()
    assert transport.post.call_count==1 and app.session_state['records'][0]['data']==snapshot


@pytest.mark.parametrize('danger,rule',[
    ('공복혈당 측정값은 999mg/dL입니다.','document_number'),
    ('공복혈당 측정값은 108g/dL입니다.','document_quantity'),
    ('공복혈당 참고기준은 100이상입니다.','document_reference_condition'),
    ('당신은 당뇨병이 확실합니다.','document_medical_claim'),
    ('복용 중인 약을 지금부터 중단하세요.','document_medical_claim'),
])
def test_multisentence_summary_does_not_bypass_grounding_or_safety(danger,rule):
    payload=fc.build_facts(HEALTH)
    generated=summary_variant(natural_report(payload),'one_block')
    generated['summary'][0]['text']+=' '+danger
    with pytest.raises(fc.ExplanationError) as error: fc.validate_plan(generated,payload)
    assert error.value.validation_rule==rule


def test_summary_evidence_and_length_still_required_without_sentence_count():
    payload=fc.build_facts(HEALTH)
    generated=summary_variant(natural_report(payload),'one_block')
    for ids in [[],['invented-id']]:
        generated['summary'][0]['evidence']=ids
        with pytest.raises(fc.ExplanationError) as error: fc.validate_plan(generated,payload)
        assert error.value.validation_rule=='document_evidence'
    generated=summary_variant(natural_report(payload),'one_block')
    generated['summary'][0]['text']='문서의 항목을 함께 확인해 볼 필요가 있습니다. '*30
    with pytest.raises(fc.ExplanationError,match='너무 깁니다'): fc.validate_plan(generated,payload)


def test_who_templates_and_schema_are_static_and_match_summary_contract():
    from carebridge.output_contract import response_format
    from carebridge.health_report import LIFESTYLE
    assert {g['url'] for g in LIFESTYLE.values()}=={
        'https://www.who.int/en/news-room/fact-sheets/detail/healthy-diet',
        'https://www.who.int/europe/news-room/fact-sheets/item/physical-activity'}
    schema=response_format(fc.build_facts(HEALTH))['json_schema']['schema']['properties']
    assert schema['summary']['minItems']==1 and schema['summary']['maxItems']==5
    assert set(schema)=={'summary','quiz'}
    # Health flexibility never changes the medication output or safety contract.
    med=response_format(fc.build_facts(MEDS))['json_schema']['schema']['properties']
    assert med['summary']['minItems']==2 and med['summary']['maxItems']==3


def test_summary_only_contract_round_trip_allows_multiple_document_items():
    from carebridge.output_contract import response_format
    from carebridge.explanation_content import HEALTH_SYSTEM
    payload=fc.build_facts(HEALTH)
    generated=summary_variant(natural_report(payload),'one_block')
    parsed=fc.parse_generated_content(json.dumps(generated,ensure_ascii=False))
    assert set(parsed)=={'summary','quiz'}
    assert fc.validate_plan(parsed,payload)==generated
    schema=response_format(payload)['json_schema']['schema']
    assert set(schema['required'])==set(schema['properties'])=={'summary','quiz'}
    assert schema['additionalProperties'] is False
    assert '항목별 장문 설명(details)이나 생활습관(lifestyle) 필드는 생성하지 마라' in HEALTH_SYSTEM
    assert len({fid.split(':')[0] for fid in generated['summary'][0]['evidence']})==2
    # A legacy details block is not requested or silently rendered as health output.
    invalid=deepcopy(generated)
    invalid['details']=[generated['summary'][0]]
    with pytest.raises(fc.ExplanationError) as error: fc.validate_plan(invalid,payload)
    assert error.value.validation_rule=='response_schema'
    # Medication schema retains its original details and complete source checks.
    medication=response_format(fc.build_facts(MEDS))['json_schema']['schema']
    assert set(medication['required'])=={'summary','details','quiz'}
    assert medication['properties']['details']['minItems']==1


@pytest.mark.parametrize('ids',[['0:result'],['invented-id']])
def test_summary_with_unrelated_or_invalid_evidence_is_rejected(ids):
    raw={'health_checkup_results':[
        {'test_item_name':'혈색소','result_value':'15.1','result_unit':'g/dL','reference_range':'13~16.5'},
        {'test_item_name':'신장 검사','result_value':'90','result_unit':'mL/min','reference_range':'60이상'}]}
    payload=fc.build_facts(raw)
    generated=plan(payload)
    generated['summary'][0]={'text':'신장 검사는 문서의 측정값과 참고범위를 함께 살펴보는 항목입니다.','evidence':ids}
    with pytest.raises(fc.ExplanationError) as error: fc.validate_plan(generated,payload)
    assert error.value.validation_rule in ('document_test_term','document_evidence')


@pytest.mark.parametrize('failure',['json','number','medical','comparison','contract'])
def test_health_failure_preserves_basic_summary_who_and_table_without_retries(monkeypatch,caplog,failure):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from carebridge.domain import normalize
    from carebridge.records import record
    from carebridge.health_report import LIFESTYLE
    payload=fc.build_facts(HEALTH)
    generated=natural_report(payload)
    if failure=='json':
        content='not json PRIVATE_RAW_RESPONSE'
    else:
        if failure=='number': generated['summary'][0]['text']='공복혈당 측정값은 999mg/dL입니다.'
        if failure=='medical': generated['summary'][0]['text']='당신은 당뇨병이 확실합니다.'
        if failure=='comparison': generated['summary'][0]['text']='공복혈당은 참고범위 안에 있습니다.'
        if failure=='contract': generated['quiz'][0]={'question_id':'unverified-question'}
        content=json.dumps(generated,ensure_ascii=False)
    transport=Mock()
    transport.post.return_value=response(payload,choices=[{'finish_reason':'stop','message':{'content':content}}])
    monkeypatch.setattr(fc.requests,'Session',lambda:transport)
    kind,clean=normalize(HEALTH)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=15).run()
    app.session_state['records']=[record(kind,clean,'synthetic.pdf','test',kind)]
    app.sidebar.radio[0].set_value('건강검진').run()
    open_explanation(app)
    next(c for c in app.checkbox if 'Gateway 전송에 동의' in c.label).check().run()
    next(b for b in app.button if b.label=='동의한 정보로 설명 생성').click().run()
    app.run()
    assert not app.exception and not app.warning and not app.error
    assert any(i.value=='AI 설명을 생성하지 못해 기본 요약을 표시합니다' for i in app.info)
    body='\n'.join(m.value for m in app.markdown)
    assert any('기본 문서 요약' in c.value for c in app.caption) and '### 기본 문서 요약' in body
    assert '<table aria-label="건강검진 결과"' in body and '문서 기준 초과' in body
    assert '### AI 건강검진 해설' not in body
    for guide in LIFESTYLE.values(): assert guide['text'] in body and guide['url'] in body
    assert transport.post.call_count==1
    assert not any(b.label=='동의한 정보로 설명 생성' for b in app.button)
    expected={'json':'response_json','number':'document_number','medical':'document_medical_claim',
              'comparison':'document_comparison','contract':'quiz_candidate'}[failure]
    assert 'state=rejected rule='+expected in caplog.text
    assert 'section=' in caplog.text
    for private in ['PRIVATE_RAW_RESPONSE','가상성명','900101','test-only-secret','999mg/dL']:
        assert private not in caplog.text
    assert app.session_state['records'][0]['data']==clean


@pytest.mark.parametrize('name,key',[
    ('BMI','BMI'),('공복혈당','공복혈당'),('HDL-콜레스테롤','HDL'),('LDL-C','LDL'),
    ('e-GFR','EGFR'),('추정 사구체 여과율(eGFR)','EGFR'),('AST(GOT)','AST'),('ALT','ALT')])
def test_reviewed_terms_have_explicit_fixed_definitions(name,key):
    from carebridge.explanation_content import term_definition,GLOSSARY
    assert term_definition(name)==GLOSSARY[key]
    assert term_definition('이름을 모르는 검사') is None


@pytest.mark.parametrize('remark,contains',[
    ('기왕력: 고혈압 치료 중','기존 질환 기록'),
    ('신규 고혈압 의심','신규 의심 소견'),
    ('고혈압 의심','신규 여부 확인 필요'),
    ('기존 질환 치료 중, 다른 소견 의심','구분 확인 필요'),
    ('과거력 없음','원문 판정·참고기준'),
])
def test_document_history_suspicion_and_numeric_comparison_stay_distinct(remark,contains):
    from carebridge.checkup_reading import result_context
    assert contains in result_context({'remark':remark,'test_item_name':'검사','result_value':'1','reference_range':'0~2'})
    assert '수치 차이' in result_context({'test_item_name':'검사','result_value':'3','reference_range':'0~2'})


def test_explicit_opinion_survives_record_export_but_private_identifiers_do_not_leave():
    from carebridge.domain import normalize
    from carebridge.records import record,export_records,import_records
    from carebridge.checkup_reading import document_opinions,related_guides
    data=deepcopy(HEALTH)
    data['overall_impression']='가상성명님 원문의 참고기준과 수치를 함께 확인하세요.'
    kind,clean=normalize(data)
    saved=record(kind,clean,'synthetic.pdf','test',kind)
    restored=import_records(export_records([saved]).encode())[0]
    assert document_opinions(restored['data'])==[data['overall_impression']]
    payload=fc.build_facts(restored['data'])
    opinions=[f for f in payload['facts'] if f['item']=='원문 종합 소견']
    assert len(opinions)==1 and not opinions[0]['quiz_allowed']
    assert '가상성명' not in json.dumps(payload,ensure_ascii=False)
    assert payload['scope']=='uploaded_document_only'
    assert related_guides(clean)==['food','activity']
    renal={'health_checkup_results':[{'test_item_name':'e-GFR'}]}
    assert not related_guides(renal) and not document_opinions(renal)


@pytest.mark.parametrize('with_opinion',[False,True])
def test_final_mvp_health_reading_partial_document_and_optional_quiz(monkeypatch,with_opinion):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from carebridge.domain import normalize
    from carebridge.records import record
    data=deepcopy(HEALTH) if with_opinion else {'health_checkup_results':[
        {'test_item_name':'HDL(mg/dL)','result_value':'42','reference_range':'60이상'},
        {'test_item_name':'e-GFR','result_value':'90','result_unit':'mL/min','reference_range':'60이상'},
        {'test_item_name':'AST','result_value':'20','result_unit':'U/L','reference_range':'0~40'},
        {'test_item_name':'ALT','result_value':'22','result_unit':'U/L','reference_range':'0~40'}]}
    if with_opinion: data['overall_impression']='문서의 참고기준과 결과를 함께 확인하세요.'
    payload=fc.build_facts(data)
    generated=natural_report(payload) if with_opinion else plan(payload)
    if not with_opinion:
        ids=[f['id'] for f in payload['facts']]
        generated['summary']=[{'text':text,'evidence':ids} for text in [
            '업로드된 문서의 HDL은 문서 참고기준과 차이가 있어 함께 살펴볼 항목입니다.',
            'e-GFR은 콩팥이 혈액을 걸러내는 정도를 추정하는 값으로 이해할 수 있습니다.',
            '이 자료만으로 특정 질병을 진단할 수는 없습니다.']]
    transport=Mock()
    transport.post.return_value=response(payload,choices=[{'finish_reason':'stop','message':{'content':json.dumps(generated,ensure_ascii=False)}}])
    monkeypatch.setattr(fc.requests,'Session',lambda:transport)
    kind,clean=normalize(data)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=15).run()
    app.session_state['records']=[record(kind,clean,'synthetic.pdf','test',kind)]
    app.sidebar.radio[0].set_value('건강검진').run()
    open_explanation(app)
    next(c for c in app.checkbox if 'Gateway 전송에 동의' in c.label).check().run()
    next(b for b in app.button if b.label=='동의한 정보로 설명 생성').click().run()
    assert not app.exception and not app.warning
    body='\n'.join(m.value for m in app.markdown)
    assert app.subheader[0].value=='AI 건강검진 해설' and '### AI 핵심 요약' in body
    assert all(part['text'] in body for part in generated['summary'])
    assert any('전체 검진이나 모든 페이지' in c.value for c in app.caption)
    quiz=next(e for e in app.expander if e.label=='이해도 확인 (선택)')
    assert not quiz.proto.expanded
    assert len(quiz.radio)>=2
    if with_opinion:
        assert next(e for e in app.expander if e.label=='원문 종합 소견 확인').text[0].value==data['overall_impression']
    else:
        assert 'HDL' in body and 'e-GFR' in body and 'AST' in body and 'ALT' in body
        assert '<strong>LDL' not in body
        assert not any(e.label=='원문 종합 소견 확인' for e in app.expander)
    app.run()
    assert transport.post.call_count==1 and app.session_state['records'][0]['data']==clean


def test_medication_uses_no_guessed_purpose_and_blocks_mixed_source_registration():
    from pathlib import Path
    from datetime import date
    from streamlit.testing.v1 import AppTest
    from carebridge.domain import normalize,make_schedule
    from carebridge.records import record
    data=deepcopy(MEDS)
    data['medication_instructions'][0]['source_excerpt']='추출 내용에 두 약품 정보 혼합'
    kind,clean=normalize(data)
    row=clean['medication_instructions'][0]
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=15).run()
    app.session_state['records']=[record(kind,clean,'synthetic.pdf','test',kind)]
    app.sidebar.radio[0].set_value('복약 관리').run()
    table=next(m.value for m in app.markdown if '<table aria-label="복약안내문 약품 목록"' in m.value)
    assert '약품 용도 확인 필요' in table and row['caution_notes'] in table
    app.toggle[0].set_value(True).run()
    next(t for t in app.text_input if t.label=='확인한 하루 복용 횟수').set_value('3').run()
    next(t for t in app.text_input if t.label.startswith('알림 시각')).set_value('09:00,13:00,18:00').run()
    next(c for c in app.checkbox if '일정 저장을 승인' in c.label).check().run()
    assert next(b for b in app.button if b.label=='확인한 내용으로 일정 저장').disabled
    assert not app.session_state['schedules'] and not app.exception
    with pytest.raises(ValueError,match='혼합'):
        make_schedule({**row,'source_excerpt':''},date(2026,10,16),['09:00','13:00','18:00'],True,original=row)


def test_health_four_cards_hide_controls_tables_and_quiz_until_opened(monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from carebridge.domain import normalize
    from carebridge.records import record
    from carebridge.explanation_ui import HEALTH_NOTICE
    payload=fc.build_facts(HEALTH)
    generated=natural_report(payload)
    transport=Mock()
    transport.post.return_value=response(payload,choices=[{'finish_reason':'stop','message':{'content':json.dumps(generated,ensure_ascii=False)}}])
    monkeypatch.setattr(fc.requests,'Session',lambda:transport)
    kind,clean=normalize(HEALTH)
    item=record(kind,clean,'synthetic.pdf','test',kind)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=15).run()
    app.session_state['records']=[item]
    app.sidebar.radio[0].set_value('건강검진').run()
    def visible(node):
        yield node
        if node.type=='expander': return
        for child in getattr(node,'children',{}).values(): yield from visible(child)
    headings=['### AI 핵심 요약','### 어려운 검사 용어 설명','### 검사 결과 의미 해설','### 생활습관 정보 제공']
    def assert_simple_page(generated=False):
        nodes=list(visible(app.main))
        shown=[n.value for n in nodes if n.type=='markdown']
        assert [value for value in shown if value.startswith('### ')]==([headings[0] if generated else '### 기본 문서 요약']+headings[1:])
        assert not any('<table' in value for value in shown)
        assert not any(n.type in ('dataframe','json','radio','checkbox') for n in nodes)
        assert not any('원본 문서명' in n.value for n in nodes if n.type=='caption')
        assert app.subheader[0].value=='AI 건강검진 해설'
        assert any(c.value==HEALTH_NOTICE for c in app.caption)
        labels=['상세 검사 결과 · 원문 대조']
        if generated:
            assert not any(e.label=='AI 설명 생성 · 외부 전송 동의' for e in app.expander)
            assert not any(b.label=='동의한 정보로 설명 생성' for b in app.button)
        else:
            labels.append('AI 설명 생성 · 외부 전송 동의')
        for label in labels:
            assert not next(e for e in app.expander if e.label==label).proto.expanded
    assert_simple_page()
    assert transport.post.call_count==0
    assert not any(b.label=='쉬운 설명 보기' for b in app.button)
    generate=next(b for b in app.button if b.label=='동의한 정보로 설명 생성')
    assert generate.disabled
    consent=next(e for e in app.expander if e.label=='AI 설명 생성 · 외부 전송 동의')
    assert any('Gateway 전송에 동의' in c.label for c in consent.checkbox)
    details=next(e for e in app.expander if e.label=='상세 검사 결과 · 원문 대조')
    assert any('<table aria-label="건강검진 결과"' in m.value for m in details.markdown)
    next(c for c in consent.checkbox if 'Gateway 전송에 동의' in c.label).check().run()
    assert transport.post.call_count==0
    next(b for b in app.button if b.label=='동의한 정보로 설명 생성').click().run()
    assert not app.exception
    assert_simple_page(generated=True)
    shown='\n'.join(n.value for n in visible(app.main) if n.type=='markdown')
    assert all(part['text'] in shown for part in generated['summary'])
    for label in ['요약 근거','이해도 확인 (선택)']:
        assert not next(e for e in app.expander if e.label==label).proto.expanded
    app.run()
    assert transport.post.call_count==1 and app.session_state['records'][0]['data']==clean
