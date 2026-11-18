#!/usr/bin/env python3
"""
Supply Horizon IC — 수급 신호의 horizon 민감도 검증
====================================================
사전등록: 2026-09-23  /  docs/RESEARCH.md

질문: Supply IC가 20D → 60D → 120D로 단조 증가하는가?

Supply proxy: 7영업일 rolling sum 기반 direction score
  쌍끌이매수 = 20 / 외국인단독 = 12 / 기관단독 = 10 / 쌍끌이매도 = -5
  (production scoring 로직과 동일)

Forward alpha: T+1 open → T+horizon+1 open, excess vs KOSPI
"""
from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path
from scipy import stats as scipy_stats

ROOT     = Path(__file__).resolve().parent.parent
FLOW_DB  = ROOT / "database" / "research.db"
OHLCV_DB = ROOT / "database" / "market_ohlcv.db"

KOSPI          = "1001"
LOOKBACK_DAYS  = 7       # production supply lookback
HORIZONS       = [20, 60, 120]
MIN_N_DATE     = 10      # IC 계산 최소 종목 수

# 방향 점수 (production config과 동일)
DUAL_BUY   = 20
FX_SINGLE  = 12
INST_SINGLE = 10
DUAL_SELL  = -5

# 판정 기준
IC_THRESHOLD  = 0.05
POS_THRESHOLD = 55.0


# ── 가격 / 거래일 로드 ────────────────────────────────────────────────────────

def load_prices() -> tuple[list[str], dict[str, dict[str, float]]]:
    """거래일 목록 + {ticker: {date: open}} 반환"""
    print("  가격 데이터 로딩...", flush=True)
    with sqlite3.connect(OHLCV_DB) as c:
        rows = c.execute(
            "SELECT ticker, trade_date, open FROM market_ohlcv WHERE open > 0 ORDER BY trade_date, ticker"
        ).fetchall()
    prices: dict[str, dict[str, float]] = defaultdict(dict)
    dates_set = set()
    for ticker, date, open_ in rows:
        prices[ticker][date] = open_
        dates_set.add(date)
    trading_dates = sorted(dates_set)
    print(f"  거래일: {len(trading_dates)}일 ({trading_dates[0]} ~ {trading_dates[-1]})")
    return trading_dates, dict(prices)


def load_investor_flow() -> dict[str, dict[str, tuple[int, int]]]:
    """{ticker: {date: (foreign_net, institution_net)}}"""
    print("  investor_flow 로딩...", flush=True)
    with sqlite3.connect(FLOW_DB) as c:
        rows = c.execute(
            "SELECT ticker, signal_date, foreign_net, institution_net FROM investor_flow"
        ).fetchall()
    flow: dict[str, dict[str, tuple[int, int]]] = defaultdict(dict)
    for ticker, date, fnet, inet in rows:
        if fnet is not None and inet is not None:
            flow[ticker][date] = (fnet, inet)
    print(f"  tickers: {len(flow):,}")
    return dict(flow)


# ── Supply proxy 계산 ─────────────────────────────────────────────────────────

def direction_score(f_net: int, i_net: int) -> int:
    f_pos = f_net > 0
    i_pos = i_net > 0
    if f_pos and i_pos:
        return DUAL_BUY
    if f_pos and not i_pos:
        return FX_SINGLE
    if not f_pos and i_pos:
        return INST_SINGLE
    return DUAL_SELL


def compute_supply_proxy(
    ticker: str,
    date: str,
    date_idx: dict[str, int],
    trading_dates: list[str],
    flow: dict[str, dict[str, tuple[int, int]]],
) -> int | None:
    """
    7영업일 rolling sum → direction score
    7일 중 4일 이상 데이터 있어야 유효
    """
    idx = date_idx.get(date)
    if idx is None:
        return None
    ticker_flow = flow.get(ticker, {})
    f_sum, i_sum, n_days = 0, 0, 0
    for offset in range(1, LOOKBACK_DAYS + 1):   # T-7 ~ T-1
        di = idx - offset
        if di < 0:
            break
        d = trading_dates[di]
        if d in ticker_flow:
            fnet, inet = ticker_flow[d]
            f_sum += fnet
            i_sum += inet
            n_days += 1
    if n_days < 4:
        return None
    return direction_score(f_sum, i_sum)


