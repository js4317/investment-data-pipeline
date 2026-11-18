from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import sqlite3

import pytest

from analyzer.pipeline.entry import FinancialInput, MarketInput, RunContext, planned_entry
from database.signal_store import KST, STRATEGY, SignalStore
from database.trading_calendar import CalendarDay, CalendarSnapshot, CalendarNoticeCheck, TradingCalendarStore

REF = 'https://regulation.krx.co.kr/contents/RGL/03/03010100/RGL03010100T1.jsp'
APPROVAL = 'unit-test approval record (판정 로직 시험용 승인 달력)'
START = datetime(2026, 10, 14, 8, 30, tzinfo=KST)
CONFIRM = START.replace(minute=49, second=59)


def calendar(start=START, version='test-v1', classification='official'):
    days = {}
    for n in range(7, 16):
        day = date(2026, 10, n)
        status = 'holiday' if n in (9, 10, 11) else 'session'
        days[day] = CalendarDay(day, status, 'KRX_EQUITY', REF, start-timedelta(minutes=10))
    return CalendarSnapshot(version, days, CalendarNoticeCheck(version+'-check',version,start-timedelta(minutes=5),'clear',(REF,)),
                            classification)


def save_calendar(path, cal):
    """DB에 달력·공지 확인·구분을 저장 (구분이 official이면 승인 근거와 함께)."""
    cs = TradingCalendarStore(path); cs.save_version(cal.version, list(cal.days.values())); cs.save_notice_check(cal.notice_check)
    if cal.classification:
        cs.classify_version(cal.version, cal.classification, now=START - timedelta(minutes=4),
                            approval_ref=APPROVAL if cal.classification == 'official' else None)
    return cs


def market(start=START, **changes):
    item = MarketInput('pbr', start+timedelta(minutes=1), calendar(start).previous_session(start.date()), 'verified')
    return replace(item, **changes)


def financial(start=START, **changes):
    item = FinancialInput('quarterly', datetime(2026,8,17,9,tzinfo=KST), start+timedelta(minutes=2),
        date(2026,6,30), 'quarter', '20260814000001', date(2026,8,14), True, True, 'verified')
    return replace(item, **changes)


def decide(inputs=None, confirmed=CONFIRM, cal=None, start=START):
    cal = cal or calendar(start)
    return planned_entry(RunContext.start(start, cal), confirmed,
                         [market(start)] if inputs is None else inputs, cal)


def test_T2_verified_preopen_same_day():
    result = decide([market(), financial()])
    assert result.planned_entry_session == date(2026,10,14)
    assert result.entry_rule == 'same_day_open' and result.eligible


@pytest.mark.parametrize('clock',[(8,50,0),(12,0,0),(17,0,0)])
def test_T3_cutoff_and_later_next_session(clock):
    result = decide(confirmed=START.replace(hour=clock[0],minute=clock[1],second=clock[2]))
    assert result.planned_entry_session == date(2026,10,15)
    assert result.entry_rule == 'next_session_open' and result.eligible


def test_T4_check_at_confirmation_is_not_prior_information():
    result = decide([market(fetched_at=CONFIRM)])
    assert 'checked_after_confirm' in result.reasons and not result.eligible


@pytest.mark.parametrize('session',[date(2026,10,14),date(2026,10,12)])
def test_T4a_c_current_or_stale_market_session(session):
    result = decide([market(),market(source_session_date=session)])
    assert 'market_session_mismatch' in result.reasons and not result.eligible


def test_T4b_old_fetch_even_with_right_source_session():
    result = decide([market(fetched_at=START-timedelta(days=1))])
    assert 'market_fetched_before_run' in result.reasons and not result.eligible


def test_T4d_cached_financial_value_fresh_filing_list():
    result = decide([market(),financial()])
    assert result.eligible and not any(r.startswith('fin_') for r in result.reasons)


@pytest.mark.parametrize('changes,reason',[
    ({'filing_list_fetched_at':START-timedelta(seconds=1)},'fin_version_check_stale'),
    ({'is_latest_version_at_fetch':False},'fin_not_latest_version'),
    ({'rcept_no_linked':False},'fin_link_missing'),
    ({'rcept_no':None},'fin_link_missing'),
])
def test_T4e_financial_provenance(changes,reason):
    result = decide([market(),financial(**changes)])
    assert reason in result.reasons and not result.eligible


def test_T5_missing_response_date_and_unverified_source():
    result = decide([market(source_session_date=None,source_status='unverified')])
    assert {'market_date_missing','market_unverified'} <= set(result.reasons)
    assert not result.eligible


