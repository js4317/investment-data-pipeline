#!/usr/bin/env python3
"""
Architecture Counterfactual
==============================
핵심 질문:
  지금까지의 연구(B/M, Earnings, Risk, ROE)를 바탕으로,
  V+E+R+S 선형 합산 구조보다 역할 분리 구조가 더 나은가?

연구 배경:
  - Value: primary ranker로 확정할 근거 없음
  - Earnings/ROE: 흑자 내부 ranker는 역방향. Gate 역할만 지지됨.
  - Risk: downside floor 역할이 가장 명확. 평균 alpha ranker 아님.
  - Supply: timing/entry 후보이나 예측력 미확인.

  반복된 패턴: component들이 서로 다른 종류의 정보를 담고 있음.
  이를 단순 합산하면 각 역할이 왜곡될 수 있다.

비교 Architecture (사전 고정):
  A. Current      V+E+R+S 선형 합산, top 25%
  B. Supply Gate  V+E+R ranker, Supply ≥ 연간 중간값 = gate
  C. E Gate       V+R ranker, Earnings ≥ 22 = gate
  D. R Filter     V+E ranker, Risk ≥ 22 = filter
  E. Role-based   E ≥ 22(gate) + R ≥ 22(filter), V = pure ranker
  F. Full Role    E ≥ 22(gate) + R ≥ 22(filter) + Supply ≥ median(timing), V = pure ranker

측정 지표:
  primary: median / trimmed mean alpha (10%)
  secondary: P10, P25, hit rate, downside deviation, n(연간 평균 선택 종목 수)

  주의: raw mean은 보조 참고용. lottery 왜곡으로 인해 primary objective 아님.

설계 원칙:
  - 가중치/배점 최적화 금지. 역할 분리 자체의 효과를 확인하는 것이 목적.
  - 같은 RRS 역사 panel (2016-2023). in-sample이므로 결과는 방향성 참고용.
  - 다중 비교: 6개 architecture 모두 기록. 잘 된 것만 선택 금지.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT    = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "database" / "research.db"

E_GATE_THRESHOLD = 22   # earnings_score ≥ 22 = gate pass
R_FILTER_THRESHOLD = 22 # risk_score ≥ 22 = filter pass
TOP_PCT = 0.25


def load() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT
            rrs.risk_year, rrs.fwd_year, rrs.ticker,
            rrs.fwd_alpha,
            rrs.normalized_score,
            rrs.value_score, rrs.earnings_score,
            rrs.risk_score,
            COALESCE(rrs.supply_score_real, rrs.supply_score) AS supply_score
        FROM rrs_stock_year rrs
        WHERE rrs.fwd_alpha IS NOT NULL
    """, conn)
    conn.close()
    return df


def robust_stats(s: pd.Series) -> dict:
    s = s.dropna()
    n = len(s)
    if n < 5:
        return {}
    sorted_s = np.sort(s.values)
    k = int(n * 0.10)
    trimmed = sorted_s[k: n - k] if k > 0 else sorted_s
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


def select_top_pct(df: pd.DataFrame, score_col: str,
                   gate_mask: pd.Series | None = None) -> pd.Series:
    """연도별 top TOP_PCT% 선택 (gate_mask 통과 후)."""
    selected = pd.Series(False, index=df.index)
    for yr, grp in df.groupby("fwd_year"):
        if gate_mask is not None:
            pool = grp[gate_mask.loc[grp.index]]
        else:
            pool = grp
        n_top = max(1, int(len(pool) * TOP_PCT))
        top_idx = pool.nlargest(n_top, score_col).index
        selected.loc[top_idx] = True
    return selected


