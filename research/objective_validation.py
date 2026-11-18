#!/usr/bin/env python3
"""
Lottery-adjusted Objective Validation
========================================
핵심 질문:
  B/M / Earnings / Risk에서 반복된 "역방향 mean alpha"가
  lottery distribution 문제인가, 아니면 진짜 구조적 현상인가?

검증 방법:
  각 핵심 비교 쌍에 대해:
    raw mean / median / trimmed mean(10%) / winsorized mean(5%)
    + P10 / P25 / hit rate / downside deviation

  만약:
    raw mean: 역방향   (나쁜 fundamental이 높음)
    median:   정방향   (좋은 fundamental이 높음)
  → lottery distribution 문제. 극단 outlier가 mean을 당기고 있음.

  만약:
    raw mean / median 둘 다 역방향
  → 구조적 현상. objective 재검토 필요.

비교 쌍 (핵심 6개):
  BM1: B/M Q5 (High B/M, Value) vs Q1 (Low B/M, Growth)
  BM2: B/M Risk-clean Q5 vs Q1 (distress 제거 후)
  EG1: 적자 vs 흑자 (ni_ytd 기준)
  EG2: 흑자 내 ROE Q5 (High) vs Q1 (Low)
  RS1: Risk=25 (clean) vs Risk 0~3 (high penalty)
  ES1: earnings_score=22 vs =9

설계 원칙:
  이미 관찰된 현상의 통계적 형태를 확인하는 연구.
  새로운 factor 탐색 아님 → researcher degrees of freedom 최소.
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
            rrs.fwd_alpha, rrs.earnings_score, rrs.value_score,
            rrs.risk_score,
            rs.pbr, rs.roe,
            rh.ni_ytd
        FROM rrs_stock_year rrs
        JOIN research_stock_year rs
            ON rs.year = rrs.fwd_year AND rs.ticker = rrs.ticker
        JOIN risk_history rh
            ON rh.year = rrs.risk_year AND rh.ticker = rrs.ticker
        WHERE rrs.fwd_alpha IS NOT NULL
    """, conn)
    conn.close()
    return df


def robust_block(s: pd.Series) -> dict:
    """raw mean / median / trimmed mean / winsorized mean + downside metrics."""
    s = s.dropna()
    n = len(s)
    if n < 15:
        return {}
    trim_pct = 0.10
    wins_pct = 0.05
    neg = s[s < 0]
    sorted_s = np.sort(s)
    k_trim = int(n * trim_pct)
    trimmed = sorted_s[k_trim: n - k_trim] if k_trim > 0 else sorted_s

    k_wins = int(n * wins_pct)
    winsorized = sorted_s.copy()
    if k_wins > 0:
        winsorized[:k_wins]  = winsorized[k_wins]
        winsorized[-k_wins:] = winsorized[-k_wins - 1]

    return {
        "n":       n,
        "mean":    round(float(s.mean()), 2),
        "median":  round(float(np.median(s)), 2),
        "tmean":   round(float(trimmed.mean()), 2),
        "wmean":   round(float(winsorized.mean()), 2),
        "P10":     round(float(np.percentile(s, 10)), 2),
        "P25":     round(float(np.percentile(s, 25)), 2),
        "hit%":    round(float((s > 0).mean() * 100), 1),
        "dd":      round(float(neg.std()), 2) if len(neg) >= 5 else float("nan"),
    }


