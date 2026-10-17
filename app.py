import csv
import io
import logging
import time
from datetime import date
import streamlit as st
from carebridge.client import AgentClient, AnalysisError, ROOT
from carebridge.domain import analysis_record_data, normalize, GROUPS, medication_issues, make_schedule, missing
from carebridge.documents import original_type
from carebridge.records import record, export_records, import_records
from carebridge.ui import summary_html, table_html, lines, badge
from carebridge.health import assess, document_sex, summarize, measurement_fields
from carebridge.explanation_ui import easy_explanation, comprehension, general_health_guide, checkup_context, health_report, show_evidence, HEALTH_NOTICE
from carebridge.medication_validation import field_errors, variable_schedule
from carebridge.stage_review import stage_review
from carebridge.checkup_reading import document_opinions
from carebridge.analysis_flow import BUSY, set_upload, queue, run_queued

logger=logging.getLogger('carebridge.app')
logger.setLevel(logging.INFO)

st.set_page_config(page_title='CareBridge · 나를 이해하는 건강기록',page_icon='🌿',layout='wide')
st.markdown('''<style>
/* Keep the first page label below Streamlit's fixed header on every viewport. */
.block-container{max-width:1080px;padding-top:5rem;padding-bottom:3rem}
h1,h2,h3{letter-spacing:-.035em}h1{font-weight:750!important;font-size:2rem!important}h3{font-size:1.25rem!important}
[data-testid="stSidebar"]{background:#edf3f1;border-right:1px solid #dfe8e5}
[data-testid="stMetric"]{background:white;border:1px solid #dfe8e5;border-radius:12px;padding:14px}
[data-testid="stVerticalBlockBorderWrapper"]{border-radius:12px}
[data-testid="stExpander"]{border-color:#dfe8e5;border-radius:10px;background:#fff}
.hero{background:linear-gradient(120deg,#123e42,#087f75);padding:34px;border-radius:20px;color:white;margin:12px 0 26px}
.hero h2{color:white;font-size:2rem;margin:6px 0 12px}.hero p{color:#d5efea;margin:0;line-height:1.8}
.eyebrow{letter-spacing:.15em;font-size:12px;font-weight:700}
.result-summary{display:grid;grid-template-columns:repeat(var(--summary-columns,3),minmax(0,1fr));background:#fff;border:1px solid #dfe8e5;border-radius:12px;margin:4px 0 12px}
.summary-item{padding:16px 20px;min-width:0}.summary-item+ .summary-item{border-left:1px solid #e6eeeb}
.summary-item span{display:block;font-size:.8rem;color:#526d6a;margin-bottom:5px}.summary-item strong{font-size:1.4rem;font-weight:650;overflow-wrap:anywhere}
.result-note{border-left:3px solid #39877c;background:#edf5f2;padding:12px 16px;border-radius:0 8px 8px 0;color:#284f4b;font-size:.9rem;line-height:1.65;margin:0 0 16px}
.result-table{border:1px solid #dfe8e5;border-radius:12px;background:#fff;overflow:hidden;margin-bottom:8px}
.result-table table{width:100%;margin:0;border-collapse:collapse;table-layout:fixed;font-size:.88rem;line-height:1.65}
.result-table th{background:#eef4f2;color:#526d6a;font-size:.78rem;font-weight:600;text-align:left;padding:11px 14px;border:0}
.result-table td{padding:13px 14px;vertical-align:top;border:0;border-top:1px solid #e8efec;white-space:normal;overflow-wrap:anywhere}
.result-table table[aria-label="복약안내문 약품 목록"]{font-size:.82rem}.result-table table[aria-label="복약안내문 약품 목록"] td{padding:10px}
/* All seven columns share 100%; never collapse the caution column to zero width. */
.result-table table[aria-label="복약안내문 약품 목록"] th:nth-child(1){width:22%}.result-table table[aria-label="복약안내문 약품 목록"] th:nth-child(2){width:10%}.result-table table[aria-label="복약안내문 약품 목록"] th:nth-child(3){width:8%}.result-table table[aria-label="복약안내문 약품 목록"] th:nth-child(4){width:8%}.result-table table[aria-label="복약안내문 약품 목록"] th:nth-child(5){width:14%}.result-table table[aria-label="복약안내문 약품 목록"] th:nth-child(6){width:16%}.result-table table[aria-label="복약안내문 약품 목록"] th:nth-child(7){width:22%}

.result-table td:first-child{font-weight:600;color:#183b40}.result-table tbody tr:hover{background:#f9fbfa}
.status-badge{display:inline-block;font-size:.76rem;font-weight:500;padding:2px 8px;border-radius:6px;background:#eaf4ef;color:#306354;white-space:normal}
.status-badge.attention{background:#fff3dc;color:#805715}
.status-badge.danger{background:#fcebea;color:#a1282d}.status-badge.muted{background:#edf0f2;color:#52616b}
@media(max-width:640px){.block-container{padding-left:1rem;padding-right:1rem}.hero{padding:24px}.hero h2{font-size:1.6rem}
.result-summary{grid-template-columns:repeat(2,minmax(0,1fr))}.summary-item:nth-child(odd){border-left:0}.summary-item:nth-child(n+3){border-top:1px solid #e6eeeb}
.summary-item{padding:12px 10px}.summary-item span{font-size:.7rem}.summary-item strong{font-size:1.12rem}
.result-table table,.result-table tbody{display:block}.result-table thead{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}
.result-table tr{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:5px 16px;padding:12px;border-top:1px solid #e8efec}.result-table tr:first-child{border-top:0}
.result-table td{display:block;padding:2px 0;border:0;font-size:.83rem}.result-table td:first-child{grid-column:1/-1;font-size:.95rem}.result-table td:first-child:before{display:none}.result-table table[aria-label="복약안내문 약품 목록"] td{padding:2px 0}
.result-table td:before{display:block;content:attr(data-label);font-size:.75rem;font-weight:400;color:#647b76}.result-table td .status-badge{justify-self:start}}
</style>''',unsafe_allow_html=True)
for key,default in [('records',[]),('schedules',{}),('originals',{}),('schedule_drafts',{}),('selected_records',{}),('stage_reviews',{})]:
    if key not in st.session_state: st.session_state[key]=default

