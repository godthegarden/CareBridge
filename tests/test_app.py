from streamlit.testing.v1 import AppTest

def test_empty_navigation():
    app=AppTest.from_file(str(__import__('pathlib').Path(__file__).resolve().parents[1]/'app.py'), default_timeout=15).run()
    assert not app.exception
    for page in ['문서 분석','건강검진','복약 관리','기록 포트폴리오','대시보드']:
        app.sidebar.radio[0].set_value(page).run()
        assert not app.exception

def test_actual_results_views_if_available():
    import json
    from pathlib import Path
    from carebridge.records import record
    app=AppTest.from_file(str(__import__('pathlib').Path(__file__).resolve().parents[1]/'app.py'), default_timeout=15).run()
    paths=list(Path('.local/live').glob('sample?.json'))
    if not paths:
        __import__('pytest').skip('실제 API 실행 결과가 없어 결과 화면 검증 생략')
    for p in paths:
        result=json.loads(p.read_text())
        from carebridge.domain import classify_job
        raw=p.with_name(p.stem+'-raw.json')
        classification=classify_job(json.loads(raw.read_text())) if raw.exists() else None
        app.session_state['records']=[record(result['kind'],result['data'],p.name,'integration',classification)]
        app.sidebar.radio[0].set_value('건강검진' if result['kind']=='health_checkup_result' else '복약 관리').run()
        assert not app.exception

def test_schedule_confirmation_in_ui():
    from carebridge.records import record
    from carebridge.domain import normalize
    kind,data=normalize({'medication_instructions':[{'medication_name':'테스트 약','single_dose':'1정','times_per_day':'1','days_of_administration':'2','administration_timing':'아침 식후','source_excerpt':'테스트용 원문'}]})
    app=AppTest.from_file(str(__import__('pathlib').Path(__file__).resolve().parents[1]/'app.py'), default_timeout=15).run()
    app.session_state['records']=[record(kind,data,'unit-test.pdf','test',classification=kind)]
    app.sidebar.radio[0].set_value('복약 관리').run()
    app.toggle[0].set_value(True).run()
    next(t for t in app.text_input if t.label=='확인한 하루 복용 횟수').set_value('1').run()
    app.text_input[-1].set_value('09:00')
    next(b for b in app.button if b.label=='확인한 내용으로 일정 저장').click().run()
    assert app.error and not app.session_state['schedules']
    app.checkbox[0].check()
    next(b for b in app.button if b.label=='확인한 내용으로 일정 저장').click().run()
    assert not app.exception and len(next(iter(app.session_state['schedules'].values()))['events'])==2


def test_result_and_medication_draft_survive_navigation():
    from carebridge.records import record
    from carebridge.domain import normalize
    kind,data=normalize({'medication_instructions':[{'medication_name':'테스트 약','single_dose':'1정','times_per_day':'1','days_of_administration':'2','administration_timing':'아침 식후','source_excerpt':'테스트용 원문'}]})
    item=record(kind,data,'test.pdf','test',classification=kind)
    app=AppTest.from_file(str(__import__('pathlib').Path(__file__).resolve().parents[1]/'app.py'),default_timeout=15).run()
    app.session_state['records']=[item]
    app.session_state['last_result_id']=item['id']
    app.sidebar.radio[0].set_value('문서 분석').run()
    app.toggle[0].set_value(True).run()
    next(t for t in app.text_input if t.label=='확인한 하루 복용 횟수').set_value('1').run()
    app.text_input[-1].set_value('09:00').run()
    next(c for c in app.checkbox if (c.key or '').endswith('confirmed')).check().run()
    assert app.session_state['records'][0]['data']==data
    assert not app.session_state['schedules']
    app.sidebar.radio[0].set_value('대시보드').run()
    app.sidebar.radio[0].set_value('복약 관리').run()
    assert not app.exception
    assert app.text_input[-1].value=='09:00'
    assert app.checkbox[0].value
    next(b for b in app.button if b.label=='확인한 내용으로 일정 저장').click().run()
    assert len(next(iter(app.session_state['schedules'].values()))['events'])==2
    app.sidebar.radio[0].set_value('문서 분석').run()
    assert not app.exception
    assert app.session_state['records'][0]['data']==data
    assert len(app.session_state['schedules'])==1


