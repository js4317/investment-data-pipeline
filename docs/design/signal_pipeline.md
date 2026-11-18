# 신호 생성·기록·성과 측정 설계 (F02·F03·F04·F05·F10)

작성: 2026-10-14 · 상태: **검토용 초안 v3 (구현 전)** · 기준 소스: v8.6.6 (commit 8a38bde)

점수 계산·가중치·등급 기준은 이 설계에서 변경하지 않는다. 바꾸는 것은 "언제 어떤 정보로 판단했는가"와 "무엇을 공식 표본으로 확정하는가"의 기록 방식이다.

**v3 변경 (v2 대비):** 입력을 시장자료와 재무로 나눠 다른 시점 규칙 적용(§2·§3), 시장자료 신선도를 실행 고정 기준일(`expected_market_session`)과 대조(§3), 시장 수집 완결성과 종목별 가격 가용성 분리·0 이하 가격 불허(§7.3·§7.4), completed 실행의 확정 실패 후에는 catchup(§5.2·§6), KRX 응답 거래일 필드 확인 결과와 남은 결정 5건 반영(§2·§11).
**v2 변경 (v1 대비):** 결정 7건 반영, 거래일 판정을 가격 수집 결과와 분리, 진입 규칙 단일 함수, primary 예외 제거와 분류·적격성 분리, 유니버스 완결성, 실행 코드 동일성, 성과 정의 고정.
이전 원문: `_verification_2026-10-14/signal_pipeline_draft_v1.md`, `..._v2.md`.

---

## 0. 목표와 완료 기준

공식 OOS 표본은 다음을 만족해야 한다.

1. 신호에 사용한 정보가 진입보다 먼저 확정·확보되었음이 기록으로 증명된다. 증명할 수 없으면 공식 표본에서 제외된다.
2. 같은 (날짜·전략·엔진·유니버스)의 공식 표본은 한 번만 확정되고, 이후 재실행·부분 실패·가격 보충이 그 구성을 바꾸지 않는다.
3. 늦은 실행과 과거 날짜 보충은 primary가 될 수 없다.
4. 진입일은 거래 달력으로 정해지며 가격 수집 성공 여부로 이동하지 않는다. 가격이 없으면 대기한다. 임의 대체 가격을 쓰지 않는다.
5. 기존 기록은 수정·삭제하지 않고, 평가 적격성과 제외 사유는 별도로 관리한다.

완료 기준은 §10의 격리 검사 전 항목 통과다. ③-D 격리 장치가 PASS하기 전에는 검사도 시작하지 않는다.

---

## 1. 현재 구조의 결함 요약

| 감사 | 현재 동작 | 결과 |
|---|---|---|
| F02 | 16시 이전 실행이면 무조건 `same_day_open`. KIS 경로는 PER·PBR·시가총액을 조회 순간의 현재가로 계산하고 `as_of_date`를 무시함. `market_data_date`는 "전 영업일"로 기록 | 장중 가격으로 판단하고 그보다 이른 시가에 진입한 것으로 기록 (정보 시점 역전) |
| F03 | 같은 날 스냅샷 저장 시 종목 병합, 최초 `analysis_timestamp`·`entry_basis` 유지, `rule_version`은 덮어씀 | 늦게 계산된 점수가 일찍 존재한 것처럼 보임. 엔진 혼합(09-30) |
| F04 | `UNIQUE(signal_date, ticker)` + `INSERT OR IGNORE` | 재기록 시 Top 10 초과(09-30 실제 14개) |
| F05 | `record_oos --date`가 분류값을 넘기지 않아 primary 기본값 | 사후 보충이 primary로 섞임 |
| F10 | 08:30에 당일 일봉 수집 시도 후 바로 entry/alpha 채움. 누락 거래일 보충 없음 | 가격 DB가 09-30에서 멈춤 |
| F06 | 진입일을 "다음 평일"로 정하고 그 날짜 가격만 찾음 | 휴장일 진입일은 영구 pending |

추가 확인: `market_db._collect_via_open_api`는 시장별 실패를 출력만 하고 성공한 시장분은 저장한다(부분 수집이 완료처럼 남음). 응답 행의 거래일 필드를 확인하지 않고 요청 날짜를 그대로 `trade_date`로 쓴다.

---

## 2. 시각·시점 정보 (모든 공식 기록에 저장)

입력은 **시장자료**와 **재무자료** 두 종류로 나누고 서로 다른 시점 필드와 규칙을 적용한다. 날짜 필드 하나로 두 종류를 판단하지 않는다.

### 2.1 공통

| 필드 | 정의 |
|---|---|
| `input_class` | `market` / `financial` |
| `fetched_at` | 우리 프로세스가 값을 조회한 시각 (로컬 시계) |
| `source_status` | `verified` / `unverified` (종류별 조건은 아래) |
| `cohort_confirmed_at` | 공식 표본 확정 트랜잭션의 커밋 시각 |
| `planned_entry_session` | §3 함수로 정한 진입 거래일 (거래 달력 기준) |

### 2.2 시장자료 (가격·시가총액·PER·PBR·변동성 등)

| 필드 | 정의 |
|---|---|
| `source_session_date` | 원천 **응답 본문**이 명시한 거래일. 요청 파라미터로 채우지 않는다 |
| `source_as_of_time` | 원천이 정확한 시각을 줄 때만 기록. 없으면 NULL. 거래일만 아는 자료에 15:30 같은 시각을 임의로 붙이지 않는다 |
| `verified` 조건 | 응답이 거래일을 명시하고, 그 값이 실행의 `expected_market_session`(§3)과 같을 때 |

### 2.3 재무자료 (DART)

| 필드 | 정의 |
|---|---|
| `report_period_end` | 보고 기간 종료일 |
| `report_type` | 사업·반기·분기보고서 구분 |
| `rcept_no`, `rcept_dt` | 실제로 수치를 가져온 공시의 접수번호·접수일 |
| `value_fetched_at` | 수치를 처음 받아온 시각 (캐시 가능) |
| `filing_list_fetched_at` | 이번 실행에서 공시 목록을 조회해 최신 버전을 확인한 시각 |
| `is_amendment` | 정정 공시 여부 |
| `verified` 조건 | ① 사용한 수치가 해당 `rcept_no` 응답에서 나왔다는 연결이 저장됨, ② `rcept_dt` ≤ 조회일, ③ 같은 (회사·기간·보고서 종류)에 대해 조회 시점까지 접수된 공시 중 최신 버전(정정 포함)을 사용함 |

재무자료에는 "직전 세션" 규칙을 적용하지 않는다. 재무는 보고 기간과 공시 시점으로 판단한다.

### 2.4 원칙
- `fetched_at`으로 원천 거래일·시각을 대신하지 않는다.
- 요청한 날짜만으로 `verified`로 승격하지 않는다.

### 2.5 입력별 공식 경로

| 입력 | 공식 경로 | 현재 상태 |
|---|---|---|
| 일봉(OHLCV)·시가총액 | KRX Open API 일별 (`stk_bydd_trd`, `ksq_bydd_trd`, `kospi_dd_trd`) | **확인됨 (2026-10-14).** 2026-09-30 요청 시 KOSPI 942행·KOSDAQ 1,824행·지수 54행 전부 `BAS_DD=20260930`. 종목 응답에 OHLCV·`MKTCAP` 포함. 당일 확정치 공개 시각은 미확인. 검사 기록: `~/Documents/Codex/2026-10-13/i/outputs/krx-response-field-check-2026-10-14.json` |
| PER·PBR | KRX 개별 종목 조회 `MDCSTAT03502` (기준 세션 하루) | **후보 확인 (2026-10-15 탐침 2회).** 응답 행마다 `TRD_DD` 존재, 114종목 모두 기준 세션과 일치, 운영 전 종목 조회(`MDCSTAT03501`, 거래일 없음)와 원 문자열·점수 114/114 일치. `verified`의 의미와 한계는 §13.1. 전환은 §13 설계 확인 후 |
| 재무제표 | DART | §2.3 조건 중 ①(접수번호–수치 연결)이 현재 코드에 없음(감사 M01) → **보완 필요** |
| Naver 분기 재무 | 공식 경로에서 제외 | 공개 시점 미확인. 화면 참고용 |
| KIS 현재가 계열 | 공식 경로에서 제외, 자동 대체 금지 | 화면용 |

공식 실행은 `MARKET_DATA_PROVIDER` 환경 변수와 무관하게 위 경로를 명시적으로 사용한다. 공식 경로 호출이 실패하면 KIS 등으로 대체하지 않고 해당 입력을 결측으로 처리한다.

---

## 3. 진입 규칙 (단일 함수)

```
CUTOFF = 08:50:00 KST  (미만이어야 당일 시가)

# 실행 시작 시 1회 고정 (signal_runs에 저장)
expected_market_session = calendar.previous_session(run_started_at의 KST 날짜)
    # run 시작일보다 엄격히 이전인 마지막 거래일. 달력이 unknown이면 None

def planned_entry(run, cohort_confirmed_at, inputs, calendar) -> (session, rule, reasons):
    D = cohort_confirmed_at의 KST 날짜
    E = run.expected_market_session
    reasons = []
    # 입력별 "이번 실행에서 확인한 시각": 시장 = fetched_at, 재무 = filing_list_fetched_at (값은 캐시 가능)
    t = lambda x: x.fetched_at if x.input_class == "market" else x.filing_list_fetched_at
    if E is None:                                   reasons.append("calendar_unknown")
    for x in inputs:
        if t(x) >= cohort_confirmed_at:             reasons.append("checked_after_confirm")
        if x.input_class == "market":
            if x.fetched_at < run.run_started_at:   reasons.append("market_fetched_before_run")  # 이전 실행의 캐시 재사용 차단
            if x.source_session_date is None:       reasons.append("market_date_missing")
            elif x.source_session_date != E:        reasons.append("market_session_mismatch")  # 오래된 캐시·장중 자료·혼합 모두 차단
            if x.source_status != "verified":       reasons.append("market_unverified")
        elif x.input_class == "financial":
            if not x.rcept_no_linked:               reasons.append("fin_link_missing")
            if x.rcept_dt is None or x.rcept_dt > x.filing_list_fetched_at.date():
                                                    reasons.append("fin_filing_date_invalid")
            if x.filing_list_fetched_at < run.run_started_at:
                                                    reasons.append("fin_version_check_stale")  # 최신 버전 확인은 이번 실행 안에서
            if not x.is_latest_version_at_fetch:    reasons.append("fin_not_latest_version")
            # 수치 자체는 rcept_no로 식별되는 불변 공시이므로 캐시 재사용 허용
    if calendar.status(D) == "unknown":             return (None, "calendar_unknown", reasons)
    if (calendar.is_session(D)
        and cohort_confirmed_at.time() < CUTOFF
        and all(t(x).date() == D and t(x).time() < CUTOFF for x in inputs)):
        return (D, "same_day_open", reasons)
    return (calendar.next_session_after(D), "next_session_open", reasons)
```

- 시각 비교는 `cohort_confirmed_at`(표본 확정 커밋)과 각 입력을 이번 실행에서 확인한 시각(시장 `fetched_at`, 재무 `filing_list_fetched_at`)으로 한다. 원천 공개 시각은 원천이 시각을 줄 때만 추가로 비교한다.
- **시장자료**는 조회일이 아니라 실행 시작 때 고정한 `expected_market_session`과 정확히 같아야 한다. 오래된 캐시(조회일 기준으로는 직전 세션처럼 보여도), 조회 당일 날짜가 찍힌 장중 자료, 서로 다른 세션 자료의 혼합이 모두 같은 검사로 걸러진다. 실행 시작 전에 조회된 값(이전 실행의 캐시)도 쓰지 않는다.
- **재무자료**는 접수번호–수치 연결, 접수일, 조회 시점 최신 버전 사용으로 판단한다. 시장 세션 규칙을 적용하지 않는다. 공시 수치는 접수번호로 식별되는 불변 자료라 캐시를 다시 써도 되지만, "그 시점의 최신 버전인가"를 판단하는 공시 목록은 이번 실행 안에서 새로 조회해야 한다.
- 08:50:00 정각 이후 확정은 다음 거래일 시가.
- `reasons`가 하나라도 있으면 공식 OOS 부적격(§5). 진입 세션 계산은 그대로 기록하되 적격으로 바뀌지 않는다.
- 08:50은 운영상 여유를 두는 마감이다. 데이터 시점의 정당성은 위의 입력 검증으로만 판단한다.
- 장중 "다음 체결가" 진입은 실제 체결 가능 가격 기록을 확보하기 전까지 도입하지 않는다.
- 진입 세션은 확정 시점에 달력으로 정하고 이후 바꾸지 않는다.

---

## 4. 저장 구조

기존 `oos_signals`와 `backtests/snapshots/*.json`은 그대로 보존한다. 공식 기록은 `database/research.db`의 새 테이블에 둔다. 화면용 스냅샷은 공식 표본에 영향을 주지 않는다.

### 4.1 `signal_runs` — 실행 1회 = 1행

| 열 | 설명 |
|---|---|
| `run_id` (PK) | UUID |
| `trigger` | `scheduled` / `manual_cli` / `ui` |
| `process_started_at`, `run_started_at` | 프로세스 시작(OS 기준)과 실행 시작 |
| `expected_market_session` | 실행 시작 시 고정한 시장자료 기준 거래일 (§3) |
| `calendar_version`, `calendar_check_id` | RunContext에서 가져온 불변 달력 버전과 당일 공지 확인 기록 ID (§7.2) |
| `cohort_confirmed_at`, `finished_at` | |
| `status` | `running` → `completed` / `incomplete` / `failed` / `aborted`. 종료 상태로 한 번만 전이 |
| `confirm_status` | `not_attempted` / `confirmed` / `skipped_existing` / `failed`. completed 실행의 표본 확정 결과 (§5.2 판정에 사용) |
| `engine_version` | 메모리에 로드된 값 |
| `source_hash_start`, `source_hash_end` | §4.6 범위의 해시. 둘이 다르면 `aborted` |
| `loaded_modules_hash` | 실행에 실제 로드된 프로젝트 모듈 파일들(`sys.modules`의 `__file__`)의 해시 |
| `git_commit`, `git_dirty` | |
| `python_executable`, `pid` | |
| `universe_rule_version`, `universe_hash`, `universe_size` | §4.2 |
| `abort_reason` | |

### 4.2 유니버스 고정과 `run_signals`

- 실행 시작 시 유니버스 선정 규칙(`universe_rule_version`, 예: `WATCHLIST_ACTIVE_REVIEW_v1`)으로 종목 목록을 만들고, 목록 전체를 `run_universe(run_id, ticker)`에 먼저 기록한다. 실행 도중 목록은 바뀌지 않는다. 워치리스트 상태 전이(`wl_save`)는 공식 실행 안에서 하지 않는다.
- `run_signals` PK `(run_id, ticker)`. 유니버스의 **모든 종목**에 대해 한 행씩, `processing_status`를 반드시 기록한다.

| `processing_status` | 의미 | 후보 여부 |
|---|---|---|
| `scored` | 정상 채점 | 전략 조건에 따라 |
| `excluded_required_missing` | 필수 결측(LOW) — 사유와 결측 필드 기록 | 제외 |
| `excluded_quick_reject` 등 | 규칙상 제외 | 제외 |
| `failed` | 예외·시간 초과 등 처리 실패 | — |

- 점수·등급·confidence·실행 가능 여부·구성 요소(JSON)와 입력별 시점 정보(§2.2 시장자료 필드, §2.3 재무자료 필드)를 함께 저장한다.
- `run_universe`와 `run_signals`의 종목 수가 같고 `failed`가 0이어야 실행이 `completed`. 하나라도 `failed`면 `incomplete` → 표본 확정 불가.
- 필수 결측 제외 비율이 임계값(§11-8)을 넘으면 `incomplete (degraded_inputs)`로 처리한다. 원천 장애로 후보가 대량 탈락한 상태를 조용히 확정하지 않기 위해서다.
- `run_signals`는 실행 종료 시 한 트랜잭션으로 기록한다.

### 4.3 `official_cohorts` — 키당 1회

| 열 | 설명 |
|---|---|
| `cohort_key` (PK) | `signal_date` + `strategy_id` + `engine_id` + `universe_rule_version` |
| `engine_id` | §4.6 공식 엔진 등록부의 (버전, 소스 해시) 식별자 |
| `source_run_id` | |
| `calendar_version`, `calendar_check_id` | 실행 기록과 일치하는 진입 판정의 달력 버전과 공지 확인 ID |
| `oos_type` | `primary` / `catchup` (§5.2) |
| `eligible` | 0/1, 확정 시점 판정, 불변 |
| `exclusion_reasons` | §3 `reasons` + §5 판정 사유 |
| `planned_entry_session`, `entry_rule` | §3 함수 결과 |
| `strategy_id` | `SB_EXEC_TOP10_v1` (§11-4) |
| `tie_break_rule` | `normalized_score DESC, ticker ASC` |
| `n_candidates`, `n_selected`, `n_tied_at_cutoff` | |
| `cohort_confirmed_at` | |

### 4.4 `cohort_members` — 불변

PK `(cohort_key, ticker)`, `rank`. `official_cohorts`와 같은 트랜잭션에서 기록.

### 4.5 `cohort_outcomes` — 성과, 채움 전용

PK `(cohort_key, ticker, window)`. 진입·청산 세션과 가격, 비용 전·후 수익률, KOSPI 수익률, `price_source`, `filled_at`, 상태:

| 상태 | 의미 |
|---|---|
| `waiting_price` | 해당 세션의 **시장 수집**이 아직 `complete`가 아님 |
| `price_wait_warning` | 진입 세션 후 5거래일이 지나도 시장 수집 미완료. 재수집은 계속, 진입일 유지 |
| `price_unavailable` | 시장 수집은 `complete`인데 이 종목 행이 없거나 가격이 NULL·0 이하 (거래정지 가능성). 대체 가격 없음 |
| `filled` | 채움 완료 |

**적격성·분류 열 없음.** 진입 세션은 `official_cohorts.planned_entry_session`을 그대로 사용한다.

저장되는 상태는 확정 결과인 `filled`·`price_unavailable`뿐이다. `waiting_price`·`price_wait_warning`은 저장하지 않고 조회할 때 계산한다(§7.4(d)). 가격은 `official_prices`에서만 읽는다(§7.4(c)).

### 4.6 실행 코드 동일성

- 공식 실행은 **매번 새 프로세스**로 시작한다(launchd 또는 CLI). `process_started_at`과 `run_started_at`의 차이가 임계값(예: 60초)을 넘으면 `aborted (stale_process)`.
- 해시 범위는 고정 목록과 실제로 로드된 프로젝트 모듈 파일의 합집합이다. 프로젝트 모듈은 패키지 이름이 아니라 프로젝트 루트 아래 경로인지로 판별한다. 고정 목록: `analyzer/`, `constants.py`, `config.py`, `run.py`, `morning_run.py`, 공식 실행 진입점, 기록 코드(`database/` 저장 모듈, `backtests/snapshot.py`, `backtests/market_db.py`).
- 시작 시 모든 공식 모듈을 import한 **뒤** `source_hash_start`와 `loaded_modules_hash`를 계산하고, 종료 직전 `source_hash_end`를 다시 계산한다.
- 해시 범위를 고정한 뒤 새로 로드된 프로젝트 파일이 범위 밖이면 `aborted (unhashed_module)`로 처리한다.
- 기준 엔진 등록 해시는 **공식 실행기와 같은 진입점·import 순서·로드된 프로젝트 모듈 상태**에서 계산한다. 같은 코드라도 import 상태가 다르면 해시가 달라질 수 있으므로, 등록과 실제 실행은 같은 초기화 경로를 사용한다.
- 공식 엔진 등록부 `official_engines(engine_id, engine_version, source_hash, git_commit, registered_at)`. 기준 엔진 확정 = 등록. 등록된 (버전, 해시)와 다르면 공식 표본을 만들지 않는다. **같은 버전 번호로 코드만 바뀐 경우**도 해시가 달라 차단된다.
- 한계: 해시는 디스크 파일 기준이다. 새 프로세스 + import 후 시작 해시 + 종료 해시 일치로 "로드된 코드 = 디스크 코드"를 실질적으로 보장하지만, 실행 중 파일이 바뀌었다가 원복된 경우까지 배제하지는 못한다.

