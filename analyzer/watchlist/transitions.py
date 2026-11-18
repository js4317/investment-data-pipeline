from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from constants import Grade, Confidence
from analyzer.watchlist.models import WatchlistItem, WatchlistState, ReviewTrigger

if TYPE_CHECKING:
    from run import AnalysisResult

_STRONG_GRADES  = {Grade.STRONG_BUY, Grade.BUY}
_NON_BUY_GRADES = {Grade.NEUTRAL, Grade.AVOID}
_CONF_ORDER     = [Confidence.HIGH, Confidence.MEDIUM, Confidence.LOW]

NEUTRAL_STREAK_THRESHOLD = 3  # ACTIVE → DORMANT 기준


def _is_quick_reject(result: AnalysisResult) -> bool:
    return any("Quick Reject" in note for note in result.scoring.notes)


def _confidence_degraded(prev: Confidence, curr: Confidence) -> bool:
    return _CONF_ORDER.index(curr) > _CONF_ORDER.index(prev)


def apply(
    item: WatchlistItem,
    result: AnalysisResult,
    as_of_date: date,
) -> tuple[WatchlistItem, list[ReviewTrigger]]:
    """
    현재 WatchlistItem + 새 AnalysisResult → (updated item, triggers).

    호출자는 triggers를 출력/기록하고 updated item을 store에 저장한다.
    """
    triggers: list[ReviewTrigger] = []
    new_state = item.state
    ticker    = item.ticker

    if _is_quick_reject(result):
        new_state = WatchlistState.REJECTED
        triggers.append(ReviewTrigger(
            ticker=ticker,
            event="quick_reject",
            detail=f"Quick Reject 발생 → REJECTED ({result.scoring.grade.value})",
        ))

    elif result.scoring.grade == Grade.AVOID:
        prev = item.last_grade
        if item.state == WatchlistState.ACTIVE and prev in _STRONG_GRADES:
            new_state = WatchlistState.REVIEW
            triggers.append(ReviewTrigger(
                ticker=ticker,
                event="grade_drop_urgent",
                detail=f"{prev.value.upper()} → AVOID",
            ))
        else:
            new_state = WatchlistState.DORMANT

    elif item.state == WatchlistState.DORMANT:
        # DORMANT 상태: BUY/STRONG_BUY만 REVIEW로 복귀. 나머지는 DORMANT 유지.
        if result.scoring.grade in _STRONG_GRADES:
            new_state = WatchlistState.REVIEW
            triggers.append(ReviewTrigger(
                ticker=ticker,
                event="grade_upgrade_from_dormant",
                detail=f"DORMANT → {result.scoring.grade.value.upper()} 회복",
            ))
        else:
            new_state = WatchlistState.DORMANT

    elif item.state == WatchlistState.REVIEW:
        # REVIEW 상태: 자동 천이 없음. 사람이 직접 변경.
        new_state = WatchlistState.REVIEW

    else:
        # ACTIVE 상태
        prev = item.last_grade
        if result.scoring.grade in _STRONG_GRADES:
            new_state = WatchlistState.ACTIVE
        elif result.scoring.grade == Grade.NEUTRAL:
            from backtests.snapshot import get_neutral_streak
            streak = get_neutral_streak(ticker, n=NEUTRAL_STREAK_THRESHOLD)
            if streak >= NEUTRAL_STREAK_THRESHOLD:
                new_state = WatchlistState.DORMANT
                triggers.append(ReviewTrigger(
                    ticker=ticker,
                    event="neutral_streak_dormant",
                    detail=f"NEUTRAL {streak}회 연속 → DORMANT",
                ))
            else:
                new_state = WatchlistState.ACTIVE
        else:
            new_state = WatchlistState.ACTIVE

    if (
        item.last_confidence is not None
        and _confidence_degraded(item.last_confidence, result.scoring.confidence)
        and new_state not in (WatchlistState.REJECTED, WatchlistState.REVIEW)
    ):
        new_state = WatchlistState.REVIEW
        triggers.append(ReviewTrigger(
            ticker=ticker,
            event="confidence_drop",
            detail=f"신뢰도 하락 {item.last_confidence.value.upper()} → {result.scoring.confidence.value.upper()}",
        ))

    updated = WatchlistItem(
        ticker=ticker,
        state=new_state,
        added_date=item.added_date,
        last_analyzed=as_of_date,
        last_grade=result.scoring.grade,
        last_score=result.scoring.normalized_score,
        last_confidence=result.scoring.confidence,
        tag=item.tag,
        name=item.name,
    )
    return updated, triggers
