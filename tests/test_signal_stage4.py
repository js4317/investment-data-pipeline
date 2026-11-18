"""Stage 4 store contracts — decision recompute, commit checks, launch evidence, test strategy.
Isolated temporary SQLite only."""
from dataclasses import replace
from datetime import datetime, timedelta
import os
import sqlite3

import pytest

from analyzer.pipeline.entry import MarketInput, RunContext, planned_entry
from analyzer.pipeline.runtime import (LaunchEvidence, ProcessStartUnknown, collect_launch_evidence,
                                       os_process_started_at)
from database.signal_store import KST, STRATEGY, TEST_STRATEGY, SignalStore
from database.trading_calendar import TradingCalendarStore

from test_signal_entry_calendar import START, calendar, market, save_calendar

LABEL = "com.investment.signal"
EARLY = START.replace(minute=47, second=30)      # 확정 마감 08:48:00 이전 (§13.31)


def launch(started=START, **kw):
    return replace(LaunchEvidence(1, LABEL, LABEL, started), **kw)


@pytest.fixture
def store(tmp_path):
    s = SignalStore(tmp_path / "stage4.db"); s.initialize(); s.register_engine("test", "hash", "c", START)
    save_calendar(s.path, calendar())
    return s


def run(s, rows, inputs, links, *, strategy=STRATEGY, trigger="scheduled", universe="u", ev=None):
    ctx = RunContext.start(START, calendar())
    r = s.start_run(signal_date="2026-10-14", strategy_id=strategy, trigger=trigger, version="test",
                    digest="hash", loaded_digest="l", git_commit="c", git_dirty=False, python_executable="p",
                    pid=1, process_started_at=START, now=START, context=ctx, universe_rule=universe,
                    tickers=[x["ticker"] for x in rows],
                    launch=(ev or launch()) if trigger == "scheduled" else ev)
    from tests.evidence_fixture import attach
    status = s.finish_run(r, rows, "hash", START + timedelta(minutes=5), inputs=attach(s, r, inputs),
                          signal_inputs=links)
    return r, status


def row(t, score=95, grade="strong_buy"):
    return dict(ticker=t, processing_status="scored", normalized_score=score, grade=grade,
                confidence="high", executable=True)


def decide(s, r, at=EARLY):
    ctx = s.run_context(r)
    cal = TradingCalendarStore(s.path).load_version(ctx.calendar_version, check_id=ctx.calendar_check_id)
    return planned_entry(ctx, at, s.decision_inputs(r), cal)


def q(s, sql, a=()):
    with sqlite3.connect(s.path) as c:
        return c.execute(sql, a).fetchall()


# ── 판정 재계산 ────────────────────────────────────────────────────────────

def test_recompute_rejects_tampered_decision(store):
    r, _ = run(store, [row("000001")], {"m": market(source_status="unverified")}, {"000001": ["m"]})
    real = decide(store, r)
    assert "market_unverified" in real.reasons
    for forged in (replace(real, reasons=()), replace(real, entry_rule="next_session_open"),
                   replace(real, is_session=False)):
        with pytest.raises(ValueError, match="decision_recompute_mismatch"):
            store.confirm(r, now=EARLY, decision=forged)
    assert q(store, "SELECT count(*) FROM official_cohorts") == [(0,)]
    assert q(store, "SELECT confirm_status FROM signal_runs") == [("not_attempted",)]
    store.confirm(r, now=EARLY, decision=real)
    assert q(store, "SELECT eligible FROM official_cohorts") == [(0,)]


def test_recompute_rejects_decision_from_unstored_inputs(store):
    r, _ = run(store, [row("000001")], {"m": market(source_status="unverified")}, {"000001": ["m"]})
    ctx = store.run_context(r)
    outside = planned_entry(ctx, EARLY, [market()], calendar())          # 저장되지 않은 검증 입력
    with pytest.raises(ValueError, match="decision_recompute_mismatch"):
        store.confirm(r, now=EARLY, decision=outside)


