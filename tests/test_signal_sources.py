"""5단계-2: 원 응답 파서·정의(순수)와 저장·입력 연결. 고정 응답과 시험용 DB만 사용 (실제 조회 없음)."""
from dataclasses import replace
from datetime import date, datetime, timedelta
import hashlib
import json
import math
import sqlite3
import statistics
import sys

import pytest

from analyzer.pipeline.entry import MarketInput, RunContext
from analyzer.pipeline.runtime import LaunchEvidence
from analyzer.pipeline.sources import (DEFINITIONS, Response, SourceParseError, decode_rows, derive,
                                       parse_number, reference_date_1y, request_00301, request_01701,
                                       request_03502, request_finder, request_open_api, to_market_input, window_sessions)
from database.signal_store import KST, STRATEGY, SignalStore
from database.trading_calendar import CalendarDay, CalendarNoticeCheck, CalendarSnapshot, TradingCalendarStore

REF = "https://krx.co.kr/test-fixture"
NOW = datetime(2026, 10, 14, 8, 30, tzinfo=KST)
E = date(2026, 10, 13)
FETCHED = NOW + timedelta(seconds=5)


def make_calendar(start=date(2025, 6, 1), end=date(2026, 10, 20), holidays=(), version="src-test"):
    days, d = {}, start
    while d <= end:
        status = "session" if d.weekday() < 5 and d not in holidays else "holiday"
        days[d] = CalendarDay(d, status, "KRX_EQUITY", REF, NOW - timedelta(days=1))
        d += timedelta(days=1)
    return CalendarSnapshot(version, days, CalendarNoticeCheck(version + "-chk", version, NOW - timedelta(minutes=5),
                                                               "clear", (REF,)))


CAL = make_calendar()


def resp(endpoint, request, payload, rid="r1", status=200, fetched=FETCHED):
    body = json.dumps(payload).encode()
    try:
        rows, err = tuple(decode_rows(endpoint, body)), None
    except SourceParseError as e:
        rows, err = None, str(e)
    return Response(rid, endpoint, request, fetched, status, rows, err)


def krx_d(d):
    return d.strftime("%Y/%m/%d")


# ── 숫자·응답 해석 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [("1,234.5", (1234.5, None)), ("-0.25", (-0.25, None)), ("12", (12.0, None)),
                                          ("-", (None, "source_dash")), ("", (None, "source_blank")),
                                          ("  ", (None, "source_blank"))])
def test_parse_number_ok(raw, expected):
    assert parse_number(raw) == expected


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-Infinity", "1e5", "1,23", "12,3456", "abc", "--", "1.", 1.0, None, True])
def test_parse_number_rejects_non_finite_and_garbage(raw):
    with pytest.raises(SourceParseError):
        parse_number(raw)


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"output": {}}', b'{"output": [{"PER": "1"}]}', b"\xff\xfe"])
def test_decode_rows_rejects_bad_shape(body):
    with pytest.raises(SourceParseError):
        decode_rows("krx.MDCSTAT03502", body)


# ── 03502 단일 행 ──────────────────────────────────────────────────────────

P03502 = {"field": "PBR", "ticker": "005930", "isin": "KR7005930003", "mkt_id": "STK"}
FINDER_ROWS = [{"short_code": "005930", "full_code": "KR7005930003", "marketCode": "STK", "codeName": "삼성전자"},
               {"short_code": "000660", "full_code": "KR7000660001", "marketCode": "STK", "codeName": "SK하이닉스"}]


def r03502(rows, **kw):
    return resp("krx.MDCSTAT03502", request_03502("KR7005930003", "STK", E), {"output": rows}, **kw)


def rfinder(rows=None):
    return resp("krx.finder_stkisu", request_finder(), {"block1": FINDER_ROWS if rows is None else rows}, rid="f1")


def ropen_close(close="61,000", day=E):
    return resp("krxopen.stk_bydd_trd", request_open_api(day),
                {"OutBlock_1": [{"BAS_DD": day.strftime("%Y%m%d"), "ISU_CD": "005930", "TDD_CLSPRC": close},
                                {"BAS_DD": day.strftime("%Y%m%d"), "ISU_CD": "000660", "TDD_CLSPRC": "200,000"}]}, rid="o1")


def row3(**kw):
    return dict({"TRD_DD": krx_d(E), "TDD_CLSPRC": "61,000", "PBR": "0.85"}, **kw)


def pack(rows, finder=None, opened=None, **kw):
    return [r03502(rows, **kw), finder or rfinder(), opened or ropen_close()]


def test_03502_verified_value_basis_and_links():
    d = derive("krx03502_field_v2", pack([row3()]), P03502, E, CAL)
    assert (d.status, d.value, d.missing_kind, d.basis_status) == ("verified", 0.85, None, "basis_period_unverified")
    assert d.rows == (("f1", 0), ("o1", 0), ("r1", 0)) and d.responses == ("f1", "o1", "r1")
    d2 = derive("krx03502_field_v2", pack([row3(DVD_YLD="2.1")]), dict(P03502, field="DVD_YLD"), E, CAL)
    assert d2.basis_status is None and d2.value == 2.1


