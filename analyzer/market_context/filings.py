from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Optional

from analyzer.market_context.models import (
    ContextSource,
    EventStatus,
    FilingImportance,
    MarketContextItem,
)

_SEEN_FILE = Path(__file__).parent.parent.parent / "data" / "seen_filings.json"


# ---------------------------------------------------------------------------
# FilingType — DART 공시 전용 타입 (ContextSource.DART 에서만 사용)
# ---------------------------------------------------------------------------

class FilingType(str, Enum):
    EARNINGS = "earnings"   # 📈 잠정실적 · 영업실적
    DIVIDEND = "dividend"   # 💰 배당 결정
    BUYBACK  = "buyback"    # 🏦 자사주 취득 · 소각
    RIGHTS   = "rights"     # ⚠️ 유상증자 (주식 희석 직접)
    CB       = "cb"         # ⚠️ 전환사채 · BW · 교환사채 (잠재 희석)
    CONTRACT = "contract"   # 🤝 공급계약 · 수주 · MOU
    REPORT   = "report"     # 📊 정기보고서
    OTHER    = "other"      # 📄 기타


_TYPE_SHORT_LABEL: dict[FilingType, str] = {
    FilingType.EARNINGS: "잠정실적",
    FilingType.DIVIDEND: "배당",
    FilingType.BUYBACK:  "자사주",
    FilingType.RIGHTS:   "유상증자",
    FilingType.CB:       "CB발행",
    FilingType.CONTRACT: "계약",
    FilingType.REPORT:   "보고서",
    FilingType.OTHER:    "공시",
}

_TYPE_ICON: dict[FilingType, str] = {
    FilingType.EARNINGS: "📈",
    FilingType.DIVIDEND: "💰",
    FilingType.BUYBACK:  "🏦",
    FilingType.RIGHTS:   "⚠️",
    FilingType.CB:       "⚠️",
    FilingType.CONTRACT: "🤝",
    FilingType.REPORT:   "📊",
    FilingType.OTHER:    "📄",
}

_TYPE_IMPORTANCE: dict[FilingType, FilingImportance] = {
    FilingType.EARNINGS: FilingImportance.CRITICAL,
    FilingType.RIGHTS:   FilingImportance.CRITICAL,
    FilingType.CB:       FilingImportance.CRITICAL,
    FilingType.DIVIDEND: FilingImportance.IMPORTANT,
    FilingType.BUYBACK:  FilingImportance.IMPORTANT,
    FilingType.CONTRACT: FilingImportance.IMPORTANT,
    FilingType.REPORT:   FilingImportance.INFO,
    FilingType.OTHER:    FilingImportance.INFO,
}

# 순서 중요: 구체적인 키워드가 앞에 와야 중복 분류 방지
_CLASSIFY_RULES: list[tuple[list[str], FilingType]] = [
    (["잠정실적", "영업실적", "영업(잠정)", "실적공시", "잠정영업실적"],          FilingType.EARNINGS),
    (["현금배당", "주식배당", "배당결정", "배당금", "현금ㆍ현물배당"],            FilingType.DIVIDEND),
    (["자기주식취득", "자기주식소각", "자기주식처분", "자사주취득"],               FilingType.BUYBACK),
    (["유상증자"],                                                                 FilingType.RIGHTS),
    (["전환사채", "신주인수권부사채", "교환사채", "전환우선주", "무기명식이권부"],  FilingType.CB),
    (["단일판매", "공급계약", "수주", "납품계약", "공사계약", "업무협약", "mou"],  FilingType.CONTRACT),
    (["사업보고서", "분기보고서", "반기보고서", "연간보고서"],                     FilingType.REPORT),
]


def get_type_label(item_type: str) -> str:
    """item_type 문자열(FilingType.value) → 표시용 짧은 한국어 레이블."""
    try:
        return _TYPE_SHORT_LABEL[FilingType(item_type)]
    except (ValueError, KeyError):
        return item_type


def classify_filing(report_nm: str) -> FilingType:
    lower = report_nm.lower()
    for keywords, ftype in _CLASSIFY_RULES:
        if any(kw in lower for kw in keywords):
            return ftype
    return FilingType.OTHER


# ---------------------------------------------------------------------------
# DartFilingProvider
# ---------------------------------------------------------------------------

def _make_item(raw: dict) -> MarketContextItem:
    rn    = raw.get("report_nm", "")
    ftype = classify_filing(rn)
    d     = raw.get("rcept_dt", "")
    return MarketContextItem(
        title      = rn,
        date       = f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else d,
        item_type  = ftype.value,
        importance = _TYPE_IMPORTANCE[ftype],
        source     = ContextSource.DART,
        url        = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={raw['rcept_no']}"
                     if raw.get("rcept_no") else None,
        source_id  = raw.get("rcept_no", ""),
        type_icon  = _TYPE_ICON[ftype],
        status     = EventStatus.PAST,
    )


class DartFilingProvider:
    """MarketContextProvider 구현체 — DART 공시."""

    def fetch(self, ticker: str, days: int = 30, **kwargs) -> list[MarketContextItem]:
        from analyzer.data.dart import get_recent_filings
        return [_make_item(r) for r in get_recent_filings(ticker, days=days)]


def get_filings(ticker: str, days: int = 30) -> list[MarketContextItem]:
    """convenience wrapper. DART API key 없으면 빈 리스트."""
    return DartFilingProvider().fetch(ticker, days=days)


# ---------------------------------------------------------------------------
# Seen 추적
# ---------------------------------------------------------------------------

class SeenTracker:
    """data/seen_filings.json 기반 읽음 상태. source_id 단위로 추적."""

    def __init__(self) -> None:
        self._data: dict[str, set[str]] = {}
        self._load()

    def _load(self) -> None:
        if _SEEN_FILE.exists():
            try:
                raw        = json.loads(_SEEN_FILE.read_text(encoding="utf-8"))
                self._data = {k: set(v) for k, v in raw.items()}
            except Exception:
                self._data = {}

    def _save(self) -> None:
        _SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SEEN_FILE.write_text(
            json.dumps({k: sorted(v) for k, v in self._data.items()},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def mark_seen(self, ticker: str, items: list[MarketContextItem]) -> None:
        self._data.setdefault(ticker, set()).update(i.source_id for i in items)
        self._save()

    def unseen(self, ticker: str, items: list[MarketContextItem]) -> list[MarketContextItem]:
        seen = self._data.get(ticker, set())
        return [i for i in items if i.source_id not in seen]

    def unseen_count(self, ticker: str, items: list[MarketContextItem]) -> int:
        return len(self.unseen(ticker, items))

    def critical_unseen(self, ticker: str, items: list[MarketContextItem]) -> list[MarketContextItem]:
        return [i for i in self.unseen(ticker, items) if i.is_critical]


_seen_tracker: Optional[SeenTracker] = None


def get_seen_tracker() -> SeenTracker:
    global _seen_tracker
    if _seen_tracker is None:
        _seen_tracker = SeenTracker()
    return _seen_tracker
