"""Consent-gated Gateway prose with document grounding and source-backed quizzes."""
import hashlib
import json
import logging
import math
import os
import re
import threading
import time

import requests
from dotenv import load_dotenv
from .client import ROOT
from .domain import normalize, missing
from .health import assess, document_sex, measurement_fields
from .explanation_content import SYSTEM, HEALTH_SYSTEM, LABELS, term_definition, validate_content
from .health_report import question_bank
from .output_contract import response_format
from .medication_validation import field_errors

BASE = 'https://api.baze.co/v1/gateway'
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
_lock = threading.Lock()
_requests = 0
_last_request = None


class ExplanationError(Exception):
    """Only fixed, non-sensitive messages may reach users/logs."""
    validation_rule = 'request_error'
    validation_section = 'request'
    validation_index = -1


def configuration():
    load_dotenv(ROOT / '.env')
    key = (os.environ.get("FACTCHAT_API_KEY") or '').strip()
    model = os.getenv('FACTCHAT_MODEL', '').strip()
    if not key or not model:
        raise ExplanationError('서버에 FACTCHAT_API_KEY와 FACTCHAT_MODEL 설정이 필요합니다. 관리자가 학교의 모델 목록과 단가를 확인한 뒤 설정해 주세요.')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:-]{0,120}', model):
        raise ExplanationError('FACTCHAT_MODEL 설정을 확인해 주세요.')
    return key, model


def _sanitize(value, names):
    text = str(value).strip()
    for name in names:
        if name:
            text = text.replace(name, '[이름 제외]')
    # Do not send whole source excerpts. Remove common identifiers even if misplaced.
    text = re.sub(r'\d{6}\s*[-–]?\s*[1-8]\d{6}', '[식별번호 제외]', text)
    text = re.sub(r'(?<!\d)0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}(?!\d)', '[전화번호 제외]', text)
    text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[이메일 제외]', text)
    if re.search(r'주민|주소|연락처|환자명|수검자|등록번호|차트번호|https?://', text):
        return ''
    return text if len(text) <= 240 else ''


