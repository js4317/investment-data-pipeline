"""Versioned, explicitly reviewed KRX equity dates; no price-based inference.

Annual/temporary notices must be reviewed into CalendarDay records. This module
does not parse derivatives schedules into equity dates or invent missing days.
Daily notice checks and production initialization belong to the official runner.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from types import MappingProxyType
from typing import Mapping
from urllib.parse import urlparse
import json
import re

from database.signal_store import CUTOFF, KST, SignalStore, _stamp


CLASSIFICATIONS = ('test', 'official')
# 보조 방어: 시험 달력 표시 (구분 기록과 별개로 항상 검사). 이름·주소만 바꿔도 구분 기록이 test/없음이면 통과하지 못한다.
TEST_VERSION_PREFIX = 'iso-test-'
_TEST_REF = re.compile(r'(?i)(iso-test|not-official|test-fixture|synthetic)')


def _test_marked_ref(ref: str) -> bool:
    url = urlparse(ref)
    return bool(_TEST_REF.search(url.path + '?' + url.query + '#' + url.fragment))


def calendar_official_reasons(calendar: 'CalendarSnapshot') -> tuple[str, ...]:
    """공식 적격 판정용 달력 구분 검사 (planned_entry 하나에서만 호출 → 확정 시 재계산도 같은 함수).
    구분 기록이 'official'이 아니면(없음·test) 부적격. 시험 표시(버전 접두어·출처 주소)가 있어도 부적격."""
    refs = [r.source_ref for r in calendar.days.values()]
    if calendar.notice_check is not None:
        refs += list(calendar.notice_check.source_refs)
    if (calendar.classification != 'official' or calendar.version.startswith(TEST_VERSION_PREFIX)
            or any(_test_marked_ref(r) for r in refs)):
        return ('calendar_not_official',)
    return ()


def _official_ref(ref: str) -> bool:
    url = urlparse(ref)
    host = url.hostname or ''
    return url.scheme == 'https' and (host == 'krx.co.kr' or host.endswith('.krx.co.kr'))


@dataclass(frozen=True)
class CalendarDay:
    day: date
    status: str
    source: str
    source_ref: str
    checked_at: datetime

    def __post_init__(self):
        if self.status not in {'session', 'holiday', 'unknown'}:
            raise ValueError('invalid calendar status')
        if self.source != 'KRX_EQUITY':
            raise ValueError('only reviewed KRX equity evidence is accepted')
        if not _official_ref(self.source_ref):
            raise ValueError('official KRX source reference required')
        _stamp(self.checked_at)


@dataclass(frozen=True)
class CalendarNoticeCheck:
    check_id: str
    calendar_version: str
    checked_at: datetime
    status: str  # clear / changes_pending / failed
    source_refs: tuple[str, ...]

    def __post_init__(self):
        if not self.check_id or not self.calendar_version:
            raise ValueError('calendar check identity required')
        if self.status not in {'clear', 'changes_pending', 'failed'}:
            raise ValueError('invalid notice check status')
        if not self.source_refs or not all(_official_ref(r) for r in self.source_refs):
            raise ValueError('official KRX notice references required')
        _stamp(self.checked_at)
        object.__setattr__(self, 'source_refs', tuple(self.source_refs))


@dataclass(frozen=True)
class CalendarSnapshot:
    version: str
    days: Mapping[date, CalendarDay]
    notice_check: CalendarNoticeCheck | None = None
    classification: str | None = None      # 저장된 달력 구분(test/official). 없음 = 미승인

    def __post_init__(self):
        if not self.version or not self.days:
            raise ValueError('nonempty calendar version required')
        if self.classification not in (None, *CLASSIFICATIONS):
            raise ValueError('invalid calendar classification')
        if any(d != record.day for d, record in self.days.items()):
            raise ValueError('calendar date mismatch')
        object.__setattr__(self, 'days', MappingProxyType(dict(self.days)))

    def status(self, day: date) -> str:
        record = self.days.get(day)
        return record.status if record else 'unknown'

    def check_reasons(self, run_started_at: datetime) -> tuple[str, ...]:
        run_date = datetime.fromisoformat(_stamp(run_started_at)).date()
        if any(r.checked_at > run_started_at for r in self.days.values()):
            return ('calendar_checked_after_run',)
        check = self.notice_check
        if check is None:
            return ('calendar_notice_check_missing',)
        if check.calendar_version != self.version:
            return ('calendar_notice_version_mismatch',)
        if check.checked_at > run_started_at:
            return ('calendar_checked_after_run',)
        if check.checked_at.astimezone(KST).date() != run_date:
            return ('calendar_check_stale',)
        if check.checked_at.astimezone(KST).time() >= CUTOFF:
            return ('calendar_check_not_preopen',)
        if check.status != 'clear':
            return ('calendar_changes_pending' if check.status == 'changes_pending'
                    else 'calendar_notice_check_failed',)
        return ()

    def _nearest(self, day: date, direction: int) -> date | None:
        candidate = day + timedelta(days=direction)
        while True:
            status = self.status(candidate)
            if status == 'unknown':
                return None
            if status == 'session':
                return candidate
            candidate += timedelta(days=direction)

    def previous_session(self, day: date) -> date | None:
        return self._nearest(day, -1)

    def next_session_after(self, day: date) -> date | None:
        return self._nearest(day, 1)


class TradingCalendarStore(SignalStore):
    """Explicit DB path with the same stage-1 production path guard."""

    def initialize(self) -> None:
        with self._transaction() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS trading_calendar (
                date TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('session','holiday','unknown')),
                calendar_version TEXT NOT NULL, source TEXT NOT NULL,
                source_ref TEXT NOT NULL, checked_at TEXT NOT NULL,
                PRIMARY KEY(date,calendar_version))''')
            conn.execute('''CREATE TABLE IF NOT EXISTS calendar_notice_checks (
                check_id TEXT PRIMARY KEY, calendar_version TEXT NOT NULL,
                checked_at TEXT NOT NULL, status TEXT NOT NULL
                CHECK(status IN ('clear','changes_pending','failed')),
                source_refs_json TEXT NOT NULL)''')
            # 달력 버전 구분: 기록이 없으면 미승인(공식 아님). official은 승인 근거가 있어야 한다. 기존 달력을 공식으로 간주하지 않는다.
            conn.execute('''CREATE TABLE IF NOT EXISTS calendar_classifications (
                calendar_version TEXT PRIMARY KEY,
                classification TEXT NOT NULL CHECK(classification IN ('test','official')),
                approval_ref TEXT, recorded_at TEXT NOT NULL,
                CHECK(classification='test' OR (approval_ref IS NOT NULL AND length(trim(approval_ref))>0)))''')
            for operation in ('UPDATE', 'DELETE'):
                conn.execute(f'''CREATE TRIGGER IF NOT EXISTS calendar_class_immutable_{operation}
                    BEFORE {operation} ON calendar_classifications
                    BEGIN SELECT RAISE(ABORT,'immutable calendar classification'); END''')
            for operation in ('UPDATE', 'DELETE'):
                conn.execute(f'''CREATE TRIGGER IF NOT EXISTS calendar_immutable_{operation}
                    BEFORE {operation} ON trading_calendar
                    BEGIN SELECT RAISE(ABORT,'immutable calendar version'); END''')
                conn.execute(f'''CREATE TRIGGER IF NOT EXISTS notice_check_immutable_{operation}
                    BEFORE {operation} ON calendar_notice_checks
                    BEGIN SELECT RAISE(ABORT,'immutable calendar notice check'); END''')

    def save_notice_check(self, check: CalendarNoticeCheck) -> None:
        with self._transaction() as conn:
            if not conn.execute('SELECT 1 FROM trading_calendar WHERE calendar_version=?',
                                (check.calendar_version,)).fetchone():
                raise ValueError('unknown calendar version')
            record = (check.check_id, check.calendar_version, _stamp(check.checked_at),
                      check.status, json.dumps(check.source_refs))
            old = conn.execute('SELECT * FROM calendar_notice_checks WHERE check_id=?',
                               (check.check_id,)).fetchone()
            if old:
                if tuple(old) != record:
                    raise ValueError('notice check already fixed')
                return
            conn.execute('INSERT INTO calendar_notice_checks VALUES(?,?,?,?,?)', record)

    def classify_version(self, version: str, classification: str, *, now: datetime,
                         approval_ref: str | None = None) -> None:
        """달력 버전 구분을 한 번만 기록한다(불변). official은 승인 근거(approval_ref)가 필요하고,
        시험 표시(버전 접두어·출처 주소)가 있는 달력은 official로 기록할 수 없다."""
        if classification not in CLASSIFICATIONS:
            raise ValueError('invalid calendar classification')
        if classification == 'official' and not (approval_ref or '').strip():
            raise ValueError('official calendar requires approval evidence')
        with self._transaction() as conn:
            rows = conn.execute('SELECT source_ref FROM trading_calendar WHERE calendar_version=?', (version,)).fetchall()
            if not rows:
                raise ValueError('unknown calendar version')
            if classification == 'official':
                refs = [r[0] for r in rows] + [ref for (j,) in conn.execute(
                    'SELECT source_refs_json FROM calendar_notice_checks WHERE calendar_version=?', (version,))
                    for ref in json.loads(j)]
                if version.startswith(TEST_VERSION_PREFIX) or any(_test_marked_ref(r) for r in refs):
                    raise ValueError('test-marked calendar cannot be official')
            record = (version, classification, approval_ref, _stamp(now))
            old = conn.execute('SELECT * FROM calendar_classifications WHERE calendar_version=?', (version,)).fetchone()
            if old:
                if tuple(old)[:3] != record[:3]:
                    raise ValueError('calendar classification already fixed')
                return
            conn.execute('INSERT INTO calendar_classifications VALUES(?,?,?,?)', record)

    def save_version(self, version: str, days: list[CalendarDay]) -> None:
        if not version or not days or len({r.day for r in days}) != len(days):
            raise ValueError('nonempty unique calendar dates required')
        records = [(r.day.isoformat(), r.status, version, r.source, r.source_ref, _stamp(r.checked_at))
                   for r in sorted(days, key=lambda r: r.day)]
        with self._transaction() as conn:
            old = [tuple(r) for r in conn.execute(
                'SELECT * FROM trading_calendar WHERE calendar_version=? ORDER BY date', (version,))]
            if old:
                if old != records:
                    raise ValueError('calendar version already fixed')
                return
            conn.executemany('INSERT INTO trading_calendar VALUES(?,?,?,?,?,?)', records)

    def load_version(self, version: str, *, check_id: str | None = None) -> CalendarSnapshot:
        with self._connection() as conn:
            rows = conn.execute('SELECT * FROM trading_calendar WHERE calendar_version=? ORDER BY date',
                                (version,)).fetchall()
            check_row = conn.execute('SELECT * FROM calendar_notice_checks WHERE check_id=?',
                                     (check_id,)).fetchone() if check_id else None
            cls_row = conn.execute('SELECT classification FROM calendar_classifications WHERE calendar_version=?',
                                   (version,)).fetchone()
        if not rows:
            raise ValueError('unknown calendar version')
        if check_id and check_row is None:
            raise ValueError('unknown notice check')
        check = None if check_row is None else CalendarNoticeCheck(
            check_row['check_id'], check_row['calendar_version'],
            datetime.fromisoformat(check_row['checked_at']), check_row['status'],
            tuple(json.loads(check_row['source_refs_json'])))
        return CalendarSnapshot(version, {
            date.fromisoformat(r['date']): CalendarDay(date.fromisoformat(r['date']), r['status'],
                r['source'], r['source_ref'], datetime.fromisoformat(r['checked_at'])) for r in rows}, check,
            cls_row['classification'] if cls_row else None)
