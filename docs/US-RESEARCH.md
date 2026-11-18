# US Stock Engine — Research Log

## 설계 원칙

**Research-First**: 가설 → Historical Research → 검증 → 구현 가능 변경. 데이터 없이 엔진 설계하지 않는다.

**Engine Isolation**: 한국 엔진(v8.5.x)과 미국 엔진은 완전히 독립. 공통 모듈은 연구가 충분히 쌓인 후에만 추출.

**PIT Principle**: 연구 시점 T에서 이용 가능했던 데이터만 사용.
`fundamental_available_date = filed ≤ T`인 가장 최근 SEC filing의 filed date.

---

## Research Design Update (2026-09-08)
> **Status: 🟡 Current Protocol — 2026-09-08 확정. 이후 모든 연구의 기준**


US-2.5/US-2.6 결과를 반영하여 검증 전략을 업데이트한다.

### 이전 설계 → 변경

**이전**: n≥100 독립 관측 누적까지 Freeze (weight/threshold 변경 금지)

**변경**: 검증 프로토콜을 고정하고, 그 안에서 Universe 확장 및 검증 구조 개선을 계속한다.

변경 이유: 같은 HO 328행을 더 세밀하게 분석하면 과적합 위험이 있다. 120종목으로 진짜 새로운 cross-sectional 정보를 추가하는 것이 시간을 기다리는 것보다 낫다.

### 새 검증 프로토콜 (2026-09-08~)

1. **Universe 확장**: 50종목 → 120종목 (10 GICS × 12종목, 시총 상위 12/섹터)
   - 선정 기준: 2025-09-01 S&P 500 시총 상위 12/섹터. 기존 50종목 연속성 유지.
   - 기존 US-2.x 결과와 새 결과는 별개의 replication으로 기록 (기존 결과 수정 아님).
2. **Cluster bootstrap**: ticker를 cluster로 잡아 재표집. point estimate 유지, CI를 보수적으로 교정.
3. **Holdout year-by-year validation**: 2022 / 2023 / 2024를 각각 독립 test window로 평가.
   - IS 내부 walk-forward (2010-2018→2019 등)는 이미 hypothesis discovery에 사용된 데이터이므로 validation 근거로 사용하지 않는다.
4. **새 가설 사전 등록 허용**: 결과를 본 뒤 threshold / weight 조정 금지 원칙은 그대로 유지.

### 변경하지 않는 것

- v1 scoring 로직 (Value 40 + Quality 25 + EH 15 + Momentum 20)
- IS 경계 (score_fy ≤ 2021 = In-sample)
- 가설 평가 기준 (Corr 방향 + HO 재현 + outlier 강건성 + year-by-year 60% 이상)

---

## US-0 — Data Integrity (2026-09-05)
> **Status: 🟢 Applied — SEC EDGAR = US Research DB raw layer 채택**


### 목적

SEC EDGAR를 US Research DB의 raw layer로 채택할 수 있는지 검증.

### US-0.1 POC 결과 (2026-09-05)

**데이터 소스**: `data.sec.gov/api/xbrl/companyfacts/{CIK}.json`

| 테스트 | 결과 | 내용 |
|--------|------|------|
| T1 API | ✅ PASS | MSFT/GE companyfacts 정상 조회 |
| T2 Coverage | ✅ PASS | MSFT 20개 fiscal year (2007~2026) |
| T3 PIT 필드 | ✅ PASS | `end / filed / form / accn / val` 존재 |
| T4 Filing delay | ✅ PASS | 143/143 records에서 `filed > end` |
| T5 GE restatement | ✅ PASS | 동일 `end`에 복수 `filed/value` 이력 확인 |
| T6 Shares | ✅ PASS | `EntityCommonStockSharesOutstanding` 이력 존재 |
| T7 Reproducibility | ✅ PASS | 동일 조회 결과 일치 |
| T8 Coverage | ✅ PASS | MSFT 20년, GE 19년 |

**최종 판정: 🟢 SEC EDGAR = US Research DB raw layer 채택**

### T5 핵심 발견 — GE FY2017 Restatement

GE는 ASC 606 소급 적용(2018)과 사업부 매각(2019)으로 동일 기간(end=2017-12-31)의 revenue가 세 차례 다르게 기록되었다.

```
concept: Revenues (us-gaap)
end = 2017-12-31

filed = 2018-02-23  val = $122.092B  ← 원본 2017 10-K
filed = 2019-02-26  val = $118.243B  ← ASC 606 소급 수정 (2018 10-K)
filed = 2020-02-24  val =  $99.279B  ← 사업부 매각 후 재작성 (2019 10-K)
```

이 구조가 PIT 재구성 원칙을 실데이터로 증명한다:

```
as_of_date = 2018-03-01
    → filed ≤ 2018-03-01 최신값 → $122.092B  (당시 알 수 있던 값)

as_of_date = 2019-03-01
    → filed ≤ 2019-03-01 최신값 → $118.243B  (ASC 606 반영 후)

as_of_date = 2020-03-01
    → filed ≤ 2020-03-01 최신값 →  $99.279B  (구조조정 반영 후)
```

**이 결과는 미국 엔진의 PIT 설계 전체를 정당화하는 핵심 근거다.**

### SimFin 검토 결과 (참고)

| 항목 | 결과 |
|------|------|
| 무료 플랜 coverage | 5년 (2021~2025) — 10년 연구 불가 |
| `Publish Date` 필드 | 존재 |
| Restatement 테스트 | UNTESTED (coverage 부족으로 FY2017 조회 불가) |
| 결론 | 유료 전환 없이 PIT 보장 불확실 → 채택 보류 |

SimFin이 PIT를 보장하지 않는다고 증명된 것은 아님.
단, SEC EDGAR가 free + PIT 완전 보장이므로 유료 결제 불필요.

---

## US-0.2 — Raw DB 구축 (완료)
> **Status: 🟢 Applied — DB 구축 완료**


### 스크립트

| 파일 | 역할 |
|------|------|
| `research/us_0_2_build_raw.py` | SEC EDGAR → sec_raw_facts (SQLite) |
| `research/us_0_2_build_panel.py` | sec_raw_facts → PIT 재구성 + yfinance 가격 → research_panel |

실행 순서:
```bash
python3 research/us_0_2_build_raw.py     # Step 1: SEC raw 수집
pip install yfinance --break-system-packages
python3 research/us_0_2_build_panel.py   # Step 2: panel 구축
```

### 수집 대상

10개 S&P 500 종목 (섹터 다양성 확보):

| Ticker | Sector | Fiscal Year End |
|--------|--------|----------------|
| MSFT | Technology | June 30 |
| AAPL | Technology | September 30 |
| NVDA | Technology (Semiconductor) | January |
| AMZN | Consumer Discretionary / Cloud | December 31 |
| JPM | Financials | December 31 |
| UNH | Health Care | December 31 |
| XOM | Energy | December 31 |
| WMT | Consumer Staples | January |
| KO | Consumer Staples | December 31 |
| CAT | Industrials | December 31 |

### 수집 Concepts

| 항목 | Primary Concept | Fallback |
|------|----------------|---------|
| Revenue | `Revenues` | `RevenueFromContractWithCustomerExcludingAssessedTax` |
| Net Income | `NetIncomeLoss` | — |
| Total Equity | `StockholdersEquity` | `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest` |
| Operating Income | `OperatingIncomeLoss` | — |
| Total Assets | `Assets` | — |
| Shares | DEI `EntityCommonStockSharesOutstanding` | `CommonStockSharesOutstanding` |

### DB Schema

**sec_raw_facts** (SEC 원천 데이터, 변형 없음):
```sql
ticker, cik, concept, unit, start, end, filed, form, accn, fy, fp, frame, val
UNIQUE (ticker, concept, unit, end, filed, accn)
```

**research_panel** (PIT 재구성 + 가격 결합):
```sql
ticker, fiscal_year, as_of_date,
fundamental_available_date,           -- max(filed) ≤ as_of_date
revenue, net_income, total_equity, operating_income, total_assets,
shares_outstanding,
price_as_of, market_cap,
per, pbr, roe,
price_fy_start, price_fy_end, annual_return,
sp500_return, alpha,
earnings_sign                         -- 1 / -1 / 0
UNIQUE (ticker, fiscal_year, as_of_date)
```

### PIT 규칙

```
as_of_date = fiscal_year_end + 90일  (10-K SEC 공시 기한 + 버퍼)
fundamental_available_date = max(filed) where filed ≤ as_of_date
```

GE FY2017 예시로 검증된 것처럼, 동일 fiscal_year라도 as_of_date가 다르면
`fundamental_available_date`와 `val`이 달라진다.

---

---

## US-0.3 — v0 Scorer 구현 및 검증 (2026-09-05)
> **Status: 🟢 Applied — v0 Scorer 구현 완료**


**스크립트**: `research/us_0_3_score.py`
**기준 문서**: `docs/US-SCORING-DESIGN.md v0.2`
**데이터**: research_panel 166행 (10종목 × 연도별, annual_return 있는 것)

### v0 채점 구조

```
US Score v0 (100점)
  Value 50pt: PER 25pt + PBR 25pt
  Quality 30pt: ROE 30pt
  Earnings Health 20pt: 흑자 여부 + YoY NI 방향
```

### v0 상관계수

| 지표 | 값 |
|------|---|
| Score ↔ 연간 수익률 | **-0.288** |
| Score ↔ Alpha | **-0.297** |
| n | 166 |

### Leave-one-out 민감도 분석

| 구성 | n | Corr(score, return) |
|------|---|-------------------|
| 전체 | 166 | -0.288 |
| NVDA 제외 | 151 | -0.150 |
| AMZN 제외 | 149 | -0.347 |
| NVDA+AMZN 제외 | 134 | **-0.097** |

**결론 (확정):**

v0의 음의 상관계수는 모델 전체의 구조적 역신호가 아니다.
NVDA+AMZN 제외 시 -0.097로 수렴 → NVDA 효과가 지배적.

NVDA 제거 시 WATCH 수익률 +79.5% → +21.0% 정상화.
AMZN은 제거해도 악화(-0.347): AMZN의 적자 성장기(WATCH)는 수익이 낮았고
제거하면 NVDA의 WATCH 고수익만 남아 역설적으로 나빠짐.

### v0의 구조적 blind spot

> "valuation이 비싸도 earnings growth가 급격히 커지는 기업"은 v0 구조상 포착 불가.
> NVDA FY2016 (PER=100x): Score=35(WATCH) → 수익률 +268%
> NVDA FY2023 (PER=494x): Score=37(WATCH) → 수익률 +343%

이 blind spot은 v0 설계상 정상. Momentum 팩터 추가(v1)로 보완 가능한지 검증 필요.

---

## US-0.4 — v1 Momentum 검증 (2026-09-05)
> **Status: 🔵 Closed — Momentum 효과 불명확. US-2.6/US-2.7A에서 재검증됨**


**스크립트**: `research/us_0_4_score_v1.py`
**기준 문서**: `docs/US-SCORING-DESIGN.md §9`

### v1 채점 구조

```
US Score v1 (100점)
  Value 40pt: PER 20pt + PBR 20pt
  Quality 25pt: ROE 25pt
  Earnings Health 15pt
  Momentum 20pt: 12-2 momentum
```

### Momentum 정의 및 구현 한계

**설계 정의**: `price(T-1M) / price(T-12M) - 1`  (T = as_of_date, 정확한 12-2 momentum)

**실제 구현**: `price_as_of(FY) / price_as_of(FY-1) - 1`  (DB-based 12M 근사)

**한계 및 근거:**
- 1차 시도(`us_0_4_score_v1.py`)는 yfinance API 403 차단으로 모든 T-1M/T-12M 가격 조회 실패 → Momentum = N/A (전부 중립 8점). 사실상 v0 재배분 효과만 측정했고 Momentum 검증 불가.
- 2차: `research_panel.price_as_of`(기존 DB 보유 데이터)를 이용한 근사. FY별 as_of_date 간격이 ~12개월이므로 연간 가격 변화율로 12M momentum을 근사.
- `price_as_of(FY) / price_as_of(FY-1)`는 정확한 12-2가 아니다 (T-1M 제외 없음, 날짜 오차 존재). **연구 검증용 proxy이며 production 정의로 확정하지 않는다.**
- 네트워크 복구 시 정확한 `price(T-1M) / price(T-12M)` 으로 재검증 필요.

FY 없는 첫 연도(12개): neutral(8점) 처리. 유효 데이터 154개/166개.

### v0 vs v1 상관계수 비교

| 구성 | v0 | v1 | Δ |
|------|----|----|---|
| 전체 (n=166) | -0.288 | **-0.051** | +0.237 |
| NVDA 제외 | -0.150 | **+0.109** | +0.260 |
| AMZN 제외 | -0.347 | -0.054 | +0.292 |
| NVDA+AMZN 제외 | -0.097 | **+0.248** | +0.346 |

### Grade별 평균수익률

| Grade | v0 | v1 |
|-------|----|----|
| STRONG_BUY | +20.4% (n=91) | +27.8% (n=77) |
| BUY | +29.4% (n=40) | +26.4% (n=61) |
| HOLD | +57.6% (n=23) | +73.0% (n=20) |
| WATCH | **+79.5%** (n=12) | **+12.0%** (n=8) |

WATCH 정상화가 핵심: NVDA 고수익 연도들이 WATCH→HOLD로 이동. +79.5% → +12.0%.

HOLD가 여전히 높은 이유: NVDA FY2016/2020/2023이 HOLD로 편입됐고 이들이 +200~343% 수익을 냄.
**이는 설계상 정상 결과** — 비싼 주식은 강한 momentum이 있어도 HOLD가 최대. Value와 Momentum의 tradeoff가 의도대로 작동.

### Momentum 구간별 평균수익률 (핵심 증거)

| Momentum 구간 | 평균 수익률 | n |
|--------------|------------|---|
| ≥ +30% | **+69.2%** | 54 |
| +10% ~ +30% | +23.5% | 41 |
| -10% ~ +10% | +7.3% | 48 |
| -30% ~ -10% | -10.9% | 7 |
| < -30% | **-42.5%** | 4 |

5개 구간 단조감소. Momentum이 Value/Quality와 독립적으로 수익률을 구분한다.

### NVDA blind spot 보완 여부

| FY | mom% | v0 grade | v1 grade | 수익률 |
|----|------|---------|---------|-------|
| 2016 | +202% | WATCH | HOLD | +268% |
| 2020 | +106% | WATCH | HOLD | +154% |
| 2023 | +199% | WATCH | HOLD | +343% |
| 2011 | **-35%** | **STRONG_BUY** | **BUY** | **-46%** |

NVDA가 WATCH→HOLD로 상승 (부분 보완). STRONG_BUY 불가능 — 설계 의도에 맞음.
FY2011: 음의 momentum이 STRONG_BUY→BUY로 정확히 다운그레이드 (-46% 수익과 일치).

### 결론

DB-based 12M momentum 근사에서 다음 패턴이 관찰되었다:

1. **단조 수익률 관계**: Momentum 구간별 평균수익률이 5구간 모두 단조감소. 독립 신호 가능성 지지.
2. **상관계수 개선**: -0.288 → -0.051 (Δ+0.237). NVDA+AMZN 제외 시 +0.248로 양수 전환.
3. **WATCH 정상화**: +79.5% → +12.0%. NVDA 고수익 연도들이 WATCH→HOLD로 이동.
4. **음의 momentum 작동**: NVDA FY2011 (-35%) STRONG_BUY→BUY, ret=-46%. 올바른 다운그레이드.
5. **완전 포착 불가**: NVDA FY2016/2023 → HOLD (STRONG_BUY 아님). 비싼 주식의 momentum 상한이 HOLD인 것은 Value-Momentum tradeoff 설계상 정상.

