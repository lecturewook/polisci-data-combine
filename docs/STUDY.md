# 연구명세 작성과 수치 검증

## 자료에서 추정대상까지

`study.json`은 연구 폴더의 기준 파일이다. `plan` 또는 화면에서 만든 명세를 열고 아래 항목을 채운다. 고정 파일의 `datasets[].release`와 `version`은 카탈로그와 일치해야 한다. 신청 파일은 실제 받은 배포판을 `register --version`으로 기록한다.

| 항목 | 기록할 내용 |
|---|---|
| `question`, `objective`, `population`, `unit` | 답할 질문, 연구목적, 모집단, 관측단위 |
| `datasets[]` | 선택 자료·배포판·버전·국가코드·기간. 빈 국가 목록은 배포판 전체 |
| `variable_definitions[]` | 자료 ID, 실제 열 이름, 이론적 개념, 유형, 원문 질문·척도·결측 코드, 코드북 URL과 쪽·표 번호 |
| `design_review` | 자료별 표집틀·가중치·코드 변경과 자료 한계 검토 |
| `comparisons[]` | 같은 개념을 측정하는 국가별 문항·표본·실사시점 대응표 |
| `analyses[]`, `sensitivity[]` | 모형 ID, 추정대상, 종속·설명·통제변수, 표본·결측·가중치·표준오차, 대안 사양과 이유 |
| `references[]` | 원문 논문·부록과 표·모형, 통계량·단위·사전 허용오차, 표본·코딩·가중치·추정방법 |
| `hypothesis`, `assumptions`, `engine`, `seed` | 사전 예상 방향과 식별 가정, R 또는 Stata, 난수시드 |

국가코드는 **선택한 배포판의 `countries` 키**를 사용한다. ERT v16의 한국·일본·중국·대만·미국·영국 코드는 KOR·JPN·CHN·TWN·USA·GBR이다. ERT의 `year`는 달력연도다. 한국 총선 후보 자료의 `assembly_id`는 국회 대수이므로 연도 필터로 사용할 수 없다. 특정 국가·연도가 실제 파일에 있는지도 실행 시 다시 확인한다.

신청 파일의 키와 설계를 추가할 때는 해당 자료 항목의 `design`에 `evidence`를 남긴다. `keys`, `panel_id`, `wave_key`, `year_key`, `country_key`, `countries`, `weight_key`, `strata_key`, `cluster_key`, `household_key`, `area_key`, `missing_tokens`를 지원한다. `countries`는 파일의 국가값을 키로, [최초연도, 최종연도]를 값으로 갖는다. 수록기간을 모르면 null로 남긴다.

## 자료점검과 비교

`validate`는 파일 해시, 관측 수·변수 수, 소스 헤더, 자료형과 인코딩, 라벨·특수결측, 후보키 중복과 누락을 검사한다. CSV 식별자는 기본적으로 문자열로 가져와 앞자리 0을 보존한다. 코드북에서 수치형으로 확인한 열만 수치 변환한다. ERT의 이름 없는 CSV 인덱스 열은 작업본에서 `source_row`로 이름을 붙인다. 원본 헤더는 검증 JSON에 기록한다.

패널 ID와 차수를 지정하면 ID×차수의 유일성과 인접 차수 진입·미관측 수를 계산한다. 가구·지역 키를 주면 동일 ID의 코드 변경과 시점별 지역코드 출입을 확인한다. 조사 중단·보충표본·분가·이사의 원인은 차수별 표본 관리 문서로 판단한다. 제작자 문서와 행 수의 차이처럼 명시적으로 보류한 사유를 해결한 경우 design_review의 해당 자료 항목에 resolved_notes, evidence, explanation을 기록한다. 키·해시·표본 검사는 계속 적용된다. 연도별 가중치와 종단 가중치를 구분해 명세한다. 동일 개념의 가중치라도 적용 모집단과 시점을 기록한다.

