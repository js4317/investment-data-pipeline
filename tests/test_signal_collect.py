"""5단계-3: 수집기의 재시도·마감·보존과 입력 생성. 가짜 전송과 시험용 DB만 사용 (실제 조회 없음)."""
from datetime import timedelta
import json

import pytest

from analyzer.pipeline import collect as C
from analyzer.pipeline.sources import reference_date_1y, request_03502, request_finder, request_open_api
from tests.test_signal_sources import (CAL, E, FINDER_ROWS, NOW, P03502, ROW, finish, krx_d, orow, q, start, store)  # noqa: F401


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s); self.t += timedelta(seconds=s)

    slept: list


class FakeTransport:
    """정해진 순서로 응답·예외를 돌려준다. 호출마다 시계를 step초 진행."""
    def __init__(self, clock, script, step=1.0):
        self.clock, self.script, self.step, self.calls = clock, list(script), step, []

    def send(self, kind, target, request):
        self.calls.append((kind, target, request))
        self.clock.t += timedelta(seconds=self.step)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok03502(raw="0.85"):
    return C.Reply(200, json.dumps({"output": [{"TRD_DD": krx_d(E), "TDD_CLSPRC": "61,000", "PBR": raw}]}).encode())


def collector(store, run, script, *, deadline_in=600, step=1.0):
    clock = Clock(NOW + timedelta(seconds=10)); clock.slept = []
    tr = FakeTransport(clock, script, step)
    col = C.Collector(store, run, tr, clock=clock, deadline=clock.t + timedelta(seconds=deadline_in), sleep=clock.sleep,
                      monotonic=lambda: clock.t.timestamp())
    return col, tr, clock


REQ = request_03502("KR7005930003", "STK", E)


def pbr(col, fetched):
    """03502 수집 결과 + 같은 실행에 저장한 종목 검색·Open API 응답(대조 근거)."""
    support = []
    for endpoint, req, payload in (
            ("krx.finder_stkisu", request_finder(), {"block1": FINDER_ROWS}),
            ("krxopen.stk_bydd_trd", request_open_api(E), {"OutBlock_1": [
                {"BAS_DD": E.strftime("%Y%m%d"), "ISU_CD": "005930", "TDD_CLSPRC": "61,000"}]})):
        r = col.store.record_response(col.run_id, endpoint=endpoint, request=req, fetched_at=NOW, http_status=200,
                                      body=json.dumps(payload).encode())
        support.append(C.Fetched(r, None, 1, NOW))
    return C.make_input("pbr", "krx03502_field_v2", P03502, E, CAL, list(fetched) + support)


def test_first_try_success_links_response(store):
    run = start(store)
    col, tr, clock = collector(store, run, [ok03502()])
    f = col.fetch("krx.MDCSTAT03502", REQ)
    assert (f.attempts, f.failure) == (1, None) and tr.calls == [("krx_web", "dbms/MDC/STAT/standard/MDCSTAT03502", REQ)]
    item = pbr(col, [f])
    assert (item.source_status, item.value) == ("verified", 0.85)
    assert finish(store, run, {"m": item}) == "completed" and store.audit_input_evidence(run) is None


def test_timeouts_then_success_backs_off(store):
    run = start(store)
    col, tr, clock = collector(store, run, [C.TransportTimeout(), C.TransportError(), ok03502()])
    f = col.fetch("krx.MDCSTAT03502", REQ)
    assert f.attempts == 3 and clock.slept == [1.0, 2.0]
    assert [x["outcome"] for x in col.log] == ["timeout", "error", "http_200"]
    assert q(store, "SELECT count(*) FROM source_responses") == [(1,)]
    assert pbr(col, [f]).source_status == "verified"


def test_three_timeouts_is_fetch_failure(store):
    run = start(store)
    col, tr, clock = collector(store, run, [C.TransportTimeout()] * 3)
    f = col.fetch("krx.MDCSTAT03502", REQ)
    assert (f.response, f.failure, f.attempts) == (None, "request_timeout", 3)
    item = pbr(col, [f])
    assert (item.source_status, item.failure_reason, item.value, item.missing_kind) == ("failed", "request_timeout", None, None)
    assert finish(store, run, {"m": item}) == "completed"      # 실패 기록은 유효. 적격성은 별도 판정