def build_facts(data):
    """Keep schema untouched; no name/date/title/file/raw excerpt leaves the app."""
    kind, clean = normalize(data)
    names = [clean.get('examinee_name', ''), clean.get('patient_name', '')]
    facts = []
    health = kind == 'health_checkup_result'
    rows = clean['health_checkup_results' if health else 'medication_instructions']
    # Refuse oversized requests rather than silently omit medications/tests.
    if len(rows) > 60:
        raise ExplanationError('쉬운 설명은 한 문서 60개 항목까지 지원합니다. 기존 분석 결과는 그대로 확인할 수 있습니다.')
    for i, row in enumerate(rows):
        name = measurement_fields(row).name if health else row['medication_name']
        name = _sanitize(name, names)
        if not name or missing(name):
            continue
        if health:
            assessment = assess(row, document_sex(clean))
            fields = [('result', row['result_value']), ('unit', measurement_fields(row).unit),
                      ('reference', row['reference_range']), ('remark', row['remark'])]
            attention = assessment.level != 'normal'
        else:
            medication_errors=field_errors(row)
            fields = [('dose', row['single_dose']), ('frequency', row['times_per_day']),
                      ('days', row['days_of_administration']), ('timing', row['administration_timing']),
                      ('caution', row['caution_notes'])]
            attention = True
        for field, value in fields:
            value = _sanitize(value, names)
            negative_measurement = health and field in ('result','reference') and bool(re.match(r'^-\d',value))
            if not value or (missing(value) and not negative_measurement):
                continue
            uncertain = bool(re.search(r'OCR|불확실|불명확|판독|인식 오류|\?', value, re.I))
            if not health:
                original_field={'dose':'single_dose','frequency':'times_per_day','days':'days_of_administration','timing':'administration_timing','caution':'caution_notes'}[field]
                uncertain = uncertain or original_field in medication_errors
            facts.append({'id': f'{i}:{field}', 'item': name, 'field': field, 'value': value,
                          'attention': attention, 'quiz_allowed': not uncertain and field != 'unit' and '[이름 제외]' not in value,
                          'origin': 'document'})
        if health:
            facts.append({'id': f'{i}:comparison', 'item': name, 'field': 'comparison',
                          'value': assessment.text, 'attention': attention, 'quiz_allowed': False, 'origin': 'rule', 'level': assessment.level, 'conflict': assessment.basis == 'conflict',
                          'direction': {'문서 기준 초과':'above','문서 범위 초과':'above','문서 기준 미달':'below','문서 범위 미만':'below'}.get(assessment.text, 'within' if assessment.level=='normal' else 'unknown' if assessment.level=='unknown' else 'category')})
            term = term_definition(name)
            if term:
                facts.append({'id': f'{i}:meaning', 'item': name, 'field': 'meaning', 'value': term[0],
                              'attention': attention, 'quiz_allowed': False, 'origin': 'glossary', 'url': term[1]})
        else:
            checks = [LABELS[field]+' 확인 필요' for field, value in fields if field != 'caution' and missing(value)]
            if not re.search(r'하루|1\s*일|매일|일일', row['times_per_day']):
                checks.append('원문 횟수의 기준 확인 필요')
            if any(re.search(r'OCR|불확실|불명확|판독|인식 오류|\?', value, re.I) for _, value in fields):
                checks.append('추출 정확도 확인 필요')
            checks.extend(issue for issue in medication_errors.values() if issue not in checks)
            if checks:
                facts.append({'id': f'{i}:check', 'item': name, 'field': 'check', 'value': ' / '.join(checks),
                              'attention': True, 'quiz_allowed': False, 'origin': 'rule'})
    if not any(f['origin'] == 'document' for f in facts):
        raise ExplanationError('설명에 사용할 명확한 추출 항목이 없습니다. 원문을 먼저 확인해 주세요.')
    payload = {'kind': kind, 'facts': facts}
    if health:
        payload['report_version'] = 1
        payload['understanding_questions'] = question_bank(facts)
    return payload


