# KR Engine Architecture Research Summary

**기준일:** 2026-09-10
**대상 엔진:** v8.5.19 (Engine Freeze 중)
**연구 패널:** rrs_stock_year 5,839건, risk_year 2016-2023, fwd_year 2017-2024 (in-sample)

---

## 연구 개요

v8.5.19 scoring engine의 각 component가 실제로 어떤 역할을 하는지,
"선형 합산" 구조가 최선인지를 RRS historical panel에서 검증.

총 10개 연구 스크립트 실행. 결과는 **방향성 참고 수준**이며,
production 변경의 근거가 되려면 Engine Freeze 해제 후 OOS 검증 필요.

---

## Component별 정리

### 1. Value (PER/PBR 기반)

| 항목 | 내용 |
|---|---|
| 현재 역할 | Primary Ranker (선형 합산 내 최대 비중) |
| 연구 결과 | B/M 초기 IC 강해 보였으나 distress/lottery 제거 후 약화. PBR 단독 연속성 미확인. |
| 증거 강도 | 약함 — "Value가 alpha ranker임"을 지지하는 robust 근거 없음 |
| 핵심 발견 | mean alpha 역방향은 대부분 LOTTERY (극단 상승주). median/tmean으로 보면 약하거나 무효 |
| 미결정 | 어떤 value 정의(PER/PBR/B/M)가 더 나은지 미확인 |
| Production 변경 | ❌ 금지 — 근거 부족, in-sample tuning 위험 |

---

### 2. Earnings (실적 점수)

| 항목 | 내용 |
|---|---|
| 현재 역할 | 선형 합산 내 component (22점 이상이 STRONG_BUY 기준) |
| 연구 결과 | Ranker로 쓸 근거 없음. Gate(흑자 여부)로의 역할이 방향 지지 |
| 증거 강도 | 약함 — Gate 방향 지지(C_20 6/8년 양수)이나 p>0.60 |
| 핵심 발견 | **Score 분포가 9/20/22 삼분위** (범주형). 연속 ranker로 쓰기 구조적으로 부적합. E≥22는 과도한 exclusion(n/yr≈37). E≥20(흑자 여부)이 가장 자연스러운 후보 |
| 미결정 | 어떤 threshold가 Gate로 최적인지 통계적으로 미결정 |
| Production 변경 | ❌ 금지 — 방향은 있으나 유의미하지 않음, in-sample threshold 선택 금지 |

---

### 3. Risk (재무 안정성)

| 항목 | 내용 |
|---|---|
| 현재 역할 | 선형 합산 내 component |
| 연구 결과 | Alpha ranker 아님. Downside discriminator로는 명확 (P10/P25/hit rate). Filter 역할 방향 지지 |
| 증거 강도 | 보통 — downside 보호는 확인됨. Architecture 내 역할은 threshold-sensitive |
| 핵심 발견 | **Score=25가 56.6%** (최고점 집중). R≥17/22는 방향 개선(ER_22: tmean +0.70pp), R≥25는 역전. E gate × R filter 조합은 느슨할 때(ER_17, p=0.051†) borderline 효과. Risk가 약할 때 E gate 기여가 더 선명 |
| 미결정 | Threshold 최적화 불가 (in-sample). 역할 분리 효과의 OOS 재현 여부 미확인 |
| Production 변경 | ❌ 금지 — downside 역할은 지지되나 architecture 반영 근거 미달 |

---

### 4. Supply (수급 신호)

| 항목 | 내용 |
|---|---|
| 현재 역할 | 선형 합산 내 component (timing signal 의도) |
| 연구 결과 | 연간/단기 모두 기대한 positive alpha 효과 없음. Architecture v2에서 A=B (Supply 포함/제외 동일 결과) |
| 증거 강도 | 약함 — 긍정적 효과 미확인 |
| 핵심 발견 | **Supply score가 현재 linear sum의 순위 변별에 실질적 기여 없음** (A=B: diff=0.00). 연말 기준 수급 신호가 다음 해 alpha를 예측하지 않음. Supply Gate 적용 시 오히려 성과 악화 |
| 미결정 | 다른 시간 기준(분기 내 단기)에서의 효과 미검증 |
| Production 변경 | ❌ 금지 — 긍정적 근거 없음 |

---

### 5. Momentum (가격 모멘텀)

