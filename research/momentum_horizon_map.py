#!/usr/bin/env python3
"""
Momentum Horizon Mapping
==========================
핵심 질문:
  KR 시장에서 reversal → continuation의 경계가 어디에 있는가?
  formation window를 1M ~ 12-1로 늘려가며 IC 지형도 작성.

창 정의 (모두 KOSPI 대비 상대수익률, 연말 기준):
  1M    : Dec vs Nov       (1개월 전)
  3M    : Dec vs Sep       (3개월 전)
  6M    : Dec vs Jun       (6개월 전)
  9M    : Dec vs Mar       (9개월 전)
  12M   : Dec vs Dec_prev  (12개월 전)
  6-1   : Nov vs Jun       (6개월, 최근 1M skip)
  12-1  : Nov vs Dec_prev  (12개월, 최근 1M skip)

Forward: fwd_alpha (다음 해 annual KOSPI-초과 수익률)

사전 고정 판정 기준:
  유효 = IC t-test p < 0.05 AND 양수연도 ≥ 6/n_years
  방향 분류: IC > 0 = continuation, IC < 0 = reversal
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT    = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "database" / "research.db"

WINDOWS = [
    ("mom_rel_1m",   "1M   (Dec→Nov)"),
    ("mom_rel_3m",   "3M   (Dec→Sep)"),
    ("mom_rel_6m",   "6M   (Dec→Jun)"),
    ("mom_rel_9m",   "9M   (Dec→Mar)"),
    ("mom_rel_12m",  "12M  (Dec→Dec_prev)"),
    ("mom_rel_6_1",  "6-1  (Nov→Jun)"),
    ("mom_rel_12_1", "12-1 (Nov→Dec_prev)"),
]


def load() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT
            fwd_year, ticker, sector, fwd_alpha,
            mom_rel_1m, mom_rel_3m, mom_rel_6m,
            mom_rel_9m, mom_rel_12m, mom_rel_6_1, mom_rel_12_1
        FROM rrs_stock_year
        WHERE fwd_alpha IS NOT NULL
    """, conn)
    conn.close()
    return df


def annual_ic(df: pd.DataFrame, x_col: str) -> tuple[float, float, list[float], int]:
    ics = []
    for _, grp in df.groupby("fwd_year"):
        sub = grp[[x_col, "fwd_alpha"]].dropna()
        if len(sub) < 10:
            continue
        r, _ = stats.spearmanr(sub[x_col], sub["fwd_alpha"])
        ics.append(r)
    if not ics:
        return float("nan"), float("nan"), [], 0
    t, p = stats.ttest_1samp(ics, 0)
    pos = sum(1 for x in ics if x > 0)
    return float(np.mean(ics)), float(p), ics, pos


def robust_q5_q1(df: pd.DataFrame, col: str) -> tuple[float, float]:
    """Q5 - Q1의 mean 차이, median 차이."""
    result = pd.Series(np.nan, index=df.index)
    for _, grp in df.groupby("fwd_year"):
        valid = grp[col].dropna()
        if len(valid) < 10:
            continue
        try:
            cut = pd.qcut(valid, 5, labels=[1, 2, 3, 4, 5], duplicates="drop")
            result.loc[cut.index] = cut.astype(float)
        except Exception:
            continue
    df2 = df.copy()
    df2["_q"] = result
    q5 = df2[df2["_q"] == 5]["fwd_alpha"]
    q1 = df2[df2["_q"] == 1]["fwd_alpha"]
    return (
        round(float(q5.mean() - q1.mean()), 2),
        round(float(q5.median() - q1.median()), 2),
    )


