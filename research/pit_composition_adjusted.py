#!/usr/bin/env python3
"""
Stage 9 — Composition-adjusted A-D 검증 (사전등록 2026-09-22)
=============================================================
목적: 60D momentum × 20D vol bucket stratification으로 구성효과를 제거한 후
      A-D 성과 차이가 남는가?

Primary: 5분위 × 5분위 = 25 strata, pooled A+D 하락일 기준 고정 cutoffs
Robustness: OLS residualization (numpy 필요)

Exit criterion:
  Absorption-consistent: Stratified A-D와 raw A-D 같은 부호
  Attenuated:            같은 부호, |stratified| < 50% × |raw|
  Composition-explained: 부호 역전 또는 |stratified| < 0.1pp
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
N_BUCKETS     = 5   # 5분위
ATTENUATION_THRESHOLD = 0.5   # |stratified| < 50% × |raw| → Attenuated
ZERO_THRESHOLD        = 0.1   # |stratified| < 0.1pp → Composition-explained

SIGNAL_TO_RISK_YEAR = {
    "2020": 2019, "2021": 2020, "2022": 2021,
    "2023": 2022, "2024": 2023, "2025": 2024,
}

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

# ── 레코드 구성 ───────────────────────────────────────────────────────────────

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

            # momentum: [idx-MOM_WINDOW] → [idx-1]
            p_mom_start = prices.get((ticker, trading_days[idx - MOM_WINDOW], "close"))
            p_mom_end   = prices.get((ticker, trading_days[idx - 1], "close"))
            if not p_mom_start or not p_mom_end:
                momentum = None
            else:
                momentum = p_mom_end / p_mom_start - 1

            # realized vol: daily returns over [idx-VOL_WINDOW, idx-1] → std
            daily_rets = []
            for i in range(idx - VOL_WINDOW, idx):
                p0 = prices.get((ticker, trading_days[i], "close"))
                p1 = prices.get((ticker, trading_days[i + 1], "close"))
                if p0 and p1:
                    daily_rets.append(p1 / p0 - 1)
            if len(daily_rets) >= VOL_WINDOW // 2:
                realized_vol = statistics.stdev(daily_rets) if len(daily_rets) >= 2 else None
            else:
                realized_vol = None

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
    """n분위의 n-1개 경계값 (pooled 데이터 기준, 사전 고정)"""
    s = sorted(vals)
    boundaries = []
    for i in range(1, n):
        idx = int(len(s) * i / n)
        boundaries.append(s[idx])
    return boundaries

def assign_bucket(val: float, boundaries: list[float]) -> int:
    for i, b in enumerate(boundaries):
        if val <= b:
            return i
    return len(boundaries)

# ── 통계 ─────────────────────────────────────────────────────────────────────

def mean_safe(vals: list[float]) -> float | None:
    return statistics.mean(vals) if vals else None

def raw_ad(recs: list[dict]) -> float | None:
    down = [r for r in recs if r["return_1d"] < 0]
    A = [r["alpha_20d"] for r in down if r["fp"] and r["pp"]]
    D = [r["alpha_20d"] for r in down if not r["fp"] and not r["pp"]]
    if not A or not D:
        return None
    return statistics.mean(A) - statistics.mean(D)

def stratified_ad(recs: list[dict], mom_bdry: list[float], vol_bdry: list[float]) -> dict:
    """
    25 strata에서 A-D 계산 후 pooled 가중 평균.
    반환: {stratified_ad, n_strata_used, n_strata_excluded, strata_coverage}
    """
    down = [r for r in recs if r["return_1d"] < 0
            and r["momentum"] is not None and r["vol"] is not None]

    # bucket 할당
    for r in down:
        r["_mb"] = assign_bucket(r["momentum"], mom_bdry)
        r["_vb"] = assign_bucket(r["vol"],      vol_bdry)
        r["_st"] = (r["_mb"], r["_vb"])

    # stratum별 A-D
    strata_results = {}
    for stratum in set(r["_st"] for r in down):
        sr = [r for r in down if r["_st"] == stratum]
        A  = [r["alpha_20d"] for r in sr if r["fp"] and r["pp"]]
        D  = [r["alpha_20d"] for r in sr if not r["fp"] and not r["pp"]]
        if A and D:
            strata_results[stratum] = {
                "ad": statistics.mean(A) - statistics.mean(D),
                "n":  len(A) + len(D),
            }

    if not strata_results:
        return {"stratified_ad": None, "n_strata_used": 0,
                "n_strata_excluded": N_BUCKETS ** 2, "strata_coverage": 0.0}

    total_n = sum(v["n"] for v in strata_results.values())
    sad = sum(v["ad"] * v["n"] / total_n for v in strata_results.values())

    n_used     = len(strata_results)
    n_excluded = N_BUCKETS ** 2 - n_used
    total_down_ad = sum(1 for r in down if (r["fp"] and r["pp"]) or (not r["fp"] and not r["pp"]))

    return {
        "stratified_ad":   round(sad, 3),
        "n_strata_used":   n_used,
        "n_strata_excluded": n_excluded,
        "strata_coverage": round(total_n / max(total_down_ad, 1), 3),
    }

def verdict(raw: float | None, strat: float | None) -> str:
    if raw is None or strat is None:
        return "판정 불가"
    if abs(strat) < ZERO_THRESHOLD:
        return "Composition-explained"
    if raw * strat < 0:  # 부호 반전
        return "Composition-explained"
    if abs(strat) < ATTENUATION_THRESHOLD * abs(raw):
        return "Attenuated"
    return "Absorption-consistent"

# ── OLS (numpy 선택적) ────────────────────────────────────────────────────────

def ols_ad(recs: list[dict]) -> float | None:
    """
    alpha_20d를 momentum + vol + is_A(dummy) 로 OLS 회귀.
    is_A 계수 = composition-adjusted A-D.
    A와 D 그룹만 사용.
    """
    try:
        import numpy as np
    except ImportError:
        return None

    down = [r for r in recs if r["return_1d"] < 0
            and r["momentum"] is not None and r["vol"] is not None]
    ad   = [r for r in down if (r["fp"] and r["pp"]) or (not r["fp"] and not r["pp"])]
    if len(ad) < 10:
        return None

    y = np.array([r["alpha_20d"]  for r in ad])
    x = np.array([
        [1.0, r["momentum"], r["vol"],
         1.0 if (r["fp"] and r["pp"]) else 0.0]
        for r in ad
    ])
    try:
        coef, _, _, _ = np.linalg.lstsq(x, y, rcond=None)
        return round(float(coef[3]), 3)  # is_A coefficient
    except Exception:
        return None

# ── 출력 ─────────────────────────────────────────────────────────────────────

def print_section(label: str, recs: list[dict],
                  mom_bdry: list[float], vol_bdry: list[float]):
    down = [r for r in recs if r["return_1d"] < 0]
    A = [r for r in down if r["fp"] and r["pp"]]
    D = [r for r in down if not r["fp"] and not r["pp"]]
    if not A or not D:
        print(f"\n  [{label}] 데이터 부족 (A={len(A)}, D={len(D)})")
        return

    raw    = raw_ad(recs)
    strat  = stratified_ad(recs, mom_bdry, vol_bdry)
    sad    = strat["stratified_ad"]
    v      = verdict(raw, sad)
    ols    = ols_ad(recs)

    print(f"\n  [ {label} ]  A={len(A)}건 / D={len(D)}건")
    print(f"    Raw A-D        = {raw:+.3f}pp")
    print(f"    Stratified A-D = {sad:+.3f}pp" if sad is not None else "    Stratified A-D = N/A")
    if raw is not None and sad is not None:
        attenuation = (1 - abs(sad) / abs(raw)) * 100 if raw != 0 else float("nan")
        print(f"    Attenuation    = {attenuation:.1f}%")
    print(f"    Strata used    = {strat['n_strata_used']}/{N_BUCKETS**2}"
          f"  (excluded: {strat['n_strata_excluded']}, coverage: {strat['strata_coverage']:.2%})")
    if ols is not None:
        print(f"    OLS A-D        = {ols:+.3f}pp  [Robustness]")
    else:
        print(f"    OLS A-D        = N/A (numpy 없음)")
    print(f"    → 판정: {v}")

# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 64)
    print("Stage 9 — Composition-adjusted A-D 검증")
    print(f"Primary: {N_BUCKETS}분위 × {N_BUCKETS}분위 = {N_BUCKETS**2} strata")
    print("Controls: momentum_60d × realized_vol_20d")
    print("=" * 64)
    print()

    pit           = load_pit_tickers()
    phase_by_year = load_phase_by_year()
    all_tickers   = {KOSPI} | {t for s in pit.values() for t in s}
    print(f"가격 로드 대상: KOSPI + {len(all_tickers)-1}개 종목")
    prices       = load_prices(all_tickers)
    trading_days = load_trading_days()
    flow         = load_investor_flow()
    universe     = load_universe(flow, pit)

    print("레코드 구성 중...")
    records = build_records(prices, trading_days, flow, universe, phase_by_year)
    print(f"총 관측치: {len(records):,}")

    # ── Bucket cutoffs: pooled A+D 하락일 기준 사전 고정 ─────────────────────
    print()
    print("Bucket cutoffs 계산 (pooled A+D 하락일, 사전 고정)...")
    down_ad = [r for r in records if r["return_1d"] < 0
               and r["momentum"] is not None and r["vol"] is not None
               and ((r["fp"] and r["pp"]) or (not r["fp"] and not r["pp"]))]

    mom_vals = [r["momentum"] for r in down_ad]
    vol_vals = [r["vol"]      for r in down_ad]
    mom_bdry = compute_boundaries(mom_vals, N_BUCKETS)
    vol_bdry = compute_boundaries(vol_vals, N_BUCKETS)

    print(f"  momentum 5분위 경계: {[round(b*100,2) for b in mom_bdry]}%")
    print(f"  vol      5분위 경계: {[round(b*100,4) for b in vol_bdry]}")
    print(f"  pooled A+D 하락일: {len(down_ad):,}건")

    # ── 전체 ─────────────────────────────────────────────────────────────────
    print()
    print("=" * 64)
    print("[ 결과 ]")
    print_section("전체 (2020–2025)", records, mom_bdry, vol_bdry)

    # ── 레짐별 ───────────────────────────────────────────────────────────────
    bull_recs = [r for r in records if r["phase"] == "Bull"]
    bsw_recs  = [r for r in records if r["phase"] in ("Bear", "Sideways")]
    print_section("Bull", bull_recs, mom_bdry, vol_bdry)
    print_section("Bear/Sideways", bsw_recs, mom_bdry, vol_bdry)

    # ── 연도별 (탐색적 참고) ─────────────────────────────────────────────────
    print()
    print("=" * 64)
    print("[ 연도별 (탐색적 참고) ]")
    for yr in sorted(SIGNAL_TO_RISK_YEAR.keys()):
        yr_recs = [r for r in records if r["year"] == yr]
        ph = phase_by_year.get(yr, "?")
        print_section(f"{yr}년 [{ph}]", yr_recs, mom_bdry, vol_bdry)

    # ── 해석 제한 ─────────────────────────────────────────────────────────────
    print()
    print("=" * 64)
    print("[ 해석 제한 ]")
    print("  - Absorption-consistent 판정 ≠ 인과 주장")
    print("  - OLS와 bucket 방향이 같을 때만 'robust'로 기술")
    print(f"  - strata coverage < 80%는 표본 부족 주의")
    print("  - PBR/market_cap 통제 미포함 (Stage 8 기준 미달)")


if __name__ == "__main__":
    main()
