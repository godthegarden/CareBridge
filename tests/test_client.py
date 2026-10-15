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
