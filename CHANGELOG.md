# CHANGELOG

날짜 포맷: v7.0.0+ 는 연-월-일

---

## v8.6.9 (2026-11-06)

### Changed

- **장기 거래정지·기준일 정지 종목 quick reject (등급 기준 변경)**: 최근 252거래일 중 거래정지(거래량 0)가 **20일 이상**이거나 **기준일에 정지 중**이면 `QuickRejectReason.TRADING_HALT` → 등급 AVOID 강제. 점수는 감사용으로 유지. 20일 미만 짧은 정지(액면분할 등)는 변동성 영향이 1~2% 수준이라 통과.
  - 배경: 정지 기간의 0% 등락이 변동성을 낮춰 리스크 점수를 부풀렸다. 서희건설(035890)은 252거래일 중 137일 정지(변동성 약 32% 과소)였는데 리스크 25/25 만점·buy~strong_buy(80~83점)로 표시됐고, 2026-05-04 재개 후 −34%(~09-30). 2026-10-22 S12에서 발견하고 "별도 연구 항목"으로 미뤘던 결함.
  - 기준 20일(`config.QUICK_REJECT["halt_days_min"]`)은 "약 1개월 이상"이라는 상식 기준이며 과거 데이터로 최적화한 값이 아니다.
  - 현행 경로: `krx.fetch_halt_info`(변동성과 같은 일봉 조회 공유, 추가 요청 없음) → `RiskData.halt_days`·`halted_on_session` → `validate`의 quick reject. 공식 입력 경로: 01701 코드 0 행 수와 기준일 코드(`detail.last_code`, 변동성 정의 상세에 추가 — 값 산식 불변).
  - 조회 실패·자료 부족(None)은 이 규칙으로 막지 않는다(기존 동작).
- ENGINE_VERSION 8.6.8 → 8.6.9.

### Calibration Result

- Regression: tests/ 396 통과 (`test_trading_halt_reject.py` 4 추가 — 20일 경계·짧은 정지 통과·기준일 정지 차단·조회 공유). 과거 데이터로 이 규칙의 성과 영향은 아직 재계산하지 않음.

---

## Unreleased — 실행 시각·시한 전송 (§13.31, 격리 구현, 운영 미적용)

ENGINE_VERSION 변경 없음(8.6.8). 점수 계산 변경 없음.

### Breaking Changes

- 확정 마감 통일: primary 판정 08:49 → **08:48:00**, COMMIT 사후 확인 기준 08:50 → **08:48:00**. 08:48 이후 확정·사후 확인 실패는 공식 평가 조회에서 제외. 진입 규칙의 08:50 기준은 그대로.
- 응답 없는 조회 실패 사유에 `collection_aborted` 추가.

### Added

- `analyzer/pipeline/transport_child.py`·`transport_proc.ProcessTransport`: 전송 전용 자식 프로세스(세션 유지, DB 쓰기 없음) + 부모 단조 시계 감독. 시도 전체(로그인·DNS·수신) 상한 초과 시 그 자식만 종료하고 수집 중단, 늦은·불완전 응답 미저장.
- 공식 시각 상수: 수집 종료 08:47:00, 확정 예비 60초(초기 예산), 확정 마감 08:48:00.

### Changed

- `Collector`: 단조 시계 마감 감독, 시도 상한 = min(75초, 남은 시간), 남은 5초 미만이면 새 시도 없음, 재시도·요청 간 대기도 마감 예산 안(`wait`). `make_official_inputs`는 시한 전송만 허용.

### Fixed

- 남은 시간 75초 검사만으로는 08:48 종료가 보장되지 않던 문제(재로그인 시 1요청이 마감 205초 초과 — 오프라인 모의).

### Calibration Result

- Regression: tests/ 392 통과 (`test_bounded_transport.py` 12 추가, 기존 시각 시험은 08:48 기준으로 조정).

---

## Unreleased — 표본 정책 `validation_exclusion_v1_2026-10-23` (결정 B 개정, 격리 구현, 운영 미적용)

ENGINE_VERSION 변경 없음(8.6.8). 점수 계산·가중치·등급 기준 변경 없음. 운영 적용 전 새 엔진 버전으로 구분한다.

