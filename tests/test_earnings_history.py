"""
earnings_history.py regression test

케이스:
  1. KOSPI 성공 + KOSDAQ 성공 → 둘 다 map에 존재
  2. KOSPI 성공 + KOSDAQ 실패 → KOSPI 데이터 유지, KOSDAQ 종목 graceful degradation
  3. 양쪽 실패 → 해당 연도 데이터 없음, Morning Run은 계속 진행
  4. 특정 종목 history 부족 → INSUFFICIENT_DATA (HMM 패턴)

목적: partial-market failure가 전체 파이프라인을 중단시키지 않음을 보장.
     특히 case 4 — history 부족 종목을 PEAK/NORMAL로 잘못 분류하지 않아야 함.
"""
from __future__ import annotations

import pandas as pd
import pytest
from datetime import date
from unittest.mock import patch, MagicMock

from analyzer.data.earnings_history import (
    _fetch_year_end_eps_map,
    _compute_for_ticker,
    build_earnings_regime_map,
    RATIO_PEAK,
    RATIO_ELEVATED,
)


# ---------------------------------------------------------------------------
# 헬퍼 — pykrx 반환값 mock
# ---------------------------------------------------------------------------

def _make_fund_df(tickers_per: dict[str, float]) -> pd.DataFrame:
    """PER 컬럼, 티커 index인 fundamental DataFrame mock.
    pd.DataFrame({"PER": {...}}) → index=tickers, columns=["PER"] — 정방향 OK.
    """
    return pd.DataFrame({"PER": tickers_per})


def _make_price_df(tickers_close: dict[str, float]) -> pd.DataFrame:
    """종가 컬럼, 티커 index인 OHLCV DataFrame mock."""
    return pd.DataFrame({"종가": tickers_close})


# ---------------------------------------------------------------------------
# Case 1: KOSPI 성공 + KOSDAQ 성공
# ---------------------------------------------------------------------------

def test_both_markets_success():
    """KOSPI + KOSDAQ 양쪽에서 데이터 오면 둘 다 eps_map에 포함."""
    kospi_tickers = {"005380": 10.0, "005490": 8.0}   # PER
    kospi_close   = {"005380": 100_000.0, "005490": 200_000.0}
    kosdaq_tickers = {"035720": 15.0, "263750": 20.0}
    kosdaq_close   = {"035720": 50_000.0, "263750": 30_000.0}

    def fake_fundamental(date_str, market):
        if market == "KOSPI":
            return _make_fund_df(kospi_tickers)
        return _make_fund_df(kosdaq_tickers)

    def fake_ohlcv(date_str, market):
        if market == "KOSPI":
            return _make_price_df(kospi_close)
        return _make_price_df(kosdaq_close)

    with patch("pykrx.stock.get_market_fundamental_by_ticker", side_effect=fake_fundamental), \
         patch("pykrx.stock.get_market_ohlcv_by_ticker", side_effect=fake_ohlcv):
        result = _fetch_year_end_eps_map(2025)

    assert "005380" in result
    assert "035720" in result
    assert result["005380"] == pytest.approx(100_000.0 / 10.0)
    assert result["035720"] == pytest.approx(50_000.0 / 15.0)


# ---------------------------------------------------------------------------
# Case 2: KOSPI 성공 + KOSDAQ 실패
# ---------------------------------------------------------------------------

def test_kospi_success_kosdaq_failure():
    """KOSDAQ fetch 예외 → KOSPI 데이터는 유지, KOSDAQ 종목은 graceful degradation."""
    kospi_tickers = {"005380": 10.0}
    kospi_close   = {"005380": 100_000.0}

    def fake_fundamental(date_str, market):
        if market == "KOSPI":
            return _make_fund_df(kospi_tickers)
        raise ConnectionError("KOSDAQ API 실패 mock")

    def fake_ohlcv(date_str, market):
        if market == "KOSPI":
            return _make_price_df(kospi_close)
        raise ConnectionError("KOSDAQ API 실패 mock")

    with patch("pykrx.stock.get_market_fundamental_by_ticker", side_effect=fake_fundamental), \
         patch("pykrx.stock.get_market_ohlcv_by_ticker", side_effect=fake_ohlcv):
        result = _fetch_year_end_eps_map(2025)

    # KOSPI 데이터 유지
    assert "005380" in result
    assert result["005380"] == pytest.approx(10_000.0)

    # KOSDAQ 종목(035720)은 map에 없음 — graceful (KeyError 아님, 단순 부재)
    assert "035720" not in result


