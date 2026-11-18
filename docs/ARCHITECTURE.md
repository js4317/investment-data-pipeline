# Architecture Notes

## Dependency Direction
올바른 의존성 방향:
```
pages → analyzer
pages → backtests
backtests → analyzer
(역방향 없음)
```

## Known Layer Violations (tolerated — lazy import)
- `analyzer/rules/sell_checklist.py` → `backtests.snapshot` (함수 내부 lazy import)
- `analyzer/tracking/score_tracker.py` → `backtests.snapshot` (함수 내부 lazy import)

이유: score_tracker와 sell_checklist는 스냅샷 히스토리를 읽어야 함.
모듈 상단 import가 아닌 함수 내부 lazy import이므로 순환 의존 위험 없음.
수정 방향: 호출자가 snapshot_dates를 주입하는 방식으로 전환 (v9 리팩토링 검토)

## Known Intentional Gap
`pages/4_ticker.py` bt_snapshot.save([result], today) — mom_map 없음.
이유: 단일 종목 분석 페이지. 배치 모멘텀 fetch 미수행. 설계상 허용.
영향: 티커 페이지 저장 스냅샷 entry는 momentum 필드가 null. Morning Run / Screener 저장 entry는 정상.

## Navigation Design
현재 페이지 간 이동은 session["ticker_goto"] + session["filing_highlight"] 두 키로 구현.
두 키는 항상 함께 사용 → "하나의 요청"을 나타내는 신호.
세션 키가 3개 이상이 되면 NavigationRequest(ticker, target, highlight) dataclass로 통합 예정.
  target 예시: "market_context" / "risk_radar" / "momentum" / "portfolio_advisory"
highlight는 수신 페이지에서 즉시 pop() — Highlight Once 원칙.

Migration trigger: session_state nav 관련 키가 10개 이상 → NavigationRequest 패턴 재검토

## Market Context Domain
- MarketContextItem stores intrinsic event metadata only — importance, source, date.
- Display priority (urgency to the user) is computed in the view/UI layer, not in the domain model.
  Priority = f(importance, status, seen) — 구현 예정 v8.2~8.3.
- SeenTracker는 현재 filings.py에 위치. 공시 외 항목(뉴스, 이벤트)에도 Seen이 생기면 market_context/seen.py로 분리.

## Portfolio Performance Distinction
- Reference Performance (v8.3.1, 현재 구현):
    "현재 보유 종목을 동일비중으로 20영업일 전부터 들고 있었다면?"의 가상 수익률.
    코드: pages/2_portfolio.py `_calc_recent_perf()`
- Actual Portfolio Performance (v9 예정):
    실제 거래 기록(매수일, 매수가, 수량) 기반 TWR/MWR.
    거래 기록 저장 구조 설계 전까지 구현하지 않는다.

## Migration Triggers
"지금 당장 고칠 필요는 없다. 하지만 이 임계값을 넘으면 반드시 고친다."

Momentum Log:
  현재: JSON 파일 (data/momentum_log/)
  트리거: 종목당 로그 항목 ≥ 500건, 또는 전체 파일 개수 ≥ 200개 → SQLite 전환 검토

@st.fragment:
  트리거: Ticker 페이지 로드 체감 ≥ 3초 + Streamlit ≥ 1.40 안정 릴리즈 확인
  도입 시 대상: Risk Radar 패널, Market Context 패널 (각각 외부 API 호출)

Calibration Alpha Windows:
  트리거: 스냅샷 누적 ≥ 60 영업일 → 60D Alpha 계산 활성화
          스냅샷 누적 ≥ 120 영업일 → 120D Alpha 계산 활성화

## v9 Broker Integration
데이터 소스 역할:
  pykrx       → 시세, 과거 가격, 시장 데이터 (분석 엔진 전용)
  DART        → 공시, 재무제표, 기업 정보 (분석 엔진 전용)
  Broker API  → 내 계좌, 내 체결, 내 보유 (개인 투자 데이터 전용)
  Local DB    → Thesis, Alpha, Derived Data, Trade Journal (콘솔 관리)

원칙:
  1. Broker API는 체결 Fact 동기화용. Local DB가 Source of Truth.
  2. Broker API 없이도 콘솔은 완전히 동작해야 한다.
  3. 분석 엔진(Score)은 Broker API에 의존하지 않는다.
  4. Broker Sync는 idempotent — 동일 체결은 고유 체결번호 기준으로 1회만 저장.

Scope (v9.0 Read-only):
  - Holdings Sync: 보유 종목 자동 업데이트
  - Orders Sync: 체결내역 → Local DB
  - Trade Journal: 체결 시 당시 Score·Grade·Market Context 자동 첨부
  - Derived Performance: Holding Days, Realized P&L, Alpha, Win Rate (콘솔 계산)
  - Investment Timeline: Morning Run 신호 → 매수 → 이벤트 → 매도 → 결과 흐름 기록
  - Auto Trading: 지원하지 않음. 최종 클릭은 항상 사람이 한다.

Trade Context: 거래 이전 가장 최근 Morning Run Snapshot 사용.
  저장 필드: trade_date / snapshot_date / snapshot_gap_days
  Freshness: 🟢 0~3일  🟡 4~7일  🟠 8~14일  🔴 >14일
  (Gap은 신선도이며 신뢰도가 아님 — 이벤트 없었다면 14일 gap도 유효)
