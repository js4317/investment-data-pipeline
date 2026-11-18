#!/usr/bin/env python3
"""
③ STRONG_BUY 내부 랭킹 — Score 단조성 검증
=============================================
사전등록: 2026-09-23  /  docs/RESEARCH.md

가설: STRONG_BUY 등급 내에서 normalized_score가 높을수록 20D alpha가 단조롭게 높다.

Primary Q1  : Daily cross-sectional Spearman IC (score vs alpha_20d, STRONG_BUY only)
Secondary Q2: Percentile bucket별 alpha 분포 — Top50% vs Bottom50% (Primary bucket)
                                              Top10/10-25/25-50/50-100 (Secondary)

Score: normalized_score as-is (컴포넌트 재가중 없음)
"""
from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path
from scipy import stats as scipy_stats

ROOT    = Path(__file__).resolve().parent.parent
FLOW_DB = ROOT / "database" / "research.db"

MIN_N_PER_DATE    = 4     # IC 계산 최소 n
MIN_N_BUCKET      = 20    # Bucket 최소 n (미달 시 Inconclusive)
IC_THRESHOLD      = 0.05  # Primary 판정
IC_POS_THRESHOLD  = 55.0  # %
DIFF_THRESHOLD    = 0.01  # 1%p


# ── 데이터 로드 ───────────────────────────────────────────────────────────────

def load_data() -> list[dict]:
    with sqlite3.connect(FLOW_DB) as c:
        rows = c.execute("""
            SELECT signal_date, ticker, normalized_score, alpha_20d
            FROM retro_calibration
            WHERE grade = 'strong_buy' AND alpha_20d IS NOT NULL
              AND normalized_score IS NOT NULL
            ORDER BY signal_date, normalized_score DESC
        """).fetchall()
    return [
        {"date": r[0], "ticker": r[1], "score": r[2], "alpha_20d": r[3] / 100.0}
        for r in rows
    ]


# ── Daily IC ──────────────────────────────────────────────────────────────────

def compute_daily_ic(rows_by_date: dict[str, list[dict]]) -> list[float]:
    ics = []
    for date, rows in sorted(rows_by_date.items()):
        if len(rows) < MIN_N_PER_DATE:
            continue
        scores = [r["score"] for r in rows]
        alphas = [r["alpha_20d"] for r in rows]
        rho, _ = scipy_stats.spearmanr(scores, alphas)
        if rho == rho:  # NaN 제외
            ics.append(rho)
    return ics


def ic_stats(ics: list[float]) -> dict:
    if not ics:
        return {}
    return {
        "mean":    statistics.mean(ics),
        "median":  statistics.median(ics),
        "pos_pct": sum(1 for v in ics if v > 0) / len(ics) * 100,
        "n":       len(ics),
    }


# ── Percentile Bucket ─────────────────────────────────────────────────────────

def assign_percentile_bucket(n: int, rank_0indexed: int) -> str:
    """score 내림차순 정렬 후 0-indexed rank → bucket 이름"""
    pct = rank_0indexed / n  # 0=최고 score
    if pct < 0.10:
        return "top10"
    elif pct < 0.25:
        return "10-25"
    elif pct < 0.50:
        return "25-50"
    else:
        return "50-100"


def compute_buckets(rows_by_date: dict[str, list[dict]]) -> dict[str, list[float]]:
    """bucket → list of alpha_20d (pooled across dates)"""
    buckets: dict[str, list[float]] = defaultdict(list)
    for date, rows in rows_by_date.items():
        n = len(rows)
        if n < MIN_N_PER_DATE:
            continue
        # rows already sorted by score DESC per load query, but re-sort to be safe
        sorted_rows = sorted(rows, key=lambda r: r["score"], reverse=True)
        for rank, row in enumerate(sorted_rows):
            b = assign_percentile_bucket(n, rank)
            buckets[b].append(row["alpha_20d"])
            # binary split
            if rank < n // 2:
                buckets["top50"].append(row["alpha_20d"])
            else:
                buckets["bottom50"].append(row["alpha_20d"])
    return dict(buckets)


