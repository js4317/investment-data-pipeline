#!/usr/bin/env python3
"""
Supply Counterfactual — supply_score 제거 시 grade 변화 및 alpha 영향
====================================================================
사전등록: 2026-09-23  /  docs/RESEARCH.md

가설: supply_score는 ranking/gate/filter 어떤 역할도 하지 않으므로,
      제거 후 재정규화했을 때 STRONG_BUY 코호트의 alpha가 유지되거나 개선된다.

방법:
  no_supply_norm = round((earn + val + risk) / 75 * 100), capped 0~100
  no_supply_grade = STRONG_BUY ≥85 / BUY ≥70 / NEUTRAL ≥55 / else AVOID

비교 그룹:
  Stable SB   : 원래 SB + no-supply SB     (supply 무관 종목)
  Promoted SB : 원래 BUY/하위 → no-supply SB  (supply가 억누른 종목)
  Demoted SB  : 원래 SB → no-supply 하위     (supply가 끌어올린 종목)

Primary 판정:
  Promoted SB median alpha > Demoted SB median alpha
  → supply가 좋은 종목을 억누르고 있었다 (supply가 noise/왜곡 역할)
"""
from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path
from scipy import stats as scipy_stats

ROOT    = Path(__file__).resolve().parent.parent
FLOW_DB = ROOT / "database" / "research.db"

SB_THRESHOLD      = 85
BUY_THRESHOLD     = 70
NEUTRAL_THRESHOLD = 55
SUPPLY_MAX        = 25
OTHERS_MAX        = 75   # earn(25) + val(25) + risk(25)

PRIMARY_DIFF_THRESHOLD = 0.005  # Promoted - Demoted > 0.5%p = 의미 있음


# ── 수식 ─────────────────────────────────────────────────────────────────────

def no_supply_norm(earn: float, val: float, risk: float) -> int:
    raw = earn + val + risk
    return max(0, min(round(raw / OTHERS_MAX * 100), 100))


def assign_grade(score: int) -> str:
    if score >= SB_THRESHOLD:
        return "strong_buy"
    if score >= BUY_THRESHOLD:
        return "buy"
    if score >= NEUTRAL_THRESHOLD:
        return "neutral"
    return "avoid"


# ── 데이터 로드 ───────────────────────────────────────────────────────────────

