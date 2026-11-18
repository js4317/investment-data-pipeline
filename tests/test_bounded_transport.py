"""§13.31 마감 보장: 전송 자식 프로세스 + 부모의 단조 시계 감독. 외부 네트워크 없음(sleep·로컬 서버로 지연 주입).
재로그인·DNS 지연·조금씩 오는 응답·재시도 대기·저장 지연 → 수집 종료 ≤ 마감, 늦은 응답 미저장, 자식 정리,
확정 마감 초과 표본의 공식 평가 제외."""
from datetime import datetime, time as dtime, timedelta
import http.server
import os
import sqlite3
import threading
import time

import pytest

from analyzer.pipeline import collect as C
from analyzer.pipeline.transport_proc import ProcessTransport
from database.signal_store import COLLECT_DEADLINE, CONFIRM_DEADLINE, CONFIRM_RESERVE_SECONDS, KST, SignalStore
from tests.test_signal_sources import NOW, request_03502, E

REQ = request_03502("KR7005930003", "STK", E)
F = "tests.proc_fakes:"


class CountingStore:
    """record_response 호출만 센다(저장 = 부모만). delay: 저장 지연(초)."""
    def __init__(self, delay=0.0):
        self.saved, self.delay = [], delay

    def record_response(self, run_id, **k):
        time.sleep(self.delay)
        from analyzer.pipeline.sources import Response
        self.saved.append(k["endpoint"])
        return Response(f"r{len(self.saved)}", k["endpoint"], k["request"], k["fetched_at"], k["http_status"], (), None)


def collector(transport, seconds, store=None):
    clock = lambda: datetime.now(KST)
    col = C.Collector(store or CountingStore(), "run", transport, clock=clock,
                      deadline=clock() + timedelta(seconds=seconds), sleep=time.sleep, monotonic=time.monotonic)
    return col


