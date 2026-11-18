"""5단계-3: 실제 전송. 원 응답 본문(bytes)을 그대로 돌려준다 — 가공·변환 없음.

API 키는 환경 변수로만 받는다(CLAUDE.md §11). 키 값은 기록·출력하지 않는다.
KRX 웹(data.krx.co.kr) 전송은 로그인 세션 처리 방식 확인 후 추가한다(미구현).
"""
from __future__ import annotations

import os

import requests

from analyzer.pipeline.collect import OPEN_API, Reply, TransportError, TransportTimeout

TIMEOUT = (10, 60)   # analyzer/net.py 기본값과 같음


class OpenApiTransport:
    """KRX Open API (data-dbg.krx.co.kr). AUTH_KEY 헤더, JSON 본문."""

    def __init__(self, session: requests.Session | None = None, key: str | None = None):
        self.session = session or requests.Session()
        self.key = key if key is not None else os.environ.get("KRX_OPEN_API_KEY", "")
        if not self.key:
            raise ValueError("KRX_OPEN_API_KEY not set")

    def send(self, kind: str, target: str, request: dict) -> Reply:
        if kind != OPEN_API:
            raise ValueError("open api transport only")
        try:
            r = self.session.post(target, headers={"AUTH_KEY": self.key, "Content-Type": "application/json"},
                                  json=request, timeout=TIMEOUT)
        except requests.Timeout as e:
            raise TransportTimeout(type(e).__name__) from None
        except requests.RequestException as e:
            raise TransportError(type(e).__name__) from None
        return Reply(r.status_code, r.content)


KRX_DATA_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
LOGIN_PAGE = "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001.cmd"
LOGIN_JSP = "https://data.krx.co.kr/contents/MDC/COMS/client/view/login.jsp?site=mdc"
LOGIN_URL = "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001D1.cmd"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
REFERER = "https://data.krx.co.kr/contents/MDC/MDI/outerLoader/index.cmd"
SESSION_MAX_AGE = 50 * 60     # KRX 세션 1시간 만료 — 여유를 두고 재로그인


class KrxWebTransport:
    """KRX 정보데이터시스템(data.krx.co.kr) getJsonData. 로그인 흐름은 pykrx 1.2.8 auth.py와 같다.

    pykrx를 import하지 않는다(import 시 자동 로그인·전역 세션). 자격 증명은 환경 변수 KRX_ID·KRX_PW에서만,
    출력·기록하지 않는다. 요청은 한 번에 하나, 기간 분할 없음(요청 범위는 호출자가 1년 남짓으로 제한).
    """

    def __init__(self, session_factory=requests.Session, login_id: str | None = None,
                 login_pw: str | None = None, monotonic=None):
        import time
        self.factory = session_factory
        self.login_id = login_id if login_id is not None else os.environ.get("KRX_ID", "")
        self.login_pw = login_pw if login_pw is not None else os.environ.get("KRX_PW", "")
        if not (self.login_id and self.login_pw):
            raise ValueError("KRX_ID/KRX_PW not set")
        self.monotonic = monotonic or time.monotonic
        self.session, self.logged_in_at, self.logins = None, None, 0

    def _login(self) -> None:
        s = self.factory()
        try:
            s.get(LOGIN_PAGE, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
            s.get(LOGIN_JSP, headers={"User-Agent": USER_AGENT, "Referer": LOGIN_PAGE}, timeout=TIMEOUT)
            payload = {"mbrNm": "", "telNo": "", "di": "", "certType": "",
                       "mbrId": self.login_id, "pw": self.login_pw}
            headers = {"User-Agent": USER_AGENT, "Referer": LOGIN_PAGE}
            code = s.post(LOGIN_URL, data=payload, headers=headers, timeout=TIMEOUT).json().get("_error_code", "")
            if code == "CD011":                                   # 중복 로그인
                payload["skipDup"] = "Y"
                code = s.post(LOGIN_URL, data=payload, headers=headers, timeout=TIMEOUT).json().get("_error_code", "")
        except requests.Timeout as e:
            raise TransportTimeout("login " + type(e).__name__) from None
        except (requests.RequestException, ValueError) as e:
            raise TransportError("login " + type(e).__name__) from None
        self.logins += 1
        if code != "CD001":
            raise TransportError(f"login_failed {code}")          # CD010: 비밀번호 변경 필요 등
        self.session, self.logged_in_at = s, self.monotonic()

    def send(self, kind: str, target: str, request: dict) -> Reply:
        from analyzer.pipeline.collect import KRX_WEB
        if kind != KRX_WEB:
            raise ValueError("krx web transport only")
        if self.session is None or self.monotonic() - self.logged_in_at > SESSION_MAX_AGE:
            self._login()
        try:
            r = self.session.post(KRX_DATA_URL, data={"bld": target, **request},
                                  headers={"User-Agent": USER_AGENT, "Referer": REFERER,
                                           "X-Requested-With": "XMLHttpRequest"}, timeout=TIMEOUT)
        except requests.Timeout as e:
            raise TransportTimeout(type(e).__name__) from None
        except requests.RequestException as e:
            raise TransportError(type(e).__name__) from None
        invalid = False
        if r.status_code == 200:
            try:
                invalid = not isinstance(r.json(), dict)
            except ValueError:
                invalid = True                                     # 로그아웃 안내 등 JSON 아님
            if invalid:
                self.session = None                                # 다음 시도 전에 재로그인
        return Reply(r.status_code, r.content, session_invalid=invalid)


class DartTransport:
    """OpenDART (opendart.fss.or.kr). GET, 인증키(crtfc_key)는 여기서만 붙인다 — 기록되는 요청에는 포함되지 않는다.
    키는 환경 변수 DART_API_KEY에서만 읽고 출력·기록하지 않는다. 오류 메시지에 URL(키 포함)을 넣지 않는다."""

    def __init__(self, session: requests.Session | None = None, key: str | None = None):
        self.session = session or requests.Session()
        self.key = key if key is not None else os.environ.get("DART_API_KEY", "")
        if not self.key:
            raise ValueError("DART_API_KEY not set")

    def send(self, kind: str, target: str, request: dict) -> Reply:
        from analyzer.pipeline.collect import DART
        if kind != DART:
            raise ValueError("dart transport only")
        if "crtfc_key" in request:
            raise ValueError("credential must not be part of the recorded request")
        try:
            r = self.session.get(target, params={**request, "crtfc_key": self.key}, timeout=TIMEOUT)
        except requests.Timeout as e:
            raise TransportTimeout(type(e).__name__) from None
        except requests.RequestException as e:
            raise TransportError(type(e).__name__) from None
        return Reply(r.status_code, r.content)