@pytest.mark.parametrize("rows,reason", [
    ([row3(TRD_DD=krx_d(E - timedelta(days=1)))], "date_mismatch"),
    ([row3()] * 2, "row_count"),
    ([], "row_count"),
    ([row3(PBR="nan")], "unparseable_number"),
    ([row3(PBR="inf")], "unparseable_number"),
    ([{"TRD_DD": krx_d(E), "TDD_CLSPRC": "61,000"}], "field_missing"),
])
def test_03502_failures_are_not_source_missing(rows, reason):
    d = derive("krx03502_field_v2", pack(rows), P03502, E, CAL)
    assert (d.status, d.value, d.missing_kind, d.failure_reason) == ("failed", None, None, reason)


def test_03502_dash_is_source_missing_and_request_http_checked():
    d = derive("krx03502_field_v2", pack([row3(PBR="-")]), P03502, E, CAL)
    assert (d.status, d.value, d.missing_kind) == ("verified", None, "source_dash")
    other = resp("krx.MDCSTAT03502", request_03502("KR7000660001", "STK", E), {"output": [row3()]})
    assert derive("krx03502_field_v2", [other, rfinder(), ropen_close()], P03502, E, CAL).failure_reason == "request_mismatch"
    assert derive("krx03502_field_v2", pack([row3()], status=500), P03502, E, CAL).failure_reason == "http_error"
    assert derive("krx03502_field_v2", [rfinder(), ropen_close()], P03502, E, CAL).failure_reason == "response_count"


@pytest.mark.parametrize("case,reason", [
    ("no_finder", "isin_link_missing"),
    ("ticker_absent", "isin_link_missing"),
    ("finder_dup", "duplicate_rows"),
    ("isin_other", "isin_link_mismatch"),
    ("market_other", "isin_link_mismatch"),
    ("no_open", "close_check_missing"),
    ("open_absent", "close_check_missing"),
    ("open_dash", "close_check_missing"),
    ("close_diff", "close_mismatch"),
    ("open_other_day", "request_mismatch"),
    ("no_close_in_03502", "close_check_missing"),
])
def test_03502_link_and_close_checks_block_verified(case, reason):
    rows, finder, opened, params = [row3()], rfinder(), ropen_close(), dict(P03502)
    if case == "no_finder":
        finder = None
    elif case == "ticker_absent":
        finder = rfinder([FINDER_ROWS[1]])
    elif case == "finder_dup":
        finder = rfinder([FINDER_ROWS[0], dict(FINDER_ROWS[0])])
    elif case == "isin_other":
        params["isin"] = "KR7000660001"
    elif case == "market_other":
        params["mkt_id"] = "KSQ"
    elif case == "no_open":
        opened = None
    elif case == "open_absent":
        opened = resp("krxopen.stk_bydd_trd", request_open_api(E),
                      {"OutBlock_1": [{"BAS_DD": E.strftime("%Y%m%d"), "ISU_CD": "000660", "TDD_CLSPRC": "1"}]}, rid="o1")
    elif case == "open_dash":
        opened = ropen_close("-")
    elif case == "close_diff":
        opened = ropen_close("61,100")
    elif case == "open_other_day":
        opened = ropen_close(day=E - timedelta(days=1))
    elif case == "no_close_in_03502":
        rows = [{"TRD_DD": krx_d(E), "PBR": "0.85"}]
    main = resp("krx.MDCSTAT03502", request_03502(params["isin"], params["mkt_id"], E), {"output": rows})
    responses = [r for r in (main, finder, opened) if r is not None]   # 요청은 params와 일치, 검색 응답과 대조
    d = derive("krx03502_field_v2", responses, params, E, CAL)
    assert (d.status, d.value, d.failure_reason) == ("failed", None, reason)


# ── Open API (시가총액·상장주식수) ─────────────────────────────────────────

def ropen(day, rows, rid="o1", market="stk"):
    return resp(f"krxopen.{market}_bydd_trd", request_open_api(day), {"OutBlock_1": rows}, rid=rid)


def orow(day, code, cap="1,000", shrs="100"):
    return {"BAS_DD": day.strftime("%Y%m%d"), "ISU_CD": code, "MKTCAP": cap, "LIST_SHRS": shrs}


def test_open_api_field_rules():
    p = {"field": "MKTCAP", "ticker": "005930"}
    good = [ropen(E, [orow(E, "005930"), orow(E, "000660")]), ropen(E, [orow(E, "035900")], rid="o2", market="ksq")]
    d = derive("krxopen_field_v1", good, p, E, CAL)
    assert (d.status, d.value, d.rows, d.responses) == ("verified", 1000.0, (("o1", 0),), ("o1", "o2"))
    dup = [ropen(E, [orow(E, "005930")]), ropen(E, [orow(E, "005930")], rid="o2", market="ksq")]
    assert derive("krxopen_field_v1", dup, p, E, CAL).failure_reason == "duplicate_rows"
    wrong = [ropen(E, [orow(E, "005930"), orow(E - timedelta(days=1), "000660")])]
    assert derive("krxopen_field_v1", wrong, p, E, CAL).failure_reason == "date_mismatch"
    assert derive("krxopen_field_v1", [ropen(E, [orow(E, "000660")])], p, E, CAL).failure_reason == "ticker_not_found"


