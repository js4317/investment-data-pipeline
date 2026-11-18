"""5단계-5 보완 (§13.31): 전송 전용 자식 프로세스 + 부모의 단조 시계 감독.

- 자식(`python -m analyzer.pipeline.transport_child`)이 실제 전송(KRX 웹 로그인 세션·Open API·DART)을 유지하며 요청한다.
  로그인·DNS·응답 수신은 모두 자식 안에서 일어난다. multiprocessing을 쓰지 않는다 — spawn은 부모 스크립트를 자식에서
  다시 실행할 수 있어서, 자식은 독립 모듈로 직접 실행한다.
- 부모(send_within)는 요청 프레임을 보내고 limit초(단조 시계) 안에 응답 프레임이 끝까지 오지 않으면 그 자식만 종료하고
  CollectionAborted를 낸다 → 수집기가 이 실행의 수집을 중단한다. 종료 대상은 이 객체가 만든 자식(Popen) 하나뿐이다.
- 자식은 DB에 쓰지 않는다. 저장은 부모(수집기)만, 완성된 응답만. 늦거나 불완전한 프레임은 버린다.
- 인증 정보는 환경 변수로만(자식이 상속), 메시지·기록에 넣지 않는다.
- 자식의 HTTP 호스트 수·금지 모듈은 stats()로 확인한다(Naver 0건·pykrx 미로드 확인용).
"""
from __future__ import annotations

import json
import os
import pickle
import select
import struct
import subprocess
import sys
import time

from analyzer.pipeline.collect import CollectionAborted, Reply, TransportError, TransportTimeout

REAL_FACTORIES = {"krx_web": "analyzer.pipeline.transport:KrxWebTransport",
                  "open_api": "analyzer.pipeline.transport:OpenApiTransport",
                  "dart": "analyzer.pipeline.transport:DartTransport"}


class _Partial(Exception):
    pass


class ProcessTransport:
    """BoundedTransport 구현. factories: {kind: "module:attr"} — 자식에서 import해 인자 없이 생성한다."""

    def __init__(self, factories: dict | None = None, *, monotonic=time.monotonic, kill_grace: float = 2.0):
        self.factories = dict(factories or REAL_FACTORIES)
        self.monotonic, self.kill_grace = monotonic, kill_grace
        self.proc: subprocess.Popen | None = None
        self.events: list[dict] = []
        self.closed = False

    def _start(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # 프로젝트 루트
        paths = [root] + [p for p in sys.path if p and p != root]
        env = dict(os.environ, TRANSPORT_CHILD_SYSPATH=json.dumps(paths),
                   PYTHONPATH=os.pathsep.join([root] + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]))
        self.proc = subprocess.Popen([sys.executable, "-B", "-m", "analyzer.pipeline.transport_child",
                                      json.dumps(self.factories)],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env, bufsize=0)
        self.events.append(dict(event="start", pid=self.proc.pid))

    def _kill(self, reason: str):
        p, self.proc = self.proc, None
        if p is None:
            return
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(self.kill_grace)
            except subprocess.TimeoutExpired:
                p.kill(); p.wait(self.kill_grace)
        for f in (p.stdin, p.stdout):
            try:
                f.close()
            except OSError:
                pass
        self.events.append(dict(event="kill", pid=p.pid, reason=reason, returncode=p.returncode,
                                alive=p.poll() is None))

    def _send_frame(self, obj):
        data = pickle.dumps(obj, protocol=4)
        self.proc.stdin.write(struct.pack(">I", len(data)) + data)
        self.proc.stdin.flush()

    def _read_frame(self, until: float):
        """until(단조 시각)까지 프레임 하나를 끝까지 읽는다. 시간 초과면 None(일부만 왔어도 버림)."""
        fd = self.proc.stdout.fileno()
        buf, need = b"", 4
        header = True
        while True:
            left = until - self.monotonic()
            if left <= 0:
                return None
            ready, _, _ = select.select([fd], [], [], left)
            if not ready:
                return None
            chunk = os.read(fd, need - len(buf))
            if not chunk:
                raise EOFError
            buf += chunk
            if len(buf) == need:
                if header:
                    header, need, buf = False, struct.unpack(">I", buf)[0], b""
                    if need == 0:
                        raise EOFError
                else:
                    return pickle.loads(buf)

    def send_within(self, kind: str, target: str, request: dict, limit: float) -> Reply:
        if self.closed:
            raise CollectionAborted("transport_closed")
        if self.proc is None:
            self._start()
        until = self.monotonic() + max(0.0, limit)
        try:
            self._send_frame((kind, target, request))
            msg = self._read_frame(until)
        except (EOFError, OSError, BrokenPipeError, pickle.UnpicklingError):
            self._kill("transport_process_died"); self.closed = True
            raise CollectionAborted("transport_process_died") from None
        if msg is None:                                      # 상한 초과: 응답(일부 포함) 버리고 자식 종료
            self._kill(f"attempt_limit_exceeded {limit:.1f}s"); self.closed = True
            raise CollectionAborted(f"attempt_limit_exceeded {limit:.1f}s")
        tag = msg[0]
        if tag == "ok":
            return Reply(msg[1], msg[2], session_invalid=msg[3])
        if tag == "timeout":
            raise TransportTimeout(msg[1])
        raise TransportError(msg[1])

    def stats(self, limit: float = 5.0):
        """자식의 HTTP 호스트 수·금지 모듈. 자식이 없거나 응답이 없으면 None."""
        if self.proc is None:
            return None
        try:
            self._send_frame("stats")
            msg = self._read_frame(self.monotonic() + limit)
        except (EOFError, OSError, BrokenPipeError):
            return None
        if msg is None:
            return None
        return {"hosts": msg[1], "forbidden_modules": msg[2]}

    def close(self):
        """정상 종료 요청 → 짧게 기다린 뒤 남아 있으면 종료. 이 객체가 만든 자식만 다룬다."""
        self.closed = True
        p = self.proc
        if p is None:
            return
        try:
            self._send_frame(None)
            p.stdin.close()
            p.wait(self.kill_grace)
        except (OSError, BrokenPipeError, subprocess.TimeoutExpired):
            pass
        if p.poll() is None:
            self._kill("close_timeout")
            return
        try:
            p.stdout.close()
        except OSError:
            pass
        self.proc = None
        self.events.append(dict(event="exit", pid=p.pid, returncode=p.returncode))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
