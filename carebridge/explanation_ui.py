"""Optional explanation controls; rendering alone never calls the Gateway."""
import streamlit as st
from .factchat import (configuration, build_facts, fingerprint, generate_once,
                       ExplanationError, explanation, question, LABELS)


def easy_explanation(record):
    rid = record['id']
    cache = st.session_state.setdefault('factchat_results', {})
    panels = st.session_state.setdefault('factchat_panels', {})
    try:
        _, model = configuration()
    except ExplanationError:
        st.button('쉬운 설명 보기', key=rid+'easy_open', disabled=True)
        st.caption('쉬운 설명 기능이 아직 준비되지 않았습니다. 아래 분석 결과는 그대로 이용할 수 있습니다.')
        return None
    try:
        payload = build_facts(record['data'])
        signature = fingerprint(payload, model)
    except ExplanationError as exc:
        st.button('쉬운 설명 보기', key=rid+'easy_open', disabled=True)
        st.caption(str(exc))
        return None
    entry = cache.get(rid)
    if entry and entry['key'] != signature:
        cache.pop(rid, None)
        entry = None
    if not entry:
        if st.button('쉬운 설명 보기', key=rid+'easy_open'):
            panels[rid] = True
        if not panels.get(rid):
            return None
        with st.container(border=True):
            st.write('외부 전송 전에 확인해 주세요')
            st.caption('학교 FactChat Gateway(api.baze.co)에 아래 검사·복약 추출 항목을 전송합니다. 이름·문서명·날짜·원본 파일·원문 전체는 보내지 않습니다. 건강정보가 포함되므로 전송 목록을 확인해 주세요.')
            st.caption('자동 호출하지 않습니다. 같은 분석의 응답과 실패 상태를 현재 세션에 재사용하며, 서버의 초기 생성 한도는 1회입니다.')
            with st.expander('전송할 항목 미리보기'):
                st.dataframe([{'항목': f['item'], '정보': LABELS[f['field']], '전송값': f['value']}
                              for f in payload['facts']], hide_index=True, use_container_width=True)
            consent = st.checkbox('전송할 항목에 불필요한 개인정보가 없음을 확인하고 학교 FactChat Gateway 전송에 동의합니다.',
                                  key=rid+signature+'easy_consent')
            if st.button('동의한 정보로 설명 생성', key=rid+'easy_generate', disabled=not consent):
                with st.spinner('문서 근거를 정리하고 있습니다…'):
                    entry = generate_once(cache, rid, record['data'], consent=consent)
    if not entry:
        return None
    if entry['state'] == 'error':
        st.warning(entry['error'])
        st.caption('쉬운 설명은 다시 요청하지 않습니다. 아래 기존 분석 결과와 기능은 계속 사용할 수 있습니다.')
        return None
    if entry['state'] == 'pending':
        st.info('이미 접수한 설명 요청을 처리 중입니다. 다시 요청하지 않습니다.')
        return None
    st.markdown('### 쉽게 읽는 문서 안내')
    st.caption('FactChat이 근거를 선택하고 검증된 문구로 구성한 안내입니다. 추출 정확도는 원본과 대조해 주세요.')
    facts = {f['id']: f for f in entry['payload']['facts']}
    for fid in entry['result']['plan']['summary']:
        st.text(explanation(facts[fid]))
    if entry['result']['plan']['attention']:
        with st.expander('주의 깊게 확인할 문서 정보', expanded=True):
            for fid in entry['result']['plan']['attention']:
                st.text(explanation(facts[fid]))
    return entry


def comprehension(record, entry):
    if not entry:
        return
    st.markdown('### 문서 이해도 확인')
    st.caption('문서에 적힌 정보를 찾아보는 질문입니다. 진단·치료 판단을 평가하지 않습니다.')
    facts = {f['id']: f for f in entry['payload']['facts']}
    ids = entry['result']['plan']['quiz']
    if not ids:
        st.info('명확한 근거가 충분하지 않아 질문을 만들지 않았습니다.')
    for index, fid in enumerate(ids):
        fact = facts[fid]
        prefix = record['id']+entry['key']+str(index)
        with st.container(border=True):
            st.text(f'{index+1}. '+question(fact))
            # Own persistent dictionary keeps drafts/answer visibility across navigation.
            answers = st.session_state.setdefault('factchat_answers', {})
            widget_key = prefix+'answer'
            if widget_key not in st.session_state:
                st.session_state[widget_key] = answers.get(widget_key, '')
            def remember(key=widget_key):
                answers[key] = st.session_state[key]
            st.text_input('내가 이해한 내용', key=widget_key, on_change=remember)
            if st.button('정답과 근거 확인', key=prefix+'reveal'):
                answers[prefix+'revealed'] = True
            if answers.get(prefix+'revealed'):
                st.text('문서에 적힌 답: '+fact['value'])
                st.caption('근거: '+fact['item']+' · '+LABELS[fact['field']]+' · 아래 원문 대조 영역에서 확인할 수 있습니다.')
    st.caption('횟수·시점·기간이 불확실한 경우 이 질문의 답을 복약 일정에 자동 반영하지 않습니다.')
