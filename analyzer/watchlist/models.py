from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional

from constants import Grade, Confidence


class WatchlistState(str, Enum):
    ACTIVE   = "active"    # 현재 추적
    REVIEW   = "review"    # 재검토 필요
    DORMANT  = "dormant"   # 관심 낮음
    REJECTED = "rejected"  # quick reject / 반복 AVOID


class WatchlistTag(str, Enum):
    GENERAL         = "general"          # 📋 일반
    CALIBRATION     = "calibration"      # 🧪 Calibration 관찰
    FALSE_POSITIVE  = "false_positive"   # ⚠️ False Positive 후보
    MOMENTUM        = "momentum"         # 🔥 Momentum 검증
    MARKET_IGNORE   = "market_ignore"    # 📉 시장 외면 중
    RESEARCH        = "research"         # 🔍 Research
    GOVERNANCE_RISK = "governance_risk"  # 🚨 Risk Radar HIGH
    CLEAN           = "clean"            # 🟢 Risk Radar LOW


WATCHLIST_TAG_EMOJI: dict[WatchlistTag, str] = {
    WatchlistTag.GENERAL:         "📋",
    WatchlistTag.CALIBRATION:     "🧪",
    WatchlistTag.FALSE_POSITIVE:  "⚠️",
    WatchlistTag.MOMENTUM:        "🔥",
    WatchlistTag.MARKET_IGNORE:   "📉",
    WatchlistTag.RESEARCH:        "🔍",
    WatchlistTag.GOVERNANCE_RISK: "🚨",
    WatchlistTag.CLEAN:           "🟢",
}


@dataclass
class WatchlistItem:
    ticker:          str
    state:           WatchlistState
    added_date:      date
    last_analyzed:   Optional[date]
    last_grade:      Optional[Grade]
    last_score:      Optional[int]
    last_confidence: Optional[Confidence]
    tag:             WatchlistTag  = WatchlistTag.GENERAL
    name:            Optional[str] = None


@dataclass(frozen=True)
class ReviewTrigger:
    ticker: str
    event:  str
    detail: str