# ── Alpha 계산 ────────────────────────────────────────────────────────────────

def get_alpha(
    ticker: str,
    date: str,
    horizon: int,
    date_idx: dict[str, int],
    trading_dates: list[str],
    prices: dict[str, dict[str, float]],
) -> float | None:
    """T+1 open → T+horizon+1 open 초과수익률 (%)"""
    idx = date_idx.get(date)
    if idx is None:
        return None

    entry_idx   = idx + 1
    exit_idx    = idx + horizon + 1
    if exit_idx >= len(trading_dates):
        return None

    entry_date = trading_dates[entry_idx]
    exit_date  = trading_dates[exit_idx]

    tp = prices.get(ticker, {})
    kp = prices.get(KOSPI, {})

    entry  = tp.get(entry_date)
    exit_  = tp.get(exit_date)
    k_entry = kp.get(entry_date)
    k_exit  = kp.get(exit_date)

    if not all([entry, exit_, k_entry, k_exit]):
        return None
    if entry <= 0 or k_entry <= 0:
        return None

    stock_ret = exit_ / entry - 1
    kospi_ret = k_exit / k_entry - 1
    return (stock_ret - kospi_ret) * 100


# ── IC 계산 ──────────────────────────────────────────────────────────────────

def compute_ic_series(
    obs_by_date: dict[str, list[tuple[int, float]]],
) -> list[float]:
    """date → [(supply_proxy, alpha)] → daily IC list"""
    ics = []
    for date in sorted(obs_by_date):
        pairs = obs_by_date[date]
        if len(pairs) < MIN_N_DATE:
            continue
        sups   = [p[0] for p in pairs]
        alphas = [p[1] for p in pairs]
        # 상수 체크 (supply score가 모두 같으면 IC 정의 불가)
        if len(set(sups)) < 2:
            continue
        rho, _ = scipy_stats.spearmanr(sups, alphas)
        if rho == rho:   # NaN 제외
            ics.append(rho)
    return ics


