import json
from pathlib import Path
import pytest
import requests
from carebridge.client import AgentClient, AnalysisError

class Response:
    def __init__(self,payload,code=200):self.payload=payload;self.status_code=code;self.ok=code<400
    def json(self):return self.payload
class Session:
    def __init__(self,responses):self.responses=iter(responses);self.headers={};self.calls=[]
    def request(self,method,url,**kwargs):
        self.calls.append((method,url,kwargs))
        value=next(self.responses)
        if isinstance(value,Exception):raise value
        return value

def client(monkeypatch,responses):
    monkeypatch.setenv('UPSTAGE_API_KEY','fake-unit-test')
    monkeypatch.setenv('UPSTAGE_AGENT_ID','fake-agent')
    monkeypatch.setattr('carebridge.client.time.sleep',lambda _:None)
    return AgentClient(session=Session(responses))

def pdf():return Path('samples/sample1.pdf').read_bytes()

def test_poll_and_cleanup(monkeypatch):
    c=client(monkeypatch,[Response({'id':'f'}),Response({'id':'j','status':'queued'}),Response({'id':'j','status':'completed','output':[]}),Response({'deleted':True})])
    assert c.analyze(pdf())['status']=='completed'
    assert [x[0] for x in c.session.calls]==['POST','POST','GET','DELETE']

def test_failed_job_deletes_file(monkeypatch):
    c=client(monkeypatch,[Response({'id':'f'}),Response({'status':'failed'}),Response({'deleted':True})])
    with pytest.raises(AnalysisError):c.analyze(pdf())
    assert c.session.calls[-1][0]=='DELETE'

def test_auth_error_is_sanitized(monkeypatch):
    c=client(monkeypatch,[Response({'secret':'DO NOT SHOW'},401)])
    with pytest.raises(AnalysisError) as e:c.analyze(pdf())
    assert 'DO NOT SHOW' not in str(e.value)

def test_post_timeout_not_retried(monkeypatch):
    c=client(monkeypatch,[requests.Timeout('sensitive request')])
    with pytest.raises(AnalysisError):c.analyze(pdf())
    assert len(c.session.calls)==1

def test_cleanup_failure_visible(monkeypatch):
    c=client(monkeypatch,[Response({'id':'f'}),Response({'status':'completed'}),Response({},500)])
    c.analyze(pdf());assert c.cleanup_warning


def test_poll_reports_actual_steps_and_returns_completed_job(monkeypatch,caplog):
    import logging
    c=client(monkeypatch,[Response({'id':'f'}),Response({'id':'j','status':'queued'}),
        Response({'id':'j','status':'in_progress','output':[{'step_type':'document-parse','status':'completed'},{'step_type':'document-classify','status':'in_progress'}]}),
        Response({'id':'j','status':'in_progress','output':[{'step_type':'information-extract','status':'in_progress','content':'PRIVATE MEDICAL TEXT'}]}),
        Response({'id':'j','status':'completed','output':[]}),Response({'deleted':True})])
    progress=[]
    with caplog.at_level(logging.INFO,logger='carebridge.client'):
        assert c.analyze(pdf(),progress.append)['status']=='completed'
    assert any('Classify' in x for x in progress) and any('Extract' in x for x in progress)
    assert any('결과 수신 완료' in x for x in progress)
    assert 'PRIVATE MEDICAL TEXT' not in caplog.text and 'fake-unit-test' not in caplog.text
    assert len([x for x in c.session.calls if x[0]=='POST' and x[1].endswith('/responses')])==1


@pytest.mark.parametrize('state',['failed','cancelled','incomplete','expired','unknown',None])
def test_terminal_or_invalid_job_stops_without_polling(monkeypatch,state):
    c=client(monkeypatch,[Response({'id':'f'}),Response({'id':'j','status':state,'error':{'message':'PRIVATE'}}),Response({'deleted':True})])
    with pytest.raises(AnalysisError) as error: c.analyze(pdf())
    assert 'PRIVATE' not in str(error.value)
    assert not any(call[0]=='GET' for call in c.session.calls)


def test_deadline_includes_submission_and_stops_polling(monkeypatch):
    c=client(monkeypatch,[Response({'id':'f'}),Response({'id':'j','status':'in_progress'}),Response({'deleted':True})])
    now=[0.0]
    monkeypatch.setattr('carebridge.client.time.monotonic',lambda:now[0])
    original=c.session.request
    def request(method,url,**kwargs):
        response=original(method,url,**kwargs)
        if method=='POST' and url.endswith('/responses'): now[0]=301
        return response
    c.session.request=request
    with pytest.raises(AnalysisError,match='대기시간'): c.analyze(pdf())
    assert not any(call[0]=='GET' for call in c.session.calls)
    assert c.session.calls[-1][0]=='DELETE'


def test_cleanup_has_short_timeout_and_does_not_discard_result(monkeypatch):
    c=client(monkeypatch,[Response({'id':'f'}),Response({'status':'completed','output':[]}),requests.Timeout('PRIVATE')])
    assert c.analyze(pdf())['status']=='completed'
    assert c.cleanup_warning
    assert sum(c.session.calls[-1][2]['timeout'])<=5


def test_retry_budget_respects_deadline(monkeypatch):
    c=client(monkeypatch,[Response({},503)])
    monkeypatch.setattr('carebridge.client.time.monotonic',lambda:0)
    with pytest.raises(AnalysisError,match='대기시간'):
        c.request('GET','/responses/test',deadline=.5)
    assert len(c.session.calls)==1
