"""Gateway structured-output contract matching the existing local response validator."""
import os


def response_format(payload):
    mode = os.environ.get('FACTCHAT_RESPONSE_FORMAT','json_schema').strip()
    if mode == 'prompt_json':
        # Explicit operator choice for an unsupported model. Never auto-retry a POST.
        return None
    if mode != 'json_schema':
        raise ValueError('FACTCHAT_RESPONSE_FORMAT는 json_schema 또는 prompt_json이어야 합니다.')
    def obj(properties):
        return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}
    def arr(items,low,high):
        return {'type':'array','items':items,'minItems':low,'maxItems':high}
    text={'type':'string'}
    ids=[fact['id'] for fact in payload['facts']]
    block=obj({'text':text,'evidence':arr({'type':'string','enum':ids},1,60)})
    report=bool(payload.get('report_version'))
    if report:
        questions=[q['id'] for q in payload['understanding_questions']]
        quiz=obj({'question_id':{'type':'string','enum':questions or ['__no_question__']}})
        schema=obj({'summary':arr(block,3,5),'details':arr(block,0,8),
                    'lifestyle':arr({'type':'string','enum':['food','activity','sleep']},2,3),
                    'quiz':arr(quiz,2 if len(questions)>=2 else 0,3 if len(questions)>=2 else 0)})
    else:
        eligible=[fact['id'] for fact in payload['facts'] if fact['quiz_allowed']]
        quiz=obj({'fact_id':{'type':'string','enum':eligible or ['__no_question__']},'explanation':block})
        schema=obj({'summary':arr(block,2,3),'details':arr(block,1,60),
                    'quiz':arr(quiz,2 if len(eligible)>=2 else 0,3 if len(eligible)>=2 else 0)})
    return {'type':'json_schema','json_schema':{'name':'carebridge_health_report' if report else 'carebridge_medication_explanation',
                                               'strict':True,'schema':schema}}