# ── 변동성 (01701 FLUC_RT) ─────────────────────────────────────────────────

SESS = window_sessions(CAL, E, 252)
PVOL = {"isin": "KR7005930003", "ticker": "005930"}


def vpack(rows, **kw):
    """01701 응답 + 종목 검색 + 기준 세션 Open API(종가 = 마지막 행 종가)."""
    close = rows[-1].get("TDD_CLSPRC", "1,000") if rows else "1,000"
    return [r01701(rows, **kw), rfinder(), ropen_close(close)]


def vol_rows(sessions=SESS, seed=7):
    import random
    rnd = random.Random(seed)
    rows = []
    for d in sessions:
        f = round(rnd.uniform(-5, 5), 2)
        tp = "1" if f > 0 else "2" if f < 0 else "3"
        rows.append({"TRD_DD": krx_d(d), "FLUC_RT": f"{f:.2f}", "FLUC_TP_CD": tp, "TDD_CLSPRC": "1,000",
                     "TDD_HGPRC": "1,010", "TDD_LWPRC": "990", "CMPPREVDD_PRC": "5" if f > 0 else "-5" if f < 0 else "0",
                     "ACC_TRDVOL": "1,000"})
    return rows


def r01701(rows, rid="v1"):
    return resp("krx.MDCSTAT01701", request_01701("KR7005930003", SESS[0], E), {"output": list(reversed(rows))}, rid=rid)


def test_volatility_uses_251_returns_and_all_rows():
    rows = vol_rows()
    d = derive("volatility_v4_krx_fluc", vpack(rows), PVOL, E, CAL)
    rets = [float(r["FLUC_RT"]) / 100 for r in rows[1:]]
    assert len(SESS) == 252 and len(rets) == 251
    assert d.status == "verified" and d.value == round(statistics.stdev(rets) * math.sqrt(252) * 100, 2)
    assert len(d.rows) == 254 and ("f1", 0) in d.rows and ("o1", 0) in d.rows     # 252행 + 검색 행 + Open API 행


def test_volatility_matches_close_returns_when_rates_are_exact():
    """종가 수익률(현행 방식)과 같은 결과: 등락률이 정확히 표현되는 경우."""
    closes, rows, c = [], [], 10000.0
    import random
    rnd = random.Random(3)
    for k, d in enumerate(SESS):
        f = 0.0 if k == 0 else rnd.choice([-2.5, -1.0, 0.5, 1.5, 3.0])
        c = c * (1 + f / 100)
        closes.append(c)
        rows.append({"TRD_DD": krx_d(d), "FLUC_RT": f"{f:.2f}", "FLUC_TP_CD": "1" if f > 0 else "2" if f < 0 else "3",
                     "TDD_CLSPRC": "1,000"})
    d = derive("volatility_v4_krx_fluc", vpack(rows), PVOL, E, CAL)
    close_rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    assert d.value == round(statistics.stdev(close_rets) * math.sqrt(252) * 100, 2)


def test_volatility_never_drops_rows_to_fit():
    rows = vol_rows()
    cases = [
        ("date_missing", rows[:100] + rows[101:]),                      # 중간 누락
        ("date_missing", rows[:-1]),                                    # E 행 누락
        ("duplicate_rows", rows + [rows[-1]]),
        ("date_outside_calendar", rows + [dict(rows[-1], TRD_DD=krx_d(E + timedelta(days=1)))]),
    ]
    for reason, rs in cases:
        d = derive("volatility_v4_krx_fluc", vpack(rs), PVOL, E, CAL)
        assert (d.status, d.value, d.missing_kind, d.failure_reason) == ("failed", None, None, reason), reason


@pytest.mark.parametrize("change,reason", [
    ({"FLUC_TP_CD": "0", "FLUC_RT": "0.00", "CMPPREVDD_PRC": "0", "ACC_TRDVOL": "15"}, "status_code_inconsistent"),
    ({"FLUC_TP_CD": "0", "FLUC_RT": "0.00", "CMPPREVDD_PRC": "-5", "ACC_TRDVOL": "0"}, "status_code_inconsistent"),
    ({"FLUC_TP_CD": "0", "FLUC_RT": "1.00", "CMPPREVDD_PRC": "0", "ACC_TRDVOL": "0"}, "status_code_inconsistent"),
    ({"FLUC_TP_CD": "", "FLUC_RT": "0.00"}, "unverified_status_code"),
    ({"FLUC_TP_CD": "9", "FLUC_RT": "0.00"}, "unverified_status_code"),
    ({"FLUC_TP_CD": "4", "FLUC_RT": "29.90", "TDD_CLSPRC": "1,000", "TDD_HGPRC": "1,010"}, "status_code_inconsistent"),
    ({"FLUC_TP_CD": "5", "FLUC_RT": "-29.90", "TDD_CLSPRC": "1,000", "TDD_LWPRC": "990"}, "status_code_inconsistent"),
    ({"FLUC_TP_CD": "5", "FLUC_RT": "29.90", "TDD_CLSPRC": "990", "TDD_LWPRC": "990"}, "status_code_inconsistent"),
    ({"FLUC_TP_CD": "1", "FLUC_RT": "-1.00"}, "status_code_inconsistent"),
    ({"FLUC_TP_CD": "3", "FLUC_RT": "-"}, "observation_missing"),
    ({"FLUC_TP_CD": "1", "FLUC_RT": "nan"}, "unparseable_number"),
])
def test_volatility_status_codes_and_missing(change, reason):
    rows = vol_rows()
    rows[50] = dict(rows[50], **change)
    d = derive("volatility_v4_krx_fluc", vpack(rows), PVOL, E, CAL)
    assert (d.status, d.missing_kind, d.failure_reason) == ("failed", None, reason)