`join_checks[]`는 실제 결합을 수행하기 전, 지정한 키만으로 양측 행 수·외부결합 행 수·일치 행 수·미결합률을 계산한다. 각 점검에는 `left`, `right`, `keys`, `key_definition`, `evidence`, `cardinality`, `max_unmatched`가 필요하다. 허용하는 관계는 one_to_one, one_to_many, many_to_one이다. 서로 다른 조사의 개인·가구 ID와 many-to-many 결합은 차단한다. 최종 분석 테이블을 만드는 연구자 코드는 키 검증 결과와 같은 관계를 사용해야 한다.

`comparability_report.md`에는 원문 문항, 국가·시점, 척도, 모집단, 조사방식, 가중치, 변환규칙이 들어간다. 다국가 사양에는 모형의 `comparison: true` 및 자료 항목의 국가 목록을 지정한다. 각 국가 대응에 `dataset, country, concept, study_country, question, variable, time, scale, population, mode, weights, transform, grade, evidence, translation, fieldwork, institutions, geography`를 기록한다. `grade`에는 ‘동일 프로젝트의 공통문항으로 비교 가능’, ‘추가 검토와 가정하에 제한적 비교 가능’, ‘현재 자료로 직접 비교 불가’ 중 하나를 쓴다. ERT는 공통 지표 정의와 선택 기간의 국가별 수록 범위를 검사한다. KGSS–GSS, ANES–BES, EASS–ESS 사이에는 자동 문항 대응 관계가 없다.

## 분석 코드

내장 추정기는 **관측 수, 평균, OLS**를 R 또는 Stata 코드로 작성한다. 모형 객체의 형식은 다음과 같다. 꺾쇠 안에는 자신의 연구와 코드북에서 확인한 값을 넣는다.

```json
{
  "id": "main",
  "dataset": "<선택한 자료 ID>",
  "method": "ols",
  "outcome": "<수치형 종속변수>",
  "predictors": ["<설명변수>"],
  "controls": [],
  "filters": [],
  "weights": "none",
  "se": "classical",
  "unit": "<보고 단위>",
  "estimand": "<표본 또는 모집단의 추정대상>",
  "sample_rule": "<포함·제외 기준>",
  "timing": "<변수·처치·결과 시점>",
  "coding": "<코딩 규칙>",
  "missing": "complete_case",
  "design_basis": "<가중치·표본설계 판단 근거>",
  "assumptions": "<해석에 필요한 가정>"
}
```

`filters`에는 `{"variable":"<변수>","op":"eq","value":"<범주>"}`처럼 조건을 쓴다. 연산자는 eq, ge, le이다. `missing`은 complete_case 또는 error다. 변수사전에 `missing_codes` 배열로 문항별 비해당·특수결측 코드를 지정할 수 있다. 사전 정의가 필요한 역코딩·파생변수는 검토한 코드에서 계산한다. count에는 종속변수·설명변수를 지정하지 않는다. mean은 수치형 결과 하나를 사용한다. 내장 모형의 weights는 none이며, OLS는 classical 표준오차를 계산한다. 복합표본이나 클러스터가 필요한 명세는 전용 코드 경로를 사용한다.

민감도 사양은 `sensitivity`에 별도 모형 ID와 `base_model`, `rationale`을 함께 넣는다. 각 모형의 완료·차단 상태를 남긴다. `hypothesis`의 `model, statistic, direction`으로 positive 또는 negative 방향을 미리 지정할 수 있다. 보고서의 방향 일치 판정은 해당 계수의 부호만 평가한다. 유의성 검정과 식별 가정에 대한 논증은 연구설계에 따라 별도로 작성한다.

### 연구자가 작성한 추정 코드

복합표본·패널·DID·이벤트스터디 등은 `method: "script"`를 지정한다. 연구자가 검사한 R 또는 Stata 코드를 연구 폴더의 `scripts/`에 넣고 상대경로 `script`, SHA-256 `script_sha256`, `method_detail`, `required_variables`, `statistics`를 명세한다. 직접 작성한 스크립트는 CLI의 run 또는 pipeline에서 실행한다. 파일명에는 영숫자·밑줄·하이픈을 쓸 수 있다.