---

## 5. 공식 표본 확정과 분류

### 5.1 확정 동작

`completed` 실행 직후 같은 프로세스가 단일 트랜잭션으로:

1. 해당 `cohort_key`가 이미 있으면 아무것도 하지 않는다.
2. 없으면 전략 규칙으로 선정 → `official_cohorts` + `cohort_members` INSERT, `cohort_confirmed_at` = 이 트랜잭션 커밋 시각.
3. §3 함수로 진입 세션·사유를 계산해 함께 기록.

**2단계에서 구현된 연결 검사:** `start_run()`은 `RunContext`, `confirm()`은 `EntryDecision`을 받는다. 확정 시각, 실행 시작 시각, 기준 세션, 달력 버전, 공지 확인 ID가 실행 기록과 일치해야 하며, 세션 여부도 판정 객체에서 가져온다.

**4단계 완료 기준 — 저장된 입력으로 판정 재계산:**

- 판정에 사용한 시장·재무 입력의 시점 및 출처 검증 정보를 §2의 구조로 `run_signals`에 저장한다. 종목 입력과 공통 입력을 식별해, 실제 사용한 입력 전체를 복원할 수 있어야 한다.
- `confirm()`은 DB의 실행 문맥, 입력, 고정된 달력 버전 및 공지 확인 기록을 읽어 `planned_entry()`를 직접 다시 계산한다. 호출자가 넘긴 사유·적격성·진입일을 계산의 근거로 사용하지 않는다.
- 넘겨받은 `EntryDecision`과 재계산 결과가 다르면 확정을 거부한다. 사유를 지우거나 진입 규칙·세션 여부를 바꾼 객체도 이 검사에서 거부되어야 한다.
- 회귀 검사: 실제 `market_unverified` 판정에서 사유만 지운 객체는 거부하고, 원래 판정은 부적격으로 기록한다. 검증 실패는 표본을 만들지 않는다.
- 현재 2단계 API는 객체와 실행 기록 사이의 일관성만 검사한다. 일반 dataclass의 값이 실제 입력에서 계산됐는지는 아직 검증하지 않으므로, 위 재계산 검사가 구현되기 전에는 공식 OOS를 시작하지 않는다. 실제 확정 커밋 완료 시각의 검증도 4단계에서 연결한다.

**4단계 구현 (2026-10-15 결정 반영):**

- **입력 기록:** `run_inputs`(실행 단위 입력, §2.2·§2.3 필드)와 `run_signal_inputs`(종목별 사용 입력 ID)를 `finish_run()`과 같은 트랜잭션에 기록한다. 둘 다 수정 불가이고 실행이 `running`일 때만 추가된다. 채점된 종목에 연결된 입력이 없거나(`inputs_unlinked`) 존재하지 않는 입력 ID를 참조하면(`inputs_invalid`) 실행은 `incomplete`.
- **판정 입력 집합:** 선정 종목이 사용한 입력의 합집합(선정 0개면 실행의 전체 입력), `input_id` 순. 실행기와 `confirm()`이 같은 함수(`decision_inputs`)를 쓴다.
- **재계산:** `confirm()`은 DB의 실행 문맥·입력·달력 버전·공지 확인으로 `planned_entry()`를 다시 계산하고, 넘겨받은 `EntryDecision`과 모든 필드가 같지 않으면 `decision_recompute_mismatch`로 거부한다.
- **마감 여유:** primary는 확정 시각이 **08:49:00 미만**일 때만이다(60초 여유). 08:49:00부터는 진입 규칙과 무관하게 catchup.
- **COMMIT 사후 확인:** 확정 트랜잭션 COMMIT 직후 시계를 다시 읽어 `commit_checks`(표본당 1행, 추가 전용)에 기록한다. 상태: `verified_before_cutoff` / `after_cutoff` / `check_failed`(시계 오류·확정 시각보다 이른 값). 성공도 기록한다. COMMIT 뒤 프로세스가 죽으면 기록이 없다.
- **예정 실행 근거:** 부모 PID 1, `XPC_SERVICE_NAME` = 등록 라벨, OS 기준 시작 시각(`ps`) 60초 이내를 모두 충족해야 `scheduled`. 확인한 값은 그대로 `signal_runs`에 저장한다(수정 불가). 미충족은 실행기가 `manual_cli`로 기록한다. 저장소는 근거가 부족한 `scheduled`를 거부한다. 시작 시각을 읽지 못하면 실행을 중단한다(실행 기록 없음). 실행 경로 확인 장치이며 완전한 증명은 아니다.
- **검사 전용 전략:** `TEST_SB_EXEC_TOP10_v1`. 전략 ID가 표본 키에 포함되므로 공식 표본 키를 선점하지 않고, 평가 조회(§5.4)에서도 제외된다.
- **원천:** 공식 실행은 `MARKET_DATA_PROVIDER`를 제거해 KIS를 끄고, KIS가 활성 상태면 중단한다. 4단계에서는 채점 입력의 원천 시점을 증명할 수 없으므로 입력을 `unverified`로 기록하고 표본은 `eligible=0`으로 확정된다.
- **진입점:** `scripts/official_signal_run.py`(장전), `scripts/official_price_collect.py`(장후, 미완료 날짜×시장만 수집 후 성과 채움). 운영 `research.db`는 저장소가 계속 거부한다.

### 5.2 분류 (`oos_type`)

`primary`는 다음을 **모두** 만족할 때만이며 예외가 없다.

- `trigger = scheduled`
- `signal_date` = 실행일(KST)이고 거래일
- 해당 키에 대해 **이전에 `completed` 실행이 하나도 없음** (그 실행의 확정이 성공했든 실패했든 무관)
- `entry_rule = same_day_open`이고 `cohort_confirmed_at` < **08:49:00** KST (60초 여유, 4단계 결정). 공식 평가에는 추가로 COMMIT 사후 확인 `verified_before_cutoff`가 필요하다(§5.4)

하나라도 아니면 `catchup`. CLI는 catchup 지정만 가능하고 primary 요청은 거부한다.

구분:
- 이전 실행이 `incomplete`/`failed`/`aborted`(분석 자체가 완료되지 않음)였다면, 마감 전 재시도가 completed·확정되면 primary가 될 수 있다.
- 이전 실행이 `completed`였는데 확정이 실패(`confirm_status = failed`)했다면, 이후 어떤 확정도 catchup이다. 분석이 끝난 결과를 보고 다시 돌리는 경로를 primary에서 배제하기 위해서다.

### 5.3 적격성 (`eligible`)

분류와 별개다. `eligible = 1`은 §3 `reasons`가 비어 있고, 엔진이 등록부와 일치하며, 실행이 `completed`일 때만.

### 5.4 평가 조회 조건 (사전등록)

```
공식 primary 평가:   strategy_id = 'SB_EXEC_TOP10_v1' AND oos_type = 'primary' AND eligible = 1
                     AND engine_id = <기준 엔진> AND universe_rule_version = <고정값>
                     AND commit_checks.status = 'verified_before_cutoff'   -- 기록 없음·after_cutoff·check_failed 제외
catchup 보조 분석:   oos_type = 'catchup' AND eligible = 1  (primary 결론에 섞지 않음)
검사 전용 전략(TEST_*)은 어떤 평가에도 포함하지 않는다.
```

구현: `SignalStore.official_primary_cohorts(engine_id, universe_rule)`.

### 5.5 공식 표본을 만들 수 없는 실행

`trigger = ui`(Streamlit Morning Run·스크리너), `incomplete`/`failed`/`aborted`, 미등록 엔진. 이들은 화면용 스냅샷만 갱신할 수 있다.

### 5.6 동시 실행과 스냅샷 파일

공식 실행은 `database/.signal_run.lock`에 배타 잠금. 실패 시 `aborted (lock_busy)`. 화면용 스냅샷 JSON은 임시 파일 작성 후 `os.replace`로 원자 교체.

---

## 6. 부분 실패·재시도

| 실패 지점 | 남는 상태 | 재실행 시 |
|---|---|---|
| 분석 도중 종료 | 실행 `running`(고아), `run_signals` 0행 | 다음 실행 시작 시 고아를 `failed (orphaned)`로 종료. 새 `run_id`로 처음부터 |
| 일부 종목 처리 실패 | 실행 `incomplete`, 표본 없음 | 새 실행으로 전체 재처리. 08:50 전에 확정되면 primary 가능 |
| 필수 결측 대량 발생 | `incomplete (degraded_inputs)` | 위와 같음 |
| 표본 확정 트랜잭션 실패 | 실행 `completed`, `confirm_status=failed`, 표본 없음 | 다음 실행이 확정하되 **시각과 무관하게 catchup** |
| 가격 일부 시장만 수집 | 해당 날짜·시장 `failed`/`partial` | 미완료분만 재수집 |
| 진입일 시장 수집 미완료 | outcome `waiting_price` | 진입일 유지, 계속 대기 (§7.3) |
| 시장 수집 완료, 종목만 가격 없음 | outcome `price_unavailable` | 그 종목만. 시장 전체 채움은 진행 |
| 성과 채움 도중 종료 | 일부 행만 반영 | 채움은 멱등, 채운 칸은 건드리지 않음 |

같은 키의 재실행은 어떤 순서로 일어나도 `official_cohorts`·`cohort_members`를 바꾸지 않는다.

---

## 7. 거래 달력과 가격 수집 (F10, F06)

### 7.1 작업 분리

| 작업 | 시각 | 하는 일 | 하지 않는 일 |
|---|---|---|---|
| 신호 실행 | 장전 (08:50 전 확정 목표) | 유니버스 고정 → 분석 → 확정 | 가격 수집, 성과 채움 |
| 가격 수집 | 18:30 → 19:30 → 21:00 → 다음 날 08:00 KST | 누락 거래일 수집(미완료 날짜·시장만) → 성과 채움 | 신호 생성 |

18:30은 최초 시도 시각이며 확정치 공개가 보장된 시각이 아니다. 완결성 검사(§7.4)를 통과해야 완료로 기록한다. 다음 날 08:00 시도도 수집만 하고 신호는 만들지 않는다.

### 7.2 거래 달력 (가격 수집과 독립)

- `trading_calendar(date, status, calendar_version, source, source_ref, checked_at)`. `status` ∈ `session` / `holiday` / `unknown`.
- 근거는 **KRX 공식 거래일·휴장 공지**. 달력은 버전으로 관리하고, 매일 장전 신호 실행 전에 변경 공지 여부를 확인해 출처와 확인 시각을 보존한다.
- 날짜별 `checked_at`은 원 출처를 처음 검토한 시각으로 보존한다. 날짜 전체를 매일 다시 입력하지 않는다.
- 별도 불변 기록 `calendar_notice_checks(check_id, calendar_version, checked_at, status, source_refs_json)`에 당일 장전 변경 공지 확인을 저장한다. `status`는 `clear` / `changes_pending` / `failed`이며, 공식 KRX 출처를 남긴다.
- 실행은 사용할 확인 ID를 고정한다. 확인 기록은 같은 달력 버전의 당일 기록이고, 실행 시작 이전 또는 같은 시각이며 08:50:00 미만, `clear`여야 한다. 기록 누락·버전 불일치·미래 또는 지난 날짜·마감 이후·검토 중·실패는 차단한다. 날짜별 원 근거도 실행 이후 시각이면 허용하지 않는다.
- 공지로 검증되지 않은 기간은 `unknown`이다. 평일이라고 `session`으로 가정하지 않는다.
- 가격 응답의 유무로 휴장을 추론하지 않는다. **"정상 응답에 지수 행이 없으면 휴장" 규칙은 사용하지 않는다.**
- 진입 세션 계산 시 필요한 날짜가 `unknown`이면 `calendar_unknown`으로 기록하고 공식 표본 부적격.
- 임시 휴장 등 달력 변경은 새 행으로 기록(이력 보존). 이미 확정된 표본의 진입 세션은 바꾸지 않고, 영향받은 표본에 사유를 추가 기록한다.

### 7.3 진입일 고정

- 진입 세션은 표본 확정 시 §3 함수로 정해지고 이후 이동하지 않는다.
- 진입 세션의 **시장 수집**이 완료되지 않았으면 `waiting_price`로 계속 기다린다. 다음 거래일로 넘기지 않는다.
- 진입 세션 후 5거래일이 지나도 미완료면 `price_wait_warning`. 재수집은 계속하며 진입일은 그대로다.
- 시장 수집이 완료됐는데 해당 종목 가격을 쓸 수 없으면 그 종목만 `price_unavailable`. 대체 가격·다른 날짜를 쓰지 않는다.

### 7.4 시장 수집 완결성과 종목별 가격 가용성 (분리)

**(a) 시장 수집 완결성** — 날짜×시장 단위. 시장 = KOSPI 종목 / KOSDAQ 종목 / KOSPI 지수.

`price_collection_status(trade_date, market, status, expected_n, got_n, response_session_date, fetched_at, error)`

`complete` 조건 (모두 충족):
- 응답 **모든 행**의 거래일 필드(`BAS_DD`)가 요청 거래일과 일치
- 종목코드 중복 없음
- 필수 필드(종가 등) 파싱 가능
- 지수 응답에 KOSPI 행 존재
- 행 수가 직전 완료 세션 대비 ±2% 이내. 이 범위는 **이상 탐지 기준**이다. 범위 안이라고 완전하다고 보지 않으며 위 조건도 모두 통과해야 한다. 범위 밖이면 `suspect`로 두고 성과 채움에 쓰지 않는다.

cohort 종목 포함 여부는 시장 완결성 조건에 넣지 않는다. 한 종목의 거래정지가 시장 전체 채움을 막지 않게 하기 위해서다.

**(b) 종목별 가격 가용성** — 시장 수집이 `complete`인 날짜에서만 판단.
- 진입가: 해당 종목 시가가 존재하고 **0보다 커야** 사용. NULL·0·음수는 `price_unavailable` (실제 응답에 시가 `"0"`인 행이 있음).
- 청산가: 종가가 존재하고 0보다 커야 사용. 아니면 그 창은 `price_unavailable`.
- KOSPI 지수 시가·종가도 같은 조건.

**(c) 저장 — 공식 가격 전용 테이블 (2026-10-14 결정)**

공식 가격은 새 테이블에 따로 저장하고, 공식 성과 채움은 **이 테이블만** 읽는다. 기존 `market_ohlcv`는 화면·연구용으로 유지하며 공식 경로에서 읽지도 쓰지도 않는다(정리는 F09).

| 테이블 | 성격 | 내용 |
|---|---|---|
| `price_collections` | 추가 전용 이력 | 수집 시도 1회 = 1행. `collection_id`, 거래일, 시장, `fetched_at`, 상태(`complete` / `partial` / `failed` / `suspect` / `conflict`), 기대·수신 행 수, 응답 `BAS_DD` 집합, 원 응답 해시, 사유 목록 |
| `official_prices` | 불변 | PK (거래일, 시장, 종목). 파싱 값(시가·고가·저가·종가·거래량·시가총액, 빈 값은 NULL), 응답 `BAS_DD`, 원 응답 행(JSON)과 해시, 최초 저장한 `collection_id` |
| `price_conflicts` | 추가 전용 | 이미 공식 가격이 있는 키에 다른 값이 들어온 경우의 기존 값·신규 값·`collection_id` |

원칙:
1. **완결성 검사(a)를 통과한 날짜×시장만** 공식 가격으로 저장한다. 그 시장의 전 종목 가격 저장과 `complete` 수집 기록은 **같은 트랜잭션**이다. 중간 실패 시 둘 다 남지 않는다.
2. 실패·부분·의심 응답은 `price_collections`에 이력으로만 남고, `official_prices`의 키를 **선점하지 않는다.**
3. 이미 `complete`인 날짜×시장을 다시 수집했을 때:
   - 종목 집합과 모든 파싱 값이 같으면 재수집을 `complete`로 기록한다(가격 행 추가 없음).
   - 하나라도 다르면 **덮어쓰지 않고** 그 수집을 `conflict`로 기록하고 차이를 `price_conflicts`에 남긴다. 기존 공식 가격은 그대로다.
4. NULL·0 이하 가격(거래정지 등)도 원 응답 근거로 **그대로 저장**한다. 정상 가격으로 쓰지 않으며 성과 채움 시 (b) 조건에 따라 `price_unavailable`로 판단한다.
5. 세 시장(KOSPI 종목·KOSDAQ 종목·KOSPI 지수)이 모두 `complete`인 날짜만 성과 채움에 쓴다.
6. 행 수 ±2% 기준이 되는 직전 완료 세션이 없는 최초 수집은 `complete`로 저장하되 사유 `no_count_baseline`을 이력에 남긴다.

**(d) 성과 저장 (`cohort_outcomes`)**
- 확정된 결과만 저장하는 **추가 전용·불변** 테이블이다: `filled` 또는 `price_unavailable`. 한 번 기록된 (표본·종목·평가창)은 바뀌지 않는다.
- `waiting_price`·`price_wait_warning`·`calendar_unknown`은 저장하지 않고, 매번 공식 가격·수집 상태·달력에서 다시 계산한다. 대기 상태를 저장했다가 고치는 갱신을 없애기 위해서다.
- 청산 세션 계산에 쓴 달력 버전을 성과 행에 함께 저장한다.

---

## 8. 성과 정의 (사전등록, 결과 확인 전 고정)

| 항목 | 정의 |
|---|---|
| 1일째 | 진입 세션 (`planned_entry_session`) |
| 진입가 | 진입 세션 시가 |
| 청산가 | N번째 거래일 종가 (진입 세션 = 1번째) |
| 주 평가창 | N = 20 |
| 보조 평가창 | N = 60, 120 |
| 탐색 지표 | N = 5, 10 (필요 시, 주·보조 결론에 쓰지 않음) |
| 벤치마크 | KOSPI 지수(1001), **같은 진입·청산 세션과 같은 가격 기준**(시가 → 종가) |
| 비용 전 수익률 | `r_gross = exit / entry − 1` |
| 비용 후 수익률 | `r_net = (1 − c_sell) × exit / ((1 + c_buy) × entry) − 1`, 기본 `c_buy = c_sell = 10bp` (왕복 20bp). **실제 비용 추정치가 아닌 고정 분석 가정**. 벤치마크에는 비용 미적용 |
| 비용 민감도 | 왕복 50bp(25/25), 100bp(50/50)도 함께 계산 |
| 초과수익 | `alpha = r_stock − r_kospi` (비용 전·후 각각) |
| 종목 평균 성과 | 선정 종목의 단순 평균 |
| cohort 성과 | 10개 슬롯 각 10%, 미선정 슬롯은 현금(수익 0). `Σ 0.1 × r_i` |
| 결측 종목 | `price_unavailable`(거래정지·가격 0 이하 등) 종목은 대체 가격 없음. 그런 종목이 있는 cohort는 성과 계산 시 `incomplete`로 표시하고 주 결과와 분리해 개수와 함께 보고 |
| 겹침 처리 | 매일 cohort가 겹치므로 단순 합산·연결해 운용 포트폴리오 수익률로 표현하지 않는다. 불확실성은 날짜 군집·보유기간을 고려한 방법(블록·군집 기반, 비중첩 표본 민감도)으로 계산 |

---

## 9. 기존 기록 처리