def test_volatility_halt_rows_are_kept_as_zero_and_limits_allowed():
    """코드 0(정지로 관측) 구간은 KRX의 0%를 그대로 포함. 4·5는 종가=고가·저가일 때 허용."""
    rows = vol_rows()
    for k in range(100, 140):
        rows[k] = dict(rows[k], FLUC_TP_CD="0", FLUC_RT="0.00", CMPPREVDD_PRC="0", ACC_TRDVOL="0")
    rows[140] = dict(rows[140], FLUC_TP_CD="2", FLUC_RT="-14.33")                # 재개일: KRX 값 그대로
    rows[150] = dict(rows[150], FLUC_TP_CD="4", FLUC_RT="29.90", TDD_CLSPRC="1,300", TDD_HGPRC="1,300")
    rows[160] = dict(rows[160], FLUC_TP_CD="5", FLUC_RT="-29.90", TDD_CLSPRC="700", TDD_LWPRC="700")
    d = derive("volatility_v4_krx_fluc", vpack(rows), PVOL, E, CAL)
    rets = [float(r["FLUC_RT"]) / 100 for r in rows[1:]]
    assert d.status == "verified" and d.value == round(statistics.stdev(rets) * math.sqrt(252) * 100, 2)
    assert d.detail["codes"]["0"] == 40 and d.detail["closes"] == 252 and d.detail["returns"] == 251


def test_volatility_prefix_missing_rules():
    """앞부분만 비면 가용 N행 → N−1개 등락률(첫 행 제외). N<20이면 값 없음 + 사유·관측 수."""
    rows = vol_rows()
    tail = rows[30:]
    tail[0] = dict(tail[0], FLUC_TP_CD="1", FLUC_RT="110.17")                  # 첫 행(공모가 기준 등)은 제외됨
    d = derive("volatility_v4_krx_fluc", vpack(tail), PVOL, E, CAL)
    rets = [float(r["FLUC_RT"]) / 100 for r in tail[1:]]
    assert d.status == "verified" and d.value == round(statistics.stdev(rets) * math.sqrt(252) * 100, 2)
    assert (d.detail["closes"], d.detail["returns"], d.detail["history"]) == (222, 221, "prefix_missing")
    short = derive("volatility_v4_krx_fluc", vpack(rows[-19:]), PVOL, E, CAL)
    assert (short.status, short.value, short.missing_kind) == ("verified", None, "definition_null")
    assert short.detail == {"closes": 19, "returns": 18, "history": "prefix_missing", "first_row": rows[-19]["TRD_DD"].replace("/", "-"),
                            "codes": short.detail["codes"], "last_code": short.detail["last_code"],
                            "reason": "insufficient_history"}
    ok20 = derive("volatility_v4_krx_fluc", vpack(rows[-20:]), PVOL, E, CAL)
    assert ok20.value is not None and ok20.detail["returns"] == 19
    gap = rows[30:100] + rows[101:]                                            # 앞부분 + 중간 누락 → 실패
    assert derive("volatility_v4_krx_fluc", vpack(gap), PVOL, E, CAL).failure_reason == "date_missing"
    assert derive("volatility_v4_krx_fluc", vpack(rows[30:-1]), PVOL, E, CAL).failure_reason == "date_missing"


def test_volatility_calendar_and_request():
    short = make_calendar(start=date(2026, 1, 1), version="short")
    assert derive("volatility_v4_krx_fluc", vpack(vol_rows()), PVOL, E, short).failure_reason == "calendar_unknown"
    bad = resp("krx.MDCSTAT01701", request_01701("KR7005930003", SESS[1], E), {"output": vol_rows()})
    assert derive("volatility_v4_krx_fluc", [bad, rfinder(), ropen_close("1,000")], PVOL, E, CAL).failure_reason == "request_mismatch"


@pytest.mark.parametrize("case,reason", [("no_finder", "isin_link_missing"), ("isin_other", "isin_link_mismatch"),
                                         ("no_open", "close_check_missing"), ("close_diff", "close_mismatch")])
