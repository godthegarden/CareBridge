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
from carebridge.health import assess, document_sex, summarize

logger=logging.getLogger('carebridge.app')
logger.setLevel(logging.INFO)

st.set_page_config(page_title='CareBridge · 나를 이해하는 건강기록',page_icon='🌿',layout='wide')
st.markdown('''<style>
.block-container{max-width:1080px;padding-top:2rem;padding-bottom:3rem}
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
.result-table table{width:100%;border-collapse:collapse;table-layout:fixed;font-size:.88rem;line-height:1.65}
.result-table th{background:#eef4f2;color:#526d6a;font-size:.78rem;font-weight:600;text-align:left;padding:11px 14px;border:0}
.result-table td{padding:13px 14px;vertical-align:top;border:0;border-top:1px solid #e8efec;white-space:normal;overflow-wrap:anywhere}
.result-table table[aria-label="복약안내문 약품 목록"]{font-size:.82rem}.result-table table[aria-label="복약안내문 약품 목록"] td{padding:12px 10px}
.result-table table[aria-label="복약안내문 약품 목록"] th:nth-child(1){width:24%}.result-table table[aria-label="복약안내문 약품 목록"] th:nth-child(2){width:10%}.result-table table[aria-label="복약안내문 약품 목록"] th:nth-child(3){width:10%}.result-table table[aria-label="복약안내문 약품 목록"] th:nth-child(4){width:10%}.result-table table[aria-label="복약안내문 약품 목록"] th:nth-child(5){width:20%}.result-table table[aria-label="복약안내문 약품 목록"] th:nth-child(6){width:26%}

.result-table td:first-child{font-weight:600;color:#183b40}.result-table tbody tr:hover{background:#f9fbfa}
.status-badge{display:inline-block;font-size:.76rem;font-weight:500;padding:2px 8px;border-radius:6px;background:#eaf4ef;color:#306354;white-space:normal}
.status-badge.attention{background:#fff3dc;color:#805715}
.status-badge.danger{background:#fcebea;color:#a1282d}.status-badge.muted{background:#edf0f2;color:#52616b}
@media(max-width:640px){.block-container{padding:1rem}.hero{padding:24px}.hero h2{font-size:1.6rem}
.result-summary{grid-template-columns:repeat(2,minmax(0,1fr))}.summary-item:nth-child(odd){border-left:0}.summary-item:nth-child(n+3){border-top:1px solid #e6eeeb}
.summary-item{padding:12px 10px}.summary-item span{font-size:.7rem}.summary-item strong{font-size:1.12rem}
.result-table table,.result-table tbody{display:block}.result-table thead{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}
.result-table tr{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:5px 16px;padding:12px;border-top:1px solid #e8efec}.result-table tr:first-child{border-top:0}
.result-table td{display:block;padding:2px 0;border:0;font-size:.83rem}.result-table td:first-child{grid-column:1/-1;font-size:.95rem}.result-table td:first-child:before{display:none}.result-table table[aria-label="복약안내문 약품 목록"] td{padding:2px 0}
.result-table td:before{display:block;content:attr(data-label);font-size:.75rem;font-weight:400;color:#647b76}.result-table td .status-badge{justify-self:start}}
</style>''',unsafe_allow_html=True)
for key,default in [('records',[]),('schedules',{}),('originals',{}),('page','대시보드'),('schedule_drafts',{}),('selected_records',{})]:
    if key not in st.session_state: st.session_state[key]=default

with st.sidebar:
    st.title('🌿 CareBridge')
    st.caption('흩어진 문서를, 나를 위한 기록으로')
    page=st.radio('내 건강 공간',['대시보드','문서 분석','건강검진','복약 관리','기록 포트폴리오'],key='page')
    st.divider()
    st.caption('문서에 담긴 정보를 쉽게 확인하는 서비스입니다. 의료적 진단이나 처방 변경을 제공하지 않습니다.')
    st.caption('기록은 현재 세션에 보관됩니다. 종료 전 포트폴리오에서 내려받으세요.')

def navigate(destination):
    st.session_state.page=destination

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
    titles={'medication_guidance_record':'복약안내문 분석 결과','health_checkup_result':'건강검진 결과 분석'}
    valid=classification in GROUPS and classification==r.get('kind')
    st.subheader(titles[classification] if valid else '문서 유형 확인 필요')
    data=r.get('data') or {}
    title=data.get('record_document_title') or data.get('document_title')
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
    (meds if classification=='medication_guidance_record' else health)(view_record)
    if classification=='health_checkup_result':
        st.button('건강기록 보관 및 내보내기 →',on_click=navigate,args=('기록 포트폴리오',),key=r['id']+'manage_records')