- `oos_signals` 200행과 `backtests/snapshots/*.json`은 수정·삭제하지 않는다. 버전 문자열도 고치지 않는다.
- `legacy_eligibility(source, signal_date, ticker, eligible, reasons, decided_at, evidence_ref)`로 판정만 관리. 재판정은 새 행(이력 보존).
- 잠정 판정: 9월 신호 전부 `eligible=0` — `engine_mismatch`, `source_time_unverified`, `run_evidence_missing`.
- `oos_signals`의 자동 채움(`fill_entry_prices`·`fill_alphas`)은 계속 중지. 필요하면 별도 `legacy_outcomes` 테이블에 §8 정의로 계산하되, 원본·적격성 판정과 섞지 않는다.
- 새 공식 표본은 기준 엔진 등록(§4.6) 이후 §5 규칙으로 처음 확정된 cohort부터 시작한다.

---

## 10. 격리 검사 항목 (구현 완료 기준)

| # | 검사 | 통과 조건 |
|---|---|---|
| T1 | 필수 결측(F01 회귀) | LOW, `excluded_required_missing`, 후보 제외 |
| T2 | 08:49:59 확정 + 전 입력 verified | `same_day_open`, primary, eligible |
| T3 | 08:50:00 확정 | `next_session_open`, catchup |
| T4 | 확정 시각 이후에 확인된 입력 | `checked_after_confirm`, eligible=0 |
| T4a | 시장자료 거래일 = 조회 당일(장중 자료) | `market_session_mismatch`, eligible=0 |
| T4b | 실행 전 캐시된 시장자료(조회일 기준으로는 직전 세션처럼 보임) | `market_fetched_before_run` 또는 `market_session_mismatch`, eligible=0 |
| T4c | 시장자료 세션 혼합(일부 E, 일부 E-1) | `market_session_mismatch`, eligible=0 |
| T4d | DART 재무 수치 캐시 + 이번 실행에서 공시 목록 조회 | 재무 사유 없음 (직전 세션 규칙 미적용) |
| T4e | DART 공시 목록을 이번 실행에서 조회하지 않음 / 정정 공시가 있는데 원본 사용 / 접수번호 연결 없음 | `fin_version_check_stale` / `fin_not_latest_version` / `fin_link_missing` |
| T5 | pykrx(응답 날짜 없음) 입력 | `market_date_missing` 또는 `market_unverified`, eligible=0, 화면 스냅샷에는 존재 |
| T6 | KIS 경로 설정 상태에서 공식 실행 | KIS 호출 0건, 공식 경로 실패 시 결측 처리 |
| T7 | 같은 키 재실행(두 번째는 Top 10이 다르게 나오도록 입력 조작) | 표본 불변, 두 번째는 실행 기록에만 |
| T8 | 10위 경계 동점, 입력 순서 뒤섞기 | `ticker ASC`로 동일 결과 |
| T9 | STRONG_BUY·실행 가능 6개 | 6개만 선정, 다른 등급으로 채우지 않음 |
| T10 | 과거 날짜 실행 / CLI primary 요청 | catchup / 거부 |
| T11 | 일부 종목 처리 실패 | `incomplete`, 표본 없음 |
| T12 | 필수 결측 종목이 유니버스의 정확히 5% / 5% 초과 | 허용 / `incomplete (degraded_inputs)` |
| T13 | 분석 도중 강제 종료 후 08:50 전 재실행 | 고아 `failed`, 새 실행이 확정, primary 가능 |
| T14 | completed 실행의 확정 실패 후 **08:50 전** 재실행 | **catchup** (시각과 무관) |
| T14b | incomplete 실행 후 08:50 전 재실행이 completed·확정 | primary |
| T15 | 동시 실행 2개 | 하나는 `aborted (lock_busy)` |
| T16 | 같은 버전 번호로 코드만 수정 | 해시 불일치 → 공식 표본 없음 |
| T17 | 실행 중 소스 파일 변경 | `source_hash_end` 불일치 → `aborted` |
| T18 | 오래된 프로세스에서 실행 | `aborted (stale_process)` |
| T19 | 휴장일 다음 진입 | 달력상 다음 세션 |
| T20 | **실제 거래일 가격 수집 실패** | **진입일 이동 없음**, `waiting_price` 유지, 재수집 후 같은 날로 채움 |
| T20b | 진입 세션 후 5거래일 경과 | `price_wait_warning`, 진입일 유지, 재수집 계속 |
| T21 | 정상 응답이지만 지수 행 없음 | 휴장 판정 안 함, `partial` |
| T22 | 응답 거래일 필드 불일치(일부 행이라도) | `failed`, 성과 채움 없음 |
| T23 | 한 시장만 수집 성공 | 그 날짜 미완료, 재시도 시 실패 시장만 |
| T23b | 행 수는 ±2% 이내지만 종목코드 중복 | `complete` 아님 |
| T23c | 행 수가 ±2% 밖 | `suspect`, 채움 없음 |
| T24 | 달력 `unknown` 날짜 / 공지로 검증 안 된 평일 | `calendar_unknown`, eligible=0 |
| T25 | 시장 수집 complete, cohort 한 종목 행 없음(거래정지) | 그 종목만 `price_unavailable`, 나머지 종목·지수는 채움 |
| T25b | 시가 NULL / `"0"` / 음수 | `price_unavailable`, 대체 없음 |
| T26 | 부적격 cohort에 가격 채움 | 성과 채워지되 `eligible`·`oos_type` 불변 |
| T27 | 성과 계산 | 종목·KOSPI 같은 세션, 비용 전·후·민감도, 슬롯 10% 현금 처리 값이 손계산과 일치 |
| T28 | 기존 `oos_signals`·스냅샷 | 해시 불변 |
| T29 | UI 경로(Streamlit) | 공식 테이블 쓰기 0건 |

---

## 11. 결정 사항

### 확정 (2026-10-14)

1. 장전 마감: 08:50:00 KST 미만에 표본 확정 완료 시 당일 시가. 정각부터 다음 거래일. 운영 여유이지 데이터 정당성 근거가 아님.
2. 공식 데이터 경로: KRX 일별 자료 명시 지정, KIS 자동 대체 금지. 요청 날짜만으로 verified 승격 금지. 공식 경로 확보 전 새 OOS 보류.
3. Naver 재무: 공개 시점 미확인 유지, 공식 입력은 DART. DART는 접수번호와 사용 수치 연결 필수.
4. 전략: `SB_EXEC_TOP10_v1` — STRONG_BUY이고 실행 가능한 종목 중 상위 10개, 동점 종목코드 오름차순, 10개 미만이면 있는 만큼만.
5. 가격 수집: 18:30 KST 최초 시도. 확정치 공개 보장 시각 아님. 완결성 검사 통과 시에만 완료, 실패 시 재시도.
6. 성과 정의: §8.
7. 기존 OOS 채움: 자동 채움 중지 유지, 필요 시 별도 legacy 성과 테이블.
8. 필수 결측 임계값: 고정된 전체 유니버스의 **5% 초과**면 `incomplete`. 필드 수가 아니라 결측 종목 수로 계산. 정확히 5%는 허용.
9. 거래 달력: KRX 공식 거래일·휴장 공지를 근거로 버전 관리. 매일 장전 변경 확인, 출처·확인 시각 보존. 검증되지 않은 기간은 `unknown`.
10. 가격 대기: 진입 세션 후 5거래일이 지나면 `price_wait_warning`. 이후에도 재수집 계속, 진입일 유지.
11. 수집 행 수: 직전 완료 세션 대비 ±2%는 이상 탐지 기준. 범위 안이라도 중복·필수 필드 검사를 통과해야 완료. cohort 종목 포함 여부는 시장 완결성 조건이 아님(§7.4).
12. 재시도: 18:30 → 19:30 → 21:00 → 다음 날 08:00 KST. 실패분만 재시도, 신호 생성 없음.

### 구현 전 선행 확인

- ~~KRX Open API 응답 행의 거래일 필드~~ → **확인됨 (2026-10-14)**: `BAS_DD` 존재, 2026-09-30 요청에 대해 세 응답 전 행 일치. 당일 확정치 공개 시각은 미확인.
- **차단:** 거래일이 명시되는 PER·PBR 공식 원천. KRX 일별 세 응답에는 PER·PBR 필드 없음. 없으면 공식 OOS를 시작할 수 없다.
- **차단:** DART 수집 경로가 접수번호와 수치를 연결해 저장하고, 실행 안에서 공시 목록을 조회해 최신 버전을 확인하도록 보완(M01).

---

## 12. 범위 밖 (별도 변경)

- 점수 정책: F15(PBR 경계), F16(과열 체계), STRONG_BUY 비율(114개 중 67개).
- F16으로 과열 차단이 작동하지 않는데 정상처럼 표시되는 부분은 표시 수정(비점수)으로 별도 처리.
- F09(시가 NULL 보완), F13·F14(Calibration 집계), 연구 재계산(F11·F12·M01~M08).


---

## 13. 5단계 — 데이터 계층의 원천 기록 (설계, 2026-10-16)

목표: 공식 실행의 채점 입력마다 "무엇을, 언제, 어떤 요청으로 받았고, 그 응답이 기준 세션의 자료라고 명시했는가"를 원 응답과 함께 남기고, 그 근거로만 `verified`를 부여한다. 점수 계산·가중치는 바꾸지 않는다. 다만 원천 교체와 대체 원천 차단은 채점 입력을 바꿀 수 있으므로 전환 전에 값·점수 비교(§13.6)를 거친다.

### 13.1 검증 범위 (두 범위를 섞지 않는다)

| 범위 | `verified`가 뜻하는 것 | 뜻하지 않는 것 |
|---|---|---|
| **시장자료** (PER·PBR·EPS·BPS·배당·시가총액·일봉 등 KRX가 거래일 기준으로 제공한 지표) | ① 이번 실행에서 조회했고 ② 응답의 종목·거래일이 요청 종목·기준 세션과 정확히 일치하며 ③ 원 응답·요청 조건·조회 시각·해시를 보존해 채점 입력과 연결했다 | 그 값이 거래일 당시 공개된 정보로만 계산됐다는 것(과거 PIT 적격성). 과거 값이 이후 정정·소급되지 않았다는 것 |
| **기초 재무의 공개 시점** (PER·PBR 산출에 쓰인 EPS·BPS의 회계 기간과 당시 공개 여부) | 별도 상태 `basis_period_unverified`로 기록. 이번 범위에서는 확인하지 않는다 | — |

결정 (2026-10-16):
- PER·PBR은 **KRX가 해당 거래일 기준으로 제공한 시장 지표**로 취급한다. 자체 계산하지 않으며, EPS·BPS를 DART 재무 수치로 확인했다고 간주하지 않는다.
- 공식 표본 적격성(§5.3)의 시장자료 조건은 위 시장자료 `verified`로 판단한다. `basis_period_unverified`는 모든 입력에 함께 기록·보고되는 공시(disclosure)이며, 이를 적격 조건으로 올리는 것은 별도 결정이다.
- 앞으로 수집하는 값은 **신호 확정 전에 실제로 확보한 응답**을 보존한다. 과거 날짜를 나중에 재조회한 값은 그 당시 이용 가능했던 증거로 대체하지 않는다(`pit_market_fundamentals`처럼 소급 수집한 표는 공식 증거가 아니다).

### 13.2 입력별 원천과 판정 규칙

| 입력 (채점 사용처) | 공식 원천 | 요청 | 응답 검증 | 상태 |
|---|---|---|---|---|
| PER·PBR·EPS·BPS·DPS·배당수익률 (밸류) | KRX `MDCSTAT03502` | 종목 ISIN, `strtDd=endDd=E`, 시장 ID | 정확히 1행, `TRD_DD`=E, 요청 ISIN의 종목 | 탐침 확인 |
| 시가총액 (필수 필드), 기준 세션 일봉 | KRX Open API `stk_bydd_trd`·`ksq_bydd_trd` (시장당 1회) | `basDd=E` | 모든 행 `BAS_DD`=E, 종목코드 중복 없음, 대상 종목 1행 | `BAS_DD` 확인, 교체 비교 필요(§13.6) |
| 변동성 일간 수익률 | KRX `MDCSTAT01701` 행별 `FLUC_RT` (§13.18) | 종목 ISIN, 기간(§13.12) | §13.12 기간 규칙 + §13.18 조건 | 원천 확정(2026-10-20), 미확인 코드·특수 상황 검사 전 적격 불가 |
| 희석률의 두 시점 상장주식수 | KRX Open API `LIST_SHRS` (`stk/ksq_bydd_trd`) | `basDd`=두 시점 | 모든 행 `BAS_DD`=요청일 | 원천 확정(2026-10-20), 기존 계산과 일치 검사 유지 |
| KOSPI 이격도 (시장 상태) | KRX `MDCSTAT00301` 원 응답 (지수 기간 조회) | 지수 코드, 기간(§13.12) | §13.12 기간 규칙 | 원천 확정(2026-10-20), 기존 계산과 일치 검사 유지 |
| 재무(실적·부채비율·적자 분기) | DART | 공시 목록 + 접수번호 지정 조회 | §2.3 세 조건 | **필수, 미구현 (§13.7)** |
| 수급·외국인 소진율 | 현재 점수 미사용(수급 0점). 공식 실행에서는 조회하지 않거나, 조회해도 채점 입력으로 연결하지 않는다 | — | — | 결정 필요 |

### 13.3 차단 규칙 (대체 원천 없음)

- 다음은 해당 입력을 **`failed`**로 기록하고 다른 원천으로 대체하지 않는다: 날짜 불일치, 종목 불일치, 행 수 ≠ 1(개별 조회)·중복 행, 빈 응답, HTTP 오류·시간 초과, 파싱 실패(숫자·`"-"`·빈 문자열 외의 값).
- 필수 입력(`market_cap`, `fundamentals`)이 `failed`인 종목은 처리 상태 `failed` → 실행 `incomplete`(§4.2). 일시 오류 재시도는 요청당 최대 3회(지수 백오프)이며, 재시도 후에도 실패하면 `failed`. 재시도 횟수와 별개로 실행 전체 마감(§13.14)이 우선한다.
- **현재 채점 경로의 대체 원천을 공식 실행에서 끈다.** `run._resolve_fundamentals`는 KRX PER이 없으면 Naver PER·ROE로 보완하거나 완전 대체한다. 공식 실행에서는 이 보완을 하지 않는다. 적자 종목의 PER은 결측으로 남는다. → 적자 종목의 밸류 점수가 바뀔 수 있으므로 §13.6 비교 대상.

### 13.4 원래 결측과 조회 실패의 구분

| 원 응답 | 상태 | 값 |
|---|---|---|
| 숫자 | `verified` | 파싱 값 |
| `"-"` (KRX 표기 결측: 적자 PER 등) | `verified`, `missing_kind=source_dash` | 없음(None). 운영 변환과 같음 |
| `""` 빈 문자열 | `verified`, `missing_kind=source_blank` | 없음 |
| 행 없음·오류·불일치 | `failed` | 없음 — 채점에 결측으로 넘기지 않고 종목 처리 실패 |

pykrx 가공 경로는 `"-"`를 `"0"`으로 바꾸므로 공식 실행은 **pykrx 래퍼가 아니라 원 응답을 직접 파싱**한다(탐침과 같은 방식).

### 13.5 원 응답 보존과 채점 입력 연결

- 새 테이블 `source_responses(response_id, run_id, endpoint, request_json, fetched_at, http_status, body_sha256, body_zlib, row_count)` — 추가 전용·불변. 원 응답 본문을 압축 저장한다.
- `run_inputs`(§5.1 4단계)에 `response_id`, `row_key`(응답 내 행 식별: ISIN·`TRD_DD` 등), `missing_kind`, `basis_status`를 추가한다. 시장자료 입력은 응답 1개와 그 안의 행 1개를 가리킨다.
- `confirm()`의 재계산은 `run_inputs`의 시점 필드로 하고, 감사 시에는 `response_id`로 원 응답을 다시 파싱해 저장된 입력 값과 일치하는지 검사할 수 있다(별도 감사 도구).
- 실행기는 데이터 계층이 돌려주는 (값, 원천 기록) 쌍을 그대로 `run_inputs`에 넘긴다. 채점 함수에 들어간 값과 기록된 값이 같은지 실행 중 대조한다.

구현 (5단계-2, 2026-10-20, 격리 시험만):
- 표: `source_responses`(본문 zlib·SHA-256·`row_count` 또는 `decode_error`), `source_response_rows`(행 번호·행 키), `run_input_responses`, `run_input_rows`. 모두 추가 전용·불변, 실행 중(`running`)에만 추가. 참조는 `(run_id, …)` 복합 외래 키라 다른 실행의 응답·없는 행을 가리킬 수 없다.
- `run_inputs` 추가 열: `value_json`(채점에 넘긴 값, 유한수 또는 null), `definition_id`, `params_json`, `missing_kind`, `basis_status`, `failure_reason`. CHECK: 결측 종류는 `verified`+값 null일 때만, 실패는 결측 종류 없이 값 null+사유.
- 정의(`analyzer/pipeline/sources.py`): `krx03502_field_v1`, `krxopen_field_v1`, `volatility_v2_krx_fluc`, `kospi_disparity_v1_krx00301`, `dilution_v1_openapi`. 수집 시 계산과 저장 시 재검증이 같은 함수를 쓴다.
- `finish_run`은 같은 트랜잭션 안에서 저장된 원 응답을 다시 해석해 상태·값·결측 종류·실패 사유·기준 상태·조회 시각·기준 세션·응답·행을 전부 대조한다. 하나라도 다르면 실행 `incomplete`(`input_evidence_mismatch`/`input_evidence_invalid`), 근거 없는 `verified` 시장 입력은 `input_evidence_missing`. 이 경우 입력은 기록하지 않는다.
- 응답 없이 끝난 조회 실패는 `request_timeout`·`request_error`·`deadline_exhausted` 사유로만 기록할 수 있다.
- `audit_input_evidence(run_id)`: 저장 후 감사(본문 해시 포함).
- 4단계 형식 DB(`run_inputs`에 근거 열 없음)는 `initialize()`가 `pipeline_schema_upgrade_required`로 거부한다. 자동 변경·삭제·재생성 없음.

### 13.6 원천 교체 전후 비교 (전환 조건)

다음 비교를 감독기 아래에서 수행하고, 차이를 그대로 보고·검토한 뒤 전환한다.
- PER·PBR: 2026-10-15 탐침 2회로 1개 세션·114종목 일치 확인(§2.5). 전환 직전 한 번 더.
- 시가총액·기준 세션 일봉: 현재 pykrx 경로 vs KRX Open API — 값, 결측, 필수 필드 판정, 리스크·밸류 점수.
- Naver 보완 차단(§13.3): 차단 전후 PER 결측 종목 수와 밸류 점수·등급 변화.
- 결과에 실패·누락을 포함하고 실제 소요 시간을 잰다.

### 13.7 DART 연결 (독립 필수 조건)

시장자료 검증이 끝나도 재무 입력은 §2.3 세 조건(접수번호–수치 연결, 접수일 ≤ 조회일, 실행 안에서 공시 목록을 조회해 최신 버전 사용)이 따로 필요하다. 이 조건이 구현·검증되기 전에는 재무 입력이 `fin_*` 사유로 부적격이며 공식 OOS를 시작하지 않는다.

### 13.8 실행 시간 판단

- 2026-10-15 탐침 관측: 개별 조회 114회 84.5초(0.5초 대기 포함). 4단계 격리 실행(기존 경로) 114종목 272초(깨어 있는 시간).
- 장전 실행 가능 여부는 **전환 후 전체 실행**(조회·재시도·채점·확정)을 감독기 아래에서 측정해 판단한다. 기준: 시작~확정이 08:49:00 여유 마감 안에 끝나고, 재시도 최악 경로(요청당 3회)를 포함한 상한도 마감 안에 들어와야 한다. 들어오지 않으면 시작 시각을 앞당기거나 실행을 `incomplete`로 끝낸다(대체 원천·부분 확정 없음).
- Mac 잠자기 대비(예약 기상 등)는 launchd 설치 결정(③-G)과 함께 정한다.

