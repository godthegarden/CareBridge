"""Offline, first-session upload -> consent -> extract -> explanation acceptance tests."""
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock
import pytest
import requests
import streamlit as st
from streamlit.testing.v1 import AppTest
from carebridge import factchat as fc
from carebridge import client
from carebridge.analysis_flow import set_upload,queue,run_queued

ROOT=Path(__file__).resolve().parents[1]
HEALTH={'examinee_name':'가상성명','overall_impression':'가상성명 900101-1234567 비공개 소견',
        'health_checkup_results':[
        {'test_item_name':'BMI(kg/m²)','result_value':'26.8','reference_range':'과체중(25-29.9)','source_excerpt':'PRIVATE SOURCE'},
        {'test_item_name':'공복혈당(mg/dL)','result_value':'108','reference_range':'100미만','remark':'가상성명 전화 010-1234-5678'}]}

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('FACTCHAT_API_KEY','fake-test-only')
    monkeypatch.setenv('FACTCHAT_MODEL','solar-mini4')
    monkeypatch.setenv('FACTCHAT_MAX_REQUESTS','1')
    monkeypatch.setenv('FACTCHAT_RESPONSE_FORMAT','json_schema')
    monkeypatch.setattr(fc,'_requests',0)
    monkeypatch.setattr(fc,'_last_request',None)
    monkeypatch.setattr(fc,'load_dotenv',lambda *a,**kw:None)
    def blocked(*args,**kwargs): raise AssertionError('Actual HTTP forbidden')
    monkeypatch.setattr(requests.sessions.Session,'request',blocked)


def complete_job(data=HEALTH):
    return {'id':'synthetic-job','status':'completed','output':[
        {'step_type':'document-classify','status':'completed','content':[{'type':'output_text','text':'health_checkup_result'}]},
        {'step_type':'information-extract','status':'completed','content':[{'type':'output_text','text':json.dumps(data,ensure_ascii=False)}]}]}


def mock_gateway(monkeypatch,unsafe=False):
    transport=Mock()
    def respond(*args,**kwargs):
        payload=json.loads(kwargs['json']['messages'][1]['content'])
        ids=[f['id'] for f in payload['facts']]
        texts=['이번 자료에서는 BMI와 공복혈당의 문서 기준을 함께 살펴볼 필요가 있습니다.',
               '공복혈당은 음식을 먹지 않은 상태에서 혈액 속 포도당의 양을 확인하는 값입니다.',
               '이 결과만으로 특정 질환을 진단할 수는 없습니다.']
        if unsafe: texts[0]='당신은 당뇨병이 확실합니다.'
        bank=payload['understanding_questions']
        selected=[next(q['id'] for q in bank if q['theme']==theme) for theme in ('relationship','limits')]
        plan={'summary':[{'text':text,'evidence':ids} for text in texts],
              'quiz':[{'question_id':qid} for qid in selected]}
        raw={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(plan,ensure_ascii=False)}}]}
        return Mock(status_code=200,json=Mock(return_value=raw))
    transport.post.side_effect=respond
    monkeypatch.setattr(fc.requests,'Session',lambda:transport)
    return transport


def start_first_session(monkeypatch,*,agree=True,unsafe=False):
    gateway=mock_gateway(monkeypatch,unsafe)
    agent=Mock(cleanup_warning=False)
    def analyze(data,progress):
        assert st.session_state.analysis_flow['state']=='running'
        assert st.session_state.active_page=='문서 분석'
        assert st.session_state.analysis_upload['data']==data
        assert gateway.post.call_count==0
        progress('Extract · 정보 추출')
        return complete_job()
    agent.analyze.side_effect=analyze
    monkeypatch.setattr(client,'AgentClient',lambda:agent)
    # File picker boundary is stubbed; the complete Streamlit workflow is real.
    upload=Mock(name='synthetic.pdf')
    upload.name='synthetic.pdf'
    upload.getvalue.return_value=b'%PDF-synthetic-test-only'
    monkeypatch.setattr(st,'file_uploader',lambda *a,**kw:upload)
    app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=15).run()
    next(b for b in app.button if b.label=='새 문서 분석하기 →').click().run()
    assert not app.exception
    next(c for c in app.checkbox if c.label.startswith('필수 ·')).check().run()
    if agree: next(c for c in app.checkbox if c.label.startswith('선택 ·')).check().run()
    assert not agent.analyze.called and not gateway.post.called
    next(b for b in app.button if b.label=='AI 분석 및 해설 보기').click().run()
    return app,agent,gateway


def test_first_session_single_click_reaches_real_generated_card_and_reuses_response(monkeypatch):
    app,agent,gateway=start_first_session(monkeypatch)
    assert not app.exception and not app.error
    assert app.session_state['analysis_flow']['state']=='completed'
    assert agent.analyze.call_count==gateway.post.call_count==1
    assert any(m.value=='### AI 핵심 요약' for m in app.markdown)
    assert any('이번 자료에서는 BMI와 공복혈당' in m.value for m in app.markdown)
    assert not any(m.value=='### 기본 문서 요약' for m in app.markdown)
    assert next(b for b in app.button if b.label=='AI 분석 및 해설 보기').disabled
    assert app.session_state['active_page']=='문서 분석'
    body=json.dumps(gateway.post.call_args.kwargs['json'],ensure_ascii=False)
    for private in ['가상성명','900101','010-1234','PRIVATE SOURCE','비공개 소견','source_excerpt']:
        assert private not in body
    app.run()
    assert agent.analyze.call_count==gateway.post.call_count==1
    assert app.session_state['last_result_id']==app.session_state['records'][0]['id']
    assert app.session_state['analysis_upload']['data']==b'%PDF-synthetic-test-only'


