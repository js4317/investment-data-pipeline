from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

import config
from constants import Grade, Confidence, MarketRegime

if TYPE_CHECKING:
    from run import AnalysisResult
    from analyzer.portfolio.models import PortfolioState


@dataclass(frozen=True)
class WeightAdvisory:
    ticker:              str
    current_pct:         Optional[float]   # None = 미보유
    recommended_max_pct: float
    status:              str               # over | ok | watch | new | exit


def _recommended_max(grade: Grade, confidence: Confidence, regime: MarketRegime) -> float:
    p = config.POSITION

    if confidence == Confidence.LOW or grade == Grade.AVOID:
        return 0.0

    if grade == Grade.STRONG_BUY:
        base = p["strong_buy_max_weight_pct"]
    elif grade == Grade.BUY:
        base = (
            p["buy_medium_confidence_pct"]
            if confidence == Confidence.MEDIUM
            else p["buy_max_weight_pct"]
        )
    else:  # NEUTRAL
        base = p["neutral_max_weight_pct"]

    multiplier = {
        MarketRegime.RISK_ON:      1.0,
        MarketRegime.RISK_NEUTRAL: p["regime_neutral_multiplier"],
        MarketRegime.RISK_OFF:     p["regime_risk_off_multiplier"],
    }.get(regime, 1.0)

    return round(min(base * multiplier, p["single_stock_hard_cap_pct"]), 1)


def compute_advisory_raw(
    ticker:     str,
    grade:      Grade,
    confidence: Confidence,
    regime:     MarketRegime,
    portfolio:  Optional[PortfolioState],
) -> Optional[WeightAdvisory]:
    """
    primitive 파라미터 → WeightAdvisory.
    portfolio=None 이면 None 반환.
    AVOID 미보유 종목은 None 반환.
    """
    if portfolio is None:
        return None

    rec_max  = _recommended_max(grade, confidence, regime)
    position = portfolio.positions.get(ticker)
    current  = position.weight_pct if position else None

    if rec_max == 0.0:
        if current is None:
            return None
        status = "exit"
    elif current is None:
        status = "new" if grade in (Grade.BUY, Grade.STRONG_BUY) else "watch"
    elif current > rec_max:
        status = "over"
    elif grade == Grade.NEUTRAL:
        status = "watch"
    else:
        status = "ok"

    return WeightAdvisory(
        ticker=ticker,
        current_pct=current,
        recommended_max_pct=rec_max,
        status=status,
    )


def compute_advisory(
    result:    AnalysisResult,
    portfolio: Optional[PortfolioState],
) -> Optional[WeightAdvisory]:
    return compute_advisory_raw(
        result.scoring.ticker,
        result.scoring.grade,
        result.scoring.confidence,
        result.market.regime,
        portfolio,
    )
