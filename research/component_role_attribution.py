#!/usr/bin/env python3
"""
Component Role Attribution — 역할 설계 검증
============================================
사전등록: 2026-09-23  /  docs/RESEARCH.md

각 component를 5가지 역할별 KPI로 검증.
역할 정의:
  Ranker        → IC mean > +0.05
  Gate          → Q4 hit% − Q1 hit% > +10pp
  Risk Filter   → Q4 P10 − Q1 P10 > +2%p  (downside protection)
  Deterioration → |det − non_det median| > 1%p, det worse
  Context       → |IC_Bear − IC_Bull| > 0.05

신규 계산: Gate KPI, Risk Filter KPI
기존 결과 재사용: Ranker (component_attribution), Deterioration (component_deterioration)
"""
from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict
from datetime import date as dt
from pathlib import Path
from scipy import stats as scipy_stats

ROOT     = Path(__file__).resolve().parent.parent
FLOW_DB  = ROOT / "database" / "research.db"
OHLCV_DB = ROOT / "database" / "market_ohlcv.db"
KOSPI    = "1001"

COMPONENTS = ["earnings_score", "valuation_score", "risk_score", "supply_score"]
COMP_LABEL = {
    "earnings_score":  "Earnings",
    "valuation_score": "Valuation",
    "risk_score":      "Risk",
    "supply_score":    "Supply",
}

# 판정 기준 (사전등록, 변경 금지)
IC_THRESHOLD        = 0.05    # Ranker
GATE_HIT_DIFF       = 0.10    # Gate: Q4 hit% − Q1 hit% > 10pp
RISKFILTER_P10_DIFF = 0.02    # Risk Filter: Q4 P10 − Q1 P10 > 2%p
DET_THRESHOLD       = 0.01    # Deterioration: |diff| > 1%p
CONTEXT_REGIME_DIFF = 0.05    # Context: |IC_Bear − IC_Bull| > 0.05
MIN_N_DATE          = 5       # IC 계산 최소 n per date

# 기존 연구 결과 (component_attribution.py 결과, 재실행 대신 재사용)
EXISTING_IC = {
    "earnings_score":  0.032,
    "valuation_score": 0.142,
    "risk_score":      0.040,
    "supply_score":   -0.003,
}
EXISTING_IC_BEAR = {
    "earnings_score":  None,   # 샘플 작아 재계산 필요
    "valuation_score": None,
    "risk_score":      None,
    "supply_score":    None,
}


# ── 데이터 로드 ───────────────────────────────────────────────────────────────

def load_retro() -> list[dict]:
    with sqlite3.connect(FLOW_DB) as c:
        rows = c.execute("""
            SELECT signal_date, ticker,
                   earnings_score, valuation_score, risk_score, supply_score,
                   alpha_20d
            FROM retro_calibration
            WHERE alpha_20d IS NOT NULL
            ORDER BY signal_date, ticker
        """).fetchall()
    result = []
    for r in rows:
        sd, tk, earn, val, risk, sup, a20 = r
        if None in (earn, val, risk, sup):
            continue
        result.append({
            "date": sd, "ticker": tk,
            "earnings_score": earn, "valuation_score": val,
            "risk_score": risk, "supply_score": sup,
            "alpha_20d": a20 / 100.0,
        })
    return result


def load_kospi_regime() -> dict[str, str]:
    """signal_date → 'Bull' | 'Sideways' | 'Bear'"""
    with sqlite3.connect(OHLCV_DB) as c:
        rows = c.execute(
            "SELECT trade_date, close FROM market_ohlcv WHERE ticker=? ORDER BY trade_date",
            (KOSPI,)
        ).fetchall()
    dates  = [r[0] for r in rows]
    closes = [r[1] for r in rows]
    result = {}
    for i, d in enumerate(dates):
        if i < 60:
            continue
        past = closes[i - 60]
        if past:
            ret = closes[i] / past - 1
            result[d] = "Bull" if ret > 0.05 else ("Bear" if ret < -0.05 else "Sideways")
    return result


# ── Quartile 할당 ─────────────────────────────────────────────────────────────

def assign_quartiles(vals: list[float]) -> list[int | None]:
    n = len(vals)
    if n < 4:
        return [None] * n
    indexed = sorted(range(n), key=lambda i: vals[i])
    qs = [0] * n
    for rank, idx in enumerate(indexed):
        qs[idx] = min(int(rank / n * 4) + 1, 4)
    return qs


# ── Role KPI 계산 ─────────────────────────────────────────────────────────────

