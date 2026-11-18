#!/usr/bin/env python3
"""
Architecture Validation v2 — Role-based Counterfactual
========================================================
핵심 질문:
  지금까지 연구에서 도출된 역할 가설을 적용했을 때
  선형 합산보다 역할 분리 구조가 실제 목적함수(robust return + downside)에 더 나은가?

역할 가설 (연구 기반, 사전 고정):
  Earnings → Gate  (흑자/품질 하한선, 진입 통제)
  Risk     → Filter (downside 보호, 재무 안정성)
  Value    → Ranker (후보 내 순위 결정)
  Supply   → 제외 (architecture에서 제외, 참고용으로만)

비교 Architecture (5개, 사전 고정):
  A. 현재 선형      V+E+R+S 선형 합산 (baseline, 실데이터 Supply 포함)
  B. V+E+R 선형    Supply 제거 선형 합산 (Supply 기여도 분리용)
  C. 역할 분리 핵심  E Gate(≥22) + R Filter(≥22), V=value_score ranker
  D. E Gate only   E Gate(≥22) only + V ranker (R filter 없을 때)
  E. R Filter only R Filter(≥22) only + V ranker (E gate 없을 때)

Primary metric (사전 고정):
  median alpha / trimmed mean(10%) / P10 / P25 / hit rate / downside deviation
  → raw mean은 lottery 왜곡 위험이 있으므로 보조 참고용

설계 원칙:
  - 가중치/배점 최적화 금지
  - 5개 architecture 전부 기록 (좋은 것만 선택 금지)
  - Threshold(≥22)는 이전 연구에서 이미 사용된 값 그대로 사용 (사후 튜닝 금지)
  - in-sample RRS panel (2016-2023), 방향성 참고용
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT    = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "database" / "research.db"

E_GATE_THR  = 22   # earnings_score ≥ 22 = gate 통과
R_FILTER_THR = 22  # risk_score ≥ 22 = filter 통과
TOP_PCT = 0.25


def load() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT
            fwd_year, ticker, fwd_alpha,
            normalized_score,
            value_score, earnings_score, risk_score,
            COALESCE(supply_score_real, supply_score) AS supply_score
        FROM rrs_stock_year
        WHERE fwd_alpha IS NOT NULL
    """, conn)
    conn.close()
    return df


def select_top(df: pd.DataFrame, score_col: str,
               mask: pd.Series | None = None) -> pd.Series:
    """연도별 gate/filter 통과 후 상위 TOP_PCT% 선택."""
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
        if len(s) == 0:
            continue
        rows[yr] = round(float(s.median()), 2)
    return pd.Series(rows)


def yearly_p10(df: pd.DataFrame, mask: pd.Series) -> pd.Series:
    rows = {}
    for yr, grp in df.groupby("fwd_year"):
        s = grp[mask.loc[grp.index]]["fwd_alpha"]
        if len(s) == 0:
            continue
        rows[yr] = round(float(np.percentile(s, 10)), 2)
    return pd.Series(rows)


