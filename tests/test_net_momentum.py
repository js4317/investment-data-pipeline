"""
v8.6.7 — 네트워크 기본 timeout(analyzer/net.py)과 모멘텀 배치 미완료 처리 테스트.
외부 네트워크 없이 로컬 소켓 서버만 사용한다.
"""
from __future__ import annotations

import logging
import socket
import subprocess
import sys
import textwrap
import threading
import time
from datetime import date
from pathlib import Path

import pytest
import requests
from requests.adapters import HTTPAdapter

from analyzer import net
from analyzer.data import momentum

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# 로컬 서버
# ---------------------------------------------------------------------------

class _Server:
    """mode='silent': 연결만 받고 응답 없음 / mode='trickle': 헤더 후 1바이트씩 천천히."""

    def __init__(self, mode: str, trickle_bytes: int = 8, trickle_interval: float = 0.3):
        self.mode = mode
        self.trickle_bytes = trickle_bytes
        self.trickle_interval = trickle_interval
        self.stop = threading.Event()
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.conns: list[socket.socket] = []
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def _run(self):
        while not self.stop.is_set():
            try:
                c, _ = self.sock.accept()
            except OSError:
                continue
            self.conns.append(c)
            if self.mode == "trickle":
                threading.Thread(target=self._trickle, args=(c,), daemon=True).start()

    def _trickle(self, c):
        try:
            c.recv(4096)
            c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % self.trickle_bytes)
            for _ in range(self.trickle_bytes):
                if self.stop.is_set():
                    return
                time.sleep(self.trickle_interval)
                c.sendall(b"x")
        except OSError:
            pass

    def close(self):
        self.stop.set()
        for c in self.conns:
            try:
                c.close()
            except OSError:
                pass
        self.sock.close()


@pytest.fixture
def short_default(monkeypatch):
    net.install_default_http_timeout()
    monkeypatch.setattr(net, "DEFAULT_TIMEOUT", (1.0, 1.0))


class _CaptureAdapter(HTTPAdapter):
    def __init__(self):
        super().__init__()
        self.timeouts = []

    def send(self, request, **kwargs):
        self.timeouts.append(kwargs.get("timeout"))
        r = requests.Response()
        r.status_code = 200
        r._content = b""
        r.request = request
        return r


# ---------------------------------------------------------------------------
# analyzer/net.py
# ---------------------------------------------------------------------------

class TestDefaultTimeout:

    def test_install_idempotent(self):
        net.install_default_http_timeout()
        first = requests.Session.request
        assert net.install_default_http_timeout() is False
        assert requests.Session.request is first
        assert not getattr(first._original, net._MARK, False)   # 이중 래핑 없음

    def test_nonresponding_server_times_out(self, short_default):
        srv = _Server("silent")
        try:
            t0 = time.monotonic()
            with pytest.raises(requests.exceptions.ReadTimeout):
                requests.get(srv.url)
            assert time.monotonic() - t0 < 5
        finally:
            srv.close()

    def test_explicit_timeout_preserved_and_none_counted(self, short_default):
        s = requests.Session()
        ad = _CaptureAdapter()
        s.mount("http://", ad)
        before = net.timeout_stats()
        s.get("http://example.invalid/", timeout=5)
        s.get("http://example.invalid/", timeout=None)
        s.get("http://example.invalid/")
        s.request("GET", "http://example.invalid/", None, None, None, None, None, None, 7)  # 위치 인자 timeout
        after = net.timeout_stats()
        assert ad.timeouts[0] == 5
        assert ad.timeouts[1] is None                     # 무제한 명시는 바꾸지 않음
        assert ad.timeouts[2] == (1.0, 1.0)               # 미지정만 기본값
        assert ad.timeouts[3] == 7
        assert after["explicit"] - before["explicit"] == 2
        assert after["explicit_none"] - before["explicit_none"] == 1
        assert after["defaulted"] - before["defaulted"] == 1

    def test_trickle_server_exceeds_read_timeout(self, short_default):
        """읽기 timeout은 총시간 한도가 아님을 확인 (조금씩 오는 응답은 끊기지 않음)."""
        srv = _Server("trickle", trickle_bytes=8, trickle_interval=0.3)
        try:
            t0 = time.monotonic()
            r = requests.get(srv.url)
            elapsed = time.monotonic() - t0
            assert r.content == b"x" * 8
            assert elapsed > 2.0          # read timeout(1초)보다 훨씬 오래 걸림
        finally:
            srv.close()


