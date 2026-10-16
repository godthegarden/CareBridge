"""Syntactic field checks, not clinical dose validation or inferred directions."""
import re
from decimal import Decimal, InvalidOperation

FIELDS = {'medication_name':'약품명','single_dose':'1회량','times_per_day':'횟수','days_of_administration':'기간','administration_timing':'복용 시점'}
UNCERTAIN = re.compile(r'미기재|불명|불확실|확인\s*필요|미상|판독|인식\s*오류|OCR|\?',re.I)
VARIABLE = re.compile(r'필요\s*시|증상\s*시|통증\s*시|격일|매주|주\s*[1-7]\s*회|월수금|교대|상충|모순|충돌|(?:추출(?:된)?\s*(?:내용|정보|값)|약품(?:별)?\s*정보)[^.!?]{0,15}(?:혼합|섞임|구분\s*불가)')
DOSE = re.compile(r'\s*(\d+(?:\.\d+)?)(?:\s*/\s*(\d+(?:\.\d+)?))?\s*(정|캡슐|포|개|회분|mL|ml|mg|g|방울|패치|매|단위|IU)\s*',re.I)


def variable_schedule(row):
    return bool(VARIABLE.search(' '.join(str(row.get(field) or '') for field in (*FIELDS,'caution_notes','source_excerpt'))))


def field_errors(row, *, schedule=False):
    errors={}
    for field,label in FIELDS.items():
        value=str(row.get(field) or '').strip()
        if not value or re.fullmatch(r'[-—]|없음|null|N/?A',value,re.I) or UNCERTAIN.search(value):
            errors[field]=label+' 확인 필요'
    if 'single_dose' not in errors:
        match=DOSE.fullmatch(str(row.get('single_dose') or ''))
        try:
            valid=bool(match) and Decimal(match[1])>0 and (match[2] is None or Decimal(match[2])>0)
        except InvalidOperation:
            valid=False
        if not valid:
            errors['single_dose']='1회량의 수치·단위 확인 필요'
    for field,unit,limit in [('times_per_day','회',12),('days_of_administration','일',366)]:
        value=str(row.get(field) or '')
        pattern=(r'\s*([1-9]\d*)\s*(?:'+unit+r')?\s*')
        if not schedule and field=='times_per_day':
            pattern=r'\s*(?:(?:하루|1\s*일|매일|일일)\s*)?([1-9]\d*)\s*(?:회)?\s*'
        match=re.fullmatch(pattern,value)
        if not match or int(match[1])>limit:
            errors[field]=FIELDS[field]+'의 고정 수치 확인 필요'
    if variable_schedule(row):
        errors['administration_timing']='필요시·비일일·상충·혼합 지시 확인 필요 (고정 일일 일정 미지원)'
    return errors