def test_T19_holiday_skips_only_explicit_verified_days():
    start = START.replace(day=9)
    result = decide(start=start,confirmed=start+timedelta(minutes=15))
    assert result.planned_entry_session == date(2026,10,12)
    assert result.entry_rule == 'next_session_open' and result.eligible


def test_T24_missing_weekday_is_unknown():
    cal = calendar();days = dict(cal.days);days.pop(START.date())
    result = decide(cal=CalendarSnapshot('missing',days))
    assert result.planned_entry_session is None
    assert result.entry_rule == 'calendar_unknown' and not result.eligible


def test_calendar_unknown_gap_cannot_be_jumped():
    cal = calendar();days = dict(cal.days);days.pop(date(2026,10,10))
    cal = CalendarSnapshot('gap',days)
    assert cal.next_session_after(date(2026,10,9)) is None
    assert cal.previous_session(date(2026,10,11)) is None


def test_expected_session_is_frozen_and_calendar_cannot_switch():
    cal = calendar();run = RunContext.start(START,cal)
    assert run.expected_market_session == date(2026,10,13)
    changed = CalendarSnapshot('new-version',cal.days)
    result = planned_entry(run,CONFIRM,[market()],changed)
    assert 'calendar_version_mismatch' in result.reasons and not result.eligible


def test_source_exact_timestamp_is_not_invented_or_future():
    assert decide([market()]).eligible  # session date does not need an invented 15:30 timestamp
    result = decide([market(source_as_of_time=CONFIRM+timedelta(seconds=1))])
    assert 'market_time_invalid' in result.reasons and not result.eligible


def test_financial_value_acquired_after_confirmation_is_rejected():
    result = decide([financial(value_fetched_at=CONFIRM+timedelta(seconds=1))])
    assert 'checked_after_confirm' in result.reasons and not result.eligible


def test_financial_future_receipt_or_missing_period_is_rejected():
    result = decide([financial(rcept_dt=date(2026,10,15),report_period_end=None)])
    assert {'fin_filing_date_invalid','fin_report_metadata_missing'} <= set(result.reasons)
    assert not result.eligible


def test_naive_times_and_empty_inputs_fail_closed():
    with pytest.raises(ValueError,match='timezone-aware'):
        decide(confirmed=CONFIRM.replace(tzinfo=None))
    assert 'inputs_missing' in decide([]).reasons


def test_utc_times_are_compared_in_kst():
    utc = market(fetched_at=market().fetched_at.astimezone(timezone.utc))
    assert decide([utc],confirmed=CONFIRM.astimezone(timezone.utc)).eligible


@pytest.mark.parametrize('delta,reason',[(timedelta(days=-1),'calendar_check_stale'),
                                       (timedelta(hours=1),'calendar_checked_after_run')])
def test_calendar_check_must_precede_run_and_be_today(delta,reason):
    cal = calendar()
    result = decide(cal=replace(cal,notice_check=replace(cal.notice_check,checked_at=START+delta)))
    assert reason in result.reasons and not result.eligible


def test_calendar_versions_immutable_and_no_operational_signal_changes(tmp_path):
    path = tmp_path/'isolated.db';store = TradingCalendarStore(path);store.initialize()
    rows = list(calendar().days.values());store.save_version('v1',rows)
    store.save_version('v1',list(reversed(rows)))  # identical replay is allowed
    with pytest.raises(ValueError,match='already fixed'):
        store.save_version('v1',[replace(rows[0],status='holiday'),*rows[1:]])
    with sqlite3.connect(path) as c:
        for sql in ['DELETE FROM trading_calendar',"UPDATE trading_calendar SET status='holiday'"]:
            with pytest.raises(sqlite3.IntegrityError):c.execute(sql)
    changed = [replace(r,status='holiday') if r.day==START.date() else r for r in rows]
    store.save_version('v2',changed)
    old,new = store.load_version('v1'),store.load_version('v2')
    assert old.status(START.date())=='session' and new.status(START.date())=='holiday'
    assert store.load_version('v1').status(START.date())=='session'
    with pytest.raises(TypeError):old.days[START.date()]=changed[0]