def source_material(r):
    with st.expander('원본 자료 대조하기'):
        if st.checkbox('개인정보가 포함될 수 있는 원본 자료를 확인합니다.',key=r['id']+'show_original'):
            evidence(r)


def health(r):
    rows=r['data']['health_checkup_results']
    sex=document_sex(r['data'])
    assessments=[assess(row,sex) for row in rows]
    counts=summarize(assessments)
    normal,attention,unknown=counts['met'],counts['attention'],counts['unknown']
    st.markdown('### 문서 기준 요약')
    st.markdown('<div class="result-note">'+lines(f'추출된 {counts["total"]}개 항목 중 문서 기준 충족 {normal}개, 주의·기준 미충족 {attention}개, 확인 필요 {unknown}개입니다.',
        '원문 판정을 우선합니다. 숫자 기준 충족은 의학적 정상 판정을 의미하지 않습니다.')+'</div>',unsafe_allow_html=True)
    st.markdown(summary_html([('검사항목 수',f'{counts["total"]}개'),('문서 기준 충족',f'{normal}개'),('기준 미충족',f'{attention}개'),('확인 필요',f'{unknown}개')]),unsafe_allow_html=True)
    st.markdown('### 검사항목별 결과')
    st.caption('검진일 · '+(r['data'].get('health_examination_date') or '미기재'))
    only_attention=draft_input(st.checkbox,'확인이 필요한 항목만 보기',r['id']+'health_filter',False)
    visible=[(i,row,assessment) for i,(row,assessment) in enumerate(zip(rows,assessments)) if not only_attention or assessment.level!='normal']
    if visible:
        st.markdown(table_html(['검사항목','측정값','단위','문서 참고범위','확인 상태'],[
            [lines(row['test_item_name'] or '항목명 확인 필요'),lines(row['result_value'] or '미기재'),
             lines(row['result_unit'] or '미기재'),lines(assessment.reference),badge(assessment.text,tone=assessment.tone)]
            for _,row,assessment in visible],'건강검진 결과'),unsafe_allow_html=True)
    else:
        st.info('확인이 필요한 항목이 없습니다. 전체 항목은 필터를 해제해 확인하세요.')
    with st.expander('항목 상세 · 비고 및 원문 근거'):
        i=draft_input(st.selectbox,'상세 확인할 검사항목',r['id']+'health_detail',0,options=list(range(len(rows))),format_func=lambda n:f"{n+1}. {rows[n]['test_item_name'] or '항목명 미기재'}")
        st.text('표시 근거: '+assessments[i].reason)
        st.text('원문 참고범위: '+(rows[i]['reference_range'] or '미기재'))
        st.text('원문 비고: '+(rows[i]['remark'] or '미기재'))
        st.text('원문 근거: '+(rows[i]['source_excerpt'] or '미기재'))
        st.caption('AI가 추출한 텍스트이므로 실제 원본과 대조해 주세요.')
    source_material(r)