def test_server_errors_are_kept_and_last_reply_is_linked(store):
    run = start(store)
    col, tr, clock = collector(store, run, [C.Reply(503, b"busy"), C.Reply(500, b"err"), ok03502()])
    f = col.fetch("krx.MDCSTAT03502", REQ)
    assert q(store, "SELECT http_status FROM source_responses ORDER BY fetched_at") == [(503,), (500,), (200,)]
    item = pbr(col, [f])
    assert f.response.response_id in item.responses and item.source_status == "verified"
    assert finish(store, run, {"m": item}) == "completed"
    linked = {r for (r,) in q(store, "SELECT response_id FROM run_input_responses")}
    stored_03502 = {r for (r,) in q(store, "SELECT response_id FROM source_responses WHERE endpoint='krx.MDCSTAT03502'")}
    assert linked & stored_03502 == {f.response.response_id}            # 503·500 응답은 보존만, 연결 안 됨


def test_persistent_server_error_fails_by_definition(store):
    run = start(store)
    col, tr, clock = collector(store, run, [C.Reply(500, b"err")] * 3)
    item = pbr(col, [col.fetch("krx.MDCSTAT03502", REQ)])
    assert (item.source_status, item.failure_reason) == ("failed", "http_error")
    assert finish(store, run, {"m": item}) == "completed"


def test_client_error_is_not_retried(store):
    run = start(store)
    col, tr, clock = collector(store, run, [C.Reply(404, b"no")])
    f = col.fetch("krx.MDCSTAT03502", REQ)
    assert f.attempts == 1 and len(tr.calls) == 1 and clock.slept == []


def test_deadline_blocks_new_requests(store):
    run = start(store)
    col, tr, clock = collector(store, run, [ok03502()], deadline_in=4)       # 남은 시간 < MIN_ATTEMPT(5초)
    f = col.fetch("krx.MDCSTAT03502", REQ)
    assert tr.calls == [] and (f.response, f.failure) == (None, "deadline_exhausted")
    assert pbr(col, [f]).failure_reason == "deadline_exhausted"


def test_deadline_stops_retries(store):
    run = start(store)
    col, tr, clock = collector(store, run, [C.TransportTimeout()] * 3, deadline_in=34, step=30)  # 재시도 대기 예산 없음
    f = col.fetch("krx.MDCSTAT03502", REQ)
    assert len(tr.calls) == 1 and f.failure == "deadline_exhausted"


def test_partial_period_inputs_fail_with_linked_evidence(store):
    """희석률: E 응답은 받았고 1년 전 응답은 시간 초과 → 조회 실패 사유로 기록, 응답 연결 없음."""
    run = start(store)
    ref = reference_date_1y(E)
    good = C.Reply(200, json.dumps({"OutBlock_1": [orow(E, "005930", shrs="110")]}).encode())
    col, tr, clock = collector(store, run, [good] + [C.TransportTimeout()] * 3)
    fe = col.fetch("krxopen.stk_bydd_trd", request_open_api(E))
    fr = col.fetch("krxopen.stk_bydd_trd", request_open_api(ref))
    item = C.make_input("dilution", "dilution_v2_openapi_prev_session", {"ticker": "005930"}, E, CAL, [fe, fr])
    assert (item.source_status, item.failure_reason, item.responses) == ("failed", "request_timeout", ())
    assert q(store, "SELECT count(*) FROM source_responses") == [(1,)]      # 받은 응답은 보존됨
    assert finish(store, run, {"d": item}) == "completed" and store.audit_input_evidence(run) is None


