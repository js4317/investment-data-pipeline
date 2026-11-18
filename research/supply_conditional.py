#!/usr/bin/env python3
"""
Supply Conditional Attribution — Core Score 통제 후 Supply 조건부 신호 검증
===========================================================================
사전등록: 2026-09-23  /  docs/RESEARCH.md

가설: Core Score(earn+val+risk)를 통제한 뒤에도 Supply Q4 > Q1 alpha이면
      Supply는 독립적인 조건부 신호. 차이가 사라지면 SB selection artifact.

Core Score = earnings_score + valuation_score + risk_score (max 75)
Core strata: Low 40-54 / Mid 55-64 / High 65-75

판정:
  Mid + High에서 Q4−Q1 > +1%p 동시 → 조건부 신호 지지
  High에서 Q4−Q1 ≤ +1%p → SB selection artifact 강력 증거
"""
from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path
from scipy import stats as scipy_stats

ROOT    = Path(__file__).resolve().parent.parent
FLOW_DB = ROOT / "database" / "research.db"

# 사전등록 고정값
CORE_STRATA = [
    ("Low",  40, 55),
    ("Mid",  55, 65),
    ("High", 65, 76),   # 76은 max(75) 포함
]
DIFF_THRESHOLD = 0.01   # 1%p
MIN_N_CELL     = 30     # cell 최소 n


# ── 데이터 로드 ───────────────────────────────────────────────────────────────

def load_data() -> list[dict]:
    with sqlite3.connect(FLOW_DB) as c:
        rows = c.execute("""
            SELECT signal_date, ticker, grade,
                   earnings_score, valuation_score, risk_score, supply_score,
                   alpha_20d
            FROM retro_calibration
            WHERE alpha_20d IS NOT NULL
              AND earnings_score IS NOT NULL
              AND valuation_score IS NOT NULL
              AND risk_score IS NOT NULL
              AND supply_score IS NOT NULL
            ORDER BY signal_date, ticker
        """).fetchall()
    result = []
    for sd, tk, grade, earn, val, risk, sup, a20 in rows:
        core = earn + val + risk
        result.append({
            "date": sd, "ticker": tk, "grade": grade,
            "core": core,
            "supply": sup,
            "alpha_20d": a20 / 100.0,
        })
    return result


# ── Quartile 내 Supply 분류 ───────────────────────────────────────────────────

def assign_supply_quartile_in_stratum(rows: list[dict]) -> list[int | None]:
    """stratum 내 cross-sectional supply quartile 부여 (Q1=하위, Q4=상위)"""
    n = len(rows)
    if n < 4:
        return [None] * n
    indexed = sorted(range(n), key=lambda i: rows[i]["supply"])
    qs = [0] * n
    for rank, idx in enumerate(indexed):
        qs[idx] = min(int(rank / n * 4) + 1, 4)
    return qs


def supply_quartile_within_date_stratum(data: list[dict]) -> list[dict]:
    """signal_date × stratum 조합 내에서 supply quartile 할당"""
    # date × stratum group
    groups: dict[tuple, list[tuple]] = defaultdict(list)
    for i, r in enumerate(data):
        for name, lo, hi in CORE_STRATA:
            if lo <= r["core"] < hi:
                groups[(r["date"], name)].append((i, r))
                break

    result = [r.copy() for r in data]
    for (date, stratum_name), idx_rows in groups.items():
        idxs = [i for i, _ in idx_rows]
        rows_sub = [r for _, r in idx_rows]
        qs = assign_supply_quartile_in_stratum(rows_sub)
        for pos, (idx, _) in enumerate(idx_rows):
            result[idx]["supply_q_in_stratum"] = qs[pos]
            result[idx]["stratum"] = stratum_name

    # stratum 없는 행 처리
    for r in result:
        if "stratum" not in r:
            r["stratum"] = None
            r["supply_q_in_stratum"] = None

    return result


# ── 통계 ─────────────────────────────────────────────────────────────────────

def stats_block(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0}
    p10 = sorted(vals)[int(len(vals) * 0.10)]
    return {
        "n":      len(vals),
        "median": statistics.median(vals),
        "mean":   statistics.mean(vals),
        "hit":    sum(1 for v in vals if v > 0) / len(vals) * 100,
        "p10":    p10,
    }


def fmt_pct(v: float | None) -> str:
    if v is None:
        return "  N/A "
    return f"{v*100:>+6.2f}%"