ROUTES={'home':'대시보드','analysis':'문서 분석','health':'건강검진','medication':'복약 관리','records':'기록 포트폴리오'}
if 'active_page' not in st.session_state:
    st.session_state.active_page=ROUTES.get(st.query_params.get('view'),'대시보드')
if 'session_recovery' not in st.session_state:
    st.session_state.session_recovery=st.query_params.get('analysis')=='started'
if 'page' not in st.session_state: st.session_state.page=st.session_state.active_page

def remember_route():
    st.session_state.active_page=st.session_state.page
    st.query_params['view']=next(k for k,v in ROUTES.items() if v==st.session_state.page)

def navigate(destination):
    st.session_state.page=destination
    remember_route()

with st.sidebar:
    st.title('🌿 CareBridge')
    st.caption('흩어진 문서를, 나를 위한 기록으로')
    page=st.radio('내 건강 공간',list(ROUTES.values()),key='page',on_change=remember_route,
                  disabled=st.session_state.get('analysis_flow',{}).get('state') in BUSY)
    st.divider()
    st.caption('문서에 담긴 정보를 쉽게 확인하는 서비스입니다. 의료적 진단이나 처방 변경을 제공하지 않습니다.')
    st.caption('기록은 현재 세션에 보관됩니다. 종료 전 포트폴리오에서 내려받으세요.')
if st.session_state.session_recovery and not st.session_state.records:
    st.warning('현재 세션에는 이전 분석 기록이 없습니다. 새 연결이나 서버 재시작으로 메모리 세션이 사라진 경우 문서를 다시 선택해 분석해 주세요. 기록을 자동 복구하거나 기존 작업을 재요청하지 않습니다.')


def draft_input(widget, label, key, value, confirmation_key=None, **kwargs):
    # Keep values outside widget state: Streamlit removes hidden widgets on navigation.
    drafts=st.session_state.schedule_drafts
    if key not in st.session_state:
        st.session_state[key]=drafts.get(key,value)
    def remember():
        drafts[key]=st.session_state[key]
        if confirmation_key:
            drafts[confirmation_key]=False
            st.session_state[confirmation_key]=False
    return widget(label,key=key,on_change=remember,**kwargs)

def choose(kind):
    rows=[r for r in st.session_state.records if r['kind']==kind]
    if not rows:
        st.info('아직 분석한 문서가 없어요. 문서 분석에서 PDF·사진을 업로드하거나 샘플을 체험해 보세요.');return None
    def label(r):
        d=r['data']; return f"{d.get('health_examination_date') or d.get('record_written_date') or '날짜 미기재'} · {r['filename']} · {r['id'][:5]}"
    rows=sorted(rows,key=lambda r:r['data'].get('health_examination_date') or r['data'].get('record_written_date') or '',reverse=True)
    ids=[r['id'] for r in rows]
    selected=st.session_state.selected_records.get(kind)
    choice=st.selectbox('날짜별 기록',ids,index=ids.index(selected) if selected in ids else 0,
                        format_func=lambda rid:label(next(r for r in rows if r['id']==rid)),key='record_choice_'+kind)
    st.session_state.selected_records[kind]=choice
    return next(r for r in rows if r['id']==choice)

