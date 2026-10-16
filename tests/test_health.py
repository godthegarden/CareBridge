from copy import deepcopy
import pytest
from carebridge.health import assess,document_sex,format_reference,numeric_rule
from carebridge.domain import normalize
from carebridge.records import record,export_records,import_records


def row(value='14',ref='13~16.5',unit='',remark=''):
    return {'test_item_name':'원문 검사','result_value':value,'reference_range':ref,'result_unit':unit,'remark':remark,'source_excerpt':'보존할 원문 근거'}


@pytest.mark.parametrize('value,ref,level',[
    ('13','13~16.5','normal'),('16.5','13-16.5','normal'),('16.6','13–16.5','abnormal'),
    ('12.9','13〜16.5','abnormal'),('-3','-5--1','normal'),('4','5-1','unknown'),
    ('18.5','18.5 이상 25 미만','normal'),('25','18.5 이상 25 미만','abnormal'),
    ('20','18.5 초과 25 이하','normal'),('18.5','18.5 초과 25 이하','abnormal'),
    ('120/80','90-119','unknown'),('미기재','13~16.5','unknown'),('14','성인 기준','unknown'),
])
def test_numeric_document_comparison(value,ref,level):
    assert assess(row(value,ref)).level==level


@pytest.mark.parametrize('ref,boundary,match',[
    ('90 이상','90',True),('90 초과','90',False),('90 이하','90',True),('90 미만','90',False),
    ('≥90','90',True),('>90','90',False),('≤90','90',True),('<90','90',False),
])
def test_threshold_condition_is_a_document_comparison(ref,boundary,match):
    result=assess(row(boundary,ref))
    assert result.level==('normal' if match else 'abnormal')
    assert result.text==('문서 범위 내' if match else ('문서 기준 미달' if numeric_rule(ref).low is not None else '문서 기준 초과'))
    assert '정상' not in result.text


@pytest.mark.parametrize('ref',[
    '남 13-16.5 여 12-15.5','남성: 13~16.5 / 여성: 12~15.5',
    '남자 13–16.5, 여자 12–15.5','male: 13~16.5; female: 12~15.5',
])
def test_sex_specific_range_only_with_known_sex(ref):
    assert assess(row('16',ref),'male').level=='normal'
    assert assess(row('16',ref),'female').level=='abnormal'
    assert assess(row('16',ref)).level=='unknown'
    assert format_reference(row('16',ref))=='남성: 13.0~16.5 / 여성: 12.0~15.5'


def test_sex_threshold_format_and_direction_are_preserved():
    raw='남 90 이상, 여 85 이상'
    value=row('92',raw,'cm')
    assert format_reference(value)=='남성: 90 이상 / 여성: 85 이상'
    assert assess(value,'male').level=='unknown'
    # Explicit adverse categories take precedence over an unlabelled reference condition.
    value['reference_range']='남 이상: 90 이상 / 여 이상: 85 이상'
    assert assess(value,'male').level=='abnormal'
    assert assess(value,'female').level=='abnormal'
    assert assess(value).level=='unknown'
    value['reference_range']='남 13~16.5'
    assert assess(value,'female').level=='unknown'
    value['reference_range']='남 13~16.5 / 남 10~20'
    assert assess(value,'male').level=='unknown'


@pytest.mark.parametrize('name',['BMI','다른 검사항목'])
@pytest.mark.parametrize('remark,level',[
    ('과체중','caution'),('비만 전단계','caution'),('경계','caution'),('주의','caution'),
    ('비만','abnormal'),('판정: 이상','abnormal'),('비정상','abnormal'),
    ('이상 없음','normal'),('정상','normal'),('확인 필요','unknown'),('정상, 이상','unknown'),
])
def test_explicit_document_verdict_overrides_wide_numeric_range(name,remark,level):
    value=row('26.8','18.5~30',remark=remark)
    value['test_item_name']=name
    result=assess(value)
    assert result.level==level
    if level in ('caution','abnormal'): assert '문서 범위 내' not in result.text


@pytest.mark.parametrize('value,label,level',[
    ('7','정상','normal'),('17','경계','caution'),('26.8','비만','abnormal')])
def test_categories_are_taken_from_the_document(value,label,level):
    reference='정상: 0~10 / 경계: 10 초과 20 미만 / 비만: 20 이상'
    result=assess(row(value,reference))
    assert result.level==level and result.text==label


@pytest.mark.parametrize('reference,value',[
    ('정상: 0~10 / 주의: 10~20','10'),  # Overlap at boundary.
    ('정상: 0~9 / 주의: 10~20','9.5'), # Gap, no rounded guess.
    ('정상: 0~10 / 주의: 불명','5'),
    ('정상: 0~10 / 주의: 20~10','5'),
])
def test_ambiguous_or_incomplete_categories_never_count_as_normal(reference,value):
    assert assess(row(value,reference)).level=='unknown'


