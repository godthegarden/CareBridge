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
