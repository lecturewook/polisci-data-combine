# PoliHub · 정치학 비교연구 데이터 허브

**[PoliHub-v7.html 다운로드](https://github.com/lecturewook/polisci-data-combine/releases/download/polihub-v7-44642268512-1/PoliHub-v7.html)** · [v7 Release](https://github.com/lecturewook/polisci-data-combine/releases/tag/polihub-v7-44642268512-1) · [이전 v6](https://github.com/lecturewook/polisci-data-combine/releases/download/polihub-v6-44513527044-1/PoliHub-v6.html) · [이전 v5](https://github.com/lecturewook/polisci-data-combine/releases/download/polihub-v5-44434383132-1/PoliHub-v5.html) · [배포 기록](https://github.com/lecturewook/polisci-data-combine/releases)

한국과 동아시아를 중심으로 선거, 정치태도, 의회, 행정, 정책, 민주주의 자료를 찾고 연구별로 수집하는 도구다. 화면과 Python CLI가 같은 카탈로그를 읽는다. 원자료 카탈로그 기준일은 2026년 11월 18일이다. 69개 저널의 논문·방법론 색인에는 별도로 수집 기간과 갱신일을 표시한다.

## 시작하기

위에서 내려받은 **PoliHub-v7.html**을 브라우저에서 열면 설치 없이 저널·방법론 색인이 표시된다. 상단 메뉴에서 원자료 탐색, 비교표, 연구명세 내보내기를 이용할 수 있다. 브라우저의 외부 파일 접근이 허용된 경우 제작자 배포처에서 파일을 받아 해시도 대조한다. 연구 폴더에 원자료를 수집하고 R·Stata를 실행하려면 소스 묶음을 풀고 아래 명령을 실행한다.

```bash
# Python 3.11–3.13. 검증 환경은 Python 3.12 / Linux x86_64
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m polihub doctor --context local
python -m polihub serve
```

브라우저에서 실행기가 표시한 주소의 8000번 포트를 연다. Windows PowerShell의 활성화 명령은 `.venv\Scripts\Activate.ps1`이다. 화면에서 자료명을 누르면 조사대상·배포 버전·배포처·변수 근거가 열린다. 자료를 선택하고 **연구 작업공간 → 일괄 수집·가져오기**를 누르면 연구 폴더가 생긴다. 원격에서 정적 화면만 여는 경우 연구명세를 내려받아 Codex에 제공할 수 있다. 로컬 API의 쓰기 작업과 연구 결과 조회는 루프백에서 이용한다.

관리형 Debian/Linux x86_64에서는 `bash scripts/setup-cloud.sh`가 Python 가상환경과 R 4.5.3, readr, haven을 준비한다. 검증한 micromamba 바이너리와 SHA-256으로 고정한 conda 패키지 목록을 사용한다. 다른 운영체제에서는 해당 플랫폼의 R을 설치하고 R에서 `install.packages(c("readr","haven"))`을 실행한다. 설치 위치를 직접 지정할 때는 `POLIHUB_RSCRIPT`와 `POLIHUB_STATA`에 해당 실행파일 경로를 설정한다.

## 자료를 찾고 연구로 가져오기

```bash
python -m polihub search "한국 총선 후보와 당선" --limit 3
python -m polihub show ert
python -m polihub variables 민주주의
python -m polihub access kgss
python -m polihub plan --ids pop-candidates ert kgss --spec studies/my/study.json
python -m polihub pipeline --spec studies/my/study.json --engine r
python -m polihub status --spec studies/my/study.json
```

`plan --question "연구질문" --spec studies/my/study.json`은 일치하는 개념과 자료명을 기준으로 최대 세 자료를 선택한다. 자료 선택 근거는 `search`에서 확인한다. 연구명세의 모집단·추정대상·모형은 연구자가 채운다. 연구질문이나 추정계획이 비어 있으면 파일 수집·가져오기·검증까지 진행한다. `examples/study.json`은 공개파일 여덟 개와 대기 사례 두 개가 들어 있는 수집 예시다. 각 연구는 개별 폴더에 둔다.

| CLI 명령 | 실행 내용 |
|---|---|
| `doctor [--spec …] [--context local\|remote\|cloud]` | OS, 엔진·패키지 버전, Stata 배치, 네트워크, 저장공간·쓰기와 인증 변수 존재 점검 |
| `acquire --spec …` | 배포판별 수집·SHA-256 검증·캐시·ZIP 해제·데이터 읽기 |
| `register --spec … --id … --version … --file …` | 이용권한이 있는 파일을 연구의 실제 배포판으로 등록 |
| `validate --spec …` | 변수·키·표본·가중치·국가·결합 점검, 변수사전과 비교 보고서 생성 |
| `prepare --spec … --engine r` | R 가져오기 실행 및 관측·열 수 대조. Stata는 `--engine stata` |
| `run --spec … --engine r` | 검증, 가져오기 및 사전 명세의 추정·민감도·원문 대조 |
| `pipeline --spec …` | 수집부터 실행까지 한 번에 처리 |
| `verify --spec …` | 실행 전 명세·현재 파일·결과표의 해시를 확인하고 원문 수치 재대조 |
| `status --spec …` | 자료별 수집·점검·엔진 상태, 모델 결과와 산출물 경로 조회 |

`--scripts-only`는 prepare·run·pipeline에서 엔진을 실행하지 않고 스크립트를 생성한다. CLI 출력은 JSON이다. 오류 종료코드 1은 요청·명세 오류, 2는 처리한 항목 중 실패 또는 차단을 뜻한다. 신청 대기와 엔진 부재는 JSON에서 각각 확인한다. 원자료 내용은 CLI 응답에 싣지 않는다.

## 접근조건과 파일 보관

- 공개 파일은 제작자 배포판의 버전·파일명·크기·SHA-256을 카탈로그와 대조한다. 출처가 공공기관이어도 민간 가공본이면 제작자와 원출처를 함께 표시한다. 다른 연구에서도 해시가 같은 파일을 재사용한다. 기본 캐시는 `.cache/polihub`이며 `POLIHUB_CACHE`로 위치를 지정할 수 있다.
- 등록·신청 자료는 기관에서 해당 배포판의 이용조건을 확인하고 내려받은 뒤 등록한다. CSV·TSV·DTA·SAV, 단일 표 RDS·RData와 해당 표를 담은 ZIP을 지원한다. CES의 배포 형식 중 DTA와 RDS를 지원한다. ZIP 내부에 표가 여럿이면 `--member "경로/파일명"`을 지정한다. CSV 인코딩은 `--encoding cp949`처럼 지정할 수 있다. 스프레드시트·Feather·HWP의 자동 추출은 제공하지 않는다.
- R 자료의 Python 점검은 pyreadr가 읽을 수 있는 단일 데이터프레임을 사용한다. 로컬 R은 원본 객체의 속성과 factor 레벨을 보존한다. Stata용 변환은 R과 haven으로 별도 DTA를 만든다. 출처와 읽기 권한이 확인된 파일을 등록한다.
- Manifesto Project는 기관에서 발급한 키를 `MANIFESTO_API_KEY`로 설정하고 선택 항목의 `version`에 실제 MPDS 버전을 지정한다. CLI는 공식 클라이언트의 POST 규격을 사용한다. 응답 JSON 원본과 디코딩한 DTA를 분리해 보관한다. 이번 검증에서 인증 수집은 대기 상태였다.
- 원자료·인증정보를 소스 묶음과 웹사이트에 포함하지 않는다. 프로젝트의 `data/raw`는 해시별 원본을 보존한다. 변환파일은 `data/processed`에 저장한다. 네트워크 오류·로그인 HTML·파일형식 불일치·해시 불일치는 자료별 실패로 기록한다.
- 링크가 바뀌면 `python -m polihub check-links --id ert --log studies/link-review/history.json`으로 공식 페이지의 배포 후보와 검사 이력을 남긴다. 검토자는 새 파일의 형식·해시·버전과 변수 대응을 확인한 뒤 새 배포판으로 등록한다.

## 카탈로그와 검증 기록

- [기계 판독용 카탈로그](catalog/catalog.json)
- [공식 페이지·파일 접속 기록](catalog/sources.json)
- [연구명세와 결과 판정 방법](docs/STUDY.md)
- [실제 시험 결과 및 확인이 필요한 항목](docs/VALIDATION.md)
- [검증 결과 JSON](docs/readiness.json)

기본 화면에는 대표자료 7개가 나온다. 국가 필터는 자료 대상 지역을 사용한다. 국제조사의 프로젝트 범위와 개별 배포판 국가표는 따로 보관한다. 접근하지 못한 배포판의 기간·수록국·변수·표본 수는 미확인 상태다. 포털·조건부 패널 후보·선별 보류 자료는 이용 안내에서 확인한다.

기능 시험은 `python -m unittest discover -s tests -v`로 실행한다. 단독 HTML과 원자료 없는 소스 묶음은 `python scripts/package.py --out /원하는/폴더`로 만든다. 지도는 Natural Earth의 공용 도메인 1:110m 국가 경계로 그렸다. [Natural Earth 원본](https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_110m_admin_0_countries.geojson)

## 저널·방법론 색인

독립 HTML을 브라우저에서 열면 저널 색인이 먼저 표시된다. 상단 메뉴에서 원자료 탐색과 연구 작업공간으로 이동할 수 있다. 저널 화면에서 출처별 응답, 분야별 저널 목록, 연도별 근거 확보율을 확인할 수 있다. 관심사별 저널에서 인구학·인구정책, 국제이주·난민, 인종·민족과 대표성, 선거·정당일체감, 인과추론·정책평가, 한국 전문 저널, 동아시아 지역연구, 미국, 정치학 종합 중 하나를 고르면 해당 저널의 발행 목록과 방법 그래프가 함께 바뀐다. v7에서 추가한 18종은 각 저널의 선정 이유와 근거 출처를 열 수 있다.

방법 그래프의 초록색 위 막대는 해당 방법의 적용을 확인한 논문을, 황토색 아래 막대는 방법명 단서만 찾은 논문을 센다. 논문을 선택하면 방법을 확인한 위치, 초록의 출처, Google Scholar에서 원제목을 검색하는 링크가 나온다. 저널별 확보 현황에서 연도별 초록 확인 편수와 분모를 볼 수 있다.

2026년 11월 20일 v7 검증본에는 69종의 고유 발행 기록 29,448건이 있다. 추가한 18종에서 6,988건을 수집했고, 그중 6,680편이 방법론 집계 대상이다. 전체 방법론 집계 대상 26,637편 중 24,240편(91.0%)에서 출처가 있는 초록을 확인했다. 69종 모두 Crossref가 보고한 건수를 마지막 페이지까지 수신했다. 출판사 전체 목차와의 대조를 마친 저널은 아직 없으며, 화면에서 실제 대조한 권호와 연도별 초록 확보율을 확인할 수 있다. [v7 수집·다운로드 검사 로그](https://github.com/lecturewook/polisci-data-combine/actions/runs/44642268512)

한국 전문 저널의 방법론 집계 대상은 『한국정치학회보』 124편, 『한국인구학』 86편, *Korea Observer* 126편이다. 세 저널 모두 2022~2026년 권호 발행연도별 논문 수가 확인일의 KCI 집계와 일치한다. *Korea Observer*의 55권 4호 목차 1건은 발행 목록에 별도로 보존했다. [Korea Observer 57권 3호](https://www.kci.go.kr/kciportal/po/search/poSereArtiList.kci?sereId=000783&volIsseId=VOL000200372) · [55권 4호 목차 서지](https://m.riss.kr/search/detail/DetailView.do?control_no=cf91561014fcdfceb7998d826d417196&p_mat_type=1a0202e37d52c72d)

첫 수집 기간은 2021년 11월 19일~2026년 11월 19일이다. v5 최초 검증본은 51종 모두 Crossref의 마지막 페이지까지 조회했고, 출처를 합친 고유 발행 기록 22,442건을 수록했다. 그중 서평·정정 등 1,798건을 제외한 20,644편을 방법 집계 대상으로 삼았다. OpenAlex 연도별 집계는 51종 모두 대조했고, 개별 레코드 조회는 저널당 첫 100건 이후 인증을 요구했다. [최초 수집 검증본](https://github.com/lecturewook/polisci-data-combine/releases/tag/polihub-v5-44434383132-1) · [수집·브라우저 검사 로그](https://github.com/lecturewook/polisci-data-combine/actions/runs/44434383132)

2026년 11월 19일 v6 검증본은 발행 기록 22,454건을 보존한다. 방법론 집계 대상 19,952편 중 18,221편(91.3%)에서 초록을 확인했다. v5에서 초록이 비어 있던 기존 논문 4,929편과 이후에 추가한 논문 7편에서 초록을 확보했다. v5의 집계 대상 중 발행 유형을 다시 확인한 편집자료·서평·정정 등 701건은 발행 목록에서 볼 수 있다. 미확인 1,731편 중 307편은 출판사가 별도 초록을 제공하지 않는다고 표시했다. [이 검증본 다운로드](https://github.com/lecturewook/polisci-data-combine/releases/download/polihub-v6-44513527044-1/PoliHub-v6.html) · [v6 수집·다운로드 검사 로그](https://github.com/lecturewook/polisci-data-combine/actions/runs/44513527044)

```bash
python -m polihub.journals
python scripts/package.py --out . --versioned-only
python scripts/journal_summary.py
```

[수집 범위·중복 처리·분류 근거](docs/JOURNALS.md) · [저널 설정](catalog/journals.json) · [방법 분류와 별칭](catalog/methods.json) · [월별 수집 워크플로](.github/workflows/journals.yml)
