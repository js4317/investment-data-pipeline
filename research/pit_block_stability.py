#!/usr/bin/env python3
"""
Stage 10 — Composition-adjusted A-D 시간 안정성 검증 (사전등록 2026-09-22)
==========================================================================
목적: Stage 9 잔여 A-D (Bull +0.57pp / Bear·Sideways -0.59pp)가
      연도 내부 6개월 블록에서도 방향이 반복되는가?

설계:
  - 비중첩 6개월 블록 (2020H1 ~ 2025H2, 총 12개)
  - Stage 9와 동일한 통제 변수 (momentum_60d, realized_vol_20d)
  - Stage 9와 동일한 bucket cutoffs (pooled A+D 전체 기준)
  - 새로운 threshold 없음 — 방향 패턴 기술
  - OLS와 bucket 방향 일치 여부 함께 보고
"""
from __future__ import annotations
import sqlite3, statistics, math
from pathlib import Path

ROOT     = Path(__file__).resolve().parent.parent
FLOW_DB  = ROOT / "database" / "research.db"
OHLCV_DB = ROOT / "database" / "market_ohlcv.db"
KOSPI    = "1001"

EARNINGS_GATE = 22
MOM_WINDOW    = 60
VOL_WINDOW    = 20
N_BUCKETS     = 5

SIGNAL_TO_RISK_YEAR = {
    "2020": 2019, "2021": 2020, "2022": 2021,
    "2023": 2022, "2024": 2023, "2025": 2024,
}

# ── 6개월 블록 정의 ────────────────────────────────────────────────────────────
BLOCKS = [
    ("2020H1", "2020-01-01", "2020-06-30"),
    ("2020H2", "2020-07-01", "2020-12-31"),
    ("2021H1", "2021-01-01", "2021-06-30"),
    ("2021H2", "2021-07-01", "2021-12-31"),
    ("2022H1", "2022-01-01", "2022-06-30"),
    ("2022H2", "2022-07-01", "2022-12-31"),
    ("2023H1", "2023-01-01", "2023-06-30"),
    ("2023H2", "2023-07-01", "2023-12-31"),
    ("2024H1", "2024-01-01", "2024-06-30"),
    ("2024H2", "2024-07-01", "2024-12-31"),
    ("2025H1", "2025-01-01", "2025-06-30"),
    ("2025H2", "2025-07-01", "2025-12-31"),
]

# ── 데이터 로드 (Stage 9와 동일) ─────────────────────────────────────────────

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
    return {(d, t): (fn, pn) for d, t, fn, pn in rows}

def load_universe(flow: dict, pit: dict) -> dict:
    universe = {}
    for (d, t) in flow:
        yr = d[:4]
        if yr in pit and t in pit[yr]:
            universe.setdefault(d, set()).add(t)
    return universe

def build_records(prices, trading_days, flow, universe, phase_by_year) -> list[dict]:
    td_idx = {d: i for i, d in enumerate(trading_days)}
    records = []
    need = max(VOL_WINDOW, MOM_WINDOW) + 1

    for signal_date, tickers in sorted(universe.items()):
        if signal_date not in td_idx:
            continue
        idx = td_idx[signal_date]
        if idx < need or idx + 20 >= len(trading_days):
            continue

        prev_date  = trading_days[idx - 1]
        entry_date = trading_days[idx + 1]
        exit_date  = trading_days[idx + 20]

        k_entry = prices.get((KOSPI, entry_date, "open")) or prices.get((KOSPI, entry_date, "close"))
        k_exit  = prices.get((KOSPI, exit_date, "close"))
        if not k_entry or not k_exit:
            continue
        kospi_ret = k_exit / k_entry - 1

        yr    = signal_date[:4]
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

            p_mom_start = prices.get((ticker, trading_days[idx - MOM_WINDOW], "close"))
            p_mom_end   = prices.get((ticker, trading_days[idx - 1], "close"))
            momentum = (p_mom_end / p_mom_start - 1) if p_mom_start and p_mom_end else None

            daily_rets = []
            for i in range(idx - VOL_WINDOW, idx):
                p0 = prices.get((ticker, trading_days[i], "close"))
                p1 = prices.get((ticker, trading_days[i + 1], "close"))
                if p0 and p1:
                    daily_rets.append(p1 / p0 - 1)
            realized_vol = (statistics.stdev(daily_rets)
                            if len(daily_rets) >= VOL_WINDOW // 2 and len(daily_rets) >= 2
                            else None)

            inv = flow.get((signal_date, ticker), (None, None))
            fn, pn = inv
            fp = fn is not None and fn > 0
            pp = pn is not None and pn > 0

            records.append({
                "signal_date": signal_date,
                "year":        yr,
                "phase":       phase,
                "return_1d":   return_1d,
                "alpha_20d":   alpha_20d,
                "fp":          fp,
                "pp":          pp,
                "momentum":    momentum,
                "vol":         realized_vol,
            })
    return records

