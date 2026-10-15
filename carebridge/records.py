"""Portable user-held records; never a shared server-side health database."""
import json
import uuid
from datetime import datetime, timezone
from .domain import normalize
from .client import AnalysisError

def record(kind,data,filename,job_id):
    return {'id':uuid.uuid4().hex,'kind':kind,'data':data,'filename':filename,'job_id':job_id,'created_at':datetime.now(timezone.utc).isoformat(),'origin':'실제 Agent 분석'}

def export_records(records):
    return json.dumps({'version':1,'records':records},ensure_ascii=False,indent=2)

def import_records(raw):
    if len(raw)>10*1024*1024: raise AnalysisError('기록 파일은 10MB 이하만 지원합니다.')
    try:
        bundle=json.loads(raw)
        if bundle.get('version')!=1 or not isinstance(bundle.get('records'),list) or len(bundle['records'])>100: raise ValueError()
        records=[]
        for r in bundle['records']:
            kind,data=normalize(r['data'])
            # Imported files are untrusted, even if they claim to be Agent outputs.
            item=record(kind,data,str(r.get('filename','가져온 기록'))[:200],'')
            item['origin']='가져온 기록 · API 재검증 안 됨'
            records.append(item)
        return records
    except (ValueError,TypeError,KeyError,AttributeError):
        raise AnalysisError('CareBridge 기록 형식을 확인해 주세요.') from None
