"""Stage 4 orchestration with injected dependencies (no network, temporary DB)."""
from datetime import date, datetime, timedelta
import os
import sqlite3
from types import ModuleType, SimpleNamespace

import pytest

from analyzer.pipeline import official as OF
from analyzer.pipeline.runtime import LaunchEvidence, ProcessStartUnknown, SourceManifest, run_lock
from constants import Confidence, Grade
from database.signal_store import KST, STRATEGY, TEST_STRATEGY, SignalStore
from database.trading_calendar import TradingCalendarStore

from test_signal_entry_calendar import START, calendar
from test_signal_prices_outcomes import index_rows, stock_rows

LABEL = OF.OFFICIAL_LABEL


def clock_from(t0):
    state = {"t": t0}
    def tick():
        state["t"] += timedelta(seconds=2)
        return state["t"]
    return tick


def result(t, score=95, conf=Confidence.HIGH, grade=Grade.STRONG_BUY, executable=True):
    comp = SimpleNamespace(name="valuation", score=40, max_score=50)
    return SimpleNamespace(scoring=SimpleNamespace(ticker=t, confidence=conf, normalized_score=score, grade=grade,
                                                   components=(comp,), supply_score=0, notes=()),
                           decision=SimpleNamespace(executable=executable))


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "official.db"
    src = tmp_path / "src"; src.mkdir(); (src / "m.py").write_text("x=1\n")
    mod = ModuleType("m"); mod.__file__ = str(src / "m.py")
    store = SignalStore(db); store.initialize()
    cal = calendar()
    cs = TradingCalendarStore(db); cs.save_version(cal.version, list(cal.days.values())); cs.save_notice_check(cal.notice_check)
    store.register_engine(OF._engine_version(), "digest-A", "c", START)
    return SimpleNamespace(db=db, src=src, mod=mod, store=store)


def deps(env, *, analyze=None, launch=None, manifests=None, kis=False, tickers=("000001", "000002")):
    seq = iter(manifests or [])
    def manifest():
        d = next(seq, "digest-A")
        return SourceManifest(d, ("m.py",))
    return OF.SignalDeps(
        collect_launch=launch or (lambda label: LaunchEvidence(1, label, label, START)),
        manifest=manifest, universe=lambda: list(tickers), market_state=lambda d: None,
        analyze=analyze or (lambda t, d, ms: result(t, 95 - int(t[-1]))),
        clock=clock_from(START), kis_active=lambda: kis,
        loaded_modules=lambda: (env.mod,), git_info=lambda root: ("commit", False))


def q(db, sql, a=()):
    with sqlite3.connect(db) as c:
        return c.execute(sql, a).fetchall()


def test_end_to_end_test_strategy_confirmed_ineligible(env):
    out = OF.run_signal(env.db, deps(env), strategy=TEST_STRATEGY, source_root=env.src)
    assert out["outcome"] == "confirmed" and out["trigger"] == "scheduled"
    assert out["status"] == "completed" and out["n_scored"] == 2 and out["supply_nonzero"] == 0
    assert out["eligible"] == 0 and out["commit_check"] is not None
    assert "market_unverified" in out["exclusion_reasons"] and "fin_unverified" in out["exclusion_reasons"]
    assert q(env.db, "SELECT strategy_id FROM official_cohorts") == [(TEST_STRATEGY,)]
    assert q(env.db, "SELECT count(*) FROM run_signal_inputs") == [(4,)]
    assert q(env.db, "SELECT launch_parent_pid, launch_service_label FROM signal_runs") == [(1, LABEL)]


def test_launch_conditions_unmet_is_manual_cli_catchup(env):
    out = OF.run_signal(env.db, deps(env, launch=lambda l: LaunchEvidence(4242, None, l, START)),
                        strategy=TEST_STRATEGY, source_root=env.src)
    assert out["trigger"] == "manual_cli" and out["oos_type"] == "catchup"
    assert set(out["launch_failures"]) == {"parent_not_launchd", "service_label_mismatch"}


def test_process_start_unknown_aborts_without_run(env):
    def boom(label):
        raise ProcessStartUnknown("ps failed")
    out = OF.run_signal(env.db, deps(env, launch=boom), strategy=TEST_STRATEGY, source_root=env.src)
    assert out["outcome"] == "aborted_no_process_start"
    assert q(env.db, "SELECT count(*) FROM signal_runs") == [(0,)]


def test_kis_active_aborts(env):
    assert OF.run_signal(env.db, deps(env, kis=True), strategy=TEST_STRATEGY, source_root=env.src)["outcome"] == "aborted_kis_active"


def test_disable_kis_removes_provider(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "kis")
    OF.disable_kis()
    assert "MARKET_DATA_PROVIDER" not in os.environ
    from analyzer.data import krx
    assert krx._get_kis() is None


def test_processing_failure_incomplete_no_cohort(env):
    def analyze(t, d, ms):
        if t == "000002":
            raise TimeoutError("fetch")
        return result(t)
    out = OF.run_signal(env.db, deps(env, analyze=analyze), strategy=TEST_STRATEGY, source_root=env.src)
    assert out["outcome"] == "run_not_completed" and out["status"] == "incomplete" and out["n_failed"] == 1
    assert q(env.db, "SELECT count(*) FROM official_cohorts") == [(0,)]


