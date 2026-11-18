from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Position:
    ticker:     str
    shares:     int
    avg_price:  float
    weight_pct: float   # shares * avg_price / total_value * 100


@dataclass
class PortfolioState:
    total_value: float
    positions:   dict[str, Position]
    stale_since: Optional[str] = None  # 브로커 연결 실패 시 마지막 저장 시각

    @property
    def cash_pct(self) -> float:
        return max(0.0, round(100.0 - sum(p.weight_pct for p in self.positions.values()), 1))
