#!/usr/bin/env python3
"""
PIT Universe A-D 시간 안정성 검증
===================================
목적: Bear/Sideways에서 A-D 음수가 전 기간 분산된 현상인지,
      특정 시점에 집중된 것인지 확인.

출력:
  - 월별 A-D (레짐 병기)
  - 레짐별 월 분포: 양수/음수/중립(|A-D|<0.5pp) 비율
  - 레짐별 A-D 중앙값 (평균 대비 이상치 민감도 확인)
"""
from __future__ import annotations
import sqlite3, statistics
from pathlib import Path

ROOT     = Path(__file__).resolve().parent.parent
FLOW_DB  = ROOT / "database" / "research.db"
OHLCV_DB = ROOT / "database" / "market_ohlcv.db"
KOSPI    = "1001"

EARNINGS_GATE = 22

SIGNAL_TO_RISK_YEAR = {
    "2020": 2019,
    "2021": 2020,
    "2022": 2021,
    "2023": 2022,
    "2024": 2023,
    "2025": 2024,
}

NEUTRAL_THRESHOLD = 0.5  # |A-D| < 0.5pp → 중립으로 분류

# ── 데이터 로드 ───────────────────────────────────────────────────────────────

def load_pit_tickers() -> dict[str, set[str]]:
    result = {}
    with sqlite3.connect(FLOW_DB) as c:
        for sig_yr, risk_yr in SIGNAL_TO_RISK_YEAR.items():
            rows = c.execute(
                "SELECT ticker FROM rrs_stock_year WHERE risk_year=? AND earnings_score>=?",
                (risk_yr, EARNINGS_GATE)
            ).fetchall()
            result[sig_yr] = {r[0] for r in rows}
    return result

def load_phase_by_year() -> dict[str, str]:
    with sqlite3.connect(FLOW_DB) as c:
        rows = c.execute("SELECT year, phase FROM research_year_meta ORDER BY year").fetchall()
    return {str(yr): ph for yr, ph in rows}

def load_prices(tickers: set[str]) -> dict:
    """KOSPI + PIT 유니버스 티커만 로드해서 메모리 절약"""
    placeholders = ",".join("?" * len(tickers))
    with sqlite3.connect(OHLCV_DB) as c:
        rows = c.execute(
            f"SELECT ticker, trade_date, open, close FROM market_ohlcv WHERE ticker IN ({placeholders})",
            list(tickers)
        ).fetchall()
    p = {}
    for ticker, td, o, cl in rows:
        if cl: p[(ticker, td, "close")] = float(cl)
        if o:  p[(ticker, td, "open")]  = float(o)
    return p

def load_trading_days() -> list[str]:
    with sqlite3.connect(OHLCV_DB) as c:
        return [r[0] for r in c.execute(
            "SELECT DISTINCT trade_date FROM market_ohlcv WHERE ticker=? ORDER BY trade_date",
            (KOSPI,)
        )]

def load_investor_flow() -> dict:
    with sqlite3.connect(FLOW_DB) as c:
        rows = c.execute(
            "SELECT signal_date, ticker, foreign_net, pension_net FROM investor_flow"
        ).fetchall()
    return {(d, t): {"foreign_net": fn, "pension_net": pn} for d, t, fn, pn in rows}

def load_universe(flow, pit):
    universe = {}
    for (d, t) in flow:
        yr = d[:4]
        if yr in pit and t in pit[yr]:
            universe.setdefault(d, set()).add(t)
    return universe

def build_records(prices, trading_days, flow, universe, phase_by_year) -> list[dict]:
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
        k_exit  = prices.get((KOSPI, exit_date, "close"))
        if not k_entry or not k_exit:
            continue
        kospi_ret = k_exit / k_entry - 1
        yr = signal_date[:4]
        phase = phase_by_year.get(yr, "Unknown")
        for ticker in tickers:
            t_close    = prices.get((ticker, signal_date, "close"))
            prev_close = prices.get((ticker, prev_date, "close"))
            if not t_close or not prev_close:
                continue
            return_1d = t_close / prev_close - 1
            t_entry = prices.get((ticker, entry_date, "open")) or prices.get((ticker, entry_date, "close"))
            t_exit  = prices.get((ticker, exit_date, "close"))
            if not t_entry or not t_exit:
                continue
            alpha_20d = (t_exit / t_entry - 1 - kospi_ret) * 100
            inv = flow.get((signal_date, ticker), {})
            fn  = inv.get("foreign_net")
            pn  = inv.get("pension_net")
            records.append({
                "signal_date": signal_date,
                "ym":          signal_date[:7],   # YYYY-MM
                "year":        yr,
                "phase":       phase,
                "return_1d":   return_1d,
                "alpha_20d":   alpha_20d,
                "fp":          fn is not None and fn > 0,
                "pp":          pn is not None and pn > 0,
            })
    return records

