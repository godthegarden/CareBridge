"""Context checks for checkup explanations, independent of medication instructions.

These are conservative checks for claim/action patterns, not a clinical fact checker.
Evidence IDs and document quantity checks remain mandatory in the caller.
"""
import re
import unicodedata
from decimal import Decimal
from .health_report import LIFESTYLE

AI_NOTICE = '본 내용은 AI가 생성한 참고 정보이며 의료전문가의 진단·처방을 대신하지 않습니다. 중요한 결정은 원문과 의료전문가를 통해 확인하세요.'
DISEASE = r'(?:당뇨병?|고혈압|고지혈증|심장병|암|특정\s*(?:질환|질병))'
DIAGNOSIS = re.compile(
    DISEASE+r'\s*(?:이|가|의|으로|로)?\s*(?:확실합니다|확실해요|확정됩니다|확정입니다|입니다|이십니다|으로\s*진단|의심됩니다|가능성이\s*높|위험이\s*높)|'
    r'(?:당신|귀하|환자|수검자).{0,35}(?:확진|확정\s*진단|확실합니다|확실해요|질환이\s*확실)|'
    r'(?:질병|질환|당뇨병?|고혈압).{0,15}(?:진단합니다|확진합니다|확정합니다)|'
    r'(?:결과|검사|수치).{0,30}(?:질병|질환).{0,20}진단할\s*수\s*있(?:습니다|어요)')
MEDICATION_ACTION = re.compile(
    r'(?:약물|약|복용|투약|용량|치료|처방).{0,45}?(?:중단|변경|증량|감량|늘리|줄이|바꾸|끊|시작|복용하세요|먹으세요|드세요)|'
    r'(?:복용량|투약량|복용\s*시각).{0,20}(?:정합니다|정해|하세요)|'
    r'(?:치료|처방)가?\s*필요합니다|'
    r'(?:치료|처방|수술).{0,12}(?:받으세요|받아야|필요합니다|해야\s*합니다|시작하세요)')
CLINICAL_CERTAINTY = re.compile(r'정상입니다|정상으로\s*(?:확정|판정)|문제\s*없습니다|문제가\s*없습니다|안전합니다|건강합니다')
LIFESTYLE_WORD = re.compile(r'운동|식단|식습관|신체활동|수면|걷기|걸으|채소|통곡물|포화지방|설탕|잠자리')
GENERAL_CONTEXT = re.compile(r'일반적|일상적|전반적|건강관리|건강\s*관리|건강\s*습관|규칙적인|꾸준한|균형\s*잡힌')
PERSONAL_ACTION = re.compile(r'당신|귀하|개인별|정해\s*드|반드시|중단|치료|처방|확실히|때문에|따라서|이므로|수치에\s*따라|검진\s*결과에\s*따라')
LIMIT_CONTEXT = re.compile(
    r'(?:결과|검진|검사|수치).{0,30}(?:만으로|만을).{0,45}(?:진단|확정|판단|치료).{0,30}(?:없|어렵|못|불가|않)')
N = r'[+-]?\d+(?:\.\d+)?'
UNIT = r'(?:mg/dL|g/dL|mmol/L|mmHg|kg/m(?:²|2|\^2)|IU/L|U/L|mg/L|g/L|cm|mm|kg|mg|g|%|mL/min)'
QUANTITY = re.compile(r'(?P<number>'+N+r')\s*(?P<unit>'+UNIT+r')(?![A-Za-z0-9/])', re.I)
ANY_QUANTITY = re.compile(r'(?<![A-Za-z0-9.])(?P<number>'+N+r')\s*(?P<unit>[A-Za-zμµ][A-Za-z0-9μµ²^]*(?:/[A-Za-z0-9μµ².^]+){0,3}|%)(?![A-Za-z0-9/])')
CONDITION = re.compile(r'('+N+r')\s*(이상|이하|미만|초과)|([<>≤≥]=?)\s*('+N+r')|('+N+r')\s*[~〜–—-]\s*('+N+r')')


def normalized(text):
    return unicodedata.normalize('NFKC', str(text))


def has_medication_directive(text):
    # An imperative is not excused by an unrelated negative disclaimer later on.
    if re.search(r'(?:중단|변경|증량|감량)(?:하세요|하십시오|해요|해야|합니다)|(?:줄이|늘리|바꾸|끊으|먹으|드)(?:세요|십시오)|복용하세요',text):
        return True
    for match in MEDICATION_ACTION.finditer(text):
        if not re.match(r'\s*(?:할\s*수(?:는)?\s*없|해서는\s*안|하면\s*안|하지\s*않|을?\s*지시(?:하는)?\s*(?:것이)?\s*아닙)',text[match.end():]):
            return True
    return False


def is_limit_notice(text):
    """Accept a non-diagnostic statement by its negative scope, not a word whitelist."""
    return bool(LIMIT_CONTEXT.search(text)) and not DIAGNOSIS.search(text) and not has_medication_directive(text)


def text_kind(text):
    if LIFESTYLE_WORD.search(text) and (GENERAL_CONTEXT.search(text) or any(g['text'] in text for g in LIFESTYLE.values())):
        return 'general_health'
    return 'document'