# ── Bucket ────────────────────────────────────────────────────────────────────

def compute_boundaries(vals: list[float], n: int) -> list[float]:
    s = sorted(vals)
    return [s[int(len(s) * i / n)] for i in range(1, n)]

def assign_bucket(val: float, boundaries: list[float]) -> int:
    for i, b in enumerate(boundaries):
        if val <= b:
            return i
    return len(boundaries)

# ── Stratified A-D ────────────────────────────────────────────────────────────

def stratified_ad(recs: list[dict], mom_bdry: list[float], vol_bdry: list[float]) -> dict:
    down = [r for r in recs if r["return_1d"] < 0
            and r["momentum"] is not None and r["vol"] is not None]
    for r in down:
        r["_st"] = (assign_bucket(r["momentum"], mom_bdry),
                    assign_bucket(r["vol"],       vol_bdry))

    strata = {}
    for st in set(r["_st"] for r in down):
        sr = [r for r in down if r["_st"] == st]
        A  = [r["alpha_20d"] for r in sr if r["fp"] and r["pp"]]
        D  = [r["alpha_20d"] for r in sr if not r["fp"] and not r["pp"]]
        if A and D:
            strata[st] = {"ad": statistics.mean(A) - statistics.mean(D), "n": len(A) + len(D)}

    if not strata:
        return {"sad": None, "used": 0, "excl": N_BUCKETS**2, "cov": 0.0}

    total_n = sum(v["n"] for v in strata.values())
    sad = sum(v["ad"] * v["n"] / total_n for v in strata.values())
    ad_pool = [r for r in down if (r["fp"] and r["pp"]) or (not r["fp"] and not r["pp"])]
    return {
        "sad":  round(sad, 3),
        "used": len(strata),
        "excl": N_BUCKETS**2 - len(strata),
        "cov":  round(total_n / max(len(ad_pool), 1), 3),
    }

def ols_ad(recs: list[dict]) -> float | None:
    try:
        import numpy as np
    except ImportError:
        return None
    down = [r for r in recs if r["return_1d"] < 0
            and r["momentum"] is not None and r["vol"] is not None]
    ad = [r for r in down if (r["fp"] and r["pp"]) or (not r["fp"] and not r["pp"])]
    if len(ad) < 10:
        return None
    y = np.array([r["alpha_20d"] for r in ad])
    x = np.array([[1.0, r["momentum"], r["vol"],
                   1.0 if (r["fp"] and r["pp"]) else 0.0] for r in ad])
    try:
        coef, _, _, _ = np.linalg.lstsq(x, y, rcond=None)
        return round(float(coef[3]), 3)
    except Exception:
        return None

def raw_ad(recs: list[dict]) -> float | None:
    down = [r for r in recs if r["return_1d"] < 0]
    A = [r["alpha_20d"] for r in down if r["fp"] and r["pp"]]
    D = [r["alpha_20d"] for r in down if not r["fp"] and not r["pp"]]
    return round(statistics.mean(A) - statistics.mean(D), 3) if A and D else None

# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 72)
    print("Stage 10 — Composition-adjusted A-D 시간 안정성 검증")
    print("6개월 비중첩 블록 / Stage 9와 동일 설계")
    print("=" * 72)
    print()

    pit           = load_pit_tickers()
    phase_by_year = load_phase_by_year()
    all_tickers   = {KOSPI} | {t for s in pit.values() for t in s}
    print(f"가격 로드: KOSPI + {len(all_tickers)-1}개 종목")
    prices       = load_prices(all_tickers)
    trading_days = load_trading_days()
    flow         = load_investor_flow()
    universe     = load_universe(flow, pit)

    print("레코드 구성 중...")
    records = build_records(prices, trading_days, flow, universe, phase_by_year)
    print(f"총 관측치: {len(records):,}\n")

    # ── pooled bucket cutoffs (Stage 9와 동일) ────────────────────────────────
    down_ad = [r for r in records if r["return_1d"] < 0
               and r["momentum"] is not None and r["vol"] is not None
               and ((r["fp"] and r["pp"]) or (not r["fp"] and not r["pp"]))]
    mom_bdry = compute_boundaries([r["momentum"] for r in down_ad], N_BUCKETS)
    vol_bdry = compute_boundaries([r["vol"]      for r in down_ad], N_BUCKETS)
    print(f"momentum 경계: {[round(b*100,2) for b in mom_bdry]}%")
    print(f"vol      경계: {[round(b*100,4) for b in vol_bdry]}")
    print()

    # ── 블록별 분석 ───────────────────────────────────────────────────────────
    print("=" * 72)
    print(f"  {'블록':<10} {'레짐':<12} {'A n':>5} {'D n':>6}  {'Raw':>7}  {'Strat':>7}  {'OLS':>7}  {'Cov':>6}  {'Used/25':>8}  방향일치")
    print("  " + "-" * 70)

    bull_blocks = []
    bsw_blocks  = []

    for block_name, start, end in BLOCKS:
        yr   = block_name[:4]
        phase = phase_by_year.get(yr, "?")
        brecs = [r for r in records if start <= r["signal_date"] <= end]
        if not brecs:
            continue

        down  = [r for r in brecs if r["return_1d"] < 0]
        n_a   = sum(1 for r in down if r["fp"] and r["pp"])
        n_d   = sum(1 for r in down if not r["fp"] and not r["pp"])

        raw  = raw_ad(brecs)
        strt = stratified_ad(brecs, mom_bdry, vol_bdry)
        sad  = strt["sad"]
        ols  = ols_ad(brecs)

        # 방향 일치 여부 (bucket vs OLS)
        if sad is not None and ols is not None:
            match = "✓" if (sad * ols > 0) or (abs(sad) < 0.05 and abs(ols) < 0.05) else "✗"
        else:
            match = "?"

        sad_str = f"{sad:+.3f}" if sad is not None else "  N/A"
        ols_str = f"{ols:+.3f}" if ols is not None else "  N/A"
        raw_str = f"{raw:+.3f}" if raw is not None else "  N/A"
        cov_str = f"{strt['cov']:.0%}" if strt["cov"] else "  N/A"

        print(f"  {block_name:<10} {phase:<12} {n_a:>5} {n_d:>6}  {raw_str}  {sad_str}  {ols_str}  {cov_str}  {strt['used']:>2}/{N_BUCKETS**2:<2}  {match}")

        if sad is not None:
            if phase == "Bull":
                bull_blocks.append(sad)
            elif phase in ("Bear", "Sideways"):
                bsw_blocks.append(sad)

    # ── 방향 요약 ─────────────────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("[ 방향 요약 (Stratified A-D 기준) ]")
    print()
    if bull_blocks:
        pos_bull = sum(1 for v in bull_blocks if v > 0)
        print(f"  Bull 블록:          {len(bull_blocks)}개 중 {pos_bull}개 양수"
              f"  (mean={round(statistics.mean(bull_blocks),3):+.3f}pp"
              f", median={round(statistics.median(bull_blocks),3):+.3f}pp)")
    if bsw_blocks:
        neg_bsw = sum(1 for v in bsw_blocks if v < 0)
        print(f"  Bear/Sideways 블록: {len(bsw_blocks)}개 중 {neg_bsw}개 음수"
              f"  (mean={round(statistics.mean(bsw_blocks),3):+.3f}pp"
              f", median={round(statistics.median(bsw_blocks),3):+.3f}pp)")

    print()
    print("[ 해석 제한 ]")
    print("  - 6개월 블록당 A 그룹 n이 작아 strata coverage 주의")
    print("  - 새로운 threshold 없음 — 방향 패턴 기술만")
    print("  - 20D alpha는 날짜간 중첩 있음 — 블록 간 완전 독립 아님")
    print("  - OLS와 bucket 방향 다른 블록은 결과 민감성 기록")


if __name__ == "__main__":
    main()
