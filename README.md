# CareBridge 🌿

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
3. 건강검진은 항목 카드·상세 표·원문 근거·문서 참고범위 비교로 확인합니다. 참고범위가 복잡하거나 측정값이 없으면 비교하지 않습니다.
4. 복약정보는 약품별 원문 추출값을 표시합니다. 누락값을 채우지 않습니다. 원문 또는 약사에게 확인한 값을 별도 입력하고 시작일·알림 시각을 직접 정한 뒤 확인 체크를 해야 일정표를 생성합니다.
5. 기록 포트폴리오에서 JSON을 내려받고 다시 가져옵니다. 가져온 기록은 API 재검증되지 않은 자료로 표시합니다.

### 사진 입력

PNG, JPG, JPEG를 파일 업로드에서 선택하세요. 20MB 이하·3천만 화소 이하 정지 사진을 지원합니다. EXIF 방향 보정과 투명 배경의 흰색 처리를 한 뒤 단일 페이지 PDF로 변환하여 같은 Agent에 전달합니다. 원본 사진은 세션에 유지하고 결과 화면에서 미리보기/다운로드합니다. 사진 속 글씨가 작거나 흐리면 인식 정확도가 떨어질 수 있습니다. GIF/HEIC/애니메이션 PNG는 지원하지 않습니다.

## 구현 구조

| 파일 | 책임 |
|---|---|
| `app.py` | 대시보드, 업로드, 검진·복약 화면, 포트폴리오 |
| `carebridge/client.py` | 인증, 실제 업로드·Agent 실행·polling·파일 삭제 요청 |
| `carebridge/documents.py` | PDF 검사, PNG/JPG 검증·방향 보정·PDF 변환 |
| `carebridge/domain.py` | 기존 JSON 스키마 처리, 문서 참고범위 비교, 일정 검증 |
| `carebridge/records.py` | 세션 기록 내보내기·가져오기 |
| `scripts/test_live.py` | 6개 PDF 실제 API 실행, 로컬 증거 저장 |
| `scripts/evaluate_live.py` | 원문에서 확인한 평가 전용 정답과 실측 결과 대조 |
| `scripts/test_photos.py` | 샘플 PDF를 렌더한 PNG/JPG 실제 분석 |
| `tests/` | 범위·일정·오류·이미지·화면 자동 테스트 |

앱에서 평가 정답이나 저장된 테스트 결과를 분석 결과로 불러오는 경로는 없습니다. 원본 Agent JSON은 읽기만 하며 원격 구성을 변경하지 않습니다. 별도 생성형 의료 설명은 제공하지 않고, 앱 요약은 추출값을 단순 집계합니다.

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

기존 로컬 `.git`을 사용합니다. `.env`, `.local/`, `tmp/`, 가상환경은 Git 및 Docker 컨텍스트에서 제외합니다. 원격 푸시와 배포 승인은 받았으며, 계정 로그인 및 대상 저장소 지정 후 진행합니다.

Railway는 `Dockerfile`과 `railway.toml`로 빌드합니다. `start.sh`가 `PORT`(로컬 기본 8501)에 맞춰 `0.0.0.0`으로 실행하며 `/_stcore/health`를 확인합니다.

계정 연결 후 진행할 단계:

1. 대상 GitHub 계정과 저장소를 선택하고 코드를 푸시합니다.
2. Railway에서 해당 저장소를 연결합니다.
3. Railway Variables에 `UPSTAGE_API_KEY`, `UPSTAGE_AGENT_ID`를 비공개로 입력합니다. `.env`를 업로드하지 않습니다.
4. 배포 후 도메인을 생성하고 공개 URL에서 문서 업로드→분석→결과를 다시 검증합니다.
5. 최종 제출물: 공개 URL, GitHub URL, Agent JSON, 데모 영상, 발표 PDF. 영상/발표자료는 이번 구현에 포함하지 않았습니다.

실제 외부 배포나 GitHub push는 아직 수행하지 않았습니다.

## 설계 참고

- [공식 해커톤 가이드](https://ku-sejong-hackathon-playbook.vercel.app/): 문서→Agent→화면→Railway 흐름, 제출물 확인. HR 예제는 복사하지 않았습니다.
- [Upstage Agents API](https://console.upstage.ai/docs/agents), [Jobs](https://console.upstage.ai/docs/agents/jobs): Files 업로드, Responses 생성, 비동기 조회와 출력 파싱.
- [Railway 설정](https://docs.railway.com/config-as-code/reference)
- 프로젝트 기획서와 `carebridge.json`의 실제 필드 정의를 기준으로 구현했습니다.
