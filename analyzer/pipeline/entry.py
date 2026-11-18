"""Pure entry/source-time decision. No fetches, calendar guessing, or DB writes."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from database.signal_store import CUTOFF, KST, _stamp
from database.trading_calendar import CalendarSnapshot, calendar_official_reasons


def _kst(value: datetime) -> datetime:
    return datetime.fromisoformat(_stamp(value))


@dataclass(frozen=True)
class MarketInput:
    name: str
    fetched_at: datetime
    source_session_date: date | None
    source_status: str
    source_as_of_time: datetime | None = None
    # 5단계 원천 근거 (§13.5). definition_id가 있으면 저장 시 원 응답 재파싱으로 전부 검증된다.
    value: float | None = None
    definition_id: str | None = None
    params_json: str | None = None
    missing_kind: str | None = None
    basis_status: str | None = None
    failure_reason: str | None = None
    responses: tuple[str, ...] = ()
    rows: tuple[tuple[str, int], ...] = ()
    detail_json: str | None = None


@dataclass(frozen=True)
class FinancialInput:
    name: str
    value_fetched_at: datetime
    filing_list_fetched_at: datetime
    report_period_end: date | None
    report_type: str
    rcept_no: str | None
    rcept_dt: date | None
    rcept_no_linked: bool
    is_latest_version_at_fetch: bool
    source_status: str
    is_amendment: bool = False


@dataclass(frozen=True)
class RunContext:
    run_started_at: datetime
    expected_market_session: date | None
    calendar_version: str
    calendar_check_id: str | None

    @classmethod
    def start(cls, now: datetime, calendar: CalendarSnapshot) -> RunContext:
        now = _kst(now)
        expected = None if calendar.check_reasons(now) else calendar.previous_session(now.date())
        return cls(now, expected, calendar.version,
                   calendar.notice_check.check_id if calendar.notice_check else None)


@dataclass(frozen=True)
class EntryDecision:
    planned_entry_session: date | None
    entry_rule: str
    reasons: tuple[str, ...]
    confirmed_at: datetime
    run_context: RunContext
    calendar_version: str
    calendar_check_id: str | None
    is_session: bool

    @property
    def eligible(self) -> bool:
        return not self.reasons


def planned_entry(run: RunContext, confirmed_at: datetime,
                  inputs: list[MarketInput | FinancialInput],
                  calendar: CalendarSnapshot) -> EntryDecision:
    """Use one pinned calendar and one expected market session throughout a run.

    confirmed_at must be the runner's verified confirmation time. This pure
    function does not establish actual SQLite COMMIT completion (stage 4).
    """
    confirmed_at, started_at = _kst(confirmed_at), _kst(run.run_started_at)
    day = confirmed_at.date()
    reasons = list(calendar.check_reasons(started_at))
    reasons += calendar_official_reasons(calendar)     # 달력 구분: official 기록이 없거나 시험 표시면 부적격
    if run.calendar_version != calendar.version:
        reasons.append('calendar_version_mismatch')
    check_id = calendar.notice_check.check_id if calendar.notice_check else None
    if run.calendar_check_id != check_id:
        reasons.append('calendar_notice_check_mismatch')
    if run.expected_market_session is None:
        reasons.append('calendar_unknown')
    elif run.expected_market_session != calendar.previous_session(started_at.date()):
        reasons.append('run_context_session_mismatch')
    if confirmed_at < started_at:
        reasons.append('confirmation_before_run')
    if not inputs:
        reasons.append('inputs_missing')
    checks = []
    for item in inputs:
        if isinstance(item, MarketInput):
            checked = _kst(item.fetched_at)
            checks.append(checked)
            if checked >= confirmed_at:
                reasons.append('checked_after_confirm')
            if checked < started_at:
                reasons.append('market_fetched_before_run')
            if item.source_session_date is None:
                reasons.append('market_date_missing')
            elif item.source_session_date != run.expected_market_session:
                reasons.append('market_session_mismatch')
            if item.source_status != 'verified':
                if item.definition_id and item.definition_id.startswith('dart_'):
                    # DART 재무 입력: 실패 사유 그대로 공식 적격성 차단 (접수번호 불일치·신선도 등, §13.23)
                    reasons.append('fin_' + (item.failure_reason or 'unverified'))
                else:
                    reasons.append('market_unverified')
            if item.source_as_of_time is not None:
                source_time = _kst(item.source_as_of_time)
                if source_time > checked or source_time.date() != item.source_session_date:
                    reasons.append('market_time_invalid')
        elif isinstance(item, FinancialInput):
            checked, value_checked = _kst(item.filing_list_fetched_at), _kst(item.value_fetched_at)
            checks.append(checked)
            if checked >= confirmed_at or value_checked >= confirmed_at:
                reasons.append('checked_after_confirm')
            if not item.rcept_no or item.rcept_no_linked is not True:
                reasons.append('fin_link_missing')
            if (item.rcept_dt is None or item.rcept_dt > checked.date()
                    or item.rcept_dt > value_checked.date()):
                reasons.append('fin_filing_date_invalid')
            if checked < started_at:
                reasons.append('fin_version_check_stale')
            if item.is_latest_version_at_fetch is not True:
                reasons.append('fin_not_latest_version')
            if item.source_status != 'verified':
                reasons.append('fin_unverified')
            if not item.report_type or item.report_period_end is None:
                reasons.append('fin_report_metadata_missing')
            elif item.rcept_dt is not None and item.report_period_end > item.rcept_dt:
                reasons.append('fin_report_period_invalid')
        else:
            reasons.append('input_class_invalid')

    if calendar.status(day) == 'unknown':
        session, rule = None, 'calendar_unknown'
        reasons.append('calendar_unknown')
    elif (calendar.status(day) == 'session' and confirmed_at.time() < CUTOFF
          and checks and len(checks) == len(inputs)
          and all(t.date() == day and t.time() < CUTOFF for t in checks)):
        session, rule = day, 'same_day_open'
    else:
        session, rule = calendar.next_session_after(day), 'next_session_open'
        if session is None:
            reasons.append('calendar_unknown')
    return EntryDecision(session, rule, tuple(sorted(set(reasons))), confirmed_at,
                         run, calendar.version, check_id, calendar.status(day) == 'session')