- `method_detail`이 panel_fe, did, event_study이면 실제 키 `id_key`와 `time_key`를 검사한다. DID·이벤트스터디는 수록된 `treatment_time` 변수, `comparison_group`, `identification` 근거가 필요하다. survey 사양에서는 `weight_key`, `strata_key`, `cluster_key`를 검사한다.
- 코드는 연구 루트에서 실행된다. `data/processed/<자료 ID>/import.rds` 또는 `import.dta`를 읽고 `scripts/sample-<모형 ID>.csv`의 1부터 시작하는 원본 행 번호 안에서 표본을 선택한다. 결측·가중치와 추정 세부 구현은 코드 검토 대상이다.
- 출력은 `outputs/<모형 ID>-estimates.csv`이며 열은 statistic, estimate, std_error, n이다. 엔진의 정상 완료 표식, 사전 통계량 목록, 유한한 추정치 및 사용가능 범위 안의 표본수를 확인한다. 임의의 통계 코드가 연구 설계를 올바르게 구현했는지는 연구자가 검토한다.

## 원문 수치 대조

`references[]`의 각 행에는 source, table, model, statistic, original, tolerance, tolerance_basis, unit, expected_n, data_version, sample, coding, weights, estimator, se, rounding, original_code를 지정한다. `measure`는 estimate(기본값), std_error, n 중 하나다. 원문 사양과 모형의 표본·코딩·추정법 표현은 같은 정의를 사용할 때 같은 문자열로 기록한다. 원문 코드의 공개 여부·위치나 이용할 수 없는 이유도 기록한다.

실행기가 추정 전에 연구명세 사본과 해시를 기록한다. 차이는 재현값에서 원문값을 뺀 값이다. 사전 절대오차, 표본 수, 배포판, 단위를 대조하고 사양 확인 결과를 함께 보고한다. 일부 통계량만 기준을 충족한 경우 연구의 판정은 ‘부분 재현’이다. 불일치 시 변수 코딩·결측·표본·가중치·표준오차·버전 차이를 검토한다. 원문 대조가 없는 연구는 ‘미실행’으로 기록한다.

## 연구 폴더에서 확인할 파일

| 위치 | 내용 |
|---|---|
| `study.json` | 연구질문, 변수·표본·모형·비교 기준 |
| `acquisition_manifest.json` | 출처, 버전, 파일 경로·크기·해시, 코드북 링크, 자료별 수집 이력 |
| `data/raw/<자료 ID>/<SHA-256>/` | 수신한 원본. API는 원본 응답도 보관 |
| `data/processed/` | 안전하게 해제한 표, 자료별 RDS·DTA, R의 이름 있는 목록 및 속성 |
| `scripts/`, `logs/` | 수집 재실행기, 자료별 가져오기·분석 코드, 표본 행 번호, 엔진 로그, 실행 전 명세·이전 결과 |
| `variable_map.csv`, `comparability_report.md` | 변수의 개념·코드북 위치, 국가·조사별 비교 근거 |
| `validation_report.json` | 해시·열·결측·키·결합·표본설계 점검 |
| `outputs/results.csv`, `outputs/figures/` | 실행된 모형의 추정치·표준오차·표본 수, 모형별 그림 |
| `outputs/verification.csv`, `outputs/report_ko.md` | 원문·재현값·차이·판정, 한국어 실행 보고서 |
| `outputs/artifact_manifest.json` | 실행 명령·시드·버전·해시·실행 단계와 산출물 목록 |

R은 자료를 ID별로 저장하고 `scripts/load-study.R`에서 이름 있는 목록으로 구성한다. Stata 16 이상은 `scripts/load-study.do`의 frames를 사용할 수 있다. 각 자료에 독립적인 import do-file도 생성한다. Stata에서 SPSS 가져오기를 지원하지 않는 버전은 DTA 배포판 또는 검토한 R 변환본을 사용한다. 명령은 운영체제의 배치 방식으로 실행하고 종료코드·로그의 오류·완료 표식을 모두 확인한다.
