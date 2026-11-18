"""
Stage 20A: Bear × Exposure Interaction 분석

사전등록 (2026-10-01):
  "Regime별 PBR 및 market-cap exposure와 20D alpha의 관계를 비교하고,
   Bear × Exposure interaction을 통해 Stage 18/19의 Risk×Regime interaction과
   Universe Exposure의 구조적 연결 여부를 검증한다."

레짐: Stage 19와 동일 (pre-registered)
  - entry_open = signal_date 당일 시가
  - exit_close = +20영업일 종가
  - Bull: exit/entry > 1 / Bear: ≤ 1
  - Retrospective forward-regime partition. 연구 전용.

Exposure tiering: Stage 20과 동일
  - PBR: signal_date별 cross-sectional tertile (Low / Mid / High)
  - Market Cap: signal_date별 cross-sectional tertile (Small / Mid / Large)

분석:
  [A] Regime × PBR tier 교차표 (Bear-Bull Δ 및 interaction)
  [B] Regime × Market Cap tier 교차표
  [C] OLS: alpha_z ~ bear + pbr_z + bear×pbr_z  (clustered SE by signal_date)
  [D] OLS: alpha_z ~ bear + log_cap_z + bear×log_cap_z
  [E] STRONG_BUY 서브그룹 [A][B] 재실행

해석 기준:
  interaction 유의 → Stage 20 exposure와 Stage 18/19 Risk×Regime 연결 가능성 제시
  interaction 미유의 → 두 효과가 독립적 구조일 가능성 지지
  주의: 교차분석은 연관성 파악용. 설명력은 별도 분석 필요.

실행:
    cd ~/Desktop/investment
    python3 scripts/analyze_bear_exposure_interaction.py
"""

from __future__ import annotations

import sqlite3
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT     = Path(__file__).parent.parent
DB_PATH  = ROOT / "database" / "research.db"
OHLCV_DB = ROOT / "database" / "market_ohlcv.db"
KOSPI_CODE = "1001"

try:
    import statsmodels.formula.api as smf
    SM_OK = True
except ImportError:
    SM_OK = False


# ---------------------------------------------------------------------------
# 레짐 로드 (Stage 19와 동일)
# ---------------------------------------------------------------------------

def load_kospi_regime() -> dict[str, str]:
    """
    signal_date → 'Bull' | 'Bear'
    사전등록 레짐 (Stage 19): kospi_exit_close / kospi_entry_open - 1 > 0 → Bull, ≤ 0 → Bear
    entry = signal_date 당일 시가, exit = 20영업일 후 종가.
    Retrospective forward-regime partition: 실시간 탐지 불가. 연구 전용.
    """
    with sqlite3.connect(OHLCV_DB) as c:
        rows = c.execute(
            "SELECT trade_date, open, close FROM market_ohlcv WHERE ticker=? ORDER BY trade_date",
            (KOSPI_CODE,),
        ).fetchall()
    dates  = [r[0] for r in rows]
    opens  = {r[0]: r[1] for r in rows}
    closes = {r[0]: r[2] for r in rows}
    regime_map: dict[str, str] = {}
    for i, d in enumerate(dates):
        if i + 20 >= len(dates):
            continue
        entry_open = opens.get(d)
        exit_close = closes.get(dates[i + 20])
        if entry_open and exit_close and entry_open != 0:
            ret = exit_close / entry_open - 1
            regime_map[d] = "Bull" if ret > 0 else "Bear"
    return regime_map


# ---------------------------------------------------------------------------
# 데이터 로드
# ---------------------------------------------------------------------------

