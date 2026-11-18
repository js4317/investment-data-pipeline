#!/usr/bin/env python3
"""
Risk Score Decomposition v2 — KOSPI-Alpha + Downside Analysis
=============================================================
Step 1: KOSPI benchmark 교정
  alpha = 개별 수익률 - 동일 연도 KOSPI 수익률
  (이전: 패널 중앙값 대비 → KOSPI 절대 기준으로 변경)

Step 2: Risk sub-component × downside
  각 컴포넌트(부채비율/적자/희석/변동성)에 대해:
  평균 alpha / P10 / P25 / worst / downside_dev / hit_rate 비교

Step 3: Aggregate Risk Score × downside
  proxy risk score = 0~4 (각 컴포넌트 flag 합산)
  → 점수별 tail risk 실제로 줄었는가?

한계: 상장폐지 종목 미포함 (survivorship bias). 결과 해석 시 명시적 고려 필요.
"""

from __future__ import annotations

import sys
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT    = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "database" / "research.db"

sys.path.insert(0, str(ROOT))

# ── KOSPI 연도별 수익률 ─────────────────────────────────────────────────────────

def fetch_kospi_returns(years: list[int]) -> dict[int, float]:
    """pykrx로 KOSPI 연도별 수익률 계산. 실패 시 None."""
    try:
        from pykrx import stock as krx
        import time
        KOSPI_IDX = "1001"
        result: dict[int, float] = {}
        for y in years:
            try:
                s = krx.get_index_ohlcv_by_date(f"{y}0102", f"{y}0131", KOSPI_IDX)
                e = krx.get_index_ohlcv_by_date(f"{y}1201", f"{y}1231", KOSPI_IDX)
                time.sleep(0.5)
                if s is not None and not s.empty and e is not None and not e.empty:
                    p_s = float(s["종가"].iloc[0])
                    p_e = float(e["종가"].iloc[-1])
                    result[y] = round((p_e / p_s - 1) * 100, 2)
            except Exception:
                pass
        return result
    except ImportError:
        return {}


# ── 패널 로드 ──────────────────────────────────────────────────────────────────

