"""Optional explanation controls; rendering alone never calls the Gateway."""
from contextlib import nullcontext
import streamlit as st
from .factchat import (configuration, build_facts, fingerprint, generate_once,
                       ExplanationError, LABELS)
from .explanation_content import question, quiz_options, term_definition
from .ui import lines, badge
from .health_report import LIFESTYLE
from .health import assess, document_sex, measurement_fields
from .checkup_reading import related_guides, result_context
from .health_safety import AI_NOTICE, text_kind


def easy_explanation(record, *, compact=False, render=True, collapse_controls=False):
    panel=None
    def controls():
        nonlocal panel
        if not collapse_controls: return nullcontext()
        if panel is None: panel=st.expander('AI 설명 생성 · 외부 전송 동의',expanded=False)
        return panel
    rid = record['id']
    cache = st.session_state.setdefault('factchat_results', {})
    panels = st.session_state.setdefault('factchat_panels', {})
    try:
        _, model = configuration()
    except ExplanationError:
        with controls():
            st.button('쉬운 설명 보기', key=rid+'easy_open', disabled=True)
            st.caption('쉬운 설명 기능이 아직 준비되지 않았습니다. 아래 분석 결과는 그대로 이용할 수 있습니다.')
            return None
    try:
        payload = build_facts(record['data'],safe_only=record.get('explanation_mode')=='structured_only')
        signature = fingerprint(payload, model)
    except ExplanationError as exc:
        with controls():
            st.button('쉬운 설명 보기', key=rid+'easy_open', disabled=True)
            st.caption(str(exc))
            return None
    entry = cache.get(rid)
    if entry and entry['key'] != signature:
        cache.pop(rid, None)
        entry = None
    if not entry:
        with controls():
            if compact:
                panels[rid] = True
            elif st.button('쉬운 설명 보기', key=rid+'easy_open'):
                panels[rid] = True
            if not panels.get(rid):
                return None
            with st.container(border=True):
                st.write('외부 전송 전에 확인해 주세요')
                st.caption('학교 FactChat Gateway(api.baze.co)에 아래 최소 추출 항목과 문서 기준 비교 결과·일반 용어 뜻을 전송합니다. 이름·문서명·날짜·원본 파일·원문 전체는 보내지 않습니다. 건강정보가 포함되므로 전송 목록을 확인해 주세요.')
                st.caption('자동 호출하지 않습니다. 같은 분석의 응답과 실패 상태를 현재 세션에 재사용하며, 서버의 초기 생성 한도는 1회입니다.')
                with st.expander('전송할 항목 미리보기'):
                    st.dataframe([{'항목': f['item'], '정보': LABELS[f['field']], '전송값': f['value']}
                                  for f in payload['facts']], hide_index=True, use_container_width=True)
                consent = st.checkbox('전송할 항목에 불필요한 개인정보가 없음을 확인하고 학교 FactChat Gateway 전송에 동의합니다.',
                                      key=rid+signature+'easy_consent')
                if st.button('동의한 정보로 설명 생성', key=rid+'easy_generate', disabled=not consent):
                    with st.spinner('문서 근거를 정리하고 있습니다…'):
                        entry = generate_once(cache, rid, record['data'], consent=consent,safe_only=record.get('explanation_mode')=='structured_only')
                    if collapse_controls and entry and entry['state']=='complete': st.rerun()
    if not entry:
        return None
    with controls() if entry['state']!='complete' or entry.get('result',{}).get('source')!='factchat_generated' else nullcontext():
        if entry['state'] == 'error' and payload.get('report_version'):
            st.info('AI 설명을 생성하지 못해 기본 요약을 표시합니다')
            return None
        if entry['state'] == 'error':
            st.warning('AI 설명 생성 실패 · '+entry['error'])
            st.caption('자동 재요청하지 않습니다. 아래는 AI 생성 설명이 아닌 추출값·규칙 기반 기본 안내이며 계속 사용할 수 있습니다.')
            return None
        if entry['state'] == 'pending':
            st.info('이미 접수한 설명 요청을 처리 중입니다. 다시 요청하지 않습니다.')
            return None
        result = entry.get('result', {})
        if result.get('source') != 'factchat_generated':
            st.warning('이전 형식의 응답에는 AI 설명 문장이 없습니다. 기본 문서 안내를 확인해 주세요.')
            return None
    if entry['payload'].get('report_version'):
        if render: health_report(entry)
        return entry
    st.markdown('### AI가 풀어쓴 문서 설명')
    st.caption('FactChat 생성 설명 · 응답 수신 및 근거 검증 완료. 원문 추출값과 대조해 읽어 주세요.')
    st.caption(AI_NOTICE)
    plan = result['plan']
    st.markdown('<div class="result-note">'+lines(' '.join(('일반 건강정보: ' if text_kind(part['text'])=='general_health' else '')+part['text'] for part in plan['summary']))+'</div>', unsafe_allow_html=True)
    with st.expander('전체 요약의 근거 확인'):
        show_evidence({'evidence': list(dict.fromkeys(fid for part in plan['summary'] for fid in part['evidence']))}, entry['payload'])
    for detail in plan['details']:
        st.markdown('<p>'+lines(detail['text'])+'</p>', unsafe_allow_html=True)
        with st.expander('이 설명의 근거 확인', expanded=False):
            show_evidence(detail, entry['payload'])
    return entry


