"""
market_regime.py cache FIFO 동작 검증.
- 10일 호출 후 cache size == CACHE_MAX_SIZE (5) 보장
- insert-then-trim FIFO: 가장 오래된 항목 제거 확인
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from analyzer.rules.market_regime import get_market_state, _state_cache, _CACHE_MAX_SIZE
from analyzer.data.models import FetchResult, FetchErrorReason


def _make_failed_disparity() -> FetchResult:
    return FetchResult.failure(FetchErrorReason.API_TIMEOUT)


@pytest.fixture(autouse=True)
def clear_cache():
    _state_cache.clear()
    yield
    _state_cache.clear()


def test_cache_size_bounded_after_many_calls():
    base = date(2026, 1, 1)
    dates = [base + timedelta(days=i) for i in range(10)]

    with patch("analyzer.rules.market_regime.krx") as mock_krx:
        mock_krx.fetch_kospi_disparity.return_value = _make_failed_disparity()
        for d in dates:
            get_market_state(d)

    assert len(_state_cache) == _CACHE_MAX_SIZE


def test_cache_fifo_oldest_evicted():
    base = date(2026, 1, 1)
    dates = [base + timedelta(days=i) for i in range(_CACHE_MAX_SIZE + 2)]

    with patch("analyzer.rules.market_regime.krx") as mock_krx:
        mock_krx.fetch_kospi_disparity.return_value = _make_failed_disparity()
        for d in dates:
            get_market_state(d)

    assert dates[0] not in _state_cache
    assert dates[1] not in _state_cache
    assert dates[-1] in _state_cache


def test_force_refresh_replaces_not_appends():
    d = date(2026, 1, 1)

    with patch("analyzer.rules.market_regime.krx") as mock_krx:
        mock_krx.fetch_kospi_disparity.return_value = _make_failed_disparity()
        get_market_state(d)
        size_before = len(_state_cache)
        get_market_state(d, force_refresh=True)
        size_after = len(_state_cache)

    assert size_before == size_after == 1