**채택 결정:**
> DB-based 12M momentum 근사에서 강한 단조 수익률 관계가 관찰되었으며, v1 Momentum을 baseline으로 채택한다.
> 단, 현재 결과는 근사치 기반이므로 정확한 12-2 momentum 및 forward sample에서 추가 검증한다.

**다음 단계:** Momentum 효과 vs 재배분 효과 분리 검증 (US-0.4b).

---

## US-0.4b — Momentum 효과 vs 재배분 효과 분리 검증 (2026-09-05)
> **Status: 🔵 Closed — 재배분 효과와 분리 검증. US-2.6에서 대체됨**


v0→v1 상관계수 개선폭(+0.237)이 Momentum 신호 자체에서 온 것인지,
단순한 배점 재배분의 수학적 효과인지를 counterfactual로 분리했다.

### 4가지 변형

| 버전 | 구조 | 설명 |
|------|------|------|
| ① v0 | 50V+30Q+20EH | baseline |
| ② redist-only | 40V+25Q+15EH + **8 고정** | 재배분만, Momentum 신호 없음 |
| ③ v1-full | 40V+25Q+15EH + **실제 M** | v1 완성 |
| ④ v0+Momentum | 50V+30Q+20EH + **실제 M** (120점) | 재배분 없이 M만 추가 |

### 결과 (Corr(score, return), n=166)

| 버전 | 전체 | NVDA 제외 | NVDA+AMZN 제외 |
|------|------|-----------|--------------|
| ① v0 | -0.288 | -0.150 | -0.097 |
| ② redist-only | -0.283 | -0.148 | -0.097 |
| ③ v1-full | **-0.051** | **+0.109** | **+0.248** |
| ④ v0+Momentum | -0.106 | +0.054 | +0.183 |

### 기여 분해

| 변화 경로 | Δ상관계수 |
|---------|---------|
| 재배분 효과만 (①→②) | **+0.005** |
| Momentum 추가 효과 (②→③) | **+0.232** |
| Momentum 순효과, 재배분 없음 (①→④) | **+0.181** |
| 총 변화 (①→③) | **+0.237** |

v0→v1 개선폭 중 **재배분이 발생시킨 비율: 1.9% / Momentum 추가 단계에서 발생한 비율: 98.1%**.

**주의**: 이는 이번 counterfactual replay에서 관찰된 실험적 기여 분해다. Momentum의 일반적 인과효과가 98.1%라는 추정이 아니다.

### 해석

**재배분(①→②)**: +0.005 — 배점 재배분만으로는 상관계수가 거의 변하지 않는다. 수학적 착시 효과가 아님.

**Momentum 순효과(①→④)**: +0.181 — v0 배점을 그대로 유지하고 Momentum만 추가해도 유의미하게 개선된다.

**③이 ④보다 좋은 이유**: v1의 재배분(Value 50→40)은 Value의 noise 비중을 줄여 Momentum 신호가 더 잘 드러나는 환경을 만든다. 재배분은 독립적 개선 요인이 아니라 **Momentum 신호의 증폭 보조**.

> **US-0.4b 결론:**  
> v0→v1 상관계수 개선의 핵심 원인은 Momentum 신호 자체다.  
> 재배분은 신호가 더 잘 드러나게 하는 보조 역할이며, 특정 종목 패치 효과는 없다.

---

## 연구 로드맵
> **Status: 🔵 Closed — Historical roadmap. Research Design Update(2026-09-08)로 대체됨**


```
US-0  Data Integrity + Baseline Scoring
  US-0.1  SEC EDGAR PIT feasibility        ✅ COMPLETE
  US-0.2  10종목 raw DB + panel 구축        ✅ COMPLETE
  US-0.3  v0 Scorer (Value+Quality+EH)     ✅ COMPLETE — Corr=-0.288, blind spot 확인
  US-0.4  v1 Momentum 검증                 ✅ COMPLETE — Corr=-0.051, 독립 신호 확인
  US-0.4b Momentum vs 재배분 효과 분리      ✅ COMPLETE — 개선 98.1%가 Momentum 단계에서 발생

US-0.5  Universe Expansion (50종목)        ✅ COMPLETE — 50종목 v0 Corr=-0.121, v1 Corr=+0.048 (양전환)
  목표: 10종목 결과를 더 큰 표본에서 검증
  설계: 10 GICS 섹터 × 5종목 = 50종목
  검증 항목:
    - v0/v1 replay (동일 scoring 그대로)
    - Momentum 구간별 단조성 재검증
    - 상관계수 + grade 방향성 + outlier sensitivity
  → 선정 알고리즘 별도 문서화 (아래 US-0.5 섹션)

US-0.6  Forward Panel 구축 + 결과           → ✅ COMPLETE
US-0.7  H-US-1 Counterfactual (Energy 제외) → ✅ COMPLETE
US-0.8  Mean Reversion Test                → ✅ COMPLETE — 가설 기각
US-0.9  Grade 역전 Decomposition           → ✅ COMPLETE — 고성장주 False Negative 확인
US-1.0  H-US-7 Growth-stock Exclusion      → ✅ COMPLETE — TSLA outlier + HOLD>BUY 분리
US-1.1  HOLD>BUY 원인 탐색                 → ✅ COMPLETE — 데이터버그+경계효과+우주특성
US-1.2  H-US-5 데이터 복구 (GOOGL/META)   → ✅ COMPLETE — 역전 유지 확인, 구조적 원인
  목표: score(FY N) → return(FY N+1) forward-looking 예측력 프레임워크 설계
  검증 프로토콜 고정 — scoring 규칙 변경 금지, Universe 확장 + cluster bootstrap으로 검증 계속

US-1~6  팩터 개별 연구 (Academic Prior 채택으로 skip → v1 구현으로 직행)

US-2.0  Research Design Update             → ✅ 2026-09-08 — 검증 프로토콜 고정으로 전환
US-2.1  H-US-11: G3 신호 분해              → ✅ COMPLETE — FCF Growth IS outlier 의존 확인
US-2.2  H-US-12: SC≥2 binary threshold     → ✅ COMPLETE (2차) — 기각, 확장 표본에서 사라짐
US-2.3  H-US-13: SC≥2 섹터 중립성          → ✅ COMPLETE (2차) — 강하게 기각
US-2.4a H-US-14: FCF Growth 확장 표본 재현  → ✅ COMPLETE — FAIL, 동일 Cyclical 메커니즘
US-2.5  H-US-15: Low Score 역전 분해        → ✅ COMPLETE — 4개 통제 후에도 유지, MOM만 예외
US-2.6  H-US-16: Momentum 독립 검증         → ✅ COMPLETE — Weak 판정
US-2.7  120종목 Universe 확장 + cluster bootstrap → 🔲 예정
```

---

*Research principle: 검증되지 않은 팩터를 production score에 반영하지 않는다.*

---

## US-0.5 — Universe Expansion 설계 (2026-09-05)
> **Status: 🔵 Closed — 50종목 확장 설계. US-2.7A에서 248 universe로 발전**


### 목적

10종목 v0/v1 결과의 일반화 가능성 검증. 특정 종목(NVDA 등) 의존성 완화.

### 구조

10 GICS 섹터 × 5종목 = 50종목  
Real Estate 제외 이유: REITs는 FFO 기반 수익 측정, PBR 해석 상이.

### 선정 알고리즘 (결정론적, 사전 고정)

- **기준일**: 2025-09-01
- **데이터**: S&P 500 구성 종목 (2025-09-01 기준)
- **순위**: GICS 섹터별 시가총액 상위 5개
- **예외 규칙**: 기존 10종목은 강제 포함, 필요 시 5위 대체
- **추가 조건**: SEC EDGAR CIK 확인 가능, ADR/외국계 제외

### 금지 사항

- 과거 replay 결과를 보고 종목을 교체하는 것
- "Momentum이 잘 먹힌 종목을 더 넣자" 식의 결과 기반 선정
- 기준일(2025-09-01) 이후 정보를 활용한 종목 변경

### 검증 항목

1. v0/v1 replay (동일 scoring 그대로)
2. Momentum 구간별 단조성 재검증
3. Corr 개선폭 기여 분해
4. Grade 방향성
5. Outlier sensitivity
6. 섹터별 Momentum 단조성

### 50종목 리스트

기준: 2025-09-01 S&P 500 구성종목, GICS 섹터별 시총 상위 5  
(네트워크 제약으로 역사적 시총 직접 조회 불가 → 2025-Q3 공개 데이터 기반 추정)  
기존 10종목 모두 섹터 내 상위 5에 자연 포함되어 강제 대체 없음.

| GICS 섹터 | Ticker 1 | Ticker 2 | Ticker 3 | Ticker 4 | Ticker 5 |
|-----------|----------|----------|----------|----------|----------|
| Information Technology  | MSFT ✓ | AAPL ✓ | NVDA ✓ | AVGO | ORCL |
| Communication Services  | GOOGL  | META   | NFLX   | TMUS  | DIS  |
| Consumer Discretionary  | AMZN ✓ | TSLA   | HD     | MCD   | BKNG |
| Consumer Staples        | WMT ✓  | COST   | PG     | KO ✓  | PEP  |
| Energy                  | XOM ✓  | CVX    | COP    | EOG   | PSX  |
| Financials              | BRK-B  | JPM ✓  | V      | MA    | BAC  |
| Health Care             | LLY    | UNH ✓  | JNJ    | ABBV  | MRK  |
| Industrials             | GE     | CAT ✓  | RTX    | HON   | DE   |
| Materials               | LIN    | SHW    | APD    | ECL   | NEM  |
| Utilities               | NEE    | SO     | DUK    | AEP   | SRE  |

✓ = 기존 10종목 포함. 신규 40종목: AVGO ORCL GOOGL META NFLX TMUS DIS  
TSLA HD MCD BKNG COST PG PEP CVX COP EOG PSX BRK-B V MA BAC  
LLY JNJ ABBV MRK GE RTX HON DE LIN SHW APD ECL NEM NEE SO DUK AEP SRE

---

## US-0.5 — Universe Expansion 결과 (2026-09-05)
> **Status: 🔵 Closed — 결과 기록. 후속 연구로 통합됨**


### 데이터

- 50종목 × 평균 15.7 FY = 789행 (scored_panel), 787행 (annual_return 있음)
- 10 GICS 섹터 × 5종목, 기간 약 FY2007~FY2025

### Score ↔ Return 상관계수

| | v0 | v1 | Δ |
|--|--|--|--|
| 전체 (n=787) | -0.121 | **+0.048** | **+0.169** |
| NVDA 제외 | -0.098 | +0.067 | +0.165 |
| AMZN 제외 | -0.119 | +0.051 | +0.170 |
| NVDA+AMZN 제외 | -0.094 | +0.072 | +0.166 |

10종목 패턴 재확인: NVDA/AMZN 제외 여부와 무관하게 v1이 일관되게 개선됨.  
v1이 50종목에서 처음으로 **양수 전환** (10종목 v1=-0.051 → 50종목 v1=+0.048).

### Grade별 평균 수익률 (v1)

| Grade | 평균 수익률 | n | 단조성 |
|-------|------------|---|-------|
| STRONG_BUY | +29.3% | 314 | ✅ |
| BUY | +25.3% | 264 | ✅ |
| HOLD | +39.1% | 119 | ❌ |
| WATCH | +19.0% | 90 | ✅ |

HOLD > BUY: TSLA FY2020 (+698.3%, v1=HOLD) 단일 이상치 영향.  
STRONG_BUY > BUY > WATCH 방향성은 확인됨.

### 신규 발견 — 구조적 한계 및 데이터 이슈

**Energy false positive 재확인 (CLAUDE.md Known False Positive 일치)**  
- COP FY2019: STRONG_BUY(v0=92), 실제 수익률 -51.6%  
- EOG FY2019: STRONG_BUY(v0=75), 실제 수익률 -59.3%  
→ 에너지 사이클 하강기에 저PER+저PBR이 저평가 신호로 오독됨.

**데이터 이슈 (점수 왜곡 요인)**  
- GOOGL/META: PER=N/A (SEC DEI shares 조회 실패 → EPS 계산 불가)  
- BRK-B: PER=0, PBR=0 (Berkshire Hathaway 지주회사 구조 — 단순 EPS/BV 해석 부적절)  
- MCD/BKNG: PBR 음수 (자사주 매입 누적 → equity 음수, 자본잠식 아닌 구조적 현상)  
- APD FY2025: PER=-137 (일회성 손실 추정 → earnings_sign 필터로 0점 처리됨)

**TSLA 모멘텀 구조**  
- TSLA mom avg +87.5% — 가장 높은 모멘텀이지만 펀더멘털 점수 낮음 (적자 성장주)  
- CLAUDE.md "적자 성장주: 이 시스템 적용 범위 밖"에 해당

### 해석

v0→v1 Corr 개선폭(+0.169)이 50종목에서도 일관되게 나타남.  
10종목 결과(+0.237)보다 폭이 줄어든 것은 NVDA 단일 효과 희석에 기인 (NVDA 제외 시 10종목 개선폭 축소됨).  
50종목에서의 v1 양전환은 Momentum 신호가 NVDA 이외에서도 작동함을 시사한다.  
단, n=787, 연도별 중복 포함 — 통계적 독립성 없음. 방향성 참고 수준.

> **US-0.5 결론:**  
> 10종목 v1 패턴이 50종목에서 재현됨. v1(Momentum 포함) Corr 양전환 확인.  
> 에너지 섹터 false positive, 지주회사/자사주 구조 이슈는 별도 프레임워크 대상.  
> 다음 단계: n≥100 Calibration 데이터 누적 (엔진 Freeze 유지).

---

## US-0.6 — Calibration Design (2026-09-05)
> **Status: 🔵 Closed — Calibration 설계. Research Design Update로 대체됨**


### 목적

지금까지의 연구(US-0~US-0.5)는 **score(FY N) vs return(FY N)** — 즉 같은 연도의 점수와 수익률의 상관관계를 분석했다.  
이것은 방향성 확인에는 유효하지만 진정한 예측력 검증이 아니다.

진짜 예측력 = **score(FY N) → return(FY N+1)**: 점수가 다음 연도 수익률을 얼마나 예측하는가.

US-0.6의 목적은 이 forward-looking 프레임워크를 설계하고, 그 위에서 threshold/weight 조정 여부를 판단하는 Calibration 기준을 확립하는 것이다.

### 1. Calibration Sample 정의

**In-sample (연구·분석용)**: FY2007~FY2022 (50종목 × 약 15개 연도)  
**Forward holdout (예측력 검증용)**: FY2023~FY2025 (3개 연도)  
→ Holdout은 threshold/weight 조정 결정에 **절대 사용하지 않는다**.  
→ 조정 결정 후 사후 검증에만 사용한다.

주의: 50종목 × 15년 = 750행이지만 동일 종목·동일 연도 중복으로 통계적 독립성 없음.  
결과는 방향성 참고 수준이며, 단일 수치로 결론 내지 않는다.

### 2. 최적화 대상 정의

**Primary**: Grade 단조성  
  → STRONG_BUY 평균 1Y forward return > BUY > HOLD > WATCH  
  → 4개 등급 모두 단조적일 때 "단조성 확인"

**Secondary**: Corr(score(FY N), return(FY N+1))  
  → 양수이면서 Grade 단조성과 일관될 것