def test_health_summary_separates_uncomparable_and_outside():
    from carebridge.records import record
    from carebridge.domain import normalize
    kind,data=normalize({'health_checkup_results':[
        {'test_item_name':'항목 A','result_value':'15','reference_range':'1-10','source_excerpt':'A 15'},
        {'test_item_name':'항목 B','result_value':'5','reference_range':'1-10','source_excerpt':'B 5'},
        {'test_item_name':'항목 C','result_value':'미기재','reference_range':'','source_excerpt':'C'}]})
    app=AppTest.from_file(str(__import__('pathlib').Path(__file__).resolve().parents[1]/'app.py'),default_timeout=15).run()
    app.session_state['records']=[record(kind,data,'test.pdf','test',classification=kind)]
    app.sidebar.radio[0].set_value('건강검진').run()
    assert not app.exception
    table=next(m.value for m in app.markdown if '<table aria-label="건강검진 결과"' in m.value)
    assert '항목 A' in table and '항목 B' in table and '항목 C' in table
    assert '문서 범위 초과' in table and '확인 필요' in table
    assert not app.metric and not app.warning
    assert any('확인 필요 1개' in m.value for m in app.markdown)
    app.checkbox[0].check().run()
    table=next(m.value for m in app.markdown if '<table aria-label="건강검진 결과"' in m.value)
    assert '항목 A' in table and '항목 C' in table and '항목 B' not in table
    app.sidebar.radio[0].set_value('대시보드').run()
    app.sidebar.radio[0].set_value('건강검진').run()
    assert app.checkbox[0].value
    assert app.session_state['records'][0]['data']==data


def test_medication_edit_requires_confirmation_again():
    from carebridge.records import record
    from carebridge.domain import normalize
    kind,data=normalize({'medication_instructions':[{'medication_name':'테스트 약','single_dose':'1정','times_per_day':'1','days_of_administration':'2','administration_timing':'아침 식후','source_excerpt':'테스트용 원문'}]})
    app=AppTest.from_file(str(__import__('pathlib').Path(__file__).resolve().parents[1]/'app.py'),default_timeout=15).run()
    app.session_state['records']=[record(kind,data,'test.pdf','test',classification=kind)]
    app.sidebar.radio[0].set_value('복약 관리').run()
    app.toggle[0].set_value(True).run()
    next(t for t in app.text_input if t.label=='확인한 하루 복용 횟수').set_value('1').run()
    app.text_input[-1].set_value('09:00').run()
    app.checkbox[0].check().run()
    app.text_input[-1].set_value('10:00').run()
    assert not app.checkbox[0].value
    next(b for b in app.button if b.label=='확인한 내용으로 일정 저장').click().run()
    assert app.error and not app.session_state['schedules']


def test_medication_dashboard_is_compact_and_requires_opt_in():
    from carebridge.records import record
    from carebridge.domain import normalize
    name='긴 약품명 '+'가나다라마'*20
    kind,data=normalize({'medication_instructions':[
        {'medication_name':name,'single_dose':'1정','times_per_day':'3','days_of_administration':'5일','administration_timing':'','caution_notes':'원문 주의 문구','source_excerpt':'원문 횟수 3'},
        {'medication_name':'두 번째 약','single_dose':'OCR 불확실','times_per_day':'2','days_of_administration':'7일','administration_timing':'식후','source_excerpt':'원문 횟수 2'}]})
    app=AppTest.from_file(str(__import__('pathlib').Path(__file__).resolve().parents[1]/'app.py'),default_timeout=15).run()
    app.session_state['records']=[record(kind,data,'test.pdf','test',classification=kind)]
    app.sidebar.radio[0].set_value('복약 관리').run()
    assert not app.exception
    assert not app.metric
    summary=next(m.value for m in app.markdown if 'class="result-summary"' in m.value)
    assert all(value in summary for value in ['2개','4건'])
    assert not app.text_input and not app.date_input
    assert not app.json and not app.get('imgs')
    assert not app.session_state['schedules']
    assert not any(b.label=='일정 CSV 내려받기' for b in app.get('download_button'))
    assert any(name in m.value for m in app.markdown)
    table=next(m.value for m in app.markdown if '<table aria-label="복약안내문 약품 목록"' in m.value)
    assert '확인 필요' in table and '원문 주의 문구' in table
    app.toggle[0].set_value(True).run()
    assert not app.exception
    assert len(app.text_input)==6
    frequency=next(t for t in app.text_input if t.label=='확인한 하루 복용 횟수')
    assert frequency.value==''
    app.text_input[-1].set_value('09:00').run()
    app.checkbox[0].check().run()
    next(b for b in app.button if b.label=='확인한 내용으로 일정 저장').click().run()
    assert app.error and not app.session_state['schedules']
    next(s for s in app.selectbox if s.label=='일정을 만들 약품').set_value(1).run()
    assert not app.exception
    assert next(t for t in app.text_input if t.label=='확인한 약품명').value=='두 번째 약'

    app.text_input[-1].set_value('10:00').run()
    app.toggle[0].set_value(False).run()
    assert not app.text_input
    app.sidebar.radio[0].set_value('대시보드').run()
    app.sidebar.radio[0].set_value('복약 관리').run()
    app.toggle[0].set_value(True).run()
    assert not app.exception
    assert next(s for s in app.selectbox if s.label=='일정을 만들 약품').value==1
    assert app.text_input[-1].value=='10:00'


