#!/usr/bin/env python3
"""
Earnings Gate Sensitivity — Architecture v2.1
===============================================
핵심 질문:
  Architecture v2에서 C(E Gate + R Filter + V ranker)가 A(현재 선형)보다 나쁜 것이
  "Earnings Gate 아이디어 자체의 실패"인가, "≥22 threshold의 과도한 exclusion"인가?

방법:
  E Gate threshold를 사전 고정된 3개 수준에서 비교.
  Threshold는 결과 보기 전 고정 — 결과 보고 추가/변경 금지.

  Candidate thresholds (사전 고정):
    ≥15 : 넓은 gate (허들 완화)
    ≥20 : 중간 gate
    ≥22 : 현재 기준 (v2 결과 재확인)

  각 threshold에서:
    C_T = E Gate(≥T) + R Filter(≥22), V=value_score ranker, top 25%
    D_T = E Gate(≥T) only,             V=value_score ranker, top 25%

  Baseline: A = 현재 선형 normalized_score top 25%

측정 지표 (사전 고정):
  n_pass, pass_rate(%), n/yr
  median, tmean(10%), P10, P25, hit%, dd
  vs A: median diff, paired t-test p-value, C>A 연도 비율

결과 해석 기준 (사전 고정):
  A. 모든 T에서 C_T < A   → Earnings Gate 아이디어 자체의 근거 약함
  B. 낮은 T에서 C_T > A   → ≥22가 과도한 exclusion이었던 것 (threshold 문제)
  C. 어떤 T에서도 차이 없음 → Earnings를 Gate로 분리하는 실익이 작음

R_FILTER_THR = 22 고정 (Risk Filter threshold는 별도 sensitivity에서 검토 예정)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT    = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "database" / "research.db"

# ── 사전 고정 파라미터 ──────────────────────────────────────────────────
E_THRESHOLDS  = [15, 20, 22]   # 결과 보기 전 고정. 이후 추가/변경 금지.
R_FILTER_THR  = 22
TOP_PCT       = 0.25


def load() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT fwd_year, ticker, fwd_alpha,
               normalized_score, value_score,
               earnings_score, risk_score
        FROM rrs_stock_year
        WHERE fwd_alpha IS NOT NULL
    """, conn)
    conn.close()
    return df


def select_top(df: pd.DataFrame, score_col: str,
               mask: pd.Series | None = None) -> pd.Series:
    sel = pd.Series(False, index=df.index)
    for _, grp in df.groupby("fwd_year"):
        pool = grp[mask.loc[grp.index]] if mask is not None else grp
        if len(pool) == 0:
            continue
        n_top = max(1, int(len(pool) * TOP_PCT))
        sel.loc[pool.nlargest(n_top, score_col).index] = True
    return sel


def robust_block(s: pd.Series) -> dict:
    s = s.dropna()
    n = len(s)
    if n < 5:
        return {}
    arr = np.sort(s.values)
    k = int(n * 0.10)
    trimmed = arr[k: n - k] if k > 0 else arr
    neg = s[s < 0]
    return {
        "n":      n,
        "mean":   round(float(s.mean()), 2),
        "median": round(float(np.median(s)), 2),
        "tmean":  round(float(trimmed.mean()), 2),
        "P10":    round(float(np.percentile(s, 10)), 2),
        "P25":    round(float(np.percentile(s, 25)), 2),
        "hit%":   round(float((s > 0).mean() * 100), 1),
        "dd":     round(float(neg.std()), 2) if len(neg) >= 5 else float("nan"),
    }


def yearly_median(df: pd.DataFrame, mask: pd.Series) -> pd.Series:
    rows = {}
    for yr, grp in df.groupby("fwd_year"):
        s = grp[mask.loc[grp.index]]["fwd_alpha"]
        if len(s) > 0:
            rows[yr] = round(float(s.median()), 2)
    return pd.Series(rows)


def paired_vs_a(ym_a: pd.Series, ym_b: pd.Series) -> dict:
    common = ym_a.index.intersection(ym_b.index)
    if len(common) < 4:
        return {"diff": float("nan"), "p": float("nan"), "pos": 0, "n": len(common)}
    diffs = (ym_b - ym_a).loc[common]
    t, p = stats.ttest_1samp(diffs, 0)
    return {
        "diff": round(float(diffs.mean()), 2),
        "p":    round(float(p), 3),
        "pos":  int((diffs > 0).sum()),
        "n":    len(common),
    }