### 13.9 격리 검사 항목 (5단계 구현 완료 기준)

| # | 검사 | 통과 조건 |
|---|---|---|
| S1 | 개별 조회 응답 날짜 ≠ E / 종목 불일치 / 2행 이상 / 빈 응답 / 파싱 불가 | 입력 `failed`, 대체 원천 호출 0건 |
| S2 | `"-"` / `""` | `verified` + `missing_kind`, 값 None |
| S3 | 필수 입력 `failed` | 종목 처리 실패 → 실행 `incomplete` |
| S4 | 원 응답 보존 | 모든 시장 입력이 `response_id`로 원 응답을 가리키고, 원 응답 재파싱 값 = 채점 입력 값 |
| S5 | Naver 보완 차단 | 공식 실행에서 Naver 호출 0건 (화면 경로는 기존 유지) |
| S6 | `basis_period_unverified` | 모든 PER·PBR 입력에 기록, 적격성 판정에는 영향 없음(현 결정) |
| S7 | 재시도 | 일시 오류 2회 후 성공 시 `verified`, 3회 실패 시 `failed` |
| S8 | 원천 교체 비교 | §13.6 비교 결과 보고(차이 포함) |
| S9 | 전체 실행 시간 | 감독기 아래 측정값 기록, §13.8 기준 판정 |
| S10 | `FLUC_TP_CD` 0·빈 값 행 (§13.18) | 의미(정상 등락·거래정지·결측) 확인 완료. 확인 전에는 해당 행이 창에 있으면 변동성 입력 `failed (unverified_status_code)` — 0% 대입·행 제거 없음 |
| S11 | 특수 상황: 상장 첫날, 거래정지·재개, 정리매매, 이력 부족 | 종목 사례로 처리 규칙 검증 후 공식 입력 연결. 이력 부족은 현행 기준(세션 < max(60, N/2) 또는 행 < 20 → 결측) 유지 |
| S12 | 변동성 교체 비교 | 같은 세션·종목에서 현행 vs KRX `FLUC_RT`의 값·**리스크 점수·최종 등급** 변화 비교 보고 (전환 전 필수) |
| S13 | 희석률·이격도 일치 | 현행 계산과 기간·필드·값 일치(희석률 두 시점, 이격도 20세션) |

### 13.10 확인된 사실과 미확인 사항

- `pit_market_fundamentals`는 2020-01-02~2026-09-30 전체가 2026-10-02 하루에 소급 수집됐다. 원천 열이 없고 수집 시각은 날짜만 있다. 원천은 수집 스크립트 코드로 추정한 것이다(스크립트의 git 최초 등록은 10-06).
- 탐침 (A)의 일치(20종목, 31,779개 거래일)는 10-02 이후 약 2주의 안정성만 보여 준다. 거래일 당시 값과의 일치(소급 정정 여부)는 미확인이다.
- KRX 개별 조회에는 있고 로컬 표에는 없는 날짜 5개 (2026-10-16 확인, 005930·035900 동일): **2026-08-26(수), 08-27(목), 08-28(금), 08-31(월), 09-01(화)** — 모두 정상 거래일이다. 가격 DB(`market_ohlcv`)에 이 5일의 행이 전혀 없고(전후 08-25·09-02는 약 2,766행씩 존재), pit 표는 가격 DB의 지수 거래일 목록을 달력으로 써서 같은 날짜가 빠졌다. 원인은 **가격 수집 누락**으로 판단한다: 장전 실행이 당일 일봉을 수집하려 해 아무것도 저장하지 못하는 구조(F10)이고, 08-20~08-25는 08-25에, 09-02~09-04는 09-05에 보충됐지만 이 5일은 보충되지 않았다. 같은 기간 아침 스냅샷(08-26·27·28·09-01)은 존재한다.
- 영향(수정하지 않음, 기록만): ① 기존 `oos_signals` 성과 채움(`fill_alphas`)은 가격 DB 지수 행으로 거래 달력을 만들므로, 이 구간을 지나는 평가창의 N번째 거래일이 5일 어긋난다(기존 채움은 중지 상태). ② 가격 DB·pit 표를 쓰는 연구에서 이 구간의 수익률·거래일 수가 왜곡될 수 있다. ③ 새 공식 경로는 KRX 공지 기반 달력과 `official_prices`를 쓰므로 영향이 없다. 보충 여부와 연구 재계산 범위는 F09·연구 재계산과 함께 별도 결정한다.


### 13.11 채점 입력 전체 목록 (2026-10-16 코드 조사)

공식 경로에 필요한 필드를 채점 사용처 기준으로 전부 나열한다. "Naver 호출 0건"은 이 표의 모든 공식 원천이 Naver가 아닐 때만 성립한다.

| 필드 | 채점 사용처 | 현재 원천 (KIS 끈 경로) | 공식 원천 후보 | 구분 |
|---|---|---|---|---|
| `per`, `pbr` | 밸류 PER 점수·PBR 점수표, Quick Reject(PBR) | pykrx `MDCSTAT03501`(거래일 없음). PER 결측 시 **Naver PER로 대체**(`_resolve_fundamentals`) | KRX `MDCSTAT03502` 기준 세션 1행 | 시장·단일 |
| `div_yield` | 채점 미사용(조회만). KRX 결측 시 Naver 보완 | 같음 | `MDCSTAT03502` 같은 행(기록만) | 시장·단일 |
| `market_cap` (필수 필드) | confidence 필수 | pykrx 시가총액 일괄 | KRX Open API `MKTCAP`, `BAS_DD`=E | 시장·단일 |
| `roe` | 실적 ROE 보너스 | **Naver** 분기 재무(항상 — KRX 재무 조회에 ROE 없음) | DART (순이익·자본) | 재무 |
| `quarters_met_expectations`, `large_miss_pct` | 실적 점수 | **Naver** 분기 영업이익(2~4분기, 전년 동기 비교 대부분 불가) | DART 분기 영업이익 | 재무 |
| `quarters_with_loss` | 리스크 | **Naver** 분기 순이익(분기 결측 시 연간 값을 분기 자리에 대입 — F17) | DART 분기 순이익 | 재무 |
| `debt_ratio_pct` | 리스크 | **Naver** | DART (부채·자본) | 재무 |
| `earnings_quality_ratio` | 밸류 EQ 감점 | **Naver** 4분기 영업이익/순이익 합 | DART | 재무 |
| `volatility_pct` | 리스크 | pykrx `get_market_ohlcv_by_date` 기본 `adjusted=True` → **Naver 차트(수정주가)** 종가 252개 → 수익률 251개의 표준편차(ddof=1)×√252 | **KRX `MDCSTAT01701` 행별 `FLUC_RT`** (같은 251개 수익률, §13.18). 수정종가 요청(`adjStkPrc=2`)은 단순주가와 같은 값이 와서 기각 | 시장·기간 |
| `dilution_pct` | 리스크 | pykrx 시가총액 일괄(KRX `MDCSTAT01501`)의 `상장주식수` (기준일 vs **달력상 1년 전 날짜**를 그대로 요청 — 비거래일이어도 KRX가 돌려준 상장주식수를 사용. ~~비거래일이면 None~~은 현행 동작을 잘못 기술한 것, 2026-10-21 정정) | **KRX Open API `LIST_SHRS`** (확정). 비거래일 기준 날짜의 대체 규칙은 §13.19 탐침 후 확정 | 시장·두 시점 |
| `kospi_disparity` | 시장 과열·국면 → 실행 차단 판정 | pykrx 지수 기간 조회(KRX `MDCSTAT00301`) 20거래일 | **같은 엔드포인트 원 응답**(확정, 행마다 `TRD_DD`) | 시장·기간 |
| `interest_coverage`, `governance_flag` | 리스크 | 항상 None / False | 해당 없음 | — |
| `supply_flow`, `foreign_ownership` | **채점 미사용** (수급 0점) | pykrx | **공식 실행에서 조회하지 않음** (2026-10-16 확정, §13.15) | — |

계산 정의가 원천 교체로 바뀌는 항목(별도 검토 대상):
- 변동성: **원천과 일간 수익률 구성 방식의 교체**(2026-10-20 분류) — Naver 수정종가의 수익률 → KRX가 보고한 등락률. 가중치·임계값 변경은 아니다. 조건은 §13.18.
- 희석률: 현행 동작은 "달력상 1년 전 날짜를 그대로 KRX에 요청"이며 비거래일에도 값이 나온다(2026-10-21 실제 응답 탐침에서 확인, 정정). 후보 규칙은 §13.19.
- 재무 전 항목: Naver 분기 값 → DART(누적 보고서의 분기 환산, 정정 공시 처리). 분기 수·환산 방식이 달라진다.

### 13.12 기간 자료의 검증 규칙 (단일 행 규칙과 별도)

"응답 거래일 = 기준 세션 E" 규칙은 단일 행 입력에만 적용한다. 기간 자료(변동성 일봉, 이격도 지수, 희석률의 두 시점)는 다음을 모두 만족해야 `verified`.

1. **요청 범위 고정:** 실행 달력에서 E와 필요한 관측 수 N으로 시작 세션을 계산해 요청한다. 변동성 N=252 세션(E 포함) → 수익률 251개(현행과 같음, §13.18). 이격도 N=20 세션. 요청 조건을 기록한다.
2. **마지막 세션 = E:** 응답의 마지막 `TRD_DD`가 E.
3. **날짜 집합 일치:** 응답 날짜 집합 = 실행 달력의 해당 구간 세션 집합. 중복 0, 누락 0, 달력에 없는 날짜 0. 달력이 그 구간에서 `unknown`이면 판정 불가 → `failed`(사유 `calendar_unknown`). 따라서 공식 달력은 최소 1년+여유 구간을 공지로 검증해 보유해야 한다.
4. **관측 수:** 계산에 실제 쓰인 관측 수 ≥ N(신규 상장 등으로 부족하면 `failed`가 아니라 값 결측 `missing_kind=insufficient_history`로 기록하고 채점 규칙대로 처리 — 현재 채점과 같은 의미).
5. **수익률 기준 명시:** 변동성은 행별 `FLUC_RT`만 쓴다(종가 수익률과 섞지 않는다). 요청 파라미터(`adjStkPrc`)는 기록만 한다 — 2026-10-16 탐침에서 1·2 응답이 같았다.
6. **근거 연결:** 계산 결과 입력(`run_inputs`)은 사용한 **모든** 원 응답(`response_id` 목록)과 계산 정의 ID(예: `volatility_v2_krx_fluc: FLUC_RT 251개 std(ddof=1) ×√252`, 현행은 `volatility_v1_naver_adj_close`)를 가리킨다.

희석률의 "1년 전 기준 세션"은 실행 달력으로 결정하고, 그 세션의 Open API 응답(`BAS_DD`)을 별도로 보존한다.

### 13.13 Naver 제거 범위

- 공식 실행은 §13.11의 모든 필드를 공식 원천에서만 받는다. 제거 대상: `_resolve_fundamentals`의 PER·ROE·배당 보완/대체 전부, `_auto_earnings`, `_auto_risk`의 Naver 재무, pykrx 수정주가(Naver 차트) 경로.
- 판정 기준: 공식 실행 중 HTTP 호스트 계측에서 `*.naver.com` 요청 0건 **그리고** §13.11 표의 모든 필드가 공식 원천의 `response_id`를 가짐. 둘 중 하나만으로 판정하지 않는다.
- 재무 필드는 DART 경로(§13.7)가 준비될 때까지 `failed`/결측으로 남는다. 이때 표본은 부적격이다. Naver 값을 임시로 쓰지 않는다.

### 13.14 실행 전체 마감 (요청 재시도와 별개)

- 실행 시작 시 `run_deadline`을 고정한다: 공식 실행은 `08:49:00 − 확정 예비 시간(기본 60초)`. 검사 실행은 감독기 제한보다 짧게 설정.
- **새 요청을 보내기 전** 남은 시간이 요청 최대 소요(연결 10초 + 읽기 60초 + 여유)보다 작으면 요청하지 않는다. 재시도도 같은 검사를 거친다.
- 마감에 걸려 받지 못한 입력은 `failed (deadline_exhausted)` → 해당 종목 처리 실패 → 실행 `incomplete`. 부분 확정·대체 원천 없음.
- 기록: 마감 시각, 중단 시점, 남은 요청 수.

### 13.15 채점 미사용 입력의 표기

- 공식 실행은 `supply_flow`·`foreign_ownership`을 조회하지 않는다.
- 이를 조회 실패(`FetchErrorReason`)로 넣지 않는다. 검증 단계에 `not_requested`(채점 미사용)로 전달하고, confidence 판정(`fetch_errors`)에 포함하지 않는다. 조회 실패와 구분되는 별도 상태로 기록한다.

### 13.16 5개 날짜 기록 (2026-10-16)

- 로컬 수집 누락으로 **확인**: 2026-08-26·27·28·31·09-01은 정상 거래일이며 가격 DB·pit 표에 행이 없다.
- 누락을 만든 정확한 실행 원인은 **추정**으로 남긴다(장전 당일 수집 구조 F10, 보충 실행 누락).
- 기존 성과의 날짜 어긋남은 일괄 판정하지 않는다. 평가창(진입~N번째 거래일)에 포함된 누락 세션 수로 개별 판단한다(누락 0이면 영향 없음).

### 13.17 5단계 구현 순서 (2026-10-16 확정)

1. 과거 일봉 원천 조사와 입력 목록 확정(수정주가·희석률 산식·이격도 포함). 계산 정의가 달라지면 별도 검토. — **원천 선정 완료(2026-10-20, §13.18)**. 미확인 코드·특수 상황 검사(S10·S11)는 공식 적격성 허용 전 완료 조건으로 남는다.
2. `source_responses`·원 응답 파서·입력 연결(단일 행·기간 자료) — 값과 근거 일치 격리 검사.
3. PER·PBR, 시가총액·일봉·지수 경로 구현 — 기존 경로와 비교, 운영 전환 없음.
4. DART 연결과 Naver 제거를 함께 검사 — 정상·적자·결측 종목의 필드별 값·점수 변화 비교.
5. 전체 격리 실행과 감독기 검사(재시도·마감 중단 포함) → 전환 여부 결정.

범위 밖: 운영 전환, 기존 DB 보충, 연구 재계산.

### 13.18 기간 자료 원천 결정 (2026-10-20)

설계·격리 구현용 결정이다. 운영 전환 승인이 아니다.

**변동성 → KRX `MDCSTAT01701` 행별 `FLUC_RT`**
- 분류: 원천과 일간 수익률 구성 방식의 교체. 점수 가중치·임계값 변경 아님. 수정종가 수익률과 반올림된 등락률(소수 2자리)이 모든 상황에서 같다고 입증된 것은 아니므로 전환 전 값·점수·등급 비교(S12)는 필수.
- 관측 수 고정: 현행 코드(`krx._calc_annualized_volatility`)는 E까지 252세션의 종가 → 수익률 251개, `std(ddof=1)×√252`. 교체 후에도 **같은 251개 수익률**(창의 두 번째~마지막 세션의 `FLUC_RT`)을 쓴다. 252개 수익률로 늘리는 것은 별도 변경. 탐침의 253개 종가·252개 수익률은 조사용이었다.
- 근거 (2026-10-16 탐침, 기준 세션 2026-10-15, 114종목, 감독기 PASS):
  - 2026-09-14 이전 29,383 종목·일에서 `FLUC_RT`와 Naver 수정종가 일간 수익률 차이 0건(허용 0.011%p). 같은 구간 200일 변동성 차이 최대 0.0096%p.
  - 기준가 조정일 4건(484870 06-26 75,600→25,200, 340570 08-06 65,600→33,200, 035890 05-04 1,623→3,200, 000640 03-11 107,700→104,600)에서도 일치. 그날 `LIST_SHRS`는 바뀌지 않아 주식수로는 이벤트를 식별할 수 없다.
  - `adjStkPrc=2`·`adjStkPrc_check=Y` 요청 종가가 단순주가(1)와 6종목 전부 같았다 → KRX 수정종가 경로 기각.
  - 2026-09-14부터 Naver 종가가 KRX 종가와 어긋난다(09-11 114종목 일치 → 09-14 110종목 불일치, 이후 2,280건 중 270건만 같음, 비율 약 0.99~1.01). 원인 **미확인**(Naver 기준 변경 가설). 현행 운영 변동성은 이 날 이후 KRX 정규장 종가와 다른 가격으로 계산되고 있을 수 있다.
- 시점 한계: 이번 표본에서 수정종가 수익률과 일치했다는 사실만 기록한다. 과거 `FLUC_RT`가 이후 정정되지 않는다는 근거는 없다. 원 응답을 보존(§13.5)해 정정 여부를 나중에 대조할 수 있게 한다.
- 미확인 상태 코드: `FLUC_TP_CD`가 0·빈 값인 182행(같은 탐침)의 의미를 확인하기 전에는 0%로 바꾸거나 제거하지 않는다(S10).
- 특수 상황: 상장 첫날, 거래정지·재개, 정리매매, 이력 부족의 처리 규칙을 검증한 뒤 공식 입력으로 연결한다(S11).
- S10·S11은 **공식 적격성 허용 전 완료 조건**이다. 원 응답 저장·파서·입력 연결 구현은 먼저 진행할 수 있다.

**희석률 → KRX Open API `LIST_SHRS`** (`stk_bydd_trd`·`ksq_bydd_trd`, `BAS_DD`)
- 근거: 2026-10-15 기준 114종목, E일 상장주식수와 희석률 모두 현행과 일치(1년 전 자료 없는 2종목은 양쪽 None). 모든 행 `BAS_DD`=요청일.
- 1년 전 날짜가 비거래일인 경우는 표본에 없었다(2025-10-15는 세션). 2026-10-21 탐침(E=10-19, 1년 전 2025-10-19 일요일)에서 현행은 값이 나오고 새 경로(`dilution_v1_openapi`, 비거래일이면 None)는 10종목 전부 값 없음 → 현행 기술 오류. §13.19.

**KOSPI 이격도 → KRX `MDCSTAT00301` 원 응답**
- 근거: 행마다 `TRD_DD`, 마지막 = E, 중복 0, 원 응답 계산값 = 현행 함수 값(−16.8986).
- 기존 계산에 필요한 기간(20세션)·필드(`CLSPRC_IDX`)·값 일치를 검사 조건으로 유지(S13).

### 13.19 출처 검증 강화와 희석률 기준 날짜 (2026-10-21)

**PER·PBR 등 03502 입력: `krx03502_field_v2` (출처 검증 강화 — 산식 변경 아님)**
- 03502 응답 행에는 종목 코드·ISIN 필드가 없다(2026-10-21 실제 응답, 10종목). 따라서 다음 세 연결을 모두 필수로 한다.
  1. 요청 종목 → 보존된 종목 검색 응답(`finder_stkisu`)의 ISIN·시장 = 03502 요청의 ISIN·시장
  2. 03502 응답 거래일 = 기준 세션
  3. 03502 종가 = 같은 세션·같은 종목의 Open API 종가
- 하나라도 불일치하거나 대조 근거가 없으면 `verified`가 아니다(`isin_link_missing`·`isin_link_mismatch`·`close_check_missing`·`close_mismatch`). 종가 일치만으로 종목 식별이 증명되지는 않으므로 요청·ISIN 연결을 함께 요구한다.
- 입력은 세 응답의 사용 행(03502 행·검색 행·Open API 행)을 모두 가리킨다.
- 같은 문제(행에 종목 필드 없음)가 01701(변동성)에도 있다 → **`volatility_v3_krx_fluc`** (2026-10-21 승인): v2 산식 그대로 + 종목 검색 → 01701 요청 ISIN 연결(01701 요청에는 시장이 없으므로 ISIN만) + 기준 세션 행 종가 = Open API 종가. 기간 전체의 날짜·중복·상태 코드 검사는 별도로 유지. 입력은 252행 + 검색 행 + Open API 행을 가리킨다.