def test_kosdaq_ticker_insufficient_data_when_kosdaq_fails():
    """
    KOSDAQ 종목이 일부 연도에서 fetch 실패해도
    build_earnings_regime_map은 해당 종목을 INSUFFICIENT_DATA로 처리 (예외 없음).
    """
    # KOSPI에 035720 없음, KOSDAQ 항상 실패 → 해당 종목은 모든 연도에 EPS 없음
    kospi_fund   = {"005380": 10.0}
    kospi_close  = {"005380": 100_000.0}

    def fake_fundamental(date_str, market):
        if market == "KOSPI":
            return _make_fund_df(kospi_fund)
        raise RuntimeError("KOSDAQ mock fail")

    def fake_ohlcv(date_str, market):
        if market == "KOSPI":
            return _make_price_df(kospi_close)
        raise RuntimeError("KOSDAQ mock fail")

    with patch("pykrx.stock.get_market_fundamental_by_ticker", side_effect=fake_fundamental), \
         patch("pykrx.stock.get_market_ohlcv_by_ticker", side_effect=fake_ohlcv):
        regime_map = build_earnings_regime_map(["035720"], date(2026, 8, 26))

    entry = regime_map["035720"]
    assert entry["earnings_regime"]    == "INSUFFICIENT_DATA"
    assert entry["earnings_confidence"] == "INSUFFICIENT"
    assert entry["earnings_ratio"]     is None


# ---------------------------------------------------------------------------
# Case 3: 양쪽 실패 → 해당 연도 데이터 없음
# ---------------------------------------------------------------------------

def test_both_markets_fail():
    """양 시장 모두 실패 → _fetch_year_end_eps_map 빈 dict 반환, 예외 없음."""
    def always_fail(date_str, market):
        raise ConnectionError("API 실패 mock")

    with patch("pykrx.stock.get_market_fundamental_by_ticker", side_effect=always_fail), \
         patch("pykrx.stock.get_market_ohlcv_by_ticker", side_effect=always_fail):
        result = _fetch_year_end_eps_map(2025)

    assert result == {}


def test_full_pipeline_continues_on_all_fail():
    """
    모든 연도에서 pykrx 실패해도 build_earnings_regime_map은 빈 dict 대신
    모든 종목을 INSUFFICIENT_DATA로 채워 반환. Morning Run 중단 없음.
    """
    def always_fail(date_str, market):
        raise RuntimeError("네트워크 실패 mock")

    tickers = ["005380", "005490"]
    with patch("pykrx.stock.get_market_fundamental_by_ticker", side_effect=always_fail), \
         patch("pykrx.stock.get_market_ohlcv_by_ticker", side_effect=always_fail):
        regime_map = build_earnings_regime_map(tickers, date(2026, 8, 26))

    for tk in tickers:
        assert tk in regime_map
        assert regime_map[tk]["earnings_regime"]    == "INSUFFICIENT_DATA"
        assert regime_map[tk]["earnings_confidence"] == "INSUFFICIENT"
        assert regime_map[tk]["earnings_ratio"]     is None


# ---------------------------------------------------------------------------
# Case 4: 특정 종목 history 부족 → INSUFFICIENT_DATA (HMM 패턴)
# ---------------------------------------------------------------------------

def test_insufficient_history_not_classified():
    """
    HMM(011200) 패턴: 과거 흑자 연도 < 3 (오랜 적자 기간) →
    earnings_ratio 계산 안 함. PEAK/NORMAL로 잘못 분류되지 않아야 함.
    """
    # year_eps: 2021~2025 중 current=2025, history=2020~2024
    # 2020~2022: 데이터 없음 (적자기간 PER 무효), 2023~2024만 있음 → history_n=2 < 3
    current_year = 2025
    year_eps = {
        2020: {},          # 데이터 없음
        2021: {},          # 데이터 없음
        2022: {},          # 데이터 없음
        2023: {"011200": 500.0},   # 유효 1년
        2024: {"011200": 400.0},   # 유효 1년 → 합계 history_n=2
        2025: {"011200": 800.0},   # current EPS (높음)
    }
    result = _compute_for_ticker("011200", current_year, year_eps, lookback_years=5)

    assert result["earnings_ratio"]     is None,  "history 부족 시 ratio 계산 금지"
    assert result["earnings_regime"]    == "INSUFFICIENT_DATA"
    assert result["earnings_confidence"] == "INSUFFICIENT"
    assert result["earnings_history_n"] == 2


