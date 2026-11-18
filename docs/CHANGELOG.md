# CHANGELOG

버전 이력. 규칙 설명은 `CLAUDE.md` 참조.
v7.0.0 이전: 연-월 표기 / v7.0.0 이후: 연-월-일 표기

허용 섹션 헤더 (Snapshot Inspector CHANGELOG 파서가 이 목록을 사용):
`### Philosophy` `### Breaking Changes` `### Added` `### Changed` `### Fixed` `### Calibration Result`
→ 이 목록 외 헤더 사용 시 파서가 해당 버전 요약을 추출하지 못할 수 있음.

작성 규칙:
- 각 섹션의 **첫 번째 bullet**이 Snapshot Inspector Diff 툴팁에 버전 설명으로 표시됨.
- 해당 버전에서 가장 중요한 변경 사항을 첫 줄에 작성할 것.

---

## v8.5.20 (2026-08-26)

### Fixed
- **스크리너 snapshot 분리** (`pages/6_screener.py`)
  - 스크리너 실행 시 `bt_snapshot.save()` 호출 제거.
  - 스크리너 결과는 기존대로 `calibration_snapshots/`에만 저장.
  - `backtests/snapshots/`는 Morning Run 전용으로 분리 — ranking_stability 분석 시 데이터 혼용 방지.
- **ENGINE_VERSION 관리 방식 변경** (`constants.py`)
  - 기존: CHANGELOG.md 파싱으로 자동 읽기 → 문서가 코드의 source of truth가 되는 구조적 문제.
  - 변경: `ENGINE_VERSION = "8.5.20"` 직접 하드코딩. 버전 업 시 이 값 수정 + CHANGELOG 추가.
  - 부수 수정: 2026-08-21~25 스냅샷 `rule_version` 8.5.6 → 8.5.19 소급 수정.

### Added
- **Morning Run 스냅샷 universe 필드 추가** (`morning_run.py`)
  - `universe="WATCHLIST"`, `universe_label="워치리스트 ACTIVE/REVIEW"` 고정값으로 전달.
  - 기존에는 `None`으로 저장되어 날짜 간 groupby 불가. ranking_stability 분석 준비.
- **Earnings Regime 연구 필드 스냅샷 저장** — 관찰/연구 목적. valuation 및 ranking score에 영향 없음.
  - `earnings_ratio`, `earnings_regime`, `earnings_history_n`, `earnings_confidence`, `earnings_source_years`를 Morning Run snapshot에 저장.
  - 본 변경은 관찰/연구 목적이며 valuation 및 ranking score에는 영향을 주지 않음.
  - 신규 파일: `analyzer/data/earnings_history.py` — `build_earnings_regime_map()` (배치 fetch)
  - 스냅샷 Schema v8 (v7 → v8): 5개 필드 추가
    - `earnings_ratio`       : 당해 EPS_proxy / 정상화 EPS (과거 N년 중앙값)
    - `earnings_regime`      : NORMAL / ELEVATED / PEAK / INSUFFICIENT_DATA
    - `earnings_history_n`   : 정상화 EPS 계산에 사용된 유효 연도 수
    - `earnings_confidence`  : HIGH(≥5년) / LOW(3~4년) / INSUFFICIENT(<3년)
    - `earnings_source_years`: 실제 사용된 연도 목록
  - EPS_proxy = 연말 종가 / 연말 PER (pykrx, 12/30→12/29→12/28 fallback)
  - 배치 구조: 연도별 1회 API 호출 (KOSPI + KOSDAQ), 종목 수에 비례하지 않음.
  - Partial-market failure graceful degradation: KOSDAQ fetch 실패 시 KOSPI 데이터 유지.
    history 부족 종목(예: HMM)은 INSUFFICIENT_DATA 처리 — PEAK/NORMAL 오분류 없음.
  - Regression test: `tests/test_earnings_history.py` 11 cases 추가.
  - 검증 목표: n≥100 누적 후 CYCLICAL-1 가설 검증
    (LOW_PER + PEAK earnings → Bear/Sideways에서 Alpha 열위인가?)

---

## v8.5.19 (2026-08-21)

### Changed
- **지주사 Valuation: PER 제외, PBR 상대평가만 적용** (`analyzer/scoring/valuation.py`, `analyzer/scoring/scorer.py`)
  - 신규 파일: `analyzer/holding_companies.py` — HOLDING_TICKERS (74개) 관리.
  - `score_valuation_hybrid`에 `is_holding: bool` 파라미터 추가.
  - 지주사(`is_holding=True`): `score = _pctl_score(pbr_rank)` 직접 반환 (0~25pt, PER contribution 제거).
  - PBR 데이터 부족 시 절대 PER fallback (기존 동작 유지).
  - 일반기업 로직 변경 없음 (regression test 6/6 통과).
  - 근거: HOLD-2(저PER 효과 비유의) + HOLD-3(저PBR ★+ CI[+0.9,+12.1]) + counterfactual replay
    (PER제거 → 지주사 Top30% 비중 0%; PBR포화율 53% vs 일반기업 50% → PBR cap 불필요 확인).
  - 이 변경은 "패널티 추가"가 아니라 "구조적으로 신뢰할 수 없는 PER을 신호에서 제외"하는 것.
  - Engine Freeze 의도적 해제 (2차): 위 연구가 해제 근거를 구성함.

### Calibration Result
- **Engine Freeze 재설정** — v8.5.19를 새 Calibration 기준점으로 확정.
  - 2026-06-09 ~ 2026-08-20 스냅샷: "v8.3.x era" 보존 (혼합 금지, 비교 자료로만 사용).
  - 2026-08-21~ 스냅샷: v8.5.19 Calibration 데이터 (새 기준점).
  - 해제 조건: n ≥ 100 (날짜 아님). 예상 해제 시점: 2027년 2월경.
  - Freeze 중 금지 항목에 HOLDING_TICKERS 변경 추가.

---

## v8.5.18 (2026-08-21)

### Changed
- **PBR 상대평가 가중치 활성화** (`config.py`)
  - `hybrid_pbr_rel_weight`: `0.0` → `1.0`
  - 근거: PBR-1 전체 유의성 (★+ CI [+3.7, +11.9]) + Replay Bear 국면 유의성 (w=1.0: ★+ CI [+0.7, +3.0])
  - `1.0`은 최적값 확정이 아닌 초기 검증 후보값. 최종 weight는 Calibration 데이터 누적 후 별도 검토.
  - 이 변경으로 Hybrid 점수 = (절대PER × 0.5 + 상대PER × 0.5 + 상대PBR × 1.0) / 2.0.
  - Engine Freeze 의도적 해제: n≈50 (2026-08-21 기준)으로 버전 혼재 비용 낮음. 변경 시점 명시 기록.

---

## v8.5.17 (2026-08-21)

### Fixed
- **스크리너 calibration snapshot 덮어쓰기 버그** (`pages/6_screener.py`)
  - 원인: 파일명을 pykrx 데이터 날짜(`as_of`)로 지정 → 장 시작 전 실행 시 전날 날짜로 저장되어 기존 파일 덮어쓰기 발생.
  - 수정: 파일명을 실행일(`date.today()`)로 변경. `market_data_date` 필드로 pykrx 데이터 날짜 보존.

---

## v8.5.16 (2026-08-21)

### Fixed
- **Dashboard 새 공시 섹터에 7일 초과 공시 표시** (`pages/1_dashboard.py`)
  - 원인: `get_filings(days=7)` 호출 시 6시간 파일 캐시가 `days=` 파라미터를 무시하고 30일치 반환.
  - 수정: `days=30`으로 가져온 뒤 `f.date >= 오늘-7일` 날짜 필터를 직접 적용.

### Added
- **Research Dataset 구축 스크립트** (`build_research_db.py`)
  - `database/research.db` 생성 — H1~H4 팩터 연구 공통 원본 데이터셋.
  - `research_stock_year`: 종목×연도 원본 관측치 (per, pbr, market_cap, p_start, p_end, return_pct, status_at_end).
  - `research_year_meta`: 연도별 KOSPI 수익률 / 시장 국면 (Bull/Sideways/Bear).
  - 분석 코드는 DB를 읽기 전용으로 사용. per_group 등 파생 변수는 분석 단계에서 계산.

### Changed
- **CLAUDE.md Section 10 추가** — Research Findings / H1 PER Backtest 결과 기록.
  - H1: 2015~2024년 KOSPI 섹터별 저PER 효과 검증 완료.
  - 통계적 유의 섹터: 비금속(★), 증권(△). 전기·전자 역효과(-6.8%)는 이익 사이클 국면 문제로 해석.
  - Engine Freeze 기간 점수 반영 금지. 연구 관찰로만 보존.
- **불필요 파일 정리**
  - `calibration_notes.md` 삭제 — 내용이 CLAUDE.md에 흡수됨.
  - `pytest-cache-files-g8rdlqr0/`, `archive/` 삭제 — 빈 디렉토리.

---

## v8.5.15 (2026-08-20)

### Fixed
- **Portfolio 20D 참고 성과 버튼 무반응** (`pages/2_portfolio.py`)
  - 원인: `portfolio_prices_date` 미설정 시 `portfolio_recent_perf`도 함께 삭제하는 버그.
  - 수정: `portfolio_prices_date`가 None인 경우는 무효화 대상에서 제외.
  - `_fetch_close_on` / `_fetch_kospi_on` DB-first로 변경 (market_db 우선 조회, pykrx fallback).
  - `_find_base_date()` 추가: KOSPI 인덱스가 있는 가장 오래된 날짜를 base_date로 사용.
  - 버튼에 명시적 key 추가 (`port_load_prices`, `port_calc_perf`).
  - `_calc_recent_perf` try/except 래핑 — 오류 시 에러 메시지 표시.

## v8.5.14 (2026-08-20)

### Fixed
- **Dashboard CRITICAL 공시 알림 과다 표시** (`pages/1_dashboard.py`)
  - 조회 기간 30일 → 7일로 단축. 오래된 공시는 이미 대응했거나 의미 없음.
  - 배너 최대 5건으로 캡. 초과 시 "외 N건 더 — Ticker 페이지에서 확인" 안내.

## v8.5.13 (2026-08-20)

### Added
- **워치리스트 자동 천이 — NEUTRAL×3 → DORMANT** (`analyzer/watchlist/transitions.py`, `backtests/snapshot.py`)
  - ACTIVE 종목이 NEUTRAL 3회 연속이면 자동 DORMANT 전환. 스냅샷 횟수 기준 (휴장일 무시).
  - `backtests/snapshot.py` — `get_neutral_streak(ticker, n=3)` 추가. 최근 n개 스냅샷 역방향 탐색.
  - `NEUTRAL_STREAK_THRESHOLD = 3` 상수로 기준 관리.
  - 천이 시 ReviewTrigger 기록: `"NEUTRAL N회 연속 → DORMANT"`.

### Fixed
- **DORMANT 상태 자동 천이 버그** (`analyzer/watchlist/transitions.py`)
  - 기존: DORMANT 종목이 NEUTRAL/AVOID 등급이면 → ACTIVE로 덮어써지는 버그.
  - 수정: DORMANT + NEUTRAL/AVOID → DORMANT 유지. DORMANT + BUY/STRONG_BUY → REVIEW.
  - REVIEW 상태 자동 천이 제외 (사람이 직접 변경).

### Added
- **Watchlist DORMANT 천이 사유 표시** (`pages/4_watchlist.py`)
  - DORMANT 섹션에 `⚫ 종목명 — NEUTRAL 3회 연속으로 자동 전환` 표시.

## v8.5.12 (2026-08-20)