def show_evidence(block, payload):
    facts = {fact['id']: fact for fact in payload['facts']}
    for fid in block['evidence']:
        fact = facts[fid]
        origin = {'document': '문서 추출값', 'rule': '기본 규칙', 'glossary': '일반 용어 설명'}.get(fact['origin'], '')
        st.text(origin+' · '+fact['item']+' · '+LABELS[fact['field']]+': '+fact['value'])
        if fact['origin'] == 'glossary':
            st.markdown('[용어 설명 출처]('+fact['url']+')')


def comprehension(record, entry):
    if not entry:
        return
    if entry['payload'].get('report_version'):
        health_comprehension(record, entry)
        return
    st.markdown('### 문서 이해도 확인')
    st.caption('문서에 적힌 내용을 고르는 객관식입니다. 답안은 원문 추출값으로 채점하며 복약 일정에 반영하지 않습니다.')
    facts = {f['id']: f for f in entry['payload']['facts']}
    quizzes = entry['result']['plan']['quiz']
    if not quizzes:
        st.info('명확한 원문 근거가 충분하지 않아 문제를 만들지 않았습니다.')
        return
    answers = st.session_state.setdefault('factchat_answers', {})
    prefix = record['id']+entry['key']
    chosen = []
    for index, quiz in enumerate(quizzes):
        fact = facts[quiz['fact_id']]
        widget_key = prefix+str(index)+'answer'
        if widget_key not in st.session_state:
            st.session_state[widget_key] = answers.get(widget_key)
        def remember(key=widget_key):
            answers[key] = st.session_state[key]
            answers.pop(prefix+'graded', None)
        chosen.append(st.radio(f'{index+1}. '+question(fact), quiz_options(fact), index=None,
                               key=widget_key, on_change=remember))
    if st.button('선택한 답 채점하기', key=prefix+'grade', disabled=any(value is None for value in chosen)):
        answers[prefix+'graded'] = list(chosen)
    submitted = answers.get(prefix+'graded')
    if submitted == chosen:
        correct = [answer == facts[quiz['fact_id']]['value'] for answer, quiz in zip(submitted, quizzes)]
        st.success(f'{len(quizzes)}문항 중 {sum(correct)}문항 정답')
        for index, (quiz, matched) in enumerate(zip(quizzes, correct)):
            fact = facts[quiz['fact_id']]
            st.write(f'{index+1}번 · '+('정답입니다.' if matched else '원문을 다시 확인해 보세요.'))
            st.text('정답: '+fact['value'])
            st.markdown('<p>'+lines(quiz['explanation']['text'])+'</p>', unsafe_allow_html=True)
            with st.expander(f'{index+1}번 원문 근거'):
                show_evidence(quiz['explanation'], entry['payload'])


HEALTH_NOTICE = '본 내용은 AI가 문서를 바탕으로 생성한 참고용 설명이며, 의료진의 진단이나 처방을 대신하지 않습니다. 중요한 건강 판단은 원문과 의료전문가를 통해 확인해 주세요.'


def health_report(entry):
    plan = entry['result']['plan']
    st.markdown('### AI 핵심 요약')
    st.caption('FactChat 핵심 요약 · 문서 근거 검증 완료')
    st.markdown('<div class="result-note">'+lines(' '.join(('일반 건강정보: ' if text_kind(part['text'])=='general_health' else '')+part['text'] for part in plan['summary']))+'</div>', unsafe_allow_html=True)


