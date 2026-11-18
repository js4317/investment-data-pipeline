"""
HTTP 요청 기본 timeout.

requests 호출에 timeout 인자가 없으면 DEFAULT_TIMEOUT(연결 10초, 읽기 60초)을 넣는다.
- 명시적 timeout은 그대로 둔다. timeout=None(무제한 명시)도 바꾸지 않고 따로 집계한다.
- 읽기 timeout은 소켓 수신 대기 한도이지 요청 전체 시간 한도가 아니다.
  응답이 조금씩 계속 오거나 재시도가 반복되면 더 오래 걸릴 수 있으므로
  배치 제한·외부 감독 제한을 별도로 유지해야 한다.
- 실행 진입점에서 pykrx import 전에 호출한다. 중복 호출해도 한 번만 설치된다.
"""
from __future__ import annotations

import functools
import logging
import sys
import threading

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT: tuple[float, float] = (10.0, 60.0)   # (connect, read)

_lock = threading.Lock()
_stats = {"defaulted": 0, "explicit": 0, "explicit_none": 0}
_MARK = "_investment_default_timeout"


def install_default_http_timeout() -> bool:
    """설치했으면 True, 이미 설치돼 있으면 False."""
    import requests

    with _lock:
        current = requests.Session.request
        if getattr(current, _MARK, False):
            return False
        if "pykrx" in sys.modules:
            log.warning("install_default_http_timeout: pykrx가 이미 import된 뒤 설치됨")

        original = current

        @functools.wraps(original)
        def request(self, method, url, *args, **kwargs):
            # requests.Session.request(method, url, params, data, headers, cookies, files,
            #                          auth, timeout, ...) — url 뒤 위치 인자 중 timeout은 인덱스 6
            if len(args) >= 7:
                key = "explicit_none" if args[6] is None else "explicit"
            elif "timeout" not in kwargs:
                kwargs["timeout"] = DEFAULT_TIMEOUT
                key = "defaulted"
            else:
                key = "explicit_none" if kwargs["timeout"] is None else "explicit"
            with _lock:
                _stats[key] += 1
            if key == "explicit_none":
                log.debug("timeout=None 명시 요청: %s %s", method, url)
            return original(self, method, url, *args, **kwargs)

        setattr(request, _MARK, True)
        request._original = original  # type: ignore[attr-defined]
        requests.Session.request = request
        return True


def timeout_stats() -> dict[str, int]:
    with _lock:
        return dict(_stats)