@__import__('pytest').mark.parametrize('classification,title,group',[
    ('medication_guidance_record','복약안내문 분석 결과','medication_instructions'),
    ('health_checkup_result','건강검진 결과 분석','health_checkup_results'),
    (None,'문서 유형 확인 필요','medication_instructions'),
    ('unsupported','문서 유형 확인 필요','health_checkup_results'),
])
def test_analysis_title_uses_classify_not_extracted_title(classification,title,group):
    from carebridge.records import record
    from carebridge.domain import normalize
    kind,data=normalize({group:[{}]})
    # Deliberately omit optional fields and use a misleading original title.
    data={'record_document_title':'검진이라는 원본 문서명','document_title':'복약이라는 원본 문서명',group:[{}]}
    item=record(kind,data,'misleading-filename.pdf','test',classification)
    app=AppTest.from_file(str(__import__('pathlib').Path(__file__).resolve().parents[1]/'app.py'),default_timeout=15).run()
    app.session_state['records']=[item]
    app.session_state['last_result_id']=item['id']
    app.sidebar.radio[0].set_value('문서 분석').run()
    assert not app.exception
    assert title in [s.value for s in app.subheader]
    assert not any('원본 문서명' in s.value for s in app.subheader)
    assert any('원본 문서명' in c.value for c in app.caption)
    if title=='문서 유형 확인 필요':
        assert not app.metric and not app.text_input and not app.toggle
    elif group=='medication_instructions':
        assert any(m.value=='### 01. 약품별 복약정보' for m in app.markdown)
    else:
        assert any(m.value=='### 검사항목별 결과' for m in app.markdown)


def test_classification_mismatch_and_empty_extraction_are_safe():
    from carebridge.records import record
    app=AppTest.from_file(str(__import__('pathlib').Path(__file__).resolve().parents[1]/'app.py'),default_timeout=15).run()
    item=record('medication_guidance_record',{'medication_instructions':[{}]},'test.pdf','test','health_checkup_result')
    app.session_state['records']=[item]
    app.session_state['last_result_id']=item['id']
    app.sidebar.radio[0].set_value('문서 분석').run()
    assert not app.exception and app.subheader[0].value=='문서 유형 확인 필요'
    assert not app.metric and not app.toggle
    item=record('medication_guidance_record',{},'test.pdf','test','medication_guidance_record')
    app.session_state['records']=[item]
    app.session_state['last_result_id']=item['id']
    app.run()
    assert not app.exception and app.subheader[0].value=='복약안내문 분석 결과'
    assert app.warning and not app.toggle


@__import__('pytest').mark.parametrize('classified,title',[
    ('medication_guidance_record','복약안내문 분석 결과'),
    ('unsupported','문서 유형 확인 필요'),
])
def test_analysis_submission_preserves_actual_classify(monkeypatch,classified,title):
    import json
    from carebridge import client
    class FakeClient:
        cleanup_warning=False
        def analyze(self,data,progress):
            return {'id':'test-job','status':'completed','output':[
                {'step_type':'document-classify','status':'completed','content':[{'type':'output_text','text':classified}]},
                {'step_type':'information-extract','status':'completed','content':[{'type':'output_text','text':json.dumps({'medication_instructions':[{'medication_name':'테스트 약'}]})}]}]}
    monkeypatch.setattr(client,'AgentClient',FakeClient)
    app=AppTest.from_file(str(__import__('pathlib').Path(__file__).resolve().parents[1]/'app.py'),default_timeout=15).run()
    app.sidebar.radio[0].set_value('문서 분석').run()
    next(r for r in app.radio if r.label=='문서 선택 방식').set_value('가상 샘플 체험').run()
    app.checkbox[0].check().run()
    next(b for b in app.button if b.label=='AI 분석 시작').click().run()
    assert not app.exception
    saved=app.session_state['records'][0]
    assert saved['classification']==('medication_guidance_record' if classified=='medication_guidance_record' else None)
    assert saved['data']['medication_instructions'][0]['medication_name']=='테스트 약'
    assert title in [s.value for s in app.subheader]


