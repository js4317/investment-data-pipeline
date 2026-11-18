#!/usr/bin/env python3
"""
PIT Universe A-D 검증 (Historical Replay of Current Production Formula)
=======================================================================
목적: v8.5.19 formula를 각 연도에 소급 적용한 universe에서
      Dip + Investor Absorption (A-D) 신호가 재현되는가?

Universe 정의 (PIT):
  signal_date ∈ 2023 → risk_year=2022 tickers (earnings_score >= 22)
  signal_date ∈ 2024 → risk_year=2023 tickers (earnings_score >= 22)

  risk_year N = N연도 재무 데이터 → fwd_year N+1 signal 기간에 적용
  threshold 완화 / ROE 대체값 없음 — 미충족 연도는 보류

해석 제한:
  "당시 실제 스크리너 복원"이 아닌
  "현재 v8.5.19 formula를 역사에 동일하게 적용했을 때의 universe"
  2025 → risk_year=2024 ROE NULL → 이번 검증 제외
"""
from __future__ import annotations
import json, sqlite3, statistics
from pathlib import Path

ROOT     = Path(__file__).resolve().parent.parent
FLOW_DB  = ROOT / "database" / "research.db"
OHLCV_DB = ROOT / "database" / "market_ohlcv.db"
KOSPI    = "1001"

EARNINGS_GATE = 22   # v8.5.19 production gate: earnings_score >= 22

# signal_year → risk_year 매핑 (PIT 규칙)
SIGNAL_TO_RISK_YEAR = {
    "2020": 2019,
    "2021": 2020,
    "2022": 2021,
    "2023": 2022,
    "2024": 2023,
    "2025": 2024,
}

# ── PIT universe 로드 ─────────────────────────────────────────────────────────
def load_pit_tickers() -> dict[str, set[str]]:
    """signal_year → earnings_score>=22 tickers (risk_year 기준)"""
    result = {}
    with sqlite3.connect(FLOW_DB) as c:
        for sig_yr, risk_yr in SIGNAL_TO_RISK_YEAR.items():
            rows = c.execute(
                "SELECT ticker FROM rrs_stock_year WHERE risk_year=? AND earnings_score>=?",
                (risk_yr, EARNINGS_GATE)
            ).fetchall()
            result[sig_yr] = {r[0] for r in rows}
            print(f"  PIT universe {sig_yr} (risk_year={risk_yr}): {len(result[sig_yr])} tickers")
    return result

# ── 가격 로드 ─────────────────────────────────────────────────────────────────
def load_prices() -> dict:
    with sqlite3.connect(OHLCV_DB) as c:
        rows = c.execute(
            "SELECT ticker, trade_date, open, close FROM market_ohlcv"
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

# ── universe 구성: signal_date × PIT tickers ─────────────────────────────────
def load_universe(flow: dict, pit: dict[str, set[str]]) -> dict[str, set[str]]:
    """
    각 signal_date에 해당 연도의 PIT universe로 필터.
    flow에서 해당 종목이 investor_flow에 있어야 수급 정보도 존재.
    """
    universe: dict[str, set[str]] = {}
    covered_years = set(pit.keys())

    for (d, t) in flow:
        yr = d[:4]
        if yr not in covered_years:
            continue
        if t in pit[yr]:
            universe.setdefault(d, set()).add(t)

    return universe

# ── 레코드 구성 ───────────────────────────────────────────────────────────────
def build_records(prices, trading_days, flow, universe) -> list[dict]:
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
                "signal_date":      signal_date,
                "ticker":           ticker,
                "year":             signal_date[:4],
                "return_1d":        return_1d,
                "alpha_20d":        alpha_20d,
                "foreign_positive": fn is not None and fn > 0,
                "pension_positive": pn is not None and pn > 0,
            })

    return records

# ── 통계 ─────────────────────────────────────────────────────────────────────
def stats(alphas: list[float]) -> dict:
    if not alphas:
        return {"n": 0, "mean": None, "median": None, "hit_rate": None, "P10": None}
    s = sorted(alphas)
    p10 = s[max(0, int(len(s) * 0.1) - 1)]
    return {
        "n":        len(alphas),
        "mean":     round(statistics.mean(alphas), 2),
        "median":   round(statistics.median(alphas), 2),
        "hit_rate": round(sum(1 for a in alphas if a > 0) / len(alphas) * 100, 1),
        "P10":      round(p10, 2),
    }

def make_groups(records: list[dict]) -> dict:
    down = [r for r in records if r["return_1d"] < 0]
    fp   = lambda r: r["foreign_positive"]
    pp   = lambda r: r["pension_positive"]
    return {
        "A": [r for r in down if fp(r) and pp(r)],
        "B": [r for r in down if fp(r) and not pp(r)],
        "C": [r for r in down if not fp(r) and pp(r)],
        "D": [r for r in down if not fp(r) and not pp(r)],
    }