def test_units_must_match_and_original_text_is_unchanged():
    value=row('14 g/dL','남 13-16.5 g/dL / 여 12-15.5 g/dL','g/dL')
    original=deepcopy(value)
    assert assess(value,'male').level=='normal'
    assert format_reference(value)=='남성: 13.0~16.5 / 여성: 12.0~15.5'
    assert value==original
    assert assess(row('140','13~16.5 g/dL','mg/dL')).level=='unknown'
    assert assess(row('14 mg/dL','13~16.5','g/dL')).level=='unknown'


def test_sex_metadata_survives_normalization_and_export_without_inference():
    kind,data=normalize({'health_checkup_results':[row()],'examinee_sex':'여성'})
    assert document_sex(data)=='female'
    restored=import_records(export_records([record(kind,data,'test.pdf','test',kind)]))[0]
    assert document_sex(restored['data'])=='female'
    assert document_sex({'examinee_sex':'남성','gender':'female'}) is None
    assert document_sex({'examinee_name':'남성 이름','document_title':'남성 건강검진','health_checkup_results':[row(ref='남 13~16.5 / 여 12~15.5')]}) is None
    assert document_sex({'examinee_sex':'확인 필요'}) is None


@pytest.mark.parametrize('remark',['과체중?','정상 여부 확인','정상 아님','과체중 범주 (확인 예정)'])
def test_uncertain_verdict_never_falls_back_to_green(remark):
    assert assess(row('26.8','18.5~30',remark=remark)).level=='unknown'


@pytest.mark.parametrize('remark,level',[
    ('정상 범위 내','normal'),('경계치','caution'),('경계역','caution'),('주의 요망','caution'),('이상 소견','abnormal')])
def test_document_verdict_wording_variants(remark,level):
    assert assess(row(remark=remark)).level==level


@pytest.mark.parametrize('value,level',[('89','normal'),('90','abnormal'),('91','abnormal')])
def test_labeled_thresholds_have_document_defined_meaning(value,level):
    assert assess(row(value,'정상: 90 미만 / 이상: 90 이상')).level==level


def test_sex_changes_only_the_applicable_condition_not_its_meaning():
    value=row('89','남 90 이상, 여 85 이상')
    assert assess(value,'male').text=='문서 임계값 미해당 · 의미 확인 필요'
    assert assess(value,'female').text=='문서 임계값 해당 · 의미 확인 필요'
    assert assess(value).level=='unknown'
    assert assess(row('14 g/dL','13~16.5 (g/dL)','g/dL')).level=='normal'


@pytest.mark.parametrize('value,reference,sex,expected',[
    ('26.8','과체중(25-29.9)',None,'과체중'),
    ('108','100미만',None,'문서 기준 초과'),
    ('216','200미만',None,'문서 기준 초과'),
    ('42','60이상',None,'문서 기준 미달'),
    ('91','남 90 이상, 여 85 이상','male','문서 임계값 해당 · 의미 확인 필요'),
    ('91','남 90 이상, 여 85 이상','female','문서 임계값 해당 · 의미 확인 필요'),
    ('91','남 90 이상, 여 85 이상',None,'확인 필요'),
    ('15.1','남 13-16.5 여 12-15.5','male','문서 범위 내'),
    ('15.1','남 13-16.5 여 12-15.5','female','문서 범위 내'),
    ('15.1','남 13-16.5 여 12-15.5',None,'확인 필요'),
])
def test_required_submission_cases(value,reference,sex,expected):
    result=assess(row(value,reference),sex)
    assert result.text==expected
    if expected=='과체중': assert result.level=='caution'


@pytest.mark.parametrize('value,reference,expected',[
    ('100','100미만','문서 기준 초과'),('100','100이하','문서 범위 내'),
    ('60','60초과','문서 기준 미달'),('60','60이상','문서 범위 내'),
    ('16.5','16.5미만','문서 기준 초과'),('16.49','16.5미만','문서 범위 내'),
])
def test_no_space_threshold_boundary_cases(value,reference,expected):
    assert assess(row(value,reference)).text==expected


@pytest.mark.parametrize('reference',[
    '과체중(25-29.9)','과체중 (25~29.9)','정상(18.5-24.9) / 과체중(25-29.9) / 비만(30이상)',
])
def test_parenthesized_categories_and_readable_reference(reference):
    result=assess(row('26.8',reference))
    assert result.text=='과체중' and result.level=='caution'
    assert '과체중: 25.0~29.9' in result.reference
    assert assess(row('26.8',reference,remark='정상')).basis=='conflict'


@pytest.mark.parametrize('reference',['과체중(25-29.9','과체중25-29.9)','과체중(29.9-25)'])
def test_malformed_category_never_becomes_normal(reference):
    assert assess(row('26.8',reference)).level=='unknown'


