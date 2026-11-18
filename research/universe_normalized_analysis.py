#!/usr/bin/env python3
"""
Universe-Normalized A-D 검증
==============================
동기: 기존 supply_dip_analysis.py는 2023-2025에 전 상장 종목, 2026에 스크리너 유니버스를
사용해 연도 간 유니버스가 달랐다. 유니버스를 통일하면 연도별 A-D 차이에서
"종목 구성 변화"를 제거하고 "시장 국면 변화"만 비교할 수 있다.

유니버스 정의 (2026 스크리너 기준 고정):
  2026 retro_calibration + snapshots에 등장한 모든 종목 집합 (n=571, 99% flow 커버리지)
  → 2023-2025에도 동일 종목 집합만 사용

주의:
  2026 기준 선별 종목이므로 성과 좋은 쪽으로 survivorship bias가 있다.
  이 분석의 목적은 "엔진이 관심 갖는 종목 내에서 시장 국면별 A-D가 얼마나 안정적인가" 확인.
  원본 전체 유니버스 결과(supply_dip_analysis.py)는 보존하며 대체하지 않는다.

출력: A-D / A-B / n / hit / P10 (연도별)
"""
from __future__ import annotations
import json, sqlite3, statistics
from pathlib import Path

ROOT     = Path(__file__).resolve().parent.parent
FLOW_DB  = ROOT / "database" / "research.db"
OHLCV_DB = ROOT / "database" / "market_ohlcv.db"
SNAP_DIR = ROOT / "backtests" / "snapshots"
KOSPI    = "1001"
YEAR_2026_START = "2026-01-01"

# ── 2026 스크리너 유니버스 로드 ───────────────────────────────────────────────
def load_screener_tickers() -> set[str]:
    """retro_calibration + snapshots에 등장한 2026 종목 집합"""
    tickers: set[str] = set()
    with sqlite3.connect(FLOW_DB) as c:
        for (t,) in c.execute(
            "SELECT DISTINCT ticker FROM retro_calibration WHERE signal_date >= ?",
            (YEAR_2026_START,)
        ):
            tickers.add(t)
    for f in sorted(SNAP_DIR.glob("*.json")):
        raw = json.loads(f.read_text())
        d = raw.get("date") or f.stem
        if d and d >= YEAR_2026_START:
            for e in raw.get("entries", []):
                if "ticker" in e:
                    tickers.add(e["ticker"])
    return tickers

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

# ── 유니버스 (날짜 × 종목) ───────────────────────────────────────────────────
def load_universe(flow: dict, screener: set[str]) -> dict[str, set[str]]:
    """
    2023-2025: investor_flow 중 screener 필터 종목만
    2026     : retro_calibration + snapshots (기존과 동일)
    """
    universe: dict[str, set[str]] = {}

    # 2023-2025 — screener 필터
    for (d, t) in flow:
        if d < YEAR_2026_START and t in screener:
            universe.setdefault(d, set()).add(t)

    # 2026 — 기존 로직 그대로
    with sqlite3.connect(FLOW_DB) as c:
        for d, t in c.execute(
            "SELECT DISTINCT signal_date, ticker FROM retro_calibration WHERE signal_date >= ?",
            (YEAR_2026_START,)
        ):
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

def print_year(yr: str, recs: list[dict]):
    dates = len(set(r["signal_date"] for r in recs))
    g = make_groups(recs)
    A = [r["alpha_20d"] for r in g["A"]]
    B = [r["alpha_20d"] for r in g["B"]]
    D = [r["alpha_20d"] for r in g["D"]]

    sA, sB, sD = stats(A), stats(B), stats(D)

    a_d = f"{sA['mean']-sD['mean']:+.2f}pp" if sA["n"] and sD["n"] else "N/A"
    a_b = f"{sA['mean']-sB['mean']:+.2f}pp" if sA["n"] and sB["n"] else "N/A"

    print(f"\n  ── {yr} ({dates}일 / 전체 {len(recs):,}관측 / 하락 {sum(1 for r in recs if r['return_1d']<0):,}) ──")
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
    print("=" * 60)
    print("Universe-Normalized A-D 검증")
    print("유니버스: 2026 스크리너 종목 고정 적용 (2023-2026)")
    print("=" * 60)

    print("\n데이터 로딩...")
    screener     = load_screener_tickers()
    prices       = load_prices()
    trading_days = load_trading_days()
    flow         = load_investor_flow()
    universe     = load_universe(flow, screener)

    print(f"스크리너 유니버스: {len(screener)} tickers")
    print(f"price DB 범위: {trading_days[0]} ~ {trading_days[-1]}")

    print("\n레코드 구성...")
    records = build_records(prices, trading_days, flow, universe)
    n_dates = len(set(r["signal_date"] for r in records))
    print(f"총 관측치: {len(records):,}건 ({n_dates}일)")

    # ── 전체 ─────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("[ 전체 (normalized) ]")
    g = make_groups(records)
    A = [r["alpha_20d"] for r in g["A"]]
    B = [r["alpha_20d"] for r in g["B"]]
    D = [r["alpha_20d"] for r in g["D"]]
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
        print(f"  → A-D: {sA['mean']-sD['mean']:+.2f}pp  /  A-B: {sA['mean']-sB['mean']:+.2f}pp" if sB["n"] else f"  → A-D: {sA['mean']-sD['mean']:+.2f}pp")

    # ── 연도별 ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("[ 연도별 (normalized) ]")
    for yr in sorted(set(r["year"] for r in records)):
        print_year(yr, [r for r in records if r["year"] == yr])

    print("\n" + "=" * 60)
    print("[ 해석 주의 ]")
    print("  - 유니버스 = 2026 스크리너 종목 → survivorship bias 있음")
    print("  - 2023-2025의 동일 종목이 당시 스크리너 통과 여부와 무관하게 포함됨")
    print("  - 원본 전체 유니버스 결과: research/supply_dip_analysis.py 참조")


if __name__ == "__main__":
    main()
