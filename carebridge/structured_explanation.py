"""Conservative automatic-transfer allowlist; unreviewed free prose stays local."""
import re
from .health import measurement_fields, UNITS, document_sex
from .explanation_content import term_definition

NAMES=set('혈색소 헤모글로빈 허리둘레 체중 신장 키 수축기혈압 이완기혈압 혈압 중성지방 트리글리세라이드 혈청크레아티닌 크레아티닌 감마지티피 γ-GTP GGT HEMOGLOBIN HGB HB TRIGLYCERIDE TRIGLYCERIDES CREATININE'.split())
LABELS=r'정상|정상범위내|비정상|이상없음|이상소견|과체중|저체중|비만전단계|비만|경계|경계치|주의|주의요망|확인필요'
REFERENCE_WORDS=r'복부비만|비만전단계|정상범위|정상기준|위험기준|주의기준|이상기준|경계기준|판정|기준|정상|비정상|이상없음|과체중|저체중|비만|경계|주의|남성|여성|남자|여자|남|여|이상|이하|초과|미만'


def structured_document(clean):
    rows=[]
    for row in clean.get('health_checkup_results',[]):
        measurement=measurement_fields(row)
        name=measurement.name
        if not term_definition(name) and re.sub(r'\s+','',name).upper() not in {n.upper() for n in NAMES}:
            continue
        unit=measurement.unit if measurement.unit in set(UNITS.values()) else ''
        value=row.get('result_value','').strip()
        if not re.fullmatch(r'[+-]?\d+(?:\.\d+)?',value) and not re.fullmatch(LABELS,re.sub(r'\s+','',value)):
            value=''
        reference=row.get('reference_range','')
        remaining=reference
        for token in sorted(set(UNITS)|set(UNITS.values()),key=len,reverse=True): remaining=remaining.replace(token,'')
        remaining=re.sub(REFERENCE_WORDS,'',remaining.replace(' ',''))
        if not re.fullmatch(r'[\d\s.+\-~〜–—<>=≤≥():：,;/％%]*',remaining) or len(reference)>160:
            reference=''
        remark=re.sub(r'\s+','',row.get('remark',''))
        if not re.fullmatch(LABELS,remark): remark=''
        if value or reference or remark:
            rows.append({'test_item_name':name,'result_value':value,'result_unit':unit,
                         'reference_range':reference,'remark':remark})
    result={'health_checkup_results':rows}
    sex=document_sex(clean)
    if sex: result['examinee_sex']='남성' if sex=='male' else '여성'
    return result
