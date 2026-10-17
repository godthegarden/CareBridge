"""Synthetic results only; no document or Gateway calls."""
from pathlib import Path
from unittest.mock import Mock
import pytest
import requests
from streamlit.testing.v1 import AppTest
from carebridge import factchat as fc
from carebridge.domain import normalize
from carebridge.records import record
from carebridge.explanation_content import term_definition


@pytest.mark.parametrize('name',[
    'BMI','공복혈당(FBS)','총콜레스테롤','HDL-콜레스테롤','LDL-C','중성지방(TG)',
    'e-GFR','AST(SGOT)','ALT(SGPT)','γ-GTP','혈압','수축기혈압','이완기혈압',
    '혈색소(Hb)','허리둘레','키/몸무게','시력(좌/우)','혈청크레아티닌',
])
def test_reviewed_glossary_covers_extracted_common_names(name):
    meaning,url=term_definition(name)
    assert len(meaning)<100 and url.startswith('https://')
    assert not any(word in meaning for word in ('당신은','중단하세요','복용하세요'))
    assert term_definition('미지원 검사명') is None


def test_compound_readings_source_verdict_and_unsupported_terms_reach_final_ui(monkeypatch):
    monkeypatch.setenv('FACTCHAT_API_KEY','')
    monkeypatch.setattr(fc,'load_dotenv',lambda *a,**kw:None)
    network=Mock(side_effect=AssertionError('Real HTTP forbidden'))
    monkeypatch.setattr(requests.sessions.Session,'request',network)
    data={'health_checkup_results':[
        {'test_item_name':'키/몸무게','result_value':'170cm / 70kg'},
        {'test_item_name':'혈압','result_value':'120/80','remark':'☑ 정상 ☐ 이상'},
        {'test_item_name':'시력(좌/우)','result_value':'좌 1.0 / 우 0.9','reference_range':'좌우 각각 0.8이상'},
        {'test_item_name':'허리둘레(cm)','result_value':'91','reference_range':'남 90 이상, 여 85 이상',
         'source_excerpt':'복부비만 기준: 남 90 이상, 여 85 이상'},
        {'test_item_name':'미지원 검사명','result_value':'문자 결과'}],
        'examinee_sex':'남성'}
    kind,clean=normalize(data)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=15).run()
    app.session_state['records']=[record(kind,clean,'synthetic.pdf','test',kind)]
    app.sidebar.radio[0].set_value('건강검진').run()
    assert not app.exception
    def visible(node):
        yield node
        if node.type=='expander': return
        for child in getattr(node,'children',{}).values(): yield from visible(child)
    body='\n'.join(n.value for n in visible(app.main) if n.type in ('markdown','text','caption'))
    for text in ('기본 측정값','170cm / 70kg','120/80','원문 판정 · 정상','☑ 정상 ☐ 이상',
                 '좌 1.0 / 우 0.9','남성: 90 이상 / 여성: 85 이상','원문 위험 기준 해당',
                 '고정 용어 설명 미지원: 미지원 검사명','개인의 건강 상태를 판정하는 설명이 아닙니다.'):
        assert text in body
    assert '측정값을 단일 숫자로' not in body
    assert not any('<table' in n.value for n in visible(app.main) if n.type=='markdown')
    assert app.session_state['records'][0]['data']==clean
    app.run()
    assert not app.exception and not network.called


@pytest.mark.parametrize('name,value', [('키','170'),('몸무게','70'),('키/몸무게','170 / 70'),('시력(좌/우)','1.0 / 0.9')])
def test_body_and_vision_without_criteria_are_measurements_not_attention(name,value):
    from carebridge.checkup_reading import measurement_only
    row={'test_item_name':name,'result_value':value,'reference_range':''}
    assert measurement_only(row)
    assert not measurement_only({**row,'remark':'이상'})
    assert not measurement_only({**row,'result_value':''})
    assert not measurement_only({**row,'result_value':'108','reference_range':'100미만'})


def test_missing_extracted_waist_reference_does_not_claim_source_has_none():
    from carebridge.health import assess
    from carebridge.checkup_reading import measurement_only
    row={'test_item_name':'허리둘레(cm)','result_value':'91','reference_range':'',
         'source_excerpt':'허리둘레 복부비만(남 90 이상, 여 85 이상)'}
    result=assess(row,'male')
    assert result.reference=='추출값에서 확인되지 않음'
    assert result.level=='unknown' and result.basis=='no_reference'
    assert not measurement_only(row,'male')
    # Do not inject common clinical thresholds to repair a missing Extract field.
    assert row['reference_range']==''
    extracted={**row,'reference_range':'복부비만 기준: 남 90 이상, 여 85 이상'}
    assert assess(extracted,'male').text=='원문 위험 기준 해당'
    assert assess(extracted).level=='unknown'
