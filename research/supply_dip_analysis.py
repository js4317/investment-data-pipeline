#!/usr/bin/env python3
"""
수급 + 하락 가설 검증 (v3 — 881일)
=====================================
데이터 소스:
  - research.db    : investor_flow (foreign_net, pension_net 등)
  - research.db    : retro_calibration (2026 유니버스)
  - market_ohlcv.db: return_1d, alpha_20d 계산
  - backtests/snapshots/: 2026 추가 유니버스

그룹:
  A: 하락 + 외인 + 연기금
  B: 하락 + 외인만
  C: 하락 + 연기금만
  D: 하락만
  E: 전체 베이스라인

지표: n, mean_alpha, median_alpha, hit_rate, P10
유니버스:
  2023-2025: investor_flow에 있는 전체 종목 (KOSPI + KOSDAQ)
  2026     : retro_calibration + snapshots (스크리너 유니버스)
"""

from __future__ import annotations
import json, sqlite3, statistics
from pathlib import Path

ROOT      = Path(__file__).resolve().parent.parent
FLOW_DB   = ROOT / "database" / "research.db"
RETRO_DB  = ROOT / "database" / "research.db"
OHLCV_DB  = ROOT / "database" / "market_ohlcv.db"
SNAP_DIR  = ROOT / "backtests" / "snapshots"
KOSPI     = "1001"

YEAR_2026_START = "2026-01-01"

# ── 가격 로드 ─────────────────────────────────────────────────────────────────
def load_prices() -> dict[tuple[str, str, str], float]:
    with sqlite3.connect(OHLCV_DB) as c:
        rows = c.execute("SELECT ticker, trade_date, open, close FROM market_ohlcv").fetchall()
    p = {}
    for ticker, td, o, cl in rows:
        if cl: p[(ticker, td, "close")] = float(cl)
        if o:  p[(ticker, td, "open")]  = float(o)
    return p

def load_trading_days() -> list[str]:
    with sqlite3.connect(OHLCV_DB) as c:
        rows = c.execute(
            "SELECT DISTINCT trade_date FROM market_ohlcv WHERE ticker=? ORDER BY trade_date",
            (KOSPI,)
        ).fetchall()
    return [r[0] for r in rows]

# ── 투자자 플로우 로드 ────────────────────────────────────────────────────────
def load_investor_flow() -> dict[tuple[str, str], dict]:
    """(signal_date, ticker) → {foreign_net, pension_net}"""
    with sqlite3.connect(FLOW_DB) as c:
        rows = c.execute("""
            SELECT signal_date, ticker, foreign_net, pension_net
            FROM investor_flow
        """).fetchall()
    result = {}
    for d, t, fn, pn in rows:
        result[(d, t)] = {"foreign_net": fn, "pension_net": pn}
    return result