def test_calendar_version_transaction_and_source_scope(tmp_path):
    store = TradingCalendarStore(tmp_path/'isolated.db');store.initialize();rows=list(calendar().days.values())
    with sqlite3.connect(store.path) as c:
        c.execute("CREATE TRIGGER injected BEFORE INSERT ON trading_calendar WHEN NEW.date='2026-10-10' BEGIN SELECT RAISE(ABORT,'failure'); END")
    with pytest.raises(sqlite3.IntegrityError):store.save_version('v1',rows)
    with sqlite3.connect(store.path) as c:assert c.execute('SELECT count(*) FROM trading_calendar').fetchone()[0]==0
    with pytest.raises(ValueError,match='equity'):
        replace(rows[0],source='KRX_DERIVATIVES')
    with pytest.raises(ValueError,match='official KRX'):
        replace(rows[0],source_ref='https://krx.co.kr.evil.example/calendar')


def stored_run(store, cal, universe_rule='test', confirmed=CONFIRM):
    """달력·입력을 DB에 저장한 실행을 만들고, 저장된 입력으로 판정을 계산한다 (실행기와 같은 경로)."""
    from analyzer.pipeline.runtime import LaunchEvidence
    from tests.evidence_fixture import attach
    save_calendar(store.path, cal)
    ctx=RunContext.start(START,cal)
    run=store.start_run(signal_date='2026-10-14',strategy_id=STRATEGY,trigger='scheduled',
        version='test',digest='hash',loaded_digest='test',git_commit='test-only',git_dirty=False,
        python_executable='test',pid=1,process_started_at=START,now=START,
        context=ctx,universe_rule=universe_rule,tickers=['000001'],
        launch=LaunchEvidence(1,'com.investment.signal','com.investment.signal',START))
    store.finish_run(run,[dict(ticker='000001',processing_status='scored',normalized_score=95,
        grade='strong_buy',confidence='high',executable=True)],'hash',START+timedelta(minutes=5),
        inputs=attach(store,run,{'m1':market()}),signal_inputs={'000001':['m1']})
    return run,planned_entry(ctx,confirmed,store.decision_inputs(run),cal)


def test_entry_decision_drives_primary_or_catchup(tmp_path):
    s = SignalStore(tmp_path/'isolated.db');s.initialize();s.register_engine('test','hash','test-only',START)
    cases=[('before',START.replace(minute=47,second=59),'primary'),
           ('margin',START.replace(minute=48),'catchup'),          # 확정 마감 08:48:00부터 catchup (§13.31)
           ('at',START.replace(minute=50),'catchup')]
    for version,confirmed,expected in cases:
        run,result=stored_run(s,calendar(version=version),universe_rule=version,confirmed=confirmed)
        key=s.confirm(run,now=confirmed,decision=result)
        with sqlite3.connect(s.path) as c:
            assert c.execute('SELECT oos_type,eligible FROM official_cohorts WHERE cohort_key=?',(key,)).fetchone()==(expected,1)


def prepared_run(tmp_path):
    store=SignalStore(tmp_path/'binding.db');store.initialize()
    store.register_engine('test','hash','test-only',START)
    run,decision=stored_run(store,calendar())
    return store,run,decision


def test_late_decision_cannot_confirm_with_earlier_clock(tmp_path):
    store,run,decision=prepared_run(tmp_path)
    late=planned_entry(decision.run_context,START.replace(minute=55),store.decision_inputs(run),calendar())
    with pytest.raises(ValueError):
        store.confirm(run,now=START.replace(minute=49),decision=late)
    with sqlite3.connect(store.path) as c:
        assert c.execute('SELECT count(*) FROM official_cohorts').fetchone()==(0,)
        assert c.execute('SELECT confirm_status FROM signal_runs').fetchone()==('not_attempted',)
    store.confirm(run,now=late.confirmed_at,decision=late)
    with sqlite3.connect(store.path) as c:
        assert c.execute('SELECT oos_type,entry_rule FROM official_cohorts').fetchone()==('catchup','next_session_open')


@pytest.mark.parametrize('change', ['session','version','check','started'])
def test_decision_context_mismatch_rejected_without_writes(tmp_path,change):
    store,run,decision=prepared_run(tmp_path)
    ctx=decision.run_context
    if change=='session': ctx=replace(ctx,expected_market_session=date(2020,1,2))
    if change=='version': ctx=replace(ctx,calendar_version='other')
    if change=='check': ctx=replace(ctx,calendar_check_id='other')
    if change=='started': ctx=replace(ctx,run_started_at=START-timedelta(seconds=1))
    with pytest.raises(ValueError):
        store.confirm(run,now=CONFIRM,decision=replace(decision,run_context=ctx))
    with sqlite3.connect(store.path) as c:
        assert c.execute('SELECT count(*) FROM official_cohorts').fetchone()==(0,)
        assert c.execute('SELECT confirm_status FROM signal_runs').fetchone()==('not_attempted',)


