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
        app.session_state['records']=[record(result['kind'],result['data'],p.name,'integration')]
        app.sidebar.radio[0].set_value('건강검진' if result['kind']=='health_checkup_result' else '복약 관리').run()
        assert not app.exception

def test_schedule_confirmation_in_ui():
    from carebridge.records import record
    from carebridge.domain import normalize
    kind,data=normalize({'medication_instructions':[{'medication_name':'테스트 약','single_dose':'1정','times_per_day':'1','days_of_administration':'2','administration_timing':'아침 식후','source_excerpt':'테스트용 원문'}]})
    app=AppTest.from_file(str(__import__('pathlib').Path(__file__).resolve().parents[1]/'app.py'), default_timeout=15).run()
    app.session_state['records']=[record(kind,data,'unit-test.pdf','test')]
    app.sidebar.radio[0].set_value('복약 관리').run()
    app.text_input[-1].set_value('09:00')
    next(b for b in app.button if b.label=='확인한 내용으로 일정 저장').click().run()
    assert app.error and not app.session_state['schedules']
    app.checkbox[0].check()
    next(b for b in app.button if b.label=='확인한 내용으로 일정 저장').click().run()
    assert not app.exception and len(next(iter(app.session_state['schedules'].values()))['events'])==2