### Added
- **Dashboard 매수 후보 개선** (`pages/1_dashboard.py`)
  - 종가 표시 추가: 최근 7일 역방향 탐색, `종가 16,530원 (1일 전)` 형태.
  - 추세 표시 추가: `mom_signal` 기반 한국어 레이블 (🔥 Strong / ✅ Positive / ⚪ Neutral / ⚠️ Negative).
  - 추천 이유 표시 추가: 수급/밸류 기반 우선순위 한 줄 요약 (외인·연기금 쌍끌이 매수 등).
- **Dashboard CRITICAL 공시 알림** (`pages/1_dashboard.py`)
  - 보유 종목 + ACTIVE 워치리스트 기준, 최근 30일 미확인 CRITICAL 공시(잠정실적/유상증자/CB) 배너 표시.
  - "공시 →" 버튼 클릭 시 Ticker 페이지 해당 공시로 직접 이동.
  - 지표 칩에 "미확인 공시" 추가 (5열 레이아웃).
- **Dashboard 정리** (`pages/1_dashboard.py`)
  - 빠른 종목 분석 섹션 제거.
  - Broker Status 섹션 제거.
  - Morning Run 변화 섹션 제거.

## v8.5.11 (2026-08-20)

### Changed
- **페이지 통합 — 12 → 9페이지** (번호 재정렬, 운용/분석/도구 그룹 유지)
  - `5_journal.py` 신규: `13_trade_journal` + `14_trade_detail` 통합. 거래 목록 내 인라인 상세 뷰.
  - `7_research.py` 신규: `6_performance` + `9_momentum_stats` + 캘리브레이션 리포트 탭 통합.
  - `8_snapshots.py` 신규: `7_snapshot_browser` + `12_snapshot_inspector` 탭 통합.
  - 단순 번호 변경: `4_ticker→3`, `5_watchlist→4`, `8_screener→6`, `11_scoring_guide→9`.
  - 구 파일 10개 삭제: `4_ticker`, `5_watchlist`, `6_performance`, `7_snapshot_browser`, `8_screener`, `9_momentum_stats`, `11_scoring_guide`, `12_snapshot_inspector`, `13_trade_journal`, `14_trade_detail`.
  - `app.py` `_PAGE_META` 재작성: 번호 1~9 연속, 선언 순서 = 사이드바 순서.
  - 내부 `switch_page` 경로 전수 업데이트 (잔여 참조 0건 검증 완료).

### Fixed
- `pages/1_dashboard.py` — `4_ticker.py` 참조 6곳 → `3_ticker.py` 수정.

## v8.5.10 (2026-08-19)

### Added
- **Market Data Cache — 로컬 SQLite OHLCV DB** (`backtests/market_db.py`)
  - KRX 반복 호출 제거 목적. Performance / Calibration 페이지가 DB를 우선 조회하고, 없을 때만 KRX fallback.
  - `database/market_ohlcv.db` 생성. 테이블: `market_ohlcv (ticker, trade_date PK, open, high, low, close, volume, source, created_at)`.
  - KOSPI 인덱스는 `ticker='IDX:1001'`로 동일 테이블에 저장.
  - 기본 저장 정책: `INSERT OR IGNORE` — Alpha 재현성 보장 (기존 데이터 덮어쓰기 금지).
  - 명시적 정정 전용: `repair_batch()` (force overwrite).
  - 수집 함수: `collect_date(trade_date)` — 전체 시장 3 API 호출 (KOSPI + KOSDAQ + 인덱스).
- **Performance 페이지 DB-first 가격 조회** (`pages/6_performance.py`)
  - `_fetch_open` / `_fetch_close` / `_fetch_index_open` / `_fetch_index_close`: market_db 우선 조회, fallback 시 KRX 호출 + 결과 DB 저장.
  - KRX IP 차단 상황에서도 DB 데이터가 있으면 조회 성공.
  - 최초 조회(DB 미적재)는 여전히 KRX 사용. Morning Run 누적 후 자동 가속.
- **Morning Run 시장 가격 DB 수집 연동** (`morning_run.py`)
  - 스냅샷 저장 전 `market_db.collect_date(today)` 호출 — 전체 시장 OHLCV 3 API 호출로 당일 데이터 적재.
  - KRX 오류 시 경고 로그 후 스냅샷 저장은 계속 진행.
- **KRX Open API 통합** (`backtests/market_db.py`)
  - `collect_date()` 수집 우선순위: KRX Open API (공식, 차단 없음) → pykrx fallback.
  - Open API 엔드포인트: `stk_bydd_trd` (KOSPI) / `ksq_bydd_trd` (KOSDAQ) / `kospi_dd_trd` (지수).
  - 지수 필터: `IDX_NM == "코스피"` → `CLSPRC_IDX` / `OPNPRC_IDX`.
  - 인증키: 환경변수 `KRX_OPEN_API_KEY`.
  - 저장 시 `source='krx_open_api'` 로 구분.

### Fixed
- **`insert_batch` 반환값 버그** (`backtests/market_db.py`)
  - `SELECT changes()` → `SELECT total_changes()` 전후 차이 계산.
  - 기존: `executemany` 후 마지막 행 1개의 변경만 반환 (2763행 삽입해도 1 반환).
  - 수정: 실제 삽입 행 수 정확히 반환.

---

## v8.5.9 (2026-08-19)

### Fixed
- **Performance 페이지 Look-ahead Bias 수정** (`pages/6_performance.py`)
  - 기존: 종목 entry price = 스냅샷 날짜 종가 (look-ahead bias).
    CLAUDE.md 원칙 위반 — "추천 결정에 사용된 당일 종가를 entry price로 쓰지 않는다."
  - 수정: entry price = entry_date 시가 (시가 = 실제 매수 가능 첫 가격).
    `entry_basis`("same_day_open" / "next_day_open") 읽어서 entry_date 결정.
  - KOSPI 기준선도 동일하게 entry_date 시가로 통일 (`snap.kospi_close` → `_fetch_index_open(entry_date)`).
  - `_fetch_open()` / `_fetch_index_open()` 함수 추가 (forward-lookup, 시가 조회).
  - `_next_trading_day_approx()` 함수 추가.

### Changed
- **Performance 페이지 기본 날짜 변경** (`pages/6_performance.py`)
  - 기존: 가장 최근 스냅샷(= 오늘) 기본 선택 → "오늘 스냅샷입니다" 메시지 보고 직접 날짜 변경 필요.
  - 수정: 20일 이상 경과한 스냅샷 중 가장 최근 날짜를 기본 선택.

---

## v8.5.8 (2026-08-19)

### Added
- **Snapshot Schema 7 — Risk/Earnings raw 원본값 보존** (`backtests/snapshot.py`)
  - `ValidatedMarketData`에 이미 계산된 `risk`/`earnings` 원본값을 snapshot에 저장.
    추가 API 호출 없음. UI 없음. Score 영향 없음. Calibration ⏸.
  - 저장 필드 (`risk_` prefix — RiskData 원본):
    `risk_debt_ratio_pct` / `risk_quarters_with_loss` / `risk_dilution_pct` /
    `risk_volatility_pct` / `risk_earnings_quality_ratio`
  - 저장 필드 (`earnings_` prefix — EarningsData 원본):
    `earnings_quarters_met` / `earnings_large_miss_pct` / `earnings_revision`
  - 목적: 과거 신호 당시 "부채비율이 얼마였는가?", "Revision이 UP이었는가?" 등
    snapshot만으로 재현 가능하게 보존.
  - 원칙: Snapshot → Score 재계산 방향으로 사용 금지.
    Engine → ValidatedMarketData → (Score 계산 / Snapshot 저장) 단방향 유지.
- `SCHEMA_HAS_RISK_EARNINGS_RAW = 7` 상수 추가

---

## v8.5.7 (2026-08-19)

### Changed
- **Dashboard 정보 다이어트** (`pages/1_dashboard.py`)
  - 정보 계층 재배치: Morning Run → ⚡ 오늘 할 일 → 📄 새 공시 → 🔌 Broker → 🌅 Morning Run 변화 → 🔎 Global Analyze.
    이전: 오늘 할 일이 Global Analyze Bar 아래 3번째에 위치. 이후: 실행 버튼 바로 다음 전면 표시.
  - Global Analyze Bar 최하단 이동 (탐색 도구, 매일 사용 안 함).
  - 🌅 Morning Run 변화 → expander (기본 접힘). 변화 건수를 expander 제목에 표시.
  - 🔌 Broker Status: 연결 정상 시 expander 접힘(한 줄), 연결 이상 시 자동 펼침 — 기존 동작 유지, 위치만 이동.
- **Dashboard 삭제 항목**
  - `상태 요약 + Quick Actions` 블록 삭제 (스냅샷 날짜, ACTIVE/REVIEW 카운트, Portfolio/REVIEW 버튼 — 오늘 할 일과 중복).
  - `REVIEW 경고 banner` 삭제 (`st.warning` REVIEW 목록 — 오늘 할 일 🟡 섹션과 중복).
  - `Watchlist 현황` 테이블 삭제 (ACTIVE/REVIEW 전체 목록 — Watchlist 페이지 전용).
  - 불필요한 `st.divider()` 다수 제거.
- **사용하지 않는 import 정리** (변경으로 인해 orphan 된 것만)
  - `NAV_WATCHLIST`, `Grade`, `GRADE_EMOJI`, `_sort_key` 함수 제거 (Watchlist 테이블 삭제로 orphan).
- **점수 계산 로직 무변경** (UI/정보 계층 변경만)

---

## v8.5.6 (2026-08-18)

### Fixed
- **Universe Scan 전체 실패 버그 수정** (`pages/8_screener.py`)
  - `_resolve_trading_date()`에서 `get_market_cap`과 `get_market_ohlcv_by_ticker`를 같은 try 블록에 묶어
    후자가 예외를 던지면 전자의 성공 여부와 무관하게 `continue`로 넘어가던 문제 수정.
  - 두 호출을 별도 try 블록으로 분리 — `get_market_cap` 성공 시 거래일로 인정,
    `get_market_ohlcv_by_ticker` 실패 시 `ohlcv=None`으로 처리 (downstream에서 이미 None 체크함).
  - `lookback` 기본값 5 → 10으로 확대 (광복절+대체공휴일 등 연휴 클러스터 대응).
- **pykrx KRX 인증 누락** (`.env`)
  - pykrx 신버전이 KRX 로그인 세션을 요구하도록 변경됨.
    Streamlit 프로세스는 `load_dotenv()`로 환경변수를 로드하므로 `.env`에 `KRX_ID` / `KRX_PW` 추가.
    터미널에서는 셸 프로파일에서 로드되어 정상 동작했으나 Streamlit 프로세스에는 누락되어
    `시가총액 sum = 0` 반환 → Universe 로딩 전 날짜 범위 전부 실패.
- **업종 분류 전체 실패 버그 수정** (`analyzer/data/krx.py`)
  - pykrx `get_market_sector_classifications(market="ALL")` 호출 시 내부에서 `KeyError: '종가'` 발생.
    pykrx 신버전 버그 — `market="ALL"` 시 응답 DataFrame에 "종가" 컬럼 없음.
  - `_fetch_sector_df()` 헬퍼 추가, `prefetch_all_sectors()` / `get_sector()` 모두
    KOSPI → KOSDAQ 순서로 개별 호출 후 결과 합산하도록 변경.
  - 이전: 전체 sector=None → Calibration 산업 분류가 "홀딩스" / "Other" 두 가지만 표시.
    이후: 정상 업종명 저장 → Morning Run 재실행 시 실제 업종별 분류 복원.

