"""Schema adapters and conservative, non-clinical document rules."""
import json
import re
from datetime import datetime, timedelta
from .client import AnalysisError, ROOT

SCHEMAS = {s['json_schema']['name']:s['json_schema']['schema'] for s in json.loads((ROOT/'carebridge.json').read_text())['workflowConfig']['informationExtractConfiguration']['schemas']}
GROUPS = {'health_checkup_result':'health_checkup_results','medication_guidance_record':'medication_instructions'}

def classify_job(job):
    """Read the actual document-classify step, never infer from Extract or filename."""
    steps=[step for step in (job.get('output') or []) if isinstance(step,dict) and step.get('step_type')=='document-classify']
    if len(steps)!=1 or steps[0].get('status')!='completed':
        return None
    parts=[part for part in (steps[0].get('content') or []) if isinstance(part,dict) and part.get('type')=='output_text']
    if len(parts)!=1 or not isinstance(parts[0].get('text'),str):
        return None
    text=parts[0]['text'].strip()
    try:
        value=json.loads(text)
    except ValueError:
        value=text
    return value if isinstance(value,str) and value in GROUPS else None

def analysis_record_data(job):
    classification=classify_job(job)
    try:
        kind,data=parse_job(job)
    except AnalysisError:
        if classification:
            raise AnalysisError('Agent 작업은 완료됐지만 추출 결과가 없거나 형식이 올바르지 않습니다. Studio에서 기존 작업의 Extract 결과를 확인해 주세요.') from None
        # Unsupported/unclassified documents still get an explicit review screen.
        kind,data=classification or 'unclassified',{}
    return kind,data,classification

def normalize(data):
    if not isinstance(data, dict):
        raise AnalysisError('분석 결과가 객체 형식이 아닙니다.')
    kinds = [k for k,g in GROUPS.items() if g in data]
    if len(kinds) != 1:
        raise AnalysisError('지원하는 문서 유형을 명확히 확인할 수 없습니다.')
    kind = kinds[0]; group = GROUPS[kind]
    if not isinstance(data[group], list) or not data[group] or len(data[group]) > 500:
        raise AnalysisError('추출된 항목 목록이 비어 있거나 올바르지 않습니다.')
    properties = SCHEMAS[kind]['properties']
    clean = {}
    for key in properties:
        if key == group:
            rows = []
            for row in data[group]:
                if not isinstance(row, dict):
                    raise AnalysisError('항목별 정보 형식이 올바르지 않습니다.')
                output = {}
                for field in properties[group]['items']['properties']:
                    value = row.get(field)
                    if value is not None and not isinstance(value, (str,int,float)):
                        raise AnalysisError('항목 값이 문자열 형식이 아닙니다.')
                    output[field] = '' if value is None else str(value)
                rows.append(output)
            clean[key] = rows
        else:
            value = data.get(key)
            if value is not None and not isinstance(value, str):
                raise AnalysisError('문서 메타데이터 형식이 올바르지 않습니다.')
            clean[key] = value or ''
    if kind=='health_checkup_result':
        # Optional explicit metadata only; do not infer sex from names or reference labels.
        for field in ('examinee_sex','sex','gender'):
            if field in data and isinstance(data[field],str): clean[field]=data[field]
    return kind, clean

def parse_job(job):
    candidates = []
    output=job.get('output')
    if not isinstance(output,list):
        raise AnalysisError('Agent 응답에 추출 결과 목록이 없습니다.')
    for step in output:
        if not isinstance(step,dict) or step.get('status') in ('queued','in_progress','failed','cancelled'):
            continue
        content=step.get('content')
        if not isinstance(content,list):
            continue
        for part in content:
            if not isinstance(part,dict): continue
            if part.get('type') == 'output_text':
                try:
                    value = json.loads(part.get('text',''))
                except (ValueError,TypeError):
                    continue
                if isinstance(value,dict) and any(g in value for g in GROUPS.values()):
                    candidates.append(value)
    if len(candidates) != 1:
        raise AnalysisError('Agent의 최종 추출 결과를 하나로 확인할 수 없습니다.')
    return normalize(candidates[0])

def missing(value):
    return not str(value).strip() or bool(re.search(r'미기재|불명|확인 필요|미상|없음|^[-—]|^null$|^N/?A$',str(value),re.I))

def positive_int(value, suffix=''):
    m = re.fullmatch(r'\s*([1-9]\d*)\s*'+suffix+r'\s*',str(value))
    return int(m[1]) if m else None

def range_status(row,sex=None):
    from .health import assess
    return assess(row,sex).text

def medication_issues(row):
    issues=[]
    for field,label in [('medication_name','약품명'),('single_dose','1회량'),('administration_timing','복용 시점'),('source_excerpt','원문 근거')]:
        if missing(row.get(field,'')): issues.append(label+' 확인 필요')
    if not positive_int(row.get('times_per_day',''),r'(?:회)?'): issues.append('원문 횟수 확인 필요')
    else: issues.append('횟수가 하루 기준인지 확인 필요')
    if not positive_int(row.get('days_of_administration',''),r'(?:일)?'): issues.append('복용 일수 확인 필요')
    combined=' '.join(row.values())
    if re.search(r'OCR|불확실|불명확|판독|인식 오류|\?',combined,re.I): issues.append('추출 정확도 확인 필요')
    if re.search('필요시|필요 시|상충|모순|충돌|전문가에게 확인',combined): issues.append('복약 지시 확인 필요')
    return issues

def make_schedule(row, start, clocks, confirmed):
    if not confirmed: raise ValueError('원문 또는 전문가에게 확인한 내용을 승인해 주세요.')
    count=positive_int(row.get('times_per_day',''),r'(?:회)?')
    days=positive_int(row.get('days_of_administration',''),r'(?:일)?')
    if not count or not days or count>12 or days>366 or missing(row.get('administration_timing','')) or missing(row.get('single_dose','')) or missing(row.get('medication_name','')):
        raise ValueError('약품명·용량·횟수(1~12)·기간(1~366일)·시점을 확인해 주세요.')
    if len(clocks)!=count or len(set(clocks))!=count: raise ValueError('하루 횟수만큼 서로 다른 알림 시간을 입력해 주세요.')
    try: times=[datetime.strptime(c,'%H:%M').time() for c in clocks]
    except ValueError: raise ValueError('시간은 HH:MM 형식으로 입력해 주세요.') from None
    return [{'일시':datetime.combine(start+timedelta(days=d),t).isoformat(timespec='minutes'),'약품명':row['medication_name'],'1회량':row['single_dose'],'복용 시점':row['administration_timing']} for d in range(days) for t in sorted(times)]