def test_calendar_provenance_persisted_on_run_and_cohort(tmp_path):
    store,run,decision=prepared_run(tmp_path)
    store.confirm(run,now=CONFIRM,decision=decision)
    with sqlite3.connect(store.path) as c:
        for table in ['signal_runs','official_cohorts']:
            assert c.execute(f'SELECT calendar_version,calendar_check_id FROM {table}').fetchone()==('test-v1','test-v1-check')
        assert c.execute('SELECT expected_market_session FROM signal_runs').fetchone()==('2026-10-13',)
    with pytest.raises(TypeError):
        store.confirm(run,now=CONFIRM,decision=decision,is_session=True)


def test_original_date_evidence_can_be_old_with_fresh_daily_check():
    cal=calendar()
    days={d:replace(row,checked_at=START-timedelta(days=30)) for d,row in cal.days.items()}
    assert decide(cal=replace(cal,days=days)).eligible


@pytest.mark.parametrize('change,reason', [
    ('missing','calendar_notice_check_missing'),
    ('pending','calendar_changes_pending'),
    ('failed','calendar_notice_check_failed'),
    ('version','calendar_notice_version_mismatch'),
    ('late','calendar_check_not_preopen'),
])
def test_daily_notice_check_fails_closed(change,reason):
    cal=calendar();check=cal.notice_check;start=START
    if change=='missing': check=None
    if change=='pending': check=replace(check,status='changes_pending')
    if change=='failed': check=replace(check,status='failed')
    if change=='version': check=replace(check,calendar_version='other')
    if change=='late':
        start=START.replace(hour=9)
        check=replace(check,checked_at=START.replace(minute=50))
    result=decide(start=start,confirmed=start+timedelta(minutes=10),cal=replace(cal,notice_check=check))
    assert reason in result.reasons and not result.eligible


def test_daily_notice_records_are_immutable_and_preserve_date_evidence(tmp_path):
    store=TradingCalendarStore(tmp_path/'calendar.db');store.initialize();cal=calendar()
    store.save_version(cal.version,list(cal.days.values()))
    check=cal.notice_check
    store.save_notice_check(check);store.save_notice_check(check)
    loaded=store.load_version(cal.version,check_id=check.check_id)
    assert loaded.days==cal.days and loaded.notice_check==check
    assert loaded.check_reasons(START)==()
    with pytest.raises(ValueError):
        store.save_notice_check(replace(check,status='failed'))
    with sqlite3.connect(store.path) as c:
        for sql in ["UPDATE calendar_notice_checks SET status='failed'",'DELETE FROM calendar_notice_checks']:
            with pytest.raises(sqlite3.IntegrityError):c.execute(sql)
    with pytest.raises(ValueError):
        store.save_notice_check(replace(check,check_id='unknown',calendar_version='unknown'))
    with pytest.raises(ValueError):
        store.load_version(cal.version,check_id='unknown')
    assert store.load_version(cal.version).days==cal.days
    with pytest.raises(ValueError):
        replace(check,source_refs=('https://krx.co.kr.evil.example/notice',))


def test_context_detects_wrong_declared_expected_session():
    cal=calendar();ctx=replace(RunContext.start(START,cal),expected_market_session=date(2020,1,2))
    result=planned_entry(ctx,CONFIRM,[market()],cal)
    assert 'run_context_session_mismatch' in result.reasons and not result.eligible


@pytest.mark.parametrize('reason', ['rcept_no_mismatch', 'financial_stale', 'filing_deadline_unknown', 'request_timeout'])
def test_dart_financial_failure_blocks_eligibility(reason):
    """DART 재무 입력 실패는 다른 입력이 모두 정상이어도 공식 적격성을 막고, 사유를 그대로 남긴다 (§13.23)."""
    fin = market(name='fin_earnings', definition_id='dart_earnings_proxy_v1', source_status='failed',
                 failure_reason=reason, value=None)
    result = decide([market(), fin])
    assert not result.eligible and ('fin_' + reason) in result.reasons and 'market_unverified' not in result.reasons
    ok = market(name='fin_earnings', definition_id='dart_earnings_proxy_v1', value=None, missing_kind='definition_null')
    assert decide([market(), ok]).eligible                    # 정의상 값 없음(검증됨)은 차단 사유 아님


# ── 달력 구분 (test / official / 미기록) — 공식 적격 판정 ───────────────────────

TEST_REF = 'https://data.krx.co.kr/#iso-test-index-derived-not-official'