def main() -> None:
    SEP = "=" * 100

    df = load()
    print(f"패널: {len(df)}건 / fwd_year {sorted(df['fwd_year'].unique())}")

    # 파생
    df["score_ver"] = df["value_score"] + df["earnings_score"] + df["risk_score"]
    e_gate   = df["earnings_score"] >= E_GATE_THR
    r_filter = df["risk_score"] >= R_FILTER_THR
    er_both  = e_gate & r_filter

    print(f"\nGate/Filter 통과율 (전체 {len(df)}건):")
    print(f"  E Gate(≥{E_GATE_THR}):      {e_gate.sum()}건  ({e_gate.mean()*100:.1f}%)")
    print(f"  R Filter(≥{R_FILTER_THR}):  {r_filter.sum()}건  ({r_filter.mean()*100:.1f}%)")
    print(f"  E+R 교집합:         {er_both.sum()}건  ({er_both.mean()*100:.1f}%)")

    # Architecture 선택 마스크
    arch = {
        "A. 현재 선형 (V+E+R+S)":   select_top(df, "normalized_score"),
        "B. V+E+R 선형 (no Supply)": select_top(df, "score_ver"),
        "C. 역할분리 (E+R gate, V)": select_top(df, "value_score", er_both),
        "D. E Gate only + V":        select_top(df, "value_score", e_gate),
        "E. R Filter only + V":      select_top(df, "value_score", r_filter),
    }

    n_per_year = {
        k: df[m].groupby(df["fwd_year"]).size().mean()
        for k, m in arch.items()
    }

    # ── 전체 통계 테이블 ─────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("Architecture별 alpha 분포 — 전기간 합산")
    print("PRIMARY: median / tmean    보조: mean (lottery 왜곡 주의)")
    print(SEP)
    print(f"  {'Architecture':<32}  {'n/yr':>5}  {'mean':>7}  {'median':>7}  "
          f"{'tmean':>7}  {'P10':>7}  {'P25':>7}  {'hit%':>6}  {'dd':>6}")
    print("  " + "-" * 92)

    stats_dict = {}
    for lbl, mask in arch.items():
        alpha = df[mask]["fwd_alpha"]
        st = robust_block(alpha)
        stats_dict[lbl] = st
        if not st:
            print(f"  {lbl:<32}  표본부족")
            continue
        print(f"  {lbl:<32}  {n_per_year[lbl]:>5.0f}  "
              f"{st['mean']:>+7.2f}  {st['median']:>+7.2f}  "
              f"{st['tmean']:>+7.2f}  {st['P10']:>+7.2f}  "
              f"{st['P25']:>+7.2f}  {st['hit%']:>5.1f}%  {st['dd']:>6.2f}")

    # ── 연도별 median ─────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("연도별 median alpha")
    print(SEP)
    ym = {lbl: yearly_median(df, mask) for lbl, mask in arch.items()}
    labels = list(arch.keys())
    years  = sorted(df["fwd_year"].unique())
    print(f"  {'Year':>6}  " + "  ".join(f"{l[:14]:>14}" for l in labels))
    print("  " + "-" * 85)
    for yr in years:
        row = f"  {yr:>6}  "
        for lbl in labels:
            v = ym[lbl].get(yr, float("nan"))
            row += f"  {v:>+14.2f}"
        print(row)

    # ── 연도별 P10 ───────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("연도별 P10 alpha (downside tail)")
    print(SEP)
    yp = {lbl: yearly_p10(df, mask) for lbl, mask in arch.items()}
    print(f"  {'Year':>6}  " + "  ".join(f"{l[:14]:>14}" for l in labels))
    print("  " + "-" * 85)
    for yr in years:
        row = f"  {yr:>6}  "
        for lbl in labels:
            v = yp[lbl].get(yr, float("nan"))
            row += f"  {v:>+14.2f}"
        print(row)

    # ── A vs 각 구조 paired t-test (연도별 median 차이) ──────────────────
    print(f"\n{SEP}")
    print("A(현재 선형) vs 각 Architecture — 연도별 median 차이 t-test")
    print(SEP)
    a_ym = ym["A. 현재 선형 (V+E+R+S)"]
    for lbl in labels[1:]:
        b_ym = ym[lbl]
        common = a_ym.index.intersection(b_ym.index)
        if len(common) < 4:
            print(f"  A vs {lbl[:28]}: 연도 부족")
            continue
        diffs = (b_ym - a_ym).loc[common]
        t, p = stats.ttest_1samp(diffs, 0)
        pos = (diffs > 0).sum()
        sig = "**" if p < 0.01 else ("*" if p < 0.05 else ("†" if p < 0.10 else "  "))
        print(f"  A vs {lbl[:28]:<28}  "
              f"diff median={diffs.mean():>+6.2f}  t={t:>+5.2f}  p={p:.3f}{sig}  "
              f"B>A {pos}/{len(common)}년")

    # ── C vs D/E (역할 분리 내부 분해) ───────────────────────────────────
    print(f"\n{SEP}")
    print("역할 분리 내부 분해 — C vs D / C vs E")
    print("  C(E+R 모두) vs D(E만) = R Filter 기여도")
    print("  C(E+R 모두) vs E(R만) = E Gate 기여도")
    print(SEP)
    c_ym = ym["C. 역할분리 (E+R gate, V)"]
    for lbl in ["D. E Gate only + V", "E. R Filter only + V"]:
        b_ym = ym[lbl]
        common = c_ym.index.intersection(b_ym.index)
        if len(common) < 4:
            continue
        diffs = (c_ym - b_ym).loc[common]   # C - X (양수면 C가 나음)
        t, p = stats.ttest_1samp(diffs, 0)
        pos = (diffs > 0).sum()
        sig = "**" if p < 0.01 else ("*" if p < 0.05 else ("†" if p < 0.10 else "  "))
        role = "R Filter 기여" if "E Gate" in lbl else "E Gate 기여"
        print(f"  C vs {lbl[:28]:<28}  ({role})  "
              f"C-X median={diffs.mean():>+6.2f}  p={p:.3f}{sig}  "
              f"C>X {pos}/{len(common)}년")

    # ── 판정 기준 ────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("판정 기준 및 해석")
    print(SEP)
    c_st = stats_dict.get("C. 역할분리 (E+R gate, V)", {})
    a_st = stats_dict.get("A. 현재 선형 (V+E+R+S)", {})
    print(f"""
  PRIMARY 판정: C(역할 분리)의 median/tmean이 A(현재 선형)보다 일관되게 나은가?
    → 기준: 연도별 median C>A 비율 ≥ 5/8 AND tmean 방향 일치

  SECONDARY 확인:
    → P10/P25가 C에서 개선 = downside 보호 강화
    → dd(downside deviation)가 C에서 감소 = 꼬리 위험 감소
    → hit rate 개선 = 일관성 상승

  현재 결과 요약:
    A median = {a_st.get('median', 'N/A')}  tmean = {a_st.get('tmean', 'N/A')}  P10 = {a_st.get('P10', 'N/A')}
    C median = {c_st.get('median', 'N/A')}  tmean = {c_st.get('tmean', 'N/A')}  P10 = {c_st.get('P10', 'N/A')}

  주의: in-sample RRS panel (2016-2023). 방향성 참고용.
        Engine Freeze 해제 전 production 변경 금지.
        잘 된 architecture만 선택하지 말고 5개 전부 기록.
""")


if __name__ == "__main__":
    main()
