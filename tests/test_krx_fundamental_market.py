"""v8.6.8 — KIS를 쓰지 않는 경로의 재무 일괄 조회가 KOSPI·KOSDAQ 전체 시장을 대상으로 하는지.

pykrx 호출은 가짜로 대체한다(네트워크 없음). 가짜는 실제 pykrx처럼 market 기본값을 KOSPI로 둔다.
"""
from datetime import date

import pandas as pd
import pytest

from analyzer.data import krx as K
from analyzer.data.models import FetchErrorReason

AS_OF = date(2026, 10, 14)
ROWS = {
    "KOSPI": pd.DataFrame({"PER": [8.0], "PBR": [0.7], "DIV": [3.0]}, index=["005930"]),
    "KOSDAQ": pd.DataFrame({"PER": [12.0], "PBR": [1.5], "DIV": [1.0]}, index=["035900"]),
}


@pytest.fixture
def fake(monkeypatch):
    calls = []

    def get_market_fundamental(date_str, market="KOSPI"):          # pykrx와 같은 기본값
        calls.append((date_str, market))
        if market == "ALL":
            return pd.concat([ROWS["KOSPI"], ROWS["KOSDAQ"]])
        return ROWS[market]

    monkeypatch.delenv("MARKET_DATA_PROVIDER", raising=False)      # KIS 경로 비활성
    monkeypatch.setattr(K.krx, "get_market_fundamental", get_market_fundamental)
    monkeypatch.setattr(K, "_fundamental_cache", {})
    monkeypatch.setattr(K._cache, "get", lambda *a, **k: None)      # 운영 캐시를 읽지 않음
    monkeypatch.setattr(K._cache, "put", lambda *a, **k: None)
    monkeypatch.setattr(K.time, "sleep", lambda s: None)
    return calls


def test_market_all_is_requested(fake):
    assert K._get_kis() is None
    K.fetch_fundamentals("005930", AS_OF)
    assert fake == [("20261014", "ALL")]


@pytest.mark.parametrize("ticker,per,pbr", [("005930", 8.0, 0.7), ("035900", 12.0, 1.5)])
def test_kospi_and_kosdaq_both_resolve(fake, ticker, per, pbr):
    r = K.fetch_fundamentals(ticker, AS_OF)
    assert r.ok and r.data.per == per and r.data.pbr == pbr


def test_unknown_ticker_keeps_existing_error(fake):
    r = K.fetch_fundamentals("999999", AS_OF)
    assert not r.ok and r.error == FetchErrorReason.TICKER_NOT_FOUND


def test_one_bulk_call_per_date(fake):
    for t in ("005930", "035900", "999999"):
        K.fetch_fundamentals(t, AS_OF)
    assert fake == [("20261014", "ALL")]