def evidence(r):
    st.caption(r['origin'])
    if r['id'] in st.session_state.originals:
        original=st.session_state.originals[r['id']]
        name,mime=original_type(original)
        st.download_button('원본 문서 내려받아 대조하기',original,file_name=name,mime=mime,key='pdf'+r['id'])
        if mime.startswith('image/'):
            with st.expander('원본 사진 확인'):
                st.image(original)
    with st.expander('추출 JSON 확인'):
        st.json(r['data'])
    st.caption('원문 근거도 AI가 추출한 텍스트입니다. 실제 원본과 대조해 주세요.')

def analysis_view(r):
    classification=r.get('classification')
    titles={'medication_guidance_record':'복약안내문 분석 결과','health_checkup_result':'AI 건강검진 해설'}
    valid=classification in GROUPS and classification==r.get('kind')
    st.subheader(titles[classification] if valid else '문서 유형 확인 필요')
    data=r.get('data') or {}
    title=data.get('record_document_title') or data.get('document_title')
    if valid and classification=='health_checkup_result':
        st.caption(HEALTH_NOTICE)
    else:
        st.caption(r['filename']+' · 원본 문서명 · '+(title or '미기재'))
    if not valid:
        st.warning('실제 Agent 분류 정보가 없거나 미지원 유형 또는 추출 정보와 불일치합니다. 문서 유형을 확인해 주세요.')
        st.caption('파일명이나 추출 필드로 문서 유형을 추측하지 않습니다. 기존 기록은 분류 정보가 포함되지 않았을 수 있습니다.')
        with st.expander('문서 상세 확인'):
            evidence(r)
        return
    if '가져온 기록' in r.get('origin',''):
        st.caption('가져온 파일에 보관된 분류 정보입니다. API에서 재검증하지 않았습니다.')
    try:
        kind,clean=normalize(data)
    except AnalysisError:
        st.warning('분류는 완료되었지만 표시할 추출 항목이 없거나 형식이 올바르지 않습니다. 원본과 분석 결과를 확인해 주세요.')
        with st.expander('문서 상세 확인'):
            evidence(r)
        return
    if kind!=classification:
        st.warning('Classify 분류와 Extract 항목이 일치하지 않습니다. 문서 유형 확인이 필요합니다.')
        return
    view_record={**r,'data':clean}
    if classification=='health_checkup_result':
        summary_slot=st.container(border=True)
        checkup_context(clean)
        general_health_guide(clean)
        easy_entry=easy_explanation(view_record,compact=True,render=False,collapse_controls=True)
        with summary_slot:
            if easy_entry:
                health_report(easy_entry)
            else:
                st.markdown('### 기본 문서 요약')
                st.caption('기본 문서 요약 · AI 생성 설명 아님')
                readings=[(row,assess(row,document_sex(clean))) for row in clean['health_checkup_results']]
                attention=[(row,value) for row,value in readings if value.level!='normal'][:2]
                sentences=[measurement_fields(row).name+'은(는) '+value.text+'로 표시되어 원문과 참고기준을 함께 확인할 항목입니다.' for row,value in attention]
                if not sentences: sentences=['추출된 항목은 문서의 참고기준과 함께 읽을 수 있습니다.']
                sentences.extend(['업로드된 자료에 있는 항목만 설명하며 전체 검진 결과로 확대하지 않습니다.','이 비교만으로 질병이나 개인별 치료 방법을 확정할 수 없습니다.'])
                st.write(' '.join(sentences))
        with st.expander('상세 검사 결과 · 원문 대조',expanded=False):
            st.caption('기본 문서 안내 · 원문 추출값과 문서 기준을 이용한 규칙 기반 표시')
            st.caption(r['filename']+' · 원본 문서명 · '+(title or '미기재'))
            st.caption('업로드된 문서의 항목만 설명합니다. 전체 검진이나 모든 페이지를 확인한 결과로 해석하지 마세요.')
            health(view_record,report=True)
            opinions=document_opinions(clean)
            if opinions:
                with st.expander('원문 종합 소견 확인',expanded=False):
                    for opinion in opinions: st.text(opinion)
        if easy_entry:
            with st.expander('요약 근거',expanded=False):
                for part in easy_entry['result']['plan']['summary']:
                    show_evidence(part,easy_entry['payload'])
            with st.expander('이해도 확인 (선택)',expanded=False):
                comprehension(view_record,easy_entry)
        st.button('건강기록 보관 및 내보내기 →',on_click=navigate,args=('기록 포트폴리오',),key=r['id']+'manage_records')
        return
    easy_entry=easy_explanation(view_record)
    st.caption('기본 문서 안내 · 원문 추출값과 문서 기준을 이용한 규칙 기반 표시')
    meds(view_record)
    comprehension(view_record,easy_entry)