def test_summary_and_color_do_not_turn_numeric_comparison_into_diagnosis():
    from carebridge.health import summarize
    results=[assess(row('108','100미만')),assess(row('26.8','과체중(25-29.9)')),
             assess(row('14','13~16.5')),assess(row('14','남13~16.5 여12~15.5')),
             assess(row('108','100미만',remark='이상'))]
    assert summarize(results)=={'total':5,'met':1,'attention':3,'unknown':1}
    assert [result.tone for result in results]==['caution','caution','normal','unknown','abnormal']
    assert results[0].basis=='comparison'
    assert results[-1].basis=='document_verdict'


@pytest.mark.parametrize('name,unit,label',[
    ('BMI(kg/m²)','kg/m²','BMI'),('BMI (kg/m2)','kg/m²','BMI'),
    ('공복혈당(mg/dL)','mg/dL','공복혈당'),('혈색소(g/dL)','g/dL','혈색소'),
    ('혈색소(Hb) [g/dL]','g/dL','혈색소(Hb)'),('허리둘레(cm)','cm','허리둘레'),
    ('AST(SGOT)(U/L)','U/L','AST(SGOT)'),('검사(설명)','','검사(설명)'),
    ('BMI','','BMI'),('검사(mg/dL)(g/dL)','','검사(mg/dL)(g/dL)'),
])
def test_only_explicit_units_are_split_from_item_name(name,unit,label):
    from carebridge.health import measurement_fields
    value=row();value['test_item_name']=name
    original=deepcopy(value)
    fields=measurement_fields(value)
    assert fields.name==label and fields.unit==unit
    assert value==original


def test_existing_unit_has_priority_and_derived_unit_is_used_for_comparison():
    from carebridge.health import measurement_fields
    value=row('108mg/dL','100mg/dL미만','',remark='')
    value['test_item_name']='공복혈당(mg/dL)'
    assert assess(value).text=='문서 기준 초과'
    value['result_unit']='미기재'
    assert measurement_fields(value).unit=='mg/dL'
    value['result_unit']='mmol/L'
    assert measurement_fields(value).unit=='mmol/L'
    assert '(mg/dL)' in measurement_fields(value).name  # Conflicting source units stay visible.
    assert assess(value).level=='unknown'  # Never convert units silently.


@pytest.mark.parametrize('sex,value,expected',[
    ('male','91cm','원문 위험 기준 해당'),('male','90cm','원문 위험 기준 해당'),
    ('female','85cm','원문 위험 기준 해당'),('female','84cm','원문 위험 기준 미해당 · 정상 여부 확인 필요'),
    (None,'91cm','확인 필요'),
])
def test_abdominal_risk_threshold_is_not_an_allowable_normal_range(sex,value,expected):
    data=row(value,'남 90cm이상, 여 85cm이상')
    data['test_item_name']='허리둘레(cm)'
    data['source_excerpt']='허리둘레 복부비만 기준: 남 90cm 이상, 여 85cm 이상'
    original=deepcopy(data)
    result=assess(data,sex)
    assert result.text==expected and result.level!='normal'
    assert result.tone==('caution' if expected=='원문 위험 기준 해당' else 'unknown')
    assert data==original


@pytest.mark.parametrize('value,reference',[
    ('19','위험 기준: 20미만'),('20','위험 기준: 20이하'),
    ('20','주의 기준: 20이상'),('21','주의 기준: 20초과'),
])
def test_risk_direction_comes_from_document_marker_and_operator(value,reference):
    result=assess(row(value,reference))
    assert result.text=='원문 위험 기준 해당' and result.tone=='caution'


def test_meaning_missing_conflicting_or_explicitly_normal():
    value=row('91','남 90 이상, 여 85 이상')
    assert assess(value,'male').level=='unknown'
    value['source_excerpt']='정상 기준: 남 90 이상, 여 85 이상'
    assert assess(value,'male').level=='normal'
    value['source_excerpt']+='; 위험 기준 여부 확인 필요'
    assert assess(value,'male').level=='unknown'
    value['reference_range']='복부비만 기준: 남 90 이상, 여 85 이상'
    value['source_excerpt']='복부비만 기준'
    assert assess(value,'male').text=='원문 위험 기준 해당'
    assert assess(value).level=='unknown'


@__import__('pytest').mark.parametrize('value,reference,remark',[
    ('108','100미만','정상'),
    ('42','60이상','이상 없음'),
    ('15','정상 범위: 10~20','이상'),
    ('26.8','정상(18.5-24.9) / 과체중(25-29.9)','정상'),
])
def test_explicit_verdict_and_clear_numeric_opposition_requires_review(value,reference,remark):
    from copy import deepcopy
    item=row(value,reference,remark=remark)
    original=deepcopy(item)
    result=assess(item)
    assert result.level=='unknown' and result.basis=='conflict'
    assert result.text.startswith('확인 필요')
    assert item==original
