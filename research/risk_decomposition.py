#!/usr/bin/env python3
"""
Risk Score Decomposition — Alpha Prediction by Sub-component
============================================================
각 Risk 서브컴포넌트가 독립적으로 알파를 얼마나 예측하는지 분석.

패널 구성:
  - Y년 Risk 데이터 (DART Q3 + pykrx 변동성)
  - Y+1년 수익률 (forward return)
  - 알파 = return(Y+1) - median_return(Y+1)  [연도별 시장 수익률 제거]

분석 대상 컴포넌트:
  1. debt_ratio    — 부채비율 (위험: 상위 50%)
  2. loss_proxy    — 9M 순이익 < 0 (binary)
  3. dilution      — 주식수 YoY 증가율 > 5%
  4. volatility    — 연환산 변동성 (위험: 상위 50%)

출력:
  - 컴포넌트별 고위험/저위험 그룹 평균 알파 + t-test
  - 분기(quintile) 분석
  - risk_history → docs/RESEARCH.md 추가용 요약
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT    = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "database" / "research.db"


# ── 데이터 로드 ───────────────────────────────────────────────────────────────

def load_panel() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)

    # Y년 Risk + Y+1년 수익률 join
    df = pd.read_sql_query("""
        SELECT
            rh.year        AS risk_year,
            rh.ticker,
            rh.debt_ratio,
            rh.ni_ytd,
            rv.vol_ann_pct AS volatility,
            rv.shares_sep30,
            rs.return_pct  AS fwd_return,
            rs.sector
        FROM risk_history rh
        JOIN risk_volatility rv
            ON rh.year = rv.year AND rh.ticker = rv.ticker
        JOIN research_stock_year rs
            ON rs.year = rh.year + 1 AND rs.ticker = rh.ticker
        WHERE rh.has_data = 1
          AND rv.has_vol  = 1
          AND rs.return_pct IS NOT NULL
    """, conn)

    # 희석률: shares_sep(Y) / shares_sep(Y-1) - 1
    shares_prev = pd.read_sql_query("""
        SELECT year, ticker, shares_sep30 AS shares_prev
        FROM risk_volatility WHERE has_shares = 1
    """, conn)
    conn.close()

    df = df.merge(
        shares_prev.rename(columns={"year": "prev_year"}),
        left_on=["risk_year", "ticker"],
        right_on=[shares_prev["year"].add(1).rename("prev_year"), "ticker"],
        how="left",
        suffixes=("", "_prev"),
    )
    # merge 후 prev_year 컬럼 정리
    if "prev_year_prev" in df.columns:
        df = df.drop(columns=["prev_year_prev"])
    if "prev_year" in df.columns:
        df = df.drop(columns=["prev_year"])

    # 희석률 계산
    df["dilution"] = np.where(
        (df["shares_prev"] > 0) & df["shares_sep30"].notna() & df["shares_prev"].notna(),
        (df["shares_sep30"] / df["shares_prev"] - 1) * 100,
        np.nan,
    )

    # 연도별 중앙값 차감 → 알파
    df["alpha"] = df["fwd_return"] - df.groupby(
        df["risk_year"] + 1
    )["fwd_return"].transform("median")

    return df


# ── 분석 유틸 ─────────────────────────────────────────────────────────────────

def group_stats(high: pd.Series, low: pd.Series, label: str) -> dict:
    """고위험(high) vs 저위험(low) 알파 비교."""
    h = high.dropna()
    l = low.dropna()
    if len(h) < 10 or len(l) < 10:
        return {}
    t, p = stats.ttest_ind(h, l)
    return {
        "component":  label,
        "n_high":     len(h),
        "n_low":      len(l),
        "alpha_high": round(h.mean(), 2),
        "alpha_low":  round(l.mean(), 2),
        "diff(low-high)": round(l.mean() - h.mean(), 2),
        "t_stat":     round(t, 2),
        "p_value":    round(p, 3),
        "sig":        "**" if p < 0.01 else ("*" if p < 0.05 else ""),
    }


def quintile_analysis(df: pd.DataFrame, col: str, label: str) -> pd.DataFrame:
    valid = df[[col, "alpha"]].dropna()
    if len(valid) < 50:
        return pd.DataFrame()
    valid["q"] = pd.qcut(valid[col], 5, labels=["Q1(저)", "Q2", "Q3", "Q4", "Q5(고)"], duplicates="drop")
    result = valid.groupby("q", observed=True)["alpha"].agg(["mean", "count"]).round(2)
    result.index.name = label
    result.columns = ["평균알파", "N"]
    return result


# ── 메인 분석 ─────────────────────────────────────────────────────────────────

def main() -> None:
    print("데이터 로드 중...")
    df = load_panel()
    print(f"패널: {len(df)}건 / {df['risk_year'].nunique()}개 연도 / {df['ticker'].nunique()}개 종목\n")

    results = []

    # ── 1. 부채비율 ──────────────────────────────────────────────────────────
    valid_debt = df[df["debt_ratio"].notna()]
    median_debt = valid_debt["debt_ratio"].median()
    high = valid_debt[valid_debt["debt_ratio"] >= median_debt]["alpha"]
    low  = valid_debt[valid_debt["debt_ratio"] <  median_debt]["alpha"]
    r = group_stats(high, low, f"부채비율 (중앙값={median_debt:.0f}% 기준)")
    if r: results.append(r)

    # ── 2. 손실 프록시 (9M NI < 0) ──────────────────────────────────────────
    valid_ni = df[df["ni_ytd"].notna()]
    high = valid_ni[valid_ni["ni_ytd"] <  0]["alpha"]
    low  = valid_ni[valid_ni["ni_ytd"] >= 0]["alpha"]
    r = group_stats(high, low, "손실 프록시 (9M NI < 0)")
    if r: results.append(r)

    # ── 3. 희석률 (> 5%) ────────────────────────────────────────────────────
    valid_dil = df[df["dilution"].notna()]
    high = valid_dil[valid_dil["dilution"] >  5]["alpha"]
    low  = valid_dil[valid_dil["dilution"] <= 5]["alpha"]
    r = group_stats(high, low, "희석률 > 5%")
    if r: results.append(r)

    # ── 4. 변동성 ────────────────────────────────────────────────────────────
    valid_vol = df[df["volatility"].notna()]
    median_vol = valid_vol["volatility"].median()
    high = valid_vol[valid_vol["volatility"] >= median_vol]["alpha"]
    low  = valid_vol[valid_vol["volatility"] <  median_vol]["alpha"]
    r = group_stats(high, low, f"변동성 (중앙값={median_vol:.1f}% 기준)")
    if r: results.append(r)

    # ── 결과 출력 ─────────────────────────────────────────────────────────────
    if not results:
        print("분석 결과 없음 (데이터 부족)")
        return

    res_df = pd.DataFrame(results)
    print("=" * 80)
    print("컴포넌트별 알파 예측력 (고위험 vs 저위험 그룹)")
    print("  diff(low-high) > 0 → 고위험 종목이 더 낮은 알파 = 예측력 있음")
    print("=" * 80)
    for _, row in res_df.iterrows():
        sig = row.get("sig", "")
        print(f"\n[{row['component']}] {sig}")
        print(f"  고위험: n={row['n_high']:4d}  평균알파={row['alpha_high']:+.2f}%")
        print(f"  저위험: n={row['n_low']:4d}  평균알파={row['alpha_low']:+.2f}%")
        print(f"  차이(저-고)={row['diff(low-high)']:+.2f}%  t={row['t_stat']:.2f}  p={row['p_value']:.3f} {sig}")

    # ── 분기 분석 ─────────────────────────────────────────────────────────────
    print("\n\n" + "=" * 80)
    print("분기(Quintile) 분석")
    print("=" * 80)

    for col, label in [
        ("debt_ratio", "부채비율"),
        ("volatility", "변동성"),
    ]:
        q_df = quintile_analysis(df, col, label)
        if not q_df.empty:
            print(f"\n{label} quintile → 알파:")
            print(q_df.to_string())

    # ── 상관관계 ─────────────────────────────────────────────────────────────
    print("\n\n" + "=" * 80)
    print("컴포넌트-알파 피어슨 상관계수")
    print("=" * 80)
    for col, label in [
        ("debt_ratio", "부채비율"),
        ("ni_ytd",     "9M 순이익(부호)"),
        ("dilution",   "희석률"),
        ("volatility", "변동성"),
    ]:
        valid = df[[col, "alpha"]].dropna()
        if len(valid) < 30:
            continue
        r_val, p_val = stats.pearsonr(valid[col], valid["alpha"])
        sig = "**" if p_val < 0.01 else ("*" if p_val < 0.05 else "  ")
        direction = "↑알파" if r_val > 0 else "↓알파"
        print(f"  {label:<20}: r={r_val:+.3f}  p={p_val:.3f} {sig}  ({direction})")

    # ── 연도별 트렌드 ─────────────────────────────────────────────────────────
    print("\n\n" + "=" * 80)
    print("연도별 패널 건수 및 평균 알파")
    print("=" * 80)
    yr = df.groupby(df["risk_year"] + 1).agg(
        N=("alpha", "count"),
        alpha_mean=("alpha", lambda x: round(x.mean(), 2)),
        fwd_return=("fwd_return", lambda x: round(x.mean(), 2)),
    )
    yr.index.name = "forward_year"
    print(yr.to_string())


if __name__ == "__main__":
    main()