def compute_quartile_data(rows_by_date: dict, comp: str) -> dict[int, list[float]]:
    """Q1~Q4 → list of alpha_20d"""
    result: dict[int, list[float]] = {1: [], 2: [], 3: [], 4: []}
    for date, rows in rows_by_date.items():
        if len(rows) < MIN_N_DATE:
            continue
        scores = [r[comp] for r in rows]
        alphas = [r["alpha_20d"] for r in rows]
        qs = assign_quartiles(scores)
        for q, alpha in zip(qs, alphas):
            if q is not None:
                result[q].append(alpha)
    return result


def p10(vals: list[float]) -> float | None:
    if len(vals) < 10:
        return None
    return sorted(vals)[int(len(vals) * 0.10)]


def hit_rate(vals: list[float]) -> float | None:
    if not vals:
        return None
    return sum(1 for v in vals if v > 0) / len(vals)


def compute_ic_by_regime(
    rows_by_date: dict, regime_map: dict[str, str], comp: str
) -> dict[str, float | None]:
    ic_by_regime: dict[str, list[float]] = {"Bull": [], "Bear": [], "Sideways": []}
    for date, rows in sorted(rows_by_date.items()):
        regime = regime_map.get(date, "Unknown")
        if regime == "Unknown":
            continue
        if len(rows) < MIN_N_DATE:
            continue
        scores = [r[comp] for r in rows]
        alphas = [r["alpha_20d"] for r in rows]
        rho, _ = scipy_stats.spearmanr(scores, alphas)
        if rho == rho:
            ic_by_regime[regime].append(rho)
    return {
        regime: statistics.mean(ics) if ics else None
        for regime, ics in ic_by_regime.items()
    }


def compute_det(rows: list[dict], comp: str) -> tuple[list[float], list[float]]:
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_ticker[r["ticker"]].append(r)
    for v in by_ticker.values():
        v.sort(key=lambda x: x["date"])
    det, non_det = [], []
    for series in by_ticker.values():
        for i in range(1, len(series)):
            prev, curr = series[i - 1], series[i]
            delta = curr[comp] - prev[comp]
            if curr["alpha_20d"] is None:
                continue
            if delta < 0:
                det.append(curr["alpha_20d"])
            else:
                non_det.append(curr["alpha_20d"])
    return det, non_det


# ── 역할 판정 함수 ────────────────────────────────────────────────────────────

def role_ranker(comp: str) -> tuple[bool, str]:
    ic = EXISTING_IC[comp]
    passed = ic > IC_THRESHOLD
    return passed, f"IC={ic:>+.3f} ({'> +0.05 ✓' if passed else '≤ 0.05 ✗'})"


def role_gate(qa: dict[int, list[float]]) -> tuple[bool, str]:
    q1, q4 = qa[1], qa[4]
    if not q1 or not q4:
        return False, "데이터 없음"
    hr1 = hit_rate(q1)
    hr4 = hit_rate(q4)
    diff = hr4 - hr1
    passed = diff > GATE_HIT_DIFF
    return passed, (f"Q4 hit={hr4*100:.1f}% / Q1 hit={hr1*100:.1f}% / "
                    f"diff={diff*100:+.1f}pp ({'> 10pp ✓' if passed else '≤ 10pp ✗'})")


def role_risk_filter(qa: dict[int, list[float]]) -> tuple[bool, str]:
    q1, q4 = qa[1], qa[4]
    p10_q1 = p10(q1)
    p10_q4 = p10(q4)
    if p10_q1 is None or p10_q4 is None:
        return False, "n 부족"
    diff = p10_q4 - p10_q1
    passed = diff > RISKFILTER_P10_DIFF
    return passed, (f"Q4 P10={p10_q4*100:>+.2f}% / Q1 P10={p10_q1*100:>+.2f}% / "
                    f"diff={diff*100:+.2f}%p ({'> 2%p ✓' if passed else '≤ 2%p ✗'})")


def role_det(rows: list[dict], comp: str) -> tuple[bool, str]:
    det, non_det = compute_det(rows, comp)
    if not det or not non_det:
        return False, f"n 부족 (det={len(det)}, non_det={len(non_det)})"
    dm  = statistics.median(det)
    ndm = statistics.median(non_det)
    diff = dm - ndm
    passed = abs(diff) > DET_THRESHOLD and diff < 0  # det worse = negative
    return passed, (f"det_med={dm*100:>+.2f}% / non_det_med={ndm*100:>+.2f}% / "
                    f"diff={diff*100:>+.2f}%p "
                    f"({'✓ det worse' if diff < 0 and abs(diff) > DET_THRESHOLD else '✗'})")