### Changed
- `constants.py` ENGINE_VERSION → `"8.5.6"` (점수 계산 로직 무변경 — 인프라 버그 수정만)

---

## v8.5.5 (2026-07-03)

### Fixed
- **`portfolio_prices` TTL 날짜 무효화** (`pages/2_portfolio.py`)
  - 날짜가 바뀌면 `portfolio_prices` / `portfolio_prices_date` / `portfolio_recent_perf` 자동 삭제.
    이전: TTL 없음 → 장중 새 세션에서도 전일 현재가로 수익률 계산.
    이후: `portfolio_prices_date != today`이면 세션 진입 시 즉시 무효화.
- **`NavigationRequest` 패턴 도입** (`analyzer/navigation.py` 신규 파일)
  - 구 session_state 개별 nav 키 6개 (`ticker_goto`, `filing_highlight`, `trade_detail_id`,
    `perf_snap_date`, `watchlist_filter`, `_trade_detail_id_persist`) → 단일 `navigation_request` dict.
  - `nav_to(target, ...)` / `consume_nav(expected_target)` 공개 API.
  - 수정 파일: pages/1, 4, 5, 6, 7, 8, 13, 14 — 모든 setter/receiver 마이그레이션 완료.
  - `pages/14_trade_detail.py` `_trade_detail_id_persist` band-aid 완전 제거.
    `_current_trade_id` (page-scoped persist key)로 교체 → rerun 생존, 컨텍스트 유실 없음.

### Changed
- `constants.py` ENGINE_VERSION → `"8.5.5"` (단, 점수 계산 로직 무변경 — 인프라 개선만)

---

## v8.5.4 (2026-07-03)

### Fixed
- **코드베이스 전체 감사 19건 일괄 수정** (Codebase Audit Pass)
  - `_name()` 중복 제거: `display_name()` / `rel_time()` 공통 헬퍼를 `analyzer/data/krx.py`에 통합.
    pages/1, 2, 4, 5, 6, 7, 8, 9, 13, 14 에서 로컬 `def _name()` 삭제 및 공통 함수 사용.
  - `get_dart_api_key()` 공개 래퍼 추가 (`analyzer/data/dart.py`) — `_load_api_key()` 직접 호출 제거
  - `CALIBRATION_CANDIDATES` 상수를 `constants.py`로 이동 — `pages/4_ticker.py` 로컬 셋 삭제
  - `momentum_tracker.py` 공개 함수 추가: `get_log_dir()` / `load_raw_log()` — pages/9 프라이빗 임포트 제거
  - `db.py _migrate()` 중복 elif 브랜치 제거 (dead code)
  - `analyzer/scoring/risk.py` interest_coverage 브랜치에 `# DISABLED` 주석 추가 (데이터 소스 미확보)
  - `pages/7_snapshot_browser.py` Quick Actions에서 삭제된 `3_snapshot_compare.py` 참조 제거
  - `pages/8_screener.py` 베어 `except: pass` 5곳 → `logging.warning()` 교체
  - `pages/1_dashboard.py` 베어 `except: pass` → `logging.warning()` 교체 + `import logging` 추가
  - `pages/14_trade_detail.py` 베어 `except: pass` → `logging.debug()` 교체 + `import logging` 추가
  - `pages/6_performance.py` ThreadPoolExecutor `as_completed()` timeout=30 추가 (pykrx hang 방지)
  - `pages/13_trade_journal.py` TradeRecord `engine_version` — `ENGINE_VERSION` 상수 대신 스냅샷 `rule_version` 사용
  - `pages/1_dashboard.py` Morning Run — 모멘텀 오버레이 계산 후 `mom_map` 포함해서 bt_snapshot.save() 호출
  - `pages/14_trade_detail.py` Navigation 컨텍스트 유실 수정 — `_trade_detail_id_persist` 키로 rerun 생존

### Changed
- `constants.py` ENGINE_VERSION → `"8.5.4"` (단, 점수 계산 로직 무변경 — 버그 수정 및 인프라 개선만)

---

## v8.5.3 (2026-07-03)

### Added
- **Trade ↔ Snapshot ↔ Performance 양방향 연결**
  - Snapshot Browser (`pages/7_snapshot_browser.py`)
    - 상세 테이블에 "거래" 컬럼 (📓 배지) — 거래 기록이 있는 종목 시각적 표시
    - Quick Actions 하단: 해당 스냅샷에 포함된 거래 기록 종목 selectbox + "Trade Detail 열기" 버튼
  - Performance (`pages/6_performance.py`)
    - 결과 테이블에 "거래" 컬럼 (📓 배지) — 거래 기록이 있는 종목 표시
    - CSV 다운로드 아래 "거래 연결" 섹션: 해당 성과 결과에 포함된 거래 기록 종목 목록 + 개별 "열기" 버튼 → Trade Detail 이동
  - 설계 원칙: 모든 연결은 `session_state["trade_detail_id"]` + `st.switch_page()` 패턴 — 기존 Navigation 아키텍처와 동일
- **Trade Detail Alpha 임시 계산** (`pages/14_trade_detail.py`)
  - 현재: pykrx 즉시 계산 (`@st.cache_data(ttl=3600)`)
  - 향후: `performance/*.json` 조회로 교체 예정 (Single Source of Truth 원칙 — CLAUDE.md Performance Batch 참조)
  - SELL 거래는 미계산 (BUY 연결 없이 realized alpha 불가 — v9 Broker Sync 이후 대응)

---

## v8.5.2 (2026-07-03)

### Added
- **Trade Detail — Performance 섹션** (`pages/14_trade_detail.py`)
  - BUY 거래 대상, `filled_at` + `filled_price` 기준으로 20D / 60D / 120D Alpha 계산
  - Alpha = 종목 수익률 − KOSPI 수익률 (pykrx 거래일 기준, Calibration과 동일 방식)
  - 윈도우 미경과 시 "계산 중" 표시. 데이터 없음(상장폐지 등) 시 명시적 안내.
  - `@st.cache_data(ttl=3600)` — 시세 재호출 최소화
  - `delta` 표시: "수익률 X%  vs KOSPI Y%" (수익률과 벤치마크 동시 확인)
  - SELL 거래: 미계산 (BUY 연결 없이 realized alpha 산출 불가 — v9 Broker Sync 이후 대응)

---

## v8.5.1 (2026-07-03)

### Added
- **Trade Detail 페이지** (`pages/14_trade_detail.py`)
  - Trade Journal 목록에서 "열기" → `session_state["trade_detail_id"]` → `st.switch_page()` 네비게이션
  - **Decision Context (Immutable)**: `score_snapshot` 에서만 렌더링. 엔진 재계산 없음.
    - Score / Grade / Valuation / Earnings / Risk / Supply / EQ Band
    - 매수·매도 이유 (레이블 표시), 체결가·수량·합계, 메모
    - Snapshot Gap 색상 표시 (🟢🟡🟠🔴)
  - **Current Status (Latest Official Snapshot)**: 최신 Morning Run 스냅샷 조회 전용.
    - Decision Context 대비 점수 변화 (▲/▼ delta)
    - 스냅샷 날짜 + Age (🟢오늘 / 🟡N일 전 / …)
    - 종목이 최신 스냅샷에 없으면 "⚪ Latest Snapshot unavailable" 표시
  - **Review**: verdict radio + 메모 입력 → DB 즉시 반영 (`reviewed_at` 기록)
  - **History**: Created / Review Added / Last Updated 3-line audit log
  - **Related**: Ticker / Snapshot Browser / Performance 원클릭 이동
- **Trade Journal 목록** (`pages/13_trade_journal.py`)
  - 데이터프레임 → 인터랙티브 행+버튼 레이아웃으로 변경 (행마다 "열기" 버튼)
  - BUY/SELL 저장 성공 피드백: `st.success()` → `st.toast()` 교체 (rerun 넘어가는 알림)
- **buy_reasons 구조화** (`analyzer/trade_journal/models.py`)
  - `thesis` 자유입력 → `BuyReason` enum 멀티셀렉트 교체
  - `BUY_REASON_LABELS`: STRONG_BUY 진입·저PER·저PBR·고ROE·외인/기관 순매수·쌍끌이·업황회복·배당·모멘텀·기타
- **종목명 입력** (`pages/13_trade_journal.py`)
  - BUY/SELL 폼: 6자리 코드 입력 → 종목명 검색으로 변경. 동명 종목 다수 시 코드 재입력 안내.

### Changed
- `app.py` `_PAGE_META`에 `14: Trade Detail` 추가 (운용 그룹, Trade Journal 하위)

---

## v8.5.0 (2026-07-03)

### Philosophy
Signal Quality → Decision Quality 전환의 시작.
기업을 평가하는 시스템에서 사용자의 투자 의사결정을 기록하고 학습하는 시스템으로.

### Added
- **Trade Journal** (`analyzer/trade_journal/`, `pages/13_trade_journal.py`)
  - SQLite 기반 거래 기록 (`data/trade_journal.db`) — 조회(Query) 중심 설계
  - `TradeRecord` 스키마: `trade_id`(UUID 영구 불변) + `broker_trade_id`(체결번호, 후에 자동 채움)
  - `source`: `"manual"` | `"toss_api"` — 수동·자동 혼합 구조. Toss Orders API 지원 시 enrich
  - `score_snapshot`: JSON 캡처 — 당시 전체 점수 구조(score/valuation/earnings/risk/supply/eq_band) 보존
  - `engine_version` 기록 — 버전별 점수 의미 구분 (v8.3.5의 91점 ≠ v9.2의 91점)
  - `buy_reasons` / `sell_reasons` / `memo` / `verdict` / `review_note` — 의사결정 맥락 저장
  - SELL `reason` 구조화: `SellReason` enum (STRONG_BUY 이탈·목표가 도달·Thesis 훼손·리밸런싱 등)
  - `snapshot_date` + `snapshot_gap_days` (🟢🟡🟠🔴) — Snapshot Freshness 일관 적용
  - UI 4탭: 거래 목록 / BUY 기록 / SELL 기록 / Review

---

## v8.4.3 (2026-07-03)

### Added
- **BrokerHealth 상태 추적** (`analyzer/broker/cache.py`)
  - `BrokerHealth`: `last_success` / `last_failure` / `consecutive_failures` / `last_error` 필드
  - `get_holdings()` 성공/실패마다 자동 갱신
  - 상태 분류: 🟢 Connected / 🟡 Warning (1~2회 실패) / 🔴 Error (3회+) / ⚪ 미확인
  - `health_info()` — 외부 조회용
- **Dashboard Broker Status 패널** (`pages/1_dashboard.py`)
  - `BROKER` 설정 시 Dashboard 상단에 `🔌 Broker` expander 표시
  - 상태·마지막 동기화·캐시 잔여·보유종목 수 4-컬럼 메트릭
  - 오류 시 자동 expanded, 정상 시 collapsed
  - "🔄 새로고침" 버튼 — Dashboard에서 직접 force refresh 가능

### Changed
- Dashboard `_held` — BROKER 모드에서 `portfolio.positions` 기준으로 변경 (positions.json 혼용 제거)

---

## v8.4.2 (2026-07-03)

### Added
- **Holdings Cache** (`analyzer/broker/cache.py`)
  - 5분 TTL 인메모리 캐시 — Portfolio 반복 진입 시 Toss API 재호출 없음
  - `Portfolio → Cache → TossProvider` 구조: Provider는 API만, Cache는 동기화 정책만 담당
  - `get_holdings(provider, force=False)` — TTL 내 캐시 반환, 만료 시 자동 재조회
  - `cache_info()` — 동기화 시각·잔여 TTL 조회용 (Dashboard 상태 패널 대비)