def print_pair(label_a: str, a: pd.Series,
               label_b: str, b: pd.Series,
               note: str = "") -> None:
    """두 그룹 나란히 출력 + diff 행."""
    W = 28
    a, b = a.dropna(), b.dropna()
    sa, sb = robust_block(a), robust_block(b)
    if not sa or not sb:
        print(f"  {label_a}/{label_b}: 표본부족")
        return

    if note:
        print(f"  [{note}]")

    # 헤더
    print(f"  {'':>{W}}  {'n':>5}  {'mean':>7}  {'median':>7}  "
          f"{'tmean':>7}  {'wmean':>7}  {'P10':>7}  {'hit%':>6}  {'dd':>6}")
    print("  " + "-" * (W + 60))

    for lbl, st in [(label_a, sa), (label_b, sb)]:
        print(f"  {lbl:>{W}}  {st['n']:>5}  {st['mean']:>+7.2f}  "
              f"{st['median']:>+7.2f}  {st['tmean']:>+7.2f}  {st['wmean']:>+7.2f}  "
              f"{st['P10']:>+7.2f}  {st['hit%']:>5.1f}%  {st['dd']:>6.2f}")

    # diff 행 (A - B)
    diff = {k: round(sa[k] - sb[k], 2)
            for k in ["mean", "median", "tmean", "wmean", "P10", "hit%", "dd"]
            if isinstance(sa.get(k), float) and isinstance(sb.get(k), float)}
    print(f"  {'diff (A - B)':>{W}}  {'':>5}  {diff.get('mean',0):>+7.2f}  "
          f"{diff.get('median',0):>+7.2f}  {diff.get('tmean',0):>+7.2f}  "
          f"{diff.get('wmean',0):>+7.2f}  {diff.get('P10',0):>+7.2f}  "
          f"{diff.get('hit%',0):>+5.1f}%  {diff.get('dd',0):>+6.2f}")

    # 방향 판정
    dirs = {
        "mean":   "→ " if diff.get("mean", 0) > 0 else "← ",
        "median": "→ " if diff.get("median", 0) > 0 else "← ",
        "tmean":  "→ " if diff.get("tmean", 0) > 0 else "← ",
        "wmean":  "→ " if diff.get("wmean", 0) > 0 else "← ",
        "P10":    "→ " if diff.get("P10", 0) > 0 else "← ",
    }
    same = all(v == "→ " for v in dirs.values())
    opposite = all(v == "← " for v in dirs.values())
    mixed = not same and not opposite

    verdict = ""
    if same:
        verdict = "✓ 모든 지표에서 A > B (일관)"
    elif dirs["mean"] != dirs["median"] and dirs["mean"] != dirs["tmean"]:
        verdict = "⚠ mean 역방향 / median·tmean 정방향 → LOTTERY 왜곡 가능성"
    elif dirs["mean"] == dirs["median"]:
        verdict = "! mean·median 동일 방향 → 구조적 패턴"
    else:
        verdict = "? 혼합 결과"

    if dirs["P10"] != dirs["mean"]:
        verdict += " | P10은 반대 방향 (downside 기여)"

    print(f"  → {verdict}")
    print()


def assign_quintile_annual(df: pd.DataFrame, col: str) -> pd.Series:
    result = pd.Series(np.nan, index=df.index)
    for _, grp in df.groupby("fwd_year"):
        valid = grp[col].dropna()
        if len(valid) < 10:
            continue
        q = pd.qcut(valid, 5, labels=[1, 2, 3, 4, 5], duplicates="drop")
        result.loc[q.index] = q.astype(float)
    return result