# ---------------------------------------------------------------------------
# 모멘텀 배치
# ---------------------------------------------------------------------------

class TestMomentumBatch:

    def test_running_timed_out_and_pending_cancelled(self, monkeypatch):
        gate = threading.Event()

        def fake(ticker, as_of, vscore=None):
            if ticker == "A":
                gate.wait(5)
            return momentum._UNAVAILABLE

        monkeypatch.setattr(momentum, "fetch_momentum", fake)
        try:
            res, rep = momentum.batch_fetch_momentum_report(
                [("A", None), ("B", None), ("C", None)], date(2026, 10, 14),
                max_workers=1, timeout_sec=0.3)
            assert rep.timed_out == ["A"]
            assert sorted(rep.cancelled) == ["B", "C"]
            assert res == {}
            assert rep.incomplete
        finally:
            gate.set()

    def test_request_failed_classification(self, monkeypatch):
        def fake(ticker, as_of, vscore=None):
            if ticker == "ERR_INSIDE":
                momentum._note_fetch_error(ConnectionError("reset"))
                return momentum._UNAVAILABLE
            if ticker == "RAISE":
                raise RuntimeError("boom")
            return momentum._UNAVAILABLE

        monkeypatch.setattr(momentum, "fetch_momentum", fake)
        res, rep = momentum.batch_fetch_momentum_report(
            [("OK", None), ("ERR_INSIDE", None), ("RAISE", None)], date(2026, 10, 14),
            max_workers=3, timeout_sec=5)
        assert set(res) == {"OK", "ERR_INSIDE"}          # 내부 오류는 기존처럼 UNAVAILABLE 반환
        assert "ConnectionError" in rep.request_failed["ERR_INSIDE"]
        assert "RuntimeError" in rep.request_failed["RAISE"]
        assert "OK" not in rep.request_failed
        assert not rep.timed_out and not rep.cancelled

    def test_wrapper_keeps_return_type_and_warns(self, monkeypatch, caplog):
        def fake(ticker, as_of, vscore=None):
            raise RuntimeError("boom")

        monkeypatch.setattr(momentum, "fetch_momentum", fake)
        with caplog.at_level(logging.WARNING, logger="analyzer.data.momentum"):
            res = momentum.batch_fetch_momentum([("X", None)], date(2026, 10, 14))
        assert res == {}
        assert any("모멘텀 배치 미완료" in r.getMessage() for r in caplog.records)

    def test_batch_timeout_then_process_exits(self, tmp_path):
        """배치 제한 후에도 실행 중 요청이 기본 timeout으로 끝나 프로세스가 스스로 종료된다."""
        child = tmp_path / "child.py"
        child.write_text(textwrap.dedent(f"""
            import sys, socket, threading
            sys.path.insert(0, {str(PROJECT_ROOT)!r})
            from analyzer import net
            net.install_default_http_timeout()
            net.DEFAULT_TIMEOUT = (1.0, 1.0)
            import requests
            from datetime import date
            from analyzer.data import momentum

            s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(8)
            port = s.getsockname()[1]
            def accept():
                conns = []
                while True:
                    c, _ = s.accept(); conns.append(c)
            threading.Thread(target=accept, daemon=True).start()

            def fake(ticker, as_of, vscore=None):
                requests.get(f"http://127.0.0.1:{{port}}/")
            momentum.fetch_momentum = fake
            res, rep = momentum.batch_fetch_momentum_report(
                [("A", None), ("B", None), ("C", None)], date(2026, 10, 14),
                max_workers=2, timeout_sec=0.3)
            print("timed_out", len(rep.timed_out), "cancelled", len(rep.cancelled), flush=True)
        """))
        t0 = time.monotonic()
        p = subprocess.run([sys.executable, "-B", str(child)], capture_output=True, text=True, timeout=30)
        elapsed = time.monotonic() - t0
        assert p.returncode == 0, p.stderr
        assert "timed_out 2 cancelled 1" in p.stdout
        assert elapsed < 15
