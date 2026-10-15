"""Real Upstage REST client. Never log payloads, credentials, or server error bodies."""
import io
import json
import os
import time
from pathlib import Path
import requests
from dotenv import load_dotenv
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://api.upstage.ai/v2'

class AnalysisError(Exception):
    pass

def validate_pdf(data):
    if not data or len(data) > 20 * 1024 * 1024 or not data.startswith(b'%PDF-'):
        raise AnalysisError('20MB 이하의 PDF 파일을 선택해 주세요.')
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 30:
            raise ValueError()
    except Exception:
        raise AnalysisError('암호가 없고 1~30페이지인 정상 PDF가 필요합니다.') from None

class AgentClient:
    def __init__(self, session=None, timeout=300):
        load_dotenv(ROOT / '.env')
        key = os.getenv('UPSTAGE_API_KEY', '').strip()
        self.agent = os.getenv('UPSTAGE_AGENT_ID', '').strip()
        if not key or not self.agent:
            raise AnalysisError('서버의 UPSTAGE_API_KEY와 UPSTAGE_AGENT_ID 설정이 필요합니다.')
        self.session = session or requests.Session()
        self.session.headers.update({'Authorization': f'Bearer {key}'})
        self.timeout = timeout
        self.cleanup_warning = False

    def request(self, method, path, **kwargs):
        # POST is deliberately not retried: a timeout may already have created a billable job.
        for attempt in range(3):
            try:
                r = self.session.request(method, BASE + path, timeout=(10, 60), **kwargs)
            except requests.RequestException:
                raise AnalysisError('Upstage 연결에 실패했습니다. 네트워크를 확인해 주세요. 요청이 접수됐을 수 있으므로 즉시 반복 실행하지 마세요.') from None
            if method == 'GET' and r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            if not r.ok:
                messages = {401:'API 인증을 확인해 주세요.',403:'Agent 접근 권한을 확인해 주세요.',404:'Agent 또는 작업을 찾을 수 없습니다.',429:'API 요청 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.'}
                raise AnalysisError(messages.get(r.status_code, f'Upstage 요청 실패 (HTTP {r.status_code}).'))
            try:
                payload = r.json()
                if not isinstance(payload, dict):
                    raise AnalysisError('Upstage 응답 구조를 확인할 수 없습니다.')
                return payload
            except ValueError:
                raise AnalysisError('Upstage 응답이 올바른 JSON이 아닙니다.') from None

    def analyze(self, data, progress=lambda text: None):
        from .documents import prepare_document
        data = prepare_document(data)
        file_id = None
        try:
            progress('문서를 안전하게 전송하고 있습니다')
            uploaded = self.request('POST', '/files', data={'purpose':'user_data'}, files={'file':('document.pdf',data,'application/pdf')})
            file_id = uploaded.get('id')
            if not file_id:
                raise AnalysisError('파일 업로드 응답에 ID가 없습니다.')
            progress('Agent가 문서를 분류하고 정보를 추출하고 있습니다')
            job = self.request('POST', '/responses', json={'model':self.agent,'include':['all'],'input':[{'role':'user','content':[{'type':'input_file','file_id':file_id}]}]})
            deadline = time.monotonic() + self.timeout
            while job.get('status') in ('queued','in_progress'):
                if time.monotonic() >= deadline:
                    raise AnalysisError('분석 대기시간을 초과했습니다. Upstage Studio에서 작업 상태를 확인해 주세요.')
                if not job.get('id'):
                    raise AnalysisError('분석 작업 ID가 없습니다.')
                time.sleep(2)
                job = self.request('GET', '/responses/' + job['id'], params={'include':'all'})
            if job.get('status') != 'completed':
                raise AnalysisError('Agent 분석이 완료되지 않았습니다. Studio에서 해당 문서 실행 결과를 확인해 주세요.')
            return job
        finally:
            if file_id:
                try:
                    self.request('DELETE', '/files/' + file_id)
                except AnalysisError:
                    self.cleanup_warning = True