**조회 실패의 기록 (수집기)**
- 입력에 필요한 조회 중 하나라도 응답 없이 실패하면 그 조회 실패 사유로 기록하고 응답을 연결하지 않는다. 받은 응답은 보존된다.

**희석률 1년 전 기준 날짜**
- 정정: 설계의 "비거래일이면 None"은 현행 동작의 잘못된 기술이었다.
- 후보 규칙: **"1년 전 기준 날짜 이하의 마지막 검증된 거래 세션"**. 기준 날짜 자체가 거래일이면 그날. 달력이 그 구간에서 `unknown`이면 앞 날짜를 임의로 고르지 않는다(`calendar_unknown`).
- 후속 탐침(10종목, E=2026-10-19, R=2025-10-19 일요일, P=2025-10-17, 감독기 PASS, 원 응답·해시 보존): 휴일 KRX 01501 상장주식수 = P의 KRX 값 = P의 Open API 값 10/10, 현행 희석률 = 후보 희석률 10/10, 휴일 Open API 응답은 두 시장 0행. 484870은 양쪽 모두 |값|>50% 제외로 None.
- **채택 (2026-10-21): `dilution_v2_openapi_prev_session`** — 위 후보 규칙을 새 경로의 명시적 규칙으로 쓴다. 비거래일 날짜의 응답은 쓰지 않는다(요청 불일치로 실패). 한계: 이번 표본(휴일 1개)에서 현행과 일치했을 뿐, 모든 휴일의 같은 동작을 입증하지 않았다. 공식 구현은 탐침의 지수 달력이 아니라 승인된 거래 달력(§7)을 쓴다 — 달력이 1년+여유 구간을 검증해 보유해야 한다(§13.12).
- 원천 결측 종류 `reference_not_session`은 더 이상 만들어지지 않는다(정의에서 제거, 스키마 CHECK 목록에는 남아 있음).

**유지**
- 035890 등 미확인 상태 코드 차단(S10) 유지.
- 운영 전환과 실행 시작 시각 결정은 전체 실행 측정(§13.8·S9) 뒤로.

### 13.20 변동성 특수 상황 규칙 — `volatility_v4_krx_fluc` (2026-10-21 승인, 격리 구현)

설계·격리 구현용 결정이다. 운영 전환 승인이 아니다. 원칙: 현행 경로(Naver 수정종가 `pct_change().dropna()`, 종목 행 < 20이면 None, 지수 세션 < max(60, N/2)이면 None)의 동작을 재현한다.

**조사 근거 (S10·S11)**
- 01501(전 시장 일별) 260세션·748,730행에는 코드 1·2·3만 관찰. 정지로 보이는 날도 01501은 코드 3(거래량 0) — 01501과 01701의 코드 체계가 다르다.
- 01701 사례 32종목·6,085행 (+ 이전 3종목): 코드 0 2,189행 전부 등락률·전일대비·거래량 0, 코드 4 50행 전부 양수·종가=고가, 코드 5 12행 전부 음수·종가=저가, 빈 코드 0행. 01501과 등락률 차이 0건(코드만 다름).
- 공시 대조: 동성화인텍 매매거래정지 2025-10-30 ~ 해제 11-18 = 01701 코드 0 구간 10-30~11-17, 재개일 코드 2·−14.33%. 명인제약 2025-10-01 신규상장 기준가격 58,000원 → 첫날 +110.17%는 기준가격 대비. 세원이앤씨 정리매매 10-16~10-24, 상장폐지 10-27 = 마지막 행.
- 한계: 공시로 사례를 확인한 것이지 **공식 코드 사전을 확보한 것은 아니다**. 코드 4·5는 상·하한가와 일관된 관측, 빈 코드는 미확인.

**규칙**
| 상황 | 처리 |
|---|---|
| 코드 1·2·3 | 등락률 부호가 각각 +·−·0 |
| 코드 0 | 등락률·전일대비·거래량이 모두 0일 때만 허용, KRX가 준 0%를 그대로 포함(현행 재현) |
| 코드 4·5 | 양수·종가=고가 / 음수·종가=저가일 때만 허용 |
| 빈 코드·그 밖의 코드·조건 불일치 | 실패(`unverified_status_code`·`status_code_inconsistent`) |
| 재개일 | KRX가 반환한 등락률 그대로. 항상 정지 전 종가 대비라고 가정하지 않는다. 재개와 기준가 조정이 겹치는 경우는 비교 대상으로 남긴다 |
| 앞부분 연속 누락 | 허용. **신규상장의 증거로 해석하지 않는다**. E 행 필수 |
| 중간·끝(E) 누락, 중복, 달력 밖 날짜 | 실패 |
| 관측 수 | 가용 행 N개 → 첫 행을 뺀 N−1개 등락률, `ddof=1`, ×√252. 첫 행 등락률(신규상장 기준가격 대비 등)은 쓰지 않는다 — 현행 `pct_change`와 같은 결과 |
| N < 20 | 값 없음 `definition_null`, `detail.reason=insufficient_history` |
| 지수 세션 최소 요건 | 승인 달력에서 E로 끝나는 252세션이 있어야 함 — **현행 max(60,126)보다 엄격한 입력 완결성 조건 변경**(아래 별도 기록) |
| 정리매매 | 특별 처리 없음(KRX 값 그대로). 01701 코드로는 식별 불가 |

- 모든 변동성 입력은 `detail`(관측 수, 전체/앞부분 누락, 첫 행 날짜, 코드별 행 수, 값이 없을 때 사유)을 `run_inputs.detail_json`에 기록하고, 저장 시 재계산과 대조한다. 이 열이 없는 이전 시험 DB는 `pipeline_schema_upgrade_required`로 거부된다.
- 출처 연결(검색 → ISIN, E 종가 = Open API 종가)은 v3와 같다.

**입력 완결성 조건 변경 (별도 기록, 2026-10-21)**
- 현행: 지수 거래일이 max(60, 126)개 이상이면 계산. 새 경로: 승인 달력이 E로 끝나는 252세션 구간 전체를 `session/holiday`로 확정하고 있어야 계산(하나라도 `unknown`이면 `calendar_unknown` 실패).
- 산식·관측 수 규칙 변경이 아니라 달력 근거 요구의 강화다. 비교(S12)에서는 "값 차이"와 "이 조건 때문에 계산하지 못한 경우"를 구분해 보고한다. 탐침 달력에서는 이 조건이 행사되지 않으므로, 실제 영향은 승인 달력 구축 후 확인한다.

**별도 항목으로 남김 (이번 범위 밖)**
- 장기 정지로 0% 관측이 많아지면, 변동성이 거래정지·유동성 위험을 충분히 나타내지 못할 수 있다(예: 035890은 252세션 중 147세션 코드 0). 현행 재현을 유지하고 별도 연구 항목으로 둔다(Evidence-Gated).
- E일 정지·정리매매 종목의 선정 제외는 별도 적격성·진입 정책. 성과 측정의 `price_unavailable`은 선정 단계의 거래 가능성을 보장하지 않는다.
- 전환 전 비교(S12)는 값과 함께 리스크 점수·최종 등급 변화까지 포함한다.

**S12 변동성 단독 비교 결과 (2026-10-22, E=2026-10-21, 114종목, 감독기 PASS)**
- 다른 채점 입력은 같은 조회 결과로 고정하고 변동성만 현행 → v4로 교체(격리 HOME). 114종목 전부 비교 가능, 조회 실패 0, 새 경로 근거 감사 통과.
- 관측 수 차이 0(전 종목 252), 앞부분 누락 0, 코드 0 포함 종목 2(033500 13행, 035890 138행 — 0% 그대로 포함).
- 변동성 차이 |Δ|: 중앙값 0.18%p, 90% 0.65%p, 최대 2.16%p.
- 리스크 점수 변화 1종목: 101160 변동성 60.10 → 59.77로 60% 기준선 아래 → 변동성 감점(−3) 해제, 종합 76 → 79, 등급 buy 유지. 최종 등급·실행 가능 여부 변화 0.
- 한계: 기준 세션 1개. 등급 변화 0은 이 날짜의 결과일 뿐이며, 60% 기준선 근처(이 날 ±1%p 안 6종목)에서는 작은 차이로 감점이 바뀔 수 있다. 승인 달력 252세션 조건은 행사되지 않았다.
- 실행 시간: v4 수집(01701 1년 범위 114건 + 검색·Open API) 837초 ≈ 종목당 7.3초 — 장전 실행 시간 판단(§13.8)에 반영. 1차 시도(2026-10-21)는 핫스팟 단절로 FAIL_TIMEOUT, 기록 보존.

### 13.21 DART 연결 — 결정과 1단계 구현 (2026-10-22, 격리 구현·비교시험 범위)

**정정 (§13.7 기술)**: `fnlttSinglAcntAll`은 접수번호를 지정하는 조회가 아니다(회사·사업연도·보고서 코드·연결/별도로 요청, 응답 행에 접수번호). 따라서 "접수번호 지정 조회"가 아니라 **공시 목록에서 사용할 보고서를 고르고, 재무 응답 행의 접수번호가 그 보고서와 일치하는지 검증**한다. 불일치면 공식 입력으로 쓰지 않는다. 과거 특정 공시 버전을 조회할 수 있다고 보장하지 않는다.

**기존 코드(`analyzer/data/dart.py`)를 공식 입력에 그대로 쓰지 않는 이유 (조사 2026-10-22)**
- 저장 재무 파일 389개·83,898행 모두 `rcept_no`가 있으나, 추출 함수 반환값에 빠진다(수치–근거 연결 없음).
- 캐시 키가 회사·연도·보고서 코드뿐이라 정정공시를 구분하지 못한다. 공시 목록은 첫 100건만 읽는다.
- 사업연도·분기를 접수월로 추정한다. 계정은 계정명 첫 일치로 고른다. ROE = 분기 순이익 ÷ 자본(연환산·지배주주 구분 없음).

**결정**
1. 실적 점수: 1차는 **흑자 분기 수 대용치 유지**. 원자료는 수집·보존하되 채점에는 최근 4개 분기만 명시적으로 넘긴다(8분기 확보로 `run._auto_earnings`의 전년 동기 비교 분기가 자동으로 켜지지 않게). 계산 방식 ID 기록. 분기 순서·중복·연속성 확인, 연간 값을 분기 자리에 넣지 않음. 전년 동기 비교·대형 실적 감소 감점 활성화는 별도 비교시험. "계산 방식 유지"와 "결과 동일"은 다르다 — 실제 분기 자료 복구로 점수는 달라질 수 있다.
2. ROE 비교시험용 정의: **최근 4분기 지배주주 순이익 합 ÷ 기초·기말 평균 지배주주 지분 × 100**. 기초 = 4분기 기간 시작 직전 분기말. 순이익·지분 결측 또는 기초·기말 지분 ≤ 0이면 값 없음(전체 순이익·전체 자본으로 대체하지 않음). 운영 보너스 반영은 별도 결정.
   - 적자 분기·영업이익/순이익 비율은 동일 연결 범위의 **전체 순이익**.
3. 재무 응답 재사용: §2.3("수치는 캐시 가능, 최신 공시 목록은 이번 실행에서 조회")에 따른다. 조건 — 회사·기간·보고서 코드·연결/별도·접수번호로 식별, 원 응답·최초 조회 시각·해시 보존, 공시 목록은 매 실행 새로 조회해 최신 버전 확인, 새 정정·접수번호 불일치면 재사용 금지, 같은 접수번호로 다른 응답을 받으면 덮어쓰지 않고 충돌로 기록. OpenDART 호출 한도는 고정 가정하지 않는다(계정별 상이 가능).
4. 보고 기간: 보고서명의 기준 연월 `(YYYY.MM)`. **12월 결산만 지원** — 사업보고서가 12월이 아니거나 분기·반기 월이 3·6·9가 아니면 `fiscal_year_unsupported`로 차단(연월만으로 확정하지 않음).

**1단계 구현 — 공시 선택·접수번호 연결 (`analyzer/pipeline/dart_select.py`, 순수 함수)**
- 목록 요청: 정기공시(`pblntf_ty=A`), 정정 포함 전 버전(`last_reprt_at=N`), 100건씩 쪽 단위. 인증키는 전송 계층(`DartTransport`)에서만 붙고 기록되는 요청에 포함되지 않는다.
- `select_periodic`: 쪽 1..total_page 전부·같은 조건, 건수 = total_count, 접수번호 중복 없음, 행의 회사 고유번호·종목코드 일치, 접수일 ≤ 확인일, 결산월 검사 → 기간별 최신 버전(접수일·접수번호 순) 선택. 과거 보고서의 정정이 최근 분기 선택에 영향 주지 않는다.
- `verify_fnltt`: 요청 = (회사, 사업연도, 보고서 코드, 연결/별도), 응답이 비면 `statement_missing`, 모든 행의 접수번호 = 선택 공시(`rcept_no_mismatch`), 회사·연도·보고서 코드 일치.
- OpenDART 상태 코드: `000` 정상, `013` 데이터 없음 → 행 0개(정상 응답), 그 밖(예: `020` 요청 한도 초과)은 해석 실패로 보존.
- 다음 단계: 실제 응답 탐침(목록 형식·정정 꼬리표·쪽 정보·응답 접수번호 일치·분기 금액 의미 확인) → 분기 자료 구성(계정 ID·재무제표 구분, 연결/별도 일관, 4분기 = 연간 − 3분기 누적) → 대용치 연결 → ROE 별도 비교.

### 13.22 DART 분기 표·채점 입력 정의 (2026-10-22, 격리 구현)

**실제 응답 근거 (runs13·14 재무 응답 280개)**
- 손익: CIS에만 있는 회사 227, IS·CIS 둘 다 53(순이익 52쌍 전부 같음). 영업이익 행은 응답마다 1행(`dart_OperatingIncomeLoss` 또는 `ifrs-full_ProfitLossFromOperatingActivities`).
- 자본변동표(SCE)에 자본·순이익 ID가 반복 → 재무상태는 BS에서만 읽는다. 통화는 전부 KRW.
- 반기보고서 `thstrm_amount` ≠ 누적(61/61) → 3개월 금액. 사업보고서 누적 금액은 빈 값(130/130).
- 1분기 + 2분기 = 반기 누적: 25쌍 중 22쌍 일치, 3쌍은 1원·809원·100만원 차이 — **원인 미확인 차이**로 기록(반올림으로 확정하지 않음). 분기 값은 각 보고서 자체 값을 쓰며 이 합계 일치를 강제하지 않는다.
- 정정: 최신 `[기재정정]` 214기간·`[첨부추가]` 6기간은 재무 응답 접수번호와 일치, `[첨부정정]` 10기간은 원본 번호 반환. 첨부정정이 재무 수치를 바꾸지 않았다는 증거는 없으므로 불일치 차단 유지(예외 허용은 별도 검증).

**분기 표 (`dart_quarters.build_quarters`)**
- 최근 분기부터 연속 4분기 + ROE 기초용 창 직전 분기말 재무상태. 분기 누락이면 실패, 기초 보고서 없으면 빈 값과 사유 기록.
- 1~3분기 = 3개월 금액(1분기는 누적과 같아야 함), 4분기 = 사업보고서 연간 − 3분기 누적(두 보고서 근거).
- 계정 ID·재무제표 구분으로 식별, 같은 표 안 중복이면 실패. IS 우선, CIS에도 있으면 값 일치 확인. 지배주주 순이익은 `ProfitLossAttributableToOwnersOfParent`만(포괄손익 계정 제외). 계정 없으면 값 없음(대체 없음).
- 연결/별도: 필요한 보고서 전부 연결이면 연결, 전부 별도면 별도, 섞이면 실패. **전환 전에 관련 응답의 검증 오류를 모두 확인** — 하나라도 접수번호 불일치 등이면 다른 구분으로 넘어가지 않는다(2026-10-22 결함 수정). 손익·재무상태표 모두 통화 검사(KRW만).

**신선도 (`dart_inputs.freshness`, 공식 적격성 차단)**
- 확인일(공시 목록 최신 조회일) 전에 제출기한이 지난 가장 최근 보고기간보다 오래된 자료만 있으면 `financial_stale`. 기한 당일은 미제출로 보지 않고 다음 날부터 적용.
- 기한 표 `dart_deadlines_v1_2026-10-22`: 사업보고서 90일, 분·반기 45일(최초 연결 등 60일 예외). 회사별 예외 확인 근거가 없으므로 분·반기 45~60일 구간에서 해당 기간이 없으면 `filing_deadline_unknown`(차단). 45·90일을 일률 적용하지 않는다.
- 새 보고서가 접수됐으나 검증 실패면 옛 분기로 돌아가지 않고 실패 유지(최신 기간을 항상 사용).

**채점 입력 정의 (현행 계산 재현, 최근 4분기만)**
| 정의 ID | 계산 ID | 현행 대응 |
|---|---|---|
| `dart_earnings_proxy_v1` | `positive_op_quarters_4q_v1` | `_auto_earnings` 히스토리 부족 분기: OP>0 비율×4 반올림(half-up), 최대 4. 8분기가 있어도 전년 동기 비교로 바꾸지 않음 |
| `dart_loss_quarters_v1` | `loss_quarters_4q_v1` | 4분기 전체 순이익 < 0 개수 |
| `dart_debt_ratio_v1` | `debt_ratio_latest_bs_v1` | 최근 분기말 부채/자본×100, 자본 ≤ 0이면 값 없음 |
| `dart_eq_ratio_v1` | `op_over_ni_4q_v1` | 4분기 OP 합/NI 합, 둘 다 자료 있고 NI 합 > 0일 때만 |
- 응답 묶음 = 공시 목록 전 쪽 + 필요한 재무 응답. 입력은 사용한 목록 행·재무 행 전부를 가리키고, 상세에 재무제표 구분·최신 기간·확인일·기한 표 ID·기간별 접수번호·값 없음 사유를 남긴다. 저장 시 같은 함수로 재검증.
- 이 입력들은 아직 `MarketInput` 경로(근거 재검증)로 기록된다. 진입 판정의 재무 적격성(`FinancialInput`의 접수번호·접수일·최신 버전)과의 연결은 5단계-5에서 정한다.

**S12 재무 단독 비교 결과 (2026-10-22, E=2026-10-21, 114종목, 감독기 PASS)**
- 다른 입력 고정, 재무 네 필드만 현행(Naver) → DART. DART 요청 693건(전송 실패 0), 근거 감사 통과.
- 정상 비교군 109종목: 실적 점수 1, 리스크 점수 0, 가치 점수 5(영업이익/순이익 비율 감점 — PER·PBR 원천 효과 아님), 종합점수 6, 최종 등급 1, 실행 가능 여부 0 변화.
  - 041190: 흑자 분기 대용치 3 → 2(실적 19 → 14, 종합 87 → 82, STRONG_BUY → BUY). 현행 Naver는 영업이익 3분기만 제공(2/3 흑자 → 3), DART는 4분기(창 2025Q3~2026Q2, 2025Q3 −9.4억 포함, 2/4 → 2) — 분기 자료 복구 효과. (2025Q2 보고서는 ROE 기초 재무상태용)
  - 부채비율은 값이 모두 달랐으나 105종목은 소수 둘째 자리에서 같음, 부채 감점 변화 없음.
