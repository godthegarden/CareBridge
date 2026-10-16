# CareBridge 🌿

- 서비스: https://carebridge-production-7518.up.railway.app
- 소스: https://github.com/godthegarden/CareBridge

건강검진 결과지와 복약안내문을 실제 Upstage Studio Agent로 분석하는 한국어 Streamlit MVP입니다. PDF, PNG, JPG/JPEG를 지원합니다. `carebridge.json`의 기존 Parse → Classify → 유형별 Extract 구성과 스키마는 변경하지 않았습니다.

## 실행

Python 3.12 이상 권장 (로컬 검증: Python 3.14).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# .env가 이미 있으면 덮어쓰지 마세요.
cp .env.example .env
# .env에 UPSTAGE_API_KEY, UPSTAGE_AGENT_ID 입력
python -m streamlit run app.py
```

이미 구성된 이 작업환경에서는 `.venv/bin/python -m streamlit run app.py`만 실행합니다. 브라우저 주소는 http://localhost:8501 입니다. 처리 모듈을 수정한 뒤에는 실행 중인 서버를 종료하고 다시 시작하세요. 키를 터미널에 출력하지 마세요.

## 사용 흐름

1. **문서 분석**에서 내 문서 또는 가상 샘플 6종을 선택합니다.
2. Upstage 전송 동의 후 분석합니다. 샘플도 실제 API 요청과 비용이 발생합니다.
3. 건강검진은 규칙 기반 핵심 요약·집계·검사항목 표로 확인합니다. 원문 판정을 우선하고 숫자 범위·단일 임계값·괄호형 범주를 비교합니다. 성별 기준은 명시적인 성별 정보가 있을 때만 적용하며, 불명확한 값은 확인 필요로 표시합니다. 기준 충족을 의학적 정상 판정으로 해석하지 않습니다.
4. 복약정보는 약품별 정보 → 일정 관리 → 원문 대조 순서로 확인합니다. 원문 횟수를 하루 기준으로 자동 적용하지 않습니다. 확인한 정보와 시각을 입력하고 명시적으로 등록한 일정만 날짜별로 표시하며 CSV로 내보냅니다. 원문 추출값과 승인한 일정값은 분리해 보관합니다.
5. 기록 포트폴리오에서 JSON을 내려받고 다시 가져옵니다. 가져온 기록은 API 재검증되지 않은 자료로 표시합니다.

### 사진 입력

PNG, JPG, JPEG를 파일 업로드에서 선택하세요. 20MB 이하·3천만 화소 이하 정지 사진을 지원합니다. EXIF 방향 보정과 투명 배경의 흰색 처리를 한 뒤 단일 페이지 PDF로 변환하여 같은 Agent에 전달합니다. 원본 사진은 세션에 유지하고 결과 화면에서 미리보기/다운로드합니다. 사진 속 글씨가 작거나 흐리면 인식 정확도가 떨어질 수 있습니다. GIF/HEIC/애니메이션 PNG는 지원하지 않습니다.

## 구현 구조

| 파일 | 책임 |
|---|---|
| `app.py` | 대시보드, 업로드, 검진·복약 화면, 포트폴리오 |
| `carebridge/client.py` | 인증, 실제 업로드·Agent 실행·polling·파일 삭제 요청 |
| `carebridge/documents.py` | PDF 검사, PNG/JPG 검증·방향 보정·PDF 변환 |
| `carebridge/domain.py` | 기존 JSON 스키마 처리, 실제 Classify 응답 연결, 일정 검증 |
| `carebridge/health.py` | 원문 판정·범위·범주·성별 기준 비교와 일관된 요약 집계 |
| `carebridge/ui.py` | 문서 문자열을 이스케이프하는 공통 요약·표·상태 표시 |
| `carebridge/records.py` | 세션 기록 내보내기·가져오기 |
| `scripts/test_live.py` | 6개 PDF 실제 API 실행, 로컬 증거 저장 |
| `scripts/evaluate_live.py` | 원문에서 확인한 평가 전용 정답과 실측 결과 대조 |
| `scripts/test_photos.py` | 샘플 PDF를 렌더한 PNG/JPG 실제 분석 |
| `tests/` | 범위·일정·오류·이미지·화면 자동 테스트 |

앱에서 평가 정답이나 저장된 테스트 결과를 분석 결과로 불러오는 경로는 없습니다. 원본 Agent JSON은 읽기만 하며 원격 구성을 변경하지 않습니다. 기본 요약은 추출값과 원문 기준의 비교를 집계하며, 동의 후 선택적으로 FactChat 문서 안내를 제공합니다. 녹색은 문서 기준 충족, 주황색은 주의·숫자 기준 미충족, 빨간색은 원문의 명시적 이상 판정·범주, 회색은 확인 필요입니다.

분석 요청은 한 번만 생성하고 같은 작업을 제한시간 안에서 조회합니다. 업로드·Parse·Classify·Extract·결과 처리의 상태와 경과시간만 로그로 남기며 API 키·작업 원문은 기록하지 않습니다. 완료 후 파일 정리는 최대 5초만 기다리고 실패해도 결과를 보존합니다. 실패·타임아웃·응답 처리 오류는 자동 재분석하지 않고 화면에 표시합니다.

## 테스트

```bash
pip install -r requirements-dev.txt
python -m pytest -q
# 아래 명령은 실제 Upstage 호출 비용이 발생합니다.
python scripts/test_live.py
python scripts/evaluate_live.py
# 사진 실측: Poppler가 설치되어 있어야 첫 명령이 가능합니다.
mkdir -p tmp/pdfs
pdftoppm -scale-to 1200 -f 1 -singlefile -png samples/sample4.pdf tmp/pdfs/sample4
python scripts/test_photos.py
```

실제 API 응답은 Git 제외 폴더 `.local/live/`에 저장합니다. 정답은 테스트 도구에만 있으며 서비스 런타임에서 참조하지 않습니다. 자세한 실측 및 한계는 [테스트 보고서](docs/TEST_REPORT.md)를 확인하세요.

## 개인정보·운영 한계

- 사용자 문서·결과를 공용 디스크나 전역 캐시에 저장하지 않습니다. 브라우저 세션별 메모리에만 둡니다. 새로고침/재연결/서버 재시작 시 기록이 없어질 수 있으므로 JSON으로 보관하세요.
- 내보낸 JSON에는 건강정보가 포함됩니다. 원본 파일과 복약 일정은 포함하지 않습니다. 일정은 CSV로 별도 내려받습니다.
- 분석 후 업로드 파일 삭제를 요청하며 실패 시 표시합니다. Upstage의 작업 결과 보관은 파일 삭제와 별개입니다. 원본 PDF/사진은 동의 후 외부 API로 전송됩니다.
- 복약 일정은 사용자가 확인한 고정 일일 일정입니다. 필요시 복용, 복잡한 간격·교대 복용은 자동 해석하지 않습니다. 푸시 알림·실제 복용 체크·계정·영구 DB·가족 공유는 미구현입니다.
- 샘플 6종은 가상 문서입니다. 실제 환자 문서 정확도나 일반화 성능을 보장하지 않습니다. 에이전트가 지원하지 않는 문서를 두 유형 중 하나로 오분류할 가능성이 있습니다.
- 공개 URL의 인증·전역 비용 제한은 미구현입니다. 공개 해커톤 데모에서는 API 사용량을 모니터링하고 사용 후 중지하세요. 실제 의료정보 운영용 배포는 별도 인증·보관정책이 필요합니다.

## GitHub · Railway

기존 로컬 `.git`을 사용합니다. `.env`, `.local/`, `tmp/`, 가상환경은 Git 및 Docker 컨텍스트에서 제외합니다. 공개 GitHub 저장소의 `main` 브랜치로 푸시했고 Railway의 CareBridge 서비스에 배포했습니다.

Railway는 `Dockerfile`을 자동 감지해 빌드합니다. 신규 서비스에서는 레거시 Config as Code를 사용할 수 없어 `railway.toml`은 참고용입니다. 실제 서비스 Settings에 Healthcheck Path `/_stcore/health`, Timeout `120`, On Failure 재시도 `3`을 직접 적용했습니다. `start.sh`가 `PORT`(로컬 기본 8501)에 맞춰 `0.0.0.0`으로 실행하며 `/_stcore/health`를 확인합니다.

새 환경에 재현할 단계:

1. 대상 GitHub 계정과 저장소를 선택하고 코드를 푸시합니다.
2. Railway에서 해당 저장소를 연결합니다.
3. Railway Variables에 `UPSTAGE_API_KEY`, `UPSTAGE_AGENT_ID`를 비공개로 입력합니다. `.env`를 업로드하지 않습니다.
4. 배포 후 도메인을 생성하고 공개 URL에서 문서 업로드→분석→결과를 다시 검증합니다.
5. 최종 제출물: 공개 URL, GitHub URL, Agent JSON, 데모 영상, 발표 PDF. 영상/발표자료는 이번 구현에 포함하지 않았습니다.

실제 GitHub push 및 Railway 배포를 완료했습니다. 현재 공개 저장소 URL로 연결한 서비스는 **자동 배포가 꺼져 있습니다**. 코드 수정 후 Railway에서 최신 main 커밋으로 수동 배포하거나 별도로 GitHub App 연결을 구성하세요. API 키는 Variables에만 저장하며 저장소에는 포함하지 않습니다.

## 설계 참고

- [공식 해커톤 가이드](https://ku-sejong-hackathon-playbook.vercel.app/): 문서→Agent→화면→Railway 흐름, 제출물 확인. HR 예제는 복사하지 않았습니다.
- [Upstage Agents API](https://console.upstage.ai/docs/agents), [Jobs](https://console.upstage.ai/docs/agents/jobs): Files 업로드, Responses 생성, 비동기 조회와 출력 파싱.
- [Railway 설정](https://docs.railway.com/config-as-code/reference)
- 프로젝트 기획서와 `carebridge.json`의 실제 필드 정의를 기준으로 구현했습니다.

## 선택 기능: FactChat 문서 이해 도우미

Upstage Parse → Classify → Extract 이후 **쉬운 설명 보기**를 선택하고 전송 목록을 확인·동의한 경우에만 학교 Gateway를 호출합니다. 키 또는 모델이 없으면 버튼만 비활성화되며 기존 분석·기록·복약 일정은 계속 동작합니다. API 키는 `os.environ.get("FACTCHAT_API_KEY")`로 읽습니다. 로컬 `.env`와 Railway Variables를 사용하고 키를 저장소·명령 인자·로그에 넣지 마세요.

필수 설정(이 기능을 사용할 때만):

- `FACTCHAT_API_KEY`: 학교에서 발급한 비공개 Gateway 키.
- `FACTCHAT_MODEL`: 해당 계정에 실제 제공되는 텍스트 모델의 정확한 ID. 기본 모델을 임의로 지정하지 않습니다.
- `FACTCHAT_MAX_REQUESTS=1`: 초기 안전 한도. 실패·타임아웃을 포함해 서버 프로세스당 생성 요청 1회, 최소 요청 간격 30초. `0`은 호출 차단, 관리자가 검증 후 최대 `100`까지 조정 가능합니다. 다중 프로세스/서버 재시작을 가로지르는 영구 사용량 제한은 아닙니다.

키를 설정한 뒤 관리자가 아래 중 필요한 명령을 명시적으로 실행하세요. **각 명령은 메타데이터 GET 1회**이며 문서를 전송하지 않습니다. 초기 실제 Gateway 호출을 1회로 제한한다면 먼저 모델 조회만 실행하고, 생성/잔액 조회는 별도 승인된 검증 단계로 남기세요.

```bash
python -m carebridge.factchat_admin models
# 모델 ID와 pricing을 비교한 뒤 Railway FACTCHAT_MODEL에 입력
# 별도 필요 시에만 잔액 조회 (추가 GET 1회)
python -m carebridge.factchat_admin credits
```

[공식 모델 카탈로그](https://docs.factchat.kr/docs/korea-sejong/api-gateway/getting-started/models)는 국내 모델 `solar-mini4` 등을 안내하지만 계정별 지원과 단가는 `/models/` 응답이 기준입니다. 한국어 지원과 낮은 단가를 확인해 선택하세요. API 키가 없는 개발 환경에서는 계정 모델 목록·가격·실제 연동 성공을 검증할 수 없습니다. [공식 Chat Completions](https://docs.factchat.kr/docs/korea-sejong/api-gateway/reference/chat-completions)의 `POST https://api.baze.co/v1/gateway/chat/completions/`를 사용하며 OpenAI 등 다른 API로 우회하지 않습니다. 새 의존성을 추가하지 않습니다.