**Tertiary**: Win rate (STRONG_BUY 등급 중 S&P 500 초과 수익 비율)

이 셋이 모두 같은 방향을 가리킬 때만 threshold/weight 변경을 논의한다.  
하나라도 반대 방향이면 "데이터 부족" 판정.

### 3. Threshold/Weight 변경 허용 범위

**현재 상태: 검증 프로토콜 고정 (2026-09-08 업데이트)**

변경 허용 조건 (모두 충족 시):
- Primary/Secondary/Tertiary 세 지표 모두 일관된 방향 확인
- IS + HO 방향 일치 (HO에서 IS 패턴 재현)
- Year-by-year Hi>Lo ≥ 60%
- Outlier 제거 후에도 방향 유지

**변경 1회당 1개 파라미터**. 여러 파라미터를 동시에 조정하지 않는다.  
변경 후 최소 20개 신규 관측 추가 전까지 다음 변경 금지.

### 4. Overfitting 방지 규칙

- US-0.5에서 발견한 사례(Energy false positive, BRK-B, GOOGL/META, MCD/BKNG, TSLA)를 즉시 penalty로 만들지 않는다.
- 특정 종목의 이상 결과를 보고 해당 종목에만 적용되는 예외를 추가하지 않는다.
- 변경은 항상 "모든 종목에 동일하게 적용되는 규칙"이어야 한다.
- In-sample 성과가 개선되더라도 Forward holdout 검증 없이 채택하지 않는다.

### 5. Forward Validation 구조

```
score(FY N) 계산 시점: as_of_date(N) = FY N end + 90d
forward return(FY N+1): as_of_date(N) → as_of_date(N+1) 기간 수익률
benchmark: S&P 500 동기간 수익률
forward alpha(FY N+1): forward return - S&P 500 return
```

구현: `research/us_0_6_forward_panel.py` (완료 2026-09-05)

### 6. 기존 발견 → 가설 등록 (점수 반영 금지)

US-0.5에서 확인된 구조적 이슈들은 **가설 목록**으로 보관한다.

| 가설 ID | 내용 | 현재 상태 |
|---------|------|----------|
| H-US-1 | Energy 섹터 사이클 바닥에서 저PER+저PBR = false positive | 관찰됨, 미검증 |
| H-US-2 | 지주회사(BRK-B) PER/PBR 해석 부적절 | 데이터 이슈, 미검증 |
| H-US-3 | Buyback 누적 음수 equity(MCD/BKNG) PBR 음수 처리 | 데이터 이슈, 미검증 |
| H-US-4 | 대형 적자 성장주(TSLA) 모멘텀 과대평가 | 관찰됨, 미검증 |
| H-US-5 | DEI shares 조회 실패(GOOGL/META) PER 누락 | **수정 대상 확인** (US-1.1) |
| H-US-6 | 일회성 손실(APD) earnings_sign 필터 효과 확인 필요 | 관찰됨, 미검증 |

검증 기준: 각 가설당 n ≥ 30 관측 + 방향성 일관 시 score 반영 후보.  
현재는 모두 "Experimental" 상태. 점수에 반영하지 않는다.

### 7. v1 검증 프로토콜 고정

```
엔진 버전: v1.0 (Value 40 + Quality 25 + EH 15 + Momentum 20)
상태: 검증 프로토콜 고정 (2026-09-08~)
```

허용:
- 버그 수정 (계산 오류, 데이터 오류)
- Universe 확장 (120종목)
- 새 가설 사전 등록 후 검증
- Cluster bootstrap, year-by-year HO 분석

금지:
- Weight / threshold 조정
- 팩터 추가 / 삭제
- IS 결과만 보고 scoring 변경

## US-0.6 — Forward Panel 결과 (2026-09-05)
> **Status: 🔵 Closed — Forward panel 결과 기록**


### 실행

```
python3 research/us_0_6_forward_panel.py
```

- forward_panel: 729행 (FY+1 없음: 58, 가격 없음: 0)
- In-sample (score_fy ≤ 2021): 578행
- Holdout (score_fy ≥ 2022): 151행
### 핵심 결과

**In-sample (score_fy ≤ 2021, n=578)**

| Grade | n | Fwd Ret% | Fwd Alpha% |
|-------|---|----------|------------|
| STRONG_BUY | 268 | 14.0% | 4.5% |
| BUY | 158 | 16.3% | 3.6% |
| HOLD | 87 | 27.2% | 15.3% |
| WATCH | 65 | 38.3% | 25.8% |

- Corr(score, return) = **-0.201** / Corr(score, alpha) = **-0.185**
- Grade 단조성: ❌ (역순 — WATCH > HOLD > BUY > STRONG_BUY)

**Holdout (score_fy ≥ 2022, n=151)**

| Grade | n | Fwd Ret% | Fwd Alpha% |
|-------|---|----------|------------|
| STRONG_BUY | 36 | 20.2% | 3.5% |
| BUY | 75 | 26.6% | 8.1% |
| HOLD | 25 | 23.7% | 6.4% |
| WATCH | 15 | 27.8% | 8.4% |

- Corr(score, return) = **-0.054** / Corr(score, alpha) = **-0.033**
- Grade 단조성: ❌ (역순, 표본 적어 불안정)

### 해석

forward Corr이 음수인 것은 same-year Corr(+0.048, US-0.5)와 다르다.
이 차이가 의미하는 바:

1. **Mean reversion 효과**: WATCH 등급을 받은 종목은 이미 나쁜 해를 보냈다(그래서 WATCH).
   다음 해에 평균 회귀로 높은 수익률을 기록할 가능성이 높다.

2. **Cyclical false positive**: Energy, 소재 등 사이클 섹터가 저PER·저PBR에서 STRONG_BUY를
   받지만, 다음 해 업황 하강으로 underperform한다. H-US-1 가설과 일치.

3. **Peak quality 역설**: 좋은 기업은 이미 좋은 해에 고평가되어 있다.
   다음 해 기저효과로 수익률이 낮아진다.

4. **Note**: S&P 500 alpha 계산 미완료 (forward_sp500=NULL). Corr은 절대수익률 기준.
   alpha 기준으로 재측정 시 결과가 달라질 수 있다.

### 해석 추가 (alpha 확인 후)

alpha 기준으로도 동일하게 역전: In-sample WATCH alpha +25.8% > STRONG_BUY +4.5%.  
Energy 종목 패턴이 주원인으로 강하게 시사됨:
- Energy STRONG_BUY 해(호황) → 이듬해 급락 (COP/EOG/XOM/PSX FY2018: -40~-63%)
- Energy WATCH 해(불황) → 이듬해 급등 (COP FY2020: +95%, EOG: +76%, XOM: +56%)

이 사이클 왜곡이 grade 역전을 주도하고 있을 가능성이 높다. H-US-1 예비 지지 증거.

### 다음 단계 (US-0.7)

- Energy 5종목 제외 후 In-sample Corr / Grade 단조성 재계산 (H-US-1 counterfactual)
- 결과 따라 US-0.7 방향 결정: 섹터 필터 실험 vs. 현행 유지


## US-0.7 — H-US-1 Counterfactual 결과 (2026-09-05)
> **Status: 🔵 Closed — Architecture counterfactual 결과**


### 실행

```
python3 research/us_0_7_energy_excl.py
```

Energy 5종목 제외 (XOM, CVX, COP, EOG, PSX): 729행 → 653행 (제거 76행)

### 핵심 결과

**In-sample 비교 (score_fy ≤ 2021)**

| | Baseline (n=578) | Excl-Energy (n=517) |
|-|------------------|---------------------|
| Corr(score, alpha) | -0.185 | -0.167 |
| STRONG_BUY alpha | +4.5% | +5.9% |
| BUY alpha | +3.6% | +3.0% |
| HOLD alpha | +15.3% | +16.2% |
| WATCH alpha | +25.8% | +25.9% |
| Grade 단조성 | ❌ | ❌ |

**판정**: 🟡 H-US-1 일부 설명 가능, 단독 원인 아님

Energy 제외 후 Corr이 -0.185 → -0.167로 미미하게 개선됨으나,
HOLD >> STRONG_BUY >> BUY 역전 패턴은 유지됨.
Energy 76행을 제거해도 근본 구조가 변하지 않는다.

### 해석

**H-US-1 상태 업데이트**: "관찰됨, 미검증" → "Energy가 주원인 아님 확인, 부분 기여 가능성"

근본 원인은 더 구조적임:

1. **Mean reversion dominant**: 나쁜 해를 보낸 종목 → 낮은 점수(WATCH/HOLD) → 다음 해 평균 회귀로 높은 수익.
   이 시스템의 scoring은 "현재 quality"를 측정하는데, 나쁜 해가 끝난 시점이 저점이 되어 forward 수익이 높아진다.

2. **Peak quality 역설**: STRONG_BUY 받는 시점 = 이미 좋은 해가 다 지난 후.
   펀더멘털이 정점일 때 점수가 높고, 이듬해 기저효과로 수익률이 낮아진다.

3. **같은 연도 Corr(+0.048) vs Forward Corr(-0.185) 괴리**: 현재 quality 측정은 유효하나,
   미래 수익률 예측력은 별개 문제임을 확인.

### H-US-1 가설 상태 업데이트

| 항목 | 이전 | 현재 |
|------|------|------|
| H-US-1 상태 | 관찰됨, 미검증 | Energy가 주원인 아님. 부분 기여 가능성. |
| 주원인 후보 | Energy false positive | Mean reversion + peak quality 효과 |
| score 반영 여부 | 미반영 | 미반영 유지 |

### 다음 단계 후보

- **grade 역전의 실제 구성 분석**: HOLD/WATCH 고수익이 어떤 종목/섹터에서 오는지 decomposition
- **Mean reversion 검증**: 직전 연도 수익률이 낮았던 종목의 다음 해 수익률 분포 확인
- 결과 종합 후 US-0.8 방향 결정


## US-0.8 — Mean Reversion Test 결과 (2026-09-05)
> **Status: 🔵 Closed — Mean reversion 검증**


### 실행

```
python3 research/us_0_8_mean_reversion.py
```

### 핵심 결과 — In-sample (n=578)

[1] Corr(prior_return, forward)

- Corr(prior, fwd_return) = **+0.045** (양수 — mean reversion이면 음수여야)
- Corr(prior, fwd_alpha)  = **+0.072** (양수)

[2] Corr(score_v1, prior_return) = **-0.010** (≈ 0)

점수는 직전 연도 성과를 포착하지 않음. 점수와 prior return은 무관.

[3] Prior return 구간별 forward alpha

| Bucket | Avg Prior% | Fwd Alpha% |
|--------|-----------|------------|
| Q1 (최하위 25%) | -6.9% | +8.3% |
| Q2 | +14.2% | +3.1% |
| Q3 | +29.2% | +7.1% |
| Q4 (최상위 25%) | +78.2% | +14.6% |

Q4 alpha(+14.6%) > Q1 alpha(+8.3%) — Q1이 가장 낮지 않음. 역전 패턴 없음.

[4] Grade별 avg prior_return

| Grade | Avg Prior% | Avg Fwd Alpha% |
|-------|-----------|----------------|
| STRONG_BUY | +27.3% | +4.5% |
| BUY | +23.8% | +3.6% |
| HOLD | +43.9% | +15.3% |
| WATCH | +25.0% | +25.8% |

HOLD grade 종목이 직전에 가장 좋은 해(+43.9%)를 보냈음에도 forward alpha도 최상위.
WATCH prior_return(+25.0%)은 STRONG_BUY(+27.3%)와 비슷 — mean reversion 구조 아님.

### 판정

**Mean reversion 가설 기각**

사전 기준:
- Corr(prior, fwd_return) < -0.1 → 실제 +0.045 (불성립)
- Q1 forward > Q4 → 실제 Q1 +8.3% < Q4 +14.6% (불성립)

두 조건 모두 불성립. Grade 역전의 원인이 mean reversion이 아님을 확인.

### 현황 정리

| 가설 | 상태 |
|------|------|
| H-US-1 Energy false positive | 부분 기여 가능, 단독 원인 아님 (US-0.7) |
| Mean reversion | **기각** (US-0.8) |
| 섹터/시기 구성 효과 | 미검증 — 다음 후보 |

HOLD 등급의 +43.9% avg prior + forward alpha +15.3%는 구성 종목/시기 분포 문제일 가능성.
어떤 종목이 어떤 연도에 HOLD를 받았는지 decomposition이 필요.

### 다음 단계 (US-0.9 후보)

- HOLD/WATCH 등급 구성 종목 × 연도 decomposition
- 시기 효과 확인: 특정 연도(예: 2020~2021 반등장)에 HOLD/WATCH가 집중되어 있는가
- 섹터 효과: HOLD/WATCH에 어떤 섹터가 몰려 있는가


## US-0.9 — Grade 역전 원인 Decomposition 결과 (2026-09-05)
> **Status: 🔵 Closed — Grade 역전 원인 분해**


### 실행

```
python3 research/us_0_9_decomposition.py
```

### 핵심 발견

**Grade 역전의 주원인 = 고성장주 False Negative**

HOLD/WATCH 상위 alpha 종목:

| Ticker | FY | Grade | Sector | Fwd Alpha% |
|--------|-----|-------|--------|------------|
| TSLA | 2019 | WATCH | Consumer_D | +513.8% |
| TSLA | 2012 | WATCH | Consumer_D | +430.8% |
| NFLX | 2009 | HOLD | Comm | +209.1% |
| NVDA | 2015 | HOLD | IT | +186.7% |
| NVDA | 2016 | HOLD | IT | +102.5% |
| NFLX | 2016 | HOLD | Comm | +88.0% |
| TSLA | 2018 | WATCH | Consumer_D | +86.8% |

섹터별 평균 forward alpha:

| Sector | STRONG_BUY n | HOLD n | WATCH n | Avg Alpha% |
|--------|-------------|--------|---------|------------|
| Consumer_D | 20 | 12 | 13 | +24.3% |
| IT | 29 | 9 | 0 | +18.4% |
| Comm | 3 | 22 | 9 | +14.0% |
| Energy | 31 | 7 | 11 | +3.9% |
| Financials | 40 | 3 | 4 | +4.4% |
| Consumer_S | 29 | 3 | 1 | +0.6% |
| Utilities | 26 | 8 | 4 | +1.9% |

### 해석

TSLA, NVDA, NFLX, AMZN은 밸류에이션 기반 점수에서 HOLD/WATCH를 받았지만
(고PER, 고PBR, 적자 등), 이후 폭발적으로 outperform했다.
이 소수 outlier들이 WATCH/HOLD 평균 alpha를 끌어올려 grade 역전을 만든다.

이는 **시스템 설계 범위와 우주 선택의 불일치**에서 비롯된 구조적 현상이다:

- 점수 시스템: quality/value 종목 평가용으로 설계
- 연구 우주(S&P 500 상위 50): 최대 성장주(TSLA, NVDA, NFLX)를 자동 포함
- 결과: 점수가 맞게(성장주를 HOLD/WATCH로) 분류했지만, 그 종목들이 가장 많이 올랐음

**CLAUDE.md 설계 원칙과 동일한 구조**:
> '적자 성장주: 바이오, 초기 적자 플랫폼 — EPS 기반 평가 불가'
> '이 시스템으로 cyclical 종목을 분석하면 결과는 참고용 이하로만 사용할 것'

US 엔진도 동일한 원칙이 적용된다. TSLA FY2019 WATCH 판정은 버그가 아니라
설계 의도대로 작동한 것 — 다만 S&P 500 상위 우주에서는 그 종목들이 가장 크게 올랐다.

