"""Real Upstage REST client. Never log payloads, credentials, or server error bodies."""
import io
import os
import logging
import math
import time
from pathlib import Path
import requests
from dotenv import load_dotenv
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://api.upstage.ai/v2'
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

STEP_LABELS={'document-parse':'Parse · 문서 읽기','document-classify':'Classify · 문서 분류','information-extract':'Extract · 정보 추출'}
JOB_STATUSES={'queued','in_progress','completed','failed','cancelled','incomplete','expired'}

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

    def request(self, method, path, *, deadline=None, **kwargs):
        # POST is deliberately not retried: a timeout may already have created a billable job.
        for attempt in range(3):
            remaining=deadline-time.monotonic() if deadline is not None else 140
            if remaining<=0:
                raise AnalysisError('분석 대기시간을 초과했습니다. 기존 작업이 진행 중일 수 있으므로 Studio에서 상태를 확인해 주세요.')
            try:
                r = self.session.request(method, BASE + path, timeout=(min(10,remaining/2), min(60,remaining/2)), **kwargs)
            except requests.Timeout:
                raise AnalysisError('API 응답 대기시간을 초과했습니다. 기존 작업이 접수됐을 수 있으므로 Studio에서 확인 후 다시 시도해 주세요.') from None
            except requests.RequestException:
                raise AnalysisError('Upstage 연결에 실패했습니다. 네트워크를 확인해 주세요. 요청이 접수됐을 수 있으므로 즉시 반복 실행하지 마세요.') from None
            if method == 'GET' and r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                delay=2 ** attempt
                if deadline is not None and time.monotonic()+delay>=deadline:
                    raise AnalysisError('상태 조회 대기시간을 초과했습니다. Studio에서 기존 작업을 확인해 주세요.')
                time.sleep(delay)
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
        started=time.monotonic()
        deadline=started+self.timeout
        file_id=None
        stage='문서 준비'
        def report(label, state='in_progress'):
            nonlocal stage
            stage=label
            elapsed=int(time.monotonic()-started)
            # Only our own labels/statuses are logged; never output, IDs, paths or exceptions.
            logger.info('analysis stage=%s state=%s elapsed_s=%d',label,state,elapsed)
            progress(f'{label} · {elapsed}초 경과')
        try:
            report('문서 준비')
            data=prepare_document(data)
            report('업로드')
            uploaded=self.request('POST','/files',deadline=deadline,data={'purpose':'user_data'},files={'file':('document.pdf',data,'application/pdf')})
            file_id=uploaded.get('id')
            if not isinstance(file_id,str) or not file_id:
                raise AnalysisError('파일 업로드 응답에 ID가 없습니다.')
            report('Agent 작업 접수')
            # Jobs API is asynchronous. Submit once, then retrieve only this job.
            job=self.request('POST','/responses',deadline=deadline,json={'model':self.agent,'include':['all'],'input':[{'role':'user','content':[{'type':'input_file','file_id':file_id}]}]})
            job_id=job.get('id')
            polls=0
            observed_steps=set()
            while True:
                state=job.get('status')
                safe_state=state if isinstance(state,str) and state in JOB_STATUSES else 'unknown'
                logger.info('analysis job_state=%s poll=%d',safe_state,polls)
                output=job.get('output')
                steps=[step for step in output if isinstance(step,dict) and isinstance(step.get('step_type'),str) and step['step_type'] in STEP_LABELS] if isinstance(output,list) else []
                for step in steps:
                    step_state=step.get('status')
                    if isinstance(step_state,str) and step_state in JOB_STATUSES:
                        event=(step['step_type'],step_state)
                        if event not in observed_steps:
                            logger.info('analysis step=%s state=%s elapsed_s=%d',step['step_type'],step_state,int(time.monotonic()-started))
                            observed_steps.add(event)
                if state=='completed':
                    report('결과 수신 완료','completed')
                    return job
                if state not in ('queued','in_progress'):
                    labels={'failed':'실패','cancelled':'취소','incomplete':'미완료','expired':'만료'}
                    raise AnalysisError('Agent 작업 '+labels.get(safe_state,'상태 확인 불가')+'입니다. Studio에서 기존 작업을 확인해 주세요.')
                if not isinstance(job_id,str) or not job_id:
                    raise AnalysisError('분석 작업 ID가 없습니다.')
                label='Agent 대기' if state=='queued' else 'Agent 처리 중'
                for step in steps:
                    step_state=step.get('status')
                    if step_state in ('in_progress','queued'):
                        label=STEP_LABELS[step['step_type']]
                        break
                    if step_state=='completed':
                        label=STEP_LABELS[step['step_type']]+' 완료 · 다음 단계 대기'
                report(label)
                remaining=deadline-time.monotonic()
                if remaining<=0 or polls>=max(1,math.ceil(self.timeout/2)):
                    raise AnalysisError('분석 대기시간을 초과했습니다. 작업을 자동 재생성하지 않습니다. Studio에서 기존 작업을 확인해 주세요.')
                time.sleep(min(2,remaining))
                job=self.request('GET','/responses/'+job_id,deadline=deadline,params={'include':'all'})
                polls+=1
        except AnalysisError:
            logger.warning('analysis stage=%s state=error',stage)
            raise
        finally:
            if file_id:
                # Cleanup must not hide a completed result behind another 60-second wait.
                report('업로드 파일 정리')
                try:
                    self.request('DELETE','/files/'+file_id,deadline=time.monotonic()+5)
                    logger.info('analysis cleanup=completed')
                except AnalysisError:
                    self.cleanup_warning=True
                    logger.warning('analysis cleanup=unconfirmed')
