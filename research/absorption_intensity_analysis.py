#!/usr/bin/env python3
"""
Dip × Absorption Intensity 분석
=================================
가설: 하락폭이 클수록, 동시에 외인·연기금 흡수 강도가 강할수록 이후 alpha가 개선되는가?

변수 정의 (사전 고정):
  return_1d 버킷: 0~-1% / -1~-3% / -3~-5% / -5%+
  absorption_ratio: (foreign_net + pension_net) / (close × volume)  [일간 거래대금 대비 순매수 비율]
    — 양수: 순흡수, 음수: 오히려 순매도
  absorption_intensity: |return_1d| × absorption_ratio  [하락충격 × 흡수강도]

분석 구성:
  1. 하락폭 버킷별 A vs D 비교  → 버킷별 A-D spread가 커지는가?
  2. A 그룹 내 absorption_ratio 분위수  → 흡수 강도가 강할수록 alpha 개선?
  3. absorption_intensity 분위수  → 연속형 결합 변수
"""

from __future__ import annotations
import sqlite3, statistics
from pathlib import Path
import json

ROOT     = Path(__file__).resolve().parent.parent
FLOW_DB  = ROOT / "database" / "research.db"
RETRO_DB = ROOT / "database" / "research.db"
OHLCV_DB = ROOT / "database" / "market_ohlcv.db"
SNAP_DIR = ROOT / "backtests" / "snapshots"
KOSPI    = "1001"
YEAR_2026_START = "2026-01-01"

# ── 사전 고정 버킷 ────────────────────────────────────────────────────────────
DIP_BUCKETS = [
    ("small(0~-1%)",      -0.01, 0.0),
    ("mod(-1~-3%)",       -0.03, -0.01),
    ("large(-3~-5%)",     -0.05, -0.03),
    ("deep(<-5%)",        -99.0, -0.05),
]
DIP_ORDER = [b[0] for b in DIP_BUCKETS]

def dip_bucket(r: float) -> str:
    for name, lo, hi in DIP_BUCKETS:
        if lo <= r < hi:
            return name
    return "deep(<-5%)"

# ── 데이터 로드 ───────────────────────────────────────────────────────────────
def load_prices_and_tv() -> tuple[dict, dict]:
    """prices dict + trading_value dict = close × volume"""
    with sqlite3.connect(OHLCV_DB) as c:
        rows = c.execute(
            "SELECT ticker, trade_date, open, close, volume FROM market_ohlcv"
        ).fetchall()
    prices, tv = {}, {}
    for ticker, td, o, cl, vol in rows:
        if cl:
            prices[(ticker, td, "close")] = float(cl)
        if o:
            prices[(ticker, td, "open")]  = float(o)
        if vol and cl and vol > 0:
            tv[(ticker, td)] = float(cl) * float(vol)  # 거래대금 근사 (원)
    return prices, tv

def load_trading_days() -> list[str]:
    with sqlite3.connect(OHLCV_DB) as c:
        return [r[0] for r in c.execute(
            "SELECT DISTINCT trade_date FROM market_ohlcv WHERE ticker=? ORDER BY trade_date",
            (KOSPI,)
        )]

def load_investor_flow() -> dict[tuple[str, str], dict]:
    with sqlite3.connect(FLOW_DB) as c:
        rows = c.execute(
            "SELECT signal_date, ticker, foreign_net, pension_net FROM investor_flow"
        ).fetchall()
    return {(d, t): {"foreign_net": fn, "pension_net": pn} for d, t, fn, pn in rows}

def load_universe(flow: dict) -> dict[str, set[str]]:
    universe: dict[str, set[str]] = {}
    for (d, t) in flow:
        if d < YEAR_2026_START:
            universe.setdefault(d, set()).add(t)
    if RETRO_DB.exists():
        with sqlite3.connect(RETRO_DB) as c:
            for d, t in c.execute("SELECT DISTINCT signal_date, ticker FROM retro_calibration"):
                if d >= YEAR_2026_START:
                    universe.setdefault(d, set()).add(t)
    retro_dates = {d for d in universe if d >= YEAR_2026_START}
    for f in sorted(SNAP_DIR.glob("*.json")):
        raw = json.loads(f.read_text())
        d = raw.get("date") or f.stem
        if d and d >= YEAR_2026_START and d not in retro_dates:
            tickers = {e["ticker"] for e in raw.get("entries", []) if "ticker" in e}
            if tickers:
                universe[d] = tickers
    return universe

