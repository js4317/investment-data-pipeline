"""5단계-4 (2): DART 분기 재무 표 구성 — 고정 응답만 사용 (실제 조회 없음)."""
from datetime import date, datetime
import json

from analyzer.pipeline.dart_quarters import build_quarters
from analyzer.pipeline.dart_select import Filing
from analyzer.pipeline.sources import Response, decode_meta, decode_rows, request_dart_fnltt
from database.signal_store import KST

CORP = "00126380"
AT = datetime(2026, 10, 22, 8, 0, tzinfo=KST)
CODE = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}


def filing(y, q, no=None):
    return Filing(y, q, CODE[q], no or f"{y}{q}0000000{q}", date(y, 12, 1), "x", False, 1, ("L", 0))


def sel(*keys):
    return {k: filing(*k) for k in keys}


def row(sj, aid, cur=None, add=None, no=None, y=None, code=None, ord_="1"):
    r = {"rcept_no": no, "corp_code": CORP, "bsns_year": str(y), "reprt_code": code, "sj_div": sj,
         "account_id": aid, "account_nm": aid, "ord": ord_, "currency": "KRW",
         "thstrm_amount": "" if cur is None else str(cur)}
    if add is not None:
        r["thstrm_add_amount"] = str(add)
    return r


def report(y, q, op, ni, ctrl, cum=None, eq=100, eq_ctrl=90, liab=50, fs="CFS", stmt="CIS", extra=(), no=None):
    """한 보고서의 재무 응답. cum: 누적 금액 (op, ni, ctrl) — 1분기는 생략 시 3개월과 같게."""
    f = filing(y, q, no)
    no, code = f.rcept_no, CODE[q]
    if cum is None and q == 1:
        cum = (op, ni, ctrl)
    rows = []
    for k, (aid, v) in enumerate((("dart_OperatingIncomeLoss", op), ("ifrs-full_ProfitLoss", ni),
                                  ("ifrs-full_ProfitLossAttributableToOwnersOfParent", ctrl))):
        if v is None:
            continue
        add = None if q == 4 else (cum[k] if cum else v * q)
        rows.append(row(stmt, aid, v, add, no, y, code, str(k)))
    for aid, v in (("ifrs-full_Equity", eq), ("ifrs-full_EquityAttributableToOwnersOfParent", eq_ctrl),
                   ("ifrs-full_Liabilities", liab)):
        rows.append(row("BS", aid, v, None, no, y, code, aid[-3:]))
    rows += [dict(r, rcept_no=no, bsns_year=str(y), reprt_code=code) for r in extra]
    raw = json.dumps({"status": "000", "list": rows}).encode()
    return Response(f"{fs}{y}{code}", "dart.fnltt", request_dart_fnltt(CORP, y, code, fs), AT, 200,
                    tuple(decode_rows("dart.fnltt", raw)), None, decode_meta("dart.fnltt", raw))


def nodata(y, q, fs="CFS"):
    raw = json.dumps({"status": "013"}).encode()
    return Response(f"N{fs}{y}{q}", "dart.fnltt", request_dart_fnltt(CORP, y, CODE[q], fs), AT, 200, (), None,
                    decode_meta("dart.fnltt", raw))


def fn(reps):
    out = {}
    for r in reps:
        key = (int(r.request["bsns_year"]), r.request["reprt_code"])
        out.setdefault(key, {})[r.request["fs_div"]] = r
    return out


KEYS = [(2025, 2), (2025, 3), (2025, 4), (2026, 1), (2026, 2)]


def standard(**kw):
    return [report(2025, 2, 20, 10, 9, eq=80, eq_ctrl=70),
            report(2025, 3, 30, 15, 14, cum=(70, 35, 33)),
            report(2025, 4, 120, 60, 57),                         # 연간
            report(2026, 1, 25, -5, -6),
            report(2026, 2, 40, 20, 18, cum=(65, 15, 12), **kw)]


def test_window_q4_from_annual_minus_q3_cumulative():
    t = build_quarters(sel(*KEYS), fn(standard()), corp_code=CORP, include_start=True)
    assert t.fs_div == "CFS"
    assert [(q.year, q.quarter) for q in t.quarters] == [(2025, 3), (2025, 4), (2026, 1), (2026, 2)]
    q4 = t.quarters[1]
    assert (q4.op, q4.ni, q4.ni_ctrl) == (120 - 70, 60 - 35, 57 - 33)
    assert [q.ni for q in t.quarters] == [15, 25, -5, 20]
    assert t.bs_end == {"equity": 100, "equity_ctrl": 90, "liabilities": 50}
    assert t.bs_start == {"equity": 80, "equity_ctrl": 70, "liabilities": 50}
    assert len(t.responses) == 5 and not t.missing


