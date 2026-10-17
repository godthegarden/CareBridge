"""Presentation of source records, never diagnoses or new clinical thresholds."""
import re
from .health import assess, document_sex, measurement_fields

OPINION_FIELDS=('overall_impression','overall_opinion','general_comment')
OPINION_NAMES={'종합소견','종합의견','종합판정','검진소견'}


def measurement_only(row,sex=None):
    """Routine body/vision measurements need no invented normative threshold.

    Keep explicit source findings, conflicts and applicable comparisons visible.
    """
    name=re.sub(r'\s+','',measurement_fields(row).name)
    basic=bool(re.fullmatch(r'키|신장|몸무게|체중|키/몸무게|신장/체중|(?:좌안|우안)?시력(?:\((?:좌/우|우/좌|좌|우|교정|나안)\))?|좌/우시력',name))
    assessment=assess(row,sex)
    return basic and assessment.basis in ('no_reference','uncompared_value','unparsed_reference') and not row.get('remark','').strip()


def document_opinions(data):
    values=[data.get(field) for field in OPINION_FIELDS]
    for row in data.get('health_checkup_results',[]):
        if re.sub(r'\s+','',row.get('test_item_name','')) in OPINION_NAMES:
            values.extend([row.get('result_value'),row.get('remark')])
    return list(dict.fromkeys(value.strip() for value in values if isinstance(value,str) and value.strip()))


def related_guides(data):
    # Relevance selects reviewed information, never a personal treatment plan.
    names=' '.join(measurement_fields(row).name for row in data.get('health_checkup_results',[]))
    metabolic=r'BMI|체질량|공복혈당|FBS|FPG|혈당|콜레스테롤|HDL|LDL|중성지방|허리둘레|혈압'
    return ['food','activity'] if re.search(metabolic,names,re.I) else []


def result_context(row,sex=None):
    remark=row.get('remark','')
    history=bool(re.search(r'기왕력|과거력|기존\s*질환|치료\s*중(?!단)|복용\s*중(?!단)',remark))
    if re.search(r'(?:과거력|기왕력|기존\s*질환)\s*(?:없|아님)',remark): history=False
    suspected=bool(re.search(r'의심|의증',remark))
    if history and suspected: return '기록 구분 확인 필요'
    if history: return '원문 기존 질환 기록 · 새 진단 아님'
    if suspected:
        return '원문 신규 의심 소견 · 확정 진단 아님' if '신규' in remark else '원문 의심 소견 · 신규 여부 확인 필요'
    assessment=assess(row,sex)
    if assessment.level=='unknown': return assessment.reason
    if assessment.basis=='comparison' and assessment.level=='abnormal': return '문서 참고기준과의 수치 차이 · 진단 아님'
    return '원문 판정·참고기준 설명 · 진단 아님'