def test_volatility_link_and_close_checks(case, reason):
    rows = vol_rows()
    responses, params = vpack(rows), dict(PVOL)
    if case == "no_finder":
        responses = [responses[0], responses[2]]
    elif case == "isin_other":
        params["isin"] = "KR7000660001"
        responses[0] = resp("krx.MDCSTAT01701", request_01701("KR7000660001", SESS[0], E), {"output": rows}, rid="v1")
    elif case == "no_open":
        responses = responses[:2]
    elif case == "close_diff":
        responses[2] = ropen_close("1,005")
    d = derive("volatility_v4_krx_fluc", responses, params, E, CAL)
    assert (d.status, d.value, d.failure_reason) == ("failed", None, reason)


# ── 이격도·희석률 ──────────────────────────────────────────────────────────

def test_disparity_formula_and_dates():
    s20 = window_sessions(CAL, E, 20)
    closes = [2500 + 10 * i for i in range(20)]
    rows = [{"TRD_DD": krx_d(d), "CLSPRC_IDX": f"{c:,.2f}"} for d, c in zip(s20, closes)]
    r = resp("krx.MDCSTAT00301", request_00301(s20[0], E), {"output": rows})
    d = derive("kospi_disparity_v1_krx00301", [r], {}, E, CAL)
    assert d.value == round((closes[-1] / (sum(closes) / 20) - 1) * 100, 4) and len(d.rows) == 20
    r2 = resp("krx.MDCSTAT00301", request_00301(s20[0], E), {"output": rows[:-1]})
    assert derive("kospi_disparity_v1_krx00301", [r2], {}, E, CAL).failure_reason == "date_missing"


def test_dilution_rules():
    ref = reference_date_1y(E)            # 2025-10-13 (월) 세션 → 그날 사용
    p = {"ticker": "005930"}
    D = "dilution_v2_openapi_prev_session"
    now = ropen(E, [orow(E, "005930", shrs="110")], rid="n")
    prev = ropen(ref, [orow(ref, "005930", shrs="100")], rid="p")
    d = derive(D, [now, prev], p, E, CAL)
    assert (d.value, d.rows) == (10.0, (("n", 0), ("p", 0)))
    absent = ropen(ref, [orow(ref, "000660")], rid="p")
    assert derive(D, [now, absent], p, E, CAL).missing_kind == "absent_at_reference"
    big = ropen(E, [orow(E, "005930", shrs="200")], rid="n")
    assert derive(D, [big, prev], p, E, CAL).missing_kind == "definition_null"
    assert derive(D, [now], p, E, CAL).failure_reason == "response_missing"
    assert derive(D, [prev], p, E, CAL).failure_reason == "response_missing"
    other = ropen(E, [orow(E, "000660")], rid="n")
    assert derive(D, [other, prev], p, E, CAL).failure_reason == "ticker_not_found"


def test_dilution_reference_is_last_session_on_or_before():
    """1년 전 날짜가 비거래일이면 그 이하의 마지막 검증 세션(P). 비거래일 날짜 응답은 쓰지 않는다."""
    ref = reference_date_1y(E)                                   # 2025-10-13 (월)
    hol = make_calendar(holidays=(ref,), version="hol")           # 월요일 휴일 → P = 2025-10-10 (금)
    P = date(2025, 10, 10)
    p, D = {"ticker": "005930"}, "dilution_v2_openapi_prev_session"
    now = ropen(E, [orow(E, "005930", shrs="110")], rid="n")
    atP = ropen(P, [orow(P, "005930", shrs="100")], rid="p")
    d = derive(D, [now, atP], p, E, hol)
    assert (d.status, d.value, d.rows) == ("verified", 10.0, (("n", 0), ("p", 0)))
    at_holiday = ropen(ref, [orow(ref, "005930", shrs="100")], rid="p")
    assert derive(D, [now, at_holiday], p, E, hol).failure_reason == "request_mismatch"
    days = dict(hol.days); days[ref] = CalendarDay(ref, "unknown", "KRX_EQUITY", REF, NOW - timedelta(days=1))
    unknown = CalendarSnapshot("unk", days, hol.notice_check)
    assert derive(D, [now, atP], p, E, unknown).failure_reason == "calendar_unknown"
    days[ref] = CalendarDay(ref, "holiday", "KRX_EQUITY", REF, NOW - timedelta(days=1))
    days[P] = CalendarDay(P, "unknown", "KRX_EQUITY", REF, NOW - timedelta(days=1))
    gap = CalendarSnapshot("gap", days, hol.notice_check)         # 앞으로 가다 unknown → 임의 선택 없음
    assert derive(D, [now, atP], p, E, gap).failure_reason == "calendar_unknown"


# ── 저장·입력 연결 ─────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    s = SignalStore(tmp_path / "src.db"); s.initialize(); s.register_engine("test", "hash", "c", NOW)
    cs = TradingCalendarStore(s.path); cs.save_version(CAL.version, list(CAL.days.values())); cs.save_notice_check(CAL.notice_check)
    return s