def test_open_api_transport_returns_raw_bytes_and_maps_errors():
    import requests
    from analyzer.pipeline.transport import OpenApiTransport

    class Sess:
        def __init__(self, outcome): self.outcome, self.seen = outcome, None
        def post(self, url, headers, json, timeout):
            self.seen = (url, headers, json, timeout)
            if isinstance(self.outcome, Exception):
                raise self.outcome
            r = requests.Response(); r.status_code = 200; r._content = self.outcome
            return r

    s = Sess(b'{"OutBlock_1":[]}')
    reply = OpenApiTransport(s, key="k").send("open_api", "https://x", {"basDd": "20261013"})
    assert reply == C.Reply(200, b'{"OutBlock_1":[]}') and s.seen[2] == {"basDd": "20261013"} and s.seen[3] == (10, 60)
    with pytest.raises(C.TransportTimeout):
        OpenApiTransport(Sess(requests.ReadTimeout("t")), key="k").send("open_api", "https://x", {})
    with pytest.raises(C.TransportError) as e:
        OpenApiTransport(Sess(requests.ConnectionError("secret-url?key=abc")), key="k").send("open_api", "https://x", {})
    assert "abc" not in str(e.value)
    with pytest.raises(ValueError):
        OpenApiTransport(Sess(b""), key="").send("open_api", "https://x", {})


class FakeKrxSession:
    """로그인 응답 코드와 데이터 응답 본문을 순서대로 돌려주는 가짜 세션."""
    def __init__(self, codes, bodies):
        self.codes, self.bodies, self.posts, self.gets = list(codes), bodies, [], 0

    def get(self, url, headers, timeout):
        self.gets += 1

    def post(self, url, data, headers, timeout):
        import requests
        self.posts.append((url, dict(data)))
        r = requests.Response(); r.status_code = 200
        if "MDCCOMS001D1" in url:
            r._content = json.dumps({"_error_code": self.codes.pop(0)}).encode()
        else:
            r._content = self.bodies.pop(0)
        return r


def krx_transport(codes, bodies, clock=None):
    from analyzer.pipeline.transport import KrxWebTransport
    made = []
    def factory():
        made.append(FakeKrxSession(codes, bodies)); return made[-1]
    return KrxWebTransport(factory, login_id="id", login_pw="secret-pw", monotonic=clock or (lambda: 0.0)), made


def test_krx_web_login_flow_and_raw_body():
    t, made = krx_transport(["CD011", "CD001"], [b'{"output":[]}'])
    reply = t.send("krx_web", "dbms/MDC/STAT/standard/MDCSTAT03502", REQ)
    assert reply == C.Reply(200, b'{"output":[]}', False)
    s = made[0]
    assert s.gets == 2 and s.posts[1][1].get("skipDup") == "Y"
    assert s.posts[2] == ("https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
                          {"bld": "dbms/MDC/STAT/standard/MDCSTAT03502", **REQ})


def test_krx_web_login_failure_hides_credentials():
    t, _ = krx_transport(["CD010"], [])
    with pytest.raises(C.TransportError) as e:
        t.send("krx_web", "b", REQ)
    assert "CD010" in str(e.value) and "secret-pw" not in str(e.value) and "id" != str(e.value)


def test_krx_web_invalid_session_is_kept_and_retried(store):
    """JSON이 아닌 200 응답: 본문은 보존하고, 재로그인 후 재시도해 정상 응답을 연결."""
    t, made = krx_transport(["CD001", "CD001"], [b"LOGOUT", ok03502().body])
    run = start(store)
    clock = Clock(NOW + timedelta(seconds=10)); clock.slept = []
    col = C.Collector(store, run, t, clock=clock, deadline=clock.t + timedelta(seconds=600), sleep=clock.sleep,
                      monotonic=lambda: clock.t.timestamp())
    f = col.fetch("krx.MDCSTAT03502", REQ)
    assert len(made) == 2 and f.attempts == 2
    assert q(store, "SELECT http_status,decode_error FROM source_responses ORDER BY rowid") == [(200, "invalid_json"), (200, None)]
    assert pbr(col, [f]).source_status == "verified"


def test_krx_web_relogins_before_session_expiry():
    now = [0.0]
    t, made = krx_transport(["CD001", "CD001"], [b'{"output":[]}', b'{"output":[]}'], clock=lambda: now[0])
    t.send("krx_web", "b", REQ)
    now[0] = 50 * 60 + 1
    t.send("krx_web", "b", REQ)
    assert len(made) == 2 and t.logins == 2
