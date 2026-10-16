from datetime import date
from copy import deepcopy
import pytest
from carebridge.medication_validation import field_errors, variable_schedule
from carebridge.domain import make_schedule

ROW={'medication_name':'가상 약','single_dose':'1정','times_per_day':'2','days_of_administration':'3','administration_timing':'식후'}

@pytest.mark.parametrize('dose',['1정','0.5정','1/2정','2.5mL','10mg','1캡슐'])
def test_explicit_positive_dose_is_not_converted(dose):
    row={**ROW,'single_dose':dose}
    assert not field_errors(row)
    assert make_schedule(row,date(2026,10,16),['09:00','18:00'],True)[0]['1회량']==dose

@pytest.mark.parametrize('field,value',[
    ('single_dose','1'),('single_dose','0정'),('single_dose','-1정'),('single_dose','1/0정'),
    ('single_dose','1~2정'),('single_dose','OCR 불확실'),('times_per_day','2~3회'),
    ('times_per_day','0'),('times_per_day','13'),('days_of_administration','367'),
    ('days_of_administration','필요시'),('administration_timing','미상'),
    ('administration_timing','격일 식후')])
def test_unresolved_field_cannot_pass_backend_even_with_approval(field,value):
    row={**ROW,field:value}
    before=deepcopy(row)
    assert field in field_errors(row,schedule=True)
    with pytest.raises(ValueError): make_schedule(row,date(2026,10,16),['09:00','18:00'],True)
    assert row==before

@pytest.mark.parametrize('text',['필요 시 복용','주 3회','매주','상충 지시','통증 시'])
def test_non_daily_original_requires_review(text):
    row={**ROW,'source_excerpt':text}
    assert variable_schedule(row)
    assert 'administration_timing' in field_errors(row)
    with pytest.raises(ValueError): make_schedule(row,date(2026,10,16),['09:00','18:00'],True)


def test_backend_preserves_original_non_daily_restriction_after_form_edits():
    with pytest.raises(ValueError,match='고정 일일 일정'):
        make_schedule(ROW,date(2026,10,16),['09:00','18:00'],True,original={**ROW,'source_excerpt':'필요시 복용'})
    assert len(make_schedule(ROW,date(2026,10,16),['09:00','18:00'],True,original=ROW))==6