def load_data() -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as c:
        df = pd.read_sql_query(
            """
            SELECT signal_date, ticker, grade, alpha_20d,
                   sector, market_cap, pbr
            FROM universe_exposure
            WHERE alpha_20d   IS NOT NULL
              AND market_cap  IS NOT NULL
              AND pbr         IS NOT NULL
              AND pbr         > 0
              AND market_cap  > 0
            """,
            c,
        )

    # regime 부착
    regime_map = load_kospi_regime()
    df["regime"] = df["signal_date"].map(regime_map).fillna("Unclassified")
    df = df[df["regime"].isin(["Bull", "Bear"])].copy()
    df["bear"] = (df["regime"] == "Bear").astype(int)

    # log market cap
    df["log_cap"] = np.log(df["market_cap"])

    # signal_date별 cross-sectional tertile (Stage 20과 동일)
    def _pbr_tier(x: pd.Series) -> pd.Categorical:
        if x.nunique() < 3:
            return pd.Categorical(["Mid"] * len(x), categories=["Low", "Mid", "High"], ordered=True)
        q1, q2 = x.quantile([1 / 3, 2 / 3])
        return pd.cut(x, bins=[-np.inf, q1, q2, np.inf],
                      labels=["Low", "Mid", "High"])

    def _cap_tier(x: pd.Series) -> pd.Categorical:
        if x.nunique() < 3:
            return pd.Categorical(["Mid"] * len(x), categories=["Small", "Mid", "Large"], ordered=True)
        q1, q2 = x.quantile([1 / 3, 2 / 3])
        return pd.cut(x, bins=[-np.inf, q1, q2, np.inf],
                      labels=["Small", "Mid", "Large"])

    df["pbr_tier"] = (
        df.groupby("signal_date")["pbr"].transform(_pbr_tier).astype(str)
    )
    df["cap_tier"] = (
        df.groupby("signal_date")["log_cap"].transform(_cap_tier).astype(str)
    )

    # signal_date별 표준화 (OLS 회귀용)
    for col in ["alpha_20d", "pbr", "log_cap"]:
        df[f"{col}_z"] = df.groupby("signal_date")[col].transform(
            lambda x: (x - x.mean()) / (x.std() + 1e-12)
        )

    return df


# ---------------------------------------------------------------------------
# 교차표 출력
# ---------------------------------------------------------------------------

def cross_table(df: pd.DataFrame, tier_col: str, tier_order: list[str], label: str) -> None:
    """Regime × Exposure tier 교차표 (n, mean alpha_20d, Bear-Bull Δ, t)"""
    print(f"\n[{label}] Regime × {tier_col}")
    print(f"  {'tier':>7}  {'Bear mean':>10}  {'n':>4}  {'Bull mean':>10}  {'n':>4}  {'Δ(B-B)':>8}  {'t':>6}")
    print("  " + "-" * 65)

    tier_stats: dict[str, dict] = {}
    for tier in tier_order:
        tier_stats[tier] = {}
        for reg in ("Bear", "Bull"):
            sub = df[(df["regime"] == reg) & (df[tier_col] == tier)]["alpha_20d"]
            n    = len(sub)
            mean = sub.mean() if n > 0 else float("nan")
            se   = sub.sem()  if n > 1 else float("nan")
            tier_stats[tier][reg] = (n, mean, se)

    for tier in tier_order:
        bn, bm, bse = tier_stats[tier]["Bear"]
        un, um, use = tier_stats[tier]["Bull"]
        delta = bm - um
        diff_se = (bse ** 2 + use ** 2) ** 0.5 if (not np.isnan(bse) and not np.isnan(use)) else float("nan")
        t = delta / diff_se if diff_se > 0 else float("nan")
        print(f"  {str(tier):>7}  {bm:>+9.2f}%  {bn:>4d}  {um:>+9.2f}%  {un:>4d}  {delta:>+7.2f}%  {t:>+6.2f}")

    # Interaction: 첫 tier vs 마지막 tier 의 Bear-Bull Δ 차이
    lo = tier_order[0]
    hi = tier_order[-1]
    lo_delta = tier_stats[lo]["Bear"][1] - tier_stats[lo]["Bull"][1]
    hi_delta = tier_stats[hi]["Bear"][1] - tier_stats[hi]["Bull"][1]
    interaction = lo_delta - hi_delta
    print(f"\n  Interaction Δ ({lo} vs {hi}): "
          f"ΔBear-Bull({lo})={lo_delta:+.2f}%  "
          f"ΔBear-Bull({hi})={hi_delta:+.2f}%  "
          f"→ Δ-of-Δ = {interaction:+.2f}%p")


# ---------------------------------------------------------------------------
# OLS
# ---------------------------------------------------------------------------