### Philosophy

- 공시 검증을 통과하지 못한 종목 하나가 전체 관찰을 무기한 막지 않게 하되, 누락을 숨기지 않는다: 원래 유니버스에 남기고 제외 사실·사유·비율을 표본에 기록한다. 결과는 "데이터 검증을 통과한 종목에 대한 전략 성과"이며 선정 편향은 남는다(범위 명시).

### Breaking Changes

- `run_signals.processing_status`에 `excluded_validation` 추가 — 이전 스키마 DB는 `pipeline_schema_upgrade_required`로 거부(자동 변경 없음).

### Changed

- 결정 B 개정: 재무 입력이 공시 검증 사유(`dart_inputs.FIN_EXCLUSION_REASONS`)로만 실패하면 `excluded_validation`(점수·등급 없음, 원인·사용 공시 보존), 실행은 계속. 통신 실패·요청 한도 초과·형식 오류 등 실행 오류는 `failed` → 실행 `incomplete`. 근거 저장·재검증 오류는 기존대로 `incomplete`. 시장 입력 검증 실패는 기존대로 `failed`.
  - `official.apply_financial_status`가 상태를 부여하고, `finish_run`의 `_financial_status_problem`이 같은 사유 목록으로 일관성 검사(어긋나면 `financial_status_inconsistent`).
- 제외 상한: 필수 결측 제외 + 검증 제외 합산 ≤ 유니버스의 5%(정확히 5% 허용, 초과 시 `degraded_inputs`). 기존 상한은 필수 결측만 셌다.
- ROE: 새 버전의 입력 정의로 `dart_roe_v1` 채택 방향(검증된 값 없음 = ROE 없음, 검증 실패 = 위 제외 규칙). 한 세션 비교는 교체 영향 확인일 뿐 수익성 근거가 아님.

### Added

- `cohort_scope`(정책 ID, 범위 `validated_subset`, 고정 유니버스 크기·해시, 상태별 수, 제외 비율)·`cohort_exclusions`(종목·상태·사유) — 확정 트랜잭션에서 기록, 불변. 과거 코호트는 소급 변경하지 않음.
- 선정은 `scored`만(기존 규칙). 제외 종목 자리는 같은 규칙의 다음 후보.

### Calibration Result

- Regression: tests/ 380 통과 (`test_validation_exclusion.py` 7 추가: 정확히 5% 완료·기록·충원, 초과·합산 상한, 통신 실패·DART 020은 제외 아님, 시장 검증 실패 유지, 잘못된 제외 표시 거부, 구 스키마 거부).
- 전체 격리 실행(2026-10-23, runs21/20261023_101038): 감독기 PASS — 109 채점·5 검증 제외(4.386%), 실행 completed, 10종목 확정, 범위 1행·제외 5행, 제외 종목 선정 0, 시험 달력 eligible=0, 보호 파일 8,558개 일치, 새 실패 0.

---

## Unreleased (2026-10-23) — 5단계 격리 구현, 운영 미적용

ENGINE_VERSION은 바꾸지 않았다(8.6.8). 점수 계산·가중치·등급 기준 변경 없음. 운영 DB 적용·엔진 등록·예약 실행 없음.

### Added

- 공식 입력 경로 `analyzer/pipeline/official_inputs.py` (격리 연결, 설계 §13.28). `SignalDeps.official_inputs`를 명시적으로 넣었을 때만 사용 — 기본 경로·CLI 불변.
  - KRX(03502·01701·00301·Open API)·DART 원 응답 → 정의별 입력(종목 12 + 이격도 1) → 기존 `validate → score → decide_execution`. Naver·pykrx 경로 미사용.
  - 입력 하나라도 verified가 아니면 종목 처리 실패(`input_failed` / `financial_input_failed`). ROE는 `dart_roe_v1`(값 없음 = ROE 없음, 검증 실패 = 종목 실패).
  - 시가총액 0이면 실패 — 현행의 전일 시가총액 대체는 새 경로에서 하지 않음.
- 달력 구분 기록 `calendar_classifications` (`test` / `official`, 불변). `TradingCalendarStore.classify_version`.
  - `official`은 승인 근거(`approval_ref`)가 있어야 하고, 시험 표시(`iso-test-` 버전·시험 출처 주소)가 있는 달력은 `official`로 기록할 수 없음.

