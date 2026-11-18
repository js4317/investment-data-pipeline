# US Stock Engine — Scoring Design

**버전**: v0.2 (확정일: 2026-09-05)  
**상태**: 🔵 Historical Design Specification

> **주의**: 이 문서는 v0/v1 설계 기준의 역사적 명세다. v1 Momentum 설계(US-RESEARCH.md US-0.4 이후)와
> Research Design Update(2026-09-08)가 이후 방향을 결정했다. 최신 empirical findings는 US-RESEARCH.md를 우선한다.
> 이 문서의 weight·threshold는 현재 production 기준이 아닌 **초기 후보값**이다.

---

## 1. 설계 철학

### Academic Prior 채택 원칙
Value, Quality, Profitability가 장기 수익률과 관련 있다는 것은 이미 충분히 검증되어 있다.
(Fama-French 1992, 2015; AQR Value and Momentum Everywhere 2013)

**우리가 다시 증명하지 않는 것:** factor의 존재  
**우리가 직접 결정하는 것:** factor의 구현 방식, 기준값, 가중치

### 절대 기준 (Absolute Threshold) 방식
Percentile/rank 기반 채점은 사용하지 않는다.

이유: universe 크기와 무관하게 고정된 valuation 기준에 따라 채점할 수 있다.
(cross-sectional percentile = universe 대비 저평가 / time-series percentile = 자기 역사 대비 저평가 / absolute threshold = 고정 경제적 기준 대비 저평가 — 이 셋은 서로 다른 신호다.)

### Freeze 원칙
가중치·기준값 변경에는 반드시 명시적 가설이 있어야 한다.
특정 종목의 결과가 마음에 들지 않아 해당 종목에 penalty를 추가하는 방식은 금지.
한국 엔진의 Freeze 원칙을 그대로 적용한다.

---

## 2. 채점 구조 (v0)

```
US Score (100점 만점)
│
├── Value           50점
│   ├── PER         25점
│   └── PBR         25점
│
├── Quality         30점
│   └── ROE         30점
│
└── Earnings Health 20점
    ├── 흑자/적자 여부
    └── YoY NI 방향
```

**Momentum, Risk → v1 이후 추가**  
모멘텀은 별도 가격 파이프라인 확장 필요. v0에서는 넣지 않는다.

---

## 3. 팩터별 채점 기준

### 3-1. PER (25점)

| PER 범위 | 점수 | 비고 |
|---------|------|------|
| 적자 또는 N/A | 0 | Earnings Health에서 별도 처리 |
| > 50x | 2 | 극단적 고평가 |
| 35 ~ 50x | 8 | 고평가 |
| 25 ~ 35x | 13 | 다소 고평가 |
| 18 ~ 25x | 18 | 적정 |
| 12 ~ 18x | 22 | 저평가 |
| < 12x | 25 | 강한 저평가 |

기준: S&P 500 역사 평균 PER ~16~18x. 12x 이하는 장기 역사 기준 명확한 저평가 구간.

### 3-2. PBR (25점)

| PBR 범위 | 점수 | 비고 |
|---------|------|------|
| < 0 (자본잠식) | 0 | |
| > 15x | 3 | 극단적 고평가 |
| 8 ~ 15x | 8 | 고평가 |
| 4 ~ 8x | 13 | 다소 고평가 |
| 2 ~ 4x | 18 | 적정 |
| 1 ~ 2x | 22 | 저평가 |
| < 1x | 25 | 강한 저평가 |

**알려진 한계:** 자사주 매입 규모가 큰 기업(AAPL, AMZN)은 장부가치가 낮아 PBR이 구조적으로 높게 나온다.
금융주(JPM)는 PBR 1~2x가 정상이다. v0에서는 섹터별 조정 없이 단일 기준 적용.
섹터 보정은 표본이 충분히 쌓인 후 calibration 데이터로 결정한다.

### 3-3. ROE (30점)

| ROE 범위 | 점수 | 비고 |
|---------|------|------|
| < 0% | 0 | |
| 0 ~ 5% | 5 | |
| 5 ~ 10% | 10 | |
| 10 ~ 15% | 15 | |
| 15 ~ 25% | 22 | 양호한 quality |
| 25 ~ 50% | 28 | 고quality |
| > 50% | 30 | |

**ROE > 50% 처리 원칙:** penalty를 적용하지 않는다.
ROE가 자사주 매입으로 왜곡되었는지 여부는 별도 `roe_extreme` flag(ROE > 50%)로 데이터에 보존한다.
"ROE > 50%가 미래 수익률에 부정적이다"는 가설은 calibration 데이터가 충분히 쌓인 후 검토한다.
명시적 근거 없이 특정 ROE 구간에 penalty를 적용하는 것은 Freeze 원칙 위반이다.

### 3-4. Earnings Health (20점)

| 조건 | 점수 |
|------|------|
| 적자 (NI < 0) | 0 |
| 흑자, 전년 대비 NI 감소 | 10 |
| 흑자, 전년 대비 NI 증가 또는 유지 | 20 |