def meds(r):
    rows=r['data']['medication_instructions']
    issues_by_row=[medication_issues(row) for row in rows]
    st.markdown('### 01. 약품별 복약정보')
    st.markdown(summary_html([('인식한 약품',f'{len(rows)}개'),('확인 필요 정보',f'{sum(map(len,issues_by_row))}건')]),unsafe_allow_html=True)
    st.caption('횟수와 일수는 원문 표기입니다. 횟수가 하루 기준인지 확인한 뒤 일정에 등록하세요.')
    st.markdown(table_html(['약품명 / 확인 상태','1회량','원문 횟수','투약 일수','복용 시점','원문 주의사항'],[
        [lines(row['medication_name'] or '약품명 확인 필요')+'<br>'+badge(f'확인 필요 {len(issues)}건',True),
         lines(row['single_dose'] or '미기재'),lines(row['times_per_day'] or '미기재'),
         lines(row['days_of_administration'] or '미기재'),lines('확인 필요' if missing(row['administration_timing']) else row['administration_timing']),
         lines(row['caution_notes'] or '추출 내용 없음')]
        for row,issues in zip(rows,issues_by_row)],'복약안내문 약품 목록'),unsafe_allow_html=True)
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
            if st.button('확인한 내용으로 일정 저장',key=key+'save',type='primary'):
                try:
                    events=make_schedule(verified,start,[c.strip() for c in clocks.split(',') if c.strip()],confirmed)
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
    def go(): st.session_state.page='문서 분석'
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
    st.caption('문서 업로드 · 분석 결과는 아래에 표시되고 건강검진 / 복약 관리에도 보관됩니다.')
    st.info('01 문서 선택 → 02 전송 동의 및 분석 → 03 원문과 분석 결과 확인')
    source=st.radio('문서 선택 방식',['내 문서 업로드','가상 샘플 체험'],horizontal=True)
    data=None;filename=''
    if source=='내 문서 업로드':
        f=st.file_uploader('건강검진 결과지 또는 약국 복약안내문',type=['pdf','png','jpg','jpeg'])
        if f: data=f.getvalue();filename=f.name
    else:
        sample=st.selectbox('샘플 선택',list(range(1,7)),format_func=lambda n:f"샘플 {n} · {['건강검진 · 표형','건강검진 · 카드형','건강검진 · 미기재 포함','복약안내 · 표형','복약안내 · 카드형','복약안내 · 미기재 포함'][n-1]}")
        filename=f'sample{sample}.pdf';data=(ROOT/'samples'/filename).read_bytes()
        st.caption('가상 자료이며 진료·복약에 사용할 수 없습니다. 샘플도 매번 실제 Agent를 호출합니다.')
        st.download_button('샘플 원문 보기',data,file_name=filename,mime='application/pdf')
    if data: st.success(f'선택 완료 · {filename} · {len(data)/1024:.0f}KB')
    consent=st.checkbox('분석을 위해 문서를 Upstage API로 전송하는 데 동의합니다.')
    st.caption('PDF·PNG·JPG 20MB 이하. PDF는 암호 없이 30페이지 이하, 사진은 3천만 화소 이하. 사진은 회전을 보정해 PDF로 변환한 뒤 분석합니다. 분석 후 Upstage 업로드 파일 삭제를 요청합니다. 제공사의 작업 결과 보관 정책은 별도로 적용됩니다.')
    if st.button('AI 분석 시작',type='primary',disabled=not data or not consent):
        with st.status('분석을 준비합니다',expanded=True) as status:
            client=None
            phase='준비'
            started=time.monotonic()
            history=[]
            def show_progress(message):
                status.update(label=message)
                phase_label=message.split(' · ')[0]
                if not history or history[-1]!=phase_label:
                    status.write(message)
                    history.append(phase_label)
            try:
                phase='Agent 실행'
                client=AgentClient();job=client.analyze(data,show_progress)
                phase='결과 처리'
                show_progress('결과 처리 · 화면을 준비합니다')
                logger.info('analysis stage=result_processing state=started')
                kind,result,classification=analysis_record_data(job)
                r=record(kind,result,filename,job.get('id',''),classification)
                st.session_state.records.append(r);st.session_state.originals[r['id']]=data
                logger.info('analysis stage=result_processing state=completed elapsed_s=%d',int(time.monotonic()-started))
                status.update(label='분석 완료 · 기록에 추가했습니다',state='complete',expanded=False)
                if client.cleanup_warning: st.warning('Upstage 업로드 파일 삭제를 확인하지 못했습니다. Console에서 확인해 주세요.')
                st.session_state['last_result_id']=r['id']
                st.session_state.selected_records[kind]=r['id']
                st.session_state.pop('record_choice_'+kind,None)
            except AnalysisError as e:
                logger.warning('analysis stage=%s state=error',phase)
                status.update(label=phase+' 오류',state='error',expanded=True);st.error(str(e))
                if client and client.cleanup_warning:
                    st.warning('업로드 파일 삭제를 확인하지 못했습니다. Upstage Console에서 확인해 주세요.')
            except Exception:
                logger.error('analysis stage=%s state=unexpected_error',phase)
                status.update(label=phase+' 오류',state='error',expanded=True)
                st.error('분석 응답 처리에 문제가 발생했습니다. 작업은 자동 재요청하지 않습니다. Studio에서 기존 작업 상태를 확인해 주세요.')
    last=next((r for r in st.session_state.records if r['id']==st.session_state.get('last_result_id')),None)
    if last:
        st.divider()
        st.caption('최근 완료한 분석 · '+last['filename'])
        destination={'health_checkup_result':'건강검진','medication_guidance_record':'복약 관리'}.get(last.get('classification'),'기록 포트폴리오')
        st.button(destination+'에서 이어서 보기 →',on_click=navigate,args=(destination,))
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
                for draft_key in list(st.session_state.schedule_drafts):
                    if draft_key.startswith(r['id']):
                        st.session_state.schedule_drafts.pop(draft_key,None)
                        st.session_state.pop(draft_key,None)
                st.session_state.schedules={k:v for k,v in st.session_state.schedules.items() if v['record_id']!=r['id']}
                st.rerun()