- 제외 5종목(009450·023160·025540·033500·215200): 모두 최신 `[첨부정정]사업보고서 (2025.12)`의 접수번호 불일치로 DART 입력 실패. 실패 입력을 빈 값으로 넣은 계산은 교체 효과로 해석하지 않는다(요약의 "점수 11·등급 2"는 이 실패군 포함 수치).
- 영향: 2025년 사업보고서가 실제 사용 입력의 의존 보고서 목록에 남는 동안 유니버스의 약 4%가 공식 적격성에서 빠진다(종료 시점은 발표일이 아니라 의존 목록에서 빠지는 시점 — 4분기 차감·ROE 기초 등). 첨부정정 예외 검증의 우선순위가 높다.
- 범위 밖: ROE 변경, Naver PER 보완 차단, 공식 재무 적격성 연결은 이 비교에 포함되지 않았다.

**ROE (별도 비교시험, 운영 반영 보류)**
- 연결: 최근 4분기 지배주주 순이익 합 ÷ 기초·기말 평균 지배주주 지분 × 100.
- 별도: 최근 4분기 전체 순이익 합 ÷ 기초·기말 평균 전체 자본 × 100 — **보고기업 단위 ROE를 명시적으로 허용**(별도 재무에도 자회사 투자가 있을 수 있으므로 "자회사가 없다"로 해석하지 않는다). 재무제표 구분과 계산 정의를 저장하고 두 기준을 섞지 않는다. 별도의 전체 순이익을 `ni_ctrl`에 넣지 않는다.

### 13.23 DART 재무 입력의 공식 적격성 연결 (2026-10-22, 격리 구현 진행 중)

**의존 보고서 = 실제 사용 보고서**
- 네 입력(대용치·적자 분기·부채비율·영업이익/순이익)은 창 4분기 보고서(+4분기 차감용 3분기 보고서)만 의존한다. ROE 기초 재무상태용 창 직전 분기 보고서는 **ROE 입력에서만** 의존 목록에 들어간다(`include_start`). 쓰지 않는 보고서의 검증 실패로 다른 입력을 막지 않는다.
- 차단 종료 시점은 해당 보고서가 의존 목록에서 빠지는 시점이다(공시 발표일로 고정하지 않음).

**입력별 재검증**
- 여러 보고서를 쓰는 입력(4분기 차감 등)은 저장 시 모든 보고서의 응답·접수번호·사용 행을 다시 검증한다. 최신 보고서 번호 하나로 대표하지 않는다(그래서 단일 접수번호 필드의 `FinancialInput` 대신 다중 근거 입력 기록을 쓴다).

**진입 판정**
- DART 재무 입력(`definition_id`가 `dart_`)이 `verified`가 아니면 판정 사유 `fin_<실패 사유>`(예: `fin_rcept_no_mismatch`, `fin_financial_stale`, `fin_filing_deadline_unknown`, `fin_request_timeout`)로 공식 적격성을 막는다. 정의상 값 없음(`definition_null`, 검증됨)은 차단 사유가 아니다.
- 시험: 선정 종목(점수 95, strong_buy)의 재무 입력이 접수번호 불일치면 실행 기록은 완결되지만 확정 표본은 `eligible=0`, 사유 `fin_rcept_no_mismatch`.

**결정 B (2026-10-22): 재무 입력 실패 종목 = 처리 실패** — 2026-10-23 개정(§13.29: 공시 검증 실패는 제외, 실행 오류만 처리 실패)
- DART 재무 입력 중 하나라도 `verified`가 아니면 그 종목은 `processing_status=failed`(점수·등급 없음, 사유 `financial_input_failed: fin_<사유>`) → 실행 `incomplete`(`processing_failed`). 5% 제외율에 섞지 않고, 빈 값으로 채점하지 않는다. 실행기는 `official.apply_financial_status`로 표시한다.
- `finish_run`은 실패한 재무 입력에 연결된 종목이 `failed`가 아니면 `financial_status_inconsistent`로 실행을 `incomplete` 처리한다(실행기 결함 방어).
- 검사 순서: 연결 무결성(`inputs_invalid`) → 근거 재검증(`input_evidence_*`) → 재무 실패 표시 일관성 → 처리 실패 → 제외율 → 미연결. 처리 실패 종목이 있어도 입력 근거는 검증된 것만 기록된다.
- 진입 판정의 `fin_<사유>` 차단은 이중 방어로 유지한다.
- 현재 영향: 첨부정정 5종목 때문에 공식 실행은 매번 `incomplete`가 된다 — 첨부정정 검증이 끝나거나 2025년 사업보고서가 의존 목록에서 빠질 때까지.

**첨부정정 원문 관찰 (2026-10-22, (b) 1단계, 보존본 runs16)**
- 원문 확보: 재무 API가 가리킨 원본 10/10, 첨부정정본 4/10(나머지 6건 OpenDART `014` 파일 없음).
- 표지의 정정 내용은 사례마다 다르다: 내부회계관리제도 보고서 누락 보완(013030·025540), 제40기 감사보고서 첨부를 제41기로 교체(033500, 연결·별도), 감사보고서 '보고기간 후 사건' 주석 수정(086280). → 꼬리표만으로 예외를 두지 않는다.
- 구조: 확보한 첨부정정본 4건의 zip에는 **첨부 문서(감사보고서·연결감사보고서 `_00760`/`_00761`)만 있고 사업보고서 본문 xml이 없다**. 원본 zip에는 본문 + 첨부가 있다. 재무 API가 원본 번호를 돌려주는 것과 일관된 관찰이나, 첨부된 감사보고서의 재무제표 수치가 본문(재무 API) 수치와 같은지는 아직 대조하지 않았다.

### 13.24 첨부정정 원문 대조 (a) — 확보 4건 (2026-10-22, 조사, 차단 유지)

방법(`stage5/attach_compare.py`, 보존본만 사용): 원 사업보고서 본문 ↔ 재무 API ↔ 최신 첨부 감사보고서(첨부정정본에 해당 감사보고서가 없으면 원본의 것). 원본 ZIP의 옛 감사보고서는 변경 확인용. 연결/별도는 재무 API 구분(4건 모두 연결)으로 고정, 재무제표 표 문맥(직전 제목·기간·단위 표), 정규화 계정명 정확 일치, 숫자 없는 소제목 행 제외, 손익계산서·포괄손익계산서가 따로 있으면 손익계산서, 지배주주 순이익은 '당기순이익 귀속' 구간에서만, 기간 열은 머리행 열 위치(COLSPAN 반영) 또는 머리 표 기간 순서, 단위는 '(단위 : …)'. 일치 = |문서값×단위 − API| < 단위/2. 모호·미발견은 확인 불가. 문서명·표 번호·행·열·원 문자열을 기록(`runs16/…/attach_compare.json`).

| 사례 | 판정 | 내용 |
|---|---|---|
| 033500 (2025) | **예외 후보** | 영업이익·순이익·지배주주 순이익·자본·지배주주 지분·부채 6계정 모두 본문 = 재무 API = 최신 첨부(제41기). 옛 첨부는 제40·39기 열만 있어 제41기 수치 없음 — "과거 기수 감사보고서를 잘못 첨부"한 정정과 일치 |
| 025540 (2025) | **불일치(차단)** | 순이익: 본문·API 106,155,718천원, 연결감사보고서 106,155,719천원(1천원 차이). 첨부정정은 별도 감사보고서만 교체했고 연결감사보고서는 원본 그대로 — 차이는 원 공시 안의 본문·감사보고서 간 차이로, 첨부정정 때문이 아님. 원인(반올림 등)은 미확인 |
| 013030 (2023) | 확인 불가 | 감사보고서에서 지배주주 순이익 미발견, 본문 재무상태표가 두 표(58·299)로 모호. 찾은 계정은 모두 일치 |
| 086280 (2024) | 확인 불가 | 본문 영업이익이 두 표(68·586)로 모호, 감사보고서에서 지배주주 순이익 미발견. 찾은 계정은 모두 일치 |

- 결론: 후보 1건(033500)은 그 사례에 한해 예외 후보로 올린다. `[첨부정정]` 전체로 일반화하지 않는다. 예외 허용 여부와 방식(사례별 승인 목록 등)은 별도 결정.
- 미확보 6건(000120·009450·016380·023160·215200·316140)은 DART 웹 뷰어 조사(`dart_viewer_probe.py`, 근거 확보 전용)로 원문을 확보한 뒤 같은 방법으로 대조한다.

**미확보 6건 추가 대조 (2026-10-22, runs17)**
- 웹 뷰어 재실행(파서 결함 2건 수정 후): 6건·7문서·22구간 전부 확보, 예상 구간 = 수신 구간. 표지의 정정 대상은 영업보고서(016380 채권자 보유주식, 316140 파일 교체, 000120 영업·자기주식보고서 파일 오류), 내부회계관리제도 보고서 첨부(215200·009450), 정관 조항(023160) — 재무제표 수치 아님.
- 첨부정정본 문서 구간 어디에도 채점 계정명(영업이익·당기순이익·자본총계·부채총계·지배기업 등)이 없음 → 최신 감사보고서 = 원본의 감사보고서로 두고 본문 ↔ 재무 API ↔ 감사보고서 대조(`attach_compare_all.json`).

| 사례 | 판정 | 비고 |
|---|---|---|
| 016380 (2023) | 예외 후보 | 6계정 일치 |
| 215200 (2025) | 예외 후보 | 6계정 일치 (현재 차단 5종목 중 하나) |
| 316140 (2024) | 예외 후보 | 6계정 일치 |
| 000120 (2024) | 확인 불가 | 지배주주 순이익: 본문 미발견, 감사보고서 두 행 모호 |
| 009450 (2025) | 확인 불가 | 감사보고서 손익 미발견·본문 재무상태표 미발견, 재무 API 지배주주 순이익 행 없음/모호 (현재 차단) |
| 023160 (2025) | 확인 불가 | 본문 순이익·지배주주 순이익 미발견 (현재 차단) |

**10건 종합**: 예외 후보 4(033500·016380·215200·316140), 불일치 1(025540), 확인 불가 5(000120·009450·013030·023160·086280). 현재 차단 5종목 기준: 후보 2(033500·215200), 불일치 1(025540), 확인 불가 2(009450·023160). 차단은 유지하며, 예외 허용 여부·방식은 별도 결정.

### 13.25 첨부정정 사례별 승인 목록 (2026-10-22, 격리 구현 — 운영 적용 보류)

**구현 (`analyzer/pipeline/dart_exceptions.py`)**
- 목록 버전 `attach_exceptions_v1_2026-10-22`. 항목 = (회사, 사업연도, 보고서 코드, 연결/별도, 목록의 최신 [첨부정정] 접수번호, 재무 API가 돌려주는 원본 접수번호) + 검증 계정별 (재무제표 구분, 계정 ID, 원 금액) + 대조 회계기간(제 N 기)·표시 단위 + 확인 기록(이미지·다른 계정명·누락 첨부) + 근거 파일 SHA-256.
- 적용: `verify_fnltt`가 `rcept_no_mismatch`일 때만 목록을 본다. 공시 목록은 매 실행 새로 조회하고, 이번 실행의 최신 버전 번호가 항목과 같아야 한다(이후 정정 추가 시 자동 차단). 재무 응답의 접수번호·회사·연도·보고서 코드·구분이 항목과 같아야 하며, 검증 계정 금액이 하나라도 다르면 `exception_evidence_changed`(차단). 재무 응답은 원본 접수번호 그대로 보존되고, 입력 상세에 `exceptions: {version, used}`가 기록된다.
- 시험: 허용·기록, 이후 정정(최신 번호 변경)·금액 변경·구분 불일치·응답 번호 불일치·회사 불일치 시 차단. 근거 해시는 검증 폴더 시험(`test_dart_exceptions_evidence.py`)으로 보존본과 대조.

**목록 v1 = 033500 한 건.** 215200은 수치 대조는 후보이나 **첨부정정 문서(내부회계관리제도 보고서)에 스캔 이미지 15개(고유 8개)** 가 있어 이미지 안의 수치 여부를 확인하지 못했다 → 사용자 기준("이미지 가능성 확인이 부족하면 목록에 넣지 않음")에 따라 제외, 차단 유지.

**대조 파서 v2 (`attach_compare.py`, v1 보존: `attach_compare_v1.py`) — 기준 유지, 탐색 정확도만 보강**
- 번호 머리표 `XⅡ.` 같은 혼합 표기 정규화, 표 제목은 직전 제목 표 칸 글자(+앞 글의 마지막 문장 뒤 꼬리)로만 판단(앞 단락의 '요약' 문구 오염 방지), "주석은 본 … 일부입니다"·"별첨 주석 참조(침조)" 꼬리 제거, 지배주주 순이익은 귀속 구간의 지배 행 또는 이름에 '순이익'이 명시된 지배 행(포괄·주당 제외). 모호·미발견은 그대로 확인 불가.
- v2 결과(`runs17/…/attach_compare_all_v2.json`): 후보 5(033500·016380·215200·316140·000120), 불일치 1(025540, 1천원), 확인 불가 4.
  - 009450: 5계정 일치, 지배주주 순이익은 재무 API가 `ifrs-full_ProfitLossAttributableToOrdinaryEquityHoldersOfParentEntity`(다른 XBRL 요소)로 제공 → 현재 정의 ID 밖이라 확인 불가.
  - 023160: 5계정 일치, 연결감사보고서 손익표에 당기순이익 귀속 행이 없음(총포괄 귀속만) → 지배주주 순이익 확인 불가.
  - 013030·086280: 본문 표 중복(모호)으로 확인 불가.
- 첨부정정 문서 이미지 수(웹 뷰어 확보본): 016380 10, 215200 15, 009450 11, 000120·316140·023160 0.

**입력별 검증 범위 (2026-10-22 승인, 목록 v2 `attach_exceptions_v2_2026-10-22`)**
- 항목에 허용 입력 정의 ID(`inputs`)를 둔다. 예외는 (입력 정의 ∈ inputs) 그리고 (그 입력이 쓰는 계정 ⊆ 항목의 검증 계정)일 때만 적용, 범위 밖 입력에는 적용하지 않는다.
- 입력별 사용 계정(`dart_inputs.DEFINITION_ACCOUNTS`): 대용치 = 영업이익, 적자 분기 = 전체 순이익, 부채비율 = 자본·부채, 영업이익/순이익 = 영업이익·전체 순이익. 향후 ROE는 선택한 정의의 지배주주 순이익과 기초·기말 지분(기초 보고서 포함)까지 검증해야 한다.
- 예외 기록: 입력 상세 `exceptions = {version, used, definition, accounts}`. 응답의 원본 접수번호는 응답 근거에 그대로 남는다.
- 033500 항목: 6계정 검증, 허용 입력 = 현재 네 입력. 009450·023160은 이 방식의 허용 후보(네 입력 계정 일치) — 이미지·첨부 누락 확인 통과 후 목록에 넣는다.

**지배주주 순이익 ID (2026-10-22 결정)**: `ifrs-full_ProfitLossAttributableToOrdinaryEquityHoldersOfParentEntity`(보통주주 귀속 순이익)는 기존 지배주주 순이익의 대체 ID로 추가하지 않는다. 현재 ROE 정의에서는 값 없음. 보통주주 귀속 순이익을 쓰려면 분모 지분 범위와 함께 별도 정의·비교 대상으로 둔다.

**이미지 확인 (진행)**: 215200·009450 첨부정정 문서의 고유 이미지(각 8개)를 원본으로 보존하고 문서·구간·주소·해시를 연결해 직접 확인한다(`viewer_image_probe.py`). 판독 불가 부분은 확인 불가. 재무 수치가 있으면 기존 대조 기준 적용.

**목록 v3 (`attach_exceptions_v3_2026-10-22`, 2026-10-22)**: 033500(6계정 검증) + **215200·009450(네 입력 계정 영업이익·전체 순이익·자본·부채만 검증)**, 세 항목 모두 허용 입력 = 현재 네 입력, ROE 미적용.
- 215200·009450 이미지 확인(runs18): 고유 이미지 각 8개 원본 보존(문서·구간·주소·SHA-256 연결, 모두 해당 공시 문서 번호) — 내부회계 감사·운영실태·평가 보고서와 통제 활동·회의 내역, 채점 재무 계정 금액·재무제표 표 없음, 판독 불가 부분 없음(사용자 확인 + 일부 이미지 재확인).
- 실제 보존 응답 재계산(runs15 시험 DB): 033500·215200·009450의 네 입력 모두 `verified`, 입력 상세에 사용 항목 기록. 025540(불일치)·023160(미등록)은 `rcept_no_mismatch` 유지.
- 023160: 네 입력 계정 일치, 첨부정정 문서 이미지 0개·누락 첨부 없음 — 목록 추가는 승인 대기.

**목록 v4 (`attach_exceptions_v4_2026-10-22`, 2026-10-22 승인)**: v3 + **023160(네 입력 계정만 검증, 허용 입력 = 네 입력, ROE 미적용)**. 025540은 원인 미확인 불일치로 차단 유지 — 새 오차 허용 기준을 만들지 않는다. 결정 B에 따라 025540 때문에 실행은 미완료로 남는다(예상된 결과).
- 보존 응답 재계산(runs15 시험 DB): 목록 네 종목 모두 네 입력 `verified`·예외 사용 기록. 같은 종목의 `dart_roe_v1`은 예외 미적용으로 `rcept_no_mismatch`(요구대로). 025540은 전 입력 실패.

### 13.26 ROE 비교시험 정의 `dart_roe_v1` (2026-10-22, 격리 구현 — 운영 보너스 반영 보류)
- 계산 방식 `roe_4q_avg_equity_v1`: 연결이면 최근 4분기 지배주주 순이익 합 ÷ 기초·기말 평균 지배주주 지분 × 100(`consolidated_controlling`), 별도면 전체 순이익 합 ÷ 평균 전체 자본(`separate_reporting_entity`, 명시적 허용). 기초 = 창 직전 분기말 재무상태(그 보고서까지 의존, `INCLUDE_START`).
- 값 하나라도 없거나 기초·기말 지분 ≤ 0이면 값 없음(`definition_null`). 다른 계정으로 대체하지 않음(보통주주 귀속 순이익 ID 포함 안 함).
- 첨부정정 예외 범위 밖(`DEFINITION_ACCOUNTS` = 순이익·지배주주 순이익·자본·지배주주 지분; 승인 항목의 `inputs`에 없음).
- 보존 응답 재계산(runs15, 기준 세션 2026-10-21, 114종목): 검증 98 / 값 없음 11 / 실패 5(`rcept_no_mismatch`). 구분 연결 107 · 별도 2.
- 비교시험(`s12_roe_probe.py`, `run_s12roe.sh` → runs19): DART 쪽은 보존 응답 재계산(추가 DART 요청 없음), 현행 경로는 격리 HOME·감독기. 같은 조회 결과로 A(현행) / B(`Fundamentals.roe`만 DART 값) 채점 → ROE 구간(<5% 패널티·10/15/20% 보너스)·실적 점수·종합 점수·등급·실행 가능 여부 변화. 분류 comparable / dart_no_value / dart_failed / current_none / current_fundamentals_missing / current_fetch_failed / probe_error.
- **결과 (runs19/20261022_155834, PASS)**: 114종목 완료, 현행 조회 실패·탐침 오류 0, 약 353초, 보호 파일 8,552개 일치.
  - 비교 가능 98: ROE 구간·실적 점수·종합 점수·등급·실행 가능 여부 변화 0. ROE 차이 중앙값 0.0032%p, 최대 1.494%p(088350, 8.78 → 7.286, 같은 구간).
  - DART 값 없음 11: B(ROE 없음)에서 8종목 점수 하락, 등급·실행 가능 여부 변화 0. 원인 — 지배주주 순이익 계정 없음 9(4분기 전부 6: 003690·028670·029780·031330·036830·322000 / 일부 분기 3: 015750·171090·340570), 지배주주 지분 계정 없음 1(001060), 기초 재무상태 보고서 없음 1(317450). 검증 실패와 구분해 `definition_null`로 기록됨.
  - DART 실패 5: 모두 `rcept_no_mismatch`. 점수 비교 해석에서 제외.
  - 한계: 한 세션 결과. 등급 변화 0을 다른 날짜로 일반화하지 않는다. 운영 보너스 반영은 보류.

