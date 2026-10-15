from datetime import date
import pytest
from carebridge.client import AnalysisError, validate_pdf
from carebridge.domain import normalize,parse_job,range_status,make_schedule,medication_issues
from carebridge.records import record,export_records,import_records

@pytest.mark.parametrize('value,ref,expected', [('22.4','18.5-24.9','문서 범위 내'),('26.1','18.5-24.9','문서 범위 초과'),('미기재','60-79','비교 불가'),('120/80','90-119','비교 불가'),('10','<10','문서 범위 밖'),('10','≤10','문서 범위 내'),('5','남 0-4 / 여 0-6','비교 불가'),('4','5-1','비교 불가')])
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