def test_sufficient_history_peak_detection():
    """
    팬오션(028670) 패턴: 과거 5년 정상 EPS가 있고 당해 EPS가 1.5× 초과 → PEAK.
    """
    current_year = 2025
    year_eps = {
        2020: {"028670": 200.0},
        2021: {"028670": 250.0},
        2022: {"028670": 180.0},
        2023: {"028670": 220.0},
        2024: {"028670": 210.0},  # 과거 5년 중앙값 ≈ 210
        2025: {"028670": 400.0},  # 400 / 210 ≈ 1.90 → PEAK
    }
    result = _compute_for_ticker("028670", current_year, year_eps, lookback_years=5)

    assert result["earnings_regime"]    == "PEAK"
    assert result["earnings_confidence"] == "HIGH"
    assert result["earnings_history_n"] == 5
    assert result["earnings_ratio"]     == pytest.approx(400.0 / 210.0, rel=0.01)


def test_normal_regime_detection():
    """정상 EPS 범위 (ratio ≤ 1.0) → NORMAL."""
    current_year = 2025
    year_eps = {
        2020: {"000660": 300.0},
        2021: {"000660": 350.0},
        2022: {"000660": 320.0},
        2023: {"000660": 310.0},
        2024: {"000660": 330.0},  # 중앙값 ≈ 320
        2025: {"000660": 300.0},  # 300 / 320 ≈ 0.94 → NORMAL
    }
    result = _compute_for_ticker("000660", current_year, year_eps, lookback_years=5)

    assert result["earnings_regime"] == "NORMAL"
    assert result["earnings_ratio"]  == pytest.approx(300.0 / 320.0, rel=0.01)


def test_elevated_regime_detection():
    """ratio 1.0 < r ≤ 1.5 → ELEVATED."""
    current_year = 2025
    year_eps = {
        2020: {"000000": 100.0},
        2021: {"000000": 110.0},
        2022: {"000000": 105.0},
        2023: {"000000": 100.0},
        2024: {"000000": 108.0},  # 중앙값 = 105
        2025: {"000000": 145.0},  # 145 / 105 ≈ 1.38 → ELEVATED
    }
    result = _compute_for_ticker("000000", current_year, year_eps, lookback_years=5)

    assert result["earnings_regime"] == "ELEVATED"


def test_low_confidence_3_to_4_years():
    """history_n 3~4 → LOW confidence, ratio는 계산함."""
    current_year = 2025
    year_eps = {
        2020: {},
        2021: {},
        2022: {"000000": 100.0},
        2023: {"000000": 110.0},
        2024: {"000000": 105.0},  # history_n=3 → LOW
        2025: {"000000": 200.0},  # 200/105 ≈ 1.90 → PEAK
    }
    result = _compute_for_ticker("000000", current_year, year_eps, lookback_years=5)

    assert result["earnings_confidence"] == "LOW"
    assert result["earnings_regime"]     == "PEAK"
    assert result["earnings_history_n"]  == 3
    assert result["earnings_ratio"]      is not None


def test_source_years_correct():
    """earnings_source_years가 실제 사용된 연도 목록을 정확히 반환."""
    current_year = 2025
    year_eps = {
        2020: {"A": 100.0},
        2021: {},
        2022: {"A": 120.0},
        2023: {"A": 110.0},
        2024: {"A": 115.0},
        2025: {"A": 130.0},
    }
    result = _compute_for_ticker("A", current_year, year_eps, lookback_years=5)

    # 2021은 없으므로 source_years = [2020, 2022, 2023, 2024]
    assert result["earnings_source_years"] == [2020, 2022, 2023, 2024]
    assert result["earnings_history_n"]    == 4
    assert result["earnings_confidence"]   == "LOW"