def test_without_optional_consent_never_calls_factchat_and_shows_basic_not_ai(monkeypatch):
    app,agent,gateway=start_first_session(monkeypatch,agree=False)
    assert not app.exception and not gateway.post.called and agent.analyze.call_count==1
    assert any(m.value=='### 기본 문서 요약' for m in app.markdown)
    assert not any(m.value=='### AI 핵심 요약' for m in app.markdown)
    assert any('문서 기준 초과' in m.value for m in app.markdown)
    app.run()
    assert not gateway.post.called and agent.analyze.call_count==1


def test_ai_failure_keeps_record_basic_summary_and_collapsed_sources(monkeypatch):
    app,agent,gateway=start_first_session(monkeypatch,unsafe=True)
    assert not app.exception and len(app.session_state['records'])==1
    assert any(m.value=='### 기본 문서 요약' for m in app.markdown)
    assert not any(m.value=='### AI 핵심 요약' for m in app.markdown)
    assert app.session_state['analysis_flow']['explanation_state']=='error'
    detail=next(e for e in app.expander if e.label=='상세 검사 결과 · 원문 대조')
    assert not detail.proto.expanded and detail.markdown
    app.run()
    assert agent.analyze.call_count==gateway.post.call_count==1


def test_rerun_during_running_never_dispatches_second_job_and_interruption_is_explicit():
    state={'records':[],'originals':{},'stage_reviews':{},'selected_records':{}}
    set_upload(state,b'synthetic','a.pdf')
    queue(state,extract_consent=True,explanation_consent=False)
    seen=[]
    class Interrupted(BaseException): pass
    def progress(message):
        seen.append(state['analysis_flow']['state'])
        assert state['analysis_flow']['state']=='running'
        second=Mock()
        run_queued(state,second,lambda _:None)
        second.assert_not_called()
        queue(state,extract_consent=True,explanation_consent=True)
        assert not state['analysis_flow']['explanation_consent']
        raise Interrupted()
    with pytest.raises(Interrupted): run_queued(state,Mock(),progress)
    assert seen==['running'] and state['analysis_flow']['state']=='interrupted'
    assert state['analysis_upload']['data']==b'synthetic'
    second=Mock()
    run_queued(state,second,lambda _:None)
    second.assert_not_called()
    assert not state['records']


def test_widget_route_cleanup_and_new_memory_session_use_safe_recovery_route():
    app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=15)
    app.session_state['active_page']='문서 분석'
    app.session_state['analysis_upload']={'data':b'pending','filename':'synthetic.pdf','identity':'synthetic'}
    app.session_state['analysis_flow']={'state':'running','phase':'Extract · 정보 추출'}
    # Render a retained session model without its widget-owned route key.
    app.run()
    assert not app.exception and app.sidebar.radio[0].value=='문서 분석'
    assert next(b for b in app.button if b.label=='AI 분석 및 해설 보기').disabled
    assert app.session_state['analysis_upload']['data']==b'pending'
    fresh=AppTest.from_file(str(ROOT/'app.py'),default_timeout=15)
    fresh.query_params['view']='analysis'
    fresh.query_params['analysis']='started'
    fresh.run()
    assert not fresh.exception and fresh.sidebar.radio[0].value=='문서 분석'
    assert not fresh.session_state['records']
    assert any('메모리 세션' in w.value and '다시 선택' in w.value for w in fresh.warning)


def test_new_document_resets_progress_and_consent_without_deleting_records():
    state={'records':[{'id':'kept'}]}
    set_upload(state,b'first','first.pdf')
    queue(state,extract_consent=True,explanation_consent=True)
    state['analysis_flow'].update(state='completed',result_id='kept')
    state['last_result_id']='kept'
    queue(state,extract_consent=True,explanation_consent=True)
    assert state['analysis_flow']['state']=='completed'  # Double click is not a new run.
    set_upload(state,b'new','new.pdf')
    assert state['analysis_flow']['state']=='idle' and state['records']==[{'id':'kept'}]
    assert 'last_result_id' not in state and not state['analysis_factchat_agreed']
    assert not state['analysis_extract_agreed']


def test_closed_vocabulary_auto_transfer_drops_unknown_names_and_unreviewed_prose():
    raw=deepcopy(HEALTH)
    raw['health_checkup_results'].append({'test_item_name':'홍길동 비공개 검사','result_value':'12','reference_range':'0~20'})
    raw['health_checkup_results'][0]['reference_range']='정상 25미만 주소 비공개'
    payload=fc.build_facts(raw,safe_only=True)
    text=json.dumps(payload,ensure_ascii=False)
    for word in ['홍길동','가상성명','주소','비공개','900101','010-1234','PRIVATE SOURCE']:
        assert word not in text
    assert payload['privacy_mode']=='structured_only'
    assert raw['health_checkup_results'][0]['reference_range'].endswith('비공개')