def test_decision_inputs_are_selected_members_union(store):
    rows = [row("000001", 95), row("000002", 50, grade="buy")]           # 000002는 선정 대상 아님
    inputs = {"m-ok": market(), "m-bad": market(source_status="unverified")}
    r, _ = run(store, rows, inputs, {"000001": ["m-ok"], "000002": ["m-bad"]})
    assert [i.source_status for i in store.decision_inputs(r)] == ["verified"]
    store.confirm(r, now=EARLY, decision=decide(store, r))
    assert q(store, "SELECT eligible, oos_type FROM official_cohorts") == [(1, "primary")]


def test_selected_member_unverified_input_makes_ineligible(store):
    r, _ = run(store, [row("000001")], {"m-ok": market(), "m-bad": market(source_status="unverified")},
               {"000001": ["m-ok", "m-bad"]})
    store.confirm(r, now=EARLY, decision=decide(store, r))
    assert q(store, "SELECT eligible FROM official_cohorts") == [(0,)]


def test_inputs_must_be_linked_and_known(store):
    _, status = run(store, [row("000001")], {"m": market()}, {}, universe="a")
    assert status == "incomplete"
    _, status = run(store, [row("000001")], {"m": market()}, {"000001": ["nope"]}, universe="b")
    assert status == "incomplete"
    assert q(store, "SELECT abort_reason FROM signal_runs ORDER BY completion_order") == [("inputs_unlinked",), ("inputs_invalid",)]


def test_run_inputs_immutable_and_final(store):
    r, _ = run(store, [row("000001")], {"m": market()}, {"000001": ["m"]})
    for sql in ("UPDATE run_inputs SET source_status='verified'", "DELETE FROM run_signal_inputs"):
        with sqlite3.connect(store.path) as c, pytest.raises(sqlite3.DatabaseError):
            c.execute(sql)
    with sqlite3.connect(store.path) as c, pytest.raises(sqlite3.DatabaseError):
        c.execute("INSERT INTO run_inputs(run_id,input_id,input_class,name,source_status) VALUES(?,?,?,?,?)",
                  (r, "late", "market", "x", "verified"))


# ── 마감 여유와 COMMIT 사후 확인 ───────────────────────────────────────────

def test_deadline_margin_microsecond_boundary(store):
    for i, (at, expected) in enumerate([(START.replace(minute=47, second=59, microsecond=999999), "primary"),
                                        (START.replace(minute=48), "catchup")]):
        r, _ = run(store, [row("000001")], {"m": market()}, {"000001": ["m"]}, universe=f"d{i}")
        key = store.confirm(r, now=at, decision=decide(store, r, at))
        assert q(store, "SELECT oos_type FROM official_cohorts WHERE cohort_key=?", (key,)) == [(expected,)]


def _confirm_with_clock(store, clock, universe):
    r, _ = run(store, [row("000001")], {"m": market()}, {"000001": ["m"]}, universe=universe)
    store._clock = clock
    return store.confirm(r, now=EARLY, decision=decide(store, r))


def test_commit_check_verified_and_evaluation_query(store):
    key = _confirm_with_clock(store, lambda: EARLY + timedelta(seconds=1), "ok")
    assert q(store, "SELECT status FROM commit_checks WHERE cohort_key=?", (key,)) == [("verified_before_cutoff",)]
    engine = q(store, "SELECT engine_id FROM official_engines")[0][0]
    assert [c["cohort_key"] for c in store.official_primary_cohorts(engine, "ok")] == [key]
    for sql in ("DELETE FROM commit_checks", "UPDATE commit_checks SET status='after_cutoff'"):
        with sqlite3.connect(store.path) as c, pytest.raises(sqlite3.DatabaseError):
            c.execute(sql)


@pytest.mark.parametrize("clock,status", [
    (lambda: START.replace(minute=48), "after_cutoff"),                   # 확정 마감 08:48:00 = 사후 확인 마감
    (lambda: EARLY - timedelta(seconds=1), "check_failed"),
    (lambda: (_ for _ in ()).throw(OSError("clock")), "check_failed"),
])
def test_commit_check_failures_excluded(store, clock, status):
    key = _confirm_with_clock(store, clock, "x")
    assert q(store, "SELECT status FROM commit_checks") == [(status,)]
    assert q(store, "SELECT oos_type, eligible FROM official_cohorts") == [("primary", 1)]
    engine = q(store, "SELECT engine_id FROM official_engines")[0][0]
    assert store.official_primary_cohorts(engine, "x") == []


