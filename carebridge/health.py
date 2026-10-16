"""Document-only comparisons. No clinical thresholds, gender inference or diagnoses."""
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

NUMBER=r'[+-]?(?:\d+(?:\.\d+)?|\.\d+)'
LABEL=r'비만\s*전\s*단계|비정상|이상\s*없음|이상\s*소견|과체중|저체중|(?:[1-3]\s*단계\s*)?비만|정상(?:\s*범위\s*내)?|경계(?:치|역)?|주의(?:\s*요망)?|이상'
SEX=re.compile(r'(?<![가-힣A-Za-z])(남성|여성|남자|여자|남|여|male|female)\s*[:：]?\s*',re.I)


@dataclass(frozen=True)
class Assessment:
    level: str
    text: str
    reason: str
    reference: str
    basis: str='comparison'

    @property
    def tone(self):
        # An arithmetic mismatch is a caution, not a new medical abnormality finding.
        return 'caution' if self.basis=='comparison' and self.level=='abnormal' else self.level


@dataclass(frozen=True)
class Measurement:
    name: str
    unit: str
    unit_source: str


# Recognize only explicitly written unit tokens, never infer a unit from the test type.
UNITS={unit:unit for unit in ('cm','mm','m','kg','g','mg','mmHg','%','mg/dL','g/dL','mg/L','g/L','U/L','IU/L','mIU/L','mL/min','mmol/L')}
UNITS.update({'kg/m2':'kg/m²','kg/m^2':'kg/m²','mL/min/1.73m2':'mL/min/1.73m²'})


def measurement_fields(row):
    name=str(row.get('test_item_name') or '').strip()
    explicit=str(row.get('result_unit') or '').strip()
    if re.fullmatch(r'미기재|미상|불명|없음|확인\s*필요|[-—]|n/?a|null|none',explicit,re.I): explicit=''
    candidates=[]
    for match in re.finditer(r'\(([^()]*)\)|\[([^\[\]]*)\]',name):
        token=unicodedata.normalize('NFKC',match[1] if match[1] is not None else match[2])
        token=re.sub(r'\s+','',token)
        if token in UNITS: candidates.append((match.start(),match.end(),UNITS[token]))
    units={unit for _,_,unit in candidates}
    derived=next(iter(units)) if len(units)==1 else ''
    unit=explicit or derived
    explicit_key=re.sub(r'\s+','',unicodedata.normalize('NFKC',explicit))
    if derived and (not explicit or UNITS.get(explicit_key,explicit)==derived):
        for start,end,_ in reversed(candidates): name=name[:start]+name[end:]
        name=re.sub(r'\s+',' ',name).strip()
    return Measurement(name,unit,'단위 필드' if explicit else '항목명' if derived else '확인 필요')


RISK_MARKER=r'(?:복부\s*비만|비만|위험|주의|이상|경계)\s*(?:판정\s*)?기준'
NORMAL_MARKER=r'정상\s*(?:기준|범위)'
REFERENCE_HEADING=re.compile(r'^(?P<label>(?:'+RISK_MARKER+'|'+NORMAL_MARKER+r'))\s*[:：]?\s*')


def threshold_meaning(row):
    context=' '.join(str(row.get(field) or '') for field in ('test_item_name','reference_range','remark','source_excerpt'))
    risk=bool(re.search(RISK_MARKER,context))
    normal=bool(re.search(NORMAL_MARKER,context))
    return 'risk' if risk and not normal else 'normal' if normal and not risk else None


def reference_condition(text):
    return REFERENCE_HEADING.sub('',text)


@dataclass(frozen=True)
class Rule:
    low: Decimal | None
    high: Decimal | None
    low_inclusive: bool=True
    high_inclusive: bool=True

    def matches(self,value):
        return (self.low is None or value>self.low or self.low_inclusive and value==self.low) and (self.high is None or value<self.high or self.high_inclusive and value==self.high)

    def display(self):
        if self.low is not None and self.high is not None:
            places=max(0,-self.low.as_tuple().exponent,-self.high.as_tuple().exponent)
            if self.low_inclusive and self.high_inclusive:
                return f'{self.low:.{places}f}~{self.high:.{places}f}'
            return f'{self.low} '+('이상' if self.low_inclusive else '초과')+f' {self.high} '+('이하' if self.high_inclusive else '미만')
        if self.low is not None:
            return f'{self.low} '+('이상' if self.low_inclusive else '초과')
        return f'{self.high} '+('이하' if self.high_inclusive else '미만')


def cleaned(text,unit=''):
    text=unicodedata.normalize('NFKC',str(text or '')).strip()
    unit=unicodedata.normalize('NFKC',str(unit or '')).strip()
    if unit:
        unit_pattern=r'(?:\('+re.escape(unit)+r'\)|'+re.escape(unit)+r')'
        text=re.sub(r'(?<=\d)\s*'+unit_pattern+r'(?=$|\s|[-~〜–—,;/)\]]|이상|이하|초과|미만)','',text)
    return text.strip()