# ── 레코드 구성 ───────────────────────────────────────────────────────────────
def build_records(prices, tv, trading_days, flow, universe) -> list[dict]:
    td_idx = {d: i for i, d in enumerate(trading_days)}
    records = []

    for signal_date, tickers in sorted(universe.items()):
        if signal_date not in td_idx:
            continue
        idx = td_idx[signal_date]
        if idx == 0 or idx + 20 >= len(trading_days):
            continue

        prev_date  = trading_days[idx - 1]
        entry_date = trading_days[idx + 1]
        exit_date  = trading_days[idx + 20]

        k_entry = prices.get((KOSPI, entry_date, "open")) or prices.get((KOSPI, entry_date, "close"))
        k_exit  = prices.get((KOSPI, exit_date,  "close"))
        if not k_entry or not k_exit:
            continue
        kospi_ret = k_exit / k_entry - 1

        for ticker in tickers:
            t_close    = prices.get((ticker, signal_date, "close"))
            prev_close = prices.get((ticker, prev_date,   "close"))
            if not t_close or not prev_close:
                continue
            return_1d = t_close / prev_close - 1
            if return_1d >= 0:
                continue  # 하락 이벤트만

            t_entry = prices.get((ticker, entry_date, "open")) or prices.get((ticker, entry_date, "close"))
            t_exit  = prices.get((ticker, exit_date,  "close"))
            if not t_entry or not t_exit:
                continue
            alpha_20d = (t_exit / t_entry - 1 - kospi_ret) * 100

            inv = flow.get((signal_date, ticker), {})
            fn  = inv.get("foreign_net")
            pn  = inv.get("pension_net")

            # absorption ratio
            daily_tv = tv.get((ticker, signal_date))
            combined_net = (fn or 0) + (pn or 0)
            absorption_ratio = combined_net / daily_tv if daily_tv and daily_tv > 0 else None

            # absorption intensity = |return_1d| × absorption_ratio
            absorption_intensity = (
                abs(return_1d) * absorption_ratio
                if absorption_ratio is not None else None
            )

            records.append({
                "signal_date":          signal_date,
                "ticker":               ticker,
                "year":                 signal_date[:4],
                "return_1d":            return_1d,
                "dip_bucket":           dip_bucket(return_1d),
                "alpha_20d":            alpha_20d,
                "foreign_net":          fn,
                "pension_net":          pn,
                "foreign_positive":     fn is not None and fn > 0,
                "pension_positive":     pn is not None and pn > 0,
                "absorption_ratio":     absorption_ratio,
                "absorption_intensity": absorption_intensity,
            })

    return records

# ── 통계 ─────────────────────────────────────────────────────────────────────
def stats(alphas: list[float]) -> dict:
    if not alphas:
        return {"n": 0}
    s = sorted(alphas)
    p10 = s[max(0, int(len(s) * 0.1) - 1)]
    return {
        "n":        len(alphas),
        "mean":     round(statistics.mean(alphas), 2),
        "median":   round(statistics.median(alphas), 2),
        "hit_rate": round(sum(1 for a in alphas if a > 0) / len(alphas) * 100, 1),
        "P10":      round(p10, 2),
    }

def print_stats_row(label: str, alphas: list[float]):
    s = stats(alphas)
    if s["n"] == 0:
        print(f"  {label:<20} {'0':>6}")
    else:
        print(f"  {label:<20} {s['n']:>7,} {s['mean']:>7} {s['median']:>7} {s['hit_rate']:>6} {s['P10']:>7}")

def header():
    print(f"  {'':20} {'n':>7} {'mean':>7} {'median':>7} {'hit%':>6} {'P10':>7}")
    print("  " + "-" * 58)

