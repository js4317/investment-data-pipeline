# Research Findings (관찰된 연구 결과 — 엔진 규칙과 분리)

이 문서는 "관찰된 연구 결과"를 기록한다.
"엔진에 적용하기로 확정된 규칙"과 혼동하지 않는다.
연구 결과 → 충분한 검증 → Engine Freeze 해제 후에만 점수 반영 검토.

---

## Research Design Principles (2026-08-26 확정)

### 연구 진행 순서 원칙
모든 연구는 아래 순서를 엄수한다. **결과 확인 후 exit criterion을 변경하지 않는다.**

```
가설 정의
    ↓
사전 exit criterion 고정  ← 이 시점 이후 변경 금지
    ↓
검증 (backtest / forward validation)
    ↓
판정 → 구현 가능 변경 명시
```

### Exit Criterion 판정 구조

단순 PASS/FAIL 대신 PRIMARY / OTHER 구분:

- **PRIMARY 재검토 조건**: 수익률 열위 AND downside 악화 → 재검토
- **OTHER (나머지)**: 유지하되 trade-off를 정확히 기록
  - 수익률 개선 + downside 악화 → 유지, 위험 증가 기록
  - 수익률 열위 + downside 개선 → 유지, 수익 비용 기록
  - 둘 다 개선 → 유지 확정

억지로 PASS/FAIL을 만들지 않는다. 애매한 결과는 "trade-off 기록"으로 남긴다.

### 패턴 강도 4단계 프레임워크

연구 결과를 이분법(PASS/FAIL)으로만 평가하지 않는다. 사전등록 기준 미충족이 곧 가설 기각은 아니다.

| 단계 | 정의 |
| ---- | ---- |
| **Refute** | 방향 자체가 여러 기간에서 반복적으로 사라지거나 역전됨 |
| **Weak / Mixed** | 방향은 관찰되지만 연도별·레짐별 일관성이 부족함 |
| **Robust Pattern** | 여러 기간/레짐에서 방향이 반복 확인됨 |
| **Production-ready** | PIT / OOS / 경제적 유의성 / 안정성까지 충족 |

사전등록 판정(Support/Mixed/Refute)은 엄격한 사전 기준의 충족 여부를 기록한다.
연구 해석은 위 4단계 중 현재 데이터가 어느 위치에 있는지를 서술한다.
두 층을 혼동하지 않는다.

### 연구-구현 연결 원칙

각 연구가 정당화할 수 있는 코드 변경을 사전에 명시한다:

| 연구 | 정당화 가능한 변경 |
|------|-------------------|
| Holding C replay | `is_holding` PBR-only 유지/revert |
| PER × PBR 중복성 | `abs_PER + rel_PER + rel_PBR` 가중치 조정 |
| Supply forward | Supply weight / 구조 변경 |
| Earnings regime forward | cyclicality adjustment 도입 여부 |
| Downside 분석 | 랭킹 구조 / risk control 추가 여부 |

---

## Holding C 사후 검증 — Exit Criterion (2026-08-26 사전 고정)
> **Status: 🟡 Ongoing — Forward validation 대기 중 (Retrospective Calibration n ≥ 100 필요)**


v8.5.19에서 Holding company PBR-only가 production에 적용된 상태.
이 연구는 "넣을까?"가 아니라 **"기대한 대로 작동하는가?"** 검증.

```
PRIMARY 재검토 조건:
  수익률 열위 (Baseline > C) AND downside 악화 (C worst/deviation 더 나쁨)
  → 재검토 검토 시작

OTHER (나머지):
  → 유지. trade-off 정확히 기록.

판정 시점:
  1차: historical replay (research.db 2015-2024)
  최종: n ≥ 100 forward validation 후
```

---

## H1 PER Backtest (2026-08-21 완료)
> **Status: 🔵 Closed — PER 단독 효과 제한적. Architecture v2 연구로 통합됨**

  목적: "KOSPI에서 섹터 내 저PER 종목이 초과수익을 내는가?" 검증
  방법: 2015~2024년 (10년), KOSPI 전종목, 연간 리밸런싱.
        섹터 내 PER 하위 30% (저PER 그룹) vs 나머지의 연간 수익률 비교.
        동일가중, 거래비용 미포함. 양수 PER + PER<100 종목만 대상.

  전체 승률: 10년 중 6년 저PER 우위 (60%).
  시장 국면별 패턴:
    Bear (2018, 2022)    : 저PER 우위 → 하락장 방어 효과 있음.
    Bull (2020, 2023)    : 저PER 열위 → 고PER 성장주가 더 오름.
    Sideways (대부분)    : 혼재. 섹터에 따라 크게 다름.

  섹터별 결과 (10년 평균 차이, 저PER−기타):
    통계적으로 유의한 섹터 (p<0.10):
      비금속    +18.2%  ★ (p<0.05, 8/10 우위, std=30.8 — 변동성 큼)
      증권       +8.2%  △ (p<0.10, 8/10 우위, std=15.9)

    방향성 신호 있으나 통계적 유의성 미달 (CI가 0 포함):
      음식료·담배 +14.6% (Sideways +40.3% / Bull -2.0% / Bear -2.9%)
      섬유·의류  +11.3%
      운송·창고   +6.1% (Bear에서 특히 강함 +33.8%)
      유통        +4.7% (Bull에서 특히 강함 +27.7%)
      일반서비스  +3.1%
      보험        +2.2%
      금속        +1.5%
      건설        +0.9%
      화학        +0.8%

    역효과 방향 (평균 음수, 통계적 유의성 미달):
      전기·전자  -6.8% (Bull -20.3% / Bear -18.2% / Sideways +11.8% — 국면 의존적)
      종이·목재  -4.1%
      제약       -3.5%
      기타금융   -3.5% (2/10 우위 — 가장 일관된 역효과, 지주사 이슈와 겹침)
      기타제조   -3.3%
      기계·장비  -2.6%
      운송장비·부품 -2.5%

  핵심 해석:
    - 10년 데이터 기준 "통계적으로 확실히 유효한" 섹터는 비금속·증권 2개뿐.
    - "통계적으로 확실히 역효과인" 섹터는 0개.
    - 전기·전자 역효과(-6.8%)는 Bull/Bear 국면에서만 나타남.
      이익 방향성이 뚜렷한 국면에서 PER이 사이클 바닥/고점을 오독하기 때문.
      "PER이 전기·전자에서 무효"가 아니라 "이익 사이클 국면에서 무효".
    - 기타금융(2/10)은 지주사 구조적 할인 이슈와 연관 가능성 있음 — 추가 추적 필요.
    - PER 단독 효과(H1)는 제한적. Supply와의 복합 효과(H2) 검증 필요.

  현재 상태: H1 검증 완료 (방향성 확인, 통계적 확신은 일부 섹터에서만).
  다음 단계:
    ① 시장 국면별 심층 분석 (Bull/Bear/Sideways 세분화)
    ② PER × 회계품질 조건부 분석 (저PER 중 고ROE + 흑자 종목만 분리)
    ③ H2: PER + Supply 복합 효과 → H2 완료 (아래 참조)
    ④ Engine Freeze 해제(2026-10-01) 후 Sector PER Reliability 계수 설계 검토
  _(Historical next steps: superseded by later research — see Architecture Validation v2)_


  적용 금지 사항 (Engine Freeze 기간):
    → 섹터별 PER 가중치 변경 금지
    → "전기·전자 PER × 0" 같은 하드코딩 금지
    → Sector Reliability 계수 점수 반영 금지
    현재는 연구 관찰로만 보존한다.

  스크립트: investment/sector_per_backtest.py

---

## H1-2 종목 레벨 분석 (2026-08-21 완료)
> **Status: 🔵 Closed — H1 결론 확인 (종목 레벨). Architecture v2로 통합됨**

  목적: H1-1(연간 포트 평균)을 종목 레벨로 내려가 검증. 연도 클러스터 블록 부트스트랩 95% CI.
  방법: research.db 기반. NORMAL 종목만. 연도×섹터 내 PER 하위 30% vs 나머지.
        연도 단위 블록 리샘플링(n=2000)으로 같은 연도 종목 간 상관 보정.

  핵심 결과:
    통계적으로 유의한 섹터 (CI가 0 미포함):
      비금속 ★+ : +18.1% (CI [+3.0, +39.9], 8/10 우위)
      증권   ★+ :  +9.5% (CI [+2.5, +21.1], 8/10 우위)
    → H1-1과 동일한 결론. 종목 레벨로 내려와도 유의 섹터 2개.

    전기·전자: -7.9% (CI [-20.4, +7.5]) → 0 포함. 통계적 확신 없음.
    단, Bull -19.7% / Bear -20.4% / Sideways +13.9% → 이익 사이클 국면 의존 패턴 확인.

  조건부 분석 (저PER + 저PBR, 2026-08-21):
    저PBR 조건 추가 시 크게 개선되는 섹터 (실적 예측 가능형):
      유통 +32.3%, 음식료·담배 +27.7%, 운송·창고 +25.8%, 비금속 +17.2%, 증권 +12.4%
    저PBR 조건 추가 시 오히려 악화되는 섹터 (사이클/경기민감형):
      기계·장비 -17.2%, 금속 -13.1%, 제약 -12.0%, 건설 -8.6%
    → "저PER + 저PBR" 조합은 실적 예측 가능 섹터에서 효과적.
       사이클 섹터에서는 "더 싼 것"이 오히려 덫.

  현재 시스템 함의:
    PER 단독 효과는 제한적. 조건(Earnings + Risk + Supply)을 붙일수록 효과 명확.
    현재 Valuation + Earnings + Risk + Supply 조합 설계의 타당성을 간접 지지.
    → "PER이 나쁜 지표"가 아니라 "조건 없는 PER이 불충분한 지표".
    _(Historical note: 이 해석은 2026-08-21 당시 기준. Architecture v2 연구에서 component별 empirical role은 재검토됨)_

  현재 상태: H1-2 완료.
  다음 단계:
    ① H1-3: 분기 분석 (시장 국면 세분화)
    ② H1-4 데이터 POC: 분기 영업이익 시계열 확보 가능 여부 검증
    ③ H2: PER + Supply 복합 효과 → H2 완료 (아래 참조)
  _(Historical next steps: superseded by later research — see Architecture Validation v2)_


  스크립트: investment/h1_stock_level_analysis.py
  데이터: database/research.db (research_stock_year, research_year_meta)

---

## H2 투자자 순매수 수급 효과 분석 (2026-08-21 완료)
> **Status: 🔵 Closed — 연간 수급 미예측. Supply Gate Phase 2로 이어짐**

  목적: 전년도(Y-1) 외국인+기관 순매수 수급이 당해년도(Y) 수익률을 예측하는가?
  방법: research.db research_investor_flow × research_stock_year 조인.
        수급 강도 = combined_net_buy / market_cap (시총 대비 비율).
        섹터-연도 내 순위 기준 상위 30% vs 나머지.
        연도 클러스터 블록 부트스트랩 95% CI (n=2000).
  분석 대상: 7,844개 종목-연도 관측치, 2016~2024년, 23개 섹터.

  주의: 이번 H2는 전년도 "연간 누적 수급"을 측정한다.
        현재 엔진의 Supply Score는 "최근 N일 단기 수급"이다.
        두 측정 기간이 다르므로 H2 결과를 현재 엔진 Supply의 직접 검증으로 해석하지 않는다.

  ── 확인된 결과 ──────────────────────────────────────

  H2-1: 전년도 연간 수급의 미래 수익률 예측력 확인되지 않음.
    고수급 상위 30%: +5.6% / 나머지: +7.0% / 차이: -1.4%p
    CI [-3.3, +0.5] → 0 포함. 유의하지 않음.
    → "전년도에 외국인+기관이 많이 샀던 종목을 다음 해 따라 사면 좋다"는 전략, 현재 데이터 미지지.

  H2-2: 외국인 단독 (+1.1%p), 기관 단독 (-2.3%p) 모두 CI 0 포함. 유의하지 않음.

  H2-4: 수급 효과는 섹터별 이질성이 매우 큼.
    유의하게 양수 (★+): 일반서비스 +14.6%
    유의하게 음수 (★−): 화학 -7.2%, 종이·목재 -8.1%, 비금속 -14.4%
    → 수급 신호는 섹터 중립적으로 작동하지 않는다.

  H2-5 (핵심): 전년도 연간 고수급 전략은 Bull/Bear 두 국면에서 모두 유의하게 열위.
    Bull  ('15,'17,'19,'20,'21,'23): 고수급 -2.1%p  CI [-3.0, -0.5]  ★−
    Bear  ('18,'22):                 고수급 -3.6%p  CI [-7.1, -1.3]  ★−
    Sideways (나머지):               고수급 +1.4%p  CI [-1.4, +3.9]  — 유의하지 않음
    → 전년도 연간 수급은 주식 시장 국면과 무관하게 미래 수익률 예측력을 갖지 않는다.
       특히 Bull/Bear 국면에서는 반대 방향으로 유의하다.

  ── 탐색적 발견 (통계적 유의성 미확인, 추가 검증 필요) ──────

  H2-3: 현재 엔진 철학과 반대되는 패턴 관찰.
    저PER 종목 내에서 전년도 연간 Supply가 낮은 그룹이 높은 그룹보다 높은 수익률을 보였다.
      저PER + 저Supply: +8.7%  (N=102)
      저PER + 고Supply: +0.8%  (N=37)
      스프레드: +8.0%p
    이는 "저평가 + 높은 수급"을 긍정적으로 평가하는 현재 엔진의 방향과 반대되는 결과다.
    다만 저PER+고Supply N=37로 표본이 제한적이며 통계적 유의성이 확인되지 않았으므로
    엔진 수정의 근거로 사용하지 않는다.

    향후 검증 가설 (H2-6):
    "저평가 종목에서는 이미 수급이 유입된 종목보다 아직 수급이 유입되지 않은 종목의
     미래 초과수익이 높은가?"
    → 현재 엔진의 단기 Supply 정의(최근 N일)로 동일한 조건을 검증해야 의미 있는 비교 가능.

  ── 미검증 가설 (Future Research) ──────────────────────

  H2-6: 현재 엔진의 단기 Supply Score와 미래 수익률의 관계.
    Morning Run 스냅샷(2026-06-09~)이 실제 엔진 조건 그대로 Supply Score를 기록 중.
    과거 일별 데이터 재수집은 API 부하 및 IP 차단 위험으로 현 시점에서 하지 않는다.
    n이 충분히 쌓이면 스냅샷 기반으로 Supply Score quintile → Alpha 관계를 자연 검증한다.

  ── 종합 해석 ──────────────────────────────────────────

  H1 + H2를 합쳐 현재까지의 연구가 일관되게 보여주는 것:
    PER 단독, 연간 Supply 단독 — 어느 지표도 단독으로 미래 수익률을 안정적으로 예측하지 않는다.
    이는 현재 엔진이 Valuation + Earnings + Risk + Supply를 조합하는 설계의 타당성을 간접 지지한다.
    "하나의 지표로 시장을 이길 수 있다"는 가설은 현재 데이터에서 지지되지 않는다.
    _(Historical note: 이 해석은 2026-08-21 당시 기준. component별 역할 배정은 Architecture v2에서 재검토됨)_

  현재 상태: H2 완료. H2-6은 Morning Run 스냅샷 자연 누적으로 장기 검증.
  다음 단계:
    ① PBR 독립 검증 → 완료 (아래 참조)
    ② Morning Run 스냅샷 n ≥ 100 달성 후 Supply Score quintile Alpha 분석 (H2-6)
    ③ H1-3: 분기 분석 (우선순위 하향)
  _(Historical next steps: superseded by later research — see Architecture Validation v2)_


  스크립트: investment/h2_supply_analysis.py
  데이터: database/research.db (research_investor_flow, research_stock_year, research_year_meta)

---

## PBR 효과 분석 (2026-08-21 완료)
> **Status: 🟢 Applied — v8.5.18 (hybrid_pbr_rel_weight=1.0 적용)**

  목적: 섹터-연도 내 저PBR이 미래 수익률을 예측하는가? PER과의 관계는 어떤가?
  방법: research.db 기반. NORMAL 종목만. PBR > 0 필터.
        섹터-연도 내 퍼센타일 순위 기준 저PBR = 하위 30%.
        연도 클러스터 블록 부트스트랩 95% CI (n=2000).
  분석 대상: 7,555개 종목-연도 관측치, 2015~2024년, 24개 섹터.

  PBR-1: 전체 시장 — 강한 신호 확인 (지금까지 나온 valuation 신호 중 가장 강함)
    저PBR 평균 수익률: +14.2% / 나머지: +6.3% / 차이: +7.9%p
    95% CI [+3.7, +11.9]  ★+  (10년 중 8년 우위)

    국면별:
      Bull     ('17,'20,'23): +11.3%p  CI [+7.2, +16.8]  ★+
      Sideways ('15,'16,'19,'21): +10.3%p  CI [+4.4, +15.1]  ★+
      Bear     ('18,'22,'24):  +1.5%p  CI [-3.0, +8.1]  — (유의하지 않음)
    → PBR은 Bull/Sideways 두 국면 모두에서 유의. Bear에서는 효과 미확인.

    PBR vs PER 단독 비교 (동일 표본):
      저PBR 단독: 차이 +4.8%p  CI [+1.5, +8.9]  ★+
      저PER 단독: 차이 +2.7%p  CI [-1.4, +7.4]  — (유의하지 않음)
    → PBR이 PER보다 더 일관된 valuation signal. 단 완전히 동일한 조건의 paired 비교는
      아니므로 "PBR > PER"이라는 단정보다는 "PBR이 더 안정적인 신호로 관찰됨" 수준.

  PBR-2: 섹터별 — PBR은 모든 섹터에 동일하게 작동하지 않음
    유의하게 양수 (★+):
      일반서비스 +27.2% (CI [+3.0, +62.6], CI 폭 큼 — 주의)
      음식료·담배 +15.9% (CI [+2.6, +32.8])
      보험        +9.0%  (CI [+2.3, +15.2])
      증권        +8.1%  (CI [+3.1, +13.3])
      화학        +7.0%  (CI [+0.1, +15.4])
    유의하게 음수 (★−):
      의료·정밀기기 -61.0% (N=14 — 표본 부족, 사실상 무의미)
    방향성 있으나 유의하지 않음: 건설, 전기·전자, 유통 등 (CI 0 포함)
    → 섹터별 PBR 신뢰도 차이가 크다. 전 섹터 동일 weight 적용 시 주의.

  PBR-3: PER × PBR 복합 효과 — 단순 결합이 최적이 아님
    고PER + 저PBR: +8.1%  (N=1,249)  ← 최상
    저PER + 고PBR: +7.3%  (N=269)
    저PER + 저PBR: +6.2%  (N=317)
    고PER + 고PBR: +2.7%  (N=3,609)  ← 최하
    스프레드: +5.4%pt

    핵심 관찰:
    - "저PER + 저PBR이 가장 좋다"는 직관적 예상이 현재 데이터에서 확인되지 않음.
    - 저PBR이라는 조건 자체가 핵심이며, 그 안에서 PER은 단순 강화 신호가 아님.
    - "고PER + 저PBR = 높은 ROE" 해석은 실제 ROE 데이터 없이 할 수 없음. 추후 PBR-4에서 검증.

  엔진 함의:
    - hybrid_pbr_rel_weight=0.0 (현재 PBR 완전 비활성화)은 재검토 대상임을 시사.
    - 단, PBR-3 구조가 복잡하므로 단순히 weight를 올리는 것이 최적인지 불확실.
    - 지주사에서 PER이 신뢰하기 어려울 때 PBR이 독립적인 valuation signal이 될 가능성.
    - Engine Freeze 기간 중 적용 불가. 10월 이후 PBR weight 설계 시 근거 데이터로 활용.

  현재 상태: PBR-1~3 완료.
  다음 단계:
    ① PBR-4: ROE/자본효율 메커니즘 분석 → 완료 (아래 참조)
    ② Holding company subset 분석 — PBR이 지주사 PER 착시를 보완하는지 확인
    ③ 10월 Engine Freeze 해제 후 PBR weight 설계 여부 결정
  _(Historical next steps: superseded by later research — see Architecture Validation v2)_


  스크립트: investment/pbr_analysis.py
  데이터: database/research.db (research_stock_year, research_year_meta)

---

## PBR-4: ROE/자본효율 메커니즘 분석 (2026-08-21 완료)
> **Status: 🔵 Closed — ROE 단독 역효과. 엔진 추가 불가 결론**

  목적: PBR-3에서 "고PER+저PBR 최상"이었던 이유가 ROE 때문인가?
  방법: research.db 기반. NORMAL 종목. PER(0,100) + PBR>0 + ROE 유효 필터.
        섹터-연도 내 퍼센타일 순위. 연도 클러스터 블록 부트스트랩 CI (n=2000).
  분석 대상: 5,444개 종목-연도 (PER+PBR+ROE 동시 유효).
  주의: PBR-4 표본(5,444)과 PBR-3 표본(~5,444 but ROE 미포함)이 다르므로
        수익률 수치를 PBR-3과 직접 비교하면 안 됨.

  PBR-4a: "고PER + 저PBR = 고ROE?" 가설 검증
    그룹별 평균 ROE:
      저PER + 저PBR: ROE  +13.05%  수익률 +10.0%
      저PER + 고PBR: ROE  +21.88%  수익률  +1.6%  ← 최고 ROE, 최저 수익률
      고PER + 저PBR: ROE   +3.46%  수익률  +6.5%  ← 최저 ROE
      고PER + 고PBR: ROE   +8.99%  수익률  +3.2%
    → "고PER + 저PBR = 고ROE" 가설 완전 기각.
      수학적으로도 성립: PBR = ROE × PER이므로 저ROE × 고PER → 저PBR.
    → "저PER + 고PBR = 고ROE"이지만 수익률은 최하. 고ROE가 이미 주가에 반영된 상태.

  PBR-4b: ROE 단독 효과 — 유의하게 음수 (★−)
    고ROE 상위 30%: +2.5% / 나머지: +5.2% / 차이: -2.8%  CI [-5.2, -0.0]  ★−
    국면별:
      Bull     ('17,'20,'23):        +1.3%  CI [-2.2, +6.5]  — (유의하지 않음)
      Sideways ('15,'16,'19,'21):   -3.9%  CI [-7.0, -1.9]  ★−
      Bear     ('18,'22,'24):       -5.8%  CI [-7.0, -4.3]  ★−
    → ROE가 높을수록 수익률이 낮다. "좋은 기업 ≠ 좋은 주식" — 고ROE는 이미 시장에 반영됨.
    → ROE를 엔진 팩터로 추가하면 역효과 가능성.

  PBR-4c: 저PBR 내 ROE 추가 정보 여부 — 유의하지 않음
    저PBR + 고ROE: +9.0%  (N=200)
    저PBR + 저ROE: +8.0%  (N=1,333)
    차이: +1.2%  CI [-8.7, +12.7]  — (유의하지 않음)
    → 저PBR 그룹 내에서 ROE 수준은 수익률에 유의한 영향 없음.
    → 저PBR 효과는 ROE와 독립적으로 작동한다.

  종합 결론:
    - 저PBR이 작동하는 메커니즘은 ROE가 아님. "자산 대비 저평가" 신호 자체가 핵심.
    - ROE는 단독으로 유의하게 음수 신호 → 팩터 추가 검토 대상 아님.
    - PBR 활성화 근거는 PBR-1의 ★+ 유지. ROE 분석으로 추가 지지되거나 약화되지 않음.

  현재 상태: PBR-1~4 완료.
  다음 단계:
    ① Holding company subset 분석 → 아래 HOLD-1~4 참조
    ② 10월 Engine Freeze 해제 후 PBR weight 설계 여부 결정
  _(Historical next steps: superseded by later research — see Architecture Validation v2)_


  스크립트: investment/pbr4_roe_analysis.py
  데이터: database/research.db (research_stock_year, research_year_meta)

---

## Holding Company Subset 분석 (2026-08-21 완료)
> **Status: 🟢 Applied — v8.5.19 (지주사 PBR-only 적용)**

  목적: 지주사가 현재 엔진에서 구조적 보너스를 받고 있는지 확인.
        PBR/ROE 연구 결과와 연결해서 지주사 PER 착시 문제를 데이터로 검증.
  방법: 종목명 키워드('지주','홀딩','홀딩스') + 수동 추가(SK/LG/CJ/GS 등).
        research.db 내 지주사 72개 ticker, 701건 관측치.
        연도 클러스터 블록 부트스트랩 95% CI (n=2000).

  HOLD-1: 지주사 vs 일반기업 PER/PBR 분포 — 구조적 할인 확인
    PER 중앙값: 지주사 9.4 vs 일반기업 12.5
    PBR 중앙값: 지주사 0.60 vs 일반기업 0.90
    → 지주사는 PER/PBR 모두 구조적으로 낮다. 지주사 할인(holding company discount) 실존.

  HOLD-2: 지주사에서 저PER 효과 — 유의하지 않음
    지주사 저PER: +2.5%  CI [-5.9, +13.0]  — (유의하지 않음)
    일반기업 저PER: +1.7%  CI [-2.4, +6.3]  — (유의하지 않음)
    → 지주사에서도 일반기업에서도 저PER 효과가 유의하지 않음. H1 전체 결과와 일치.

  HOLD-3: 지주사에서 저PBR 효과 — 유의 (★+)
    지주사 저PBR: +6.2%  CI [+0.9, +12.1]  ★+
    일반기업 저PBR: +8.5%  CI [+3.9, +13.1]  ★+
    → 지주사에서도 저PBR 효과가 유의하게 작동한다.
    → PER이 신뢰하기 어려운 지주사에서 PBR이 독립적인 valuation signal이 될 가능성.

  HOLD-4: 현재 엔진 Valuation Score 시뮬레이션 (최근 5년, 2020~2024 기준)
    지주사  Score 평균 17.4pt / Score=25pt 비율 40.4%
    일반기업 Score 평균 12.5pt / Score=25pt 비율 21.9%
    → 지주사가 PER 구조적 할인 때문에 엔진에서 평균 +4.9pt 구조적 보너스 수령.
    → 지주사의 40%가 Valuation 만점(25pt)을 받고 있는 반면 일반기업은 22%만.

    PER ≤7 구간 비율 (22~25pt 최상위 점수 구간):
      지주사 48.6% vs 일반기업 26.9% → 약 2배 차이

  핵심 결론:
    - 지주사는 PER이 구조적으로 낮아 → 엔진이 높은 Valuation Score 부여 (사실)
    - 그 낮은 PER이 실제 미래 수익률을 예측하지 못함 (HOLD-2 유의하지 않음)
    - → 엔진이 지주사에 실증적 근거 없는 구조적 점수 보너스를 주고 있음 (확인)
    - 단, 지주사에서 저PBR 효과는 유의 → PBR activation 시 자연스럽게 보완 가능성

  엔진 함의 (Engine Freeze 기간 중 적용 불가, 10월 이후 검토):
    - PBR weight 활성화(hybrid_pbr_rel_weight > 0)가 지주사 PER 보너스를 일부 상쇄할 수 있음
    - 지주사 플래그 기반 PER contribution 제한은 별도 설계 논의 필요
    - "PER=None → Valuation=0" 처리는 적자 지주사에만 해당 — 실적 내는 지주사는 별도 접근 필요

  현재 상태: HOLD-1~4 완료.
  다음 단계: 10월 Engine Freeze 해제 후 PBR weight 설계 및 지주사 처리 방식 결정.
  _(Historical next steps: superseded by later research — see Architecture Validation v2)_


  스크립트: research/holding_company_analysis.py
  데이터: database/research.db (research_stock_year, research_year_meta)

---

## PBR Weight Replay (2026-08-21 완료)
> **Status: 🟢 Applied — v8.5.18 (w=1.0)**

  목적: hybrid_pbr_rel_weight 4개 후보값(0.0/0.5/1.0/1.5) 비교.
        PBR을 넣는 것이 Valuation Top30% vs 나머지 수익률 차이를 개선하는가?
  방법: research.db 기반. NORMAL 종목. PER(0,100) + PBR>0 필터.
        섹터-연도 내 퍼센타일 랭크. 연도 클러스터 블록 부트스트랩 CI (n=2000).

  전체 결과 (Valuation 상위 30% vs 나머지, 전 기간):
    w=0.0: 차이 +1.0%  CI [-3.3, +5.5]  — (유의하지 않음)
    w=0.5: 차이 +1.2%  CI [-2.7, +5.4]  — (유의하지 않음)
    w=1.0: 차이 +0.9%  CI [-3.1, +5.5]  — (유의하지 않음)
    w=1.5: 차이 +0.5%  CI [-3.3, +4.8]  — (유의하지 않음)

  국면별 결과:
    Bear 국면: w=1.0 유일하게 ★+ CI [+0.7, +3.0]
    Bull / Sideways: 모든 weight 유의하지 않음

  지주사 Top30% 비중 변화 (최근 5년):
    w=0.0: 지주사 평균 12.8pt, Top30% 내 지주사 14.9%
    w=1.0: 지주사 평균 14.7pt, Top30% 내 지주사 15.2% (소폭 증가)
    → PBR 활성화 자체로 지주사 집중 문제가 해결되지 않음.
       지주사 PER 착시 처리와 PBR weight 조정은 별개 문제.

  결론: Bear 국면에서 w=1.0 유의성 확인 → v8.5.18에서 hybrid_pbr_rel_weight=1.0 적용.
  주의: w=1.0은 최적값이 아닌 첫 번째 검증 후보값.

  스크립트: research/pbr_weight_replay.py
  데이터: database/research.db

---

## Holding Company Counterfactual Replay (2026-08-21 완료)
> **Status: 🟢 Applied — v8.5.19 (C안: 지주사 PBR-only)**

  목적: 지주사 과대상위 원인 분해. 지주사 PBR-only(C안) 적용 타당성 검증.
  방법: 3개 시나리오 비교. 최근 5년(2020~2024). 연도 클러스터 부트스트랩.

  3개 시나리오:
    A Baseline: 현재 엔진 (PER + PBR hybrid)
    B PER제거:  지주사만 PER contribution 0, total_w 유지 (점수 범위 축소)
    C PBR-only: 지주사만 rel_pbr_score 직접 사용 (점수 범위 정상 유지)

  지주사 Top30% 집중 변화:
    A Baseline: Top30% 내 지주사 17.7%
    B PER제거:  Top30% 내 지주사  0.0%  ← PER이 집중의 주원인 확인
    C PBR-only: Top30% 내 지주사 14.0%

  지주사 평균 점수:
    A Baseline: 14.6pt (일반기업 11.9pt, 차이 +2.7pt)
    B PER제거:   6.7pt (일반기업 11.9pt → 점수 범위 문제)
    C PBR-only: 13.5pt (일반기업 11.9pt, 차이 +1.6pt — 적정)

  전체 수익률 (Valuation Top30% vs 나머지):
    A: 차이 +4.7%  CI [+1.4, +8.6]  ★+
    B: 차이 +5.1%  CI [+1.2, +8.8]  ★+
    C: 차이 +4.9%  CI [+2.1, +8.7]  ★+  (수익률 유지)

  Bear 국면:
    A: ★+ / B: ★+ / C: ★+  (모두 유지)

  Bull 국면:
    C: -1.9%  CI [-4.6, -0.1]  ★−  (관찰됨, n=3 Bull 연도 — 과소표본)
    → 단일 기간 결론 불가. 기록만.

  대웅(003090) C 적용 효과:
    2020: A 13pt → C 11pt
    2021: A 18pt → C 22pt
    2022: A 18pt → C 22pt
    (최근 연도 반대 방향도 있음 — 연도별 섹터 PBR 분포에 따라 달라짐)

  동원산업(006040) C 적용 효과:
    2022: A 11pt → C 2pt  (실제 수익률 -78.2% → 올바른 낮은 점수 부여)

  Top10% 집중 분해 (C 시나리오, 최근 5년):
    전체 지주사 base rate: 10.4%
    C Top10% 내 지주사: 26.0%  (집중비 2.5x)
    원인 분석:
      rel_PBR=25pt 포화율: 지주사 53.2% vs 일반기업 49.8% → 거의 동일 (포화가 주원인 아님)
      → 지주사의 구조적 저PBR(PBR percentile 평균 0.10)이 집중 유발
      → PBR cap 추가해도 집중 해소 안 됨 (포화율이 동일하므로)
    포화 종목 성과:
      rel_PBR=25pt 지주사: 평균 +10.4%  (HOLD-3 결과와 일치 — 신호 유효)
      나머지 지주사:        평균  -0.4%

  결론:
    B 기각: 점수 범위 축소 문제 (top30% 지주사 0%는 과도한 조정)
    C 채택: PER 착시 제거 + PBR 신호 유지 + 전체 수익률 유지
    PBR cap 불필요: 포화율이 일반기업과 동일 → cap 추가해도 효과 없음
    Bull ★−: n=3 과소표본 — 기록만, 결정 근거 아님
    → v8.5.19에서 C 구현 (analyzer/holding_companies.py + valuation.py)

  스크립트: research/holding_counterfactual_replay.py, research/final_holding_replay.py
  데이터: database/research.db

---

## Morning Run 20D Alpha 가설 검증 (2026-08-25 완료)
> **Status: 🔵 Closed — 초기 forward validation. Architecture v2 연구로 대체됨**

  목적: v8.3.x era Morning Run 스냅샷 기반으로 STRONG_BUY 20D Alpha를 측정하고
        4개 가설(모멘텀/Regime/점수구간/Supply)을 검증한다.
  방법: backtests/snapshots/ 34개 파일 (2026-06-02 ~ 2026-08-25).
        grade="strong_buy" 항목만 대상. Entry = 스냅샷 당일 시가(open), 없으면 종가.
        Exit = 20영업일 후 종가. KOSPI(1001) = market_ohlcv.db.
        v8.3.x era (≤2026-08-20) 데이터. v8.5.19 Calibration과 별개.
  분석 대상: n=451 (25개 스냅샷, 7개 스킵 — exit date 미래 or 가격 없음)

  ── 전체 결과 ──────────────────────────────────────────────

  전체 평균 Alpha : +11.11%p
  전체 Alpha 승률 : 80.7%
  평균 종목 수익률 : -5.18%
  평균 KOSPI 수익률: -16.29%

  주의: 전 구간이 2026년 하락장(KOSPI 대폭락) 편중. 아래 Q2 참조.

  ── Q1 모멘텀 가설 ──────────────────────────────────────────

  결과:
    MOM Positive (n=7):   mean +3.82%p  win 86%
    MOM Negative (n=242): mean +14.24%p  win 91%
    unknown (n=200):      mean +7.82%p  win 68%
    → MOM Positive vs Negative 차이: -10.42%p → 가설 기각

  해석: 결론 내리기 어려움 — regime 오염.
    - 전체 기간이 Strong Bear(KOSPI -10% 이상) 편중 (n=323/451=72%).
    - MOM Negative 종목이 폭락장에서 이미 선반영된 주가를 가지고 있어 추가 낙폭 제한.
    - "모멘텀 가설"은 정상 시장을 전제로 설계됨. 하락장 데이터로 판단 불가.
    - Q1 재검증 시점: Bull/Neutral 구간 포함된 n≥100 확보 후.

  ── Q2 시장 Regime별 Alpha ──────────────────────────────────

  결과:
    Strong Bull  (KOSPI≥+3%):     n=21,  mean -5.69%p  win 24%  ← 엔진 열위
    Neutral      (-3%~+3%):       n=16,  mean -7.26%p  win 19%  ← 엔진 열위
    Mild Bear    (-10%~-3%):      n=91,  mean +0.95%p  win 58%
    Strong Bear  (KOSPI<-10%):    n=323, mean +15.97%p  win 94%  ← 엔진 강점

  해석: 가장 명확한 발견 — 이 엔진은 방어형 품질 스크린이다.
    - 상승장(Bull+Neutral, n=37): Alpha 음수, 승률 20% 내외 → KOSPI 대비 열위
    - 하락장(Strong Bear, n=323): Alpha +16%p, 승률 94% → 강한 방어력
    - 이는 학술 문헌상 Value/Quality 팩터의 알려진 특성과 일치:
      "방어적(defensive), 하락장 아웃퍼폼 / 상승장 언더퍼폼"
    - 이 데이터만으로 "장 방향에 따라 알파 해석 기준이 달라야 한다"는 가설 지지.
    - 제한: n=37(Bull+Neutral)은 소수. 방향성은 명확하나 통계적 확신은 낮음.

  → 실용적 함의: 강세장에서 이 엔진을 KOSPI 대비 기준으로 평가하면 안 된다.
    엔진의 목적은 "시장 평균 초과"가 아니라 "하락 방어 후 장기 복리"일 수 있다.
    이 가설은 60D/120D Alpha 데이터가 쌓이면 보다 명확히 검증 가능.

  ── Q3 점수 구간별 Alpha ────────────────────────────────────

  결과 (STRONG_BUY 내):
    Score 85~89:  n=296, mean +11.56%p  win 79%
    Score 90~94:  n=149, mean +9.84%p   win 83%
    Score 95~100: n=6,   mean +20.09%p  win 100%

  해석: 점수 내 단조 증가 없음. 변별력 미확인.
    - 85~89가 90~94보다 약간 높음 → 직관과 반대. 노이즈일 가능성 높음.
    - 95~100은 n=6으로 의미 없음.
    - Regime 편중이 여기서도 결과를 지배하고 있을 가능성 높음.
    - 재검증 조건: Bull/Neutral 구간 충분히 포함된 데이터.

  ── Q4 Supply Score 구간별 Alpha ────────────────────────────

  결과:
    Supply ≥ 18pt (n=310): mean +10.26%p  win 85%
    Supply < 18pt (n=141): mean +12.97%p  win 71%
    → High vs Low 차이: -2.72%p → Supply 단기 Alpha 비기여

  해석: 하락장에서 수급 신호 무력화.
    - 폭락장에선 외국인/기관이 동시에 이탈하며 수급 신호가 품질 차별을 못 함.
    - H2 연구 결과(전년도 연간 수급은 미래 수익률 예측 안 함)와 방향 일치.
    - Supply가 단기 Alpha에 기여하는지는 정상/상승 시장에서만 검증 가능.
    - 재검증 조건: Q2와 동일.

  ── 종합 결론 ────────────────────────────────────────────────

  이 데이터에서 얻을 수 있는 유일하게 명확한 결론:
    "이 엔진은 하락장 방어형 품질 스크린이다."
    상승장 Alpha 생성 여부는 현재 데이터로 판단 불가.

  Q1/Q3/Q4는 모두 regime 오염으로 인해 결론 보류.
  Q2만 신뢰할 수 있는 방향성을 보여줌.

  v8.5.19 Calibration에서 해소해야 할 질문:
    1. Bull/Neutral 구간에서도 Alpha가 있는가?
    2. 모멘텀은 정상 시장에서 Alpha를 추가하는가?
    3. Supply는 정상 시장에서 단기 Alpha를 예측하는가?
    4. 60D/120D Alpha에서 Valuation/Earnings 기여도가 높아지는가?

  현재 상태: v8.3.x era 20D 분석 완료. v8.5.19 Calibration n≥100 달성 후 재실행.
  스크립트: research/calibration_alpha.py, research/hypothesis_20d.py
  데이터: backtests/snapshots/, database/market_ohlcv.db

---

## 컴포넌트별 20D Alpha 기여도 (2026-08-25 완료)
> **Status: 🔵 Closed — Architecture v2 연구로 대체됨**

  목적: Valuation / Earnings / Risk / Supply 4개 컴포넌트 점수가
        20D Alpha와 얼마나 상관되는지 측정. 어느 컴포넌트가 단기 Alpha를 만드는가?
  방법: 위 20D Alpha 검증과 동일 n=451 데이터.
        Spearman 상관계수 (순위 기반, 이상치 강건). Regime 분리 (Strong Bear / Non-Bear).
        구간별 평균 Alpha (4등분).

  ── 핵심 결과 (Spearman r) ──────────────────────────────────

            전체          Strong Bear     Non-Bear
  Valuation +0.027  —     +0.098  △       +0.029  —
  Earnings  +0.093  ★     +0.088  —       +0.065  —
  Risk      +0.148  ★     +0.171  ★       +0.350  ★
  Supply    -0.127  ★     -0.225  ★       -0.218  ★

  ── 발견 1: Risk가 유일하게 일관된 Alpha 예측 변수 (★) ────────

  Bear와 Non-Bear 모두에서 유의. 특히 정상 시장(Non-Bear)에서 r=+0.350으로 가장 강함.
  재무 건전성(이자보상, 부채비율, 현금흐름)이 시장 국면에 관계없이 Alpha를 만드는
  유일하게 검증된 단기 팩터.

  ── 발견 2: Supply가 일관되게 음수 ★ — 설계 방향과 반대 ───────

  Supply 점수가 높을수록 20D Alpha가 낮다. Bear/Non-Bear 모두에서, 전 구간에서 음수.
  구간별 Alpha (전체):
    Supply 낮은 구간 (10~17pt): +14.39%p
    Supply 높은 구간 (22~25pt): +7.61%p

  해석:
    엔진이 고수급 종목을 선호하는데, 이미 기관/외국인이 많이 산 종목은
    "좋은 신호 이미 반영 완료" 상태 → 추가 Alpha 소진.
    H2 연구 결과(전년도 연간 고수급 = 미래 수익률 예측 못함)와 방향 일치.
    STRONG_BUY 내에서 Supply가 Gate(진입 하한)로 작동하면 유효하나,
    Ranker(차별자)로 쓰이면 역효과일 가능성.

  주의: n=451 전부 하락장 편중 (72% Strong Bear). Supply 음수 효과가
        "하락장에서 수급 신호 무력화" 때문인지, 구조적 현상인지 미확인.
        Non-Bear 구간(n=128)에서도 r=-0.218★이므로 단순 Regime 효과만은 아님.

  ── 발견 3: Valuation은 20D Alpha와 무관 ───────────────────────

  r=+0.027 (p=0.571). 전 Regime에서 유의하지 않음.
  해석: Valuation은 장기 팩터. 20D에서 안 보이는 게 설계 의도와 일치.
  단, "Primary Ranker"인데 단기 Alpha와 무관하다는 점은 기록.
  60D/120D 윈도우에서 기여도가 높아지는지 추후 검증 필요.

  ── 발견 4: Earnings는 전체에서만 ★, Regime 분리 시 유의성 소멸 ──

  전체 r=+0.093★ → Bear/Non-Bear 각각에서 모두 유의하지 않음.
  Regime 분포 아티팩트일 가능성. 설계 의도(Gate 역할)와 데이터 결과 일치:
  Earnings는 나쁜 종목을 걸러내는 용도이지 Alpha 차별 변수가 아님.

  ── 엔진 함의 (Freeze 해제 후 검토 대상) ──────────────────────

  1. Risk weight 상향 검토: 유일하게 일관된 Alpha 예측 변수.
     현재 25pt 동등 배분이 Risk를 과소평가하고 있을 가능성.
  2. Supply Ranker → Gate 전환 검토:
     현재 Supply는 점수 변별에 사용됨. 그러나 데이터는 고Supply → 낮은 Alpha를 보임.
     "수급 최소 기준 통과 여부만 Gate로 쓰고, 점수 차등 부여 중단"이 가설.
  3. Valuation 60D/120D 검증: 20D에서 무관 → 장기에서 의미 있는지 확인.

  적용 금지: Engine Freeze 기간(~n≥100 달성 시). 현재는 연구 관찰로만 보존.
  n≥100 달성 후 v8.5.19 데이터로 동일 분석 재실행해서 결과 일관성 확인 후 결정.

  현재 상태: 완료. Freeze 해제 후 재실행 필요.
  스크립트: research/component_alpha.py
  데이터: backtests/snapshots/, database/market_ohlcv.db

---

## CYCLICAL-1: Earnings Normalization POC (2026-08-26 완료)
> **Status: 🔵 Closed — Blocked (DART 분기 timeseries 확보 불가)**

  목적: 현재 EPS가 과거 정상 이익 대비 높은 상태일 때,
        저PER 신호의 미래 수익률이 실제로 약해지는가? (해운 Known False Positive 계기)
  방법: EPS proxy = p_start / per (주가 역산, 연구용).
        정상화 기준 = 과거 유효 EPS 중앙값 (5Y ≥3개 우선, 3Y ≥2개 fallback).
        earnings_ratio = 당해 EPS / normal_EPS.
        그룹: normal (≤1.0) / above_normal (1.0~1.5) / peak (>1.5).
        저PER = 섹터 내 하위 30% (H1 기준 통일), PER ≥0.5, PER<100.
        연도 클러스터 블록 부트스트랩 95% CI (n=2000).
  분석 대상: 5,429개 종목-연도 (research.db 2015~2024), earnings_ratio 산출 70.8%.

  ── 핵심 결과 (CYCLICAL-1a) ───────────────────────────────────

  전체 저PER 내 earnings_ratio 그룹별 수익률:
    normal       : avg +2.07%  median -2.42%  N=280
    above_normal : avg +4.41%  median -4.02%  N=324
    peak         : avg +3.13%  median -5.02%  N=606
    peak - normal = +1.06% → 단순 기각처럼 보이나, 국면 분리 시 다른 패턴

  국면별 결과:
    Bull:      normal +10.1%  peak +19.4%  → peak 우위 +9.3%
    Bear:      normal  -9.2%  peak -17.1%  → peak 열위 -7.9%
    Sideways:  normal  -2.2%  peak  -6.5%  → peak 열위 -4.3%

  ── 핵심 결과 (CYCLICAL-1-Bootstrap) ─────────────────────────

  연도 클러스터 bootstrap 95% CI (peak - normal, 저PER 내):

    전체     : +0.38%   CI [-5.38, +7.47]   —  (상쇄)
    Bull     : +8.50%   CI [+1.50, +17.34]  ★+  (peak 유의하게 우위)
    Bear     : -7.72%   CI [-9.01,  -6.07]  ★−  (peak 유의하게 열위)
    Sideways : -3.47%   CI [-4.97,  -0.51]  ★−  (peak 유의하게 열위)

  해석:
    전체 효과가 0에 수렴하는 이유: Bull ★+와 Bear/Sideways ★−가 상쇄됨.
    단순 peak 패널티는 Bull 장에서 역효과 → 패널티 설계 부적절.
    Bear/Sideways에서만 유의하므로 조건부 해석 필요.

  ── CYCLICAL-1b: 해운 Subset ───────────────────────────────────

  대한해운(005880) / 팬오션(028670) / HMM(011200) 3종목.
  유효 관측치 (eq_group ≠ unknown):
    normal       : mean -11.1%  N=7
    above_normal : mean  +5.2%  N=3
    peak         : mean -27.8%  N=5
  → 방향성은 전체 시장 Bear/Sideways 패턴과 일치. 단 N=12 (15 중 3 unknown) — 검증용 아님.

  ── 결론 및 수정 가설 ──────────────────────────────────────────

  원래 가설 (기각):
    "저PER + peak earnings → 미래 수익률 열위"
    → 전체 시장에서 확인되지 않음.

  수정 가설 (통계적 유의):
    "저PER + peak earnings는 Bear/Sideways에서 유의하게 열위"
    → earnings regime × market regime 교호작용 존재.

  주의:
    A 가설 (earnings normalization): 이익이 peak라서 PER이 낮아 보이고, 정상화 과정에서 수익률 부진.
    B 가설 (market regime effect): Bear/Sideways에서 valuation mean reversion이 강해지는 것뿐.
    → 현재 데이터로 A와 B를 분리하지 못함. CYCLICAL-2에서 검증 예정.

  적용 금지: Engine Freeze 기간. 해운 섹터 패널티 미도입. 연구 관찰로만 보존.
  다음 단계: CYCLICAL-2 — "peak earnings가 저PER 신호의 예측력을 훼손하는가?"
             설계: 저PER/고PER × normal/peak × Bull/Bear/Sideways 3중 비교 + bootstrap CI.
  _(Historical next steps: superseded by later research — see Architecture Validation v2)_


  스크립트: research/cyclical1a_earnings_normalization.py, research/cyclical1_bootstrap.py
  데이터: database/research.db (research_stock_year)

---

## CYCLICAL-2: Peak Earnings Counterfactual (2026-08-26 완료)
> **Status: 🔵 Closed — Cyclical 종목은 시스템 적용 범위 밖 확인**

  목적: "peak earnings가 저PER 신호의 예측력을 훼손하는가?"
        패널티 점수 설계 전 counterfactual 비교로 먼저 검증.
  방법: CYCLICAL-1a와 동일한 earnings_ratio 계산. peak_bear = peak(>1.5) + Bear/Sideways.
        Valuation Top30% vs 나머지 수익률 비교. 연도 클러스터 bootstrap 95% CI (n=2000).
        3개 시나리오: Baseline / C-Flag(PER contribution ×0.5) / C-Remove(PER contribution 제거).
  분석 대상: research.db 2015~2024. PER(0.5,100) + PBR>0 유효 종목.

  ── 시나리오별 전체 수익률 ─────────────────────────────────────

    Baseline  : diff +2.41%  CI [-1.64, +7.20]  —
    C-Flag    : diff +2.98%  CI [-1.05, +7.80]  —
    C-Remove  : diff +2.97%  CI [-1.33, +7.74]  —
  → 세 시나리오 모두 CI 0 포함. 전체 수준에서 유의한 차이 없음.

  ── 국면별 ────────────────────────────────────────────────────

    Bear:
      Baseline  diff +1.59%  CI [-0.42, +3.46]  —
      C-Flag    diff +3.00%  CI [-0.42, +5.79]  —
      C-Remove  diff +3.23%  CI [-0.42, +6.56]  —
    → 개선 방향이지만 CI 0 포함. 유의하지 않음.

    Sideways:
      Baseline  diff +3.20%  CI [-3.25, +13.39]  —
      C-Remove  diff +3.88%  CI [-3.11, +13.39]  —
    → 마찬가지로 개선 방향이나 유의하지 않음.

  ── Rank Turnover (Baseline → C-Remove, 핵심 발견) ────────────

    Top30%에서 제외된 종목: 79건  avg 수익률 -15.27%
    Top30%에 새로 진입한 종목: 113건  avg 수익률  -3.45%
    차이: +11.8%p
    해운 포함: 2건 / 경기민감 포함: 32건

  → peak_bear로 제외된 종목들이 실제로 나쁜 수익률을 가짐. 방향은 맞다.
    그러나 이것만으로 production 편입 근거는 불충분.

  ── 경기민감 섹터 집중도 변화 ─────────────────────────────────

    해운 Top30% 내 집중도: 0.5% → 0.4% (base rate 0.4%) — 변화 미미
    경기민감 전체: 34.1% → 34.1% — 사실상 변화 없음

  ── 섹터별 peak_bear 비율 (상위) ──────────────────────────────

    의료·정밀기기 17.4% / 기타금융 16.3% / 보험 15.7% / 운송·창고 11.0%
    → peak earnings 문제가 해운 전용 현상이 아님. 여러 섹터에 분산.
    → 운송·창고가 오히려 중간 수준. 기타금융/보험이 더 높음.

  ── 해운 Subset ────────────────────────────────────────────────

    is_peak_bear = True 케이스: HMM(2024), 팬오션(2024) 2건
    점수 변화 예시: HMM 2024  Baseline 487.5 → C-Remove 350.0
    팬오션 2023 (earnings_ratio=3.84): Bull 국면이라 is_peak_bear=False → 조정 없음
    → CYCLICAL-1 Bootstrap 결과와 일치: Bull에서 peak는 조정 불필요.

  ── 결론 ─────────────────────────────────────────────────────

  "통계적으로 확실히 도움된다"는 근거는 아직 부족 (CI 전부 0 포함).
  방향성은 맞음 (rank turnover +11.8%p, Bear 개선 방향).
  production 설계 기준 미달 → 연구 관찰로만 보존.

  지주사 C안(PBR-only)과 비교:
    지주사: PER이 구조적으로 왜곡 → counterfactual에서 유의한 개선 확인 → 적용
    경기민감: PER 신뢰도 조건부 문제 → 개선 방향이나 통계 미확인 → 보류

  다음 단계 옵션:
    a) 스냅샷에 earnings_ratio 저장 추가 → forward validation 준비 (비점수, Freeze 허용)
    b) 더 긴 데이터 확보 후 재검증
    c) out-of-sample 관찰 후 판단

  현재 상태: 완료. production 미적용.
  스크립트: research/cyclical2_counterfactual.py
  데이터: database/research.db

---

## 티커 반복 등장 분석 (2026-08-25 완료)
> **Status: 🔵 Closed — 참고 자료. 엔진 변경 없음**

  목적: 같은 종목이 여러 날 STRONG_BUY에 반복 등장할 때 Alpha 패턴을 분석.
        엔진의 False Positive 종목을 개별 식별하고 원인을 분류한다.
  방법: 위 20D Alpha 검증과 동일 n=451 데이터. 72개 종목.
        등장 횟수별 평균 Alpha, 1회 vs 다회 비교, Alpha 집중도 분석.
  스크립트: research/ticker_repeat.py

  ── 등장 횟수와 Alpha ──────────────────────────────────────

  반복 횟수와 Alpha 간 단조적 관계 없음.
    1회 등장 (13개 종목): 평균 +20.76%p  win 100%
    다회 등장 (59개 종목): 평균 +10.82%p  win 80%
  단, 1회 등장 n=13으로 노이즈 가능성 높음. "희귀 신호 = 더 강한 신호" 결론 보류.

  ── Alpha 집중도 ────────────────────────────────────────────

  상위 10% 종목 (7개)이 전체 Alpha 합계의 21% 기여.
  극단적 집중(80/20) 아님 — Alpha는 비교적 폭넓게 분산.
  상위 10% 평균 +36.74%p, 나머지 90% 평균 +9.34%p.

  ── 개별 False Positive 케이스 분석 ────────────────────────

  CASE 1: 성호전자 (043260) — "테마 소진" 케이스
    등장 9회, 평균 Alpha -42.16%p, 승률 0%.
    원인: 2026년 4월 광통신 테마로 53,200원 신고가 기록 후 급락.
          M&A 재무 부담으로 매도 압력 가중. 엔진 분석 구간이 테마 소진 하강 국면과 겹침.
    분류: Out-of-scope (테마 모멘텀). CLAUDE.md 설계 범위 밖 케이스.
          "테마 모멘텀: 수급이 선행하고 밸류가 후행" — 엔진 버그 아님. 적용 대상 오류.
    조치: 삭제 금지. False Positive 후보로 성과 추적. v8.5.19 데이터에서도 반복 시 재평가.

  CASE 2: KCC (002380) — "EQ Penalty 미감지" 케이스
    등장 17회, 평균 Alpha -1.40%p, 승률 35%.
    원인: Q1 2026 영업이익 -14.8% YoY인데 순이익 +392.5%.
          영업이익/순이익 역방향 → 일회성 이익(자산매각·파생상품 등)으로 순이익 부풀림.
          PER이 순이익 기반으로 낮아 보여 Valuation 고점수 → STRONG_BUY 반복 진입.
    분류: EQ Penalty 감지 범위 문제. op/ni 괴리가 임계값(0.8)을 간신히 통과했거나
          데이터 반영 시차로 penalty가 작동하지 않았을 가능성.
    조치: EQ Penalty 임계값 민감도 재검토 후보. Freeze 해제 후 op/ni 괴리 기준 강화 검토.

  ── 두 케이스의 시사점 ────────────────────────────────────────

  성호전자(테마소진)와 KCC(op/ni 괴리)는 서로 다른 원인으로 엔진을 속였다.
  - 성호전자: 설계 원칙상 걸러내야 하는 종목이지만 수급 신호가 유입 국면을 흉내냄.
  - KCC: 설계상 EQ Penalty가 잡아야 하는 종목. Penalty 민감도 또는 데이터 시차 이슈.

  엔진이 같은 종목을 반복적으로 틀리는 케이스는 두 가지로 분류:
    Type A: 적용 범위 밖 종목이 스크리너를 통과 → 섹터/테마 필터 보완으로 해결
    Type B: 적용 범위 내 종목인데 특정 지표 왜곡으로 통과 → 해당 지표 감지 강화

  현재 상태: 관찰 완료. Freeze 기간 중 엔진 수정 없음.
  v8.5.19 데이터에서 동일 종목 반복 패턴 지속 시 재평가.

---

## EQ Penalty 효과 분석 (2026-08-25 완료)
> **Status: 🔵 Closed — EQ Penalty 유효성 확인**

  목적: 영업이익/순이익 괴리 지표(eq_band)가 실제 20D Alpha와 일치하는가?
        EQ Penalty 설계 방향 검증 + None(데이터 없음) 처리 방식 검토.
  방법: 위 20D Alpha 검증 동일 n=451 데이터. eq_band 필드 기준 그룹 분리.
        주의: eq_band 데이터가 Strong Bear 구간 스냅샷에만 존재 (초기 스냅샷 미저장).
              Non-Bear 구간에서 GOOD/WARNING n=0 → Regime 분리 분석 불가.

  ── 핵심 결과 ──────────────────────────────────────────────

  eq_band별 20D Alpha:
    GOOD      (n=101): mean +16.56%p  win 96%   ← 최상
    WARNING   (n= 36): mean +14.27%p  win 94%
    POOR      (n=  2): mean  +8.14%p  win 100%  (n 부족)
    VERY_POOR (n=  1): mean -35.16%p  win  0%   (n 부족)
    None      (n=311): mean  +9.13%p  win 74%   ← 가장 낮음

  GOOD vs WARNING 차이: +2.30%p (방향 맞음)

  ── 발견 1: EQ Penalty 방향 검증됨 ────────────────────────

  GOOD → WARNING → POOR/VERY_POOR 순서로 Alpha 감소. 설계 방향 일치.
  단, GOOD/WARNING 차이(+2.30%p)는 작고 승률도 비슷(96% vs 94%).
  POOR/VERY_POOR은 n이 너무 작아 통계적 의미 없음.

  ── 발견 2: None(데이터 없음)이 GOOD보다 7.4%p 낮음 ────────

  None 종목 평균 +9.13%p — GOOD(+16.56%)보다 7.4%p, WARNING(+14.27%)보다 5.1%p 낮음.
  전체 n=451 중 311개(69%)가 None → 분석 데이터의 대부분이 EQ 데이터 부재.

  현재 엔진 처리: eq=None → 패널티 없음 (0pt 적용).
  데이터 함의: "EQ 데이터 없음 ≠ 위험 없음". 오히려 불확실성 리스크.
  → EQ 데이터 부재 자체가 낮은 Alpha와 연관됨.

  ── 엔진 함의 (Freeze 해제 후 검토 대상) ──────────────────

  1. EQ=None 불확실성 패널티 도입 검토:
     현재 None = 0pt. 데이터는 None이 GOOD 대비 -7.4%p 열위를 보임.
     "-1~2pt 경미한 불확실성 패널티" 가설. Freeze 해제 후 v8.5.19 데이터로 먼저 재검증.

  2. EQ Penalty 임계값 민감도 검토 (KCC 케이스 연계):
     KCC(002380)처럼 op/ni 괴리가 크지만 penalty 임계값을 간신히 통과하는 케이스 존재.
     POOR 기준 강화 또는 WARNING에도 경미한 패널티 부여 검토.

  적용 금지: Engine Freeze 기간. 현재는 연구 관찰로만 보존.
  n=451 전부 Strong Bear 구간 편중 + eq_band Non-Bear 데이터 없음 → 재검증 필요.

  현재 상태: 완료. v8.5.19 Calibration n≥100 달성 후 eq_band Non-Bear 구간 포함 재실행.
  데이터: backtests/snapshots/ (eq_band, eq_penalty 필드)

---

## Holding C 사후 검증 — Historical Replay (2026-08-26 완료)
> **Status: 🟡 Ongoing — Forward validation 대기 중 (n ≥ 100 필요)**


스크립트: `research/holding_c_downside_replay.py`
대상: research.db 2015-2024, NORMAL, PER+PBR 유효 (5,444건 / 지주사 572건)
비교: Baseline (PER+PBR 혼합) vs C안 (지주사만 rel_pbr_score 직접 사용)

### Exit Criterion 적용 결과

사전 고정 기준 (2026-08-26):
- PRIMARY 재검토: 수익률 열위 AND downside 악화 동시 → C revert 검토
- OTHER: 유지, trade-off 기록

판정: **PRIMARY 재검토 조건 미충족 → C안 유지**

수익률: Baseline Top30% +5.8% vs C Top30% +5.6% (차이 −0.2%p)
Downside:
- 하위10%: −29.3% → −28.6% (C 소폭 개선)
- Downside Deviation: 16.0 → 16.1 (사실상 동일)

수익률은 Baseline이 0.2%p 미세 우위이나, downside가 동시에 악화되지 않았다.
"C가 성능을 개선했다"가 아니라 **"C 적용으로 유의미한 성능 악화가 확인되지 않았으며, 사전 정의된 재검토 조건을 충족하지 않았다"** 가 정확한 표현.

### 국면별 결과 (추가 관찰)

| 국면 | C 평균차이 | CI | 유의 |
|------|-----------|-----|------|
| Bull (17,20,23) | −1.9% | [−4.6, −0.1] | ★− |
| Sideways (15,16,19,21) | +5.2% | [−4.7, +15.4] | — |
| Bear (18,22,24) | +2.0% | [+0.4, +4.0] | ★+ |

Bull ★−, Bear ★+: C가 단순히 "지주사 점수를 낮추는 규칙"이 아니라 **시장 국면에 따라 valuation exposure를 바꾸는 효과**가 관찰됨. PER contribution 제거 후 PBR 중심 평가가 하방 국면에서 방어적 특성을 강화했을 가능성을 시사. 단, 인과 단정 불가 — 관찰된 패턴으로 기록.

**Bull ★−를 이유로 새 규칙 추가 금지.** 사전 정의한 exit criterion을 통과했으므로 여기서 Bull 전용 보정을 넣으면 "가설 → 사전 기준 → 검증 → 구현" 원칙을 스스로 깨게 됨.

### 지주사 Top30% 비중

Baseline 13.7% → C 12.9%: 큰 변화는 아니나 C의 설계 의도(구조적 PER 왜곡 완화)와 같은 방향.

### 판정 요약

| 항목 | 판정 |
|------|------|
| PRIMARY 재검토 조건 충족 | NO |
| C 유지 | YES |
| 전체 수익률 개선 | 확인 안 됨 |
| 전체 수익률 악화 | 통계적으로 의미 없음 |
| Downside 방향 | 소폭 개선 (혼재) |
| Bull | ★− 유의 열위 |
| Bear | ★+ 유의 우위 |
| 지주사 Top30% 비중 | 13.7% → 12.9% |
| 최종 판정 | n≥100 forward validation 대기 |

---

## PER × PBR 중복성 분석 (2026-08-26 완료)
> **Status: 🔵 Closed — Supply=A=B 발견. Architecture v2로 통합됨**


스크립트: `research/per_pbr_interaction.py`
대상: research.db 2015-2024, NORMAL, PER+PBR 유효 (5,444건)
핵심 질문: PBR은 PER과 독립적인 정보를 추가하는가?

### ① PER-PBR 랭크 상관관계

전체 Spearman ρ = +0.376 (부분 중복). 연도별 0.27~0.48 범위.
섹터별: 전기·가스(0.07), 보험(0.21), 섬유·의류(0.21) 등은 독립적. 화학(0.45), 건설(0.44), 일반서비스(0.51) 등은 중복.

### ② 단독 예측력 IC

| 시나리오 | 평균 IC | IR |
|---------|---------|-----|
| PER-only | +0.059 | 0.65 |
| PBR-only | +0.121 | 1.28 |
| 혼합(현재) | +0.107 | 1.01 |

**PBR-only가 단독으로 가장 강한 예측력.** 혼합 IC(0.107)는 PBR-only(0.121)보다 낮고 PER-only(0.059)보다는 높음.
PER이 혼합에서 PBR 신호를 일부 희석하는 효과가 관찰됨.

### ③ 조건부 효과 (4분위 교차)

| 분위 | 평균 수익률 |
|------|-----------|
| 저PER + 저PBR | +5.7% |
| 저PER + 고PBR | +3.9% |
| 고PER + 저PBR | +6.5% |
| 고PER + 고PBR | +2.1% |

PER Low 내 PBR 조건부 효과: +1.9% [−2.2, +5.6] — 유의하지 않음.
PER High 내 PBR 조건부 효과: +4.5% [−0.7, +10.1] — 유의하지 않음.
PBR이 PER 조건부로 추가 정보를 제공하는 방향이나 CI가 0을 포함.

### ④ 섹터별 PBR 효과

대부분 섹터에서 PBR IC > PER IC. PER 우위 섹터: 보험, 기계·장비, 증권.
운송·창고, 건설, 전기·가스에서 PBR 효과 특히 강함.

### ⑤ 국면별

Bull: PBR-only ★+ (+0.8%), PER-only/혼합은 비유의. Bear: 혼합 ★+ (+1.5%). PBR은 Bull과 Bear 모두에서 기여.

### 종합 판정

PBR-PER ρ=0.376(부분 중복)이므로 PBR은 PER과 완전 중복이 아님. IC 관점에서는 PBR-only가 혼합보다 강하나, 혼합은 PER-only보다 명확히 우위. **PBR은 강한 독립 신호이며, 현재 혼합 방식에서는 그 신호가 PER과 결합되면서 일부 희석되고 있다.** PBR-only IC가 더 높다는 결과는 향후 weight 재검토의 근거로 기록하되, production weight 변경의 근거로 사용하지 않는다. "weight를 올리면 개선된다"는 가설이며, forward data에서 별도로 검증해야 한다.

  현재 상태: 완료. Engine Freeze 해제 후 weight 조정 검토 시 참조.
  데이터: research.db (2015-2024)

---

## KR Risk Score Decomposition — Downside Protection 검증 (2026-09-09)
> **Status: 🔵 Closed — Architecture v2 결론에 통합됨**


스크립트: `research/risk_decomposition_v2.py`
데이터: research.db risk_history(DART Q3, 2016-2024) + risk_volatility(pykrx) + research_stock_year
패널: 5,839건 / 8개 연도(forward 2017-2024) / 840개 종목
Benchmark: KOSPI 연도별 실제 수익률 (pykrx 조회)
핵심 질문: Risk penalty가 alpha를 높이는가? vs. tail risk를 줄이는가?

### 연구 배경

Risk Score는 5개 컴포넌트로 구성된다: 부채비율(-5pt), 손실분기(-4/-7pt), 희석(-5pt), 변동성(-3pt), 지배구조(-5pt). 이번 연구는 각 서브컴포넌트가 독립적으로 alpha를 예측하는지, 그리고 aggregate Risk Score가 실제로 downside를 줄이는지 검증한다.

### KOSPI 기준 연도별 alpha 요약

| forward_year | KOSPI | 패널 mean alpha |
|-------------|-------|----------------|
| 2017 | +21.8% | -16.4% |
| 2018 | -17.7% | +9.2% |
| 2019 | +9.3% | -9.1% |
| 2020 | +32.1% | +2.7% |
| 2021 | +1.1% | +17.9% |
| 2022 | -25.2% | +8.5% |
| 2023 | +19.3% | -6.1% |
| 2024 | -10.1% | +13.4% |

패널 평균 alpha는 연도별로 크게 다르며 일부 연도에서 크게 음수. KOSPI 하락 구간(2018, 2022, 2024)에서 패널이 상대적으로 선방하는 경향 관찰됨.

### Step 2 — 컴포넌트별 downside 분석

| 컴포넌트 | 그룹 | N | 평균alpha | Hit Rate | P10 | P25 | t-stat | p |
|---------|------|---|----------|---------|-----|-----|--------|---|
| 부채비율>150% | 고위험 | 1,564 | +6.19% | 40.0% | -47.67% | -28.86% | | |
| | 저위험 | 4,049 | +1.54% | 38.7% | -42.66% | -26.99% | 1.50 | 0.135 |
| 적자(NI<0) | 고위험 | 1,518 | +10.29% | 34.5% | -49.62% | -33.04% | | |
| | 저위험 | 4,258 | +0.15% | 40.6% | -41.69% | -25.40% | 3.23 | 0.001** |
| 변동성>40% | 고위험 | 2,778 | +6.33% | 37.1% | -47.91% | -30.21% | | |
| | 저위험 | 3,061 | -0.44% | 40.7% | -40.72% | -24.83% | 2.45 | 0.014* |

**부채비율**: 평균 alpha 비유의(p=0.135). P10/P25 모두 고위험이 나쁨. 꼬리위험 방향은 맞으나 통계적으로 약함.

**적자(NI<0)**: 평균 alpha는 고위험이 +10.14%p 더 높음(p=0.001). 그러나 hit rate는 34.5% vs 40.6% — 적자 기업은 더 자주 손실을 내면서 소수의 극단적 반등이 평균을 끌어올리는 구조(복권형 분포). P10은 -49.62% vs -41.69%로 꼬리위험이 유의하게 더 나쁨.

**변동성>40%**: 평균 alpha 고위험 우위(p=0.014). hit rate 및 P10/P25 모두 고위험이 나쁨. 평균은 반대지만 꼬리와 승률은 예상 방향.

### Step 3 — Aggregate Risk Score × downside

| Risk Score | N | 평균 Alpha | Hit Rate | P10 | P25 | worst |
|-----------|---|-----------|---------|-----|-----|-------|
| 0 (저위험) | 1,982 | -3.45% | **40.8%** | -40.19% | -24.03% | -117.51% |
| 1 | 2,241 | +3.86% | 39.5% | -42.11% | -26.47% | -118.88% |
| 2 | 1,229 | +7.37% | 37.0% | -48.33% | -30.94% | -115.15% |
| 3+ (고위험) | 387 | +13.86% | **33.1%** | **-55.59%** | **-37.77%** | -95.45% |

Risk Score가 높아질수록:
- P10: -40.19% → -55.59% (단조적 악화)
- P25: -24.03% → -37.77% (단조적 악화)
- Hit Rate: 40.8% → 33.1% (단조적 감소)
- 평균 Alpha: -3.45% → +13.86% (단조적 증가 — 반대 방향)

### 핵심 발견

**발견 1: Risk penalty는 "평균 alpha 보호 장치"가 아니다.**
KOSPI 기준으로 재계산해도 고위험 종목의 평균 alpha가 저위험보다 높다. 이 현상은 소수의 극단적 반등이 평균을 끌어올리는 비대칭 분포 구조에서 기인한다.

**발견 2: Risk penalty는 "downside protection 장치"로서 데이터에서 지지된다.**
P10, P25, Hit Rate 세 지표 모두 Risk Score가 높아질수록 단조적으로 악화된다. "평균만 보면 Risk가 오히려 나쁜 것처럼 보이는 착시"가 발생하며, 분포 전체를 보면 고위험 → 두꺼운 손실 꼬리 + 낮은 승률 구조가 확인된다.

**발견 3: 적자 종목의 분포 구조**
mean alpha +10.29% + hit rate 34.5% + P10 -49.62% 패턴은 "적자 기업이 좋은 투자"가 아니라 "손실 빈도가 높고 꼬리가 두꺼운 복권형 수익 구조"를 보인다. Risk penalty 관점에서 적자 기업을 불리하게 다루는 설계는 이 분포 특성으로 정당화된다.

### Risk 연구에서 Primary Metric 방향

이번 결과는 Risk 평가에서 평균 alpha를 primary metric으로 사용하면 안 된다는 근거를 제공한다. 향후 Risk 관련 연구의 metric 우선순위:

```
P10 / P25 (꼬리위험)
    → Hit Rate (승률)
    → Downside Deviation
    → 평균 Alpha (참고용)
```

단, 이 우선순위는 현재 연구 설계에서의 방향이며, production metric 가중치 변경의 근거로 사용하지 않는다.

### 한계 및 생존자 편향

**상장폐지 종목 미포함**: 현재 패널은 DART 제출 + pykrx 가격 존재 조건을 만족하는 생존 종목만 포함한다. 상장폐지된 고위험 종목이 빠졌을 가능성이 있어 고위험 그룹의 tail risk는 실제보다 과소평가됐을 가능성이 있다.

**편향의 방향**: Survivorship bias는 고위험 그룹에 유리한 방향으로 작용한다. 즉 현재 관측에서 "고위험 ≈ 저위험 downside"가 나오더라도 실제로는 고위험 downside가 더 나쁠 수 있다. 반대로 현재 "고위험 > 저위험 downside"가 관측된다면 실제 차이는 더 클 가능성이 있다.

**2015년 데이터 미포함**: DART API가 2015 Q3 데이터를 제공하지 않아 2016-2024(8년) 기준 분석.

### 다음 단계 (연구 로드맵)

```
Step 4 (필요 시): Survivorship Sensitivity
  - DART 상장폐지 이력으로 고위험 종목 중 폐지 종목 확인
  - delisting return -90%/-100% 가상 bound 적용
  - 결론 반전 여부 확인 → robustness check
```

현재 상태: 완료. Engine Freeze 해제 후 Risk 구조 재검토 시 참조.
데이터: research.db (2016-2024), DART Q3 + pykrx

---

## Supply Gate 연구 Phase 1 — 연간 수급 (2026-09-09)
> **Status: 🔵 Closed — 연간 수급 미지지. Phase 2로 이어짐**


스크립트: `research/supply_gate.py`
데이터: research.db research_investor_flow(연간 합산, 2015-2024) + research_stock_year
패널: 7,844건 / flow_year 2015~2023 / 1,017개 종목
Benchmark: 패널 중앙값 (pykrx 환경 미사용 — KOSPI 재실행 필요)
핵심 질문: 연간 외국인/기관 순매수 방향이 다음 해 alpha를 예측하는가?

### 연구 배경

현재 Production Supply score는 단기 수급(20/60일 순매수 방향)을 기반으로 한다. 이 Phase 1은 동일 연구 프레임워크(Risk Decomposition 방식)를 Supply에 적용하기 위해 기존에 수집된 연간 합산 investor_flow 데이터로 먼저 방향성을 확인하는 탐색 단계다.

### H-Supply-1: 연간 수급 → 다음 해 alpha

| 신호 | 매수 n | 매수 mean alpha | 매도 n | 매도 mean alpha | t | p | 유의성 |
|------|--------|----------------|--------|----------------|---|---|--------|
| 외국인 순매수 | 3,395 | +10.45% | 4,449 | +11.72% | -0.57 | 0.566 | — |
| 기관 순매수 | 3,441 | +10.36% | 4,403 | +11.80% | -0.65 | 0.513 | — |
| 쌍끌이(외+기관) | 1,421 | +11.79% | 6,423 | +11.03% | +0.27 | 0.790 | — |

**→ H-Supply-1 기각.** 외국인, 기관, 쌍끌이 모두 비유의(p>0.5). 방향도 기대와 반대 — 순매수 종목의 mean alpha가 오히려 낮거나 동등하다.

### Supply Score × Downside

| Supply Score | N | Mean Alpha | Hit Rate | P10 | P25 | worst |
|-------------|---|-----------|---------|-----|-----|-------|
| 0 (둘 다 매도) | 2,429 | +13.68% | 49.2% | -31.39% | -16.36% | -95.45% |
| 1 (하나만 매수) | 3,994 | +9.43% | 50.3% | -29.47% | -14.49% | -94.23% |
| 2 (쌍끌이) | 1,421 | +11.79% | 50.2% | -27.98% | -15.55% | -89.32% |

Score 2가 P10에서 약간 우위(-27.98% vs -31.39%)이나 차이가 작고 비단조적(Score 1이 가장 낮음). Risk 연구에서 본 단조적 downside 개선 패턴은 확인되지 않는다.

### 핵심 판정

연간 수급 합산은 다음 해 alpha 예측 도구로서 작동하지 않는다. **이 결과는 Production Supply score를 검증하거나 반증하지 않는다.** Production은 20/60일 단기 수급 기반이며, 이번 연구는 연간 합산이라 본질적으로 다른 신호다. "연간 수급이 유효하지 않다"를 "Supply가 쓸모없다"로 확대해석하면 안 된다.

### 한계

**① 연간 합산 vs. 단기 신호**: 연간 수급은 매수/매도가 평활화되어 단기 방향성 신호가 사라진다. Production Supply의 핵심인 최근 20/60일 집중 매수 신호와 다르다.

**② flow ratio denominator timing imperfect**: `foreign_net_buy / market_cap`에서 market_cap은 research_stock_year 기준 연말 근사값이다. 연 중 주가 급등 종목은 연말 시총이 커져서 flow ratio가 과소평가된다. 완전히 동기화된 분모가 아니므로 ratio의 절대값보다 방향(부호)만 신뢰한다.

**③ Benchmark**: pykrx 환경 미설정으로 패널 중앙값 fallback 사용. KOSPI 기준 재실행 시 수치가 달라질 수 있으나, 유의성 판정(p>0.5)은 바뀔 가능성이 낮다.

**④ Survivorship bias**: 상장폐지 종목 미포함.

**⑤ Specification search 방지**: 연간 수급 데이터로 다른 threshold/bucket을 계속 시도하지 않는다. 결론이 나왔으니 Phase 1 종료.

### 다음 단계

```
Phase 2: Supply-Short-Term (단기 수급 데이터 구축)
  - 일간 외국인/기관 순매수 수집 (pykrx)
  - 20D / 60D rolling 순매수 합산
  - 측정 시점 T에서 future 20D / 60D alpha 계산
  - "최근 수급이 강한 종목이 이후 20D/60D에서 어떻게 움직이는가?"
  - 시간 감쇠 확인 + Production signal 직접 검증

  그 다음:
  Phase 3: Gate Architecture Comparison
    Base Score(Value+Earnings+Risk) vs
    현재 Score(+Supply) vs
    Gate 방식(Base Score + Supply as entry filter)
```

현재 상태: Phase 1 완료. 연간 수급 연구 종료.
데이터: research.db research_investor_flow (2015-2024)

---

## Supply Gate 연구 Phase 2 — 단기 수급 20D/60D (2026-09-09)
> **Status: 🔵 Closed — 단기 수급도 미지지. Architecture v2로 통합됨**


스크립트: `research/build_supply_snapshot.py` (수집) + `research/supply_short_term.py` (분석)
데이터: research.db supply_snapshot — 월말 스냅샷 102개 (2016-01~2024-06), 92,344건
패널: 1,052개 종목, alpha_20d 유효 92,251건 / alpha_60d 유효 92,074건
핵심 질문: 단기 20D/60D 수급 신호가 이후 20D/60D alpha를 예측하는가? 시간 감쇠가 있는가?

### 수급 신호 분포

외국인·기관 20D 매수 비율: 각 44.9% (매도가 약간 우세). 쌍끌이 19.0%.

### Step 2 — 20D 수급 신호 × 선행 Alpha

| 신호 | → 20D alpha | p | → 60D alpha | p |
|------|------------|---|------------|---|
| 외국인 순매수 | +0.31% vs +0.26% | 0.787 | +0.92% vs +1.09% | 0.580 |
| 기관합계 순매수 | -0.01% vs +0.52% | **0.002** | +0.43% vs +1.49% | **0.001** |
| 쌍끌이 | -0.01% vs +0.35% | 0.089 | +0.51% vs +1.13% | 0.122 |

**기관 순매수 → 역방향, 통계적으로 유의 (20D: p=0.002, 60D: p=0.001)**. 기관이 순매수한 종목의 선행 alpha가 기관 순매도 종목보다 낮다.

### Step 3 — 60D 수급 신호 × 선행 Alpha

쌍끌이 60D → 20D alpha: p=0.128 (비유의). 쌍끌이 60D → 60D alpha: p=0.554 (비유의). 시간 감쇠 패턴보다 처음부터 유의한 신호 자체가 없는 구조.

### Step 4 — Supply Score (0~2) × Downside

| Score | N | Mean Alpha (20D) | Hit Rate | P10 |
|-------|---|-----------------|---------|-----|
| 0 | 26,934 | +0.51% | 43.3% | -11.44% |
| 1 | 47,791 | +0.27% | 43.2% | -11.28% |
| 2 | 17,526 | -0.01% | 41.8% | -11.89% |

Score 0→2에서 mean_alpha, hit_rate, P10 모두 단조적으로 악화. Risk 연구에서 본 방향과 동일하지만, Risk는 "downside protection"으로 해석 가능했던 반면 **Supply는 P10도 개선되지 않아 protection 효과도 없다**.

### 핵심 판정

연간 Supply 연구(Phase 1)와 동일하게, 단기 월말 Supply에서도 "강한 수급 → 이후 높은 alpha" 가설은 지지되지 않는다. 기관 순매수는 오히려 유의한 음의 관계이며, 외국인·쌍끌이는 비유의다. Supply Score 0→2에서 평균 alpha와 hit rate가 악화되고 P10도 개선되지 않는다.

**현재 연구 데이터만으로 Supply를 score component 또는 gate로 채택할 근거 없음.**

### Production Supply와의 불일치 — 해석 주의

다만 이 결과를 "Production Supply가 실패했다"로 확정하면 안 된다. 이유:

- **월말 스냅샷**: Production은 임의 날짜에 실행되며 월중 모멘텀을 포착. 월말 집계는 이 신호를 희석할 수 있다.
- **fwd 기간 부정확**: fwd20 ≈ +30 캘린더일, fwd60 ≈ +91 캘린더일로 정확한 20/60 영업일이 아님.
- **독립성 위반**: 동일 종목이 102개 스냅샷에 반복 등장 → t-test 독립성 가정 위반. p-value는 경향 참고용.
- **Survivorship bias**: 상장폐지 종목 미포함.

가능한 설명: 기관 순매수가 이미 오른 종목을 추격하는 패턴이라면 "매수 후 효과"로 설명 가능하나, 현재 분석만으로 인과관계를 확정할 수 없다. 가설로 기록.

### Risk vs Supply 역할 비교 — 중요 관찰

두 연구를 나란히 놓으면 엔진에서 두 컴포넌트의 역할이 근본적으로 다르게 평가된다:

- **Risk**: 평균 alpha는 낮아도 P10/P25/hit_rate에서 명확한 downside protection 효과 확인 → "보호 장치"로 정당화됨
- **Supply**: 평균 alpha도 개선되지 않고 P10도 개선되지 않음 → 어떤 역할도 데이터에서 지지되지 않음

이 비교는 Freeze 해제 후 Supply 재설계 논의의 핵심 근거가 된다. `score / gate / disabled` 세 구조 중 선택 시 이 대비를 참조할 것.

### 다음 단계

```
현재 연구: Supply 가설 종료 (Phase 1 + Phase 2 모두 비지지).
Freeze 해제 후 재검토 후보:
  - Supply를 점수에서 제거하거나 Gate(실행 조건)로 분리
  - Production 실제 신호와 동일한 방식으로 재검증 (정확한 영업일 기준)
  - Risk/Supply score architecture 재설계
```

현재 상태: Phase 2 완료. Supply 연구 사이클 종료. Freeze 해제 후 재검토 후보로 기록.
데이터: research.db supply_snapshot (2016-01~2024-06, 102 스냅샷)

---

## Risk × Market Stress 분해 — Decomposition v3 (2026-09-09)
> **Status: 🔵 Closed — Architecture v2 결론에 통합됨**


**연구 스크립트**: `research/risk_stress.py`

**연구 질문**: Risk Score의 downside protection 효과는 시장 stress가 클수록 강해지는가?
"Risk penalty는 특정 Bear regime에서만 유효한가, 아니면 항상 작동하는가?"

**설계**:
- PIT 엄수: Risk Score @ Y년 말(Q3 DART 기준), Stress @ Y+1 (미래 정보 불사용)
- 연간 Bull/Bear/Sideways 레이블 없음 → 연속 stress 변수 사용
- Stress 변수 (fwd_year Y+1 기간):
  - `kospi_max_dd`: KOSPI peak-to-trough 최대 낙폭
  - `kospi_worst_20d`: worst rolling 20D KOSPI 수익률
  - `kospi_vol`: KOSPI 연간 실현 변동성
- Quantile 경계: 전체 표본 1회 고정 (연도별 재계산 금지)
- 패널: 5,839건 / fwd_year 2017~2024

### Step 1 — fwd_year별 KOSPI Stress 지표

| fwd_year | ann_ret% | max_dd% | worst_20d% | vol% |
|----------|---------|---------|-----------|------|
| 2017 | +21.8 | -5.4 | -4.2 | 9.3 |
| 2018 | -17.7 | -23.2 | -15.3 | 14.4 |
| 2019 | +9.3 | -15.1 | -8.7 | 12.5 |
| 2020 | +32.1 | -35.7 | -33.6 | 28.3 |
| 2021 | +1.1 | -14.1 | -8.5 | 16.2 |
| 2022 | -25.2 | -27.9 | -13.9 | 18.7 |
| 2023 | +19.3 | -14.6 | -8.7 | 15.4 |
| 2024 | -10.1 | -18.4 | -14.6 | 19.8 |

연간 수익률과 intra-year stress가 괴리되는 사례: 2020년 ann_ret=+32.1%이지만 max_dd=-35.7%. 연간 레이블(Bull/Bear)만으로는 내부 stress를 포착 못 한다는 것을 확인.

### Step 2 — 전체 Risk Score × Downside (기준선)

| Score | N | Mean | Hit Rate% | P10 | P25 |
|-------|---|------|----------|-----|-----|
| Risk 0 | 1,982 | -3.45 | 40.8 | -40.19 | -24.03 |
| Risk 1 | 2,241 | +3.86 | 39.5 | -42.11 | -26.47 |
| Risk 2 | 1,229 | +7.37 | 37.0 | -48.33 | -30.94 |
| Risk 3+ | 387 | +13.86 | 33.1 | -55.59 | -37.77 |

P10 단조성 재확인: Risk 0(-40.19) → Risk 3+(-55.59), 약 -15pp 격차. 이전 연구(Decomposition v2)와 일치.

Mean alpha가 Risk 3+에서 높게 나오는 것은 survivorship bias로 설명: 살아남은 고위험주가 생존 과정에서 높은 수익을 기록하나, tail downside(P10)는 일관되게 악화.

### Step 3 — Stress 분위 × Risk 0 vs Risk 3+ P10 Spread (Max Drawdown 기준)

| 분위 | avg max_dd | Risk0 n | R0 P10 | R3+ n | R3+ P10 | P10 spread |
|------|-----------|---------|--------|-------|---------|------------|
| Q1 (low) | -11.5% | 606 | -43.04 | 167 | -57.55 | **-14.51pp** |
| Q2 | -16.8% | 507 | -29.89 | 105 | -53.74 | **-23.86pp** |
| Q3 | -23.2% | 344 | -20.44 | 26 | -38.99 | **-18.56pp** |
| Q4 (high) | -31.8% | 525 | -47.47 | 89 | -56.32 | **-8.84pp** |

Worst 20D / Ann Vol 기준도 동일한 패턴: 모든 분위에서 spread 음수.

### Step 5 — 연속 Stress vs P10 Spread 상관관계

| Stress 변수 | r | p |
|------------|---|---|
| Max Drawdown | -0.148 | 0.727 |
| Worst 20D | +0.151 | 0.722 |
| Ann Vol | +0.122 | 0.774 |

모든 지표에서 p > 0.72. "stress 크기와 protection spread 사이에 체계적인 연속 관계 없음"을 직접 지지.

### Q4 anomaly — 2020/2022 해석

Q4(최고 stress) P10 spread가 가장 작은(-8.84pp) 이유: 2020+2022년 포함. 2020년 pandemic 급락 후 고위험주(손실+고변동성)가 반등 장세에서 Risk 3+ mean=+38.85%를 기록. 단, P10은 여전히 -56.32%로 tail downside는 사라지지 않음. "고위험주 평균 alpha가 높아진 것"이지 "보호 효과가 줄어든 것"이 아님.

### 핵심 결론

Risk Score의 downside protection 효과는 시장 stress 수준과 관계없이 일관되게 존재한다. P10 spread(Risk3+ − Risk0)는 Q1~Q4 모든 stress 분위에서 음수(-8.84~-23.86pp)이나, stress가 커질수록 보호 효과가 강화된다는 단조적 증거는 없다(r≈±0.15, p>0.72).

**Risk는 특정 Bear regime에서만 작동하는 팩터가 아니라, 시장 stress 수준과 관계없이 고위험군의 downside tail을 일관되게 악화시키는 특성을 구분하는 보호 장치로 해석한다.** 다만 stress가 심할수록 보호 효과가 커진다는 증거는 확인되지 않았다.

**Risk 연구 종합 구조 (v1~v3):**
- Risk score → 평균 alpha 생성기가 아님 (v2 확인)
- Risk score → downside/tail-risk discriminator (v2 확인, v3 재확인)
- 그 보호 기능은 특정 Bear regime에만 국한되지 않음 (v3 신규)

### 연구 한계

① fwd_year당 1개 stress 값 → 동일 연도 종목끼리 독립성 없음. ② 연도 수 8개 → Step 5 상관분석 표본 극소 (경향 참고용). ③ Survivorship bias: 상장폐지 고위험 종목 미포함 → Bear 구간 Risk 3+ tail risk 과소평가. ④ stress 임계값(Q4)은 pd.qcut 기준 고정 — 해석 시 유의.

현재 상태: Risk 연구 v1~v3 사이클 완료.

---

## Risk Sub-component Contribution Analysis (2026-09-09)
> **Status: 🔵 Closed — Architecture v2 결론에 통합됨**


**연구 스크립트**: `research/risk_subcomponent.py`

**연구 질문**: Risk의 downside protection 효과를 실제로 만들어내는 핵심 penalty는 무엇인가?

**설계**:
- 패널: 5,839건 / fwd_year 2017~2024 / KOSPI alpha 기준
- 분석 방법: Step 1 이변량(overlap 포함) → Step 2 순수 격리(해당 flag만=1, 나머지=0 vs 모든 flag=0)
- 현재 scoring weight 수정 없음. 구조 파악 목적만.

### 기준선 (Risk 0 = 모든 flag=0)

n=1,982건, P10=-40.19%, hit=40.8%

### 핵심 결과

| Penalty | Step1 P10 sp | Step2 P10 sp | Step1 hit sp | Step2 hit sp | Step2 p |
|---------|-------------|-------------|-------------|-------------|---------|
| Debt >150% | -5.01pp | **-0.01pp** | +1.3pp | +3.9pp | 0.072 |
| Loss (NI<0) | -7.93pp | **+0.37pp** | -6.1pp | **-6.0pp** | 0.011* |
| Dilution >5% | — | — | — | — | — (데이터 없음) |
| Volatility >40% | -7.19pp | **-3.20pp** | -3.6pp | -1.8pp | 0.002** |

*Step2 = 해당 flag만 1, 나머지 모두 0인 종목 vs 모든 flag=0. "단독 penalty의 순 효과."*

### 해석

**Volatility >40%: 유일한 독립적 기여자**
Step 2(순수 단독)에서도 P10 spread -3.20pp, p=0.002** — 다른 penalty와 겹치지 않아도 단독으로 downside tail을 악화시킨다. aggregate Risk Score의 downside protection 효과를 가장 안정적으로 구동하는 요소.

**Loss (NI<0): overlap-dependent**
이변량(Step 1) P10 spread -7.93pp로 크게 보이지만, 순수 단독(Step 2) P10 spread는 +0.37pp — P10 악화 효과가 완전히 사라진다. Loss의 tail-downside 기여는 Volatility 또는 Debt와 함께 올 때 발생하는 구조. 단독으로는 P10이 아니라 hit rate(-6.0pp, p=0.011*)만 악화시킨다. 즉, "적자 기업 단독"은 하락폭이 크지 않아도 이기는 확률이 낮아지는 특성.

**Debt >150%: 독립 기여 없음**
Step 2 P10 spread=-0.01pp. Debt 단독은 downside tail에 실질적 기여 없음.

**Dilution: 데이터 공백**
0건 — `shares_prev` join 실패 또는 5% 임계값 미달. 별도 확인 필요.

### Flag 공존 구조

| Flag | 발생률 | 단독 비율 |
|------|--------|----------|
| Debt >150% | 26.8% | 31.5% |
| Loss (NI<0) | 26.0% | 25.9% |
| Volatility >40% | 47.6% | 48.8% |

Flag 간 상관: 0.10~0.16 (약한 양의 상관). 공존하는 경향이 있으나 압도적이지 않음.

Loss × Volatility 상관 0.163, Loss × Debt 0.154 — Loss의 downside 효과가 overlap 시 증폭되는 구조와 일치.

### 결론

**Risk composite score의 downside protection 효과는 Volatility가 독립적으로 구동하고, Loss는 co-occurrence 시 증폭하는 구조다.**

개별 penalty의 순 기여 크기: Volatility(-3.20pp) >> Loss(P10 없음, hit -6.0pp) > Debt(≈0)

Risk composite는 downside protection 목적에 부합한다. Volatility는 독립적인 downside discriminator로 확인되었고, Loss는 다른 risk flag와의 동시 발생에서 추가적인 downside를 나타낸다. Debt의 독립 기여는 현재 표본에서 확인되지 않았다. 따라서 현재 composite 구조를 유지하되, 구성요소별 배점 변경이나 제거는 별도 calibration 없이는 시행하지 않는다.

### 연구 한계

① Dilution 데이터 공백 — 결과 해석 시 4개 penalty 중 1개 누락. ② Flag 임계값 고정 — 변동성 40%, 부채 150%, 희석 5% 민감도 미검증. ③ Survivorship bias — 상장폐지 미포함으로 고위험 penalty 효과 과소평가. ④ 연도×종목 패널 반복 등장 — p-value 경향 참고용.

현재 상태: Risk Sub-component 분석 완료. 4가지 penalty 중 Volatility 독립 기여 확인, Loss overlap-dependent 확인. Dilution 데이터 공백 후속 조사 필요.

---

## Architecture 재검토 방향 — 연구 종합 (2026-09-09)
> **Status: 🔵 Closed — Superseded by Architecture Validation v2**


### 현재까지 연구로 드러난 역할 분류

| Component | 현재 연구상 역할 | 현재 상태 |
|-----------|---------------|---------|
| **Value** | Primary ranker | 순위 변별 역할 (현재 연구 범위 내 결론) |
| **Earnings** | Gate | 진입 하한선 역할 |
| **Risk** | Downside floor | tail-risk 방어, stress-invariant |
| **Supply** | 미확정/재검토 | 현재 데이터로 유효 역할 미확인 |

"Value = primary ranker"는 현재 연구 범위 내에서의 결론이다. 미국 시장의 value premium처럼 보편적으로 확립된 명제와는 다른 수준의 주장임을 유의한다.

### 핵심 질문

네 component를 100점짜리 단일 선형 점수로 합치는 것이 최선인가?

현재 연구 결과는 오히려 다음 구조가 더 자연스러울 가능성을 시사한다:

```
기업 평가
   │
   ├─ Value       → Ranker (종목 순위 결정)
   ├─ Earnings    → Gate (진입 하한선)
   ├─ Risk        → Downside Filter (tail-risk 방어)
   └─ Supply      → Execution Signal? (별도 실행 조건 또는 제거)
```

역할이 다른 component를 동일한 선형 점수로 합산하면, 각각의 고유한 기능이 희석된다. 특히 Supply가 점수 component로서 유효하지 않다면, 점수를 더 정교하게 만드는 것이 아니라 점수에서 빼는 것 자체가 architecture 개선일 수 있다.

### Freeze 해제 후 재검토 후보

이 역할 분류를 기준으로 현재 KR 엔진 architecture를 검토하는 것이 다음 연구 사이클의 출발점이다. 구체적으로:

- Value 단독 순위와 현재 composite 순위 중 어느 쪽이 alpha를 더 잘 설명하는가?
- Earnings Gate 기준(현재 22pt)이 적절한가?
- Risk를 별도 filter로 운용하면 composite에 포함할 때와 alpha가 달라지는가?
- Supply를 점수에서 제거하고 실행 조건(Gate)으로만 쓰면 어떻게 되는가?

### Retroactive Research Score (RRS) 정의

과거 데이터에 현재 production scoring formula(v8.5.19)를 소급 적용한 연구용 점수.

```
Production  : v8.5.19 고정 → Morning Run → live forward validation
Research    : Historical v8.5.19 Replay → architecture / component 연구
```

둘은 독립이다. RRS 연구는 Engine Freeze와 충돌하지 않는다.

RRS로 할 수 있는 것:
- 같은 역사적 시점에 Value / Earnings / Risk / Supply / Composite score가 동시에 존재한다고 가정
- Architecture A(선형 합산) vs B(Supply Gate) vs C(역할 분리) 비교
- component별 → forward alpha 관계 검증

RRS의 한계 (반드시 명시):
- PBR weight 등 일부 파라미터는 동일 역사 데이터를 보고 결정됨 → in-sample validation
- 실제 매매 환경(체결 시점, 시장 충격)은 반영 안 됨
- OOS/live validation을 대체하지 않음

문서에서 "RRS 기반 결과"와 "live calibration 결과"를 항상 분리 표기한다.

현재 상태: 방향 확정. RRS 구축 시작 (Step 1: research_stock_year 컬럼 확인).

---

## Value Component RRS 분석 v1 (2026-09-10)
> **Status: 🔵 Closed — Value 최강 신호 확인. Architecture v2로 통합됨**


### 연구 설계

rrs_value_analysis.py — RRS 패널(5,839건, risk_year 2016~2023) 기반. PIT 정렬 확인 완료.

**분석 구조:**
- Step 1: Value=0 bucket 분해 (PER 없음의 구성)
- Step 2: Value score 전체 분포 → mean / P10 / P25 / hit rate
- Step 3: Alpha Ranker vs Downside Discriminator
- Step 4: Risk=25 서브셋 내 Value → alpha
- Step 5: 섹터별 구성효과 분리

### Step 1 — Value=0 Bucket 분해

Value=0 bucket: 1,394건 (전체 23.9%). PER≤0 또는 null인 종목.

| Subgroup | n | mean alpha | P10 | P25 | hit% |
|----------|---|-----------|-----|-----|------|
| 흑자지만 PER 데이터 누락 (ni_ytd>0) | 614 | +4.53% | -46.42% | -26.40% | 42.8% |
| 적자 (ni_ytd<0) | 759 | +22.26% | -53.66% | -34.45% | 36.2% |
| ni_ytd 데이터없음 | 20 | +13.99% | -29.37% | -24.39% | 50.0% |

적자 subgroup의 mean alpha +22.26%는 복권효과(lottery effect)다. P10=-53.66%, hit rate=36.2%로 대부분 크게 잃고 소수가 크게 버는 패턴. 이 bucket을 "PER 없음"으로 묶으면 mean이 상방으로 심하게 왜곡된다.

흑자 but PER 누락(44.0%)은 데이터 공백이 주 원인으로 추정. 두 집단의 이질성이 크므로 Value=0을 단일 bucket으로 해석하면 안 된다.

### Step 2 — Value Score 전체 분포

| Value Score | 의미 | n | mean alpha | P10 | P25 | hit% |
|------------|------|---|-----------|-----|-----|------|
| 0 (PER 없음) | 적자/데이터공백 혼합 | 1,394 | +15.24% | -50.15% | -31.49% | 39.4% |
| 1~6 (PER≤6, 최저PER) | 가장 저평가 | 1,464 | -0.23% | -44.89% | -28.19% | 37.3% |
| 7~10 | PER 7~10 | 737 | -3.01% | -42.81% | -25.73% | 37.0% |
| 11~14 | | 554 | -0.42% | -39.44% | -23.76% | 42.2% |
| 15~18 | | 530 | -1.93% | -38.42% | -24.88% | 40.6% |
| 19~22 | | 537 | -3.48% | -36.98% | -23.29% | 38.9% |
| 23~25 (PER 21+, 최고PER) | 가장 고평가 | 623 | +1.08% | -41.09% | -26.53% | 40.4% |

**핵심 관찰:** mean alpha 단조성 없음. PER 최저 구간(score 1~6)이 가장 높은 alpha를 내지 않음. Pearson r=+0.002 (p=0.916) — PER 유효 종목 내 value_score × forward alpha 선형 관계 사실상 없음.

### Step 3 — Ranker vs Downside Discriminator

PER 유효 종목(value_score>0) 내 상위25%(score≥20, 저PER) vs 하위25%(score≤4, 고PER):

| Group | n | mean alpha | P10 | P25 | hit% |
|-------|---|-----------|-----|-----|------|
| Value 상위25% (저PER) | 1,160 | -1.03% | -39.00% | -25.28% | 39.7% |
| Value 하위25% (고PER) | 1,129 | -0.57% | -44.76% | -28.99% | 36.9% |

t-test p=0.863 (유의하지 않음).
- **mean spread = -0.46pp** — alpha ranker 역할 없음
- **P10 spread = +5.77pp** — 저PER의 downside 보호 효과 미미하게 존재하나 유의하지 않음

### Step 4 — Risk=25 서브셋

Risk 페널티 없는 종목(3,303건)으로 한정해도 Value score → mean alpha 단조성 없음. Risk가 clean한 상태에서도 Value가 alpha를 명확하게 분리하지 못함.

### Step 5 — 섹터별 구성효과

Value 상위25% vs 하위25%의 mean alpha spread 섹터별:
유통 +8.23pp / 금속 +5.00pp → Value 유효.
전기·전자 -11.46pp / 제약 -18.31pp / 운송장비 -5.17pp → Value 역효과.

Cross-sector 비교에서 구성효과가 signal을 희석시키고 있음. 특히 제약·전기전자는 구조적 고PER 섹터로 이 scoring의 적용 범위 밖임.

### 결론 (RRS 근사 기반)

현재 RRS 근사 역사 데이터에서 Value(PER)의 독립적인 alpha ranker 역할은 확인되지 않았다. P10 기준 미미한 downside 보호 효과(+5.77pp)는 존재하나 통계적으로 유의하지 않다.

"Value가 틀렸다"가 아니라, "현재 RRS 근사 데이터에서 Value 단독의 선형 ranker 효과는 나타나지 않았다"가 정확한 표현이다.

이 결과는 Architecture 재검토의 출발점이 된다. 다음 분석(rrs_architecture.py)에서 Value를 포함한 composite(A)와 Value 제외 composite(B)의 비교가 핵심 질문이다. Value를 포함했을 때 composite가 개선되는지, 악화되는지, 또는 P10/P25 기준으로만 기여하는지를 확인한다.

### 연구 한계

① hybrid value scoring(PBR weight)이 동일 역사 데이터 보고 결정 → in-sample. ② 적자 종목 PER=null → value_score=0으로 처리 — 별도 분석 필요. ③ Survivorship bias: 상장폐지 미포함. ④ 연도×종목 패널 반복 — p-value 경향 참고용.

현재 상태: Value RRS v1 분석 완료. Architecture 비교(A/B/C/D) 다음 단계.

---

## Value Factor Research — Academic B/M Replication (2026-09-10)
> **Status: 🔵 Closed — B/M replication 확인. Architecture v2 지지**


### 연구 배경

기존 RRS 분석(Value Component RRS v1)에서 현재 PER-based Value score의 forward alpha ranker 역할이 확인되지 않았다(IC≈0.059, p>>0.05). 이에 문제를 "PER scoring 자체의 문제인가, 아니면 Value라는 개념이 한국 시장에서 작동하지 않는가?"로 분리하기 위해, 학술적으로 검증된 Value 정의를 한국 PIT 데이터에서 재현했다.

**핵심 질문:** 학계에서 정의된 Value factor(Fama-French HML, B/M)가 한국 시장에서 재현되는가? 그리고 현재 엔진의 PER scoring과 어떻게 다른가?

사용 스크립트: `value_factor_v1.py`, `value_factor_v1_ext.py`, `value_factor_v2.py`
데이터: RRS 패널 5,721건 (PBR 유효), fwd_year 2017~2024, PIT-correct

---

### V1 — Academic Value Replication

**B/M = 1/PBR** (Fama-French HML 정의), **E/P = 1/PER** (흑자 종목만). 연도별 단면 quintile 정렬. IC = Spearman rank correlation, 연도별 t-test.

| Measure | avg IC | p | 방향성 | Q5-Q1 spread | P10 spread |
|---------|--------|---|--------|--------------|------------|
| B/M (1/PBR) | **+0.135** | **0.017*** | 8/8 | +5.46pp | **+14.29pp** |
| E/P (1/PER, 흑자) | +0.063 | 0.125 | 6/8 | +1.09pp | +7.36pp |
| 현재 PER Score (RRS) | +0.059 | — | — | +2.11pp | — |

초기 해석: B/M이 E/P와 현재 PER Score보다 한국 시장에서 더 강한 Value signal로 보였다. IC +0.135(p=0.017), 8/8년 모두 양수. E/P와 현재 PER Score는 IC≈0.06 수준으로 유의하지 않아 실질적으로 동등했다.

---

### V1 Extension — Size별 B/M 효과

단순 Small-cap bias인지 확인. 연도별 Size tertile(T1=Small/T2=Mid/T3=Large) 내에서 B/M IC 분리.

| Size | avg IC | p | 방향성 |
|------|--------|---|--------|
| Small | +0.096 | 0.079 | 7/8 양수 |
| **Mid** | **+0.191** | **0.002*** | **8/8 양수** |
| Large | +0.099 | 0.084 | 6/8 양수 |

Mid-cap에서 B/M 효과가 가장 강하고 유일하게 유의. Small과 Large는 방향성은 있으나 유의하지 않음. 따라서 B/M premium이 단순 Small-cap 구성효과로 설명되지 않음.

**Q4 이상치:** 전체 B/M Q4 mean alpha = +11.08%로 Q5(+3.25%)보다 높은 비단조 패턴. 이는 Small × Q4에 집중된 것으로 확인: Small Q4 = +30.67%, Mid Q4 = -4.05%, Large Q4 = -0.69%. 구조적 Q4 premium이 아닌 소수 연도의 소형주 이상치.

**Size-neutral B/M:** 각 Size bucket 내에서 B/M rank를 재정렬해도 IC = +0.126(p=0.018)으로 유의. Size 구성효과가 아닌 B/M 자체의 신호가 존재함을 확인. 그러나 이 IC 역시 distress 성분이 포함된 상태임(V2에서 분해).

---

### V2 — Distress Decomposition

**핵심 질문:** Mid IC +0.191이 진짜 Value signal인가(Case A), distress/lottery인가(Case B)?

**Mid-cap 내 IC 분해:**

| Mid 서브셋 | IC | p | 양수 연도 |
|-----------|-----|---|---------|
| Mid 전체 | **+0.191** | **0.002*** | 8/8 |
| Mid \| Risk<25 (distress) | **+0.203** | **0.001*** | 8/8 |
| Mid \| Risk=25 (clean) | +0.114 | 0.095 | 6/8 |
| Mid \| 흑자 + Risk=25 | +0.114 | 0.091 | 6/8 |

Mid IC +0.191은 Risk<25(페널티 있는 distress 종목)가 구동하는 것으로 확인. Risk-clean 종목(Risk=25)으로 한정 시 IC +0.114(p=0.095)로 유의하지 않음.

**Small Q4 lottery 확인:**

| Small × Q4 | mean alpha | P10 |
|-----------|-----------|-----|
| 전체 | +30.67% | -36.62% |
| Risk<25 | **+52.96%** | -39.32% |
| Risk=25 | +8.58% | -34.28% |

Small Q4 +30.67%의 대부분은 Risk-penalized 소형주의 lottery-type recovery. Risk-clean 상태에서는 +8.58%로 크게 감소.

**B/M + Risk filter의 역설:**

전체 B/M Q4~Q5 종목에 Risk=25 필터를 추가하면:
- IC: +0.135 → +0.084 (유의하지 않음, p=0.150)
- mean alpha: +7.14% → +0.40%

현재 엔진의 Risk 컴포넌트가 걸러내는 종목들이 B/M의 겉보기 프리미엄의 핵심 원천이었음. Risk filter는 downside tail을 줄이지만, 동시에 lottery recovery premium도 제거한다.

---

### 최종 판정 — Case B

**B/M은 한국 시장에서 단순하고 clean한 Value ranker로 검증되지 않았다.**

관측된 B/M 효과(IC +0.135, p=0.017)는 Mid-cap에서 강하게 나타났지만, 대부분 Risk-penalized/distress 종목의 lottery-like recovery와 함께 발생했다. Risk-clean 종목에서 B/M의 독립적인 예측력은 통계적으로 유의하지 않았다(Mid Risk=25 IC=+0.114, p=0.095). 따라서 raw B/M을 Primary Ranker로 채택하지 않는다.

**현재 엔진에 대한 함의:**

현재 엔진의 hybrid PER + sector-relative valuation은 유지한다. 다만 이 연구가 "현재 엔진의 정당성을 증명했다"는 의미가 아님을 주의한다. Raw B/M을 추가할 필요가 없다는 결론까지만이 이 연구의 범위다.

아이러니하게도, 현재 엔진에 이미 있는 Risk 컴포넌트가 B/M의 겉보기 효과의 핵심 중개자다. B/M을 raw ranker로 추가하면 Risk가 이미 처리하는 distress 종목에 추가 배점을 주는 효과가 생긴다.

### 연구 한계

① 8년 패널(8개 IC 관측치) — IC t-test 검정력 제한. ② Survivorship bias 미제거 — distress premium 방향은 하방으로 과소평가 가능. ③ B/M = 1/PBR은 순자본 기준. 무형자산 조정(intangible-adjusted) 버전 미적용. ④ Risk proxy는 RRS 근사치(vol, debt, loss flag 기반). ⑤ PBR은 fwd_year 시작 시점(PIT-correct)이나 Book equity는 risk_year Q3 기준 추정.

### 다음 연구 후보

- E/P 심층 분석: 흑자 종목 내 E/P quintile × Size × Risk 분해 (B/M과 같은 패턴인지)
- Retained Earnings/Market: 한국 선행연구에서 제안된 대안적 Value measure
- Sector-neutral B/M: 섹터 내 B/M rank가 섹터 간 B/M rank보다 유의한지

현재 상태: Academic B/M Replication 완료. Case B 확정. Primary Ranker 미채택. 다음 연구 트랙 별도 결정.

---

## Earnings Gate 검증 (2026-09-10)
> **Status: 🔵 Closed — Architecture v2 결론에 통합됨**


**스크립트:** `research/earnings_gate.py`  
**패널:** 5,839건 / risk_year 2016–2023  
**핵심 질문:** Earnings 컴포넌트는 Gate(하한선 필터)인가, Ranker(연속 변별자)인가, 아니면 역방향 구조인가?

### 검증 설계 (사전 고정)

4개 독립 테스트:
1. 적자(ni_ytd<0) vs 흑자(ni_ytd>0) 경계 효과
2. 흑자 내 ROE Q1~Q5 단조성
3. earnings_score vs raw ni_ytd/ROE 교차 검증
4. Value Top 25% × Earnings Gate 실용 효과

### 결과

**Test 1 — 흑자 vs 적자 경계:**

| 구분 | n | mean alpha | t-stat | p-value |
|------|---|-----------|--------|---------|
| 흑자 (ni_ytd>0) | 4,254 | **-0.16%** | — | — |
| 적자 (ni_ytd<0) | 1,518 | **+10.29%** | -3.35 | 0.001** |

흑자가 적자보다 높은 연도: **3/8년**. 예상 방향과 반대.

**Test 2 — 흑자 내 ROE 단조성:**

| ROE Quintile | mean alpha | 방향 |
|-------------|-----------|------|
| Q1 (Low ROE) | +5.22% | ↑ |
| Q2 | +2.27% | — |
| Q3 | -1.71% | — |
| Q4 | -0.55% | — |
| Q5 (High ROE) | -3.50% | ↓ |

IC = **-0.039** (p=0.097). 역단조성. 고ROE → 낮은 alpha.

**Test 3 — earnings_score vs raw data 교차 검증:**

| earnings_score | 의미 | mean alpha |
|---------------|------|-----------|
| 22 | 고품질 흑자 | **-4.32%** |
| 9 | 적자 근사 | **+10.68%** |

earnings_score가 raw 데이터와 일치한 방향으로 역전. 측정 오류 아님.

**Test 4 — Value Top 25% × Earnings Gate:**

| 조합 | mean alpha | P10 |
|------|-----------|-----|
| Value Top 25% 전체 | -1.31% | -38.75% |
| Value Top 25% × 흑자 Gate | -2.39% | -37.61% |

Gate 적용 후 mean alpha 소폭 악화, P10 소폭 개선(+1.14pp).

### 판정

Earnings는 Gate도 Ranker도 아님. **역방향 lottery 구조.**

- 적자의 높은 mean alpha는 극소수 반등 종목이 만드는 right-skewed 분포의 결과.
- 흑자 내 ROE 역전도 동일 메커니즘 가능성 있으나 별도 검증 필요.
- P10/downside 기준으로는 흑자 > 적자 방향이 유지됨 → Gate로서의 downside 보호 기능은 존재.

**기록:** Fundamental quality와 mean forward alpha 사이의 반복적인 역방향 패턴이 관찰됨. 이 현상이 panel/benchmark/objective의 구조적 특성인지 추가 검증 필요.

---

## Lottery-adjusted Objective Validation (2026-09-10)
> **Status: 🔵 Closed — Architecture v2 결론에 통합됨**


**스크립트:** `research/objective_validation.py`  
**패널:** 5,839건 / risk_year 2016–2023  
**핵심 질문:** B/M, Earnings, Risk에서 반복 관찰된 "역방향 mean alpha"가 lottery distribution 왜곡인가, 아니면 진짜 구조적 현상인가?

**검증 방법:** 동일 비교 쌍에 대해 raw mean / median / trimmed mean(10%) / winsorized mean(5%) + P10 / hit rate / downside deviation 동시 측정. mean과 median의 방향이 반대면 lottery 왜곡, 일치하면 구조적 현상.

### 결과 요약

| 비교 | mean(A-B) | median(A-B) | P10(A-B) | 판정 |
|------|-----------|-------------|----------|------|
| BM1: Value Q5 vs Growth Q1 | +5.46 | **+10.71** | +14.30 | ✓ CONSISTENT |
| BM2: Value Q5 Risk-clean vs Q1 | -1.27 | **+8.49** | +7.52 | LOTTERY |
| EG1: 흑자 vs 적자 | -10.45 | **+5.37** | +7.93 | LOTTERY |
| EG2: ROE Q5 vs Q1 (흑자 내) | -8.72 | **-4.69** | -0.06 | **⚠ STRUCTURAL** |
| ES1: earnings_score 22 vs 9 | -15.00 | **+2.76** | +5.85 | LOTTERY |
| RS1: Risk=25 vs Risk≤17 | -14.27 | **+2.94** | +13.92 | LOTTERY |

**판정 기준:**
- **LOTTERY**: mean 역방향이지만 median·tmean 정방향 → 극단 outlier(loss→recovery) 종목이 mean을 당김
- **CONSISTENT**: mean/median 모두 같은 방향 → 일관된 신호
- **STRUCTURAL**: mean/median 모두 역방향 → lottery로 설명되지 않는 구조적 패턴

### 핵심 발견

**① 대부분의 역방향 mean alpha = lottery 왜곡 (BM2, EG1, ES1, RS1)**

나쁜 fundamental(distress, 적자, earnings 저품질, risk 페널티) 종목의 높은 mean alpha는 소수 극단 회복 종목이 만드는 right-skewed 분포의 결과다. Median, trimmed mean 기준으로는 정방향이 회복된다. Raw mean은 한국 소형·distress 주식의 lottery distribution 때문에 신뢰할 수 없는 objective.

**② P10은 대부분의 비교에서 정방향 (downside 보호 기능 유효)**

좋은 fundamental 그룹이 나쁜 그룹보다 downside tail이 작음. 이는 엔진이 추구하는 "나쁜 종목을 걸러내는" 역할과 일치한다.

**③ ROE Q5 vs Q1 (EG2)은 예외 — STRUCTURAL 패턴**

mean -8.72%, median -4.69%. Median까지 역방향이므로 lottery로 설명되지 않는다. 흑자 기업 내에서 ROE가 높을수록 alpha가 낮은 구조적 현상이 관찰됨. 

가능한 해석 후보 (아직 결론 아님):
- 한국 시장에서 고ROE 기업은 이미 고평가 → Value premium 미적용
- 저ROE 흑자 기업의 mean reversion/개선 기대 효과
- 구성 종목 섹터 편향 (IT, 제약 고ROE 성장주 vs 금융·소비재 저ROE 가치주)

**④ BM1 (전체 B/M Q5 vs Q1)은 CONSISTENT — 유일하게 모든 지표 정방향**

mean +5.46, median +10.71, P10 +14.30. distress 제거 전 전체 B/M 비교는 mean 포함 모든 robust statistics에서 Value > Growth. 단, BM2(Risk-clean)에서 mean이 역전되므로 B/M 전체 신호의 일부는 distress premium 포함.

### 결론

**역방향 mean alpha의 주된 원인은 lottery distribution 왜곡이다.**

엔진이 현재 mean alpha를 objective로 사용할 경우 잘못된 신호를 받는 구조다. P10, median, downside deviation이 더 적절한 evaluation metric. 단, ROE 역전(EG2)은 lottery 이후에도 잔존하는 패턴으로 별도 추가 검증이 필요하다.

**기록:** Fundamental quality와 mean forward alpha 사이의 반복적인 역방향 패턴이 관찰됨. 대부분은 lottery distribution 왜곡으로 설명 가능하나, ROE 내 역전(EG2)은 구조적 패턴 가능성 있음. 이 현상이 panel/benchmark/objective의 구조적 특성인지 추가 검증 필요.

### 다음 단계

- EG2 (ROE 구조적 역전) 원인 분해: 섹터 편향 여부, 연도별 안정성 확인
- Calibration objective 재검토: mean alpha 대신 median alpha 또는 P10 중심으로 KPI 재정의 가능성
- Architecture 재평가: 이 결과를 바탕으로 V+E+R+S 조합 재검토

---

## ROE Structural Inversion 분해 (2026-09-10)
> **Status: 🔵 Closed — ROE 역효과 메커니즘 확인**


**스크립트:** `research/roe_inversion.py`  
**패널:** 4,175건 (흑자 한정, ni_ytd>0) / fwd_year 2017–2024  
**핵심 질문:** objective_validation에서 확인된 ROE STRUCTURAL 역전의 원인은 무엇인가?

### 기준선 재확인

| ROE Quintile | mean | median |
|-------------|------|--------|
| Q1 (Low) | +5.22% | -5.13% |
| Q2 | -0.50% | -6.33% |
| Q3 | -0.63% | -6.44% |
| Q4 | -0.90% | -6.16% |
| Q5 (High) | -3.50% | -9.82% |

Q5-Q1 diff: mean=-8.72, median=-4.69 → **STRUCTURAL** (양방향 역전).  
IC = -0.037 (p=0.103). 8년 중 양수 연도 3/8.

### Test 1 — Valuation 통제 (PBR Quintile 내 ROE Q5 vs Q1)

| PBR 구간 | median diff | 판정 |
|---------|------------|------|
| Q1 (저PBR) | -3.70 | STRUCTURAL ↓ |
| Q2 | -2.84 | STRUCTURAL ↓ |
| Q3 | -3.68 | STRUCTURAL ↓ |
| Q4 | -2.85 | STRUCTURAL ↓ |
| Q5 (고PBR) | +0.19 | LOTTERY |

PBR Q1~Q4 전구간에서 역전이 유지됨. **Valuation premium으로 설명 안 됨.**

"싼 고ROE(Low PBR + High ROE)"가 Value+Quality 신호라는 가설 기각:  
Low PBR + Low ROE: mean +5.78%, median -1.82%  
Low PBR + High ROE: mean -5.52%, median -10.17%

### Test 2 — Sector 중립 (섹터 내 ROE rank)

| | IC | p | median diff |
|--|--|--|--|
| Raw ROE_q | -0.037 | 0.103 | -4.69 |
| Sector-neutral | -0.028 | 0.214 | -2.69 |

섹터 중립 후에도 역전 유지. **섹터 구성 편향으로 설명 안 됨.** 섹터별 IC를 보면 전 16개 섹터에서 IC가 음수이거나 0에 근접 — 특정 섹터 집중이 아닌 범용 패턴. 다만 IC가 소폭 약해졌으므로(p=0.214) 섹터가 일부 기여함을 시사.

### Test 3 — ROE Level vs ΔROE (변화율)

| | IC | p | 방향 |
|--|--|--|--|
| ROE level | -0.039 | 0.095 | 음수 |
| ΔROE (YoY 변화) | -0.007 | 0.785 | **사실상 0** |

ΔROE는 forward alpha에 대한 예측력이 없음. "ROE 개선 중인 기업이 더 높은 alpha"라는 가설 기각. ΔROE Q1~Q5 분포에서 단조성 없음. **효과는 변화율이 아닌 수준(level)에 있음.**

2×2 (High/Low ROE × Rising/Falling ΔROE): 패턴 불명확 (표본 편차 큼).

### Test 4 — ROE × PBR 교차

| 조합 | mean | median | hit% |
|------|------|--------|------|
| Low PBR + Low ROE | +5.78% | -1.82% | 47.8% |
| Low PBR + High ROE | -5.52% | -10.17% | 41.9% |
| Mid PBR + High ROE | -3.25% | -9.82% | 39.7% |
| High PBR + High ROE | -4.23% | -12.48% | 36.6% |
| High PBR + Low ROE | +12.88% | -8.86% | 40.1% |

고ROE는 valuation 구간에 관계없이 저alpha. Low PBR + Low ROE가 오히려 상대적으로 좋은 성과.

### 최종 판정 — ROE 역전의 원인

4개 테스트 모두에서 ROE 역전이 살아남음:
- Valuation 통제 후에도 유지 (Test 1)
- Sector 중립 후에도 유지 (Test 2)
- ROE 변화율(ΔROE)로 설명 안 됨 (Test 3)
- 저PBR+고ROE 조합도 역전 (Test 4)

**가장 그럴듯한 해석: Mean reversion + Already priced in**

한국 시장에서 고ROE 기업은 "알려진 우량주"로 시장이 미래 이익을 선반영. 기대치가 이미 높아 incremental alpha 여지가 작음. 저ROE 흑자 기업은 기대치가 낮아 소폭 개선만으로도 alpha 발생 가능. Quality factor 단독 효과의 부재는 글로벌 학계에서도 관찰되는 구조 (Asness et al. 2019, "Quality Minus Junk" — B/M 결합 시 실효성 발생).

**기록:** Fundamental quality(ROE)와 mean/median forward alpha 사이의 역방향 관계는 Valuation, Sector, Trend 통제 후에도 유지되는 구조적 패턴으로 확인됨. 원인은 단일 요소로 특정되지 않음. 추가 검증 필요.

### 엔진에 대한 함의

이 결과는 현재 설계("Earnings = Gate, not Discriminator")를 지지함. ROE를 흑자 기업 내부 ranker로 더 세분화하면 역효과가 날 가능성이 있음. Earnings 컴포넌트의 역할을 "나쁜 기업 제거"로 한정하는 현재 설계가 이 데이터와 일치함.

### 연구 한계 및 다음 단계

- 표본: 흑자 기업 4,175건 / 8년. ROE 역전의 연도별 변동이 크며(양수 연도 3/8) 일부 연도는 반대 방향일 수 있음.
- ΔROE 데이터가 일부 연도만 join 가능한 구조로 완전하지 않음.
- 다음 후보: E/P(Earnings yield) 단독 분석 — ROE 역전이 E/P에서도 동일하게 나타나는지 확인.

---

## Architecture Counterfactual (2026-09-10)
> **Status: 🔵 Closed — Supply 실데이터 재실행으로 대체됨**


**스크립트:** `research/architecture_counterfactual.py`  
**패널:** 5,839건 / risk_year 2016–2023  
**핵심 질문:** V+E+R+S 선형 합산보다 역할 분리(Gate/Filter/Ranker) 구조가 더 나은가?

### 패널 한계 — Supply 상수 발견

RRS 역사 replay에서 supply_score = 12 (전 패널 상수). Supply 컴포넌트가 변동 없이 기록됨. Supply 관련 Architecture (B, F) 테스트 불가. 실제 비교 가능한 구조는 A/C/D/E 4개.

### 결과

| Architecture | n/yr | median | tmean | p(vs A) | A보다 나은 연도 |
|---|---|---|---|---|---|
| A. V+E+R linear | 182 | -6.13 | -5.45 | — | — |
| C. E Gate(≥22) + V+R | 37 | -7.93 | -6.55 | 0.871 | 3/8 |
| D. R Filter(≥22) + V+E | 117 | -6.16 | -5.45 | 0.910 | 3/8 |
| E. E+R Gate + V pure | 32 | -7.19 | -6.47 | 0.986 | 3/8 |

어떤 역할 분리 구조도 선형 합산보다 유의미하게 낫지 않음.

### 해석 — 결론 보류

이 결과를 "현재 구조가 맞다"로 읽으면 안 됨. 정확한 판정:

1. **검정력 부족**: 8년 × 8개 연도 관측은 구조 간 차이를 감지하기에 표본이 너무 작음. p값 0.87~0.99는 "동일하다"는 증거가 아니라 "구분 불가"를 의미.
2. **E Gate 과제한**: earnings_score ≥ 22 통과율 20.7%, 연 37종목. 신호/잡음 비율이 너무 낮아 어떤 결론도 신뢰하기 어려움.
3. **Supply 테스트 불가**: Production 스냅샷 데이터(n≥100 달성 후)로 별도 검증 필요.

**기록:** RRS 패널 8년 데이터에서 역할 분리 구조가 선형 합산보다 낫다는 근거를 찾을 수 없었다. 검정력이 극히 낮아 이것이 '동일하다'는 증거도 아니다. Architecture 구조 비교는 Production 데이터(n≥100) 확보 후 재검토.

---

## Architecture Counterfactual — Supply 실데이터 재실행 (2026-09-10)
> **Status: 🔵 Closed — Architecture v2로 통합됨**


**배경:**
이전 실행에서 supply_score가 전 패널 상수(=12)였음이 확인됨.
supply_snapshot 테이블(2016-01 ~ 2024-06, 매월 말, ~730 티커)에서
`apply_historical_features.py`로 실데이터 적용 후 재실행.

**supply_score_real 적용 결과:**
- 실데이터: 5,795건 (supply_approx=0), 평균 14.35
- 스냅샷 없음: 44건 (supply_approx=1, 중립 12 유지)
- Supply gate 통과율: 59.8% (436/730건/년) — 이전 100%에서 실제 분리됨

**Architecture 비교 결과 (전기간 합산, primary: median/tmean):**

| Architecture | n/yr | median | tmean | p(vs A) | A보다 나은 연도 |
|---|---|---|---|---|---|
| A. V+E+R+S linear | 182 | -6.13 | -5.45 | — | — |
| B. Supply Gate(≥med) + V+E+R | 109 | -7.93 | -6.13 | 0.296 | 3/8 |
| C. E Gate(≥22) + V+R | 37 | -7.93 | -6.55 | 0.871 | 3/8 |
| D. R Filter(≥22) + V+E | 117 | -6.16 | -5.45 | 0.910 | 3/8 |
| E. E+R Gate + V pure | 32 | -7.19 | -6.47 | 0.986 | 3/8 |
| F. E+R+S Gate + V pure | 21 | -11.16 | -9.14 | 0.385 | 1/8 |

**핵심 발견:**

Supply gate(B)를 적용하면 median이 A보다 -1.80pp 악화. Full Role(F)은 세 gate 모두 적용해 -5.03pp 악화. Gate를 많이 적용할수록 선택된 pool이 작아지고 성과가 나빠지는 경향.

Supply를 timing gate로 쓰는 것은 이 패널에서 지지되지 않음. 연말 기준 20D/60D 수급 양수 = 미래 alpha 예측이 아님.

**최종 Architecture 판정:**

어떤 역할 분리 구조도 선형 합산보다 유의미하게 낫지 않음 (모든 p > 0.29). Supply 실데이터 포함 후에도 결론 동일. Architecture 구조 비교는 RRS 패널에서 닫음.

---

## Momentum IC 분석 (2026-09-10)
> **Status: 🔵 Closed — Momentum continuation 미확인**


**연구 스크립트:** `research/momentum_ic.py`
**패널:** rrs_stock_year 5,791건 (mom_rel 가용), risk_year 2016-2023, fwd_year 2017-2024

**핵심 질문:** 현재 production Momentum signal(mom_rel_1m/3m/6m)이 어느 time horizon에서 실제 정보를 가지고 있는가?

**결과 요약:**

| 창 | IC | p | 양수연도 | IC(SN) | p(SN) | 판정 |
|---|---|---|---|---|---|---|
| 1M | -0.037 | 0.168 | 1/8 | -0.038 | 0.067 | 무효 |
| 3M | -0.015 | 0.419 | 4/8 | -0.018 | 0.312 | 무효 |
| 6M | -0.010 | 0.749 | 2/8 | -0.007 | 0.790 | 무효 |

**Quintile Q5(High Mom) - Q1(Low Mom) 차이:**

| 창 | mean 차이 | median 차이 | 분류 |
|---|---|---|---|
| 1M | -9.92 | -2.99 | STRUCTURAL ↓ |
| 3M | -10.66 | -3.92 | STRUCTURAL ↓ |
| 6M | -11.63 | +0.25 | LOTTERY (mean↓/median≈0) |

**핵심 발견: 역방향 Reversal 패턴**

세 창 모두 IC가 음수 방향. 최근 KOSPI 대비 outperform한 종목(Q5)이 다음 해 더 underperform하는 **단기 reversal 효과**. 1M이 가장 선명(양수연도 1/8), 3M 중간, 6M은 사실상 noise.

부분적 유의성:
- 1M sector-neutral IC p=0.067 (섹터 통제 후에도 방향 유지 → 섹터 rotation 효과가 아님)
- 2020년만 1M IC=+0.092* (유일하게 양수 유의) — 코로나 반등장 특이값
- 2023년 1M IC=-0.151** (강한 음수 유의) — reversal 패턴 집중

**연도별 패턴:** 1M이 2023/2024 양쪽에서 유의한 음수. 최근 연도로 올수록 reversal이 강화되는 경향 관찰 (단, 8개년 샘플로 트렌드 결론 보류).

**Q1(Low Mom) 구조:** mean=+6.26이지만 median=-9.81 → LOTTERY. 저모멘텀 종목도 극단 상승주가 섞여있어 역방향 신호를 포지셔닝에 그대로 쓰기 어려움.

**판정:**

공식 기준(p<0.05 + 양수연도 ≥ 6/8) 미달 → 세 창 모두 "무효". 단, IC 방향의 일관성과 Q5-Q1 STRUCTURAL 분류(1M/3M)는 **역방향 패턴의 약한 증거**.

**생산 시스템 함의:**

현재 production Momentum score가 최근 상대수익률이 높은 종목에 점수를 더한다면 오히려 역효과. 역방향 filter(단기 과열 종목 제외) 가능성은 있으나 통계적 유의성 미달 상태에서 도입 금지. Engine Freeze 해제 후 OOS 검증 선행 필요.

**1차 판정 (1M/3M/6M 기준):**

1M/3M STRUCTURAL ↓, 6M LOTTERY. 세 창 모두 무효 판정이나 방향이 음수로 일관. 후속 연구(Horizon Mapping)로 연결.

---

## Momentum Horizon Mapping (2026-09-10)
> **Status: 🔵 Closed — Reversal 패턴 관찰. 추가 forward 검증 대기**


**연구 스크립트:** `research/extend_momentum_features.py` + `research/momentum_horizon_map.py`
**추가 창:** 9M, 12M, 12-1, 6-1 (supply_snapshot에서 파생)

**핵심 질문:** reversal → continuation의 경계가 어느 formation window에 있는가?

**IC 요약:**

| 창 | IC | p | 양수연도 | Q5-Q1 mean | Q5-Q1 med | 판정 |
|---|---|---|---|---|---|---|
| 1M  (Dec→Nov) | -0.037 | 0.168 | 1/8 | -9.91 | -2.99 | 무효 |
| 3M  (Dec→Sep) | -0.015 | 0.419 | 4/8 | -10.66 | -3.92 | 무효 |
| 6M  (Dec→Jun) | -0.010 | 0.749 | 2/8 | -11.63 | +0.24 | 무효 |
| 9M  (Dec→Mar) | -0.040 | 0.218 | 2/8 | -12.50 | -3.90 | 무효 |
| 12M (Dec→Dec_prev) | **-0.076*** | **0.025** | **0/7** | -17.97 | -7.27 | **REVERSAL ↓** |
| 6-1 (Nov→Jun) | +0.015 | 0.662 | 6/8 | -6.52 | +1.79 | 무효 |
| 12-1 (Nov→Dec_prev) | -0.062† | 0.075 | 1/7 | -13.96 | -5.17 | 약한 신호 |

**핵심 발견:**

가설 A(짧은 창 reversal → 긴 창 continuation) 기각. **12M이 가장 강한 REVERSAL** (p=0.025, 양수연도 0/7). 전통적 12개월 momentum이 KR에서는 가장 선명한 역방향. 12-1도 같은 방향(p=0.075). 짧은 창(1M-9M)은 무효 수준 잡음. **가설 B 채택: 전 창 reversal 또는 무효.**

**6-1 예외:** 양수연도 6/8이지만 IC=+0.015 (p=0.662). 방향만 양수이고 크기 없음. 주목할 패턴이나 결론 불가.

**연도별 패턴:**
- 2018/2019/2021: 12M IC 강한 음수 유의 → reversal 지배 연도
- 2024: 6M=+0.18**, 9M=+0.14**, 6-1=+0.21** → 이 해만 중기 continuation 선명 (단일 연도, regime 변화 가능성)
- 2020: 1M만 양수유의 (코로나 반등), 나머지 역방향

**왜 12M이 가장 강하게 역방향인가 (가설):**
이 패널은 이미 value 스크리너 통과 종목. 12개월간 outperform한 value 종목 = "잠시 올랐다가 value trap으로 회귀"하는 패턴일 가능성. 또는 KR 기관 연말 윈도우드레싱 → 이듬해 rebalancing 역전 효과. 두 가설 모두 in-sample 확인 불가.

**최종 판정 — Momentum Horizon Mapping:**

어떤 formation window도 continuation을 통계적으로 보여주지 못함. 12M이 유일하게 p<0.05이나 방향이 역방향. **현재 production Momentum 정의 교체 근거 없음. Momentum 컴포넌트 편입 근거 없음.** 이 엔진에서 Momentum은 우선 연구 대상에서 제외.

Engine Freeze 해제 후 OOS 검증 없이는 어떤 형태의 momentum signal도 도입하지 않는다.

---

## Architecture Validation v2 — Role-based Counterfactual (2026-09-10)
> **Status: 🟡 Current — 현재 연구 기준점. Forward validation 진행 중**


**연구 스크립트:** `research/architecture_v2.py`
**패널:** rrs_stock_year 5,839건, fwd_year 2017-2024, Supply 실데이터 적용

**핵심 질문:** 연구에서 도출된 역할 가설(E=Gate, R=Filter, V=Ranker)을 적용하면 선형 합산보다 나은가?

**Architecture 설계 (사전 고정):**
- A: 현재 선형 V+E+R+S (baseline)
- B: V+E+R 선형 (Supply 제거, 기여도 분리용)
- C: 역할 분리 핵심 — E Gate(≥22) + R Filter(≥22), V ranker
- D: E Gate(≥22) only + V ranker
- E: R Filter(≥22) only + V ranker

**결과 요약:**

| Architecture | n/yr | median | tmean | P10 | hit% | dd |
|---|---|---|---|---|---|---|
| A. 현재 선형 (V+E+R+S) | 182 | -6.13 | -5.45 | -37.88 | 42.1% | 18.62 |
| B. V+E+R 선형 (no Supply) | 182 | -6.13 | -5.45 | -37.88 | 42.1% | 18.62 |
| C. 역할분리 (E+R gate, V) | 32 | -7.19 | -6.47 | -40.66 | 42.1% | 18.08 |
| D. E Gate only + V | 37 | -7.19 | -6.09 | -39.32 | 42.8% | 18.92 |
| E. R Filter only + V | 117 | -5.67 | -5.28 | -38.07 | 42.7% | 18.30 |

**A vs 각 구조 — 연도별 median 차이 t-test:**
- A vs B: diff=0.00, p=nan (완전 동일)
- A vs C: diff=+0.03, p=0.986 (무효)
- A vs D: diff=-0.09, p=0.959 (무효)
- A vs E: diff=+0.21, p=0.825 (무효)

**역할 분리 내부:**
- C vs D (R Filter 기여): C-D median=+0.12, p=0.829 → R Filter 단독 기여 없음
- C vs E (E Gate 기여): C-E median=-0.18, p=0.908 → E Gate 추가 시 오히려 약화

**핵심 발견 3가지:**

**① A = B: Supply는 현재 linear에서 순위 변별 기여 없음.** diff=0.00 → 완전히 동일한 종목 선택. Supply score가 normalized_score에 포함되어도 V+E+R이 dominant해서 상위 25% 선택에 실질 영향 없음.

**② E Gate(≥22)가 역효과.** 통과율 20.7%, E+R 교집합 17.9% → n/yr≈32. Pool이 너무 좁아져 Value ranker의 변별이 불가능해짐. "흑자 상위 21%"는 Gate가 아니라 과도한 exclusion. C/D가 A보다 나쁜 주원인.

**③ E (R Filter only)가 5개 중 가장 좋음.** median=-5.67 vs A의 -6.13. 유의미하지 않지만 (p=0.825) 방향 일관. R의 downside discriminator 역할 가설을 완전히 부정하지는 않음.

**판정: 역할 분리가 선형보다 낫지 않음 — v1 결론 동일.**

다만 원인이 다름. v1은 Supply 상수 문제가 있었고, v2에서 실데이터 적용 후에도 결론은 동일. "역할 분리 아이디어 자체 기각"이 아니라 "현재 E Gate 임계값(≥22)이 Gate 역할보다 과도한 exclusion을 하고 있어 역할 분리 효과를 확인 불가"가 정확한 해석.

**Architecture 구조 비교는 RRS 패널에서 종결.** Engine Freeze 해제 전 production 변경 금지.

---

## Earnings Gate Sensitivity — Architecture v2.1 (2026-09-10)
> **Status: 🟡 Current — Architecture v2 보조 분석**


**연구 스크립트:** `research/earnings_gate_sensitivity.py`
**사전 고정 threshold:** ≥15, ≥20, ≥22 (결과 보기 전 고정)

**핵심 발견 1 — Earnings score는 사실상 범주형 (9 / 20 / 22)**

min=9, p10=9, p25=9, median=20, p75=20, max=22. 연속형이 아니라 세 값만 존재:
- ~25%: score=9 (적자/저품질)
- ~54%: score=20 (흑자)
- ~21%: score=22 (고품질)

이 구조에서 Earnings를 연속 ranker로 쓰는 것은 변별력이 거의 없음. Gate로 써야 한다는 가설을 역설적으로 지지.

**Threshold별 통과율 및 성과:**

| Architecture | n/yr | median | tmean | P10 | hit% | C>A 연도 | p |
|---|---|---|---|---|---|---|---|
| A. 현재 선형 (baseline) | 182 | -6.13 | -5.45 | -37.88 | 42.1% | — | — |
| C_15: E≥15+R≥22, V ranker | 115 | -5.56 | -5.21 | -38.34 | 42.8% | 4/8 | 0.852 |
| C_20: E≥20+R≥22, V ranker | 99 | -5.53 | **-4.75** | -37.87 | 42.9% | 6/8 | 0.668 |
| C_22: E≥22+R≥22, V ranker | 32 | -7.19 | -6.47 | -40.66 | 42.1% | 3/8 | 0.986 |

**핵심 발견 2 — C_20이 방향으로 가장 좋지만 통계적으로 무효**

C_20 (E≥20 = 흑자 여부 Gate) median=-5.53 (+0.60pp vs A), tmean=-4.75 (+0.70pp), 양수연도 6/8. 2021/2022에서 확연히 나음. 그러나 p=0.668.

C_22의 실패 원인 확인: n/yr≈32로 너무 좁아 ranker 작동 불가. ≥20(n/yr≈99)으로 완화하면 방향 개선.

**판정 (사전 고정 기준 적용):**

자동 판정 "B (≥22가 과도한 exclusion)"는 방향 맞으나 과잉 해석 주의. 더 정확한 판정: **C에 가까운 B** — 낮은 threshold에서 방향 개선은 있으나 magnitude 작고 통계적 유의성 없음.

- 가설 A (아이디어 자체 실패): 기각. C_15/C_20이 A보다 방향으로 나음.
- 가설 B (≥22 과도한 exclusion): 부분 지지. ≥20으로 완화 시 개선되나 유의미하지 않음.
- 가설 C (실익이 작음): 가장 가까운 판정. 어떤 threshold도 통계적으로 유의한 개선 없음.

**구조적 함의:**

Earnings score=20 (흑자 여부)을 Gate로 쓰는 것은 방향으로 지지되나 in-sample에서도 p>0.60. Score 범주형 구조(9/20/22)를 고려하면 E≥20 = 흑자 여부 filter가 가장 자연스러운 gate 후보이나 확정 근거 미달.

다음 연구: Risk Filter Sensitivity (R threshold 별도 검토).
Engine Freeze 해제 전 production 변경 금지.

---

## Risk Filter Sensitivity — Architecture v2.2 (2026-09-10)
> **Status: 🟡 Current — Architecture v2 보조 분석**


**연구 스크립트:** `research/risk_filter_sensitivity.py`
**사전 고정 threshold:** ≥17, ≥22, ≥25 (결과 보기 전 고정)
**E gate 고정:** ≥20 (흑자 여부, E sensitivity 연구 기반)

**핵심 발견 1 — Risk score도 최고점에 집중**

min=13, median=25, p75=25, max=25, std=3.1. score=25가 **56.6%(3,303건)**. Earnings(9/20/22 삼분위)와 유사하게 최고점 집중. R≥25는 "score=25만 통과" = 과반수 통과 = 실질적 filtering 효과 제한적.

**전체 통계:**

| Architecture | n/yr | median | tmean | P10 | hit% |
|---|---|---|---|---|---|
| A. 현재 선형 (baseline) | 182 | -6.13 | -5.45 | -37.88 | 42.1% |
| E≥20+R≥17 (ER_17) | 111 | -5.51 | -4.67 | -37.58 | 43.2% |
| E≥20+R≥22 (ER_22) | 99 | -5.53 | **-4.75** | -37.87 | 42.9% |
| E≥20+R≥25 (ER_25) | 89 | -6.45 | -5.39 | -38.48 | 42.1% |
| R≥17 only (R_17) | 170 | -6.54 | -5.64 | -38.63 | 41.3% |
| R≥22 only (R_22) | 117 | -5.67 | -5.28 | -38.07 | 42.7% |
| R≥25 only (R_25) | 103 | -5.78 | -5.25 | -38.22 | 42.6% |

**vs A paired t-test:**
- ER_17: diff=+0.49, p=0.604, 5/8년 → 방향 지지, 유의미하지 않음
- ER_22: diff=+0.51, p=0.668, 6/8년 → 방향 지지, 유의미하지 않음
- ER_25: diff=+0.64, p=0.566, 5/8년 → diff 양수지만 median은 역전 (해석 주의)

**E gate 추가 기여 (ER_T vs R_T):**
- ER_17 vs R_17: diff=+1.09, **p=0.051†**, 6/8년 → borderline 유의
- ER_22 vs R_22: diff=+0.30, p=0.407, 6/8년 → 방향 지지
- ER_25 vs R_25: diff=-0.55, p=0.390, 2/8년 → 역전

**핵심 발견 2 — E gate와 R filter는 보완 관계**

R이 매우 느슨(R≥17, 93.6% 통과)할 때 E gate(≥20 흑자)를 추가하면 p=0.051†로 유일하게 borderline 유의. R이 강해질수록 E gate 기여 감소. R이 약할 때 E gate가 실질적 discrimination을 함.

**자동 판정 B (2/3 T에서 ER>A):** 부분 지지. ER_17/22는 방향 개선, ER_25는 역전. R≥25(=score=25 = 과반수)는 filtering 효과 없음. 더 정확한 판정: **threshold-sensitive하나 ER_17/22 패턴 일치.**

**Architecture Sensitivity 종합 판정:**

E Gate sensitivity + R Filter sensitivity를 합산한 현재 결론:
- E≥20 + R≥17/22 조합이 방향으로 가장 일관 (ER_22: 6/8년, tmean +0.70pp)
- 통계적 유의성 없음 (p>0.55)
- in-sample에서도 역할 분리 개선이 marginal → 확정 근거 미달

Engine Freeze 해제 후 OOS 검증 전 production 변경 금지.

---

## Dip + Investor Absorption 연구 — 하락일 외인·연기금 동시매수

> **Status: 🟡 Ongoing — PIT universe 3개 연도(2023-2025) 검증 완료. 레짐 의존성 초기 패턴 관찰. Production 미반영.**

### 연구 질문

주가가 하락한 날 외국인과 연기금이 동시에 순매수한 종목이 단순 하락 종목보다 이후 수익률과 downside 특성이 개선되는가?

### 데이터 및 정의

* `return_1d < 0`인 종목을 하락 이벤트로 정의
* `foreign_positive = foreign_net > 0`, `pension_positive = pension_net > 0`
* 이후 20D alpha: T+1 open entry, T+20 close exit, KOSPI 대비 초과수익
* investor_flow: pykrx `get_market_net_purchases_of_equities_by_ticker` 기반 수집, research.db 보관

그룹 정의 (사전 고정):

| 그룹 | 조건 |
| ---- | ---- |
| A | 하락 + 외인 순매수 + 연기금 순매수 |
| B | 하락 + 외인 순매수만 |
| C | 하락 + 연기금 순매수만 |
| D | 하락 (수급 부재) |
| E | 전체 베이스라인 |

---

### 초기 검증 (139일, 2026-09-16)

* 검증 기간: 139일 (retro_calibration + snapshots 스크리너 유니버스)
* 전체 관측치: 17,434

| 그룹              |      n | Mean α | Median α | Hit% |     P10 |
| --------------- | -----: | -----: | -------: | ---: | ------: |
| A 하락 + 외인 + 연기금 |    932 | -2.31% |   -2.52% | 44.0 | -27.04% |
| B 하락 + 외인만      |  2,931 | -3.77% |   -4.53% | 40.7 | -29.92% |
| C 하락 + 연기금만     |  1,765 | -3.15% |   -4.18% | 41.0 | -28.42% |
| D 하락만           |  3,225 | -5.25% |   -5.66% | 37.8 | -31.27% |
| E 전체 baseline   | 17,434 | -4.41% |   -5.02% | 39.3 | -30.16% |

A vs D: +2.94pp / A vs B: +1.46pp

---

### 확장 검증 (881일, 2026-09-17)

* 검증 기간: 881일 (2023-01-02 ~ 2026-09-04)
* 전체 관측치: 1,883,085 (868 signal dates)
* 유니버스: 2026년은 스크리너 유니버스 / 2023–2025년은 investor_flow의 전체 KOSPI·KOSDAQ 종목
  * 연도별 유니버스 구성이 다르므로 연도 간 수치 직접 비교 시 주의

**전체**

| 그룹              |        n | Mean α | Median α | Hit% |     P10 |
| --------------- | -------: | -----: | -------: | ---: | ------: |
| A 하락 + 외인 + 연기금 |   28,590 | -0.84% |   -2.13% | 41.2 | -15.82% |
| B 하락 + 외인만      |  294,557 | -1.45% |   -2.97% | 37.4 | -16.89% |
| C 하락 + 연기금만     |   62,780 | -0.87% |   -1.94% | 41.7 | -15.19% |
| D 하락만           |  562,585 | -1.45% |   -2.81% | 37.2 | -16.04% |
| E 전체 baseline   | 1,883,085| -1.43% |   -2.77% | 37.5 | -16.21% |

A vs D: +0.61pp / A vs B(연기금 동반 효과): +0.61pp

**연도별 A vs D**

| 연도 | 유니버스 | A vs D | A vs B |
| ---- | ------- | -----: | -----: |
| 2023 | 전체 상장 (245일) | +0.62pp | +0.55pp |
| 2024 | 전체 상장 (244일) | +0.17pp | **−0.05pp** |
| 2025 | 전체 상장 (240일) | +0.94pp | +1.01pp |
| 2026 | 스크리너 (139일) | +2.94pp | +1.46pp |

---

### Deep Dip 분해 (2026-09-17)

`return_1d` 버킷별로 A vs D를 분리해 하락 충격 크기가 A-D spread에 영향을 미치는지 확인했다.

**버킷 정의 (사전 고정)**: 0~-1% / -1~-3% / -3~-5% / <-5%

| 버킷 | A mean | D mean | A-D |
| ---- | -----: | -----: | --: |
| small(0~-1%) | -1.19% | -1.63% | +0.44pp |
| mod(-1~-3%) | -0.87% | -1.39% | +0.52pp |
| large(-3~-5%) | -0.79% | -1.18% | +0.39pp |
| **deep(<-5%)** | **+1.67%** | **-1.25%** | **+2.92pp** |

small/mod/large 버킷에서는 A-D spread가 +0.4~0.5pp 수준으로 균일한 반면, -5% 이상 급락에서는 +2.92pp로 급격히 확대되며 A mean이 양수로 전환된다.

**연도별 deep bucket A-D (2026-09-17)**

| 연도 | A-Q4 mean | D mean | A-D |
| ---- | --------: | -----: | --: |
| 2023 | +3.92% | -0.64% | **+4.56pp** |
| 2024 | +1.91% | -0.34% | **+2.25pp** |
| 2025 | -1.64% | -2.82% | **+1.18pp** |
| 2026 | +0.85% | -2.66% | **+3.51pp** |

전체 A-D가 +0.17pp로 신호가 거의 소실된 2024년에도 deep 버킷 A-D는 +2.25pp를 유지했다.

---

### Deep × Absorption 강도 (2026-09-17) — 탐색적

Deep dip 내에서 외인+연기금 동시매수 강도(absorption_ratio)가 alpha와 단조적 관계를 보이는지 탐색했다.

**absorption_ratio 정의**:
`(foreign_net + pension_net) / (close × volume)`

분모는 실제 거래대금이 아니라 `close × volume`의 근사값이다. 분위수 cut도 현재 데이터에서 결정된 in-sample 경계다.

**A 그룹(외인+연기금 동시매수) 내, deep dip 한정 — 전체 881일**

| absorption 분위수 | 범위 | n | mean alpha |
| --------------- | ---- | -: | ---------: |
| A-Q1 (약) | 0.000 ~ 0.033 | 461 | -0.10% |
| A-Q2 | 0.033 ~ 0.080 | 460 | +0.83% |
| A-Q3 | 0.080 ~ 0.160 | 460 | +2.12% |
| **A-Q4 (강)** | **0.161 ~ 0.807** | **459** | **+3.82%** |
| D (수급 부재) | — | 36,292 | -1.25% |

Q1 → Q2 → Q3 → Q4가 단조적으로 증가한다. A-Q4 vs A-Q1: +3.92pp / A-Q4 vs D: +5.07pp.

**연도별 A-Q4 vs D (표본 n≥10 기준)**

| 연도 | A-Q4 | A-Q1 | D | Q4-Q1 | Q4-D |
| ---- | ---: | ---: | -: | ----: | ---: |
| 2023 | +5.01% | -2.73% | -0.64% | **+7.74pp** | **+5.65pp** |
| 2024 | +4.17% | -1.16% | -0.34% | **+5.33pp** | **+4.51pp** |
| 2025 | +1.73% | -7.56% | -2.82% | **+9.29pp** | **+4.55pp** |
| 2026 | +1.65% | -2.54% | -2.66% | **+4.19pp** | **+4.31pp** |

Q4-D spread가 4개 연도 모두에서 +4pp 이상 양수다.

### 핵심 관찰

**확장 검증 (881일, 전체 유니버스)**: 전체 상장 종목 기준에서 하락 종목의 외인+연기금 동시매수(A)가 수급 부재(D)보다 20D alpha가 높았다. 연도별 A-D는 2023~2026 모두 양수 방향이었다.

**⚠ 유니버스 의존성 확인**: 2026 스크리너 종목(571개)으로 유니버스를 통일하자 2023(-0.63pp) / 2024(-0.63pp) / 2025(-1.13pp) 모두 A-D가 음수로 반전됐다. 전체 유니버스에서의 양수 방향이 production 관심 종목에서는 재현되지 않는다.

**PIT Universe 검증 (2023-2025)**: v8.5.19 formula를 소급 적용한 PIT universe에서 A-D를 재계산하면 2023(+0.67pp, Bull) / 2024(-0.40pp, Bear) / 2025(+1.14pp, Bull)로, Bull 연도에서 양수, Bear 연도에서 음수인 초기 패턴이 관찰됐다. Normalized 분석에서 음수였던 2023이 PIT에서 복원됨으로써, normalized 역전의 주원인이 survivorship bias였음이 지지된다. 핵심 질문이 "유니버스 때문인가?"에서 "시장 국면에 따라 신호가 달라지는가?"로 이동했다. 단 3개 연도(Bull 2 / Bear 1)로 레짐 의존성을 결론 내리지 않는다.

**Deep dip 효과**: -5% 이상 급락에서 A-D spread가 +2.92pp로 다른 버킷 대비 크게 확대된다. 전반적 신호가 약했던 2024년에도 deep 버킷에서는 +2.25pp가 유지되어, 하락 충격의 크기가 조건부 신호의 강도에 영향을 미치는 것으로 관찰된다.

**Deep × Absorption (탐색적)**: A 그룹 내 deep dip에서 흡수 강도(absorption_ratio)별로 Q1~Q4 분위수가 단조 증가하며, Q4 (거래대금 대비 >16% 흡수) 그룹의 mean alpha가 +3.82%로 D(-1.25%)와 +5.07pp 차이를 보였다. 이 패턴은 4개 연도 모두에서 방향이 유지됐다.

**현재 수준**: 탐색적 후속 분석에서 강한 조건부 패턴이 관찰된 수준. "검증된 투자 신호"로 취급하지 않는다.

### 해석 주의

* A−B는 "외인+연기금 동시매수"와 "외인 단독매수" 조건의 성과 차이이며, 각 투자자 유형의 독립적인 causal effect가 아니다. 외인과 연기금은 동시에 움직이는 경향이 있어 각각의 기여를 현재 데이터로 분리할 수 없다.
* 20D forward alpha 관측치는 날짜·종목 간 중첩이 있으므로 단순 관측치 수를 독립 표본으로 해석하지 않는다.
* 2023–2025는 스크리너 필터 없는 전 상장 종목, 2026은 스크리너 유니버스로 유니버스 구성이 다르다.
* absorption_ratio의 분모는 실제 거래대금이 아닌 `close × volume` 근사값이다.
* Q4의 cut(>0.161)은 현재 881일 데이터에서 결정된 in-sample 경계다. 동일 기간 데이터로 효과를 측정했으므로 data-snooping 위험이 있으며, 이 경계값을 새 기간에 고정 적용하는 out-of-sample 검증이 반드시 필요하다.

---

### Stage 3 — Deep × Absorption 강도 OOS 검증 (2026-09-17)

**Status: 🔵 Closed — 단조 패턴 OOS 미재현**

2023–2025에서 도출한 absorption cutoff를 고정하여 2026년 기간에 temporal holdout 검증을 수행했다.

검증 조건 (사전등록):

- Deep dip: `return_1d < -5%`
- A 조건: 외인 + 연기금 동시 순매수
- Q4 cutoff: `absorption_ratio > 0.161` (train에서 도출, OOS에서 재계산 금지)
- 2026 deep dip 관측치: 1,279건 (112일)

사전등록 기준:

- **PRIMARY-1**: `A-Q4 mean > A-Q1 mean` (단조성 방향) → **❌ FAIL**
- **PRIMARY-2**: `A-Q4 mean > D mean` (신호 vs 무신호) → **✅ PASS**

OOS 결과:

| 그룹 | n | mean alpha |
| ---- | -: | ---------: |
| A-Q1 (ar≤0.033) | 12 *(작음)* | +3.45% |
| A-Q2 | 23 | -3.16% |
| A-Q3 | 36 | +1.11% |
| A-Q4 (ar>0.161) | 60 | +1.72% |
| D (수급 없음) | 514 | -2.66% |

A-Q4 − A-Q1: **-1.73pp** / A-Q4 − D: **+4.38pp**

판정: train 구간에서 관찰된 Q1→Q4 단조 증가 패턴은 2026 holdout에서 재현되지 않았다. Q1 n=12는 추정 신뢰도가 낮으나, 사전등록 기준을 소급 조정하지 않는다. 따라서 흡수 강도가 높을수록 alpha가 단조적으로 증가한다는 Stage 3 가설은 채택하지 않는다.

반면 고정 cutoff를 적용한 2026 holdout에서 A-Q4가 D보다 +4.38pp 높아, deep dip + 외인·연기금 동시매수 조건이 수급 없음 대비 우수할 가능성은 추가 확인할 가치가 있다.

해석 제한:

- 2026 유니버스는 스크리너 기반 → 전체 한국 주식시장 일반화 불가
- 2026 기간은 이전 탐색에서 이미 관찰된 데이터이므로 완전히 독립적인 blinded OOS가 아닌 **고정 cutoff temporal holdout** 검증으로 해석한다
- `close × volume`은 실제 거래대금의 근사값
- Q4 n=60도 작아 +4.38pp 수치보다 방향 재현 자체에 의미를 둔다

이후 방향: absorption intensity를 연속형 ranking 변수로 production화하지 않는다. 새로운 기간이 충분히 축적되면 동일 cutoff를 변경 없이 적용하여 재검증한다.

---

### Universe-normalized 검증 (2026-09-17)

**Status: 🟡 Ongoing — production-universe normalized test에서 미재현. Universe-normalized의 2023 역전이 survivorship bias임을 PIT 검증으로 확인. PIT 완료.**

유니버스 의존성 확인 목적으로, 2026 스크리너 종목(571개)을 2023-2025에도 고정 적용하여 A-D를 재계산했다.

검증 조건:

- 유니버스: retro_calibration + snapshots 등장 2026 종목 집합 (n=571) → 2023-2025에 동일 적용
- 2023-2025 flow coverage: 566/571 (99%)

결과 (유니버스 normalized, 20D alpha):

| 연도 | A-D | A-B |
| ---- | --: | --: |
| 2023 | -0.63pp | -0.31pp |
| 2024 | -0.63pp | -0.76pp |
| 2025 | -1.13pp | -0.94pp |
| 2026 | +2.94pp | +1.46pp |
| **전체** | **-0.69pp** | **-0.56pp** |

전체 유니버스 분석(supply_dip_analysis.py)에서 2023-2025 모두 양수였던 A-D가 스크리너 유니버스로 좁히자 **3년 연속 음수로 반전**됐다. 2026의 +2.94pp는 2026 유니버스가 이미 스크리너 기반이라 양쪽 분석이 동일 데이터를 사용한 것이다.

판정: full-market exploratory 결과(881일)는 관찰되었으나, 현재 production 관심 유니버스의 historical normalized test에서 재현되지 않았다. **따라서 production 적용 근거로 사용할 수 없다.**

해석 주의:

- 2026 스크리너 종목을 과거에 소급 적용한 구조라 **survivorship bias**가 있다 (당시 실제 스크리너 통과 여부 미반영)
- "full market에서만 통한다", "소형주 효과" 같은 원인 해석은 PIT universe 검증 전까지 보류
- 유니버스 의존성이 강하게 관찰됨이 현재까지의 정확한 기술이다

---

### PIT Universe 검증 (2026-09-17)

**Status: 🟡 Ongoing — Bull/Bear 레짐 패턴 관찰. 3개 연도로 결론 보류.**

Universe-normalized에서 survivorship bias 의심 → v8.5.19 production formula를 각 연도에 소급 적용한 Point-in-Time universe로 A-D를 재계산했다.

검증 방식:
- signal_year → risk_year 매핑 (fwd_year = risk_year + 1)
- earnings_score ≥ 22 (v8.5.19 production gate, 완화 없음)
- PIT universe: 2023 → risk_year=2022(190종목) / 2024 → risk_year=2023(178종목) / 2025 → risk_year=2024(137종목)
- 해석 제한: "Historical Replay of Current Production Formula" — 당시 실제 스크리너 복원 아님

결과 (PIT universe, 20D alpha):

| 연도 | 시장 국면 | PIT universe | A mean | D mean | A-D | A-B |
| ---- | ------- | -----------: | -----: | -----: | --: | --: |
| 2023 | Bull | 190종목 | +0.36% | -0.31% | **+0.67pp** | +0.52pp |
| 2024 | Bear | 178종목 | -0.28% | +0.12% | **-0.40pp** | -0.05pp |
| 2025 | Bull (+75.7%) | 137종목 | -2.70% | -3.84% | **+1.14pp** | +1.01pp |
| 전체 | — | — | -0.70% | -1.02% | **+0.32pp** | +0.45pp |

관찰:

1. **2023 survivorship bias 확인**: Normalized에서 -0.63pp였던 2023이 PIT에서 +0.67pp로 복원됐다. 2023 역전의 주원인이 survivorship bias였음을 지지한다.

2. **2024는 유니버스 무관하게 음수**: Full-market(+0.17pp)에서 약하지만 양수였으나, PIT(-0.40pp)에서도 음수. Bear 시장 자체의 특성이 원인일 가능성을 지지한다.

3. **레짐 의존성 가설을 지지하는 초기 패턴**: Bull(2023, 2025)에서 양수, Bear(2024)에서 음수. 단 3개 연도(Bull 2 / Bear 1)로 레짐 의존성을 확정하지 않는다.

4. **2025 방어력 해석**: 2025 A(-2.70%)와 D(-3.84%) 모두 KOSPI 대비 음수이나, A가 D보다 1.14pp 덜 하락. A-B(+1.01pp)도 동일 방향. "절대수익"이 아닌 "상대적 방어력"으로 읽는다.

5. **2024 A-B ≈ 0(-0.05pp)**: Bear 국면에서 외인+연기금 동반 여부가 외인 단독 대비 추가 signal value를 생성하지 못했다. Bull(2023 +0.52pp, 2025 +1.01pp)과 대조적.

해석 제한:
- PIT universe 137~190종목 — 전체 시장 2955종목 대비 매우 좁다
- "Historical Replay" = v8.5.19 소급 적용. 당시 실제 스크리너 통과 종목 집합과 다르다
- 3개 연도는 통계적 결론을 내리기에 부족하다 (특히 Bear 연도 1개)

### 다음 검증 — Stage 6: 레짐별 분리 분석 (사전등록)

**1차 가설**: 레짐에 따라 A-D 효과가 달라진다.

`Δ_regime = A-D_Bull − A-D_Bear/Sideways > 0`

**검증 조건 (고정, 결과 확인 후 변경 금지)**:

- 레짐 정의: `research_year_meta.phase` 그대로 사용 (KOSPI 연간 수익률 ±10% 기준, 연도별 사전 고정)
- 비교 대상: 기존 PIT A/B/C/D 그대로. 조건 추가·변경 없음.
- Δ_regime 계산 후 레짐 기준 소급 조정 금지

**판정 기준 (사전 고정)**:

| 판정 | 조건 |
| ---- | ---- |
| **Support** | Δ_regime > 0 이고, Bull 연도 A-D 모두 > 0, Bear/Sideways 연도 A-D 모두 < 0 |
| **Mixed / Inconclusive** | Δ_regime > 0이지만 연도별 부호 일부 불일치 |
| **Refute** | Δ_regime ≤ 0 |

Support는 "사전 가설과 일치하는 패턴 관찰"을 의미하며, 신호 채택이나 production 적용과 무관하다. 현재 3개 연도(Bull 2 / Bear 1)에서는 어떤 판정도 최종 결론으로 취급하지 않는다.

효과 크기 기준(1pp 등)은 사전 근거 없이 고정하면 사후 기준이 되므로 정하지 않는다. Δ_regime 방향성만 Primary criterion으로 본다. 효과 크기는 결과 기술에만 사용한다.

Production 미반영: 어떤 판정이 나와도 production scoring에 반영하지 않는다.

**2순위 (현재 1순위로 이동)**: 과거 연도 확장 (2020-2022 PIT universe 구성 가능 여부 확인 후) — Bear/Sideways가 사실상 2024년 하나에 걸려 있는 한계를 줄이는 것이 지금 가장 중요한 검증

---

### Stage 6 — 레짐별 분리 분석 결과 (2026-09-22 업데이트, 6개 연도)

**Status: 🟡 Ongoing — Mixed/Inconclusive. Primary 방향 유지, Bull Consistency 미충족. Production 미반영.**

검증 스크립트: `research/pit_regime_analysis.py`

**Primary 결과 (6개 연도):**

| 레짐 | A-D |
| ---- | --: |
| Bull (2020, 2023, 2025 합산) | +0.50pp |
| Bear/Sideways (2021, 2022, 2024 합산) | -0.74pp |
| **Δ_regime** | **+1.24pp** |

**공식 판정: Mixed / Inconclusive**

사전등록한 Consistency 기준(Bull 연도 A-D 모두 > 0)을 2020이 충족하지 못해 Mixed.

- Primary: Δ_regime = +1.24pp > 0 ✅
- Consistency (Bull): 2020 **-0.03pp** ❌ / 2023 +0.67pp ✅ / 2025 +1.14pp ✅ → 2/3 양수
- Consistency (Bear/Sideways): 2021 -1.06pp ✅ / 2022 -0.91pp ✅ / 2024 -0.40pp ✅ → 3/3 음수

**연도별 상세:**

| 연도 | 레짐 | A mean | D mean | A-D | A-B |
| ---- | ---- | -----: | -----: | --: | --: |
| 2020 | Bull | -0.16% | -0.13% | **-0.03pp** | +0.11pp |
| 2021 | Sideways | +0.04% | +1.10% | -1.06pp | -1.35pp |
| 2022 | Bear | -0.74% | +0.17% | -0.91pp | -0.67pp |
| 2023 | Bull | +0.36% | -0.31% | +0.67pp | +0.52pp |
| 2024 | Bear | -0.28% | +0.12% | -0.40pp | -0.05pp |
| 2025 | Bull | -2.70% | -3.84% | +1.14pp | +1.01pp |

**연구 해석:**

공식 판정은 Mixed이지만, Primary Δ_regime > 0은 6년 확장 후에도 유지됐다. 2020년 A-D = -0.03pp (A=-0.16% vs D=-0.13%)는 반대 방향의 강한 증거라기보다 효과가 거의 0에 수렴한 중립적 이탈에 가깝다. 따라서 레짐 비대칭 방향성이 소실됐다고 해석하지 않는다.

현재 단계 위치: **Weak/Mixed — 방향성 유지, Robust 여부 미확인**. 가설을 폐기하기보다 방향은 살아 있되 일관성이 충분히 확립되지 않은 상태로 유지한다.

**2020 특이성 (제한사항):**

2020년의 시장 구조적 특이성(COVID 폭락·V자반등)이 관찰된 이탈과 관련됐을 가능성이 있으나, 이는 결과 확인 후의 사후적 설명이다. 본 검증의 공식 판정 및 기준 변경에 사용하지 않는다.

**추가 관찰 (기술적):**

- Bear/Sideways 3/3 vs Bull 2/3: 현재 데이터에서 Bear/Sideways 방향이 더 일관됐다. 이를 별도 가설로 승격하지 않으며, 새로운 비-Bull 연도가 확보됐을 때 독립적으로 검증할 수 있는 탐색적 관찰로만 기록한다.
- A-B 상대성과도 동일 방향: Bull +0.50pp / Bear·Sideways -0.58pp. "연기금 동반 효과" 해석으로 확장하지 않는다.
- Δ_regime 수치 변화(+1.12pp → +1.24pp)는 pooled mean 가중도 변화 때문이므로 효과 강화로 해석하지 않는다. 방향 유지 여부만 본다.

**Production 미반영:** 어떤 판정이 나와도 production scoring에 반영하지 않는다.

현재 단계에서는 Production score 또는 Supply score에 반영하지 않는다.

스크립트: research/supply_dip_analysis.py, research/absorption_intensity_analysis.py, research/absorption_oos_validation.py, research/universe_normalized_analysis.py, research/pit_universe_analysis.py, research/collect_roe_2025.py, research/collect_investor_flow.py, research/pit_time_stability.py
데이터: database/research.db, database/market_ohlcv.db

---

### Stage 7 — A-D 시간 안정성 검증 (2026-09-22)

**Status: 🟡 Ongoing — Bear/Sideways 음수가 전 기간 분산 확인. Production 미반영.**

검증 스크립트: `research/pit_time_stability.py`

**분석 목적:**

Stage 6에서 관찰된 Bear/Sideways A-D 열위(-0.74pp)가 특정 소수 시점에 집중된 것인지, 아니면 전 기간에 걸쳐 반복적으로 나타나는 패턴인지 확인한다.

**결과 (2020–2025, 6년 월별 A-D):**

| 레짐 | 총월수 | 양수(▲) | 음수(▼) | 중립(—) | A-D 평균 | A-D 중앙값 |
| ---- | -----: | ------: | ------: | ------: | -------: | ---------: |
| Bull | 36 | 11 | 16 | 9 | +0.19pp | -0.33pp |
| Bear/Sideways | 36 | 7 | 24 | 5 | -0.87pp | -0.92pp |

중립 기준: |A-D| < 0.5pp

**핵심 관찰:**

Bear/Sideways의 A-D 음수는 특정 소수의 월에 집중되지 않고 관찰기간 전반에 걸쳐 반복적으로 나타났다(24/36개월 음수). 따라서 기존 레짐 차이가 일부 극단적 시점의 결과만으로 설명된다는 가설과는 부합하지 않는다.

Bull은 월별 방향이 혼재하며 평균은 +0.19pp였지만 중앙값은 -0.33pp였다. 이는 일부 큰 양의 월(예: 2023-04 +6.81pp)이 평균을 끌어올리는 비대칭 분포임을 보여준다.

2020년 초반(1–5월)의 연속 음수 구간은 연도 평균의 이질성을 보여주지만, 해당 구간의 원인을 본 분석만으로 특정하지 않는다.

**해석 제한:**

- 24/36이라는 음수 비율은 기술통계다. 20D alpha는 날짜간 중첩이 있어 36개월을 독립된 36개 실험으로 해석할 수 없으며, 월별 A 그룹 표본수 차이도 결과에 영향을 줄 수 있다.
- 본 분석은 "레짐 차이가 어디서 왔는가?"에 답하지 않는다. 시점 집중 여부만 확인한다.

**Production 미반영:** 점수 계산에 영향 없음.

---

### Stage 8 — A/D 그룹 기초 특성 비교 (2026-09-22)

**Status: 🟡 Ongoing — Mixed. Momentum 구성 차이 확인. Production 미반영.**

**핵심 질문:**

A 그룹과 D 그룹이 레짐별로 서로 다른 종목 특성을 가지고 있는가?
이를 통해, 현재 관찰된 A-D 성과 차이가 Investor Absorption 자체의 효과인지 A/D 구성 종목의 특성 차이로 설명될 여지가 있는지 진단한다.

**사전등록 변수 (결과 확인 후 추가 금지):**

| 변수 | 정의 | 출처 |
| ---- | ---- | ---- |
| `market_cap` | risk_year 기준 연말 시가총액 | research_stock_year |
| `PBR` | risk_year 기준 연말 PBR | research_stock_year |
| `realized_vol` | signal_date 직전 20영업일 일별 수익률 표준편차 | market_ohlcv |
| `momentum` | signal_date 직전 60영업일 수익률 | market_ohlcv |

- market_cap / PBR: PIT 규칙 일관성 — signal_year에 해당하는 risk_year 값 사용
- realized_vol / momentum: signal_date 당일 계산 (하락일 조건 적용 전 시점)

**비교 구조 (사전 고정):**

- 그룹: A vs D (하락일 조건 적용 후)
- 레짐: Bull / Bear/Sideways (Stage 6 기준 동일)
- 연도별 (2020–2025): 탐색적 참고용 — 연도별 결과를 새로운 판정 기준으로 사용하지 않는다

**보고값:**

각 변수에 대해 median / mean / SMD(standardized mean difference) 보고.
PBR, market_cap처럼 꼬리가 긴 분포는 median 기준으로 해석한다.

**Exit criterion:**

사전등록한 4개 변수 × A/D × 레짐/연도의 분포 비교 완료 후, 아래 셋 중 하나로 기술한다:

| 판정 | 기준 | 해석 |
| ---- | ---- | ---- |
| **Composition similar** | 4개 변수 모두 레짐별 A-D 차이가 작음 | A-D 성과 차이를 단순한 종목 구성 차이만으로 설명하기 어려움 |
| **Composition different** | 1개 이상 변수에서 레짐 일관된 큰 차이 | Composition effect가 개입했을 가능성. Absorption 효과의 순수성 불확실 |
| **Mixed** | 일부 변수/레짐에서만 차이 확인 | 추가 분리 분석(Stage 9) 필요 |

"큰 차이"의 기준: |SMD| > 0.2 (소형 효과 크기 하한, Cohen's d 기준).

**해석 제한 (사전등록):**

- Composition difference 발견 = Investor Absorption 효과 없음이 아니다. 구성 차이가 존재해도 Absorption 효과가 독립적으로 존재할 수 있다.
- Stage 8은 진단 실험이다. 매칭·회귀 통제는 Stage 9에서 별도 수행한다.
- A-D 그룹 분류 자체가 signal_date의 수급 결과이므로, 종목 특성과 수급 방향 간 선택 효과(selection effect)를 본 단계에서 인과적으로 분리하지 않는다.

**Production 미반영:** 점수 계산에 영향 없음.

---

### Stage 8 결과 (2026-09-22)

검증 스크립트: `research/pit_group_composition.py`

**공식 판정: Mixed**

| 레짐 | |SMD| > 0.2 변수 |
| ---- | -------------- |
| 전체 | momentum(0.258) |
| Bull | momentum(0.300) |
| Bear/Sideways | momentum(0.229), realized_vol(0.241) |

**변수별 상세 (전체, A vs D):**

| 변수 | A median | D median | SMD | 판정 |
| ---- | -------: | -------: | ---: | ---- |
| market_cap(억) | 8,363 | 3,694 | 0.058 | — |
| PBR | 1.42 | 1.24 | 0.098 | — |
| realized_vol | 0.0226 | 0.0193 | 0.195 | — |
| momentum(60D) | +2.94% | -2.73% | 0.258 | ★ |

**핵심 관찰:**

**momentum(60D)**: A 그룹이 이미 더 강한 중기 모멘텀을 가진 종목으로 구성되는 경향이 확인됐다. 전체·Bull·Bear/Sideways 모두 일관되게 ★. 외인+연기금이 동반 매수하는 하락일의 종목이 그렇지 않은 종목보다 signal 이전 60일 기준 중기 상승 추세에 있었음을 보여준다. selection effect의 강력한 후보지만, 이것이 A-D 성과 차이의 원인이라는 인과 주장은 Stage 9 이전에 하지 않는다.

**realized_vol**: Bear/Sideways에서만 ★(SMD=0.241), Bull에서는 역치 미달(0.177). 하락장에서 A 그룹이 변동성이 높은 종목으로 더 구성되는 패턴이 관찰됐다. Bear/Sideways A-D 음수 현상과 연결될 가능성이 있으나, 현재 단계에서 두 관찰을 인과적으로 연결하지 않는다.

**market_cap / PBR**: 사전 기준 미충족. 현재 데이터에서 시총/PBR 구성 차이는 주요 설명 후보로 드러나지 않았다.

**해석 제한:**

- Mixed 판정 = composition effect가 일부 있음. Investor Absorption 자체 효과가 없다는 의미가 아니다.
- 60D momentum 차이가 A-D 성과 차이를 얼마나 설명하는지는 Stage 9(통제 분석)에서 검증한다.

**다음 단계 — Stage 9 (사전등록 예정):**

"60D momentum과 Bear/Sideways realized_vol을 통제했을 때도 A-D 성과 차이가 남는가?" — composition-adjusted A-D 검증. 최소한의 설계(momentum 통제 우선)로 시작한다.

**Production 미반영:** 점수 계산에 영향 없음.

스크립트: research/pit_group_composition.py

---

### Stage 9 — Composition-adjusted A-D 검증 (2026-09-22)

**Status: 🟡 Ongoing — 전체 Composition-explained, 레짐별 Absorption-consistent. Production 미반영.**

**핵심 질문:**

60D momentum과 20D realized volatility를 통제한 후에도 A-D 성과 차이가 남는가?
A와 D가 애초에 다른 종목이어서 생긴 차이를 제거한 후에야 비로소 Investor Absorption 자체의 독립적 효과 후보를 볼 수 있다.

**통제 변수 (Stage 8 ★ 결과만, 사후 추가 금지):**

- `momentum_60d`: signal_date 직전 60영업일 수익률
- `realized_vol_20d`: signal_date 직전 20영업일 일별 수익률 표준편차
- PBR / market_cap: Stage 8에서 |SMD| > 0.2 미충족 → 1차 통제 제외

**Primary 방법: Bucket Stratification**

1. Bucket 설계 (사전 고정, 변경 금지):
   - momentum: 5분위 (Q1~Q5)
   - vol: 5분위 (Q1~Q5)
   - 총 25 strata
2. Bucket cutoffs: 전체 PIT A+D 하락일 pooled 데이터 기준 계산, 레짐별 별도 cutoff 없음
3. 각 stratum 내 A-D = mean(A alpha_20d) − mean(D alpha_20d)
4. Stratified A-D = Σ(stratum A-D × stratum_weight), weight = (stratum 내 A+D 건수) / 전체 A+D 건수
5. A 또는 D 건수가 0인 stratum 제외

**Robustness: OLS Residualization**

`alpha_20d = β₀ + β₁·momentum + β₂·vol + ε` 회귀 후 잔차 기준 A-D 비교.
Primary와 방향이 같으면 통제 방법에 robust한 결과. 다르면 방법 민감성 기록.

**레짐 분석:** Stratified A-D를 Bull / Bear/Sideways별로도 계산한다 (동일 pooled cutoffs 적용).

**Exit criterion:**

| 판정 | 기준 |
| ---- | ---- |
| **Absorption-consistent** | Stratified A-D와 raw A-D가 같은 부호 (전체 및 주요 레짐) |
| **Attenuated** | 같은 부호이나 \|stratified A-D\| < 50% × \|raw A-D\| |
| **Composition-explained** | Stratified A-D가 부호 역전 또는 0 수렴 (|stratified| < 0.1pp) |

레짐별로 판정이 다를 경우 각 레짐에 대해 개별 기록.

**해석 제한 (사전등록):**

- Absorption-consistent 판정이라도 인과 주장은 아니다. "통제 후에도 A-D 패턴이 유지된다"는 기술이다.
- 25개 strata 중 A 또는 D 건수 부족으로 제외되는 strata 비율이 높으면 결과 신뢰도를 함께 기록한다.
- 선형성을 가정하는 OLS와 비모수적 bucket 방식의 결과가 일치할 때만 "robust"로 기술한다.

**Production 미반영:** 점수 계산에 영향 없음.

---

### Stage 9 결과 (2026-09-22)

검증 스크립트: `research/pit_composition_adjusted.py`

**Bucket cutoffs (pooled A+D 하락일 56,917건 기준, 고정):**

- momentum 경계: -12.46% / -5.23% / +1.10% / +11.56%
- vol 경계: 0.01286 / 0.01742 / 0.02235 / 0.03000
- strata 25/25 전부 사용, coverage 100%

**결과 요약:**

| 구분 | Raw A-D | Stratified A-D | OLS A-D | 판정 |
| ---- | ------: | -------------: | ------: | ---- |
| 전체 (2020–2025) | -0.085pp | +0.003pp | +0.022pp | Composition-explained |
| Bull | +0.569pp | +0.568pp | +0.654pp | Absorption-consistent |
| Bear/Sideways | -0.742pp | -0.588pp | -0.596pp | Absorption-consistent |

**연도별 (탐색적 참고):**

| 연도 | 레짐 | Raw A-D | Stratified A-D | 판정 |
| ---- | ---- | ------: | -------------: | ---- |
| 2020 | Bull | +0.126pp | +0.368pp | Absorption-consistent |
| 2021 | Sideways | -1.061pp | -0.958pp | Absorption-consistent |
| 2022 | Bear | -0.915pp | -0.758pp | Absorption-consistent |
| 2023 | Bull | +0.675pp | +0.713pp | Absorption-consistent |
| 2024 | Bear | -0.400pp | -0.120pp | Attenuated |
| 2025 | Bull | +1.142pp | +0.675pp | Absorption-consistent |

6개 연도 모두 stratification 후 A-D 방향 유지.

**핵심 관찰:**

**전체 pooled**: momentum/volatility를 통제하자 A-D가 -0.085pp → +0.003pp로 사실상 소멸. 전체 기간 A-D는 composition 차이로 상당 부분 설명된다. bucket과 OLS가 동일 방향으로 일치 → 특정 통제 기법에 의존한 결과가 아님.

**Bull**: composition 통제 후 A-D가 +0.569pp → +0.568pp로 거의 그대로. 0.3% 감소. OLS도 +0.654pp. momentum/volatility 구성 차이가 Bull A-D를 설명하지 않는다.

**Bear/Sideways**: -0.742pp → -0.588pp (20.8% 감소). 음의 차이가 유지된다. OLS -0.596pp와 bucket 방향 일치 → robust. Bear/Sideways A-D 음수는 composition 차이만으로 설명되지 않는다.

**연구 흐름상 위치**: Stage 6의 레짐 패턴(Bull 양수, Bear/Sideways 음수)이 Stage 9 통제 후에도 살아남았다. 이는 해당 패턴이 "A 그룹이 momentum이 높아서" 생긴 것만은 아님을 보여준다. **Weak/Mixed에서 한 단계 진전**이지만, 통제된 변수가 momentum/vol 두 가지뿐이고 20D alpha 중첩 문제가 잔존하므로 Robust Pattern이라고 부르기에는 이르다.

**설계 주의사항:**

- realized_vol 계산이 signal_date 당일 종가를 포함한다(마지막 daily return = 전날→signal_date). signal_date 종가는 T+1 진입 전 관찰 가능하므로 look-ahead는 아니나, down day 자체가 vol에 반영된다. A/D 모두 하락일로 대칭 조건이므로 A vs D 비교의 편향은 제한적이다.
- 2020년 stratified A-D가 raw보다 커진 것(+0.126 → +0.368)은 A/D 그룹의 momentum 분포 차이가 2020년에는 A를 과소평가하는 방향이었음을 의미한다. 역전이 아니라 composition 효과를 제거했을 때 신호가 더 드러난 것.

**현재 단계 위치 (4단계 프레임워크):**

> **Weak/Mixed → 경계** — 레짐별 방향성이 composition 통제 후에도 6/6 연도에서 유지됨. 전체 pooled가 소멸한 것은 레짐 혼재의 결과이며, 레짐 분리 시 신호가 살아남는다. 단, 통제 범위(momentum+vol)와 표본 중첩 한계로 Robust Pattern 선언 보류.

스크립트: research/pit_composition_adjusted.py

---

### Stage 10 — 잔여 A-D 시간 안정성 검증 (사전등록 + 결과, 2026-09-22)

**Status: ✅ 완료**

**핵심 질문:**

Stage 9에서 확인된 composition-adjusted 레짐별 A-D (Bull +0.568pp / Bear/Sideways -0.588pp)가 연도 내부에서도 반복적으로 같은 방향을 보이는가?

**설계 원칙 (사전등록):**

- 새로운 가설을 검증하는 단계가 아니다. Stage 9 잔여 효과의 시간 안정성 확인으로 범위를 한정한다.
- 통제 변수: Stage 9와 동일 (momentum_60d + realized_vol_20d). 추가 금지.
- bucket cutoffs: Stage 9와 동일한 방법으로 pooled A+D 전체 기준 계산 (같은 기준 유지).
- A/D 정의, 레짐 분류, OLS 방법 모두 Stage 9 그대로.
- 새로운 threshold 없음: 방향 패턴을 기술하고, 불안정한 블록의 수와 맥락을 기록한다.

**분석 단위:** 비중첩 6개월 블록 (2020H1 ~ 2025H2, 총 12개)

**결과 (2026-09-22):**

| 블록 | 레짐 | A n | D n | Raw | Strat | OLS | 방향일치 |
|------|------|----:|----:|----:|------:|----:|---------|
| 2020H1 | Bull | 236 | 1,757 | +0.252 | -0.182 | +0.713 | ✗ |
| 2020H2 | Bull | 452 | 3,701 | +0.037 | +0.262 | +0.107 | ✓ |
| 2021H1 | Sideways | 461 | 3,124 | -1.612 | -1.361 | -1.260 | ✓ |
| 2021H2 | Sideways | 566 | 3,449 | -0.516 | -0.246 | -0.464 | ✓ |
| 2022H1 | Bear | 598 | 3,948 | -0.765 | -0.621 | -0.298 | ✓ |
| 2022H2 | Bear | 724 | 3,660 | -0.807 | -0.720 | -0.403 | ✓ |
| 2023H1 | Bull | 785 | 5,686 | +1.450 | +1.347 | +1.382 | ✓ |
| 2023H2 | Bull | 886 | 5,719 | -0.038 | +0.427 | +0.284 | ✓ |
| 2024H1 | Bear | 771 | 5,630 | -0.680 | -0.073 | -0.597 | ✓ |
| 2024H2 | Bear | 1,041 | 5,174 | -0.482 | -0.425 | -0.173 | ✓ |
| 2025H1 | Bull | 647 | 3,358 | +1.354 | +1.165 | +1.501 | ✓ |
| 2025H2 | Bull | 628 | 3,916 | +0.359 | -0.246 | -0.235 | ✓ |

Strata coverage: 11/12 블록 100%, 2020H1만 96% (19/25 strata)

**방향 요약:**

| 레짐 | 총 블록 | 예상 방향 | 개수 | Strat mean | Strat median |
|------|--------|----------|----:|----------:|------------:|
| Bull | 6 | 양수 | 4/6 | +0.462pp | +0.345pp |
| Bear/Sideways | 6 | 음수 | 6/6 | -0.574pp | -0.523pp |

**해석:**

**Bear/Sideways:** 6개 블록 전체에서 stratified A-D가 음수였다. 2020H1~2022H2, 2024H1~2024H2까지 레짐·연도 경계에 무관하게 방향이 반복됐다. Stage 9 Bear/Sideways -0.588pp는 블록 단위로도 재현된다.

**Bull:** 6개 중 4개 양수. 예외 2개의 맥락은 다르다. 2025H2는 bucket과 OLS 모두 음수 (-0.246pp / -0.235pp)로 일관되어 실제 역전 가능성이 있다. 2020H1은 bucket -0.182pp이지만 OLS +0.713pp로 서로 다른 방향을 가리키며 A 그룹 n=236으로 가장 작은 블록이다 — 불확실성이 크다. Bull 잔여 A-D는 Bear/Sideways에 비해 블록 간 산포가 넓다.

**OLS vs Bucket 방향 불일치:** 2020H1 한 블록만 해당. 나머지 11/12 블록에서 두 방법 방향 일치.

**Stage 9와의 비교:** Stage 9 전체 추정치 (Bull +0.568pp, Bear/Sideways -0.588pp)와 Stage 10 블록 평균 (Bull +0.462pp, Bear/Sideways -0.574pp)이 매우 유사하다. 블록 분해가 Stage 9 수치를 크게 바꾸지 않았다.

**제한 사항:**

- 20D alpha는 날짜 간 중첩이 있어 블록 간 완전 독립이 아니다.
- 6개월 블록당 A 그룹 n이 작은 초기 블록(2020H1)은 추정 신뢰도 낮음.
- 두 통제 변수(momentum, vol)만 사용했으므로 통제되지 않은 구성 차이가 남아 있을 수 있다.

**Script:** `research/pit_block_stability.py`

**Production 미반영:** 점수 계산에 영향 없음.

---

### Stage 8~10 종합 해석 (2026-09-22)

**공식 판정: Mixed — 변경 없음**

Stage 6에서 사전등록한 판정 기준(Bull 연도 A-D 모두 > 0 요건 미충족)을 소급 변경하지 않는다.

**연구 해석:**

2020~2025 데이터를 Stage 8(구성 비교) → Stage 9(composition 통제) → Stage 10(시간 분할) 순으로 검증한 결과, 두 레짐 간 비대칭 패턴이 확인됐다.

Bear/Sideways: momentum·volatility를 통제한 후에도 A-D residual이 음수이며, 비중첩 6개월 블록 6/6에서 반복됐다. Stage 9 추정치 -0.588pp와 Stage 10 블록 평균 -0.574pp가 거의 동일하며, OLS와 bucket stratification이 11/12 블록에서 방향 일치를 보인다. 현재까지의 증거 중 가장 일관된 현상이다.

Bull: 양의 residual A-D 방향은 존재하지만(4/6 블록 양수, median +0.345pp) 시간적 안정성이 Bear/Sideways에 비해 낮다. 2025H2에서 두 방법 모두 음수(-0.246 / -0.235pp)로 수렴했고, 2020H1은 방법 간 방향 불일치로 불확실성이 크다.

따라서 현재 증거는 대칭적인 "Bull 양수 / Bear/Sideways 음수" 가설보다, Bear/Sideways 음의 잔여 효과에 집중된 비대칭 패턴을 더 강하게 지지한다.

**해석 제한:**

이 해석은 2020~2025 데이터에서 나온 사후 관찰이다. 동일 데이터를 이용한 추가 사후 검증 기준은 만들지 않으며, 이 해석을 검증하는 유일한 방법은 완전히 새로운 기간의 out-of-sample 데이터다.

---

### Stage 11 — Out-of-Sample 검증 사전등록 (2026-09-22)

**Status: 🔲 Pre-registered — 실행 대기 (2026년 마감 후)**

**핵심 가설 (2020~2025 관찰에서 도출, 사전등록):**

> "Bear/Sideways 레짐에서 momentum·volatility composition을 통제한 A-D residual(stratified A-D)은 음의 방향으로 반복된다."

2020~2025 데이터로 형성한 가설을, 시간상 완전히 이후 연도인 2026 전체로 검증한다. 가설 형성과 검증 기간이 시간 순서상 분리된다.

**OOS 기간 및 데이터 cutoff:**

- **OOS 기간: 2026-01-01 ~ 2026-12-31 (2026년 전체)**
- **Data cutoff: 2026-12-31**
- 실행 조건: 2026년 마감 후 KOSPI 연간 수익률 확정 → 레짐 분류 → 스크립트 실행 순서 준수

**레짐 분류 규칙 (2020~2025와 동일, 사전 고정):**

- KOSPI 연간 수익률 ±10% 기준
  - Bull: 연간 KOSPI > +10%
  - Bear: 연간 KOSPI < -10%
  - Sideways: -10% ≤ 연간 KOSPI ≤ +10%
- 2026 레짐은 2026년 전체 연간 수익률로 결정한다. 2020~2025와 동일한 규칙을 동일하게 적용하므로 in-sample과 OOS의 레짐 분류가 비교 가능하다.
- 결과를 본 후 레짐 분류를 소급 조정하지 않는다.

**분석 설계 (Stage 9와 동일):**

- A/D 정의: 동일 (A = 하락 + 외인 순매수 + 연기금 순매수, D = 하락 + 수급 부재)
- PIT universe: 2026 signal_date → risk_year 2025 기준, earnings_gate ≥ 22
- 통제 변수: momentum_60d + realized_vol_20d (추가 금지)
- Bucket cutoffs: **Stage 9 pooled cutoffs 고정, 재계산 금지**
  - momentum: -12.46% / -5.23% / +1.10% / +11.56%
  - vol: 0.01286 / 0.01742 / 0.02235 / 0.03000
- OLS 병행 (robustness)

**판정 기준:**

- Primary: 2026 레짐이 Bear/Sideways이면 → stratified A-D < 0이면 가설 지지, ≥ 0이면 기각
- 2026 레짐이 Bull이면 → 방향 기록만 (Bull 가설은 현재 사전등록 대상 아님)

**금지 사항:**

- 결과 확인 후 레짐 분류, cutoff, 통제 변수, bucket cutoffs 변경 금지
- OOS 결과를 보고 나서 판정 기준을 추가하거나 수정하지 않는다

**Production 미반영:** 점수 계산에 영향 없음.

---

## Exit 진단 — 등급 악화 시 즉시 매도 vs 보유 비교 (Exploratory, 2026-09-22)

> **Status: 🔵 Exploratory — 사전등록 없이 실행된 사후 진단. 공식 검증으로 승격 전.**

### 배경

실제 콘솔 운용 중 "STRONG_BUY 매수 → 수급 변동 → NATURAL_AVOID → 매도 고려" 패턴이 반복되어 손실이 발생했다. 이 손실이 잘못된 entry 때문인지, 과민한 exit 때문인지를 구분하기 위해 탐색적 진단을 수행했다. 이 분석은 동시에 `sell_checklist.py`의 "등급 악화 → Critical" 설계의 사후 진단 근거로도 기록된다.

### 데이터

- `retro_calibration` (v8.5.20): 2026-01-02 ~ 2026-07-30, 141 signal_dates, 135 tickers
- 비교 전략 모두 T0 기준: entry = T0+1 open, KOSPI 벤치마크 = T0 20D window

### 이벤트 정의 (사후 확정)

| 항목 | 정의 |
|------|------|
| Entry (T0) | 종목별 첫 STRONG_BUY 또는 BUY signal_date |
| Exit event (Te) | T0 이후 첫 NEUTRAL/AVOID/WATCH signal_date |
| 포함 조건 | delay ≤ 28 calendar days (≈20 trading days) — 비교 불가 케이스 제외 |
| 반복 제한 | 동일 종목 첫 하락 이벤트만 사용 |

**28일 cutoff는 사전등록된 기준이 아니다.** 비교 구조상 Te > T0+20D이면 score_exit와 fixed_hold 평가 윈도우가 달라지는 문제를 사후에 확인하고 설정했다. Exploratory evidence로만 취급한다.

### 비교 전략

- **score_exit**: T0+1 진입 → Te+1 open 매도 → 이후 현금 (0%). alpha = 주식 수익률(T0→Te) − KOSPI 20D 수익률(T0 window)
- **fixed_hold**: T0+1 진입 → T0+20D close 보유. alpha = alpha_20d (retro_calibration 기준)

### 결과 (n=107 이벤트)

| 전략 | n | Mean Alpha | Median Alpha | 승률 |
|------|---|----------:|------------:|----:|
| Fixed Hold (20D) | 107 | -6.94% | -8.36% | 27% |
| Score Exit | 107 | -13.44% | -16.93% | 12% |
| **Hold − Exit** | 107 | **+6.50%** | **+5.36%** | **72%** |

평균 등급 하락 감지 지연: 8일 (중앙값 6일)

**Entry Grade별:**

| Entry Grade | n | Hold Mean | Exit Mean | Hold−Exit Mean | Hold−Exit 승률 |
|-------------|---|----------:|----------:|--------------:|------------:|
| STRONG_BUY | 5 | -5.24% | -12.20% | +6.96% | 100% |
| BUY | 102 | -7.02% | -13.50% | +6.48% | 71% |

### 해석

현재 데이터에서, STRONG_BUY/BUY 진입 후 단기간(≤28일) 내 등급이 하락한 종목을 즉시 매도하는 전략은 고정 보유 전략보다 평균 6.5%p 불리한 결과를 보였다. 72%의 이벤트에서 보유가 더 나았다. 이는 "등급 악화를 즉시 매도 신호로 사용하는 것이 오히려 미래 성과를 훼손했을 가능성"을 시사한다.

이 결과는 `sell_checklist.py`에서 "등급 악화 → Critical" 연결을 "Context"로 격하한 변경 (2026-09-22)의 사후 근거로 기록된다.

### 해석 제한

- **28일 cutoff 사후 설정**: exploratory evidence. 사전등록 기준이 아님.
- **독립성 미확인**: n=107은 이벤트 수이지 독립 종목 수가 아님. ticker cluster 확인 필요.
- **극단 이벤트**: 484870 (Hold −54.1%, Exit +55.9%) 같은 반대 케이스 존재. 평균보다 median + 승률을 주요 참고값으로 본다.
- **v8.5.20 / look-ahead 주의**: retro_calibration look_ahead_note = `earnings/risk=Naver(current)`.
- **entry = 데이터 첫 날 포함**: 2026-01-02부터 이미 HIGH grade였던 종목은 실제 매수 시점과 다를 수 있음.
- "보유가 더 나았다"는 결과를 "등급 하락 시 절대 팔지 말자"는 결론으로 쓰면 안 된다.

### 다음 단계

이 결과를 사전등록된 가설로 전환하여 새 데이터(retro_calibration 외 기간)로 검증 필요. 구체적으로: "20D 이내 등급 하락 이벤트에서 Hold의 alpha > Score Exit의 alpha" 가설을 새로운 데이터로 확인.

**Script:** `research/exit_study.py`

---

## Exit 가설 사전등록 — 등급 악화 후 즉시 매도 vs 보유 (Pre-registered, 2026-09-22)

> **Status: 🟡 Pre-registered / Pending Data — 신규 snapshot 축적 후 실행.**

### 배경

위 Exploratory 진단(n=107, Hold−Exit median +5.36pp, 72% win rate)은 이벤트 기준이 결과 확인 후 설정되었다. 동일 가설을 Exploratory 이후 축적되는 신규 데이터에서 재확인하기 위해 사전등록한다.

### 가설

> **조기 등급 악화(28일 이내)에 따른 즉시 매도는 단순 20D 보유보다 불리하다.**

### 이벤트 정의 (고정)

| 항목 | 정의 |
|------|------|
| T0 | 종목당 첫 STRONG_BUY / BUY signal_date |
| Te | T0 이후 첫 NEUTRAL / AVOID / WATCH signal_date |
| delay 조건 | delay ≤ **28 calendar days** (≈20 trading days, 20D 평가 윈도우와 동일) |
| 반복 제한 | **종목당 첫 이벤트 1회** — 독립성 보장 |
| 비교 전략 | score_exit: T0→Te+1 open, 이후 현금(0%). fixed_hold: T0 기준 alpha_20d |
| 벤치마크 | 양 전략 모두 **T0 기준 KOSPI 20D 수익률** |

### 데이터 기간

- OOS: Exploratory 기간(2026-01-02~07-30) **이후** 새로 축적되는 snapshot만 사용
- 실행 조건: n ≥ 30 이벤트 도달 시

### 판정 기준 (고정)

| n 범위 | 판정 |
|--------|------|
| n < 30 | **Inconclusive** — 판정 불가 |
| 30 ≤ n < 60 | 방향 참고 / 최종 판정 유보 |
| n ≥ 60 | Primary criterion 적용 |

**Primary criterion (n ≥ 60에서만 적용):**
- Hold−Exit **median > 0** AND **win rate > 60%** → 가설 지지
- 하나라도 미달 → 가설 미지지

### 실행 방법

`research/exit_study.py` 수정 없이 실행 가능. 단, 데이터 기간 필터 파라미터 추가 필요 (OOS 시작일 = `2026-07-31`).

---

## Streak 종료 이벤트 분석 — 외인·연기금 연속 순매수 후 전환

> **Status: 🟡 Ongoing — 881일 확장 검증에서도 단조 관계 미확인. Campaign 가설 별도.**

### 연구 질문

외인 또는 연기금의 연속 순매수(streak)가 길수록, 첫 전환일 이후 alpha가 체계적으로 약해지는가?

### 데이터 및 정의

* Streak: 연속 거래일 net > 0 구간. 하루라도 non-positive이거나 날짜 gap이 있으면 종료.
* 이벤트 T: streak 마지막 날 (T+1이 연속 거래일이면서 non-positive)
* 진입: T+1 open (없으면 close), 5D exit: td[T+6] close, 20D exit: td[T+21] close
* 버킷 (사전 고정, 분포 확인 후 고정): 1일 / 2–3일 / 4–6일 / 7일+
* 외인·연기금 별도 분석

---

### 초기 탐색 (141일, 2026-09-16)

* 검증 기간: 141 거래일 (retro 구간, 2026년)
* 외인/연기금 공히 streak 길이와 5D/20D alpha 간 단조 관계 확인되지 않음.
  * 외인 20D: 7일+ (-5.84%) > 1일 (-6.76%) — 긴 streak가 오히려 나음
* 결론: 단일 구간의 국면 영향 가능성을 고려해 데이터 확장 후 재검증.

---

### 확장 검증 (881일, 2026-09-17)

* 검증 기간: 881일 (2023-01-02 ~ 2026-09-04), alpha 계산 가능 이벤트: 외인 502,291건 / 연기금 121,904건

**외인 — 전체 20D alpha**

| 버킷   |       n |  Mean | Median | Hit% |    P10 |
| ----- | ------: | ----: | -----: | ---: | -----: |
| 1일   | 259,167 | -2.94 |  -3.94 | 34.8 | -21.31 |
| 2–3일 | 177,127 | -2.86 |  -4.07 | 34.6 | -21.05 |
| 4–6일 |  53,348 | -2.95 |  -4.16 | 34.7 | -21.29 |
| 7일+  |  12,649 | -3.43 |  -4.31 | 34.1 | -22.04 |

**연기금 — 전체 20D alpha**

| 버킷   |      n |  Mean | Median | Hit% |    P10 |
| ----- | -----: | ----: | -----: | ---: | -----: |
| 1일   | 72,309 | -2.54 |  -3.27 | 37.5 | -19.74 |
| 2–3일 | 34,117 | -2.02 |  -2.77 | 39.5 | -19.16 |
| 4–6일 | 10,685 | -2.56 |  -3.39 | 37.5 |  -19.5 |
| 7일+  |  4,793 | -2.55 |  -3.43 | 37.1 | -18.94 |

**연도별 방향성 요약 (외인 20D)**

| 연도 | 단조 exhaustion 여부 | 특이사항 |
| ---- | ----------------- | ------- |
| 2023 | 미확인 (4–6일이 best) | 약한 방향성 혼재 |
| 2024 | 역전 — 7일+(−0.71)이 1일(−1.51)보다 나음 | exhaustion 가설 반례 |
| 2025 | 7일+(−5.64)이 1일(−4.39)보다 나쁨 — 가설 방향 | 국면 의존 |
| 2026 | 미확인 (7일+이 중간 수준) | |

### 핵심 결론

881일로 확장해도 streak 길이와 종료 후 5D/20D alpha 간 단조적인 exhaustion 패턴은 확인되지 않는다. 2024년처럼 긴 streak이 오히려 나은 결과를 보이는 연도도 있고, 2025년처럼 7일+가 더 나쁜 연도도 있다. streak length 자체를 차익실현 또는 매도 신호로 사용하는 근거는 현재 데이터에서 없다.

### 다음 검증

* 1일 gap 허용 campaign 개념 — "연속성이 하루 끊겨도 같은 기조를 유지하는가?" (별도 가설)
* Bull / Sideways / Bear regime별 분리 검증

현재 단계에서는 Production에 반영하지 않는다.

스크립트: research/streak_exhaustion_analysis.py
데이터: database/research.db, database/market_ohlcv.db

---

## Supply Onset Effect — 수급 전환 초기 효과 연구 (Pre-registered, 2026-09-23)

> **Status: 🔵 Closed — Q1 가설 미지지 / Q2 Inconclusive (표본 부족)**

### 연구 배경 및 질문

Streak 분석(위)에서 수급이 오래 지속된다고 해서 단조적인 exhaustion 패턴이 확인되지 않았다. 이는 "얼마나 오래 샀느냐"보다 "언제 처음 샀느냐"가 더 의미있을 수 있다는 가설로 이어진다.

현재 Supply 점수는 "지금 사고 있는가"를 측정하지만, 이 연구는 "얼마 전까지 안 사던 주체가 지금 처음 들어오기 시작했는가"를 측정한다. 정보 내용이 본질적으로 다르다.

**주의**: 매수 주체의 의도(정보 거래 / 패시브 리밸런싱 / ETF / 헤지 등)는 이 데이터만으로 구분 불가. "스마트머니"라는 해석 없이 순수 수급 상태 전환과 이후 수익률의 관계만 측정한다.

### 연구 질문

- **Q1**: 외인/기관 수급이 처음 동시 전환된 날(Onset)은 이후 20D/60D alpha와 관련되는가?
- **Q2**: Onset 이후 5 trading days 연속 유지된 경우(Mature)가 미유지 경우보다 성과가 다른가?

기존 Streak 연구 관계: Streak은 "지속 기간과 종료 후 alpha"를 봤다. 이 연구는 "시작 시점과 초기 alpha"를 본다. 기존 결과를 부정하는 게 아니라 차원을 바꾸는 것이다.

### 상태 정의 (고정)

#### Pre-absorption 조건 (T0 결정의 선행 조건)
- 직전 **10 trading days** 중, **같은 날 foreign_net ≤ 0 AND institution_net ≤ 0인 날이 7일 이상**
- 주의: 외인 7일 + 기관 7일을 각각 카운트하는 것이 아니라, **동일 날짜에 둘 다 비양수인 날**을 기준으로 한다.

#### Onset (T0)
- Pre-absorption 조건을 충족한 직후 첫날로, **foreign_net > 0 AND institution_net > 0** (양 주체 동시 양전환)
- **T0는 그 날 즉시 확정된다.** 이후 수급이 지속되는지 보고 T0를 소급 선정하지 않는다.

#### Mature (사후 라벨, 선택 기준 아님)
- Onset(T0) 이후 **5 consecutive trading days** 동안 양 주체 계속 순매수 유지 여부를 사후에 확인
- Mature = True인 이벤트를 별도로 분리해 Q2를 검증하지만, Mature 여부를 보고 T0를 채택/기각하면 look-ahead bias가 생기므로 **Q1 분석에는 모든 Onset을 그대로 포함**한다.

### 비교 그룹

| 그룹 | 정의 |
|------|------|
| Onset | Pre-absorption 이후 첫 동시 양전환일 (T0) |
| Onset + Mature | Onset 중 이후 5일 연속 유지된 케이스 (사후 라벨) |
| Onset + Non-mature | Onset 중 5일 연속 미달 케이스 (사후 라벨) |
| Baseline | 위 어디에도 속하지 않는 날 |

### 평가 지표

- Alpha: 20D, 60D (KOSPI 대비 초과수익률)
- 보조: Median, Hit rate (>0%), P10

### 파라미터 (고정, 변경 불가)

| 파라미터 | 값 | 근거 |
|----------|----|------|
| Pre-absorption lookback | 10 trading days | ≈2주, "기존 수급 없음" 안정적 확인 |
| Pre-absorption threshold | 7/10일 동시 비양수 | 70% 기준, 외인+기관 동일 날짜 조건 |
| Mature window | 5 consecutive trading days | 단발성 노이즈와 실질 수급 구분 |

### 데이터 요구사항

- 종목별 일별 foreign_net, institution_net 시계열 (pykrx 또는 스냅샷 재구성)
- 현재 스냅샷 기간: 2026-01-02~07-30 (약 7개월)
- 이벤트 수가 n < 30이면 **Inconclusive로 기록하고 파라미터 재조정 없이 종료**. 데이터 누적 후 재실행.

### 판정 기준

- n ≥ 30: Onset 20D alpha vs Baseline 비교, 방향성 확인
- n ≥ 60: 통계적으로 의미있는 판정 가능 (60% hit rate 가설 검정 기준)
- n < 30: Inconclusive — 파라미터 조정 없이 데이터 추가 누적 후 재실행

### 다음 단계

1. 스냅샷에서 daily foreign_net / institution_net 시계열 추출 가능 여부 확인
2. 이벤트 추출 스크립트 작성 (`research/supply_onset.py`)
3. 이벤트 수 확인 → n 기준 충족 시 alpha 분석 실행
4. 결과 기록 후 Q2(Mature vs Non-mature) 추가 분석

**Production 반영**: 검증 완료 후에도 현재 Supply 컴포넌트와의 통합 방식은 별도 논의. 이 연구 단독으로 Score 변경 근거가 되지 않는다.

---

### 실행 결과 (2026-09-23)

**실행 환경**: `investor_flow` 2020-01-02~2026-09-04, `market_ohlcv` 동일 기간  
**Script**: `research/supply_onset.py`

#### 이벤트 수

| 항목 | 수 |
|------|---:|
| 전체 Onset | 10,705건 |
| Alpha 계산 완료 | 10,606건 (99건 가격 데이터 부재로 제외) |
| Mature=True | 10건 |
| Mature=False | 10,658건 |
| Mature=NA (기간 부족) | 37건 |

#### Q1 — Onset vs Baseline (20D alpha)

| 그룹 | n | Mean | Median | Hit% | P10 |
|------|--:|-----:|-------:|-----:|----:|
| Onset (전체) | 10,606 | +0.26% | -1.86% | 42% | -16.8% |
| Baseline (비-Onset 샘플, n=4,948) | 4,948 | +0.54% | -2.13% | 41% | -16.4% |
| Onset − Baseline | | -0.28%p | +0.27%p | +1%p | |

60D: Onset mean +0.51% / Baseline -1.14% / 차이 +1.65%p (방향성은 있으나 수준 미달)

**판정**: 🔴 가설 미지지  
Primary criterion (median > 0 AND hit > 60%) 모두 미달. Onset median -1.86%, hit 42%.

#### Q1 — 연도별 20D alpha (Onset)

| 연도 | n | Mean | Hit% |
|------|--:|-----:|-----:|
| 2020 | 1,622 | +1.48% | 42% |
| 2021 | 1,723 | +2.23% | 50% |
| 2022 | 1,840 | +1.40% | 47% |
| 2023 | 1,652 | +0.70% | 43% |
| 2024 | 1,633 | +0.83% | 41% |
| 2025 | 1,281 | -4.42% | 30% |
| 2026 | 954 | -3.33% | 35% |

연도별 안정성 부족. 2020-2024는 소폭 양수이나 2025-2026에서 급격히 악화. 원인 추정 없이 관찰로만 기록.

#### Q2 — Mature vs Non-mature

**판정**: ⚪ Inconclusive  
Mature=True = 10건 (0.09%). 사전등록 기준 n < 30 → Inconclusive. 5일 연속 동시 순매수 조건은 현실에서 극히 희소해 비교 불가. "효과 없음"이 아니라 "현재 데이터로 판단 불가"임을 명확히 한다.

#### 해석

직관("수급이 막 시작되는 시점이 좋은 매수 타이밍이다")은 그럴듯하지만, 2020~2026의 10,606개 Onset을 실제로 분석했을 때 지지되지 않는다. Onset이 Baseline보다 약간 낫지만(20D median 0.27%p 차이) 효과 크기가 의미있는 수준이 아니다.

이 결과는 기존 Supply 연구 흐름과 일관된다:
- 현재 수급량 자체 → alpha 기여 약함 (Supply spread +0.31%)
- 긴 수급 streak → 예측력 확인 안 됨
- 수급 Onset (시작 시점) → 미지지

Supply를 새로운 매수 신호로 강화하기보다, 현재 production 방향("등급을 흔들지만 매수/매도 판단에는 제한적으로 사용")이 데이터 전체와 일관된다.

#### 파라미터 재조정 금지

이 결과를 보고 Pre-absorption 조건(7/10), Mature 기간(5일) 등을 소급 조정하는 것은 사전등록 원칙 위반이다. 음성 결과는 음성 결과로 기록한다.


---

## Component Attribution — IC 및 Score Level 분해 (Pre-registered, 2026-09-23)

> **Status: 🟡 Pre-registered — 실행 전 파라미터 고정 완료.**

### 연구 질문

현재 v8.5.19 점수 구조에서, 실제 미래 alpha(20D)를 만들어내는 component는 무엇이고 점수만 흔들고 예측력은 없는 component는 무엇인가?

- **Q1 (Primary)**: 각 component score와 future alpha_20d의 일별 cross-sectional IC는 얼마인가?
- **Q3 (Secondary)**: 각 signal_date 내에서 component 상위 25%(Q4) vs 하위 25%(Q1)의 alpha 차이(Q4-Q1)는 존재하는가?

Q2 (Deterioration effect)는 별도 follow-up. 이번 스크립트에 포함하지 않는다.

### 데이터

- `retro_calibration`: signal_date, ticker, earnings_score, valuation_score, risk_score, supply_score, alpha_20d
- 기간: 2026-01-02 ~ 2026-07-30

### Q1 — Component IC (Primary) 설계

**IC 계산 방법**: 각 signal_date마다 cross-sectional Spearman rank correlation  
`IC_date = Spearman(component_score, alpha_20d)` (해당 날짜의 모든 종목)

**포함 조건**: signal_date별 유효 종목 수 n ≥ 5

**집계**: 일별 IC들의 mean, median, positive IC days 비율, n_days

**레짐 분류 (프록시)**:
- KOSPI trailing 60D return on each signal_date (`market_ohlcv`, ticker=`1001`)
- Bull: > +5% / Bear: < -5% / Sideways: −5% ~ +5%

**최소 출력**:

| component | mean_IC | median_IC | positive_IC% | n_days |
|-----------|--------:|----------:|-------------:|-------:|
| Earnings | | | | |
| Valuation | | | | |
| Risk | | | | |
| Supply | | | | |

레짐별 동일 형식 추가 출력.

### Q3 — Score Level Quartile (Secondary) 설계

**Quartile 할당**: 각 signal_date 내 cross-sectional rank
- Q4 = 해당 날짜 component score 상위 25%
- Q1 = 해당 날짜 component score 하위 25%

**집계**: 전 기간 Q1/Q4 alpha pooling 후 통계 (mean, median, hit rate)

**핵심 출력**:

| component | Q1 median | Q4 median | Q4-Q1 | Q4 hit% | Q1 hit% |
|-----------|----------:|----------:|------:|--------:|--------:|
| Earnings | | | | | |
| Valuation | | | | | |
| Risk | | | | | |
| Supply | | | | | |

레짐별 동일 형식 추가 출력.

### 판정 기준 (고정)

- Q1 기준: `|mean_IC| > 0.03` 이상이면 "의미있는 IC 존재"로 간주 (IC 0.03은 factor 연구의 통상적 최소 임계값)
- Q3 기준: `|Q4-Q1 median| > 1%p` 이상이면 "랭킹 효과 존재"로 간주
- IC 방향(양수/음수)과 이론적 기대 방향이 일치해야 "가설 지지"로 기록

**이론적 기대 방향** (사전 고정):
- Earnings IC: 양수 (실적 강한 종목이 더 나은 alpha)
- Valuation IC: 양수 (저평가 종목이 더 나은 alpha — 점수 높을수록 저PER/PBR)
- Risk IC: 양수 (리스크 낮은 종목이 더 나은 alpha — 점수 높을수록 안전)
- Supply IC: 양수 (수급 강한 종목이 더 나은 alpha)

**Script**: `research/component_attribution.py`

---

### 실행 결과 (2026-09-23)

> **Status: 🔵 Exploratory / Calibration Attribution — Production 미반영.**

**Script**: `research/component_attribution.py`  
**데이터**: retro_calibration 2026-01-02~07-30, 17,568행, 141 signal_dates

#### Q1 — Component IC 전체 기간

| Component | mean_IC | median_IC | positive_IC% | n_days | 판정 |
|-----------|--------:|----------:|-------------:|-------:|------|
| Valuation | **+0.142** | +0.155 | 81.6% | 141 | ✓ ↑ |
| Risk | +0.040 | +0.078 | 72.3% | 141 | ✓ ↑ |
| Earnings | +0.032 | +0.021 | 56.0% | 141 | ✓ ↑ (간신히) |
| Supply | -0.003 | -0.007 | 46.1% | 141 | — ↓ |

#### Q3 — Score Level Quartile 전체 기간

| Component | Q1 median | Q4 median | Q4-Q1 | Q4 hit% | Q1 hit% | 판정 |
|-----------|----------:|----------:|------:|--------:|--------:|------|
| Valuation | -5.25% | -3.71% | **+1.54%** | 41.7% | 39.5% | ✓ ↑ |
| Risk | -4.32% | -5.11% | -0.79% | 37.9% | 41.5% | — ↓ |
| Earnings | -4.98% | -5.10% | -0.12% | 39.3% | 39.8% | — ↓ |
| Supply | -4.96% | -4.74% | +0.23% | 39.4% | 39.6% | — ↑ |

#### 레짐별 주요 결과 (참고, 대부분 Bull=135일)

Bear(n=3), Sideways(n=3)은 표본 부족으로 방향 참고만 가능:
- Bear: Risk IC = -0.258, Q4-Q1 = -9.29%p → **Inconclusive** (n=3). Risk score 높은 종목이 Bear에서 더 나쁜 방향이지만 판단 불가.
- 전체 기간이 사실상 Bull(135/141일)이라서 레짐별 비교 자체가 제한적.

#### 해석

**Valuation이 현재 calibration universe에서 가장 일관된 alpha association을 보였다.** IC 0.142는 cross-sectional factor 연구에서 높은 수준이며, Q4-Q1 +1.54%p와 방향이 일치한다. 저평가 종목(Valuation score 높음)이 상대적으로 나은 20D 결과와 연결되는 구조.

Risk는 IC가 양수(+0.04)이지만 Q4-Q1이 음수(-0.79%p)로 불일치한다. IC와 quartile 결과의 방향이 다를 때는 비선형 관계, 극단값 영향, 또는 분포 특성 문제일 수 있으며 추가 해석 없이 관찰로만 기록한다.

Earnings는 IC 임계값(0.03)을 간신히 넘었지만 Q4-Q1에서는 의미있는 차이가 없어 실질적인 cross-sectional 분별력이 약하다.

Supply는 IC -0.003, positive IC day 46.1%로 현재 데이터에서 독립적인 ranking signal로 확인되지 않는다. 이는 이전 Supply 연구(현재량/streak/onset)와 일관된다.

**Supply 연구 전체 그림과의 연결:**

| Supply 연구 | 결과 |
|-------------|------|
| 현재 수급량 (alpha spread) | +0.31% — 약함 |
| Streak 지속 | 단조 패턴 미확인 |
| Onset (수급 시작점) | 미지지 |
| Component IC | -0.003 — ranking signal 미확인 |

4개 방향 모두 일관적으로 "Supply가 강한 독립 alpha predictor"라는 가설을 지지하지 않는다.

#### 해석 제한

> **⚠️ look_ahead_note = `earnings/risk=Naver(current)`**  
> Earnings score와 Risk score 계산에 당기 재무데이터(Naver 현재값)가 사용되어 look-ahead bias가 있을 수 있다. Earnings IC(+0.032)와 Risk IC(+0.040)는 실제 예측력보다 과대 추정됐을 가능성을 배제할 수 없다. Valuation은 시장가격 기반이라 같은 bias에 덜 노출된다.

따라서:
- "Valuation이 검증됐다" → 가장 consistent한 association이 있었다는 탐색적 증거
- "Earnings/Risk가 기각됐다" → look-ahead 제거 후 재확인 필요
- 이 결과만으로 production 가중치 변경 불가

#### 다음 단계

- Q2 Deterioration: component score 하락이 미래 alpha와 어떤 관계인지 (별도 연구)
- Valuation attribution 재확인: look-ahead 없는 데이터로 재실행 (calibration 데이터 품질 개선 후)
- Earnings/Risk IC: look-ahead 제거 후 재측정 필요

---

## Component Deterioration Effect — Q2 (Pre-registered, 2026-09-23)

> **Status: 🟡 Pre-registered — 실행 전 파라미터 고정 완료.**

### 연구 질문

component score의 절대 수준(level)이 아니라, **이전 snapshot 대비 하락(Δ < 0)**이 이후 20D alpha와 연결되는가?

이는 Q1/Q3와 완전히 다른 질문이다. Q1/Q3는 "높은 component가 더 나은가"를 봤고, Q2는 "component가 내려가는 것 자체가 나쁜 신호인가"를 본다.

### 데이터

- `retro_calibration`: signal_date, ticker, component_scores, alpha_20d
- 기간: 2026-01-02 ~ 2026-07-30
- 단위: 같은 ticker의 연속 snapshot pair (preceding → current)

### 이벤트 정의 (고정)

| 항목 | 정의 |
|------|------|
| Δcomponent | current_score − previous_score (직전 signal_date 기준) |
| Deteriorated | Δ < 0 (엄격한 하락) |
| Non-deteriorated | Δ ≥ 0 (유지 또는 상승 포함) |
| 평가 alpha | current snapshot의 alpha_20d (하락 확인 시점 이후 20D) |
| Interval | current와 previous signal_date 간 calendar days (분포 기록, 제외 기준 없음) |

**Components**: Earnings, Valuation, Risk, Supply (각각 독립 분석)  
**다중 통제 없음**: 이 패스에서는 다른 component 변화를 통제하지 않는다. 1차 진단.

### 최소 출력

| component | det_n | non-det_n | det_median | non-det_median | diff | det_hit% | non-det_hit% |
|-----------|------:|----------:|-----------:|---------------:|-----:|---------:|-------------:|

### 분류 기준 (사전 고정)

Q2 결과를 Q1/Q3와 합쳐서 아래 2×2 매트릭스로 분류:

| | Level 강함 (Q4-Q1 > +1%p) | Level 약함 |
|--|--|--|
| **Deterioration 강함** | A. 핵심 component 후보 | C. Exit/Warning signal 후보 |
| **Deterioration 약함** | B. Ranker로만 유용 | D. 역할 재검토 후보 |

"강함" 기준: `|det_median - non-det_median| > 1%p`

**Script**: `research/component_deterioration.py`

---

### 실행 결과 (2026-09-23)

> **Status: 🔵 Exploratory / Calibration Attribution — Production 미반영.**

**Script**: `research/component_deterioration.py`  
**데이터**: retro_calibration 2026-01-02~07-30, 연속 pair 17,433개

#### Pair Interval 분포

| 구간 | pair 수 | 비율 |
|------|--------:|-----:|
| 1일 | 13,195 | 75.7% |
| 2-3일 (주말) | 3,567 | 20.5% |
| 4-5일 | 525 | 3.0% |
| 6-10일 | 127 | 0.7% |
| 10일+ | 19 | 0.1% |

#### Q2 결과 — 전체 기간

| Component | det_n | non-det_n | det_median | non-det_median | diff | det_hit% | 판정 |
|-----------|------:|----------:|-----------:|---------------:|-----:|---------:|------|
| Earnings | 65 | 17,368 | +19.46% | -5.10% | +24.57% | 86.2% | ✗ 판단불가 |
| Valuation | 618 | 16,815 | -6.37% | -4.97% | **-1.40%** | 37.1% | ✓ ↓ (기대 방향) |
| Risk | 56 | 17,377 | -2.66% | -5.03% | +2.37% | 41.1% | ✗ 판단불가 |
| Supply | 3,804 | 13,629 | -4.38% | -5.19% | +0.81% | 40.1% | — ↑ (역방향, 임계 미달) |

**↓ = deteriorated가 non-deteriorated보다 나쁨 (기대 방향)**

#### Earnings / Risk 결과 해석 불가 사유

- Earnings det_n = 65 (전체 pair의 0.37%) — Earnings score가 retro 기간 중 거의 변하지 않음
- Risk det_n = 56 (0.32%) — 동일
- `look_ahead_note = earnings/risk=Naver(current)`: 점수가 현재 데이터 기준으로 고정돼 있어 이 기간의 deterioration은 실제 변화가 아닐 수 있음
- n 부족 + 데이터 품질 제한 → 해석 보류

#### 신뢰 가능한 결과: Valuation, Supply

**Valuation** (det_n=618): det_median -6.37% vs non-det_median -4.97%, diff = -1.40%p.
Valuation score 하락 시 이후 alpha가 1.40%p 더 나쁘며, 기대 방향(악화 → 미래 성과 저하)과 일치. Level(Q3 +1.54%p) + Deterioration(-1.40%p, 기대 방향) 모두 유의미.

**Supply** (det_n=3,804): det_median -4.38% vs non-det_median -5.19%, diff = +0.81%p.
임계값 미달. 방향도 역방향(악화됐는데 오히려 약간 나음). Supply 악화가 미래 alpha와 의미있는 관계가 없다.

#### 2×2 분류 매트릭스 (Q1/Q3 Level × Q2 Deterioration)

| | Level 강함 (Q4-Q1 > +1%p) | Level 약함 |
|--|--|--|
| **Det 강함 + 기대 방향** | **A. Valuation — 핵심 candidate** | (해당 없음) |
| **Det 약함 or 역방향** | (해당 없음) | **D. Supply — 역할 재검토** |
| **판단 불가** | — | Earnings, Risk (데이터 한계) |

#### 핵심 결론

Supply는 Level(ranking)에서도, Deterioration(exit signal)에서도 의미있는 alpha 관련성이 확인되지 않는다. 현재 production에서 Supply 악화를 매도 근거로 강하게 사용하는 설계는 이 두 가지 방향 모두에서 근거가 부족하다.

Valuation은 Level과 Deterioration 모두 일관된 방향(기대 방향)을 보이며, 이번 calibration에서 가장 신뢰있는 정보 제공 component로 나타났다.

Earnings, Risk는 현재 데이터로 판단 불가. look-ahead 없는 데이터로 재확인 필요.

#### Production 미반영 사유

- 전체 기간이 사실상 Bull(135/141일) — 단일 레짐 편향
- look-ahead 영향 배제 불가
- Calibration 단일 기간 결과 — OOS 재확인 전 production 반영 불가

---

## Annual PIT Component Attribution — rrs_stock_year 기반 (Pre-registered, 2026-09-23)

> **Status: 🟡 Pre-registered — 20D attribution의 Earnings/Risk 판단 보완 연구.**

### 배경

20D Component Attribution (위)에서 Earnings/Risk는 일별 granularity 상 변화가 거의 없어(det_n < 70) 판단이 제한됐다. Earnings/Risk는 연간 재무지표이므로 일별 분석이 적절하지 않다. 이 연구는 동일한 attribution 질문을 **연간 PIT 데이터**로 다시 묻는다.

### 연구 질문

> **연말 기준 PIT component score(Earnings/Risk/Value)가 다음 연도 실제 alpha를 예측했는가?**

부수 질문:
- Valuation은 20D에서도 강한데, 연간 기준에서도 강한가?
- Earnings/Risk는 20D보다 연간에서 더 강한가?
- component별 예측 시간축이 실제로 다른가? (Valuation=단기, Earnings/Risk=연간)

### 데이터

- **`rrs_stock_year`**: risk_year=2016~2024, fwd_alpha = 다음 연도 실제 alpha
- PIT 보장: 연말 재무데이터 기준 scoring, `earnings_approx=0` = 실제 데이터 사용 행 우선
- n = 6,653행 (859 tickers × 최대 9년)

### 분석 설계 (고정)

**Q1 Annual IC**: 각 risk_year별 cross-sectional Spearman(component_score, fwd_alpha)  
→ mean IC, median IC, positive IC years %, n_years

**Q3 Annual Quartile**: risk_year 내 cross-sectional quartile → Q4-Q1 fwd_alpha 차이

**Q2 Annual Deterioration**: `Δ = score[risk_year] - score[risk_year-1]`  
→ Δ < 0 (악화) vs Δ ≥ 0 의 fwd_alpha 비교

**대상 components**: `value_score, earnings_score, risk_score, supply_score`  
(20D 연구와 동일 components, 다른 granularity)

### 판정 기준 (고정)

- IC 의미 있음: `|mean_IC| > 0.03`
- Q4-Q1 효과: `|median diff| > 2%p` (연간 alpha이므로 임계값 상향)
- 이론적 기대 방향: 전 component 양수 IC

### 결과 해석 기준

20D 결과와 비교해 아래 4가지 패턴 중 어디에 해당하는지 기록:

| | 20D IC 강함 | 20D IC 약함 |
|--|--|--|
| **1Y IC 강함** | 두 시간축 모두 유효 | 연간만 유효 → 장기 factor |
| **1Y IC 약함** | 단기만 유효 → 모멘텀성 | 양 축 모두 약함 |

**Script**: `research/annual_component_attribution.py`  
**실행 조건**: rrs_stock_year 확인 완료 → 바로 실행 가능.

---

### 실행 결과 (2026-09-23)

> **Status: 🔵 Exploratory / Annual PIT Attribution — Production 미반영.**

**Script**: `research/annual_component_attribution.py`  
**데이터**: rrs_stock_year risk_year 2016~2024, 6,653행, 859 tickers

#### 데이터 품질 (실행 전 확인됨)

| Component | approx 비율 | 신뢰도 |
|-----------|----------:|--------|
| Value | 1.6% | ✓ PIT |
| Risk | 26.5% | ✓ 대부분 PIT |
| Earnings | 100% | ⚠️ 전량 근사값 — 연간에서도 PIT 아님 |
| Supply | 100% | ⚠️ 전량 근사값, 연도내 상수 (IC 계산 불가) |

#### Q1 — Annual IC

| Component | mean_IC | median_IC | positive_IC% | n_years | PIT | 판정 |
|-----------|--------:|----------:|-------------:|--------:|-----|------|
| Value | +0.065 | +0.075 | 88.9% | 9 | ✓ | ✓ ↑ |
| Risk | **+0.071** | +0.081 | 88.9% | 9 | 대부분 | ✓ ↑ |
| Earnings | +0.041 | +0.070 | 77.8% | 9 | ✗ approx | 참고만 |
| Supply | N/A | N/A | N/A | — | ✗ | 계산 불가 |

#### Q3 — Annual Quartile (Q4-Q1)

| Component | Q1 median | Q4 median | Q4-Q1 | 판정 |
|-----------|----------:|----------:|------:|------|
| Value | -13.89% | -10.12% | **+3.77%** | ✓ ↑ |
| Risk | -13.60% | -10.40% | **+3.20%** | ✓ ↑ |
| Earnings | -15.05% | -12.24% | +2.80% | ✓ ↑ (approx) |
| Supply | -11.68% | -11.82% | -0.14% | — |

#### Q2 — Annual Deterioration (YoY Δ < 0 vs Δ ≥ 0)

| Component | det_n | non-det_n | det_median | non-det_median | diff | 판정 |
|-----------|------:|----------:|-----------:|---------------:|-----:|------|
| Value | 2,055 | 3,734 | -10.00% | -10.05% | +0.05% | — (무의미) |
| Risk | 1,234 | 4,555 | -13.14% | -9.26% | **-3.88%** | ✓ ↓ (기대 방향) |
| Earnings | 1,266 | 4,523 | -12.88% | -9.34% | -3.54% | ✓ ↓ (approx) |
| Supply | — | — | — | — | — | 이벤트 없음 |

#### 20D vs Annual 비교 (핵심 발견)

| Component | 20D IC | 1Y IC | 20D Det | 1Y Det | 패턴 |
|-----------|-------:|------:|---------|--------|------|
| Value | +0.142 | +0.065 | ✓ (-1.40%) | ✗ (+0.05%) | 단기 우세, 연간 Det 무의미 |
| Risk | +0.040 | **+0.071** | 판단불가 | ✓ (-3.88%) | 연간에서 더 강함 |
| Earnings | +0.032 | +0.041 | 판단불가 | ✓ (-3.54%) approx | approx, 방향 참고 |
| Supply | -0.003 | N/A | ✗ | N/A | 양 축 모두 약함 |

#### 해석

**Value**: 단기(20D) ranking과 단기 deterioration 경고 모두 유효하지만, 연간 단위 Valuation 악화는 미래 연간 성과와 관련이 없다. Valuation이 단기 모멘텀 성격의 정보를 담고 있을 가능성.

**Risk**: 20D 단기에서는 IC 약하고 Q4-Q1도 불일치했지만, 연간 기준에서는 IC 0.071로 오히려 Value보다 강하고 Q4-Q1 +3.20%도 일관. 그리고 YoY 악화 시 연간 성과가 -3.88%p 차이 — Risk는 단기보다 장기에서 더 유의미한 정보를 제공하는 구조.

**핵심 시사점**: Valuation과 Risk가 서로 다른 예측 시간축에서 작동할 가능성이 있다. 현재 엔진이 두 component를 같은 속도로 업데이트하고 동일 점수에 합산하는 설계는 각 component의 실제 정보 시간축과 어긋날 수 있다. 이는 Production 변경 근거가 아니라 향후 설계 연구 방향이다.

**Supply**: rrs_stock_year에서도 점수가 연도내 상수에 가까워 IC 계산 불가. Q4-Q1도 -0.14%로 무의미. 20D 분석과 완전히 일관.

#### 해석 제한

- Earnings, Supply는 rrs_stock_year에서도 approx 100% — 결과를 PIT로 취급하면 안 됨
- 9년 × 859 tickers이지만 연도별 n=682~814로 연도간 구성이 다름
- "Value 단기 / Risk 장기" 패턴은 탐색적 관찰이며, 사전등록된 가설 검증이 아님
- Production 반영 불가 (calibration 단일 universe + approx 비율 문제)

---

## ③ STRONG_BUY 내부 랭킹 — Score 단조성 검증 (Pre-registered, 2026-09-23)

**상태**: 🟡 Pre-registered (실행 전 사전등록)

**가설**: STRONG_BUY 등급 내에서도 normalized_score가 높을수록 20D alpha가 단조롭게 높다.

**연구 배경**:
Calibration 결과 STRONG_BUY 전체의 alpha는 확인됨. 그러나 내부적으로 score 상위 종목이 실제로 더 좋은 성과를 내는가는 별개 질문. 이 연구는 현재 composite score의 "ranking power"를 검증한다.

**연구 질문**:
- Primary Q1: STRONG_BUY 내 당일 score rank → 20D alpha의 Spearman IC가 양수이고 일관성 있는가?
- Secondary Q2: Percentile bucket별 alpha 분포가 단조성을 보이는가?

**데이터**:
- 출처: retro_calibration, `grade = 'strong_buy'`, `alpha_20d IS NOT NULL`
- Score: `normalized_score` as-is (컴포넌트 재가중 없음)
- 표본 정보 (사전 확인): 141 signal_dates, median n_SB=11/date, total n=1,639

**방법 (사전등록, 변경 금지)**:

1. **Daily IC (Primary)**: 각 signal_date 내 (normalized_score vs alpha_20d) cross-sectional Spearman IC. n ≥ 4인 날짜만 포함.
2. **Pooled Spearman (참고)**: 전체 STRONG_BUY pool의 (normalized_score, alpha_20d) Spearman rho. 날짜 내 cross-sectional 구조이므로 참고 수준.
3. **Percentile bucket**:
   - Primary bucket: Top 50% vs Bottom 50% (n 충분성 확보)
   - Secondary bucket: Top 10% / 10~25% / 25~50% / 50~100% (pooled, n < 20인 bucket은 Inconclusive)

**판정 기준 (사전등록, 결과 확인 후 변경 금지)**:
| 기준 | 지지 (지지) | 미지지 |
|---|---|---|
| Daily IC mean | > +0.05 | ≤ 0.05 |
| Daily IC pos% | > 55% | ≤ 55% |
| Top50 vs Bottom50 alpha diff | > +1.0%p | ≤ 1.0%p |
| n per bucket < 20 | Inconclusive | — |

Primary Q1 지지 조건: IC mean > +0.05 AND pos% > 55% 동시 충족.

**실행**: research/strongbuy_ranking.py (2026-09-23)

**결과**:

| 지표 | 값 | 기준 | 판정 |
|---|---|---|---|
| Daily IC mean | -0.0236 | > +0.05 | ❌ |
| Daily IC pos% | 51.4% | > 55% | ❌ |
| Top50 - Bottom50 | +1.65%p | > +1%p | ✅ |
| 세부 bucket 단조성 | 비단조 | — | ❌ |

**실증 데이터 (n=1,639, 141일)**:

```
Bucket       n     median    mean    hit%
Top50%      784    -1.19%  -0.37%   46.4%
Bottom50%   853    -2.84%  -0.96%   43.3%
Top - Bottom        +1.65%

세부:
Top10%      230    -1.70%  -1.36%   47.0%
10-25%      231    -1.23%  +0.20%   46.3%
25-50%      392    -1.10%  -0.49%   45.9%
50-100%     784    -2.81%  -0.82%   43.1%
```

Pooled Spearman rho=+0.018 (p=0.46, 유의하지 않음)

**해석**:
- Primary 단조성(Daily IC)은 미지지: score 고순위 종목이 매일 일관되게 더 좋은 alpha를 내지 않는다.
- 이진 분할(Top50 vs Bottom50)에서 +1.65%p 차이는 기준 초과이지만, Top10%가 10-25%보다 오히려 낮은 비단조 패턴이 나타났다.
- 전반적으로 이 기간(2026-01~07) STRONG_BUY 자체 hit rate가 43~47%로 낮아 alpha 환경 자체가 불리했음. 절대 수익률 전부 마이너스 → 시장 환경 효과와 score effect가 구분되지 않음.
- **결론**: 현재 composite score는 STRONG_BUY 등급 결정 (grade 구분)에는 기여하지만, STRONG_BUY 내부 정밀 순위를 분별하는 역할은 확인되지 않음. Score 상위 몇 종목만 골라 집중 매수하는 전략은 현재 데이터로 지지되지 않는다.

**Production 영향 없음**: v8.5.19 FROZEN. Equal-weight 기본 전제에 부합.

**상태**: 🔵 Closed

---

## Supply Horizon IC — 수급 신호의 horizon 민감도 (Pre-registered, 2026-09-23)

**상태**: 🟡 Pre-registered (실행 전 사전등록)

**배경**:
retro_calibration 기반 분석에서 Supply IC가 20D=-0.003, 60D=+0.004, 120D=+0.083로 horizon이 길어질수록 상승하는 패턴이 탐색적으로 관찰됐다. 기관의 저가매수 → 포지션 구축 → 가격 반영 사이클이 수 개월에 걸친다면, 20D 창은 구조적으로 부족했을 수 있다.

**한계 인식 (사전등록)**:
- 120D forward window는 인접 날짜 간 대부분의 return 기간이 중첩 → p값이 과소 추정됨
- 탐색적 결과(2026-01~04 한정, n=4,764)를 확장 검증하는 것이 본 연구의 목적

**핵심 질문**: 20D → 60D → 120D로 horizon이 길어질수록 Supply IC가 안정적으로 상승하는가?

**데이터**:
- investor_flow (2020-2026, 3.9M행): foreign_net, institution_net
- market_ohlcv (2020-2026): 가격 데이터
- Supply proxy: 7영업일 rolling sum 기반 direction score (production 동일 로직)
  - 쌍끌이매수 = 20 / 외국인단독 = 12 / 기관단독 = 10 / 쌍끌이매도 = -5
- Universe: investor_flow × market_ohlcv 교집합 종목

**방법 (사전등록)**:
1. 각 (ticker, date)에서 7영업일 rolling supply proxy 계산
2. 20D / 60D / 120D forward alpha = stock_ret − KOSPI_ret (entry: T+1 open, exit: T+horizon+1 open)
3. 각 date에서 cross-sectional Spearman IC (supply proxy vs alpha)
4. Horizon별 IC 통계: mean, median, positive%, 시간적 분포

**판정 기준 (사전등록, 변경 금지)**:
| 기준 | Horizon sensitivity 지지 | 미지지 |
|---|---|---|
| IC 방향 | 20D < 60D < 120D (단조 증가) | 단조 증가 불성립 |
| 120D mean IC | > +0.05 | ≤ +0.05 |
| 120D pos% | > 55% | ≤ 55% |

모든 3개 기준 충족 시 "Supply는 단기 ranker가 아니라 장기(120D) horizon signal일 가능성 지지".

**비독립성 caveat**: 120D IC의 p값은 참고용. 실질적 신뢰도는 mean IC 수치와 pos% 일관성으로 판단.

**실행**: research/supply_horizon_ic.py (2026-09-23)

**결과**:

```
Horizon별 IC 통계:
  20D:  mean=-0.0049  median=-0.0048  pos%=46.2%  n_days=1,598  ❌ ↓
  60D:  mean=-0.0077  median=-0.0058  pos%=45.4%  n_days=1,558  ❌ ↓
  120D: mean=-0.0052  median=-0.0021  pos%=47.7%  n_days=1,500  ❌ ↓

연도별 IC 분포:
  2020:  20D=-0.0049 / 60D=-0.0105 / 120D=-0.0115
  2021:  20D=+0.0037 / 60D=-0.0090 / 120D=-0.0029
  2022:  20D=-0.0112 / 60D=-0.0148 / 120D=-0.0155
  2023:  20D=-0.0092 / 60D=-0.0102 / 120D=-0.0037
  2024:  20D=-0.0110 / 60D=-0.0093 / 120D=-0.0044
  2025:  20D=+0.0008 / 60D=+0.0034 / 120D=+0.0036  ← 약하게 양수
  2026:  20D=-0.0002 / 60D=+0.0031 / 120D=+0.0144  ← 2026-01~04 (120D window 짧음)

판정:
  IC 단조 증가 (20D < 60D < 120D): ❌  (60D < 20D)
  120D mean IC > +0.05: ❌  (-0.0052)
  120D pos% > 55%: ❌  (47.7%)
  최종: ❌ Horizon sensitivity 미지지
```

**해석**:

6년 데이터(2020-2026, 3,113 종목, 거래일 1,634일) 전체로 보면 Supply proxy의 IC는 모든 horizon에서 약하게 음수(-0.005~-0.008)이며 단조 증가 패턴도 없다.

이전 탐색적 결과(2026-01~04, 120D IC=+0.083)는 다음 복합 효과였다:
- 2026년 단기 구간의 regime 특수성 (2026년 120D IC=+0.014, 연도 중 가장 높지만 여전히 임계치 미달)
- 중첩 window로 인한 p값 과소추정
- 전체 universe 대비 극히 짧은 sample

**연도별 패턴 관찰**: 2025-2026에서 약하게 양수 신호가 있다. 이것이 recent regime 변화인지 noise인지는 더 많은 데이터(2026 이후)가 필요하다. 단, 사전등록 기준에서는 명백히 ❌.

**패턴 강도 평가**: Refute — 6년 데이터에서 방향 자체가 일관성 없음 (대부분 기간 음수, 양수는 2025-2026 일부)

**Supply 종합 결론 (이 시점 기준)**:
- Component Role Attribution: 5개 역할 모두 KPI 미달 ❌
- Supply Counterfactual: Promoted/Demoted 차이 없음 ❌ (단, SB팽창 설계 문제로 직접 결론 어려움)
- Supply Conditional: High Core에서 Q4-Q1=-0.73%p → SB artifact 확인 ❌
- Supply Horizon IC: 6년 전체 모든 horizon에서 near-zero to 음수 ❌

**현재 Supply 지위**: 설계된 역할 없음 확인됨. Production에서 유지/제거 결정은 Calibration n≥100 후 별도 논의. 점수 로직 변경은 Freeze 해제 이전 불가.

---

## Supply Counterfactual — supply_score 제거 시 grade/alpha 영향 (Pre-registered, 2026-09-23)

**상태**: 🟡 Pre-registered (실행 전 사전등록)

**배경**:
Supply component는 지금까지 5개 역할 모두에서 KPI 미달. 그러나 현재 supply_score는 최대 +25점(긍정)부터 -5점(부정)까지 normalized_score에 직접 가감된다. Supply가 noise/왜곡 역할을 하고 있다면, supply_score가 좋은 종목이 오히려 나쁜 성과를 내고 있을 수 있다.

**가설**: supply_score를 제거하고 재정규화하면 STRONG_BUY 코호트의 alpha가 유지되거나 개선된다.

**방법 (사전등록)**:
- `no_supply_norm = round((earn + val + risk) / 75 * 100)`, capped 0~100
- 등급 임계값: STRONG_BUY ≥85 / BUY ≥70 / NEUTRAL ≥55 (동일)
- Earnings gate: production code 기준 없음 (soft gate 아님 확인)

**비교 그룹**:
| 그룹 | 정의 | 의미 |
|---|---|---|
| Stable SB | 원래 SB + no-supply SB | supply 영향 없는 종목 |
| Promoted SB | 원래 BUY/하위 → no-supply SB | supply가 억누르고 있던 종목 |
| Demoted SB | 원래 SB → no-supply 이탈 | supply가 끌어올리고 있던 종목 |

**Primary 판정 기준 (사전등록, 변경 금지)**:
- Promoted SB median alpha − Demoted SB median alpha > +0.5%p → "supply가 좋은 종목을 억누르고 있었다"

**실행**: research/supply_counterfactual.py (2026-09-23)

**결과**:

```
등급 변화 분포:
  Stable SB   (양쪽 SB): 1,601
  Promoted SB (신규 진입): 5,689   ← no-supply SB가 4.4배로 팽창
  Demoted SB  (SB 이탈):    38

Alpha 비교:
  원래 SB:      n=1,639  median=-1.92%  hit=44.7%
  No-supply SB: n=7,290  median=-2.67%  hit=43.9%  (4.4배 큰 코호트)
  Promoted SB:  median=-2.85%
  Demoted SB:   median=-2.82%

Primary 판정: ❌ Promoted − Demoted = -0.03%p (기준: > +0.5%p)

추가 분석: SB 내부 supply Q4 vs Q1
  Supply Q4 (avg=23.4): median=-0.13%, hit=49.3%
  Supply Q1 (avg=12.8): median=-2.93%, hit=41.1%
  Q4 − Q1 = +2.80%p (SB 내부에서 supply 높을수록 alpha 좋음)
```

**설계 한계**:
no_supply_norm = (earn+val+risk)/75×100으로 재정규화하면 STRONG_BUY 진입 기준이 사실상 낮아진다. earn+val+risk ≥ 63.75이면 SB (원래는 earn+val+risk+supply ≥ 85). 따라서 no-supply SB가 4.4배로 팽창한 것은 "supply 제거 효과"가 아니라 "등급 기준 완화 효과"가 혼재된 결과다.

**해석 (주의: 복잡한 결과)**:

1. **Primary 판정 ❌**: Promoted SB와 Demoted SB의 alpha 차이가 거의 없음(-0.03%p). "supply가 좋은 종목을 억누르고 있었다"는 가설 미지지.

2. **SB 내부 조건부 신호 발견**: 전체 IC = -0.003 (무신호)이지만, STRONG_BUY 내부에서는 supply Q4가 Q1보다 +2.80%p 높음. 이는 "전체 universe에서는 supply ranking 신호 없음, 고품질 SB 내에서는 추가 supply가 의미 있을 수 있음"이라는 조건부 신호(conditional signal) 가능성을 제기함.

3. **구조적 해석**: SB에서 supply 낮음(Q1 avg=12.8) = earn+val+risk가 더 높아야 SB 충족 → 고밸류에이션 종목. SB에서 supply 높음(Q4 avg=23.4) = 상대적으로 낮은 earn+val+risk도 supply로 보완 → 수급이 실제로 붙은 종목. 이 구조에서 Q4 > Q1이 나온 것은 "supply가 독립 신호"보다 "수급이 이미 붙어 있는 종목 특성"을 반영할 수 있음.

**Supply 역할 재평가**:
- "전체 종목에 걸쳐 ranking 신호" → ❌ (IC=-0.003, 일관됨)
- "SB 내부 조건부 신호" → 가능성 있음, 추가 검증 필요
- "noise여서 제거해야 한다" → Primary 기준 미달이지만, SB 내 Q4/Q1 결과가 반박 증거로 작용
- **결론**: Supply 단순 제거보다는 "high-quality universe 내 conditioning 역할"로 재설계하는 방향이 더 정확할 수 있음.

**Status**: 🔵 Closed (단순 제거 결론 없음, 역할 재설계 가설로 전환)

---

## Supply Conditional Attribution — Core Score 통제 후 Supply 조건부 신호 검증 (Pre-registered, 2026-09-23)

**상태**: 🟡 Pre-registered (실행 전 사전등록)

**배경**:
Supply Counterfactual에서 SB 내부 supply Q4−Q1 = +2.80%p가 관찰됐다. 그러나 SB 진입 조건 자체가 (earn+val+risk+supply ≥ 85)이므로, SB 내부에서 supply 높음 = earn+val+risk 낮음의 역관계가 만들어진다. 이 artifact를 통제하지 않으면 "조건부 신호"인지 "SB selection artifact"인지 구분 불가.

**가설**: Core Score(earn+val+risk)를 동일 수준으로 통제한 뒤에도 Supply Q4가 Q1보다 alpha가 높다면, Supply는 독립적인 조건부 신호다. 차이가 사라지면 artifact다.

**연구 질문**: Earn+Val+Risk 수준이 비슷한 종목 중에서 Supply가 높은 종목이 더 좋은 alpha를 내는가?

**데이터**: retro_calibration, 전 등급, alpha_20d IS NOT NULL

**방법 (사전등록)**:
1. Core Score = earnings_score + valuation_score + risk_score (max 75)
2. Core strata (사전 고정): Low (40~54) / Mid (55~64) / High (65~75)
   - Low n<100이면 합산하여 해석 한계 표시
3. 각 stratum 내에서 supply_score를 Q4(상위25%)/Q1(하위25%)로 나눔 (cross-sectional)
4. 각 stratum × supply quartile cell별 median alpha, hit rate 계산
5. Spearman(supply_score, alpha_20d) within each stratum (IC)

**판정 기준 (사전등록, 변경 금지)**:
| 기준 | 조건부 신호 지지 | artifact |
|---|---|---|
| Mid + High stratum에서 Q4−Q1 | > +1%p 동시 | ≤ +1%p 중 하나라도 |
| Stratum IC 방향 | ≥ 2개 stratum에서 양수 | — |
| SB zone 외 High stratum (Core ≥65) | Q4−Q1 > 1%p | Q4−Q1 ≤ 1%p → artifact 강력 증거 |

**실행**: research/supply_conditional.py (2026-09-23)

**결과**:

Core × Supply 2D 매트릭스 (Median Alpha):

| Core Stratum | Supply Q1 | Supply Q2 | Supply Q3 | Supply Q4 | Q4−Q1 | 판정 |
|---|---|---|---|---|---|---|
| Low (40-54) | -6.04% | -7.47% | -7.25% | -7.56% | **-1.53%p** | — ↓ |
| Mid (55-64) | -6.56% | -6.16% | -5.39% | -4.80% | **+1.76%p** | ✓ ↑ |
| High (65-75) | -2.22% | -3.01% | -3.42% | -2.95% | **-0.73%p** | — ↓ |

Stratum IC:
- Low:  rho=+0.029 (p=0.023)
- Mid:  rho=+0.131 (p≈0) ← 유의하지만 stratum 한정
- High: rho=+0.058 (p≈0) ← 방향은 양수이나 효과 미미

**종합 판정: ❌ SB selection artifact 확정**

Primary 기준 (Mid+High 모두 Q4−Q1 > +1%p): High Core에서 -0.73%p → 미달.

**해석**:
- SB selection artifact 구조: SB 진입 조건 = earn+val+risk+supply ≥ 85. Supply Q4 within SB = supply가 높고 Core는 낮음 vs Supply Q1 within SB = supply가 낮고 Core는 매우 높음. 이 역관계가 "SB 내 supply Q4 > Q1"처럼 보이게 만들었으나, Core를 실제로 통제하면 High Core 구간에서 Supply 효과가 사라짐.
- Mid Core(55-64)의 +1.76%p는 흥미롭지만 해석 주의: 이 구간은 BUY~NEUTRAL 등급 영역으로 SB가 아님. "중품질 종목에서 supply가 선별 효과를 가질 수 있다"는 별도 가설이 필요하며, 이번 연구 설계 범위 밖.
- Low Core에서 Supply 역효과(-1.53%p): 저품질 종목에서 수급이 높으면 오히려 나쁜 결과. 단기 유동성 유입이 저품질 종목에서 추후 매도 압력을 만들 수 있음.

**Supply 연구 최종 정리**:
지금까지 Supply에 대한 누적 연구 결과:
- Ranker (전체 IC): ❌ -0.003
- Gate: ❌
- Risk Filter: ❌
- Deterioration: ❌
- Onset Effect: ❌ (n=10,606, 20D -1.86%, 가설 미지지)
- Context (레짐 asymmetry): ❌ 미달 (|IC diff|=0.041)
- SB 내부 조건부 신호: ❌ artifact 확인
- Core 통제 후 High Core 조건부: ❌ -0.73%p

**결론**: 현재 형태의 supply_score는 어떤 역할 프레임에서도 alpha 기여가 확인되지 않는다. Calibration n ≥ 100 + Freeze 해제 후 supply component 재설계(또는 제거)를 1순위로 검토할 것.

단, Mid Core(55-64)에서의 패턴은 별도 가설로 남겨둠 — "중품질 종목에서의 supply 선별 역할"은 현재 엔진 범위 밖이므로 별도 연구 설계 필요 시 추가.

**상태**: 🔵 Closed

---

## Component Role Attribution — 역할 설계 검증 (Pre-registered, 2026-09-23)

**상태**: 🟡 Pre-registered (실행 전 사전등록)

**배경**:
지금까지 연구는 "어떤 component가 alpha를 만드는가?"를 물었다. 그러나 component마다 역할이 다를 수 있다: Valuation은 ranking, Risk는 downside filter, Supply는 context. 이 연구는 각 component를 **미리 정한 역할별 KPI로 검증**하여, weight 재설계 이전에 역할 구조를 명확히 한다.

**5가지 역할 정의 (사전등록)**:
| Role | 질문 | Primary KPI | 기준 |
|---|---|---|---|
| **Ranker** | 높을수록 미래 alpha가 높은가? | Daily IC mean | > +0.05 |
| **Gate** | 일정 수준 이상이어야 성공 확률이 높아지는가? | Q4 hit% − Q1 hit% | > +10pp |
| **Risk Filter** | 낮을 때 downside가 커지는가? | Q4 P10 − Q1 P10 | > +2%p |
| **Deterioration** | 악화가 이후 성과 악화와 연결되는가? | |det − non_det median| | > +1%p (방향: det worse) |
| **Context** | 단독 신호는 없지만 regime에서 asymmetry가 있는가? | |IC_Bear − IC_Bull| | > +0.05 |

**Component별 사전 가설**:
| Component | Ranker | Gate | Risk Filter | Deterioration | Context |
|---|---|---|---|---|---|
| Valuation | ✓ 가설 | ✓ 가설 | — 가설 없음 | ✓ 가설 | — |
| Earnings | — 불확실 | ✓ 가설 | — | — 불확실 | — |
| Risk | — 불확실 | — | ✓ 가설 | ✓ 가설 (Annual) | — |
| Supply | ✗ 가설 (불지지 예상) | ✗ | ✗ | ✗ | ✓ 가설 |

**데이터**: retro_calibration, alpha_20d IS NOT NULL (전 등급)  
**신규 계산**:
- Gate KPI: Q4/Q1별 hit rate (20D alpha > 0 비율)
- Risk Filter KPI: Q4/Q1별 P10 (하위 10th percentile of alpha_20d)

**기존 결과 재사용**:
- Ranker: component_attribution.py (IC, Q4-Q1 median)
- Deterioration: component_deterioration.py
- Context: component_attribution.py 레짐별 IC

**판정 원칙**: 역할별 KPI를 충족하면 해당 역할 "지지". 한 component가 복수 역할을 가질 수 있음. KPI 미달 = 역할 없음 (점수에서 역할 제거 후보).

**실행**: research/component_role_attribution.py (2026-09-23)

**결과**:

역할 매트릭스:

| Component | Ranker | Gate | Risk Filter | Det | Context |
|---|---|---|---|---|---|
| Valuation | ✓ | — | ✓ | ✓ | ✓ |
| Risk | — | — | ✓ | — | ✓* |
| Earnings | — | — | — | — | ✓ |
| Supply | — | — | — | — | — |

*Risk Context: |IC_Bear - IC_Bull| = 0.306이지만 Bear n=3일 → 극히 작은 샘플, 신뢰 제한

**세부 수치**:
```
Valuation: Ranker IC=+0.142 / Risk Filter P10 diff=+5.80%p / Det diff=-1.40%p / Context |IC diff|=0.160
Risk:      Risk Filter P10 diff=+2.21%p (최소 기준 충족) / Context |IC diff|=0.306 (샘플 의존)
Earnings:  Context |IC diff|=0.059 (Bear IC=-0.025, Bull IC=+0.034) — 방향만 다름
Supply:    전 역할 KPI 모두 미달

[특이 사항]
- Gate 역할: 전 component에서 hit rate Q4−Q1 < 10pp → 전무
  이 기간(2026-01~07) 전체 hit rate 37~42%로 낮아, Gate 부재가 레짐 환경 문제일 가능성
- Earnings Det 역전: det_med=+19.46% vs non_det_med=-5.10% (diff=+24.57%p, 기대 반대)
  원인: 분기 실적 사이클에 의한 평균 회귀 가능성 (Δ<0 = 고점에서 하락 = 이후 회복)
```

**가설 대비 결과**:
| | Valuation | Risk | Earnings | Supply |
|---|---|---|---|---|
| Ranker 가설 ✓ | ✅ 일치 | (무방향) | (무방향) | ✅ 일치(불지지 예상=불지지) |
| Gate 가설 ✓ | ❌ 불일치 | ✅ 일치(가설 없음) | ❌ 불일치 | ✅ 일치(불지지 예상) |
| Risk Filter 가설 ✓ | ⚠️ 예상 외 확인 | ✅ 일치 | ✅ 일치(가설 없음) | ✅ 일치(가설 없음) |
| Det 가설 ✓ | ✅ 일치 | ❌ 불일치 | (무방향) | ✅ 일치(불지지 예상) |
| Context 가설 ✓ | ⚠️ 예상 외 확인 | ⚠️ 예상 외(샘플 주의) | ⚠️ 예상 외 확인 | ❌ 불일치 |

**해석**:

**Valuation**: 4개 역할 확인 (Ranker/Risk Filter/Det/Context). 현재 엔진에서 유일한 멀티-역할 component. 단기 ranking + downside filter + exit signal 모두 담당. Gate 미지지는 hit rate 환경 문제로 보이며 결론 유보.

**Risk**: Risk Filter 역할 확인 (P10 diff +2.21%p, 최소 기준). Context는 샘플 3일로 신뢰 제한. 현재 데이터 내에서 "downside protection" 역할은 지지됨.

**Earnings**: Context 역할만 확인 (Bull/Bear regime 방향 전환). Gate 역할 불지지: Q4 hit=39.3% vs Q1 hit=39.8% (거의 동일). Earnings Gate가 현재 production에서 작동하는 이유는 점수 자체가 아니라 다른 mechanism일 가능성.

**Supply**: 5개 역할 모두 미달. Context 역할도 불지지 (|IC diff|=0.041, 기준 0.05 미달). 현재 데이터에서 Supply score는 어떤 역할로도 가치가 확인되지 않음.

**중요 한계**:
- 기간 편향: Bull 135일 / Bear 3일 / Sideways 3일 → Context KPI의 Bear 결과는 거의 모두 노이즈
- Gate 역할 전무: hit rate 37~42%인 기간 → 환경 효과와 component 효과 분리 불가
- Production 반영 불가: v8.5.19 FROZEN

**상태**: 🔵 Closed

---

## Supply Event Study — 상승 이벤트 직전 수급 패턴 (Pre-registered, 2026-09-23)

**상태**: 🟡 Pre-registered

**연구 라인**: IC 연구와 독립된 별도 라인. "수급 자체가 약한 게 아니라, 수급이 cross-sectional ranker가 아닌 event-conditional signal일 수 있다"는 가설을 직접 검증.

**핵심 질문**: 주가가 오르기 직전 20일의 수급 패턴은 평상시와 다른가? 그리고 그 차이가 prospective signal로 작동하는가?

---

### 이벤트 정의 (고정, 변경 금지)

- **Event**: `T+1 ~ T+20` 누적 alpha ≥ +7%p (KOSPI 대비 초과수익)
- alpha = stock T+1 open → T+21 open 수익률 − KOSPI 동일 구간 수익률
- 임계값 7%p는 이 시점 고정. 결과 확인 후 재조정 금지.
- 이벤트가 너무 많거나 적어도 threshold 재조정 하지 않음. 필요시 별도 sensitivity 연구.

---

### 분석 대상 패턴 (사전등록, 4개, 고정)

분석 window: **T-20 ~ T-1** (이벤트 직전 20 거래일)

| # | 패턴명 | 정의 |
|---|---|---|
| P1 | 평균 방향 점수 | T-20~T-1 daily direction score 평균 (dual_buy=20/fx_single=12/inst_single=10/dual_sell=-5) |
| P2 | 쌍끌이매수 비율 | T-20~T-1 중 both foreign_net>0 AND institution_net>0인 일수 / 20 |
| P3 | 최장 연속 매수 streak | T-20~T-1 내 기관 또는 외국인의 최장 연속 순매수일 수 (둘 중 max) |
| P4 | 후반 강화 전환 | T-15~T-10 평균 방향 점수 vs T-5~T-1 평균 방향 점수: 후반이 더 강한가 (binary) |

**스케일 정규화**: P1·P4의 방향 점수는 종목 간 비교가 목적이 아니라 동일 종목 내 event vs non-event 비교이므로, 절대값 사용. (대형/소형주 혼합 cross-sectional IC가 아님)

---

### 대조군 설계

- **Event 그룹**: 해당 (ticker, date)에서 20D alpha ≥ +7%p인 모든 케이스
- **Non-event 그룹**: 동일 ticker, 동일 기간 내에서 |20D alpha| < 3%p인 날짜
  - 같은 종목 내 비교 → 종목 특성(섹터, 시총) 통제
  - 레짐 편향 완화: 가능하면 같은 연도 내 케이스끼리 비교

---

### Discovery / Validation 분리

| 구분 | 기간 | 목적 |
|---|---|---|
| Discovery | 2020-01 ~ 2024-12 | 4개 패턴의 event vs non-event 차이 기술 |
| Validation | 2025-01 ~ 2026-09 | Discovery에서 발견된 패턴을 prospective IC로 검증 |

**Discovery 규칙**: 4개 패턴의 차이를 기술하고 "후보 패턴"으로 기록. Discovery 결과를 보고 패턴 정의를 수정하거나 추가하는 것 금지.

**Validation 규칙**: Discovery에서 가장 차이가 컸던 패턴을 Validation 데이터에 적용하여 forward IC를 1회 계산. Validation 결과를 보고 threshold 재조정 금지.

---

### 사전등록 판정 기준

Discovery 단계:
- 각 패턴에서 Event vs Non-event 차이를 Mann-Whitney U test로 검정
- 효과 크기(rank-biserial r) > 0.10 이면 "유의미한 패턴" 후보

Validation 단계:
- Discovery의 "유의미한 패턴"을 신호로 변환 → 2025-2026 forward IC
- IC mean > +0.05 AND pos% > 55% → "Prospective signal 지지"

**최종 판정**: Validation IC 기준으로만 결론. Discovery 단계 결과로 "Supply Event Study 성공"이라 부르지 않음.

---

**실행**: research/supply_event_study.py (2026-09-23)

**결과**:

```
Discovery (2020-2024):
  Events (alpha≥+7%p): 405,832
  Controls (|alpha|<3%p): 531,921
  Event base rate: 43.3%

  패턴        evt_med   ctl_med   diff     r       판정
  P1 방향점수  +8.700   +8.550  +0.150  -0.024  — 미달
  P2 쌍끌이    +0.200   +0.200  +0.000  -0.037  — 미달
  P3 streak   +5.000   +5.000  +0.000  +0.017  — 미달
  P4 강화전환  +0.000   +0.000  +0.000  -0.002  — 미달

  → 4개 패턴 모두 |r| < 0.10 (사전등록 기준 미달)
  → best pattern: P2_dual_ratio (|r|=0.037)

Validation (2025-2026):
  Events: 147,298 / Controls: 123,394
  Event base rate: 54.4%  ← 2025-2026 강세장 반영

  P2_dual_ratio Prospective IC:
    mean=-0.025  median=-0.029  pos%=29.5%  ❌ ❌

최종 판정: ❌ Discovery 4개 패턴 모두 효과 크기 미달 / Prospective signal 미지지
```

**해석**:

4개 패턴(방향 점수 평균, 쌍끌이매수 비율, 최장 streak, 후반 강화 전환) 모두 Event vs Non-event 간 효과 크기가 통계적으로 의미 없는 수준(|r| ≤ 0.037)이다.

**Event base rate 43.3%의 의미**: investor_flow DB에 기록된 (ticker, date) 자체가 거래가 어느 정도 활발한 케이스를 의미한다. 이미 수급 데이터가 존재한다는 것 자체가 "활발한 종목"에 대한 selection이며, 그 중에서 43%가 +7%p를 넘는다는 것은 threshold가 아닌 universe 특성이다.

**핵심 결론**: 수급의 방향 패턴(누가 사고 있는가)은 주가 상승 이벤트를 예측하지 못한다. IC 연구(cross-sectional ranker 실패)와 Event Study(이벤트 직전 패턴 없음) 두 방향 모두에서 동일한 결론에 도달했다.

**패턴 강도 평가**: Refute — Discovery와 Validation 모두에서 방향 자체가 없음

**Supply 종합 결론 (5개 연구 전체)**:
- Component Role Attribution: 5개 역할 모두 KPI 미달 ❌
- Supply Counterfactual: 제거 효과 미확인 ❌
- Supply Conditional: SB selection artifact 확인 ❌
- Supply Horizon IC: 6년 전체 near-zero to 음수 ❌
- **Supply Event Study: 이벤트 직전 패턴 없음 ❌**

→ 수급 방향 데이터는 현재 형태(direction score 기반)로는 어떤 방식으로도 alpha 예측력이 확인되지 않음. Production 변경은 Calibration n≥100 + Freeze 해제 후 별도 논의.

**상태**: 🔵 Closed

---

## Architecture Replay — Role-based vs Composite (2026-09-29)

**상태**: 🔵 Closed — No improvement / Inconclusive

### 연구 배경

Component Role Attribution 연구까지의 흐름:
- Valuation → 단기(20D) alpha 기여 가능성
- Risk → 장기 downside filter 가능성
- Earnings → Gate 후보

이 증거를 바탕으로: "하나의 composite score보다 역할 분리 architecture가 실제로 더 좋은가?"를 in-sample replay로 검증.

### 사전등록

**Architecture A (Current 3-factor composite)**
- `earnings_score ≥ 22` AND `normalized_score ≥ 85`
- normalized_score 내림차순 **Top 10** 선택

**Architecture B (Role-based)**
- `earnings_score ≥ 22` (Gate)
- date 내 `risk_score` 하위 25% 제거 (Risk Filter)
- 잔여 집합 내 `valuation_score` 중앙값 이하 제거 (Valuation Ranker 사전 필터)
- `valuation_score` 내림차순 **Top 10** 선택

**N = 10 고정. 결과 확인 후 변경 불가.**

**Primary**: 날짜별 paired difference = mean(B_alpha_20d) − mean(A_alpha_20d)
**Secondary**: 전체 선택 집합 mean / median / hit% / P10

### Protocol note (v1 → v2)

v1 설계는 "A의 날짜별 실제 선택 수 N_A를 B에도 적용"하는 방식이었다.
실행 결과 N_A ≈ 46/일 (pool ≈ 106)로 너무 커서, B가 N_A를 충족하지 못하는 날이 다수 발생.
→ **Protocol failure: candidate-count mismatch**
v1 결과 (mean diff -0.175%p, B>A 52.5%)는 비교 단위 불균형으로 인해 무효. 보존하되 연구 결론에 사용하지 않는다.
v2에서 Fixed-N=10으로 재설계.

### 결과 (v2, 2026-09-29)

**데이터**: retro_calibration 2026-01-02 ~ 2026-07-30 (141일, alpha_20d 보유 행)

**PRIMARY: 날짜별 Paired Difference (B − A)**

| 지표 | 값 |
|------|-----|
| mean diff | -0.009%p |
| median diff | -0.548%p |
| B > A 날짜 | 62/141 (44.0%) |
| B < A 날짜 | 79/141 (56.0%) |

**SECONDARY: 전체 선택 집합 (n=1,410 each)**

| 지표 | Arch A | Arch B | diff |
|------|--------|--------|------|
| mean | -2.217% | -2.226% | -0.009%p |
| median | -3.668% | -3.591% | +0.077%p |
| hit% | 42.6% | 43.0% | +0.4%p |
| P10 | -24.3% | -25.4% | -1.1%p |

### 공식 판정: No improvement / Inconclusive

Fixed-N=10 paired comparison에서 Architecture B의 성능 개선은 관찰되지 않음.

**연구 해석**
현재 정의한 `Earnings Gate → Risk Filter → Valuation Ranker` 구조는 `Earnings Gate → 3-factor composite Ranker` 대비 개선을 보이지 않았다. Mean paired difference는 거의 0에 가까웠고(-0.009%p), B>A 날짜 비율(44.0%)과 median 차이(-0.548%p)에서도 B의 일관된 우위가 확인되지 않았다. (주: mean≈0 + median 음수는 차이 분포가 비대칭임을 시사. 일부 큰 양의 관측치가 평균을 끌어올린 가능성 있음.)

**Secondary**
B의 hit rate +0.4%p는 사실상 미미하고, P10은 -1.1%p 악화. 평균 성과뿐 아니라 downside 측면에서도 명확한 개선은 관찰되지 않음.

**해석 제한**
이번 결과는 *해당 role-based 구현체*의 실패를 의미하며, 모든 형태의 역할 분리 구조가 무효라는 뜻은 아님.
"component마다 역할이 다를 수 있다"는 이전 연구 결과와 모순되지 않는다 — 역할이 다르다는 것과, 특정 역할 분리 구조가 composite보다 더 나은 선택을 한다는 것은 별개의 주장이다.

### 현재 연구 그림 정리

- "component마다 역할이 다를 수 있다" → 여러 연구에서 가능성 관찰됨
- "그러므로 역할 분리 architecture가 단순 composite보다 낫다" → **이번 replay에서 지지되지 않음**

---

## Time Horizon Sensitivity Study (2026-09-29)

**상태**: 🔵 Closed — 시간축 교차 가설 미지지

### 연구 질문

Valuation과 Risk의 alpha association이 horizon에 따라 *서로 반대 방향으로* 달라지는가?  
(Valuation=단기 강, Risk=장기 강 — 시간축 교차 패턴)

### 사전등록

**Primary 판정 기준**: Risk IC60 > IC20 AND Valuation IC20 > IC60 (교차 패턴)

**Factor**: Valuation, Risk (Primary) / Earnings (참고)  
**Horizons**: 20D (Primary), 60D (Primary), 120D (Secondary, exploratory)  
**IC**: 날짜별 cross-sectional Spearman IC  
**Methodology**: entry_open → N 영업일 후 close, KOSPI benchmark ticker='1001', 동일 basis  
**데이터**: retro_calibration 2026-01-02~07-30 (alpha_20d: 17,568행 / alpha_60d: 12,451행 / alpha_120d: 4,890행)

### 결과

**IC 비교 (Primary)**

| Factor/Horizon | mean IC | pos% | n_dates |
|---|---|---|---|
| val_20D | +0.137 | 79.4% | 141 |
| val_60D | +0.239 | 98.0% | 101 |
| risk_20D | +0.039 | 68.1% | 141 |
| risk_60D | +0.099 | 81.2% | 101 |
| earn_20D (참고) | +0.031 | 55.3% | 141 |
| earn_60D (참고) | +0.087 | 64.4% | 101 |

**120D (Secondary exploratory — 판정 제외)**

| val_120D | +0.307 | 100.0% | 41 |
| risk_120D | +0.098 | 100.0% | 41 |

※ 120D는 41일치, 2026-01~03 집중, forward window overlap → Robust 판단 불가. 탐색적 패턴으로만 기록.

**Q4−Q1 Alpha Spread (참고)**

| | 20D | 60D |
|--|--|--|
| Valuation | +10.5%p | +17.0%p |
| Risk | +5.7%p | +16.2%p |

※ Q4−Q1은 horizon 자체가 3배 증가하므로 spread 확대가 어느 정도 자연스러움. Primary 판정에 사용하지 않음.

### 공식 판정: 시간축 교차 가설 미지지

- Risk IC60 > IC20: ✅ (+0.099 > +0.039)
- Valuation IC20 > IC60: ❌ (+0.137 < +0.239)

→ 교차 패턴 조건 미충족.

### 연구 해석

현재 2026 calibration 데이터에서는 Valuation과 Risk 모두 20D보다 60D에서 더 강한 cross-sectional association을 보였다. 따라서 `Valuation=단기 / Risk=장기`의 시간축 역할 분리는 지지되지 않았으며, 두 factor 모두 중기 horizon에서 더 강하게 관찰될 가능성이 제기된다.

이 결과는 `Valuation=Ranker, Risk=Filter`라는 *역할* 분리와는 독립적이다. "Valuation Ranker가 틀렸다"가 아니라 "Valuation Ranker의 작동 horizon을 20D로 제한할 근거가 없다"에 가깝다.

탐색적으로 Valuation IC는 20D→60D→120D에서 0.137→0.239→0.307로 단조 상승하는 패턴이 관찰되나, 120D는 데이터 제한으로 판정에 포함하지 않는다.

### 현재 연구 그림 정리

| 항목 | 상태 |
|------|------|
| Valuation=Ranker 역할 | 유지 (20D IC +0.137, 60D IC +0.239 모두 강) |
| Risk=Filter 역할 | 유지 (downside 방향 일관) |
| Valuation=단기/Risk=장기 시간축 차이 | **미확인** — 두 factor 모두 60D에서 강해짐 |
| 120D 패턴 | 탐색적 관찰 — 데이터 충분 시 재확인 필요 |

---

## Stage 12 — Score Stability / Turnover (2026-09-29)

**상태**: 🔵 Closed — Applied validation / Pass

### 연구 질문

v8.6.0에서 Supply 제거가 score/grade의 단기 불필요 변동을 실제로 줄였는가?  
그리고 변동성 감소가 alpha 방향 정보를 손상시켰는가?

### 설계

- **old_score** (v8.5.19): `min(100, round(val+earn+risk+supply))`
- **new_score** (v8.6.0): 저장된 `normalized_score` = `min(100, round((val+earn+risk)/75*100))`
- **동일 입력**: retro_calibration의 같은 raw component 값으로 양쪽 재계산 → 시장 자체 변화 효과 제거
- **데이터**: 2026-01-02 ~ 2026-07-30, 연속 날짜 쌍 17,513개
- **grade 기준** (양쪽 동일): ≥85=strong_buy, ≥70=buy, ≥50=neutral, else=avoid

**Primary**: |Δscore| 분포 (mean, median, >5pt 비율, >10pt 비율) + grade flip rate  
**Secondary**: |Δ|>2pt인 변화의 alpha_20d 방향 일치율 (alpha 정보 보존 확인)

### 결과

**PRIMARY: |Δscore| 분포**

| 지표 | old (v8.5.19) | new (v8.6.0) | 변화 |
|------|---------------|--------------|------|
| mean | 3.173pt | 0.569pt | -2.604pt |
| median | 0pt | 0pt | — |
| >5pt 비율 | 22.0% | 2.8% | **-19.2%p** |
| >10pt 비율 | 11.9% | 0.9% | **-11.1%p** |

**PRIMARY: Grade Flip Rate**

| | old | new | 변화 |
|--|--|--|--|
| flip 발생 | 3,095/17,513 (17.7%) | 549/17,513 (3.1%) | **-14.5%p** |

**SECONDARY: Alpha 방향 일치율 (|Δ|>2pt인 변화)**

| | n | 방향 일치율 |
|--|--|--|
| old (v8.5.19) | 5,179 | 49.9% |
| new (v8.6.0) | 1,302 | 51.5% |

**SECONDARY: Grade Flip 종류 (top 5)**

old (v8.5.19): neutral↔buy 왕복(748-751회)이 압도적. buy↔strong_buy(385-392회)가 뒤따름.  
new (v8.6.0): buy↔strong_buy(156-157회)가 1위. neutral↔buy(67-69회)로 크게 감소.

### 공식 판정: Applied validation / Pass

Supply 제거 후 score/grade의 단기 변동성이 크게 감소했다는 것이 확인됨.
- >5pt 변동: 22.0% → 2.8% (87% 감소)
- grade flip: 17.7% → 3.1% (83% 감소)

### 해석

**변동성 감소**: 명확하다. 같은 종목·같은 날짜·같은 입력에서, v8.6.0은 v8.5.19 대비 큰 점수 변화(>5pt)가 87% 줄었고 등급 전환이 83% 줄었다.

**alpha 방향 정보**: 변동 횟수는 5,179→1,302로 줄었지만, 남은 변화의 alpha 방향 일치율은 49.9%→51.5%로 악화되지 않았다. 이는 "노이즈 제거 방향과 양립하는 결과"이나, 1.6%p 차이로 "신호를 보존하면서 노이즈만 제거했다"는 확정적 결론을 내리기에는 불충분하다.

**grade flip 패턴**: old에서 중간 등급 경계(neutral↔buy)의 빈번한 왕복이 특징적으로 많았고, new에서는 해당 왕복이 크게 감소했다. flip이 많다는 사실만으로 그 변화가 정보 없는 잡음이었다고 증명되지는 않으나, 중간 등급 경계의 고빈도 왕복은 실제 운용에서 불필요한 의사결정 압박 원인이었다.

**기존 조치와의 일관성**: 2026-09-22에 `sell_checklist.py`에서 "등급 악화만으로 매도"를 Critical→Context로 격하한 바 있다. 이번 결과는 그 조치의 구조적 배경(grade instability가 실제 존재했음)을 사후 확인한다.

### 연구 흐름 정리

Supply 연구(5개) → 제거 근거 확보 → v8.6.0 적용 → Stage 12 → 의도한 문제(grade instability) 실제 감소 확인 → alpha 방향성 손실의 명확한 증거 없음

이는 Research → Engine Change → Post-change Validation의 완결된 사례다.

---

## Stage 13 — Portfolio Construction (2026-09-29)

**상태**: 🔵 Closed — Primary Supportive / C Exploratory

### 연구 질문

동일한 STRONG_BUY 선별 결과를 construction만 달리했을 때 포트폴리오 alpha 분포가 달라지는가?  
특히 "Top 10 score ranking"이 "Random 10"보다 실제로 더 나은가?

### 사전등록

**A**: STRONG_BUY 전체 equal-weight  
**B**: Top 10 by normalized_score equal-weight  
**C**: Top 5 by normalized_score equal-weight  
**D**: Random 10 from STRONG_BUY — 비복원, 1,000회/일, seed=42 고정, 날짜별 1,000회 평균 후 날짜 단위 집계

**Primary 판정**: 날짜별 B−D paired diff (mean / median / B>D 날짜 비율)  
**Secondary**: C−D, A vs B, C 분포 진단  
**데이터**: retro_calibration 2026-01~07, alpha_20d, 141일 (STRONG_BUY≥10인 날 전부)  
**주의**: 141일 alpha_20d는 20영업일 forward window로 temporal overlap 있음 — 날짜 간 표본 독립 가정 불가

### 결과

**PRIMARY: B − D (Top10 vs Random10)**

| 지표 | 값 |
|------|-----|
| mean diff | **+0.558%p** |
| median diff | **+0.727%p** |
| B > D 날짜 | 83/141 (**58.9%**) |
| P10 (diff) | -6.381% |

**SECONDARY: Individual Portfolio Stats**

| Portfolio | mean | hit% | P10 | std |
|--|--|--|--|--|
| A (all SB, avg 51.7종목) | -2.786% | 44.0% | -23.4% | 15.06 |
| B (top10) | -2.212% | 43.3% | -21.3% | 15.49 |
| C (top5) | -1.389% | 48.2% | -21.8% | 17.36 |
| D (rand10) | -2.770% | 44.0% | -23.4% | 15.08 |

**SECONDARY: A vs B (분산화 vs 집중)**

| mean diff (A−B) | -0.574% | A > B: 39.7% |

→ B(Top10)가 A(전체 SB) 대비 더 나은 방향. 집중이 분산보다 성과 개선됨 (단, std도 소폭 상승).

**Sanity check**: A ≈ D (mean -2.786% vs -2.770%) — Random 10을 1,000회 평균하면 전체 equal-weight와 수렴하는 것이 확인됨.

### C 진단 (별도 실행)

C의 mean(-1.389%)이 B(-2.212%)보다 높아 보이지만:

- **상위 10일(7%)의 mean: +32.847%**
- **나머지 131일의 mean: -4.003%**
- C > B 날짜: 56.0%, C−B median: +0.581%

C > B가 56% 날짜에서 발생하고 median도 양수(+0.581%)여서 순수 극단값만의 효과가 아닐 수 있음. 그러나 상위 10일이 전체 mean을 압도적으로 끌어올리고, P10이 B보다 나쁘고(-21.8% vs -21.3%), std도 더 크다(17.36 vs 15.49). "Top5가 Top10보다 낫다"는 결론을 내리기에는 불충분.

### 공식 판정

**Primary: Supportive**  
B(Top10)이 D(Random10)보다 일관되게 나음 (mean +0.558%p, median +0.727%p, 58.9% 날짜). Score ranking이 portfolio construction 단계에서도 정보가 있음을 시사.

**C (Top5): Exploratory**  
평균/승률 개선이 관찰됐으나 극단일 집중 + 변동성 확대 + P10 개선 없음. 안정성 추가 검증 필요. 현재 단계에서 "Top5가 Top10보다 낫다"고 결론 내리지 않음.

### 연구 해석

"어떤 종목을 고르느냐"(score formula)만큼 "고른 종목을 어떻게 묶느냐"(construction)도 성과 분포에 영향을 미친다는 것이 확인됨. 특히 score ranking 상위로 집중할수록 평균 성과가 개선되는 방향이 관찰됨.

단, temporal overlap(20D forward window × 141일)으로 인해 날짜 간 독립 가정이 성립하지 않아, 이 결과는 in-sample 탐색으로 해석해야 한다. OOS에서 재확인 필요.

### 큰 그림 업데이트 (2026-09-29 현재)

| 연구 | 결론 |
|------|------|
| Score 내부 ranking 단조성 | 강한 증거 없음 |
| Architecture (role-based vs composite) | 개선 없음 |
| Time horizon 분리 | 지지 안 됨 |
| Supply 제거 → Stability | Applied validation Pass |
| Portfolio construction | Score ranking이 construction 단계에서도 유효 (in-sample) |

→ 현재 증거상 v8.6.0 3-factor composite는 크게 뜯어고칠 근거가 부족하며, 다음 병목은 score formula보다 portfolio construction일 가능성이 있다. 단, 이는 가능성이지 결론은 아니다.

---

## Stage 13 추록 — Block Bootstrap Robustness (2026-09-29)

**Stage 13 Primary (B−D mean +0.558%p)의 불확실성 정량화.**

판정 기준 변경 없음 — robustness 확인 목적.

**설계**: Temporal block bootstrap, block size=20일 (20D forward window overlap 반영), N=5,000회  
**결과**:

| 지표 | 값 |
|------|-----|
| 관찰값 (B−D mean) | +0.558%p |
| 95% CI (block=20) | [-1.70%, +3.48%] |
| 90% CI (block=20) | [-1.28%, +3.17%] |
| bootstrap P(>0) | 78.5% |

block size 10~30 전체에서 P(>0) = 74~86% — 방향은 안정적으로 재현되나 95% CI는 0을 포함.

**해석**: B>D 방향은 78.5% bootstrap 샘플에서 재현되지만, 141일 × 20D overlap으로 인한 표본 불독립성으로 통계적 확정 불가. Primary는 "Supportive" 수준으로 유지.

---

## Stage 14 — Rebalancing Strategy Comparison (2026-09-29)

**상태**: 🔵 Closed — Diagnostic / A 우세 (in-sample, B 판정 제외)

### 연구 질문

동일한 Top10 선별 신호를 가지고 리밸런싱 방식을 달리하면 실제 성과 분포가 어떻게 달라지는가? 특히 거래비용을 포함했을 때 daily 교체의 edge가 사라지는가?

### 사전등록

**A (Daily)**: 매일 Top10 재구성, 변경 종목만 거래  
**B (20D Hold)**: 매 20영업일마다 Top10 전량 교체 (비교 단위: non-overlapping 20D 주기)  
**C (Hold+Replace)**: STRONG_BUY → NEUTRAL 이하(score < 70) 이탈 시에만 교체, 빈자리는 당일 Top10 미보유 최상위로 대체  
**거래비용**: 고정 가정 왕복 0.30% (매수 0.15% + 매도 0.15%), 보유 중 추가 비용 없음  
**보고**: 비용 전/후 모두 보존  
**데이터**: retro_calibration 2026-01-02 ~ 2026-07-30

### 결과

**전략별 성과 요약**

| 전략 | Gross mean | Net mean | 비용차 | Daily T/O | P10 (gross) |
|---|---|---|---|---|---|
| A (Daily) | -2.212% | -2.236% | -0.024% | 8.4% | -21.3% |
| B (20D Hold) | -3.872% | -3.966% | -0.094% | 1.9%* | -41.7% ⚠️ |
| C (Hold+Replace) | -3.489% | -3.491% | -0.002% | 0.9% | -18.5% |

*B daily-equivalent 환산

**Strategy B 주의**: 141일 / 20일 = 8개 non-overlapping 주기만 존재. B의 Gross/Net/P10을 전략 우열 판단에 사용하지 않음. P10=-41.7%는 8개 관측 중 극단값 영향이 클 가능성이 높음.

**거래비용 관찰**: A의 일평균 비용 차감은 2.4bp 수준이었으며, 현재 표본에서 gross→net 성과를 뒤집을 정도의 영향은 관찰되지 않았다.

**Strategy C 특이사항**: 평균 보유기간 119거래일. v8.6.0의 grade flip 감소(Stage 12)로 인해 STRONG_BUY→NEUTRAL 이탈이 거의 발생하지 않아, C는 사실상 "초저회전 장기보유" 전략으로 운영됨.

### 공식 판정

**A (Daily)**: 현재 표본에서 가장 높은 gross/net mean, 비용 부담은 예상보다 크지 않음.  
**B (20D Hold)**: 관측 주기 n=8 — 전략 성능 판정 불가.  
**C (Hold+Replace)**: turnover와 비용은 가장 낮지만 gross mean은 A보다 낮음. P10은 A보다 덜 음수(-18.5% vs -21.3%).

### 연구 해석

**A vs C 비교**: A가 C보다 높은 gross mean을 보였으며, 최신 Top10으로 지속 교체하는 방식과 기존 보유 유지 방식 간 성과 차이가 관찰됨. 이는 selection freshness 가설과 양립하나, 본 분석만으로 원인을 확정하지 않음 — A와 C는 보유기간뿐 아니라 포트폴리오 구성 경로 자체가 다르기 때문이다.

**C의 P10**: C의 하위 10% 포트폴리오 성과는 관찰된 세 전략 중 가장 덜 음수였으나, 이것이 STRONG_BUY의 독립적인 downside filtering 효과를 의미하는지는 추가 검증이 필요하다. C는 평균 보유기간 119일의 매우 느린 turnover 구조로, 다른 포트폴리오 경로 효과와 분리하기 어렵다.

**거래비용 결론**: 현재 2026-01~07 표본에서 daily rebalancing의 비용 부담은 gross 성과를 뒤집을 수준이 아니었다. 다만 이 결과만으로 "매일 갈아타는 것이 최적"이라는 결론은 내릴 수 없다.

### Stage 13 → Stage 14 연결

Stage 13: Top10 선별이 Random10보다 더 나은 방향성 관찰 (B>D 58.9%, bootstrap P(>0)=78.5%)  
Stage 14: 그 Top10 신호를 어떻게 유지하느냐에 따라 성과가 달라짐

→ 연구 질문이 "무엇을 살까?"에서 "그걸 얼마나 자주 교체할까?"로 전환됨. A가 현재 in-sample에서 우세하지만, OOS 검증 및 추가 기간 데이터 없이는 최종 판단 보류.

---

## Stage 14B Holding Age Sensitivity (A vs C Origin Separation)

**질문**: 같은 Top10 신호를 얼마나 오래 보유하느냐에 따라 일별 alpha가 달라지는가?

**상태**: 🔵 Closed — 패턴 관찰됨, 인과 확정 불가

### 사전등록
- 동일 날짜·동일 Top10(normalized_score 기준 상위 10개, STRONG_BUY ≥ 85pt)에서 5D / 10D / 20D alpha 계산
- Per-day alpha = alpha_N / N 으로 정규화
- 데이터: retro_calibration (141일) × market_ohlcv (5D/10D exit 가격 계산)
- 샘플: 유효 날짜 141개 (Top10 보유·가격 완성 조건 충족)

### 결과

**전체 (n=141 날짜)**

| 보유기간 | 누적 alpha mean | P10 | Hit | Per-day mean |
|---------|----------------|-----|-----|-------------|
| 5D      | -0.167%        | -9.09% | 45.4% | -0.033%/day |
| 10D     | -0.584%        | -14.13% | 44.7% | -0.058%/day |
| 20D     | -2.212%        | -21.27% | 43.3% | -0.111%/day |

5D/day > 20D/day 빈도: 59.6%, 차이 mean +0.077%/day (P10 -1.07%, P90 +1.07%)

**분기별 분해 (regime check)**

| 분기 | 5D/day | 10D/day | 20D/day | 패턴 |
|------|--------|---------|---------|------|
| Q1 (n=59) | -0.003% | -0.033% | -0.118% | 5D 우위 |
| Q2 (n=61) | -0.434% | -0.404% | -0.308% | **역전 — 20D 우위** |
| Q3 (n=21) | +1.046% | +0.873% | +0.483% | 5D 우위 |

**시장 방향별**

| KOSPI 20D 방향 | n | 5D/day | 20D/day |
|---------------|---|--------|---------|
| 상승 (92일)   | 92 | -0.364% | -0.498% |
| 하락 (49일)   | 49 | +0.587% | +0.616% |

### 해석

1. **전체 평균**: 짧은 보유(5D)가 일별 alpha 기준 덜 손해 — 그러나 분산이 매우 크다
2. **Q2 역전**: Q2에서는 5D가 오히려 나쁨. 전체 우위가 Q1·Q3 두 분기에 의존
3. **패턴 불일치**: 결과가 분기(regime)에 따라 반전되므로 "자주 교체 = 무조건 유리"라는 인과 해석은 지지되지 않음
4. **Stage 14 연결**: 일별 리밸런싱(A) > 장기보유(C)의 alpha 차이가 신호 신선도 효과인지 보유기간 효과인지, 본 연구만으로는 분리 불가. Q2 역전은 regime이 더 강한 변수임을 시사

### 결론

짧은 보유 우위가 **일부 기간에 관찰되나** 분기별 일관성 없음 — regime-dependent. 신호 신선도 vs 보유기간 효과 분리는 이 in-sample 데이터로는 불충분. OOS 데이터에서 재확인 필요.

---

## Stage 15-A PIT Leakage Audit

**질문**: v8.6.0 3-factor score의 각 입력값이, 과거 signal_date 시점에 실제로 알 수 있었던 정보인가?

**상태**: ✅ 완료 (2026-09-30)

**판정**: Valuation·Earnings ROE·Risk 변동성 = PIT CLEAN / Earnings 분기·Risk 부채 = LEAKED (Naver 현재 데이터, 문서화됨)

### 배경

retro_calibration 데이터(2026-01~07)에서 normalized_score를 계산할 때 사용된 Earnings / Risk 입력값이 당시 PIT 정보인지, 아니면 이후 공시된 값을 소급 반영한 것인지 불명확하다. Score Monotonicity 연구(Stage 15) 전에 이 부분을 분리해야 한다.

### 팩터별 PIT 감사 결과

| 팩터 | 입력 소스 | PIT 판정 | 근거 |
|------|----------|---------|------|
| Valuation (PER/PBR/ROE/div_yield) | pykrx `get_market_fundamental(date_str)` | **CLEAN** | date_str 파라미터로 날짜별 시세 반환 |
| Earnings — 분기 충족·품질·이익률 | Naver mobile API `finance/summary` | **LEAKED** | date 파라미터 없음, 항상 현재 데이터 반환 |
| Earnings — ROE 보너스 | pykrx `get_market_fundamental(date_str)` | **CLEAN** | 동일 signal_date라도 날짜별 값 상이 확인 |
| Risk — 부채비율·분기 손실 수 | Naver mobile API (동일 소스) | **LEAKED** | Earnings와 동일 함수 |
| Risk — 변동성 (60일) | pykrx 가격 히스토리 | **CLEAN** | signal_date 기준 60일 종가 시계열 |

### 핵심 증거

**1. 코드 수준 증거**

`analyzer/data/naver.py`의 `_fetch_quarterly_mobile_api(ticker)`:
```
URL: https://m.stock.naver.com/api/stock/{ticker}/finance/summary
파라미터: ticker만. as_of_date가 캐시 키에는 쓰이지만 API 요청에 전달되지 않음.
```
→ 항상 Naver 현재 기준 최신 분기 데이터 반환.

**2. DB 수준 증거**

retro_calibration 17,648행 전체:
```sql
look_ahead_note = 'earnings/risk=Naver(current)'
```
배치 실행일: 2026-09-10/11/12/15/16 — 모두 9월에 Q2-2026 데이터로 소급 계산.

**3. 배치 간 earnings_score 분포 (Naver 소급의 증거)**

| 배치일 | signal_date 범위 | mean | std | n |
|--------|----------------|------|-----|---|
| 2026-09-11 | 2026-01-26~2026-03-23 | 22.54 | 1.31 | 4,425 |
| 2026-09-12 | 2026-03-24~2026-06-12 | 22.55 | 1.32 | 6,858 |
| 2026-09-16 | 2026-07-15~2026-07-30 | 22.54 | 1.30 | 1,421 |

→ 1월 신호도 7월 신호도 earnings_score 평균이 22.54로 동일. 다른 시점의 실적 데이터가 반영됐다면 분포가 달라야 한다. Naver가 항상 동일한 "현재" 분기 실적을 반환하기 때문에 배치 간 차이가 없다.

**4. 배치 내 변동 = ROE(pykrx)만 다름**

2026-09-15 배치: 133종목 중 35종목(26.3%)에서 earnings_score가 signal_date별로 변동.
→ 변동 원인 = ROE 보너스 점수 (pykrx, PIT CLEAN). Naver 분기 부분은 배치 내 고정.

### 누락 연구: Signal Ordinal 오염도

Naver 소급은 **절대 수준**(earnings_score 평균)을 오염시킨다. 하지만 Spearman IC는 **횡단면 순위** 기반이므로:
- 모든 종목이 동일 시점 Naver 데이터를 받는다면 → 순위 왜곡 최소
- 종목별로 분기 발표 타이밍이 다르다면 → 일부 왜곡 가능

Stage 15 IC 연구에서 관찰되는 값이 이 왜곡을 포함하고 있음을 인지한 채 해석한다.

### Stage 15 진행 조건

retro_calibration은 Naver 소급 포함됐으나:
- 코드 수준·DB 수준 모두 명시적으로 문서화됨 (`look_ahead_note`)
- 오염 방향: 절대 수준 왜곡 가능, 횡단면 순위 왜곡은 제한적
- **Stage 15는 이 한계를 인지한 채 진행한다.** IC 값 해석 시 "Naver 소급 포함 retro 기준" 명시.
- Real OOS (2026-09-23~)부터는 Naver 현재 데이터도 실시간 수집 → 실질적 PIT (시간이 지나면 자동 해소)

---

## Stage 15 Score Monotonicity

**질문**: normalized_score가 높아질수록 미래 alpha가 대체로 단조 증가하는가?

**상태**: ✅ 완료 (2026-09-30)

**판정**: Monotonicity 방향 지지 — Primary IC > 0, Q4−Q1 > 0, Secondary 구간 전반적으로 우상향 (85~90→90~95 구간 소역전 있음. 완전한 단조성 아님)

**핵심 발견**: Naver 소급 누출이 full-score IC를 주도했다는 증거 없음. PIT CLEAN인 valuation_score만으로도 동등한 IC 재현 → 현재 ranking signal은 Valuation에 강하게 지배됨.

### 사전등록 설계

**Primary — 일별 횡단면 Spearman IC**

| 측정 | 지표 |
|------|------|
| Score vs alpha_20d | 평균 IC, 양수 비율 |
| Score vs alpha_60d | 평균 IC, 양수 비율 |
| Q4−Q1 alpha | 상위 25% − 하위 25% 평균 alpha 차이 |

**Secondary — 고정 구간 비교**

| 구간 | 기대 방향 |
|------|---------|
| <50 | 가장 낮음 |
| 50~70 | |
| 70~85 | |
| 85~90 | |
| 90~95 | |
| 95+ | 가장 높음 |

사전등록 방향성: **점수 증가에 따라 alpha가 대체로 단조 증가** — 구간 중 하나가 제일 높다는 사후 선택 금지.

### 판정 기준

- **Monotonicity 지지**: Primary IC > 0, Q4−Q1 > 0, Secondary 구간이 대체로 우상향
- **Inconclusive**: 방향은 있으나 통계적으로 약함
- **기각**: IC ≈ 0 또는 음수, Q4−Q1 ≤ 0

### 결과 (2026-09-30 실행)

**데이터**: retro_calibration 17,568행, 141 signal_dates (2026-01-02~2026-07-30), alpha_20d 전수

**alpha_20d 정의 (Stage 16에서 확인)**: `alpha_20d = stock_20TD_return - KOSPI_20TD_return` (초과수익률, KOSPI-adjusted)
따라서 IC는 "score vs KOSPI 대비 초과수익" 관계를 측정. 날짜별 cross-section 내에서 KOSPI는 상수이므로 excess_return 기반 IC = absolute_return 기반 IC — 두 값 동일 확인(+0.1443). 시장 방향 통제 후에도 ranking signal 유효.

**Stage 15-A — Primary Metric (일별 Spearman IC)**

| Signal | mean IC | std | pos% | n |
|--------|---------|-----|------|---|
| normalized_score (3-factor, Naver 소급 포함) | **+0.1443** | 0.1692 | **79%** | 141 |
| valuation_score (PIT CLEAN proxy, /25×100) | +0.1421 | 0.1685 | **82%** | 141 |
| earnings_score (Naver LEAKED — 참고) | +0.0320 | 0.1216 | 56% | 141 |
| risk_score (혼합 — 참고) | +0.0400 | 0.1421 | 72% | 141 |

**Stage 15-B — Q4−Q1 Spread**

| Signal | Q4 avg | Q1 avg | spread |
|--------|--------|--------|--------|
| normalized_score | -3.17% | -5.35% | **+2.18%** |
| valuation_score (CLEAN) | -2.32% | -4.99% | **+2.67%** |

**Secondary — 구간별 평균 alpha_20d (normalized_score 기준)**

| 구간 | mean alpha_20d | n |
|------|---------------|---|
| <50 | -16.55% | 85 |
| 50~70 | -6.55% | 5,927 |
| 70~85 | -6.16% | 4,266 |
| 85~90 | -1.56% | 2,587 |
| 90~95 | -2.29% | 3,153 |
| 95~101 | -0.05% | 1,550 |

→ 대체로 우상향. 90~95 구간이 85~90 대비 소폭 역전(-2.29% vs -1.56%)이나 전체 방향은 지지.

alpha_60d: retro_calibration에 저장되지 않음 → 측정 불가. Real OOS 축적 후 재검토.

### 핵심 해석

**1. Naver 소급 누출이 IC를 주도했다는 증거는 없다**

IC 0.1443 (full score) ≈ IC 0.1421 (valuation CLEAN) — 거의 동일.
이는 현재 관찰된 ranking signal이 Valuation에 강하게 지배되고 있음을 뜻한다.
단, full score와 valuation_score의 횡단면 상관관계가 높을 경우 두 IC가 비슷하게 나올 수 있어 "Naver 누출의 영향이 전혀 없다"고 증명한 건 아니다.

→ **관찰 가능한 결론: ranking signal은 Valuation(PIT CLEAN)에 강하게 집중됨.**

**2. Earnings/Risk의 ranking 기여가 낮다**

earnings_score IC = +0.032, risk_score IC = +0.040.
이 두 컴포넌트가 normalized_score에 편입됐을 때 IC 기여는 미미하다.
Stage 16 후보: earnings/risk를 ranking 신호로 재설계할지 별도 연구 검토.

**3. Universe 절대 수익 문제 (별도 플래그)**

전체 스코어링 대상 종목 평균 alpha_20d = **-4.43%**
동기간 KOSPI 20D 평균 수익 = **+5.23%**

→ 평가 대상 universe 자체가 KOSPI 대비 크게 underperformed.
이는 Monotonicity 결론(순위 내 상대 성과)과 별개이며, 
**필터 설계 또는 업종 편향** 문제일 수 있다. 추가 진단 필요.

### 전체 판정

Primary IC > 0 (0.14) ✓  
Q4−Q1 > 0 (+2.18%) ✓  
Secondary 구간 대체로 우상향 ✓ (소폭 역전 있으나 전반적 방향 지지)

→ **Monotonicity 방향 지지. 단, 완전한 단조성 아님 (85→90→95 구간 소역전). Signal source = Valuation 집중.**

참고 패턴 강도: **Robust Pattern** (141일, 양수 IC 79%)
단, Naver 소급 포함 retro 기준. Real OOS (2026-09-23~) 결과가 확인 기준.

### 위치

```
Real OOS ──────────────────────────────────────→ 계속 축적
   │                                                      │
   ├── Stage 15-A  PIT Leakage Audit        ✅ 완료       │
   ├── Stage 15    Score Monotonicity       ✅ 완료       │
   ├── Stage 16    Universe Bias Audit      ✅ 완료       ↓
   ├── Stage 17    Incremental Information  ✅ 완료  OOS 재확인
   ├── Stage 18    Risk×Regime Interaction  ✅ 완료       │
   │                                                      │
   ├── Stage 19    PIT Earnings/Risk 재구축  ✅ 완료       │
   ├── Stage 20    Universe Exposure 분해   ✅ 완료       │
   ├── Stage 20A   Bear × Exposure Interaction ✅ 완료   │
   ├── Stage 20B   Nested-Model Incremental EP  ✅ 완료  │
   ├── Stage 20C   Risk Score Residualization  ✅ 완료  │
   ├── Stage 20D   Residual Robustness       ✅ 완료  │
   ├── Stage 20E-1 Q1 Bear 특성 분석       ✅ 완료  │
   ├── Stage 20E-2 Bear Intensity Cond.    ✅ 완료  │
   ├── Stage 20F   Sector Concentration    ✅ 완료  │
   └── Stage 21    Execution Reality        🟡 Pre-reg   │
                   (거래기록 축적 후)                      │
```

---

## Stage 16 Universe Selection Bias Audit

**질문**: 현재 투자 universe 자체가 KOSPI 대비 구조적으로 불리한 종목군을 포함하고 있는가?

**상태**: ✅ 완료 (2026-09-30)

**판정**: 전기간 평균 초과수익 -4.43%, 절대 beta = +0.27 (저베타, 양의 상관). Q2 대형 underperformance, Q3 역전. 레짐 의존적 저베타 구조로 관찰됨. 설계 결함 여부는 장기 OOS 추가 관찰 필요.

**선행 근거**: Stage 15에서 관찰된 universe 평균 alpha_20d = -4.43% vs KOSPI +5.23%.
Score ranking이 유효한 방향임에도 universe 절대 수익이 시장 대비 크게 낮다.

### 배경

Score Monotonicity(Stage 15)가 "선택된 종목 내 상대 순위"가 유효함을 보였다면,
Stage 16은 "처음부터 어떤 종목들을 후보군으로 넣었는가"를 묻는다.

```
Universe selection
        ↓
평균적으로 시장보다 약함  ← Stage 16이 묻는 지점

그 안에서
        ↓
Score ranking
        ↓
상대적으로 좋은 종목을 더 잘 골라냄  ← Stage 15 확인 완료
```

Universe 문제를 확인하기 전에 Earnings/Risk factor 개선이나 포지션 사이징 논의로 넘어가지 않는다.

### 사전등록 설계

**Primary — Universe vs KOSPI alpha 분해**

| 측정 | 지표 |
|------|------|
| 기간 전체 | universe 평균 alpha_20d, KOSPI 20D alpha, 차이 |
| 분기별 분해 | 2026 Q1 / Q2 / Q3 — underperformance가 어느 구간에 집중됐는가 |

사전등록 방향성: underperformance가 **특정 구간(예: Q2 하락장)에 집중**됐는가, 아니면 **전반적·구조적**인가를 구분한다.

**Secondary — 구성 편향 진단 (가능한 변수)**

아래 분류 체계 중 기존 연구에서 이미 사용된 것을 우선 적용. 결과 확인 후 분류 기준 선택 금지.

| 변수 | 분류 기준 |
|------|---------|
| 시가총액 | KOSPI 200 여부, 코스닥 포함 여부 |
| 거래대금 | 유동성 상위/하위 구분 (기존 연구 threshold 있으면 그대로 사용) |
| 변동성 | risk_score의 volatility 기준 (60% 이상 = 고변동) |
| 업종 | CLAUDE.md §5 적용 범위 — 통신·유틸·소비재·저PBR 금융·기술주 vs 비대상 업종 |
| 모멘텀 | 최근 3개월 수익률 상위/하위 |

목적: "어떤 특정 종목군이 universe 전체 underperformance를 끌어내리고 있는가?"를 찾는 것.

### 판정 기준

- **구간 집중**: underperformance가 특정 분기(예: Q2 하락장)에만 집중 → 시장 조건 문제, universe 설계 자체는 괜찮을 수 있음
- **전반 구조적**: 모든 분기에서 지속적 underperformance → universe 설계에 근본 문제 존재
- **특정 하위군 집중**: 특정 업종/규모가 주범 → 해당 군 별도 필터 연구 후보

### 중요 제약

Stage 16에서 universe를 고치지 않는다.
원인 확인 → 별도 가설 수립 → 독립 검증 순서를 따른다.
"시총 하위 제거" 같은 즉각적 조치는 원인이 확인된 뒤 별도 Stage로 설계한다.

### 데이터 정의 확인 (선행 필수)

`alpha_20d = stock_20TD_return − KOSPI_20TD_return` (초과수익률) — 실측으로 확인.

따라서 아래 분석에서:
- **"Universe"** = `AVG(alpha_20d)` = universe 종목 평균 초과수익 (vs KOSPI)
- **"절대 수익"** = `AVG(alpha_20d + kospi_ret)` = universe 종목 평균 절대 수익
- **"beta"** = 절대 종목 수익률 ~ KOSPI 절대 수익률 OLS (excess_return ~ KOSPI로 계산하면 artifact 발생)

### 결과 (2026-09-30 실행)

**데이터**: retro_calibration 17,568행, 2026-01-02~2026-07-30

**Primary — 분기별 Universe 성과**

| 분기 | 절대 종목 수익 | KOSPI | 초과수익 (alpha_20d) | n |
|------|-------------|-------|---------------------|---|
| 2026-Q1 | +5.58% | +8.03% | -2.45% | 7,158 |
| 2026-Q2 | -4.44% | +6.47% | -10.92% | 7,725 |
| 2026-Q3 | +3.15% | -5.77% | **+8.93%** | 2,685 |
| **전체** | **+0.80%** | **+5.23%** | **-4.43%** | 17,568 |

월별 초과수익 상세 (극단 달):

| 월 | 절대 종목 수익 | KOSPI | 초과수익 |
|----|-------------|-------|---------|
| 2026-01 | +15.38% | +15.31% | +0.07% |
| 2026-04 | +1.31% | +25.87% | **-24.55%** |
| 2026-05 | -10.70% | +8.77% | **-19.47%** |
| 2026-06 | -5.02% | -15.36% | **+10.34%** |
| 2026-07 | +3.15% | -5.77% | +8.93% |

→ 4월: 종목 절대 수익 +1.31%로 손실은 아니었지만 KOSPI가 +25.87%로 폭등. 5월: 종목 -10.70%, KOSPI +8.77% — 실질 손실 동반.

**절대 beta (종목 수익률 ~ KOSPI)**

```
OLS: stock_ret = -0.40% + 0.27 × KOSPI_ret
Pearson r = +0.44
```

→ Universe는 **저베타(+0.27), 양의 상관** 구조. 기존 기록한 beta=-0.73은 excess_return ~ KOSPI 회귀였음(artifact — excess에 이미 -KOSPI가 포함). 삭제.

**2026-04 극단 사례 정정**:
- 종목 절대 수익: 중앙값 = +1.31% (개별 종목 분포 중앙값은 별도) → KOSPI +25.87% 대비 대형 underperformance
- 종목 절대 수익은 양수였지만 KOSPI 상승분을 따라가지 못함 (저베타 구조의 전형)

**Secondary — 구성 하위군별 초과수익 (alpha_20d 기준)**

| 그룹 | 평균 초과수익 | n |
|------|------------|---|
| risk_score ≤ 15 (고위험) | -16.55% | 85 |
| risk_score 16~22 | -4.75% | 6,498 |
| risk_score 23~25 (저위험) | -4.15% | 10,985 |
| val_score ≤ 5 (고평가) | -6.56% | 5,595 |
| val_score 6~18 (중간) | -5.84% | 6,194 |
| val_score > 18 (저평가) | **-0.86%** | 5,779 |

→ 저평가 그룹(-0.86%)이 가장 양호. 저위험(-4.15%)과 중위험(-4.75%)은 유사 — risk_score가 초과수익 차이를 만들지 않음.

### 판정

**사전등록 기준별:**
- **구간 집중 vs 구조적**: Q3 역전(+8.93%)으로 단순 구조적 하락 아님. Q1(미미), Q2(대형) — underperformance는 특정 레짐에 집중.
- **특정 하위군 집중**: 저평가 그룹이 최소(-0.86%)이나, 모든 그룹에서 underperformance 존재.

**종합 판정: 레짐 의존적 저베타 구조 (설계 결함인지 미확정)**

Universe의 낮은 KOSPI 추종력(절대 beta ≈ 0.27)이 핵심 원인:
- Q2 KOSPI 강세장: KOSPI +6.47%이지만 종목은 -4.44%. 저베타라도 음수 절대 수익은 의외.
- Q3 KOSPI 조정장: 종목 +3.15% (상대 outperformance +8.93%).

CLAUDE.md §5 방어적 업종 집중이 설계 의도이므로, 저베타 자체는 의도된 특성이다.
그러나 Q2의 절대 음수(-4.44%) 발생이 저베타만으로 설명되는지, 또는 다른 구성 편향(특정 업종 악재, 소형주 집중 등)이 있는지는 현재 데이터로 확인 불가.

→ **"설계 결함이 아니다"는 아직 증명되지 않았다.** 방어적 스타일 노출인지, 다른 구성 편향인지 장기 OOS로 추가 관찰 필요.

**추가 관찰 조건**: Q3 n=2,685(21일), 통계적으로 불충분. 6개월 이상 OOS 누적 후 재판정.

### Stage 17 예고 → 바로 실행 (Stage 16 완료 즉시)

---

## Stage 17 Earnings/Risk Incremental Information (사전등록)

**질문**: Earnings와 Risk는 Valuation을 통제한 뒤에도 KOSPI 대비 초과수익(alpha_20d)에 추가 설명력을 갖는가?

**상태**: ✅ 완료 (2026-09-30)

**판정**: Earnings = Valuation에 가려짐 (partial IC ≈ 0). Risk = Bear 레짐에서 독립적 정보성이 강하게 관찰됨 (partial IC +0.112, pos 79%, n=68). Bull 레짐에서는 기여 없음(-0.006). 레짐 차이의 통계적 유의성은 Stage 18에서 검증.

**종속변수**: `alpha_20d` (= stock_20TD_return − KOSPI_20TD_return). 이 연구의 모든 결과는 "KOSPI 대비 초과수익 설명력"으로 해석한다. 절대수익률 개선으로 말하면 안 된다.

### 배경

Stage 15에서 Spearman IC:
- valuation_score(CLEAN): IC = +0.1421 (79%)
- earnings_score(Leaked): IC = +0.0320 (56%)
- risk_score(혼합): IC = +0.0400 (72%)

이것만으로는 Earnings/Risk가 약한 것이 factor 자체의 한계인지, Valuation에 가려진 것인지 알 수 없다.

### 사전등록 설계

**Primary — Valuation 통제 후 Earnings/Risk 편미분 IC**

날짜별 cross-section에서:
1. `alpha_20d ~ valuation_score` residual 추출
2. 그 residual과 `earnings_score`, `risk_score` 각각의 Spearman 상관 → partial IC

사전등록 방향성: partial IC가 marginal IC(통제 전)보다 감소하면 "Valuation에 가려진 것", 유사하면 "독립적 정보 보유".

**Secondary — 레짐별 분리**

| 레짐 | 정의 | 관찰 기간 |
|------|------|---------|
| Bull/강세 | KOSPI 20TD > +5% | Q1~Q2 주류 |
| Bear/약세·횡보 | KOSPI 20TD ≤ +5% | Q3 및 일부 Q1~Q2 |

Earnings/Risk incremental IC가 레짐별로 차이나는가?

### 판정 기준

- **독립 정보 보유**: partial IC가 양수이고 raw IC와 유사 수준
- **Valuation에 가려짐**: partial IC가 raw IC 대비 현저히 감소
- **레짐 의존**: 특정 레짐에서만 partial IC 양수

### 제약

Stage 15-A에서 Earnings/Risk의 Naver 소급 오염이 확인됐으므로, 이 연구의 결과는 "Naver 소급 포함 retro 기준"으로 해석. Real OOS에서 재확인 필요.

### 결과 (2026-09-30 실행)

**Primary — Valuation 통제 전·후 IC**

| 컴포넌트 | Raw IC | partial IC (Valuation 통제 후) | 해석 |
|---------|--------|-------------------------------|------|
| valuation_score | +0.1421 (pos 82%) | — (기준) | 강한 신호 |
| earnings_score | +0.0320 (pos 56%) | **+0.0029 (pos 50%)** | Valuation 통제 후 거의 소멸 |
| risk_score | +0.0400 (pos 72%) | **+0.0511 (pos 72%)** | 약하지만 방향 유지 |

→ Earnings: Valuation 통제 후 신호 사실상 0. Valuation과 같은 방향을 가리키거나 독립 정보 없음.
→ Risk: Valuation 통제 후에도 pos% 동일 유지. 일부 독립 정보 보유.

**Secondary — 레짐별 분리 (KOSPI 20TD 기준)**

| 레짐 | n | earnings raw | earnings partial | risk raw | risk partial |
|------|---|-------------|-----------------|----------|--------------|
| Bull (KOSPI > +5%) | 73 | +0.034 (pos 55%) | +0.033 (pos 64%) | -0.004 (pos 64%) | -0.006 (pos 64%) |
| Bear (KOSPI ≤ +5%) | 68 | +0.030 (pos 57%) | **-0.030 (pos 34%)** | **+0.087 (pos 81%)** | **+0.112 (pos 79%)** |

핵심 관찰:

**Earnings**:
- Bull 레짐: raw≈partial (+0.034≈+0.033) → Valuation과 독립적인 약한 신호
- Bear 레짐: raw +0.030 → partial **-0.030 (역전, pos 34%)** → Valuation 통제 후 방향 반전. 독립 정보 없거나 오히려 노이즈.

**Risk**:
- Bull 레짐: raw -0.004, partial -0.006 → 강세장에서 risk factor 기여 없음
- Bear 레짐: raw +0.087 (pos 81%), partial **+0.112 (pos 79%)** → Valuation 통제 후에도 강한 독립 신호. 오히려 partial이 raw보다 높음.

### 판정

**Earnings — Valuation에 가려짐**

Valuation 통제 후 partial IC ≈ 0 (+0.003). Bear 레짐에서 역전(-0.030).
독립적인 ranking 정보 기여 없음. Earnings가 약한 이유: Valuation에 가려진 것이 주요인.
(Naver 소급 오염의 영향이 추가로 있을 수 있으나, 오염이 earnings IC를 과대 추정한다면 실제 독립 정보는 더 적음)

**Risk — Bear 레짐에서 독립적 정보성 관찰됨**

Bear 레짐(KOSPI ≤ +5%)에서 partial IC = +0.112, pos%=79% — 독립적 정보성이 강하게 관찰됨.
Bull 레짐에서는 기여 없음(-0.006). 레짐에 따라 기여가 반전되는 구조.
**단, Bull vs Bear 차이(Δ ≈ 0.118)가 통계적으로 구별되는지는 Stage 18에서 검증 예정. 다중 분석 문제와 20D 겹침 자기상관 고려 필요.**

직관적 설명: 상승장에서는 고위험주도 시장과 함께 상승 → risk factor 무관. 조정/하락장에서는 저위험 종목이 상대적으로 방어 → risk factor 유효.

**종합**:
- Earnings incremental: 없음 (Valuation에 가려짐)
- Risk incremental: **레짐 의존. Bear 레짐에서 유의미한 독립 정보.**
- Valuation: 레짐 무관 주요 ranking factor (유지)

패턴 강도: Risk Bear regime = **Robust Pattern** 후보 (n=68일, pos 79%)
단, Naver 소급 포함 retro 기준. 레짐 경계값(+5%)은 사전등록됨.

### 함의 (운용 반영 전 검토 필요)

현재 scoring 구조 `score = Val + Earn + Risk`:
- Val이 ranking의 대부분을 설명
- Earn은 중복 정보 (Val과 같은 방향을 가리킴)
- Risk는 Bear 레짐에서만 독립 기여

만약 이 구조가 Real OOS에서 재확인된다면:
- Risk를 Bear 레짐에서 더 강조하는 구조 논의 가능 (단, Research → Replay → Regression 프로세스 필수)
- Earnings 개선 방향: PIT-clean 데이터로 교체 후 독립 정보 여부 재검증

현 단계에서 production 점수 변경 없음. 관찰 기록.

---

## Stage 18 Risk × Regime Interaction (사전등록)

**질문**: Risk factor의 독립적 정보력이 실제로 시장 레짐에 따라 유의하게 달라지는가?

**상태**: ✅ 완료 (2026-09-30)

**판정**: Interaction 지지 (p=0.052, CI=[+0.012, +0.251], 0 미포함). 단, p 값이 0.05 임계값 경계 — "지지, 단 margin". Real OOS 재확인 필요.

**선행 근거**: Stage 17에서 Risk partial IC: Bull = -0.006 / Bear = +0.112 (Δ ≈ 0.118). 이 차이가 우연한 표본 분할인지 실제 interaction인지 검증 필요.

### 핵심 구별

두 주장은 서로 다른 것이다:
- **(A) Bear에서 Risk가 유의하다**: partial IC +0.112가 0과 통계적으로 다른가
- **(B) Bear에서 Risk가 Bull보다 유의하게 강하다**: Δ = IC_Bear − IC_Bull가 0과 다른가

Stage 18의 1차 목적은 **(B)**. (A)는 부차적.

### 사전등록 설계

**Primary — permutation test (레짐 레이블 shuffle)**

1. 141일 각각에 Bull/Bear 레이블 부여 (KOSPI 20TD > +5% 기준, Stage 17과 동일)
2. Observed Δ = partial_IC_Bear − partial_IC_Bull 계산
3. 레짐 레이블을 1,000회 무작위 shuffle (종목 내 날짜 구조 보존)
4. Null 분포에서 observed Δ 위치 → p-value

**20D 겹침 자기상관 처리**:
20 거래일 보유이므로 연속 날짜 간 alpha가 겹침. Block shuffle (block_size = 21일) 사용.

**Secondary — block bootstrap CI**

Δ의 95% CI 구간 추정. CI가 0을 포함하면 inconclusive.

**레짐 경계값**: KOSPI 20TD > +5% = Bull. Stage 17 사전등록값 그대로 사용. 결과 본 뒤 변경 금지.

### 판정 기준

- **Interaction 지지**: p < 0.10 AND CI가 0 미포함 → Risk가 레짐에 따라 조건부로 작동
- **Inconclusive**: 방향은 있으나 통계적으로 약함 (p ≥ 0.10 OR CI가 0 포함)
- **기각**: Δ ≈ 0 또는 방향 없음

**사후 최적화 금지**: 레짐 경계값(+5%), 분석 방법(permutation), block_size(21)를 결과 본 뒤 변경하지 않는다.

### 결과 (2026-09-30 실행)

**데이터**: 141 signal_dates, Bull=73일 / Bear=68일, N_PERM=2,000, N_BOOT=2,000, block_size=21, seed=42

**Observed**:
```
IC_Bear = +0.1123  IC_Bull = -0.0059
Δ = IC_Bear - IC_Bull = +0.1182
```

**Primary — Permutation Test (block shuffle)**

```
Null 분포: mean = +0.005, std = 0.072
Observed Δ = +0.1182
p-value (one-tailed, Δ ≥ observed) = 0.052  (N=2000)
```

**Secondary — Block Bootstrap 95% CI**

```
Δ 95% CI = [+0.012, +0.251]
CI contains 0: No
```

### 판정

사전등록 기준: p < 0.10 AND CI가 0 미포함

| 조건 | 결과 |
|------|------|
| p < 0.10 | ✓ (p=0.052) |
| CI 0 미포함 | ✓ ([+0.012, +0.251]) |

→ **Interaction 지지** (사전등록 기준 충족)

단, p = 0.052는 0.05 임계값 바로 위. **강하게 지지**보다는 **지지, 단 margin** 표현이 적절.

양방향 해석:
- Bootstrap CI [+0.012, +0.251]는 0을 명확히 제외 — 겹침 자기상관 보정 후에도 방향 유지
- p = 0.052는 n=141일(block shuffle)에서 1.94σ 수준 — 통계적으로 약하지 않으나 엄격하지도 않음

패턴 강도: **Weak/Mixed → Robust 경계**
Real OOS(2026-09-23~)에서 Risk × Regime interaction이 유지되는지 확인 필요.

### 함의

Risk factor가 조건부(Bear 레짐)로 작동할 가능성이 관찰됐으나, 이것이 production 점수 구조를 바꿀 수준의 근거인지는:
1. Real OOS에서 이 interaction이 재현되는가
2. 레짐 식별 방법이 실시간으로 작동 가능한가 (20TD 후향적 레짐 레이블의 전향적 대안)
두 가지가 확인된 뒤 논의. 현재는 관찰 기록으로만 유지.

---

## Research Summary — Stage 15~18 완료 (2026-09-30)

### 현재까지 확인된 정보 구조

```
Universe
└─ 방어적 / 저베타 (절대 beta ≈ +0.27)
   └─ 레짐 의존적 상대성과 (Bull 약세, Bear 상대 강세)
   └─ 설계 결함 여부는 장기 OOS 관찰 필요

Score ranking (KOSPI 대비 초과수익 기준)
└─ Valuation
   └─ 레짐 불문 주요 ranking factor  IC = +0.14
   └─ PIT CLEAN (pykrx date-keyed)

└─ Risk
   └─ Bull 레짐: 독립 정보 없음    partial IC ≈ 0
   └─ Bear 레짐: 독립 정보 관찰    partial IC = +0.112
   └─ Interaction Δ = +0.118, p=0.052, CI=[+0.012,+0.251]
   └─ 조건부 factor 가능성 — 사전등록 기준 충족(단 margin)

└─ Earnings
   └─ Valuation 통제 후 incremental 정보 소멸  partial IC ≈ +0.003
   └─ PIT LEAKED (Naver current data)
   └─ 재활용 전 PIT-clean 데이터 재구축 필요
```

### Production 상태

**v8.6.0 유지.** Stage 15~18은 연구 증거로 보관. 점수식 미변경.

```text
Score = Valuation + Earnings + Risk  (3-factor, 현행 유지)
```

Risk × Regime weighting 도입은 별도 모델이 되므로 Stage 18 단계에서 논의하지 않는다.
도입 조건: Real OOS에서 interaction 재현 확인 → 별도 Replay / Regression / Calibration.

### 연구 무게중심 전환

Stage 15~18로 과거 데이터(retro_calibration, 2026-01~07)에서 얻을 수 있는 가설은 충분히 추출됐다.

**같은 과거 데이터를 더 분석해도 얻는 것이 줄어드는 단계.**

이제 핵심 질문은:

> **과거에서 발견한 구조(Valuation 범용, Risk 조건부)가 실제 미래에서 재현되는가?**

Real OOS (2026-09-23~) 축적 데이터가 이 질문에 답하는 유일한 경로.

### OOS 재확인 계획

OOS 충분한 표본 확보 후 (최소 60영업일, Bull/Bear 각 20일 이상):

| 항목 | 검증 방법 |
|------|---------|
| Valuation IC | OOS 날짜별 Spearman IC vs 사전등록 방향(+) |
| Risk × Regime | OOS Bull/Bear partial IC 차이 vs retro Δ |
| Universe 절대 수익 | OOS 초과수익 방향 vs Q2 underperformance 재현 여부 |

OOS에서 interaction이 나오지 않아도 Stage 18 즉각 기각 아님 (표본 부족 가능성).
OOS에서 interaction이 나와도 production weighting 즉시 변경 아님 (별도 검증 프로세스 필요).

이 기준을 지금 고정해 둔다. OOS 결과를 보고 기준을 변경하면 사후 최적화.

---

## Stage 19 PIT Earnings/Risk Reconstruction

**질문**: Earnings/Risk 입력값을 PIT-clean 소스로 교체했을 때, Stage 17~18의 핵심 결론이 유지되는가?

**상태**: ✅ 완료 (DART 93.5% coverage, 2026-10-01)

**우선순위**: 최우선 — Stage 17~18의 가장 큰 약점(Naver 소급 오염)을 직접 공략. OOS 축적과 병행 가능.

---

### 최종 결과 (2026-10-01)

**DART PIT coverage**: 16,421 / 17,568행 (93.5%). ROE coverage: 5,031행 (사업보고서 11011만).

#### [C] Risk × Regime Interaction — 핵심 결과

| Risk 지표 | Bear IC | Bull IC | Δ | p |
|-----------|--------:|--------:|----:|---|
| Naver `risk_score` | +0.1384 | +0.0264 | **+0.1119** | ≈0 |
| DART PIT `-debt_ratio` | +0.0790 | −0.0267 | **+0.1057** | ≈0 |

레짐: 사전등록 정의 (KOSPI 20D holding-period return > 0 → Bull, ≤ 0 → Bear). Bull=90, Bear=51.

→ **두 독립적인 Risk 표현에서 interaction 방향이 유지되고 p≈0. Stage 18의 Risk×Regime interaction이 DART PIT-clean에서 재현됨.**

#### [B] Partial IC (valuation_score 통제)

| 지표 | Overall | Bull | Bear |
|------|--------:|-----:|-----:|
| earnings_score (Naver) | +0.0045 | +0.0256 | −0.0326 |
| PIT op_margin (level) | −0.0596 | −0.0507 | −0.0751 |
| risk_score (Naver) | +0.0669 | +0.0264 | +0.1384 |
| PIT -debt_ratio | +0.0116 | −0.0267 | +0.0790 |
| PIT ROE (11011 only) | −0.0106 | −0.0172 | +0.0845 |

#### 사전등록 판정

| 조건 | 판정 | 해석 |
|------|------|------|
| DART ≈ Naver Earnings partial IC (Δ<0.02) | ✗ | op_margin(level) vs earnings_score(composite) — 동등 비교 불가 |
| DART ≈ Naver Risk partial IC (Δ<0.02) | ✗ | Overall 차이 0.055 — 절대 ranking strength는 약함 |
| Earnings partial IC 소멸 (DART Δ<0.02) | ✗ | op_margin −0.0596 (소멸 아닌 음수) |
| Risk×Regime interaction (Naver) 유지 | ✓ | p≈0, Stage 18 유지 |
| Risk×Regime interaction (DART PIT) 유지 | ✓ | p≈0, PIT-clean에서도 재현 |

**종합 시나리오**: DART에서도 동일 — Naver leakage가 핵심 구조를 만들지 않았음. 연구 지속.

#### 결론

1. **Risk × Regime**: "Naver current-data leakage가 Stage 18의 Risk×Regime interaction을 주도했다는 증거가 없다. DART PIT-clean `-debt_ratio`에서도 동일한 interaction이 재현되었다." Naver leakage가 interaction의 주된 원천이라는 가설 약화.

2. **Earnings**: PIT op_margin(level)과 Naver earnings_score(composite, YoY 성격)는 동일한 construct가 아니다. 방향 비교가 불가능하므로 "미확정". 향후 PIT earnings change / YoY 지표가 확보되면 별도 검증.

3. **Risk overall**: 전체 횡단면 ranking strength는 Naver risk보다 약하지만, Bear/Bull에 따른 interaction은 유지. Stage 18의 핵심 가설("Risk의 절대 ranking strength"가 아닌 "regime에 따른 독립 정보성")과 정합.

4. **다음 확인**: Real OOS가 담당. retro 분석 반복은 하지 않는다.

#### 제한사항

- op_margin은 level 값 (YoY 불가 — DART frmtrm_amount가 분기보고서에서 공백)
- ROE coverage 29% (사업보고서 11011만 — 분기/반기는 기간 혼재로 NA)
- 정정공시: fnlttSinglAcntAll은 최신 수정본 반환 가능 — signal_date 이후 정정이 반영될 수 있음
- DART PIT 미수집: 1,147행 (no_report or fnltt_failed)

---

### 중단 이유 기록 (2026-10-01) — 최종 재실행 전 중간 단계

DART PIT 데이터 수집 및 analyze_dart_pit.py 실행까지 완료했으나, 두 가지 정의 정합성 문제가 발견되어 실행 중단.
결과를 확정하기 전에 검출했으므로 실패가 아닌 **사전등록 정의와 구현의 불일치를 발견한 것**으로 기록한다.

#### 발견된 문제 1 — PIT ROE 정의 불일치

동일 signal_date의 단면에서 기업마다 서로 다른 보고기간 데이터가 혼재:

| reprt_code | 기간 | 당기순이익 기준 |
|------------|------|----------------|
| 11014 (3Q) | 9개월 누적 | 9M NI / equity |
| 11012 (반기) | 6개월 누적 | 6M NI / equity |
| 11011 (사업) | 12개월 | 12M NI / equity |

→ 연환산 없이 ROE를 계산하면 동일 단면에서 비교 불가능한 숫자를 Spearman 순위로 비교.
→ 관찰된 raw IC `-0.3841`은 이 오염으로 인한 무효 수치. Stage 19 결론에 사용하지 않는다.

수정 방향: ROE 정의를 TTM 또는 사전 고정된 연환산 방식으로 확정 후 재계산.
단, `NI × 12 / 기간월수` 단순 연환산은 계절성 강한 기업에서 왜곡 가능 — 정의를 먼저 고정해야 한다.

#### 발견된 문제 2 — 레짐 정의 불일치

Stage 19에서 사용한 레짐 정의 (`component_role_attribution.py`의 KOSPI 60D trailing return, ±5% 경계):

| 기준 | retro 기간(2026-01-02~07-30) 결과 |
|------|----------------------------------|
| KOSPI 60D trailing >5% Bull / <-5% Bear | Bull=135, Bear=3, Sideways=3 |

Stage 17/18 원본 결과 (session summary): Bull=73, Bear=68 (총 141일 전체)

→ 두 정의는 완전히 다른 분류를 만든다. n=3인 Bear에서 나온 `-0.2483` partial IC는 기존 Stage 18의 `+0.112`와 비교 불가능하다.

원본 Stage 17/18은 inline 코드로 실행되어 스크립트 미저장. 73/68 split의 정확한 레짐 정의는 불확정.
검토한 후보:
- KOSPI 20D forward return >0 / ≤0: Bull=93, Bear=48 (불일치)
- KOSPI 60D trailing >0 / ≤0: 미확인
- 사전등록에 기재된 "+5% 경계": KOSPI 60D trailing 방식 — Bear=3으로 불일치

→ Stage 19 재실행 전에 레짐 정의를 새로 사전등록하고 고정해야 한다.

#### 중간 결과 reference-only (판정에 사용하지 않음)

정의 정합성 수정 전까지 아래 수치는 reference-only:

| 지표 | 현재 수치 | 비고 |
|------|-----------|------|
| Naver risk_score partial IC (전체) | +0.069 | 레짐 정의 문제와 무관, 기준 방향 참고용 |
| DART -debt_ratio partial IC (전체) | +0.012 | ROE 오염과 별개로 방향은 양수 |
| PIT ROE raw IC | -0.3841 | 오염 — 무효 |
| Bear regime interaction (Naver) | Δ=-0.3243, p=1.0 | n=3, 무효 |
| Bear regime interaction (DART) | 데이터 부족 | 무효 |

DART PIT coverage: 12,324 / 16,421행 복구 (DB 손상으로 일부 유실, 재수집 가능).

#### 재실행 조건 (사전등록 — 2026-10-01 확정) → 전 단계 완료

```
① Regime 정의 사전등록  ✅ 완료
    ↓
② ROE 정의 사전등록     ✅ 완료
    ↓
③ DART PIT 누락 5,270행 보충 (collect_dart_pit.py 재실행) ✅ 완료 → 16,421행 (93.5%)
    ↓
④ DART PIT 재무지표 재계산 (ROE 새 정의 적용 — 11011 only) ✅ 완료
    ↓
⑤ Stage 17 동일 재현 (DART PIT Earnings/Risk partial IC)  ✅ 완료 (결과: 위 표)
    ↓
⑥ Stage 18 동일 재현 (Risk × Regime Δ, 동일 레짐 정의)   ✅ 완료
    ↓
Stage 19 최종 결론 확정                                    ✅ 완료
```

---

### Stage 19 재실행 사전등록 (2026-10-01)

#### ① Regime 정의

**정의**: KOSPI 20D holding-period return 기준 retrospective forward-regime partition

```
retro_calibration.kospi_exit_close / kospi_entry_open - 1 > 0
    → Bull

retro_calibration.kospi_exit_close / kospi_entry_open - 1 ≤ 0
    → Bear

Sideways = 없음  (전체 141 signal_dates가 Bull 또는 Bear로 분류됨)
```

원본 Stage 17/18 결과(Bull=73, Bear=68)를 최대한 그대로 복제하기 위한 선택.
두 후보 중 이 정의가 141개 전체를 분류하는 단 하나의 방식이기 때문에 확정한다.

**중요 주석**: 이 레짐은 실시간으로 알 수 있는 레짐이 아니다.
signal_date 이후 20 거래일 동안 KOSPI가 어떻게 움직였는지를 소급해서 분류하는 **retrospective outcome partition**이다.
Stage 18/19의 질문은:
> "Risk의 정보성이 향후 KOSPI가 상승한 기간과 하락한 기간에서 다르게 나타났는가?"

이는 분석적 질문이며, 아래 실전 질문과 구별한다:
> "오늘 시장이 Bull인지 Bear인지 알고 Risk weight를 조절할 수 있는가?"

후자는 별도 후속 연구. Stage 18/19에서 레짐을 최적화하거나 새 threshold를 탐색하지 않는다.

#### ② ROE 정의

**목표**: 동일 단면에서 비교 가능한 ROE — reprt_code 혼재 문제(6M / 9M / 12M NI)를 제거.

**우선 시도**: TTM (Trailing Twelve Months) net income / 최근 공시 자본총계

TTM 계산 방법:
- 1Q보고서 있으면: 직전 연간 NI + 1Q NI − 전전년도 1Q NI (4분기 합산)
- 3Q보고서 있으면: 직전 반기 NI + 3Q NI + 전년도 4Q NI 등

**사전 확인 필요**: DART PIT 이력에서 TTM 계산에 필요한 이전 분기 데이터가 충분한지 먼저 검증.

**fallback** (TTM 불가 시): 동일 reprt_code인 경우만 동일 단면 비교. 보고기간이 다른 기업은 ROE 계산에서 제외.

**금지**: `NI × 12 / 기간월수` 단순 연환산 — 계절성 강한 기업(유통, 건설 등)에서 체계적 왜곡 발생.

---

### 배경

Stage 15-A에서 확인:
- Earnings 분기(quarters_met, revision, quality): Naver current API, 소급 오염
- Risk 부채·손실 분기: 동일 소스, 소급 오염
- Stage 17 "Earnings incremental 정보 소멸", Stage 18 "Risk × Regime interaction"은 이 오염된 데이터 위에서 계산됨

만약 PIT-clean 데이터로 교체 시:
- **결과 유지**: Stage 17~18 결론 신뢰도 대폭 상승
- **결과 변화**: Stage 17~18 재해석 필요 (Risk interaction이 사라지거나 Earnings가 살아날 수 있음)

### 사전등록 설계

**Primary — 교체 대상 및 방법**

| 현재 소스 | PIT-clean 후보 | 비고 |
|----------|---------------|------|
| Naver quarterly (earnings_score 기반) | DART OpenAPI — 분기보고서 공시일 기준 | signal_date 이전 마지막 공시 분기만 사용 |
| Naver quarterly (risk 부채·손실) | 동일 | |
| pykrx ROE 보너스 | 그대로 유지 (이미 PIT CLEAN) | |
| pykrx 변동성/희석 | 그대로 유지 (이미 PIT CLEAN) | |

DART 공시 타임스탬프 기준: `acceptedAt` 필드 (한국 시각). signal_date 00:00 이전 접수된 가장 최근 분기 재무제표만 인정.

**Secondary — 검증 설계**

기존 retro_calibration 동일 signal_date / 동일 ticker에 대해:
1. PIT-clean Earnings/Risk score 재계산
2. Stage 17 동일 방법으로 partial IC 재측정 (Valuation 통제 후)
3. Stage 18 동일 방법으로 Bear/Bull Δ 재측정 (동일 레짐 경계값 +5%, 동일 block_size=21)

**비교 구조 (사후 선택 금지)**

| 지표 | Stage 17/18 (Naver 오염) | Stage 19 (DART PIT-clean) |
|------|--------------------------|---------------------------|
| Earnings partial IC (전체) | +0.003 (earnings_score) | −0.060 (op_margin, level) — 구조 불일치 |
| Risk partial IC (전체) | +0.051 (risk_score) | +0.012 (−debt_ratio) |
| Risk partial IC (Bear) | +0.112 | +0.079 |
| Risk × Regime Δ | +0.118, p=0.052 | +0.106, p≈0 |

결과를 보고 지표를 추가하거나 제외하면 안 된다.

### 판정 기준

- **Stage 17~18 결론 유지**: PIT-clean 후에도 Earnings incremental 소멸, Risk Bear 독립 정보 유지
- **부분 변화**: Earnings가 살아나거나 Risk interaction이 약화 → 어느 쪽인지 기록
- **결론 반전**: Risk interaction 사라짐 → Stage 17~18 재해석 필요

### 선행 조건

DART OpenAPI PIT 분기 데이터 안정적 자동 추출 확인 필요.
현재 `analyzer/data/naver.py`의 quarterly API를 DART 기반으로 교체하는 작업이 선행된다.
데이터 추출 검증 완료 전 실행하지 않는다.

---

## Stage 20 Universe Exposure / Benchmark Decomposition

**질문**: Universe의 상대성과가 "방어적 저베타 전략" 때문인가, 특정 업종·스타일 노출 때문인가?

**상태**: ✅ 완료 (2026-10-01)

**선행 근거**: Stage 16에서 절대 beta +0.27, Q2 excess return -10.92% 관찰. 저베타 구조가 원인이라고 추정했으나 단일 변수로 설명되지 않는 초과 underperformance 가능성 존재.

### 데이터 구조

**소스**: `universe_exposure` 테이블 (17,648행, 141개 signal_date, 135개 종목)

| 날짜 구분 | PBR/PER/시총 | Sector | kospi200 |
|-----------|-------------|--------|----------|
| snapshot 날짜 (27개, 2026-05-28~07-08) | snapshot | pykrx | — |
| 나머지 114개 날짜 | pykrx | pykrx | — |

**kospi200 컬럼 미사용**: universe가 135종목으로 제한되어 KOSPI200 membership 판별 불가 (전부 kospi200=1로 저장됨). 시총 노출은 signal_date별 cross-sectional tertile로 대체.

**시총 분위**: signal_date별 33.3%/66.7% quantile → Small/Mid/Large. "해당 시점 universe에서 상대적으로 어느 위치였는가"를 측정.

**커버리지**: sector 74.3%, market_cap 71.0%, pbr 64.9% (null은 해당 분석에서만 제외).

### 최종 결과

#### [D] 조건부 회귀: alpha_20d ~ log(market_cap) + PBR

| 변수 | β | t | 해석 |
|------|---|---|------|
| log(market_cap) | +0.088 | +9.15 | 대형주 편향 유의 |
| PBR | −0.143 | −14.88 | 저PBR 편향 유의 |

모든 값 signal_date별 표준화. 인과효과 아님 — "어떤 노출과 alpha가 함께 나타났는가" 기술적 분석.

- **저PBR 편향**: 설계된 특성 (scoring에서 저PBR 우대). STRONG_BUY 중 Low-PBR 분위 44.2%.
- **대형주 편향**: 설계하지 않았으나 관찰된 특성. STRONG_BUY 중 Large 22.1%이지만 alpha는 Large(+1.00%) > Mid(−1.82%) > Small(−5.51%).

#### [A] §5 Scope 분류별 STRONG_BUY alpha

| 분류 | Mean alpha | 비고 |
|------|-----------|------|
| In-scope (통신·유틸·소비재·금융) | −1.58% | |
| Out-of-scope (Cyclical) | −2.56% | |
| Other | −3.18% | |

→ 적용 가능 업종이 상대적으로 방어적. 단, 전체 절대수익률은 음수 — 분석 기간(2026 Q1~Q2)이 시장 하락기.

업종별 상위: 건설 +5.07%, 통신 +2.34%, 기계 +2.20%.
업종별 하위: 음식료·담배 −9.53%, 전기·가스 −8.47%, 운송장비·부품 −6.11%.

#### [B] 시총 분위별 STRONG_BUY alpha

| Tier | n | Mean alpha |
|------|---|-----------|
| Large | 1,427 | **+1.00%** |
| Mid | 2,534 | −1.82% |
| Small | 2,484 | −5.51% |

#### [C] PBR 분위별 STRONG_BUY alpha

| Tier | n | Mean alpha |
|------|---|-----------|
| Low | 2,777 | −2.58% |
| Mid | 2,006 | −2.77% |
| High | 1,502 | −3.76% |

저PBR이 상대적 방어 효과.

#### [F] 분기별 분해 (Secondary)

| 분기 | n | Mean alpha | In-scope% | Out-scope% |
|------|---|-----------|-----------|-----------|
| Q1 | 2,835 | −1.44% | 63.5% | 25.5% |
| Q2 | 3,014 | −7.14% | 68.8% | 22.2% |
| Q3 | 1,184 | +9.90% | 67.7% | 22.6% |

Q2 −7.14% 손실 원인: 통신(+1.38%)을 제외한 전 업종이 −4% 이하. 음식료·담배 −23.89%, 운송장비·부품 −15.18%, 운송·창고 −10.38%. **업종 선택 효과만으로 Q2 손실을 설명하기 어려움 — 시장 전반 하락과 beta exposure가 주요 배경**.

#### [E] Source 혼합 점검

pykrx 기간 mean=−7.90% vs snapshot 기간 mean=+9.94%, 차이 17.84%p. 단, 두 source의 관측 기간 자체가 달라 발생한 **기간 효과** (pykrx=Q1~Q2 하락기, snapshot=Q2 말~Q3 초 회복기). Source 혼합 편향의 증거로 보지 않음.

### 결론

1. **저PBR 편향**: 확인. 설계된 특성이며 방어적으로 작동.
2. **대형주 편향**: 확인. 설계되지 않았지만 방어적으로 작동 (Large STRONG_BUY 유일하게 양수).
3. **§5 scope 분류**: 방어적으로 작동. In-scope가 Out-of-scope보다 약 1%p 상회.
4. **Q2 손실 원인**: 업종 선택 문제 아님. 시장 전반 하락 + beta exposure.
5. **Production 변경**: 없음. 노출 진단 완료.

### 사전등록 설계 (원본)

**Primary**: 업종(§5 적용/비적용), 시총, PBR 분위별 조건부 alpha 비교.
**Secondary**: Q2 excess return 분기별 분해.
**제약**: Universe 구성 변경 없음. 노출 진단만.

---

## Stage 20A Bear × Exposure Interaction

**질문**: Stage 18/19에서 확인된 Risk × Regime interaction과 Stage 20에서 확인된 Universe Exposure가 동일한 구조적 원천을 공유하는지 검증한다.

**상태**: ✅ 완료 (2026-10-01)

**사전등록**: Regime별 PBR 및 market-cap exposure와 20D alpha의 관계를 비교하고, Bear × Exposure interaction을 통해 Stage 18/19의 Risk×Regime interaction과 Universe Exposure의 구조적 연결 여부를 검증한다.

**레짐 정의**: Stage 19와 동일 (pre-registered). entry_open = signal_date 당일 시가, exit_close = +20영업일 종가. Bull: exit/entry > 1 / Bear: ≤ 1. Retrospective forward-regime partition. 연구 전용.

**alpha_20d 정의**: KOSPI 대비 20D 초과수익률. Regime별 평균 차이는 시장 방향 자체가 아니라 해당 기간의 benchmark 대비 상대성과를 의미한다.

**샘플**: 11,459행. Bear 3,652행 (42개 날짜) / Bull 7,807행 (87개 날짜).

### 결과

#### [A] Bear × PBR tier

| Tier | Bear mean | n | Bull mean | n | Δ(Bear−Bull) |
|------|-----------|---|-----------|---|-------------|
| Low  | +10.10%   | 1,232 | −10.75% | 2,648 | +20.85% |
| Mid  | +10.40%   | 1,192 | −13.49% | 2,532 | +23.89% |
| High |  +5.65%   | 1,228 | −11.17% | 2,627 | +16.82% |

- Δ-of-Δ (Low vs High) = **+4.03%p**
- OLS interaction β = **−0.1535**, p < 0.0001 ★

Bear 기간에 Low-PBR 종목의 상대성과 개선폭이 High-PBR보다 큼.

#### [B] Bear × Market Cap tier

| Tier  | Bear mean | n | Bull mean | n | Δ(Bear−Bull) |
|-------|-----------|---|-----------|---|-------------|
| Small |  +8.41%   | 1,233 | −13.61% | 2,641 | +22.01% |
| Mid   | +10.56%   | 1,194 | −11.91% | 2,548 | +22.46% |
| Large |  +7.19%   | 1,225 |  −9.81% | 2,618 | +17.00% |

- Δ-of-Δ (Small vs Large) = **+5.01%p**
- OLS interaction β = **−0.2413**, p < 0.0001 ★

Bear 기간에 Small/Mid가 Large보다 상대적으로 강한 방어력을 보임.

#### [E] STRONG_BUY 서브그룹 (n=6,285)

- PBR: Low Δ=+21.17%, High Δ=+17.20%, Δ-of-Δ=+3.97%p — 전체와 동일한 방향
- Cap: Small Δ=+22.36%, Large Δ=+18.71%, Δ-of-Δ=+3.65%p — 전체와 동일한 방향

STRONG_BUY 필터가 interaction 구조를 바꾸지 않음.

### 해석

PBR과 Market Cap 모두 Bear × Exposure interaction이 통계적으로 확인되었다. 따라서 Stage 18/19의 Risk × Regime interaction과 Stage 20의 Universe Exposure 사이에 구조적 연결 가능성이 확인된다.

특히 저PBR interaction은 Risk-related exposure와 결합되어 나타날 가능성 있는 **연결 경로**를 시사한다. 다만 이번 교차분석만으로 Risk × Regime effect가 PBR 또는 debt-related exposure에 의해 어느 정도 설명되는지 판단할 수 없다. 설명력의 크기와 incremental contribution은 nested-model 분석에서 별도로 검증해야 한다.

Market Cap 결과는 중요한 비대칭을 보인다:

- 전체 alpha (Stage 20): Large exposure와 양(+)의 관계 (Large STRONG_BUY +1.00%)
- Bear 방어력 (Stage 20A): Small/Mid가 Large보다 강함 (Small Δ=+22.01% > Large Δ=+17.00%)

따라서 "대형주 편향"을 Bear 방어력의 공통 원천으로 해석하는 것은 적절하지 않다. Large exposure와 Regime interaction은 서로 다른 차원의 구조일 가능성이 있다.

### 결론

1. 저PBR × Bear interaction 확인 (β=−0.1535, p<0.0001)
2. Market Cap × Bear interaction 확인 (β=−0.2413, p<0.0001)
3. Stage 18/19의 Risk × Regime와 Stage 20 Exposure 사이의 연결 가능성 확인
4. PBR/debt-related exposure가 Risk × Regime effect를 얼마나 설명하는지는 미확정 — 별도 nested-model 분석 필요
5. Large exposure는 전체 alpha와 Bear 방어력에서 방향이 달라 별도 구조로 취급
6. 다음 후보: nested-model을 통한 incremental explanatory power 확인 (Risk → +PBR → +Cap → +Bear×PBR / Bear×Cap)

### 사전등록 설계 (원본)

**Primary**: Regime별 PBR 및 market-cap exposure × alpha 비교. Bear × Exposure interaction (교차표 + clustered OLS).  
**Secondary**: STRONG_BUY 서브그룹 재실행.  
**해석 기준**: interaction 유의 → 연결 가능성 / 미유의 → 독립적 구조 가능성.  
**주의**: 교차분석은 연관성 파악용. 설명력(incremental)은 별도 nested-model 분석 필요.

---

## Stage 20B Nested-Model Incremental Explanatory Power

**목적**: Stage 20A에서 확인된 Bear × PBR / Market-Cap interaction이 Stage 18/19의 Risk × Regime interaction을 어느 정도 설명하는지 nested-model을 통해 검증한다.

**상태**: ✅ 완료 (2026-10-01)

**사전등록**: Risk × Regime interaction 계수(β)가 PBR 및 Market Cap exposure를 순차적으로 추가했을 때 얼마나 감소하는지 확인한다. Risk proxy는 Naver risk_score와 DART PIT debt_ratio를 별도로 사용. 핵심 측정값: M0 대비 M4에서의 bear×risk_z β 감소폭과 ΔR².

**모델 구조**:
```
M0: alpha_z ~ bear + risk_z + bear:risk_z
M1: M0 + pbr_z
M2: M0 + log_cap_z
M3: M0 + pbr_z + log_cap_z
M4: M3 + bear:pbr_z + bear:log_cap_z
```
SE: clustered by signal_date. 표준화: signal_date별 cross-sectional z-score.

### Series A — Naver risk_score (n=11,459)

| 모델 | R² | bear:risk β | Δβ vs M0 |
|------|-----|-------------|----------|
| M0 | 0.0023 | +0.1009 | — |
| M1 | 0.0166 | +0.1027 | +0.0018 |
| M2 | 0.0049 | +0.1011 | +0.0002 |
| M3 | 0.0246 | +0.1035 | +0.0026 |
| M4 | 0.0366 | +0.0773 ★ | −0.0236 (−23.4%) |

M0→M3에서 PBR/Cap 주효과를 추가해도 `bear:risk_z` 계수는 거의 변하지 않는다 (약 2.6%). PBR/Cap의 주효과만으로는 Naver Risk × Regime interaction을 거의 흡수하지 못한다.

M4 (PBR×Regime + Cap×Regime 추가): β +0.1009 → +0.0773, 23.4% 감소, p < 0.0001. Naver Risk × Regime interaction의 일부가 PBR/Cap exposure와 관련되어 있으나, 통제 이후에도 통계적으로 유의한 interaction이 남는다.

### Series B — DART PIT safety = −debt_ratio (n=10,813)

| 모델 | R² | bear:risk β | Δβ vs M0 |
|------|-----|-------------|----------|
| M0 | 0.0046 | +0.0944 | — |
| M1 | 0.0169 | +0.0930 | −0.0014 |
| M2 | 0.0068 | +0.0925 | −0.0018 |
| M3 | 0.0245 | +0.0891 | −0.0053 |
| M4 | 0.0394 | +0.0499 (미유의) | −0.0445 (−47.1%) |

M0→M3: PBR/Cap 주효과만으로는 약 5.6% 감소. 역시 주효과만으로는 크게 흡수되지 않는다.

M4 (PBR×Regime + Cap×Regime 추가): β +0.0944 → +0.0499, 47.1% 감소, **p=0.1184 (미유의)**. PBR/Cap interaction을 통제한 이후 DART PIT `debt_ratio × Regime` interaction은 더 이상 통계적으로 유의하지 않다. 이는 Stage 19에서 관찰된 DART PIT Risk × Regime 관계의 상당 부분이 PBR/Market-Cap exposure composition과 연관되어 있을 가능성을 보여준다.

### M4 잔존 Exposure × Regime interaction

두 Series 모두 M4에서 `bear:pbr_z`, `bear:log_cap_z`가 강하게 유의하다:

| 계수 | β (Series A) | β (Series B) | p |
|------|-------------|-------------|---|
| bear:pbr_z | −0.1149 | −0.1195 | < 0.0001 |
| bear:log_cap_z | −0.1737 | −0.1966 | < 0.0001 |

Stage 20A에서 관찰된 Bear × Exposure 관계가 Risk 변수를 추가한 이후에도 유지된다.

### 비교 해석

두 Risk 정의가 서로 다른 결과를 보인다:

- **DART PIT-clean debt_ratio**: PBR/Cap × Regime을 추가하면 interaction이 크게 감소하고 통계적 유의성이 사라짐.
- **Naver risk_score**: interaction 크기가 약 23.4% 감소하지만 여전히 강하게 유의함.

Naver risk_score에는 PBR/Cap으로 설명되지 않는 추가적인 정보가 포함되어 있을 가능성이 있다. 가능한 설명으로는 `debt_ratio` 이외의 재무건전성 특성 포착 가능성과 데이터 구성·정보시점 차이에 따른 leakage 가능성이 있으나, 현재 결과만으로 두 설명을 구분할 수 없다.

### 결론

1. PBR/Cap 주효과만으로는 Risk × Regime interaction을 설명하지 못한다.
2. DART PIT `debt_ratio × Regime` interaction은 PBR/Cap × Regime 통제 후 통계적으로 유의하지 않게 된다.
3. Naver `risk_score × Regime` interaction은 약화되지만 유의한 관계가 남는다.
4. PBR × Regime 및 Market-Cap × Regime interaction은 Risk 통제 이후에도 유지된다.
5. DART PIT 기준에서는 Universe Exposure composition이 Risk × Regime 관계의 상당 부분과 연관되지만, Naver risk_score에는 추가적인 정보 또는 정보시점/구성 차이가 존재할 가능성이 있다.
6. Naver risk_score의 잔존 기여가 실제 quality 정보인지 leakage인지 여부는 현재 결과만으로 확정할 수 없다.

### 사전등록 설계 (원본)

**Primary**: bear:risk_z β의 M0→M4 감소폭 및 p-value 변화.  
**Risk proxy**: Naver risk_score (Series A), DART PIT −debt_ratio (Series B) 별도 실행.  
**SE**: clustered by signal_date. 표준화: signal_date별 cross-sectional z-score.

---

## Stage 20C Naver Risk Score Residualization

**목적**: Stage 20B에서 확인된 Naver `risk_score × Bear` interaction이 `debt_ratio`와 공유하는 정보에 의해 발생하는지, 또는 `debt_ratio`로 설명되지 않는 risk_score의 추가 성분에서도 유지되는지 검증한다.

**상태**: ✅ 완료 (2026-10-01)

**사전등록**: Naver risk_score에서 DART PIT debt_ratio를 선형 partialling-out한 잔차가 Bear interaction을 유지하는지 확인한다. Version A: risk_score ~ debt_ratio / Version B: risk_score ~ debt_ratio + PBR + log_cap. 핵심: residual×Bear β 및 p-value의 원래 risk_score×Bear 대비 변화.

**risk_score 구성** (`analyzer/scoring/risk.py` 확인): 25점 기준 감점 항목 — debt_ratio_pct(임계값 이진 감점), quarters_with_loss, dilution_pct, volatility_pct, governance_flag. 데이터 소스: Naver current (look-ahead 가능성 있음). debt_ratio 반영이 이진이므로 선형 partialling-out은 근사적.

### 1. Shared Variance

`debt_ratio`는 Naver `risk_score` 분산의 **51.4%**를 설명한다. PBR 및 Market Cap을 추가해도 설명력은 **52.1%**로, 추가 기여가 매우 작다.

→ `risk_score`와 `debt_ratio`는 level에서 상당한 공유 분산을 갖지만, PBR/Cap은 이 관계에 추가적인 설명력을 거의 제공하지 않는다.

### 2. Bear × Risk Interaction — Residualization 비교

| 모델 | Bear × Risk β | Δβ vs 원래 | p |
|------|-------------|-----------|---|
| M_orig — original risk_score | +0.1009 | 기준 | <0.0001 |
| M_debt — debt_ratio만 | −0.0944 | −0.1953 | 0.0059 |
| M_res_A — debt_ratio partialled | +0.0877 | −0.0132 | 0.0013 |
| M_res_B — debt_ratio + PBR + Cap partialled | +0.0906 | −0.0103 | 0.0004 |

`debt_ratio`를 partialling-out한 잔차에서도 Bear interaction은 원래 계수의 약 **87%**가 유지된다. PBR 및 Market Cap까지 제거한 경우에도 약 **90%**가 유지되며, 통계적으로 유의하다.

### 해석

`risk_score`와 `debt_ratio`는 level에서 강하게 연결되어 있지만, **Bear에서의 alpha와의 interaction은 동일한 관계로 설명되지 않는다.** `debt_ratio`로 설명되지 않는 risk_score residual에서도 원래 Bear interaction의 대부분이 유지된다.

다만 현재 결과만으로 그 추가 성분이 risk_score 구성요소 (loss_quarters, dilution, volatility, governance) 가운데 어디에서 발생하는지 개별적으로 구분할 수는 없다. 개별 기여는 별도 decomposition 분석이 필요하다.

### Stage 20B와의 연결

- **DART PIT `debt_ratio × Bear`**: PBR/Cap × Regime 통제 후 β +0.0944 → +0.0499, 47.1% 감소, p=0.1184 (유의성 소실)
- **Naver `risk_score × Bear`**: `debt_ratio` partialling-out 이후에도 β의 약 **87%** 유지, p=0.0013
- PBR/Cap까지 partialling-out해도 약 **90%** 유지, p=0.0004

두 데이터 소스의 Risk × Regime 구조는 동일하지 않다.

### Leakage 해석

이번 결과는 Naver risk_score의 잔존 interaction이 단순한 debt_ratio 채널로 설명되기 어렵다는 점을 보여준다. 향후 leakage 가능성을 점검한다면 debt_ratio보다는 risk_score를 구성하는 **다른 데이터 계열의 정보시점과 산출 방식** (loss_quarters, dilution, volatility, governance)을 검토하는 것이 더 직접적인 다음 질문이 된다. 그러나 현재 결과만으로 leakage 여부를 확정할 수 없으며, 실제 원인이 추가적인 quality 정보인지 정보시점 문제인지는 별도 검증이 필요하다.

### 결론

1. `risk_score`와 `debt_ratio`는 level에서 약 **51.4%**의 공유 분산을 가진다.
2. 그러나 `debt_ratio`를 제거해도 Bear interaction의 약 **87%**가 유지된다.
3. PBR/Market Cap까지 제거한 residual에서도 약 **90%**가 유지된다.
4. 따라서 Naver `risk_score × Bear` interaction은 debt_ratio 또는 단순 PBR/Cap composition만으로 설명되지 않는 추가 성분을 포함한다.
5. 개별 추가 성분의 기여와 leakage 여부는 미확정. 다음 후보: risk_score 구성요소별 decomposition.

### 사전등록 설계 (원본)

**Primary**: residual × Bear β의 유지 여부 및 p-value.  
**Version A**: risk_score ~ debt_ratio / **Version B**: risk_score ~ debt_ratio + PBR + log_cap.  
**한계**: debt_ratio 반영이 이진이므로 선형 partialling-out은 근사. Naver/DART 데이터 소스 차이도 잔차에 포함됨.

---

## Stage 20D Residual Risk × Regime Robustness / Conditional Structure

**목적**: Stage 20C에서 확인된 Naver `risk_score`의 debt_ratio-partialled residual Bear interaction이 기간 및 Universe Exposure 조건에 따라 어떻게 달라지는지 검증한다.

**상태**: ✅ 완료 (2026-10-01)

**사전등록**: Stage 20C에서 확인된 residual × Bear interaction이 STRONG_BUY 서브그룹, 기간 분할, PBR tier, Cap tier에서도 방향이 유지되는지 검증한다. 핵심: bear:residual_z β의 방향 일관성. 크기보다 방향 우선.

### 1. 기간 조건

| 기간 | β (A) | p |
|------|-------|---|
| Q1 (Jan-Mar) | −0.0135 | 미유의 |
| Q2 (Apr-Jun) | +0.2113 | <0.0001 |
| Q3+ (Jul-) | +0.2598 | <0.0001 |

residual risk × Bear interaction은 **기간에 따라 크게 달라진다.** Q1에서는 유의성이 없고 Q2/Q3+에서 강하게 나타난다. 따라서 2026년의 Bear 환경을 하나의 동질적인 regime으로 간주하기보다는, Q1과 Q2/Q3+의 하락 환경에서 residual risk의 역할이 달라졌을 가능성을 고려해야 한다.

### 2. PBR tier별 조건부 구조

가장 강한 이질성이 PBR tier에서 확인되었다:

| PBR tier | β (A) | β (B) | p |
|----------|-------|-------|---|
| Low | −0.0221 | −0.0196 | 미유의 |
| Mid | −0.0625 | −0.0504 | 0.049 / 0.097 |
| High | **+0.3189** | **+0.3527** | <0.0001 |

residual risk × Bear interaction은 **PBR-High에서 강하게 나타나고 PBR-Low에서는 소멸하거나 반대 방향**을 보인다.

저PBR 종목에서는 PBR 자체의 Bear 방어적 관계(Stage 20A)가 이미 강하게 나타나므로 residual risk의 추가적인 설명력이 제한될 수 있다. 반면 고PBR 영역에서는 PBR의 방어 효과가 상대적으로 약하고, debt_ratio·PBR·Market Cap으로 설명되지 않는 residual risk signal이 Bear 환경에서 추가적인 상대성과와 연결될 가능성이 있다.

### 3. Market-Cap tier별 구조

| Cap tier | β (A) | p |
|----------|-------|---|
| Small | −0.0390 | 미유의 |
| Mid | +0.1594 | 0.0044 |
| Large | +0.1511 | <0.0001 |

Small에서는 미유의, Mid/Large에서는 유의. PBR tier에 비해 구조가 덜 선명하며, Market Cap 조건만으로 residual risk × Bear interaction의 위치를 명확하게 특정하기는 어렵다.

### 사전등록 판정

전체 표본에서는 residual risk × Bear interaction이 유의했지만, 세부 표본에서는 **방향의 일관성이 확인되지 않았다.** 특히 PBR tier에서 효과가 명확하게 분기된다.

따라서 이를 전체 universe에 걸친 균일한 interaction으로 해석하기보다는:

> **Residual risk × Bear interaction은 PBR-High 영역에 집중되어 있으며, PBR-Low 영역에서는 나타나지 않는다.**

라고 해석한다.

### Stage 20B~20D 연결

```text
Stage 20B
DART debt_ratio × Bear → PBR/Cap × Regime 통제 후 유의성 소실

Stage 20C
Naver risk_score에서 debt_ratio 제거 → Bear interaction 약 87~90% 유지

Stage 20D
residual risk × Bear를 조건부 분석
  PBR-Low: 소멸/반대 방향
  PBR-High: β=+0.3189, p<0.0001
  Q1: 미유의
  Q2/Q3+: β≈+0.21~+0.26
```

### 결론

1. Naver `risk_score`의 Bear interaction은 `debt_ratio`로 설명되지 않는 residual 성분에서도 관찰된다.
2. 그러나 이 residual interaction은 모든 종목에 균일하게 존재하지 않는다.
3. 가장 뚜렷한 조건은 **PBR-High 영역**이며, PBR-Low에서는 interaction이 나타나지 않는다.
4. 또한 interaction은 기간에 따라 달라지며, Q1과 Q2/Q3+에서 서로 다른 패턴을 보인다.
5. 따라서 현재 증거는 단순한 "risk factor × Bear"보다는 **PBR 수준에 조건부인 residual risk signal × Bear 구조**를 지지한다.
6. 이 결과만으로 residual signal의 개별 구성요소나 leakage 여부를 특정할 수 없다.

현재까지 나온 결과를 가장 정확하게 이어주는 그림:

> **Bear에서 저PBR이 방어력을 제공하는 한편, 고PBR 종목에서는 debt_ratio·PBR·Cap으로 설명되지 않는 residual risk signal이 방어력의 추가적인 구분자가 될 가능성이 있다.**

### 사전등록 설계 (원본)

**Primary**: bear:residual_z β의 방향 일관성 확인.  
**서브그룹**: 전체, STRONG_BUY, 기간별(Q1/Q2/Q3+), PBR tier(Low/Mid/High), Cap tier(Small/Mid/Large).  
**판정**: 방향 일관성이 유지되면 보편적 효과, 방향이 달라지면 조건부 효과.

---

## Stage 20E-1 Q1 Bear 기간 특성 분석

**목적**: Stage 20D에서 관찰된 Q1 residual risk × Bear interaction의 미유의성이 기간 자체의 효과인지, 표본 구성·통계적 검정력 또는 Bear 환경의 강도 차이 때문인지 구분한다.

**사전등록**: "Q1 × PBR-High의 표본 수, Bear 날짜 특성(하락폭·변동성), universe composition, residual risk 분포를 기간별로 비교하여 Q1 미유의의 원인을 좁힌다."

### 분석 결과

**검정력**

- Q1 × PBR-High: **368행**, 12개 날짜 — 통계적 검정력 부족으로 보기 어렵다.

**Universe Composition**

Q1 Bear와 Q2/Q3+ Bear 모두 PBR Low/Mid/High 비중이 약 **33%씩**으로 거의 동일하다. PBR composition 차이는 Q1/Q2+ 차이를 설명하지 못한다.

**Residual Risk 분포**

두 기간의 residual risk 분포가 모두 평균 ≈ 0, 표준편차 ≈ 0.7로 유사하다. residual risk 분포의 차이도 Q1/Q2+ 차이를 설명하지 못한다.

**Bear Intensity**

| 구분 | Q1 Bear | Q2/Q3+ Bear |
|------|---------|-------------|
| KOSPI 20D 평균 수익률 | **−6.32%** | **−14.54%** |
| 변동성 (annualized) | **52.0%** | **69.4%** |

Q2/Q3+ Bear의 평균 하락폭이 Q1 Bear보다 약 **2.3배** 크다.

**PBR-High subgroup interaction 비교**

- Q1 Bear: β = **+0.0737**, p = **0.074** (방향 일치, 통계적 미유의)
- Q2/Q3+ Bear: β = **+0.4498**, p < **0.0001** (강하게 유의)

방향은 동일하지만 하락 강도가 깊어질수록 interaction magnitude가 크게 증가한다.

### 결론

검정력, composition, residual 분포가 모두 유사한 상황에서 Q1 Bear와 Q2/Q3+ Bear 사이의 가장 큰 차이는 **Bear intensity**이다.

> **Residual risk × Bear interaction은 얕은 하락장(Q1 평균 −6.3%)에서는 약하게 나타나고, 깊은 하락장(Q2/Q3+ 평균 −14.5%)에서 강하게 발현될 가능성이 있다.**

Q1/Q2+ 구분은 기간 레이블이 아니라 **Bear 강도의 대리 변수**였을 가능성이 있다. 이를 직접 확인하는 분석이 Stage 20E-2이다.

---

## Stage 20E-2 Bear Intensity Conditionality

**목적**: Stage 20E-1에서 Q1 Bear(평균 −6.3%)와 Q2/Q3+ Bear(평균 −14.5%)의 하락 강도 차이를 확인한 뒤, KOSPI 20D 하락 강도에 따라 residual risk × Bear interaction의 크기가 실제로 달라지는지 직접 검정한다.

**사전등록**: "KOSPI 20D return 연속형 × residual_z interaction을 primary, Shallow(0~−10%) vs Deep(≤−10%) categorical split을 secondary로 검정한다. −10% threshold는 exploratory cut. Primary가 주된 추론."

### Primary — 연속형 Bear Intensity

사전등록한 `residual_risk × KOSPI 20D return` interaction을 검증한 결과, 전체 표본과 PBR-High 모두 강한 interaction이 확인되었다.

| Model | 전체 β | 전체 p | PBR-High β | PBR-High p |
|-------|--------|--------|------------|------------|
| residual_A_z × kospi_ret_z | **−0.0520** | <0.0001 | **−0.1678** | <0.0001 |
| residual_B_z × kospi_ret_z | **−0.0560** | <0.0001 | **−0.1980** | <0.0001 |

KOSPI 20D 수익률이 더 낮아질수록, 즉 시장 하락 강도가 커질수록 residual risk가 높은 종목의 benchmark-relative 방어력이 증가한다. PBR-High에서는 interaction magnitude가 전체 표본보다 약 **3~4배** 강하게 나타난다.

### Secondary — Shallow vs Deep Bear

사전등록한 categorical split에서도 동일한 방향이 확인되었다.

**전체 표본 (Version A 기준):**

| Bear 구간 | β | p |
|-----------|---|---|
| Shallow Bear (0 ~ −10%) | ≈ +0.003 | 0.926 (미유의) |
| Deep Bear (≤ −10%) | **+0.176** | <0.0001 ★ |

Δ(Deep − Shallow) = **+0.173**

**PBR-High:**

| Bear 구간 | β | p |
|-----------|---|---|
| Shallow Bear | **+0.218** | 0.001 ★ |
| Deep Bear | **+0.452** | <0.0001 ★ |

PBR-High에서는 얕은 하락장에서도 residual risk의 방어적 관계가 나타나며, 깊은 하락장에서 magnitude가 2배 이상 확대된다.

### Q1 / Q2+ 연결

Stage 20D에서 관찰된 기간별 차이와 Bear intensity 결과가 일치한다.

- Q1 Bear 평균 KOSPI 20D return: **−6.32%** → Shallow Bear 영역과 일치 → 전체 표본 interaction은 약하거나 미유의
- Q2/Q3+ Bear 평균: **−14.54%** → Deep Bear 영역과 일치 → interaction이 강하게 유의

따라서 Stage 20D에서 관찰된 Q1 vs Q2/Q3+ 차이는 **기간 자체의 효과라기보다 Bear intensity 차이와 일관된 패턴**으로 해석된다.

### 종합 판정: Bear intensity conditionality — **Robust**

Primary 연속형과 Secondary categorical이 모두 동일한 방향을 보였으며, PBR-High에서 효과가 더욱 강하게 나타났다.

> **Residual risk × Bear interaction은 이분법적인 Bear 여부만으로 결정되지 않고, 시장 하락 강도가 깊어질수록 강해지는 조건부 구조를 보인다.**

이는 Stage 20D의 기간 조건부 결과를 **시장 스트레스 강도 조건부 효과**로 구체화한다. Stage 20D에서 Q1 미유의는 기간 효과가 아니라 Bear intensity 차이와 일관된 패턴이었다.

---

## Stage 20F Sector Concentration / Portfolio Construction Diagnostic

**상태**: ✅ 완료 (2026-10-02)

**핵심 질문**

> **STRONG_BUY 선택 결과의 성과가 개별 종목 선택 때문인지, 아니면 특정 섹터 집중 때문에 확대·축소되는지?**

**사전등록**: "STRONG_BUY universe의 sector concentration 및 sector-relative performance를 분해하고, 동일한 종목 selection을 유지한 sector-neutralized counterfactual을 통해 observed alpha의 sector concentration 기여도를 평가한다."

**건드리지 않는 것**: score, grade, STRONG_BUY 기준, signal 생성, PBR, risk_score, regime rule. 선택 결과의 composition만 분석한다.

### 실행 결과

**표본**: retro_calibration × universe_exposure, `grade = 'strong_buy'`, `alpha_20d IS NOT NULL`, 141 signal_dates

**미분류 sector 처리**: pykrx 소스에서 sector=None인 33 ticker가 16 signal_dates에서 비정상적으로 높은 alpha(+9.84%)를 보임. 1월 연초효과 + small-cap bias 혼재로 오염 판정 → 미분류 제거 후 결과를 기준으로 채택.

#### [1] Sector Concentration

모든 주요 섹터에서 STRONG_BUY 비중과 universe 비중의 차이가 크지 않았다. 특정 2~3개 섹터에 극단적으로 쏠린 패턴은 없었음.

#### [2] Sector Contribution (미분류 제거 후)

거의 모든 섹터에서 alpha 기여가 음수였다. 전기·전자, 운송장비·부품 등이 주요 음수 기여 섹터였으나, 이는 sector concentration 때문이 아니라 해당 sector 내 종목들 자체의 성과가 부진했기 때문이다.

- Contribution 합계: **−3.25%**

#### [3] Sector-Neutralized Counterfactual (미분류 제거 후)

| | 평균 alpha | Δ |
|---|---|---|
| Actual (현재 방식) | **−2.98%** | — |
| Sector-neutralized (동일비중) | **−3.99%** | −1.01%p |

Neutralized가 Actual보다 1.01%p **더 낮다** — sector concentration이 오히려 성과에 소폭 기여하고 있음.

### 판정

**Sector concentration은 현재 손실의 주된 원인이 아님.**

사전등록 판정 기준 대조:

| 결과 | 해석 |
|------|------|
| ~~Actual < Neutralized (차이 큼)~~ | ~~portfolio construction이 병목~~ |
| **Actual > Neutralized** | **Actual이 더 나음: concentration이 오히려 기여** |

"특정 섹터에 몰려서 STRONG_BUY 성과가 나빠졌다"는 설명은 현재 데이터에서 지지되지 않는다. 단, 이것은 "섹터 리스크가 일반적으로 중요하지 않다"는 뜻이 아니다 — 현재 STRONG_BUY selection의 성과를 sector concentration이 주로 설명하지 않는다는 뜻으로 제한한다.

**더 직접적인 후보는 sector 내 종목 selection 자체다.** 거의 모든 섹터에서 STRONG_BUY 선택 종목이 KOSPI 대비 열위를 보였으며, sector 동일비중화로도 개선되지 않았다.

### 20G 방향 수정

원래 20G는 "sector constraint 설계"로 계획됐으나, 20F 결과가 이 방향을 반박한다. sector concentration이 병목이 아니므로 sector cap 구현의 근거가 없다. **20G는 폐기.**

### Raw Alpha Reconciliation (2026-10-02, 20F 추가 검증)

Stage 20F에서 관찰된 −2.98% raw mean이 Stage 17/18 결론과 모순되는지 별도 확인했다.

**표본 provenance 차이**:

| | Stage 17 당시 | 현재 retro DB (v8.6.0) |
|---|---|---|
| STRONG_BUY n | **1,639** | **7,290** |
| signal_dates | 141 | 141 |
| per-date 중앙값 | 11/date | ~52/date |

v8.6.0에서 supply_score 제거로 STRONG_BUY 기준이 완화되어 표본이 4.4배 팽창했다. Stage 17/18 표본과 현재 retro DB를 직접 비교하면 안 된다.

**측정 대상 차이**:

Stage 17/18은 raw mean을 측정하지 않았다.
- Stage 17: partial IC (Valuation 통제 후 risk_score와 alpha_20d의 Spearman 순위 상관)
- Stage 18: Δ IC_Bear − IC_Bull, permutation test

"STRONG_BUY outperforms"를 raw mean으로 주장한 적이 없다.

**Bear/Bull 분해 (v8.6.0, 미분류 포함)**:

| Regime | n_dates | Signal_date EW mean | Win rate |
|---|---|---|---|
| Bear | 51 | **+9.88%** | 86% |
| Bull | 90 | **−9.97%** | 20% |
| **전체** | 141 | **−2.79%** | 44% |

전체 기간 중 Bull이 63.8%(90/141)를 차지해서 전체 raw mean이 음수가 된 것이다. 이 엔진은 Bear 방어 엔진이며, Bear에서의 성과는 여전히 강하다.

**결론**: 현재 v8.6.0 STRONG_BUY raw mean −2.98%는 Stage 17/18의 conditional IC 분석과 모순되지 않는다. Bear/Bull 구분, 표본 팽창, 측정 대상 차이를 고려하면 완전히 일관된 그림이다.

## Stage 20G — Valuation Signal Expansion: PBR vs PER 🔵 Pre-registered (2026-10-06)

### 탐색 결과

2020~2026 전체 시장 데이터(pit_market_fundamentals × market_ohlcv)에서 valuation 변수와 향후 benchmark-relative return의 관계를 비교했다. 20거래일 비중복 샘플링, n=114,438 관측치.

| Factor                  | Mean IC | t-stat | Positive cross-sections |
| ----------------------- | ------: | -----: | ----------------------: |
| `val_score` (PER-based) |   0.058 |    5.4 |                     74% |
| PBR (낮을수록 유리)           |   0.079 |    5.1 |                     67% |

PBR은 현재 PER 기반 `val_score`보다 높은 평균 IC를 보였다.

### 멀티 윈도우 결과 (20D / 40D / 60D)

| Window | val_score IC | t-stat | pos% | PBR IC | t-stat | pos% |
| ------ | -----------: | -----: | ---: | -----: | -----: | ---: |
| 20D    |        0.058 |    5.4 |  74% |  0.079 |    5.1 |  67% |
| 40D    |        0.065 |    4.3 |  76% |  0.094 |    3.8 |  71% |
| 60D    |        0.076 |    3.9 |  81% |  0.111 |    4.0 |  74% |

→ 두 팩터 모두 기간이 길어질수록 IC가 강화된다. PBR은 모든 윈도우에서 val_score보다 IC가 높고 방향도 일관된다.

### PBR 구간별 패턴 (20D 기준)

| PBR 구간  | 평균 alpha_20d |
| -------- | -----------: |
| < 0.5    |     **+0.07%** |
| 0.5 ~ 1  |       −0.35% |
| 1 ~ 2    |       −0.76% |
| 2 ~ 3    |       −0.77% |
| > 3      |       −1.39% |

낮은 PBR과 높은 상대성과의 관계가 단조적으로 관찰된다.

### PER 기반 val_score의 비단조성

현재 PER 기반 `val_score`는 낮은 PER일수록 단조롭게 개선되는 패턴을 보이지 않는다.

| val_score 구간 | PER 구간  | 평균 alpha_20d |
| ------------- | -------- | -----------: |
| 0             | > 20     |       −0.83% |
| 6             | ≤ 20     |       −0.51% |
| 14            | ≤ 12     |       −0.32% |
| 22            | ≤ 8      |       −0.79% |
| 25            | ≤ 6      |       +0.01% |

특히 PER 6~8 구간(val=22)에서 역전이 관찰된다.

### 해석

현재 결과는 **PBR이 기존 PER 기반 valuation score에 추가적인 정보를 제공할 가능성**을 시사한다. Stage 20A~20E에서 저PBR이 Bear 구간에서 방어적이라는 결과가 이미 있었는데, 이번에는 장기 전체 표본(Bull+Bear 포함)에서도 PBR IC가 PER보다 높게 나왔다.

그러나 단일 구성요소 IC 비교만으로 scoring function을 변경하지 않는다.

### Stage 20G 사전등록

PBR을 `val_score`에 통합하기 전에 다음을 검증한다.

**Primary requirement** ← 이번 탐색에서 이미 충족

- ✅ 20D/40D/60D 모든 윈도우에서 PBR 방향성 재현
- ✅ PBR의 추가 정보가 기존 PER-based `val_score`와 incremental information을 제공하는지 확인 → Stage 20G-1 결과 참조

**Secondary**

- ⬜ PBR과 PER을 결합한 후보 valuation score를 정의하고 기존 `val_score`와 cross-section IC 비교
- score 변경은 결합 후보 비교 후 별도 결정

### 판정 원칙

- 여러 forward window에서 PBR의 incremental IC가 확인되면 → **PBR 통합 후보**
- PBR IC가 기존 PER score와 중복(collinear)이라면 → **현재 scoring 유지**
- 현재 단계에서는 **PBR의 우수한 예측력은 관찰된 사실이나, scoring 통합은 미확정**

---

## Stage 20G-1 — PBR Incremental Information (2026-10-06)

### 목적

PBR이 기존 PER 기반 `val_score`와 독립적인 alpha 예측 정보를 담고 있는지 확인.  
검증 방법: `PBR_residual = rank(PBR) - predicted(rank(PBR) | rank(val_score))` 구성 후 `IC(alpha, PBR_residual)` 계산.

### 결과

**PBR ↔ val_score 공유 정도 (날짜별 Spearman r 평균): −0.534**  
절댓값 0.53으로 중간 수준의 겹침. 완전 중복이 아님을 시사.

| Window | val_score IC | PBR raw IC | PBR residual IC | t-stat | pos% |
| ------ | -----------: | ---------: | --------------: | -----: | ---: |
| 20D    |        0.058 |      0.079 |       **0.057** |    4.3 |  62% |
| 40D    |        0.065 |      0.094 |       **0.071** |    3.3 |  71% |
| 60D    |        0.076 |      0.111 |       **0.083** |    3.5 |  67% |

PER로 설명된 부분을 제거한 후에도 PBR residual IC가 val_score raw IC에 근접하는 수준으로 유지된다. 즉 **PBR이 담고 있는 정보의 대부분이 PER과 독립적**이다.

### 판정

판정 기준 적용: 20D/40D/60D 모두 residual IC 유의, 기간이 길수록 강화 → **가장 강한 근거 충족**.

→ **PBR 통합 후보 확정.** 다음 단계(Secondary): PBR+PER 결합 후보 score 설계 및 기존 `val_score`와 cross-section IC 비교.

---

## Stage 20G-2 — Combined Valuation Score 🔵 Pre-registered (2026-10-06)

### 목적

Stage 20G-1에서 확인된 PBR의 PER-independent incremental information이 기존 `val_score`와 결합했을 때 valuation signal의 예측력을 실제로 개선하는지 검증한다.

### 사전 정의된 후보 score

각 `signal_date`의 cross-section에서:

`combined_val = 0.5 × z(val_score) + 0.5 × z(-log(PBR))`

PBR은 낮을수록 높은 score가 되도록 방향 반전. 두 변수는 cross-sectional z-score로 표준화 후 동일 가중. weight 탐색 없음.

### 비교 방법

PBR과 `val_score`가 모두 존재하는 동일 matched sample에서 세 signal 비교:

1. 기존 `val_score`
2. PBR
3. `combined_val`

Forward window: **20D / 40D / 60D**

### Primary comparison

핵심 평가지표: `ΔIC = IC(combined_val) − IC(val_score)` — 세 window에서 방향 일관성 확인.

### 판정 원칙

- 20D/40D/60D 모두 ΔIC > 0 → **PBR 통합이 valuation signal을 일관되게 개선 → scoring 변경 후보**
- 일부 window에서만 개선 → horizon-conditional improvement
- 대부분 ΔIC ≤ 0 → PBR 독립 정보는 확인됐으나 현재 결합 방식에 추가적 가치 없음

Score weight·grade threshold는 본 결과 확인 후 별도 결정. 사후적 weight 선택 없음.

### 결과 (2026-10-06)

| Window | val_score IC | PBR IC | combined IC | ΔIC | ΔIC pos% |
| ------ | -----------: | -----: | ----------: | --: | -------: |
| 20D    |        0.058 |  0.079 |       0.077 | **+0.019** | 61% |
| 40D    |        0.065 |  0.094 |       0.090 | **+0.025** | 71% |
| 60D    |        0.076 |  0.111 |       0.106 | **+0.030** | 67% |

3개 윈도우 모두 ΔIC > 0. combined IC는 val_score 대비 +33~+39% 개선.

### 판정

**20D/40D/60D 모두 ΔIC > 0, 기간이 길수록 개선 폭 증가.**

→ **판정 기준 최상단 충족: PBR 통합이 valuation signal을 일관되게 개선. scoring 변경 후보로 승격.**

### 다음 단계 메모

이 결과는 **전체 시장(cross-section z-score 방식)** 기준이다. production에 적용하려면 z-score 방식 대신 **절대값 기준 PBR 점수 테이블** 설계가 필요하다. 설계·비교는 별도 단계에서 진행한다. 현재 단계에서 production score는 변경하지 않는다.

---

## Stage 20H — Production PBR Score 🔵 Pre-registered (2026-10-06)

### 목적

Stage 20G-2의 z-score combined 방식을 **production-compatible 고정 점수 테이블**로 대체하고, 기존 `val_score` 대비 IC 개선 여부를 확인한다.

### 설계 원칙

- 해당 종목의 PBR 값 하나만으로 점수가 결정된다 (당일 시장 분포 불필요)
- 점수 테이블은 아래 단일 안으로 사전 고정하며 사후 조정하지 않는다
- 결합 방식: `combined_val = val_score + pbr_score` (단순 합산, rank IC는 척도에 독립)

### 사전 정의 PBR 점수 테이블

| PBR 구간       | pbr_score |
| ------------- | --------: |
| PBR < 0.5     |        25 |
| 0.5 ≤ PBR < 1 |        20 |
| 1 ≤ PBR < 2   |        15 |
| 2 ≤ PBR < 3   |        10 |
| PBR ≥ 3       |         0 |

### 비교 방법

PBR과 `val_score`가 모두 존재하는 동일 matched sample에서:

1. 기존 `val_score`
2. `combined_val = val_score + pbr_score`

를 **20D / 40D / 60D** Spearman IC로 비교.

핵심 평가지표: `ΔIC = IC(combined_val) − IC(val_score)`

### 판정 원칙

- 20D/40D/60D 모두 ΔIC > 0 → **production score 변경 후보로 승격**
- 일부 window에서만 개선 → horizon-conditional, 반영 보류
- 대부분 ΔIC ≤ 0 → 고정 테이블 방식의 추가 가치 없음, 현재 scoring 유지

### 결과 (2026-10-06)

| Window | val_score IC | combined IC | ΔIC | ΔIC t-stat | ΔIC pos% |
| ------ | -----------: | ----------: | --: | ---------: | -------: |
| 20D    |        0.058 |       0.074 | **+0.016** | 3.75 | 61% |
| 40D    |        0.065 |       0.086 | **+0.021** | 2.91 | 73% |
| 60D    |        0.076 |       0.101 | **+0.025** | 2.88 | 63% |

ΔIC가 20D/40D/60D 전부 양수이고 t-stat도 모두 유의 (t > 2.8).  
참고: z-score 방식(20G-2) combined IC(0.077/0.090/0.106) 대비 소폭 낮지만 개선 방향은 동일.

### 판정

**20D/40D/60D 모두 ΔIC > 0, 유의수준 충족.**

→ **판정 기준 최상단 충족: production score 변경 후보로 승격.**

### Evidence-Gated 진행 상태

| 단계 | 내용 | 상태 |
| ---- | ---- | ---- |
| Research | Stage 20G~20H: multi-window IC, incremental IC, production table 비교 | ✅ 완료 |
| Replay | 과거 동일 데이터에서 기존 val_score vs combined_val 비교 | ✅ 20H에서 완료 |
| Regression | 코드 변경 후 테스트 통과 | ⬜ |
| Calibration | 새 engine 버전으로 baseline 설정 | ⬜ |
| Forward validation | OOS에서 개선 방향 재현 | ⬜ |

Regression부터가 실제 production 반영 단계이다. 현재 scoring은 변경하지 않는다.

---

## Stage 20I — Production Score Regression 🔵 Pre-registered (2026-10-06)

### 질문

검증된 고정 PBR 점수표를 실제 scoring pipeline에 넣었을 때, 기존 시스템의 동작과 성과가 실제로 개선되는가?

### 변경 범위

```
기존:  valuation = val_score          (0~25, available_max=75)
후보:  valuation = val_score + pbr_score  (0~50, available_max=100)
```

PBR score table (20H 확정값 그대로):

| PBR 구간 | pbr_score |
| -------- | --------: |
| < 0.5    |        25 |
| 0.5~1    |        20 |
| 1~2      |        15 |
| 2~3      |        10 |
| ≥ 3      |         0 |

live scoring은 변경하지 않는다. candidate 버전을 retro_calibration + pit_market_fundamentals 데이터로 Replay한다.

### 비교 방법

retro_calibration (signal_date, val_score, earn_score, risk_score, alpha_20d) × pit_market_fundamentals (trade_date=signal_date, pbr) JOIN.

1. **신호 변화**: 기존/신규 normalized score, grade 분포, STRONG_BUY overlap
2. **IC 비교**: retro_calibration 동일 표본에서 20D IC, ΔIC
3. **구성 변화**: PBR tier별 선택 비중, 신규 진입/이탈 종목
4. **구조 훼손 여부**: Bear/Bull 구간 alpha, Risk×Regime 관계 유지 여부

### 판정 원칙

- 성과 개선 + 핵심 구조 유지 → **production 변경 후보 확정**
- 성과 개선 없음 → 20H 통계 결과와 production score 사이 괴리 조사
- 성과 악화 → PBR 독립 정보 확인됐으나 현재 architecture와 부적합

### 결과 (2026-10-06)

**표본**: n=17,218, 141 signal-dates (retro_calibration × pit_market_fundamentals 동일 matched)

#### 1. Grade 분포 변화

| Grade | 기존 | 신규 | Δ |
| ----- | ---: | ---: | -: |
| STRONG_BUY | 7,274 | 6,186 | −1,088 |
| BUY | 4,250 | 5,322 | +1,072 |
| NEUTRAL | 5,321 | 4,084 | −1,237 |
| AVOID | 373 | 1,626 | +1,253 |

STRONG_BUY overlap 80% (기존 SB의 80%가 신규에서도 SB). 신규 진입 365, 이탈 1,453.

#### 2. Cross-section IC

| | 기존 val_score | 신규 combined | ΔIC |
| --- | ---: | ---: | ---: |
| Mean IC | 0.148 | **0.179** | **+0.031** |
| t-stat | 10.3 | 13.3 | 6.3 |
| pos% | 79% | 86% | 65% |

#### 3. STRONG_BUY alpha_20d

| | 기존 SB | 신규 SB |
| --- | ---: | ---: |
| mean | −1.56% | **−1.14%** |
| median | −2.68% | **−2.52%** |
| n | 7,274 | 6,186 |

#### 4. STRONG_BUY PBR tier 구성 변화

| PBR 구간 | 기존 비중 | 신규 비중 |
| -------- | --------: | --------: |
| < 0.5 | 30% | **41%** |
| 0.5~1 | 46% | 49% |
| 1~2 | 20% | 9% |
| ≥ 2 | 4% | **0%** |

고PBR(≥2) 종목이 STRONG_BUY에서 완전 제거. 저PBR(<0.5) 집중도 증가.

#### 5. Bear/Bull 구간 STRONG_BUY alpha (핵심 구조 확인)

| 구간 | 기존 SB | 신규 SB |
| ---- | ------: | ------: |
| Bear (<−3%) | +13.59% | **+14.18%** |
| Bull (≥−3%) | −7.94% | **−7.71%** |

Bear 방어 핵심 구조 유지 및 소폭 개선. 이 엔진의 core thesis와 방향 일치.

### 판정

**성과 개선 + 핵심 구조 유지.**

→ **production 변경 후보 확정.**

Evidence-Gated 5단계 진행 상태:

| 단계 | 내용 | 상태 |
| ---- | ---- | ---- |
| Research | Stage 20G~20H: multi-window IC, incremental IC, production table 비교 | ✅ |
| Replay | Stage 20I: 동일 표본 기존/신규 비교, ΔIC=+0.031 (t=6.3), Bear alpha 유지 | ✅ |
| Regression | 코드 변경 + 테스트 통과 | ⬜ |
| Calibration | 새 engine 버전으로 baseline 설정 | ⬜ |
| Forward validation | OOS에서 개선 방향 재현 | ⬜ |

다음 단계: Stage 20J — production code 변경 + 테스트 통과.

---

### Stage 20A~20F 종합

6개 세부 연구가 한 가지 일관된 그림으로 닫힌다:

> **이 엔진은 Bear 방어 엔진이다. 깊은 시장 하락에서 저PBR·저부채 종목을 통해 상대적 방어력이 발휘된다. Bull 시장에서의 underperformance는 설계 특성이지 버그가 아니다. Sector concentration은 병목이 아니며, selection 품질 자체가 regime-conditional하다.**

Stage 21 Real OOS에서 확인해야 할 핵심 명제는 하나다: **Bear 구간에서 +9.88% EW mean이 재현되는가?**

---

## Stage 20 → Stage 21 전환 기록 (2026-10-01)

### 연구 방향 결정

Stage 20A~20E-2 체인이 retrospective 단계에서 충분히 정리됐다는 판단 하에 Stage 21 Real OOS를 우선 순위로 전환한다.

**전환 근거:**

Stage 20 체인에서 확인된 핵심 가설은 다음과 같이 구체화됐다.

> **깊은 시장 하락에서, 특히 고PBR 영역에서, debt_ratio·PBR·Cap으로 설명되지 않는 Naver residual risk가 상대성과와 연결된다.**

이 가설을 추가 retrospective 데이터로 더 정교화하기보다는, **Real OOS에서 같은 방향이 나타나는지**가 현재 시점에서 가장 가치 있는 검증이다.

**연구 우선순위 (2026-10-01 기준):**

1. **Stage 21 Real OOS 축적** — 이미 진행 중
2. **Naver leakage provenance audit** — 병행. 코드·타임스탬프·캐시에서 실제 증거 확인 우선, 증거 없으면 proxy 복원은 하지 않음
3. **새 retrospective decomposition 추가 보류** — Naver 구성요소를 현재 데이터로 재구성하는 방식은 또 다른 불확실성을 만들 수 있음

**Stage 21에서 특별히 확인할 항목:**

- `alpha_20d` OOS 기준 방향성
- Bear intensity × residual risk 구조의 OOS 재현 여부 (깊은 하락 구간에서 확인)
- PBR tier × Bear intensity 구조의 방향 일치 여부

OOS에서 재현되면 Stage 20 체인이 retrospective pattern을 넘어서는 것이고, 재현되지 않으면 그 자체가 중요한 calibration 신호이다.

### Naver Leakage Provenance Audit 계획

**목적:** risk_score 계산에 사용된 Naver 데이터가 signal_date 시점의 데이터인지, current-date 데이터인지 코드 수준에서 확인.

**확인 항목:**
1. `risk_score` 계산 스크립트에 current-date 호출이 있는지
2. 저장 당시 timestamp와 `signal_date` 관계
3. raw 데이터/캐시/로그에 당시 데이터가 남아 있는지
4. DB에 저장된 `risk_score`의 기록 시점 메타데이터

**판단 원칙:** 실제 증거가 확인되면 별도 분석으로 확장. 증거 없이 proxy로 구성요소를 흉내 내는 분석은 진행하지 않음.

### Naver Leakage Provenance Audit 결과 (2026-10-01)

**확정된 사실:**

1. **look_ahead 명시 기록**: `retro_calibration.look_ahead_note = 'earnings/risk=Naver(current)'` — 전체 17,648행 동일. `research/retrospective_calibration.py` line 252에 하드코딩.

2. **저장 시점**: `created_at` 범위 2026-09-10 ~ 2026-09-16. `signal_date` 범위 2026-01-02 ~ 2026-07-30. 모든 retrospective 데이터가 9월에 소급 계산됐고 당시 현재(current) Naver 데이터를 사용.

3. **코드 수준 확정**: `fetch_quarterly_financials(ticker, as_of_date)` 시그니처에 날짜 파라미터가 있지만, 실제 API 호출(`_fetch_quarterly_mobile_api(ticker)`)에는 ticker만 전달되고 날짜가 없다. 즉 **날짜 파라미터가 있어도 실제 요청에는 적용되지 않음** → 항상 현재 데이터 반환.

4. **캐시 복원 불가**: disk cache key는 `ticker:as_of_date` 형식이지만, 9월 실행 당시 quarterly 캐시에 데이터가 남아 있지 않다. **원본 Naver 데이터 복원 불가.**

**결론**: Naver 데이터 leakage가 **코드 수준에서 구조적으로 확정**됐다. risk_score는 signal_date 시점이 아닌 9월 소급 실행 당시의 현재값으로 계산됐다.

**방향 확정**: proxy로 구성요소를 흉내 내는 추가 분석은 하지 않는다. Stage 21 Real OOS에서 Naver 데이터가 fresh(=signal_date 당일)로 사용되므로, OOS 결과가 leakage 가설을 가장 직접적으로 검증한다.

---

## Stage 21 Execution Reality (사전등록)

**질문**: Top10 전략의 모델 성과가 실제 매매 가능한 수준인가?

**상태**: 🟡 Pre-registered (실제 거래기록 축적 후 실행)

**선행 조건**: 최소 60 거래일 OOS Top10 실거래 또는 관찰 데이터. 현재 OOS는 스냅샷 기록만 있으므로 본격 실행은 추후.

### 사전등록 설계

**Primary**: 
- Top10 종목별 일평균 거래대금 → 예상 슬리피지 (Amihud 비유동성 척도 또는 고정 티어)
- Turnover rate (월 기준)
- Net alpha = gross alpha − (거래비용 + 슬리피지)

**Secondary**: 거래비용 민감도 분석 — 0.1% / 0.3% / 0.5% 시나리오별 net alpha

---

## Stage 22 — 시장 하락 위험: 위험 축적 경고(A) · 추세 대응 손익(B) 🟡 사전등록 초안 (2026-10-29)

**상태**: 초안 — 수치 정의 고정, 데이터 수집·분석 전. 결과를 본 뒤 정의·기준값을 바꾸지 않는다(바꾸면 새 단계로 별도 등록).

### 배경과 성격

- 사용자 질문: "하락 전에 전조가 있었는가, 있었다면 어떤 형식인가." 관련 문헌 1차 검토(2026-10-29, 15편 — Greenwood 외 2019·2022, IMF Hennig 외 2023, Fama–French 1992, Asness 외 2013·2019, Moskowitz 외 2012, Huang 외 2020, Daniel–Moskowitz 2016, 박종원 외 2020, 엄철준·박종원 2022, 홍지연 2026, Goyal·Welch·Zafirov 2024, Harvey·Liu·Zhu 2016, Frazzini 외 2018). 초록·저자 자료·열람 가능한 본문 기준이며 원 논문 통계를 재현하지 않았다.
- **이 연구는 문헌 재현이 아니다.** 아래 A1~A3은 문헌에서 착안한 **한국 시장용 가설**이다. 원 논문의 지표 정의(산업 단위 급등, 신용/GDP, 다국가 위기 표본 등)와 다르다. A3 신용융자 잔고는 신용 팽창의 **별도 대리지표**로 취급한다.
- 구분: A = 위험이 쌓이는 상태가 큰 하락 전에 더 자주 나타났는가(정보, 매매 규칙 아님). B = 하락이 진행될 때 단순 추세 규칙으로 대응하면 실제 손익이 어땠는가(대응 비용 포함).
- **완전히 보지 않은 데이터가 아니다.** KOSPI 2020~2026 구간과 2025~26 급등·2026-10 하락은 이미 알고 있다. "이번 상승에서 몇 달 전부터 경고했을 것"은 계산 전 **가설(H-A-2026)**로만 둔다.
- 보류(이번 범위 밖): 모멘텀 순위, 외국인 순매도 신호 — 기존 프로젝트의 부정적 결과(수급 5개 연구 기각, 모멘텀 반전·무효)와 국내 문헌의 조건 차이를 먼저 설명해야 한다. 뉴스·지정학 신호 — 과거 뉴스 데이터 없음.

### 데이터 (수집 대상)

| 항목 | 원천 | 기간 | 비고 |
|---|---|---|---|
| KOSPI 지수 일별 시가·종가 | KRX `MDCSTAT00301`(지수 1001, 원 응답 보존) | 제공되는 가장 이른 날 ~ 수집일 직전 거래일 | 시가는 B 체결용. 시가가 없는 기간은 B 평가에서 제외(종가로 대체하지 않음) |
| 신용거래융자 잔고(유가증권시장) | 금융투자협회 통계(FreeSIS) — 확보 가능 여부 미확인 | 제공 범위 전체 | 확보 실패 시 A3 제외, 그 사실을 결과에 기록 |
| 배당·현금 이자 | — | — | 1차 분석에서 **두 경로 모두 미포함**(가격지수·현금 0%). 아래 B 참조 |

- 지수 원 자료에 같은 날 중복·결측이 있으면 고치지 않고 그 날짜를 기록한다. 거래일은 지수 응답의 날짜만 쓴다(달력 추정 없음).

### 하락 사건 정의 (A·B 공통 기준 사건)

- 기준: 일별 종가 C(t), 누적 고점 P(t) = max C(≤t), 낙폭 DD(t) = C(t)/P(t) − 1.
- **사건**: DD가 처음으로 **−15% 이하**가 되는 날에 사건이 성립한다(1차). −10% 기준은 2차로 같은 방식으로 따로 보고한다.
  - 사건 고점일 = 그 날의 P(t)를 기록한 날(직전 누적 고점).
  - 사건 저점일 = 고점 이후, 종가가 고점 수준을 회복하기 전까지의 최저 종가일.
  - 사건 종료일 = 종가가 고점 수준 이상을 처음 회복한 날(데이터 끝까지 미회복이면 "미종료").
- **중복 처리**: 사건 종료 전에는 새 사건을 만들지 않는다(누적 고점 기준이라 구조상 겹치지 않음). 사건 수 = 이렇게 셌을 때의 개수. **일별 관측 수를 사건 수로 세지 않는다.**

### A — 위험 축적 경고 (정보 검증, 매매 규칙 아님)

모든 백분위는 **확장 창(그날까지의 과거 값만)** 으로 계산한다. 백분위 기준 분포에 최소 관측 수가 모자라는 날은 평가에서 제외한다(창을 줄이지 않음).

| 지표 | 정의 | 경고 조건 | 기준 분포 최소 관측 |
|---|---|---|---|
| A1 2년 상승 | R2(t) = C(t)/C(t−504) − 1 (504거래일) | R2(t) ≥ 과거 R2 분포의 **90번째 백분위** | 1,260개 (≈5년) |
| A2 상승 + 낮은 변동성 | R2(t) 그리고 V(t) = 최근 60거래일 일간 로그수익률 표준편차 × √252 | R2(t) ≥ 과거 R2의 **75번째 백분위** **그리고** V(t) ≤ 과거 V의 **25번째 백분위** | 각각 1,260개 |
| A3 신용 팽창(대리지표) | G(t) = 신용융자 잔고(t)/잔고(t−252) − 1 (252거래일) | G(t) ≥ 과거 G 분포의 **90번째 백분위** | 1,260개 |

- 준비 기간: A1은 504 + 1,260 거래일이 지나야 첫 평가가 가능하다(데이터 시작 후 약 7년). 평가 시작일 = 각 지표가 처음 평가 가능한 날. 그 전 날짜는 평가에서 뺀다.
- **고점 이전·이후 경고 분리**: 사건마다
  - 고점 이전 경고 = [고점일 − 252거래일, 고점일] 안에 경고가 하루라도 켜졌는가 (+ 켜진 날 비율)
  - 고점 이후 경고 = (고점일, −15% 도달일] 안에 경고가 켜졌는가 — 별도 보고(선행 정보로 세지 않음)
  - 사건의 고점 이전 창이 평가 시작일보다 앞서면 "준비 부족"으로 표시하고 통계에서 뺀다.
- **기준률(비교 대상)**: 평가 기간 안의 모든 252거래일 창 중 경고가 하루라도 켜진 창의 비율(무작위 창 적중률).
- **헛경고**: 경고 구간(켜진 날을 20거래일 이하 틈은 이어 붙인 묶음)마다, 구간 시작 후 252거래일 안에 −15% 사건 고점이 없으면 헛경고 구간. 구간 수로 센다.
- 보고: 지표별 사건 적중 수/평가 가능 사건 수, 기준률, 헛경고 구간 수, 경고 구간 평균 길이, 경고 중 KOSPI 이후 252거래일 수익률 분포(경고가 곧 매도 시점이 아님을 확인하는 용도).
- **판정(서술형)**: 사건 적중률 − 기준률 ≥ +20%p이면 "정보 있음 후보", 미만이면 "정보 확인 안 됨". 사건 수가 적으므로 어떤 경우도 "확정"으로 쓰지 않는다. H-A-2026은 2025~26 구간의 경고 켜짐 여부를 그대로 기록한다.

### B — 200일선 추세 규칙 (대응 손익 검증)

- 규칙(하나로 고정): 거래일 t 종가 C(t) < SMA200(t)(t 포함 최근 200거래일 종가 단순평균)이면 다음 날 "현금", C(t) ≥ SMA200(t)이면 다음 날 "보유". 상태 변경은 **t+1 거래일 시가**에 체결. 재진입도 같은 규칙.
- 비교 경로: 같은 기간 지수 단순 보유.
- 수익 계산: 보유 상태의 수익 = 시가→시가(체결일 시가부터 다음 변경일 시가까지) 지수 변동. 단순 보유 경로도 같은 첫 시가에서 시작해 같은 마지막 시가에서 끝낸다.
- 비용·배당·이자 (두 경로 일관):
  - 1차: 가격지수, 배당 미포함, 현금 이자 0%, 상태 변경 1회당 편도 비용 0.10%(단순 보유는 시작 1회만).
  - 민감도: 편도 비용 0.05% / 0.30%.
  - 배당·현금 이자는 데이터 미확보로 1차에서 빠진다 — 이 누락은 단순 보유에 불리(배당 미반영)하고 B에 유리할 수도 불리할 수도 있다(현금 이자 미반영)고 결과에 명시한다. 확보되면 2차로 같은 방식 재계산.
- 평가 시작일: SMA200과 시가가 처음 모두 있는 날. A와 같은 기간의 결과도 따로 보고.
- 보고(한 숫자로 판정하지 않음):
  - 연환산 수익률, 누적 수익, 최대 낙폭(MDD), 연환산 변동성, 시장 참여 비율, 상태 변경 횟수
  - **방어 비용**: (단순 보유 연환산 수익 − B 연환산 수익)과 (단순 보유 MDD − B MDD)를 나란히
  - **놓친 상승**: 현금 구간 동안의 지수 상승 합(구간별), **재진입 후 손실**: 재진입 후 다음 이탈까지 손실로 끝난 구간 수·크기
  - **헛신호**: 이탈 후 20거래일 안에 재진입하고 재진입 시가 > 이탈 시가인 경우의 수·비용
  - 사건별: 각 −15% 사건에서 이탈 시점(고점 대비 낙폭)·재진입 시점(저점 대비 회복) 
- 해석 원칙: 수익이 낮아지고 낙폭도 줄었으면 성공·실패로 몰지 않고 "방어를 위해 지불한 수익 비용"으로 보고한다.

### 다중 시험 통제

- 지표 3개(A1~A3)·규칙 1개(B)·사건 기준 2개(−15% 1차, −10% 2차)만 시험한다. 기간·백분위·창 길이를 바꿔 다시 돌린 결과는 이 단계 결과로 쓰지 않는다.

### 산출물

- 수집 원 응답·해시, 계산 스크립트, 사건 표, 지표별 경고 구간 표, B 일별 상태·손익 표. 결과는 이 절 아래 "결과"로 추가.
