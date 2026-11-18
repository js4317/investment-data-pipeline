from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import ModuleType

import pytest

from analyzer.pipeline.runtime import (RunBusy, UnhashedModule, loaded_modules_hash,
                                       run_lock, source_hash, source_manifest)
from database.signal_store import CUTOFF, KST, STRATEGY, SignalStore

NOW = datetime(2026,10,14,8,47,tzinfo=KST)
CONFIRM = NOW + timedelta(seconds=30)   # 08:47:30 — 확정 마감(08:48:00) 이전 (§13.31)


@pytest.fixture
def store(tmp_path):
    s=SignalStore(tmp_path/'isolated.db');s.initialize()
    s.register_engine('8.6.7','hash-A','commit-A',NOW)
    return s


def start(s,tickers=None,**kw):
    from analyzer.pipeline.entry import RunContext
    from analyzer.pipeline.runtime import LaunchEvidence
    from database.trading_calendar import CalendarSnapshot, CalendarDay, CalendarNoticeCheck, TradingCalendarStore
    ref='https://krx.co.kr/test-fixture'
    days={ (NOW+timedelta(days=i)).date():CalendarDay((NOW+timedelta(days=i)).date(),'session','KRX_EQUITY',ref,NOW-timedelta(days=1)) for i in (-1,0,1)}
    cal=CalendarSnapshot('foundation-test',days,CalendarNoticeCheck('daily-test','foundation-test',NOW-timedelta(minutes=1),'clear',(ref,)))
    cs=TradingCalendarStore(s.path);cs.save_version(cal.version,list(cal.days.values()));cs.save_notice_check(cal.notice_check)
    args=dict(signal_date='2026-10-14',strategy_id=STRATEGY,trigger='scheduled',version='8.6.7',
              digest='hash-A',loaded_digest='loaded-A',git_commit='commit-A',git_dirty=False,
              python_executable=sys.executable,pid=1,process_started_at=NOW,now=NOW,
              context=RunContext.start(NOW,cal),universe_rule='WATCHLIST_v1',
              tickers=tickers or [f'{i:06}' for i in range(20)])
    args.update(kw)
    if args['trigger']=='scheduled' and 'launch' not in kw:
        args['launch']=LaunchEvidence(1,'com.investment.signal','com.investment.signal',args['process_started_at'])
    return s.start_run(**args)


def signals(tickers):
    return [dict(ticker=t,processing_status='scored',normalized_score=95,grade='strong_buy',
                 confidence='high',executable=True,components={},inputs=[]) for t in tickers]


def finish(s,r,rows,now=NOW,input_status='verified',digest='hash-A'):
    """채점 입력을 함께 기록한다: 실행 기준 세션의 시장자료 1건을 모든 종목이 사용."""
    from analyzer.pipeline.entry import MarketInput
    ctx=s.run_context(r)
    item=MarketInput('krx_daily',NOW+timedelta(seconds=10),ctx.expected_market_session,input_status)
    from tests.evidence_fixture import attach
    return s.finish_run(r,rows,digest,now,inputs=attach(s,r,{'m-krx':item}),signal_inputs={x['ticker']:['m-krx'] for x in rows})


def complete(s,rows=None,input_status='verified',**kw):
    tickers=[r['ticker'] for r in rows] if rows is not None else None
    r=start(s,tickers,**kw)
    finish(s,r,rows if rows is not None else signals([f'{i:06}' for i in range(20)]),input_status=input_status)
    return r


def confirm(s,r,at=None,**kw):
    """저장된 입력·달력으로 판정을 계산해 확정 (실행기와 같은 경로)."""
    from analyzer.pipeline.entry import planned_entry
    from database.trading_calendar import TradingCalendarStore
    at=at or CONFIRM
    ctx=s.run_context(r)
    cal=TradingCalendarStore(s.path).load_version(ctx.calendar_version,check_id=ctx.calendar_check_id)
    decision=planned_entry(ctx,at,s.decision_inputs(r),cal)
    return s.confirm(r,now=at,decision=decision,**kw)


def query(s,sql,args=()):
    with sqlite3.connect(s.path) as c:return c.execute(sql,args).fetchall()


def test_T7_cohort_is_unchanged_after_new_top10(store):
    first=complete(store);key=confirm(store,first)
    before=query(store,'SELECT * FROM official_cohorts')+query(store,'SELECT * FROM cohort_members')
    rows=signals([f'{i:06}' for i in range(20)])
    for i,row in enumerate(rows):row['normalized_score']=80+i
    second=complete(store,rows);assert confirm(store,second)==key
    assert before==query(store,'SELECT * FROM official_cohorts')+query(store,'SELECT * FROM cohort_members')
    assert query(store,'SELECT confirm_status FROM signal_runs WHERE run_id=?',(second,))==[('skipped_existing',)]


