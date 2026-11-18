#!/usr/bin/env python3
"""
Component Deterioration Effect — Q2
=====================================
사전등록: 2026-09-23  /  docs/RESEARCH.md

질문: component score가 직전 snapshot 대비 하락(Δ < 0)한 경우,
      이후 20D alpha가 비하락 경우보다 나쁜가?

정의 (고정):
  Δcomponent  = current_score - previous_score
  Deteriorated    = Δ < 0
  Non-deteriorated = Δ ≥ 0
  평가: current snapshot의 alpha_20d

Components: Earnings, Valuation, Risk, Supply (각각 독립 분석)
다중 통제 없음 — 1차 진단.
"""
from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict
from datetime import date as dt
from pathlib import Path

ROOT    = Path(__file__).resolve().parent.parent
FLOW_DB = ROOT / "database" / "research.db"

COMPONENTS = ["earnings_score", "valuation_score", "risk_score", "supply_score"]
COMP_LABEL = {
    "earnings_score":  "Earnings",
    "valuation_score": "Valuation",
    "risk_score":      "Risk",
    "supply_score":    "Supply",
}
DIFF_THRESHOLD = 0.01  # |det_median - non-det_median| > 1%p = 강함


# ── 데이터 로드 ───────────────────────────────────────────────────────────────

def load_retro() -> dict[str, list[dict]]:
    """ticker → sorted list of snapshot dicts"""
    with sqlite3.connect(FLOW_DB) as c:
        rows = c.execute("""
            SELECT ticker, signal_date,
                   earnings_score, valuation_score, risk_score, supply_score,
                   alpha_20d
            FROM retro_calibration
            ORDER BY ticker, signal_date
        """).fetchall()

    result: dict[str, list[dict]] = {}
    for ticker, sd, earn, val, risk, sup, a20 in rows:
        if a20 is None:
            continue
        result.setdefault(ticker, []).append({
            "date":             sd,
            "earnings_score":   earn,
            "valuation_score":  val,
            "risk_score":       risk,
            "supply_score":     sup,
            "alpha_20d":        a20 / 100.0,  # %p → decimal
        })
    return result


# ── Pair 생성 ─────────────────────────────────────────────────────────────────

def build_pairs(retro: dict[str, list[dict]]) -> list[dict]:
    """
    같은 ticker의 연속 snapshot pair를 생성.
    pair = {ticker, interval_days, alpha_20d(current), Δ per component}
    """
    pairs = []
    for ticker, series in retro.items():
        for i in range(1, len(series)):
            prev = series[i - 1]
            curr = series[i]
            # 두 snap 모두 component 값이 있어야 포함
            if any(prev.get(c) is None or curr.get(c) is None for c in COMPONENTS):
                continue
            interval = (dt.fromisoformat(curr["date"]) - dt.fromisoformat(prev["date"])).days
            pair = {
                "ticker":       ticker,
                "date":         curr["date"],
                "interval":     interval,
                "alpha_20d":    curr["alpha_20d"],
            }
            for comp in COMPONENTS:
                pair[f"delta_{comp}"] = curr[comp] - prev[comp]
            pairs.append(pair)
    return pairs


# ── 통계 ─────────────────────────────────────────────────────────────────────

def stats_block(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0}
    return {
        "n":      len(vals),
        "mean":   statistics.mean(vals),
        "median": statistics.median(vals),
        "hit":    sum(1 for v in vals if v > 0) / len(vals) * 100,
    }


def analyze_component(pairs: list[dict], comp: str) -> dict:
    det     = [p["alpha_20d"] for p in pairs if p[f"delta_{comp}"] <  0]
    non_det = [p["alpha_20d"] for p in pairs if p[f"delta_{comp}"] >= 0]
    return {"det": stats_block(det), "non_det": stats_block(non_det)}