역할: Value/Quality가 높아도 적자 기업은 Gate에서 막힌다.
주의: 이 컴포넌트는 earnings의 규모나 quality를 평가하는 것이 아니다.
흑자 여부와 방향성(profitability + YoY trend)만 측정한다.

---

## 4. 등급 기준

| 점수 | 등급 |
|------|------|
| ≥ 70 | **STRONG_BUY** |
| 55 ~ 69 | **BUY** |
| 40 ~ 54 | **HOLD** |
| < 40 | **WATCH** |

*v0 기준값. 충분한 운용 데이터 축적 후 calibration으로 조정.*

---

## 5. v0 적용 결과 (2025 FY 기준 예상)

ROE > 50% penalty 제거 반영.

| Ticker | PER점 | PBR점 | ROE점 | EH점 | 합계 | 등급 |
|--------|-------|-------|-------|------|------|------|
| JPM    | 22    | 18    | 22    | 20   | **82** | STRONG_BUY |
| KO     | 13    | 8     | 28    | 20   | **69** | BUY |
| UNH    | 18    | 18    | 15    | 10   | **61** | BUY |
| XOM    | 13    | 18    | 15    | 20   | **66** | BUY |
| MSFT   | 8     | 8     | 28    | 20   | **64** | BUY |
| CAT    | 8     | 8     | 28    | 20   | **64** | BUY |
| AMZN   | 13    | 13    | 18    | 20   | **64** | BUY |
| AAPL   | 8     | 3     | 30    | 20   | **61** | BUY |
| NVDA   | 2     | 3     | 30    | 20   | **55** | BUY |
| WMT    | 2     | 8     | 22    | 20   | **52** | HOLD |

*주의: 코드 구현 후 실제 research_panel 값으로 재계산한다.*

---

## 6. v0에서 의도적으로 제외하는 것

| 항목 | 이유 |
|------|------|
| Momentum | 별도 가격 파이프라인 확장 필요 |
| Risk (Beta, Volatility) | 기준값 설계에 추가 연구 필요 |
| 섹터별 PBR 보정 | 표본 부족. calibration 후 결정 |
| 부채비율 / 이자보상배율 | 데이터 확보 후 v1에서 추가 |
| Earnings revision | Analyst data 별도 수집 필요 |
| ROE 왜곡 보정 | `roe_extreme` flag로 보존. calibration 후 결정 |

---

## 7. 다음 단계

```
US-SCORING-DESIGN.md v0.2 확정   ← 지금
        ↓
v0 scorer 구현 (research_panel → US Score)
        ↓
10종목 전 연도 결과 출력 (연도별 점수 + 등급)
        ↓
annual_return vs score 상관관계 확인
        ↓
Universe 확장 (S&P 500 30~50종목)
        ↓
Momentum 추가 (v1)
        ↓
Risk 추가 (v1)
        ↓
Production
```

---

## 9. v1 Momentum Design (Draft — 구현 전 확정 필요)

**버전**: v1 Draft (2026-09-05)  
**상태**: 설계 확정 → 코드 구현 → 10종목 replay 순서로 진행

### 9-1. 설계 배경

v0 sensitivity 분석 결과 (2026-09-05):
- 전체 Score ↔ Return 상관계수: -0.288
- NVDA+AMZN 제외: -0.097 (≈ 0)
- NVDA 제외 시 WATCH 수익률: +79.5% → +21.0% (정상화)

**결론:** v0의 음의 상관계수는 모델 전체의 역신호가 아니라 NVDA 효과가 지배적이었음. 그러나 "valuation이 비싸도 earnings growth가 급격히 커지는 기업"은 v0 구조상 포착 불가 — 이것이 구조적 blind spot.

**v1의 질문:**
> "Value + Quality가 포착하지 못하는 고성장 전환을 Momentum이라는 독립적인 팩터가 보완할 수 있는가?"

이 질문은 NVDA를 보고 만든 것이 아니다. Momentum은 이미 검증된 독립적 팩터다 (Jegadeesh & Titman 1993; AQR Value and Momentum Everywhere 2013). Value와 Momentum은 구조적으로 부의 상관관계를 가지며, 두 팩터를 결합하면 각각보다 강한 설명력을 가진다.

### 9-2. Momentum 정의

**12–2 Momentum** (학술 표준)

```
mom = price(T - 1M) / price(T - 12M) - 1
```

- `T` = as_of_date (= fiscal_year_end + 90일, v0과 동일)
- `T - 1M` = T - 30일 (최근 1개월 제외 — short-term reversal 회피)
- `T - 12M` = T - 365일
- 가격: split-adjusted (auto_adjust=True), v0 get_price()와 동일 방식
- PIT 보장: T-1M, T-12M 모두 as_of_date 이전 → look-ahead bias 없음

최근 1개월을 제외하는 이유: 단기 반전(reversal) 효과. 직전 1개월 수익률은 다음 달 수익률과 음의 상관관계를 갖는 것이 실증적으로 확인됨. 12개월에서 최근 1개월을 뺀 11개월 수익률이 실제 Momentum signal.

### 9-3. Momentum 점수 (절대 기준)