def test_T8_deterministic_ties(store):
    rows=signals([f'{i:06}' for i in reversed(range(20))])
    confirm(store,complete(store,rows))
    assert query(store,'SELECT ticker FROM cohort_members ORDER BY rank')==[(f'{i:06}',) for i in range(10)]
    assert query(store,'SELECT n_tied_at_cutoff FROM official_cohorts')==[(20,)]


def test_T9_only_six_executable_strong_buys(store):
    rows=signals([f'{i:06}' for i in range(20)])
    for row in rows[6:]:row['grade']='buy'
    rows[6]['grade']='strong_buy';rows[6]['executable']=False
    confirm(store,complete(store,rows))
    assert query(store,'SELECT n_selected FROM official_cohorts')==[(6,)]


def test_T10_past_and_manual_are_catchup(store):
    r=complete(store,signal_date='2026-10-13');confirm(store,r)
    assert query(store,'SELECT oos_type FROM official_cohorts')==[('catchup',)]
    r=complete(store,trigger='manual_cli')
    with pytest.raises(ValueError,match='primary'):confirm(store,r,requested_type='primary')
    confirm(store,r);assert query(store,"SELECT oos_type FROM official_cohorts WHERE signal_date='2026-10-14'")==[('catchup',)]


def test_T11_processing_failure_no_cohort(store):
    rows=signals([f'{i:06}' for i in range(20)]);rows[0]['processing_status']='failed'
    r=complete(store,rows)
    assert query(store,'SELECT status FROM signal_runs WHERE run_id=?',(r,))==[('incomplete',)]
    with pytest.raises(ValueError):confirm(store,r)
    assert query(store,'SELECT count(*) FROM official_cohorts')==[(0,)]


@pytest.mark.parametrize('missing,status',[(1,'completed'),(2,'incomplete')])
def test_T12_exact_five_percent_boundary(store,missing,status):
    rows=signals([f'{i:06}' for i in range(20)])
    for row in rows[:missing]:row.update(processing_status='excluded_required_missing',confidence='low',executable=False)
    r=complete(store,rows)
    assert query(store,'SELECT status FROM signal_runs WHERE run_id=?',(r,))==[(status,)]


def test_T13_killed_run_recovered_under_lock(store,tmp_path):
    # Separate process persists a running record then exits without finishing.
    source=Path(__file__).read_text();helpers=source[source.index('def start('):source.index('def signals(')]
    code=f"from datetime import datetime, timedelta\nimport sys,os\nfrom database.signal_store import SignalStore,KST,STRATEGY\nNOW=datetime(2026,10,14,8,47,tzinfo=KST)\n{helpers}\ns=SignalStore({str(store.path)!r});start(s);os._exit(1)\n"
    child=subprocess.run([sys.executable,'-B','-c',code],timeout=10)
    assert child.returncode==1
    with run_lock(tmp_path/'run.lock'):
        assert store.recover_orphans(NOW,lock_acquired=True)==1
        confirm(store,complete(store))
    assert query(store,"SELECT abort_reason FROM signal_runs WHERE status='failed'")==[('orphaned',)]
    assert query(store,'SELECT oos_type FROM official_cohorts')==[('primary',)]


def test_T14_transaction_failure_then_catchup(store):
    first=complete(store)
    with sqlite3.connect(store.path) as c:
        c.execute("CREATE TRIGGER fail_confirm BEFORE INSERT ON cohort_members WHEN NEW.rank=2 BEGIN SELECT RAISE(ABORT,'injected failure'); END")
    with pytest.raises(sqlite3.IntegrityError,match='injected'):confirm(store,first)
    assert query(store,'SELECT count(*) FROM official_cohorts')==[(0,)]
    assert query(store,'SELECT count(*) FROM cohort_members')==[(0,)]
    assert query(store,'SELECT confirm_status FROM signal_runs WHERE run_id=?',(first,))==[('failed',)]
    with sqlite3.connect(store.path) as c:c.execute('DROP TRIGGER fail_confirm')
    confirm(store,complete(store))
    assert query(store,'SELECT oos_type FROM official_cohorts')==[('catchup',)]


def test_T14b_incomplete_retry_before_cutoff_primary(store):
    rows=signals([f'{i:06}' for i in range(20)]);rows[0]['processing_status']='failed'
    complete(store,rows);confirm(store,complete(store))
    assert query(store,'SELECT oos_type FROM official_cohorts')==[('primary',)]