def source_material(r):
    with st.expander('원본 자료 대조하기'):
        if st.checkbox('개인정보가 포함될 수 있는 원본 자료를 확인합니다.',key=r['id']+'show_original'):
            evidence(r)
            review=st.session_state.stage_reviews.get(r['id'])
            if review:
                st.caption('먼저 원본과 Parse 텍스트를 대조하고, Parse가 정확한 경우에만 Extract 필드·규칙을 점검하세요. 완료 상태만으로 추출 정확도나 오류 원인을 판단하지 않습니다.')
                st.dataframe(review['steps'],hide_index=True,width='stretch')
                with st.expander('Parse 중간 텍스트 대조 (현재 세션만)'):
                    st.text(review['parse_text'] or '대조할 Parse 텍스트가 없습니다. 오류 단계는 단정할 수 없습니다.')
                    st.caption('최대 30,000자. 로그·기록 내보내기에 포함하지 않습니다.')
            else:
                st.caption('이 기록에는 Parse 중간 결과가 없습니다. 원문과 중간 결과 없이 오류 단계를 단정할 수 없습니다.')


def health(r,report=False):
    rows=r['data']['health_checkup_results']
    sex=document_sex(r['data'])
    assessments=[assess(row,sex) for row in rows]
    measurements=[measurement_fields(row) for row in rows]
    counts=summarize(assessments)
    normal,attention,unknown=counts['met'],counts['attention'],counts['unknown']
    if not report:
        st.markdown('### 문서 기준 요약')
        st.markdown('<div class="result-note">'+lines(f'추출된 {counts["total"]}개 항목 중 문서 기준 충족 {normal}개, 주의·기준 미충족 {attention}개, 확인 필요 {unknown}개입니다.',
            '원문 판정을 우선합니다. 숫자 기준 충족은 의학적 정상 판정을 의미하지 않습니다.')+'</div>',unsafe_allow_html=True)
    st.markdown('### 상세 검진 수치')
    st.markdown(summary_html([('검사항목 수',f'{counts["total"]}개'),('문서 기준 충족',f'{normal}개'),('기준 미충족',f'{attention}개'),('확인 필요',f'{unknown}개')]),unsafe_allow_html=True)
    st.caption('검진일 · '+(r['data'].get('health_examination_date') or '미기재'))
    only_attention=draft_input(st.checkbox,'확인이 필요한 항목만 보기',r['id']+'health_filter',False)
    visible=[(i,row,assessment) for i,(row,assessment) in enumerate(zip(rows,assessments)) if not only_attention or assessment.level!='normal']
    if visible:
        st.markdown(table_html(['검사항목','측정값','단위','문서 참고범위','확인 상태'],[
            [lines(measurements[i].name or '항목명 확인 필요'),lines(row['result_value'] or '미기재'),
             lines(measurements[i].unit or '미기재'),lines(assessment.reference),badge(assessment.text,tone=assessment.tone)]
            for i,row,assessment in visible],'건강검진 결과'),unsafe_allow_html=True)
    else:
        st.info('확인이 필요한 항목이 없습니다. 전체 항목은 필터를 해제해 확인하세요.')
    with st.expander('항목 상세 · 비고 및 원문 근거'):
        i=draft_input(st.selectbox,'상세 확인할 검사항목',r['id']+'health_detail',0,options=list(range(len(rows))),format_func=lambda n:f"{n+1}. {rows[n]['test_item_name'] or '항목명 미기재'}")
        st.text('표시 근거: '+assessments[i].reason)
        st.text('표시 단위: '+(measurements[i].unit or '미기재')+' · '+measurements[i].unit_source)
        st.text('원문 항목명: '+(rows[i]['test_item_name'] or '미기재'))
        st.text('원문 단위 필드: '+(rows[i]['result_unit'] or '미기재'))
        st.text('원문 참고범위: '+(rows[i]['reference_range'] or '미기재'))
        st.text('원문 비고: '+(rows[i]['remark'] or '미기재'))
        st.text('원문 근거: '+(rows[i]['source_excerpt'] or '미기재'))
        st.caption('AI가 추출한 텍스트이므로 실제 원본과 대조해 주세요.')
    source_material(r)