### Changed

- **공식 적격 판정**: `planned_entry`가 `calendar_official_reasons`로 달력 구분을 검사 — 구분 기록이 `official`이 아니거나(없음·`test`) 시험 표시가 있으면 `calendar_not_official`로 부적격. `confirm()`의 재계산도 같은 `planned_entry`를 쓴다.
  - 기존 달력(구분 기록 없음)은 공식으로 간주하지 않음 → 승인 근거로 `official` 기록 전까지 모든 코호트가 부적격. 이번에 공식 달력을 승인하지 않았다.
- `validate`: 수급·외국인 결과에 `None` = 조회하지 않음(not_requested, §13.15) — 조회 실패(fetch_errors)로 세지 않음. 기존 호출(값 전달)은 동작 불변.
- `analyzer/rules/market_regime.py`: `analyzer.data.krx`(pykrx import 시 KRX 로그인)를 첫 사용 시 import. 공식 경로가 별도 로그인을 만들지 않게. `get_market_state` 동작 불변.
- `analyzer/pipeline/sources.py` 이격도 정의(`kospi_disparity_v1_krx00301`)에 `detail = {last_close, mean_close}` 추가 — 값 산식 불변, 저장 시 재검증 대상.
- `constants.DataSource.KRX` 추가 (KRX 원 응답 직접 해석 표기).

### Fixed

- `analyzer/pipeline/official.py` `_git`과 격리 실행기(`stage5/official_run.py`)의 Git 조회에 `GIT_OPTIONAL_LOCKS=0` — `git status`가 `.git/index`를 갱신해 감독기 보호 파일이 바뀌던 문제(2026-10-22 전체 격리 실행 `FAIL_PROTECTED`). `.git/index`는 보호 대상 유지.

### Calibration Result

- Regression: tests/ 372 통과 (`test_official_inputs.py` 6, 달력 구분 5, Git index 불변 1 추가; 판정 로직 시험 달력은 승인 근거와 함께 `official`로 기록하도록 고정 자료 수정).
- 2026-10-22 전체 격리 실행(수정 전): 114종목 중 109 채점, 예상 차단 5, 새 실패·네트워크 실패 0, 근거 감사 정상, Naver 요청·금지 모듈 0, 약 15분 39초. 감독기 `FAIL_PROTECTED`(`.git/index`) → 수정 후 재실행 필요.
- 2026-10-23 전체 격리 재실행(수정 후, 기준 세션 2026-10-22): 감독기 PASS — 보호 파일 8,556개 일치(`.git/index` 포함), 109 채점·예상 차단 5·새 실패 0, 시험 달력 `calendar_not_official`, 근거 감사 정상(응답 927개), Naver·금지 모듈 0, 약 17분 5초. 실행 `incomplete`(결정 B), 공식 표본 0. 운영 적용 전 새 버전으로 구분한다.

---

## v8.6.8 (2026-10-15)

### Fixed

- KIS를 쓰지 않는 경로에서 KOSDAQ 종목의 PER·PBR·배당 조회 누락 수정 (데이터 수집 버그, 점수 로직 변경 아님).
  - `analyzer/data/krx.py` `_cached_fundamental()`: `get_market_fundamental(date)` → `get_market_fundamental(date, market="ALL")`. pykrx의 기본 시장이 KOSPI라 KOSDAQ 종목이 일괄 조회 결과에 없었음.
  - 영향: KIS 경로가 꺼진 실행(공식 신호 실행기, KIS 미설정 시 morning_run 등)에서 KOSDAQ 종목이 `fundamentals ticker_not_found` → LOW → 제외되던 문제 해소. 해당 종목이 새로 채점되므로 결과가 달라짐.
  - 발견: 4단계 격리 검사(2026-10-15)에서 워치리스트 114종목 중 KOSDAQ 35종목이 같은 사유로 제외 → `degraded_inputs`.
  - 점수 계산·가중치·등급 기준은 변경하지 않음. 시가총액 일괄 조회(`get_market_cap`)는 pykrx 기본값이 이미 ALL이라 변경 없음. `analyzer/data/earnings_history.py`는 KOSPI·KOSDAQ을 명시해 각각 호출하므로 변경 없음.