def start(s, universe="u"):
    return s.start_run(signal_date="2026-10-14", strategy_id=STRATEGY, trigger="scheduled", version="test",
                       digest="hash", loaded_digest="l", git_commit="c", git_dirty=False, python_executable="p",
                       pid=1, process_started_at=NOW, now=NOW, context=RunContext.start(NOW, CAL),
                       universe_rule=universe, tickers=["005930"],
                       launch=LaunchEvidence(1, "com.investment.signal", "com.investment.signal", NOW))


def record_pack(s, run, raw="0.85"):
    """03502 + 종목 검색 + Open API 원 응답을 실행에 저장."""
    out = []
    for endpoint, req, payload in (
            ("krx.MDCSTAT03502", request_03502("KR7005930003", "STK", E), {"output": [row3(PBR=raw)]}),
            ("krx.finder_stkisu", request_finder(), {"block1": FINDER_ROWS}),
            ("krxopen.stk_bydd_trd", request_open_api(E), {"OutBlock_1": [
                {"BAS_DD": E.strftime("%Y%m%d"), "ISU_CD": "005930", "TDD_CLSPRC": "61,000", "MKTCAP": "1"}]})):
        out.append(s.record_response(run, endpoint=endpoint, request=req, fetched_at=FETCHED, http_status=200,
                                     body=json.dumps(payload).encode()))
    return out


def pbr_input(s, run, raw="0.85"):
    rs = record_pack(s, run, raw)
    return to_market_input("pbr", "krx03502_field_v2", P03502, E, derive("krx03502_field_v2", rs, P03502, E, CAL), FETCHED)


ROW = [dict(ticker="005930", processing_status="scored", normalized_score=95, grade="strong_buy",
            confidence="high", executable=True)]


def finish(s, run, inputs):
    return s.finish_run(run, ROW, "hash", NOW + timedelta(minutes=5), inputs=inputs,
                        signal_inputs={"005930": sorted(inputs)})


def q(s, sql, args=()):
    with sqlite3.connect(s.path) as c:
        return c.execute(sql, args).fetchall()


def test_record_response_preserves_body_and_rows(store):
    run = start(store)
    body = json.dumps({"output": [{"TRD_DD": krx_d(E), "PBR": "0.85"}]}).encode()
    r = store.record_response(run, endpoint="krx.MDCSTAT03502", request=request_03502("KR7005930003", "STK", E),
                              fetched_at=FETCHED, http_status=200, body=body)
    (sha, n, err), = q(store, "SELECT body_sha256,row_count,decode_error FROM source_responses")
    assert sha == hashlib.sha256(body).hexdigest() and n == 1 and err is None
    assert q(store, "SELECT row_index,row_key FROM source_response_rows") == [(0, json.dumps([krx_d(E)]).replace(" ", ""))]
    bad = store.record_response(run, endpoint="krx.MDCSTAT03502", request={}, fetched_at=FETCHED,
                                http_status=200, body=b"<html>error</html>")
    assert bad.rows is None and bad.decode_error == "invalid_json"
    for sql in ("UPDATE source_responses SET http_status=500", "DELETE FROM source_response_rows"):
        with pytest.raises(sqlite3.DatabaseError):
            q(store, sql)


def test_linked_input_completes_and_round_trips(store):
    run = start(store)
    item = pbr_input(store, run)
    assert finish(store, run, {"m": item}) == "completed"
    assert store.audit_input_evidence(run) is None
    (got,) = store.decision_inputs(run)
    assert got == item and got.value == 0.85 and got.rows == item.rows
    assert q(store, "SELECT value_json,definition_id,basis_status FROM run_inputs") == [
        ("0.85", "krx03502_field_v2", "basis_period_unverified")]


@pytest.mark.parametrize("tamper", [
    dict(value=0.86),                                    # 값만 변경
    dict(rows=(("other", 0),)),                          # 근거 행만 변경
    dict(definition_id="krxopen_field_v1"),              # 정의만 변경
    dict(params_json=json.dumps({"field": "PER", "isin": "KR7005930003", "mkt_id": "STK"})),
    dict(missing_kind="source_dash", value=None),        # 실패·값을 원천 결측으로 위장
    dict(fetched_at=FETCHED + timedelta(seconds=1)),     # 시점만 변경
    dict(basis_status=None),
])
def test_value_or_evidence_changed_alone_is_rejected(store, tamper):
    run = start(store)
    item = pbr_input(store, run)
    expected = "input_evidence_mismatch"
    if tamper.get("rows"):
        other = pbr_input(store, run)                    # 같은 값의 다른 응답 행 (입력의 응답 목록 밖)
        tamper, expected = dict(rows=other.rows), "input_evidence_invalid"
    assert finish(store, run, {"m": replace(item, **tamper)}) == "incomplete"
    assert q(store, "SELECT abort_reason FROM signal_runs WHERE run_id=?", (run,)) == [(expected,)]
    assert q(store, "SELECT count(*) FROM run_inputs") == [(0,)]


