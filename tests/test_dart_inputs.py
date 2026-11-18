"""5단계-4 (3): DART 채점 입력 정의·신선도 — 고정 응답과 시험용 DB만 사용 (실제 조회 없음)."""
from dataclasses import replace
from datetime import date, datetime
import json

import pytest

from analyzer.pipeline.dart_inputs import freshness
from analyzer.pipeline.sources import Response, decode_meta, decode_rows, derive, request_dart_list, to_market_input
from database.signal_store import KST
from tests.test_dart_quarters import CODE, CORP, KEYS, filing, nodata, report, standard
from tests.test_signal_sources import CAL, E, FETCHED, finish, q, start, store  # noqa: F401

TICKER = "005930"
P = {"ticker": TICKER, "corp_code": CORP}
NAME = {1: "분기보고서", 2: "반기보고서", 3: "분기보고서", 4: "사업보고서"}
AT = datetime(2026, 10, 22, 8, 0, tzinfo=KST)


def list_body(keys=KEYS):
    rows = [{"corp_code": CORP, "stock_code": TICKER, "report_nm": f"{NAME[qq]} ({y}.{qq * 3:02d})",
             "rcept_no": filing(y, qq).rcept_no, "rcept_dt": f"{y if qq < 4 else y + 1}{'05' if qq == 1 else '08' if qq == 2 else '11' if qq == 3 else '03'}10"}
            for y, qq in keys]
    return json.dumps({"status": "000", "page_no": 1, "total_page": 1, "total_count": len(rows), "list": rows}).encode()


def lst(keys=KEYS, at=AT):
    raw = list_body(keys)
    return Response("L1", "dart.list", request_dart_list(CORP, date(2024, 1, 1), at.date(), 1), at, 200,
                    tuple(decode_rows("dart.list", raw)), None, decode_meta("dart.list", raw))


def pack(**kw):
    return [lst(**kw)] + standard()


@pytest.mark.parametrize("latest,checked,expect", [
    ((2026, 2), date(2026, 10, 22), None),
    ((2026, 1), date(2026, 10, 22), "financial_stale"),
    ((2026, 1), date(2026, 8, 14), None),                      # 반기 기한 당일: 미제출로 보지 않음
    ((2026, 1), date(2026, 8, 20), "filing_deadline_unknown"),  # 45~60일 구간: 적용 기한 확인 불가
    ((2026, 2), date(2026, 8, 20), None),
    ((2026, 1), date(2026, 8, 30), "financial_stale"),
    ((2025, 3), date(2026, 3, 31), None),                      # 사업보고서 기한 당일
    ((2025, 3), date(2026, 4, 1), "financial_stale"),
])
def test_freshness_rule(latest, checked, expect):
    assert freshness(latest, checked) == expect


def test_definitions_replicate_current_calculations():
    resp = pack()
    ep = derive("dart_earnings_proxy_v1", resp, P, E, CAL)
    assert (ep.status, ep.value) == ("verified", 4.0)          # OP 30, 50, 25, 40 모두 흑자
    assert ep.detail["calc"] == "positive_op_quarters_4q_v1" and ep.detail["fs_div"] == "CFS"
    lq = derive("dart_loss_quarters_v1", resp, P, E, CAL)
    assert lq.value == 1.0                                      # 2026Q1 순이익 −5
    dr = derive("dart_debt_ratio_v1", resp, P, E, CAL)
    assert dr.value == 50.0
    eq = derive("dart_eq_ratio_v1", resp, P, E, CAL)
    assert eq.value == (30 + 50 + 25 + 40) / (15 + 25 - 5 + 20)
    assert set(ep.detail["rcept_nos"]) == {"2025Q3", "2025Q4", "2026Q1", "2026Q2"}   # ROE 기초(2025Q2)는 의존 아님


def test_stale_and_mismatch_fail_inputs_not_values():
    stale = derive("dart_earnings_proxy_v1", [lst(keys=KEYS[:-1])] + standard()[:-1], P, E, CAL)
    assert (stale.status, stale.value, stale.failure_reason) == ("failed", None, "financial_stale")
    reps = standard(); reps[-1] = report(2026, 2, 40, 20, 18, cum=(65, 15, 12), no="20990101000001")
    bad = derive("dart_debt_ratio_v1", [lst()] + reps, P, E, CAL)
    assert bad.failure_reason == "rcept_no_mismatch"           # 새 보고서 검증 실패 → 옛 분기로 돌아가지 않음


def test_proxy_partial_and_negative_equity():
    reps = standard(); reps[3] = report(2026, 1, None, -5, -6)   # 2026Q1 영업이익 계정 없음
    ep = derive("dart_earnings_proxy_v1", [lst()] + reps, P, E, CAL)
    assert ep.value == 4.0 and ep.detail["missing"] == {"2026Q1.op": "account_missing"}   # 3/3 흑자 → 4 (현행 정규화)
    reps = standard(); reps[-1] = report(2026, 2, 40, 20, 18, cum=(65, 15, 12), eq=-10)
    dr = derive("dart_debt_ratio_v1", [lst()] + reps, P, E, CAL)
    assert (dr.value, dr.missing_kind) == (None, "definition_null")