def role_context(ic_regime: dict[str, float | None]) -> tuple[bool, str]:
    bull = ic_regime.get("Bull")
    bear = ic_regime.get("Bear")
    if bull is None or bear is None:
        return False, "레짐 데이터 부족"
    diff = abs(bear - bull)
    passed = diff > CONTEXT_REGIME_DIFF
    return passed, (f"IC_Bull={bull:>+.3f} / IC_Bear={bear:>+.3f} / "
                    f"|diff|={diff:.3f} ({'> 0.05 ✓' if passed else '≤ 0.05 ✗'})")


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 72)
    print("Component Role Attribution — 역할 설계 검증")
    print("사전등록: 2026-09-23")
    print("=" * 72)

    print("\n[1] 데이터 로딩...", flush=True)
    rows = load_retro()
    regime_map = load_kospi_regime()
    print(f"    retro_calibration: {len(rows):,}행")

    rows_by_date: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        rows_by_date[r["date"]].append(r)

    dates = sorted(rows_by_date)
    print(f"    signal_dates: {len(dates)}일  ({dates[0]} ~ {dates[-1]})")

    regime_counts = defaultdict(int)
    for d in dates:
        regime_counts[regime_map.get(d, "Unknown")] += 1
    print(f"    레짐: Bull={regime_counts['Bull']} / Bear={regime_counts['Bear']} / "
          f"Sideways={regime_counts['Sideways']}")

    # ── 역할별 KPI 계산 ──────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("[ 역할별 KPI 계산 ]")

    results: dict[str, dict[str, tuple[bool, str]]] = {}

    for comp in COMPONENTS:
        lbl = COMP_LABEL[comp]
        qa = compute_quartile_data(rows_by_date, comp)
        ic_regime = compute_ic_by_regime(rows_by_date, regime_map, comp)

        results[comp] = {
            "Ranker":      role_ranker(comp),
            "Gate":        role_gate(qa),
            "Risk Filter": role_risk_filter(qa),
            "Det":         role_det(rows, comp),
            "Context":     role_context(ic_regime),
        }

        print(f"\n  ── {lbl} ──")
        for role, (passed, detail) in results[comp].items():
            mark = "✅" if passed else "❌"
            print(f"    {role:<14} {mark}  {detail}")

    # ── 역할 매트릭스 요약 ─────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("[ 역할 매트릭스 요약 ]")
    roles = ["Ranker", "Gate", "Risk Filter", "Det", "Context"]
    header = f"  {'Component':<12}" + "".join(f"  {r:<13}" for r in roles)
    print(header)
    print("  " + "-" * (12 + 15 * len(roles)))
    for comp in COMPONENTS:
        lbl = COMP_LABEL[comp]
        row = f"  {lbl:<12}"
        for role in roles:
            passed, _ = results[comp][role]
            row += f"  {'✓':<13}" if passed else f"  {'—':<13}"
        print(row)

    # ── 가설 대비 결과 ────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("[ 사전 가설 vs 실제 결과 ]")
    hypotheses = {
        "valuation_score": {"Ranker": True, "Gate": True, "Risk Filter": False, "Det": True, "Context": False},
        "earnings_score":  {"Ranker": None, "Gate": True, "Risk Filter": False, "Det": None, "Context": False},
        "risk_score":      {"Ranker": None, "Gate": False, "Risk Filter": True, "Det": True, "Context": False},
        "supply_score":    {"Ranker": False, "Gate": False, "Risk Filter": False, "Det": False, "Context": True},
    }
    print(f"  ({'✓' if True else '—'}=가설 지지, H=가설 있음, ?=가설 불확실)")
    for comp in COMPONENTS:
        lbl = COMP_LABEL[comp]
        hyp = hypotheses[comp]
        print(f"\n  {lbl}:")
        for role in roles:
            passed, _ = results[comp][role]
            h = hyp.get(role)
            actual = "✓" if passed else "✗"
            if h is True:
                expected = "가설=✓"
                match = "✅ 일치" if passed else "❌ 불일치"
            elif h is False:
                expected = "가설=✗"
                match = "✅ 일치" if not passed else "⚠️ 가설 불일치"
            else:
                expected = "가설=?"
                match = "(무방향)"
            print(f"    {role:<14} 실제={actual}  {expected}  → {match}")

    # ── 종합 해석 ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("[ 종합 역할 배정 제안 ]")
    for comp in COMPONENTS:
        lbl = COMP_LABEL[comp]
        confirmed = [role for role, (passed, _) in results[comp].items() if passed]
        denied    = [role for role, (passed, _) in results[comp].items() if not passed]
        print(f"  {lbl}:")
        print(f"    확인된 역할: {', '.join(confirmed) if confirmed else '없음'}")
        print(f"    부재 역할:   {', '.join(denied) if denied else '없음'}")


if __name__ == "__main__":
    main()
