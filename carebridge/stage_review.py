"""Session-only intermediate evidence. Status is never a claim of OCR correctness."""
from .client import STEP_LABELS, JOB_STATUSES


def stage_review(job):
    steps=[]
    parse=[]
    output=job.get('output') if isinstance(job,dict) else None
    for step in output if isinstance(output,list) else []:
        if not isinstance(step,dict) or not isinstance(step.get('step_type'),str) or step['step_type'] not in STEP_LABELS:
            continue
        kind=step['step_type']
        state=step.get('status')
        texts=[part['text'] for part in (step.get('content') or [])
               if isinstance(part,dict) and part.get('type')=='output_text' and isinstance(part.get('text'),str)] if isinstance(step.get('content'),list) else []
        steps.append({'단계':STEP_LABELS[kind],'작업 상태':state if isinstance(state,str) and state in JOB_STATUSES else '확인 필요','텍스트 결과':'있음' if texts else '없음'})
        if kind=='document-parse':
            parse.extend(texts)
    # Bound memory/UI exposure; originals remain in the existing consent-gated viewer.
    return {'steps':steps,'parse_text':'\n'.join(parse)[:30000]}
