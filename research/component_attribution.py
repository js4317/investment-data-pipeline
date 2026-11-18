#!/usr/bin/env python3
"""
Component Attribution — IC 및 Score Level 분해
================================================
사전등록: 2026-09-23  /  docs/RESEARCH.md

Q1 (Primary)  : component score vs alpha_20d 일별 cross-sectional Spearman IC
Q3 (Secondary): 각 signal_date 내 component quartile별 alpha 분포 (Q4-Q1 spread)

레짐 프록시: KOSPI trailing 60D return on signal_date
  Bull     > +5%
  Bear     < -5%
  Sideways  -5% ~ +5%

판정 기준 (사전등록):
  IC 의미 있음: |mean_IC| > 0.03
  랭킹 효과 있음: |Q4-Q1 median| > 1%p
  이론적 기대 방향: Earnings/Valuation/Risk/Supply 모두 양수 IC
"""
from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict
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
MIN_STOCKS_PER_DATE = 5
IC_THRESHOLD        = 0.03   # |mean_IC| 기준
Q4Q1_THRESHOLD      = 0.01   # |Q4-Q1 median| 기준 (1%p)
REGIME_60D_BULL     = 0.05
REGIME_60D_BEAR     = -0.05


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
    return [
        {
            "date": r[0], "ticker": r[1],
            "earnings_score": r[2], "valuation_score": r[3],
            "risk_score": r[4], "supply_score": r[5],
            "alpha_20d": r[6] / 100.0,  # %p → decimal
        }
        for r in rows
        if all(r[i] is not None for i in range(2, 7))
    ]


def load_kospi_60d_returns() -> dict[str, float]:
    """signal_date → KOSPI trailing 60D return (해당일 기준)"""
    with sqlite3.connect(OHLCV_DB) as c:
        rows = c.execute("""
            SELECT trade_date, close FROM market_ohlcv
            WHERE ticker = ? ORDER BY trade_date
        """, (KOSPI,)).fetchall()

    dates  = [r[0] for r in rows]
    closes = [r[1] for r in rows]
    date_to_idx = {d: i for i, d in enumerate(dates)}

    result = {}
    for i, d in enumerate(dates):
        if i < 60:
            continue
        past_close = closes[i - 60]
        if past_close:
            result[d] = closes[i] / past_close - 1
    return result


def classify_regime(ret: float) -> str:
    if ret > REGIME_60D_BULL:
        return "Bull"
    if ret < REGIME_60D_BEAR:
        return "Bear"
    return "Sideways"


# ── Q1: Component IC ──────────────────────────────────────────────────────────

def compute_ic(rows_by_date: dict[str, list[dict]], comp: str) -> list[float]:
    """날짜별 cross-sectional Spearman IC → list of daily IC values"""
    ics = []
    for date, rows in sorted(rows_by_date.items()):
        valid = [r for r in rows if r[comp] is not None]
        if len(valid) < MIN_STOCKS_PER_DATE:
            continue
        scores = [r[comp] for r in valid]
        alphas = [r["alpha_20d"] for r in valid]
        rho, _ = scipy_stats.spearmanr(scores, alphas)
        if rho == rho:  # NaN 제외
            ics.append(rho)
    return ics


def ic_stats(ics: list[float]) -> dict:
    if not ics:
        return {}
    return {
        "mean":     statistics.mean(ics),
        "median":   statistics.median(ics),
        "pos_pct":  sum(1 for v in ics if v > 0) / len(ics) * 100,
        "n_days":   len(ics),
    }


# ── Q3: Component Quartile ────────────────────────────────────────────────────

def assign_quartile(vals: list[float]) -> list[int]:
    """각 값에 1~4 quartile 번호 부여 (날짜 내 cross-sectional)"""
    n = len(vals)
    if n < 4:
        return [None] * n
    indexed = sorted(range(n), key=lambda i: vals[i])
    qs = [0] * n
    for rank, idx in enumerate(indexed):
        q = min(int(rank / n * 4) + 1, 4)
        qs[idx] = q
    return qs


def compute_quartile_alphas(
    rows_by_date: dict[str, list[dict]], comp: str
) -> dict[int, list[float]]:
    """quartile (1~4) → list of alpha_20d"""
    result: dict[int, list[float]] = {1: [], 2: [], 3: [], 4: []}
    for date, rows in rows_by_date.items():
        valid = [r for r in rows if r[comp] is not None]
        if len(valid) < MIN_STOCKS_PER_DATE:
            continue
        scores = [r[comp] for r in valid]
        alphas = [r["alpha_20d"] for r in valid]
        qs = assign_quartile(scores)
        for q, alpha in zip(qs, alphas):
            if q is not None:
                result[q].append(alpha)
    return result


# ── 출력 ─────────────────────────────────────────────────────────────────────