def print_table(label: str, pairs: list[dict]):
    print(f"\n  [{label}]")
    header = (f"  {'Component':<12} {'det_n':>6} {'non_n':>6}  "
              f"{'det_med':>8} {'non_med':>8} {'diff':>8}  "
              f"{'det_hit%':>8} {'non_hit%':>8}  판정")
    print(header)
    print("  " + "-" * 84)
    for comp in COMPONENTS:
        r     = analyze_component(pairs, comp)
        det   = r["det"]
        nd    = r["non_det"]
        if not det.get("n") or not nd.get("n"):
            print(f"  {COMP_LABEL[comp]:<12}  데이터 없음")
            continue
        diff  = det["median"] - nd["median"]
        flag  = "✓" if abs(diff) > DIFF_THRESHOLD else "—"
        dir_s = "↓" if diff < 0 else "↑"  # ↓ = det worse (기대 방향)
        print(f"  {COMP_LABEL[comp]:<12} {det['n']:>6} {nd['n']:>6}  "
              f"{det['median']*100:>+7.2f}% {nd['median']*100:>+7.2f}% "
              f"{diff*100:>+7.2f}%  "
              f"{det['hit']:>7.1f}% {nd['hit']:>7.1f}%  {flag} {dir_s}")


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 68)
    print("Component Deterioration Effect — Q2")
    print("사전등록: 2026-09-23")
    print(f"  Deteriorated: Δ < 0  /  Non-det: Δ ≥ 0")
    print(f"  '강함' 기준: |det_med - non-det_med| > {DIFF_THRESHOLD*100:.0f}%p")
    print("  ↓ = det worse than non-det (기대 방향)")
    print("=" * 68)

    print("\n[1] 데이터 로딩...", flush=True)
    retro = load_retro()
    pairs = build_pairs(retro)
    print(f"    연속 pair 수: {len(pairs):,}")

    # Interval 분포
    intervals = [p["interval"] for p in pairs]
    iv_buckets = {1: 0, 3: 0, 5: 0, 10: 0, 99: 0}
    for v in intervals:
        if v <= 1:     iv_buckets[1]  += 1
        elif v <= 3:   iv_buckets[3]  += 1
        elif v <= 5:   iv_buckets[5]  += 1
        elif v <= 10:  iv_buckets[10] += 1
        else:          iv_buckets[99] += 1
    print(f"    Interval 분포: 1일={iv_buckets[1]:,} / 2-3일={iv_buckets[3]:,} / "
          f"4-5일={iv_buckets[5]:,} / 6-10일={iv_buckets[10]:,} / 10일+={iv_buckets[99]:,}")

    # ── 전체 기간 ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 68)
    print("[ Q2 — Component Deterioration vs Non-deterioration ]")
    print_table("전체 기간", pairs)

    # ── Interval별 분리 (참고) ────────────────────────────────────────────────
    print("\n[ Interval별 분리 (참고) ]")
    for label, lo, hi in [("1일 gap", 0, 1), ("2-3일 gap", 2, 3), ("4일+ gap", 4, 999)]:
        sub = [p for p in pairs if lo <= p["interval"] <= hi]
        if len(sub) > 50:
            print_table(f"{label} (n={len(sub)})", sub)

    # ── 2×2 분류 매트릭스 ─────────────────────────────────────────────────────
    print("\n" + "=" * 68)
    print("[ Q1/Q3 × Q2 분류 매트릭스 ]")
    print("  Q1/Q3 Level 결과 (이전 연구):")
    print("    Valuation: Q4-Q1 +1.54%p → Level 강함")
    print("    Risk:      Q4-Q1 -0.79%p → Level 약함")
    print("    Earnings:  Q4-Q1 -0.12%p → Level 약함")
    print("    Supply:    Q4-Q1 +0.23%p → Level 약함")
    print()
    print("  Q2 Deterioration 결과 (|diff| > 1%p 기준):")
    for comp in COMPONENTS:
        r    = analyze_component(pairs, comp)
        det  = r["det"]
        nd   = r["non_det"]
        if not det.get("n") or not nd.get("n"):
            continue
        diff = det["median"] - nd["median"]
        det_strong = abs(diff) > DIFF_THRESHOLD
        det_label  = f"강함 ({diff*100:+.2f}%p)" if det_strong else f"약함 ({diff*100:+.2f}%p)"
        print(f"    {COMP_LABEL[comp]}: Deterioration {det_label}")

    print()
    print("  2×2 매트릭스 해석 (아래 확인 후 채워넣기):")
    print("  A = Level↑ + Det↑ → 핵심 component")
    print("  B = Level↑ + Det↓ → Ranker 전용")
    print("  C = Level↓ + Det↑ → Exit/Warning signal 후보")
    print("  D = Level↓ + Det↓ → 역할 재검토 후보")


if __name__ == "__main__":
    main()