### 13.27 Naver PER 보완 차단 비교 (§13.6, 2026-10-22)
- `s12_per_probe.py`(runs20/20261022_162517, 기준 세션 2026-10-21, PASS): 같은 KRX 조회 결과로 A(현행 `_resolve_fundamentals`) / B(PER만 KRX 원 값, 보완 없음) 채점. 114종목 완료, 조회 실패·탐침 오류 0, 약 370초, 보호 파일 8,552개 일치.
- 114종목 모두 KRX PER 있음 → Naver 보완 대상 0, 밸류·종합 점수·등급·실행 가능 여부 변화 0.
- 한계: 이번 세션에 PER 결측 종목이 없어 차단이 실제로 영향을 주는 경우는 **미확인**. B의 PER은 pykrx 03501 값이며 03502 직접 재검증이 아니다(§2.5 일치 근거). 03502 값은 전체 격리 실행에서 확인.

### 13.28 공식 입력 경로 통합 연결 (5단계-5, 2026-10-22 승인 — 격리 전용)

**승인 범위**: 명시적으로 선택했을 때만 새 경로(`SignalDeps.official_inputs`). 기본 경로·CLI(`scripts/official_signal_run.py`)는 그대로. 운영 DB 적용·엔진 등록·예약 실행 설치 제외. ROE는 `dart_roe_v1`(값 없음 = ROE 없음, 검증 실패 = 결정 B로 종목 실패, ROE 예외 없음) — 격리 비교용이며 운영 반영 승인 아님. 시험 달력은 지수 거래일 기반이며 그 결과는 공식 OOS 적격성을 인정하지 않는다.

**구현**
- `analyzer/pipeline/official_inputs.py`: 실행 1회에 검색·Open API(E, 1년 전 세션)·00301 공통 수집 → 종목마다 03502(PER·PBR·배당)·01701(변동성)·DART(목록 전 쪽 + 정의별 필요 보고서, ROE만 기초 보고서 포함) → 정의로 입력 13개(종목 12 + 이격도 1) → 모든 입력이 verified인 종목만 기존 `validate → score → decide_execution`. 실패 사유 `input_failed: 이름:사유` / `financial_input_failed: fin_사유`. 이격도 실패면 전 종목 실패(대체 없음).
- 요청을 만들 수 없는 입력(ISIN 연결 없음·달력 미확정·회사 코드 없음)은 정의 ID 없이 실패 사유만 기록(재파싱할 응답이 없음).
- 시가총액 0 → `invalid_zero_value`(현행과 같음), **현행의 전일 시가총액 대체는 하지 않음**. 시가총액·거래대금 원천 결측 → 필수 결측(LOW).
- 시장 상태: 검증된 이격도 입력으로 `score_market`·`_regime_from_heat`(현행과 같은 규칙, pykrx 조회 없음). 이격도 정의에 `detail = {last_close, mean_close}` 추가(값 산식 불변, 재검증 대상).
- `official.run_signal`: `official_inputs`가 있으면 그 결과(신호·입력·연결)를 `finish_run`에 넘김 — 저장 시 원 응답 재검증·결정 B 검사는 기존 그대로. 연결은 실패 종목 포함 전 종목.
- 공유 코드 변경(기존 경로 동작 불변): `validate`의 수급·외국인에 `None` = not_requested(§13.15, fetch_errors 제외), `market_regime`의 `krx` import를 첫 사용 시로 지연(공식 경로가 pykrx 로그인을 만들지 않게), `DataSource.KRX` 추가.
- 시험 `tests/test_official_inputs.py` 6개: 전 구간 완료·감사 통과, ROE만 실패 → 종목 실패(다른 재무 입력 verified), 시장 입력 실패 → 종목 실패, 이격도 실패 → 전 종목 실패, pykrx·run 미로드(별도 프로세스), not_requested는 HIGH 유지. 전체 364 통과.

**전체 격리 실행 1차 (runs21, 2026-10-22)**: 109종목 채점, 예상 차단 5(025540 재무, 033500·215200·009450·023160 ROE만), 새 실패·네트워크 실패 0, 근거 감사 정상, Naver 요청·금지 모듈 0, 약 15분 39초. 감독기 `FAIL_PROTECTED` — 실행기의 `git status`가 `.git/index` 갱신.

**보완 (2026-10-23 승인)**
- Git 조회(`official._git`, 실행기) 모두 `GIT_OPTIONAL_LOCKS=0`. `.git/index`는 보호 대상 유지. 시험: 임시 저장소에서 mtime만 바뀐 파일이 있어도 조회 전후 index 해시·mtime 불변(환경 변수 없이는 index가 바뀌는 것 대조 확인).
- 달력 구분 `calendar_classifications`(test/official, 불변). 구분 없음·test → `calendar_not_official`. 보조 방어로 `iso-test-` 버전·시험 출처 주소 표시도 검사. `official`은 승인 근거 필수, 시험 표시 달력은 official 기록 불가. 판정은 `planned_entry` 안의 `calendar_official_reasons` 하나 — `confirm()` 재계산도 같은 함수(저장된 구분으로 재계산하므로 판정만 official로 꾸며 넘기면 `decision_recompute_mismatch`). 기존 달력은 공식으로 간주하지 않으며, 이번에 공식 달력을 승인하지 않았다.
- 실행기는 시험 달력을 `test`로 기록하고, 저장된 달력으로 판정 사유에 `calendar_not_official`이 있는지 확인한다.

**전체 격리 실행 2차 (runs21/20261023_094039, 기준 세션 2026-10-22) — 감독기 PASS**
- 보호 파일 8,556개 일치(`.git/index` 포함), 자식 정상 종료, 감독기 신호 0.
- 109종목 채점, 예상 실패 5(025540 재무 5입력, 033500·215200·009450·023160 ROE만 `rcept_no_mismatch`), 새 실패·네트워크 실패 0. 실행 `incomplete`(결정 B), 공식 표본 0.
- 요청 927건 전부 첫 시도 HTTP 200(재시도 0) — DART 693, KRX 웹 234, Open API 4. 근거 감사 정상(응답 927개 해시 일치). Naver 요청·금지 모듈 0.
- 시험 달력: 저장 구분 `test`, 판정 사유 `calendar_not_official`.
- 035890 변동성 verified(코드 0 137행 포함, 51.7).
- 시간: 실행 1,007.6초(수집 991.9초, 채점 0.0초), 프로세스 전체 1,025초 ≈ 17분 5초(1차 15분 39초). 재시도 없는 경로의 측정이며 최악 재시도 경로는 미측정 — 마감 도달 시 새 요청을 멈추고 `deadline_exhausted` → incomplete.
- 결론: 격리 통합 검증 통과. 운영 가능 상태 아님(남은 결정: 재무 차단 정책, 공식 달력 승인, 실행 시각·여유, 운영 연결).

**확인된 사실 (보완 전)**: 입력이 모두 verified면 현 판정(`planned_entry`)은 `eligible=1`을 준다. 시험 달력·S10/S11·공식 달력 공지 검증 여부는 판정 함수에 없고, 지금은 시험 전략 키·격리 DB로만 구분된다. 운영 전환 전 별도 결정 필요.

**정정**: 이전 보고의 "035890 미확인 상태 코드 차단 예상"은 틀렸다(§13.19 '유지' 문구는 v4 이전). v4 규칙에서 035890은 코드 0 138행이 조건을 만족해 verified(runs12, 값 51.69). 전체 실행에서 실패하면 행·위반 조건을 기록한다.

**격리 실행기**: `stage5/official_run.py`·`run_official.sh`(→ runs21). 감독기, 격리 HOME·DB, 시험 전략, HTTP 호스트 계측(Naver 0건), 금지 모듈(pykrx·run·현행 krx/naver) 미로드 확인, 예상 실패(025540 재무, 033500·215200·009450·023160 ROE만)와 새 실패·네트워크 실패 분리, 변동성 코드 실패 행 기록. 가짜 전송으로 실행기 전 구간 점검(PASS, 코드 실패 행 기록 경로 확인).

### 13.29 결정 B 개정 — 데이터 검증 제외 (`validation_exclusion_v1_2026-10-23`, 2026-10-23 승인 — 격리 구현)

배경: 결정 B(재무 입력 실패 = 처리 실패 = 실행 incomplete)에서는 원인 미확인 불일치 1종목(025540)이 매일 실행을 막아 공식 관찰이 무기한 0개가 된다. 반대로 워치리스트에서 지우면 누락이 숨겨진다. → 원래 유니버스에 남기고 제외 사실을 기록한다.

**규칙**
| 경우 | 처리 상태 | 실행 |
|---|---|---|
| 재무 입력이 공시 검증 사유로만 실패(`dart_inputs.FIN_EXCLUSION_REASONS`: 접수번호 불일치·보고서 식별 불일치·재무제표 없음·예외 근거 변경·연결/별도 불가·통화·계정 모호·IS/CIS 불일치·1분기 누적 불일치·분기 누락·정기보고서 없음·결산월 미지원·신선도 미달·제출기한 판단 불가) | `excluded_validation` — 점수·등급 없음, 원인·사용 공시(응답 연결) 보존 | 계속 |
| 필수 결측(기존) | `excluded_required_missing` | 계속 |
| 제외 상한 | 필수 결측 + 검증 제외 ≤ 유니버스의 5% (정확히 5% 허용, 넘으면 `degraded_inputs`) | — |
| 통신 실패·요청 한도 초과(DART 020 등 → `parse_failed`)·응답 형식·페이지 불일치·회사 코드 없음 등 위 목록 밖 사유 | `failed` | `incomplete` (`processing_failed`) |
| 근거 저장 오류·재검증 불일치 | — | `incomplete` (`input_evidence_*`) |
| 시장 입력 검증 실패(종가 대조 불일치·미확인 코드 등) | `failed` (이번 개정 범위 밖, 기존 유지) | `incomplete` |
| 표시 불일치(실행 오류를 제외로, 제외에 점수, 재무 실패를 채점으로) | — | `incomplete` (`financial_status_inconsistent`) |

- 판정 함수: `official.apply_financial_status`(종목 상태 부여)와 `SignalStore._financial_status_problem`(저장 시 일관성 검사)이 같은 사유 목록을 쓴다.
- 선정: `scored`만 후보(기존 규칙 그대로). 제외 종목 자리는 같은 규칙의 다음 후보가 채운다.
- 기록: 확정 시 같은 트랜잭션에서 `cohort_scope`(정책 ID, 범위 `validated_subset`, 실행 전 고정 유니버스 크기·해시, 상태별 수, 제외 비율)와 `cohort_exclusions`(종목·상태·사유) — 불변. 과거 코호트는 소급 변경하지 않는다(새 표는 개정 이후 확정분만).
- 표본 범위: 결과는 "고정 유니버스 전체"가 아니라 **데이터 검증을 통과한 종목에 대한 전략 성과**다. 선정 편향은 남으며, 평가·보고에서 이 범위를 명시한다.
- 구 스키마 DB(`run_signals`에 `excluded_validation` 없음)는 `pipeline_schema_upgrade_required`로 거부(자동 변경 없음).

**ROE**: 새 버전의 입력 정의로 `dart_roe_v1` 채택 방향(검증된 `definition_null` = 값 없음, 검증 실패 = 위 제외 규칙). 한 세션 비교(§13.26)는 수익성 근거가 아니라 교체 영향 확인이었다. ROE 첨부정정 예외 추가 조사는 하지 않는다.

**시험** (`tests/test_validation_exclusion.py` 7개 + 기존 시험 갱신, 전체 380 통과): 1/20(정확히 5%) 완료·확정, 제외 종목 미선정·다음 후보 충원, 범위·제외 기록과 비율 0.05, 제외 종목 실패 근거 보존·감사 통과 / 2/20 → `degraded_inputs` / 필수 결측 1 + 검증 제외 1 합산 → `degraded_inputs` / 통신 실패·DART 020 → `failed`·`processing_failed` / 시장 입력 검증 실패 → `failed` / 실행 오류를 제외로 표시 → `financial_status_inconsistent` / 구 스키마 거부.

**격리 실행기 기대값 변경**: 114종목에서 예상 5종목은 `excluded_validation`(4.4%) → 실행 `completed`·확정(시험 달력이라 `eligible=0`), 범위 기록 1행·제외 5행, 제외 종목은 선정 안 됨.

승인되지 않은 것: 운영 DB 적용·엔진 등록·예약 실행. 다음: 공식 달력 절차·시작 시각.

**전체 격리 실행 3차 (runs21/20261023_101038, 개정 정책) — 감독기 PASS**: 109 채점·5 검증 제외(4.386%), 실행 `completed`, 10종목 표본 확정, 범위 1행·제외 5행, 제외 종목 선정 0, 시험 달력 `eligible=0`, 보호 파일 8,558개 일치, 새 실패 0.

### 13.30 공식 달력 절차·실행 시각·아침 관찰 (2026-10-23 결정 — 운영 전환 보류)

**순서**: 원천 탐침(S13) → 달력 절차 확정 → 아침 5거래일 격리 관찰 → 운영 판단. 예약 기상·실행 설치는 별도 설치 범위.

**달력 절차 (초안, 탐침 후 확정)**
- 원천: KRX 휴장일 목록(open.krx.co.kr MKD01100305) 원 응답·해시 보존, 규정 근거(업무규정 제5조) 페이지 보존. 가격 기반 추정 금지 — 지수 거래일은 대조용.
- **적용 범위 확인**: 해당 연도 전체를 다루는 공식 목록임을 확인한 연도만 평일을 거래일로 생성. 빈 목록 = 미제공(휴장일 없음으로 판단 금지), 범위 미확인 연도는 `unknown`.
- 대조: 과거 구간 목록 기반 거래일 vs 지수 거래일 — 불일치 1건이라도 승인 보류·원인 확인.
- 승인: 사용자가 연도별 목록·대조 결과 검토 후 `official` 기록(승인 근거 = 승인일·원천 해시). 불변.
- **매일 비교 기준**: (날짜·시장·휴장 구분)만 비교 — 조회 시각 등 부수 정보 차이는 변경 아님. 원 응답·해시는 별도 보존.
- **임시 휴장**: 연간 목록 재조회만으로 긴급 변경이 포착되는지 탐침에서 확인. 확인되지 않으면 목록 일치만으로 `clear`를 주지 않는다(별도 공지 경로 필요).
- 연말: 다음 해 목록 승인 전에는 연말 마지막 거래일 진입 세션 `unknown` → 부적격.

**실행 시각**: 관찰용 시작 07:30. 같은 날 전체 자동 재실행 없음. 요청 단위 재시도(3회)는 유지하되 실행 마감을 따른다.

**아침 5거래일 격리 관찰**: 승인된 범위의 격리 검증(시험 달력, 부적격). 다섯 번의 정상 실행을 최악 시간 검증으로 표현하지 않는다.
- 별도 확인 필요: 남은 시간 75초 검사만으로 08:48 종료가 보장되지 않는다(요청 읽기 제한은 소켓 읽기 단위, 로그인 요청 여러 개가 한 요청 안에 있음, DNS 해석은 제한 밖, 재시도 대기·저장·확정 시간). 통신 지연(느린 응답·로그인 지연·DNS 지연)을 가짜 전송으로 주입해 확정이 마감을 넘지 못하는지 시험한다.

**S13 탐침** (`s13_calendar_probe.py`·`run_s13cal.sh` → runs22): Q1 응답 형식(화면 HTML·스크립트에서 조회 정의 발견, 후보 경로 시도 — 후보는 추정), Q2 연도별 제공 범위(2024~내년, 빈 목록 = 미제공), Q3 지수 거래일 대조(판정용 아님), Q4 과거 임시 휴장 사례 포함 여부(적시성은 현재 목록으로 확인 불가 → 공지 경로 후보 기록), Q5 같은 연도 재조회의 해시 vs 내용 차이. 원 응답 전부 보존. 공식 페이지 두 곳은 사용자 조회에서 403 — 응답 형식·제공 범위 미확인 상태로 시작.

**마감 보장 확인 — 오프라인 모의 (2026-10-23, `stage5/sim/deadline_sim.py`, 네트워크 없음)**
- 조건: 마감 08:48:00, 남은 시간이 정확히 75초(08:46:45)일 때 KRX 웹 요청 1건. 각 HTTP 호출이 읽기 제한 직전까지 걸리는 경우(호출당 70초)를 가짜 세션으로 주입, 실제 `Collector`·`KrxWebTransport` 코드 사용.
- 로그인된 상태: 08:47:55 종료(마감 5초 전) — 75초 검사가 맞는 경우.
- **재로그인이 필요한 상태(세션 만료·무효 응답 뒤): 로그인 호출 3개 + 본 요청 = 08:51:25 종료, 마감 205초 초과.** 75초 검사는 한 요청 안의 로그인 호출을 계산하지 않는다.
- 그 밖에 제한 밖: DNS 해석 시간, 조금씩 오는 응답(읽기 제한은 소켓 읽기 단위), 재시도 대기, 수집 뒤 저장·재검증·확정 시간(3차 실행에서 약 15초).
- 결론: 현재 코드는 통신 지연 시 08:48 종료를 보장하지 않는다. 보완(요청 시도 전체의 시간 상한 등)은 승인 후 구현.

### 13.31 마감 보장 — 전송 자식 프로세스·단조 시계 감독 (2026-10-23 승인, 격리 구현)

**시각 (고정)**: 수집 종료 08:47:00 → 저장·재검증·확정 예비 최소 60초(초기 예산, 보장된 충분 시간 아님) → 최종 확정 마감 08:48:00.
- `signal_store.COLLECT_DEADLINE`·`CONFIRM_RESERVE_SECONDS`·`CONFIRM_DEADLINE`. primary 판정 마감(`PRIMARY_DEADLINE`)을 08:49 → **08:48**로, COMMIT 사후 확인 기준을 08:50 → **08:48**로 맞춤. 확정이 08:48 이후이거나 사후 확인 시각을 얻지 못하면 공식 평가 조회(`official_primary_cohorts`)에서 제외. 진입 규칙(`same_day_open`)의 08:50 기준(`CUTOFF`)은 그대로.
- 주의: 코드의 이전 값(primary 08:49, 사후 확인 08:50)과 §13.14의 `run_deadline` 08:48 기술이 달랐다 — 이번에 08:48 하나로 통일.

**구조**
- `analyzer/pipeline/transport_child.py`: 전송 전용 자식(`python -m`으로 직접 실행 — multiprocessing spawn은 부모 스크립트를 자식에서 다시 실행할 수 있어 쓰지 않음). 실제 전송 객체를 자식 안에서 만들고 세션을 유지. 표준 입출력 길이+pickle 프레임. DB 쓰기 없음. HTTP 호스트 수·금지 모듈 보고(`stats`).
- `analyzer/pipeline/transport_proc.ProcessTransport.send_within(…, limit)`: 부모가 단조 시계로 응답 프레임 전체 수신을 감독(로그인·DNS·수신 포함). 상한 초과·자식 비정상 종료 → 그 자식(Popen)만 terminate→kill, `CollectionAborted`. 일부만 온 프레임은 버림.
- `Collector`: 마감은 단조 시계로 감독(`remaining`). 시도 상한 = min(75초, 남은 시간). 남은 시간 < 5초면 새 시도 없음. 재시도 대기·요청 간 대기(`wait`)도 같은 예산 안에서만. `CollectionAborted` 뒤 모든 조회는 `collection_aborted`(새 실패 사유, 응답 없는 조회 실패로 허용) → 해당 종목 실패 → 실행 incomplete. 저장은 부모만, 완성된 응답만.
- `make_official_inputs`는 시한 전송(`send_within`)만 받는다(일반 전송이면 TypeError).
- 격리 실행기: `ProcessTransport` 사용, 자식 호스트 수로 Naver 0건·자식 금지 모듈 확인, `--official-times`(아침 관찰: 08:47/08:48)와 사후 확인 상태 기록. 아침 관찰 스크립트 `run_morning.sh`(→ runs23). 시험 달력 공지 확인 시각은 실행 시작 전으로.

