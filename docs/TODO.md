# TODO

액션 아이템 전용. 설계 원칙·규칙은 CLAUDE.md 참조.
트리거 조건 기반으로 섹션 구분. 완료된 항목은 삭제하지 말고 ✅ 표시.

---

## 즉시 (다음 Morning Run 이후)

- ✅ 실제 스냅샷에서 earnings regime 5개 필드 채워지는지 확인
  → v8.5.20에서 snapshot 저장 로직에 earnings_regime 필드 추가됨 (CHANGELOG 참조)
- ✅ Forward Validation 방법론 freeze 문서화
  → docs/RESEARCH.md Research Design Principles 섹션 추가 (2026-08-26)

## 단기 미결

- [ ] 스냅샷 `name` 필드 누락 수정 — 모든 항목 "?" 표시
  → `_result_to_dict()` 또는 Morning Run에서 종목명 추가 필요
- [ ] `kis_provider.py:572` — VI 발동 시 실제 코드값 확인
  → 현재 `_VI_INACTIVE = ("", "0", "N", None)` 으로 'N'=정상 확인. 발동 시 코드('Y'? '1'? 다른 값?) 미확인.
  → VI 발동 종목에서 Morning Run 실행 후 `vi_cls_code` 값 직접 로깅해서 확인 필요.

---

## n≥20 영업일 도달 시 (예상 2026년 9월 말)

- [ ] `research/ranking_stability.py` 작성 및 실행
  → 조건: v8.5.19 기준 스냅샷(universe="WATCHLIST") ≥ 20개
  → 측정 항목: Top30% Jaccard overlap, 주간 turnover, 연속 등장률, 신규진입/이탈률,
     Score band별 집중도, 섹터 집중도, 상위 종목 평균 체류기간
  → 주의: 날짜별 종목 수가 달라 Jaccard는 공통 등장 종목(intersection) 기준으로 계산

---

## n≥60 영업일 도달 시 (예상 2026년 11월)

- [ ] 60D Alpha 계산 활성화
  → Calibration Report 코드에서 60D 윈도우 활성화
  → 트리거: 스냅샷 파일 ≥ 60개

---

## n≥100 영업일 도달 시 (Engine Freeze 해제 조건)

_(예상: 2026년 말~2027년 2월. Retrospective Calibration 배치 완료 시 in-sample 분석은 즉시 가능하나, OOS live validation은 별도 누적 필요.)_

### Calibration Report 결과 보고 결정할 항목
- [ ] PBR weight 최적값 확인 (현재 1.0은 후보값, 확정값 아님)
- [ ] 지주사 PBR-only 유지 여부
- [ ] EQ Penalty (eq_band) 유지 여부
- [ ] Supply 가중치 변경 여부

### 가설 검증
- [ ] CYCLICAL-1 가설 검증: LOW_PER + PEAK → Bear/Sideways Alpha 열위?
  → 팩터 상태: Experimental (2026-08-26~) → 검증 결과에 따라 Validated or Deprecated
- [ ] Q1 Momentum 가설 검증: STRONG_BUY + Strong MOM > STRONG_BUY + Negative MOM (20D Alpha)
  → 팩터 상태: Calibrating
- [ ] 120D Alpha 활성화 (별도 조건: 스냅샷 ≥ 120개)

### 팩터 상태 결론 내릴 항목 (n≥100 후)
| 팩터 | 현재 상태 | 시작일 |
|------|-----------|--------|
| EQ Penalty (eq_band) | Calibrating | 2026-07-01~ |
| Supply Score | Calibrating | 2026-06-09~ |
| Valuation Score | Calibrating | 2026-06-09~ |
| Sector Adjustment | Experimental | 2026-07-01~ |
| Momentum Overlay | Calibrating | 2026-06-09~ |
| Earnings Regime | Experimental | 2026-08-26~ |

---

## Structural Trend (BLOCKED — 선행 조건 미충족)

선행 조건: DART에서 8분기 분기 재무 Timeseries 안정 추출 가능 여부 검증

- [ ] DART 8분기 분기 재무 추출 POC 실행
  → 성공 시: Stage 1 시작 (Revenue YoY / Operating Income YoY / ROE 저장)
  → 실패 시: v8.5 Structural Trend 설계 전면 재검토

---

## v9 (LOCKED — Calibration n≥100 + Alpha 유의성 확보 후)

- [ ] Broker Integration (Read-only)
  - Holdings Sync — 보유 종목 자동 업데이트
  - Orders Sync — 체결내역 → Local DB
  - Trade Journal — 체결 시 당시 Score·Grade·Market Context 자동 첨부
  - Derived Performance — Holding Days, Realized P&L, Alpha, Win Rate
  - Investment Timeline — Morning Run 신호 → 매수 → 이벤트 → 매도 흐름 기록
- [ ] Actual Portfolio Performance (TWR/MWR) — 실제 거래 기록 기반

## v10 (LOCKED — v9 이후)

- [ ] Position Sizing
- [ ] Risk Budget
- [ ] Kelly / Volatility Scaling (필요 시)

---

## Migration Triggers 현황 (임계값 도달 시 재검토)

| 항목 | 현황 | 트리거 |
|------|------|--------|
| Navigation | ✅ 완료 (2026-07-03) — nav 키 6개 → navigation_request 1개 | nav 키 ≥ 10개 |
| Momentum Log | JSON 파일 (data/momentum_log/) — 아직 트리거 미달 | 종목당 ≥500건 or 파일 ≥200개 |
| @st.fragment | 미도입 — 생태계 안정성 우려 | 로드 체감 ≥3초 + Streamlit ≥1.40 |
| 60D Alpha | 미활성 — 데이터 미도달 | 스냅샷 ≥60 영업일 |
| 120D Alpha | 미활성 — 데이터 미도달 | 스냅샷 ≥120 영업일 |

