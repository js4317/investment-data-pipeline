#!/usr/bin/env python3
"""
Momentum IC 분석
==================
핵심 질문:
  현재 production Momentum signal(mom_rel_1m/3m/6m)이
  어느 time horizon에서 실제 정보를 가지고 있는가?

검증 항목:
  1. 각 창별 IC (Spearman rank correlation vs fwd_alpha)
  2. 창별 quintile alpha 분포 (median/tmean/P10/hit%)
  3. 연도별 IC 안정성
  4. Sector-neutral IC (섹터 내 rank 후 IC)
  5. Outlier sensitivity (robust stats로 lottery 왜곡 확인)

사전 고정 기준:
  "유효" 판정 = IC t-test p < 0.05 + 양수 연도 ≥ 6/8 + median 방향 일치
  결과와 상관없이 3개 창 모두 기록.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT    = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "database" / "research.db"


def load() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT
            rrs.risk_year, rrs.fwd_year, rrs.ticker, rrs.sector,
            rrs.fwd_alpha,
            rrs.mom_rel_1m, rrs.mom_rel_3m, rrs.mom_rel_6m
        FROM rrs_stock_year rrs
        WHERE rrs.fwd_alpha IS NOT NULL
    """, conn)
    conn.close()
    return df


def robust_block(s: pd.Series) -> dict:
    s = s.dropna()
    n = len(s)
    if n < 10:
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


def annual_ic(df: pd.DataFrame, x_col: str, y_col: str = "fwd_alpha") -> tuple:
    ics = []
    for _, grp in df.groupby("fwd_year"):
        sub = grp[[x_col, y_col]].dropna()
        if len(sub) < 10:
            continue
        r, _ = stats.spearmanr(sub[x_col], sub[y_col])
        ics.append(r)
    if not ics:
        return float("nan"), float("nan"), []
    t, p = stats.ttest_1samp(ics, 0)
    return float(np.mean(ics)), float(p), ics


def assign_q_annual(df: pd.DataFrame, col: str,
                    scope_col: str | None = None) -> pd.Series:
    result = pd.Series(np.nan, index=df.index)
    gcols = ["fwd_year"] if scope_col is None else ["fwd_year", scope_col]
    for _, grp in df.groupby(gcols, dropna=True):
        valid = grp[col].dropna()
        if len(valid) < 10:
            continue
        try:
            cut = pd.qcut(valid, 5, labels=[1, 2, 3, 4, 5], duplicates="drop")
            result.loc[cut.index] = cut.astype(float)
        except Exception:
            continue
    return result


def direction_verdict(mean_d: float, median_d: float) -> str:
    if mean_d > 0 and median_d > 0:
        return "CONSISTENT ↑"
    elif mean_d < 0 and median_d < 0:
        return "STRUCTURAL ↓"
    elif mean_d > 0:
        return "LOTTERY (mean↑/median↓)"
    else:
        return "LOTTERY (mean↓/median↑)"