@__import__('pytest').mark.parametrize('sample,page,label',[
    (1,'건강검진','건강검진 결과'),(4,'복약 관리','복약안내문 약품 목록')])
def test_saved_agent_samples_render_compact_results(sample,page,label):
    import json
    from pathlib import Path
    from carebridge.domain import analysis_record_data,GROUPS
    from carebridge.records import record
    path=Path(f'.local/live/sample{sample}-raw.json')
    if not path.exists(): __import__('pytest').skip('저장된 샘플 응답 없음')
    kind,data,classification=analysis_record_data(json.loads(path.read_text()))
    item=record(kind,data,path.name,'test',classification)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=15).run()
    app.session_state['records']=[item]
    app.sidebar.radio[0].set_value(page).run()
    assert not app.exception
    table=next(m.value for m in app.markdown if f'<table aria-label="{label}"' in m.value)
    assert table.count('<tr>')==len(data[GROUPS[kind]])+1
    assert not app.metric and not app.json and not app.text_input
    assert len(app.subheader)==1
    app.run()
    assert not app.exception and app.session_state['records'][0]['data']==data


@__import__('pytest').mark.parametrize('sample',[1,4])
def test_real_client_completed_poll_reaches_result_even_when_cleanup_times_out(monkeypatch,sample):
    import json
    import requests
    from pathlib import Path
    from unittest.mock import Mock
    from carebridge import client
    path=Path(f'.local/live/sample{sample}-raw.json')
    if not path.exists(): __import__('pytest').skip('저장된 실제 샘플 응답 없음')
    completed=json.loads(path.read_text())
    def response(data):
        value=Mock(ok=True,status_code=200)
        value.json.return_value=data
        return value
    session=Mock(headers={})
    session.request.side_effect=[response({'id':'file-test'}),response({'id':'job-test','status':'queued'}),
        response({'id':'job-test','status':'in_progress','output':[{'step_type':'information-extract','status':'in_progress'}]}),
        response(completed),requests.Timeout('DO NOT SHOW THIS PRIVATE ERROR')]
    monkeypatch.setenv('UPSTAGE_API_KEY','fake-test-key')
    monkeypatch.setenv('UPSTAGE_AGENT_ID','fake-test-agent')
    monkeypatch.setattr(client.time,'sleep',lambda _:None)
    real=client.AgentClient(session=session)
    monkeypatch.setattr(client,'AgentClient',lambda:real)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=15).run()
    app.sidebar.radio[0].set_value('문서 분석').run()
    next(r for r in app.radio if r.label=='문서 선택 방식').set_value('가상 샘플 체험').run()
    app.checkbox[0].check().run()
    next(b for b in app.button if b.label=='AI 분석 시작').click().run()
    assert not app.exception and not app.error
    assert len(app.session_state['records'])==1
    assert app.session_state['last_result_id']==app.session_state['records'][0]['id']
    assert app.warning  # Cleanup failure is visible without losing the result.
    assert any('분석 완료' in element.label for element in app.get('status'))
    assert not any('DO NOT SHOW' in w.value for w in app.warning)
    assert len([call for call in session.request.call_args_list if call.args[0]=='POST'])==2
    app.run()
    assert session.request.call_count==5  # Widget reruns never resubmit the job.


@__import__('pytest').mark.parametrize('error',[__import__('carebridge.client',fromlist=['AnalysisError']).AnalysisError('분석 대기시간을 초과했습니다.'),RuntimeError('PRIVATE RESPONSE')])
def test_analysis_errors_end_status_without_resubmission(monkeypatch,error):
    from pathlib import Path
    from carebridge import client
    class FailingClient:
        cleanup_warning=False
        calls=0
        def analyze(self,data,progress):
            self.calls+=1
            progress('Extract · 정보 추출 · 1초 경과')
            raise error
    fake=FailingClient()
    monkeypatch.setattr(client,'AgentClient',lambda:fake)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=15).run()
    app.sidebar.radio[0].set_value('문서 분석').run()
    next(r for r in app.radio if r.label=='문서 선택 방식').set_value('가상 샘플 체험').run()
    app.checkbox[0].check().run()
    next(b for b in app.button if b.label=='AI 분석 시작').click().run()
    assert not app.exception and app.error
    assert not app.session_state['records']
    assert any('오류' in element.label for element in app.get('status'))
    assert all('PRIVATE RESPONSE' not in e.value for e in app.error)
    app.run()
    assert fake.calls==1


