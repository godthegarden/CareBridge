from carebridge.stage_review import stage_review

def test_only_actual_intermediate_evidence_no_accuracy_attribution():
    job={'output':[
        {'step_type':'document-parse','status':'completed','content':[{'type':'output_text','text':'가상 원문 1정'}]},
        {'step_type':'information-extract','status':'completed','content':[{'type':'output_text','text':'가상 추출 9정'}]},
        {'step_type':'document-classify','status':'PRIVATE','content':None},
        {'step_type':['invalid'],'status':'completed'},
    ]}
    result=stage_review(job)
    assert result['parse_text']=='가상 원문 1정'
    assert len(result['steps'])==3
    assert result['steps'][2]['작업 상태']=='확인 필요'
    assert 'PRIVATE' not in str(result)
    assert '정상' not in str(result['steps'])
    assert stage_review({})=={'steps':[],'parse_text':''}
    assert len(stage_review({'output':[{'step_type':'document-parse','content':[{'type':'output_text','text':'a'*40000}]}]})['parse_text'])==30000
