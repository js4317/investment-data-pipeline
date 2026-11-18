from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class TradeSide(str, Enum):
    BUY  = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class Holding:
    ticker:             str
    name:               str
    shares:             int
    avg_price:          float    # 매수 평균가 (native currency)
    current_price:      float    # 현재가 (native currency)
    unrealized_pnl:     float    # 평가손익 세전 (native currency)
    unrealized_pnl_pct: float    # 손익률 소수비율 (0.1077 = 10.77%)
    market_value:       float    # 평가금액 (native currency)
    currency:           str      # "KRW" | "USD"


@dataclass(frozen=True)
class Trade:
    """
    체결 완료된 거래 1건.
    trade_id = Broker가 발급한 고유 주문 식별자. Sync idempotency key.
    """
    trade_id:        str        # Toss orderId
    ticker:          str
    side:            TradeSide
    filled_price:    float      # 평균 체결가 (native currency)
    filled_quantity: int
    filled_amount:   float      # 총 체결금액 (native currency)
    commission:      float      # 수수료 (native currency)
    tax:             float      # 세금 (native currency)
    filled_at:       datetime   # 체결 시각 (KST, timezone-aware)
    broker:          str        # "toss"