def test_T15_real_process_lock_contention(store,tmp_path):
    lock=tmp_path/'run.lock'
    code=f"import time\nfrom pathlib import Path\nfrom analyzer.pipeline.runtime import run_lock\nwith run_lock(Path({str(lock)!r})):\n print('locked',flush=True)\n time.sleep(5)\n"
    p=subprocess.Popen([sys.executable,'-B','-c',code],stdout=subprocess.PIPE,text=True)
    try:
        assert p.stdout.readline().strip()=='locked'
        with pytest.raises(RunBusy):
            with run_lock(lock):pass
        r=start(store,lock_acquired=False)
        assert query(store,'SELECT status,abort_reason FROM signal_runs WHERE run_id=?',(r,))==[('aborted','lock_busy')]
        with pytest.raises(ValueError):store.recover_orphans(NOW,lock_acquired=False)
    finally:p.terminate();p.wait(timeout=10)
    with run_lock(lock):pass


def test_T16_same_version_different_hash_aborts(store):
    r=start(store,digest='modified')
    assert query(store,'SELECT status,abort_reason FROM signal_runs WHERE run_id=?',(r,))==[('aborted','engine_unregistered')]
    with pytest.raises(ValueError):confirm(store,r)


def test_T17_mid_run_hash_change_aborts(store):
    r=start(store)
    assert store.finish_run(r,signals([f'{i:06}' for i in range(20)]),'modified',NOW)=='aborted'
    with pytest.raises(ValueError):confirm(store,r)


def test_T18_stale_process_aborts(store):
    with pytest.raises(ValueError,match='scheduled_evidence'):
        start(store,process_started_at=NOW-timedelta(seconds=61))    # 예정 실행 근거 미충족
    r=start(store,process_started_at=NOW-timedelta(seconds=61),trigger='manual_cli')
    assert query(store,'SELECT status,abort_reason FROM signal_runs WHERE run_id=?',(r,))==[('aborted','stale_process')]


def test_atomic_result_write_rollback(store):
    r=start(store);rows=signals([f'{i:06}' for i in range(20)]);rows[-1]['processing_status']='invalid'
    with pytest.raises(sqlite3.IntegrityError):store.finish_run(r,rows,'hash-A',NOW)
    assert query(store,'SELECT count(*) FROM run_signals')==[(0,)]
    assert query(store,'SELECT status FROM signal_runs WHERE run_id=?',(r,))==[('running',)]


def test_universe_exact_membership_and_ui_exclusion(store):
    r=start(store);rows=signals([f'{i:06}' for i in range(20)]);rows[-1]['ticker']='outside'
    assert store.finish_run(r,rows,'hash-A',NOW)=='incomplete'
    assert query(store,'SELECT count(*) FROM run_signals')==[(0,)]
    r=complete(store,trigger='ui')
    with pytest.raises(ValueError):confirm(store,r)


def test_immutability_and_legacy_history(store):
    r=complete(store);key=confirm(store,r)
    for sql in ["UPDATE run_signals SET grade='buy'",'DELETE FROM cohort_members',
                "UPDATE official_cohorts SET oos_type='catchup'","UPDATE signal_runs SET status='running'",
                "UPDATE signal_runs SET engine_version='new'",'DELETE FROM official_engines']:
        with sqlite3.connect(store.path) as c:
            with pytest.raises(sqlite3.IntegrityError):c.execute(sql)
    for eligible in [False,True]:
        store.append_legacy_decision(source='oos_signals',signal_date='2026-09-30',ticker='000001',eligible=eligible,
            reasons=['evidence'],now=NOW,evidence_ref='test-only')
    assert query(store,'SELECT eligible FROM legacy_eligibility ORDER BY decision_id')==[(0,),(1,)]


def test_hash_coverage_and_loaded_files(tmp_path):
    for name in ['analyzer/a.py','database/a.py','constants.py','config.py','run.py','morning_run.py',
                 'backtests/snapshot.py','backtests/market_db.py','scripts/official.py']:
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('pass\n')
    original=source_hash(tmp_path,['scripts/official.py'])
    (tmp_path/'database/a.py').write_text('x=1\n')
    assert source_hash(tmp_path,['scripts/official.py'])!=original
    m=ModuleType('test');m.__file__=str(tmp_path/'constants.py')
    assert loaded_modules_hash(tmp_path,[m])


def test_zero_candidates_and_source_exclusion(store):
    rows=signals([f'{i:06}' for i in range(20)])
    for row in rows:row['grade']='neutral'
    confirm(store,complete(store,rows,input_status='unverified'))
    assert query(store,'SELECT n_selected,eligible FROM official_cohorts')==[(0,0)]