def test_failure_disguised_as_source_dash_is_rejected(store):
    run = start(store)
    item = pbr_input(store, run, raw="nan")
    assert (item.source_status, item.failure_reason) == ("failed", "unparseable_number")
    fake = replace(item, source_status="verified", missing_kind="source_dash", failure_reason=None)
    assert finish(store, run, {"m": fake}) == "incomplete"


def test_references_must_belong_to_this_run(store):
    other_run = start(store, universe="other")
    foreign = pbr_input(store, other_run)
    run = start(store)
    assert finish(store, run, {"m": foreign}) == "incomplete"
    assert q(store, "SELECT abort_reason FROM signal_runs WHERE run_id=?", (run,)) == [("input_evidence_invalid",)]
    run2 = start(store, universe="u2")
    item = pbr_input(store, run2)
    assert finish(store, run2, {"m": replace(item, rows=((item.responses[0], 5),))}) == "incomplete"
    run3 = start(store, universe="u3")
    item = pbr_input(store, run3)
    assert finish(store, run3, {"m": replace(item, responses=("nope",), rows=(("nope", 0),))}) == "incomplete"


def test_database_rejects_cross_run_reference(store):
    other_run = start(store, universe="other")
    foreign = pbr_input(store, other_run)
    run = start(store)
    with sqlite3.connect(store.path) as c:
        c.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO run_input_responses VALUES(?,?,?)", (run, "m", foreign.responses[0]))


def test_verified_without_evidence_and_fetch_failures(store):
    run = start(store)
    bare = MarketInput("pbr", FETCHED, E, "verified")
    assert finish(store, run, {"m": bare}) == "incomplete"
    assert q(store, "SELECT abort_reason FROM signal_runs WHERE run_id=?", (run,)) == [("input_evidence_missing",)]
    run2 = start(store, universe="u2")
    d = derive("krx03502_field_v2", [], P03502, E, CAL)
    timeout = replace(to_market_input("pbr", "krx03502_field_v2", P03502, E, d, FETCHED), failure_reason="request_timeout")
    assert finish(store, run2, {"m": timeout}) == "completed"     # 실패 기록 자체는 유효(적격성은 별도 판정)
    run3 = start(store, universe="u3")
    assert finish(store, run3, {"m": replace(timeout, failure_reason="made_up")}) == "incomplete"


def test_volatility_input_tracks_every_row(store):
    run = start(store)
    rows = vol_rows()
    body = json.dumps({"output": list(reversed(rows))}).encode()
    r = store.record_response(run, endpoint="krx.MDCSTAT01701", request=request_01701("KR7005930003", SESS[0], E),
                              fetched_at=FETCHED, http_status=200, body=body)
    support = [store.record_response(run, endpoint=e, request=req, fetched_at=FETCHED, http_status=200,
                                     body=json.dumps(payload).encode()) for e, req, payload in (
        ("krx.finder_stkisu", request_finder(), {"block1": FINDER_ROWS}),
        ("krxopen.stk_bydd_trd", request_open_api(E),
         {"OutBlock_1": [{"BAS_DD": E.strftime("%Y%m%d"), "ISU_CD": "005930", "TDD_CLSPRC": rows[-1]["TDD_CLSPRC"]}]}))]
    item = to_market_input("volatility", "volatility_v4_krx_fluc", PVOL, E,
                           derive("volatility_v4_krx_fluc", [r] + support, PVOL, E, CAL), FETCHED)
    assert item.source_status == "verified" and json.loads(item.detail_json)["returns"] == 251
    assert finish(store, run, {"v": replace(item, detail_json=item.detail_json.replace("251", "250"))}) == "incomplete"
    run = start(store, universe="u-ok")
    r = store.record_response(run, endpoint="krx.MDCSTAT01701", request=request_01701("KR7005930003", SESS[0], E),
                              fetched_at=FETCHED, http_status=200, body=body)
    support = [store.record_response(run, endpoint=e, request=req, fetched_at=FETCHED, http_status=200,
                                     body=json.dumps(payload).encode()) for e, req, payload in (
        ("krx.finder_stkisu", request_finder(), {"block1": FINDER_ROWS}),
        ("krxopen.stk_bydd_trd", request_open_api(E),
         {"OutBlock_1": [{"BAS_DD": E.strftime("%Y%m%d"), "ISU_CD": "005930", "TDD_CLSPRC": rows[-1]["TDD_CLSPRC"]}]}))]
    item = to_market_input("volatility", "volatility_v4_krx_fluc", PVOL, E,
                           derive("volatility_v4_krx_fluc", [r] + support, PVOL, E, CAL), FETCHED)
    assert finish(store, run, {"v": item}) == "completed"
    assert store.decision_inputs(run)[0].detail_json == item.detail_json
    assert q(store, "SELECT count(*) FROM run_input_rows WHERE input_id='v'") == [(254,)]
    assert store.audit_input_evidence(run) is None