def numeric_rule(text):
    text=text.strip()
    match=re.fullmatch(rf'({NUMBER})\s*[-~〜–—]\s*({NUMBER})',text)
    if match:
        low,high=map(Decimal,match.groups())
        return Rule(low,high) if low<=high else None
    match=re.fullmatch(rf'({NUMBER})\s*(이상|초과)\s*[,;]?\s*({NUMBER})\s*(이하|미만)',text)
    if match:
        low,lower,high,upper=match.groups();low,high=Decimal(low),Decimal(high)
        return Rule(low,high,lower=='이상',upper=='이하') if low<high or low==high and lower=='이상' and upper=='이하' else None
    match=re.fullmatch(rf'(<=|>=|≤|≥|<|>)\s*({NUMBER})',text)
    if match:
        op,value=match.groups()
        return Rule(Decimal(value),None,op in ('>=','≥')) if op in ('>=','≥','>') else Rule(None,Decimal(value),True,op in ('<=','≤'))
    match=re.fullmatch(rf'({NUMBER})\s*(이상|이하|초과|미만)',text)
    if match:
        value,op=match.groups()
        return Rule(Decimal(value),None,op=='이상') if op in ('이상','초과') else Rule(None,Decimal(value),True,op=='이하')
    return None


def label_level(label):
    label=re.sub(r'\s','',label)
    if label in ('정상','정상범위내','이상없음'): return 'normal'
    if label in ('과체중','저체중','비만전단계') or label.startswith(('경계','주의')): return 'caution'
    return 'abnormal'


def explicit_verdict(text):
    text=str(text or '').strip()
    if re.search(r'확인\s*필요|판정\s*불가|불명|여부|판독\s*불가|\?|추정|가능성|의심',text):
        return ('unknown','확인 필요')
    text=re.sub(r'^(?:판정|결과|분류|상태)\s*[:：]\s*','',text)
    match=re.fullmatch(rf'({LABEL})(?:\s*\([^)]*\)|[.,;·].*)?',text)
    if match:
        tail=text[match.end(1):]
        if re.search(rf'(?<![가-힣A-Za-z])(?:{LABEL})(?![가-힣A-Za-z])',tail):
            return ('unknown','확인 필요')
        return label_level(match[1]),match[1]
    if re.search(r'정상|과체중|저체중|비만|경계|주의|이상\s*소견',text):
        # A verdict-like but unparsed remark must not silently become a green range match.
        return ('unknown','확인 필요')
    return None


def sex_key(value):
    value=str(value or '').strip().lower()
    if value in ('남','남성','남자','male','m'): return 'male'
    if value in ('여','여성','여자','female','f'): return 'female'
    return None


def document_sex(data):
    values=[sex_key(data[key]) for key in ('examinee_sex','sex','gender') if data.get(key)]
    return values[0] if values and values[0] and len(set(values))==1 else None


def sex_parts(text):
    matches=list(SEX.finditer(text))
    if not matches: return None
    if text[:matches[0].start()].strip(' ():,;/\n'): return []
    return [(sex_key(match[1]),text[match.end():matches[i+1].start() if i+1<len(matches) else len(text)].strip(' (),;/\n')) for i,match in enumerate(matches)]


def categories(text):
    matches=list(re.finditer(rf'(?<![가-힣A-Za-z])({LABEL})\s*[:：]?\s*(?=\(?\s*[<>≤≥+\-\d.])',text))
    if not matches: return None
    if text[:matches[0].start()].strip(' ():;,/\n'): return []
    def category_rule(fragment):
        fragment=fragment.strip(' ,;/\n')
        if fragment.startswith('(') or fragment.endswith(')'):
            if not (fragment.startswith('(') and fragment.endswith(')')): return None
            fragment=fragment[1:-1].strip()
        return numeric_rule(fragment)
    return [(match[1],category_rule(text[match.end():matches[i+1].start() if i+1<len(matches) else len(text)])) for i,match in enumerate(matches)]


def format_reference(row):
    raw=row.get('reference_range') or ''
    unit=measurement_fields(row).unit
    text=cleaned(raw,unit)
    heading=REFERENCE_HEADING.match(text)
    text=reference_condition(text)
    def format_part(part):
        rule=numeric_rule(part)
        if rule: return rule.display()
        groups=categories(part)
        if groups and all(rule is not None for _,rule in groups):
            return ' / '.join(label+': '+rule.display() for label,rule in groups)
        return part
    parts=sex_parts(text)
    if parts:
        formatted=' / '.join(('남성' if sex=='male' else '여성')+': '+format_part(part) for sex,part in parts)
    else:
        formatted=format_part(text)
    # Unit conversions are deliberately not performed; differing units remain unreadable.
    return ((heading['label']+' · ') if heading else '')+(formatted or '미기재')