def test_project_membership_uses_root_path_not_module_name(tmp_path):
    local=tmp_path/'anything.py';local.write_text('pass\n')
    m=ModuleType('report');m.__file__=str(local)
    external=ModuleType('analyzer_like_library');external.__file__=str(tmp_path.parent/'outside.py')
    assert loaded_modules_hash(tmp_path,[m,external])==loaded_modules_hash(tmp_path,[m])


def test_terminal_run_audit_columns_cannot_be_rewritten(store):
    r=complete(store)
    mutations={'finished_at':'2026-10-14T07:00:00+09:00','source_hash_end':'forged',
               'abort_reason':'forged','completion_order':999,'cohort_confirmed_at':'forged'}
    for name,value in mutations.items():
        with sqlite3.connect(store.path) as c:
            with pytest.raises(sqlite3.IntegrityError):
                c.execute(f'UPDATE signal_runs SET {name}=? WHERE run_id=?',(value,r))
    confirm(store,r)  # legitimate not_attempted -> confirmed update still succeeds
    before=query(store,'SELECT finished_at,source_hash_end,abort_reason,completion_order,cohort_confirmed_at FROM signal_runs WHERE run_id=?',(r,))
    for name,value in mutations.items():
        with sqlite3.connect(store.path) as c:
            with pytest.raises(sqlite3.IntegrityError):
                c.execute(f'UPDATE signal_runs SET {name}=? WHERE run_id=?',(value,r))
    assert query(store,'SELECT finished_at,source_hash_end,abort_reason,completion_order,cohort_confirmed_at FROM signal_runs WHERE run_id=?',(r,))==before


def test_hash_includes_report_diff_and_unhashed_module_aborts(store,tmp_path,monkeypatch):
    root=tmp_path/'project'
    names=['analyzer/a.py','database/a.py','constants.py','config.py','run.py','morning_run.py',
           'backtests/snapshot.py','backtests/market_db.py','report.py','backtests/diff.py']
    for name in names:
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('pass\n')
    modules=[]
    for name in ['report.py','backtests/diff.py']:
        m=ModuleType(name.removesuffix('.py').replace('/','.'));m.__file__=str(root/name);modules.append(m)
    original=source_hash(root,[],modules)
    for name in ['report.py','backtests/diff.py']:
        (root/name).write_text('changed=1\n')
        assert source_hash(root,[],modules)!=original
        (root/name).write_text('pass\n')
    manifest=source_manifest(root,[],modules)
    late_file=root/'late_plugin.py';late_file.write_text('pass\n')
    late=ModuleType('unlisted_top_level');late.__file__=str(late_file)
    with pytest.raises(UnhashedModule,match='unhashed_module'):
        manifest.verify_loaded(root,[*modules,late])
    store.register_engine('8.6.7',manifest.digest,'test-only',NOW)
    r=start(store,digest=manifest.digest,manifest=manifest,source_root=root,loaded_modules=tuple([*modules,late]))
    assert query(store,'SELECT status,abort_reason FROM signal_runs WHERE run_id=?',(r,))==[('aborted','unhashed_module')]
    monkeypatch.setitem(sys.modules,late.__name__,late)
    r=start(store,digest=manifest.digest,manifest=manifest,source_root=root)
    assert query(store,'SELECT status,abort_reason FROM signal_runs WHERE run_id=?',(r,))==[('aborted','unhashed_module')]


def test_primary_depends_on_completion_order_not_later_runs(store):
    a=complete(store)
    b=start(store)
    finish(store,b,signals([f'{i:06}' for i in range(20)]),NOW+timedelta(seconds=1))
    confirm(store,a)
    assert query(store,'SELECT oos_type FROM official_cohorts WHERE source_run_id=?',(a,))==[('primary',)]
    # Equal wall-clock timestamps use serialized completion order, not run start order.
    a=start(store,universe_rule='tie_v1');b=start(store,universe_rule='tie_v1')
    finish(store,b,signals([f'{i:06}' for i in range(20)]))
    finish(store,a,signals([f'{i:06}' for i in range(20)]))
    confirm(store,b)
    assert query(store,'SELECT oos_type FROM official_cohorts WHERE source_run_id=?',(b,))==[('primary',)]


def test_existing_records_preserved(store):
    with sqlite3.connect(store.path) as c:
        c.execute('CREATE TABLE oos_signals(ticker,grade)');c.execute("INSERT INTO oos_signals VALUES('000001','buy')")
    store.initialize();confirm(store,complete(store))
    assert query(store,'SELECT * FROM oos_signals')==[('000001','buy')]


def test_production_path_rejected_without_creating_file():
    import database.signal_store as module
    with pytest.raises(ValueError,match='production'):
        SignalStore(Path(module.__file__).resolve().parent/'research.db')
