"""Stage 1 signal records. Explicit SQLite path; no import-time database writes.

Entry/calendar/source eligibility decisions are supplied by the later pipeline
stages. No official runner or engine registration is installed by this module.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
from typing import Iterator, TYPE_CHECKING
from types import ModuleType
import uuid
import zlib
from zoneinfo import ZoneInfo
from analyzer.pipeline.runtime import SourceManifest, UnhashedModule

if TYPE_CHECKING:
    from analyzer.pipeline.entry import RunContext, EntryDecision

KST = ZoneInfo("Asia/Seoul")
STRATEGY = "SB_EXEC_TOP10_v1"
TEST_STRATEGY = "TEST_SB_EXEC_TOP10_v1"      # 검사 전용: 공식 표본 키를 선점하지 않고 평가에서 제외
STRATEGIES = {STRATEGY, TEST_STRATEGY}
CUTOFF = time(8, 50)
# 공식 실행 시각 (§13.31, 2026-10-23): 수집 종료 08:47:00 → 저장·재검증·확정 예비 최소 60초 → 최종 확정 마감 08:48:00.
# 60초는 보장된 충분 시간이 아니라 초기 예산이다. 확정 마감 이후 확정·사후 확인 실패는 공식 평가에서 제외.
COLLECT_DEADLINE = time(8, 47)
CONFIRM_RESERVE_SECONDS = 60
CONFIRM_DEADLINE = time(8, 48)
PRIMARY_DEADLINE = CONFIRM_DEADLINE          # 08:48:00부터 확정은 catchup
# 응답 없이 끝난 조회의 실패 사유 (§13.3·§13.14). 원 응답이 있으면 정의의 재계산 사유를 쓴다.
FETCH_FAILURES = ("request_timeout", "request_error", "deadline_exhausted", "collection_aborted")
# 결정 B 개정 (2026-10-23, §13.29): 재무 검증 실패 종목은 채점하지 않고 제외(excluded_validation), 실행은 계속.
# 제외 상한: 필수 결측 제외 + 검증 제외 ≤ 유니버스의 5%(정확히 5% 허용). 실행 오류는 제외로 넘기지 않는다.
EXCLUSION_POLICY = "validation_exclusion_v1_2026-10-23"
EXCLUSION_SCOPE = "validated_subset"          # 표본 범위: 실행 전 고정 유니버스 중 데이터 검증 통과 종목
CAPPED_EXCLUSIONS = ("excluded_required_missing", "excluded_validation")
EXCLUDED_STATUSES = ("excluded_required_missing", "excluded_rule", "excluded_validation")


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _stamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")
    return value.astimezone(KST).isoformat(timespec="microseconds")


def cohort_key(signal_date: str, strategy_id: str, engine_id: str, universe_rule: str) -> str:
    return hashlib.sha256(_json([signal_date, strategy_id, engine_id, universe_rule]).encode()).hexdigest()


def _opt_stamp(v):
    return None if v is None else _stamp(v)


def _opt_date(v):
    return None if v is None else v.isoformat()


INPUT_COLUMNS = ("input_id", "input_class", "name", "fetched_at", "source_session_date", "source_status",
                 "source_as_of_time", "value_fetched_at", "filing_list_fetched_at", "report_period_end",
                 "report_type", "rcept_no", "rcept_dt", "rcept_no_linked", "is_latest_version_at_fetch",
                 "is_amendment", "value_json", "definition_id", "params_json", "missing_kind",
                 "basis_status", "failure_reason", "detail_json")


def _input_row(input_id: str, obj) -> tuple:
    """MarketInput/FinancialInput → run_inputs 행 (run_id 제외, INPUT_COLUMNS 순)."""
    from analyzer.pipeline.entry import FinancialInput, MarketInput
    if not input_id:
        raise ValueError("input_id required")
    if isinstance(obj, MarketInput):
        return (input_id, "market", obj.name, _opt_stamp(obj.fetched_at), _opt_date(obj.source_session_date),
                obj.source_status, _opt_stamp(obj.source_as_of_time),
                None, None, None, None, None, None, None, None, None,
                _json(obj.value), obj.definition_id, obj.params_json, obj.missing_kind,
                obj.basis_status, obj.failure_reason, obj.detail_json)
    if isinstance(obj, FinancialInput):
        return (input_id, "financial", obj.name, None, None, obj.source_status, None,
                _opt_stamp(obj.value_fetched_at), _opt_stamp(obj.filing_list_fetched_at),
                _opt_date(obj.report_period_end), obj.report_type, obj.rcept_no, _opt_date(obj.rcept_dt),
                int(bool(obj.rcept_no_linked)), int(bool(obj.is_latest_version_at_fetch)),
                int(bool(obj.is_amendment)), None, None, None, None, None, None, None)
    raise TypeError("MarketInput or FinancialInput required")


def _input_from_row(r: sqlite3.Row, responses: tuple = (), rows: tuple = ()):
    from analyzer.pipeline.entry import FinancialInput, MarketInput
    dt = lambda v: None if v is None else datetime.fromisoformat(v)
    d = lambda v: None if v is None else date.fromisoformat(v)
    if r["input_class"] == "market":
        return MarketInput(r["name"], dt(r["fetched_at"]), d(r["source_session_date"]),
                           r["source_status"], dt(r["source_as_of_time"]),
                           value=json.loads(r["value_json"]), definition_id=r["definition_id"],
                           params_json=r["params_json"], missing_kind=r["missing_kind"],
                           basis_status=r["basis_status"], failure_reason=r["failure_reason"],
                           responses=responses, rows=rows, detail_json=r["detail_json"])
    return FinancialInput(r["name"], dt(r["value_fetched_at"]), dt(r["filing_list_fetched_at"]),
                          d(r["report_period_end"]), r["report_type"], r["rcept_no"], d(r["rcept_dt"]),
                          bool(r["rcept_no_linked"]), bool(r["is_latest_version_at_fetch"]),
                          r["source_status"], bool(r["is_amendment"]))


def _context_from_row(run: sqlite3.Row):
    from analyzer.pipeline.entry import RunContext
    exp = run["expected_market_session"]
    return RunContext(datetime.fromisoformat(run["run_started_at"]),
                      date.fromisoformat(exp) if exp else None,
                      run["calendar_version"], run["calendar_check_id"])


SCHEMA = """
CREATE TABLE IF NOT EXISTS official_engines (
 engine_id TEXT PRIMARY KEY, engine_version TEXT NOT NULL, source_hash TEXT NOT NULL,
 git_commit TEXT NOT NULL, registered_at TEXT NOT NULL,
 UNIQUE(engine_version,source_hash)
);
CREATE TABLE IF NOT EXISTS signal_runs (
 run_id TEXT PRIMARY KEY, cohort_key TEXT NOT NULL, signal_date TEXT NOT NULL,
 strategy_id TEXT NOT NULL, trigger TEXT NOT NULL CHECK(trigger IN ('scheduled','manual_cli','ui')),
 engine_id TEXT, engine_version TEXT NOT NULL, source_hash_start TEXT NOT NULL,
 source_hash_end TEXT, loaded_modules_hash TEXT NOT NULL, git_commit TEXT NOT NULL,
 git_dirty INTEGER NOT NULL CHECK(git_dirty IN (0,1)), python_executable TEXT NOT NULL,
 pid INTEGER NOT NULL, process_started_at TEXT NOT NULL, run_started_at TEXT NOT NULL,
 expected_market_session TEXT, calendar_version TEXT NOT NULL, calendar_check_id TEXT,
 universe_rule_version TEXT NOT NULL,
 universe_hash TEXT NOT NULL, universe_size INTEGER NOT NULL CHECK(universe_size>0),
 status TEXT NOT NULL CHECK(status IN ('running','completed','incomplete','failed','aborted')),
 confirm_status TEXT NOT NULL DEFAULT 'not_attempted'
 CHECK(confirm_status IN ('not_attempted','confirmed','skipped_existing','failed')),
 abort_reason TEXT, finished_at TEXT, completion_order INTEGER, cohort_confirmed_at TEXT,
 launch_parent_pid INTEGER, launch_service_label TEXT, launch_expected_label TEXT,
 os_process_started_at TEXT,
 FOREIGN KEY(engine_id) REFERENCES official_engines(engine_id)
);
CREATE INDEX IF NOT EXISTS signal_runs_cohort ON signal_runs(cohort_key,status);
CREATE TABLE IF NOT EXISTS run_universe (
 run_id TEXT NOT NULL, ticker TEXT NOT NULL, PRIMARY KEY(run_id,ticker),
 FOREIGN KEY(run_id) REFERENCES signal_runs(run_id)
);
CREATE TABLE IF NOT EXISTS run_signals (
 run_id TEXT NOT NULL, ticker TEXT NOT NULL, processing_status TEXT NOT NULL
 CHECK(processing_status IN ('scored','excluded_required_missing','excluded_rule','excluded_validation','failed')),
 normalized_score REAL, grade TEXT, confidence TEXT, executable INTEGER NOT NULL
 CHECK(executable IN (0,1)), payload_json TEXT NOT NULL, PRIMARY KEY(run_id,ticker),
 FOREIGN KEY(run_id,ticker) REFERENCES run_universe(run_id,ticker)
);
CREATE TABLE IF NOT EXISTS official_cohorts (
 cohort_key TEXT PRIMARY KEY, source_run_id TEXT NOT NULL UNIQUE,
 signal_date TEXT NOT NULL, strategy_id TEXT NOT NULL, engine_id TEXT NOT NULL,
 universe_rule_version TEXT NOT NULL, calendar_version TEXT NOT NULL, calendar_check_id TEXT,
 oos_type TEXT NOT NULL CHECK(oos_type IN ('primary','catchup')),
 eligible INTEGER NOT NULL CHECK(eligible IN (0,1)), exclusion_reasons TEXT NOT NULL,
 planned_entry_session TEXT, entry_rule TEXT NOT NULL,
 tie_break_rule TEXT NOT NULL, n_candidates INTEGER NOT NULL,
 n_selected INTEGER NOT NULL CHECK(n_selected BETWEEN 0 AND 10), n_tied_at_cutoff INTEGER NOT NULL,
 cohort_confirmed_at TEXT NOT NULL, FOREIGN KEY(source_run_id) REFERENCES signal_runs(run_id),
 FOREIGN KEY(engine_id) REFERENCES official_engines(engine_id)
);
CREATE TABLE IF NOT EXISTS cohort_members (
 cohort_key TEXT NOT NULL, ticker TEXT NOT NULL, rank INTEGER NOT NULL CHECK(rank BETWEEN 1 AND 10),
 PRIMARY KEY(cohort_key,ticker), UNIQUE(cohort_key,rank),
 FOREIGN KEY(cohort_key) REFERENCES official_cohorts(cohort_key)
);
CREATE TABLE IF NOT EXISTS cohort_scope (
 cohort_key TEXT PRIMARY KEY, source_run_id TEXT NOT NULL UNIQUE, exclusion_policy TEXT NOT NULL,
 scope TEXT NOT NULL, universe_size INTEGER NOT NULL CHECK(universe_size>0), universe_hash TEXT NOT NULL,
 n_scored INTEGER NOT NULL, n_excluded_required_missing INTEGER NOT NULL, n_excluded_validation INTEGER NOT NULL,
 n_excluded_rule INTEGER NOT NULL, exclusion_ratio REAL NOT NULL CHECK(exclusion_ratio BETWEEN 0 AND 1),
 FOREIGN KEY(cohort_key) REFERENCES official_cohorts(cohort_key),
 FOREIGN KEY(source_run_id) REFERENCES signal_runs(run_id)
);
CREATE TABLE IF NOT EXISTS cohort_exclusions (
 cohort_key TEXT NOT NULL, ticker TEXT NOT NULL, processing_status TEXT NOT NULL
 CHECK(processing_status IN ('excluded_required_missing','excluded_rule','excluded_validation')),
 reason TEXT NOT NULL, PRIMARY KEY(cohort_key,ticker),
 FOREIGN KEY(cohort_key) REFERENCES cohort_scope(cohort_key)
);
CREATE TABLE IF NOT EXISTS run_inputs (
 run_id TEXT NOT NULL, input_id TEXT NOT NULL,
 input_class TEXT NOT NULL CHECK(input_class IN ('market','financial')), name TEXT NOT NULL,
 fetched_at TEXT, source_session_date TEXT, source_status TEXT NOT NULL, source_as_of_time TEXT,
 value_fetched_at TEXT, filing_list_fetched_at TEXT, report_period_end TEXT, report_type TEXT,
 rcept_no TEXT, rcept_dt TEXT, rcept_no_linked INTEGER, is_latest_version_at_fetch INTEGER,
 is_amendment INTEGER,
 value_json TEXT, definition_id TEXT, params_json TEXT,
 missing_kind TEXT CHECK(missing_kind IS NULL OR missing_kind IN
   ('source_dash','source_blank','reference_not_session','absent_at_reference','definition_null')),
 basis_status TEXT, failure_reason TEXT, detail_json TEXT,
 CHECK(input_class='financial' OR value_json IS NOT NULL),
 CHECK(missing_kind IS NULL OR (source_status='verified' AND value_json='null')),
 CHECK(failure_reason IS NULL OR source_status='failed'),
 CHECK(definition_id IS NULL OR source_status<>'failed' OR
       (missing_kind IS NULL AND value_json='null' AND failure_reason IS NOT NULL)),
 CHECK(definition_id IS NULL OR params_json IS NOT NULL),
 PRIMARY KEY(run_id,input_id),
 FOREIGN KEY(run_id) REFERENCES signal_runs(run_id)
);
CREATE TABLE IF NOT EXISTS source_responses (
 run_id TEXT NOT NULL, response_id TEXT NOT NULL, endpoint TEXT NOT NULL, request_json TEXT NOT NULL,
 fetched_at TEXT NOT NULL, http_status INTEGER NOT NULL, body_sha256 TEXT NOT NULL CHECK(length(body_sha256)=64),
 body_zlib BLOB NOT NULL, row_count INTEGER, decode_error TEXT,
 CHECK((row_count IS NULL) <> (decode_error IS NULL)),
 PRIMARY KEY(run_id,response_id), UNIQUE(response_id),
 FOREIGN KEY(run_id) REFERENCES signal_runs(run_id)
);
CREATE TABLE IF NOT EXISTS source_response_rows (
 run_id TEXT NOT NULL, response_id TEXT NOT NULL, row_index INTEGER NOT NULL CHECK(row_index>=0),
 row_key TEXT NOT NULL, PRIMARY KEY(run_id,response_id,row_index),
 FOREIGN KEY(run_id,response_id) REFERENCES source_responses(run_id,response_id)
);
CREATE TABLE IF NOT EXISTS run_input_responses (
 run_id TEXT NOT NULL, input_id TEXT NOT NULL, response_id TEXT NOT NULL,
 PRIMARY KEY(run_id,input_id,response_id),
 FOREIGN KEY(run_id,input_id) REFERENCES run_inputs(run_id,input_id),
 FOREIGN KEY(run_id,response_id) REFERENCES source_responses(run_id,response_id)
);
CREATE TABLE IF NOT EXISTS run_input_rows (
 run_id TEXT NOT NULL, input_id TEXT NOT NULL, response_id TEXT NOT NULL, row_index INTEGER NOT NULL,
 PRIMARY KEY(run_id,input_id,response_id,row_index),
 FOREIGN KEY(run_id,input_id,response_id) REFERENCES run_input_responses(run_id,input_id,response_id),
 FOREIGN KEY(run_id,response_id,row_index) REFERENCES source_response_rows(run_id,response_id,row_index)
);
CREATE TABLE IF NOT EXISTS run_signal_inputs (
 run_id TEXT NOT NULL, ticker TEXT NOT NULL, input_id TEXT NOT NULL,
 PRIMARY KEY(run_id,ticker,input_id),
 FOREIGN KEY(run_id,ticker) REFERENCES run_signals(run_id,ticker),
 FOREIGN KEY(run_id,input_id) REFERENCES run_inputs(run_id,input_id)
);
CREATE TABLE IF NOT EXISTS commit_checks (
 cohort_key TEXT PRIMARY KEY, run_id TEXT NOT NULL, cohort_confirmed_at TEXT NOT NULL,
 observed_after_commit_at TEXT, cutoff_at TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('verified_before_cutoff','after_cutoff','check_failed')),
 detail TEXT, FOREIGN KEY(cohort_key) REFERENCES official_cohorts(cohort_key)
);
CREATE TABLE IF NOT EXISTS legacy_eligibility (
 decision_id INTEGER PRIMARY KEY, source TEXT NOT NULL, signal_date TEXT NOT NULL,
 ticker TEXT NOT NULL, eligible INTEGER NOT NULL CHECK(eligible IN (0,1)),
 reasons TEXT NOT NULL, decided_at TEXT NOT NULL, evidence_ref TEXT NOT NULL
);
"""


class SignalStore:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        # Stage 4 must explicitly remove this guard before production initialization.
        if self.path == Path(__file__).resolve().parent / "research.db":
            raise ValueError("stage1_production_database_disabled")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def initialize(self) -> None:
        with self._connection() as conn:
            columns = {r[1]: r for r in conn.execute('PRAGMA table_info(signal_runs)')}
            if columns and (not {'calendar_version','calendar_check_id','completion_order',
                                 'launch_parent_pid','os_process_started_at'} <= columns.keys()
                            or columns['expected_market_session'][3]):
                raise ValueError('pipeline_schema_upgrade_required: preserve old isolated DB; initialize a new copy')
            input_columns = {r[1] for r in conn.execute('PRAGMA table_info(run_inputs)')}
            if input_columns and not set(INPUT_COLUMNS) <= input_columns:
                raise ValueError('pipeline_schema_upgrade_required: run_inputs evidence fields missing '
                                 '(stage 5: ' + ','.join(sorted(set(INPUT_COLUMNS) - input_columns)) +
                                 '); preserve old isolated DB; initialize a new copy')
            cohort_columns = {r[1] for r in conn.execute('PRAGMA table_info(official_cohorts)')}
            if cohort_columns and not {'calendar_version','calendar_check_id'} <= cohort_columns:
                raise ValueError('pipeline_schema_upgrade_required: calendar audit fields missing')
            sig_sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='run_signals'").fetchone()
            if sig_sql and 'excluded_validation' not in sig_sql[0]:
                raise ValueError('pipeline_schema_upgrade_required: run_signals lacks excluded_validation '
                                 '(validation exclusion policy); preserve old isolated DB; initialize a new copy')
            conn.executescript(SCHEMA)
            if 'completion_order' not in {r[1] for r in conn.execute('PRAGMA table_info(signal_runs)')}:
                conn.execute('ALTER TABLE signal_runs ADD COLUMN completion_order INTEGER')
            for table in ("official_engines", "run_universe", "run_signals",
                          "official_cohorts", "cohort_members", "legacy_eligibility",
                          "run_inputs", "run_signal_inputs", "commit_checks", "source_responses",
                          "source_response_rows", "run_input_responses", "run_input_rows",
                          "cohort_scope", "cohort_exclusions"):
                for action in ("UPDATE", "DELETE"):
                    conn.execute(f"""CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action}
                    BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'immutable record'); END""")
            conn.executescript("""
            CREATE TRIGGER IF NOT EXISTS terminal_run_status BEFORE UPDATE OF status ON signal_runs
            WHEN OLD.status <> 'running' BEGIN SELECT RAISE(ABORT,'terminal run'); END;
            CREATE TRIGGER IF NOT EXISTS terminal_run_audit BEFORE UPDATE ON signal_runs
            WHEN OLD.status <> 'running' AND (
              OLD.finished_at IS NOT NEW.finished_at OR
              OLD.source_hash_end IS NOT NEW.source_hash_end OR
              OLD.abort_reason IS NOT NEW.abort_reason OR
              OLD.completion_order IS NOT NEW.completion_order)
            BEGIN SELECT RAISE(ABORT,'immutable terminal audit'); END;
            CREATE TRIGGER IF NOT EXISTS confirmed_timestamp_once BEFORE UPDATE ON signal_runs
            WHEN OLD.cohort_confirmed_at IS NOT NEW.cohort_confirmed_at AND NOT (
              OLD.status = 'completed' AND OLD.confirm_status = 'not_attempted' AND
              NEW.confirm_status = 'confirmed' AND NEW.cohort_confirmed_at IS NOT NULL AND
              EXISTS (SELECT 1 FROM official_cohorts c WHERE c.source_run_id=OLD.run_id
                      AND c.cohort_confirmed_at=NEW.cohort_confirmed_at))
            BEGIN SELECT RAISE(ABORT,'immutable confirmation timestamp'); END;
            CREATE TRIGGER IF NOT EXISTS run_identity_immutable BEFORE UPDATE OF
              run_id,cohort_key,signal_date,strategy_id,trigger,engine_id,engine_version,
              source_hash_start,loaded_modules_hash,git_commit,git_dirty,python_executable,
              pid,process_started_at,run_started_at,expected_market_session,calendar_version,calendar_check_id,
              universe_rule_version,universe_hash,universe_size,launch_parent_pid,
              launch_service_label,launch_expected_label,os_process_started_at ON signal_runs
            BEGIN SELECT RAISE(ABORT,'immutable run identity'); END;
            CREATE TRIGGER IF NOT EXISTS run_no_delete BEFORE DELETE ON signal_runs
            BEGIN SELECT RAISE(ABORT,'immutable run'); END;
            CREATE TRIGGER IF NOT EXISTS terminal_confirm BEFORE UPDATE OF confirm_status ON signal_runs
            WHEN OLD.confirm_status <> 'not_attempted'
            BEGIN SELECT RAISE(ABORT,'confirmation already attempted'); END;
            CREATE TRIGGER IF NOT EXISTS universe_insert_limit BEFORE INSERT ON run_universe
            WHEN (SELECT count(*) FROM run_universe WHERE run_id=NEW.run_id) >=
                 (SELECT universe_size FROM signal_runs WHERE run_id=NEW.run_id)
            BEGIN SELECT RAISE(ABORT,'universe is fixed'); END;
            CREATE TRIGGER IF NOT EXISTS signals_insert_only_running BEFORE INSERT ON run_signals
            WHEN (SELECT status FROM signal_runs WHERE run_id=NEW.run_id) <> 'running'
            BEGIN SELECT RAISE(ABORT,'run results are final'); END;
            CREATE TRIGGER IF NOT EXISTS inputs_insert_only_running BEFORE INSERT ON run_inputs
            WHEN (SELECT status FROM signal_runs WHERE run_id=NEW.run_id) <> 'running'
            BEGIN SELECT RAISE(ABORT,'run inputs are final'); END;
            CREATE TRIGGER IF NOT EXISTS signal_inputs_insert_only_running BEFORE INSERT ON run_signal_inputs
            WHEN (SELECT status FROM signal_runs WHERE run_id=NEW.run_id) <> 'running'
            BEGIN SELECT RAISE(ABORT,'run inputs are final'); END;
            CREATE TRIGGER IF NOT EXISTS responses_insert_only_running BEFORE INSERT ON source_responses
            WHEN (SELECT status FROM signal_runs WHERE run_id=NEW.run_id) IS NOT 'running'
            BEGIN SELECT RAISE(ABORT,'run responses are final'); END;
            CREATE TRIGGER IF NOT EXISTS response_rows_insert_only_running BEFORE INSERT ON source_response_rows
            WHEN (SELECT status FROM signal_runs WHERE run_id=NEW.run_id) IS NOT 'running'
            BEGIN SELECT RAISE(ABORT,'run responses are final'); END;
            CREATE TRIGGER IF NOT EXISTS input_responses_insert_only_running BEFORE INSERT ON run_input_responses
            WHEN (SELECT status FROM signal_runs WHERE run_id=NEW.run_id) IS NOT 'running'
            BEGIN SELECT RAISE(ABORT,'run inputs are final'); END;
            CREATE TRIGGER IF NOT EXISTS input_rows_insert_only_running BEFORE INSERT ON run_input_rows
            WHEN (SELECT status FROM signal_runs WHERE run_id=NEW.run_id) IS NOT 'running'
            BEGIN SELECT RAISE(ABORT,'run inputs are final'); END;
            CREATE TRIGGER IF NOT EXISTS scope_insert_only_confirming BEFORE INSERT ON cohort_scope
            WHEN (SELECT r.confirm_status FROM official_cohorts c JOIN signal_runs r
                  ON r.run_id=c.source_run_id WHERE c.cohort_key=NEW.cohort_key) <> 'not_attempted'
            BEGIN SELECT RAISE(ABORT,'cohort is final'); END;
            CREATE TRIGGER IF NOT EXISTS exclusions_insert_only_confirming BEFORE INSERT ON cohort_exclusions
            WHEN (SELECT r.confirm_status FROM official_cohorts c JOIN signal_runs r
                  ON r.run_id=c.source_run_id WHERE c.cohort_key=NEW.cohort_key) <> 'not_attempted'
            BEGIN SELECT RAISE(ABORT,'cohort is final'); END;
            CREATE TRIGGER IF NOT EXISTS members_insert_only_confirming BEFORE INSERT ON cohort_members
            WHEN (SELECT r.confirm_status FROM official_cohorts c JOIN signal_runs r
                  ON r.run_id=c.source_run_id WHERE c.cohort_key=NEW.cohort_key) <> 'not_attempted'
            BEGIN SELECT RAISE(ABORT,'cohort is final'); END;
            """)
        from database.trading_calendar import TradingCalendarStore
        TradingCalendarStore.initialize(self)

    def register_engine(self, version: str, digest: str, git_commit: str, now: datetime) -> str:
        """Explicit operation, used only in temporary test databases in stage 1."""
        engine_id = hashlib.sha256(_json([version, digest]).encode()).hexdigest()
        with self._transaction() as conn:
            existing = conn.execute("SELECT * FROM official_engines WHERE engine_id=?", (engine_id,)).fetchone()
            if not existing:
                conn.execute("INSERT INTO official_engines VALUES(?,?,?,?,?)",
                             (engine_id, version, digest, git_commit, _stamp(now)))
        return engine_id

    def start_run(self, *, signal_date: str, strategy_id: str, trigger: str,
                  version: str, digest: str, loaded_digest: str, git_commit: str,
                  git_dirty: bool, python_executable: str, pid: int,
                  process_started_at: datetime, now: datetime, context: RunContext,
                  universe_rule: str, tickers: list[str], lock_acquired: bool = True,
                  manifest: SourceManifest | None = None, source_root: Path | None = None,
                  loaded_modules: tuple[ModuleType, ...] | None = None,
                  launch: "LaunchEvidence | None" = None) -> str:
        from analyzer.pipeline.entry import RunContext
        from analyzer.pipeline.runtime import LaunchEvidence
        if not isinstance(context, RunContext):
            raise TypeError('RunContext required')
        if _stamp(now) != _stamp(context.run_started_at):
            raise ValueError('run context start timestamp mismatch')
        if strategy_id not in STRATEGIES:
            raise ValueError("unsupported strategy")
        if trigger == 'scheduled':
            # 예정 실행은 실행 경로 근거가 조건을 모두 충족할 때만. 미충족은 호출자가 manual_cli로 기록해야 한다.
            if not isinstance(launch, LaunchEvidence) or launch.scheduled_failures(now):
                raise ValueError('scheduled_evidence_insufficient')
            if _stamp(launch.os_process_started_at) != _stamp(process_started_at):
                raise ValueError('process start must come from OS evidence')
        if not tickers or len(set(tickers)) != len(tickers) or any(not t for t in tickers):
            raise ValueError("nonempty unique universe required")
        now_s, started_s = _stamp(now), _stamp(process_started_at)
        run_id = uuid.uuid4().hex
        with self._transaction() as conn:
            engine = conn.execute("SELECT engine_id FROM official_engines WHERE engine_version=? AND source_hash=?",
                                  (version, digest)).fetchone()
            engine_id = engine[0] if engine else None
            reason = None
            age = (now - process_started_at).total_seconds()
            if not lock_acquired:
                reason = "lock_busy"
            elif age < 0 or age > 60:
                reason = "stale_process"
            elif not engine_id:
                reason = "engine_unregistered"
            elif context.expected_market_session is None:
                reason = 'calendar_unknown'
            elif manifest is not None:
                if source_root is None or manifest.digest != digest:
                    reason = 'source_manifest_mismatch'
                else:
                    try:
                        manifest.verify_loaded(source_root, tuple(sys.modules.values())
                                               if loaded_modules is None else loaded_modules)
                    except UnhashedModule:
                        reason = 'unhashed_module'
            values = dict(run_id=run_id, cohort_key=cohort_key(signal_date,strategy_id,engine_id or digest,universe_rule),
                signal_date=signal_date,strategy_id=strategy_id,trigger=trigger,engine_id=engine_id,
                engine_version=version,source_hash_start=digest,loaded_modules_hash=loaded_digest,
                git_commit=git_commit,git_dirty=int(git_dirty),python_executable=python_executable,pid=pid,
                process_started_at=started_s,run_started_at=now_s,
                expected_market_session=context.expected_market_session.isoformat() if context.expected_market_session else None,
                calendar_version=context.calendar_version,calendar_check_id=context.calendar_check_id,
                universe_rule_version=universe_rule,universe_hash=hashlib.sha256(_json(sorted(tickers)).encode()).hexdigest(),
                universe_size=len(tickers),status="aborted" if reason else "running",abort_reason=reason,
                finished_at=now_s if reason else None,
                launch_parent_pid=launch.parent_pid if launch else None,
                launch_service_label=launch.service_label if launch else None,
                launch_expected_label=launch.expected_label if launch else None,
                os_process_started_at=_stamp(launch.os_process_started_at) if launch else None)
            columns=','.join(values)
            conn.execute(f"INSERT INTO signal_runs({columns}) VALUES({','.join('?' for _ in values)})",tuple(values.values()))
            conn.executemany("INSERT INTO run_universe VALUES(?,?)",[(run_id,t) for t in sorted(tickers)])
        return run_id

    def recover_orphans(self, now: datetime, *, lock_acquired: bool) -> int:
        """Caller must hold the shared run lock; don't mistake an active run for an orphan."""
        if not lock_acquired:
            raise ValueError("orphan recovery requires exclusive lock")
        with self._transaction() as conn:
            return conn.execute("UPDATE signal_runs SET status='failed',abort_reason='orphaned',finished_at=? WHERE status='running'",
                                (_stamp(now),)).rowcount

    def record_response(self, run_id: str, *, endpoint: str, request: dict, fetched_at: datetime,
                        http_status: int, body: bytes):
        """원 응답을 실행 중에 추가 전용으로 보존한다. 해석 실패도 본문과 사유를 남긴다. → sources.Response"""
        from analyzer.pipeline.sources import Response, SourceParseError, decode_meta, decode_rows, row_key
        if not isinstance(body, (bytes, bytearray)):
            raise TypeError("raw response bytes required")
        body = bytes(body)
        try:
            rows, error = decode_rows(endpoint, body), None
        except SourceParseError as e:
            rows, error = None, str(e)
        response_id = uuid.uuid4().hex
        with self._transaction() as conn:
            if self._run(conn, run_id)['status'] != 'running':
                raise ValueError("run is not running")
            conn.execute("INSERT INTO source_responses VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (run_id, response_id, endpoint, _json(request), _stamp(fetched_at), int(http_status),
                          hashlib.sha256(body).hexdigest(), zlib.compress(body),
                          None if rows is None else len(rows), error))
            conn.executemany("INSERT INTO source_response_rows VALUES(?,?,?,?)",
                             [(run_id, response_id, i, row_key(endpoint, r)) for i, r in enumerate(rows or [])])
        return Response(response_id, endpoint, json.loads(_json(request)),
                        datetime.fromisoformat(_stamp(fetched_at)), int(http_status),
                        None if rows is None else tuple(rows), error, decode_meta(endpoint, body))

    @staticmethod
    def _load_responses(conn: sqlite3.Connection, run_id: str, ids) -> dict:
        """이 실행의 저장 응답을 다시 해석한다(본문 해시 확인 포함). 없는 응답은 결과에서 빠진다."""
        from analyzer.pipeline.sources import Response, SourceParseError, decode_meta, decode_rows
        out = {}
        for rid in sorted(set(ids)):
            r = conn.execute("SELECT * FROM source_responses WHERE run_id=? AND response_id=?",
                             (run_id, rid)).fetchone()
            if r is None:
                continue
            body = zlib.decompress(r["body_zlib"])
            if hashlib.sha256(body).hexdigest() != r["body_sha256"]:
                raise ValueError("response_body_hash_mismatch")
            try:
                rows, error = tuple(decode_rows(r["endpoint"], body)), None
            except SourceParseError as e:
                rows, error = None, str(e)
            out[rid] = Response(rid, r["endpoint"], json.loads(r["request_json"]),
                                datetime.fromisoformat(r["fetched_at"]), r["http_status"], rows, error,
                                decode_meta(r["endpoint"], body))
        return out

    def _evidence_problem(self, conn: sqlite3.Connection, run: sqlite3.Row, inputs: dict) -> str | None:
        """채점 입력의 값·근거·시점을 원 응답 재파싱 결과와 대조한다. 문제 없으면 None.
        - 검증(verified) 시장 입력은 정의(definition_id)와 원 응답이 있어야 한다.
        - 응답·행은 이 실행에 저장된 것이어야 하고, 정의가 실제로 쓴 응답·행과 정확히 같아야 한다.
        - 상태·값·결측 종류·실패 사유·기준 상태·조회 시각·기준 세션이 재계산과 같아야 한다."""
        from analyzer.pipeline.entry import MarketInput
        from analyzer.pipeline.sources import derive
        calendar = None
        session = date.fromisoformat(run['expected_market_session']) if run['expected_market_session'] else None
        for obj in inputs.values():
            if not isinstance(obj, MarketInput):
                continue
            if obj.definition_id is None:
                if obj.source_status == 'verified':
                    return 'input_evidence_missing'
                if obj.responses or obj.rows or obj.value is not None or obj.missing_kind or obj.detail_json:
                    return 'input_evidence_invalid'
                continue
            if session is None or obj.params_json is None:
                return 'input_evidence_invalid'
            if not obj.responses:
                # 응답을 받지 못한 조회 실패: 재파싱할 근거가 없으므로 실패 기록만 허용
                if (obj.source_status != 'failed' or obj.failure_reason not in FETCH_FAILURES or obj.rows
                        or obj.value is not None or obj.missing_kind or obj.detail_json
                        or obj.source_session_date != session):
                    return 'input_evidence_invalid'
                continue
            try:
                stored = self._load_responses(conn, run['run_id'], obj.responses)
            except (ValueError, zlib.error):
                return 'input_evidence_invalid'      # 본문 해시 불일치 등
            if set(stored) != set(obj.responses) or len(set(obj.responses)) != len(obj.responses):
                return 'input_evidence_invalid'      # 다른 실행의 응답·없는 응답·중복 참조
            for rid, idx in obj.rows:
                if rid not in stored or not conn.execute(
                        "SELECT 1 FROM source_response_rows WHERE run_id=? AND response_id=? AND row_index=?",
                        (run['run_id'], rid, idx)).fetchone():
                    return 'input_evidence_invalid'  # 없는 행
            try:
                if calendar is None:
                    from database.trading_calendar import TradingCalendarStore
                    calendar = TradingCalendarStore(self.path).load_version(
                        run['calendar_version'], check_id=run['calendar_check_id'])
                d = derive(obj.definition_id, list(stored.values()), json.loads(obj.params_json), session, calendar)
            except Exception:
                return 'input_evidence_mismatch'
            fetched_ok = (d.fetched_at is None or obj.fetched_at is not None and
                          _stamp(obj.fetched_at) == _stamp(d.fetched_at))
            if (obj.source_status != d.status or obj.value != d.value or obj.missing_kind != d.missing_kind
                    or obj.failure_reason != d.failure_reason or obj.basis_status != d.basis_status
                    or obj.detail_json != (None if d.detail is None else _json(d.detail))
                    or tuple(sorted(obj.responses)) != d.responses or tuple(sorted(obj.rows)) != d.rows
                    or not fetched_ok or obj.source_session_date != session):
                return 'input_evidence_mismatch'
        return None

    def _write_inputs(self, conn: sqlite3.Connection, run_id: str, rows: list, inputs: dict,
                      links: list | None) -> None:
        from analyzer.pipeline.entry import MarketInput
        conn.executemany(f"INSERT INTO run_inputs(run_id,{','.join(INPUT_COLUMNS)}) "
                         f"VALUES({','.join('?' * (len(INPUT_COLUMNS) + 1))})", [(run_id, *r) for r in rows])
        for input_id, obj in sorted(inputs.items()):
            if isinstance(obj, MarketInput):
                conn.executemany("INSERT INTO run_input_responses VALUES(?,?,?)",
                                 [(run_id, input_id, rid) for rid in sorted(set(obj.responses))])
                conn.executemany("INSERT INTO run_input_rows VALUES(?,?,?,?)",
                                 [(run_id, input_id, rid, idx) for rid, idx in sorted(set(obj.rows))])
        if links is not None:
            conn.executemany("INSERT INTO run_signal_inputs VALUES(?,?,?)", links)

    def audit_input_evidence(self, run_id: str) -> str | None:
        """저장된 입력을 저장된 원 응답으로 다시 검증한다(감사용). 문제 없으면 None."""
        with self._connection() as conn:
            run = self._run(conn, run_id)
            inputs = {r['input_id']: self._stored_input(conn, r)
                      for r in conn.execute("SELECT * FROM run_inputs WHERE run_id=?", (run_id,))}
            return self._evidence_problem(conn, run, inputs)

    @staticmethod
    def _stored_input(conn: sqlite3.Connection, r: sqlite3.Row):
        resp = tuple(x[0] for x in conn.execute(
            "SELECT response_id FROM run_input_responses WHERE run_id=? AND input_id=? ORDER BY response_id",
            (r['run_id'], r['input_id'])))
        rows = tuple((x[0], x[1]) for x in conn.execute(
            "SELECT response_id,row_index FROM run_input_rows WHERE run_id=? AND input_id=? "
            "ORDER BY response_id,row_index", (r['run_id'], r['input_id'])))
        return _input_from_row(r, resp, rows)

    @staticmethod
    def _financial_status_problem(signals, inputs, signal_inputs) -> bool:
        """결정 B 개정(§13.29): 재무(DART) 입력이 verified가 아닌 종목의 처리 상태 일관성.
        - 사유가 모두 검증 제외 사유이고 다른 입력 실패가 없으면 excluded_validation(점수·등급 없음) 또는 failed.
        - 실행 오류 사유(통신·요청 한도·형식 등)가 하나라도 있으면 failed여야 한다.
        - excluded_validation은 위 조건의 재무 실패가 있을 때만, 점수·등급 없이.
        어긋나면 실행기 결함으로 보고 실행을 incomplete로 둔다(빈 값 채점·제외율 혼입 금지)."""
        from analyzer.pipeline.dart_inputs import FIN_EXCLUSION_REASONS
        from analyzer.pipeline.entry import MarketInput
        for s in signals:
            fin, other = [], []
            for i in signal_inputs.get(s['ticker'], []):
                obj = inputs.get(i)
                if isinstance(obj, MarketInput) and obj.source_status != 'verified':
                    (fin if (obj.definition_id or '').startswith('dart_') else other).append(obj.failure_reason)
            status = s['processing_status']
            validation_only = bool(fin) and not other and all(r in FIN_EXCLUSION_REASONS for r in fin)
            if status == 'excluded_validation':
                if not validation_only or s.get('normalized_score') is not None or s.get('grade') is not None:
                    return True
            elif fin and status != 'failed':
                return True
        return False

    def finish_run(self, run_id: str, signals: list[dict], end_digest: str, now: datetime,
                   inputs: dict | None = None, signal_inputs: dict | None = None) -> str:
        """inputs: {input_id: MarketInput|FinancialInput}, signal_inputs: {ticker: [input_id,...]}.
        채점에 쓴 입력과 종목별 사용 입력을 실행 결과와 같은 트랜잭션에 기록한다."""
        inputs = dict(inputs or {})
        signal_inputs = {t: list(ids) for t, ids in (signal_inputs or {}).items()}
        rows = [_input_row(i, obj) for i, obj in sorted(inputs.items())]
        with self._transaction() as conn:
            run = self._run(conn,run_id)
            if run['status'] != 'running':
                raise ValueError("run is not running")
            expected={r[0] for r in conn.execute("SELECT ticker FROM run_universe WHERE run_id=?",(run_id,))}
            found=[s['ticker'] for s in signals]
            if end_digest != run['source_hash_start']:
                status,reason="aborted","source_hash_changed"
            elif len(set(found)) != len(found) or set(found) != expected:
                status,reason="incomplete","universe_mismatch"
            # 무결성 검사(연결·근거)를 처리 상태 검사보다 먼저: 실패 종목이 있어도 입력 근거는 검증된 것만 기록
            elif (set(signal_inputs) - set(found)
                  or any(i not in inputs for ids in signal_inputs.values() for i in ids)):
                status,reason="incomplete","inputs_invalid"
            elif (evidence := self._evidence_problem(conn, run, inputs)):
                status,reason="incomplete",evidence
            elif self._financial_status_problem(signals, inputs, signal_inputs):
                status,reason="incomplete","financial_status_inconsistent"
            elif any(s['processing_status']=='failed' for s in signals):
                status,reason="incomplete","processing_failed"
            elif 100*sum(s['processing_status'] in CAPPED_EXCLUSIONS for s in signals)>5*len(expected):
                status,reason="incomplete","degraded_inputs"
            elif any(s['processing_status']=='scored' and not signal_inputs.get(s['ticker']) for s in signals):
                status,reason="incomplete","inputs_unlinked"
            else:
                status,reason="completed",None
            if set(found)==expected and len(set(found))==len(found):
                for s in signals:
                    score=s.get('normalized_score')
                    if score is not None and (not math.isfinite(score) or not 0<=score<=100):
                        raise ValueError("invalid normalized score")
                    conn.execute("INSERT INTO run_signals VALUES(?,?,?,?,?,?,?,?)",
                        (run_id,s['ticker'],s['processing_status'],score,s.get('grade'),s.get('confidence'),int(s.get('executable',False)),_json(s)))
                # 근거 불일치 입력은 기록하지 않는다(검증되지 않은 주장을 남기지 않음). 같은 트랜잭션.
                if not (reason or '').startswith('input_evidence'):
                    links = None if reason == "inputs_invalid" else [
                        (run_id, t, i) for t, ids in sorted(signal_inputs.items()) for i in sorted(set(ids))]
                    self._write_inputs(conn, run_id, rows, inputs, links)
            completion_order=conn.execute('SELECT coalesce(max(completion_order),0)+1 FROM signal_runs').fetchone()[0]
            conn.execute("UPDATE signal_runs SET status=?,abort_reason=?,source_hash_end=?,finished_at=?,completion_order=? WHERE run_id=?",
                         (status,reason,end_digest,_stamp(now),completion_order,run_id))
        return status

    @staticmethod
    def _select(conn: sqlite3.Connection, run_id: str):
        candidates=conn.execute("""SELECT ticker,normalized_score FROM run_signals
            WHERE run_id=? AND processing_status='scored' AND grade='strong_buy'
            AND executable=1 AND confidence IN ('high','medium') AND normalized_score IS NOT NULL
            ORDER BY normalized_score DESC,ticker ASC""",(run_id,)).fetchall()
        return candidates, candidates[:10]

    @classmethod
    def _decision_inputs(cls, conn: sqlite3.Connection, run_id: str) -> list:
        """판정 입력 = 선정 종목이 사용한 입력의 합집합(선정 0개면 실행의 전체 입력). input_id 순."""
        _, selected = cls._select(conn, run_id)
        if selected:
            marks = ','.join('?' for _ in selected)
            rows = conn.execute(f"""SELECT DISTINCT i.* FROM run_inputs i JOIN run_signal_inputs l
                ON l.run_id=i.run_id AND l.input_id=i.input_id
                WHERE i.run_id=? AND l.ticker IN ({marks}) ORDER BY i.input_id""",
                (run_id, *[s['ticker'] for s in selected])).fetchall()
        else:
            rows = conn.execute("SELECT * FROM run_inputs WHERE run_id=? ORDER BY input_id", (run_id,)).fetchall()
        return [cls._stored_input(conn, r) for r in rows]

    def decision_inputs(self, run_id: str) -> list:
        """실행기가 판정에 쓸 입력. confirm()의 재계산과 같은 규칙."""
        with self._connection() as conn:
            return self._decision_inputs(conn, run_id)

    def run_context(self, run_id: str):
        with self._connection() as conn:
            return _context_from_row(self._run(conn, run_id))

    def _run_row(self, run_id: str) -> sqlite3.Row:
        with self._connection() as conn:
            return self._run(conn, run_id)

    def _cohort_row(self, key: str) -> sqlite3.Row | None:
        with self._connection() as conn:
            return conn.execute("SELECT * FROM official_cohorts WHERE cohort_key=?", (key,)).fetchone()

    def _commit_check_row(self, key: str) -> sqlite3.Row | None:
        with self._connection() as conn:
            return conn.execute("SELECT * FROM commit_checks WHERE cohort_key=?", (key,)).fetchone()

    @staticmethod
    def _run(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        run=conn.execute("SELECT * FROM signal_runs WHERE run_id=?",(run_id,)).fetchone()
        if run is None:
            raise ValueError("unknown run")
        return run

    def confirm(self, run_id: str, *, now: datetime, decision: EntryDecision,
                requested_type: str | None = None) -> str | None:
        """Persist a decision from stage 2. Caller cannot request primary.

        `now` is the proposed confirmation timestamp. Production deadline/commit
        verification belongs to the official runner (stage 4); not wired here.
        """
        from analyzer.pipeline.entry import EntryDecision
        if not isinstance(decision, EntryDecision):
            raise TypeError('EntryDecision required')
        if _stamp(now) != _stamp(decision.confirmed_at):
            raise ValueError('decision confirmation timestamp mismatch')
        if requested_type not in (None,'catchup'):
            raise ValueError("primary cannot be requested")
        timestamp=_stamp(now)
        attempted=False
        created=None
        try:
            with self._transaction() as conn:
                run=self._run(conn,run_id)
                if run['status']!='completed' or run['trigger']=='ui' or not run['engine_id']:
                    raise ValueError("run cannot create official cohort")
                context = decision.run_context
                expected = context.expected_market_session.isoformat() if context.expected_market_session else None
                if expected != run['expected_market_session']:
                    raise ValueError('decision market session mismatch')
                if (_stamp(context.run_started_at) != run['run_started_at']
                        or context.calendar_version != run['calendar_version']
                        or decision.calendar_version != run['calendar_version']):
                    raise ValueError('decision run context or calendar version mismatch')
                if (context.calendar_check_id != run['calendar_check_id']
                        or decision.calendar_check_id != run['calendar_check_id']):
                    raise ValueError('decision calendar notice check mismatch')
                if timestamp < run['finished_at']:
                    raise ValueError("confirmation cannot precede completion")
                # 저장된 실행 문맥·입력·달력으로 판정을 다시 계산해 모든 필드를 비교한다
                from analyzer.pipeline.entry import planned_entry
                from database.trading_calendar import TradingCalendarStore
                calendar = TradingCalendarStore(self.path).load_version(
                    run['calendar_version'], check_id=run['calendar_check_id'])
                recomputed = planned_entry(_context_from_row(run), decision.confirmed_at,
                                           self._decision_inputs(conn, run_id), calendar)
                if recomputed != decision:
                    raise ValueError('decision_recompute_mismatch')
                if run['confirm_status']!='not_attempted':
                    existing=conn.execute("SELECT cohort_key FROM official_cohorts WHERE source_run_id=?",(run_id,)).fetchone()
                    return existing[0] if existing else None
                attempted=True
                key=run['cohort_key']
                if conn.execute("SELECT 1 FROM official_cohorts WHERE cohort_key=?",(key,)).fetchone():
                    conn.execute("UPDATE signal_runs SET confirm_status='skipped_existing' WHERE run_id=?",(run_id,))
                    return key
                prior=conn.execute("""SELECT 1 FROM signal_runs WHERE cohort_key=? AND status='completed'
                    AND run_id<>? AND (finished_at<? OR (finished_at=? AND
                    (completion_order IS NULL OR completion_order<?)))""",
                    (key,run_id,run['finished_at'],run['finished_at'],run['completion_order'])).fetchone()
                local=now.astimezone(KST)
                primary=(run['trigger']=='scheduled' and run['signal_date']==local.date().isoformat()
                         and decision.is_session and local.time()<PRIMARY_DEADLINE
                         and decision.entry_rule=='same_day_open'
                         and not prior and requested_type!='catchup')
                candidates, selected = self._select(conn, run_id)
                tied=sum(c['normalized_score']==selected[-1]['normalized_score'] for c in candidates) if len(selected)==10 else 0
                reasons=sorted(set(decision.reasons))
                planned_entry_session = decision.planned_entry_session.isoformat() if decision.planned_entry_session else None
                if planned_entry_session is None:
                    reasons=sorted(set(reasons+['calendar_unknown']))
                conn.execute("INSERT INTO official_cohorts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (key,run_id,run['signal_date'],run['strategy_id'],run['engine_id'],run['universe_rule_version'],
                     run['calendar_version'],run['calendar_check_id'],
                     'primary' if primary else 'catchup',int(not reasons),_json(reasons),planned_entry_session,
                     decision.entry_rule,'normalized_score DESC, ticker ASC',len(candidates),len(selected),tied,timestamp))
                conn.executemany("INSERT INTO cohort_members VALUES(?,?,?)",[(key,s['ticker'],i+1) for i,s in enumerate(selected)])
                self._record_scope(conn, key, run)
                conn.execute("UPDATE signal_runs SET confirm_status='confirmed',cohort_confirmed_at=? WHERE run_id=?",(timestamp,run_id))
                created=(key, run['signal_date'])
        except BaseException:
            if attempted and created is None:
                with self._transaction() as conn:
                    conn.execute("UPDATE signal_runs SET confirm_status='failed' WHERE run_id=? AND confirm_status='not_attempted'",(run_id,))
            raise
        # COMMIT 이후: 실제 완료 시각 확인을 추가 전용으로 남긴다. 기록이 없으면 공식 평가에서 제외된다.
        self._record_commit_check(created[0], run_id, now, created[1])
        return created[0]

    @staticmethod
    def _record_scope(conn: sqlite3.Connection, key: str, run: sqlite3.Row) -> None:
        """표본 범위: 실행 전 고정 유니버스와 제외 종목·사유·비율을 코호트에 연결(같은 트랜잭션, 불변)."""
        rows = conn.execute("SELECT ticker, processing_status, payload_json FROM run_signals WHERE run_id=?",
                            (run['run_id'],)).fetchall()
        count = lambda st: sum(r['processing_status'] == st for r in rows)
        excluded = [r for r in rows if r['processing_status'] in EXCLUDED_STATUSES]
        conn.execute("INSERT INTO cohort_scope VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (key, run['run_id'], EXCLUSION_POLICY, EXCLUSION_SCOPE, run['universe_size'], run['universe_hash'],
                      count('scored'), count('excluded_required_missing'), count('excluded_validation'),
                      count('excluded_rule'), len(excluded) / run['universe_size']))
        conn.executemany("INSERT INTO cohort_exclusions VALUES(?,?,?,?)",
                         [(key, r['ticker'], r['processing_status'],
                           json.loads(r['payload_json']).get('error') or r['processing_status']) for r in excluded])

    def _clock(self) -> datetime:
        return datetime.now(KST)

    def _record_commit_check(self, key: str, run_id: str, confirmed_at: datetime, signal_date: str) -> None:
        cutoff_at = datetime.combine(date.fromisoformat(signal_date), CONFIRM_DEADLINE, KST)   # 확정 마감 (§13.31)
        observed, detail = None, None
        try:
            observed = self._clock()
            _stamp(observed)
            if observed < confirmed_at:
                status, detail = "check_failed", "clock_before_confirmation"
            elif observed < cutoff_at:
                status = "verified_before_cutoff"
            else:
                status = "after_cutoff"
        except Exception as exc:
            status, detail = "check_failed", f"{type(exc).__name__}: {exc}"
            observed = None
        with self._transaction() as conn:
            conn.execute("INSERT INTO commit_checks VALUES(?,?,?,?,?,?,?)",
                         (key, run_id, _stamp(confirmed_at), _opt_stamp(observed) if observed else None,
                          _stamp(cutoff_at), status, detail))

    def official_primary_cohorts(self, engine_id: str, universe_rule: str) -> list[sqlite3.Row]:
        """사전등록 평가 조회: 공식 전략·primary·적격·사후 확인 성공(확정 마감 08:48 전 COMMIT)만."""
        with self._connection() as conn:
            return conn.execute("""SELECT c.* FROM official_cohorts c JOIN commit_checks k
                ON k.cohort_key=c.cohort_key AND k.status='verified_before_cutoff'
                WHERE c.oos_type='primary' AND c.eligible=1 AND c.strategy_id=?
                AND c.engine_id=? AND c.universe_rule_version=? ORDER BY c.signal_date""",
                (STRATEGY, engine_id, universe_rule)).fetchall()

    def append_legacy_decision(self, *, source: str, signal_date: str, ticker: str,
                               eligible: bool, reasons: list[str], now: datetime, evidence_ref: str) -> int:
        if not evidence_ref:
            raise ValueError("evidence_ref required")
        with self._transaction() as conn:
            return conn.execute("INSERT INTO legacy_eligibility(source,signal_date,ticker,eligible,reasons,decided_at,evidence_ref) VALUES(?,?,?,?,?,?,?)",
                (source,signal_date,ticker,int(eligible),_json(reasons),_stamp(now),evidence_ref)).lastrowid