def fingerprint(payload, model):
    return hashlib.sha256(json.dumps([6 if payload.get('report_version') else 3, model, payload], ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def validate_plan(plan, payload):
    return validate_content(plan, payload, ExplanationError)


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def _reserve():
    global _requests, _last_request
    try:
        limit = int(os.getenv('FACTCHAT_MAX_REQUESTS', '1'))
        if not 0 <= limit <= 100:
            raise ValueError()
    except ValueError:
        raise ExplanationError('FACTCHAT_MAX_REQUESTS는 0~100의 정수여야 합니다.') from None
    with _lock:
        now = time.monotonic()
        if _requests >= limit:
            raise ExplanationError('서버의 쉬운 설명 호출 한도에 도달했습니다. 기존 분석 결과는 계속 사용할 수 있습니다.')
        if _last_request is not None and now - _last_request < 30:
            raise ExplanationError('요청 간격 제한에 도달했습니다. 기존 분석 결과를 확인해 주세요.')
        _requests += 1  # Failed/timeout requests also consume a slot. Never retry automatically.
        _last_request = now


def parse_generated_content(content):
    """Decode the API envelope's content; document safety is checked separately."""
    if isinstance(content, str):
        fenced = re.fullmatch(r'\s*```(?:json)?\s*(\{[\s\S]*\})\s*```\s*', content)
        if fenced:
            content = fenced[1]
    try:
        return json.loads(content)
    except (ValueError, TypeError):
        error = ExplanationError('Gateway 응답 JSON 형식을 확인할 수 없습니다. 기존 분석 결과를 확인해 주세요. [response_json]')
        error.validation_rule = 'response_json'
        error.validation_section = 'response'
        raise error from None


def request_plan(payload, *, consent=False, session=None):
    if consent is not True:
        raise ExplanationError('외부 전송 동의가 필요합니다.')
    key, model = configuration()
    try:
        output_format = response_format(payload)
    except ValueError as error:
        raise ExplanationError(str(error)) from None
    _reserve()
    started = time.monotonic()
    transport = session or requests.Session()
    try:
        body={'model':model,'messages':[{'role':'system','content':HEALTH_SYSTEM if payload.get('report_version') else SYSTEM},
                                      {'role':'user','content':json.dumps(payload,ensure_ascii=False)}],
              'stream':False,'max_tokens':4500}
        if output_format is not None:
            body['response_format']=output_format
        response = transport.post(BASE + '/chat/completions/',
            headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
            json=body, timeout=(5, 35), allow_redirects=False)
        logger.info('factchat state=response http=%d elapsed_s=%.1f', response.status_code, time.monotonic()-started)
        if response.status_code != 200:
            errors = {401: 'Gateway 인증 설정을 확인해 주세요.', 402: 'Gateway 크레딧이 부족합니다.',
                      403: '모델 접근 권한 또는 크레딧을 확인해 주세요.', 429: 'Gateway 호출 한도에 도달했습니다.'}
            raise ExplanationError(errors.get(response.status_code, 'Gateway 호출에 실패했습니다. 기존 분석 결과를 확인해 주세요.'))
        raw = response.json()
        usage = raw.get('usage') or {}
        billing = raw.get('billing') or {}
        metrics = {'prompt_tokens': _number(usage.get('prompt_tokens')),
                   'completion_tokens': _number(usage.get('completion_tokens')),
                   'total_tokens': _number(usage.get('total_tokens')),
                   'deducted_credit': _number(billing.get('deducted_credit'))}
        logger.info('factchat state=billed prompt_tokens=%s completion_tokens=%s total_tokens=%s deducted_credit=%s',
                    *metrics.values())
        choice = raw['choices'][0]
        if choice.get('finish_reason') != 'stop':
            raise ExplanationError('생성이 정상 완료되지 않아 설명을 표시하지 않습니다. 자동으로 다시 호출하지 않습니다.')
        parsed = parse_generated_content(choice['message']['content'])
        plan = validate_plan(parsed, payload)
        logger.info('factchat state=content_validated')
        return {'plan': plan, 'usage': metrics, 'model': model, 'source': 'factchat_generated'}
    except ExplanationError as error:
        logger.warning('factchat state=rejected rule=%s section=%s item=%d',
                       error.validation_rule, error.validation_section, error.validation_index)
        raise
    except requests.Timeout:
        logger.warning('factchat state=timeout')
        raise ExplanationError('Gateway 응답 시간이 초과되었습니다. 과금될 수 있어 자동 재호출하지 않습니다.') from None
    except requests.RequestException:
        logger.warning('factchat state=connection_error')
        raise ExplanationError('Gateway 연결에 실패했습니다. 자동 재호출하지 않습니다.') from None
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        logger.warning('factchat state=invalid_response')
        raise ExplanationError('Gateway 응답 형식을 확인할 수 없습니다. 기존 분석 결과를 확인해 주세요.') from None
    finally:
        if session is None:
            transport.close()


def generate_once(cache, record_id, data, *, consent=False):
    """Session-local cache stores failures too; never share health content globally."""
    payload = build_facts(data)
    _, model = configuration()
    cache_key = fingerprint(payload, model)
    # Lock also prevents double-click races. No network call while holding a global lock.
    with _lock:
        entry = cache.get(record_id)
        if entry and entry['key'] == cache_key:
            return entry
        if consent is not True:
            raise ExplanationError('외부 전송 동의가 필요합니다.')
        entry = {'key': cache_key, 'state': 'pending', 'payload': payload}
        cache[record_id] = entry
    try:
        entry.update(state='complete', result=request_plan(payload, consent=True))
    except ExplanationError as exc:
        entry.update(state='error', error=str(exc))
        logger.warning('factchat state=unavailable')
    except Exception:
        entry.update(state='error', error='설명 처리 중 오류가 발생했습니다. 기존 결과를 확인해 주세요. 자동 재호출하지 않습니다.')
        logger.error('factchat state=unexpected_error')
    return entry
