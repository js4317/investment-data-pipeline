#!/usr/bin/env python3
"""
Investor Flow Persistence — Streak 종료 이벤트 분석 (v2 — 881일)
=================================================================
가설: streak이 길수록 종료(첫 순매도 전환) 이후 alpha가 약해지는가?

이벤트 정의:
  T   = streak 마지막 날 (T일 순매수, T+1일 첫 순매도 전환, 연속 거래일)
  진입: T+1 open
  5D  = trading_days[idx(T)+6]  close
  20D = trading_days[idx(T)+21] close

버킷 (사전 고정):
  1일 / 2-3일 / 4-6일 / 7일+

외인 / 연기금 별도 분석.
연도별 breakdown 포함.
"""

from __future__ import annotations
import sqlite3, statistics
from itertools import groupby
from pathlib import Path

ROOT     = Path(__file__).resolve().parent.parent
FLOW_DB  = ROOT / "database" / "research.db"
OHLCV_DB = ROOT / "database" / "market_ohlcv.db"
KOSPI    = "1001"

# ── 데이터 로드 ───────────────────────────────────────────────────────────────
def load_trading_days() -> list[str]:
    with sqlite3.connect(OHLCV_DB) as c:
        return [r[0] for r in c.execute(
            "SELECT DISTINCT trade_date FROM market_ohlcv WHERE ticker=? ORDER BY trade_date",
            (KOSPI,)
        )]

def load_prices() -> dict[tuple[str, str, str], float]:
    with sqlite3.connect(OHLCV_DB) as c:
        rows = c.execute(
            "SELECT ticker, trade_date, open, close FROM market_ohlcv"
        ).fetchall()
    p = {}
    for ticker, td, o, cl in rows:
        if o:  p[(ticker, td, "open")]  = float(o)
        if cl: p[(ticker, td, "close")] = float(cl)
    return p

def load_flow(max_date: str | None = None) -> list[tuple]:
    """(signal_date, ticker, foreign_net, pension_net) — max_date 이하만"""
    with sqlite3.connect(FLOW_DB) as c:
        if max_date:
            return c.execute("""
                SELECT signal_date, ticker, foreign_net, pension_net
                FROM investor_flow
                WHERE signal_date <= ?
                ORDER BY ticker, signal_date
            """, (max_date,)).fetchall()
        return c.execute("""
            SELECT signal_date, ticker, foreign_net, pension_net
            FROM investor_flow
            ORDER BY ticker, signal_date
        """).fetchall()

# ── streak 종료 이벤트 추출 ───────────────────────────────────────────────────
def extract_events(rows: list[tuple], td_idx: dict[str, int], field: str) -> list[tuple]:
    """
    field: 'foreign' or 'pension'
    반환: [(T날짜, streak길이, ticker), ...]
    streak 조건: 연속 거래일의 net > 0 연속 구간
    """
    col = 2 if field == "foreign" else 3
    events = []

    for ticker, group in groupby(rows, key=lambda x: x[1]):
        seq = [(r[0], r[col]) for r in group]  # (date, net)
        streak = 0
        for i, (d, net) in enumerate(seq):
            is_pos = net is not None and net > 0
            if is_pos:
                if streak > 0:
                    prev_d = seq[i - 1][0]
                    if td_idx.get(d, -1) != td_idx.get(prev_d, -2) + 1:
                        streak = 0  # 날짜 gap → streak 리셋
                streak += 1
            else:
                if streak > 0:
                    prev_d = seq[i - 1][0]
                    if td_idx.get(d, -1) == td_idx.get(prev_d, -2) + 1:
                        events.append((prev_d, streak, ticker))
                streak = 0

    return events