def main() -> None:
    SEP = "=" * 80
    df = load()
    n_years = df["fwd_year"].nunique()

    print(f"패널: {len(df)}건 / fwd_year {sorted(df['fwd_year'].unique())}")
    print(f"\n가용 건수:")
    for col, lbl in WINDOWS:
        print(f"  {lbl:<28} n={df[col].notna().sum()}")

    # ── 핵심 요약 테이블 ─────────────────────────────────────────────────
    print(f"\n\n{SEP}")
    print("Momentum Horizon Map — IC 요약")
    print(SEP)
    print(f"  {'창':<28}  {'IC':>7}  {'p':>7}  {'양수연도':>8}  "
          f"{'Q5-Q1 mean':>11}  {'Q5-Q1 med':>10}  방향")
    print("  " + "-" * 80)

    summary = {}
    for col, lbl in WINDOWS:
        sub = df[df[col].notna()].copy()
        ic, p, ics, pos = annual_ic(sub, col)
        dm, dd = robust_q5_q1(sub, col)
        sig = "**" if p < 0.01 else ("*" if p < 0.05 else ("†" if p < 0.10 else "  "))

        if ic > 0 and p < 0.05:
            direction = "CONTINUATION ↑"
        elif ic < 0 and p < 0.05:
            direction = "REVERSAL ↓"
        elif p < 0.10:
            direction = "약한 신호"
        else:
            direction = "무효"

        summary[col] = {"ic": ic, "p": p, "pos": pos, "n": len(ics),
                        "dm": dm, "dd": dd, "direction": direction}

        print(f"  {lbl:<28}  {ic:>+7.3f}{sig}  p={p:.3f}  "
              f"{pos:>2}/{len(ics)}년  "
              f"{dm:>+11.2f}  {dd:>+10.2f}  {direction}")

    # ── 연도별 IC 히트맵 ─────────────────────────────────────────────────
    print(f"\n\n{SEP}")
    print("연도별 IC (행=연도, 열=창)")
    print(SEP)
    cols  = [c for c, _ in WINDOWS]
    years = sorted(df["fwd_year"].unique())
    hdrs  = ["1M", "3M", "6M", "9M", "12M", "6-1", "12-1"]
    print(f"  {'Year':>6}  " + "  ".join(f"{h:>7}" for h in hdrs))
    print("  " + "-" * 65)
    for yr in years:
        row = f"  {yr:>6}  "
        for col in cols:
            sub = df[df["fwd_year"] == yr][[col, "fwd_alpha"]].dropna()
            if len(sub) < 10:
                row += "      NA  "
            else:
                r, pv = stats.spearmanr(sub[col], sub["fwd_alpha"])
                s = "**" if pv < 0.01 else ("*" if pv < 0.05 else "  ")
                row += f"  {r:>+5.2f}{s}"
        print(row)

    # ── 해석 가이드 ──────────────────────────────────────────────────────
    print(f"\n\n{SEP}")
    print("해석")
    print(SEP)

    rev_windows  = [lbl for col, lbl in WINDOWS
                    if summary[col]["ic"] < 0 and summary[col]["p"] < 0.10]
    cont_windows = [lbl for col, lbl in WINDOWS
                    if summary[col]["ic"] > 0 and summary[col]["p"] < 0.10]
    null_windows = [lbl for col, lbl in WINDOWS
                    if summary[col]["p"] >= 0.10]

    if rev_windows:
        print(f"\n  Reversal 후보  (p<0.10): {', '.join(rev_windows)}")
    if cont_windows:
        print(f"  Continuation 후보 (p<0.10): {', '.join(cont_windows)}")
    if null_windows:
        print(f"  무효 창         (p≥0.10): {', '.join(null_windows)}")

    print(f"""
  가설 A — 짧은 창만 reversal, 긴 창은 continuation:
    → 현재 Production 정의(1M/3M/6M)가 너무 짧을 가능성.
    → 12-1 같은 장기 formation을 production에서 검토 여지.

  가설 B — 모든 창 reversal:
    → KR 시장/현 표본에서 continuation보다 reversal 우세.
    → Momentum 컴포넌트 편입 보류 근거.

  가설 C — 모든 창 무효:
    → 이 엔진에서 Momentum은 우선 연구 대상에서 제외.

  주의:
    12M / 12-1은 risk_year 2016 제외 (2015-12 스냅샷 없음) → n_years=7.
    in-sample RRS 패널 결과이므로 Production 변경 전 OOS 검증 필요.
""")


if __name__ == "__main__":
    main()