- **Force Refresh** (`pages/2_portfolio.py`)
  - Portfolio 상단 "🔄 새로고침" 버튼 — 캐시 무시 후 즉시 API 호출
  - 마지막 동기화 시각·경과 시간·캐시 잔여 시간 caption 표시

### Changed
- `portfolio_loader.load(force_refresh=False)` — force_refresh 파라미터 추가 (하위 호환)

---

## v8.4.1 (2026-07-03)

### Fixed
- **Toss API 응답 구조 불일치 수정** (`analyzer/broker/toss.py`)
  - 계좌 목록: `"accounts"` → `"result"` (배열)
  - 보유종목: `"holdings"` → `"result.items"` (중첩 객체)
  - 두 오류 모두 "브로커 연동 실패" 화면으로 이어지던 버그

### Added
- **브로커 모드 현금 잔고 UI** (`analyzer/portfolio/cash.py`, `pages/2_portfolio.py`)
  - Toss API v1 현금 잔고 조회 미지원 → `cash.json` 파일로 수동 관리
  - Portfolio 페이지 하단 "💰 현금 잔고" 섹션에서 입력·저장
  - 저장 시 `cash.json`에 영구 보존, 재시작 후에도 유지
  - `BROKER` 환경변수 설정 시에만 노출

---

## v8.4.0 (2026-07-03)

### Added
- **Broker Integration 레이어** (`analyzer/broker/`)
  - `BrokerProvider` Protocol: `holdings()` / `trades()` + `supports_*()` Capability 인터페이스
  - `TossBrokerProvider`: 토스증권 Open API 구현체
    - `holdings()` ✅ 보유종목 실시간 조회
    - `trades()` ⏸ stub — Toss API v1 미지원 (`status=CLOSED` → 400). 지원 시 구현 예정.
    - `supports_holdings()=True` / `supports_trades()=False` / `supports_orders()=False`
  - OAuth 2.0 Client Credentials 토큰 관리 (만료 5분 전 자동 갱신)
  - `TOSS_ACCOUNT_SEQ` 미설정 시 `GET /api/v1/accounts` 자동 조회
- **Portfolio Holdings Sync** (`analyzer/portfolio/loader.py`)
  - `BROKER=toss` 환경변수 설정 시 `positions.json` 대신 토스 계좌 실시간 데이터 사용
  - 브로커 API 실패 시 `positions.json` 폴백 없음 — 명시적 실패
  - `Holding → PortfolioState` 변환: `market_value` 기준 비중(weight_pct) 계산
- **`.env` 자동 로드** (`app.py`)
  - Streamlit 앱 시작 시 `.env` 파일을 `python-dotenv`로 자동 로드
  - 기존 환경변수 우선 (`override=False`)

---

## v8.3.6 (2026-07-03)

### Changed
- **Dashboard "오늘의 변화" → "🌅 Morning Run 변화"** 리포맷 (`pages/1_dashboard.py`)
  - 등급 경계 이벤트 우선 표시: 🟢 신규 STRONG_BUY → 🔴 STRONG_BUY 탈락 → 🔒 신규 AVOID → 🔵 신규 BUY
  - 각 항목에 "분석→" 버튼 추가 — 클릭 시 Ticker 페이지로 직접 이동
  - 주요 원인 (밸류/실적/리스크/수급 delta) 인라인 표시
  - 점수 delta (↗↘) 섹션 제거 — 경계 이벤트와 무관한 노이즈
  - Quick Action "🔍 Compare" 버튼 제거

### Breaking Changes
- **`pages/3_snapshot_compare.py` 삭제**
  삭제 이유: 점수 변화 자체는 투자 정보가 아님. 같은 +8도 Valuation +8과 Supply +8은 완전히
  다른 의미. 이 판단은 각 Component별로 Ticker 페이지에서 해야 한다.
  대체: Dashboard Morning Run 변화 (등급 경계 이벤트만) + Ticker 페이지 (상세 분석)

---

## v8.3.5 (2026-07-01)

### Added
- **Version Stamp + Calibration Health** (`pages/12_snapshot_inspector.py`, 버튼 없이 상시 표시)
  - Engine Version + 🔒 Frozen 표시 (Freeze 해제일 2026-10-01 이전)
  - Snapshots / 20D Results / 60D Results / Maturity 5-metric 헤더
  - Maturity: 🔴 Collecting (20D<30) / 🟡 Early (20D≥30) / 🟢 Mature (전 윈도우 ≥100)
  - `get_calibration_health()` (`backtests/calibration.py`)
- 나중에 v9로 교체 시 ρ 추이 비교 가능: "v8.3.x ρ=0.42 → v9.0 ρ=0.51"

---

## v8.3.4 (2026-07-01)

### Philosophy
**Engine Freeze 선언 (2026-07-01 ~ 2026-10-01)**
점수 체계를 3개월 고정. 버그 수정·비점수 기능만 허용.
이유: Calibration 데이터가 여러 버전에 걸치면 "무슨 버전이 성과를 냈는지" 알 수 없다.
v8.3.x가 검증 대상. 2026-10-01 이후 Calibration 결과 보고 다음 액션 결정.

### Added
- **④ Component Calibration** (`backtests/calibration.py` + 인스펙터 ④ 섹션)
  `build_component_report(window)` — Valuation / Earnings / Risk / Supply 각각
  High(≥중앙값) vs Low(<중앙값) 그룹 Alpha 비교. Spread = High − Low.
  "실제로 어떤 컴포넌트가 Alpha를 만드는가?" 직접 검증.
- **Engine Health KPI — ρ (Score vs Alpha 상관계수)**
  `calc_score_alpha_correlation(window)` — Pearson ρ.
  ρ > 0.2 = ✅ Positive / ρ ≤ 0.05 = ⚠ 거의 없음.
  Calibration 섹션 하단에 상시 표시.

---

## v8.3.3 (2026-07-01)

### Added
- **Calibration Report 3섹션 완성** (`pages/12_snapshot_inspector.py`)
  ① Industry Type — 업종별 n / Avg Score / SB% / 20D Alpha / Status
  ② Score Band — 95~100 / 90~94 / 85~89 / 80~84 / <80 구간별 동일 구조.
     핵심 KPI: "점수가 높을수록 Alpha가 실제로 큰가?" 검증용.
  ③ Factor: EQ Band — GOOD/WARNING/POOR/VERY_POOR별 Alpha 검증.
     EQ Penalty가 실제 성과 차별화에 기여하는지 확인.
- **4단계 Calibration Status** (`backtests/calibration.py`)
  ⏳ Collecting = alpha 없음 / 📊 Small Sample = n < 30 /
  ✅ Validated = n ≥ 100 + alpha ≥ 0 / ⚠ Review = n ≥ 30 + alpha < 0
- **`build_score_band_distribution` / `build_score_band_report`** — Score Band 집계
- **`build_factor_distribution` / `build_factor_report`** — 팩터 제네릭 집계
  factor_key 파라미터로 eq_band, mom_signal 등 어떤 필드도 분석 가능.

---

## v8.3.2 (2026-07-01)

### Added
- **Industry Type Calibration Report** (`pages/12_snapshot_inspector.py` 하단)
  - "📊 Calibration 조회" 버튼 클릭 시 업종별 분포 + 20D Alpha 집계 표 표시.
  - 성과 데이터 없을 때: `n (스냅샷)` 열만 채워진 준비 중 상태로 표시.
  - 성과 데이터 있을 때: Avg Score + 20D Alpha + Status(✅/⚠️) 자동 채워짐.
- **`analyzer/industry_type.py`** — Industry type 분류 모듈
  - 이름 기반 지주사 감지 우선 ("지주", "홀딩스", "holdings").
  - KRX/WICS 업종명 → 의미 있는 type 매핑 (부분 매칭 방식).
  - Bank / Insurance / Securities / Holding / Semiconductor / Auto / Steel 등 30+ 규칙.
- **`backtests/calibration.py`** — 집계 모듈
  - `build_snapshot_distribution(sector_map, name_map)` : 최근 스냅샷 기준 type별 n 카운트.
  - `build_alpha_report(sector_map, name_map, window=20)` : performance 데이터 기반 alpha 집계.
  - 저장된 sector가 null이면 live pykrx sector_map으로 fallback.
- **Morning Run sector_map 전달** (`pages/1_dashboard.py`)
  - `prefetch_all_sectors(today)` 호출 후 `bt_snapshot.save(..., sector_map=sector_map)`.
  - 이후 Morning Run 스냅샷부터 `sector` 필드 실제 값으로 저장됨.

### Philosophy
  관찰(observation)을 데이터(evidence)로 바꾸는 파이프라인.
  "지주사가 너무 많이 올라온다"는 느낌을 → Holding Type avg_alpha n=152로 검증.
  패널티 적용 결정은 이 표가 n≥100 + 3개 윈도우 모두 채워진 후.

---

## v8.3.1 (2026-07-01)

### Added
- **Portfolio Reference Performance 섹션** (`pages/2_portfolio.py`)
  - "📊 20D 참고 성과" 버튼 추가 — 보유 종목 최근 ~20영업일(28 calendar days) 종가 기준 조회.
  - Equal-Weight 수익률 / KOSPI (동기간) / Relative Alpha — 3-metric 표시.
  - 종목별 상세 테이블: 20D 수익률, KOSPI, Alpha.
  - 헬퍼 함수: `_fetch_close_on`, `_fetch_kospi_on`, `_calc_recent_perf`.
  - **개념 구분 명시**: Reference Performance (현재) ≠ Actual Portfolio Performance (v9 예정).
    Reference = 동일비중 가상 보유 가정. 매수 시점·비중 무관. 종목 선택력 참고용.
    Actual = 거래 기록 기반 TWR/MWR. 거래 기록 구조 설계 후 v9에서 구현 예정.

---

## v8.3.0 (2026-07-01)

### Philosophy
**v8.5 Validate 단계 시작** — 가설 기반 시스템에서 증거 기반 시스템으로 전환.
엔진이 복잡해지는 게 아니라, 기존 엔진이 실제로 Alpha를 만드는지 데이터로 검증하는 단계.

### Added
- **`backtests/performance.py` — Alpha Pipeline (데이터 생성부)**
  - `calc_performance(snap_date, window)` : 스냅샷 기준 window 영업일 알파 계산.
    entry price = entry_date 시가 (Look-ahead Bias 방지), exit price = exit_date 종가.
    alpha = ticker_return − kospi_return (동기간)
  - `run_batch(window)` : 계산 가능한 모든 스냅샷 자동 처리 및 저장.
  - `list_calculable(window)` : exit_date < 오늘인 미계산 스냅샷 목록.
  - `load_all(window)` : 저장된 전체 결과 로드 — Calibration Report 입력 소스.
  - 결과 저장 위치: `backtests/performance/YYYY-MM-DD_w20.json`
  - `ALPHA_WINDOWS = [20, 60, 120]` — 이후 60D / 120D 확장 기준.

---

## v8.2.1 (2026-07-01)

### Added
- **Snapshot 헤더에 알파 기준 메타데이터 3종 추가** (Morning Run + Screener 공통)
  - `analysis_timestamp` : 실행 시각 (ISO 8601). 원본 기록 및 감사용.
  - `entry_basis`        : `"same_day_open"` | `"next_day_open"`. 알파 계산 진입가 기준.
  - `market_data_date`   : pykrx가 실제 반환한 KRX 데이터 날짜.
  - 판단 규칙: hour < 16 → same_day_open (전일 종가 데이터), hour ≥ 16 → next_day_open (당일 종가 데이터).
  - 같은 날 2회 실행 시 최초 실행 메타데이터 보존 — "기록 시 결정, 분석은 기록을 신뢰."