---

## 코드 내 미결 항목 (인라인 TODO에서 이관)

코드에서 `TODO:` 프리픽스 제거 완료. 아래가 전체 목록.

### Engine Freeze 중 허용 (버그 수정 / 비점수 기능)

- [ ] `scorer.py:81` — notes 중복 제거
  → validation/scoring/override notes 합산 시 동일 경고 반복 가능. 버그성.
- [ ] `diff.py:41` — quick_reject reason을 snapshot에 명시적으로 저장
  → 현재는 heuristic(grade=avoid + score≥55)으로 추정. 저장하면 정확해짐.
- [ ] `models.py:21` — `RATE_LIMITED` FetchErrorReason 추가
  → 네이버 429·captcha·block 대응. 현재 API_TIMEOUT으로 뭉뚱그려 처리 중.
- [ ] `run.py:60` — fallback trace reporting
  → 병합/대체 발생 시(KRX→Naver fallback 등) report에 반영 고려.

### 데이터 소스 확보 후 활성화

- [ ] `config.py:92` + `models.py:152` — interest_coverage 이자비용 데이터 소스 확보
  → EBIT / 이자비용 < 1.0 + 부채비율 > 120% 복합 조건 Risk -3 활성화 대기 중.
  → `RiskData.interest_coverage` 필드 존재하나 항상 None. DART 또는 외부 소스 필요.
- [ ] `kis_provider.py:632` — DART 사업보고서 기반 YoY 희석률 구현
  → KIS `inquire-price`는 현재 상장주식수만 제공. 1년 전 값 조회 API 없어 None 처리 중.
- [ ] `krx.py:629` — merger-induced dilution 예외 처리 정교화
  → 현재 50% 초과 희석률을 corporate action으로 간주해 None 처리.
  → 정확히 하려면 공시(합병등록일, 신주배정비율) 연동 필요.
- [ ] `run.py:218` — 필드별 데이터 소스 분리
  → volatility/dilution=pykrx, debt/loss=naver 혼합. `*_source` 필드 추적 체계화 고려.

### Engine Freeze 해제 후 검토 (점수·구조 변경 관련 — 현재 금지)

- [ ] `valuation.py:90` — PER invalid 시 PSR 대안 지표 고려
  → 적자 턴어라운드·플랫폼·AI 성장주에서 PER 붕괴 케이스 多. 현재 0점 처리.
- [ ] `supply.py:78` — bonus 구조 개선: `bonus_applied`, `bonus_reason` 필드로 추적
  → 현재 bonus가 왜 부여됐는지 스냅샷에 저장 안 됨. Calibration 연구 시 필요.
- [ ] `market.py:88` + `market_regime.py:40` — 시장 지표 고도화
  → 거래대금 급등, 상승 가속 캔들, VIX, 코스피 추세·채권금리 복합 판단.
  → 현재 regime은 heat의 alias처럼 동작 (과열 ≠ 시장 방향 — 추후 분리 필요).
- [ ] `execution.py:8,10` — structured decision code + suggested_position_limit_pct
  → `DecisionCode.BLOCKED_LOW_CONFIDENCE` 등 enum 기반 결정 코드.
  → 등급·regime·heat 조합에서 권고 비중 상한 자동 산출. v9 범위.
- [ ] `validator.py:82` — supply_flow / foreign_ownership 기반 quick reject 조건
  → 외국인+기관 쌍끌이 매도 지속 → `QuickRejectReason.DUAL_FOREIGN_EXIT`
  → 연속 미스 3분기 → `QuickRejectReason.CONSECUTIVE_MISS`

### 기타

- [ ] `risk.py:61` — governance_reason 필드 추가
  → "왜 governance 위험?" 리포트 설명용. 현재 flag만 있고 이유 없음.
- [ ] `research/poc/poc_holding_list.py:26` — KY(000040) 지주사 여부 검토
  → HOLDING_TICKERS 포함 여부 확인 필요. Engine Freeze 중 변경 금지이므로 해제 후 처리.

---

## Engine Calibration 스냅샷 (참고용, 변경 금지)

**2026-06-30 기준 분석:**
- 분석 대상: 스크리너 후보 55개, STRONG_BUY 12개
- available_max = 100 (전 종목) → 부분채점 가설 기각
- Earnings std = 0.0 (STRONG_BUY) → 스크리너 필터가 이미 Earnings 하한을 걸러냄. 설계상 정상.
- Valuation std = 7.6 (전체), 2.4 (SB) → 변별력 충분. ceiling은 PER 2~4대 종목 (수학적 정당).
- Supply std = 5.5 (전체) — 2026-06-30 기준.
  _(Architecture v2 연구(2026-09-10)에서 Supply A=B 확인 — 선형 합산 내 순위 변별에 실질 기여 없음. 이 수치는 역사적 참고값.)_
- 결론: 엔진 버그 없음. 지주사 이슈는 Valuation 해석 문제 → PBR-only로 해결 (v8.5.19).

**지주사 Calibration Candidate 추적:**
- 하림지주(003380) 등: 구조적 특성상 연결 PER 낮음 → PBR-only 적용 후 성과 추적 중
- v8.5.19 적용일(2026-08-21)로부터 n≥100 후 결론. 삭제 금지.