def print_cell(label: str, s: dict, inconclusive: bool = False):
    if not s.get("n"):
        print(f"  {label:<22}  n=   0  —")
        return
    ic_note = "  Inconclusive(n<30)" if inconclusive and s["n"] < MIN_N_CELL else ""
    print(f"  {label:<22}  n={s['n']:>5}  med={fmt_pct(s['median'])}  "
          f"hit={s['hit']:>5.1f}%  P10={fmt_pct(s['p10'])}{ic_note}")


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 72)
    print("Supply Conditional Attribution — Core Score 통제")
    print("사전등록: 2026-09-23")
    print("Core = earn + val + risk (max 75)")
    print("Strata: Low 40-54 / Mid 55-64 / High 65-75")
    print(f"판정: Mid+High에서 Q4−Q1 > +{DIFF_THRESHOLD*100:.0f}%p 동시 → 조건부 신호 지지")
    print("=" * 72)

    print("\n[1] 데이터 로딩...", flush=True)
    data = load_data()
    print(f"    n={len(data):,}")

    print("\n[2] Core stratum × supply quartile 할당...", flush=True)
    data = supply_quartile_within_date_stratum(data)

    # 각 stratum 크기
    for name, lo, hi in CORE_STRATA:
        n_stratum = sum(1 for r in data if r.get("stratum") == name)
        print(f"    {name} ({lo}-{hi-1}): n={n_stratum:,}")

    # ── Stratum별 Supply Q4 vs Q1 ─────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("[ Supply Q4 vs Q1 — Core Stratum별 ]")

    stratum_diffs = {}
    for stratum_name, lo, hi in CORE_STRATA:
        rows_s = [r for r in data if r.get("stratum") == stratum_name]
        q1_alpha = [r["alpha_20d"] for r in rows_s if r.get("supply_q_in_stratum") == 1]
        q4_alpha = [r["alpha_20d"] for r in rows_s if r.get("supply_q_in_stratum") == 4]

        s_q1 = stats_block(q1_alpha)
        s_q4 = stats_block(q4_alpha)

        print(f"\n  ── {stratum_name} Core ({lo}-{hi-1}) ──")
        print_cell(f"Supply Q1 (하위25%)", s_q1, inconclusive=True)
        print_cell(f"Supply Q4 (상위25%)", s_q4, inconclusive=True)

        if s_q1.get("n") and s_q4.get("n"):
            diff = s_q4["median"] - s_q1["median"]
            flag = "✓" if diff > DIFF_THRESHOLD else "—"
            dir_s = "↑" if diff > 0 else "↓"
            print(f"  {'Q4 − Q1':<22}          {fmt_pct(diff)}                {flag} {dir_s}")
            stratum_diffs[stratum_name] = diff

        # Supply IC within stratum
        sup_vals  = [r["supply"] for r in rows_s]
        alp_vals  = [r["alpha_20d"] for r in rows_s]
        if len(sup_vals) > 10:
            rho, p = scipy_stats.spearmanr(sup_vals, alp_vals)
            print(f"  {'Stratum IC (supply)':<22}          rho={rho:>+.4f}  p={p:.4f}")

    # ── Grade별 Supply Q4 vs Q1 (참고) ────────────────────────────────────────
    print("\n" + "=" * 72)
    print("[ Supply Q4 vs Q1 — Grade별 (참고, 통제 없음) ]")
    for grade in ["strong_buy", "buy", "neutral", "avoid"]:
        rows_g = [r for r in data if r.get("grade") == grade and r.get("supply_q_in_stratum") is not None]
        q1 = [r["alpha_20d"] for r in rows_g if r["supply_q_in_stratum"] == 1]
        q4 = [r["alpha_20d"] for r in rows_g if r["supply_q_in_stratum"] == 4]
        if q1 and q4:
            diff = statistics.median(q4) - statistics.median(q1)
            flag = "✓" if diff > DIFF_THRESHOLD else "—"
            print(f"  {grade:<12}  Q4_med={fmt_pct(statistics.median(q4))}  "
                  f"Q1_med={fmt_pct(statistics.median(q1))}  "
                  f"diff={fmt_pct(diff)}  {flag}")

    # ── 2D 매트릭스 (Core × Supply) ──────────────────────────────────────────
    print("\n" + "=" * 72)
    print("[ Core × Supply 2D 매트릭스 — Median Alpha ]")
    header_label = "Core / Supply"
    print(f"  {header_label:>14}", end="")
    for q in [1, 2, 3, 4]:
        print(f"   Q{q}(sup)", end="")
    print()
    print("  " + "-" * 55)
    for stratum_name, lo, hi in CORE_STRATA:
        rows_s = [r for r in data if r.get("stratum") == stratum_name]
        print(f"  {stratum_name:<14}", end="")
        for q in [1, 2, 3, 4]:
            cell = [r["alpha_20d"] for r in rows_s if r.get("supply_q_in_stratum") == q]
            med = statistics.median(cell) * 100 if len(cell) >= 5 else float("nan")
            print(f"  {med:>+6.2f}%" if med == med else "     N/A", end="")
        print()

    # ── 종합 판정 ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("[ 종합 판정 ]")
    mid_diff  = stratum_diffs.get("Mid")
    high_diff = stratum_diffs.get("High")
    low_diff  = stratum_diffs.get("Low")

    print(f"  Low  Core Q4−Q1: {fmt_pct(low_diff)  if low_diff  is not None else '  N/A'}")
    print(f"  Mid  Core Q4−Q1: {fmt_pct(mid_diff)  if mid_diff  is not None else '  N/A'}")
    print(f"  High Core Q4−Q1: {fmt_pct(high_diff) if high_diff is not None else '  N/A'}")

    if mid_diff is not None and high_diff is not None:
        both_positive = mid_diff > DIFF_THRESHOLD and high_diff > DIFF_THRESHOLD
        high_only = high_diff > DIFF_THRESHOLD
        if both_positive:
            verdict = "✅ 조건부 신호 지지 — Mid + High 모두 Q4 > Q1 + 1%p"
        elif high_only:
            verdict = "⚠️ 부분 지지 — High Core에서만 확인 (Mid 미지지)"
        elif high_diff <= DIFF_THRESHOLD:
            verdict = "❌ SB selection artifact — High Core에서 Q4−Q1 ≤ 1%p"
        else:
            verdict = "❌ 조건부 신호 미지지"
        print(f"\n  판정: {verdict}")
        print()
        if high_diff is not None:
            if high_diff <= DIFF_THRESHOLD:
                print("  해석: SB selection artifact 강력 증거 (earn+val+risk 높아도 supply 무관)")
            else:
                print("  해석: Supply는 Core가 높은 종목 내에서 조건부 alpha 신호 가능성")
    else:
        print("\n  데이터 부족 — 판정 불가")


if __name__ == "__main__":
    main()