def ols_analysis(df: pd.DataFrame, exposure_z: str, label: str) -> None:
    """OLS: alpha_z ~ bear + exposure_z + bear:exposure_z (clustered SE by signal_date)"""
    if not SM_OK:
        print("  [skip] statsmodels 없음")
        return

    formula = f"alpha_20d_z ~ bear + {exposure_z} + bear:{exposure_z}"
    try:
        model = smf.ols(formula, data=df).fit(
            cov_type="cluster",
            cov_kwds={"groups": df["signal_date"]},
        )
        coef = model.params
        tval = model.tvalues
        pval = model.pvalues

        print(f"\n[OLS-{label}]  {formula}")
        print(f"  n={len(df)}  R²={model.rsquared:.4f}")
        print(f"  {'변수':>30}  {'β':>8}  {'t':>7}  {'p':>7}")
        print("  " + "-" * 58)
        for name in coef.index:
            sig = " ★" if pval[name] < 0.05 else ""
            print(f"  {name:>30}  {coef[name]:>+8.4f}  {tval[name]:>+7.2f}  {pval[name]:>7.4f}{sig}")

        int_key = f"bear:{exposure_z}"
        if int_key in coef:
            beta = coef[int_key]
            p    = pval[int_key]
            flag = "★유의 (p<0.05)" if p < 0.05 else "미유의"
            print(f"\n  → interaction β={beta:+.4f}  p={p:.4f}  [{flag}]")

    except Exception as e:
        print(f"  OLS 오류: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # 사전등록 출력
    print("=" * 70)
    print("Stage 20A: Bear × Exposure Interaction")
    print("사전등록 (2026-10-01)")
    print("-" * 70)
    print("질문: Regime별 PBR 및 market-cap exposure와 20D alpha의 관계를 비교하고,")
    print("      Bear × Exposure interaction을 통해 Stage 18/19 Risk×Regime")
    print("      interaction과 Universe Exposure의 구조적 연결 여부를 검증한다.")
    print()
    print("레짐  : Stage 19 pre-registered (KOSPI 20D 보유기간, retrospective)")
    print("Tiering: Stage 20과 동일 (signal_date별 cross-sectional tertile)")
    print("해석  : interaction 유의 → 연결 가능성 / 미유의 → 독립적 구조 가능성")
    print("주의  : 교차분석 = 연관성. 설명력은 별도 nested-model 분석 필요.")
    print("=" * 70)

    print("\n데이터 로드 중...", end=" ", flush=True)
    df = load_data()
    print(f"완료  n={len(df)}")

    # 기본 통계
    n_bear  = (df["regime"] == "Bear").sum()
    n_bull  = (df["regime"] == "Bull").sum()
    nd_bear = df[df["regime"] == "Bear"]["signal_date"].nunique()
    nd_bull = df[df["regime"] == "Bull"]["signal_date"].nunique()

    print(f"\n샘플 요약")
    print(f"  Bear : {n_bear}행  ({nd_bear}개 날짜)  mean alpha = {df[df['regime']=='Bear']['alpha_20d'].mean():+.2f}%")
    print(f"  Bull : {n_bull}행  ({nd_bull}개 날짜)  mean alpha = {df[df['regime']=='Bull']['alpha_20d'].mean():+.2f}%")
    print(f"  전체 : {len(df)}행  mean alpha = {df['alpha_20d'].mean():+.2f}%")

    # [A] PBR tier
    cross_table(df, "pbr_tier", ["Low", "Mid", "High"], "A")

    # [B] Cap tier
    cross_table(df, "cap_tier", ["Small", "Mid", "Large"], "B")

    # [C] OLS PBR
    ols_analysis(df, "pbr_z", "C")

    # [D] OLS Cap
    ols_analysis(df, "log_cap_z", "D")

    # [E] STRONG_BUY 서브그룹
    df_sb   = df[df["grade"] == "strong_buy"].copy()
    n_sb    = len(df_sb)
    n_sb_br = (df_sb["regime"] == "Bear").sum()
    n_sb_bl = (df_sb["regime"] == "Bull").sum()

    print(f"\n\n[E] STRONG_BUY 서브그룹  n={n_sb}  Bear={n_sb_br}  Bull={n_sb_bl}")
    if n_sb >= 60:
        cross_table(df_sb, "pbr_tier", ["Low", "Mid", "High"], "E-PBR")
        cross_table(df_sb, "cap_tier", ["Small", "Mid", "Large"], "E-Cap")
    else:
        print("  n < 60 → 교차표 생략 (셀 당 n 부족)")

    print("\n\n완료.")


if __name__ == "__main__":
    main()
