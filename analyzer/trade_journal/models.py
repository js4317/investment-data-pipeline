from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class BuyReason(str, Enum):
    STRONG_BUY      = "strong_buy"
    LOW_PER         = "low_per"
    LOW_PBR         = "low_pbr"
    HIGH_ROE        = "high_roe"
    FOREIGN_BUY     = "foreign_buy"
    INSTITUTION_BUY = "institution_buy"
    DUAL_BUY        = "dual_buy"
    SECTOR_RECOVERY = "sector_recovery"
    DIVIDEND        = "dividend"
    MOMENTUM        = "momentum"
    OTHER           = "other"


BUY_REASON_LABELS: dict[str, str] = {
    BuyReason.STRONG_BUY:      "STRONG_BUY 진입",
    BuyReason.LOW_PER:         "저PER",
    BuyReason.LOW_PBR:         "저PBR",
    BuyReason.HIGH_ROE:        "고ROE",
    BuyReason.FOREIGN_BUY:     "외인 순매수",
    BuyReason.INSTITUTION_BUY: "기관 순매수",
    BuyReason.DUAL_BUY:        "쌍끌이 매수",
    BuyReason.SECTOR_RECOVERY: "업황 회복",
    BuyReason.DIVIDEND:        "배당 매력",
    BuyReason.MOMENTUM:        "모멘텀 강세",
    BuyReason.OTHER:           "기타",
}


class SellReason(str, Enum):
    STRONG_BUY_EXIT    = "strong_buy_exit"
    TARGET_REACHED     = "target_reached"
    THESIS_BROKEN      = "thesis_broken"
    REBALANCING        = "rebalancing"
    CASH_NEED          = "cash_need"
    DUAL_SELL          = "dual_sell"
    FOREIGN_SELL       = "foreign_sell"
    INSTITUTION_SELL   = "institution_sell"
    OTHER              = "other"


SELL_REASON_LABELS: dict[str, str] = {
    SellReason.STRONG_BUY_EXIT:  "STRONG_BUY 이탈",
    SellReason.TARGET_REACHED:   "목표가 도달",
    SellReason.THESIS_BROKEN:    "Thesis 훼손",
    SellReason.REBALANCING:      "리밸런싱",
    SellReason.CASH_NEED:        "현금 확보",
    SellReason.DUAL_SELL:        "쌍끌이 매도 (외인+기관)",
    SellReason.FOREIGN_SELL:     "외인 매도",
    SellReason.INSTITUTION_SELL: "기관 매도",
    SellReason.OTHER:            "기타",
}


class Verdict(str, Enum):
    THESIS_CORRECT = "thesis_correct"
    THESIS_WRONG   = "thesis_wrong"
    UNCLEAR        = "unclear"


VERDICT_LABELS: dict[str, str] = {
    Verdict.THESIS_CORRECT: "✅ Thesis 맞음",
    Verdict.THESIS_WRONG:   "❌ Thesis 틀림",
    Verdict.UNCLEAR:        "🤔 불명확",
}


@dataclass
class TradeRecord:
    ticker:            str
    side:              str               # "BUY" | "SELL"
    engine_version:    str

    trade_id:          str               = field(default_factory=lambda: str(uuid.uuid4()))
    broker_trade_id:   Optional[str]     = None
    source:            str               = "manual"

    filled_price:      Optional[float]   = None
    filled_quantity:   Optional[int]     = None
    filled_at:         Optional[str]     = None  # ISO 8601 date

    snapshot_date:     Optional[str]     = None
    snapshot_gap_days: Optional[int]     = None
    score_snapshot:    Optional[dict]    = None  # JSON

    ticker_name:       Optional[str]     = None  # 저장 시점 종목명 (pykrx 의존 제거)

    buy_reasons:       list[str]         = field(default_factory=list)  # BuyReason values
    sell_reasons:      list[str]         = field(default_factory=list)  # SellReason values
    memo:              Optional[str]     = None

    reviewed_at:       Optional[str]     = None
    verdict:           Optional[str]     = None  # Verdict value
    review_note:       Optional[str]     = None

    created_at:        Optional[str]     = None
    updated_at:        Optional[str]     = None