- **Screener Snapshot에 `sector` 필드 추가** — 종목별 KRX 업종명 저장 시작.
  섹터별 알파 분석(Sector × Alpha 테이블) 준비. 90일 이상 누적 후 분석 가능.

---

## v8.2.0 (2026-06-30)

### Philosophy
**Sell Checklist → Position Status** — "팔아라" 판정에서 "무슨 변화가 생겼는가" 상태 진단으로 전환.

기존 6개 신호 균등 가중치 구조의 핵심 문제: 점수 -20점과 외인 -1주가 동일한 1점. 대신 두 계층으로 분리:
- **Critical (구조적 변화)**: 엔진이 자동 판단하는 투자 논리 이탈 신호 (점수 하락, 등급 악화)
- **Context (시장 맥락)**: 맥락 정보. 판단의 근거이지 판정 주체가 아님.

### Breaking Changes
- `SellChecklist` / `SellCheckItem` → `PositionStatus` / `PositionCheckItem` (alias 유지로 하위 호환)
- `triggered_count` / `total_count` → `critical_alerts` / `context_alerts`로 의미 분리
- verdict 체계: `GREEN` / `YELLOW` / `RED` → `GREEN` / `WATCH` / `YELLOW` / `RED` (4단계)
- 헤더: `"2/6 신호 · 주의"` → `"🔴 구조 변화 1건 / 🟡 맥락 변화 2건 · 주의"`

### Changed
- **수급 신호**: `foreign_positive` boolean 단독 체크 → 4상태 분류 + raw 금액 표시
  - 🟢 쌍끌이 매수 (외인+, 기관+)
  - 🟡 외국인 단독 매수 (외인+, 기관−)
  - 🟡 기관 단독 매수 (외인−, 기관+) — 이전엔 "외인 순매도" 🔴로 경보
  - 🔴 동반 순매도 (외인−, 기관−)
  - 표시: raw 금액 "외인 −3.9억 / 기관 +10.1억" (snapshot의 won 단위 활용)
- **Risk Radar**: Critical 제거 → Context로 이동
  - "미실행" = 정보 없음 (⚪, 위험 신호 아님)
  - "HIGH" = Context alert (🔴), 다른 Critical 신호와 독립 판단
- **Portfolio 페이지**: "매도 체크리스트" → "Position Status", 구조적 변화 / 시장 맥락 섹션 분리
- **Dashboard**: WATCH verdict 추가 (확인 필요 섹션에 포함)

---

## v8.1.0 (2026-06-29)

### Added
- **EQ 연구 필드 (`eq_penalty`, `eq_band`)** — Score 분해 및 장기 알파 연구용
  - `ScoringResult.eq_penalty: int = 0` — 영업이익 신뢰도 감점 크기 저장 (음수 또는 0)
  - `ScoringResult.eq_band: str = ""` — EQ 분류 레이블 (`GOOD` / `WARNING` / `POOR` / `VERY_POOR`)
  - `eq_penalty_from_ratio()` / `eq_band_from_ratio()` 순수 함수 분리 (scorer·UI 양쪽 재사용)
  - `apply_sector_correction()` 두 브랜치 모두 `eq_penalty` / `eq_band` 보존
- **Snapshot Schema 5** (`backtests/snapshot.py`)
  - 엔트리에 `eq_band` / `eq_penalty` 추가 저장 (`""` → `None`, `0` → `None` 변환)
  - `SCHEMA_HAS_EQ_BAND = 5` 게이트 상수 추가
- **Screener 스냅샷 raw 분해 필드** (`pages/8_screener.py`)
  - `val_raw` = 순수 PER 점수 (EQ·Sector 보정 전)
  - `sector_adj` = 업종 상대평가 보정 크기
  - `eq_penalty` / `eq_band` / `val_final` 추가
- **Ticker Score 분해 테이블** (`pages/4_ticker.py`)
  - Valuation expander 내 `PER (raw) → EQ Adj → Sector Adj → Final` 행 표시
  - 조정 없는 종목(eq_penalty=0, val_score_abs=0)은 표시 생략
- **Ticker Quality Badge** (`pages/4_ticker.py`)
  - Valuation 메트릭 아래 `⚠ Low Earnings Quality` 배지 — expander 열기 전 가시화
  - 밴드별: VERY_POOR → "Low" / POOR → "Poor" / WARNING → "Warning" / GOOD → 배지 없음
- **Snapshot Inspector Financial Quality 분포** (`pages/12_snapshot_inspector.py`)
  - Research 섹션에 `eq_band` 밴드별 종목 수 표시 (GOOD / WARNING / POOR / VERY_POOR)
  - Schema 5 이전 스냅샷은 경고 표시

---

## v8.0.5 (2026-06-29)

### Changed
- **영업이익 신뢰도 보정 (Earnings Quality Adjustment)** — Score 엔진 품질 개선
  - `RiskData.earnings_quality_ratio` 필드 추가 (trailing 4Q 영업이익 합 / 당기순이익 합)
  - `_auto_risk()`에서 `op_quarters` / `ni_quarters` 연간 합산 후 자동 계산
  - `score_valuation()` / `score_valuation_hybrid()` 에 `earnings_quality_ratio` 파라미터 추가
  - 비율 기준 valuation 점수 보정: `< 0.2` → -5pt / `0.2~0.5` → -2pt / `0.5~0.8` → 경고(감점 없음)
  - 성호전자 사례 (op=75억, ni=909억, ratio=0.08): valuation 점수에서 -5pt 적용
  - 일회성 이익(파생상품·자산매각·환율평가)으로 인한 PER 왜곡을 자동으로 감지
- **이자보상배율 복합 조건** — Risk Score 신규 항목 (구조 추가, 데이터 소스 미확보)
  - `RiskData.interest_coverage` 필드 추가 (EBIT / 이자비용)
  - `score_risk()`에 체크 추가: `interest_coverage < 1.0 AND debt_ratio > 120%` → -3pt
  - **현재 항상 None** — 이자비용 데이터 소스 미확보. 데이터 추가 시 자동 활성화
- **Screener 스냅샷** `raw` 필드에 `eq_ratio`, `int_cov` 추가 (모멘텀 알파 연구용)

### Calibration Result
- 성호전자 기준: valuation 점수 최대 -5pt 적용 예상 → STRONG_BUY 임계값 기준 등급 변동 가능

---

## v8.0.0 (2026-06-26)

### Philosophy
"좋은 기업을 찾는다(v7.x)" → "좋은 기업을 이해한다(v8.x)"

핵심 원칙 확정: **"콘솔은 정보를 제공하고, 판단은 투자자가 한다."**

4축 역할 분리:
- Score → 기업의 질 평가 (변경 없음, 백테스트 가능)
- Risk Radar → 장기 비재무 리스크 탐지 (변경 없음)
- Market Context → 최신 사실·이벤트 제공 (신규, 해석 없음)
- Portfolio → 투자자가 정의한 규칙에 따른 행동 제안 (변경 없음)

### Breaking Changes
- ENGINE_VERSION 8.0.0으로 상향. Score 로직 변경 없음. 하위 호환 유지.

### Added
- **DART 공시 클라이언트** (`analyzer/data/dart.py`, NEW)
  - `get_corp_code(ticker)` — 종목코드 → DART corp_code 변환 (30일 캐시)
  - `get_recent_filings(ticker, days=30)` — 최근 공시 목록 (6시간 파일 캐시)
  - DART_API_KEY 미설정 시 graceful degradation (빈 리스트 반환)
- **공시 분류 + Seen 추적** (`analyzer/market_context/filings.py`, NEW)
  - `FilingCategory` — EARNINGS/DIVIDEND/BUYBACK/DILUTION/CONTRACT/PERIODIC/OTHER
  - 중요도: 🔴 RED(실적·증자) / 🟡 YELLOW(배당·자사주·계약) / 🟢 GREEN(정기보고·기타)
  - `classify_filing(report_nm)` — 키워드 기반 공시 자동 분류
  - `Filing` dataclass — icon, importance_emoji, date_display, dart_url 프로퍼티
  - `SeenTracker` — `data/seen_filings.json` 기반 읽음 상태 관리
- **Ticker 페이지 "📄 최근 공시" 섹션** (`pages/4_ticker.py`)
  - 모멘텀 섹션 아래, Watchlist 섹션 위에 삽입
  - 중요도 이모지 + 공시 분류 아이콘 + DART 원문 링크
  - 새 공시 🔵 표시, 섹션 렌더 시 자동 읽음 처리
  - DART_API_KEY 미설정 시 안내 메시지 표시
- **Dashboard 새 공시 알림** (`pages/1_dashboard.py`)
  - "⚡ 오늘 할 일" 하단에 보유 종목 새 공시(7일) 표시
  - 중요도 이모지 + "보기 →" 버튼으로 Ticker 페이지 이동
  - DART API 오류 시 try/except로 Dashboard 전체 보호

---

## v8.0.4 (2026-06-26)

### Added
- **`get_type_label(item_type)`** (`analyzer/market_context/filings.py`)
  - `_TYPE_SHORT_LABEL` dict 추가 — FilingType → 한국어 짧은 레이블 (잠정실적, CB발행 등)
  - `get_type_label(item_type: str) -> str` — 공개 함수, 아이콘과 함께 표시용

### Changed
- **Watchlist 공시 배지** (`pages/5_watchlist.py`)
  - 아이콘만 → `icon + short_label` 형식으로 변경 (예: `📈 잠정실적  ⚠ CB발행`)
  - Streamlit data_editor 셀 내 hover tooltip 미지원으로 레이블 병기 방식 채택
- **Dashboard 새 공시 미리보기** (`pages/1_dashboard.py`)
  - 최대 3건 미리보기 (type_icon + 종목명 + type_label + date)
  - 초과분 `st.expander("+ N건 더 보기")`로 접기
  - `_render_filing_row()` 헬퍼로 미리보기 / 더보기 렌더 중복 제거

---

## v8.0.3 (2026-06-26)

### Added
- **Dashboard 새 공시 섹션 개선** (`pages/1_dashboard.py`)
  - 대상 확장: 보유 종목 → 보유 + Watchlist ACTIVE/REVIEW 전체
  - Importance 기준 그룹핑: 🔴 CRITICAL N건  ·  🟡 IMPORTANT N건
  - 보유 종목은 🔵 dot으로 Watchlist와 시각 구분
  - INFO(정기보고, 기타)는 Dashboard에서 노출하지 않음
  - 표시 상한 6건 (CRITICAL 우선)
- **Watchlist 공시 배지 컬럼** (`pages/5_watchlist.py`)
  - ACTIVE/REVIEW 테이블에 "공시" 컬럼 추가
  - 7일 이내 IMPORTANT+ 공시의 type_icon 표시 (예: `📈 ⚠`)
  - 최대 3개, importance 내림차순 (CRITICAL 먼저)
  - `_load_filing_badges()` — 21600초(6h) 캐시, DART 파일 캐시와 동일 주기

---

## v8.0.2 (2026-06-26)

### Added
- **`analyzer/market_context/models.py`** (NEW) — Market Context 공통 모델 레이어
  - `ContextSource(str, Enum)` — DART / CLAUDE / NEWS / USER (출처별 UI 분기 기반)
  - `EventStatus(str, Enum)` — TODAY / UPCOMING / PAST (Dashboard 필터링 기준)
  - `FilingImportance(int, Enum)` — INFO=1 / IMPORTANT=2 / CRITICAL=3
    - `int` 상속으로 `sorted(items, reverse=True)` → CRITICAL 먼저 자동 정렬
  - `MarketContextItem` — `source`, `type_icon`, `status` 필드 추가
    - `is_active` property: TODAY·UPCOMING 여부 (Dashboard 표시 필터)
    - `source_icon` property: 출처 아이콘 (📄 DART / 📰 AI / 🌐 뉴스 / 👤 User)
    - `__lt__` 정의 → `importance` 기준 정렬