| 항목 | 내용 |
|---|---|
| 현재 역할 | Production score에 미편입 상태에서 연구 |
| 연구 결과 | 1M~12M 전 창에서 continuation 미확인. 12M IC=-0.076* (p=0.025) reversal |
| 증거 강도 | Reversal 방향으로 약한 증거 — continuation 근거 없음 |
| 핵심 발견 | **KR 시장에서 모든 formation window가 continuation을 보여주지 않음**. 12M이 유일하게 p<0.05이나 방향이 역방향. 6-1만 양수 방향(6/8년)이나 p=0.662. 2024년만 6M/9M/6-1에서 continuation 선명 (단일 연도, 결론 보류) |
| 미결정 | 2024년 regime 변화 가능성. Value-screened universe의 구조적 bias 여부 |
| Production 변경 | ❌ 금지 — continuation 근거 없음. reversal을 역방향 filter로 채택도 금지 (in-sample 방향 전환 위험) |

---

## Architecture 후보 (미채택 — 후일 OOS 검증 후 결정)

```
Candidate 1: 현재 선형 합산 유지 (V+E+R+S)
  → 현재 baseline. 역할 분리 대비 유의미한 열위 없음.

Candidate 2: E≥20 Gate + R Filter + V Ranker
  → 방향 지지(ER_22: 6/8년, tmean +0.70pp). 통계적 유의성 없음.
  → 채택 조건: OOS n≥50 이상에서 동일 방향 재현 시 검토.

Candidate 3: R Filter only + V Ranker (E gate 없음)
  → R≥22/25 only: 방향 소폭 개선(R_25: 6/8년). 단독으로는 약함.

Supply: 현재 미채택. 새 역할 정의 연구 필요 (단기 timing 가설 등).
Momentum: 현재 미채택. 모든 창 무효 또는 reversal.
```

---

## 변경하지 않을 것 (확정)

Engine Freeze 해제 조건(v8.5.19 n≥100) 달성 전까지:

| 항목 | 결정 |
|---|---|
| Value weight 변경 | ❌ |
| Earnings weight/역할 변경 | ❌ |
| Risk threshold 조정 | ❌ |
| Supply 반전 또는 제거 | ❌ |
| Momentum 편입 (어떤 형태도) | ❌ |
| Architecture 구조 변경 | ❌ |

---

## 다음 결정 조건

**Engine Freeze 해제 (v8.5.19 n≥100):**
_(예상 시점: 2026년 말~2027년 2월 — Retrospective Calibration 배치 완료 시 대폭 앞당겨질 수 있음. RRS 데이터는 in-sample이므로 live forward validation과 병행 해석 필요.)_
- Score Band / Component / Factor Calibration 결과 검토
- Candidate 1 vs Candidate 2 OOS 비교
- 그때 연구 결과와 live forward validation이 같은 방향이면 architecture 변경 검토

**그 전에 할 수 있는 것:**
- v8.5.19 live snapshot 수집 계속 (n 누적)
- **Retrospective Calibration (RRS)**: 2026년 전체 trading days에 v8.5.19 공식 소급 적용 → in-sample 분포/Score Band 분석 즉시 가능
- 20D/60D/120D alpha 창 순차 활성화
- Calibration Report 기능 개발 (점수 변경 없음)

---

## 연구 파일 목록

| 파일 | 주제 |
|---|---|
| `objective_validation.py` | Lottery-adjusted objective validation |
| `roe_inversion.py` | ROE structural inversion decomposition |
| `risk_decomposition.py` | Risk component downside analysis |
| `risk_decomposition_v2.py` | Risk downside v2 |
| `architecture_counterfactual.py` | Architecture v1 (Supply 상수 기간) |
| `apply_historical_features.py` | Supply/Momentum 실데이터 적용 |
| `extend_momentum_features.py` | 9M/12M/12-1/6-1 momentum 파생 |
| `momentum_ic.py` | Momentum IC (1M/3M/6M) |
| `momentum_horizon_map.py` | Momentum horizon mapping (전 창) |
| `architecture_v2.py` | Architecture v2 (역할 분리 counterfactual) |
| `earnings_gate_sensitivity.py` | Earnings gate threshold sensitivity |
| `risk_filter_sensitivity.py` | Risk filter threshold sensitivity |