- `tests/test_krx_fundamental_market.py`: 5케이스 추가 (ALL 전달, KOSPI·KOSDAQ 정상 조회, 미등록 종목은 기존 `ticker_not_found` 유지, 날짜당 일괄 조회 1회). 수정 전 코드에서 3케이스 실패 확인.

### Calibration Result

- Regression: tests/ 206/206 통과
- 엔진 소스 해시가 바뀌므로 이전 등록 해시는 사용할 수 없음. 운영 기준 엔진 등록·OOS 시작 보류.

---

## v8.6.7 (2026-10-14)

### Fixed

- 네트워크 기본 timeout 추가, 모멘텀 미완료 작업 취소·관측 개선. 점수 계산·가중치·등급 기준은 변경하지 않음.
  - `analyzer/net.py` 신규: `requests` 호출에 timeout이 없으면 기본값(연결 10초, 읽기 60초)을 넣음. 명시적 timeout은 그대로 두고, `timeout=None`(무제한 명시)도 바꾸지 않되 따로 집계. 중복 호출해도 한 번만 설치.
  - 설치 위치: 실행 진입점에서 pykrx import 전 — `app.py`(Streamlit, `st.navigation` 라우터라 모든 페이지에 적용), `morning_run.py`, `run.py`, `scripts/auto_run.py`, `scripts/run_screener.py`.
  - 원인: pykrx 데이터 조회가 timeout 없이 요청해 응답이 없으면 작업 스레드가 무기한 대기 → 배치 반환 후에도 비데몬 스레드 때문에 프로세스가 종료되지 않음(③-D 2차 격리 시험에서 관측, 샌드박스 재현).
  - 읽기 timeout은 요청 전체 시간 한도가 아님(조금씩 오는 응답은 끊기지 않음). 배치 제한·외부 감독 제한은 별도로 유지.
- `analyzer/data/momentum.py`: 배치 종료 시 `shutdown(wait=False, cancel_futures=True)`로 시작 전 작업 취소. 미완료를 시간 초과·취소·조회 실패로 분류하는 `batch_fetch_momentum_report()` 추가. 기존 `batch_fetch_momentum()`은 반환 형식 그대로 두고 미완료 시 WARNING 로그.
- `tests/test_net_momentum.py`: 8케이스 추가 (중복 설치, 무응답 서버, 명시 timeout·None 보존, 느린 응답, 실행 중/대기 작업 분류, 조회 실패 분류, 래퍼 경고, 배치 제한 후 프로세스 자체 종료).

### Calibration Result

- Regression: tests/ 79/79 통과
- 이 버전도 신규 OOS 기준 엔진이 아님 (기준 엔진 등록·OOS 시작 보류).

---

## v8.6.6 (2026-10-14)

### Fixed

- 필수 데이터 누락 시 오류 종류와 관계없이 LOW로 판정하여 실행 차단 (`analyzer/validation/validator.py` `_determine_confidence()`, 감사 F01).
  - 기존: 필수 필드(`market_cap`, `fundamentals`)가 일시 오류(API_TIMEOUT 등)로 비어 있으면 `fetch_errors`에 있다는 이유로 필수 누락 검사를 건너뛰어 MEDIUM → 실행 가능으로 통과.
  - 수정: 필수 여부를 오류 종류와 무관하게 resolved 값으로 판정. 결측 원인은 노트에 함께 기록 (예: `market_cap 없음 (api_timeout) → LOW confidence`).
  - 점수 계산·가중치·등급 기준은 변경하지 않음. 점수는 참고용으로 계속 계산되며, 실행가능 여부만 기존 LOW 차단 규칙(`decide_execution`)으로 분리됨.
  - 영향: 필수 필드가 일시 오류로 비어 있던 종목의 confidence(MEDIUM→LOW)와 실행가능 판정(True→False). 비필수 필드 일시 오류는 기존대로 MEDIUM.
- `tests/test_integration.py`: `TestRequiredFieldMissing` 5케이스 추가 (수정 전 코드에서 3케이스 실패 확인).

