#!/usr/bin/env python3
"""
PIT Universe × 레짐별 A-D 검증 (사전등록 Stage 6)
=================================================
목적: v8.5.19 PIT universe에서 레짐(Bull / Bear·Sideways)에 따라
      A-D 효과가 달라지는가?

Primary criterion (사전등록):
  Δ_regime = (A-D)_Bull − (A-D)_BearSideways > 0

판정:
  Support:   Δ_regime > 0 AND Bull 연도 A-D 모두 > 0 AND BearSideways 연도 A-D 모두 < 0
  Mixed:     Δ_regime > 0 이지만 연도별 부호 일부 불일치
  Refute:    Δ_regime ≤ 0

주의:
  - Bear와 Sideways는 이번 검증에서 합산 (사전등록 조건 변경 금지)
  - 결과 확인 후 레짐 기준 변경 금지
  - 어떤 판정이나 production 미반영
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

# ── 데이터 로드 (pit_universe_analysis.py와 동일) ─────────────────────────────

def load_pit_tickers() -> dict[str, set[str]]:
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

def load_phase_by_year() -> dict[str, str]:
    """research_year_meta.phase → signal_year 기준으로 반환"""
    with sqlite3.connect(FLOW_DB) as c:
        rows = c.execute(
            "SELECT year, phase FROM research_year_meta ORDER BY year"
        ).fetchall()
    result = {str(yr): ph for yr, ph in rows}
    print("  레짐 매핑:")
    for yr, ph in sorted(result.items()):
        if yr in SIGNAL_TO_RISK_YEAR:
            print(f"    {yr}: {ph}")
    return result

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

def load_universe(flow: dict, pit: dict[str, set[str]]) -> dict[str, set[str]]:
    universe: dict[str, set[str]] = {}
    covered = set(pit.keys())
    for (d, t) in flow:
        yr = d[:4]
        if yr in covered and t in pit[yr]:
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
                "signal_date":      signal_date,
                "ticker":           ticker,
                "year":             yr,
                "phase":            phase,
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
    fp = lambda r: r["foreign_positive"]
    pp = lambda r: r["pension_positive"]
    return {
        "A": [r for r in down if fp(r) and pp(r)],
        "B": [r for r in down if fp(r) and not pp(r)],
        "C": [r for r in down if not fp(r) and pp(r)],
        "D": [r for r in down if not fp(r) and not pp(r)],
    }

def print_group_table(label: str, records: list[dict]):
    g = make_groups(records)
    sA = stats([r["alpha_20d"] for r in g["A"]])
    sB = stats([r["alpha_20d"] for r in g["B"]])
    sD = stats([r["alpha_20d"] for r in g["D"]])
    print(f"\n  {label}")
    print(f"  {'그룹':<6} {'n':>6} {'mean':>7} {'median':>7} {'hit%':>6} {'P10':>7}")
    print("  " + "-" * 42)
    for name in ["A", "B", "C", "D"]:
        s = stats([r["alpha_20d"] for r in g[name]])
        if s["n"] == 0:
            print(f"  {name:<6} {'0':>6}")
        else:
            print(f"  {name:<6} {s['n']:>6} {s['mean']:>7} {s['median']:>7} {s['hit_rate']:>6} {s['P10']:>7}")
    a_d = f"{sA['mean']-sD['mean']:+.2f}pp" if sA["n"] and sD["n"] else "N/A"
    a_b = f"{sA['mean']-sB['mean']:+.2f}pp" if sA["n"] and sB["n"] else "N/A"
    print(f"  → A-D: {a_d}  /  A-B: {a_b}")
    return sA, sD

# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 62)
    print("PIT Universe × 레짐별 A-D 검증 (Stage 6)")
    print("Primary: Δ_regime = (A-D)_Bull − (A-D)_BearSideways > 0")
    print("=" * 62)

    print("\n데이터 로딩...")
    pit          = load_pit_tickers()
    phase_by_year = load_phase_by_year()
    prices       = load_prices()
    trading_days = load_trading_days()
    flow         = load_investor_flow()
    universe     = load_universe(flow, pit)
    records      = build_records(prices, trading_days, flow, universe, phase_by_year)
    print(f"총 관측치: {len(records):,}")

    # ── 레짐별 ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 62)
    print("[ 레짐별 (Bull vs Bear/Sideways) ]")

    bull_recs = [r for r in records if r["phase"] == "Bull"]
    bear_sw_recs = [r for r in records if r["phase"] in ("Bear", "Sideways")]

    sA_bull, sD_bull = print_group_table("Bull", bull_recs)
    sA_bsw, sD_bsw   = print_group_table("Bear/Sideways", bear_sw_recs)

    # ── 연도별 (레짐 명시) ────────────────────────────────────────────────────
    print("\n" + "=" * 62)
    print("[ 연도별 (PIT, 레짐 병기) ]")
    yr_a_d = {}
    for yr in sorted(SIGNAL_TO_RISK_YEAR.keys()):
        yr_recs = [r for r in records if r["year"] == yr]
        ph = phase_by_year.get(yr, "?")
        sA, sD = print_group_table(f"{yr}년 [{ph}]", yr_recs)
        if sA["n"] and sD["n"]:
            yr_a_d[yr] = {"a_d": sA["mean"] - sD["mean"], "phase": ph}

    # ── Δ_regime 계산 및 판정 ──────────────────────────────────────────────
    print("\n" + "=" * 62)
    print("[ Primary: Δ_regime ]")

    if sA_bull["n"] and sD_bull["n"] and sA_bsw["n"] and sD_bsw["n"]:
        a_d_bull = sA_bull["mean"] - sD_bull["mean"]
        a_d_bsw  = sA_bsw["mean"] - sD_bsw["mean"]
        delta = a_d_bull - a_d_bsw

        print(f"\n  (A-D)_Bull          = {a_d_bull:+.2f}pp")
        print(f"  (A-D)_Bear/Sideways = {a_d_bsw:+.2f}pp")
        print(f"  Δ_regime            = {delta:+.2f}pp")

        # 판정
        bull_yrs   = [v for v in yr_a_d.values() if v["phase"] == "Bull"]
        bsw_yrs    = [v for v in yr_a_d.values() if v["phase"] in ("Bear", "Sideways")]
        bull_all_pos = all(v["a_d"] > 0 for v in bull_yrs)
        bsw_all_neg  = all(v["a_d"] < 0 for v in bsw_yrs)

        print(f"\n  Bull 연도별 A-D 모두 > 0: {bull_all_pos}")
        print(f"  Bear/Sideways 연도별 A-D 모두 < 0: {bsw_all_neg}")

        if delta > 0 and bull_all_pos and bsw_all_neg:
            verdict = "Support"
        elif delta > 0:
            verdict = "Mixed / Inconclusive"
        else:
            verdict = "Refute"

        print(f"\n  → 판정: {verdict}")
        print()
        print("  해석 제한:")
        print("  - Support = '사전 가설과 일치하는 패턴 관찰'. 신호 채택 아님.")
        print(f"  - 3개 연도(Bull {len(bull_yrs)}개 / Bear·Sideways {len(bsw_yrs)}개) — 최종 결론 아님.")
        print("  - Production 미반영.")
    else:
        print("  ⚠ 데이터 부족 — Δ_regime 계산 불가")

    # ── 해석 제한 ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 62)
    print("[ 해석 제한 ]")
    print("  - Bear와 Sideways 합산은 사전등록 조건. 분리 분석은 조건 변경임.")
    print("  - PIT universe = v8.5.19 Historical Replay. 당시 실제 스크리너 ≠.")
    print("  - 효과 크기(pp) 해석은 기술 목적. Primary criterion은 방향성만.")


if __name__ == "__main__":
    main()