def main() -> None:
    SEP = "=" * 88

    df = load()
    print(f"패널: {len(df)}건 / risk_year {sorted(df['risk_year'].unique())}")
    print()
    print("  Robust statistics 범례:")
    print("  tmean = trimmed mean (상하 10% 제거)")
    print("  wmean = winsorized mean (상하 5% 경계로 대체)")
    print("  dd    = downside deviation (음수 수익 표준편차)")
    print()

    df["bm"] = np.where(df["pbr"] > 0, 1.0 / df["pbr"], np.nan)
    df["bm_q"] = assign_quintile_annual(df[df["pbr"] > 0], "bm")
    df.loc[df["pbr"] <= 0, "bm_q"] = np.nan  # invalid PBR 제거

    df["roe_q"] = np.nan
    prof = df[(df["ni_ytd"] > 0) & df["roe"].notna()]
    df.loc[prof.index, "roe_q"] = assign_quintile_annual(prof, "roe").values

    # ── Section 1: B/M ───────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("Section 1 — B/M: Value vs Growth")
    print(SEP + "\n")

    bm_q5 = df[df["bm_q"] == 5]["fwd_alpha"]
    bm_q1 = df[df["bm_q"] == 1]["fwd_alpha"]
    print_pair("Q5 (High B/M, Value)", bm_q5,
               "Q1 (Low B/M, Growth)", bm_q1,
               "BM1: 전체 B/M Q5 vs Q1")

    risk25 = df[df["risk_score"] == 25]
    bm_q5_r = risk25[risk25["bm_q"] == 5]["fwd_alpha"]
    bm_q1_r = risk25[risk25["bm_q"] == 1]["fwd_alpha"]
    print_pair("Q5 Risk-clean", bm_q5_r,
               "Q1 Risk-clean", bm_q1_r,
               "BM2: Risk=25 서브셋 내 Q5 vs Q1 (distress 제거 후)")

    # ── Section 2: Earnings ──────────────────────────────────────────────────
    print(f"{SEP}")
    print("Section 2 — Earnings: 적자 vs 흑자, 흑자 내 ROE")
    print(SEP + "\n")

    profit_a = df[df["fwd_alpha"].notna() & (df["ni_ytd"] > 0)]["fwd_alpha"]
    loss_a   = df[df["fwd_alpha"].notna() & (df["ni_ytd"] < 0)]["fwd_alpha"]
    print_pair("흑자 (ni_ytd > 0)", profit_a,
               "적자 (ni_ytd < 0)", loss_a,
               "EG1: 적자 vs 흑자 경계 효과")

    roe_q5 = df[df["roe_q"] == 5]["fwd_alpha"]
    roe_q1 = df[df["roe_q"] == 1]["fwd_alpha"]
    print_pair("ROE Q5 (High)", roe_q5,
               "ROE Q1 (Low)", roe_q1,
               "EG2: 흑자 내 ROE Q5 vs Q1")

    es22 = df[df["earnings_score"] == 22]["fwd_alpha"]
    es9  = df[df["earnings_score"] == 9]["fwd_alpha"]
    print_pair("earnings_score=22 (고품질)", es22,
               "earnings_score=9  (적자근사)", es9,
               "ES1: earnings_score 22 vs 9")

    # ── Section 3: Risk ──────────────────────────────────────────────────────
    print(f"{SEP}")
    print("Section 3 — Risk: clean vs penalized")
    print(SEP + "\n")

    r25  = df[df["risk_score"] == 25]["fwd_alpha"]
    r_lo = df[df["risk_score"] <= 17]["fwd_alpha"]   # 3+ penalties
    print_pair("Risk=25 (페널티없음)", r25,
               "Risk≤17 (3+페널티)", r_lo,
               "RS1: Risk 양 극단 비교")

    # ── Section 4: 전체 패턴 요약 ────────────────────────────────────────────
    print(f"{SEP}")
    print("Section 4 — 전체 패턴 요약")
    print(SEP)
    print()
    print("  mean 방향 vs median 방향 요약 (A=좋은 fundamental, B=나쁜 fundamental):")
    print()
    print(f"  {'비교':>28}  {'mean(A-B)':>10}  {'median(A-B)':>12}  {'P10(A-B)':>10}  판정")
    print("  " + "-" * 78)

    comparisons = [
        ("BM1: Value(Q5) vs Growth(Q1)", bm_q5, bm_q1),
        ("BM2: Value Q5 Risk-clean",     bm_q5_r, bm_q1_r),
        ("EG1: 흑자 vs 적자",             profit_a, loss_a),
        ("EG2: ROE Q5 vs Q1",            roe_q5, roe_q1),
        ("ES1: score22 vs score9",        es22, es9),
        ("RS1: Risk=25 vs Risk≤17",       r25, r_lo),
    ]

    for lbl, a, b in comparisons:
        sa, sb = robust_block(a.dropna()), robust_block(b.dropna())
        if not sa or not sb:
            continue
        dm = sa["mean"] - sb["mean"]
        dd = sa["median"] - sb["median"]
        dp = sa["P10"] - sb["P10"]
        mean_dir   = "A>B" if dm > 0 else "B>A"
        median_dir = "A>B" if dd > 0 else "B>A"
        p10_dir    = "A>B" if dp > 0 else "B>A"

        if mean_dir != median_dir:
            verdict = "LOTTERY (mean/median 반대)"
        elif mean_dir == "A>B":
            verdict = "CONSISTENT (양방향)"
        else:
            verdict = "STRUCTURAL (양방향 역전)"

        print(f"  {lbl:>28}  {dm:>+10.2f}  {dd:>+12.2f}  {dp:>+10.2f}  {verdict}")

    print()
    print("  [해석 가이드]")
    print("  LOTTERY:     mean은 역방향이지만 median은 정방향 → 극단 outlier가 mean을 당김")
    print("  CONSISTENT:  mean/median 모두 같은 방향 → 일관된 신호")
    print("  STRUCTURAL:  mean/median 모두 역방향 → 진짜 구조적 현상 (추가 검토 필요)")
    print()
    print("  [P10 방향이 mean과 반대인 경우]")
    print("  → 나쁜 fundamental이 더 나쁜 downside를 가짐 (보호 기능은 존재)")


if __name__ == "__main__":
    main()
