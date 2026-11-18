"""5단계-4 (1): DART 정기공시 선택·접수번호 연결 — 고정 응답만 사용 (실제 조회 없음)."""
from datetime import date, datetime
import json

import pytest

from analyzer.pipeline.dart_select import parse_report_nm, select_periodic, verify_fnltt
from analyzer.pipeline.sources import (Response, SourceParseError, decode_meta, decode_rows,
                                       request_dart_fnltt, request_dart_list)
from database.signal_store import KST

CORP, TICKER = "00126380", "005930"
CHECK = date(2026, 10, 22)
AT = datetime(2026, 10, 22, 8, 0, tzinfo=KST)


def filing(no, dt, nm, corp=CORP, stock=TICKER):
    return {"corp_code": corp, "corp_name": "삼성전자", "stock_code": stock, "corp_cls": "Y",
            "report_nm": nm, "rcept_no": no, "flr_nm": "삼성전자", "rcept_dt": dt, "rm": ""}


def lst(rows, page=1, total_page=1, total_count=None, rid=None, status="000", **req):
    body = {"status": status, "message": "정상", "page_no": page, "page_count": 100,
            "total_count": len(rows) if total_count is None else total_count, "total_page": total_page,
            "list": rows} if status == "000" else {"status": status, "message": "조회된 데이타가 없습니다."}
    raw = json.dumps(body).encode()
    request = dict(request_dart_list(CORP, date(2024, 1, 1), CHECK, page), **req)
    return Response(rid or f"L{page}", "dart.list", request, AT, 200, tuple(decode_rows("dart.list", raw)),
                    None, decode_meta("dart.list", raw))


BASE = [filing("20250311000001", "20250311", "사업보고서 (2024.12)"),
        filing("20250515000002", "20250515", "분기보고서 (2025.03)"),
        filing("20250814000003", "20250814", "반기보고서 (2025.06)"),
        filing("20251114000004", "20251114", "분기보고서 (2025.09)"),
        filing("20260310000005", "20260310", "사업보고서 (2025.12)"),
        filing("20260515000006", "20260515", "분기보고서 (2026.03)"),
        filing("20260814000007", "20260814", "반기보고서 (2026.06)")]


def test_parse_report_nm():
    assert parse_report_nm("분기보고서 (2026.03)") == ("분기보고서", 2026, 3, False)
    assert parse_report_nm("[기재정정]사업보고서 (2025.12)") == ("사업보고서", 2025, 12, True)
    assert parse_report_nm("[기재정정][첨부정정] 반기보고서 (2026.06)") == ("반기보고서", 2026, 6, True)
    assert parse_report_nm("증권신고서(채무증권)") is None
    assert parse_report_nm("등록법인결산서류(자본시장법이전)") is None


def test_select_by_report_period_not_receipt_month():
    late = BASE + [filing("20260402000008", "20260402", "사업보고서 (2025.12)")]   # 같은 기간 재제출(꼬리표 없음)도 새 버전
    out = select_periodic([lst(BASE)], ticker=TICKER, corp_code=CORP, checked_on=CHECK)
    assert sorted(out) == [(2024, 4), (2025, 1), (2025, 2), (2025, 3), (2025, 4), (2026, 1), (2026, 2)]
    q = out[(2025, 4)]
    assert (q.reprt_code, q.rcept_no, q.period_end, q.is_amendment) == ("11011", "20260310000005", date(2025, 12, 31), False)
    assert out[(2025, 3)].reprt_code == "11014" and out[(2025, 1)].reprt_code == "11013"
    out2 = select_periodic([lst(late)], ticker=TICKER, corp_code=CORP, checked_on=CHECK)
    assert out2[(2025, 4)].rcept_no == "20260402000008" and out2[(2025, 4)].versions == 2


def test_amendment_of_old_report_does_not_displace_recent_quarter():
    rows = [filing("20261001000009", "20261001", "[기재정정]사업보고서 (2024.12)")] + BASE[::-1]   # 목록은 최신순
    out = select_periodic([lst(rows)], ticker=TICKER, corp_code=CORP, checked_on=CHECK)
    assert out[(2024, 4)].rcept_no == "20261001000009" and out[(2024, 4)].is_amendment
    assert out[(2026, 2)].rcept_no == "20260814000007"          # 최근 분기는 그대로