def ic_stats(ics: list[float]) -> dict:
    if not ics:
        return {"n": 0}
    return {
        "n":      len(ics),
        "mean":   statistics.mean(ics),
        "median": statistics.median(ics),
        "pos_pct": sum(1 for v in ics if v > 0) / len(ics) * 100,
    }


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 68)
    print("Supply Horizon IC — 수급 신호의 horizon 민감도 검증")
    print("사전등록: 2026-09-23")
    print(f"  Horizons: {HORIZONS}D")
    print(f"  Supply lookback: {LOOKBACK_DAYS} 영업일")
    print(f"  판정: IC 단조 증가 + 120D mean>{IC_THRESHOLD} + pos%>{POS_THRESHOLD}%")
    print("=" * 68)

    trading_dates, prices = load_prices()
    flow = load_investor_flow()
    date_idx = {d: i for i, d in enumerate(trading_dates)}

    tickers = [t for t in flow if t != KOSPI and t in prices]
    print(f"  분석 대상 ticker: {len(tickers):,}")

    # ── Horizon별 observation 구축 ─────────────────────────────────────────
    print("\n[2] Horizon별 IC 계산...", flush=True)

    obs: dict[int, dict[str, list[tuple[int, float]]]] = {h: defaultdict(list) for h in HORIZONS}

    for t_idx, ticker in enumerate(tickers):
        if t_idx % 500 == 0:
            print(f"  ticker {t_idx}/{len(tickers)}...", flush=True)
        ticker_dates = sorted(flow[ticker].keys())

        for date in ticker_dates:
            sup_proxy = compute_supply_proxy(ticker, date, date_idx, trading_dates, flow)
            if sup_proxy is None:
                continue
            for h in HORIZONS:
                alpha = get_alpha(ticker, date, h, date_idx, trading_dates, prices)
                if alpha is not None:
                    obs[h][date].append((sup_proxy, alpha))

    # ── IC 통계 계산 ─────────────────────────────────────────────────────────
    print("\n" + "=" * 68)
    print("[ Horizon별 IC 통계 ]")

    ic_series: dict[int, list[float]] = {}
    for h in HORIZONS:
        ic_series[h] = compute_ic_series(obs[h])

    print(f"\n  {'Horizon':>10} {'mean_IC':>10} {'median_IC':>10} {'pos%':>8} {'n_days':>8}  판정")
    print("  " + "-" * 58)
    for h in HORIZONS:
        s = ic_stats(ic_series[h])
        if not s.get("n"):
            print(f"  {h:>8}D  데이터 없음")
            continue
        flag = "✓" if s["mean"] > IC_THRESHOLD else "—"
        dir_s = "↑" if s["mean"] > 0 else "↓"
        print(f"  {h:>8}D  {s['mean']:>+10.4f} {s['median']:>+10.4f} "
              f"{s['pos_pct']:>7.1f}% {s['n']:>8}  {flag} {dir_s}")

    # ── 연도별 IC 분포 ────────────────────────────────────────────────────────
    print("\n" + "=" * 68)
    print("[ 연도별 IC 분포 (20D / 60D / 120D) ]")
    print(f"  {'Year':>5}", end="")
    for h in HORIZONS:
        print(f"  {'IC_'+str(h)+'D':>10}", end="")
    print()
    print("  " + "-" * 45)

    years = sorted(set(d[:4] for h in HORIZONS for d in obs[h]))
    for year in years:
        print(f"  {year:>5}", end="")
        for h in HORIZONS:
            year_ics = [ic for date, ic in zip(sorted(obs[h].keys()), ic_series[h])
                        if date.startswith(year)]
            # 날짜별 IC 재계산 (연도 필터)
            year_obs = {d: v for d, v in obs[h].items() if d.startswith(year)}
            yics = compute_ic_series(year_obs)
            if yics:
                print(f"  {statistics.mean(yics):>+10.4f}", end="")
            else:
                print(f"  {'N/A':>10}", end="")
        print()

    # ── 단조성 판정 ───────────────────────────────────────────────────────────
    print("\n" + "=" * 68)
    print("[ 단조성 판정 ]")

    means = [ic_stats(ic_series[h]).get("mean") for h in HORIZONS]
    if all(m is not None for m in means):
        monotone = all(means[i] < means[i + 1] for i in range(len(means) - 1))
        ic_120_ok = means[-1] > IC_THRESHOLD if means[-1] is not None else False
        pos_120 = ic_stats(ic_series[120]).get("pos_pct", 0)
        pos_ok = pos_120 > POS_THRESHOLD

        print(f"  IC 단조 증가 (20D < 60D < 120D): {'✅' if monotone else '❌'}")
        print(f"  120D mean IC > +{IC_THRESHOLD}: {'✅' if ic_120_ok else '❌'} ({means[-1]:>+.4f})")
        print(f"  120D pos% > {POS_THRESHOLD}%: {'✅' if pos_ok else '❌'} ({pos_120:.1f}%)")

        all_pass = monotone and ic_120_ok and pos_ok
        print(f"\n  최종 판정: {'✅ Horizon sensitivity 지지 — Supply는 장기(120D) signal 가능성' if all_pass else '❌ Horizon sensitivity 미지지'}")
    else:
        print("  데이터 부족 — 판정 불가")

    print()
    print("  ⚠️ 120D IC의 n_days는 중첩 window로 인해 통계적 독립성 없음")
    print("     p값 아닌 mean_IC와 pos%를 신뢰도 근거로 사용")


if __name__ == "__main__":
    main()
