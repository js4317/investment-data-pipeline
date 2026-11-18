#!/usr/bin/env python3
"""
Risk Filter Sensitivity — Architecture v2.2
=============================================
핵심 질문:
  R filter 역할 가설이 threshold 선택에 얼마나 민감한가?
  "R≥22가 미세하게 좋았던 것"이 threshold 특이성인가, R filter 역할 자체의 효과인가?

설계 원칙:
  1. Risk score 분포를 먼저 확인 (범주형인지 연속형인지)
  2. R threshold 후보는 결과 보기 전 사전 고정:
       ≥17 (낮은 gate), ≥22 (현재 기준), ≥25 (엄격한 gate, max score)
  3. E threshold는 E sensitivity 연구에서 방향 지지된 ≥20 (흑자 여부)으로 고정
  4. 비교 구조 (threshold별):
       E_R_T = E≥20 Gate + R≥T Filter, V ranker   (E+R gate)
       R_T   = R≥T Filter only,         V ranker   (R only, E gate 없음)
  5. Baseline A = 현재 선형 (고정)

  혼동 방지:
    Risk decomposition 결과 ("Risk score 높을수록 downside 나쁨") ≠ 이 실험
    여기서는 "R Filter로 후보를 걸러낼 때 architecture 성과가 좋아지는가"만 본다.

사전 고정 해석 기준:
  A. 모든 T에서 E_R_T ≈ A       → R filter 분리 실익 없음
  B. 특정 T에서만 E_R_T > A     → R filter 효과가 threshold-specific (취약)
  C. 모든/대부분 T에서 E_R_T > A → R filter 자체의 구조적 역할 지지
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
R_THRESHOLDS = [17, 22, 25]   # 결과 보기 전 고정
E_GATE_THR   = 20             # 흑자 여부 (E sensitivity 연구 결과 고정)
TOP_PCT      = 0.25


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
    _, p = stats.ttest_1samp(diffs, 0)
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

    # ── Risk score 분포 ──────────────────────────────────────────────────
    r_scores = df["risk_score"]
    print(f"\nRisk score 분포:")
    print(f"  min={r_scores.min():.0f}  p10={r_scores.quantile(0.10):.0f}  "
          f"p25={r_scores.quantile(0.25):.0f}  median={r_scores.median():.0f}  "
          f"p75={r_scores.quantile(0.75):.0f}  max={r_scores.max():.0f}  "
          f"std={r_scores.std():.1f}")

    # 값별 빈도 (상위 10개)
    val_counts = r_scores.value_counts().sort_index(ascending=False).head(10)
    print(f"\n  Risk score 값별 빈도 (상위 10):")
    for v, c in val_counts.items():
        pct = c / total * 100
        bar = "█" * int(pct / 2)
        print(f"  {v:>4}: {c:>5}건  ({pct:>5.1f}%)  {bar}")

    # threshold별 통과율
    e_mask = df["earnings_score"] >= E_GATE_THR
    print(f"\nThreshold별 통과율 (E gate 고정: ≥{E_GATE_THR}):")
    for t in R_THRESHOLDS:
        r_mask  = df["risk_score"] >= t
        er_mask = e_mask & r_mask
        n_r   = r_mask.sum()
        n_er  = er_mask.sum()
        print(f"  R≥{t:>2}: {n_r:>5}건  ({n_r/total*100:>5.1f}%)  n/yr≈{n_r/n_years:>5.0f}  │  "
              f"E≥{E_GATE_THR}+R≥{t}: {n_er:>5}건  ({n_er/total*100:>5.1f}%)  n/yr≈{n_er/n_years:>5.0f}")

    # ── Baseline A ───────────────────────────────────────────────────────
    mask_a = select_top(df, "normalized_score")
    ym_a   = yearly_median(df, mask_a)
    st_a   = robust_block(df[mask_a]["fwd_alpha"])
    n_yr_a = df[mask_a].groupby("fwd_year").size().mean()

    # ── 각 threshold별 아키텍처 ──────────────────────────────────────────
    archs = {}
    for t in R_THRESHOLDS:
        r_mask  = df["risk_score"] >= t
        er_mask = e_mask & r_mask

        mask_er = select_top(df, "value_score", er_mask)   # E+R gate
        mask_r  = select_top(df, "value_score", r_mask)    # R only

        archs[t] = {
            "ER": {
                "mask": mask_er,
                "ym":   yearly_median(df, mask_er),
                "st":   robust_block(df[mask_er]["fwd_alpha"]),
                "n_yr": df[mask_er].groupby("fwd_year").size().mean(),
            },
            "R": {
                "mask": mask_r,
                "ym":   yearly_median(df, mask_r),
                "st":   robust_block(df[mask_r]["fwd_alpha"]),
                "n_yr": df[mask_r].groupby("fwd_year").size().mean(),
            },
        }

    # ── 전체 통계 테이블 ─────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("전기간 합산 — 전체 통계")
    print("PRIMARY: median / tmean    보조: mean")
    print(SEP)
    print(f"  {'Architecture':<32}  {'n/yr':>5}  {'mean':>7}  {'median':>7}  "
          f"{'tmean':>7}  {'P10':>7}  {'P25':>7}  {'hit%':>6}  {'dd':>6}")
    print("  " + "-" * 88)

    def print_row(lbl: str, st: dict, n_yr: float) -> None:
        if not st:
            print(f"  {lbl:<32}  표본부족")
            return
        print(f"  {lbl:<32}  {n_yr:>5.0f}  "
              f"{st['mean']:>+7.2f}  {st['median']:>+7.2f}  "
              f"{st['tmean']:>+7.2f}  {st['P10']:>+7.2f}  "
              f"{st['P25']:>+7.2f}  {st['hit%']:>5.1f}%  {st['dd']:>6.2f}")

    print_row("A. 현재 선형 (baseline)", st_a, n_yr_a)
    print("  " + "·" * 88)
    for t in R_THRESHOLDS:
        print_row(f"E≥{E_GATE_THR}+R≥{t}, V ranker (ER_{t})",
                  archs[t]["ER"]["st"], archs[t]["ER"]["n_yr"])
    print("  " + "·" * 88)
    for t in R_THRESHOLDS:
        print_row(f"R≥{t} only, V ranker (R_{t})",
                  archs[t]["R"]["st"], archs[t]["R"]["n_yr"])

    # ── vs A paired t-test ───────────────────────────────────────────────
    print(f"\n{SEP}")
    print(f"E_R_T vs A — 연도별 median 차이 t-test  (E gate 고정: ≥{E_GATE_THR})")
    print(SEP)
    print(f"  {'Architecture':<32}  {'diff':>6}  {'p':>7}  {'B>A':>6}  판정")
    print("  " + "-" * 62)
    for t in R_THRESHOLDS:
        r = paired_vs_a(ym_a, archs[t]["ER"]["ym"])
        sig = "**" if r["p"] < 0.01 else ("*" if r["p"] < 0.05 else
              ("†" if r["p"] < 0.10 else "  "))
        v = "ER>A" if r["diff"] > 0 else "ER<A"
        print(f"  E≥{E_GATE_THR}+R≥{t}, V ranker (ER_{t})  "
              f"{r['diff']:>+6.2f}  p={r['p']:.3f}{sig}  "
              f"{r['pos']:>2}/{r['n']}년  {v}")

    print(f"\nR_T only vs A — 연도별 median 차이 t-test  (E gate 없음)")
    print("  " + "-" * 62)
    for t in R_THRESHOLDS:
        r = paired_vs_a(ym_a, archs[t]["R"]["ym"])
        sig = "**" if r["p"] < 0.01 else ("*" if r["p"] < 0.05 else
              ("†" if r["p"] < 0.10 else "  "))
        v = "R>A" if r["diff"] > 0 else "R<A"
        print(f"  R≥{t} only, V ranker (R_{t})          "
              f"{r['diff']:>+6.2f}  p={r['p']:.3f}{sig}  "
              f"{r['pos']:>2}/{r['n']}년  {v}")

    # ── ER_T vs R_T: E gate 추가 기여도 ──────────────────────────────────
    print(f"\n{SEP}")
    print(f"ER_T vs R_T — E gate(≥{E_GATE_THR}) 추가 기여도 분리")
    print(SEP)
    print(f"  {'Comparison':<32}  {'diff(ER-R)':>10}  {'p':>7}  {'ER>R':>6}")
    print("  " + "-" * 62)
    for t in R_THRESHOLDS:
        r = paired_vs_a(archs[t]["R"]["ym"], archs[t]["ER"]["ym"])
        sig = "**" if r["p"] < 0.01 else ("*" if r["p"] < 0.05 else
              ("†" if r["p"] < 0.10 else "  "))
        v = "ER>R" if r["diff"] > 0 else "ER<R"
        print(f"  ER_{t} vs R_{t}                     "
              f"{r['diff']:>+10.2f}  p={r['p']:.3f}{sig}  "
              f"{r['pos']:>2}/{r['n']}년  {v}")

    # ── 연도별 median 비교 ────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("연도별 median alpha — ER_T vs A")
    print(SEP)
    cols_lbl = ["A"] + [f"ER_{t}" for t in R_THRESHOLDS] + [f"R_{t}" for t in R_THRESHOLDS]
    print(f"  {'Year':>6}  " + "  ".join(f"{c:>8}" for c in cols_lbl))
    print("  " + "-" * 68)
    for yr in sorted(df["fwd_year"].unique()):
        row = f"  {yr:>6}  {ym_a.get(yr, float('nan')):>+8.2f}"
        for t in R_THRESHOLDS:
            v = archs[t]["ER"]["ym"].get(yr, float("nan"))
            row += f"  {v:>+8.2f}"
        for t in R_THRESHOLDS:
            v = archs[t]["R"]["ym"].get(yr, float("nan"))
            row += f"  {v:>+8.2f}"
        print(row)

    # ── 판정 ─────────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("판정")
    print(SEP)

    er_medians = [archs[t]["ER"]["st"].get("median", -999) for t in R_THRESHOLDS]
    a_median   = st_a.get("median", 0)
    n_better   = sum(1 for m in er_medians if m > a_median)

    if n_better == 0:
        conclusion = "A. 모든 T에서 ER_T ≤ A → R filter 분리 실익 없음"
    elif n_better == len(R_THRESHOLDS):
        conclusion = "C. 모든 T에서 ER_T > A → R filter 자체의 구조적 역할 지지"
    else:
        conclusion = f"B. {n_better}/{len(R_THRESHOLDS)} T에서만 ER_T > A → R filter 효과가 threshold-specific"

    print(f"\n  → {conclusion}")
    print(f"""
  사전 고정 해석 기준:
    A. 모든 T에서 ER_T ≈ A       → R filter 분리 실익 없음
    B. 특정 T에서만 ER_T > A     → R filter 효과가 threshold-specific (취약)
    C. 모든/대부분 T에서 ER_T > A → R filter 자체의 구조적 역할 지지

  주의:
    이 실험은 "R filter로 후보를 걸러낼 때 성과가 좋아지는가"를 보는 것.
    Risk decomposition 결과("Risk 높을수록 downside 나쁨")와 혼동하지 않는다.
    in-sample RRS panel. Engine Freeze 해제 전 production 변경 금지.
""")


if __name__ == "__main__":
    main()
