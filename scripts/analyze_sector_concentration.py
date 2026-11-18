"""
Stage 20F: Sector Concentration / Portfolio Construction Diagnostic

사전등록 (2026-10-01):
  "STRONG_BUY universe의 sector concentration 및 sector-relative performance를
   분해하고, 동일한 종목 selection을 유지한 sector-neutralized counterfactual을
   통해 observed alpha의 sector concentration 기여도를 평가한다."

분석 구조:
  [1] Sector Concentration
      signal_date별 STRONG_BUY sector 비중 vs universe 비중 → overweight
  [2] Sector Contribution
      sector weight × sector alpha 분해 → 전체 alpha attribution
  [3] Sector-Neutralized Counterfactual
      종목 selection 그대로, sector 동일 비중 → actual vs neutralized 비교

건드리지 않는 것: score, grade, selection rule. composition만 분석.

실행:
    cd ~/Desktop/investment
    python3 scripts/analyze_sector_concentration.py
"""

from __future__ import annotations

import sqlite3
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT    = Path(__file__).parent.parent
DB_PATH = ROOT / "database" / "research.db"


# ---------------------------------------------------------------------------
# 데이터 로드
# ---------------------------------------------------------------------------

def load_data() -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as c:
        df = pd.read_sql_query(
            """
            SELECT
                r.signal_date, r.ticker, r.grade, r.alpha_20d,
                u.sector
            FROM retro_calibration r
            JOIN universe_exposure u
              ON r.signal_date = u.signal_date AND r.ticker = u.ticker
            WHERE r.alpha_20d IS NOT NULL
            """,
            c,
        )
    # sector None → "미분류"
    df["sector"] = df["sector"].fillna("미분류")
    return df


# ---------------------------------------------------------------------------
# [1] Sector Concentration
# ---------------------------------------------------------------------------

def analyze_concentration(df: pd.DataFrame) -> pd.DataFrame:
    """
    전체 기간 집계:
      STRONG_BUY 내 sector 비중 vs universe 비중 → overweight
    """
    sb  = df[df["grade"] == "strong_buy"]
    uni = df  # 전체 universe

    sb_cnt  = sb["sector"].value_counts()
    uni_cnt = uni["sector"].value_counts()

    sb_pct  = sb_cnt  / sb_cnt.sum()  * 100
    uni_pct = uni_cnt / uni_cnt.sum() * 100

    result = pd.DataFrame({
        "SB_n":       sb_cnt,
        "SB_pct":     sb_pct,
        "Uni_n":      uni_cnt,
        "Uni_pct":    uni_pct,
    }).fillna(0)
    result["overweight"] = result["SB_pct"] - result["Uni_pct"]
    result = result.sort_values("overweight", ascending=False)
    return result


# ---------------------------------------------------------------------------
# [2] Sector Contribution
# ---------------------------------------------------------------------------

def analyze_contribution(df: pd.DataFrame) -> pd.DataFrame:
    """
    signal_date별로:
      sector_weight = sector 내 SB 종목 수 / 전체 SB 종목 수
      sector_alpha  = sector 내 SB 종목 alpha 평균
      contribution  = sector_weight × sector_alpha

    전체 기간 평균 contribution 합산.
    """
    sb = df[df["grade"] == "strong_buy"].copy()

    records = []
    for date, grp in sb.groupby("signal_date"):
        total_n = len(grp)
        for sector, sg in grp.groupby("sector"):
            weight = len(sg) / total_n
            alpha  = sg["alpha_20d"].mean()
            records.append({
                "signal_date":  date,
                "sector":       sector,
                "weight":       weight,
                "sector_alpha": alpha,
                "contribution": weight * alpha,
            })

    contrib_df = pd.DataFrame(records)

    # 전체 기간 평균
    summary = contrib_df.groupby("sector").agg(
        avg_weight    =("weight",       "mean"),
        avg_alpha     =("sector_alpha", "mean"),
        avg_contrib   =("contribution", "mean"),
        n_dates       =("signal_date",  "nunique"),
    ).sort_values("avg_contrib")

    return summary, contrib_df


# ---------------------------------------------------------------------------
# [3] Sector-Neutralized Counterfactual
# ---------------------------------------------------------------------------