def assess(row,sex=None):
    reference=format_reference(row)
    def result(level,text,reason,basis='comparison'): return Assessment(level,text,reason,reference,basis)
    remark=row.get('remark') or ''
    explicit=None if re.fullmatch(RISK_MARKER,remark.strip()) else explicit_verdict(remark)
    if explicit is None:
        explicit=explicit_verdict(row.get('result_value'))
    if explicit:
        level,label=explicit
        # A clear source verdict and a clear numeric comparison may disagree.
        # Preserve both raw inputs; do not turn that disagreement into a diagnosis.
        if re.fullmatch(NUMBER,cleaned(row.get('result_value'),measurement_fields(row).unit)):
            calculated=assess({**row,'remark':''},sex)
            if ((level=='normal' and calculated.level in ('caution','abnormal')) or
                    (level in ('caution','abnormal') and calculated.level=='normal' and
                     (threshold_meaning(row)=='normal' or calculated.basis=='document_category'))):
                return result('unknown','확인 필요 · 원문 판정과 수치 기준 불일치',
                              '원문 판정과 수치 비교가 다릅니다. 어느 쪽도 임의로 확정하지 않고 원본 대조가 필요합니다.',basis='conflict')
        return result(level,'원문 판정 · '+label if level!='unknown' else '확인 필요','문서의 항목별 판정을 우선 표시했습니다. 원문 비고를 대조하세요.',basis='document_verdict')
    unit=measurement_fields(row).unit
    value=cleaned(row.get('result_value'),unit)
    if not re.fullmatch(NUMBER,value):
        return result('unknown','확인 필요','측정값을 단일 숫자로 확인할 수 없습니다.')
    value=Decimal(value)
    ref=reference_condition(cleaned(row.get('reference_range'),unit))
    parts=sex_parts(ref)
    if parts is not None:
        if not parts or len({key for key,_ in parts})!=len(parts):
            return result('unknown','확인 필요','성별별 기준이 중복되거나 형식이 불명확합니다.')
        selected=[part for key,part in parts if key==sex]
        if len(selected)!=1:
            return result('unknown','확인 필요','명확한 성별 정보와 해당 성별 기준이 필요합니다. 다른 성별의 기준을 대신 적용하지 않았습니다.')
        ref=selected[0]
    rule=numeric_rule(ref)
    groups=categories(ref) if rule is None else None
    if groups is not None:
        if not groups or any(rule is None for _,rule in groups):
            return result('unknown','확인 필요','문서 범주의 기준을 모두 해석할 수 없습니다.')
        matched=[label for label,rule in groups if rule.matches(value)]
        if len(matched)!=1:
            return result('unknown','확인 필요','해당하는 원문 범주가 없거나 범주가 겹칩니다.')
        label=matched[0]
        return result(label_level(label),label,'문서에 적힌 범주별 숫자 조건을 적용했습니다. 외부 기준은 사용하지 않았습니다.',basis='document_category')
    if not rule:
        return result('unknown','확인 필요','문서 기준을 해석할 수 없거나 값과 기준의 단위가 일치하지 않습니다.')
    if rule.low is None or rule.high is None:
        meaning=threshold_meaning(row)
        applies=rule.matches(value)
        if meaning=='risk':
            return result('caution' if applies else 'unknown','원문 위험 기준 해당' if applies else '원문 위험 기준 미해당 · 정상 여부 확인 필요',
                          '문서가 위험 기준으로 명시한 조건의 해당 여부입니다. 위험 조건 미해당을 정상 판정으로 바꾸지 않습니다.',basis='risk_condition')
        if parts is not None and meaning!='normal':
            return result('unknown','문서 임계값 '+('해당' if applies else '미해당')+' · 의미 확인 필요',
                          '해당 성별의 숫자 조건은 비교했지만 정상 기준인지 위험 기준인지 확정할 수 없습니다.')
        if applies:
            return result('normal','문서 범위 내','문서 참고기준의 단일 임계값을 충족합니다. 의학적 정상 여부를 판단한 결과가 아닙니다.')
        below=rule.low is not None
        return result('abnormal','문서 기준 미달' if below else '문서 기준 초과','문서에 적힌 임계값을 충족하지 않습니다. 질병 진단이 아닙니다.')
    if rule.matches(value):
        return result('normal','문서 범위 내','추출값과 문서 참고범위를 단순 비교했습니다.')
    return result('abnormal','문서 범위 미만' if value<rule.low or value==rule.low and not rule.low_inclusive else '문서 범위 초과','문서 참고범위를 벗어난 수치이며 질병 진단이 아닙니다.')


def summarize(assessments):
    """The summary and table consume the same assessments, including unresolved items."""
    return {
        'total':len(assessments),
        'met':sum(a.level=='normal' for a in assessments),
        'attention':sum(a.level in ('caution','abnormal') for a in assessments),
        'unknown':sum(a.level=='unknown' for a in assessments),
    }
