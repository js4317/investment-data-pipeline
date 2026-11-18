from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping, Optional

from constants import Confidence, QuickRejectReason
from analyzer.data.models import (
    FetchErrorReason,
    MarketCap, Fundamentals, SupplyFlow, ForeignOwnership, KospiDisparity, EarningsData, RiskData,
)


@dataclass(frozen=True)
class ValidatedMarketData:
    """
    데이터 수집 결과를 검증한 읽기 전용 스냅샷.

    정책:
    - quick_reject는 confidence와 독립.
      "확신 높게 탈락(HIGH + quick_reject=True)"은 유효한 상태.
      예: PBR 데이터는 완벽하지만(HIGH) 기준 초과로 탈락.
    - quick_reject=True  →  quick_reject_reason 반드시 있어야 함.
    - quick_reject=False →  quick_reject_reason 반드시 없어야 함.
    - validation_notes: confidence 판정 근거 + quick_reject 사유를 순서대로 기록.
      Explainable Scoring Principle 구현체.
    """

    ticker:      str
    as_of_date:  date
    confidence:  Confidence
    quick_reject: bool
    quick_reject_reason: Optional[QuickRejectReason]

    market_cap:        Optional[MarketCap]
    fundamentals:      Optional[Fundamentals]
    supply_flow:       Optional[SupplyFlow]
    foreign_ownership: Optional[ForeignOwnership]
    kospi_disparity:   Optional[KospiDisparity]
    earnings:          Optional[EarningsData]
    risk:              Optional[RiskData]

    fetch_errors:      Mapping[str, FetchErrorReason]
    validation_notes:  tuple[str, ...]

    def __post_init__(self) -> None:
        if self.quick_reject and self.quick_reject_reason is None:
            raise ValueError("quick_reject=True인데 quick_reject_reason이 없음")
        if not self.quick_reject and self.quick_reject_reason is not None:
            raise ValueError("quick_reject=False인데 quick_reject_reason이 있음")