def test_medication_steps_and_daily_schedule():
    from datetime import date
    from pathlib import Path
    from carebridge.records import record
    from carebridge.domain import normalize
    kind,data=normalize({'medication_instructions':[{'medication_name':'테스트 약','single_dose':'1정','times_per_day':'원문 불명','days_of_administration':'2','administration_timing':'원문 시점','caution_notes':'원문 주의','source_excerpt':'대조 근거'}]})
    item=record(kind,data,'test.pdf','test',kind)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=15).run()
    app.session_state['records']=[item]
    app.sidebar.radio[0].set_value('복약 관리').run()
    headings=[m.value for m in app.markdown if m.value.startswith('### ')]
    assert headings==['### 01. 약품별 복약정보','### 02. 복약 일정 관리','### 03. 원문 대조 및 상세정보']
    app.toggle[0].set_value(True).run()
    next(t for t in app.text_input if t.label=='확인한 하루 복용 횟수').set_value('1').run()
    app.date_input[0].set_value(date(2026,10,20)).run()
    app.text_input[-1].set_value('09:00').run()
    app.checkbox[0].check().run()
    next(b for b in app.button if b.label=='확인한 내용으로 일정 저장').click().run()
    assert not app.exception and len(app.session_state['schedules'])==1
    dates=next(s for s in app.selectbox if s.label=='일정 확인 날짜')
    assert dates.options==['2026-10-20','2026-10-21']
    dates.set_value('2026-10-21').run()
    assert any('09:00' in frame.value.to_string() and '복용 시각' in frame.value.columns for frame in app.dataframe)
    assert any(b.label=='일정 CSV 내려받기' for b in app.get('download_button'))
    assert app.session_state['records'][0]['data']==data


def test_health_verdict_colors_summary_and_original_preservation():
    from pathlib import Path
    from copy import deepcopy
    from carebridge.records import record
    from carebridge.domain import normalize
    raw={'examinee_sex':'남성','health_checkup_results':[
        {'test_item_name':'BMI','result_value':'26.8','reference_range':'18.5-30','remark':'과체중','source_excerpt':'BMI 원문'},
        {'test_item_name':'혈색소','result_value':'14','result_unit':'g/dL','reference_range':'남 13-16.5 여 12-15.5','source_excerpt':'혈색소 원문'},
        {'test_item_name':'허리둘레','result_value':'92','result_unit':'cm','reference_range':'남 90 이상, 여 85 이상','source_excerpt':'허리둘레 원문'},
        {'test_item_name':'추가 항목','result_value':'12','reference_range':'1~10','source_excerpt':'추가 원문'},
        {'test_item_name':'정보 누락','result_value':'','reference_range':'1~10','source_excerpt':'누락 원문'}]}
    kind,data=normalize(raw)
    before=deepcopy(data)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=15).run()
    app.session_state['records']=[record(kind,data,'test.pdf','test',kind)]
    app.sidebar.radio[0].set_value('건강검진').run()
    assert not app.exception
    summary=next(m.value for m in app.markdown if 'class="result-summary"' in m.value)
    assert summary.count('<strong>1개</strong>')==1
    assert '<strong>2개</strong>' in summary
    table=next(m.value for m in app.markdown if '<table aria-label="건강검진 결과"' in m.value)
    assert 'status-badge attention">원문 판정 · 과체중' in table
    assert 'status-badge attention">문서 범위 초과' in table
    assert 'status-badge muted">확인 필요' in table
    assert '남성: 13.0~16.5 / 여성: 12.0~15.5' in table
    assert '남성: 90 이상 / 여성: 85 이상' in table
    assert table.count('class="status-badge">문서 범위 내')==2
    app.checkbox[0].check().run()
    table=next(m.value for m in app.markdown if '<table aria-label="건강검진 결과"' in m.value)
    assert 'BMI' in table and '추가 항목' in table and '허리둘레' not in table and '혈색소' not in table
    next(s for s in app.selectbox if s.label=='상세 확인할 검사항목').set_value(1).run()
    assert any(t.value=='원문 참고범위: 남 13-16.5 여 12-15.5' for t in app.text)
    assert app.session_state['records'][0]['data']==before


