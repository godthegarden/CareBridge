"""Session-local analysis lifecycle. No resubmission on rendering or rerun."""
import hashlib
import logging
from .client import AnalysisError
from .domain import analysis_record_data
from .records import record
from .stage_review import stage_review
from . import factchat

logger=logging.getLogger(__name__)
BUSY={'queued','running'}


def set_upload(state,data,filename):
    identity=hashlib.sha256(data).hexdigest() if data else None
    old=state.get('analysis_upload',{})
    if old.get('identity')!=identity or old.get('filename')!=filename:
        if state.get('analysis_flow',{}).get('state') in BUSY: return
        state['analysis_upload']={'data':data,'filename':filename,'identity':identity}
        state['analysis_flow']={'state':'idle','phase':'준비'}
        state['analysis_extract_agreed']=False
        state['analysis_factchat_agreed']=False
        state.pop('analysis_extract_consent',None)
        state.pop('analysis_llm_consent',None)
        state.pop('last_result_id',None)


def queue(state, *, extract_consent, explanation_consent):
    if state.get('analysis_flow',{}).get('state') in BUSY|{'completed'}: return
    if not extract_consent or not state.get('analysis_upload',{}).get('data'): return
    state['analysis_flow']={'state':'queued','phase':'분석 대기','explanation_consent':explanation_consent}
    state.pop('last_result_id',None)


def run_queued(state,agent_factory,progress):
    flow=state.get('analysis_flow',{})
    if flow.get('state')!='queued': return
    flow['state']='running'
    upload=state['analysis_upload']
    client=None
    def update(message):
        flow['phase']=message
        progress(message)
    try:
        update('Upstage · 문서 추출 및 분류')
        client=agent_factory()
        job=client.analyze(upload['data'],update)
        update('결과 처리 · 추출값 검증')
        kind,data,classification=analysis_record_data(job)
        item=record(kind,data,upload['filename'],job.get('id',''),classification)
        if classification=='health_checkup_result': item['explanation_mode']='structured_only'
        state['records'].append(item)
        state['originals'][item['id']]=upload['data']
        state['stage_reviews'][item['id']]=stage_review(job)
        state['last_result_id']=item['id']
        state['selected_records'][kind]=item['id']
        state.pop('record_choice_'+kind,None)
        flow['result_id']=item['id']
        flow['cleanup_warning']=bool(client.cleanup_warning)
        if flow['explanation_consent'] and classification==kind=='health_checkup_result':
            update('FactChat · 검증된 정보로 핵심 해설 생성')
            try:
                entry=factchat.generate_once(state.setdefault('factchat_results',{}),item['id'],data,consent=True,safe_only=True)
                flow['explanation_state']=entry['state']
            except factchat.ExplanationError as error:
                flow['explanation_state']='error'
                logger.warning('analysis state=explanation_unavailable rule=%s section=%s',error.validation_rule,error.validation_section)
        else:
            flow['explanation_state']='not_requested'
        flow.update(state='completed',phase='분석 완료')
    except AnalysisError as error:
        flow.update(state='error',error=str(error),phase='분석 실패')
        logger.warning('analysis state=failed stage=extraction')
    except Exception:
        if flow.get('result_id'):
            flow.update(state='completed',phase='분석 완료',explanation_state='error')
        else:
            flow.update(state='error',phase='분석 실패',error='분석 처리에 문제가 발생했습니다. 자동 재요청하지 않습니다. 기존 작업 상태를 확인한 뒤 다시 시도하세요.')
        logger.warning('analysis state=failed stage=processing')
    finally:
        if client and client.cleanup_warning: flow['cleanup_warning']=True
        if flow['state']=='running':
            flow.update(state='interrupted',phase='분석 중단',error='화면 재실행 또는 연결 중단으로 완료 여부를 확인하지 못했습니다. 자동으로 재요청하지 않습니다. 기존 작업 상태를 확인하세요.')