### 가설 최종 정리

| 가설 | 상태 | 결론 |
|------|------|------|
| H-US-1 Energy false positive | 부분 기여 | 단독 원인 아님 |
| Mean reversion | 기각 | Corr=+0.045, Q4 > Q1 |
| 고성장주 False Negative | **주원인 확인** | TSLA/NVDA/NFLX가 HOLD/WATCH alpha 지배 |

### 함의

1. **v1 scoring 로직 변경 불필요**: 점수가 틀린 게 아니라 우주가 설계 범위를 벗어남
2. **우주 선택 재검토 후보**: 고성장/적자 종목을 제외한 value/quality 우주 제한 실험
3. **현재 v1 Freeze 유지**: 이 발견이 weight/threshold 조정의 근거가 되지 않음

### 다음 단계 (US-1.0 후보)

- TSLA/NVDA/NFLX 제외 후 In-sample Corr / Grade 단조성 재계산
- 결과가 단조성을 회복하면: 우주 필터 설계 논의
- 결과가 여전히 역전이면: 추가 원인 탐색


## US-1.0 — H-US-7 Growth-stock Exclusion 결과 (2026-09-05)
> **Status: 🔵 Closed — Growth stock exclusion 검증**


### 실행

```
python3 research/us_1_0_growth_excl.py
```

### 핵심 결과 — In-sample (score_fy ≤ 2021)

| 실험 | n | SB alpha | BUY alpha | HOLD alpha | WATCH alpha | Mono | Corr(α) |
|------|---|----------|-----------|------------|-------------|------|--------|
| Baseline | 578 | +4.5% | +3.6% | +15.3% | +25.8% | ❌ | -0.185 |
| CF-A (-TSLA) | 567 | +4.5% | +3.9% | +14.9% | +11.8% | ❌ | -0.108 |
| CF-B (-NVDA) | 568 | +4.5% | +3.1% | +12.1% | +25.8% | ❌ | -0.179 |
| CF-C (-NFLX) | 565 | +4.5% | +4.3% | +11.3% | +25.0% | ❌ | -0.176 |
| CF-D (-TSL/NVD/NFL) | 544 | +4.5% | +4.0% | +7.0% | +10.5% | ❌ | -0.070 |
| CF-E (-TSL/NVD/NFL/AMZN) | 531 | +4.5% | +4.1% | +5.9% | +9.6% | ❌ | -0.048 |

HOLD/WATCH grade 내 제거 종목별 기여:

| Ticker | HOLD/WATCH 행 수 | avg alpha |
|--------|-----------------|----------|
| TSLA | 10 | +105.9% |
| NVDA | 5 | +67.9% |
| NFLX | 9 | +58.6% |
| AMZN | 11 | +18.6% |

### 핵심 발견 — 두 개의 분리된 문제

**문제 1: WATCH 이상 — TSLA 단일 outlier**

TSLA 단독 제거만으로 WATCH alpha +25.8% → +11.8%, Corr -0.185 → -0.108.
TSLA 10행 avg alpha +105.9% — 10개 데이터포인트가 WATCH 평균을 왜곡.
이건 구조적 Growth 문제가 아니라 TSLA 단일 종목 outlier 문제.

**문제 2: HOLD > BUY — 성장주와 무관한 구조적 원인**

CF-E에서 4종목 전부 제거 후에도 HOLD=+5.9% > BUY=+4.1% 역전 유지.
HOLD > BUY는 TSLA/NVDA/NFLX/AMZN과 무관한 별도 원인이 있음.

### 판정

| 이상 | 원인 판정 | 근거 |
|------|----------|------|
| WATCH >> SB/BUY | TSLA outlier (10행 × +105.9%) | CF-A 단독으로 절반 이상 해소 |
| HOLD > BUY | 미확인 구조적 원인 | CF-E 후에도 유지 |

Corr 기준: -0.185 → -0.048 (CF-E), 성장주가 Corr 왜곡의 상당 부분 설명.
Grade 단조성: HOLD > BUY 문제는 별도 탐색 필요.

### 다음 단계 (US-1.1)

HOLD > BUY 역전의 구조적 원인 탐색:
- HOLD grade 구성 종목 × 연도 분석
- HOLD를 받는 scoring 조건과 forward alpha의 관계
- BUY와 HOLD의 점수 구성 차이 (어떤 컴포넌트가 HOLD/BUY를 가르는가)


## US-1.1 — HOLD > BUY 역전 원인 탐색 결과 (2026-09-05)
> **Status: 🔵 Closed — Grade 역전 원인 탐색**


### 실행

```
python3 research/us_1_1_hold_vs_buy.py
```

### 발견된 원인 — 세 가지 독립적 요인

**원인 1: 데이터 품질 — GOOGL/META PER·PBR 누락 (H-US-5 확인)**

HOLD 상위 alpha 종목에 META 2014(+39.7%), GOOGL 2019(+28.7%), GOOGL 2020(+20.8%)가
s_per=0, s_pbr=0으로 포함됨. DEI shares 조회 실패로 value 점수가 0이 되어
비싼 우량 기업이 HOLD로 강등됨. 이후 실제 높은 alpha 기록.
→ scoring 설계 문제가 아니라 데이터 수집 버그. H-US-5 상태: 수정 대상.

**원인 2: 점수 경계 효과 — 50~59 구간이 가장 낮음**

| 점수 구간 | Grade | avg alpha |
|----------|-------|-----------|
| 40~49 | HOLD | +7.1% |
| 50~59 | BUY/HOLD 혼재 | +2.6% |
| 60~69 | BUY | +4.8% |
| 70~79 | STRONG_BUY | +4.0% |

50~59 구간(하위 BUY + 상위 HOLD 경계)이 +2.6%로 가장 낮아.
하위 BUY가 약한 것이 BUY 평균을 끌어내리고, 상위 HOLD(40~49)가 강한 것이 HOLD 평균을 올리는 구조.
→ 'HOLD 자체가 강하다'가 아니라 'BUY 하위 경계가 약하다'.

**원인 3: S&P 500 우주 효과**

Top-50 기업은 HOLD를 받아도 S&P 500 최상위권 대형주.
BUY(ROE score 14.9)와 HOLD(ROE score 8.5)의 절대적 quality 차이가
우주 선택 기준에 의해 희석됨.

### 컴포넌트 비교 (성장주 제외, In-sample)

| Grade | n | Score | s_per | s_pbr | s_roe | s_mom | Fwd Alpha |
|-------|---|-------|-------|-------|-------|-------|----------|
| STRONG_BUY | 268 | ~76.8 | 17.7 | 13.9 | 17.7 | 14.1 | +4.5% |
| BUY | 146 | ~63.3 | 14.2 | 13.0 | 14.9 | 11.1 | +4.1% |
| HOLD | 67 | ~47.2 | 6.8 | 10.1 | 8.5 | 11.8 | +5.9% |
| WATCH | 50 | ~29.3 | 1.3 | 12.1 | 2.0 | 10.6 | +9.6% |

### 결론

HOLD > BUY는 단일 원인이 아님. 세 요인이 합산:
- GOOGL/META 데이터 버그 → H-US-5 수정 검토
- 점수 경계 50~59 구간 약세 → 설계 참고용 (freeze 중, 수정 금지)
- S&P 500 우주 특성 → 설계 범위 문제, v9 우주 재설계 시 반영

v1 scoring 로직 변경 불필요. H-US-5 데이터 버그만 별도 수정 검토.

### 가설 상태 업데이트

H-US-5 (DEI shares 조회 실패): '수정 대상 확인' → **✅ 수정 완료 (US-1.2)**.
HOLD alpha 일부 해소 기대했으나 역전 유지 확인 → 구조적 원인 별도 존재.

---

## US-1.2 — H-US-5 데이터 복구 결과 (2026-09-05)
> **Status: 🔵 Closed — 데이터 복구 결과**


**목적:** GOOGL/META shares_outstanding 수집 실패 수정 → PER/PBR 재계산 → v1 재점수

### 버그 원인 및 수정 전략

| 종목 | 원인 | 사용 개념 | 특이사항 |
|------|------|-----------|----------|
| GOOGL | DEI EntityCommonStockSharesOutstanding 없음 (Class A/B/C 다중 주식) | us-gaap `CommonStockSharesOutstanding` (FY 10-K) | pre-split 값(< 1B) × 20 (2022-07-18 분할) |
| META | 동일 이유 | us-gaap `WeightedAverageNumberOfSharesOutstandingBasic` (FY 10-K) | 분할 없음, FY2012~2024 전체 커버 |

**PIT 조건 유지:** `filed <= as_of_date` 중 최신 filing 선택

### 등급 변경 요약 (In-sample)

**GOOGL (6건):**
| FY | PER | PBR | Old Grade | New Grade | Fwd Alpha |
|----|-----|-----|-----------|-----------|-----------|
| 2015 | 32.0x | 4.35x | WATCH | HOLD | -4.1% |
| 2018 | 26.4x | 4.57x | HOLD | BUY | +4.7% |
| 2019 | 22.8x | 3.88x | HOLD | BUY | +28.7% |
| 2020 | 34.3x | 6.20x | HOLD | BUY | +20.8% |

**META (7건, In-sample):**
| FY | PER | PBR | Old Grade | New Grade | Fwd Alpha |
|----|-----|-----|-----------|-----------|-----------|
| 2014 | 72.5x | 5.90x | HOLD | WATCH | +39.7% |
| 2015 | 86.4x | 7.21x | HOLD | WATCH | +9.4% |
| 2017 | 28.8x | 6.18x | HOLD | BUY | -3.0% |
| 2018 | 21.6x | 5.68x | HOLD | BUY | +6.9% |
| 2019 | 25.4x | 4.65x | WATCH | HOLD | +26.2% |
| 2020 | 28.6x | 6.49x | HOLD | BUY | -38.5% |
| 2021 | 15.8x | 4.97x | HOLD | STRONG_BUY | +4.6% |

### HOLD alpha 비교 (In-sample)

| 구분 | n | avg forward alpha |
|------|---|------------------|
| 수정 전 HOLD | 87 | +15.3% |
| 수정 후 HOLD | 80 | +16.0% |
| 수정 후 BUY | 164 | +3.6% |

**HOLD-BUY gap: +11.7% → +12.4% (역전 유지)**

### 핵심 발견

1. **데이터 버그 수정 효과**: GOOGL FY2019(+28.7%), FY2020(+20.8%)가 HOLD→BUY로 이동 → 고alpha 행이 BUY로 재분류됨
2. **역전 해소 실패**: HOLD-BUY gap이 오히려 소폭 확대(+11.7%→+12.4%)
   - META FY2014/2015가 HOLD→WATCH로 이동하면서 고alpha 행이 WATCH로 빠짐
   - WATCH(+9.6%) > HOLD(+16%) 구조는 별도 문제
3. **H-US-5 판정: 🟡 부분 완료** — 데이터 수정 자체는 완료, 그러나 HOLD>BUY 역전의 주원인은 아님

### 결론

HOLD>BUY 역전의 근본 원인은 데이터 버그가 아닌 구조적 문제:
- Score 40~49 구간(HOLD 하단)의 종목들이 BUY 하단(50~59)보다 alpha 높음
- S&P 500 우주의 고품질 편향 — 저점수 종목도 절대 수준에서 우수한 기업

**v1 scoring 로직 변경 없음 (Freeze 유지). H-US-5 버그 수정 완료.**

### Roadmap 업데이트

```
US-1.2  H-US-5 데이터 복구 (GOOGL/META shares)      → ✅ COMPLETE — 역전 유지 확인
US-1.3  Growth Engine Research Design                → 🔲 진행 중
```

구현: `research/us_1_2_shares_fix.py` (완료 2026-09-05)

---

## US-1.3 — Growth Engine Research Design (2026-09-05)
> **Status: 🔵 Closed — Growth engine 설계. US-1.7로 발전됨**


### 목적

v1(Value/Quality Engine)과 완전히 분리된 별도 연구 트랙.
v1은 Freeze 유지. Growth Engine은 검증 이후에만 production 편입 논의.

핵심 질문:
> "비싸더라도 성장에 비해 시장 기대가 덜 반영된 기업을 어떻게 구분하는가?"

### 설계 원칙

1. **Signal → Validation → Score 순서 엄수.** 먼저 각 신호의 예측력을 독립 검증.
2. **PIT 조건 유지.** score(FY N)에 쓰는 데이터는 as_of_date(= FY_end + 90d) 이전 공시만.
3. **Forward alpha 기준.** v1과 동일: Corr(signal, forward_alpha) + 구간 단조성.
4. **v1과 독립.** Growth score가 v1 score에 영향 없음. 별도 growth_panel 테이블.

### Growth Signal 후보 4개 (1차)

| Signal | 계산식 | 데이터 출처 | 상태 |
|--------|--------|------------|------|
| H-GE-1: rev_growth | (Rev_N − Rev_{N-1}) / Rev_{N-1} | research_panel.revenue | 즉시 계산 가능 |
| H-GE-2: rev_accel | rev_growth_N − rev_growth_{N-1} | research_panel.revenue | 즉시 계산 가능 |
| H-GE-3: opm_trend | OPM_N − OPM_{N-1} (OPM = OI/Rev) | research_panel.operating_income | 즉시 계산 가능 |
| H-GE-4: fcf_growth | (FCF_N − FCF_{N-1}) / |FCF_{N-1}| | companyfacts 추가 수집 필요 |

**FCF = NetCashProvidedByUsedInOperatingActivities − PaymentsToAcquirePropertyPlantAndEquipment**

**GrossProfit 제외 이유:** GOOGL/META/JPM/V/PG/DIS/BRK-B 등 다중 섹터에서 개념 누락.
금융사는 구조적으로 COGS가 없고, 일부 테크사도 보고 방식 차이. 커버리지 불균형으로 제외.

**OCF 커버리지:** 50개 전 종목 ✅  
**CapEx 커버리지:** 50개 중 JPM·V·HD 제외 (금융사 CapEx 개념 다름) → FCF는 나머지 47종목

### 가설 (사전 고정)

| ID | 가설 | 검증 방법 | 통과 기준 |
|----|------|---------|---------|
| H-GE-1 | 고매출 성장 FY N → 더 높은 forward alpha FY N+1 | Corr(rev_growth, fwd_alpha) + Q1-Q5 단조성 | Corr > 0 AND Q5 alpha > Q1 alpha |
| H-GE-2 | 성장 가속 FY N → 더 높은 forward alpha FY N+1 | Corr(rev_accel, fwd_alpha) + Q1-Q5 | Corr > 0 AND Q5 > Q1 |
| H-GE-3 | OPM 확장 FY N → 더 높은 forward alpha FY N+1 | Corr(opm_trend, fwd_alpha) + Q1-Q5 | Corr > 0 AND Q5 > Q1 |
| H-GE-4 | 높은 FCF 성장 FY N → 더 높은 forward alpha FY N+1 | Corr(fcf_growth, fwd_alpha) + Q1-Q5 | Corr > 0 AND Q5 > Q1 |

**판정 기준 (사전 고정):**
- ✅ 지지: Corr > 0 + Q5 alpha > Q3 > Q1 (단조성)
- 🟡 약한 지지: Corr > 0 but 단조성 미달
- ⚪ 기각: Corr ≤ 0

### 구현 계획

```
US-1.3  growth_panel 구축 (OCF/CapEx 수집 + 4개 신호 계산)  → us_1_3_growth_panel.py
US-1.4  Signal 독립 검증 (H-GE-1~4 각각)                  → us_1_4_signal_validation.py
US-1.5  복합 Growth Score 설계 (검증된 신호만)              → 조건: n ≥ 30 통과 신호 2개 이상
```

