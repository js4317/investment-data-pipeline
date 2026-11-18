#!/usr/bin/env python3
"""
Annual PIT Component Attribution — rrs_stock_year 기반
=======================================================
사전등록: 2026-09-23  /  docs/RESEARCH.md

질문: 연말 PIT component score가 다음 연도 fwd_alpha를 예측했는가?
데이터: rrs_stock_year (risk_year 2016~2024, fwd_alpha = 다음 연도 alpha)

⚠️ 데이터 품질 제한 (실행 전 확인됨):
  earnings_approx = 100% → Earnings score 전량 근사값 (PIT 아님)
  supply_approx   = 100% → Supply score 전량 근사값
  risk_approx     = 26.5% → Risk score 73.5%는 실제 데이터
  value_approx    =  1.6% → Value score 98.4%는 실제 데이터

따라서:
  Value, Risk(73.5% real): 연간 PIT attribution 가능
  Earnings, Supply: approx 비율 100% → 해석 제한 (20D 문제와 동일 구조)
"""
from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path
from scipy import stats as scipy_stats

ROOT    = Path(__file__).resolve().parent.parent
FLOW_DB = ROOT / "database" / "research.db"

COMPONENTS = ["value_score", "earnings_score", "risk_score", "supply_score"]
COMP_LABEL = {
    "value_score":    "Value",
    "earnings_score": "Earnings",
    "risk_score":     "Risk",
    "supply_score":   "Supply",
}
APPROX_FLAG = {
    "value_score":    "value_approx",
    "earnings_score": "earnings_approx",
    "risk_score":     "risk_approx",
    "supply_score":   "supply_approx",
}
IC_THRESHOLD  = 0.03
Q4Q1_THRESHOLD = 0.02   # 연간 alpha 기준 2%p


# ── 데이터 로드 ───────────────────────────────────────────────────────────────

def load_rrs() -> list[dict]:
    with sqlite3.connect(FLOW_DB) as c:
        rows = c.execute("""
            SELECT risk_year, ticker,
                   value_score, earnings_score, risk_score, supply_score,
                   fwd_alpha,
                   value_approx, earnings_approx, risk_approx, supply_approx
            FROM rrs_stock_year
            WHERE fwd_alpha IS NOT NULL
            ORDER BY risk_year, ticker
        """).fetchall()
    result = []
    for r in rows:
        (yr, ticker, val, earn, risk, sup, fwd,
         va, ea, ra, sa) = r
        if None in (val, earn, risk, sup):
            continue
        result.append({
            "year":           yr,
            "ticker":         ticker,
            "value_score":    val,
            "earnings_score": earn,
            "risk_score":     risk,
            "supply_score":   sup,
            "fwd_alpha":      fwd / 100.0,   # % → decimal
            "value_approx":   va,
            "earnings_approx":ea,
            "risk_approx":    ra,
            "supply_approx":  sa,
        })
    return result


# ── IC 계산 ───────────────────────────────────────────────────────────────────

def compute_annual_ic(
    rows_by_year: dict[int, list[dict]], comp: str
) -> dict[int, float]:
    """risk_year → cross-sectional Spearman IC"""
    ics = {}
    for year, rows in sorted(rows_by_year.items()):
        if len(rows) < 5:
            continue
        scores = [r[comp] for r in rows]
        alphas = [r["fwd_alpha"] for r in rows]
        rho, _ = scipy_stats.spearmanr(scores, alphas)
        if rho == rho:
            ics[year] = rho
    return ics


# ── Quartile ──────────────────────────────────────────────────────────────────

def assign_quartile(vals: list[float]) -> list[int | None]:
    n = len(vals)
    if n < 4:
        return [None] * n
    indexed = sorted(range(n), key=lambda i: vals[i])
    qs = [0] * n
    for rank, idx in enumerate(indexed):
        qs[idx] = min(int(rank / n * 4) + 1, 4)
    return qs


def compute_quartile_alphas(
    rows_by_year: dict[int, list[dict]], comp: str
) -> dict[int, list[float]]:
    result: dict[int, list[float]] = {1: [], 2: [], 3: [], 4: []}
    for year, rows in rows_by_year.items():
        if len(rows) < 5:
            continue
        scores = [r[comp] for r in rows]
        alphas = [r["fwd_alpha"] for r in rows]
        qs = assign_quartile(scores)
        for q, alpha in zip(qs, alphas):
            if q is not None:
                result[q].append(alpha)
    return result


