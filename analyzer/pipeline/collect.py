"""5단계-3: 공식 원천 수집기. 원 응답을 그대로 받아 실행에 보존하고, 정의(sources)로 채점 입력을 만든다.

설계: docs/design/signal_pipeline.md §13.3(재시도·차단)·§13.5(보존)·§13.14(실행 전체 마감).
- 요청은 sources의 요청 생성 함수로만 만든다(파서 검증과 같은 조건).
- 받은 응답은 상태 코드와 무관하게 모두 보존한다. 입력에는 마지막 응답만 연결한다.
- 대체 원천 없음. 실패는 실패 사유로 남긴다.
- 전송(transport)은 주입한다. 이 모듈은 네트워크를 직접 열지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import time
from typing import Callable, Protocol

from analyzer.pipeline import sources as S

KRX_WEB = "krx_web"
OPEN_API = "open_api"
DART = "dart"
ENDPOINT_SPECS = {
    "krx.MDCSTAT03502": (KRX_WEB, "dbms/MDC/STAT/standard/MDCSTAT03502"),
    "krx.MDCSTAT01701": (KRX_WEB, "dbms/MDC/STAT/standard/MDCSTAT01701"),
    "krx.MDCSTAT00301": (KRX_WEB, "dbms/MDC/STAT/standard/MDCSTAT00301"),
    "krx.finder_stkisu": (KRX_WEB, "dbms/comm/finder/finder_stkisu"),
    "dart.list": (DART, "https://opendart.fss.or.kr/api/list.json"),
    "dart.fnltt": (DART, "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"),
    "krxopen.stk_bydd_trd": (OPEN_API, "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"),
    "krxopen.ksq_bydd_trd": (OPEN_API, "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd"),
}
MAX_ATTEMPTS = 3
BACKOFF = (1.0, 2.0)                       # 1·2번째 실패 후 대기(초)
REQUEST_BUDGET = timedelta(seconds=75)     # 시도 1회의 전체 상한(로그인·DNS·수신 포함, §13.31) — 시한 전송에서 강제
MIN_ATTEMPT = 5.0                          # 남은 시간이 이보다 적으면 새 시도를 시작하지 않는다(초)
RETRY_STATUS = {429, 500, 502, 503, 504}


class TransportTimeout(Exception):
    pass


class TransportError(Exception):
    pass


class CollectionAborted(Exception):
    """시한 전송이 시도 상한을 넘겨 전송 프로세스를 종료함 → 이 실행의 수집 중단(§13.31)."""


@dataclass(frozen=True)
class Reply:
    http_status: int
    body: bytes
    session_invalid: bool = False    # 전송 계층이 판단한 로그인 세션 무효(본문은 그대로 보존 후 재시도)


class Transport(Protocol):
    def send(self, kind: str, target: str, request: dict) -> Reply: ...


class BoundedTransport(Protocol):
    """시도 전체(로그인·DNS·수신)를 limit초 안에 끝내거나 CollectionAborted. 공식 경로는 이 형태만 쓴다."""
    def send_within(self, kind: str, target: str, request: dict, limit: float) -> Reply: ...


@dataclass(frozen=True)
class Fetched:
    response: S.Response | None          # 입력에 연결할 마지막 응답
    failure: str | None                  # 응답이 없을 때만: request_timeout·request_error·deadline_exhausted·collection_aborted
    attempts: int
    attempted_at: datetime


class Collector:
    """한 실행의 수집. clock은 KST aware datetime(기록용), 마감 감독은 단조 시계(monotonic), sleep은 초 단위.
    - 시도마다 남은 시간 = 마감 − 지금(단조). MIN_ATTEMPT 미만이면 새 시도를 시작하지 않는다.
    - 시한 전송(send_within)이면 시도 상한 = min(REQUEST_BUDGET, 남은 시간) — 넘으면 전송이 자식을 종료하고
      CollectionAborted → 이후 모든 조회는 collection_aborted(수집 중단). 늦은 응답은 저장되지 않는다.
    - 재시도 대기·요청 간 대기도 같은 마감 예산 안에서만(wait)."""

    def __init__(self, store, run_id: str, transport, *, clock: Callable[[], datetime],
                 deadline: datetime, sleep: Callable[[float], None], monotonic: Callable[[], float] = time.monotonic):
        self.store, self.run_id, self.transport = store, run_id, transport
        self.clock, self.deadline, self.sleep, self.monotonic = clock, deadline, sleep, monotonic
        self._mono_deadline = monotonic() + (deadline - clock()).total_seconds()
        self.aborted: str | None = None
        self.log: list[dict] = []

    def remaining(self) -> float:
        return self._mono_deadline - self.monotonic()

    def wait(self, seconds: float) -> bool:
        """마감 예산 안에서만 대기. 대기 뒤에도 새 시도를 시작할 시간이 남지 않으면 대기하지 않고 False."""
        if seconds <= 0:
            return True
        if self.aborted or self.remaining() - seconds < MIN_ATTEMPT:
            return False
        self.sleep(seconds)
        return True

    def _send(self, kind, target, request, limit):
        if hasattr(self.transport, "send_within"):
            return self.transport.send_within(kind, target, request, limit)
        return self.transport.send(kind, target, request)

    def fetch(self, endpoint: str, request: dict) -> Fetched:
        kind, target = ENDPOINT_SPECS[endpoint]
        first = self.clock()
        if self.aborted:
            self.log.append(dict(endpoint=endpoint, attempt=0, outcome="collection_aborted"))
            return Fetched(None, "collection_aborted", 0, first)
        last, failure = None, None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            rem = self.remaining()
            if rem < MIN_ATTEMPT:              # 새 요청 전 남은 시간 확인 (재시도 포함)
                if last is None:
                    failure = "deadline_exhausted"
                self.log.append(dict(endpoint=endpoint, attempt=attempt, outcome="deadline_exhausted"))
                break
            try:
                reply = self._send(kind, target, request, min(REQUEST_BUDGET.total_seconds(), rem))
            except CollectionAborted as e:
                self.aborted = str(e) or "attempt_limit_exceeded"
                self.log.append(dict(endpoint=endpoint, attempt=attempt, outcome="collection_aborted",
                                     detail=self.aborted))
                return Fetched(last, None if last else "collection_aborted", attempt, first)
            except TransportTimeout:
                failure, outcome = "request_timeout", "timeout"
            except TransportError:
                failure, outcome = "request_error", "error"
            else:
                last = self.store.record_response(self.run_id, endpoint=endpoint, request=request,
                                                  fetched_at=self.clock(), http_status=reply.http_status,
                                                  body=reply.body)
                failure, outcome = None, f"http_{reply.http_status}"
                self.log.append(dict(endpoint=endpoint, attempt=attempt, outcome=outcome,
                                     response_id=last.response_id))
                if reply.http_status not in RETRY_STATUS and not reply.session_invalid:
                    return Fetched(last, None, attempt, first)
                if attempt < MAX_ATTEMPTS and not self.wait(BACKOFF[attempt - 1]):
                    self.log.append(dict(endpoint=endpoint, attempt=attempt + 1, outcome="deadline_exhausted"))
                    break
                continue
            self.log.append(dict(endpoint=endpoint, attempt=attempt, outcome=outcome))
            if attempt < MAX_ATTEMPTS and not self.wait(BACKOFF[attempt - 1]):
                failure = "deadline_exhausted"               # 재시도할 예산이 없음(응답 없음)
                self.log.append(dict(endpoint=endpoint, attempt=attempt + 1, outcome="deadline_exhausted"))
                break
        # 마지막으로 받은 응답이 있으면 그것을 연결(정의가 http_error 등으로 판정), 없으면 조회 실패
        return Fetched(last, None if last else failure, attempt, first)


def make_input(name: str, definition_id: str, params: dict, session: date, calendar,
               fetched: list[Fetched]):
    """수집 결과 → 채점 입력(MarketInput).
    필요한 조회 중 하나라도 응답 없이 실패했으면 그 사유(첫 번째)로 실패 기록하고 응답을 연결하지 않는다
    (재파싱할 근거가 불완전하므로). 그 외에는 정의가 받은 응답 전체로 판정한다."""
    responses = [f.response for f in fetched if f.response is not None]
    attempted_at = min(f.attempted_at for f in fetched)
    if any(f.response is None for f in fetched):
        reason = next(f.failure for f in fetched if f.response is None)
        d = S.Derived("failed", None, None, reason, None, (), (), None)
        return S.to_market_input(name, definition_id, params, session, d, attempted_at)
    return S.to_market_input(name, definition_id, params, session,
                             S.derive(definition_id, responses, params, session, calendar), attempted_at)