### Calibration Result

- Regression: tests/ 71/71 통과
- 이 버전은 신규 OOS 기준 엔진이 아님 (신호 시점·기록·가격 수집 수정 후 기준 엔진 별도 확정).

---

## v8.6.5 (2026-10-08)

### Fixed

- `analyzer/scoring/valuation.py` `score_valuation_hybrid()`: `pbr=None` 전달 시 분모 버그 수정.
  - 기존: `hybrid_pbr_rel_weight=1.0`이므로 pbr=None이어도 `total_w=2.0` 유지 → PER 점수 절반으로 깎임 (per=8.0 기준 22→11).
  - 수정: `effective_pbr_w`로 실제 사용된 PBR 가중치만 분모에 반영. pbr=None이면 `effective_pbr_w=0.0`, `total_w=1.0`.
  - apply_sector_correction에서 `pbr=None`으로 hybrid 호출하는 경로에서 영향.
- `tests/test_integration.py`: 보정 실제 발생 케이스 / 지주사 pbr=None+peers 케이스 추가 (총 8케이스).

### Calibration Result

- Regression: tests/ 66/66 통과

---

## v8.6.4 (2026-10-07)

### Fixed

- `analyzer/scoring/scorer.py` `apply_sector_correction()`: PBR 소멸 버그 수정 (Stage 20K).
  - **일반기업**: 기존 코드는 `hybrid_score(PBR 포함)`로 `valuation_score`를 덮어써 PBR 20pt 소멸. 수정 후: `per_hybrid(pbr=None) + pbr_score_from_table(pbr)`, max_score=50 유지.
  - **지주사 peers 없음**: 기존 코드는 PER=None 시 fallback score=0으로 덮어씀. 수정 후: peers 부족 시 early return — 보정 전 원본 그대로 유지.
  - **지주사 상대평가 성공**: PBR-only 25점 정책 유지 (변경 없음).
  - `components[0]`, `available_max`, `total_score`, `normalized_score`, `grade`, `decision` 전부 갱신.
- `tests/test_integration.py`: `TestApplySectorCorrection` 6개 케이스 추가.

### Calibration Result

- Regression: tests/ 64/64 통과

---

## v8.6.3 (2026-10-06)

### Changed

- `analyzer/scoring/scorer.py`: PBR 고정 점수표를 valuation score에 통합 (Stage 20J).
  - `score()`: PBR 있으면 valuation_comp = per_score + pbr_score, max_score 25→50
  - `apply_sector_correction()`: avail을 hybrid_comp.max_score 기준으로 재계산
- `analyzer/scoring/valuation.py`: `pbr_score_from_table(pbr)` 헬퍼 추가
- `config.py`: `VALUATION["pbr_score_table"]` 추가 — Stage 20H 확정값 그대로 (<0.5→25, 0.5-1→20, 1-2→15, 2-3→10, ≥3→0)

### Calibration Result

- Research: Stage 20G~20I (multi-window IC, incremental IC, production table 비교)
- Replay: retro_calibration × pit_market_fundamentals, n=17,218 — 기존 IC 0.148 → 신규 IC 0.179 (ΔIC=+0.031, t=6.3, pos=86%)
- Bear구간 SB alpha: +13.59% → +14.18% (핵심 구조 유지)
- Regression: tests/ 58/58 통과
- available_max: 75→100 (PBR available 시). normalized 비율은 PBR 구간에 따라 낮아질 수 있음 — 의도된 재가중.
- **Known limitation**: `apply_sector_correction()` (screener 경로)에서 hybrid_score가 PBR 포함 val을 덮어씀 → screener에서 PBR 점수 소멸. `run.py` 콘솔은 영향 없음. Forward validation 전 수정 예정.

---

## v8.6.2 (2026-10-02)

### Added

- `scripts/collect_pit_fundamentals.py`: pykrx 기반 PIT PBR/PER 일별 수집기.
  - 2020-01-01 ~ 현재 1,650 거래일 × KOSPI+KOSDAQ 전 종목 수집 완료 (417만 행)
  - `database/research.db` → `pit_market_fundamentals` 테이블
  - retro_calibration Naver 오염 검증 기반 마련

### Changed