### 탈락 기준

팩터 삭제 기준(CLAUDE.md)과 동일: n ≥ 100 + 20D/60D/120D 세 윈도우 모두 지속 음수.
현재 in-sample n ≈ 650행 (growth signal 있는 행 기준). Forward panel 행수는 더 적음.

**아직 만들지 않는 것:**
- Growth Score 공식 (검증 전 금지)
- Growth Grade 기준 (검증 전 금지)
- v1과의 합산 방식 (v1 Freeze 해제 후 논의)

구현: `research/us_1_3_growth_panel.py` (완료 2026-09-05)

---

## US-1.4 — Growth Signal 독립 검증 결과 (2026-09-05)
> **Status: 🔵 Closed — Growth signal G1/G2/G3 검증**


### 결과 요약 (In-sample, n=347~484)

| ID | Signal | n | Corr(α) | 단조성 | 판정 |
|----|--------|---|---------|--------|------|
| H-GE-1 | rev_growth | 484 | +0.104 | ❌ | 🟡 약한 지지 |
| H-GE-2 | rev_accel | 431 | -0.095 | ❌ | ⚪ **기각** |
| H-GE-3 | opm_trend | 347 | +0.013 | ❌ | 🟡 약한 지지 (사실상 노이즈) |
| H-GE-4 | fcf_growth | 376 | +0.247 | ❌ | 🟡 약한 지지 (가장 강한 신호) |

Holdout Corr: rev_growth +0.153, rev_accel +0.120, opm_trend +0.010, fcf_growth +0.163

### 신호별 Quintile 분포 (In-sample)

**H-GE-1 rev_growth:**

| 분위 | avg signal | avg alpha |
|------|-----------|----------|
| Q1 | -14.1% | +1.8% |
| Q2 | +1.2% | +4.3% |
| Q3 | +6.7% | +10.1% |
| Q4 | +14.6% | +16.1% |
| Q5 | +45.8% | +10.9% ← Q5 반전 |

Q1→Q4: 단조 증가. Q5에서 alpha 하락 → 극단적 성장주는 이미 시장이 가격에 반영.

**H-GE-4 fcf_growth (가장 강한 신호):**

| 분위 | avg signal | avg alpha |
|------|-----------|----------|
| Q1 | -91.6% | +10.7% |
| Q2 | -9.2% | +3.2% ← 최저 |
| Q3 | +9.0% | +10.4% |
| Q4 | +29.1% | +8.2% |
| Q5 | +243.0% | +13.0% |

Q2 alpha 최저 → FCF가 소폭 감소하는 구간이 worst. 극단 감소(Q1)는 회복 기대로 alpha 양수.

### 핵심 발견

1. **모든 신호에서 단조성 없음.** 단순 성장률 높을수록 forward alpha 높다는 가설 기각.
   - 시장은 이미 성장 기대를 주가에 반영 → 원시 성장률은 예측력 약함.

2. **H-GE-2 rev_accel 기각.** Corr=-0.095 (음수). Q1(성장 최대 둔화)의 alpha가 +16.7%로 가장 높음.
   - 역설: 성장 둔화가 이미 주가에 반영됐다면 다음 해에 회복 기대 → mean reversion 효과?

3. **H-GE-4 fcf_growth이 가장 강한 신호 (Corr=+0.247).** In-sample/Holdout 모두 양수.
   - FCF는 회계 왜곡 적고 현금창출 능력 반영 → 성장 신호 중 가장 신뢰도 높음.

4. **H-GE-3 opm_trend 사실상 노이즈 (Corr=+0.013).** OPM 개선만으로는 alpha 예측 불가.

### 해석 — 왜 단조성이 없는가

S&P 500 universe의 구조적 특성:
- 고성장주(Q5)는 이미 high valuation → 실현 alpha는 기대 이하
- 저성장주(Q1)는 저평가 가능성 존재 → 회복 시 alpha 발생
- **"성장 자체"가 아니라 "기대 대비 성장 surprise"가 alpha를 만든다**

→ 다음 연구 방향: Growth + Valuation 조합 (PEG-like 접근)
   - 고성장 AND 저PER/PBR → 기대 대비 저평가 신호?
   - fcf_growth AND v1 score 조합 검증?

### Roadmap 업데이트

```
US-1.3  growth_panel 구축                        → ✅ COMPLETE
US-1.4  Signal 독립 검증 (H-GE-1~4)              → ✅ COMPLETE — fcf_growth 최강, 단조성 없음
US-1.5  Growth × Valuation 조합 검증              → ✅ COMPLETE — H-US-8 3/3 통과
US-1.6  Growth Engine 설계 방향 결정              → 🔲 TODO
```

구현: `research/us_1_4_signal_validation.py` (완료 2026-09-05)

---

## US-1.5 — H-US-8 Growth × Valuation 결과 (2026-09-05)
> **Status: 🔵 Closed — Growth × Valuation 복합 효과**


### 결과: ✅ H-US-8 3/3 통과

**2×2 분석 (In-sample, n=376)**

| 그룹 | n | avg FCF Growth | avg v1 Score | avg Alpha |
|------|---|---------------|-------------|----------|
| G1: Low Growth + Low Score | 97 | -47.6% | 47.5 | +10.0% |
| G2: Low Growth + High Score | 88 | -16.3% | 74.8 | +5.1% |
| **G3: High Growth + Low Score** | **72** | **+150.8%** | **48.0** | **+23.0%** |
| G4: High Growth + High Score | 119 | +80.4% | 76.3 | +2.9% |

**Holdout:**
| 그룹 | n | avg Alpha |
|------|---|----------|
| G1 | 48 | +6.6% |
| G2 | 18 | +8.0% |
| **G3** | **34** | **+14.5%** |
| G4 | 26 | +5.4% |

**TSLA/NVDA/NFLX 제거 후 (In-sample):**
- G3: +23.0% → **+12.6%** (여전히 G3 우위)
- G1: +10.0% → +3.1% (해당 종목들이 G1에도 기여하고 있었음)

### 핵심 발견: Interaction 효과

| v1 Score 구간 | n | Corr(FCF Growth, alpha) | avg alpha |
|-------------|---|------------------------|---------|
| Low Score (≤ median) | 193 | **+0.311** | +13.5% |
| High Score (> median) | 183 | +0.055 | +4.5% |

**FCF Growth의 예측력은 v1 저평가 종목에서만 작동한다.**

- G3 (High FCF + Low Score) +23.0% vs G4 (High FCF + High Score) +2.9%
- "시장이 이미 알고 있는 성장주(G4)"는 alpha 없음
- "v1이 놓친 FCF 성장주(G3)"가 강한 alpha

### 성공 기준 체크

| 기준 | 결과 |
|------|------|
| ① G3 In-sample 우위 | ✅ G3 +23.0% > G1 G2 G4 모두 상회 |
| ② Holdout 방향 유지 | ✅ G3 +14.5% 여전히 최고 |
| ③ Outlier 제거 후 유지 | ✅ G3 +12.6% 여전히 우위 |

### 주의사항 / 한계

1. **NVDA 집중**: G3 IT sector avg +45.8% — NVDA 2015/2016/2018/2021 등 다수 포함.
   G3 효과의 일부는 NVDA 집중에 기인. NVDA를 제거하면 G3 추가 하락 예상.

2. **G3 n=72**: In-sample 기준 72행. n≥100 미달 → 통계적 유의성 제한적.
   방향성 참고, 결론 보류.

3. **G4 역설 확인**: High Growth + High Score = +2.9% (최저)
   → "좋은 성장주이고 시장도 이미 안다" = alpha 없음. 예측력의 진짜 원천은 Surprise.

4. **G1 outlier 의존**: G1 +10.0% → TSLA/NVDA 제거 후 +3.1%. G1의 alpha도 고성장 outlier 의존.

### 해석

성립하는 구조:
> **v1이 "저평가"로 판단한 종목(Low Score) 중 FCF 성장이 강한 종목이 가장 높은 alpha.**

이것은 두 가지 가능성을 시사:
- (A) v1의 false negative: 성장주를 PER/PBR 기준으로 저평가로 분류했지만 실제 Quality가 높음
- (B) GARP(Growth at Reasonable Price): 성장에 비해 가격이 합리적인 구간에서 초과수익 발생

현재 데이터만으로는 (A)/(B) 구분 불가. 다음 단계에서 NVDA를 개별 분석하면 분리 가능.

### Roadmap 업데이트

```
US-1.5  Growth × Valuation 조합 검증              → ✅ COMPLETE — 3/3 통과
US-1.6  G3 구성 분해 — 구조적 효과 vs outlier 집중 → ✅ COMPLETE — 구조적 효과 확인
US-1.7  Growth Engine 설계 방향 결정              → 🔲 TODO
```

구현: `research/us_1_5_growth_valuation.py` (완료 2026-09-05)

---

## US-1.6 — G3 구성 분해 결과 (2026-09-05)
> **Status: 🔵 Closed — G3 FCF/Revenue/EPS 분해**


### 핵심 질문: NVDA 효과인가, 구조적 G3 효과인가?

### 실험 결과 (In-sample, G3 vs G2 vs G4)

| 실험 | G3_n | G3_α | G2_α | G4_α | G1_α | 판정 |
|------|------|------|------|------|------|------|
| Baseline | 72 | +23.0% | +5.1% | +2.9% | +10.0% | ✅ G3 우위 |
| CF-A (-NVDA) | 64 | +19.4% | +5.1% | +2.9% | +9.5% | ✅ G3 우위 |
| CF-B (-NVDA/TSLA) | 60 | +12.3% | +5.1% | +2.9% | +5.1% | ✅ G3 우위 |
| CF-C (-NVDA/TSLA/NFLX) | 57 | +12.6% | +5.1% | +2.9% | +3.1% | ✅ G3 우위 |
| **CF-D (-NVDA/TSLA/NFLX/AMZN)** | **50** | **+12.0%** | **+5.1%** | **+2.9%** | **+2.5%** | **✅ G3 우위** |

**최종 판정: ✅ 구조적 G3 효과 확인** — 4개 주요 성장주 모두 제거 후에도 G3 +12.0% > G2/G4 유지.

### Ticker 기여 분석 (기여비중 기준)

| Ticker | n | avg alpha | 기여비중 |
|--------|---|----------|---------|
| TSLA | 4 | +127.1% | 30.7% |
| NVDA | 8 | +51.8% | 25.0% |
| LLY | 3 | +45.5% | 8.2% |
| AMZN | 7 | +16.6% | 7.0% |
| TMUS | 3 | +32.4% | 5.9% |
| META | 6 | +10.0% | 3.6% |
| ... | | | |

NVDA 단독 제거: G3 +23.0% → +19.4% (하락 폭 작음 — TSLA가 더 큰 기여자).

### CF-D 잔여 G3 n=50 구성

**상위 종목:** LLY(+64.3%), TMUS(+53.6%), PG(+52.6%), GE 2021(+43.7%), ABBV(+42.7%), LLY 2020(+41.6%)

**섹터 분포:**

| Sector | n | avg alpha |
|--------|---|----------|
| Health | 7 | +28.2% |
| Cons_S | 2 | +26.2% |
| IT | 2 | +21.8% |
| Comm | 11 | +17.1% |
| Energy | 3 | +15.5% |
| Indust | 4 | +10.6% |
| Fin | 3 | +10.1% |
| Cons_D | 4 | +7.6% |
| Util | 8 | +0.0% |
| Matl | 6 | -5.3% |

**Health/Comm/Energy/IT 섹터에서 G3 효과 일관적. Util/Matl에서 미작동.**

### 종합 결론 (US-1.3 ~ US-1.6)

1. **단순 성장 신호는 예측력 약함 (US-1.4)**: rev_growth, opm_trend, rev_accel 단독 기각 또는 약한 지지.
2. **FCF Growth가 가장 강한 단독 신호 (Corr +0.247)**: 그러나 단조성 없음.
3. **FCF Growth × Low v1 Score 조합에서 강한 alpha (US-1.5/1.6)**:
   - G3 (High FCF + Low Score): In-sample +23.0%, Holdout +14.5%
   - 4개 주요 outlier 제거 후에도 +12.0% (구조적 효과)
   - Interaction: Low Score 그룹 Corr=+0.311 vs High Score +0.055
4. **G4 역설 (US-1.5)**: High Growth + High Score = +2.9% (최저). 시장이 이미 아는 성장주에는 alpha 없음.

### Growth Engine 채택 기준 (미결)

현재 상태: G3 효과 확인. 단, 아직 다음이 미결:
- n=50(CF-D 잔여)은 통계적으로 제한적 (n≥100 기준 미달)
- Util/Matl 섹터에서 미작동 → 섹터별 적용 범위 정의 필요
- v1과 Growth Engine 결합 방식 미결 (v1 Freeze 중)

**Growth Engine을 production에 반영하기 위한 선행 조건:**
1. v1 Freeze 해제 (n≥100 Calibration 완료)
2. G3 효과의 Holdout n≥30 추가 누적
3. 섹터별 적용 필터 설계

구현: `research/us_1_6_g3_decomposition.py` (완료 2026-09-05)

---

## US-1.7 — Growth Engine Design (설계 문서, 2026-09-05)
> **Status: 🔵 Closed — Growth engine v2_A 설계. v2_A Production 채택으로 완결**


### 목표

G3 발견을 가장 적은 자유도로 재현할 수 있는 scoring function을 찾는다.
"G3가 잘 나왔으니 Growth 점수를 더하자"가 아니라,
검증된 interaction 구조를 scoring으로 변환 후 동일 historical sample에서 재검증.

**v1 Freeze 유지.** US-1.7 결과는 "v2 candidate"이며 production 즉시 반영 금지.

### 현재 v1 구조 (변경 불가)

```
Value (PER + PBR)      40
Quality (ROE)          25  
Earnings Health        15
Momentum               20
─────────────────────────
Total                 100
```

### 세 가지 후보 설계 (A/B/C)

#### A. Main Effect
```
v2_A = v1_score + fcf_growth_score(5단계)
```
- fcf_growth를 in-sample 백분위 기준 5단계 점수화 (0/3/5/7/10)
- 자유도: 낮음 (분위 경계 + 점수 배분)
- 가설: FCF Growth main effect 존재

#### B. Interaction (연구 결과와 가장 직접 대응)
```
v2_B = v1_score + growth_bonus
growth_bonus: v1_score ≤ median AND fcf_growth > median → +bonus
              나머지 → 0
```
- 자유도: 중간 (bonus 크기, median 기준)
- 가설: FCF Growth 효과는 Low v1 Score 영역에서만 작동

#### C. Threshold Interaction
```
v2_C = v1_score + growth_bonus
조건: v1_score < 55 AND fcf_growth > 상위 30%
bonus: 고정값 (예: +10)
```
- 자유도: 중간 (threshold 2개 + bonus 크기)
- 가설: 명확한 경계값(BUY 기준 이하 + 상위 FCF)에서만 효과

### 평가 기준 (동일 조건 적용)

모든 후보를 동일하게 평가:

| 기준 | 방법 |
|------|------|
| 상관관계 개선 | Corr(v2, fwd_alpha) vs Corr(v1, fwd_alpha) |
| Grade 단조성 | SB > BUY > HOLD > WATCH (새 grade 기준) |
| Outlier 강건성 | TSLA/NVDA/NFLX/AMZN 제거 후 재검증 |
| Holdout 일관성 | In-sample 방향이 Holdout에서도 유지 |
| 섹터 안정성 | Util/Matl 적용 시 효과 여부 |