def meds(r):
    rows=r['data']['medication_instructions']
    issues_by_row=[medication_issues(row) for row in rows]
    checks_by_row=[field_errors(row) for row in rows]
    def medication_value(row,field,checks):
        return lines(row[field] or '미기재')+('<br>'+badge('확인 필요',tone='unknown') if field in checks else '')
    st.markdown('### 01. 약품별 복약정보')
    st.markdown(summary_html([('인식한 약품',f'{len(rows)}개'),('확인 필요 정보',f'{sum(map(len,issues_by_row))}건')]),unsafe_allow_html=True)
    st.caption('검증된 약품정보 데이터가 연결되지 않아 약 이름으로 용도·성분·효능을 추측하지 않습니다.')
    st.caption('횟수와 일수는 원문 표기입니다. 횟수가 하루 기준인지 확인한 뒤 일정에 등록하세요.')
    st.markdown(table_html(['약품명 / 확인 상태','1회량','원문 횟수','투약 일수','복용 시점','약품 용도','원문 주의사항'],[
        [lines(row['medication_name'] or '약품명 확인 필요')+'<br>'+badge(f'확인 필요 {len(issues)}건',True),
         medication_value(row,'single_dose',checks),medication_value(row,'times_per_day',checks),
         medication_value(row,'days_of_administration',checks),medication_value(row,'administration_timing',checks),
         lines('약품 용도 확인 필요'),lines(row['caution_notes'] or '추출 내용 없음')]
        for row,issues,checks in zip(rows,issues_by_row,checks_by_row)],'복약안내문 약품 목록'),unsafe_allow_html=True)
    st.divider()
    st.markdown('### 02. 복약 일정 관리')
    st.caption('확인한 정보만 약품별로 등록합니다. 입력값을 수정하면 다시 승인해야 합니다.')
    creating=draft_input(st.toggle,'복약 일정 만들기',r['id']+'creating',False)
    if creating:
        i=draft_input(st.selectbox,'일정을 만들 약품',r['id']+'schedule_choice',0,options=list(range(len(rows))),format_func=lambda n:f"{n+1}. {rows[n]['medication_name'] or '약품명 확인 필요'}")
        row=rows[i];key=r['id']+str(i)
        with st.container(border=True):
            st.text('선택한 약품: '+(row['medication_name'] or '확인 필요'))
            st.text('원문 근거: '+(row['source_excerpt'] or '미기재 — 원본 또는 전문가에게 확인하세요.'))
            st.caption('원문 횟수: '+(row['times_per_day'] or '미기재')+' · 하루 기준으로 자동 적용하지 않습니다. 확인한 하루 복용 횟수를 직접 입력하세요.')
            verified={}
            fields=st.columns(2)
            for field_index,(f,l) in enumerate([('medication_name','확인한 약품명'),('single_dose','확인한 1회량'),('times_per_day','확인한 하루 복용 횟수'),('days_of_administration','확인한 투약 일수'),('administration_timing','확인한 복용 시점')]):
                with fields[field_index%2]:
                    verified[f]=draft_input(st.text_input,l,key+f,'' if f=='times_per_day' else row[f],confirmation_key=key+'confirmed')
            start=draft_input(st.date_input,'복용 시작일',key+'start',date.today(),confirmation_key=key+'confirmed')
            clocks=draft_input(st.text_input,'알림 시각 (HH:MM, 쉼표로 구분)',key+'clocks','',confirmation_key=key+'confirmed',placeholder='직접 확인한 시각을 입력하세요')
            confirmed=draft_input(st.checkbox,'원본 또는 전문가를 통해 약품명·용량·하루 기준 횟수·기간·시점·시작일·알림 시각을 모두 확인하고 일정 저장을 승인합니다.',key+'confirmed',False)
            blocking=field_errors(verified,schedule=True)
            if variable_schedule(row):
                blocking['administration_timing']='필요시·비일일·상충·혼합 지시는 고정 일일 일정으로 등록할 수 없습니다.'
            if blocking: st.warning(' / '.join(blocking.values()))
            if st.button('확인한 내용으로 일정 저장',key=key+'save',type='primary',disabled=bool(blocking)):
                try:
                    events=make_schedule(verified,start,[c.strip() for c in clocks.split(',') if c.strip()],confirmed,original=row)
                    st.session_state.schedules[key]={'record_id':r['id'],'confirmed_values':verified,'events':events}
                    st.rerun()
                except ValueError as e: st.error(str(e))
    saved=[(i,st.session_state.schedules[r['id']+str(i)]) for i in range(len(rows)) if r['id']+str(i) in st.session_state.schedules]
    if saved:
        events=sorted([event for _,schedule in saved for event in schedule['events']],key=lambda event:event['일시'])
        dates=sorted({event['일시'][:10] for event in events})
        with st.container(border=True):
            selected_date=st.selectbox('일정 확인 날짜',dates,key=r['id']+'schedule_date')
            st.caption('사용자 승인 일정 · Asia/Seoul · '+selected_date)
            st.dataframe([{'복용 시각':event['일시'][11:],**{field:event[field] for field in ('약품명','1회량','복용 시점')}} for event in events if event['일시'].startswith(selected_date)],hide_index=True,width='stretch')
            with st.expander('약품별 CSV 내보내기 및 일정 삭제'):
                for i,schedule in saved:
                    key=r['id']+str(i)
                    st.text(schedule['confirmed_values']['medication_name'])
                    buf=io.StringIO();writer=csv.DictWriter(buf,fieldnames=list(schedule['events'][0]));writer.writeheader();writer.writerows(schedule['events'])
                    st.download_button('일정 CSV 내려받기',buf.getvalue().encode('utf-8-sig'),file_name='medication-schedule.csv',mime='text/csv',key=key+'csv')
                    if st.button('이 일정 삭제',key=key+'delete'):
                        del st.session_state.schedules[key];st.rerun()
    else:
        st.caption('등록한 일정이 없습니다. 정보를 확인하고 등록하면 날짜별 일정이 표시됩니다.')
    st.caption('일정표와 CSV를 제공합니다. 백그라운드 알림은 제공하지 않습니다.')
    st.divider()
    st.markdown('### 03. 원문 대조 및 상세정보')
    with st.expander('약품별 추출값과 원문 근거 대조'):
        i=draft_input(st.selectbox,'상세 확인할 약품',r['id']+'med_detail',0,options=list(range(len(rows))),format_func=lambda n:f"{n+1}. {rows[n]['medication_name'] or '약품명 확인 필요'}")
        row=rows[i]
        extracted,source=st.columns(2)
        with extracted:
            st.caption('AI가 추출한 항목별 값')
            saved_values=st.session_state.schedules.get(r['id']+str(i),{}).get('confirmed_values',{})
            comparison=[]
            for field,label in [('medication_name','약품명'),('single_dose','1회량'),('times_per_day','횟수'),('days_of_administration','투약 일수'),('administration_timing','복용 시점'),('caution_notes','주의사항')]:
                entry={'항목':label,'문서 추출값':row[field] or '미기재'}
                if saved_values:
                    entry['사용자 승인 일정값']=saved_values.get(field) or '일정 반영 대상 아님'
                comparison.append(entry)
            st.dataframe(comparison,hide_index=True,width='stretch')
            if saved_values: st.caption('횟수의 추출값은 원문 표기이며, 일정값은 사용자가 별도로 확인한 하루 기준입니다.')
        with source:
            st.caption('AI가 추출한 원문 근거')
            st.text(row['source_excerpt'] or '미기재')
            st.caption('확인 필요 · '+' / '.join(issues_by_row[i]))
        st.caption('원문 근거도 AI 추출 텍스트입니다. 실제 원본과 대조하세요. 추출된 주의사항이 없어도 주의사항이 없다는 의미는 아닙니다.')
    source_material(r)