def test_store_round_trip_and_tamper(store):
    run = start(store)
    recorded = []
    for r in pack():
        body = (list_body() if r.endpoint == "dart.list" else
                json.dumps({"status": "000", "list": list(r.rows)}).encode())
        recorded.append(store.record_response(run, endpoint=r.endpoint, request=r.request, fetched_at=r.fetched_at,
                                              http_status=200, body=body))
    d = derive("dart_earnings_proxy_v1", recorded, P, E, CAL)
    item = to_market_input("fin_earnings_proxy", "dart_earnings_proxy_v1", P, E, d, FETCHED)
    assert item.source_status == "verified" and len(item.rows) > 5
    assert finish(store, run, {"e": replace(item, value=3.0)}) == "incomplete"
    run2 = start(store, universe="u2")
    rec2 = [store.record_response(run2, endpoint=r.endpoint, request=r.request, fetched_at=r.fetched_at,
                                  http_status=200, body=(list_body() if r.endpoint == "dart.list" else
                                                         json.dumps({"status": "000", "list": list(r.rows)}).encode()))
            for r in pack()]
    item2 = to_market_input("fin_earnings_proxy", "dart_earnings_proxy_v1", P, E,
                            derive("dart_earnings_proxy_v1", rec2, P, E, CAL), FETCHED)
    assert finish(store, run2, {"e": item2}) == "completed" and store.audit_input_evidence(run2) is None


def test_failed_financial_input_also_blocks_entry_decision(store):
    """이중 차단: 결정 B로 실행이 incomplete가 되지만, 판정 함수만 따로 불러도 fin_<사유>로 부적격이다."""
    from datetime import timedelta
    from analyzer.pipeline.entry import planned_entry
    from tests.test_signal_sources import NOW, pbr_input
    run = start(store)
    reps = standard(); reps[-1] = report(2026, 2, 40, 20, 18, cum=(65, 15, 12), no="20990101000001")
    recorded = [store.record_response(run, endpoint=r.endpoint, request=r.request, fetched_at=r.fetched_at,
                                      http_status=200, body=(list_body() if r.endpoint == "dart.list" else
                                                             json.dumps({"status": "000", "list": list(r.rows)}).encode()))
                for r in [lst()] + reps]
    fin = to_market_input("fin_earnings_proxy", "dart_earnings_proxy_v1", P, E,
                          derive("dart_earnings_proxy_v1", recorded, P, E, CAL), FETCHED)
    assert (fin.source_status, fin.failure_reason) == ("failed", "rcept_no_mismatch")
    decision = planned_entry(store.run_context(run), NOW + timedelta(minutes=10), [pbr_input(store, run), fin], CAL)
    assert not decision.eligible and "fin_rcept_no_mismatch" in decision.reasons


def test_revised_decision_b_validation_failure_is_excluded_not_scored(store):
    """결정 B 개정: 공시 검증 실패(접수번호 불일치)는 excluded_validation(점수·등급 없음), 실행 오류는 failed.
    표시하지 않고 채점된 채 넘기면 financial_status_inconsistent. 1종목 유니버스에서 제외 1 = 100% > 5% → degraded_inputs."""
    from datetime import timedelta
    from analyzer.pipeline.official import apply_financial_status
    from tests.test_signal_sources import NOW, ROW, pbr_input

    def failed_fin(run):
        reps = standard(); reps[-1] = report(2026, 2, 40, 20, 18, cum=(65, 15, 12), no="20990101000001")
        rec = [store.record_response(run, endpoint=r.endpoint, request=r.request, fetched_at=r.fetched_at,
                                     http_status=200, body=(list_body() if r.endpoint == "dart.list" else
                                                            json.dumps({"status": "000", "list": list(r.rows)}).encode()))
               for r in [lst()] + reps]
        return to_market_input("fin_earnings_proxy", "dart_earnings_proxy_v1", P, E,
                               derive("dart_earnings_proxy_v1", rec, P, E, CAL), FETCHED)
    run = start(store)
    fin = failed_fin(run)
    inputs = {"m": pbr_input(store, run), "f": fin}
    assert store.finish_run(run, ROW, "hash", NOW + timedelta(minutes=5), inputs=inputs,
                            signal_inputs={"005930": ["f", "m"]}) == "incomplete"
    assert q(store, "SELECT abort_reason FROM signal_runs WHERE run_id=?", (run,)) == [("financial_status_inconsistent",)]
    run2 = start(store, universe="u2")
    fin2 = failed_fin(run2)
    sig = [apply_financial_status(ROW[0], [fin2])]
    assert (sig[0]["processing_status"], sig[0]["normalized_score"], sig[0]["grade"]) == ("excluded_validation", None, None)
    assert sig[0]["error"] == "financial_validation_excluded: fin_rcept_no_mismatch"
    assert store.finish_run(run2, sig, "hash", NOW + timedelta(minutes=5), inputs={"m": pbr_input(store, run2), "f": fin2},
                            signal_inputs={"005930": ["f", "m"]}) == "incomplete"
    assert q(store, "SELECT abort_reason FROM signal_runs WHERE run_id=?", (run2,)) == [("degraded_inputs",)]
    assert q(store, "SELECT count(*) FROM run_inputs WHERE run_id=?", (run2,)) == [(2,)]   # 실패 근거도 기록
    assert apply_financial_status(ROW[0], []) is ROW[0]
    timeout = replace(fin2, failure_reason="request_timeout", responses=(), rows=())
    assert apply_financial_status(ROW[0], [timeout])["processing_status"] == "failed"      # 실행 오류는 제외로 넘기지 않음
    assert apply_financial_status(ROW[0], [fin2, timeout])["processing_status"] == "failed"