### 선택 원칙

동일 성과라면 자유도가 낮은 모델 선택.
섹터 필터는 H-US-9 별도 가설 검증 후에만 추가.
분위 경계/threshold는 FCF Growth 실제 분포 확인 후 결정.

구현: `research/us_1_7_growth_score.py`

### 결과 (2026-09-05)

**Baseline**
```
Corr(v1, fwd_alpha)  In-sample: -0.228  /  Holdout: +0.002
Grade (In-sample):  SB=+4.1%  BU=+2.5%  HO=+17.6%  WA=+31.2%  ← 역전 구조
Grade (Holdout):    SB=+5.3%  BU=+12.1% HO=+4.5%   WA=+3.5%   ← 단조성 미달
```

**후보별 결과**

| 모델 | IS Corr | Δ | IS Mono | HO Corr | HO Mono |
|------|---------|---|---------|---------|---------|
| v1 Baseline | -0.228 | — | ❌ | +0.002 | ❌ |
| A. Main Effect | -0.212 | +0.016 | ❌ | +0.036 | **✅** |
| B. Interaction | -0.209 | +0.019 | ❌ | +0.030 | ❌ |
| C. Threshold | -0.212 | +0.016 | ❌ | -0.006 | ❌ |

**A Holdout Grade 분포 (핵심)**
```
SB=+11.8%  BU=+7.0%  HO=+5.9%  WA=+3.2%  ← ✅ 완전 단조 달성
```

**Outlier 제거(-NVDA/TSLA/NFLX/AMZN) Holdout**
```
A:  Corr=+0.068  SB=+8.2% > BU=+7.0% > HO=+0.2% > WA=-2.2%  ✅
B:  Corr=+0.064  단조성 ❌
C:  Corr=+0.040  단조성 ❌
```

### 핵심 발견

**1. In-sample 역전은 레짐 효과 (모델로 수정 불가)**
2015~2021(In-sample): WATCH > HOLD > SB > BU. FCF Growth 보너스를 추가해도 역전 구조 유지.
이 역전은 pre-2022 성장주 강세 레짐에서 저평가 소형·성장주가 고퀄리티 종목을 압도한 결과다.
→ 어떤 scoring 조정으로도 In-sample 역전을 교정하기 어렵다.

**2. 2022+ Holdout에서 v1 방향 정상화**
Holdout에서 baseline v1조차 SB=+5.3% vs WA=+3.5%로 방향이 맞다 (단조성은 미달).
레짐 전환(2022 금리 급등)이 "Quality pays" 패턴을 복원시켰다.

**3. A(Main Effect)가 Holdout 단조성 달성**
FCF Growth 5분위 보너스(+0~10)가 SB를 추가로 올려, Holdout에서 완전 단조 달성.
B(Interaction)는 In-sample Corr이 살짝 더 높지만 Holdout 단조성 실패 → In-sample 과적합 징후.
C(Threshold)는 Holdout Corr이 음수 → 탈락.

### 판정

**v2 Candidate: A (Main Effect)**

우선순위 기준:
1. Holdout 단조성 ✅ (A만 달성)
2. Outlier 제거 후에도 Holdout 단조성 유지 ✅
3. 자유도 B보다 낮음 (단방향 main effect)
4. In-sample Corr 개선 미미 (+0.016) → 기대치 낮게 설정

**중요 한계**
- In-sample(2015~2021) 역전: A도 해결 못 함. 역전의 원인으로 pre-2022 시장 레짐 효과를 가설로 제시하지만, 이는 H-US-10에서 별도 검증 예정. 현재는 관찰된 패턴(IS 역전 / HO 정상화)이 사실.
- Holdout n=126으로 여전히 표본 소규모. 1개 연도(2022/2023)가 결과 좌우 가능.
- FCF Growth 단독 Corr이 +0.247이었음에도 A 편입 후 Corr 개선은 +0.016에 그침. FCF Growth의 Score 편입 시 incremental predictive contribution이 작다는 것을 시사하지만, "v1이 FCF 정보를 이미 내포한다"는 직접 해석은 아직 검증되지 않음.

### 다음 연구

| ID | 가설 | 내용 |
|----|------|------|
| H-US-9 | 섹터 안정성 | Util/Matl vs IT/Health에서 A 효과 차이 | ✅ 통과 |
| H-US-10 | 레짐 조건부 | 2022+ 전용 A vs 전체 기간 A 성과 차이 | ✅ 통과 |

---

## US-1.9 — H-US-10: Regime Conditionality (2026-09-05)
> **Status: 🔵 Closed — Regime conditionality 검증**


구현: `research/us_1_9_regime_conditionality.py`

### 결과

**연도별 Corr(v1_score, alpha) 시계열**

```
2010: +0.334   2011: -0.164   2012: -0.687   2013: +0.226
2014: -0.633   2015: -0.274   2016: -0.215   2017: -0.235
2018: -0.246   2019: -0.262   2020: -0.165   2021: -0.122  ← IS (평균 -0.228)
────────────────────────────────────────────────────────────────
2022: -0.018   2023: -0.291   2024: +0.246   2025: N/A     ← HO (평균 +0.002)
```

**레짐별 Grade 분포**

```
Pre-2022 (n=376):
  v1 Baseline: SB=+4.1%  BU=+2.5%  HO=+17.6%  WA=+31.2%  Mono=❌
  v2_A:        SB=+4.9%  BU=+3.7%  HO=+15.3%  WA=+36.8%  Mono=❌

2022+ (n=126):
  v1 Baseline: SB=+5.3%  BU=+12.1% HO=+4.5%   WA=+3.5%   Mono=❌
  v2_A:        SB=+11.8% BU=+7.0%  HO=+5.9%   WA=+3.2%   Mono=✅
```

**세그먼트별 비교**

| 세그먼트 | n | v1 Corr | v2_A Corr | Δ | v1 Mono | v2 Mono |
|---------|---|---------|-----------|---|---------|---------|
| Pre-2022 전체 | 376 | -0.228 | -0.212 | +0.016 | ❌ | ❌ |
| Pre-2022 IT+Comm | 70 | -0.232 | -0.237 | -0.005 | ❌ | ❌ |
| Pre-2022 비-IT/Comm | 306 | -0.222 | -0.205 | +0.017 | ❌ | ❌ |
| 2022+ 전체 | 126 | +0.002 | +0.036 | +0.034 | ❌ | ✅ |
| 2022+ IT+Comm | 28 | -0.134 | -0.080 | +0.053 | ❌ | ❌ |
| **2022+ 비-IT/Comm** | **98** | **+0.052** | **+0.073** | **+0.021** | **✅** | **✅** |

### 핵심 발견

**1. H-US-10 통과 ✅ — Corr 부호 전환 확인**
pre-2022 Corr=-0.228 / 2022+ Corr=+0.002로 부호 전환 확인.
2022+ v2_A Grade 단조성(SB>BU>HO>WA) 유지.

**2. 연도별 Corr은 단조 증가가 아님 (주의)**
2023(HO)=-0.291로 다시 음수, 2024(HO)=+0.246. 연도별 n=30~42 수준에서 노이즈가 크다.
"2022년부터 정상화"가 아니라 "HO 기간 평균적으로 양수 방향"이 더 정확한 표현.

**3. IS 역전의 원인은 IT/Comm이 아닌 시장 전반 레짐**
Pre-2022 비-IT/Comm Corr=-0.222 — IT+Comm(-0.232)과 거의 동일한 수준의 역전.
US-1.8에서 "IT+Comm 제거 시 v1 단조성 달성"이라는 관찰은 HO(2022+) 기간에 해당하는 것이며,
IS 역전의 원인이 IT/Comm에 집중된다는 것은 이 데이터로 확인되지 않는다.

**4. 2022+ 단조성의 핵심 기여자는 비-IT/Comm**
2022+ 비-IT/Comm은 v1 자체가 이미 Mono ✅(Corr=+0.052).
2022+ IT+Comm은 v2_A 후에도 Mono ❌(Corr=-0.080).
Holdout 전체 단조성 달성은 비-IT/Comm의 구조적 정상화가 주도.

### 판정

**H-US-10 통과. v2_A Candidate 검증 완료.**

| 검증 | 결과 |
|------|------|
| H-US-8 FCF × Low Score 효과 | ✅ |
| H-US-9 섹터 안정성 | ✅ |
| H-US-10 레짐 조건부 | ✅ |

**v2_A의 한계 (production 채택 전 고려사항)**
- HO n=126, 연도별 30~42개로 통계적 신뢰도 제한
- FCF Growth incremental Corr 개선: +0.016~+0.034 (작은 효과)
- Pre-2022 Grade 역전: A로도 미교정 (레짐 의존적 구조)
- 2023(HO) Corr=-0.291: HO 내부에서도 연도별 불안정

---

## v2_A Production 채택 결정 (2026-09-05)
> **Status: 🟢 Applied — v2_A architecture 채택 (2026-09-05)**


### 결정: Candidate 유지 — production 반영 보류

| 항목 | 현재 결정 |
|------|---------|
| v1 baseline | 유지 |
| v2_A | Candidate 유지 |
| FCF Growth 구조 | OOS 추가 검증 |
| 0/3/5/7/10 배점 | **pre-committed** |
| IS 재활용한 배점 최적화 | **금지** |
| 다음 검증 | 신규 OOS 데이터 (2025 FY) |
| production 반영 | 추가 OOS 확인 후 결정 |

**이유:**

"효과가 존재한다"(H-US-8/9/10 통과)와 "지금 넣을 만큼 확실하다"는 다른 명제다.
현재 증거는 전자의 기준은 통과했지만 후자의 기준까지는 아직 부족하다.

구체적 근거:
- 2023(HO) Corr=-0.291, 2024=+0.246 — 2024 단일 연도가 HO 전체 방향을 끌어올리고 있다. "레짐 정상화"인지 "2024라는 1개 FY 효과"인지 아직 분리되지 않는다.
- incremental Corr 개선(+0.016~+0.034)이 작아서 "FCF Growth가 v1과 독립적인 정보를 추가한다"는 것이 완전히 확인되지 않았다.

**배점(0/3/5/7/10) — pre-committed, IS 재최적화 금지:**
IS 데이터는 이미 (1) FCF Growth 신호 선택(US-1.4), (2) A 구조 선택(US-1.7), (3) 현재 배점 설계에 사용됐다.
같은 IS 데이터로 배점까지 최적화하면 검증과 설계가 섞인다.

0/3/5/7/10은 **pre-committed**으로 고정.
다음 OOS에서 이 배점을 그대로 적용했을 때 G3 성과 방향, Grade 방향, outlier 강건성이 유지되면
"배점이 최적이다"가 아니라 "사전에 정한 단순 구조가 새 데이터에서 살아남았다"는 훨씬 강한 결과가 된다.
반대로 무너지면 배점 미세조정으로 살리지 않는다 — v2_A 구조 자체의 일반화 실패로 기록한다.

### 다음 단계

**데이터 수집과 OOS 검증 타이밍 구분 (중요):**

2025 FY 펀더멘털(10-K)은 지금(2026-09) 수집 가능하다.
그러나 2025 FY score → 2026 FY end forward return은 2026년 전체 가격 구간이 끝난 후에야 완전한 OOS 관측치가 된다.
→ 2025 FY raw/PIT 데이터 수집은 지금 해도 되지만, 그것을 v2_A OOS 검증에 포함시키는 건 2027년 초로 미룬다.
→ `as_of_date = FY_end + 90d` 기준 PIT 재구성은 2025 FY에도 동일하게 적용.

```
지금 (2026-09)
    v1 → production 유지
    v2_A → Candidate 유지
    2025 FY raw/PIT 데이터 수집 (선택)
        ↓
2027년 초
    2025 FY score → 2026 forward return/alpha 완성
    pre-committed 0/3/5/7/10 그대로 적용
    v2_A OOS 검증
        ↓ (통과 시)
    production 채택 검토
```

**OOS 검증 통과 기준 (사전 고정):**
- v2_A Grade monotonicity 유지
- forward alpha 방향이 기존 결과와 같은 방향
- 특정 종목/섹터 outlier 제거 후에도 효과 유지
- 배점 0/3/5/7/10은 변경 없이 그대로 적용 (결과에 맞춰 사후 조정 금지)

**OOS에서 효과가 사라질 경우:**
production 채택하지 않되, v2_A를 폐기하지 않는다.
"historical regime-dependent candidate"로 상태를 변경하고 연구 기록으로 보존.
배점 미세조정으로 결과를 구제하지 않는다 — 구조 자체의 일반화 실패로 기록한다.

---

## US-1.8 — H-US-9: Sector Stability (2026-09-05)
> **Status: 🔵 Closed — Sector stability 검증**


구현: `research/us_1_8_sector_stability.py`

### 결과

**섹터별 Corr 개선 비율**

| 구분 | A ≥ v1 비율 | 판정 |
|------|------------|------|
| In-sample | 8/11 (73%) | ✅ |
| Holdout | 7/10 (70%) | ✅ |

**Holdout 섹터별 상세**

| Sector | n | v1 Corr | v2_A Corr | Δ | v1 Mono | v2 Mono |
|--------|---|---------|-----------|---|---------|---------|
| IT | 16 | -0.155 | -0.026 | +0.129 | ❌ | ❌ |
| Util | 15 | +0.120 | +0.157 | +0.037 | ❌ | ✅ |
| Energy | 15 | -0.464 | -0.384 | +0.081 | ❌ | ❌ |
| Indust | 12 | -0.124 | -0.095 | +0.029 | ✅ | ✅ |
| Cons_S | 12 | -0.420 | -0.332 | +0.088 | ❌ | ❌ |
| Cons_D | 12 | +0.331 | +0.283 | -0.048 | ✅ | ❌ |
| **Comm** | **12** | **-0.040** | **-0.144** | **-0.104** | ❌ | ❌ |
| Health | 12 | +0.128 | +0.145 | +0.017 | ❌ | ❌ |
| Matl | 11 | +0.398 | +0.432 | +0.034 | ❌ | ❌ |
| Fin | 6 | +0.410 | +0.402 | -0.008 | ❌ | ❌ |

**IT+Comm 제거 Counterfactual**

```
IT+Comm 제거 후 Holdout (n=98):
  v1 Baseline:  SB=+4.8% > BU=+4.3% > HO=-0.2% > WA=-2.0%  Mono=✅
  v2_A:         SB=+3.7% > BU=+3.7% > HO=+3.2% > WA=-3.1%  Mono=✅

판정: ✅ 분산된 효과 — IT/Comm 제거 후에도 HO 단조성 유지
```

**Util+Matl 단독**

```
Holdout (n=26):
  v1 Baseline:  SB=+20.0% / BU=-2.6% / HO=+5.0% / WA=-10.5%  Mono=❌
  v2_A:         SB=+12.6% > BU=-3.6% > HO=-4.9% > WA=-10.5%  Mono=✅ (n 소규모 주의)
```

### 핵심 발견

**1. H-US-9 통과 ✅ — 분산된 효과**
A Main Effect의 효과가 IT/Comm에 집중되지 않음.
IT+Comm 제거 후에도 Holdout 단조성 유지, Corr 개선도 다수 섹터에서 확인.

**2. IT+Comm 제거 시 v1 자체도 HO 단조성 달성 (중요 부가 발견)**
IT+Comm을 제거하면 v1 Baseline의 Holdout grade 분포가 단조적이 된다.
→ "IS에서 관찰된 grade 역전이 IT/Comm 섹터에 집중되어 있다"는 간접 증거.
→ H-US-10 레짐 분석의 출발점. IT/Comm 섹터에서 pre-2022 성장주 효과가 두드러졌는지 확인 필요.