- **`analyzer/market_context/provider.py`** (NEW) — `MarketContextProvider(Protocol)`
  - 모든 데이터 소스(DART, UpcomingEvent, Claude, 뉴스)의 공통 인터페이스
  - `def fetch(ticker, **kwargs) -> list[MarketContextItem]`

### Changed
- **Market Context 모델 리팩토링** (`analyzer/market_context/filings.py`)
  - `FilingImportance(str, Enum)` 추가 — CRITICAL/IMPORTANT/INFO (기존 "RED"/"YELLOW"/"GREEN" 문자열 대체)
  - `FilingType(str, Enum)` 추가 — EARNINGS/DIVIDEND/BUYBACK/RIGHTS/CB/CONTRACT/REPORT/OTHER
    - 기존 `DILUTION` → `RIGHTS`(유상증자)와 `CB`(전환사채·BW·교환사채)로 분리
  - `MarketContextItem` dataclass 신설 — Filings·UpcomingEvents·미래 기능 전부 `List[MarketContextItem]`으로 처리
    - 필드: `title`, `date`, `item_type`, `importance`, `url`, `source_id`
    - 기존 `Filing.report_nm` → `title`, `rcept_no` → `source_id`, `dart_url` → `url`, `date_display` → `date`
  - `SeenTracker` — `source_id` 단위 추적으로 변경, `critical_unseen()` 메서드 추가
  - `get_filings()` — `list[MarketContextItem]` 반환 (하위 레이어 변경 없음)
- `pages/4_ticker.py` / `pages/1_dashboard.py` — 신규 필드명 반영

---

## v7.13.0 (2026-06-26)

### Added
- **Portfolio Health** (`analyzer/portfolio/health.py`, NEW)
  - `compute_health(portfolio, snap_map, rr_read_fn) -> PortfolioHealth` — 포트폴리오 종합 건강도
  - Health Score 구성: 평균점수(50%) + 등급품질(20%) + Risk Radar(20%) + 현금적정성(10%)
  - 등급: 80+ 🟢 양호 / 60+ 🟡 주의 / <60 🔴 점검 필요
  - 지표: avg_score, strong_buy_pct, cash_pct, avg_risk, avg_momentum
  - 경고 자동 생성: NEUTRAL/AVOID 보유, Risk HIGH 종목, 현금 부족(<5%), 섹터 집중도(>25%)
- **Portfolio 페이지 Health 카드** (`pages/2_portfolio.py`)
  - 총 노출 요약 앞에 삽입 — 평균점수, STRONG_BUY%, Risk, Momentum, 현금 한 줄 요약
  - 경고 조건 자동 표시
- **Dashboard Health 카드 + Action Link** (`pages/1_dashboard.py`)
  - "⚡ 오늘 할 일" 상단에 Portfolio Health 지표 표시
  - 각 Action 항목에 네비게이션 버튼 추가:
    - 🔴 매도 검토 → `Portfolio →` 버튼
    - 🟡 확인 필요 → `Portfolio →` 버튼
    - 🟢 매수 후보 → `분석 →` 버튼 (Ticker 페이지로 이동, session_state 전달)
    - 🔄 Risk Radar → `Portfolio →` 버튼

---

## v7.12.0 (2026-06-26)

### Added
- **Dashboard "⚡ 오늘 할 일" (Action Required 패널)** (`pages/1_dashboard.py`)
  - 앱 진입 즉시 오늘 행동을 4개 지표로 요약: 🔴 매도 검토 / 🟡 확인 필요 / 🟢 매수 후보 / 🔄 Risk Radar 갱신
  - 🔴 매도 검토: 보유 종목 매도 체크리스트 RED verdict (triggered 3+) — 해당 신호 목록 포함
  - 🟡 확인 필요: 보유 종목 매도 체크리스트 YELLOW verdict (triggered 2) — 해당 신호 목록 포함
  - 🟢 매수 후보: 스냅샷 STRONG_BUY + executable + 미보유 상위 3종목 (외인+/모멘텀+ 태그)
  - 🔄 Risk Radar 갱신: 보유 종목 중 캐시 없는 종목 → Portfolio 페이지 유도
  - 추가 API 호출 없음: 스냅샷 + sell_checklist + risk_radar 캐시만 사용
- **투자 운용 콘솔 완성 — Screener/Portfolio/Watchlist/Risk Radar/Score Tracker가 Dashboard로 통합**

---

## v7.11.0 (2026-06-26)

### Added
- **점수 변화 추적** (`analyzer/tracking/score_tracker.py`, NEW)
  - `ScoreDelta` — 두 스냅샷 간 컴포넌트별 델타 + 방향/원인 요약 불변 dataclass
  - `compute_delta(ticker, snapshot_dates, lookback_days=7)` — 단일 종목 델타
  - `batch_compute_deltas(tickers, snapshot_dates, lookback_days=7)` — 스냅샷 로드 2회로 최소화한 배치 버전
  - `ScoreDelta.explain(top_n=2)` — 가장 큰 컴포넌트 변화 + 등급 변화 요약 문자열
  - 방향 분류: ▲ UP (>+2p) / ▼ DOWN (<-2p) / ━ FLAT (±2p 이내)
- **Portfolio 페이지 "📊 점수 변화" 섹션** (`pages/2_portfolio.py`)
  - Sell Checklist 아래, 미보유 BUY+ 위에 삽입
  - 최근 스냅샷 vs lookback 7일 전 비교 — 현재·이전 점수, 변화, 주요 원인 테이블
  - 등급 변화 발생 시 노란 경고 메시지 표시

---

## v7.10.0 (2026-06-26)

### Added
- **매도 체크리스트** (`analyzer/rules/sell_checklist.py`, NEW)
  - `build_sell_checklist(ticker, snap_entry, snapshot_dates)` — 보유 종목별 6개 기준 자동 평가
  - 6개 기준: 점수 하락(이력 최고점 −15p+), 등급 악화(NEUTRAL↓), Risk Radar HIGH, 실적 약화(<15/25), 외인 순매도, 모멘텀 부정
  - 판정: 🟢 유지(0–1 신호) / 🟡 주의(2 신호) / 🔴 매도 검토(3+ 신호)
  - `SellCheckItem(label, triggered, detail)`, `SellChecklist(ticker, items, triggered_count, total_count)` 불변 dataclass
- **Portfolio 페이지 매도 체크리스트 섹션** (`pages/2_portfolio.py`)
  - 보유 종목 테이블 아래, 미보유 BUY+ 위에 삽입
  - 판정이 GREEN이 아닌 종목은 자동 펼침 (확장 상태)
  - 각 기준별 🟢/🔴 인디케이터 + 상세 사유 표시

---

## v7.9.0 (2026-06-26)

### Added
- **업종 내 Hybrid 밸류에이션 점수** (`analyzer/scoring/valuation.py`, `analyzer/scoring/scorer.py`)
  - `score_valuation_hybrid(per, pbr, peers)` — 절대 PER 50% + 업종 내 PER 퍼센타일 50%
  - 퍼센타일 점수표: 하위 10% → 25pt / 20% → 22pt / 33% → 18pt / 50% → 14pt / 67% → 10pt / 80% → 6pt
  - `SectorPeers(bucket, per_sorted, pbr_sorted)` — PBR 상대평가 구조 예비 탑재 (weight=0, 향후 활성화용)
  - 표본 n < 5이면 절대 PER 기준 fallback (소표본 업종 왜곡 방지)
  - `build_sector_peers_map(results, sector_map)` — scan 결과 전체에서 버킷별 분포 구축
  - `apply_sector_correction(ar, sector_map, peers_map)` — AnalysisResult에 Hybrid 보정 적용
- **KRX 업종 일괄 조회** (`analyzer/data/krx.py`)
  - `prefetch_all_sectors(as_of_date)` — 전체 종목 업종 1회 조회 → `_sector_cache` 일괄 저장
  - per-ticker 반복 API 호출 방지. screener post 단계에서 1회 호출.
- **논리적 업종 버킷 매핑** (`analyzer/scoring/sector_buckets.py`, NEW)
  - `krx_to_bucket(krx_sector)` — KRX 업종명 → 논리 버킷
  - 운송 통합: 해상/항공/육상운수 → "운송" (소표본 단독 버킷 문제 해결)
  - 금융 분리: 은행/보험/증권/기타금융 → 각 독립 버킷 (PER 구조 상이)
  - 지주사: "지주회사" → "지주사" 버킷
- **Screener post 페이즈 업종 보정 스텝** (`pages/8_screener.py`)
  - post 단계 1번: KRX 업종 pre-fetch → Hybrid 보정 → candidates 재구성 → grade_dist 갱신
  - 보정 실패 시 try/except로 graceful fallback (기존 절대 PER 결과 유지)
  - 보정 후 candidates는 Hybrid score 기준으로 재정렬
- **Snapshot Schema 4** (`backtests/snapshot.py`)
  - `sector`: KRX 업종명 — 업종 상대평가 연구 및 groupby 분석용
  - `val_score_abs`: 절대 PER 기준 원본 점수 — 6개월 후 Hybrid vs 절대 알파 비교용
  - `SCHEMA_HAS_SECTOR_RELATIVE = 4` 게이트 상수 추가
  - `has_sector_relative(snap)` 헬퍼 함수 추가
- **`ScoringResult.val_score_abs`** (`analyzer/scoring/models.py`)
  - Hybrid 보정 전 절대 PER val score 보존 (기본값 0 = 보정 없음)

### Changed
- **`score_valuation()` 한계 주석** 업데이트 → cross-sector 비교 부적합 경고 → Hybrid로 해결됨 명시
- **Hybrid 가중치 config화** (`config.py`)
  - `VALUATION.hybrid_per_abs_weight = 0.5` / `hybrid_per_rel_weight = 0.5` / `hybrid_pbr_rel_weight = 0.0`
  - 가중치 실험 시 코드 수정 없이 config만 변경 가능

### Calibration Result
- 시뮬레이션 기준 (2026-06-26, 309종목, 이름확인 135개):
  - STRONG_BUY: 21 → 18 (소폭 감소 — 의도된 변화)
  - 지주사 STRONG_BUY 비율: 11.1% → 0% ✅
  - 보험 STRONG_BUY 비율: 12.5% → 0% ✅
  - 기타 제조/서비스 STRONG_BUY 비율: 변동 없음 (6.2% 유지)
  - "정말 이상했던 애들만 내려간다" 철학 검증

---

## v7.8.1 (2026-06-26)

### Fixed
- **Screener 288/288 무한 로딩 hang 버그** (`analyzer/data/momentum.py`)
  - `batch_fetch_momentum()` 내부에서 `ThreadPoolExecutor` context manager + `as_completed()` 사용 시 pykrx 네트워크 hang이 `pool.shutdown(wait=True)` 를 영원히 block하는 문제
  - `concurrent.futures.wait(futures, timeout=120)` + `pool.shutdown(wait=False)` 패턴으로 교체
  - `_BATCH_TIMEOUT_SEC = 120` 상수 추가 — 모멘텀 배치 전체 타임아웃
  - 타임아웃 초과 시 완료된 항목만 반환, 미완료 스레드는 백그라운드에서 자동 종료