def load_panel() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT
            rh.year          AS risk_year,
            rh.ticker,
            rh.debt_ratio,
            rh.ni_ytd,
            rv.vol_ann_pct   AS volatility,
            rv.shares_sep30,
            rs.return_pct    AS fwd_return,
            rs.sector,
            rs.year          AS fwd_year
        FROM risk_history rh
        JOIN risk_volatility rv
            ON rh.year = rv.year AND rh.ticker = rv.ticker
        JOIN research_stock_year rs
            ON rs.year = rh.year + 1 AND rs.ticker = rh.ticker
        WHERE rh.has_data = 1
          AND rv.has_vol  = 1
          AND rs.return_pct IS NOT NULL
    """, conn)

    # 희석률 (shares_sep(Y) / shares_sep(Y-1) - 1)
    sp = pd.read_sql_query(
        "SELECT year, ticker, shares_sep30 FROM risk_volatility WHERE has_shares=1", conn
    )
    conn.close()

    sp_prev = sp.copy()
    sp_prev["fwd_year"] = sp_prev["year"] + 1
    sp_prev = sp_prev.rename(columns={"shares_sep30": "shares_prev"}).drop(columns="year")

    df = df.merge(sp_prev, on=["fwd_year", "ticker"], how="left")
    df["dilution"] = np.where(
        (df["shares_prev"] > 0) & df["shares_sep30"].notna() & df["shares_prev"].notna(),
        (df["shares_sep30"] / df["shares_prev"] - 1) * 100,
        np.nan,
    )
    return df


# ── 컴포넌트 플래그 ────────────────────────────────────────────────────────────

def add_flags(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["flag_debt"]     = (df["debt_ratio"].notna()  & (df["debt_ratio"]  > 150)).astype(float)
    df["flag_loss"]     = (df["ni_ytd"].notna()       & (df["ni_ytd"]      < 0  )).astype(float)
    df["flag_dilution"] = (df["dilution"].notna()     & (df["dilution"]    > 5  )).astype(float)
    df["flag_vol"]      = (df["volatility"].notna()   & (df["volatility"]  > 40 )).astype(float)

    # proxy risk score (데이터 있는 플래그만 합산)
    flag_cols = ["flag_debt", "flag_loss", "flag_dilution", "flag_vol"]
    df["risk_score"] = df[flag_cols].sum(axis=1, min_count=1)
    return df


# ── downside 통계 ──────────────────────────────────────────────────────────────

def downside_stats(series: pd.Series, label: str) -> dict:
    s = series.dropna()
    if len(s) < 10:
        return {}
    neg = s[s < 0]
    return {
        "group":         label,
        "n":             len(s),
        "mean_alpha":    round(s.mean(), 2),
        "hit_rate%":     round((s > 0).mean() * 100, 1),
        "P10":           round(np.percentile(s, 10), 2),
        "P25":           round(np.percentile(s, 25), 2),
        "worst":         round(s.min(), 2),
        "downside_dev":  round(neg.std() if len(neg) > 2 else 0.0, 2),
    }


def compare_groups(df: pd.DataFrame, mask_high: pd.Series,
                   col: str, label_high: str, label_low: str) -> None:
    alpha = df[col]
    high  = alpha[mask_high]
    low   = alpha[~mask_high & df["debt_ratio"].notna() | True]  # all non-high
    low   = alpha[~mask_high]

    h = downside_stats(high, label_high)
    l = downside_stats(low,  label_low)
    if not h or not l:
        return

    t, p = stats.ttest_ind(high.dropna(), low.dropna())
    sig = "**" if p < 0.01 else ("*" if p < 0.05 else "  ")

    header = f"{'지표':<18} {'고위험':>10} {'저위험':>10} {'차이(저-고)':>12}"
    print(header)
    print("-" * 52)
    for k in ["n", "mean_alpha", "hit_rate%", "P10", "P25", "worst", "downside_dev"]:
        hv = h.get(k, "—")
        lv = l.get(k, "—")
        diff = ""
        if isinstance(hv, (int, float)) and isinstance(lv, (int, float)) and k != "n":
            diff = f"{lv - hv:+.2f}"
        print(f"  {k:<16} {str(hv):>10} {str(lv):>10} {diff:>12}")
    print(f"  t-stat p-value {'':>10} {'':>10} t={t:.2f} p={p:.3f} {sig}")


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("패널 로드...")
    df = load_panel()
    fwd_years = sorted(df["fwd_year"].unique())

    # ── Step 1: KOSPI alpha ────────────────────────────────────────────────────
    print(f"\nKOSPI 수익률 조회 중 ({fwd_years[0]}~{fwd_years[-1]})...")
    kospi = fetch_kospi_returns(fwd_years)

    if kospi:
        df["kospi_ret"] = df["fwd_year"].map(kospi)
        df["alpha"]     = df["fwd_return"] - df["kospi_ret"]
        bench_label     = "KOSPI 기준"
        print("  KOSPI 수익률:")
        for y in sorted(kospi):
            print(f"    {y}: {kospi[y]:+.1f}%")
    else:
        print("  [경고] pykrx 미사용 환경. 패널 중앙값 fallback.")
        df["alpha"] = df["fwd_return"] - df.groupby("fwd_year")["fwd_return"].transform("median")
        bench_label = "패널 중앙값 기준 (fallback)"

    df = add_flags(df)
    print(f"\n패널: {len(df)}건 / {df['risk_year'].nunique()}개 연도 / {df['ticker'].nunique()}개 종목")
    print(f"benchmark: {bench_label}")
    print("[주의] 상장폐지 종목 미포함 — survivorship bias 존재")

    # ── 연도별 요약 ───────────────────────────────────────────────────────────
    print("\n\n연도별 KOSPI alpha 요약:")
    yr = df.groupby("fwd_year").agg(
        N=("alpha", "count"),
        mean_alpha=("alpha", lambda x: round(x.mean(), 2)),
        kospi=("kospi_ret", "first") if kospi else ("alpha", lambda x: "—"),
    )
    print(yr.to_string())

    # ── Step 2: 컴포넌트별 downside ───────────────────────────────────────────
    components = [
        ("flag_debt",     "부채비율 > 150%",  "debt_ratio",  "부채비율 ≤ 150%"),
        ("flag_loss",     "적자 (NI < 0)",    "ni_ytd",      "흑자 (NI ≥ 0)"),
        ("flag_dilution", "희석 > 5%",        "dilution",    "희석 ≤ 5%"),
        ("flag_vol",      "변동성 > 40%",     "volatility",  "변동성 ≤ 40%"),
    ]

    SEP = "=" * 60

    print(f"\n\n{SEP}")
    print("Step 2 — 컴포넌트별 고위험 vs 저위험 downside 비교")
    print(SEP)

    for flag_col, label_high, data_col, label_low in components:
        valid_mask = df[data_col].notna()
        mask_high  = valid_mask & (df[flag_col] == 1)
        mask_low   = valid_mask & (df[flag_col] == 0)

        print(f"\n[ {label_high} vs {label_low} ]  (유효 데이터: {valid_mask.sum()}건)")
        compare_groups(df, mask_high, "alpha", label_high, label_low)

    # ── Step 3: Aggregate Risk Score ──────────────────────────────────────────
    print(f"\n\n{SEP}")
    print("Step 3 — Aggregate Risk Score × downside")
    print("proxy risk_score = flag_debt + flag_loss + flag_dilution + flag_vol (0~4)")
    print(SEP)

    for score, label in [(0, "Risk 0 (위험 없음)"), (1, "Risk 1"), (2, "Risk 2"), (3, "Risk 3+")]:
        if score == 3:
            mask = df["risk_score"] >= 3
        else:
            mask = df["risk_score"] == score
        s = df.loc[mask, "alpha"]
        stat = downside_stats(s, label)
        if not stat:
            continue
        print(f"\n  {label}  (n={stat['n']})")
        print(f"    mean_alpha={stat['mean_alpha']:+.2f}%  hit_rate={stat['hit_rate%']}%")
        print(f"    P10={stat['P10']:+.2f}%  P25={stat['P25']:+.2f}%  "
              f"worst={stat['worst']:+.2f}%  down_dev={stat['downside_dev']:.2f}%")

    # Risk Score별 추세 (mean alpha + P10)
    print("\n  Risk Score 요약 테이블:")
    rows = []
    for score in [0, 1, 2, 3]:
        mask = df["risk_score"] >= score if score == 3 else df["risk_score"] == score
        s = df.loc[mask, "alpha"].dropna()
        if len(s) < 5:
            continue
        rows.append({
            "risk_score": f"{score}+" if score == 3 else str(score),
            "n": len(s),
            "mean_alpha": round(s.mean(), 2),
            "P10": round(np.percentile(s, 10), 2),
            "P25": round(np.percentile(s, 25), 2),
            "hit_rate%": round((s > 0).mean() * 100, 1),
            "worst": round(s.min(), 2),
        })
    if rows:
        print(pd.DataFrame(rows).set_index("risk_score").to_string())

    print(f"\n\n{'='*60}")
    print("한계: 상장폐지/거래정지 종목 미포함 → 고위험 결과는 실제보다 낙관적일 수 있음")


if __name__ == "__main__":
    main()