def test_finish_is_atomic(store, monkeypatch):
    run = start(store)
    item = pbr_input(store, run)
    original = SignalStore._write_inputs

    def broken(self, conn, run_id, rows, inputs, links):
        original(self, conn, run_id, rows, inputs, links)
        raise RuntimeError("simulated crash after input rows")
    monkeypatch.setattr(SignalStore, "_write_inputs", broken)
    with pytest.raises(RuntimeError):
        finish(store, run, {"m": item})
    assert q(store, "SELECT status FROM signal_runs WHERE run_id=?", (run,)) == [("running",)]
    for table in ("run_signals", "run_inputs", "run_input_rows", "run_signal_inputs"):
        assert q(store, f"SELECT count(*) FROM {table}") == [(0,)], table


def test_audit_detects_stored_body_corruption(store):
    run = start(store)
    assert finish(store, run, {"m": pbr_input(store, run)}) == "completed"
    with sqlite3.connect(store.path) as c:      # 트리거를 지운 외부 변조를 흉내 (시험 DB 한정)
        c.execute("DROP TRIGGER immutable_source_responses_UPDATE")
        c.execute("UPDATE source_responses SET body_zlib=?", (__import__("zlib").compress(b'{"output":[]}'),))
    assert store.audit_input_evidence(run) == "input_evidence_invalid"


def test_stage5_2_db_without_detail_column_is_reported(tmp_path):
    path = tmp_path / "s52.db"
    s = SignalStore(path); s.initialize()
    with sqlite3.connect(path) as c:      # 5단계-2 형식 흉내: detail_json 열이 없는 run_inputs
        c.execute("DROP TABLE run_signal_inputs"); c.execute("DROP TABLE run_input_rows")
        c.execute("DROP TABLE run_input_responses")
        c.execute("ALTER TABLE run_inputs DROP COLUMN detail_json")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="detail_json"):
        s.initialize()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_old_isolated_db_is_reported_not_rebuilt(tmp_path):
    path = tmp_path / "old.db"
    s = SignalStore(path)
    with sqlite3.connect(path) as c:      # 4단계 형식의 run_inputs (근거 열 없음)
        c.execute("""CREATE TABLE run_inputs (run_id TEXT NOT NULL, input_id TEXT NOT NULL, input_class TEXT NOT NULL,
                     name TEXT NOT NULL, fetched_at TEXT, source_session_date TEXT, source_status TEXT NOT NULL,
                     source_as_of_time TEXT, value_fetched_at TEXT, filing_list_fetched_at TEXT, report_period_end TEXT,
                     report_type TEXT, rcept_no TEXT, rcept_dt TEXT, rcept_no_linked INTEGER,
                     is_latest_version_at_fetch INTEGER, is_amendment INTEGER, PRIMARY KEY(run_id,input_id))""")
        c.execute("INSERT INTO run_inputs(run_id,input_id,input_class,name,source_status) VALUES('r','i','market','x','unverified')")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="pipeline_schema_upgrade_required"):
        s.initialize()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert q(s, "SELECT count(*) FROM run_inputs") == [(1,)]


def test_definitions_registry_is_closed():
    assert set(DEFINITIONS) == {"krx03502_field_v2", "krxopen_field_v1", "volatility_v4_krx_fluc",
                                "kospi_disparity_v1_krx00301", "dilution_v2_openapi_prev_session"}
    with pytest.raises(ValueError):
        derive("made_up", [], {}, E, CAL)


def test_row_swapped_within_same_response_is_rejected(store):
    """같은 응답 안의 다른 종목 행으로 근거만 바꾼 경우 (행은 존재하지만 정의가 쓴 행이 아님)."""
    run = start(store)
    body = json.dumps({"OutBlock_1": [orow(E, "005930", cap="1,000"), orow(E, "000660", cap="1,000")]}).encode()
    r = store.record_response(run, endpoint="krxopen.stk_bydd_trd", request=request_open_api(E),
                              fetched_at=FETCHED, http_status=200, body=body)
    p = {"field": "MKTCAP", "ticker": "005930"}
    item = to_market_input("market_cap", "krxopen_field_v1", p, E, derive("krxopen_field_v1", [r], p, E, CAL), FETCHED)
    assert item.rows == ((r.response_id, 0),)
    assert finish(store, run, {"m": replace(item, rows=((r.response_id, 1),))}) == "incomplete"
    assert q(store, "SELECT abort_reason FROM signal_runs WHERE run_id=?", (run,)) == [("input_evidence_mismatch",)]


def test_evidence_checked_even_when_processing_failed(store):
    """처리 실패 종목이 있어도 입력 근거 검증이 먼저다: 위변조 입력은 기록되지 않는다."""
    run = start(store)
    item = pbr_input(store, run)
    failed_row = [dict(ROW[0], processing_status="failed", normalized_score=None, grade=None)]
    assert store.finish_run(run, failed_row, "hash", NOW + timedelta(minutes=5), inputs={"m": replace(item, value=9.9)},
                            signal_inputs={"005930": ["m"]}) == "incomplete"
    assert q(store, "SELECT abort_reason FROM signal_runs WHERE run_id=?", (run,)) == [("input_evidence_mismatch",)]
    assert q(store, "SELECT count(*) FROM run_inputs") == [(0,)]