def test_latest_is_q4():
    keys = [(2024, 4), (2025, 1), (2025, 2), (2025, 3), (2025, 4)]
    reps = [report(2024, 4, 100, 50, 45), report(2025, 1, 10, 5, 4), report(2025, 2, 20, 10, 9, cum=(30, 15, 13)),
            report(2025, 3, 30, 15, 14, cum=(60, 30, 27)), report(2025, 4, 100, 50, 45)]
    t = build_quarters(sel(*keys), fn(reps), corp_code=CORP, include_start=True)
    assert [q.op for q in t.quarters] == [10, 20, 30, 40] and t.bs_start["equity"] == 100


def test_comprehensive_income_is_not_used_for_controlling_net_income():
    reps = standard()
    reps[-1] = report(2026, 2, 40, 20, None, cum=(65, 15, 12), extra=[
        row("CIS", "ifrs-full_ComprehensiveIncomeAttributableToOwnersOfParent", 999, 999, ord_="9")])
    t = build_quarters(sel(*KEYS), fn(reps), corp_code=CORP)
    assert t.quarters[-1].ni_ctrl is None and t.missing == {"2026Q2.ni_ctrl": "account_missing"}
    assert t.quarters[-1].ni == 20                                # 전체 순이익으로 대체하지 않음


def test_is_preferred_and_must_match_cis():
    ok = report(2026, 2, 40, 20, 18, cum=(65, 15, 12), stmt="IS",
                extra=[row("CIS", "ifrs-full_ProfitLoss", 20, 15, ord_="7")])
    reps = standard(); reps[-1] = ok
    assert build_quarters(sel(*KEYS), fn(reps), corp_code=CORP).quarters[-1].ni == 20
    bad = report(2026, 2, 40, 20, 18, cum=(65, 15, 12), stmt="IS",
                 extra=[row("CIS", "ifrs-full_ProfitLoss", 21, 15, ord_="7")])
    reps[-1] = bad
    assert build_quarters(sel(*KEYS), fn(reps), corp_code=CORP) == "is_cis_inconsistent"


def test_ambiguous_account_fails():
    reps = standard(); reps[-1] = report(2026, 2, 40, 20, 18, cum=(65, 15, 12),
                                         extra=[row("CIS", "ifrs-full_ProfitLoss", 1, 1, ord_="8")])
    assert build_quarters(sel(*KEYS), fn(reps), corp_code=CORP) == "account_ambiguous"


def test_separate_only_when_all_lack_consolidated():
    ofs = [report(y, q, 1, 1, None, cum=None if q in (1, 4) else (q, q, None), fs="OFS") for y, q in KEYS]
    allnone = fn([nodata(y, q) for y, q in KEYS] + ofs)
    t = build_quarters(sel(*KEYS), allnone, corp_code=CORP)
    assert t.fs_div == "OFS"
    mixed = fn(standard()[:-1] + [nodata(2026, 2)] + [report(2026, 2, 1, 1, 1, cum=(2, 2, 2), fs="OFS")])
    assert build_quarters(sel(*KEYS), mixed, corp_code=CORP) == "fs_div_unavailable_or_mixed"


def test_rcept_mismatch_in_any_report_fails():
    reps = standard()
    reps[2] = report(2025, 4, 120, 60, 57, no="20260318000849")   # 목록의 최신 버전과 다른 접수번호
    assert build_quarters(sel(*KEYS), fn(reps), corp_code=CORP) == "rcept_no_mismatch"


def test_missing_quarter_and_q1_cumulative():
    assert build_quarters(sel((2025, 2), (2025, 4), (2026, 1), (2026, 2)), fn(standard()), corp_code=CORP) == "quarter_missing"
    reps = standard(); reps[3] = report(2026, 1, 25, -5, -6, cum=(26, -5, -6))
    assert build_quarters(sel(*KEYS), fn(reps), corp_code=CORP) == "q1_cumulative_mismatch"


def test_start_report_missing_is_recorded_not_filled():
    t = build_quarters(sel(*KEYS[1:]), fn(standard()[1:]), corp_code=CORP, include_start=True)
    assert t.bs_start == {} and t.missing == {"2025Q2.bs": "report_missing"}


def test_roe_base_report_is_a_dependency_only_when_requested():
    """ROE 기초 보고서(창 직전 분기)의 검증 실패는 ROE를 쓰지 않는 입력을 막지 않는다."""
    reps = standard(); reps[0] = report(2025, 2, 20, 10, 9, eq=80, eq_ctrl=70, no="20990101000003")
    t = build_quarters(sel(*KEYS), fn(reps), corp_code=CORP)
    assert t.bs_start == {} and len(t.responses) == 4 and not t.missing
    assert build_quarters(sel(*KEYS), fn(reps), corp_code=CORP, include_start=True) == "rcept_no_mismatch"