def analyze_window(df: pd.DataFrame, col: str, label: str, SEP: str) -> dict:
    """단일 모멘텀 창 전체 분석."""
    valid = df[df[col].notna()].copy()
    print(f"\n{SEP}")
    print(f"{label}  (n={len(valid)}건 / 가용연도={valid['fwd_year'].nunique()}개)")
    print(SEP)

    # ── IC ──────────────────────────────────────────────────────────────────
    ic, p, ics = annual_ic(valid, col)
    pos = sum(1 for x in ics if x > 0)
    sig = "**" if p < 0.01 else ("*" if p < 0.05 else "  ")
    print(f"\n  IC = {ic:+.3f}{sig}  p={p:.3f}  양수연도={pos}/{len(ics)}")

    # ── Sector-neutral IC ────────────────────────────────────────────────────
    valid["q_sn"] = assign_q_annual(valid, col, scope_col="sector")
    ic_sn, p_sn, ics_sn = annual_ic(valid, "q_sn")
    pos_sn = sum(1 for x in ics_sn if x > 0)
    sig_sn = "**" if p_sn < 0.01 else ("*" if p_sn < 0.05 else "  ")
    print(f"  IC (sector-neutral) = {ic_sn:+.3f}{sig_sn}  p={p_sn:.3f}  양수연도={pos_sn}/{len(ics_sn)}")

    # ── Quintile alpha ───────────────────────────────────────────────────────
    valid["q"] = assign_q_annual(valid, col)
    print(f"\n  Quintile alpha 분포:")
    print(f"  {'Q':>4}  {'n':>5}  {'mean':>7}  {'median':>7}  {'tmean':>7}  "
          f"{'P10':>7}  {'P25':>7}  {'hit%':>6}")
    print("  " + "-" * 62)
    q_stats = {}
    for q in [1, 2, 3, 4, 5]:
        lbl = {1: "Q1 (Low Mom)", 5: "Q5 (High Mom)"}.get(q, f"Q{q}")
        s = valid[valid["q"] == q]["fwd_alpha"]
        st = robust_block(s)
        if not st:
            print(f"  {lbl:<14} 표본부족")
            continue
        q_stats[q] = st
        print(f"  {lbl:<14}  {st['n']:>5}  {st['mean']:>+7.2f}  "
              f"{st['median']:>+7.2f}  {st['tmean']:>+7.2f}  "
              f"{st['P10']:>+7.2f}  {st['P25']:>+7.2f}  {st['hit%']:>5.1f}%")

    if 5 in q_stats and 1 in q_stats:
        dm = q_stats[5]["mean"] - q_stats[1]["mean"]
        dd = q_stats[5]["median"] - q_stats[1]["median"]
        print(f"\n  Q5 - Q1:  mean={dm:>+.2f}  median={dd:>+.2f}  "
              f"→ {direction_verdict(dm, dd)}")

    # ── 연도별 IC ────────────────────────────────────────────────────────────
    print(f"\n  연도별 IC:")
    print(f"  {'Year':>6}  {'IC':>8}  {'p':>7}  {'n':>5}")
    print("  " + "-" * 30)
    for yr, grp in valid.groupby("fwd_year"):
        sub = grp[[col, "fwd_alpha"]].dropna()
        if len(sub) < 10:
            continue
        r, p_yr = stats.spearmanr(sub[col], sub["fwd_alpha"])
        sig_yr = "**" if p_yr < 0.01 else ("*" if p_yr < 0.05 else "  ")
        print(f"  {yr:>6}  {r:>+8.3f}{sig_yr}  p={p_yr:.3f}  n={len(sub):>4}")

    return {"ic": ic, "p": p, "pos": pos, "n_years": len(ics),
            "ic_sn": ic_sn, "p_sn": p_sn}


def main() -> None:
    SEP = "=" * 72

    df = load()
    print(f"패널: {len(df)}건 / risk_year {sorted(df['risk_year'].unique())}")
    print(f"Momentum 가용: 1m={df['mom_rel_1m'].notna().sum()} / "
          f"3m={df['mom_rel_3m'].notna().sum()} / "
          f"6m={df['mom_rel_6m'].notna().sum()}")

    windows = [
        ("mom_rel_1m", "1M Momentum (KOSPI 대비 1개월 상대수익률)"),
        ("mom_rel_3m", "3M Momentum (KOSPI 대비 3개월 상대수익률)"),
        ("mom_rel_6m", "6M Momentum (KOSPI 대비 6개월 상대수익률)"),
    ]

    summary = {}
    for col, label in windows:
        summary[col] = analyze_window(df, col, label, SEP)

    # ── 창별 요약 비교 ───────────────────────────────────────────────────────
    print(f"\n\n{SEP}")
    print("창별 IC 비교 요약")
    print(SEP)
    print(f"  {'창':>12}  {'IC':>7}  {'p':>7}  {'양수연도':>8}  "
          f"{'IC(SN)':>8}  {'p(SN)':>7}  판정")
    print("  " + "-" * 72)
    for col, label in windows:
        st = summary[col]
        short = col.replace("mom_rel_", "").upper()
        sig   = "**" if st["p"] < 0.01 else ("*" if st["p"] < 0.05 else "  ")
        sig_sn = "**" if st["p_sn"] < 0.01 else ("*" if st["p_sn"] < 0.05 else "  ")

        if st["p"] < 0.05 and st["pos"] >= 6:
            verdict = "유효"
        elif st["p"] < 0.10:
            verdict = "약한 신호"
        else:
            verdict = "무효"

        print(f"  {short:>12}  {st['ic']:>+7.3f}{sig}  "
              f"p={st['p']:.3f}  {st['pos']:>2}/{st['n_years']}년  "
              f"{st['ic_sn']:>+8.3f}{sig_sn}  p={st['p_sn']:.3f}  {verdict}")

    print(f"""
  판정 기준:
    유효      = IC t-test p < 0.05 AND 양수연도 ≥ 6/{summary['mom_rel_1m']['n_years']}
    약한 신호  = p < 0.10
    무효      = p ≥ 0.10

  해석 가이드:
    1M > 3M > 6M (감쇠): 단기 timing signal → gate/filter 역할 후보
    1M ≈ 3M ≈ 6M (지속): 지속 효과 → ranker 역할 후보
    모두 무효: 현재 Momentum 정의로는 정보 없음
""")


if __name__ == "__main__":
    main()