def annual_stats(df: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
    """연도별 alpha 통계 (median/P10/hit%)."""
    rows = []
    for yr, grp in df.groupby("fwd_year"):
        sel = grp[mask.loc[grp.index]]
        if len(sel) == 0:
            continue
        s = sel["fwd_alpha"]
        rows.append({
            "year": yr,
            "n": len(s),
            "median": round(s.median(), 2),
            "P10": round(np.percentile(s, 10), 2),
            "hit%": round((s > 0).mean() * 100, 1),
        })
    return pd.DataFrame(rows).set_index("year")


def print_arch(label: str, alpha: pd.Series, n_per_year: float,
               yearly: pd.DataFrame) -> None:
    st = robust_stats(alpha)
    if not st:
        print(f"  {label}: 표본부족")
        return
    print(f"  {label:<20}  n/yr≈{n_per_year:>4.0f}  "
          f"mean={st['mean']:>+7.2f}  median={st['median']:>+7.2f}  "
          f"tmean={st['tmean']:>+7.2f}  P10={st['P10']:>+7.2f}  "
          f"P25={st['P25']:>+7.2f}  hit={st['hit%']:>5.1f}%  dd={st['dd']:>6.2f}")


def main() -> None:
    SEP = "=" * 100

    df = load()
    print(f"패널: {len(df)}건 / risk_year {sorted(df['risk_year'].unique())}")

    # 파생 점수
    df["score_ver"] = df["value_score"] + df["earnings_score"] + df["risk_score"]
    df["score_vr"]  = df["value_score"] + df["risk_score"]
    df["score_ve"]  = df["value_score"] + df["earnings_score"]

    # Supply 연간 중간값 gate
    df["supply_gate"] = False
    for yr, grp in df.groupby("fwd_year"):
        med = grp["supply_score"].median()
        df.loc[grp[grp["supply_score"] >= med].index, "supply_gate"] = True

    supply_gate_mask  = df["supply_gate"]
    e_gate_mask       = df["earnings_score"] >= E_GATE_THRESHOLD
    r_filter_mask     = df["risk_score"] >= R_FILTER_THRESHOLD
    role_base_mask    = e_gate_mask & r_filter_mask
    full_role_mask    = role_base_mask & supply_gate_mask

    # Architecture 선택
    arch = {
        "A. Current":    select_top_pct(df, "normalized_score"),
        "B. Supply Gate": select_top_pct(df, "score_ver", supply_gate_mask),
        "C. E Gate":     select_top_pct(df, "score_vr",  e_gate_mask),
        "D. R Filter":   select_top_pct(df, "score_ve",  r_filter_mask),
        "E. Role-based": select_top_pct(df, "value_score", role_base_mask),
        "F. Full Role":  select_top_pct(df, "value_score", full_role_mask),
    }

    # 연도별 평균 선택 종목 수
    n_per_year = {
        k: df[mask].groupby("fwd_year").size().mean()
        for k, mask in arch.items()
    }

    # ── 전체 요약 테이블 ───────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("전체 비교 — Architecture별 alpha 분포 (전체 기간 합산)")
    print("primary: median / tmean    secondary: mean (참고용)")
    print(SEP)
    print(f"  {'Architecture':<20}  {'n/yr':>5}  {'mean':>7}  {'median':>7}  "
          f"{'tmean':>7}  {'P10':>7}  {'P25':>7}  {'hit%':>6}  {'dd':>6}")
    print("  " + "-" * 90)

    yearly_arch = {}
    for lbl, mask in arch.items():
        alpha = df[mask]["fwd_alpha"]
        yearly_df = annual_stats(df, mask)
        yearly_arch[lbl] = yearly_df
        print_arch(lbl, alpha, n_per_year[lbl], yearly_df)

    # ── 연도별 median 비교 ────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("연도별 median alpha 비교")
    print(SEP)
    years = sorted(df["fwd_year"].unique())
    labels = list(arch.keys())
    header = f"  {'Year':>6}  " + "  ".join(f"{l:<20}" for l in labels)
    print(header[:120])
    print("  " + "-" * 100)
    for yr in years:
        row = f"  {yr:>6}  "
        for lbl in labels:
            val = yearly_arch[lbl].loc[yr, "median"] if yr in yearly_arch[lbl].index else float("nan")
            row += f"  {val:>+20.2f}"
        print(row)

    # ── 연도별 P10 비교 ───────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("연도별 P10 alpha 비교 (downside tail)")
    print(SEP)
    print(header[:120])
    print("  " + "-" * 100)
    for yr in years:
        row = f"  {yr:>6}  "
        for lbl in labels:
            val = yearly_arch[lbl].loc[yr, "P10"] if yr in yearly_arch[lbl].index else float("nan")
            row += f"  {val:>+20.2f}"
        print(row)

    # ── 각 Architecture의 gate 통과율 ─────────────────────────────────────
    print(f"\n{SEP}")
    print("Gate/Filter 통과율 (연간 평균 pool 크기)")
    print(SEP)
    pool_info = [
        ("전체 universe",      len(df) / df["fwd_year"].nunique()),
        ("E gate(≥22)",        e_gate_mask.sum() / df["fwd_year"].nunique()),
        ("R filter(≥22)",      r_filter_mask.sum() / df["fwd_year"].nunique()),
        ("Supply gate(≥med)",  supply_gate_mask.sum() / df["fwd_year"].nunique()),
        ("E+R (Role base)",    role_base_mask.sum() / df["fwd_year"].nunique()),
        ("E+R+S (Full role)",  full_role_mask.sum() / df["fwd_year"].nunique()),
    ]
    for lbl, n in pool_info:
        pct = n / (len(df) / df["fwd_year"].nunique()) * 100
        print(f"  {lbl:<28}  {n:>5.0f}건/년  ({pct:.1f}%)")

    # ── A vs 각 구조 쌍별 t-test ─────────────────────────────────────────
    print(f"\n{SEP}")
    print("A(Current) vs 각 Architecture — 연도별 median 차이 t-test")
    print("  (median 차이의 연도 평균이 0과 다른가?)")
    print(SEP)

    a_med = yearly_arch["A. Current"]["median"]
    for lbl in labels[1:]:
        b_med = yearly_arch[lbl]["median"]
        common = a_med.index.intersection(b_med.index)
        if len(common) < 4:
            print(f"  A vs {lbl:<20}: 연도 부족")
            continue
        diffs = (b_med - a_med).loc[common]
        t, p = stats.ttest_1samp(diffs, 0)
        pos = (diffs > 0).sum()
        sig = "**" if p < 0.01 else ("*" if p < 0.05 else "  ")
        print(f"  A vs {lbl:<20}: diff median={diffs.mean():>+6.2f}  "
              f"t={t:>+5.2f}  p={p:.3f}{sig}  "
              f"B>A in {pos}/{len(common)} years")

    # ── 요약 판정 기준 ────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("해석 기준")
    print(SEP)
    print("""
  PRIMARY 판정: median/tmean 기준 A보다 B가 일관되게 나은가?
    → 연도별 median이 A보다 높은 연도 비율 ≥ 5/8 + tmean 방향 일치 시 "유효"

  SECONDARY 확인:
    → P10/P25가 개선되면 downside 보호 강화
    → dd(downside deviation)가 감소하면 꼬리 위험 감소
    → hit rate 개선이면 일관성 상승

  주의: 이 비교는 RRS in-sample (2016-2023).
        "역할 분리가 방향성으로 나은가"의 탐색 목적. OOS 검증 필요.
        잘 된 architecture만 선택하지 말고 6개 전부 기록.
""")


if __name__ == "__main__":
    main()
