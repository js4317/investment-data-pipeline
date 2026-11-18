"""
Market Context Domain

This package represents factual market information only.

Responsibilities:
- Corporate filings (DART)
- Upcoming corporate events (earnings dates, dividend record dates, AGM)
- AI-generated market summaries (Layer 2, on-demand)

Non-responsibilities:
- Investment scoring or grade assignment
- Buy/sell recommendations
- Portfolio decisions or position sizing

Priority and presentation order are computed by the UI layer, not stored here.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ContextSource(str, Enum):
    DART   = "dart"    # 📄 공시 (DART API)
    CLAUDE = "claude"  # 📰 AI 분석 요약
    NEWS   = "news"    # 🌐 뉴스
    USER   = "user"    # 👤 사용자 직접 입력


class EventStatus(str, Enum):
    TODAY    = "today"     # 🔴 오늘
    UPCOMING = "upcoming"  # 🟡 예정
    PAST     = "past"      # ⚪ 지남 (공시 기본값)


class FilingImportance(int, Enum):
    """
    int 상속으로 비교·정렬 가능.
    sorted(items, reverse=True) → CRITICAL 먼저.
    """
    INFO      = 1   # 🟢 일상 정보 — 정기보고, 기타
    IMPORTANT = 2   # 🟡 주요 확인 — 배당, 자사주, 계약
    CRITICAL  = 3   # 🔴 즉각 확인 — 실적, 유상증자, CB


_IMPORTANCE_EMOJI: dict[FilingImportance, str] = {
    FilingImportance.INFO:      "🟢",
    FilingImportance.IMPORTANT: "🟡",
    FilingImportance.CRITICAL:  "🔴",
}

_SOURCE_ICON: dict[ContextSource, str] = {
    ContextSource.DART:   "📄",
    ContextSource.CLAUDE: "📰",
    ContextSource.NEWS:   "🌐",
    ContextSource.USER:   "👤",
}

_STATUS_EMOJI: dict[EventStatus, str] = {
    EventStatus.TODAY:    "🔴",
    EventStatus.UPCOMING: "🟡",
    EventStatus.PAST:     "⚪",
}


# NOTE:
# MarketContextItem stores intrinsic event metadata only.
# Display priority is intentionally NOT stored here.
#
# Dashboard/Watchlist priority is computed in the view layer from:
#   - importance  (event severity — how significant is this event?)
#   - status      (today / upcoming / past — how urgent is it right now?)
#   - seen state  (has the user already acknowledged this item?)
#
# This keeps the domain model immutable and reusable across all views.
# Priority computation will be introduced in v8.2–8.3 as a separate layer.


@dataclass(frozen=True)
class MarketContextItem:
    """
    Market Context 공통 단위.
    DART 공시, Upcoming Events, Claude 요약, 뉴스 전부 이 모델로 표현.

    item_type  : provider별 타입 문자열 (FilingType.value, "earnings_call" 등)
    type_icon  : provider가 직접 설정하는 표시용 아이콘
    source     : 데이터 출처 (ContextSource)
    source_id  : 고유 식별자 (DART = rcept_no, 이벤트 = 자체 ID)
    status     : 이벤트 시점 (공시는 PAST 기본값, 예정 이벤트는 TODAY/UPCOMING)

    정렬:
        sorted(items, reverse=True)          → importance CRITICAL 먼저
        sorted(items, key=lambda i: i.date)  → 날짜 오름차순
    """
    title:      str
    date:       str                # "YYYY-MM-DD"
    item_type:  str                # provider-specific
    importance: FilingImportance
    source:     ContextSource
    url:        Optional[str]
    source_id:  str
    type_icon:  str       = "📄"          # provider가 설정
    status:     EventStatus = EventStatus.PAST

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, MarketContextItem):
            return NotImplemented
        return self.importance < other.importance

    @property
    def importance_emoji(self) -> str:
        return _IMPORTANCE_EMOJI.get(self.importance, "⚪")

    @property
    def source_icon(self) -> str:
        return _SOURCE_ICON.get(self.source, "📌")

    @property
    def status_emoji(self) -> str:
        return _STATUS_EMOJI.get(self.status, "⚪")

    @property
    def is_critical(self) -> bool:
        return self.importance == FilingImportance.CRITICAL

    @property
    def is_active(self) -> bool:
        """Dashboard 표시 대상: TODAY 또는 UPCOMING."""
        return self.status in (EventStatus.TODAY, EventStatus.UPCOMING)