def _with_refs(cal, ref):
    days = {d: replace(r, source_ref=ref) for d, r in cal.days.items()}
    return replace(cal, days=days, notice_check=replace(cal.notice_check, source_refs=(ref,)))


@pytest.mark.parametrize('classification', [None, 'test'])
def test_calendar_without_official_classification_is_ineligible(classification):
    result = decide(cal=calendar(classification=classification))
    assert result.reasons == ('calendar_not_official',) and not result.eligible
    assert decide(cal=calendar()).eligible                                 # official 기록이 있을 때만 통과


@pytest.mark.parametrize('cal', [
    lambda: calendar(version='iso-test-2026-10-14-abc'),                   # 이름 표시 (구분 기록이 official이어도)
    lambda: _with_refs(calendar(), TEST_REF),                              # 출처 주소 표시
])
def test_test_markers_block_even_when_marked_official(cal):
    assert 'calendar_not_official' in decide(cal=cal()).reasons


def test_renamed_test_calendar_still_fails_through_stored_classification(tmp_path):
    """시험 달력을 일반 이름·일반 주소로 바꿔도 저장된 구분이 test면 확정 재계산까지 부적격."""
    store = SignalStore(tmp_path / 'cls.db'); store.initialize(); store.register_engine('test', 'hash', 'test-only', START)
    cal = calendar(version='calendar-2026-10', classification='test')     # 표시 없음, 구분만 test
    run, result = stored_run(store, cal)
    loaded = TradingCalendarStore(store.path).load_version(cal.version, check_id=cal.notice_check.check_id)
    assert loaded.classification == 'test' and result.reasons == ('calendar_not_official',)
    key = store.confirm(run, now=CONFIRM, decision=result)                 # confirm()도 같은 판정 함수로 재계산
    with sqlite3.connect(store.path) as c:
        assert c.execute('SELECT eligible, exclusion_reasons FROM official_cohorts WHERE cohort_key=?',
                         (key,)).fetchone() == (0, '["calendar_not_official"]')
    # 판정만 official 달력으로 계산해 넘기면, 저장된 구분(test)으로 재계산한 결과와 달라 거부된다
    cal2 = calendar(version='calendar-2026-10b', classification='test')
    run2, _ = stored_run(store, cal2, universe_rule='u2')
    forged = planned_entry(store.run_context(run2), CONFIRM, store.decision_inputs(run2),
                           replace(cal2, classification='official'))
    assert forged.eligible
    with pytest.raises(ValueError, match='decision_recompute_mismatch'):
        store.confirm(run2, now=CONFIRM, decision=forged)


def test_unclassified_stored_calendar_is_not_official(tmp_path):
    """기존(구분 기록 없는) 달력은 공식으로 간주하지 않는다."""
    store = SignalStore(tmp_path / 'old.db'); store.initialize()
    cal = calendar(version='legacy-v1', classification=None)
    save_calendar(store.path, cal)
    loaded = TradingCalendarStore(store.path).load_version(cal.version)
    assert loaded.classification is None and 'calendar_not_official' in decide(cal=loaded).reasons


def test_classification_rules_and_immutability(tmp_path):
    store = SignalStore(tmp_path / 'rules.db'); store.initialize()
    cs = TradingCalendarStore(store.path)
    for version, ref in (('v-ok', REF), ('iso-test-x', REF), ('v-url', TEST_REF)):
        cal = _with_refs(calendar(version=version, classification=None), ref)
        cs.save_version(version, list(cal.days.values())); cs.save_notice_check(cal.notice_check)
    with pytest.raises(ValueError, match='approval evidence'):
        cs.classify_version('v-ok', 'official', now=START)                  # 승인 근거 없음
    for version in ('iso-test-x', 'v-url'):
        with pytest.raises(ValueError, match='test-marked'):
            cs.classify_version(version, 'official', now=START, approval_ref=APPROVAL)
    with pytest.raises(ValueError, match='unknown calendar version'):
        cs.classify_version('nope', 'test', now=START)
    cs.classify_version('v-ok', 'test', now=START)
    cs.classify_version('v-ok', 'test', now=START + timedelta(minutes=1))   # 같은 기록 반복은 허용
    with pytest.raises(ValueError, match='already fixed'):
        cs.classify_version('v-ok', 'official', now=START, approval_ref=APPROVAL)
    with sqlite3.connect(store.path) as c, pytest.raises(sqlite3.IntegrityError):
        c.execute("UPDATE calendar_classifications SET classification='official'")