def test_process_death_after_commit_has_no_check_and_is_excluded(store, monkeypatch):
    def die(*a, **k):
        raise KeyboardInterrupt("killed after commit")
    monkeypatch.setattr(store, "_record_commit_check", die)
    r, _ = run(store, [row("000001")], {"m": market()}, {"000001": ["m"]}, universe="dead")
    with pytest.raises(KeyboardInterrupt):
        store.confirm(r, now=EARLY, decision=decide(store, r))
    assert q(store, "SELECT oos_type, eligible FROM official_cohorts") == [("primary", 1)]
    assert q(store, "SELECT confirm_status FROM signal_runs") == [("confirmed",)]   # 확정 자체는 유지
    assert q(store, "SELECT count(*) FROM commit_checks") == [(0,)]
    engine = q(store, "SELECT engine_id FROM official_engines")[0][0]
    assert store.official_primary_cohorts(engine, "dead") == []


# ── 예정 실행 근거 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("ev,match", [
    (dict(parent_pid=4242), "scheduled_evidence"),
    (dict(service_label="other"), "scheduled_evidence"),
    (dict(service_label=None), "scheduled_evidence"),
    (dict(os_process_started_at=START - timedelta(seconds=61)), "scheduled_evidence"),
    (dict(os_process_started_at=START - timedelta(seconds=5)), "OS evidence"),
])
def test_scheduled_requires_launch_evidence(store, ev, match):
    with pytest.raises(ValueError, match=match):
        run(store, [row("000001")], {"m": market()}, {"000001": ["m"]}, ev=launch(**ev))


def test_manual_cli_stores_evidence_and_is_catchup(store):
    ev = launch(parent_pid=4242, service_label=None)
    r, _ = run(store, [row("000001")], {"m": market()}, {"000001": ["m"]}, trigger="manual_cli", ev=ev)
    assert q(store, "SELECT launch_parent_pid, launch_service_label, launch_expected_label FROM signal_runs") == [(4242, None, LABEL)]
    store.confirm(r, now=EARLY, decision=decide(store, r))
    assert q(store, "SELECT oos_type FROM official_cohorts") == [("catchup",)]
    with sqlite3.connect(store.path) as c, pytest.raises(sqlite3.DatabaseError):
        c.execute("UPDATE signal_runs SET launch_parent_pid=1")


def test_os_process_start_time_and_failure():
    started = os_process_started_at(os.getpid())
    assert started.tzinfo is not None and started <= datetime.now(KST) + timedelta(seconds=1)
    with pytest.raises(ProcessStartUnknown):
        os_process_started_at(2 ** 22 + 12345)
    ev = collect_launch_evidence(LABEL)
    assert ev.parent_pid == os.getppid()


# ── 검사 전용 전략 ─────────────────────────────────────────────────────────

def test_test_strategy_never_occupies_official_key(store):
    store._clock = lambda: EARLY + timedelta(seconds=1)
    t, _ = run(store, [row("000001")], {"m": market()}, {"000001": ["m"]}, strategy=TEST_STRATEGY)
    tkey = store.confirm(t, now=EARLY, decision=decide(store, t))
    o, _ = run(store, [row("000001")], {"m": market()}, {"000001": ["m"]})
    okey = store.confirm(o, now=EARLY, decision=decide(store, o))
    assert tkey != okey
    assert q(store, "SELECT strategy_id, oos_type FROM official_cohorts ORDER BY strategy_id") == [
        (STRATEGY, "primary"), (TEST_STRATEGY, "primary")]
    engine = q(store, "SELECT engine_id FROM official_engines")[0][0]
    assert [c["cohort_key"] for c in store.official_primary_cohorts(engine, "u")] == [okey]
