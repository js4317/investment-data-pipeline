"""v8.6.9: 거래정지 이력(변동성 창 중 거래량 0) 종목은 quick reject → AVOID. 점수는 유지(감사용)."""
from datetime import date
from unittest.mock import patch

import pandas as pd

from analyzer.data import krx as K
from analyzer.data.models import FetchResult, Fundamentals, KospiDisparity, MarketCap, RiskData
from analyzer.scoring.scorer import score
from analyzer.validation.validator import validate
from constants import DataSource, Grade, QuickRejectReason

D = date(2026, 10, 21)


def _validated(halt_days, halted_on_session=False):
    risk = RiskData(quarters_with_loss=0, debt_ratio_pct=50.0, dilution_pct=0.0, volatility_pct=20.0,
                    halt_days=halt_days, halted_on_session=halted_on_session, source=DataSource.KRX, as_of_date=D)
    return validate(ticker="035890", as_of_date=D,
                    market_cap_result=FetchResult.success(MarketCap(market_cap=10**11, trading_value=10**9,
                                                                    source=DataSource.KRX, as_of_date=D)),
                    fundamentals_result=FetchResult.success(Fundamentals(per=4.0, pbr=0.3, div_yield=None,
                                                                         source=DataSource.KRX, as_of_date=D)),
                    supply_flow_result=None, foreign_ownership_result=None,
                    kospi_disparity_result=FetchResult.success(KospiDisparity(current=1.0, ma_n=1.0, disparity_pct=0.0,
                                                                              window=20, source=DataSource.KRX, as_of_date=D)),
                    risk_result=FetchResult.success(risk))


def test_halt_history_forces_avoid_but_keeps_score():
    v = _validated(137)
    assert v.quick_reject and v.quick_reject_reason == QuickRejectReason.TRADING_HALT
    sc = score(v)
    assert sc.grade == Grade.AVOID and sc.normalized_score > 0
    assert any("trading_halt" in n for n in sc.notes)


def test_boundary_20_days_and_short_halts():
    assert _validated(20).quick_reject_reason == QuickRejectReason.TRADING_HALT      # 20일 이상 차단
    for halt in (0, 1, 2, 19, None):                                                 # 짧은 정지·확인 불가는 통과
        assert _validated(halt).quick_reject_reason != QuickRejectReason.TRADING_HALT


def test_halted_on_session_rejects_even_if_short():
    assert _validated(1, halted_on_session=True).quick_reject_reason == QuickRejectReason.TRADING_HALT


def test_fetch_halt_info_counts_zero_volume_and_shares_fetch():
    K._WINDOW_CACHE.clear()
    df = pd.DataFrame({"종가": [100.0] * 30, "거래량": [0] * 5 + [10] * 24 + [0]})
    with patch.object(K, "_get_trading_dates", return_value=[D] * 252), \
         patch.object(K, "_call_krx", return_value=df) as call:
        assert K.fetch_halt_info("035890", D) == (6, True)
        K._calc_annualized_volatility("035890", D, 252)
        assert call.call_count == 1                      # 변동성과 같은 조회를 공유
    K._WINDOW_CACHE.clear()
