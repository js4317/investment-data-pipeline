"""Stage 4 orchestration: official signal run and post-close price collection.

Dependencies (scoring, universe, fetchers, clock, launch evidence, source manifest) are
injected so the whole path can be exercised in isolation. Thin CLIs live in scripts/.

Stage-4 data reality: scoring inputs do not carry provable source times yet, so every
input is recorded `unverified` and cohorts are confirmed with eligible=0. KIS is disabled
for official runs; there is no automatic fallback to another source.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import logging
import os
import time
from pathlib import Path
from typing import Callable, Iterable

from analyzer.pipeline.entry import FinancialInput, MarketInput, RunContext, planned_entry
from analyzer.pipeline.runtime import (LaunchEvidence, ProcessStartUnknown, RunBusy, SourceManifest,
                                       UnhashedModule, loaded_modules_hash, run_lock)

log = logging.getLogger(__name__)

OFFICIAL_LABEL = "com.investment.signal"
UNIVERSE_RULE = "WATCHLIST_ACTIVE_REVIEW_v1"


def disable_kis() -> None:
    """공식 실행: KIS 현재가 경로 차단. 다른 원천으로 자동 대체하지 않는다."""
    os.environ.pop("MARKET_DATA_PROVIDER", None)
    os.environ["INVESTMENT_OFFICIAL_RUN"] = "1"


def _now_kst() -> datetime:
    from database.signal_store import KST
    return datetime.now(KST)


@dataclass
class SignalDeps:
    collect_launch: Callable[[str], LaunchEvidence]
    manifest: Callable[[], SourceManifest]          # 공식 모듈 import 이후 호출
    universe: Callable[[], list[str]]
    market_state: Callable[[date], object]
    analyze: Callable[[str, date, object], object]  # run.analyze_ticker 형태
    clock: Callable[[], datetime] = field(default=_now_kst)
    kis_active: Callable[[], bool] = field(default=lambda: False)
    loaded_modules: Callable[[], Iterable] = field(default=lambda: tuple(os.sys.modules.values()))
    git_info: Callable[[Path], tuple[str, bool]] = field(default=lambda root: (_git_commit(root), _git_dirty(root)))
    # 5단계-5 공식 입력 경로(official_inputs.make_official_inputs). None이면 기존 경로(analyze) 그대로.
    # 명시적으로 넣었을 때만 쓴다 — 기본값으로 켜지지 않는다.
    official_inputs: Callable | None = None


def _calendar_for(db: Path, day: date):
    """오늘 장전 공지 확인이 있는 달력 버전(없으면 가장 최근 버전, 공지 확인 없음)."""
    from database.trading_calendar import TradingCalendarStore
    store = TradingCalendarStore(db)
    with store._connection() as conn:
        row = conn.execute("""SELECT check_id, calendar_version FROM calendar_notice_checks
            WHERE substr(checked_at,1,10)=? ORDER BY checked_at DESC LIMIT 1""", (day.isoformat(),)).fetchone()
        if row:
            return store.load_version(row["calendar_version"], check_id=row["check_id"])
        latest = conn.execute("SELECT calendar_version FROM trading_calendar ORDER BY checked_at DESC LIMIT 1").fetchone()
    return store.load_version(latest[0]) if latest else None


def _classify(result) -> dict:
    from constants import Confidence
    s = result.scoring
    low = s.confidence == Confidence.LOW
    return dict(ticker=s.ticker,
                processing_status="excluded_required_missing" if low else "scored",
                normalized_score=s.normalized_score, grade=s.grade.value, confidence=s.confidence.value,
                executable=bool(result.decision.executable),
                components={c.name: [c.score, c.max_score] for c in s.components},
                supply_score=s.supply_score, notes=list(s.notes))


def apply_financial_status(signal: dict, fin_inputs: list) -> dict:
    """결정 B 개정(validation_exclusion_v1, §13.29). 종목의 DART 재무 입력 중 verified가 아닌 것이 있으면 채점 결과를 쓰지 않는다.
    - 사유가 모두 공시 검증 사유(FIN_EXCLUSION_REASONS)면 데이터 검증 제외(excluded_validation) — 실행은 계속(5% 상한).
    - 실행 오류 사유(통신·요청 한도·형식 등)가 하나라도 있으면 처리 실패(failed) → 실행 incomplete.
    점수·등급은 지우고 사유(fin_<사유>)를 남긴다."""
    from analyzer.pipeline.dart_inputs import FIN_EXCLUSION_REASONS
    reasons = {i.failure_reason or "unverified" for i in fin_inputs if i.source_status != "verified"}
    if not reasons:
        return signal
    bad = ",".join(sorted("fin_" + r for r in reasons))
    if reasons <= FIN_EXCLUSION_REASONS:
        return dict(signal, processing_status="excluded_validation", normalized_score=None, grade=None,
                    confidence=None, executable=False, error="financial_validation_excluded: " + bad)
    return dict(signal, processing_status="failed", normalized_score=None, grade=None, confidence=None,
                executable=False, error="financial_input_failed: " + bad)


def run_signal(db: Path, deps: SignalDeps, *, strategy: str, source_root: Path,
               expected_label: str = OFFICIAL_LABEL, universe_rule: str = UNIVERSE_RULE) -> dict:
    """공식 장전 실행 1회. 결과 사실을 dict로 반환한다(판정은 호출자/감독기)."""
    from database.signal_store import SignalStore
    out: dict = {"strategy": strategy, "db": str(db)}
    try:
        ev = deps.collect_launch(expected_label)
    except ProcessStartUnknown as exc:            # 새 프로세스 조건을 검증할 수 없음 → 중단
        return dict(out, outcome="aborted_no_process_start", error=str(exc))
    if deps.kis_active():
        return dict(out, outcome="aborted_kis_active")
    now = deps.clock()
    failures = ev.scheduled_failures(now)
    trigger = "scheduled" if not failures else "manual_cli"
    out.update(trigger=trigger, launch_failures=list(failures), parent_pid=ev.parent_pid,
               service_label=ev.service_label, os_process_started_at=ev.os_process_started_at.isoformat())

    store = SignalStore(db)
    store.initialize()
    cal = _calendar_for(db, now.date())
    if cal is None:
        return dict(out, outcome="aborted_no_calendar")
    ctx = RunContext.start(now, cal)
    lock = Path(db).with_name(".signal_run.lock")
    try:
        with run_lock(lock):
            out["orphans_recovered"] = store.recover_orphans(now, lock_acquired=True)
            manifest = deps.manifest()
            modules = tuple(deps.loaded_modules())
            commit, dirty = deps.git_info(source_root)
            tickers = deps.universe()
            if not tickers:
                return dict(out, outcome="aborted_empty_universe")
            run_id = store.start_run(
                signal_date=now.date().isoformat(), strategy_id=strategy, trigger=trigger,
                version=_engine_version(), digest=manifest.digest,
                loaded_digest=loaded_modules_hash(source_root, modules),
                git_commit=commit, git_dirty=dirty,
                python_executable=os.sys.executable, pid=os.getpid(),
                process_started_at=ev.os_process_started_at, now=now, context=ctx,
                universe_rule=universe_rule, tickers=tickers, lock_acquired=True,
                manifest=manifest, source_root=source_root, loaded_modules=modules, launch=ev)
            out["run_id"] = run_id
            run = store._run_row(run_id)
            if run["status"] != "running":
                return dict(out, outcome="run_aborted", abort_reason=run["abort_reason"])

            fetch_start = deps.clock()
            # 시장자료 기준일 = 실행 시작 때 고정한 직전 완료 세션 (§2.2). 실행 당일 날짜로 조회하지 않는다.
            as_of = ctx.expected_market_session
            out["as_of"] = as_of.isoformat()
            if deps.official_inputs is not None:
                # 5단계-5: 공식 원천 수집 → 근거 있는 입력 → 채점. 입력·연결은 finish_run이 원 응답으로 재검증한다.
                log.info("공식 입력 경로 — %d종목 (run %s)", len(tickers), run_id)
                res = deps.official_inputs(store, run_id, as_of, cal, tickers, deps.clock)
                signals, inputs, links = res.signals, res.inputs, res.links
                out["official_inputs"] = res.report
            else:
                market_state = deps.market_state(as_of)
                signals = []
                log.info("채점 시작 — %d종목 (run %s)", len(tickers), run_id)
                t_start = time.monotonic()
                for n, t in enumerate(tickers, 1):
                    if n == 1 or n % 10 == 0 or n == len(tickers):
                        log.info("[%d/%d] %s  (%.0fs)", n, len(tickers), t, time.monotonic() - t_start)
                    try:
                        signals.append(_classify(deps.analyze(t, as_of, market_state)))
                    except Exception as exc:
                        signals.append(dict(ticker=t, processing_status="failed", normalized_score=None,
                                            grade=None, confidence=None, executable=False,
                                            error=f"{type(exc).__name__}: {exc}"))
                # 4단계: 채점 입력의 원천 시점을 증명할 수 없다 → unverified로 기록(적격성 0)
                inputs = {
                    "mkt-default-provider": MarketInput("pykrx_market_data", fetch_start, None, "unverified"),
                    "fin-naver": FinancialInput("naver_financials", fetch_start, fetch_start, None, "",
                                                None, None, False, False, "unverified"),
                }
                links = {s["ticker"]: sorted(inputs) for s in signals if s["processing_status"] != "failed"}
            try:
                end = deps.manifest()
                end.verify_loaded(source_root, tuple(deps.loaded_modules()))
                end_digest = end.digest
            except UnhashedModule:
                end_digest = "unhashed_module_at_end"
            status = store.finish_run(run_id, signals, end_digest, deps.clock(), inputs=inputs, signal_inputs=links)
            log.info("실행 종료 — status=%s", status)
            out.update(status=status, n_universe=len(tickers),
                       n_scored=sum(s["processing_status"] == "scored" for s in signals),
                       n_failed=sum(s["processing_status"] == "failed" for s in signals),
                       n_excluded=sum(s["processing_status"] == "excluded_required_missing" for s in signals),
                       n_excluded_validation=sum(s["processing_status"] == "excluded_validation" for s in signals),
                       supply_nonzero=sum(1 for s in signals if (s.get("supply_score") or 0) != 0))
            if status != "completed":
                return dict(out, outcome="run_not_completed")
            at = deps.clock()
            decision = planned_entry(store.run_context(run_id), at, store.decision_inputs(run_id), cal)
            key = store.confirm(run_id, now=at, decision=decision)
            log.info("확정 — cohort %s", key)
            row = store._cohort_row(key)
            check = store._commit_check_row(key)
            out.update(outcome="confirmed", cohort_key=key, oos_type=row["oos_type"],
                       eligible=row["eligible"], exclusion_reasons=row["exclusion_reasons"],
                       n_selected=row["n_selected"], entry_rule=row["entry_rule"],
                       commit_check=check["status"] if check else None)
            return out
    except RunBusy:
        return dict(out, outcome="aborted_lock_busy")


def collect_prices(db: Path, fetcher, *, clock: Callable[[], datetime] = _now_kst,
                   start: date | None = None, calendar_version: str | None = None) -> dict:
    """장후 수집: 미완료(날짜×시장)만 수집 → 성과 채움. 신호는 만들지 않는다."""
    from analyzer.pipeline.outcomes import fill_outcomes, initialize_outcomes
    from database.price_store import FIRST_ATTEMPT, OfficialPriceStore
    from database.trading_calendar import TradingCalendarStore
    store = OfficialPriceStore(db)
    initialize_outcomes(store)
    now = clock()
    today = now.date()
    end = today if now.time() >= FIRST_ATTEMPT else today - timedelta(days=1)
    cal_store = TradingCalendarStore(db)
    if calendar_version is None:
        with cal_store._connection() as conn:
            row = conn.execute("SELECT calendar_version FROM trading_calendar ORDER BY checked_at DESC LIMIT 1").fetchone()
        if row is None:
            return {"outcome": "aborted_no_calendar"}
        calendar_version = row[0]
    cal = cal_store.load_version(calendar_version)
    if start is None:
        with store._connection() as conn:
            row = conn.execute("SELECT min(planned_entry_session) FROM official_cohorts").fetchone()
        start = date.fromisoformat(row[0]) if row and row[0] else end
    results = []
    for day, market in store.pending(cal, start, end):
        try:
            results.append((day.isoformat(), market, store.collect(day, market, fetcher, cal, now)[1]))
        except ValueError as exc:
            results.append((day.isoformat(), market, f"skipped:{exc}"))
    counts = fill_outcomes(store, cal, today, now)
    return {"outcome": "done", "calendar_version": calendar_version, "range": [start.isoformat(), end.isoformat()],
            "collections": results, "fill": counts}


def _engine_version() -> str:
    import constants
    return constants.ENGINE_VERSION


GIT_ENV = {"GIT_OPTIONAL_LOCKS": "0"}     # 조회 전용: git status가 .git/index를 갱신하지 않게 (보호 파일 불변)


def _git(root: Path, *args: str) -> str:
    import subprocess
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                              timeout=10, env={**os.environ, **GIT_ENV}).stdout.strip()
    except Exception:
        return ""


def _git_commit(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD") or "unknown"


def _git_dirty(root: Path) -> bool:
    return bool(_git(root, "status", "--porcelain", "--untracked-files=no"))