if page=='대시보드':
    st.markdown('<div class="hero"><span class="eyebrow">YOUR HEALTH, CONNECTED</span><h2>내 건강을 이해하는<br>가장 가까운 시작.</h2><p>검진 결과부터 복약안내까지.<br>문서 속 정보를 꺼내, 한눈에 확인하는 나만의 건강기록.</p></div>',unsafe_allow_html=True)
    a,b,c=st.columns(3)
    a.metric('건강검진 기록',sum(r['kind']=='health_checkup_result' for r in st.session_state.records))
    b.metric('복약안내 기록',sum(r['kind']=='medication_guidance_record' for r in st.session_state.records))
    c.metric('확인한 복약 일정',sum(len(s['events']) for s in st.session_state.schedules.values()))
    st.subheader('한 장의 문서에서 시작하세요')
    a,b=st.columns(2)
    with a,st.container(border=True):
        st.subheader('01  건강검진 결과');st.write('검사별 수치와 문서의 참고범위를 함께 확인하세요.')
    with b,st.container(border=True):
        st.subheader('02  복약안내');st.write('약품별 복용 정보를 확인하고, 직접 승인한 일정만 관리하세요.')
    def go(): navigate('문서 분석')
    st.button('새 문서 분석하기 →',type='primary',on_click=go)
    st.subheader('이어서 관리하기')
    a,b,c=st.columns(3)
    a.button('건강검진 결과 보기',on_click=navigate,args=('건강검진',),width='stretch')
    b.button('복약 정보와 일정 보기',on_click=navigate,args=('복약 관리',),width='stretch')
    c.button('건강기록 보관하기',on_click=navigate,args=('기록 포트폴리오',),width='stretch')
    st.subheader('최근 기록')
    for r in reversed(st.session_state.records[-4:]):
        st.write(f"📄 {r['filename']} · {r['data'].get('health_examination_date') or r['data'].get('record_written_date') or '날짜 미기재'}")
    if not st.session_state.records: st.caption('분석한 문서가 이곳에 쌓입니다. 샘플 6종으로 먼저 체험해 보세요.')