def quartile_split(records: list[dict], key: str) -> list[list[dict]]:
    """key 기준 Q1~Q4 분위수 분할"""
    valid = [r for r in records if r[key] is not None]
    if not valid:
        return [[], [], [], []]
    vals = sorted(v[key] for v in valid)
    n = len(vals)
    cuts = [vals[n // 4], vals[n // 2], vals[3 * n // 4]]
    groups = [[], [], [], []]
    for r in valid:
        v = r[key]
        if v <= cuts[0]:      groups[0].append(r)
        elif v <= cuts[1]:    groups[1].append(r)
        elif v <= cuts[2]:    groups[2].append(r)
        else:                 groups[3].append(r)
    return groups

# ── 분석 함수들 ───────────────────────────────────────────────────────────────
def analysis_dip_bucket_x_group(down: list[dict], label: str = "전체"):
    """분석 1: 하락폭 버킷 × A/D 비교"""
    fp = lambda r: r["foreign_positive"]
    pp = lambda r: r["pension_positive"]

    print(f"\n{'='*65}")
    print(f"[ 분석 1: 하락폭 버킷 × A vs D ] — {label}")
    print(f"{'버킷':<18} {'그룹':<6} {'n':>7} {'mean':>7} {'median':>7} {'hit%':>6} {'P10':>7}  A-D")
    print("-" * 65)

    for bkt in DIP_ORDER:
        b_recs = [r for r in down if r["dip_bucket"] == bkt]
        A = [r["alpha_20d"] for r in b_recs if fp(r) and pp(r)]
        D = [r["alpha_20d"] for r in b_recs if not fp(r) and not pp(r)]
        sA, sD = stats(A), stats(D)
        a_d = f"{sA['mean']-sD['mean']:+.2f}pp" if sA["n"] and sD["n"] else "N/A"
        print(f"  {bkt:<16} A     {sA['n']:>7,} {sA['mean']:>7} {sA['median']:>7} {sA['hit_rate']:>6} {sA['P10']:>7}  {a_d}")
        print(f"  {'':16} D     {sD['n']:>7,} {sD['mean']:>7} {sD['median']:>7} {sD['hit_rate']:>6} {sD['P10']:>7}")
        print()

def analysis_absorption_quartile(down: list[dict], label: str = "전체"):
    """분석 2: A 그룹 내 absorption_ratio 분위수"""
    fp = lambda r: r["foreign_positive"]
    pp = lambda r: r["pension_positive"]
    A_recs = [r for r in down if fp(r) and pp(r)]
    groups = quartile_split(A_recs, "absorption_ratio")

    print(f"\n{'='*65}")
    print(f"[ 분석 2: A 그룹 내 흡수 강도 분위수 ] — {label}")
    print(f"absorption_ratio = (foreign_net + pension_net) / (close × volume)")
    header()
    for i, grp in enumerate(groups):
        if grp:
            ar_min = min(r["absorption_ratio"] for r in grp)
            ar_max = max(r["absorption_ratio"] for r in grp)
            lbl = f"Q{i+1} [{ar_min:.3f},{ar_max:.3f}]"
        else:
            lbl = f"Q{i+1}"
        print_stats_row(lbl, [r["alpha_20d"] for r in grp])

    # 참고: D 그룹 (absorption 없음)
    D_recs = [r for r in down if not fp(r) and not pp(r)]
    print_stats_row("D(수급없음)", [r["alpha_20d"] for r in D_recs])

def analysis_intensity_quartile(down: list[dict], label: str = "전체"):
    """분석 3: absorption_intensity 분위수 (전체 하락 종목 기준)"""
    # intensity: 양수만 의미 있음 (외인+연기금 모두 순매수 + 하락)
    valid = [r for r in down if r["absorption_intensity"] is not None and r["absorption_intensity"] > 0]
    groups = quartile_split(valid, "absorption_intensity")

    print(f"\n{'='*65}")
    print(f"[ 분석 3: absorption_intensity 분위수 ] — {label}")
    print(f"absorption_intensity = |return_1d| × absorption_ratio (양수만 — 하락+동시순매수)")
    header()
    for i, grp in enumerate(groups):
        if grp:
            ai_min = min(r["absorption_intensity"] for r in grp)
            ai_max = max(r["absorption_intensity"] for r in grp)
            lbl = f"Q{i+1}[{ai_min:.4f},{ai_max:.4f}]"
        else:
            lbl = f"Q{i+1}"
        print_stats_row(lbl, [r["alpha_20d"] for r in grp])

    # D(하락만) 비교
    D_recs = [r for r in down if not r["foreign_positive"] and not r["pension_positive"]]
    print_stats_row("D(수급없음)", [r["alpha_20d"] for r in D_recs])

# ── main ─────────────────────────────────────────────────────────────────────
def main():
    print("데이터 로딩...")
    prices, tv    = load_prices_and_tv()
    trading_days  = load_trading_days()
    flow          = load_investor_flow()
    universe      = load_universe(flow)

    print("레코드 구성...")
    records = build_records(prices, tv, trading_days, flow, universe)
    down    = records  # build_records already filters return_1d < 0
    n_dates = len(set(r["signal_date"] for r in records))
    print(f"하락 관측치: {len(records):,}건 ({n_dates}일)\n")

    no_tv   = sum(1 for r in records if r["absorption_ratio"] is None)
    print(f"absorption_ratio 계산 불가(거래대금 없음): {no_tv:,}건 ({no_tv/len(records)*100:.1f}%)")

    # ── 전체 ─────────────────────────────────────────────────────────────────
    analysis_dip_bucket_x_group(down, "전체 881일")
    analysis_absorption_quartile(down, "전체 881일")
    analysis_intensity_quartile(down, "전체 881일")

    # ── 연도별 (dip bucket × A/D만 — 나머지는 너무 많음) ──────────────────────
    print(f"\n{'='*65}")
    print("[ 연도별 A-D spread (버킷별) ]")
    fp = lambda r: r["foreign_positive"]
    pp = lambda r: r["pension_positive"]
    years = sorted(set(r["year"] for r in records))

    for yr in years:
        yr_down = [r for r in records if r["year"] == yr]
        print(f"\n  ── {yr}년 ({len(yr_down):,}건) ──")
        print(f"  {'버킷':<18} {'A mean':>8} {'D mean':>8} {'A-D':>8}")
        print("  " + "-" * 44)
        for bkt in DIP_ORDER:
            b = [r for r in yr_down if r["dip_bucket"] == bkt]
            A = [r["alpha_20d"] for r in b if fp(r) and pp(r)]
            D = [r["alpha_20d"] for r in b if not fp(r) and not pp(r)]
            sA, sD = stats(A), stats(D)
            if sA["n"] and sD["n"]:
                diff = f"{sA['mean']-sD['mean']:+.2f}pp"
            else:
                diff = "N/A"
            nA = f"n={sA['n']}" if sA["n"] else "n=0"
            nD = f"n={sD['n']}" if sD["n"] else "n=0"
            print(f"  {bkt:<18} {sA.get('mean','—'):>8} {sD.get('mean','—'):>8} {diff:>8}  ({nA}/{nD})")


if __name__ == "__main__":
    main()