def test_verification_error_anywhere_blocks_fallback_to_separate():
    """앞 보고서는 연결 자료 없음, 뒤 보고서는 연결 접수번호 불일치, 별도는 모두 정상 → 별도로 통과하면 안 된다."""
    st = standard()                                               # 검사 순서상 앞쪽(2025Q3)을 자료 없음으로
    cfs = [st[0], nodata(2025, 3)] + st[2:4] + [report(2026, 2, 40, 20, 18, cum=(65, 15, 12), no="20990101000001")]
    ofs = [report(y, q, 1, 1, None, cum=None if q in (1, 4) else (q, q, None), fs="OFS") for y, q in KEYS]
    assert build_quarters(sel(*KEYS), fn(cfs + ofs), corp_code=CORP) == "rcept_no_mismatch"
    ofs_bad = ofs[:-1] + [report(2026, 2, 1, 1, None, cum=(2, 2, None), fs="OFS", no="20990101000002")]
    assert build_quarters(sel(*KEYS), fn([nodata(y, q) for y, q in KEYS] + ofs_bad), corp_code=CORP) == "rcept_no_mismatch"


def test_balance_sheet_currency_checked():
    r = report(2026, 2, 40, 20, 18, cum=(65, 15, 12))
    rows = [dict(x, currency="USD") if x["sj_div"] == "BS" else x for x in r.rows]
    usd = Response(r.response_id, r.endpoint, r.request, r.fetched_at, 200, tuple(rows), None, r.meta)
    reps = standard()[:-1] + [usd]
    assert build_quarters(sel(*KEYS), fn(reps), corp_code=CORP) == "currency_unsupported"


def _exception(**kw):
    from analyzer.pipeline.dart_exceptions import AttachmentException
    base = dict(id="T-2025-11011-CFS", corp_code=CORP, ticker="005930", bsns_year=2025, reprt_code="11011", fs_div="CFS",
                latest_rcept_no="ATTACH000001", response_rcept_no="ORIGIN000001",
                accounts=(("op", ("IS", "CIS"), ("dart_OperatingIncomeLoss",), "120"),
                          ("ni", ("IS", "CIS"), ("ifrs-full_ProfitLoss",), "60"),
                          ("equity", ("BS",), ("ifrs-full_Equity",), "100")),
                inputs=("dart_earnings_proxy_v1", "dart_eq_ratio_v1"),
                period_no=41, unit="원", checks=("test",), evidence=())
    base.update(kw)
    return AttachmentException(**base)


SCOPE = ("dart_eq_ratio_v1", ("op", "ni"))


def _attach_case(response_op=120, latest_no="ATTACH000001"):
    s = sel(*KEYS)
    s[(2025, 4)] = filing(2025, 4, latest_no)                    # 목록의 최신 버전 = [첨부정정]
    reps = standard(); reps[2] = report(2025, 4, response_op, 60, 57, no="ORIGIN000001")   # 재무 API = 원본 번호
    return s, fn(reps)


def test_approved_attachment_exception_allows_original_receipt_and_is_recorded():
    s, f = _attach_case()
    assert build_quarters(s, f, corp_code=CORP) == "rcept_no_mismatch"                       # 목록 없으면 차단
    t = build_quarters(s, f, corp_code=CORP, scope=SCOPE, exceptions=(_exception(),))
    assert t.exceptions_used == ("T-2025-11011-CFS",) and t.quarters[1].op == 120 - 70


def test_attachment_exception_auto_blocks():
    s, f = _attach_case(latest_no="ATTACH000002")                 # 이후 정정 추가 → 최신 번호가 달라짐
    assert build_quarters(s, f, corp_code=CORP, scope=SCOPE, exceptions=(_exception(),)) == "rcept_no_mismatch"
    s, f = _attach_case(response_op=121)                          # 재무 응답 금액이 근거와 다름
    assert build_quarters(s, f, corp_code=CORP, scope=SCOPE, exceptions=(_exception(),)) == "exception_evidence_changed"
    s, f = _attach_case()
    assert build_quarters(s, f, corp_code=CORP, scope=SCOPE, exceptions=(_exception(fs_div="OFS"),)) == "rcept_no_mismatch"
    assert build_quarters(s, f, corp_code=CORP, scope=SCOPE, exceptions=(_exception(response_rcept_no="OTHER"),)) == "rcept_no_mismatch"
    assert build_quarters(s, f, corp_code=CORP, scope=SCOPE, exceptions=(_exception(corp_code="99999999"),)) == "rcept_no_mismatch"


def test_attachment_exception_applies_only_within_input_scope():
    s, f = _attach_case()
    ex = (_exception(),)                                           # 검증 계정: op·ni·equity, 허용 입력: 대용치·비율
    assert build_quarters(s, f, corp_code=CORP, scope=("dart_earnings_proxy_v1", ("op",)), exceptions=ex).exceptions_used
    assert build_quarters(s, f, corp_code=CORP, scope=("dart_loss_quarters_v1", ("ni",)), exceptions=ex) == "rcept_no_mismatch"
    assert build_quarters(s, f, corp_code=CORP, scope=("dart_debt_ratio_v1", ("equity", "liabilities")),
                          exceptions=(_exception(inputs=("dart_debt_ratio_v1",)),)) == "rcept_no_mismatch"   # 부채 미검증
    assert build_quarters(s, f, corp_code=CORP, exceptions=ex) == "rcept_no_mismatch"                       # 범위 미지정