**3. Comm 섹터 HO Corr 악화 (Δ-0.104)**
v2_A 편입 후 Comm 섹터 HO Corr이 -0.040 → -0.144로 악화.
원인 가설: GOOGL/META의 높은 FCF Growth → v2 점수 상승 → HO에서 시장 기대 선반영으로 underperform.
→ H-US-10에서 연도별로 분리해서 확인 예정.

**4. Util/Matl에서 v2_A HO 단조성 달성 (예상 외)**
US-1.6에서 "G3 효과 약함"으로 관찰됐으나, v2_A는 Util+Matl HO에서 단조성 달성.
단, n=26으로 통계 신뢰도 낮음. 섹터 수준 결론은 보류.

### 판정

**H-US-9 통과. v2_A Candidate 유지.**
다음: H-US-10 레짐 조건부 검증.

---

## US-2.1 — H-US-11: G3 Growth Signal Decomposition (2026-09-05)
> **Status: 🔵 Closed — G3 decomposition**


구현: `research/us_2_1_g3_decomposition.py`

### 목적

G3(High FCF Growth + Low v1 Score)가 어떤 재무 변화 패턴을 대표하는지 이해.
원인 규명이 아닌 패턴 묘사. FCF Growth 단독 신호가 충분한지, 더 구조적인 조합이 필요한지 판단 근거 마련.

### G3 신호 분포 (vs Rest, In-sample)

| 신호 | G3 중위 | Rest 중위 | Δ |
|------|---------|----------|---|
| Revenue Growth | +15.4% | +5.9% | +9.5% |
| OPM Trend | +0.9pp | +0.3pp | +0.6pp |
| OCF Growth | +31.0% | +3.2% | +27.8% |
| CapEx Intensity Δ | -0.2pp | +0.0pp | -0.3pp |
| FCF Growth | +49.2% | +0.2% | +48.9% |

G3 기업들은 매출 성장, 마진 소폭 개선, OCF 성장, CapEx 집약도 감소를 동반한다. "성장하면서 효율화되는" 패턴.

### G3 내 신호-alpha 상관

| 신호 | IS Corr | IS outlier 제거 후 | HO Corr (참고) |
|------|---------|----------------|-------------|
| FCF Growth | +0.864 | +0.055 | +0.197 |
| Revenue Growth | -0.079 | +0.080 | **+0.289** |
| OCF Growth | -0.089 | -0.172 | +0.241 |
| OPM Trend | -0.088 | -0.085 | +0.194 |
| CapEx Intensity Δ | -0.004 | -0.086 | -0.039 |

주의: FCF=OCF-CapEx 기계적 관계로 OCF Growth와 FCF Growth 간 독립 해석 불가.

### G3 내 패턴 분류

기준: Rev Growth > 0 / OPM Trend > 0 / OCF Growth > 0 각각 충족 수

| 패턴 | IS n | IS avg alpha | HO n | HO avg alpha |
|------|------|-------------|------|-------------|
| Triple+ (Rev+OPM+OCF) | 32 | +23.1% | 21 | +14.5% |
| Double | 33 | +25.3% | 13 | +15.5% |
| Single | 9 | +3.4% | 2 | +2.2% |

G3 IS n=75 중 86%가 복합 신호 기업(Triple+/Double). "FCF만 높고 나머지 약한" G3 기업은 소수이며 alpha도 낮다.

### 핵심 발견

**1. IS FCF Growth Corr +0.864은 outlier 4개 집중**
TSLA/NVDA/NFLX/AMZN 제거 후 +0.864 → +0.055. G3 내에서 "더 높은 FCF Growth → 더 높은 individual alpha"는 IS에서 outlier가 만든 관계다.

**2. Holdout에서 Revenue Growth가 더 강한 내부 신호 (+0.289)**
IS outlier 제거 후에도 Revenue Growth(+0.080)가 FCF Growth(+0.055)보다 안정적.

**3. G3는 복합 신호 기업의 집합 (86%가 Triple+/Double)**
"FCF Growth 기준 G3 진입" 자체가 복합 신호 기업을 자연스럽게 걸러내는 효과를 내고 있다.

### Growth Engine 설계 시사점

현재 v2_A(FCF Growth 5-tier) 단순 구조 유지가 정당화된다:
- FCF Growth가 G3 그룹 분리 기준으로는 유효 (US-1.5~1.7 확인)
- G3 내부 86%가 복합 신호 → FCF Growth 진입 기준이 복합 패턴을 내포

다음 버전 연구 방향:
- Revenue Growth + OPM Trend + OCF Growth 복합 지표가 G3 내부에서 더 안정적
- 단, 현재 봉인된 v2_A OOS 검증과 독립적으로 진행

*Research principle: 검증되지 않은 팩터를 production score에 반영하지 않는다.*

---

## US-2.2 — H-US-12: 복합 성장 신호 개수 vs Forward Alpha
> **Status: 🔵 Closed — SC≥2 alpha 효과 검증**


구현: `research/us_2_2_composite_signal.py`

### 목적

Revenue Growth > 0 / OPM Trend > 0 / OCF Growth > 0 의 동시 충족 수(signal_count 0~3)와 forward alpha의 관계 검증.
H-US-12 가설: signal_count가 높을수록 alpha가 단조 증가 (0 < 1 < 2 < 3).

### 실험 1차 (2026-09-05, n=578 IS / 50종목)

| Signal Count | IS n | IS avg α | HO n | HO avg α |
|:---:|---:|:---:|---:|:---:|
| 0 | 69 | +4.3% | 16 | +9.6% |
| 1 | 92 | +3.9% | 36 | +5.0% |
| 2 | 144 | **+12.7%** | 43 | **+11.9%** |
| 3 | 110 | +11.7% | 43 | +6.1% |

1차 잠정 해석: 단조성(❌)은 기각이나, SC≥2 binary threshold가 관찰됨.
- SC≥2: IS avg=+12.1%, SC≤1: IS avg=+4.1%, IS Δ=**+8.0%**

### 실험 2차 — 확장 표본 재현 시도 (2026-09-08, n=969 IS / 112종목)

Universe 50종목 → 112종목으로 확장 후 동일 분석 재실행.

| Signal Count | IS n | IS avg α | HO n | HO avg α |
|:---:|---:|:---:|---:|:---:|
| 0 | 142 | **+11.0%** | 47 | −2.4% |
| 1 | 224 | +5.5% | 92 | −0.2% |
| 2 | 360 | +7.7% | 91 | **+3.7%** |
| 3 | 243 | +6.7% | 79 | −0.4% |
| **단조성** | | ❌ | | ❌ |

SC≥2 spread:

| | 1차 (n=578 IS) | 2차 (n=969 IS) |
|--|:--:|:--:|
| SC<2 avg | +4.1% | +7.6% |
| SC≥2 avg | +12.1% | +7.3% |
| **Δ** | **+8.0%** | **−0.3%** |

**1차에서 관찰된 SC≥2 binary threshold(+8.0%)가 확장 표본에서 완전히 사라졌다.**

### 핵심 발견

**1. H-US-12 기각 — SC≥2 binary threshold는 샘플 편향이었다**
1차(n=578)에서 보인 SC≥2 premium은 확장 표본(n=969)에서 재현되지 않는다(Δ=-0.3%).
이 패턴이 작은 표본에서만 나타났으며 일반적 구조가 아님을 확인.

**2. SC=0 IS avg=+11.0% — Energy 사이클 왜곡**
확장 표본에서 SC=0이 가장 높은 alpha를 보인다. 섹터 테이블에서 Energy avg SC=1.29 (가장 낮음).
Energy 사이클 바닥(복합 개선 신호 0개) → 업황 반등기에 큰 수익의 구조.
**펀더멘털 개선 신호 부재가 주가 반등과 별개임을 보여주는 사례 (CLAUDE.md 설계 범위 밖).**

**3. 섹터별 분포 (확장 표본 IS)**

| Sector | avg SC | SC=3% | avg alpha |
|--------|:------:|:-----:|:---------:|
| IT | 2.01 | 40% | +17.4% |
| CD | 1.96 | 32% | +19.1% |
| Comm | 2.14 | 37% | +4.9% |
| Ener | 1.29 | 13% | +5.6% |
| Util | 1.63 | 17% | +1.3% |

### 결론

**H-US-12 기각. SC≥2 binary threshold는 alpha signal이 아니다.**

1차(n=578)에서 관찰된 SC≥2 premium은 소표본 편향이었음이 확장 표본(n=969)으로 확인.
이전 "binary gate 발견"은 철회. "초기 표본에서 관찰된 패턴이 확장 표본에서 재현되지 않음"으로 기록.

이는 **replication이 올바르게 작동한 사례**다: n=50이 n=112에서 수정됐다.

*Research principle: 검증되지 않은 팩터를 production score에 반영하지 않는다.*

---

## US-2.3 — H-US-13: SC≥2 alpha 효과의 섹터-중립성 검증

구현: `research/us_2_3_sector_neutral.py`

### 목적

SC≥2 raw spread가 섹터 구성 효과인지, within-sector 독립 효과인지 분리.
US-2.2 확장 표본 결과(SC≥2 Δ=-0.3%)로 인해 검증의 의미가 크게 달라졌다.

### 전체 spread 비교 (1차 n=578 vs 2차 n=969)

| | SC<2 avg | SC≥2 avg | Δ |
|--|:---:|:---:|:---:|
| IS (n=578, 1차) | +4.1% | +12.3% | **+8.2%** |
| IS (n=969, 2차) | +7.6% | +7.3% | **−0.3%** |
| HO (n=309, 2차) | −1.0% | +1.8% | +2.8% |

### 섹터별 within-sector 결과 (IS, n=969)

| Sector | SC<2 avg | SC≥2 avg | Δ | n_lo | n_hi |
|--------|:---:|:---:|:---:|:---:|:---:|
| CD | +7.5% | +23.9% | +16.4% ✅ | 30 | 72 |
| CS | +2.7% | −1.4% | −4.1% ❌ | 51 | 64 |
| Comm | +12.0% | +3.4% | −8.6% ❌ | 16 | 75 |
| Ener | +15.7% | −6.1% | **−21.8%** ❌ | 58 | 50 |
| Fin | +6.0% | +10.4% | +4.4% ✅ | 46 | 32 |
| HC | +6.6% | +8.8% | +2.2% ✅ | 41 | 78 |
| IT | +23.8% | +15.3% | −8.6% ❌ | 23 | 68 |
| Indu | +0.3% | +7.6% | +7.3% ✅ | 33 | 67 |
| Matl | +4.9% | +4.7% | −0.2% ❌ | 30 | 48 |
| Util | +2.0% | +0.8% | −1.3% ❌ | 38 | 49 |

IS 양(+) 효과 섹터: 4/10 (40%) — 랜덤 수준

### 섹터-가중 aggregate 및 counterfactual

| 지표 | 1차 (n=578) | 2차 (n=969) |
|------|:--:|:--:|
| IS 섹터-가중 Δ | +2.0% | **−1.1%** |
| HO 섹터-가중 Δ | +1.3% | **+3.8%** |
| IS IT+CD 제거 Δ | +2.3% | **−2.9%** |
| HO IT+CD 제거 Δ | −0.4% | **−1.9%** |

### 핵심 발견

**1. H-US-13 강하게 기각 — SC≥2는 섹터-중립 factor가 아니다**

1차(n=578) 분석에서 섹터-가중 Δ=+2.0%가 남아 있었으나, 확장 표본(n=969)에서 IS=-1.1%로 음전환.
IT+CD 제거 시 IS=-2.9%, HO=-1.9% 모두 음수.
양(+) 효과 섹터 4/10(40%)는 랜덤 수준. SC≥2는 어떤 형태의 within-sector 신호도 아님.

**2. Energy 섹터가 SC<2 쪽을 끌어올림**

Energy SC<2 avg=+15.7% vs SC≥2 avg=-6.1%, Δ=-21.8%.
성장 신호가 없는(SC<2) Energy 주식이 업황 반등기에 더 높은 alpha를 내는 구조.
이는 SC signal이 cyclical recovery를 포착하지 못함을 보여주는 구조적 증거.

**3. 1차 분석의 소표본 이슈 재확인**

1차에서 n이 작아 신뢰할 수 없던 셀들(Comm SC<2 n=3, IT SC<2 n=4)이 확장 표본에서 정상화됨(Comm n=16, IT n=23). 1차 n=578 내 within-sector 분석은 해석력이 없었음.

### 결론

**H-US-13 기각. SC≥2의 alpha premium은 within-sector에 존재하지 않는다.**

US-2.2(H-US-12 기각)와 함께 결론: **SC≥2 composite signal은 어떤 alpha predictive power도 없다.**
다음 연구(US-2.4a)의 질문은 FCF Growth 단독 효과가 확장 표본에서도 살아 있는가이다.

*Research principle: 검증되지 않은 팩터를 production score에 반영하지 않는다.*

---

## US-2.4a — FCF Growth Expanded-Sample Replication (2026-09-08)
> **Status: 🔵 Closed — FCF replication 확장**


구현: `research/us_2_4a_fcf_growth.py`

### 목적

US-2.1에서 관찰된 FCF Growth 5-tier alpha 패턴이 확장 표본(n=969 IS)에서도 재현되는지 검증.

### 가설

**H-US-14**: FCF Growth가 forward alpha와 양의 관계를 가진다 (Q5 > Q1, Corr > 0).

### 결과

**FCF Growth 5-tier (IS, n=969)**

| Quintile | avg fcf_growth | avg alpha |
|:---:|:---:|:---:|
| Q1 (최저) | 극단 음수 | **+14.4%** ← 최고 |
| Q2 | 소폭 음수 | +낮음 |
| Q3 | 중립 | 중간 |
| Q4 | 양수 | 중간 |
| Q5 (최고) | 극단 양수 | 중간 이하 |

IS Corr(fcf_growth_quintile, fwd_alpha) spread = **−4.5pp** (음수 — 기대 방향과 반대)

**FCF × v1 Score 2×2 Matrix (IS)**

| | SCORE_LO | SCORE_HI |
|---|:---:|:---:|
| **FCF_LO** | **+12.9%** ← 최고 | 중간 |
| **FCF_HI** | +12.2% | 낮음 |

원래 G3(FCF_HI × SCORE_LO)가 최고 셀이 아님. FCF_LO × SCORE_LO가 더 높다.

### 핵심 발견

**H-US-14 FAIL — 동일 Cyclical 메커니즘**

SC=0, Low v1 Score와 동일한 구조: 성장 신호가 약한(FCF Q1) 종목이 업황 반등기에 큰 alpha를 낸다.
Energy 사이클 바닥에서 FCF가 극단적으로 낮은 종목이 다음 해 급등하는 패턴.

이것은 FCF Growth의 예측력이 없다는 게 아니라, Q1이 "진짜 FCF 저성장"이 아니라 "사이클 바닥 Energy"를 포함하기 때문이다.

**G3 설계 시사점**:
- FCF Growth 단독 신호: 확장 표본에서 예측 방향이 반전 → H-US-14 기각
- FCF_LO × SCORE_LO > FCF_HI × SCORE_LO → G3(High FCF + Low Score) 우위 구조 약화
- US-1.5~1.7에서 확인된 G3 효과는 소표본(IS n=72~376)에서의 관찰이었으며, 확장 표본에서 재현 강도가 감소함

### 결론