elif page=='문서 분석':
    st.title('문서 한 장, 더 쉬운 건강관리')
    st.caption('업로드 → 명시적 동의 → AI 분석 및 해설 → 결과 보기')
    busy=st.session_state.get('analysis_flow',{}).get('state') in BUSY
    def source_changed():
        st.session_state.analysis_source=st.session_state.analysis_source_widget
        set_upload(st.session_state,None,'')
        st.session_state.pop('analysis_file',None)
    if 'analysis_source_widget' not in st.session_state:
        st.session_state.analysis_source_widget=st.session_state.get('analysis_source','내 문서 업로드')
    source=st.radio('문서 선택 방식',['내 문서 업로드','가상 샘플 체험'],horizontal=True,
                    key='analysis_source_widget',on_change=source_changed,disabled=busy)
    def remember_file():
        value=st.session_state.get('analysis_file')
        set_upload(st.session_state,value.getvalue() if value else None,value.name if value else '')
    if source=='내 문서 업로드':
        f=st.file_uploader('건강검진 결과지 또는 약국 복약안내문',type=['pdf','png','jpg','jpeg'],key='analysis_file',on_change=remember_file,disabled=busy)
        if f: set_upload(st.session_state,f.getvalue(),f.name)
    else:
        if 'analysis_sample_widget' not in st.session_state:
            st.session_state.analysis_sample_widget=st.session_state.get('analysis_sample',1)
        def remember_sample(): st.session_state.analysis_sample=st.session_state.analysis_sample_widget
        sample=st.selectbox('샘플 선택',list(range(1,7)),key='analysis_sample_widget',on_change=remember_sample,disabled=busy,
                           format_func=lambda n:f"샘플 {n} · {['건강검진 · 표형','건강검진 · 카드형','건강검진 · 미기재 포함','복약안내 · 표형','복약안내 · 카드형','복약안내 · 미기재 포함'][n-1]}")
        filename=f'sample{sample}.pdf'
        data=(ROOT/'samples'/filename).read_bytes()
        set_upload(st.session_state,data,filename)
        st.caption('가상 자료이며 진료·복약에 사용할 수 없습니다. 분석 버튼을 누를 때 Upstage를 호출합니다.')
        st.download_button('샘플 원문 보기',data,file_name=filename,mime='application/pdf')
    upload=st.session_state.get('analysis_upload',{})
    if upload.get('data'): st.success('선택 완료 · '+upload['filename'])
    st.caption('PDF·PNG·JPG 20MB 이하. 파일과 진행 상태는 현재 세션 메모리에만 보관됩니다.')
    st.caption('문서 원본은 Upstage로 전송됩니다. FactChat은 선택 동의 시 추출·검증 후 건강검진의 최소 구조화 항목만 받습니다.')
    with st.expander('외부 제공처와 전송 정보 확인',expanded=False):
        st.write('Upstage Studio Agent: 원본 PDF·사진을 Parse → Classify → Extract로 분석합니다.')
        st.write('학교 FactChat Gateway(api.baze.co): 허용된 검사명, 검증된 수치·단위·참고기준·정해진 판정과 고정 용어 설명만 전달합니다. 이름·날짜·문서명·원본·원문 근거·자유 서술 소견은 자동 전송하지 않습니다.')
        st.caption('검증되지 않은 항목은 외부 해설에서 제외하고 기본 결과에 남깁니다. 복약안내문은 기존 원문 확인·수동 일정 절차를 사용합니다. 업로드 파일 삭제 요청과 제공사 작업 결과 보관 정책은 별개입니다.')
    for widget,stored in [('analysis_extract_consent','analysis_extract_agreed'),('analysis_llm_consent','analysis_factchat_agreed')]:
        if widget not in st.session_state: st.session_state[widget]=st.session_state.get(stored,False)
    def remember_consent(widget,stored): st.session_state[stored]=st.session_state[widget]
    consent=st.checkbox('필수 · 분석을 위해 문서를 Upstage API로 전송하는 데 동의합니다.',key='analysis_extract_consent',on_change=remember_consent,args=('analysis_extract_consent','analysis_extract_agreed'),disabled=busy)
    llm_consent=st.checkbox('선택 · 검증된 최소 건강검진 정보를 학교 FactChat Gateway로 전송해 AI 해설을 생성하는 데 동의합니다.',key='analysis_llm_consent',on_change=remember_consent,args=('analysis_llm_consent','analysis_factchat_agreed'),disabled=busy)
    if not llm_consent: st.caption('FactChat 동의 없이도 Upstage 기본 분석을 이용할 수 있습니다. AI 생성 해설 대신 기본 문서 요약을 표시합니다.')
    flow=st.session_state.get('analysis_flow',{})
    def begin_analysis():
        queue(st.session_state,extract_consent=st.session_state.analysis_extract_consent,
              explanation_consent=st.session_state.analysis_llm_consent)
        st.query_params['view']='analysis'
        st.query_params['analysis']='started'
    st.button('AI 분석 및 해설 보기',type='primary',on_click=begin_analysis,
              disabled=not upload.get('data') or not consent or busy or flow.get('state')=='completed')
    if flow.get('state') in BUSY:
        with st.status(flow.get('phase','분석 중'),expanded=True) as status:
            def progress(message):
                status.update(label=message)
                status.write(message)
            run_queued(st.session_state,AgentClient,progress)
            current=st.session_state.analysis_flow
            status.update(label=current['phase'],state='complete' if current['state']=='completed' else 'error' if current['state'] in ('error','interrupted') else 'running',expanded=current['state']!='completed')
        if st.session_state.analysis_flow['state'] not in BUSY: st.rerun()
    flow=st.session_state.get('analysis_flow',{})
    if flow.get('state')=='completed':
        with st.status('분석 완료 · 결과를 확인하세요',state='complete',expanded=False):
            st.write('결과는 현재 세션에 유지되며 화면 조작으로 API를 다시 호출하지 않습니다.')
    if flow.get('state') in ('error','interrupted'):
        with st.status('분석 오류 · 자동 재요청하지 않습니다',state='error',expanded=False): st.write(flow['phase'])
        st.error(flow['error'])
    if flow.get('cleanup_warning'): st.warning('Upstage 업로드 파일 삭제를 확인하지 못했습니다. Console에서 확인해 주세요.')
    if flow.get('state')=='completed':
        def new_analysis():
            set_upload(st.session_state,None,'')
            st.session_state.pop('analysis_file',None)
            st.session_state.session_recovery=False
        st.button('다른 문서 분석하기',on_click=new_analysis)
    last=next((r for r in st.session_state.records if r['id']==st.session_state.get('last_result_id')),None)
    if last:
        st.divider()
        if flow.get('explanation_state')=='error': st.info('AI 해설을 생성하지 못해 기본 문서 요약을 표시합니다. 자동 재요청하지 않습니다.')
        analysis_view(last)