전송 데이터는 스키마 검증을 거친 검사명·수치·단위·참고기준·원문 판정 또는 약품명·복용정보·주의사항으로 제한합니다. 이름·문서명·날짜·파일·원문 근거 전체는 제외하고 알려진 이름/식별번호/전화번호/이메일을 필터링합니다. 자유 텍스트의 완전한 비식별화를 보장하지 않으므로 전송 미리보기를 사용자가 확인해야 합니다. 60개 초과 항목과 긴 필드는 설명 대상으로 처리하지 않습니다.

FactChat은 실제 한국어 **건강검진 핵심 요약 3~5문장(최대 650자)·접힌 항목별 설명 또는 복약 안내 요약 2~3문장·정답 해설**과 각 문장의 근거 ID를 함께 반환합니다. 응답 수신과 내용 검증은 별개이며, 문장/근거/수치 검사를 통과한 응답만 “FactChat 생성 설명”으로 직접 표시합니다. 기존 표·규칙 안내는 “기본 문서 안내”로 구분하고, 실패 시 원인을 표시하며 대체 안내를 AI 설명으로 표시하지 않습니다. 구형 ID 선택 응답은 새 설명 형식으로 인정하지 않습니다.

BMI·공복혈당·콜레스테롤의 일상적 정의는 [CDC BMI](https://www.cdc.gov/bmi/about/index.html), [MedlinePlus 혈당 검사](https://medlineplus.gov/lab-tests/blood-glucose-test/), [MedlinePlus 콜레스테롤 검사](https://medlineplus.gov/lab-tests/cholesterol-levels/)를 바탕으로 한 고정 용어 자료만 사용합니다. 일반 정의와 기존 규칙의 문서 비교 결과를 최소 추출값에 덧붙이며, 전송 미리보기와 설명 근거에서 원문·규칙·일반 용어를 구분합니다. 외부 진단 기준이나 치료 권고를 추가하지 않습니다.

생성 문장은 유효한 근거 ID·근거에 있는 숫자·기존 판정 보존 여부를 검사합니다. 복약 설명에는 약품별 원문 값과 확인 필요 안내를 보존하도록 하고 원문 밖 어휘·시점·용량·성분·부작용을 보수적으로 차단합니다. 의미의 완전한 자동 검증을 보장하지 않으며, 허용 문구 밖의 답변은 유용하더라도 표시를 거절할 수 있습니다. 각 설명의 근거를 펼쳐 원문과 대조할 수 있습니다.

복약안내문에서는 명확한 원문 근거가 두 개 이상일 때 객관식 2~3문항을 제공합니다. 질문 대상과 해설은 생성 응답을 사용하되 질문 문구는 원문 읽기 형식으로 제한하고, 정답은 앱이 해당 추출값에서 직접 정합니다. 오답 선택지로 임의의 용량·복용 시각을 만들지 않습니다. 모든 답을 선택한 뒤 명시적으로 채점하며 원문 정답·생성 해설·근거를 표시합니다. 답 변경 시 이전 채점은 초기화하고 화면 이동 시 답안은 유지합니다. 근거가 부족하면 문제를 생략하고, 답안을 복약 일정에 자동 반영하지 않습니다.

응답과 실패는 현재 세션의 분석별 캐시에 유지됩니다. 화면 이동·체크 조작은 재호출하지 않습니다. 실패/크레딧 부족/타임아웃은 기존 화면에 영향을 주지 않으며 자동 재시도하지 않습니다. 관리자 로그 `carebridge.factchat`에는 고정 상태·HTTP 코드·경과시간·토큰 수와 공식 응답 `billing.deducted_credit`만 남습니다. `billing`이 없는 계정은 사용량을 `None`(확인 불가)으로 기록하며 0으로 추정하지 않습니다. 키·문서 내용·응답 본문·사용자/기록 ID는 기록하지 않습니다. 설명/답안은 세션에만 두며 기록 JSON과 일정 CSV는 기존 형식을 유지합니다.


### 건강검진 리포트 표시

화면은 핵심 요약 → 생활 속 건강관리 가이드 → 상세 검진 수치 → 이해도 확인 순서입니다. 항목별 긴 설명과 근거는 기본적으로 접혀 있습니다. 원문 판정과 명확한 수치 비교가 충돌하면 요약·표 모두 `확인 필요`로 표시합니다. 단순히 숫자가 넓은 구간에 들어간다는 이유로 문서의 주의 판정을 뒤집지는 않습니다.

생활습관 가이드는 LLM이 `food/activity/sleep` 중 2~3개를 선택하고 앱이 검증된 고정 문구를 표시합니다. 검진표 원문이나 개인별 치료 권고가 아닌 일반 건강정보임을 표시합니다. 출처: [WHO 식습관](https://www.who.int/news-room/fact-sheets/detail/healthy-diet), [WHO 신체활동](https://www.who.int/news-room/fact-sheets/detail/physical-activity), [CDC 수면](https://www.cdc.gov/sleep/about/). 질병별 처방이나 운동량·식단 수치 목표를 생성하지 않습니다.

건강검진 이해도 확인은 명확한 문서 비교·검증된 용어 정의·판단의 한계로부터 만든 제한된 문제 후보 중 2~3개를 선택합니다. 숫자를 암기하는 문제는 거절하고, 선택 즉시 정답과 고정된 근거 해설을 표시합니다. 정보가 부족하면 문제를 생략합니다. 기존 복약 설명·확인 문제·승인 후 일정 등록 흐름은 유지합니다. 새 리포트 구조는 캐시 키에 반영하며 화면 조작으로 자동 재호출하지 않습니다.

### 설명 검증 실패 식별

HTTP 200은 응답 수신 성공이며 내용 검증 성공과 구분합니다. 건강검진은 `health_safety.py`에서 문맥을 검사합니다. 의학 용어가 등장했다는 이유로 거절하지 않고, 확정 진단·개인별 약물/치료 지시·근거 없는 정상 확정·개인별 생활습관 처방을 차단합니다. 진단의 한계는 고정 문장과의 일치 대신 부정 범위로 판별하며, 생성 문장에 제한 안내가 없더라도 UI가 검토된 진단·처방 한계 안내를 항상 표시합니다. 일반적인 건강정보는 허용하고 UI에서 일반 건강정보로 구분하며, 별도 생활습관 가이드는 검증된 `food/activity/sleep` 템플릿 2~3개를 계속 사용합니다. 복약 설명에는 원문 값 보존·엄격한 어휘/복용 시점 검사를 별도로 유지합니다.

API JSON 해석은 `parse_generated_content`, 안전성 검증은 `validate_plan`으로 분리했습니다. 관리자 로그는 `rule=document_medical_claim section=summary item=0`처럼 고정된 규칙 ID·섹션·항목 순번만 추가로 기록합니다. `document_lifestyle_prose`는 일반 안내를 벗어난 개인별 생활습관 처방, `lifestyle_template_only`는 승인되지 않은 템플릿 선택, `response_json`은 JSON 형식 오류입니다. 응답 원문·일치한 금지 단어·의료 수치·개인정보·API 키는 기록하지 않습니다. 실패 후 자동 재호출하지 않는 기존 정책은 유지합니다.


건강검진 수치 검사는 원문 숫자의 존재뿐 아니라 수치·단위 조합, 측정값과 참고기준의 역할, 범위·이상/미만 연산 방향을 대조합니다. 근거 ID가 틀리거나 원문 비교 결과를 바꾼 응답은 계속 거절합니다. 문맥 검사는 규칙 기반 보조 장치이며 모든 의학적 의미의 참·거짓이나 모든 우회 표현을 판별하는 의료 검증기는 아닙니다. 정상/위험 모의 Gateway 응답을 이용해 최종 UI·개념 이해 문제·실패 시 기본 화면 유지·동의·재사용을 테스트하며, 수정 작업에서 실제 Gateway 호출은 하지 않습니다.

### 데모 안전 검증 및 중간 결과 대조

- 원본 자료 확인에 동의하면 현재 세션의 Parse 텍스트·단계 상태를 Extract 원문 근거와 대조할 수 있습니다. 원본/중간 결과가 없거나 작업이 단순히 완료된 사실만으로 Parse 또는 Extract 오류라고 단정하지 않습니다. Parse 텍스트는 로그·기록 내보내기에 포함하지 않습니다.
- 복약 1회량은 양수와 명시된 단위를 확인하며 범위·OCR 불확실·불명확한 횟수/기간/시점은 확인 필요로 표시합니다. 일정 입력과 저장 함수에서 각각 검증하고 사용자 승인을 요구합니다. 필요시·격일 등 비일일 지시는 고정 일일 일정 등록을 차단합니다. 용량의 임상적 적절성을 판단하거나 단위를 변환하지 않습니다.
- [Gateway 공식 문서](https://docs.factchat.kr/docs/korea-sejong/api-gateway/reference/chat-completions)에 명시된 `response_format.type=json_schema`, `strict=true`, `additionalProperties=false`를 적용합니다. `FACTCHAT_RESPONSE_FORMAT=json_schema`가 기본이며 기존 응답 필드·근거 ID·문항 ID를 고정합니다. 로컬 수치·근거·의료정보 검증은 별도로 유지합니다. 일반 생활습관은 검토된 사전 작성 템플릿만 표시합니다.
- 모델이 구조화 출력을 지원하지 않는 경우 관리자가 `FACTCHAT_RESPONSE_FORMAT=prompt_json`을 명시적으로 선택할 수 있습니다. 이 경우 JSON 프롬프트 계약과 동일한 로컬 검증을 사용하며 실패 시 자동 재호출/형식 우회하지 않습니다. 모델별 실제 지원 여부는 모의 테스트만으로 확인할 수 없습니다.
- 회귀 테스트는 정상 검진 문서와 불확실한 복약 추출값의 모의 Agent → 동의 → 모의 Gateway → 리포트·문항 표시/일정 제한 흐름을 포함합니다. 실제 API 호출 없이 실행합니다.