def print_year(yr: str, recs: list[dict], pit_n: int, full_n_flow: int):
    g    = make_groups(recs)
    down = [r for r in recs if r["return_1d"] < 0]
    A    = [r["alpha_20d"] for r in g["A"]]
    B    = [r["alpha_20d"] for r in g["B"]]
    D    = [r["alpha_20d"] for r in g["D"]]
    sA, sB, sD = stats(A), stats(B), stats(D)
    dates = len(set(r["signal_date"] for r in recs))

    a_d = f"{sA['mean']-sD['mean']:+.2f}pp" if sA["n"] and sD["n"] else "N/A"
    a_b = f"{sA['mean']-sB['mean']:+.2f}pp" if sA["n"] and sB["n"] else "N/A"

    print(f"\n  ── {yr}년 ──")
    print(f"  PIT universe: {pit_n} tickers / flow coverage: {len(set(r['ticker'] for r in recs))}종목")
    print(f"  날짜: {dates}일 / 전체관측: {len(recs):,} / 하락: {len(down):,}")
    print(f"  {'그룹':<6} {'n':>6} {'mean':>7} {'median':>7} {'hit%':>6} {'P10':>7}")
    print("  " + "-" * 42)
    for name, grp_key in [("A", "A"), ("B", "B"), ("C", "C"), ("D", "D")]:
        s = stats([r["alpha_20d"] for r in g[grp_key]])
        if s["n"] == 0:
            print(f"  {name:<6} {'0':>6}")
        else:
            print(f"  {name:<6} {s['n']:>6} {s['mean']:>7} {s['median']:>7} {s['hit_rate']:>6} {s['P10']:>7}")
    print(f"  → A-D: {a_d}  /  A-B: {a_b}")

# ── main ─────────────────────────────────────────────────────────────────────
def main():
    print("=" * 62)
    print("PIT Universe A-D 검증")
    print("Historical Replay of Current Production Formula (v8.5.19)")
    print("=" * 62)
    print(f"Earnings gate: earnings_score >= {EARNINGS_GATE}")
    print(f"대상 연도: {list(SIGNAL_TO_RISK_YEAR.keys())}")
    print()

    print("PIT universe 로드...")
    pit = load_pit_tickers()
    print()

    print("데이터 로딩...")
    prices       = load_prices()
    trading_days = load_trading_days()
    flow         = load_investor_flow()
    print(f"price DB: {trading_days[0]} ~ {trading_days[-1]}")

    print("\nUniverse 구성...")
    universe = load_universe(flow, pit)
    n_dates  = len(universe)
    print(f"신호 날짜수: {n_dates}일")

    print("\n레코드 구성...")
    records = build_records(prices, trading_days, flow, universe)
    print(f"총 관측치: {len(records):,}")

    if not records:
        print("⚠ 관측치 없음")
        return

    # ── 전체 ─────────────────────────────────────────────────────────────────
    print("\n" + "=" * 62)
    print("[ 전체 (PIT universe, 2020-2025) ]")
    g  = make_groups(records)
    A  = [r["alpha_20d"] for r in g["A"]]
    B  = [r["alpha_20d"] for r in g["B"]]
    D  = [r["alpha_20d"] for r in g["D"]]
    sA, sB, sD = stats(A), stats(B), stats(D)
    print(f"\n  {'그룹':<6} {'n':>6} {'mean':>7} {'median':>7} {'hit%':>6} {'P10':>7}")
    print("  " + "-" * 42)
    for name, grp_key in [("A", "A"), ("B", "B"), ("C", "C"), ("D", "D")]:
        s = stats([r["alpha_20d"] for r in g[grp_key]])
        if s["n"] == 0:
            print(f"  {name:<6} {'0':>6}")
        else:
            print(f"  {name:<6} {s['n']:>6} {s['mean']:>7} {s['median']:>7} {s['hit_rate']:>6} {s['P10']:>7}")
    if sA["n"] and sD["n"]:
        a_d = f"{sA['mean']-sD['mean']:+.2f}pp"
        a_b = f"{sA['mean']-sB['mean']:+.2f}pp" if sB["n"] else "N/A"
        print(f"  → A-D: {a_d}  /  A-B: {a_b}")

    # ── 연도별 ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 62)
    print("[ 연도별 (PIT universe, 레짐 병기) ]")
    for yr in sorted(SIGNAL_TO_RISK_YEAR.keys()):
        yr_recs   = [r for r in records if r["year"] == yr]
        risk_yr   = SIGNAL_TO_RISK_YEAR[yr]
        pit_n     = len(pit.get(yr, set()))
        print_year(yr, yr_recs, pit_n, 0)

    # ── 참고: 유니버스 크기 비교 ──────────────────────────────────────────────
    print("\n" + "=" * 62)
    print("[ 유니버스 크기 비교 ]")
    print(f"  {'연도':<6} {'PIT (E>=22)':>12} {'2026-fixed(571)':>16} {'full-market':>12}")
    print("  " + "-" * 48)
    full_counts = {yr: 2955 for yr in SIGNAL_TO_RISK_YEAR}  # investor_flow 전체 종목 수 (근사)
    for yr in sorted(SIGNAL_TO_RISK_YEAR.keys()):
        pit_n = len(pit.get(yr, set()))
        print(f"  {yr:<6} {pit_n:>12} {'571':>16} {full_counts.get(yr,'?'):>12}")

    print()
    print("[ 해석 제한 ]")
    print("  - 'Historical Replay' = v8.5.19 소급, 당시 실제 스크리너 ≠")
    print("  - Earnings 근사 (ROE 기반) / Supply 중립 고정")
    print("  - 2025 risk_year=2024: ROE NULL → 검증 보류")


if __name__ == "__main__":
    main()