**시험** (`tests/test_bounded_transport.py` 12개, 외부 네트워크 없음 — sleep·로컬 서버로 지연 주입, 전체 392 통과)
- 재로그인(실제 `KrxWebTransport` + 느린 세션 0.6초×4)·DNS 정지·무응답 → 시도 상한 1.5초에서 자식 종료, 수집 중단, 저장 0, 자식 회수, 새 자식 미생성. 같은 재로그인이 상한 안이면 정상.
- 조금씩 오는 응답(로컬 서버 0.3초마다 1바이트, 읽기 제한 2초로는 안 끊김) → 상한에서 종료·저장 0.
- 재시도 대기(503 반복): 수집 종료 ≤ 마감. 저장 지연(1.2초/건): 마감 뒤 새 시도 없음.
- 확정: 08:47:50 확정 = primary·사후 확인 통과·평가 포함 / 08:48:05 확정 = catchup·after_cutoff / 08:47:59 확정·08:48:01 사후 관측 = after_cutoff / 사후 시각 없음 = check_failed → 셋 다 평가 제외.
- 부모 스크립트(주 모듈 보호 없음)가 자식에서 다시 실행되지 않음(실제로 발견한 결함의 회귀 시험).
- 한계: 모의 지연은 대표 경우다. 저장·재검증·확정의 실제 소요(응답 수 비례)는 아침 관찰에서 측정하며, 60초 예산을 넘는 경우 그 표본은 평가에서 제외될 뿐 마감 안 완료가 보장되지는 않는다.

**S13 결과 (runs22/20261023_133726, 감독기 PASS, 원 응답 27개 해시 일치)**
- 조회 정의(화면에서 발견): OTP(`GenerateOTP.jspx`, bld `MKD/01/0110/01100305/mkd01100305_01`) → `OPN99000001.jspx`, 파라미터 `search_bas_yy`·`gridTp`(KRX/CME/EUR)·`pagePath`. 필드 `calnd_dd`·`calnd_dd_dy`·`dy_tp_cd`·`holdy_nm`·`kr_dy_tp`. 두 번째 정의(`_02`, "거래일정표": 구분·일자·요일·시장·비고)는 CME·Eurex 연계시장용으로 보이며 이번에 조회하지 않음.
- 연도별 행: 2024 18 / 2025 19 / 2026 17 / 2027 16. 모든 행이 요청 연도, 1/1·12/31 포함. **화면의 연도 선택 목록은 2026이 최신(2027 없음)** — 2027 응답은 공식 발표·확정 근거로 인정하지 않음(미승인).
- 지수 대조: 실제 비교 구간 2025-08-19~2026-10-23만, 불일치 0. 요약의 `years_compared`(2024~2027)는 표기 오류 — 비교 구간 밖 연도는 대조되지 않았다.
- 임시 휴장 사례 5개 모두 목록에 있음. 반영 시점(적시성)은 미확인.
- 같은 연도 2회 조회: 본문 해시·행 동일.
- 공지 경로 단서: 휴장일 화면 자체의 안내 문구가 2025년 특별 휴장(추석 전일 야간거래)을 KRX **「보도자료」**(open.krx.co.kr OPN05000000) 링크로 안내 — 임시 변경 공지 경로 후보. 검증 전.

**승인 근거 묶음 준비 (2026-10-23, `stage5/calendar_package.py` → runs22/20261023_133726/package, 네트워크 없음·승인 아님)**
- S13 보존 원 응답만 사용(해시 재확인). 범위 2025-08-19~2026-12-31, 범위 밖 unknown(기록 안 함), 2027 미승인(화면 연도 선택 목록에 없음).
- 근거 구분: `past_index_confirmed` 431일(2025-08-19~2026-10-23, 지수 대조 불일치 0 — 묶음에서 다시 계산) / `future_list_only` 69일(2026-10-24~12-31, 목록 근거만, 매일 공지 확인 전제). 거래일 334·휴장 166.
- 연도 점검(2025·2026): 화면 연도 선택 목록 포함, 모든 행이 그 연도, 1/1·12/31 연말휴장일 포함, `gridTp=KRX`.
- 제안 버전 `krx-official-20250819-20261231-1875375dfe64`(내용 해시). 승인 전 남은 것: S14 공지 경로 검증, 규정 근거 페이지 원문 보존, 사용자 승인 기록.

**매일 확인 방식 보완 (2026-10-23 지시)**
- 보도자료 제목 키워드는 탐색용 — 걸리는 제목이 없다는 사실만으로 변경 없음을 증명하지 않는다.
- 신규 게시물뿐 아니라 기존 게시물 수정·첨부 변경도 감지 가능해야 한다(S14에서 확인).
- 페이지 누락·조회 기간 공백·파싱 실패는 "새 공지 없음"이 아니다 → `failed`.
- 사람이 검토한 항목은 게시물 ID·본문 해시·첨부 해시와 함께 기록 → 같은 내용이면 다음 실행에서 다시 막지 않고, 해시가 바뀌면 다시 검토.
- 보도자료 게시일은 공지 시점 근거로만 기록 — 휴장일 목록 반영 시점으로 쓰지 않는다.
- 달력 승인과 매일 자동 `clear`는 S14 결과를 본 뒤 확정.

**S14 탐침** (`s14_notice_probe.py`·`run_s14.sh` → runs24, 읽기 전용): KRX 보도자료 게시판(coreboard: `/board/{bbsUrl}/list|view|list?cmd=filelist`, 화면 스크립트에서 확인) 2024-01 이후 전 쪽 목록, 여섯 사례(2024-04-10·2024-10-01·2025-01-27·2025-06-03·2026-06-03·2025-10-02 파생 야간) 휴장일 전 60일 창 게시물 **전부** 본문·첨부 목록, 사례 관련 첨부 해시(건당 20MB 상한). 확인: Q1 공식 경로(게시판 제목·휴장일 화면 링크·다른 공지 게시판 후보), Q2 제목 누락(본문엔 있고 제목 키워드엔 없는 게시물), Q3 수집 신뢰성(전체 건수·쪽 경계·같은 쪽 재조회·범위 밖 쪽·게시일/수정일 필드·ID 형식), Q4 정규장 vs 야간·연계 문구 구분. 가짜 게시판으로 실행 경로 점검(실제 화면 구조는 미확인 — 원 응답 전부 보존).

**S14 1차 (runs24/20261023_144156) — FAIL `board_form_not_found` = 게시판 미도달(공지 없음 아님)**: 보도자료 첫 화면은 탭 바깥 화면(`design-tabs`, 구성 요소 `fe3647848b826aa1ddee224c4b5526f4`). 1번 탭 "한국거래소" = `/contents/OPN/05/05000000/OPN05000000T1.jsp`, 나머지 탭은 `/board/OPN05000000T{n}/bbs`. 휴장일 화면 링크 `#fe36…=1&view=2025091001`은 같은 구성 요소의 1번 탭을 가리킨다.
- 보완: `stage5/s14_board.py`(탭 따라가기: 바깥 화면 → "한국거래소" 탭 → 게시판 폼, 폼이 없으면 같은 형식의 `/board/{id}/bbs`를 **추정 경로로 표시**해 시도, 끝내 없으면 `board_not_reached`), 회귀 시험 `stage5/test_s14_board.py` 5개(보존 HTML: 바깥 화면에 폼 없음·탭 순서·휴장일 링크 = 같은 구성 요소 1번 탭 / 합성: 탭 화면 폼 발견·추정 경로 표시·미도달 보고). 탐침은 휴장일 링크와 탭의 일치 여부를 `holiday_link_matches_krx_tab`으로 기록.

**S14 2차 (runs24/20261023_144627) — FAIL `board_not_reached`, 탭은 도달**: "한국거래소" 탭(OPN05000000T1.jsp)은 `bbsUrl` 게시판이 아니라 조회 정의(bld) 게시판 — 목록 `OPN/05/05000000/opn05000000t1_01`·본문 `_02`·첨부 `_03`, 키 `noti_no`, 화면 필드 `title`·`creat_ddtm`·`contn`(본문 화면엔 `creatr_nm`), 폼 전송 주소 `/contents/OPN/99/OPN99000001.jspx`, 쪽 크기 6, 조회기간 필드 `fromdate`·`todate`. 추정 경로 `/board/OPN05000000T1/bbs`는 404.
- 요청 방식 근거: 공통 스크립트 `/res/pc/js/func.js`(조사 시 원문 확인, 이번 실행 보존 대상)의 `$.board.list/view/attachFile`·`$.otpCode`: OTP `GET /contents/COM/GenerateOTP.jspx?bld=…&name=list|view|attach` → `POST /contents/OPN/99/OPN99000001.jspx?code=OTP`(본문 = 폼 필드 + `curPage`·`pageSize` / + `seq=noti_no`), 응답 JSON(행에 `totCnt`). 첨부 받기는 `name=fileDown` OTP → `getFileServer()/download.jspx` — 파일 서버 주소는 func.js에 없음(미확인).
- 보완: `s14_board.find_bld_board`(탭 HTML의 `$.board.init` 설정 + 폼), `discover`가 bld 게시판을 우선 반환. 회귀 시험 2개 추가(보존 탭 HTML, 총 7개). 탐침 v2(`s14_notice_probe.py`, v1은 `_attempt1` 보존): 월 단위 연속 조회 창 2024-01~현재(창마다 보고 건수 = 수집 고유 건수, 쪽 크기 6 기준, 같은 쪽 재조회·쪽 크기 100 교차·범위 밖 쪽), 사례 창 게시물 전부 본문·첨부 목록, 파일 서버를 스크립트에서 찾을 때만 첨부 받기(아니면 첨부 목록 해시만, "받기 경로 미확인"). 가짜 응답으로 경로 점검(PASS).

**S14 3차 (runs24/20261023_150019) — 탐침 PASS, 내용 검증 미완** (사용자 검토): 34개월 1,478건(월별 보고 건수 일치·중복 0·교차 확인 정상), 사례 창 508건 본문·첨부 목록, 응답 2,564개 200·해시 일치. 한계: 본문 508건 전부 "첨부참조" 류 → 첨부 미확보로 제목 누락·시장 구분 미판정. 요약 오류: `modified_date_field: []`(탐색식이 `adj_ddtm`을 놓침), `posts_outside_their_window: 1478`(`/` 날짜 형식 처리 오류).

**보존 응답 재분석 (`stage5/s14_reanalyze.py` → runs24/20261023_150019/reanalysis, 네트워크 없음, 해시 불일치 0, 날짜 해석 오류 0)**
- 날짜 필드: 목록 `noti_dd`(형식 `YYYY/MM/DD`) = 조회 월과 1,478/1,478 일치 → **게시일**로 해석. 목록 `creat_ddtm`은 1,473건만 일치 — 5건은 게시일보다 늦음(예: 2025-08-19 게시물의 `creat_ddtm` 2026-06-25) → 게시일이 아님(생성·재등록 시각으로 보임, 의미 미확정). 본문 응답(형식 `YYYYMMDD`)에 `adj_ddtm`·`adjprn_id`(조정일·조정자) 존재 — 508건 중 2건이 생성일과 다름(2024040302: 04-04→04-05, 2024090301: 08-05→09-03). `adj_ddtm`은 **일 단위**라 같은 날 여러 번 수정은 구분 못 함.
- 변경 감지에 쓰면 안 되는 부수 필드: `inq_cnt`(조회수), `rn`(순번), `totCnt`. 이번 재조회 사이에는 목록 필드 변동 0이었으나 조회수는 본질적으로 변한다.
- 게시물 ID: 1,386건 `YYYYMMDDNN`, **92건(2026-08 이후) 9자리 다른 형식**(예 260815725) → ID로 날짜·순서를 추정하지 않는다. 게시일은 `noti_dd`.
- 본문 값: "첨부참조" 491 / "첨부 참조" 14 / 변형 3. 첨부 필드 `file_seq`·`file_path`·`save_file_nm`·`file_nm`, 게시물당 2~10개(hwp·pdf 쌍이 대부분).
- 사례 공지(게시일 기준 사례까지 일수): 2024-04-10 총선 2024032702(14일) / 2024-10-01 임시공휴일 2024091001(21일) / 2025-01-27 2025011601(11일) / 2025-06-03 2025052002(14일) / 2026-06-03 2026052001(14일, 제헌절 7/17 휴장 함께 공지 — 연중 목록 변경 사례) / 2025-10-02 파생 야간 2025091001(22일). 일정 관련 제목 게시물 전체 17건(연말 운영·연초 개장, 야간거래 휴장 2026012605·260915743 포함).
- 이 게시일은 공지 시점 근거로만 기록 — 휴장일 목록 반영 시점이 아니다.

**첨부 받기 경로**: 보도자료 화면 인라인 스크립트 `getFileServer()` = `https://file.krx.co.kr`(보존 화면에서 확인). `$.down`: OTP(`name=fileDown&filetype=att&url=<첨부 bld>&폼 필드&seq&file_seq`) → `POST https://file.krx.co.kr/download.jspx`(code, tabCode, tabNumber). **S14b 탐침**(`s14b_attach_probe.py`·`run_s14b.sh` → runs25, 목록 재수집 없음): 일정 관련 제목 17건 + 조정일 다른 2건의 첨부 목록을 지금 다시 받아 S14 보존 목록과 비교(변경 감지 기준 안정성), PDF 받기(형식 `%PDF` 확인·해시), HWP는 해시만. PDF 본문 해석(정규장·야간 구분, 휴장 문구, 제목 누락)은 받은 파일로 오프라인에서.

**S14b (runs25/20261023_160602) — 감독기 PASS**: 받기 경로 확인(PDF 25개 `%PDF`, 총 53개 파일, 요청 154건 200, HTML 오류 0). 대상은 일정 관련 제목 22건 + 조정일 차이 2건 = 24건(앞선 "17건" 설명은 틀림 — 재분석 파일의 22건이 맞다). 첨부 목록 13건 보존본과 일치, 11건은 최초 확보(변경 없음으로 세지 않음).

**PDF 오프라인 분석 (pdftotext, 2026-10-23)**
- 정규장 휴장 공지(2024032702·2024091001·2025011601·2025052002·2026052001): 유가증권·코스닥·파생상품시장본부 공동 배포, 제목·본문 "증권·파생·일반상품시장 휴장", 근거 업무규정 제5조. 2026052001은 제헌절이 법정 공휴일로 지정되어 **2026년부터 정기 휴장일에 포함**된다고 명시(연중 목록 변경의 공지 근거).
- 야간거래만 휴장(2025091001·2026012605·260915743): 파생상품시장본부 단독 배포, "파생상품 야간거래 휴장" — 정규장 공지와 배포 부서·제목·본문으로 구분 가능.
- **휴장이 아닌 정규장 거래시간 임시 변경**: 수능일(2024103104 11.14, 2025103001 11.13 — 증권시장 정규시장 개장 지연·종료 순연), 연초 개장일(2024121701·2025121801 — 1.2 정규시장 10시 개장, 연말 휴장일 12.31). 휴장일 목록에는 나타나지 않는다 → 매일 확인 대상에 포함 여부 결정 필요(세션 상태는 그대로, 시가 시각만 바뀜).
- 범위 안 정규장 공지와 승인 후보 목록 대조: 2025-12-31 연말 휴장, 2026-06-03 지방선거, 2026-07-17 제헌절 — 모두 2025·2026 목록에 있음.
- **첨부 교체 감지 한계**: `save_file_nm`은 대부분 `noti_no + file_seq`(1,229/1,236)로 게시물 번호에서 만들어진 이름 → 같은 순번 파일을 바꿔 올려도 이름이 그대로일 수 있어 **첨부 목록 해시로는 교체를 감지 못할 수 있다**. 2024040302는 순번 3이 없고 순번 4(이름 1712302368506 = 2024-04-05 epoch ms)가 조정일 04-05와 같아 추가·교체 흔적. → 변경 감지는 첨부 **내용 해시**(받은 파일)까지 필요.
- 미검증: 508건(또는 전체 1,478건)의 제목 누락, 실제 교체 사례 관찰.

**매일 공지 확인 방식 결정 (2026-10-23, 격리 구현·검증 범위 — 예약 설치·운영 전환 아님)**
- 신호 실행과 분리: 07:00 공지 확인 → 07:25 확인 완료 마감 → 07:30 신호 실행(수집 08:47·확정 08:48 유지). 당일 확인이 완료되지 않았거나 `changes_pending`이면 그날 공식 적격성 불허.
- 검토 대상: 정규장 휴장, **정규장 거래시간 변경**(휴장은 아니어도 예정 진입 시각이 달라짐), 모호한 공지. 검토 전 `changes_pending`. 검토 후 기록: 게시물 번호·본문 해시·첨부 내용 해시·적용 날짜·개장/종료 시각·근거. 새 일정(거래시간) 기능 전에는 거래시간 변경 날짜의 공식 적격성 차단.
- 휴장일이 달력에 이미 있다는 사실만으로 자동 통과하지 않는다(같은 공지에 거래시간 변경 등이 함께 있을 수 있음) — 검토한 게시물·첨부 해시와 적용 내용 기준으로만 통과.
- 야간거래만 휴장·파생 거래시간 변경: 기록(정규장 차단 아님). 구분이 모호하면 검토 대상.
- 최근 60일 재확인 채택(완전한 변경 감시 아님 — 60일보다 오래된 공시도 수정될 수 있음). **앞으로 적용될 일정이 있는 게시물은 게시일과 무관하게 감시 대상 유지.** PDF 없음·읽기 불가는 확인 실패(조용히 제외 금지).
- 변경 감지 기준: 게시물 번호·본문 해시·첨부 목록 해시·첨부 **내용** 해시(목록 해시만으로는 교체 감지 불가), 조회수·순번·전체 건수 제외.
- 구현(공지 확인기·적격성 연결)은 S14c 결과로 본문 검사 규칙을 확정한 뒤.

**S14c 준비 (2026-10-23)**
- 정답 후보: 기존 22건을 PDF 원문으로 판독한 **제안 라벨** `stage5/s14_labels_proposed.json`(사람 확정 전 — 정답으로 쓰지 않음). 분류: 정규장 휴장 5 / 정규장 거래시간 변경 4(연말 공지 2건은 휴장 함께) / 거래시간 변경 언급(개장식) 2 / 날짜 미정 정책·기사 해명 2 / 야간만 휴장 3 / 파생 거래시간 변경 3 / 파생 신시장 1 / 기념식 2. 검토 대상 클래스 = 정규장 휴장·거래시간 변경·변경 언급·모호.
- 본문 검사 규칙 첫 판 `stage5/notice_scan.py`: PDF 텍스트에서 정규장 휴장·거래시간·정책 신호, 정규장 담당 부서(유가·코스닥 본부) 머리글 + 일정 단어, 야간·파생 구분. 누락보다 오탐 우선. 텍스트 200자 미만은 확인 실패. 22건(학습용 — 평가 아님)에서 검토 대상 판정 누락 0·오탐 0, 세부 분류는 거침. **PDF 텍스트 추출이 본문 일부를 잃는 사례 확인**(2024121701: 표·글머리 내용 누락 → 부서 머리글 규칙으로 보완).
- 탐침 `s14c_pdf_probe.py`·`run_s14c.sh`(→ runs26): 1,478건 전부의 PDF(첨부 목록은 S14 보존 508건 재사용, 나머지 조회), PDF 없음·받기 실패·HTML 응답 기록, 연속 실패 10이면 중단. 평가 `s14c_evaluate.py`(오프라인): pdftotext → 규칙 → 라벨 대비 누락·오탐, 라벨 밖 새 후보는 원문으로 사람 판정.