# ── 유니버스 로드 ─────────────────────────────────────────────────────────────
def load_universe(flow: dict) -> dict[str, set[str]]:
    """
    signal_date → set of tickers
    2023-2025: investor_flow에 있는 모든 종목
    2026     : retro_calibration + snapshots
    """
    universe: dict[str, set[str]] = {}

    # 2023-2025: investor_flow 전체 종목
    for (d, t) in flow:
        if d < YEAR_2026_START:
            universe.setdefault(d, set()).add(t)

    # 2026: retro_calibration
    if RETRO_DB.exists():
        with sqlite3.connect(RETRO_DB) as c:
            rows = c.execute(
                "SELECT DISTINCT signal_date, ticker FROM retro_calibration"
            ).fetchall()
        for d, t in rows:
            if d >= YEAR_2026_START:
                universe.setdefault(d, set()).add(t)

    # 2026: snapshots (retro에 없는 날짜만)
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
        entry_date = trading_days[idx + 1]   # T+1
        exit_date  = trading_days[idx + 20]  # T+20

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

            t_entry = prices.get((ticker, entry_date, "open")) or prices.get((ticker, entry_date, "close"))
            t_exit  = prices.get((ticker, exit_date,  "close"))
            if not t_entry or not t_exit:
                continue
            alpha_20d = (t_exit / t_entry - 1 - kospi_ret) * 100

            inv = flow.get((signal_date, ticker), {})
            foreign_net = inv.get("foreign_net")
            pension_net = inv.get("pension_net")

            records.append({
                "signal_date":      signal_date,
                "ticker":           ticker,
                "year":             signal_date[:4],
                "return_1d":        return_1d,
                "foreign_positive": foreign_net is not None and foreign_net > 0,
                "pension_positive": pension_net is not None and pension_net > 0,
                "foreign_net":      foreign_net,
                "pension_net":      pension_net,
                "alpha_20d":        alpha_20d,
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

def print_table(title: str, groups: dict, header_suffix: str = ""):
    label = f"{title}{header_suffix}"
    print(f"\n{label}")
    print(f"{'그룹':<22} {'n':>6} {'mean':>7} {'median':>7} {'hit%':>6} {'P10':>7}")
    print("-" * 62)
    for name, recs in groups.items():
        s = stats([r["alpha_20d"] for r in recs])
        if s["n"] == 0:
            print(f"{name:<22} {'0':>6}")
        else:
            print(f"{name:<22} {s['n']:>6} {s['mean']:>7} {s['median']:>7} {s['hit_rate']:>6} {s['P10']:>7}")

def make_groups(records: list[dict]) -> dict:
    down = [r for r in records if r["return_1d"] < 0]
    fp   = lambda r: r["foreign_positive"]
    pp   = lambda r: r["pension_positive"]
    return {
        "A: 하락+외인+연기금": [r for r in down if fp(r) and pp(r)],
        "B: 하락+외인만":      [r for r in down if fp(r) and not pp(r)],
        "C: 하락+연기금만":    [r for r in down if not fp(r) and pp(r)],
        "D: 하락만":           [r for r in down if not fp(r) and not pp(r)],
        "E: 전체(베이스라인)": records,
    }

# ── main ─────────────────────────────────────────────────────────────────────
def main():
    print("데이터 로딩...")
    prices       = load_prices()
    trading_days = load_trading_days()
    flow         = load_investor_flow()
    universe     = load_universe(flow)

    print(f"price DB 범위: {trading_days[0] if trading_days else 'N/A'} ~ {trading_days[-1] if trading_days else 'N/A'} ({len(trading_days)}일)")
    print(f"investor_flow: {len(flow)}건 (ticker×date)")
    print(f"유니버스 날짜수: {len(universe)}일")

    print("\n레코드 구성...")
    records = build_records(prices, trading_days, flow, universe)
    n_dates = len(set(r["signal_date"] for r in records))
    print(f"총 관측치: {len(records):,}건 ({n_dates}일)\n")

    # ── 전체 결과 ────────────────────────────────────────────────────────────
    print("=" * 62)
    print("[ 전체 ]")
    print_table("전체", make_groups(records))

    down_all = [r for r in records if r["return_1d"] < 0]
    print(f"\n하락일: {len(down_all):,}건 / 전체: {len(records):,}건 ({len(down_all)/len(records)*100:.0f}%)")
    no_flow = sum(1 for r in records if r["foreign_net"] is None)
    print(f"investor_flow 누락: {no_flow:,}건 ({no_flow/len(records)*100:.1f}%)")

    A_all = [r["alpha_20d"] for r in make_groups(records)["A: 하락+외인+연기금"]]
    B_all = [r["alpha_20d"] for r in make_groups(records)["B: 하락+외인만"]]
    if A_all and B_all:
        print(f"연기금 추가 효과 (A-B): {statistics.mean(A_all)-statistics.mean(B_all):+.2f}pp")

    # ── 연도별 결과 ──────────────────────────────────────────────────────────
    print("\n" + "=" * 62)
    print("[ 연도별 ]")
    years = sorted(set(r["year"] for r in records))
    for yr in years:
        yr_recs = [r for r in records if r["year"] == yr]
        yr_dates = len(set(r["signal_date"] for r in yr_recs))
        groups = make_groups(yr_recs)
        print_table(f"{yr}년 ({yr_dates}일 / {len(yr_recs):,}관측)", groups)

        A = [r["alpha_20d"] for r in groups["A: 하락+외인+연기금"]]
        D = [r["alpha_20d"] for r in groups["D: 하락만"]]
        if A and D:
            print(f"  A vs D: {statistics.mean(A)-statistics.mean(D):+.2f}pp", end="")
        B = [r["alpha_20d"] for r in groups["B: 하락+외인만"]]
        if A and B:
            print(f"  /  A vs B(연기금 효과): {statistics.mean(A)-statistics.mean(B):+.2f}pp", end="")
        print()


if __name__ == "__main__":
    main()