def print_ic_table(label: str, rows_by_date: dict[str, list[dict]]):
    print(f"\n  [{label}]")
    header = f"  {'Component':<12} {'mean_IC':>8} {'median_IC':>10} {'pos_IC%':>8} {'n_days':>7}  판정"
    print(header)
    print("  " + "-" * 65)
    for comp in COMPONENTS:
        ics = compute_ic(rows_by_date, comp)
        s = ic_stats(ics)
        if not s:
            print(f"  {COMP_LABEL[comp]:<12}  데이터 없음")
            continue
        flag = "✓" if abs(s["mean"]) > IC_THRESHOLD else "—"
        dir_ok = "↑" if s["mean"] > 0 else "↓"
        print(f"  {COMP_LABEL[comp]:<12} {s['mean']:>+8.4f} {s['median']:>+10.4f} "
              f"{s['pos_pct']:>7.1f}% {s['n_days']:>7}  {flag} {dir_ok}")


def print_quartile_table(label: str, rows_by_date: dict[str, list[dict]]):
    print(f"\n  [{label}]")
    header = (f"  {'Component':<12} {'Q1 med':>8} {'Q4 med':>8} "
              f"{'Q4-Q1':>8} {'Q4 hit%':>8} {'Q1 hit%':>8}  판정")
    print(header)
    print("  " + "-" * 72)
    for comp in COMPONENTS:
        qa = compute_quartile_alphas(rows_by_date, comp)
        q1, q4 = qa[1], qa[4]
        if not q1 or not q4:
            print(f"  {COMP_LABEL[comp]:<12}  데이터 없음")
            continue
        q1m = statistics.median(q1)
        q4m = statistics.median(q4)
        diff = q4m - q1m
        q4h  = sum(1 for v in q4 if v > 0) / len(q4) * 100
        q1h  = sum(1 for v in q1 if v > 0) / len(q1) * 100
        flag = "✓" if abs(diff) > Q4Q1_THRESHOLD else "—"
        dir_ok = "↑" if diff > 0 else "↓"
        print(f"  {COMP_LABEL[comp]:<12} {q1m*100:>+7.2f}% {q4m*100:>+7.2f}% "
              f"{diff*100:>+7.2f}% {q4h:>7.1f}% {q1h:>7.1f}%  {flag} {dir_ok}")


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 68)
    print("Component Attribution — IC 및 Score Level 분해")
    print("사전등록: 2026-09-23  /  Q1(Primary) + Q3(Secondary)")
    print(f"  IC 임계값: |mean_IC| > {IC_THRESHOLD}")
    print(f"  Q4-Q1 임계값: |median| > {Q4Q1_THRESHOLD*100:.0f}%p")
    print("=" * 68)

    print("\n[1] 데이터 로딩...", flush=True)
    rows = load_retro()
    kospi_ret = load_kospi_60d_returns()
    print(f"    retro_calibration: {len(rows):,}행")

    # 날짜별 그룹
    rows_by_date: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        rows_by_date[r["date"]].append(r)

    dates = sorted(rows_by_date)
    print(f"    signal_dates: {len(dates)}일  ({dates[0]} ~ {dates[-1]})")

    # 레짐 분류
    regime_dates: dict[str, list[str]] = {"Bull": [], "Bear": [], "Sideways": [], "Unknown": []}
    for d in dates:
        ret = kospi_ret.get(d)
        if ret is None:
            regime_dates["Unknown"].append(d)
        else:
            regime_dates[classify_regime(ret)].append(d)
    print(f"    레짐: Bull={len(regime_dates['Bull'])}일 / "
          f"Bear={len(regime_dates['Bear'])}일 / "
          f"Sideways={len(regime_dates['Sideways'])}일 / "
          f"Unknown={len(regime_dates['Unknown'])}일")

    # ── Q1: IC ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 68)
    print("[ Q1 — Component IC (Primary) ]")
    print("  이론적 기대 방향: 전 component 양수 IC")

    # 전체
    print_ic_table("전체 기간", rows_by_date)

    # 레짐별
    for regime in ["Bull", "Sideways", "Bear"]:
        rdates = set(regime_dates[regime])
        sub = {d: v for d, v in rows_by_date.items() if d in rdates}
        if sub:
            print_ic_table(f"레짐={regime} (n={len(sub)}일)", sub)

    # ── Q3: Quartile ─────────────────────────────────────────────────────────
    print("\n" + "=" * 68)
    print("[ Q3 — Component Score Level 분해 (Secondary) ]")
    print("  Q4=상위25%, Q1=하위25% (각 signal_date 내 cross-sectional)")

    print_quartile_table("전체 기간", rows_by_date)

    for regime in ["Bull", "Sideways", "Bear"]:
        rdates = set(regime_dates[regime])
        sub = {d: v for d, v in rows_by_date.items() if d in rdates}
        if sub:
            print_quartile_table(f"레짐={regime} (n={len(sub)}일)", sub)

    # ── 판정 요약 ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 68)
    print("[ 판정 요약 ]")
    print(f"  IC 의미 있음: |mean_IC| > {IC_THRESHOLD} (✓ 표시)")
    print(f"  Q4-Q1 효과 있음: |median diff| > {Q4Q1_THRESHOLD*100:.0f}%p (✓ 표시)")
    print("  이론적 기대 방향 일치 여부: ↑=양수(기대), ↓=음수(반기대)")


if __name__ == "__main__":
    main()