### Added
- **Screener "post" 페이즈** (`analyzer/screener_state.py`, `pages/8_screener.py`)
  - scan_job phase: `idle | scanning | post | done | error` ("post" 신규 추가)
  - `post` 상태: 모멘텀 오버레이 계산 중. UI에서 진행률 100% + "후처리 중..." 표시
  - `start_time` 필드 추가 — 전체 스캔 시작 시각 (time.time())
  - 10분(600초) 전체 타임아웃 — 초과 시 자동으로 error 상태 전환
- **`get_sector()` 함수** (`analyzer/data/krx.py`)
  - pykrx `get_market_sector_classifications()` 기반 KRX 업종 코드 조회
  - `_sector_cache` 인메모리 캐시 (세션 내 중복 API 호출 방지)
  - Risk Radar `scan()` 호출 시 sector 파라미터로 전달 — 업종 맥락 포함 분석
  - 조회 실패 시 `None` 반환 (graceful degradation)

---

## v7.8.0 (2026-06-17)

### Added
- **Risk Radar — confidence 필드** (`analyzer/risk_radar.py`)
  - `"confidence": "LOW|MEDIUM|HIGH"` — 출처 수·독립성 기반 근거 강도 평가
  - HIGH: 복수 독립 출처 확인 / MEDIUM: 1~2개 출처 / LOW: 출처 없음·일반론 기반
  - Ticker 페이지에 종합 리스크 옆 신뢰도 표시 (🟢/🟡/🔴 + 한국어 라벨)
- **Risk Radar — 캐시 버전 관리** (`analyzer/risk_radar.py`)
  - `RADAR_VERSION = "1.1"` 상수 추가. 캐시 파일에 `radar_version` 기록
  - 버전 불일치 시 자동 무효화 — 프롬프트 변경 후 구버전 캐시 오사용 방지
- **Risk Radar — 통계 추적** (`analyzer/risk_radar.py`, `pages/10_claude_stats.py`)
  - `data/risk_radar_stats.json`: 총 요청 / 캐시 히트 / API 호출 / 평균 응답시간 / 실패율
  - `10_claude_stats.py`를 "🤖 AI 사용 통계"로 리네이밍. Claude 분석 + Risk Radar 통계 동시 표시
  - 캐시 정리 / 통계 초기화를 두 서비스 독립적으로 분리
- **Risk Radar — 히스토리** (`analyzer/risk_radar.py`)
  - `data/risk_radar_history/{ticker}.jsonl` — API 호출 시 자동 append (주 1회 누적)
  - `load_history(ticker)` 공개 함수. 향후 `HIGH → LOW 전환 알파` 등 연구 데이터셋으로 활용
  - 통계 페이지에 히스토리 종목 수 / 레코드 수 표시
- **Watchlist 태그 확장** (`analyzer/watchlist/models.py`)
  - `GOVERNANCE_RISK = "governance_risk"` 🚨 — Risk Radar HIGH 종목 분류
  - `CLEAN = "clean"` 🟢 — Risk Radar LOW 종목 분류
  - Ticker 페이지: Risk Radar 결과 표시 후 Watchlist 등록 종목에 태그 변경 버튼 자동 노출

---

## v7.7.0 (2026-06-17)

### Added
- **Risk Radar — 비재무 리스크 탐지** (`analyzer/risk_radar.py`, `pages/4_ticker.py`)
  - Anthropic API + web_search 도구로 최근 뉴스·공시 기반 비재무 리스크 탐지
  - 4개 축 평가: 지배구조(governance) / 경영진(management) / 규제·소송(regulatory) / 업종(industry)
  - 종합 등급: LOW / MEDIUM / HIGH, 출처 URL/기사 목록 포함
  - 주간 캐시 (`CACHE_DAYS=7`). 캐시 키: ticker (점수·등급·엔진버전과 무관)
  - Ticker 페이지 [Risk Radar 실행] 버튼 — 모든 등급에서 사용 가능 (등급 제한 없음)
  - 금지사항: 점수 반영, 등급 변경, 매수/매도/보유 추천, 목표가/수익률 예측
  - `claude-sonnet-4-6` 사용 (claude_advisor의 haiku보다 웹서치 품질 우선)

---

## v7.6.0 (2026-06-16)

### Added
- **Snapshot Schema 3 — 밸류에이션 raw 필드 추가** (`backtests/snapshot.py`)
  - `per`, `pbr`, `roe`, `div_yield` — 팩터 분석용 원본값. 과거 재조회 불가, 스냅샷에만 존재
  - `foreign_ownership_pct` — 외인 순매수 방향 플래그 외에 지분율 절댓값 보존
  - 헤더 필드: `n_total` (병합 후 실제 entries 수), `universe` (안정 식별자: `KOSPI_500B` 등), `universe_label` (표시용: `KOSPI 5000억+`)
  - Schema Migration Helper: `SCHEMA_HAS_INSTITUTION_DETAIL`, `SCHEMA_HAS_VALUATION_RAW` 상수 + `has_institution_detail()`, `has_valuation_fields()`, `snap_schema()` 함수
- **Snapshot Inspector 업데이트** (`pages/12_snapshot_inspector.py`)
  - Evolution / Diff / 커버리지 테이블에 PER / PBR / ROE / 배당 / 외인지분율 커버리지 표시
  - 구형 Schema 경고 메시지를 Schema 버전별 누락 필드 설명으로 세분화 (매직 넘버 → 상수 참조)
  - 드릴다운 KOSPI 기준 metric에 Universe 툴팁 표시

### Fixed
- **`n_total` 병합 버그** — 같은 날 두 번 저장 시 `len(results)` (마지막 스캔 수) ≠ `len(entries)` (병합 총계) 불일치 가능성. 병합 완료 후 `len(merged_entries)`로 산출하도록 수정

---

## v7.5.1 (2026-06-13)

### Fixed
- **`foreign_positive` / `institution_positive` 카운트 버그** — `_count()` (non-null 기준) → `_count_val(..., True)` 로 통일. `False` 저장 시 오카운트 방지
- **Snapshot Evolution `kospi_close` 버그** — 숫자값을 count로 오용해 `~819700%` 표시되던 문제. scalar 플래그(`is_scalar=True`)로 분기 처리
- **Diff Health Score `delta_color`** — `"normal" if >= 0 else "inverse"` → `"normal"` 고정. 점수 하락 시 녹색으로 잘못 표시되던 버그

### Added
- **`fp_and_pp` 실제 교집합 필드** — `foreign_positive AND pension_positive` 동시 만족 종목 수. Research Q2 외인+연기금 조합 표본 정확도 개선
- **Research Readiness 3단계 판정** — ✅ 가능 (n≥threshold) / 🟡 가능(표본 부족, 진행률 바) / ❌ 불가
- **`_RESEARCH_THRESHOLDS` dict** — 연구 유형별 최소 표본 기준 분리 (`momentum` / `institution` / `combo`)
- **`_HEALTH_THRESHOLDS` dict** — `_MOM_HEALTH_THRESH` 단일 상수 대체. Health 기준값 실험 시 딕셔너리 한 곳 수정으로 반영
- **`_build_research_checks(info, n)` 함수** — Research Readiness 항목 생성 로직 분리. 신규 연구 유형 추가 시 리스트만 확장
- **Diff 빠른 선택 버튼** — 최근 / 1주 / 1개월 / 3개월 radio 버튼으로 기준 날짜 자동 세팅
- **Diff Engine 버전 CHANGELOG 연동** — 버전 변경 시 해당 버전 첫 Added 항목을 툴팁으로 표시
- **Diff 등급 분포 비율 추가** — `10 → 20` → `10 (3%) → 20 (6%)`. 종목수 다른 스냅샷 비교 시 상대 비중 확인 가능
- **CHANGELOG 파서 `_CHANGELOG_SECTIONS` set** — `### New Features` 같은 비허용 헤더 방지. CHANGELOG.md 허용 목록과 동기화
- **Health Score 툴팁** — 드릴다운 상단 `Health Score` metric에 `help=` 채점 기준 표시

### Changed
- **Snapshot Evolution 정렬** — 오름차순 → 최신 날짜 우선 (내림차순)
- **Coverage bar / `_readiness()` bar 상한 방어** — `min(20, ...)` / `min(10, ...)` guard 추가. 계산 버그로 101% 들어와도 바 길이 초과 없음

---

## v7.5.0 (2026-06-13)

### Added
- **Snapshot Inspector 페이지** (`pages/12_snapshot_inspector.py`) — 스냅샷 연구 DB 품질 확인
  - **전체 요약 테이블** — 날짜 / 버전 / Schema / Health Score / Alpha 가능 여부 / 필드 커버리지
  - **Health Score** (0~100) — Schema 최신 +20 | Engine ≥ 7.2.0 +20 | 시총 100% +20 | 거래대금 100% +20 | MOM ≥ 80% +20. 드릴다운 헤더에 ℹ 툴팁 포함
  - **Snapshot Evolution** — 날짜별 × 15개 필드 매트릭스 (✅/➖/~X%). `kospi_close`는 scalar이므로 별도 처리
  - **Snapshot Diff** — 두 날짜 선택 → Health delta / Schema·Engine 변화 / 필드 커버리지 증감 (▲▼) / 등급 분포 변화
  - **Research Readiness** — Alpha 계산 / 모멘텀 검증 / 기관 세부 분석 / 외인+연기금 조합 / 시총·거래대금 팩터 5개 연구 가능 여부 표시
  - **커버리지 정렬** — 커버리지 % / 필드명 선택 정렬, 100% 미만만 필터 체크박스

---

## v7.4.0 (2026-06-13)

### Added
- **Performance 페이지 Alpha 추가** (`pages/6_performance.py`)
  - `_fetch_index_close()` — KOSPI 종가 fetch (동일 TTL 캐시 공유)
  - KOSPI return 계산: `snap.kospi_close` (스냅샷 기준일) → horizon_date KOSPI 종가
  - `alpha_pct = return_pct − kospi_return` 각 종목 계산
  - Primary KPI = 알파, Secondary = 수익률 (CLAUDE.md 원칙 반영)
  - 등급 헤더: `알파 avg | 수익률 avg  승률 n/N`
  - 종목 테이블: 알파 컬럼 추가, 알파 기준 정렬
  - KOSPI 기준선 caption: `KOSPI +X.X% (동기간) | 8100 → 8200`
  - Graceful degradation: `kospi_close` 없는 구형 스냅샷(schema 1)은 수익률만 표시

---

## v7.3.1 (2026-06-13)

### Fixed
- **ENGINE_VERSION** `"7.1.0"` → `"7.3.0"` (`constants.py`) — 스냅샷 `rule_version` 오기록 수정
- **`run.py` CLI `--snapshot` mom_map 누락** — `batch_fetch_momentum` 호출 후 저장. Screener 경로와 스냅샷 품질 동일화
- **MOM 표시 체계 혼재 수정** — `MOMENTUM_EMOJI` / `MOMENTUM_LABEL` 체계를 `mom_strength_display()` 화살표 체계로 통일
  - `4_ticker.py` — 모멘텀 헤더 `🔥 STRONG MOM` → `▲▲ +120pt` 형식
  - `2_portfolio.py` — 포지션 테이블 MOM 컬럼 동일 적용
  - `8_screener.py` — `mom_emoji` 필드 제거, 🔥 태그 감지를 `mom_signal == "strong"` 직접 비교로 변경
  - `9_momentum_stats.py` — 통계 카테고리 레이블 유지 (개별 종목 강도 표시 아님)
- **`database/schema.py`** → `archive/database_schema_unused.py` 이동 — JSON 기반으로 전환된 미사용 SQLite DDL

---

## v7.3.0 (2026-06-13)

