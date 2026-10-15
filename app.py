import csv
import io
import json
from datetime import date
from pathlib import Path
import streamlit as st
from carebridge.client import AgentClient, AnalysisError, ROOT
from carebridge.domain import GROUPS, parse_job, range_status, medication_issues, make_schedule
from carebridge.documents import original_type
from carebridge.records import record, export_records, import_records

st.set_page_config(page_title='CareBridge · 나를 이해하는 건강기록',page_icon='🌿',layout='wide')
st.markdown('''<style>
.block-container{max-width:1180px;padding-top:2rem;padding-bottom:3rem}
h1,h2,h3{letter-spacing:-.045em}h1{font-weight:750!important}
[data-testid="stSidebar"]{background:#eaf3f0}
[data-testid="stMetric"]{background:white;border:1px solid #dce9e5;border-radius:18px;padding:20px}
[data-testid="stVerticalBlockBorderWrapper"]{border-radius:18px}
.hero{background:linear-gradient(120deg,#123e42,#087f75);padding:34px;border-radius:24px;color:white;margin:12px 0 26px}
.hero h2{color:white;font-size:2rem;margin:6px 0 12px}.hero p{color:#d5efea;margin:0;line-height:1.8}
.eyebrow{letter-spacing:.15em;font-size:12px;font-weight:700}
@media(max-width:640px){.block-container{padding:1rem}.hero{padding:24px}.hero h2{font-size:1.6rem}}
</style>''',unsafe_allow_html=True)
for key,default in [('records',[]),('schedules',{}),('originals',{}),('page','대시보드')]:
    if key not in st.session_state: st.session_state[key]=default

with st.sidebar:
    st.title('🌿 CareBridge')
    st.caption('흩어진 문서를, 나를 위한 기록으로')
    page=st.radio('내 건강 공간',['대시보드','문서 분석','건강검진','복약 관리','기록 포트폴리오'],key='page')
    st.divider()
    st.caption('문서에 담긴 정보를 쉽게 확인하는 서비스입니다. 의료적 진단이나 처방 변경을 제공하지 않습니다.')
    st.caption('기록은 현재 세션에 보관됩니다. 종료 전 포트폴리오에서 내려받으세요.')

def choose(kind):
    rows=[r for r in st.session_state.records if r['kind']==kind]
    if not rows:
        st.info('아직 분석한 문서가 없어요. 문서 분석에서 PDF·사진을 업로드하거나 샘플을 체험해 보세요.');return None
    def label(r):
        d=r['data']; return f"{d.get('health_examination_date') or d.get('record_written_date') or '날짜 미기재'} · {r['filename']} · {r['id'][:5]}"
    return st.selectbox('날짜별 기록',sorted(rows,key=lambda r:r['data'].get('health_examination_date') or r['data'].get('record_written_date') or '',reverse=True),format_func=label)

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

def health(r):
    st.subheader(r['data']['document_title'] or '건강검진 결과')
    rows=r['data']['health_checkup_results']
    statuses=[range_status(x) for x in rows]
    outside=sum(x in ('문서 범위 초과','문서 범위 미만','문서 범위 밖') for x in statuses)
    st.info(f'문서 기준 요약 · {len(rows)}개 항목 중 {outside}개가 문서의 참고범위를 벗어났고, {statuses.count("비교 불가")}개는 비교할 수 없습니다. 질환 여부를 판단한 결과가 아닙니다.')
    st.caption('앱이 추출값과 문서 참고범위를 단순 비교한 요약입니다. 추가 의학 설명은 생성하지 않습니다.')
    cols=st.columns(3)
    for i,row in enumerate(rows):
        with cols[i%3],st.container(border=True):
            st.text(row['test_item_name'] or '항목명 확인 필요')
            st.subheader((row['result_value'] or '미기재')+' '+row['result_unit'])
            st.caption('문서 기준 '+(row['reference_range'] or '미기재'))
            st.write(statuses[i])
            with st.expander('원문 근거'):
                st.text(row['source_excerpt'] or '근거 미기재')
                st.text(row['remark'])
    labels={'test_item_name':'검사항목','result_value':'측정값','result_unit':'단위','reference_range':'문서 참고범위','remark':'비고','source_excerpt':'원문 근거'}
    st.dataframe([{**{labels[k]:v for k,v in x.items()},'문서 기준 비교':s} for x,s in zip(rows,statuses)],hide_index=True,width='stretch')
    evidence(r)