def test_exception_scope_end_to_end(monkeypatch):
    """입력 정의 경로: 범위 안 입력은 예외 적용·기록, 범위 밖 입력은 접수번호 불일치로 실패."""
    import analyzer.pipeline.dart_inputs as DI
    from tests.test_dart_quarters import _exception
    rows = json.loads(list_body())
    for r in rows["list"]:
        if r["report_nm"].startswith("사업보고서 (2025"):
            r["rcept_no"], r["report_nm"] = "ATTACH000001", "[첨부정정]사업보고서 (2025.12)"
    raw = json.dumps(rows).encode()
    listed = Response("L1", "dart.list", request_dart_list(CORP, date(2024, 1, 1), AT.date(), 1), AT, 200,
                      tuple(decode_rows("dart.list", raw)), None, decode_meta("dart.list", raw))
    reps = standard(); reps[2] = report(2025, 4, 120, 60, 57, no="ORIGIN000001")
    monkeypatch.setattr(DI, "APPROVED_EXCEPTIONS", (_exception(ticker=TICKER),))
    ep = derive("dart_earnings_proxy_v1", [listed] + reps, P, E, CAL)
    assert ep.status == "verified" and ep.detail["exceptions"] == {
        "version": DI.EXCEPTIONS_VERSION, "used": ["T-2025-11011-CFS"], "definition": "dart_earnings_proxy_v1", "accounts": ["op"]}
    assert derive("dart_loss_quarters_v1", [listed] + reps, P, E, CAL).failure_reason == "rcept_no_mismatch"


def test_roe_definition_consolidated_and_separate():
    resp = pack()
    r = derive("dart_roe_v1", resp, P, E, CAL)
    # 연결: 지배주주 순이익 14 + (57−33) + (−6) + 18 = 50, 평균 지배 지분 (70 + 90)/2 = 80
    assert r.status == "verified" and r.value == round(50 / 80 * 100, 4) and r.detail["basis"] == "consolidated_controlling"
    assert "2025Q2" in r.detail["rcept_nos"]                         # ROE는 기초 보고서까지 의존
    noctrl = [lst()] + standard()[:-1] + [report(2026, 2, 40, 20, None, cum=(65, 15, 12))]
    d = derive("dart_roe_v1", noctrl, P, E, CAL)
    assert (d.value, d.missing_kind) == (None, "definition_null")     # 지배주주 순이익 없으면 대체하지 않음
    ofs = [lst()] + [nodata(y, q) for y, q in KEYS] + [
        report(y, q, 1, 40 if q == 4 else 10, None, cum=None if q in (1, 4) else (q, 10 * q, None), eq=100, fs="OFS")
        for y, q in KEYS]                                              # 4분기 = 연간 40 − 3분기 누적 30 = 10
    o = derive("dart_roe_v1", ofs, P, E, CAL)
    assert o.detail["basis"] == "separate_reporting_entity" and o.value == round(40 / 100 * 100, 4)


def test_exception_not_applied_to_roe_even_for_listed_case(monkeypatch):
    """승인 항목이 있어도 ROE(허용 입력 밖)에는 적용하지 않는다."""
    import analyzer.pipeline.dart_inputs as DI
    from tests.test_dart_quarters import _exception
    rows = json.loads(list_body())
    for r in rows["list"]:
        if r["report_nm"].startswith("사업보고서 (2025"):
            r["rcept_no"], r["report_nm"] = "ATTACH000001", "[첨부정정]사업보고서 (2025.12)"
    raw = json.dumps(rows).encode()
    listed = Response("L1", "dart.list", request_dart_list(CORP, date(2024, 1, 1), AT.date(), 1), AT, 200,
                      tuple(decode_rows("dart.list", raw)), None, decode_meta("dart.list", raw))
    reps = standard(); reps[2] = report(2025, 4, 120, 60, 57, no="ORIGIN000001")
    four = ("dart_earnings_proxy_v1", "dart_loss_quarters_v1", "dart_debt_ratio_v1", "dart_eq_ratio_v1")
    monkeypatch.setattr(DI, "APPROVED_EXCEPTIONS", (_exception(ticker=TICKER, inputs=four),))
    assert derive("dart_roe_v1", [listed] + reps, P, E, CAL).failure_reason == "rcept_no_mismatch"