### Added
- **기관 세부 수급 데이터 수집** — 점수 반영 없음, 연구 데이터 축적 목적
  - `SupplyFlow` 모델에 5개 필드 추가: `pension_net` / `investment_trust_net` / `financial_investment_net` / `private_fund_net` / `insurance_net`
  - `fetch_supply_flow()` (`krx.py`) — 기존에 버리던 세부 컬럼(연기금등, 투신, 금융투자, 사모, 보험) 파싱 추가. 컬럼 없으면 None (구 형식·신 형식 모두 대응)
  - `snapshot.py` `_result_to_dict()` 신규 필드:
    - `market_cap` — 비율 계산용 분모. 소급 복원 불가이므로 지금 수집
    - `supply_lookback_days` — 기준 기간 혼동 방지
    - `foreign_net` / `institution_net` + 5개 세부 기관 필드
    - `foreign_positive` / `institution_positive` / `pension_positive` — 동시 매수 groupby 분석용 방향 플래그
- **Research Q2 (예정)** — 60일 알파 기준: 연기금 단독 vs 외인 단독 vs 외인+연기금 동시 매수 비교. 검증 가능 시점: 2026-08-13 이후

---

## v7.2.0 (2026-06-10)

### Added
- **Snapshot 모멘텀 필드** (`backtests/snapshot.py`) — 스냅샷 엔트리에 모멘텀 데이터 저장
  - `mom_signal`, `mom_rel_1m`, `mom_rel_3m`, `mom_rel_6m`, `mom_strength` 추가
  - `mom_map` 없는 종목은 null로 저장 (구조 일관성 유지)
- **Snapshot KOSPI 기준선** — `kospi_close` 메타 필드 추가
  - 알파(종목 수익률 − KOSPI 수익률) 계산을 위한 기준선
  - `fetch_kospi_disparity()` 결과에서 `r.data.current` 재사용 (추가 API 호출 없음)
- **Screener → Snapshot 연동** (`pages/8_screener.py`) — `mom_map`을 `bt_snapshot.save()`에 전달

### Fixed
- **Watchlist 스크롤 위치 초기화 버그** (`pages/5_watchlist.py`)
  - `@st.fragment` 내 `st.rerun()` 호출 제거
  - `wl_save()` 직접 호출로 즉시 저장, `selected_ticker`는 루프 후 session_state에서 확정
- **`snap_map` / `sort_key` NameError** — fragment 외부에서 접근하는 변수 재선언
  - `snap_map`: `_main_section()` 호출 후 최신 스냅샷에서 재로드
  - `sort_key`: `st.radio`에 `key="wl_sort_key"` 추가 후 `session_state`로 읽기

---

## v7.1.0 (2026-06-09)

### Added
- **`mom_strength_display()`** (`analyzer/data/momentum.py`) — Screener / Watchlist 공용 화살표 표시 함수
  - ▲▲ ≥100pt / ▲ 50~99pt / · 0~49pt / ▼ -1~-49pt / ▼▼ -50~-99pt / ▼▼▼ ≤-100pt
- **Watchlist MOM 컬럼** — 메인 테이블에 MOM 강도 표시 (📈 strength≥50 / 📉 value_trap 자동 배지)
- **Watchlist MOM 정렬** — MOM ▲ (강한 순) / MOM ▼ (약한 순)
- **Watchlist MOM 필터** — 전체 / ▲▲ Strong / ▲ Positive / ▼ Negative / ▼▼▼ Extreme Negative
- **Watchlist Quick Inspect 모멘텀 통일** — 기존 `MOMENTUM_EMOJI` / `MOMENTUM_LABEL` → 화살표 체계 교체

### Fixed
- Screener `_mom_display` NameError — `mom_strength_display()` 공용 함수 호출로 교체

---

## v7.0.0 (2026-06-08)

### Philosophy
Deep Value Screener에서 **Deep Value + Momentum Research Platform**으로 확장.
점수 엔진(밸류·실적·리스크·수급)은 그대로 유지하고,
모멘텀은 별도 정보 레이어로 쌓아 시스템 신뢰도 데이터를 축적하기 시작.

### Breaking Changes
- **Momentum Overlay 도입** — 점수 계산과 독립된 레이어. 기존 스코어에 영향 없음
- **Momentum Tracker 로그 구조 추가** — `data/momentum_log/{ticker}.jsonl` per-ticker 적재
- **"🚨 VALUE TRAP" → "⚠️ 시장 외면 중"** — 전 페이지 명칭 변경

### Added
- **Momentum Overlay 시스템**
  - `MomentumSignal`: STRONG / POSITIVE / NEUTRAL / NEGATIVE / UNAVAILABLE
  - `MomentumData`: rel_1m / rel_3m / rel_6m + positive_count / negative_count
  - strength 가중: `0.5×rel_1m + 1.0×rel_3m + 1.5×rel_6m` (장기 모멘텀 우선)
  - STRONG 조건: 3개 기간 전부 데이터 존재 + 전부 임계값 초과
- **시장 외면 중 감지** — 고밸류 종목 + NEGATIVE 모멘텀 조합 탐지
- **Momentum Tracker** (`momentum_tracker.py`)
  - `prev_signal` / `days_since_prev` 자동 추출
  - `score_bucket`: 90+ / 80~89 / 70~79 / <70
  - `is_market_ignore` 플래그 기록
- **Forward Return / Alpha 계산** — `forward_return - kospi_return`
- **Survivorship Bias 가시화** — `unresolvable` 상태 분리 집계 + UI 표시
- **Signal Transition 분석** — `NEGATIVE→STRONG` 등 transition key별 통계
- **Market State × Signal 교차 분석**
- **Score Bucket × Signal / Grade × Signal 교차 테이블**
- **Daily Screener Snapshot** (`screener_snapshot.py`) — grade별 group return 계산 지원
- **Regime Change Log** (`regime_log.py`) — 시장 이벤트 수동 기록 + 조회
- **`ENGINE_VERSION`** (`constants.py`) — 모든 로그 항목에 자동 삽입
- **Momentum Stats 페이지** (`9_momentum_stats.py`)
  - min_sample 필터 / forward period 슬라이더
  - 시장 외면 중 전용 섹션 (avg > 0 시 "재검토 필요" 알림)
  - Snapshot 그룹 수익률 테이블 / Regime 로그 입력 UI
- **Screener Momentum 필터 + Strength 슬라이더**
- **Watchlist `MOMENTUM` 태그** (`WatchlistTag.MOMENTUM` / 🔥)

### Changed
- `_nearest_price()` backward-only 탐색 (미래 데이터 오염 방지)
- Screener 백그라운드 스캔 상태를 별도 모듈(`screener_state.py`)로 분리
- `log_signal()` 호출 시 `score` / `grade` / `market_state` 함께 기록

### Fixed
- median 계산 오류 수정 (홀수/짝수 분기)
- alpha 표본 수 return 표본 수와 분리 집계
- cache key에서 `value_score` 제거 (is_value_trap 재평가 오염 방지)
- `_run_scan_job` 핵심 import를 try 블록 내로 이동 (phase stuck 버그)
- Screener 상태 모듈 분리로 Streamlit 재실행 시 상태 리셋 문제 해결

---

## v6.6.3 (2026-05)

### Added
- **ROE 기반 실적 보너스** — 자기자본이익률 연동 실적 점수 추가 축

### Calibration Result
- STRONG_BUY: 2 → 10
- 실적 만점 종목 등장

---

## v6.6.2 (2026-05)

### Added
- **외인지분율 변화 보너스** — 외국인 지분율 증감 기반 수급 점수 보강
- 수급 strength 산정 방식 개선

---

## v6.6.1 (2026-05)

### Added
- Quick Reject Rule 신설 (즉시 탈락 5개 조건)
- §15 실행 가능 상태 추가 (등급 vs 시장 규칙 충돌 시 행동 명확화)
- 수급 순매수·지분율 충돌 해석 기준 신설
- 관찰 리스트 최대 6개 제한

### Changed
- 질적 우선권 수익화 방향 확인 조건 추가

---

## v6.6 (2026-05)

### Changed
- 리스크 감점 기준 정교화 — 업황 사이클 "존재" 자체 감점 폐지
- 피크아웃 "신호" 확인 시만 감점 (판단 기준 4개 중 2개 이상)
- 정치·관세도 동일 원칙 적용

---

## v6.5 (2026-05)

### Added
- 판단 우선순위 §0 신설 (최우선 3개 하드룰)
- 탐색 포지션 규칙 신설 (최대 5%)
- 선행 지표 슬롯 신설 (진입 트리거 전용, 점수 반영 금지)
- 관찰 리스트 즉시 재평가 트리거 (-0.5%p)
- 포지션 추적 로그 §10 신설

### Changed
- 외국인 단독 수급 점수 15 → 12점 (패시브 노이즈 반영)
- 수급 필터1 지속성: 기본 5일, 시총 0.5% 이상 단독이면 3일
- 시장 과열 경보 이진법 → 4단계 점수제 (🟢0~2 / 🟡3~5 / 🟠6~8 / 🔴9↑)
- 🟢 희귀 등급 명문화 (연 1~3회 정상)

---

## v6.4 (2026-05)

### Changed
- 비중 상한 재설계: 🟢 35% (확인 후 50%) / 🔵 20% / 🟡 7%
- 동시 보유 최대 4종목 제한
- 단일 섹터 상한 40% → 50%
- 현금 10~20% 권장 (🔴 시 강제)

---

## v6.3 (2026-05)

### Added
- 분석 중단 조건 신설 (상충 데이터 3개↑ 점수 산출 중단)

### Changed
- 승률 ≠ 기대수익률 명시
- 시장 체제 급변 시 시스템 무효 명시

---

## v6.2 (2026-05)

### Added
- 관찰 리스트 3개월 유효기간 신설
- 점수 급등 제한 규칙 신설 (단일 이벤트 20점↑ 즉시 최고등급 금지)

### Changed
- 과열 경보 "자동" 표현 제거
- 현금 철학 강화 (기대수익률 의사결정)
- 외국인 지분율 대형주 탄력 적용 예외 추가

---

## v6.1 (2026-05)

### Added
- 좋은 기업 vs 좋은 주식 원칙 신설
- 현금 확대 검토 트리거 5개 조건
- 이벤트 대기 상태 ⚫ 신설

### Changed
- 관찰 리스트 승격 조건 4개 명문화
- 기대치 위험 평가 정량화 (뉴스·커뮤니티 → 개인 거래대금·거래량 기준)

---

## v6.0 (2026-05)

### Added
- 불확실성 기록 §16 신설
- 시스템 적용 범위 명시

### Changed
- 보너스 경계 조정 방식 전환
- 손절 논리붕괴 1순위 재구성
- 과열 정성 항목 정량 대체
- 기대 대비 실적 원칙 추가
- 업황 구조 성장 5개 지표 강화
- 목표주가 → 영업이익 컨센서스 전환

---

## v5.0 이전

| 버전 | 주요 내용 |
|------|----------|
| v5.0 | 점수 착시 방지 / 시간 분산 매수 / 시스템 적합도 / 시장 과열 경보 / 매도 유형 4가지 |
| v4.0 | 점수 중복 제한 / 질적 판단 우선권 / 기대치 위험 / 현금 철학 / 관찰 리스트 / 침묵권 |
| v3.0 | 투자 유효기간 / 업황 피크아웃 / 실적의 질 / 리스크 세분화 / 진입 방해 요소 |
| v2.0 | 시장 상태 필터 / 포지션 크기 규칙 / 수급 정량 기준 |
| v1.0 | 최초 작성. 기본 4축 + 수급 보조센서 + 초입 탐지 |

# 인터넷 ip 확인 코드
curl ifconfig.me