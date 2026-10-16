"""Reviewed general information and document-grounded comprehension candidates."""
LIFESTYLE = {
    'food': {'title': '식습관', 'text': '설탕이 많이 든 음료와 과도한 포화지방 섭취를 줄이고, 채소·통곡물 등 다양한 식품을 균형 있게 먹는 습관을 실천해 보세요.',
             'url': 'https://www.who.int/news-room/fact-sheets/detail/healthy-diet'},
    'activity': {'title': '신체활동', 'text': '자신의 몸 상태에 맞춰 걷기 등 일상에서 할 수 있는 신체활동을 꾸준히 이어가 보세요.',
                 'url': 'https://www.who.int/news-room/fact-sheets/detail/physical-activity'},
    'sleep': {'title': '수면', 'text': '잠자리에 드는 시간과 일어나는 시간을 가능한 한 일정하게 유지해 보세요.',
              'url': 'https://www.cdc.gov/sleep/about/'},
}

LIMIT = '검진 결과와 문서 기준의 비교만으로 특정 질병이나 개인별 치료 방법을 확정할 수는 없습니다.'


def question_bank(facts):
    rows = {}
    for fact in facts:
        rows.setdefault(fact['id'].split(':')[0], {})[fact['field']] = fact
    bank = []
    clear_ids = []
    for index, row in rows.items():
        result, comparison = row.get('result'), row.get('comparison')
        # OCR/missing evidence never becomes an authoritative understanding question.
        if not result or not result['quiz_allowed'] or not comparison:
            continue
        name = result['item']
        evidence = [f['id'] for f in row.values() if f['field'] in ('result', 'reference', 'remark', 'comparison')]
        clear_ids.extend(evidence)
        level = comparison['level']
        if level in ('caution', 'abnormal'):
            correct = '문서의 주의 판정이나 참고기준과의 차이를 함께 살펴볼 필요가 있습니다.'
            explanation = name+'의 문서 비교 결과는 ‘'+comparison['value']+'’입니다. 수치와 문서 기준을 함께 확인하는 것이 핵심입니다.'
        elif level == 'normal':
            correct = '문서의 비교 조건을 충족했다는 뜻이며 전체 건강 상태를 확정하는 것은 아닙니다.'
            explanation = name+'은 ‘'+comparison['value']+'’로 비교되었습니다. 문서 기준 충족과 의학적 정상 확정은 다른 의미입니다.'
        else:
            correct = '판정에 필요한 정보나 서로 다른 근거를 확인하기 전에는 결론을 정하지 않습니다.'
            explanation = name+'은 ‘'+comparison['value']+'’입니다. 정보 부족이나 근거 불일치를 임의로 정상·이상 판정으로 바꾸지 않습니다.'
        bank.append({'id': index+':relationship', 'theme': 'relationship',
                     'question': name+'의 결과와 문서 기준을 함께 읽을 때 알맞은 이해는 무엇인가요?',
                     'answer': correct, 'options': [correct, '이 결과 하나로 특정 질병을 확정할 수 있습니다.', '참고기준이나 원문 판정은 살펴볼 필요가 없습니다.'],
                     'explanation': explanation, 'evidence': evidence})
        meaning = row.get('meaning')
        if meaning:
            answer = meaning['value']
            bank.append({'id': index+':meaning', 'theme': 'meaning', 'question': name+'은 어떤 정보를 이해하는 데 쓰이는 항목인가요?',
                         'answer': answer, 'options': [answer, '개인에게 필요한 약의 종류와 복용량을 정하는 항목입니다.', '이 항목 하나로 전체 건강 상태를 확정하는 항목입니다.'],
                         'explanation': answer+' 이는 일반 용어 설명이며 개인별 진단은 아닙니다.', 'evidence': [meaning['id']]})
    if clear_ids:
        answer = '특정 질병의 진단이나 개인별 치료 방법입니다.'
        bank.append({'id': 'report:limits', 'theme': 'limits', 'question': '이 검진 결과와 참고기준의 비교만으로 확정할 수 없는 것은 무엇인가요?',
                     'answer': answer, 'options': [answer, '문서에 어떤 검사항목이 적혀 있는지입니다.', '문서에 어떤 참고기준이 적혀 있는지입니다.'],
                     'explanation': LIMIT, 'evidence': list(dict.fromkeys(clear_ids))})
    levels = {f['id'].split(':')[0]: f['level'] for f in facts if f['field']=='comparison'}
    relationships = sorted([q for q in bank if q['theme']=='relationship'],
                           key=lambda q: {'caution':0,'abnormal':0,'unknown':1,'normal':2}[levels[q['id'].split(':')[0]]])[:3]
    meanings = [q for q in bank if q['theme']=='meaning'][:2]
    return relationships + meanings + [q for q in bank if q['theme']=='limits']


def validate_report_selection(plan, payload, fail):
    lifestyle = plan['lifestyle']
    if (not isinstance(lifestyle, list) or not 2 <= len(lifestyle) <= 3 or
            any(not isinstance(key, str) or key not in LIFESTYLE for key in lifestyle) or len(set(lifestyle)) != len(lifestyle)):
        fail('생활습관 정보는 검증된 일반 건강정보 2~3개만 선택할 수 있습니다.')
    bank = {q['id']: q for q in payload['understanding_questions']}
    quiz = plan['quiz']
    if not isinstance(quiz, list) or (len(bank) >= 2 and len(quiz) not in (2, 3)) or (len(bank) < 2 and quiz):
        fail('명확한 이해도 문제 근거 또는 문항 수가 올바르지 않습니다.')
    selected = []
    for item in quiz:
        if not isinstance(item, dict) or set(item) != {'question_id'} or not isinstance(item['question_id'], str) or item['question_id'] not in bank:
            fail('수치 암기 질문이나 검증되지 않은 문제는 표시할 수 없습니다.')
        selected.append(item['question_id'])
    if len(set(selected)) != len(selected):
        fail('이해도 질문이 중복되어 있습니다.')
    if quiz and (not any(bank[key]['theme']=='relationship' for key in selected) or
                 not any(bank[key]['theme'] in ('meaning', 'limits') for key in selected)):
        fail('이해도 질문은 기준과의 관계 및 의미·판단의 한계를 함께 다뤄야 합니다.')
