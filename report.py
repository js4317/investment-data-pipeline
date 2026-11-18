from __future__ import annotations

from datetime import date
from typing import Optional, TYPE_CHECKING

import config
from constants import Grade, GRADE_EMOJI
from analyzer.data.krx import get_ticker_name
from analyzer.portfolio.rules import WeightAdvisory
from analyzer.watchlist import store as wl_store
from analyzer.watchlist import transitions as wl_transitions
from analyzer.watchlist.models import WatchlistItem, WatchlistState

if TYPE_CHECKING:
    from run import AnalysisResult
    from backtests.diff import SnapshotDiff

_STATE_EMOJI = {
    WatchlistState.ACTIVE:   "🟢",
    WatchlistState.REVIEW:   "🔶",
    WatchlistState.DORMANT:  "⚫",
    WatchlistState.REJECTED: "❌",
}


_ADVISORY_LABEL = {
    "over":  "⚠️  비중 초과",
    "ok":    "✅  비중 적정",
    "watch": "👁  관찰 유지",
    "new":   "💡  진입 가능",
    "exit":  "🚫  비중 정리",
}


def _format_advisory(adv: WeightAdvisory) -> str:
    label = _ADVISORY_LABEL.get(adv.status, adv.status)
    rec   = f"권장 한도 {adv.recommended_max_pct}%"
    if adv.current_pct is not None:
        excess = f"  (+{adv.current_pct - adv.recommended_max_pct:.1f}%p)" if adv.status == "over" else ""
        return f"     {label}  현재 {adv.current_pct}% / {rec}{excess}"
    return f"     {label}  미보유 / {rec}"


def print_report(
    results: list[AnalysisResult],
    advisories: Optional[dict[str, WeightAdvisory]] = None,
) -> None:
    if not results:
        print("분석 결과 없음")
        return

    market = results[0].market
    w = 64
    print(f"\n{'=' * w}")
    print(f"  투자 분석 리포트  {market.as_of_date}  {config.RULE_VERSION_STR}")
    print(f"  시장: {market.regime.value}  |  과열: {market.heat.heat_level.value} ({market.heat.heat_score}점)")
    print(f"  {market.heat.detail}")
    print(f"{'=' * w}")

    for r in sorted(results, key=lambda x: (x.decision.executable, x.scoring.total_score), reverse=True):
        s = r.scoring
        d = r.decision
        emoji  = GRADE_EMOJI[s.grade]
        status = "✅ 실행가능" if d.executable else "🚫 차단"
        score_display = (
            f"{s.normalized_score}점 (raw {s.total_score}/{s.available_max})"
            if s.available_max < 100
            else f"{s.normalized_score}점"
        )

        name = get_ticker_name(s.ticker)
        print(
            f"\n  {emoji} {name}({s.ticker})  [{s.grade.value.upper()}]  "
            f"{score_display}  |  {s.confidence.value}  |  {status}"
        )
        print(f"     {d.reason}")
        print(
            f"     밸류 {s.valuation_score:>2} / 실적 {s.earnings_score:>2} "
            f"/ 리스크 {s.risk_score:>2} / 수급 {s.supply_score:>2}"
        )
        for comp in s.components:
            print(f"     • {comp.name:<12} {comp.score:>3}/{comp.max_score:<3}  {comp.detail}")
        for note in s.notes:
            print(f"     ⚠  {note}")
        if advisories and s.ticker in advisories:
            print(_format_advisory(advisories[s.ticker]))

    print(f"\n{'=' * w}\n")


def print_delta_summary(diff: SnapshotDiff) -> None:
    from backtests.diff import grade_direction, is_likely_quick_reject

    w = 64
    print(f"\n{'─' * w}")
    print("  📊 Delta Summary")
    print(f"{'─' * w}")

    if not diff.has_changes:
        print("  변화 없음")
        print(f"{'─' * w}\n")
        return

    exec_changed = {ticker for ticker, _, _ in diff.exec_changes}

    for ticker, a, b in diff.grade_changes:
        name      = get_ticker_name(ticker)
        direction = grade_direction(a["grade"], b["grade"])
        arrow     = "🟢" if direction == "up" else "🔴"
        delta     = b["normalized_score"] - a["normalized_score"]
        delta_str = f"+{delta}" if delta >= 0 else str(delta)
        exec_tag  = (
            "  🔒 blocked"    if ticker in exec_changed and not b["executable"] else
            "  🔓 executable" if ticker in exec_changed and b["executable"]     else ""
        )
        qr_tag    = "  ⚡ quick reject" if is_likely_quick_reject(b) else ""
        print(f"  {arrow} {name}({ticker})")
        print(f"       {a['grade'].upper()} → {b['grade'].upper()}  {delta_str}점{exec_tag}{qr_tag}")

    for ticker, a, b in diff.exec_changes:
        if a["grade"] == b["grade"]:   # grade 변화 없는 exec 변화만
            name  = get_ticker_name(ticker)
            arrow = "🔒" if not b["executable"] else "🔓"
            state = "blocked" if not b["executable"] else "executable"
            print(f"  {arrow} {name}({ticker})  {state}")

    for ticker, b in diff.new_tickers:
        name  = get_ticker_name(ticker)
        emoji = GRADE_EMOJI[Grade(b["grade"])]
        print(f"  🆕 {emoji}{name}({ticker})  {b['grade'].upper()}  {b['normalized_score']}점")

    for ticker, a in diff.removed_tickers:
        name = get_ticker_name(ticker)
        print(f"  🗑  {name}({ticker})  제외")

    print(f"{'─' * w}\n")


def print_watchlist_update(
    results_by_ticker: dict[str, AnalysisResult],
    as_of_date: date,
) -> None:
    w = 64
    print(f"\n{'─' * w}")
    print("  Watchlist 업데이트")
    print(f"{'─' * w}")

    for ticker, result in sorted(results_by_ticker.items()):
        item = wl_store.load(ticker) or WatchlistItem(
            ticker=ticker,
            state=WatchlistState.ACTIVE,
            added_date=as_of_date,
            last_analyzed=None,
            last_grade=None,
            last_score=None,
            last_confidence=None,
        )
        updated, triggers = wl_transitions.apply(item, result, as_of_date)
        wl_store.save(updated)

        state_changed = item.last_analyzed is not None and item.state != updated.state
        emoji  = _STATE_EMOJI[updated.state]
        change = f"  [{item.state.value} → {updated.state.value}]" if state_changed else ""
        name   = get_ticker_name(ticker)
        print(f"\n  {emoji} {name}({ticker})  {updated.state.value.upper()}{change}")
        for t in triggers:
            print(f"     ⚡ {t.event}: {t.detail}")

    print(f"\n{'─' * w}\n")