def test_source_change_during_run_aborts(env):
    out = OF.run_signal(env.db, deps(env, manifests=["digest-A", "digest-B"]), strategy=TEST_STRATEGY, source_root=env.src)
    assert out["status"] == "aborted"
    assert q(env.db, "SELECT abort_reason FROM signal_runs") == [("source_hash_changed",)]


def test_unregistered_engine_aborts(env):
    out = OF.run_signal(env.db, deps(env, manifests=["digest-X"]), strategy=TEST_STRATEGY, source_root=env.src)
    assert out["outcome"] == "run_aborted" and out["abort_reason"] == "engine_unregistered"


def test_lock_busy(env):
    with run_lock(env.db.with_name(".signal_run.lock")):
        import subprocess, sys, json, textwrap
        code = textwrap.dedent(f"""
            import sys; sys.path.insert(0, {str(os.getcwd())!r})
            from analyzer.pipeline.runtime import run_lock, RunBusy
            from pathlib import Path
            try:
                with run_lock(Path({str(env.db.with_name('.signal_run.lock'))!r})): print('acquired')
            except RunBusy: print('busy')
        """)
        assert subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True).stdout.strip() == "busy"


def test_no_calendar_aborts(tmp_path, env):
    db = tmp_path / "empty.db"
    out = OF.run_signal(db, deps(env), strategy=TEST_STRATEGY, source_root=env.src)
    assert out["outcome"] == "aborted_no_calendar"


def test_official_and_test_strategies_coexist(env):
    a = OF.run_signal(env.db, deps(env), strategy=TEST_STRATEGY, source_root=env.src)
    b = OF.run_signal(env.db, deps(env), strategy=STRATEGY, source_root=env.src)
    assert a["cohort_key"] != b["cohort_key"] and b["outcome"] == "confirmed"


def test_collect_prices_pending_only_and_fill(env):
    from database.price_store import OfficialPriceStore
    def fetch(market, d):
        return index_rows(d) if market == "KOSPI_INDEX" else stock_rows(d, {"000001": ("100", "101")})
    evening = datetime(2026, 10, 14, 18, 40, tzinfo=KST)
    out = OF.collect_prices(env.db, fetch, clock=lambda: evening, start=date(2026, 10, 13))
    assert out["outcome"] == "done"
    assert sorted({s for _, _, s in out["collections"]}) == ["complete"]
    assert len(out["collections"]) == 6                     # 10/13, 10/14 × 3시장
    again = OF.collect_prices(env.db, fetch, clock=lambda: evening, start=date(2026, 10, 13))
    assert again["collections"] == []                       # 완료분은 다시 수집하지 않음
    morning = datetime(2026, 10, 15, 8, 0, tzinfo=KST)
    late = OF.collect_prices(env.db, fetch, clock=lambda: morning, start=date(2026, 10, 13))
    assert late["range"][1] == "2026-10-14"                 # 18:30 전에는 전날까지만


def test_empty_universe_aborts_without_run(env):
    out = OF.run_signal(env.db, deps(env, tickers=()), strategy=TEST_STRATEGY, source_root=env.src)
    assert out["outcome"] == "aborted_empty_universe"
    assert q(env.db, "SELECT count(*) FROM signal_runs") == [(0,)]


def test_scoring_uses_expected_market_session_not_run_date(env):
    seen = []
    def analyze(t, d, ms):
        seen.append(d); return result(t)
    out = OF.run_signal(env.db, deps(env, analyze=analyze), strategy=TEST_STRATEGY, source_root=env.src)
    assert set(seen) == {date(2026, 10, 13)} and out["as_of"] == "2026-10-13"   # 실행일 10/14가 아님


def test_git_queries_do_not_rewrite_index(tmp_path):
    """git status는 기본적으로 .git/index의 stat 정보를 갱신할 수 있다 → 조회는 GIT_OPTIONAL_LOCKS=0으로만 (보호 파일 불변)."""
    import hashlib, shutil, subprocess, time
    if shutil.which("git") is None:
        pytest.skip("git 없음")
    repo = tmp_path / "repo"; repo.mkdir()
    g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True)
    g("init", "-q"); g("config", "user.email", "t@example.com"); g("config", "user.name", "t")
    (repo / "a.py").write_text("x = 1\n"); g("add", "a.py"); g("commit", "-q", "-m", "init")
    time.sleep(1.1); os.utime(repo / "a.py")                  # 내용 같고 mtime만 변경 → 일반 status는 index를 다시 씀
    index = repo / ".git" / "index"
    before = (hashlib.sha256(index.read_bytes()).hexdigest(), index.stat().st_mtime_ns)
    assert OF._git_dirty(repo) is False and OF._git_commit(repo) != "unknown"
    assert (hashlib.sha256(index.read_bytes()).hexdigest(), index.stat().st_mtime_ns) == before
    assert OF.GIT_ENV == {"GIT_OPTIONAL_LOCKS": "0"}