- `database/oos_store.py`: `oos_type` 컬럼 추가 (schema v1→v2).
  - `primary` (PIT 정합) vs `catchup` (분석 시점이 entry 이후) 분류
  - 09-02~09-04 backfill → `catchup`, Stage 21 Primary OOS에서 제외
  - `summary()`: `primary_dates` / `catchup_dates` 분리 반환

### Fixed

- `constants.py`: `ENGINE_VERSION` 8.5.20 → 8.6.2 (v8.6.0~8.6.1 bump 누락 보정)

---

## v8.6.1 (2026-09-29)

### Added

- `database/oos_store.py`: OOS Signal Store 모듈. STRONG_BUY Top10을 PIT 원칙으로 기록·채움.
  - `record_signals()`: 당일 스냅샷에서 신호 append (OR IGNORE — 한 번 기록 후 수정 없음)
  - `fill_entry_prices()`: market_ohlcv에서 entry_open / kospi_entry_open 채움
  - `fill_alphas()`: alpha_5d / alpha_10d / alpha_20d / alpha_60d 윈도우별 채움
  - `summary()`: 현황 요약
- `scripts/record_oos.py`: 독립 실행 스크립트 (--date / --fill-only / --summary)
- OOS 첫 기록: 2026-09-23 (5종목), 2026-09-29 (9종목) — oos_signals 테이블

---

## v8.6.0 (2026-09-23)

### Philosophy

Engine Freeze 해제. Supply 5개 연구(Component Role Attribution / Counterfactual / Conditional / Horizon IC / Event Study) 전 방향 기각 근거로 supply_score를 scoring에서 제거. 3-factor 구조(Valuation + Earnings + Risk)로 전환.

### Breaking Changes

- `normalized_score` 계산 기준 변경: `(earn+val+risk) / 75 * 100` (기존 `/ 100 * 100`)
- `available_max` 상한 75 (기존 100). 데이터 전부 있을 때 `normalized_score` 최대 100 유지.
- `ScoringResult.supply_score` 항상 0 반환 (deprecated, 필드 유지는 snapshot 하위 호환용)
- `COMPONENT_ORDER`: `("valuation", "earnings", "risk")` (supply 제거)
- `score_tracker._COMPONENT_KEYS`: supply_score 제거

### Changed

- `analyzer/scoring/scorer.py`: supply_comp 제거, available_max에서 supply 제외
- `analyzer/scoring/models.py`: COMPONENT_ORDER supply 제거
- `analyzer/tracking/score_tracker.py`: supply_score 추적 제거
- `tests/test_integration.py`: supply 관련 assertion 갱신 (44/44 pass)
- `CLAUDE.md`: Freeze 해제, v8.6.0으로 버전 업, 3-factor 구조 명시

### Calibration Result

Supply factor 연구 (2026-09-23 완료):
- Component Role Attribution: 5개 역할 모두 KPI 미달
- Supply Counterfactual: Promoted/Demoted 차이 없음
- Supply Conditional: SB selection artifact 확인
- Supply Horizon IC: 6년 전체 mean IC = -0.005 (모든 horizon)
- Supply Event Study: 이벤트 직전 패턴 없음 (|r| ≤ 0.037)

---

## v8.5.19 (2026-08-21)

> Engine Freeze 시작. 점수 공식 고정.

---

## [비점수 기능] 2026-09-22

### Changed

- `sell_checklist.py`: "등급 악화" 항목을 Critical → Context로 격하
  - 변경 전: `grade in (NEUTRAL, AVOID)` → `critical` alert → `verdict=RED` → "매도 검토"
  - 변경 후: `context` alert → "매도 판단은 Valuation/Risk 변화 기준으로 별도 확인"
  - 근거: Calibration v8.3.x (n=6,145) Supply component spread +0.31% — Supply-driven 등급 변화를 즉시 매도 근거로 쓰기에는 alpha 기여 미확인. 전체 score ρ=+0.316으로 점수 자체는 유효하나, 등급 변화와 매도 판단의 연결은 별도 검증된 조건이 생기기 전까지 차단.
  - 점수 공식·가중치·컴포넌트 변경 없음. Production freeze 범위 외 UI 로직 변경.
