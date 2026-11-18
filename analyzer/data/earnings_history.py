"""
Earnings History: Historical Earnings Normalization

목적: earnings_ratio / earnings_regime / earnings_history_n /
      earnings_confidence / earnings_source_years 계산 후 스냅샷 저장.

이 필드들은 연구 전용. 점수에 영향 없음.
Forward Validation 용도: CYCLICAL-1 가설 (peak earnings → 저PER 신호 약화)
검증을 위해 스냅샷에 저장.

Method:
  EPS_proxy = year-end 종가 / year-end PER  (pykrx, 연말 기준)
  normal_EPS = 과거 유효 N년의 중앙값
  earnings_ratio = current_EPS / normal_EPS

Confidence:
  HIGH        : 과거 유효 연도 ≥ 5
  LOW         : 과거 유효 연도 3~4
  INSUFFICIENT: 과거 유효 연도 < 3 → ratio/regime = None

Regime:
  NORMAL        : earnings_ratio ≤ 1.0
  ELEVATED      : 1.0 < earnings_ratio ≤ 1.5
  PEAK          : earnings_ratio > 1.5
  INSUFFICIENT_DATA: 데이터 부족
"""
from __future__ import annotations

import logging
import statistics
from datetime import date
from typing import Optional

log = logging.getLogger(__name__)

# 연말 PER fetch fallback — 12/31은 대부분 공휴일
_YEAR_END_CANDIDATES = [30, 29, 28, 27, 26]  # 12월 일자 (순서대로 시도)

RATIO_ELEVATED = 1.0
RATIO_PEAK     = 1.5
MIN_YEARS_HIGH = 5   # ≥5 → HIGH
MIN_YEARS_LOW  = 3   # 3~4 → LOW  / <3 → INSUFFICIENT

LOOKBACK_YEARS = 5   # 과거 참조 연도 수


def _fetch_year_end_eps_map(year: int) -> dict[str, float]:
    """
    연말 기준 종목별 EPS_proxy = 종가 / PER 반환.
    KOSPI + KOSDAQ 합산. 유효하지 않은 종목(PER ≤ 0.5 또는 종가 ≤ 0)은 제외.

    Returns: {ticker: eps_proxy}  — 빈 dict 가능
    """
    try:
        from pykrx import stock as pykrx_stock
    except ImportError:
        log.warning("pykrx 없음 — earnings_history 건너뜀")
        return {}

    eps_map: dict[str, float] = {}

    for month_day in _YEAR_END_CANDIDATES:
        date_str = f"{year}12{month_day:02d}"
        for market in ("KOSPI", "KOSDAQ"):
            try:
                fund_df = pykrx_stock.get_market_fundamental_by_ticker(
                    date_str, market=market
                )
                price_df = pykrx_stock.get_market_ohlcv_by_ticker(
                    date_str, market=market
                )
                if fund_df is None or price_df is None:
                    continue
                if fund_df.empty or price_df.empty:
                    continue

                common_tickers = fund_df.index.intersection(price_df.index)
                for tk in common_tickers:
                    if tk in eps_map:
                        continue  # 이미 KOSPI에서 처리됨
                    per = float(fund_df.loc[tk].get("PER", 0) or 0)
                    if per <= 0.5:
                        continue
                    close = float(price_df.loc[tk].get("종가", 0) or 0)
                    if close <= 0:
                        continue
                    eps_map[tk] = close / per
            except Exception as e:
                log.debug("earnings_history fetch %s %s %s: %s", year, date_str, market, e)
                continue

        # 데이터가 있으면 이 날짜로 확정
        if eps_map:
            log.debug("earnings_history year=%d date=%s tickers=%d", year, date_str, len(eps_map))
            break

    return eps_map


def build_earnings_regime_map(
    tickers: list[str],
    as_of_date: date,
    lookback_years: int = LOOKBACK_YEARS,
) -> dict[str, dict]:
    """
    여러 종목의 earnings regime 필드를 일괄 계산.
    pykrx는 연도별 1회만 호출 (배치). 총 API 호출 = 연도 수 × 2개 market.

    Args:
        tickers       : 분석 대상 종목 코드 목록
        as_of_date    : Morning Run 날짜. 직전 연말 기준 계산.
        lookback_years: 과거 참조 연도 수 (기본 5)

    Returns:
        {ticker: {earnings_ratio, earnings_regime, earnings_history_n,
                  earnings_confidence, earnings_source_years}}
    """
    current_year  = as_of_date.year - 1          # 직전 연말 (가장 최신 연간 실적)
    history_start = current_year - lookback_years

    # 연도별 EPS_proxy 맵 (배치 fetch)
    # current_year 포함, 과거 lookback_years 포함
    years_to_fetch = list(range(history_start, current_year + 1))
    log.info(
        "earnings_history: %d년치 연말 데이터 fetch (current=%d, history=%d~%d)",
        len(years_to_fetch), current_year, history_start, current_year - 1,
    )

    year_eps: dict[int, dict[str, float]] = {}
    for yr in years_to_fetch:
        year_eps[yr] = _fetch_year_end_eps_map(yr)

    result: dict[str, dict] = {}
    for ticker in tickers:
        result[ticker] = _compute_for_ticker(
            ticker, current_year, year_eps, lookback_years
        )
    return result


def _compute_for_ticker(
    ticker: str,
    current_year: int,
    year_eps: dict[int, dict[str, float]],
    lookback_years: int,
) -> dict:
    """종목 1개에 대한 earnings regime 계산."""
    null_result = {
        "earnings_ratio":        None,
        "earnings_regime":       "INSUFFICIENT_DATA",
        "earnings_history_n":    0,
        "earnings_confidence":   "INSUFFICIENT",
        "earnings_source_years": [],
    }

    # 당해 EPS
    current_eps = year_eps.get(current_year, {}).get(ticker)
    if not current_eps or current_eps <= 0:
        return null_result

    # 과거 유효 EPS
    history_start = current_year - lookback_years
    valid: list[tuple[int, float]] = []
    for yr in range(history_start, current_year):
        eps = year_eps.get(yr, {}).get(ticker)
        if eps and eps > 0:
            valid.append((yr, eps))

    history_n    = len(valid)
    source_years = [yr for yr, _ in valid]

    if history_n < MIN_YEARS_LOW:
        return {
            "earnings_ratio":        None,
            "earnings_regime":       "INSUFFICIENT_DATA",
            "earnings_history_n":    history_n,
            "earnings_confidence":   "INSUFFICIENT",
            "earnings_source_years": source_years,
        }

    confidence = "HIGH" if history_n >= MIN_YEARS_HIGH else "LOW"
    normal_eps = statistics.median(eps for _, eps in valid)

    if normal_eps <= 0:
        return {
            "earnings_ratio":        None,
            "earnings_regime":       "INSUFFICIENT_DATA",
            "earnings_history_n":    history_n,
            "earnings_confidence":   confidence,
            "earnings_source_years": source_years,
        }

    ratio = current_eps / normal_eps

    if ratio > RATIO_PEAK:
        regime = "PEAK"
    elif ratio > RATIO_ELEVATED:
        regime = "ELEVATED"
    else:
        regime = "NORMAL"

    return {
        "earnings_ratio":        round(ratio, 3),
        "earnings_regime":       regime,
        "earnings_history_n":    history_n,
        "earnings_confidence":   confidence,
        "earnings_source_years": source_years,
    }
