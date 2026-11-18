#!/usr/bin/env python3
"""
Stage 8 — A/D 그룹 기초 특성 비교 (사전등록 2026-09-22)
=========================================================
목적: A/D 그룹이 레짐별로 다른 종목 특성을 갖는지 진단한다.

사전등록 변수:
  market_cap    — research_stock_year (risk_year 기준 연말)
  PBR           — research_stock_year (risk_year 기준 연말)
  realized_vol  — market_ohlcv, signal_date 직전 20영업일 일별 수익률 std
  momentum      — market_ohlcv, signal_date 직전 60영업일 수익률

보고값: median / mean / SMD(|A-D| / pooled_std)
Exit criterion: Composition similar / different / Mixed (|SMD| > 0.2 기준)

해석 제한:
  - Composition different ≠ Investor Absorption 효과 없음
  - 매칭·회귀 통제는 Stage 9에서 수행
"""
from __future__ import annotations
import sqlite3, statistics, math
from pathlib import Path

ROOT     = Path(__file__).resolve().parent.parent
FLOW_DB  = ROOT / "database" / "research.db"
OHLCV_DB = ROOT / "database" / "market_ohlcv.db"
KOSPI    = "1001"

EARNINGS_GATE  = 22
VOL_WINDOW     = 20   # realized_vol: 직전 N영업일
MOM_WINDOW     = 60   # momentum: 직전 N영업일

SMD_THRESHOLD  = 0.2  # |SMD| > 0.2 → "큰 차이" 기준