@__import__('pytest').mark.parametrize('sex',['남성','여성',None])
def test_required_health_cases_reach_table_and_match_summary(sex):
    from pathlib import Path
    from copy import deepcopy
    from carebridge.domain import normalize
    from carebridge.records import record
    examples=[('BMI','26.8','과체중(25-29.9)'),('공복혈당','108','100미만'),
              ('총콜레스테롤','216','200미만'),('추가 검사','42','60이상'),
              ('허리둘레','91','남 90 이상, 여 85 이상'),('혈색소','15.1','남 13-16.5 여 12-15.5')]
    raw={'health_checkup_results':[{'test_item_name':name,'result_value':value,'reference_range':reference,'source_excerpt':name+' 원문 근거'} for name,value,reference in examples]}
    if sex: raw['examinee_sex']=sex
    kind,data=normalize(raw)
    original=deepcopy(data)
    item=record(kind,data,'existing-extract.json','test',kind)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=15).run()
    app.session_state['records']=[item]
    app.sidebar.radio[0].set_value('건강검진').run()
    assert not app.exception
    assert any(m.value=='### 문서 기준 요약' for m in app.markdown)
    summary=next(m.value for m in app.markdown if 'class="result-summary"' in m.value)
    values=[part.split('</strong>')[0] for part in summary.split('<strong>')[1:]]
    assert values==(['6개','2개','4개','0개'] if sex else ['6개','0개','4개','2개'])
    table=next(m.value for m in app.markdown if '<table aria-label="건강검진 결과"' in m.value)
    rows=table.split('<tbody>')[1].split('</tbody>')[0].split('</tr>')[:-1]
    expected=['과체중','문서 기준 초과','문서 기준 초과','문서 기준 미달']+(['문서 범위 내']*2 if sex else ['확인 필요']*2)
    assert len(rows)==6
    for html,(name,_,_),status in zip(rows,examples,expected):
        assert name in html and '>'+status+'</span>' in html
    assert '과체중: 25.0~29.9' in table and '100 미만' in table
    assert '남성: 13.0~16.5 / 여성: 12.0~15.5' in table
    assert not app.metric and not app.json
    assert len(app.subheader)==1
    app.checkbox[0].check().run()
    filtered=next(m.value for m in app.markdown if '<table aria-label="건강검진 결과"' in m.value)
    assert filtered.count('<tr>')==(5 if sex else 7)
    assert app.session_state['records'][0]['data']==original


@__import__('pytest').mark.parametrize('kind,page,label,group',[
    ('health_checkup_result','건강검진','상세 확인할 검사항목','health_checkup_results'),
    ('medication_guidance_record','복약 관리','상세 확인할 약품','medication_instructions'),
])
def test_details_selection_survives_navigation(kind,page,label,group):
    from pathlib import Path
    from carebridge.domain import normalize
    from carebridge.records import record
    _,data=normalize({group:[{},{}]})
    item=record(kind,data,'test.pdf','test',kind)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=15).run()
    app.session_state['records']=[item]
    app.sidebar.radio[0].set_value(page).run()
    next(s for s in app.selectbox if s.label==label).set_value(1).run()
    app.sidebar.radio[0].set_value('대시보드').run()
    app.sidebar.radio[0].set_value(page).run()
    assert not app.exception
    assert next(s for s in app.selectbox if s.label==label).value==1
    assert app.session_state['records'][0]['data']==data


def test_medication_details_compare_extraction_with_approved_values():
    from datetime import date
    from pathlib import Path
    from carebridge.domain import normalize,make_schedule
    from carebridge.records import record
    kind,data=normalize({'medication_instructions':[{'medication_name':'테스트 약','single_dose':'1정','times_per_day':'원문 횟수 불명','days_of_administration':'2','administration_timing':'식후','source_excerpt':'테스트 원문'}]})
    item=record(kind,data,'test.pdf','test',kind)
    verified={**data['medication_instructions'][0],'times_per_day':'1'}
    events=make_schedule(verified,date(2026,10,16),['09:00'],True)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=15).run()
    app.session_state['records']=[item]
    app.session_state['schedules']={item['id']+'0':{'record_id':item['id'],'confirmed_values':verified,'events':events}}
    app.sidebar.radio[0].set_value('복약 관리').run()
    assert not app.exception
    comparison=next(f.value for f in app.dataframe if '사용자 승인 일정값' in f.value.columns)
    frequency=comparison[comparison['항목']=='횟수'].iloc[0]
    assert frequency['문서 추출값']=='원문 횟수 불명'
    assert frequency['사용자 승인 일정값']=='1'
    assert app.session_state['records'][0]['data']==data
    assert not app.json