def analyze_counterfactual(df: pd.DataFrame) -> dict:
    """
    각 signal_date에서:
      Actual:     SB 종목 단순 평균 alpha (= 현재 방식)
      Neutralized: sector별 평균 alpha를 구한 뒤 sector 동일 비중으로 합산

    전체 기간 평균 비교.
    """
    sb = df[df["grade"] == "strong_buy"].copy()

    actual_alphas      = []
    neutralized_alphas = []
    dates              = []

    for date, grp in sb.groupby("signal_date"):
        # Actual: 종목 단순 평균
        actual = grp["alpha_20d"].mean()

        # Neutralized: sector 동일 비중
        sector_means = grp.groupby("sector")["alpha_20d"].mean()
        neutralized  = sector_means.mean()

        actual_alphas.append(actual)
        neutralized_alphas.append(neutralized)
        dates.append(date)

    result_df = pd.DataFrame({
        "signal_date":  dates,
        "actual":       actual_alphas,
        "neutralized":  neutralized_alphas,
    })
    result_df["diff"] = result_df["neutralized"] - result_df["actual"]

    summary = {
        "actual_mean":      np.mean(actual_alphas),
        "neutralized_mean": np.mean(neutralized_alphas),
        "actual_std":       np.std(actual_alphas),
        "neutralized_std":  np.std(neutralized_alphas),
        "diff_mean":        np.mean([n - a for n, a in zip(neutralized_alphas, actual_alphas)]),
        "actual_win_rate":  np.mean([a > 0 for a in actual_alphas]),
        "neutralized_win_rate": np.mean([n > 0 for n in neutralized_alphas]),
        "n_dates":          len(dates),
        "df":               result_df,
    }
    return summary


# ---------------------------------------------------------------------------
# Bear regime별 counterfactual
# ---------------------------------------------------------------------------