SIGNAL_TO_RISK_YEAR = {
    "2020": 2019,
    "2021": 2020,
    "2022": 2021,
    "2023": 2022,
    "2024": 2023,
    "2025": 2024,
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

def load_fundamentals(pit: dict[str, set[str]]) -> dict[tuple, dict]:
    """(ticker, risk_year) → {market_cap, pbr}
    research_stock_year.year = risk_year (연말 재무 데이터 기준)
    """
    all_tickers = {t for s in pit.values() for t in s}
    all_risk_yrs = list(set(SIGNAL_TO_RISK_YEAR.values()))
    placeholders_t = ",".join("?" * len(all_tickers))
    placeholders_y = ",".join("?" * len(all_risk_yrs))
    with sqlite3.connect(FLOW_DB) as c:
        rows = c.execute(
            f"""SELECT ticker, year, market_cap, pbr
                FROM research_stock_year
                WHERE ticker IN ({placeholders_t})
                  AND year IN ({placeholders_y})""",
            list(all_tickers) + all_risk_yrs
        ).fetchall()
    result = {}
    for ticker, yr, mc, pb in rows:
        result[(ticker, yr)] = {
            "market_cap": float(mc) if mc else None,
            "pbr":        float(pb) if pb else None,
        }
    return result

def load_prices_for_tickers(tickers: set[str]) -> dict:
    """(ticker, trade_date) → close"""
    placeholders = ",".join("?" * len(tickers))
    with sqlite3.connect(OHLCV_DB) as c:
        rows = c.execute(
            f"SELECT ticker, trade_date, close FROM market_ohlcv WHERE ticker IN ({placeholders})",
            list(tickers)
        ).fetchall()
    p = {}
    for ticker, td, cl in rows:
        if cl:
            p[(ticker, td)] = float(cl)
    return p

def load_trading_days() -> list[str]:
    with sqlite3.connect(OHLCV_DB) as c:
        return [r[0] for r in c.execute(
            "SELECT DISTINCT trade_date FROM market_ohlcv WHERE ticker=? ORDER BY trade_date",
            (KOSPI,)
        )]

def load_investor_flow() -> dict:
    """(signal_date, ticker) → (foreign_net, pension_net)"""
    with sqlite3.connect(FLOW_DB) as c:
        rows = c.execute(
            "SELECT signal_date, ticker, foreign_net, pension_net FROM investor_flow"
        ).fetchall()
    return {(d, t): (fn, pn) for d, t, fn, pn in rows}

def load_universe(flow: dict, pit: dict) -> dict:
    """flow keys = (signal_date, ticker). Returns {signal_date: set(tickers)}"""
    universe = {}
    for (d, t) in flow:
        yr = d[:4]
        if yr in pit and t in pit[yr]:
            universe.setdefault(d, set()).add(t)
    return universe

# ── 특성 계산 ─────────────────────────────────────────────────────────────────

def compute_vol_and_mom(ticker: str, signal_date: str, td_idx: dict, trading_days: list, prices: dict):
    """realized_vol(20D), momentum(60D) — None if insufficient data"""
    idx = td_idx.get(signal_date)
    if idx is None:
        return None, None
    # need max(VOL_WINDOW, MOM_WINDOW) + 1 days before signal_date
    need = max(VOL_WINDOW, MOM_WINDOW) + 1
    if idx < need:
        return None, None
    # momentum: close[idx-MOM_WINDOW] → close[idx-1]  (직전 60영업일)
    start_date = trading_days[idx - MOM_WINDOW]
    prev_date  = trading_days[idx - 1]
    p_start = prices.get((ticker, start_date))
    p_prev  = prices.get((ticker, prev_date))
    if not p_start or not p_prev:
        momentum = None
    else:
        momentum = p_prev / p_start - 1

    # realized_vol: std of daily returns over last VOL_WINDOW days
    daily_rets = []
    for i in range(idx - VOL_WINDOW, idx):
        p0 = prices.get((ticker, trading_days[i]))
        p1 = prices.get((ticker, trading_days[i + 1]))
        if p0 and p1:
            daily_rets.append(p1 / p0 - 1)
    if len(daily_rets) < VOL_WINDOW // 2:
        realized_vol = None
    else:
        realized_vol = statistics.stdev(daily_rets) if len(daily_rets) >= 2 else None

    return realized_vol, momentum

# ── 통계 ─────────────────────────────────────────────────────────────────────

def pooled_std(a: list[float], b: list[float]) -> float | None:
    if len(a) < 2 or len(b) < 2:
        return None
    var_a = statistics.variance(a)
    var_b = statistics.variance(b)
    n_a, n_b = len(a), len(b)
    return math.sqrt(((n_a - 1) * var_a + (n_b - 1) * var_b) / (n_a + n_b - 2))

def smd(a: list[float], b: list[float]) -> float | None:
    if not a or not b:
        return None
    ps = pooled_std(a, b)
    if not ps or ps == 0:
        return None
    return round((statistics.mean(a) - statistics.mean(b)) / ps, 3)

def summary(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0, "mean": None, "median": None}
    return {
        "n":      len(vals),
        "mean":   round(statistics.mean(vals), 4),
        "median": round(statistics.median(vals), 4),
    }

def print_var_table(var_name: str, a_vals: list, d_vals: list):
    sa, sd = summary(a_vals), summary(d_vals)
    s = smd(a_vals, d_vals)
    flag = ""
    if s is not None:
        flag = "  ★" if abs(s) > SMD_THRESHOLD else "  —"
    print(f"  {var_name:<16} A n={sa['n']:>4}  mean={sa['mean']:>10}  median={sa['median']:>10}")
    print(f"  {'':16} D n={sd['n']:>4}  mean={sd['mean']:>10}  median={sd['median']:>10}")
    print(f"  {'':16} SMD = {s}{flag}")

# ── 레코드 구성 ───────────────────────────────────────────────────────────────

def build_records(pit, phase_by_year, fundamentals, prices, trading_days, flow, universe):
    td_idx = {d: i for i, d in enumerate(trading_days)}
    records = []
    for signal_date, tickers in sorted(universe.items()):
        yr      = signal_date[:4]
        phase   = phase_by_year.get(yr, "Unknown")
        risk_yr = SIGNAL_TO_RISK_YEAR.get(yr)
        idx     = td_idx.get(signal_date)
        if idx is None or idx == 0:
            continue
        prev_date = trading_days[idx - 1]
        for ticker in tickers:
            t_close    = prices.get((ticker, signal_date))
            prev_close = prices.get((ticker, prev_date))
            if not t_close or not prev_close:
                continue
            return_1d = t_close / prev_close - 1
            fn, pn = flow.get((signal_date, ticker), (None, None))
            fp = fn is not None and fn > 0
            pp = pn is not None and pn > 0
            fund = fundamentals.get((ticker, risk_yr), {})
            vol, mom = compute_vol_and_mom(ticker, signal_date, td_idx, trading_days, prices)
            records.append({
                "signal_date": signal_date,
                "year":        yr,
                "phase":       phase,
                "return_1d":   return_1d,
                "fp":          fp,
                "pp":          pp,
                "market_cap":  fund.get("market_cap"),
                "pbr":         fund.get("pbr"),
                "vol":         vol,
                "momentum":    mom,
            })
    return records

# ── 그룹 분류 ─────────────────────────────────────────────────────────────────

def make_ad_groups(records):
    down = [r for r in records if r["return_1d"] < 0]
    A = [r for r in down if r["fp"] and r["pp"]]
    D = [r for r in down if not r["fp"] and not r["pp"]]
    return A, D

def extract(recs, key):
    return [r[key] for r in recs if r[key] is not None]

# ── 비교 출력 ─────────────────────────────────────────────────────────────────

def compare_groups(label: str, recs: list[dict]):
    A, D = make_ad_groups(recs)
    if not A or not D:
        print(f"\n  {label}: 데이터 부족 (A={len(A)}, D={len(D)})")
        return {}
    print(f"\n  [ {label} ]  A={len(A)}건 / D={len(D)}건")
    results = {}
    for var_key, var_name in [
        ("market_cap", "market_cap(억)"),
        ("pbr",        "PBR"),
        ("vol",        "realized_vol"),
        ("momentum",   "momentum(60D)"),
    ]:
        a_vals = extract(A, var_key)
        d_vals = extract(D, var_key)
        # market_cap: 억원 단위로 환산 (원 단위 저장 가정 → /1e8)
        if var_key == "market_cap":
            a_vals = [v / 1e8 for v in a_vals]
            d_vals = [v / 1e8 for v in d_vals]
        print_var_table(var_name, a_vals, d_vals)
        s = smd(a_vals, d_vals)
        results[var_key] = s
    return results

# ── 최종 판정 ─────────────────────────────────────────────────────────────────

def verdict(smd_by_var_regime: dict):
    """모든 비교 SMD를 취합해서 Composition similar / different / Mixed 판정"""
    large = []
    all_smds = []
    for regime, v_dict in smd_by_var_regime.items():
        for var, s in v_dict.items():
            if s is not None:
                all_smds.append(abs(s))
                if abs(s) > SMD_THRESHOLD:
                    large.append((regime, var, s))

    if not all_smds:
        return "판정 불가 — 데이터 부족"

    large_count = len(large)
    total_count = len(all_smds)
    ratio = large_count / total_count if total_count else 0

    if ratio == 0:
        return "Composition similar"
    elif ratio >= 0.5:
        return "Composition different"
    else:
        return "Mixed"

# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 64)
    print("Stage 8 — A/D 그룹 기초 특성 비교")
    print("사전등록 2026-09-22 / Exit criterion: |SMD| > 0.2")
    print("=" * 64)
    print()

    pit           = load_pit_tickers()
    phase_by_year = load_phase_by_year()

    print("펀더멘털 로드...")
    fundamentals = load_fundamentals(pit)

    all_tickers = {KOSPI} | {t for s in pit.values() for t in s}
    print(f"가격 로드 대상: KOSPI + {len(all_tickers)-1}개 종목")
    prices       = load_prices_for_tickers(all_tickers)

    trading_days = load_trading_days()
    flow     = load_investor_flow()   # (signal_date, ticker) → (fn, pn)
    universe = load_universe(flow, pit)

    print("레코드 구성 중... (시간 소요)")
    records = build_records(pit, phase_by_year, fundamentals, prices, trading_days,
                            flow, universe)
    print(f"총 관측치: {len(records):,}\n")

    smd_summary: dict[str, dict] = {}

    # ── 전체 ─────────────────────────────────────────────────────────────────
    print("=" * 64)
    print("[ 전체 (2020–2025) ]")
    smd_summary["전체"] = compare_groups("전체", records)

    # ── 레짐별 ───────────────────────────────────────────────────────────────
    print()
    print("=" * 64)
    print("[ 레짐별 ]")
    bull_recs  = [r for r in records if r["phase"] == "Bull"]
    bsw_recs   = [r for r in records if r["phase"] in ("Bear", "Sideways")]
    smd_summary["Bull"]          = compare_groups("Bull", bull_recs)
    smd_summary["Bear/Sideways"] = compare_groups("Bear/Sideways", bsw_recs)

    # ── 연도별 (탐색적 참고) ─────────────────────────────────────────────────
    print()
    print("=" * 64)
    print("[ 연도별 (탐색적 참고) ]")
    for yr in sorted(SIGNAL_TO_RISK_YEAR.keys()):
        yr_recs = [r for r in records if r["year"] == yr]
        ph = phase_by_year.get(yr, "?")
        compare_groups(f"{yr}년 [{ph}]", yr_recs)

    # ── 판정 ─────────────────────────────────────────────────────────────────
    print()
    print("=" * 64)
    print("[ Exit Criterion 판정 ]")
    print(f"  |SMD| > {SMD_THRESHOLD} → 큰 차이 기준\n")
    for regime, v_dict in smd_summary.items():
        large = [(var, s) for var, s in v_dict.items() if s is not None and abs(s) > SMD_THRESHOLD]
        print(f"  {regime}: 큰 차이 변수 = {[f'{var}(SMD={s:.3f})' for var, s in large] if large else '없음'}")
    v = verdict(smd_summary)
    print(f"\n  → 판정: {v}")

    print()
    print("[ 해석 제한 ]")
    print("  - Composition different ≠ Investor Absorption 효과 없음")
    print("  - 연도별 결과를 새로운 판정 기준으로 사용하지 않는다")
    print("  - 매칭·회귀 통제는 Stage 9에서 수행")


if __name__ == "__main__":
    main()