def load_data() -> list[dict]:
    with sqlite3.connect(FLOW_DB) as c:
        rows = c.execute("""
            SELECT signal_date, ticker, grade, normalized_score,
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
    for r in rows:
        sd, tk, grade, ns, earn, val, risk, sup, a20 = r
        ns_new = no_supply_norm(earn, val, risk)
        grade_new = assign_grade(ns_new)
        result.append({
            "date": sd, "ticker": tk,
            "grade_orig": grade,
            "grade_new": grade_new,
            "score_orig": ns,
            "score_new": ns_new,
            "supply_score": sup,
            "alpha_20d": a20 / 100.0,
        })
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
        return "   N/A"
    return f"{v*100:>+6.2f}%"


def print_stats(label: str, s: dict):
    if not s.get("n"):
        print(f"  {label:<20}  데이터 없음")
        return
    print(f"  {label:<20}  n={s['n']:>4}  med={fmt_pct(s['median'])}  "
          f"hit={s['hit']:>5.1f}%  P10={fmt_pct(s['p10'])}")


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 72)
    print("Supply Counterfactual — supply_score 제거 후 grade 변화 및 alpha 영향")
    print("사전등록: 2026-09-23")
    print(f"  no_supply_norm = round((earn+val+risk)/75*100)")
    print(f"  STRONG_BUY ≥{SB_THRESHOLD} / BUY ≥{BUY_THRESHOLD} / NEUTRAL ≥{NEUTRAL_THRESHOLD}")
    print("=" * 72)

    print("\n[1] 데이터 로딩...", flush=True)
    data = load_data()
    print(f"    총 rows: {len(data):,}")

    # ── 등급 변화 분포 ────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("[ 1. 등급 변화 분포 ]")

    grade_matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in data:
        grade_matrix[r["grade_orig"]][r["grade_new"]] += 1

    grade_order = ["strong_buy", "buy", "neutral", "avoid"]
    print(f"\n  원래\\ No-supply", end="")
    for g in grade_order:
        print(f"  {g[:7]:>8}", end="")
    print()
    print("  " + "-" * 60)
    for go in grade_order:
        print(f"  {go[:11]:<12}", end="")
        for gn in grade_order:
            n = grade_matrix[go][gn]
            print(f"  {n:>8}", end="")
        print()

    # ── SB 그룹 분류 ─────────────────────────────────────────────────────────
    stable_sb   = [r for r in data if r["grade_orig"] == "strong_buy" and r["grade_new"] == "strong_buy"]
    promoted_sb = [r for r in data if r["grade_orig"] != "strong_buy" and r["grade_new"] == "strong_buy"]
    demoted_sb  = [r for r in data if r["grade_orig"] == "strong_buy" and r["grade_new"] != "strong_buy"]
    orig_sb     = [r for r in data if r["grade_orig"] == "strong_buy"]
    new_sb      = [r for r in data if r["grade_new"] == "strong_buy"]

    print(f"\n  Stable SB  (양쪽 다 SB): {len(stable_sb):,}")
    print(f"  Promoted SB (신규 진입): {len(promoted_sb):,}")
    print(f"  Demoted SB  (SB 이탈): {len(demoted_sb):,}")
    print(f"  원래 SB 합계: {len(orig_sb):,}  / No-supply SB 합계: {len(new_sb):,}")

    # supply_score 분포 by group
    print(f"\n  Supply score 분포 by group:")
    for label, grp in [("Stable SB", stable_sb), ("Promoted SB", promoted_sb), ("Demoted SB", demoted_sb)]:
        if not grp:
            continue
        sups = [r["supply_score"] for r in grp]
        print(f"    {label:<14}: mean={statistics.mean(sups):>+5.1f}  "
              f"min={min(sups):>+3.0f}  max={max(sups):>+3.0f}  "
              f"neg%={sum(1 for v in sups if v < 0)/len(sups)*100:.0f}%")

    # ── Alpha 비교 ────────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("[ 2. Alpha 비교 ]")
    print()

    grps = [
        ("원래 SB", orig_sb),
        ("No-supply SB", new_sb),
        ("Stable SB", stable_sb),
        ("Promoted SB", promoted_sb),
        ("Demoted SB", demoted_sb),
    ]
    for label, grp in grps:
        s = stats_block([r["alpha_20d"] for r in grp])
        print_stats(label, s)

    # ── Primary 판정 ─────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("[ Primary 판정 ]")
    promoted_alpha = [r["alpha_20d"] for r in promoted_sb]
    demoted_alpha  = [r["alpha_20d"] for r in demoted_sb]

    if not promoted_alpha or not demoted_alpha:
        print("  데이터 없음 — 판정 불가")
    else:
        pm = statistics.median(promoted_alpha)
        dm = statistics.median(demoted_alpha)
        diff = pm - dm
        passed = diff > PRIMARY_DIFF_THRESHOLD
        print(f"  Promoted SB median: {pm*100:>+.2f}%")
        print(f"  Demoted SB  median: {dm*100:>+.2f}%")
        print(f"  Promoted − Demoted: {diff*100:>+.2f}%p")
        print(f"  판정: {'✅ supply가 좋은 종목을 억누르고 있었다 (제거 효과 양성)' if passed else '❌ 제거 효과 미확인 (Promoted ≤ Demoted + 0.5%p)'}")

    # ── Score 변화 분석 ───────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("[ 3. Score 변화 분석 ]")

    # supply가 높았을수록 no-supply norm이 낮아짐 (희석 효과)
    score_diffs = [r["score_new"] - r["score_orig"] for r in data]
    print(f"  Score 변화 (no-supply norm − orig):")
    print(f"    전체: mean={statistics.mean(score_diffs):>+.1f}  "
          f"min={min(score_diffs):>+.0f}  max={max(score_diffs):>+.0f}")

    # 각 grade 별 score 변화
    for go in grade_order:
        grp = [r for r in data if r["grade_orig"] == go]
        if not grp:
            continue
        diffs = [r["score_new"] - r["score_orig"] for r in grp]
        print(f"    {go:<12}: mean={statistics.mean(diffs):>+.1f}  "
              f"supply avg={statistics.mean(r['supply_score'] for r in grp):>+.1f}")

    # ── No-supply SB에서 IC 변화 ──────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("[ 4. No-supply SB 내부 Ranker IC 비교 ]")
    print("  (원래 SB vs No-supply SB에서 valuation_score의 IC)")

    # 원래 SB 데이터 로드 (valuation_score 포함)
    with sqlite3.connect(FLOW_DB) as c:
        full_rows = c.execute("""
            SELECT signal_date, grade, normalized_score,
                   earnings_score, valuation_score, risk_score, supply_score,
                   alpha_20d
            FROM retro_calibration
            WHERE alpha_20d IS NOT NULL
              AND earnings_score IS NOT NULL
              AND valuation_score IS NOT NULL
              AND risk_score IS NOT NULL
              AND supply_score IS NOT NULL
        """).fetchall()

    def compute_sb_ic(rows_by_date: dict, use_nosupply: bool) -> float | None:
        ics = []
        for date, rows in sorted(rows_by_date.items()):
            if len(rows) < 4:
                continue
            scores = [r["ns_new" if use_nosupply else "ns"] for r in rows]
            alphas = [r["a"] for r in rows]
            rho, _ = scipy_stats.spearmanr(scores, alphas)
            if rho == rho:
                ics.append(rho)
        return statistics.mean(ics) if ics else None

    # SB 그룹별 날짜 내 normalized_score IC
    orig_sb_by_date: dict[str, list] = defaultdict(list)
    new_sb_by_date: dict[str, list] = defaultdict(list)

    for row in full_rows:
        sd, grade, ns, earn, val, risk, sup, a20 = row
        a = a20 / 100.0
        ns_new = no_supply_norm(earn, val, risk)
        grade_new = assign_grade(ns_new)
        r = {"ns": ns, "ns_new": ns_new, "a": a}
        if grade == "strong_buy":
            orig_sb_by_date[sd].append(r)
        if grade_new == "strong_buy":
            new_sb_by_date[sd].append(r)

    ic_orig = compute_sb_ic(orig_sb_by_date, use_nosupply=False)
    ic_new  = compute_sb_ic(new_sb_by_date, use_nosupply=True)

    print(f"  원래 SB 내부 IC (normalized_score): {ic_orig:>+.4f}" if ic_orig else "  데이터 없음")
    print(f"  No-supply SB 내부 IC (no_supply_norm): {ic_new:>+.4f}" if ic_new else "  데이터 없음")
    if ic_orig is not None and ic_new is not None:
        print(f"  IC 변화: {ic_new - ic_orig:>+.4f}  "
              f"({'개선' if ic_new > ic_orig else '악화'})")

    # ── 종합 판정 ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("[ 종합 해석 ]")

    orig_med = statistics.median(r["alpha_20d"] for r in orig_sb) if orig_sb else None
    new_med  = statistics.median(r["alpha_20d"] for r in new_sb)  if new_sb  else None

    print(f"  원래 SB 전체 median alpha:    {orig_med*100:>+.2f}%" if orig_med is not None else "")
    print(f"  No-supply SB 전체 median alpha: {new_med*100:>+.2f}%" if new_med is not None else "")
    if orig_med is not None and new_med is not None:
        print(f"  SB 코호트 alpha 변화: {(new_med-orig_med)*100:>+.2f}%p")


if __name__ == "__main__":
    main()