def validate_health_claims(text, source_facts, fail):
    # Only source quotations are exempt; stripping occurs after intent checks below.
    quoted_source = {f['value'] for f in source_facts if f['field'] in ('remark','result')}
    for sentence in re.split(r'(?<=[.!?])\s+|\n+', text.strip()):
        if not sentence:
            continue
        # Pre-authored lifestyle guidance is safe general information, not a personal plan.
        if sentence in {guide['text'] for guide in LIFESTYLE.values()}:
            continue
        claim = sentence
        if re.search(r'원문|문서|검진표', sentence):
            def source_quote(match):
                return '[원문 인용]' if match[1] in quoted_source else match[0]
            claim = re.sub(r"[‘“\"']([^‘’“”\"']+)[’”\"']", source_quote, claim)
        if re.search(r'(?:검증|안전검사|금지|단어).{0,20}(?:무시|모두\s*허용|전부\s*허용|비활성|우회)', claim):
            fail('문서 설명과 무관한 안전 규칙 변경 지시를 포함할 수 없습니다.', 'document_medical_claim')
        if DIAGNOSIS.search(claim):
            fail('특정 질환의 확정 진단이나 근거 없는 위험 판단을 생성할 수 없습니다.', 'document_medical_claim')
        if has_medication_directive(claim):
            fail('건강검진 설명에서 개인별 치료·약물 변경·복용 지시를 생성할 수 없습니다.', 'document_medical_claim')
        if CLINICAL_CERTAINTY.search(claim):
            fail('검진 수치만으로 의학적 정상이나 안전을 확정할 수 없습니다.', 'document_medical_claim')
        if LIFESTYLE_WORD.search(sentence):
            if not GENERAL_CONTEXT.search(sentence) or PERSONAL_ACTION.search(sentence) or re.search(r'\d', sentence):
                fail('일반 건강정보를 개인별 생활습관 처방으로 바꿀 수 없습니다.', 'document_lifestyle_prose')


def quantity_pairs(text):
    text = normalized(text)
    return {(Decimal(m['number']),normalized(m['unit']).lower())
            for pattern in (QUANTITY,ANY_QUANTITY) for m in pattern.finditer(text)}


def number_values(text):
    text = re.sub(UNIT, '', normalized(text), flags=re.I)
    return {Decimal(m[0]) for m in re.finditer(r'(?<![\d.])'+N, text)}


def _conditions(text):
    # Unit-free tuples preserve operator and direction, not just a bag of numbers.
    text = re.sub(UNIT, '', normalized(text), flags=re.I)
    output = set()
    for match in CONDITION.finditer(text):
        a, op, symbol, b, low, high = match.groups()
        if a is not None:
            output.add(({'이상':'>=','이하':'<=','미만':'<','초과':'>'}[op],Decimal(a)))
        elif b is not None:
            output.add(({'≤':'<=','≥':'>='}.get(symbol,symbol),Decimal(b)))
        else:
            output.add(('range',Decimal(low),Decimal(high)))
    return output


def validate_health_quantities(text, selected, all_facts, fail):
    ids = {f['id'].split(':')[0] for f in selected}
    rows = {}
    for fact in all_facts:
        row = fact['id'].split(':')[0]
        if row in ids:
            rows.setdefault(row,{})[fact['field']] = fact
    known_pairs = set()
    allowed_conditions = set()
    for row in rows.values():
        unit = normalized(row.get('unit',{}).get('value','')).replace(' ','').lower()
        for field in ('result','reference'):
            if field not in row or row[field] not in selected:
                continue
            value = normalized(row[field]['value'])
            known_pairs.update(quantity_pairs(value))
            if unit:
                known_pairs.update((n,unit) for n in number_values(value))
            if field=='reference':
                allowed_conditions.update(_conditions(value))
    for pair in quantity_pairs(text):
        if pair not in known_pairs:
            fail('설명의 수치·단위 조합이 원문과 일치하지 않습니다.', 'document_quantity')
    if not _conditions(text) <= allowed_conditions:
        fail('설명의 참고범위 또는 이상·미만 조건이 원문과 일치하지 않습니다.', 'document_reference_condition')
    # Explicit result roles cannot borrow a number that appeared only in a reference.
    result_pattern = re.compile(r'(?:측정값|결과값|측정\s*결과|검사\s*결과|결과)\s*(?:은|는|이|가|:)?\s*('+N+r')')
    for match in result_pattern.finditer(normalized(text)):
        possible = [row for row in rows.values() if 'result' in row and row['result'] in selected]
        before = text[:match.start()]
        named = [(before.rfind(row['result']['item']),row) for row in possible if row['result']['item'] in before]
        if named:
            possible = [max(named,key=lambda item:item[0])[1]]
        allowed = {number for row in possible for number in number_values(row['result']['value'])}
        if Decimal(match[1]) not in allowed:
            fail('측정값을 참고기준의 다른 숫자로 바꿀 수 없습니다.', 'document_result_value')

    reference_pattern = re.compile(r'(?:참고기준|참고범위)\s*(?:은|는|이|가|:)?\s*('+N+r')')
    for match in reference_pattern.finditer(normalized(text)):
        possible = [row for row in rows.values() if 'reference' in row and row['reference'] in selected]
        allowed = {number for row in possible for number in number_values(row['reference']['value'])}
        if Decimal(match[1]) not in allowed:
            fail('참고기준을 측정값의 다른 숫자로 바꿀 수 없습니다.', 'document_reference_value')

    for row in rows.values():
        result = row.get('result')
        if not result or result not in selected:
            continue
        pattern = re.compile(re.escape(result['item'])+r'\s*(?:은|는|이|가|:)\s*('+N+r')')
        for match in pattern.finditer(normalized(text)):
            tail = re.sub(r'^\s*'+UNIT, '', normalized(text)[match.end():], flags=re.I)
            if re.match(r'\s*(?:이상|이하|초과|미만|[~〜–—-])',tail):
                continue
            if Decimal(match[1]) not in number_values(result['value']):
                fail('항목의 측정값을 다른 원문 숫자로 바꿀 수 없습니다.', 'document_result_value')