def meds(r):
    st.subheader(r['data']['record_document_title'] or '복약안내')
    st.caption('원문 추출값과 사용자 확인값을 분리해 보관합니다. 알림 시간은 자동 추천하지 않습니다.')
    for i,row in enumerate(r['data']['medication_instructions']):
        key=r['id']+str(i)
        with st.container(border=True):
            st.subheader(row['medication_name'] or '약품명 미기재')
            a,b,c=st.columns(3)
            a.metric('원문 · 1회량',row['single_dose'] or '미기재')
            b.metric('원문 · 하루 횟수',row['times_per_day'] or '미기재')
            c.metric('원문 · 복용 일수',row['days_of_administration'] or '미기재')
            st.write('복용 시점: '+(row['administration_timing'] or '미기재'))
            st.write('주의사항: '+(row['caution_notes'] or '미기재'))
            issues=medication_issues(row)
            if issues: st.warning(' / '.join(issues)+' — 확인 전 일정 생성이 차단됩니다.')
            with st.expander('원문 근거와 일정 만들기'):
                st.text(row['source_excerpt'] or '원문 근거 미기재')
                st.caption('아래는 사용자 확인값입니다. 누락·상충 정보는 원문 또는 약사에게 확인 후 입력하세요. 시점에 맞는 알림 시각도 직접 정해 주세요.')
                with st.form('schedule'+key):
                    verified={}
                    for f,l in [('medication_name','약품명'),('single_dose','1회량'),('times_per_day','하루 횟수'),('days_of_administration','복용 일수'),('administration_timing','복용 시점')]:
                        verified[f]=st.text_input(l,value=row[f],key=key+f)
                    start=st.date_input('복용 시작일',value=date.today(),key=key+'start')
                    clocks=st.text_input('알림 시각 (HH:MM, 쉼표로 구분)',placeholder='직접 확인한 시각을 입력하세요',key=key+'clocks')
                    confirmed=st.checkbox('원문 또는 전문가를 통해 용량·횟수·기간·시점과 알림 시각을 확인했습니다.',key=key+'confirmed')
                    submitted=st.form_submit_button('확인한 내용으로 일정 저장')
                    if submitted:
                        try:
                            events=make_schedule(verified,start,[c.strip() for c in clocks.split(',') if c.strip()],confirmed)
                            st.session_state.schedules[key]={'record_id':r['id'],'confirmed_values':verified,'events':events}
                            st.success(f'{len(events)}개 일정을 저장했습니다.')
                        except ValueError as e: st.error(str(e))
            if key in st.session_state.schedules:
                st.caption('사용자가 확인한 일정 · Asia/Seoul')
                events=st.session_state.schedules[key]['events']
                st.dataframe(events,hide_index=True,width='stretch')
                buf=io.StringIO();w=csv.DictWriter(buf,fieldnames=list(events[0]));w.writeheader();w.writerows(events)
                st.download_button('일정 CSV 내려받기',buf.getvalue().encode('utf-8-sig'),file_name='medication-schedule.csv',mime='text/csv',key=key+'csv')
                if st.button('이 일정 삭제',key=key+'delete'):
                    del st.session_state.schedules[key];st.rerun()
    st.caption('현재 MVP는 일정표와 CSV를 제공합니다. 백그라운드 푸시 알림은 제공하지 않습니다.')
    evidence(r)

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
    st.subheader('최근 기록')
    for r in reversed(st.session_state.records[-4:]):
        st.write(f"📄 {r['filename']} · {r['data'].get('health_examination_date') or r['data'].get('record_written_date') or '날짜 미기재'}")
    if not st.session_state.records: st.caption('분석한 문서가 이곳에 쌓입니다. 샘플 6종으로 먼저 체험해 보세요.')
elif page=='문서 분석':
    st.title('문서 한 장, 더 쉬운 건강관리')
    st.caption('01 문서 선택 → 02 실제 AI 분석 → 03 원문과 결과 확인')
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
            try:
                client=AgentClient();job=client.analyze(data,lambda msg:status.update(label=msg))
                kind,result=parse_job(job)
                r=record(kind,result,filename,job.get('id',''))
                st.session_state.records.append(r);st.session_state.originals[r['id']]=data
                status.update(label='분석 완료 · 기록에 추가했습니다',state='complete')
                if client.cleanup_warning: st.warning('Upstage 업로드 파일 삭제를 확인하지 못했습니다. Console에서 확인해 주세요.')
                st.session_state['last_result_id']=r['id']
            except AnalysisError as e:
                status.update(label='분석 오류',state='error');st.error(str(e))
                if client and client.cleanup_warning:
                    st.warning('업로드 파일 삭제를 확인하지 못했습니다. Upstage Console에서 확인해 주세요.')
    last=next((r for r in st.session_state.records if r['id']==st.session_state.get('last_result_id')),None)
    if last:
        st.divider()
        st.caption('최근 완료한 분석 · '+last['filename'])
        (health if last['kind']=='health_checkup_result' else meds)(last)
elif page=='건강검진':
    st.title('내 수치를 이해하는 시간')
    r=choose('health_checkup_result')
    if r: health(r)
elif page=='복약 관리':
    st.title('확인하고, 챙기는 복약 관리')
    r=choose('medication_guidance_record')
    if r: meds(r)
else:
    st.title('나의 건강기록 포트폴리오')
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
            if st.button('기록 삭제',key='delete'+r['id']):
                st.session_state.records=[x for x in st.session_state.records if x['id']!=r['id']]
                st.session_state.originals.pop(r['id'],None)
                st.session_state.schedules={k:v for k,v in st.session_state.schedules.items() if v['record_id']!=r['id']}
                st.rerun()