# ── 통계 ─────────────────────────────────────────────────────────────────────

def mean_alpha(recs) -> float | None:
    v = [r["alpha_20d"] for r in recs]
    return round(statistics.mean(v), 2) if v else None

def median_alpha(recs) -> float | None:
    v = [r["alpha_20d"] for r in recs]
    return round(statistics.median(v), 2) if v else None

def a_d(recs) -> float | None:
    down = [r for r in recs if r["return_1d"] < 0]
    A = [r["alpha_20d"] for r in down if r["fp"] and r["pp"]]
    D = [r["alpha_20d"] for r in down if not r["fp"] and not r["pp"]]
    if not A or not D:
        return None
    return round(statistics.mean(A) - statistics.mean(D), 2)

def a_n(recs) -> int:
    down = [r for r in recs if r["return_1d"] < 0]
    return sum(1 for r in down if r["fp"] and r["pp"])

# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 64)
    print("PIT Universe A-D 시간 안정성 검증")
    print("=" * 64)

    pit           = load_pit_tickers()
    phase_by_year = load_phase_by_year()
    all_tickers   = {KOSPI} | {t for s in pit.values() for t in s}
    print(f"가격 로드 대상: KOSPI + {len(all_tickers)-1}개 종목")
    prices        = load_prices(all_tickers)
    trading_days  = load_trading_days()
    flow          = load_investor_flow()
    universe      = load_universe(flow, pit)
    records       = build_records(prices, trading_days, flow, universe, phase_by_year)
    print(f"총 관측치: {len(records):,}\n")

    # 월별 A-D 집계
    months = sorted(set(r["ym"] for r in records))
    monthly = []
    for ym in months:
        recs_m = [r for r in records if r["ym"] == ym]
        yr     = ym[:4]
        phase  = phase_by_year.get(yr, "?")
        ad     = a_d(recs_m)
        n_a    = a_n(recs_m)
        monthly.append({"ym": ym, "phase": phase, "a_d": ad, "n_a": n_a})

    # ── 월별 출력 ────────────────────────────────────────────────────────────
    print("[ 월별 A-D (레짐 병기) ]")
    print(f"  {'월':<9} {'레짐':<12} {'A n':>5} {'A-D':>7}  방향")
    print("  " + "-" * 42)
    for m in monthly:
        ad  = m["a_d"]
        if ad is None:
            continue
        bar = "▲" if ad > NEUTRAL_THRESHOLD else ("▼" if ad < -NEUTRAL_THRESHOLD else "—")
        print(f"  {m['ym']:<9} {m['phase']:<12} {m['n_a']:>5} {ad:>+7.2f}  {bar}")

    # ── 레짐별 월 분포 ───────────────────────────────────────────────────────
    print()
    print("[ 레짐별 월 분포 ]")
    print(f"  {'레짐':<14} {'총월수':>6} {'양수(▲)':>8} {'음수(▼)':>8} {'중립(—)':>8} {'A-D 중앙값':>10}")
    print("  " + "-" * 58)

    regimes = [("Bull", ["Bull"]), ("Bear/Sideways", ["Bear", "Sideways"])]
    for label, phases in regimes:
        ms = [m for m in monthly if m["phase"] in phases and m["a_d"] is not None]
        if not ms:
            continue
        ads    = [m["a_d"] for m in ms]
        pos    = sum(1 for v in ads if v >  NEUTRAL_THRESHOLD)
        neg    = sum(1 for v in ads if v < -NEUTRAL_THRESHOLD)
        neu    = len(ads) - pos - neg
        med    = round(statistics.median(ads), 2)
        print(f"  {label:<14} {len(ms):>6} {pos:>8} {neg:>8} {neu:>8} {med:>+10.2f}")

    # ── Bull/Bear 월 A-D 분포 상세 ──────────────────────────────────────────
    print()
    print("[ 레짐별 월 A-D 분포 ]")
    for label, phases in regimes:
        ads = sorted(m["a_d"] for m in monthly if m["phase"] in phases and m["a_d"] is not None)
        if not ads:
            continue
        mn   = min(ads)
        mx   = max(ads)
        mean = round(statistics.mean(ads), 2)
        med  = round(statistics.median(ads), 2)
        print(f"  {label}: n={len(ads)}개월  min={mn:+.2f}  max={mx:+.2f}  mean={mean:+.2f}  median={med:+.2f}")

    print()
    print("[ 해석 주의 ]")
    print(f"  중립 기준: |A-D| < {NEUTRAL_THRESHOLD}pp")
    print("  월별 A 그룹 n이 작은 달은 노이즈 가능성 높음")
    print("  20D alpha는 날짜간 중첩 있음 — 독립 관측 아님")


if __name__ == "__main__":
    main()