def load_kospi_regime() -> dict[str, str]:
    from pathlib import Path
    import sqlite3
    ohlcv_db = ROOT / "database" / "market_ohlcv.db"
    with sqlite3.connect(ohlcv_db) as c:
        rows = c.execute(
            "SELECT trade_date, open, close FROM market_ohlcv WHERE ticker='1001' ORDER BY trade_date"
        ).fetchall()
    dates  = [r[0] for r in rows]
    opens  = {r[0]: r[1] for r in rows}
    closes = {r[0]: r[2] for r in rows}
    regime_map = {}
    for i, d in enumerate(dates):
        if i + 20 >= len(dates):
            continue
        eo, ec = opens.get(d), closes.get(dates[i + 20])
        if eo and ec and eo != 0:
            regime_map[d] = "Bull" if ec / eo - 1 > 0 else "Bear"
    return regime_map


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 72)
    print("Stage 20F: Sector Concentration / Portfolio Construction Diagnostic")
    print("사전등록 (2026-10-01)")
    print("=" * 72)

    print("\n데이터 로드 중...", end=" ", flush=True)
    df = load_data()
    n_sb  = (df["grade"] == "strong_buy").sum()
    n_all = len(df)
    n_dates = df["signal_date"].nunique()
    print(f"완료  전체={n_all}  STRONG_BUY={n_sb}  dates={n_dates}")

    # ── [1] Sector Concentration ─────────────────────────────────────────────
    print("\n\n" + "=" * 72)
    print("[1] Sector Concentration (전체 기간)")
    print("    STRONG_BUY 비중 vs Universe 비중 → Overweight")
    print("=" * 72)
    conc = analyze_concentration(df)
    print(f"\n  {'섹터':15s}  {'SB_n':>6}  {'SB%':>6}  {'Uni%':>6}  {'OW':>7}")
    print("  " + "-" * 48)
    for sector, row in conc.iterrows():
        ow = row["overweight"]
        ow_s = f"{ow:>+7.1f}%"
        flag = " ▲" if ow > 5 else (" ▼" if ow < -3 else "  ")
        print(f"  {str(sector):15s}  {row['SB_n']:>6.0f}  {row['SB_pct']:>5.1f}%  "
              f"{row['Uni_pct']:>5.1f}%  {ow_s}{flag}")

    # ── [2] Sector Contribution ───────────────────────────────────────────────
    print("\n\n" + "=" * 72)
    print("[2] Sector Alpha Contribution (STRONG_BUY)")
    print("    avg_weight × avg_alpha = avg_contribution (전체 기간 평균)")
    print("=" * 72)
    summary_contrib, contrib_df = analyze_contribution(df)
    total_contrib = summary_contrib["avg_contrib"].sum()

    print(f"\n  {'섹터':15s}  {'비중':>6}  {'alpha':>7}  {'기여도':>8}  {'날수':>5}")
    print("  " + "-" * 52)
    for sector, row in summary_contrib.sort_values("avg_contrib").iterrows():
        flag = " ◀" if abs(row["avg_contrib"]) > 0.3 else ""
        print(f"  {str(sector):15s}  {row['avg_weight']*100:>5.1f}%  "
              f"{row['avg_alpha']:>+7.2f}%  {row['avg_contrib']:>+8.3f}%{flag}  "
              f"{row['n_dates']:>5.0f}")
    print(f"\n  {'합계':15s}  {'':>6}  {'':>7}  {total_contrib:>+8.3f}%")

    # 상위 손실 기여 섹터
    worst = summary_contrib.nsmallest(3, "avg_contrib")
    print(f"\n  손실 기여 Top 3 섹터: {', '.join(worst.index.tolist())}")
    worst_share = worst["avg_contrib"].sum() / total_contrib * 100 if total_contrib != 0 else 0
    print(f"  → 전체 alpha 기여도의 {worst_share:.0f}% 차지")

    # ── [3] Sector-Neutralized Counterfactual ────────────────────────────────
    print("\n\n" + "=" * 72)
    print("[3] Sector-Neutralized Counterfactual")
    print("    종목 selection 그대로, sector 동일 비중으로 재계산")
    print("=" * 72)
    cf = analyze_counterfactual(df)
    print(f"\n  전체 기간 ({cf['n_dates']}개 날짜):")
    print(f"  {'':30s}  {'평균 alpha':>10}  {'std':>8}  {'승률':>7}")
    print("  " + "-" * 60)
    print(f"  {'Actual (현재 방식)':30s}  {cf['actual_mean']:>+10.3f}%  "
          f"{cf['actual_std']:>8.3f}%  {cf['actual_win_rate']*100:>6.1f}%")
    print(f"  {'Neutralized (sector 동일비중)':30s}  {cf['neutralized_mean']:>+10.3f}%  "
          f"{cf['neutralized_std']:>8.3f}%  {cf['neutralized_win_rate']*100:>6.1f}%")
    diff = cf["diff_mean"]
    print(f"\n  Δ(Neutralized − Actual) = {diff:>+.3f}%")
    if diff > 0.5:
        print("  → Neutralized가 유의미하게 높음: sector concentration이 성과를 깎고 있음")
    elif diff < -0.5:
        print("  → Actual이 더 높음: sector concentration이 오히려 성과에 기여함")
    else:
        print("  → 차이 미미: sector concentration 영향이 크지 않음")

    # Bear / Bull 구분
    print("\n  --- Bear / Bull 구분 ---")
    regime_map = load_kospi_regime()
    cf_df = cf["df"].copy()
    cf_df["regime"] = cf_df["signal_date"].map(regime_map).fillna("Unclassified")

    for regime in ["Bear", "Bull"]:
        sub = cf_df[cf_df["regime"] == regime]
        if len(sub) == 0:
            continue
        a_mean = sub["actual"].mean()
        n_mean = sub["neutralized"].mean()
        d_mean = sub["diff"].mean()
        print(f"\n  [{regime}]  n={len(sub)}dates")
        print(f"    Actual:      {a_mean:>+.3f}%")
        print(f"    Neutralized: {n_mean:>+.3f}%")
        print(f"    Δ:           {d_mean:>+.3f}%")

    # ── 종합 요약 ─────────────────────────────────────────────────────────────
    print("\n\n" + "=" * 72)
    print("[종합] 판정")
    print("=" * 72)
    print(f"\n  1. 가장 overweight된 섹터: {conc.index[0]} "
          f"(+{conc.iloc[0]['overweight']:.1f}%p)")
    print(f"  2. alpha 손실 기여 Top 섹터: {', '.join(worst.index.tolist())}")
    print(f"  3. Neutralized − Actual = {diff:>+.3f}%")
    if diff > 0.5:
        print("\n  → Portfolio construction(sector concentration)이 성과의 병목임을 시사.")
        print("    20G: sector / position constraint 설계 필요성 검토.")
    elif abs(diff) <= 0.5:
        print("\n  → Sector concentration의 영향은 제한적. 근본 문제는 selection 자체일 가능성.")
    else:
        print("\n  → Concentration이 오히려 도움. 현재 구조 유지.")

    print("\n완료.")


if __name__ == "__main__":
    main()
