"""시한 전송 시험용 가짜 전송 (자식 프로세스에서 import·생성). 외부 네트워크 없음 — 지연은 sleep·로컬 서버로 만든다."""
import json
import os
import socket
import time

from analyzer.pipeline.collect import Reply, TransportError


class Fast:
    def send(self, kind, target, request):
        return Reply(200, json.dumps({"output": [], "echo": request}).encode())


class Hang:
    """응답이 오지 않는 전송 (부모가 상한에서 종료해야 함)."""
    def send(self, kind, target, request):
        time.sleep(30)
        return Reply(200, b'{"late": true}')


class Always503:
    def send(self, kind, target, request):
        return Reply(503, b'{"busy": true}')


class _SlowSession:
    """로그인 화면·로그인 POST·본 요청이 각각 STEP초 걸리는 세션."""
    STEP = float(os.environ.get("FAKE_STEP", "0.6"))

    class R:
        def __init__(self, body):
            self.content, self.status_code = body, 200

        def json(self):
            return json.loads(self.content)

    def get(self, url, **k):
        time.sleep(self.STEP); return self.R(b"<html></html>")

    def post(self, url, data=None, **k):
        time.sleep(self.STEP)
        return self.R(b'{"_error_code":"CD001"}' if "MDCCOMS001D1" in url else b'{"output":[]}')


def SlowRelogin():
    """실제 KrxWebTransport 코드 + 느린 세션: 첫 요청에서 로그인 3회 + 본 요청 1회."""
    from analyzer.pipeline.transport import KrxWebTransport
    return KrxWebTransport(session_factory=_SlowSession, login_id="x", login_pw="y")


class DnsHang:
    """이름 해석이 멈춘 경우: getaddrinfo가 오래 걸린 뒤 실패."""
    def send(self, kind, target, request):
        import requests
        real = socket.getaddrinfo
        def slow(*a, **k):
            time.sleep(30)
            raise socket.gaierror("fake dns failure")
        socket.getaddrinfo = slow
        try:
            requests.get("https://dns-hang.invalid/", timeout=(10, 60))
        except requests.RequestException as e:
            raise TransportError(type(e).__name__) from None
        finally:
            socket.getaddrinfo = real
        return Reply(200, b"{}")


class Trickle:
    """로컬 서버가 1바이트씩 천천히 보내는 응답 — 읽기 제한(소켓 읽기 단위)으로는 끊기지 않는다."""
    def send(self, kind, target, request):
        import requests
        port = os.environ["FAKE_TRICKLE_PORT"]
        r = requests.get(f"http://127.0.0.1:{port}/", timeout=(2, 2))
        return Reply(r.status_code, r.content)