elif page=='건강검진':
    st.caption('내 건강 공간 / 건강검진')
    r=choose('health_checkup_result')
    if r: analysis_view(r)
elif page=='복약 관리':
    st.caption('내 건강 공간 / 복약 관리')
    r=choose('medication_guidance_record')
    if r: analysis_view(r)
else:
    st.title('건강기록 보관함')
    st.caption('분석한 문서를 모아보고 기록을 내보내거나 다시 불러오세요.')
    st.info('기록은 현재 브라우저 세션에만 보관됩니다. 새로고침·연결 종료·서버 재시작으로 사라질 수 있습니다. JSON을 내려받아 보관하고 다시 불러올 수 있습니다. 원본 PDF와 일정은 기록 JSON에 포함되지 않습니다.')
    st.download_button('전체 기록 JSON 내려받기',export_records(st.session_state.records),file_name='carebridge-records.json',mime='application/json')
    f=st.file_uploader('보관한 기록 다시 불러오기',type=['json'])
    if st.button('기록 가져오기',disabled=f is None):
        try:
            records=import_records(f.getvalue());st.session_state.records.extend(records);st.success(f'{len(records)}개 기록을 가져왔습니다.')
        except AnalysisError as e: st.error(str(e))
    for r in list(reversed(st.session_state.records)):
        with st.container(border=True):
            st.write(r['filename']);st.caption(r['origin'])
            st.write(r['data'].get('health_examination_date') or r['data'].get('record_written_date') or '날짜 미기재')
            with st.expander('분석 결과 보기'):
                if st.checkbox('이 기록의 분석 결과 표시',key=r['id']+'show_result'):
                    analysis_view(r)
            if st.button('기록 삭제',key='delete'+r['id']):
                st.session_state.records=[x for x in st.session_state.records if x['id']!=r['id']]
                st.session_state.originals.pop(r['id'],None)
                st.session_state.stage_reviews.pop(r['id'],None)
                for name in ('factchat_results','factchat_panels'):
                    st.session_state.get(name,{}).pop(r['id'],None)
                for name in ('factchat_answers',):
                    values=st.session_state.get(name,{})
                    for key in list(values):
                        if key.startswith(r['id']): values.pop(key,None)
                for draft_key in list(st.session_state.schedule_drafts):
                    if draft_key.startswith(r['id']):
                        st.session_state.schedule_drafts.pop(draft_key,None)
                        st.session_state.pop(draft_key,None)
                st.session_state.schedules={k:v for k,v in st.session_state.schedules.items() if v['record_id']!=r['id']}
                st.rerun()