# ── Deterioration (YoY) ──────────────────────────────────────────────────────

def compute_deterioration(rows: list[dict], comp: str) -> tuple[list[float], list[float]]:
    """연도별 YoY Δ < 0 vs Δ ≥ 0의 fwd_alpha 분리"""
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_ticker[r["ticker"]].append(r)
    for v in by_ticker.values():
        v.sort(key=lambda x: x["year"])

    det, non_det = [], []
    for series in by_ticker.values():
        for i in range(1, len(series)):
            prev = series[i - 1]
            curr = series[i]
            if curr["year"] != prev["year"] + 1:
                continue   # 연속 연도만
            delta = curr[comp] - prev[comp]
            alpha = curr["fwd_alpha"]
            if delta < 0:
                det.append(alpha)
            else:
                non_det.append(alpha)
    return det, non_det


# ── 출력 ─────────────────────────────────────────────────────────────────────

def fmt_pct(v: float) -> str:
    return f"{v*100:+.2f}%"


def print_ic_table(ics_by_comp: dict[str, dict[int, float]]):
    print(f"  {'Component':<12} {'mean_IC':>8} {'med_IC':>8} {'pos%':>6} {'n_yr':>6}  판정")
    print("  " + "-" * 55)
    for comp in COMPONENTS:
        ics = ics_by_comp.get(comp, {})
        vals = list(ics.values())
        if not vals:
            print(f"  {COMP_LABEL[comp]:<12}  데이터 없음")
            continue
        mean   = statistics.mean(vals)
        median = statistics.median(vals)
        pos    = sum(1 for v in vals if v > 0) / len(vals) * 100
        flag   = "✓" if abs(mean) > IC_THRESHOLD else "—"
        dir_s  = "↑" if mean > 0 else "↓"
        print(f"  {COMP_LABEL[comp]:<12} {mean:>+8.4f} {median:>+8.4f} {pos:>5.1f}% {len(vals):>5}  {flag} {dir_s}")


def print_q4q1_table(rows_by_year: dict[int, list[dict]]):
    print(f"  {'Component':<12} {'Q1 med':>8} {'Q4 med':>8} {'Q4-Q1':>8} {'Q4 hit':>7} {'Q1 hit':>7}  판정")
    print("  " + "-" * 66)
    for comp in COMPONENTS:
        qa = compute_quartile_alphas(rows_by_year, comp)
        q1, q4 = qa[1], qa[4]
        if not q1 or not q4:
            continue
        q1m, q4m = statistics.median(q1), statistics.median(q4)
        diff = q4m - q1m
        q4h  = sum(1 for v in q4 if v > 0) / len(q4) * 100
        q1h  = sum(1 for v in q1 if v > 0) / len(q1) * 100
        flag = "✓" if abs(diff) > Q4Q1_THRESHOLD else "—"
        dir_s = "↑" if diff > 0 else "↓"
        print(f"  {COMP_LABEL[comp]:<12} {fmt_pct(q1m):>8} {fmt_pct(q4m):>8} "
              f"{fmt_pct(diff):>8} {q4h:>6.1f}% {q1h:>6.1f}%  {flag} {dir_s}")


