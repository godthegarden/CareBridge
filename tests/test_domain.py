from datetime import date
import pytest
from carebridge.client import AnalysisError, validate_pdf
from carebridge.domain import normalize,parse_job,range_status,make_schedule,medication_issues
from carebridge.records import record,export_records,import_records

@pytest.mark.parametrize('value,ref,expected', [('22.4','18.5-24.9','문서 범위 내'),('26.1','18.5-24.9','문서 범위 초과'),('미기재','60-79','확인 필요'),('120/80','90-119','추가 대조 필요'),('10','<10','문서 기준 초과'),('10','≤10','문서 범위 내'),('5','남 0-4 / 여 0-6','확인 필요'),('4','5-1','추가 대조 필요')])
def test_ranges(value,ref,expected):
    assert range_status({'result_value':value,'reference_range':ref})==expected

def med(): return {'medication_name':'검증용','single_dose':'1정','times_per_day':'2','days_of_administration':'3','administration_timing':'식후','source_excerpt':'검증용 1정 하루 2회 3일 식후'}

def test_schedule_requires_confirmation_and_no_invention():
    r=med()
    with pytest.raises(ValueError):make_schedule(r,date(2026,1,1),['08:00','18:00'],False)
    r['days_of_administration']=''
    assert medication_issues(r)
    with pytest.raises(ValueError):make_schedule(r,date(2026,1,1),['08:00','18:00'],True)

@pytest.mark.parametrize('clocks',[['08:00'],['08:00','08:00'],['25:00','18:00']])
def test_bad_clocks(clocks):
    with pytest.raises(ValueError):make_schedule(med(),date(2026,1,1),clocks,True)

def test_schedule_boundaries():
    events=make_schedule(med(),date(2026,1,31),['08:00','18:00'],True)
    assert len(events)==6 and events[-1]['일시']=='2026-02-02T18:00'

def test_schema_and_import_provenance():
    k,d=normalize({'medication_instructions':[med()]})
    r=record(k,d,'test.pdf','job-test')
    imported=import_records(export_records([r]))
    assert imported[0]['data']==d and '재검증 안 됨' in imported[0]['origin']
    assert imported[0]['job_id']==''

@pytest.mark.parametrize('data',[{}, {'health_checkup_results':[]},{'health_checkup_results':[{}],'medication_instructions':[{}]}, {'medication_instructions':['bad']}])
def test_invalid_schema(data):
    with pytest.raises(AnalysisError):normalize(data)

def test_invalid_pdf():
    with pytest.raises(AnalysisError):validate_pdf(b'not pdf')

def test_no_extract():
    with pytest.raises(AnalysisError):parse_job({'output':[]})


def test_medication_issues_preserve_frequency_meaning_and_cautions():
    row=med()
    row['caution_notes']='원문 주의사항'
    assert medication_issues(row)==['횟수가 하루 기준인지 확인 필요']
    row['administration_timing']=''
    assert '복용 시점 확인 필요' in medication_issues(row)
    assert not any('주의사항' in issue for issue in medication_issues(row))
    row['medication_name']='OCR 판독 불확실'
    assert '약품명 확인 필요' in medication_issues(row)


def classification_step(value, status='completed'):
    return {'step_type':'document-classify','status':status,'content':[{'type':'output_text','text':value}]}


@pytest.mark.parametrize('value,expected',[
    ('medication_guidance_record','medication_guidance_record'),
    ('health_checkup_result','health_checkup_result'),
    ('"health_checkup_result"','health_checkup_result'),
    ('unsupported',None),('',None),('{"type":"health_checkup_result"}',None),
])
def test_actual_classify_step_format(value,expected):
    from carebridge.domain import classify_job
    assert classify_job({'output':[classification_step(value)]})==expected


def test_classification_is_never_inferred_from_extract_or_filename():
    from carebridge.domain import classify_job,analysis_record_data
    import json
    extraction={'step_type':'information-extract','content':[{'type':'output_text','text':json.dumps({'medication_instructions':[med()]})}]}
    assert classify_job({'output':[extraction],'filename':'health_checkup_result.pdf'}) is None
    assert analysis_record_data({'output':[extraction]})[2] is None
    step=classification_step('medication_guidance_record')
    assert classify_job({'output':[step,step]}) is None
    assert classify_job({'output':[classification_step('health_checkup_result','failed')]}) is None
    assert analysis_record_data({'output':[classification_step('unsupported')]})==('unclassified',{},None)


def test_stored_live_classify_responses():
    import json
    from pathlib import Path
    from carebridge.domain import analysis_record_data
    paths=list(Path('.local/live').glob('sample?-raw.json'))
    if not paths: pytest.skip('저장된 실제 응답 없음')
    for path in paths:
        kind,data,classification=analysis_record_data(json.loads(path.read_text()))
        assert kind==classification and data


def test_classification_survives_export_and_legacy_stays_unknown():
    k,d=normalize({'medication_instructions':[med()]})
    r=record(k,d,'test.pdf','test',classification=k)
    assert import_records(export_records([r]))[0]['classification']==k
    r.pop('classification')
    assert import_records(export_records([r]))[0]['classification'] is None
    empty=record('unclassified',{},'test.pdf','test')
    assert import_records(export_records([empty]))[0]['kind']=='unclassified'


def test_completed_job_with_unavailable_extract_is_an_explicit_error():
    from carebridge.domain import analysis_record_data
    with pytest.raises(AnalysisError,match='작업은 완료됐지만'):
        analysis_record_data({'status':'completed','output':[classification_step('medication_guidance_record'),{'step_type':'information-extract','status':'in_progress','content':None}]})