def test_all_pages_required_and_consistent():
    p1 = lst(BASE[:4], page=1, total_page=2, total_count=7)
    p2 = lst(BASE[4:], page=2, total_page=2, total_count=7)
    assert len(select_periodic([p1, p2], ticker=TICKER, corp_code=CORP, checked_on=CHECK)) == 7
    assert select_periodic([p1], ticker=TICKER, corp_code=CORP, checked_on=CHECK) == "pages_incomplete"
    p2bad = lst(BASE[4:6], page=2, total_page=2, total_count=7)
    assert select_periodic([p1, p2bad], ticker=TICKER, corp_code=CORP, checked_on=CHECK) == "count_mismatch"
    p2other = lst(BASE[4:], page=2, total_page=2, total_count=7, end_de="20261021")
    assert select_periodic([p1, p2other], ticker=TICKER, corp_code=CORP, checked_on=CHECK) == "request_mismatch"
    assert select_periodic([p1, p1], ticker=TICKER, corp_code=CORP, checked_on=CHECK) == "duplicate_page"
    none = lst([], status="013")
    assert select_periodic([none], ticker=TICKER, corp_code=CORP, checked_on=CHECK) == {}


@pytest.mark.parametrize("rows,reason", [
    (BASE + [filing("20250311000001", "20250311", "사업보고서 (2024.12)")], "duplicate_rows"),
    (BASE + [filing("20260901000010", "20260901", "분기보고서 (2026.09)", stock="000660")], "corp_link_mismatch"),
    (BASE + [filing("20261023000011", "20261023", "분기보고서 (2026.09)")], "filed_after_check"),
    (BASE + [filing("20260601000012", "20260601", "사업보고서 (2026.03)")], "fiscal_year_unsupported"),
    (BASE + [filing("20260601000013", "2026-06-01", "분기보고서 (2026.03)")], "invalid_date"),
])
def test_select_blocks(rows, reason):
    assert select_periodic([lst(rows)], ticker=TICKER, corp_code=CORP, checked_on=CHECK) == reason


def test_list_request_must_be_periodic_all_versions():
    r = lst(BASE, last_reprt_at="Y")
    assert select_periodic([r], ticker=TICKER, corp_code=CORP, checked_on=CHECK) == "request_mismatch"


def test_dart_status_handling():
    assert decode_rows("dart.fnltt", json.dumps({"status": "013", "message": "없음"}).encode()) == []
    with pytest.raises(SourceParseError, match="dart_status_020"):
        decode_rows("dart.list", json.dumps({"status": "020", "message": "요청 제한 초과"}).encode())


def fn_rows(no="20260814000007", year="2026", code="11012"):
    return [{"rcept_no": no, "reprt_code": code, "bsns_year": year, "corp_code": CORP, "sj_div": "IS",
             "account_id": "ifrs-full_ProfitLoss", "account_nm": "당기순이익", "ord": "20",
             "thstrm_amount": "100", "thstrm_add_amount": "250", "currency": "KRW"}]


def fn(rows, fs_div="CFS", year=2026, code="11012"):
    raw = json.dumps({"status": "000", "message": "정상", "list": rows}).encode()
    return Response("F1", "dart.fnltt", request_dart_fnltt(CORP, year, code, fs_div), AT, 200,
                    tuple(decode_rows("dart.fnltt", raw)), None, decode_meta("dart.fnltt", raw))


def test_verify_fnltt_links_rows_to_selected_filing():
    sel = select_periodic([lst(BASE)], ticker=TICKER, corp_code=CORP, checked_on=CHECK)[(2026, 2)]
    assert verify_fnltt(fn(fn_rows()), sel, corp_code=CORP, fs_div="CFS") is None
    assert verify_fnltt(fn(fn_rows(no="20260901999999")), sel, corp_code=CORP, fs_div="CFS") == "rcept_no_mismatch"
    assert verify_fnltt(fn(fn_rows(year="2025")), sel, corp_code=CORP, fs_div="CFS") == "report_identity_mismatch"
    assert verify_fnltt(fn(fn_rows(), fs_div="OFS"), sel, corp_code=CORP, fs_div="CFS") == "request_mismatch"
    assert verify_fnltt(fn([]), sel, corp_code=CORP, fs_div="CFS") == "statement_missing"


def test_dart_transport_keeps_key_out_of_request():
    import requests
    from analyzer.pipeline.transport import DartTransport
    from analyzer.pipeline.collect import TransportError

    class Sess:
        def __init__(self, exc=None): self.exc, self.seen = exc, None
        def get(self, url, params, timeout):
            self.seen = params
            if self.exc:
                raise self.exc
            r = requests.Response(); r.status_code = 200; r._content = b'{"status":"013"}'
            return r
    s = Sess()
    req = request_dart_list(CORP, date(2024, 1, 1), CHECK, 1)
    assert DartTransport(s, key="K").send("dart", "https://x", req).body == b'{"status":"013"}'
    assert s.seen["crtfc_key"] == "K" and "crtfc_key" not in req
    with pytest.raises(ValueError):
        DartTransport(Sess(), key="K").send("dart", "https://x", dict(req, crtfc_key="K"))
    with pytest.raises(TransportError) as e:
        DartTransport(Sess(requests.ConnectionError("https://x?crtfc_key=K")), key="K").send("dart", "https://x", req)
    assert "K" not in str(e.value).replace("ConnectionError", "")