def dead(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    # 좀비(부모가 회수 전)도 종료로 본다 — join으로 회수됨
    return False


@pytest.fixture
def short_budget(monkeypatch):
    monkeypatch.setattr(C, "REQUEST_BUDGET", timedelta(seconds=1.5))
    monkeypatch.setattr(C, "MIN_ATTEMPT", 0.2)


def test_fast_child_round_trip_and_clean_exit():
    with ProcessTransport({"krx_web": F + "Fast"}) as tr:
        col = collector(tr, 30)
        f = col.fetch("krx.MDCSTAT03502", REQ)
        assert f.response is not None and f.failure is None and col.aborted is None
        st = tr.stats()
        assert st["forbidden_modules"] == []
        pid = tr.events[0]["pid"]
    assert tr.events[-1]["event"] == "exit" and tr.events[-1]["returncode"] == 0 and dead(pid)


@pytest.mark.parametrize("fake", ["SlowRelogin", "DnsHang", "Hang"])
def test_overrun_kills_child_aborts_collection_and_stores_nothing(fake, short_budget, monkeypatch):
    """재로그인(0.6초 × 4) / DNS 정지 / 무응답: 시도 상한 1.5초에서 자식 종료, 이후 조회는 collection_aborted."""
    monkeypatch.setenv("FAKE_STEP", "0.6")
    store = CountingStore()
    with ProcessTransport({"krx_web": F + fake}) as tr:
        col = collector(tr, 20, store)
        t0 = time.monotonic()
        f = col.fetch("krx.MDCSTAT03502", REQ)
        took = time.monotonic() - t0
        assert (f.response, f.failure) == (None, "collection_aborted") and col.aborted.startswith("attempt_limit_exceeded")
        assert took < 1.5 + 3.0                               # 상한 + 종료 유예(terminate→join 2초 이내)
        assert store.saved == []                              # 늦은·불완전 응답 미저장
        kill = [e for e in tr.events if e["event"] == "kill"]
        assert len(kill) == 1 and kill[0]["alive"] is False and kill[0]["returncode"] is not None
        g = col.fetch("krx.MDCSTAT03502", REQ)                # 수집 중단: 새 자식을 만들지 않음
        assert g.failure == "collection_aborted" and len([e for e in tr.events if e["event"] == "start"]) == 1


def test_relogin_within_budget_succeeds(monkeypatch):
    """같은 재로그인 경로라도 상한 안이면 정상 (상한이 로그인을 포함해 계산됨을 대조)."""
    monkeypatch.setenv("FAKE_STEP", "0.1")
    with ProcessTransport({"krx_web": F + "SlowRelogin"}) as tr:
        f = collector(tr, 30).fetch("krx.MDCSTAT03502", REQ)
        assert f.response is not None


def test_trickle_response_is_cut_at_attempt_limit(short_budget):
    """로컬 서버가 0.3초마다 1바이트(읽기 제한 2초로는 끊기지 않음) — 상한 1.5초에서 종료, 저장 없음."""
    class Slow(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Length", "40"); self.end_headers()
            for _ in range(40):
                try:
                    self.wfile.write(b"x"); self.wfile.flush()
                except OSError:
                    return
                time.sleep(0.3)
        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.environ["FAKE_TRICKLE_PORT"] = str(srv.server_address[1])
    try:
        store = CountingStore()
        with ProcessTransport({"krx_web": F + "Trickle"}) as tr:
            col = collector(tr, 20, store)
            f = col.fetch("krx.MDCSTAT03502", REQ)
            assert f.failure == "collection_aborted" and store.saved == []
    finally:
        srv.shutdown(); os.environ.pop("FAKE_TRICKLE_PORT", None)


def test_retry_waits_stay_inside_deadline(monkeypatch):
    """503 반복: 재시도 대기(1·2초)도 마감 예산 안에서만 — 수집 종료 ≤ 마감."""
    monkeypatch.setattr(C, "MIN_ATTEMPT", 0.5)
    with ProcessTransport({"krx_web": F + "Always503"}) as tr:
        col = collector(tr, 2.0)
        t0 = time.monotonic()
        f = col.fetch("krx.MDCSTAT03502", REQ)
        assert time.monotonic() - t0 <= 2.0 and col.remaining() >= 0
        assert f.response is not None and f.response.http_status == 503      # 받은 응답은 연결(정의가 http_error로 판정)
        assert [e["outcome"] for e in col.log][-1] == "deadline_exhausted"


def test_storage_delay_cannot_start_requests_past_deadline(monkeypatch):
    """저장 지연(1.2초/건)으로 예산을 쓰면 다음 요청을 시작하지 않는다 — 수집 종료가 마감을 넘지 않음."""
    monkeypatch.setattr(C, "MIN_ATTEMPT", 0.5)
    store = CountingStore(delay=1.2)
    with ProcessTransport({"krx_web": F + "Fast"}) as tr:
        col = collector(tr, 3.0, store)
        results = [col.fetch("krx.MDCSTAT03502", REQ) for _ in range(5)]
        assert col.remaining() > -1.3                         # 마지막 저장 1건의 지연만큼만(새 시도는 시작하지 않음)
        assert results[-1].failure == "deadline_exhausted" and len(store.saved) <= 3


def test_official_deadline_constants():
    assert (COLLECT_DEADLINE, CONFIRM_DEADLINE, CONFIRM_RESERVE_SECONDS) == (dtime(8, 47), dtime(8, 48), 60)
    day = datetime(2026, 10, 26, tzinfo=KST)
    assert (datetime.combine(day.date(), CONFIRM_DEADLINE) - datetime.combine(day.date(), COLLECT_DEADLINE)
            ).total_seconds() >= CONFIRM_RESERVE_SECONDS


def test_official_inputs_requires_bounded_transport():
    from analyzer.pipeline.official_inputs import make_official_inputs
    class Plain:
        def send(self, *a):
            pass
    with pytest.raises(TypeError, match="bounded"):
        make_official_inputs(Plain(), corp_map={}, deadline=NOW, sleep=time.sleep)


def test_confirm_after_deadline_is_excluded_from_official_evaluation(tmp_path):
    """저장·재검증 지연으로 확정이 08:48:00을 넘으면 catchup + 사후 확인 after_cutoff → 공식 평가 조회에서 제외.
    사후 확인 시각을 얻지 못해도 제외(check_failed)."""
    from tests.test_signal_entry_calendar import START, calendar, save_calendar
    from tests.test_signal_stage4 import market, row, run as stage4_run, decide
    s = SignalStore(tmp_path / "late.db"); s.initialize(); s.register_engine("test", "hash", "c", START)
    save_calendar(s.path, calendar())
    with sqlite3.connect(s.path) as c:
        engine = c.execute("SELECT engine_id FROM official_engines").fetchone()[0]
    cases = [("on_time", START.replace(minute=47, second=50), START.replace(minute=47, second=55), "primary", "verified_before_cutoff"),
             ("late_confirm", START.replace(minute=48, second=5), START.replace(minute=48, second=6), "catchup", "after_cutoff"),
             ("late_commit_observed", START.replace(minute=47, second=59), START.replace(minute=48, second=1), "primary", "after_cutoff"),
             ("no_observation", START.replace(minute=47, second=50), None, "primary", "check_failed")]
    keys = {}
    for name, confirm_at, observed, oos, check in cases:
        r, _ = stage4_run(s, [row("000001")], {"m": market()}, {"000001": ["m"]}, universe=name)
        s._clock = (lambda o=observed: o) if observed else (lambda: (_ for _ in ()).throw(OSError("clock")))
        keys[name] = s.confirm(r, now=confirm_at, decision=decide(s, r, confirm_at))
        with sqlite3.connect(s.path) as c:
            assert c.execute("SELECT oos_type FROM official_cohorts WHERE cohort_key=?", (keys[name],)).fetchone()[0] == oos
            assert c.execute("SELECT status FROM commit_checks WHERE cohort_key=?", (keys[name],)).fetchone()[0] == check
    assert [k["universe_rule_version"] for k in s.official_primary_cohorts(engine, "on_time")] == ["on_time"]
    for name in ("late_confirm", "late_commit_observed", "no_observation"):
        assert s.official_primary_cohorts(engine, name) == []


def test_child_does_not_rerun_parent_script(tmp_path):
    """보호 장치: 자식은 독립 모듈로 실행 — 부모 스크립트(주 모듈 보호 없음)를 다시 실행하지 않는다."""
    import subprocess, sys
    from pathlib import Path
    marker = tmp_path / "ran.txt"
    script = tmp_path / "parent.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        f"open({str(marker)!r}, 'a').write('x')\n"
        "from analyzer.pipeline.transport_proc import ProcessTransport\n"
        "with ProcessTransport({'krx_web': 'tests.proc_fakes:Fast'}) as tr:\n"
        "    r = tr.send_within('krx_web', 't', {'a': 1}, 20)\n"
        "    assert r.http_status == 200\n")
    out = subprocess.run([sys.executable, "-B", str(script)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert marker.read_text() == "x"
