"""전송 전용 자식 프로세스 본체 (§13.31). transport_proc.ProcessTransport가 `python -m`으로 직접 실행한다.

부모의 주 모듈을 다시 import하지 않도록 multiprocessing을 쓰지 않는다(spawn은 부모 스크립트를 자식에서 재실행할 수 있음).
표준 입력·출력 = 길이(4바이트) + pickle 프레임. 출력 스트림은 프레임 전용 — print는 표준 오류로 돌린다.
DB에 쓰지 않는다. 인증 정보는 상속한 환경 변수로만, 메시지에 넣지 않는다.
"""
from __future__ import annotations

import collections
import importlib
import json
import os
import pickle
import struct
import sys

FORBIDDEN_PREFIXES = ("pykrx", "run")
FORBIDDEN_MODULES = ("analyzer.data.krx", "analyzer.data.naver")


def _read_exact(fd: int, n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        chunk = os.read(fd, n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        view = view[n:]


def main() -> None:
    for p in reversed(json.loads(os.environ.get("TRANSPORT_CHILD_SYSPATH", "[]"))):
        if p not in sys.path:
            sys.path.insert(0, p)
    factories = json.loads(sys.argv[1])
    fd_in, fd_out = sys.stdin.fileno(), os.dup(sys.stdout.fileno())
    sys.stdout = sys.stderr                                   # 프레임 출력과 섞이지 않게
    from analyzer.pipeline.collect import TransportError, TransportTimeout
    hosts = collections.Counter()
    try:
        import requests
        from urllib.parse import urlparse
        orig = requests.Session.request
        def counted(self, method, url, *a, **k):
            hosts[urlparse(url).hostname or "?"] += 1
            return orig(self, method, url, *a, **k)
        requests.Session.request = counted
    except ImportError:
        pass
    built = {}
    while True:
        head = _read_exact(fd_in, 4)
        if head is None:
            return
        body = _read_exact(fd_in, struct.unpack(">I", head)[0])
        if body is None:
            return
        msg = pickle.loads(body)
        if msg is None:
            return
        if msg == "stats":
            bad = sorted(m for m in sys.modules if m.split(".")[0] in FORBIDDEN_PREFIXES or m in FORBIDDEN_MODULES)
            _write_all(fd_out, _frame(("stats", dict(hosts), bad)))
            continue
        kind, target, request = msg
        try:
            if kind not in built:
                module, attr = factories[kind].split(":")
                built[kind] = getattr(importlib.import_module(module), attr)()
            r = built[kind].send(kind, target, request)
            out = ("ok", int(r.http_status), bytes(r.body), bool(r.session_invalid))
        except TransportTimeout as e:
            out = ("timeout", str(e))
        except TransportError as e:
            out = ("error", str(e))
        except Exception as e:                                # 메시지에 인증 정보가 섞일 수 있어 형식명만
            out = ("error", type(e).__name__)
        _write_all(fd_out, _frame(out))


def _frame(obj) -> bytes:
    data = pickle.dumps(obj, protocol=4)
    return struct.pack(">I", len(data)) + data


if __name__ == "__main__":
    main()