**H-US-14 기각. G3 성장 신호 전체가 확장 표본에서 재현력이 약함.**

G3 연구(US-1.3~US-2.1)는 소표본 기간(50종목)에서의 탐색적 연구로 기록한다.
현재 v2_A Candidate 상태 유지(production 반영 보류), 2027년 OOS 검증 계획은 그대로 유효.

*Research principle: 검증되지 않은 팩터를 production score에 반영하지 않는다.*

---

## US-2.5 — H-US-15: Low Score → High Alpha 역전 분해 (2026-09-08)
> **Status: 🔵 Closed — Low score → high alpha 역전 분해. Research Design Update 트리거**


구현: `research/us_2_5_low_score_decomp.py`

### 목적

IS에서 일관되게 관찰되는 "Low v1 Score → High Alpha" 역전이 구조적인가, 아니면 통제 가능한 요인(섹터, prior return, 연도, 컴포넌트)으로 설명되는가.

### 가설

**H-US-15**: Low v1 Score → High Alpha 역전은 섹터 / prior return / 연도 통제 후 사라진다.

통과 기준(기각 = 역전이 구조적): 통제 후에도 IS Spread ≤ −5pp로 유지.

### 결과

**기준선 (Score 5-tier, IS n=969)**

IS Corr(score_quintile, fwd_alpha) spread: **−12.6pp** (Q5 alpha - Q1 alpha = −12.6pp)
방향: High Score → Low Alpha. 설계 기대 방향과 반대.

**Control 1: 섹터 중립**

섹터-가중 spread 계산: 섹터 구성 효과 제거 후에도 역전 유지.

**Control 2: Prior Return (3 bucket)**

직전 연도 수익률로 3구간 나눠 각 구간 내 spread 계산: 역전 유지.

**Control 3: 연도별 (Year-by-year)**

| 연도 | IS spread | 방향 |
|:---:|:---:|:---:|
| 11개년 | 음수 | ❌ 역전 |
| 2개년 | 양수 | ✅ 정상 |
| 합계 | **11/13** | 역전 압도적 |

**Control 4: 컴포넌트 분해**

각 score 컴포넌트 단독으로 alpha와의 spread 계산:

| 컴포넌트 | IS spread | 방향 |
|---------|:---:|:---:|
| PER score (s_per) | **−14.4pp** | ❌ 가장 강한 역전 |
| EH score (s_eh) | −8.6pp | ❌ |
| PBR score (s_pbr) | −5.9pp | ❌ |
| ROE score (s_roe) | −5.6pp | ❌ |
| MOM score (s_mom) | **+0.9pp** | ✅ 유일하게 설계 방향 |

### 핵심 발견

**H-US-15 기각 — 역전은 4개 통제 후에도 구조적으로 유지된다**

1. **섹터, prior return, 연도 통제 모두 후에도 역전이 사라지지 않는다.** 이 패턴은 특정 기간/섹터의 composition 효과가 아니다.

2. **MOM score만 유일하게 설계 방향 (+0.9pp).** Value (PER, PBR), Quality (ROE), EH 모두 역전이지만, Momentum만 약하게나마 High score → High alpha 방향을 유지한다.

3. **PER score가 가장 강한 역전 (−14.4pp).** 저PER(고 PER 점수)이 S&P 500 우주에서 오히려 underperform하는 구조 — 성장주 강세 regime의 반영.

4. **이 역전이 IS 전체(2007~2021)에 걸쳐 11/13년에서 나타난다.** 단일 연도나 단일 섹터의 이상 현상이 아니다.

### 시사점

"IS 결과를 보고 Value/Quality weight를 낮추거나 뒤집어야 한다"는 결론을 내리면 IS regime에 overfitting하는 것이다. → US-2.6 Holdout 검증이 핵심.

MOM score의 +0.9pp는 "Value/Quality가 역전되는 구간에서도 Momentum만 방향이 맞았다"는 약한 긍정 증거. US-2.6에서 Holdout 재현 여부 확인.

*Research principle: IS 패턴이 HO에서 재현되지 않으면 scoring 변경 근거로 사용하지 않는다.*

---

## US-2.6 — H-US-16: Momentum 독립 검증 (2026-09-08)
> **Status: 🔵 Closed — Momentum 독립 검증. Continuation 미확인**


구현: `research/us_2_6_momentum_validation.py`

### 목적

Momentum이 Value/Quality와 독립적으로 alpha를 예측하는가를 IS/HO 모두에서 검증.

### 가설

**H-US-16**: Momentum(raw_mom, s_mom)은 v1_ex_mom(Value+Quality+EH)을 통제한 후에도 독립적으로 positive alpha를 예측한다.

**판정 기준 (사전 고정)**:
- Strong: IS+HO 모두 양수 + Hi>Lo ≥ 60% + outlier 제거 후 안정
- Weak: HO 양수 + IS 약하거나 outlier 의존
- No evidence: HO ≈ 0 또는 음수

### 신호 정의

| ID | 신호 | 설명 |
|---|---|---|
| ① | raw_mom | 12-2 raw 수익률 |
| ② | s_mom | v1 Momentum score (0~20) |
| ③ | v1_ex_mom | s_per + s_pbr + s_roe + s_eh (Value+Quality+EH, Momentum 제외) |
| ④ | v1 Full | 전체 v1 score |

IS cuts를 IS에서만 계산 → HO에 동일 cut 적용 (data leakage 방지).

### 결과

**IS / HO Corr (Q5−Q1 spread)**

| 신호 | IS spread | HO spread | IS/HO 일치 |
|---|:---:|:---:|:---:|
| ① raw_mom | +1.2pp | **+3.4pp** | ✅ |
| ② s_mom | +0.9pp | **+4.8pp** | ✅ |
| ③ v1_ex_mom | −12.9pp | **+0.0pp** | ❌ (방향 불일치) |
| ④ v1 Full | −12.6pp | −0.7pp | ✅ (둘 다 음수) |

**Extreme Momentum 제거 효과**

| | IS spread | HO spread |
|---|:---:|:---:|
| 전체 | +1.2pp | +3.4pp |
| 상위 5% raw_mom 제거 | **−2.7pp** ← 반전 | **+3.6pp** ← 유지 |
| 상위 1% raw_mom 제거 | −0.1pp | (유사) |

IS는 상위 5% 제거 시 음수로 반전 → IS 양수 Corr은 소수 extreme 종목 의존.
HO는 상위 5% 제거 후에도 +3.6pp 유지 → HO는 outlier 독립적.

**Year-by-year Hi>Lo (IS 기준)**

| 항목 | 결과 |
|---|---|
| Momentum Hi>Lo 연도 | 7/12 (58%) |
| 60% 기준 달성 | ❌ (2pp 미달) |
| 단조성 | 없음 |

**Sector-neutral (HO)**

섹터-가중 Δ = −2.6%, 양(+) 섹터 7/10 — 가중치 기준 음수이지만 섹터 방향은 과반 양수.

**v1_ex_mom IS→HO 비교 (가장 중요한 발견)**

| | IS | HO |
|---|:---:|:---:|
| v1_ex_mom spread | −12.9pp | **+0.0pp** |

IS에서 관찰된 "Low v1_ex_mom(Value/Quality) → High Alpha" 역전이 HO(2022-2024)에서 완전히 사라진다.

### 판정: **Weak**

| 기준 | 결과 |
|---|---|
| IS+HO 모두 양수 | ✅ (+1.2pp / +3.4pp) |
| Hi>Lo ≥ 60% | ❌ (58%) |
| IS outlier 제거 후 안정 | ❌ (−2.7pp로 반전) |
| HO outlier 제거 후 안정 | ✅ (+3.6pp 유지) |

**최종 판정: Weak — HO에서 약한 긍정 증거, production 채택 근거로는 불충분**

### 핵심 해석

**발견 1 — v1_ex_mom IS 역전의 HO 붕괴**

IS에서 −12.9pp였던 Value/Quality 역전이 HO에서 +0.0pp로 사라진다.
이것이 이번 실험에서 가장 중요한 결과다: IS 역전 패턴을 보고 v1 weight를 뒤집었다면, HO에서 그 결정이 틀렸음이 확인된다.
**IS의 Value/Quality 역전은 2007~2021 growth stock 강세 regime의 특성이었다.** HO(2022~2024)에서는 이 regime이 종료됐다.

**발견 2 — Momentum HO 강건성**

IS는 outlier 의존적이지만, HO(2022~2024)에서 Momentum은 outlier와 무관하게 방향이 살아 있다.
"Momentum이 방향은 맞지만 아직 충분한 증거가 없다"가 현재 가장 정확한 상태다.

**발견 3 — v1 Freeze 유지 근거**

IS 역전이 HO에서 재현되지 않는다는 것은 v1 scoring을 IS 결과 기반으로 수정하지 않아야 한다는 가장 강한 근거다. 검증 설계가 의도대로 작동했다.

### 다음 단계

Momentum의 "Weak" 판정은 "버려야 한다"가 아니라 "아직 표본이 부족하다"다.
HO n을 늘리는 것이 같은 데이터를 더 분석하는 것보다 낫다. → US-2.7: 120종목 Universe 확장.

*Research principle: IS 패턴이 HO에서 재현되지 않으면 scoring 변경 근거로 사용하지 않는다.*

---

## US-2.7A — Survivor-Controlled 248 Universe Momentum Replication (2026-09-08)
> **Status: 🟡 Current — 248 universe momentum replication. 현재 연구 기준점**


구현: `research/us_2_7a_build_248.py` → `us_0_4_score_v1.py` → `us_2_7a_forward_panel.py` → `us_2_7a_momentum_validation.py`

### 목적

US-2.6 (120종목) 결과를 더 큰 생존자 통제 Universe에서 재현하고 HO 표본 수를 늘린다.

생존편향 통제: 분석 Universe = 2007년 말 S&P500 ∩ 2025년 S&P500 = **248종목**.
양쪽 모두에 속한 종목만 포함 → IS/HO 전 기간 생존 기업. Bias 방향 명시: 생존편향으로 인해 momentum 효과가 과대평가될 가능성.

### Universe

| 항목 | 값 |
|---|---|
| 설계 Universe | 2007 S&P500 ∩ 2025 S&P500 = 248종목 |
| 실제 커버 | 65종목 (yfinance 가격 + scored_panel_v1 교집합) |
| CIK 조회 실패 | 7종목 (AVB, BK, EQR, IPG, K, MMC, WBA) |
| IS (≤ FY2021) | n = 728 |
| HO (≥ FY2022) | n = 193 |
| IS raw_mom 분위 경계 | [−0.034, +0.090, +0.189, +0.321] |

### 결과

**A. 전체 IS / HO spread (Q5−Q1)**

| | IS spread | HO spread |
|---|:---:|:---:|
| 전체 | +0.74pp | **+8.55pp** ✅ |
| 상위 5% 제거 | −0.62pp ❌ | **+12.59pp** ✅ |
| 상위 1% 제거 | −1.26pp ❌ | **+8.72pp** ✅ |

IS는 outlier 의존적(5% 제거 시 음수 반전). HO는 outlier 제거 후 오히려 강화.

**B. Year-by-year Holdout**

| FY | n | spread | 판정 |
|---|:---:|:---:|:---:|
| 2022 | 64 | +12.50pp | ✅ |
| 2023 | 65 | +7.19pp | ✅ |
| 2024 | 64 | −0.05pp | ❌ |
| 양(+) 연도 | — | 2/3 | ❌ (<60%) |

**C. Ticker-cluster bootstrap (B=2000, 95% CI)**

| | point | 95% CI | 판정 |
|---|:---:|:---:|:---:|
| IS | +0.74pp | [−4.30pp, +5.58pp] | ⚠ CI ∋ 0 |
| HO | +8.55pp | [−8.41pp, +24.79pp] | ⚠ CI ∋ 0 |
| FY2022 | +12.50pp | [−14.96pp, +61.60pp] | ⚠ |
| FY2023 | +7.19pp | [−11.12pp, +25.37pp] | ⚠ |
| FY2024 | −0.05pp | [−25.25pp, +28.88pp] | ⚠ |

CI가 모두 0을 포함 — HO n=193으로 통계적 유의성 미달.

**E. Sector-neutral (HO)**

| 섹터 | n | HO spread | |
|---|:---:|:---:|:---:|
| CD | 18 | +20.09pp | ✅ |
| CS | 31 | +6.77pp | ✅ |
| Comm | 12 | +8.69pp | ✅ |
| Ener | 21 | −36.55pp | ❌ |
| Fin | 19 | +2.71pp | ✅ |
| HC | 24 | +7.05pp | ✅ |
| IT | 12 | −62.96pp | ❌ |
| Indu | 26 | +30.49pp | ✅ |
| Matl | 18 | +27.99pp | ✅ |
| Util | 12 | +6.52pp | ✅ |
| **양(+) 섹터** | — | **8/10** | — |

Ener(−36.55pp)와 IT(−62.96pp)가 강한 역방향 outlier.

**F. Sector-excluded (Ener / IT 제외)**

| | n | HO spread | |
|---|:---:|:---:|:---:|
| 전체 HO (참고) | 193 | +8.55pp | ✅ |
| Ener/IT 제외 | 160 | **+19.30pp** | ✅ |
| Ener/IT 제외 + 상위5% 제거 | 153 | **+24.20pp** | ✅ |

| FY | n | spread (제외 후) | |
|---|:---:|:---:|:---:|
| 2022 | 52 | +23.76pp | ✅ |
| 2023 | 53 | +13.33pp | ✅ |
| 2024 | 53 | +12.16pp | ✅ |
| **양(+) 연도** | — | **3/3** | ✅ |

Ener/IT 제외 시: 전 연도 양수 + outlier 제거 후 더 강화. 나머지 8개 섹터에서 Momentum은 HO 전 기간 일관성 있음.

### 판정 요약

| 기준 | 전체 HO | Ener/IT 제외 |
|---|:---:|:---:|
| HO spread 양수 | ✅ +8.55pp | ✅ +19.30pp |
| IS+HO 모두 양수 | ✅ | ✅ |
| Hi>Lo ≥ 60% | ❌ 2/3 | ✅ 3/3 |
| IS outlier 제거 후 안정 | ❌ 반전 | ❌ 반전 |
| HO outlier 제거 후 안정 | ✅ 강화 | ✅ 강화 |

**최종 판정: 🟡 Weak** (IS outlier 의존으로 Strong 기준 미달)

### 핵심 해석

**IS 불안정은 구조적 문제** — IS(2007~2021)는 극단적 성장주 regime이 포함됨. IS에서 관찰된 역전 패턴이 HO(2022~2024)에서 재현되지 않는 US-2.6과 동일한 패턴.

**HO에서 Momentum은 Ener/IT 제외 시 일관성 있음** — 8/10 섹터 양수, 전 기간 단조적. Ener과 IT의 역전은 해당 섹터의 가격-이익 관계 특수성(에너지 가격 사이클, IT 고밸류 구조 전환)에 기인할 가능성.

**표본 부족이 핵심 제약** — HO n=193. Bootstrap CI 전 구간 포함. 통계적 결론 보류. US-2.6과 US-2.7A 누적 HO n ≈ 390으로도 bootstrap CI 폭이 크다.

### 다음 단계

US-2.7A는 US-2.6 결과의 방향을 재확인. IS outlier 의존 문제는 동일하게 존재.
Momentum 검증은 HO n 누적(영업일 시간 경과)에 의존. 추가 IS 확장보다 HO 누적 대기.

*Research principle: HO에서 재현되지 않는 IS 패턴은 scoring 변경 근거로 사용하지 않는다.*