def bucket_stats(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0, "median": None, "mean": None, "hit": None}
    return {
        "n":      len(vals),
        "median": statistics.median(vals),
        "mean":   statistics.mean(vals),
        "hit":    sum(1 for v in vals if v > 0) / len(vals) * 100,
    }


# ── 출력 ─────────────────────────────────────────────────────────────────────

def fmt(v: float | None, pct: bool = True) -> str:
    if v is None:
        return "  N/A"
    return f"{v*100:>+6.2f}%" if pct else f"{v:>+7.4f}"


def print_bucket_row(label: str, stats: dict, suffix: str = ""):
    if stats["n"] == 0:
        print(f"  {label:<12}  데이터 없음")
        return
    conclusive = "Inconclusive" if stats["n"] < MIN_N_BUCKET else suffix
    print(f"  {label:<12} {stats['n']:>5}  {fmt(stats['median']):>8}  "
          f"{fmt(stats['mean']):>8}  {stats['hit']:>6.1f}%  {conclusive}")


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 68)
    print("③ STRONG_BUY 내부 랭킹 — Score 단조성 검증")
    print("사전등록: 2026-09-23")
    print(f"  Primary IC 판정: mean > +{IC_THRESHOLD} AND pos% > {IC_POS_THRESHOLD}%")
    print(f"  Bucket diff 판정: Top50 - Bottom50 > +{DIFF_THRESHOLD*100:.0f}%p")
    print(f"  n < {MIN_N_BUCKET} bucket: Inconclusive")
    print("=" * 68)

    print("\n[1] 데이터 로딩...", flush=True)
    rows = load_data()
    print(f"    STRONG_BUY rows: {len(rows):,}")

    rows_by_date: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        rows_by_date[r["date"]].append(r)

    dates = sorted(rows_by_date)
    counts = [len(v) for v in rows_by_date.values()]
    print(f"    signal_dates: {len(dates)}일  ({dates[0]} ~ {dates[-1]})")
    print(f"    n per date: min={min(counts)} / median={statistics.median(counts):.0f} / max={max(counts)}")
    valid_dates = sum(1 for c in counts if c >= MIN_N_PER_DATE)
    print(f"    IC 계산 가능 날짜 (n≥{MIN_N_PER_DATE}): {valid_dates}일")

    # ── Primary Q1: Daily IC ──────────────────────────────────────────────────
    print("\n" + "=" * 68)
    print("[ Primary Q1 — Daily Cross-sectional IC (STRONG_BUY 내부) ]")
    print("  기대 방향: IC > 0 (score 높을수록 alpha 높음)")

    ics = compute_daily_ic(rows_by_date)
    s = ic_stats(ics)

    print(f"\n  mean_IC  : {s['mean']:>+8.4f}  {'✓' if s['mean'] > IC_THRESHOLD else '—'}"
          f"  (기준: > +{IC_THRESHOLD})")
    print(f"  median_IC: {s['median']:>+8.4f}")
    print(f"  pos_IC%  : {s['pos_pct']:>7.1f}%  {'✓' if s['pos_pct'] > IC_POS_THRESHOLD else '—'}"
          f"  (기준: > {IC_POS_THRESHOLD}%)")
    print(f"  n_days   : {s['n']:>5}")

    # Primary 판정
    q1_pass = s["mean"] > IC_THRESHOLD and s["pos_pct"] > IC_POS_THRESHOLD
    print(f"\n  Primary Q1 판정: {'✅ 단조성 지지' if q1_pass else '❌ 단조성 미지지'}")

    # Pooled Spearman (참고)
    all_scores = [r["score"] for r in rows]
    all_alphas = [r["alpha_20d"] for r in rows]
    rho_pooled, p_pooled = scipy_stats.spearmanr(all_scores, all_alphas)
    print(f"\n  [참고] Pooled Spearman (전체 {len(rows):,}행):")
    print(f"    rho={rho_pooled:>+.4f}  p={p_pooled:.4f}"
          f"  {'→ rho>0, 방향 일치' if rho_pooled > 0 else '→ rho≤0, 방향 불일치'}")
    print("    ※ Pooled는 날짜간 독립성 가정 위반 — 방향성 참고만")

    # ── Secondary Q2: Bucket 분석 ─────────────────────────────────────────────
    print("\n" + "=" * 68)
    print("[ Secondary Q2 — Percentile Bucket Alpha 분포 ]")
    buckets = compute_buckets(rows_by_date)

    print("\n  [A] 이진 분할: Top50% vs Bottom50%")
    print(f"  {'Bucket':<12} {'n':>5}  {'median':>8}  {'mean':>8}  {'hit%':>6}  판정")
    print("  " + "-" * 55)
    top50  = bucket_stats(buckets.get("top50", []))
    bot50  = bucket_stats(buckets.get("bottom50", []))
    diff_binary = (top50["median"] - bot50["median"]) if top50["median"] is not None and bot50["median"] is not None else None

    print_bucket_row("Top50%", top50)
    print_bucket_row("Bottom50%", bot50)
    if diff_binary is not None:
        flag = "✓" if diff_binary > DIFF_THRESHOLD else "—"
        print(f"  {'Top - Bottom':<12} {'':>5}  {diff_binary*100:>+7.2f}%  {'':>8}  {'':>6}  {flag}")
    q2_pass = diff_binary is not None and diff_binary > DIFF_THRESHOLD
    print(f"\n  Secondary Q2 판정: {'✅ Top>Bottom' if q2_pass else '❌ Top≤Bottom (또는 <1%p)'}")

    print("\n  [B] 세부 분할: Top10% / 10-25% / 25-50% / 50-100%")
    print(f"  {'Bucket':<12} {'n':>5}  {'median':>8}  {'mean':>8}  {'hit%':>6}  비고")
    print("  " + "-" * 65)
    bucket_order = [("top10", "Top10%"), ("10-25", "10-25%"), ("25-50", "25-50%"), ("50-100", "50-100%")]
    bucket_meds = []
    for key, label in bucket_order:
        st = bucket_stats(buckets.get(key, []))
        suffix = "" if st["n"] >= MIN_N_BUCKET else "Inconclusive"
        print_bucket_row(label, st, suffix)
        bucket_meds.append((key, st["median"]))

    # 단조성 체크 (세부 bucket)
    meds = [m for _, m in bucket_meds if m is not None]
    is_monotone = all(meds[i] >= meds[i + 1] for i in range(len(meds) - 1))
    print(f"\n  세부 bucket 단조성 (Top→Bottom 순서로 내림차순?): "
          f"{'✓ 단조 감소' if is_monotone else '✗ 비단조'}")

    # ── 종합 판정 ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 68)
    print("[ 종합 판정 ]")
    print(f"  Primary   Q1 (Daily IC): {'✅ 지지' if q1_pass else '❌ 미지지'}")
    print(f"  Secondary Q2 (Top50>Bottom50): {'✅ 지지' if q2_pass else '❌ 미지지'}")
    if q1_pass and q2_pass:
        verdict = "Score ranking이 STRONG_BUY 내에서도 유효한 순위 신호"
    elif q1_pass or q2_pass:
        verdict = "부분 지지 — 한 지표만 기준 충족"
    else:
        verdict = "Score ranking이 STRONG_BUY 내부 순위 신호로 작동하지 않음"
    print(f"\n  결론: {verdict}")
    print()
    print("  실증 데이터:")
    print(f"    STRONG_BUY n={len(rows):,}, dates={len(dates)}, IC days={s['n']}")
    print(f"    IC mean={s['mean']:>+.4f}, pos%={s['pos_pct']:.1f}%")
    if diff_binary is not None:
        print(f"    Top50 - Bottom50 = {diff_binary*100:+.2f}%p")


if __name__ == "__main__":
    main()