def print_det_table(rows: list[dict]):
    print(f"  {'Component':<12} {'det_n':>6} {'non_n':>6} {'det_med':>9} {'non_med':>9} {'diff':>8}  판정")
    print("  " + "-" * 65)
    for comp in COMPONENTS:
        det, non_det = compute_deterioration(rows, comp)
        if not det or not non_det:
            print(f"  {COMP_LABEL[comp]:<12}  이벤트 없음")
            continue
        dm  = statistics.median(det)
        ndm = statistics.median(non_det)
        diff = dm - ndm
        flag = "✓" if abs(diff) > Q4Q1_THRESHOLD else "—"
        dir_s = "↓" if diff < 0 else "↑"
        print(f"  {COMP_LABEL[comp]:<12} {len(det):>6} {len(non_det):>6} "
              f"{fmt_pct(dm):>9} {fmt_pct(ndm):>9} {fmt_pct(diff):>8}  {flag} {dir_s}")


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 68)
    print("Annual PIT Component Attribution — rrs_stock_year")
    print("사전등록: 2026-09-23")
    print(f"  IC 임계값: |mean_IC| > {IC_THRESHOLD}")
    print(f"  Q4-Q1 / Det 임계값: > {Q4Q1_THRESHOLD*100:.0f}%p (연간 alpha 기준)")
    print("=" * 68)

    print("\n[1] 데이터 로딩...", flush=True)
    rows = load_rrs()
    print(f"    총 행 수: {len(rows):,}")

    # approx 비율
    total = len(rows)
    for comp in COMPONENTS:
        flag_col = APPROX_FLAG[comp]
        n_approx = sum(1 for r in rows if r[flag_col] == 1)
        pct = n_approx / total * 100 if total else 0
        pit_note = "⚠️ 전량 근사값 — PIT 아님" if pct > 90 else (
                   f"⚠️ {pct:.0f}% 근사값" if pct > 10 else "✓ PIT")
        print(f"    {COMP_LABEL[comp]:<10} approx={n_approx:,} ({pct:.1f}%)  {pit_note}")

    rows_by_year: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        rows_by_year[r["year"]].append(r)

    years = sorted(rows_by_year)
    tickers = {r["ticker"] for r in rows}
    print(f"    risk_years: {years[0]}~{years[-1]}  ({len(years)}년)")
    print(f"    tickers: {len(tickers):,}")

    # ── IC ──────────────────────────────────────────────────────────────────
    print("\n" + "=" * 68)
    print("[ Q1 — Annual Cross-sectional IC ]")
    print("  (↑=기대 방향, 전 component 양수 IC 예상)")
    ics_by_comp = {comp: compute_annual_ic(rows_by_year, comp) for comp in COMPONENTS}
    print_ic_table(ics_by_comp)

    # 연도별 IC
    print(f"\n  연도별 IC:")
    print(f"  {'Year':>5} " + "  ".join(f"{COMP_LABEL[c]:>10}" for c in COMPONENTS))
    print("  " + "-" * 55)
    for yr in years:
        vals = "  ".join(
            f"{ics_by_comp[c].get(yr, float('nan')):>+10.4f}"
            if yr in ics_by_comp[c] else f"{'N/A':>10}"
            for c in COMPONENTS
        )
        print(f"  {yr:>5}  {vals}")

    # ── Quartile ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 68)
    print("[ Q3 — Annual Score Level Quartile ]")
    print_q4q1_table(rows_by_year)

    # ── Deterioration (YoY) ──────────────────────────────────────────────────
    print("\n" + "=" * 68)
    print("[ Q2 — Annual Deterioration (YoY Δ < 0 vs Δ ≥ 0) ]")
    print("  ↓ = det worse than non-det (기대 방향)")
    print_det_table(rows)

    # ── 20D vs Annual 비교 요약 ───────────────────────────────────────────────
    print("\n" + "=" * 68)
    print("[ 20D vs Annual 비교 요약 ]")
    print("  (20D 결과는 이전 연구, research/component_attribution.py 기준)")
    print()
    print(f"  {'Component':<12} {'20D IC':>8} {'1Y IC':>8}  패턴")
    print("  " + "-" * 42)
    ic_20d = {"value_score": 0.142, "earnings_score": 0.032,
              "risk_score": 0.040, "supply_score": -0.003}
    for comp in COMPONENTS:
        ic_ann_vals = list(ics_by_comp[comp].values())
        ic_ann = statistics.mean(ic_ann_vals) if ic_ann_vals else float("nan")
        ic20   = ic_20d[comp]
        if ic20 > IC_THRESHOLD and ic_ann > IC_THRESHOLD:
            pattern = "양 축 모두 유효"
        elif ic20 > IC_THRESHOLD and ic_ann <= IC_THRESHOLD:
            pattern = "단기(20D)만 유효"
        elif ic20 <= IC_THRESHOLD and ic_ann > IC_THRESHOLD:
            pattern = "연간만 유효"
        else:
            pattern = "양 축 모두 약함"
        print(f"  {COMP_LABEL[comp]:<12} {ic20:>+8.4f} {ic_ann:>+8.4f}  {pattern}")

    print()
    print("  ⚠️ Earnings/Supply는 rrs_stock_year에서도 approx 100% — 비교 참고 수준")


if __name__ == "__main__":
    main()