def main() -> None:
    SEP = "=" * 96

    df = load()
    total = len(df)
    n_years = df["fwd_year"].nunique()
    print(f"패널: {total}건 / fwd_year {sorted(df['fwd_year'].unique())}")

    # Earnings score 분포 먼저 확인
    e_scores = df["earnings_score"]
    print(f"\nEarnings score 분포:")
    print(f"  min={e_scores.min():.0f}  p10={e_scores.quantile(0.10):.0f}  "
          f"p25={e_scores.quantile(0.25):.0f}  median={e_scores.median():.0f}  "
          f"p75={e_scores.quantile(0.75):.0f}  max={e_scores.max():.0f}")
    print(f"\nThreshold별 통과율:")
    r_mask = df["risk_score"] >= R_FILTER_THR
    for t in E_THRESHOLDS:
        e_mask = df["earnings_score"] >= t
        er_mask = e_mask & r_mask
        nyr = e_mask.sum() / n_years
        nyr_er = er_mask.sum() / n_years
        print(f"  E≥{t:>2}: {e_mask.sum():>5}건  ({e_mask.mean()*100:>5.1f}%)  "
              f"n/yr≈{nyr:>5.0f}  │  E≥{t}+R≥{R_FILTER_THR}: "
              f"{er_mask.sum():>5}건  ({er_mask.mean()*100:>5.1f}%)  n/yr≈{nyr_er:>5.0f}")

    # ── Baseline A ───────────────────────────────────────────────────────
    mask_a  = select_top(df, "normalized_score")
    ym_a    = yearly_median(df, mask_a)
    st_a    = robust_block(df[mask_a]["fwd_alpha"])
    n_yr_a  = df[mask_a].groupby("fwd_year").size().mean()

    # ── 각 threshold별 C_T / D_T ────────────────────────────────────────
    results = {}
    for t in E_THRESHOLDS:
        e_mask  = df["earnings_score"] >= t
        er_mask = e_mask & r_mask

        mask_c = select_top(df, "value_score", er_mask)   # C_T: E+R gate
        mask_d = select_top(df, "value_score", e_mask)    # D_T: E gate only

        results[t] = {
            "C": {
                "mask": mask_c,
                "ym":   yearly_median(df, mask_c),
                "st":   robust_block(df[mask_c]["fwd_alpha"]),
                "n_yr": df[mask_c].groupby("fwd_year").size().mean(),
            },
            "D": {
                "mask": mask_d,
                "ym":   yearly_median(df, mask_d),
                "st":   robust_block(df[mask_d]["fwd_alpha"]),
                "n_yr": df[mask_d].groupby("fwd_year").size().mean(),
            },
        }

    # ── 전체 통계 테이블 ─────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("전기간 합산 — 전체 통계")
    print("PRIMARY: median / tmean    보조: mean")
    print(SEP)
    print(f"  {'Architecture':<28}  {'n/yr':>5}  {'mean':>7}  {'median':>7}  "
          f"{'tmean':>7}  {'P10':>7}  {'P25':>7}  {'hit%':>6}  {'dd':>6}")
    print("  " + "-" * 86)

    def print_row(lbl: str, st: dict, n_yr: float) -> None:
        if not st:
            print(f"  {lbl:<28}  표본부족")
            return
        print(f"  {lbl:<28}  {n_yr:>5.0f}  "
              f"{st['mean']:>+7.2f}  {st['median']:>+7.2f}  "
              f"{st['tmean']:>+7.2f}  {st['P10']:>+7.2f}  "
              f"{st['P25']:>+7.2f}  {st['hit%']:>5.1f}%  {st['dd']:>6.2f}")

    print_row("A. 현재 선형 (baseline)", st_a, n_yr_a)
    print("  " + "·" * 86)
    for t in E_THRESHOLDS:
        print_row(f"C_{t}: E≥{t}+R≥{R_FILTER_THR}, V ranker",
                  results[t]["C"]["st"], results[t]["C"]["n_yr"])
    print("  " + "·" * 86)
    for t in E_THRESHOLDS:
        print_row(f"D_{t}: E≥{t} only, V ranker",
                  results[t]["D"]["st"], results[t]["D"]["n_yr"])

    # ── vs A paired t-test ───────────────────────────────────────────────
    print(f"\n{SEP}")
    print(f"C_T vs A — 연도별 median 차이 t-test  (R Filter 고정: ≥{R_FILTER_THR})")
    print(SEP)
    print(f"  {'Architecture':<28}  {'diff':>6}  {'p':>7}  {'C>A':>6}  판정")
    print("  " + "-" * 60)
    for t in E_THRESHOLDS:
        r = paired_vs_a(ym_a, results[t]["C"]["ym"])
        sig = "**" if r["p"] < 0.01 else ("*" if r["p"] < 0.05 else
              ("†" if r["p"] < 0.10 else "  "))
        verdict = ("C>A" if r["diff"] > 0 else "C<A")
        print(f"  C_{t}: E≥{t}+R≥{R_FILTER_THR}, V ranker  "
              f"{r['diff']:>+6.2f}  p={r['p']:.3f}{sig}  "
              f"{r['pos']:>2}/{r['n']}년  {verdict}")

    print(f"\nD_T vs A — 연도별 median 차이 t-test  (E Gate only)")
    print("  " + "-" * 60)
    for t in E_THRESHOLDS:
        r = paired_vs_a(ym_a, results[t]["D"]["ym"])
        sig = "**" if r["p"] < 0.01 else ("*" if r["p"] < 0.05 else
              ("†" if r["p"] < 0.10 else "  "))
        verdict = "D>A" if r["diff"] > 0 else "D<A"
        print(f"  D_{t}: E≥{t} only, V ranker          "
              f"{r['diff']:>+6.2f}  p={r['p']:.3f}{sig}  "
              f"{r['pos']:>2}/{r['n']}년  {verdict}")

    # ── 연도별 median — C_T 시리즈 ──────────────────────────────────────
    print(f"\n{SEP}")
    print("연도별 median alpha — C_T vs A")
    print(SEP)
    cols = ["A"] + [f"C_{t}" for t in E_THRESHOLDS]
    print(f"  {'Year':>6}  " + "  ".join(f"{c:>10}" for c in cols))
    print("  " + "-" * 52)
    for yr in sorted(df["fwd_year"].unique()):
        row = f"  {yr:>6}  {ym_a.get(yr, float('nan')):>+10.2f}"
        for t in E_THRESHOLDS:
            v = results[t]["C"]["ym"].get(yr, float("nan"))
            row += f"  {v:>+10.2f}"
        print(row)

    # ── 핵심 판정 ────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("판정")
    print(SEP)

    all_worse = all(results[t]["C"]["st"].get("median", 0) <
                    st_a.get("median", 0) for t in E_THRESHOLDS)
    lower_better = any(results[t]["C"]["st"].get("median", -999) >
                       st_a.get("median", 0) for t in [E_THRESHOLDS[0]])

    if all_worse:
        conclusion = "A. 모든 threshold에서 C_T ≤ A → Earnings Gate 아이디어 자체의 근거 약함"
    elif lower_better:
        conclusion = "B. 낮은 threshold에서 C_T > A → ≥22가 과도한 exclusion이었던 것"
    else:
        conclusion = "C. Threshold에 관계없이 차이 미미 → Earnings Gate 분리의 실익이 작음"

    print(f"\n  → {conclusion}")
    print(f"""
  사전 고정 해석 기준:
    A. 모든 T에서 C_T ≤ A  → Earnings Gate 아이디어 자체의 근거 약함
    B. 낮은 T에서 C_T > A  → ≥22가 과도한 exclusion (threshold 문제)
    C. 어떤 T에서도 차이 없음 → Earnings Gate 분리의 실익이 작음

  다음 연구 예정: Risk Filter Sensitivity (R threshold 별도 검토)
  주의: in-sample RRS panel. Engine Freeze 해제 전 production 변경 금지.
""")


if __name__ == "__main__":
    main()