# ── alpha 계산 ────────────────────────────────────────────────────────────────
def compute_alpha(event: tuple, td: list[str], td_idx: dict, prices: dict) -> dict | None:
    T, streak_len, ticker = event
    idx_T = td_idx.get(T)
    if idx_T is None or idx_T + 21 >= len(td):
        return None

    entry_date = td[idx_T + 1]
    exit_5d    = td[idx_T + 6]
    exit_20d   = td[idx_T + 21]

    t_entry = prices.get((ticker, entry_date, "open")) or prices.get((ticker, entry_date, "close"))
    t_5d    = prices.get((ticker, exit_5d,   "close"))
    t_20d   = prices.get((ticker, exit_20d,  "close"))
    k_entry = prices.get((KOSPI, entry_date, "open")) or prices.get((KOSPI, entry_date, "close"))
    k_5d    = prices.get((KOSPI, exit_5d,   "close"))
    k_20d   = prices.get((KOSPI, exit_20d,  "close"))

    if not all([t_entry, t_5d, t_20d, k_entry, k_5d, k_20d]):
        return None

    return {
        "streak":    streak_len,
        "T_date":    T,
        "year":      T[:4],
        "alpha_5d":  (t_5d  / t_entry - 1 - (k_5d  / k_entry - 1)) * 100,
        "alpha_20d": (t_20d / t_entry - 1 - (k_20d / k_entry - 1)) * 100,
    }

# ── 버킷 분류 ─────────────────────────────────────────────────────────────────
BUCKET_ORDER = ["1일", "2-3일", "4-6일", "7일+"]

def bucket(n: int) -> str:
    if n == 1:  return "1일"
    if n <= 3:  return "2-3일"
    if n <= 6:  return "4-6일"
    return "7일+"

# ── 통계 ─────────────────────────────────────────────────────────────────────
def stats(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0}
    s = sorted(vals)
    p10 = s[max(0, int(len(s) * 0.1) - 1)]
    return {
        "n":        len(vals),
        "mean":     round(statistics.mean(vals), 2),
        "median":   round(statistics.median(vals), 2),
        "hit_rate": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1),
        "P10":      round(p10, 2),
    }

def print_table(title: str, records: list[dict], window: str):
    key = f"alpha_{window}"
    buckets: dict[str, list[float]] = {b: [] for b in BUCKET_ORDER}
    for r in records:
        buckets[bucket(r["streak"])].append(r[key])

    print(f"\n  {title} — {window.upper()} alpha")
    print(f"  {'버킷':<8} {'n':>5} {'mean':>7} {'median':>7} {'hit%':>6} {'P10':>7}")
    print("  " + "-" * 40)
    for b in BUCKET_ORDER:
        s = stats(buckets[b])
        if s["n"] == 0:
            print(f"  {b:<8} {'0':>5}")
        else:
            print(f"  {b:<8} {s['n']:>5} {s['mean']:>7} {s['median']:>7} {s['hit_rate']:>6} {s['P10']:>7}")

# ── main ─────────────────────────────────────────────────────────────────────
def main():
    print("데이터 로딩...")
    td      = load_trading_days()
    td_idx  = {d: i for i, d in enumerate(td)}
    prices  = load_prices()
    rows    = load_flow()

    print(f"price DB 범위: {td[0] if td else 'N/A'} ~ {td[-1] if td else 'N/A'} ({len(td)}일)")
    print(f"investor_flow: {len(rows)}행")

    for field, label in [("foreign", "외인"), ("pension", "연기금")]:
        print(f"\n{'='*55}")
        print(f"{label} streak 종료 이벤트 분석")

        events = extract_events(rows, td_idx, field)
        print(f"총 이벤트: {len(events)}건")

        records = []
        for ev in events:
            r = compute_alpha(ev, td, td_idx, prices)
            if r:
                records.append(r)
        print(f"alpha 계산 가능: {len(records)}건")

        # 전체
        print(f"\n[ 전체 ]")
        print_table(label, records, "5d")
        print_table(label, records, "20d")

        # 연도별
        years = sorted(set(r["year"] for r in records))
        print(f"\n[ 연도별 ]")
        for yr in years:
            yr_recs = [r for r in records if r["year"] == yr]
            print(f"\n  ── {yr}년 ({len(yr_recs)}건) ──")
            print_table(f"{yr} {label}", yr_recs, "5d")
            print_table(f"{yr} {label}", yr_recs, "20d")


if __name__ == "__main__":
    main()