| 12–2 Momentum | 점수 | 비고 |
|--------------|------|------|
| > +30% | 20 | 강한 상승 추세 |
| +10% ~ +30% | 15 | 완만한 상승 |
| -10% ~ +10% | 8 | 중립 / 보합 |
| -30% ~ -10% | 3 | 하락 추세 |
| ≤ -30% | 0 | 강한 하락 추세 |

**임계값 설정 원칙:**
- 모든 임계값은 round number. NVDA 사례를 보고 역설계하지 않았다.
- S&P 500 역사 평균 11개월 수익률 ≈ 8~9%. +10%/+30%는 평균 대비 경제적으로 의미 있는 구분.
- ≤ -30%는 시장 평균에서 약 40%p 이상 underperform — 구조적 문제 신호.

### 9-4. 총점 구조 (v1)

**방식 A 채택: 100점 내부 재배분 (총점 불변)**

```
US Score v1 (100점 만점)
│
├── Value           40점  (v0: 50점)
│   ├── PER         20점  (v0: 25점)
│   └── PBR         20점  (v0: 25점)
│
├── Quality         25점  (v0: 30점)
│   └── ROE         25점
│
├── Earnings Health 15점  (v0: 20점)
│   ├── 흑자/적자 여부
│   └── YoY NI 방향
│
└── Momentum        20점  (v0: 없음)
    └── 12-2 momentum
```

재배분 근거: Value(-10), Quality(-5), EH(-5) → Momentum(+20)
- Value는 Momentum과 부의 상관이므로 가장 많이 줄인다.
- Quality(ROE)와 EH는 소폭 축소 — gate 역할은 유지.

**방식 B/C를 채택하지 않는 이유:**
- B (기존 100점 유지 + Momentum 20점 추가 → 120점): 총점이 달라지면 grade 기준도 재설계해야 함. v0 비교 불가.
- C (v0 100점 + Momentum 20점 별도): 같은 이유. 또한 "점수를 부풀렸다"는 해석 문제.

### 9-5. Grade 기준 (v1 — 유지)

| 점수 | 등급 |
|------|------|
| ≥ 70 | STRONG_BUY |
| 55 ~ 69 | BUY |
| 40 ~ 54 | HOLD |
| < 40 | WATCH |

threshold 변경 없음. v1 replay 결과를 보고 calibration 데이터로만 결정한다.

### 9-6. PER/PBR/ROE/EH 세부 기준 (v1)

PER 25점 → 20점, PBR 25점 → 20점, ROE 30점 → 25점으로 비율 조정.
점수 테이블의 각 구간 배점을 비례 축소한다. 구간 임계값(12x, 18x 등)은 변경하지 않는다.

**PER (20점)**

| PER 범위 | 점수 |
|---------|------|
| 적자 또는 N/A | 0 |
| > 50x | 2 |
| 35 ~ 50x | 6 |
| 25 ~ 35x | 10 |
| 18 ~ 25x | 14 |
| 12 ~ 18x | 17 |
| < 12x | 20 |

**PBR (20점)**

| PBR 범위 | 점수 |
|---------|------|
| < 0 (자본잠식) | 0 |
| > 15x | 2 |
| 8 ~ 15x | 6 |
| 4 ~ 8x | 10 |
| 2 ~ 4x | 14 |
| 1 ~ 2x | 17 |
| < 1x | 20 |

**ROE (25점)**

| ROE 범위 | 점수 |
|---------|------|
| < 0% | 0 |
| 0 ~ 5% | 4 |
| 5 ~ 10% | 8 |
| 10 ~ 15% | 12 |
| 15 ~ 25% | 18 |
| 25 ~ 50% | 22 |
| > 50% | 25 |

**Earnings Health (15점)**

| 조건 | 점수 |
|------|------|
| 적자 (NI < 0) | 0 |
| 흑자, NI 감소 | 7 |
| 흑자, NI 증가 또는 유지 | 15 |

### 9-7. 검증 계획

v1 구현 후 아래 순서로 검증:
1. 10종목 전 연도 v1 replay → v0 대비 점수 변화 분포 확인
2. NVDA: v0에서 WATCH였던 연도들이 v1에서 grade 상승하는가?
3. JPM/KO/XOM 같은 안정 성숙주: v1에서 grade 과도하게 변동하지 않는가?
4. Score ↔ Return / Alpha 상관계수 재계산 (전체 + Leave-one-out)
5. v0 vs v1 상관계수 비교

**주의:** NVDA FY2016이 v1에서 STRONG_BUY로 바뀐다면 "좋은 결과"가 아니라 "Momentum이 정말 독립적인 신호를 추가하는지" 확인이 필요한 케이스다. NVDA만 좋아지고 다른 종목이 나빠지면 overfitting.

---

## 8. 변경 이력

| 날짜 | 버전 | 내용 |
|------|------|------|
| 2026-09-05 | v0.1 | 초안. Value(PER+PBR) + Quality(ROE) + Earnings 3개 컴포넌트. |
| 2026-09-05 | v0.2 | (1) Absolute threshold 정의 수정 (2) ROE >50% penalty 제거 → `roe_extreme` flag로 대체 (3) `Earnings` → `Earnings Health` 명칭 변경 |