def checkup_context(data):
    """Two concise cards; factual tables and source text live in detail views."""
    with st.container(border=True):
        st.markdown('### 어려운 검사 용어 설명')
        defined=set()
        sources=[]
        unsupported=[]
        st.caption('검사 용어의 일반적인 뜻이며, 개인의 건강 상태를 판정하는 설명이 아닙니다.')
        for row in data['health_checkup_results']:
            name=measurement_fields(row).name
            definition=term_definition(name)
            if definition and definition not in defined:
                defined.add(definition)
                sources.append((name,definition[1]))
                st.markdown('<p><strong>'+lines(name)+'</strong> · '+lines(definition[0])+'</p>',unsafe_allow_html=True)
            elif not definition and name not in unsupported:
                unsupported.append(name)
        if not defined: st.caption('추출된 항목에 해당하는 고정 용어 설명이 없습니다.')
        if unsupported:
            st.text('고정 용어 설명 미지원: '+', '.join(unsupported))
        if sources:
            with st.expander('용어 설명 출처',expanded=False):
                for name,url in sources: st.markdown('[용어 설명 출처 · '+name+']('+url+')')
    with st.container(border=True):
        st.markdown('### 검사 결과 의미 해설')
        sex=document_sex(data)
        candidates=sorted(data['health_checkup_results'],key=lambda row:
                          0 if assess(row,sex).level in ('caution','abnormal') or assess(row,sex).basis=='conflict' else
                          1 if result_context(row,sex).startswith(('원문 기존','원문 의심','원문 신규','기록 구분')) else
                          2 if assess(row,sex).level=='unknown' else 3)
        for row in candidates[:5]:
            measurement=measurement_fields(row)
            assessment=assess(row,sex)
            value=row.get('result_value') or '미기재'
            if measurement.unit and measurement.unit not in value:
                value+=' '+measurement.unit
            facts='측정값: '+value+' · 참고기준: '+assessment.reference
            remark=('원문 판정·비고: '+row['remark']) if row.get('remark') else ''
            st.markdown('<p><strong>'+lines(measurement.name)+'</strong> · '+badge(assessment.text,tone=assessment.tone)+'<br>'+lines(facts)+
                        ('<br>'+lines(remark) if remark else '')+'<br>'+lines(result_context(row,sex))+'</p>',unsafe_allow_html=True)
        if len(candidates)>5: st.caption('주요 5개 항목입니다. 전체 항목은 상세 보기에서 확인하세요.')
        if not candidates: st.caption('추출된 항목은 문서 기준으로 비교되었습니다. 기준 충족만으로 전체 건강 상태를 확정하지 않습니다.')


def general_health_guide(data):
    """Reviewed local WHO information, independent of generated explanations."""
    with st.container(border=True):
        st.markdown('### 생활습관 정보 제공')
        keys=related_guides(data)
        if not keys:
            st.caption('이 문서의 항목에 직접 연결할 고정 가이드가 없습니다. 원문 확인과 일반 정보는 의료전문가에게 문의할 수 있습니다.')
            return
        st.caption('WHO 자료 기반 일반 건강정보입니다. 개인별 진단이나 치료 권고가 아닙니다.')
        for key in keys:
            guide=LIFESTYLE[key]
            st.markdown('**'+guide['title']+'**')
            st.markdown('<p>'+lines(guide['text'])+'</p>',unsafe_allow_html=True)
            st.markdown('[WHO 공식 출처 · '+guide['title']+']('+guide['url']+')')


def health_comprehension(record, entry):
    st.markdown('### 문서 이해도 확인')
    st.caption('숫자 암기 대신 결과를 읽는 방법을 확인해 보세요. 선택하면 해설이 표시됩니다.')
    bank = {q['id']: q for q in entry['payload']['understanding_questions']}
    quizzes = [bank[item['question_id']] for item in entry['result']['plan']['quiz']]
    if not quizzes:
        st.info('의미와 정답을 명확히 확인할 근거가 부족해 문제를 표시하지 않습니다.')
        return
    answers = st.session_state.setdefault('factchat_answers', {})
    for index, quiz in enumerate(quizzes):
        widget_key = record['id']+entry['key']+str(index)+'understanding'
        if widget_key not in st.session_state:
            st.session_state[widget_key] = answers.get(widget_key)
        def remember(key=widget_key):
            answers[key] = st.session_state[key]
        offset = sum(ord(c) for c in quiz['id']) % 3
        options = quiz['options'][offset:]+quiz['options'][:offset]
        chosen = st.radio(f'{index+1}. '+quiz['question'], options, index=None, key=widget_key, on_change=remember)
        if chosen is not None:
            st.caption('정답입니다.' if chosen==quiz['answer'] else '다시 확인해 보세요. 정답: '+quiz['answer'])
            st.markdown('<p>'+lines(quiz['explanation'])+'</p>', unsafe_allow_html=True)
            with st.expander(f'{index+1}번 설명의 근거', expanded=False):
                show_evidence({'evidence': quiz['evidence']}, entry['payload'])
