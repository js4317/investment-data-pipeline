"""5단계-2: 원 응답 파서와 채점 입력 정의 (순수 함수). 조회·DB 쓰기 없음.

설계: docs/design/signal_pipeline.md §13.3·§13.4·§13.12·§13.18.
- 원 응답 본문(bytes)만 해석한다. pykrx 가공 경로("-"→"0")를 거치지 않는다.
- 숫자 파싱 성공 = 유한한 숫자. "-"·빈 값은 원천 결측(missing_kind), 그 밖의 값은 실패.
- 실패를 원천 결측으로 바꾸지 않고, 미확인 코드·날짜 누락 행을 제거해 관측 수를 맞추지 않는다.
- 같은 정의 함수가 수집 시 계산과 저장 시 재검증(재파싱)에 함께 쓰인다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
import math
import re
import statistics

ENDPOINTS = {
    # endpoint: (행 목록 키, 행 식별 필드)
    "krx.MDCSTAT03502": ("output", ("TRD_DD",)),
    "krx.MDCSTAT01701": ("output", ("TRD_DD",)),
    "krx.MDCSTAT00301": ("output", ("TRD_DD",)),
    "krxopen.stk_bydd_trd": ("OutBlock_1", ("BAS_DD", "ISU_CD")),
    "krxopen.ksq_bydd_trd": ("OutBlock_1", ("BAS_DD", "ISU_CD")),
    "krx.finder_stkisu": ("block1", ("short_code", "full_code")),     # 종목코드 ↔ ISIN (요청 파라미터용)
    "dart.list": ("list", ("rcept_no",)),                               # OpenDART 공시검색 list.json
    "dart.fnltt": ("list", ("rcept_no", "sj_div", "account_id", "ord")),  # 단일회사 전체 재무제표
}
DART_ENDPOINTS = ("dart.list", "dart.fnltt")
DART_NO_DATA = "013"          # OpenDART status: 조회된 데이터 없음 → 행 0개(정상 응답)
OPEN_API = ("krxopen.stk_bydd_trd", "krxopen.ksq_bydd_trd")
MISSING_KINDS = ("source_dash", "source_blank", "absent_at_reference", "definition_null")
BASIS_UNVERIFIED_FIELDS = ("PER", "PBR", "EPS", "BPS")
VOL_N = 252        # 세션 252개(E 포함) → 수익률 251개. 현행 krx._calc_annualized_volatility와 같음 (§13.18)
DISP_N = 20
# 01701 FLUC_TP_CD → 등락률 부호. 관측 기반(2026-10-21 S10 조사: 6,085행, 공시 사례 대조) — 공식 코드 사전 미확보.
# 0(정지로 관측)·4·5는 _code_check의 추가 조건까지 만족해야 한다. 빈 값·그 밖의 코드는 실패.
VERIFIED_FLUC_TP = {"1": 1, "2": -1, "3": 0, "4": 1, "5": -1, "0": 0}


class SourceParseError(ValueError):
    """원 응답을 해석할 수 없음. 사유 코드가 메시지."""


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


# ── 응답 해석 ────────────────────────────────────────────────────────────────

def decode_rows(endpoint: str, body: bytes) -> list[dict]:
    if endpoint not in ENDPOINTS:
        raise SourceParseError("unknown_endpoint")
    list_key, key_fields = ENDPOINTS[endpoint]
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise SourceParseError("invalid_json") from None
    if endpoint in DART_ENDPOINTS:
        status = data.get("status") if isinstance(data, dict) else None
        if status == DART_NO_DATA:
            return []
        if status != "000":
            raise SourceParseError(f"dart_status_{status}")
    if not isinstance(data, dict) or not isinstance(data.get(list_key), list):
        raise SourceParseError("unexpected_shape")
    rows = data[list_key]
    for r in rows:
        if not isinstance(r, dict) or any(not isinstance(r.get(f), str) for f in key_fields):
            raise SourceParseError("row_key_missing")
    return rows


def decode_meta(endpoint: str, body: bytes) -> dict | None:
    """응답 최상위의 스칼라 값(OpenDART 쪽수·건수·상태 등). 행 목록은 제외."""
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {k: v for k, v in data.items() if isinstance(v, (str, int, float)) and not isinstance(v, bool)}


def row_key(endpoint: str, row: dict) -> str:
    return _json([row[f] for f in ENDPOINTS[endpoint][1]])


_NUM = re.compile(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")


def parse_number(raw) -> tuple[float | None, str | None]:
    """(값, missing_kind). 유한한 숫자만 성공. NaN·inf·지수 표기·기타 문자열은 SourceParseError."""
    if not isinstance(raw, str):
        raise SourceParseError("non_string_value")
    s = raw.strip()
    if s == "-":
        return None, "source_dash"
    if s == "":
        return None, "source_blank"
    if not _NUM.fullmatch(s):
        raise SourceParseError("unparseable_number")
    v = float(s.replace(",", ""))
    if not math.isfinite(v):
        raise SourceParseError("non_finite_number")
    return v, None


_KRX_DATE = re.compile(r"(\d{4})/(\d{2})/(\d{2})")
_YMD = re.compile(r"(\d{4})(\d{2})(\d{2})")


def _date(raw: str, pattern: re.Pattern) -> date:
    m = pattern.fullmatch(raw.strip()) if isinstance(raw, str) else None
    if not m:
        raise SourceParseError("invalid_date")
    try:
        return date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        raise SourceParseError("invalid_date") from None


def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


# ── 요청 (수집 단계가 같은 함수로 만든다; 정의 검증은 요청 일치까지 본다) ─────────────

def request_03502(isin: str, mkt_id: str, session: date) -> dict:
    return {"isuCd": isin, "mktId": mkt_id, "strtDd": ymd(session), "endDd": ymd(session)}


def request_01701(isin: str, start: date, session: date) -> dict:
    return {"isuCd": isin, "strtDd": ymd(start), "endDd": ymd(session), "adjStkPrc": 1}


def request_00301(start: date, session: date) -> dict:
    return {"indIdx": "1", "indIdx2": "001", "strtDd": ymd(start), "endDd": ymd(session)}


def request_finder() -> dict:
    return {"locale": "ko_KR", "mktsel": "ALL", "searchText": "", "typeNo": 0}


def request_dart_list(corp_code: str, bgn: date, end: date, page_no: int) -> dict:
    """정기공시(A) 전 버전(정정 포함, last_reprt_at=N). 인증키는 전송 계층이 붙이며 기록하지 않는다."""
    return {"corp_code": corp_code, "bgn_de": ymd(bgn), "end_de": ymd(end), "pblntf_ty": "A",
            "last_reprt_at": "N", "page_no": str(page_no), "page_count": "100"}


def request_dart_fnltt(corp_code: str, bsns_year: int, reprt_code: str, fs_div: str) -> dict:
    return {"corp_code": corp_code, "bsns_year": str(bsns_year), "reprt_code": reprt_code, "fs_div": fs_div}


def request_open_api(day: date) -> dict:
    return {"basDd": ymd(day)}


# ── 정의 ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Response:
    """저장된(또는 저장할) 원 응답. rows는 decode_rows 결과, 해석 실패면 None과 decode_error."""
    response_id: str
    endpoint: str
    request: dict
    fetched_at: datetime
    http_status: int
    rows: tuple | None
    decode_error: str | None = None
    meta: dict | None = None


@dataclass(frozen=True)
class Derived:
    status: str                      # 'verified' | 'failed'
    value: float | None
    missing_kind: str | None
    failure_reason: str | None
    basis_status: str | None
    responses: tuple[str, ...]       # 검증에 쓴 응답 전체
    rows: tuple[tuple[str, int], ...]  # 계산·판정에 쓴 행 전체 (response_id, row_index)
    fetched_at: datetime | None      # 쓴 응답 중 최신 조회 시각
    detail: dict | None = None       # 계산 근거 요약(관측 수 등). 저장·재검증 대상


def window_sessions(calendar, end: date, n: int) -> tuple[date, ...] | None:
    """E로 끝나는 세션 n개(오름차순). 달력이 구간에서 unknown이면 None."""
    if calendar.status(end) != "session":
        return None
    out, d = [end], end
    while len(out) < n:
        d = calendar.previous_session(d)
        if d is None:
            return None
        out.append(d)
    return tuple(reversed(out))


def _base(responses, basis=None):
    ids = tuple(sorted(r.response_id for r in responses))
    fetched = max((r.fetched_at for r in responses), default=None)
    def fail(reason):
        return Derived("failed", None, None, reason, basis, ids, (), fetched)
    def ok(value, missing, rows, detail=None):
        if value is not None and (not isinstance(value, float) or not math.isfinite(value)):
            raise SourceParseError("non_finite_value")
        if missing is not None and (value is not None or missing not in MISSING_KINDS):
            raise SourceParseError("invalid_missing_kind")
        return Derived("verified", value, missing, None, basis, ids, tuple(sorted(rows)), fetched, detail)
    return fail, ok


def _usable(responses, endpoints) -> str | None:
    if not responses:
        return "response_missing"
    for r in responses:
        if r.endpoint not in endpoints:
            return "endpoint_mismatch"
        if r.http_status != 200:
            return "http_error"
        if r.rows is None:
            return "parse_failed"
    return None


def _finder_isin(responses, ticker: str):
    """보존된 종목 검색 응답에서 요청 종목의 (ISIN, 시장, (response_id, index)) 또는 사유."""
    if len(responses) != 1:
        return "isin_link_missing"
    r = responses[0]
    if r.request != request_finder():
        return "request_mismatch"
    hits = [(i, row) for i, row in enumerate(r.rows) if row["short_code"] == ticker]
    if len(hits) != 1:
        return "isin_link_missing" if not hits else "duplicate_rows"
    i, row = hits[0]
    return row["full_code"], row.get("marketCode"), (r.response_id, i)


def _link_and_close(responses, ticker: str, isin: str, market: str | None, session: date, row: dict):
    """종목 검색 → ISIN(·시장) 연결과 기준 세션 종가 대조. 사용 행 [(검색 행), (Open API 행)] 또는 사유.
    market이 None이면(01701처럼 요청에 시장이 없음) ISIN만 대조한다."""
    finder = [r for r in responses if r.endpoint == "krx.finder_stkisu"]
    opened = [r for r in responses if r.endpoint in OPEN_API]
    link = _finder_isin(finder, ticker)
    if isinstance(link, str):
        return link
    f_isin, f_market, link_row = link
    if f_isin != isin or (market is not None and f_market != market):
        return "isin_link_mismatch"
    if not opened:
        return "close_check_missing"
    close_rows = _open_api_rows(opened, session)
    if isinstance(close_rows, str):
        return close_rows
    if ticker not in close_rows:
        return "close_check_missing"
    orid, oi, orow = close_rows[ticker]
    if "TDD_CLSPRC" not in row or "TDD_CLSPRC" not in orow:
        return "close_check_missing"
    try:
        c1, m1 = parse_number(row["TDD_CLSPRC"])
        c2, m2 = parse_number(orow["TDD_CLSPRC"])
    except SourceParseError as e:
        return str(e)
    if m1 or m2:
        return "close_check_missing"
    if c1 != c2:
        return "close_mismatch"
    return [link_row, (orid, oi)]


def krx03502_v2(responses, params, session, calendar) -> Derived:
    """PER·PBR·EPS·BPS·DPS·DVD_YLD (출처 검증 강화, 2026-10-21 — 산식 변경 아님).
    응답 묶음: 03502 1개 + 종목 검색 1개 + 같은 세션 Open API(시장별).
    ① 요청 종목 → 검색 응답의 ISIN·시장 = 03502 요청의 ISIN·시장
    ② 03502 정확히 1행, TRD_DD = 기준 세션
    ③ 03502 종가 = 같은 세션·같은 종목의 Open API 종가
    하나라도 불일치하거나 대조 근거가 없으면 verified가 아니다."""
    field, ticker = params["field"], params["ticker"]
    fail, ok = _base(responses, "basis_period_unverified" if field in BASIS_UNVERIFIED_FIELDS else None)
    if (bad := _usable(responses, ("krx.MDCSTAT03502", "krx.finder_stkisu") + OPEN_API)):
        return fail(bad)
    main = [r for r in responses if r.endpoint == "krx.MDCSTAT03502"]
    if len(main) != 1:
        return fail("response_count")
    r = main[0]
    if r.request != request_03502(params["isin"], params["mkt_id"], session):
        return fail("request_mismatch")
    if len(r.rows) != 1:
        return fail("row_count")
    row = r.rows[0]
    try:
        if _date(row["TRD_DD"], _KRX_DATE) != session:
            return fail("date_mismatch")
    except SourceParseError as e:
        return fail(str(e))
    linked = _link_and_close(responses, ticker, params["isin"], params["mkt_id"], session, row)
    if isinstance(linked, str):
        return fail(linked)
    if field not in row:
        return fail("field_missing")
    try:
        value, missing = parse_number(row[field])
    except SourceParseError as e:
        return fail(str(e))
    return ok(value, missing, [(r.response_id, 0)] + linked)


def _open_api_rows(responses, day: date):
    """하루치 Open API 응답(시장별)의 전체 검증 → {ISU_CD: (response_id, index, row)} 또는 사유."""
    out = {}
    for r in responses:
        if r.request != request_open_api(day):
            return "request_mismatch"
        for i, row in enumerate(r.rows):
            try:
                if _date(row["BAS_DD"], _YMD) != day:
                    return "date_mismatch"
            except SourceParseError as e:
                return str(e)
            if row["ISU_CD"] in out:
                return "duplicate_rows"
            out[row["ISU_CD"]] = (r.response_id, i, row)
    return out


def krxopen_field_v1(responses, params, session, calendar) -> Derived:
    """시가총액 등: Open API 시장별 응답(basDd=E). 모든 행 BAS_DD=E, 종목 중복 없음, 대상 1행."""
    fail, ok = _base(responses)
    if (bad := _usable(responses, OPEN_API)):
        return fail(bad)
    rows = _open_api_rows(responses, session)
    if isinstance(rows, str):
        return fail(rows)
    hit = rows.get(params["ticker"])
    if hit is None:
        return fail("ticker_not_found")
    rid, i, row = hit
    if params["field"] not in row:
        return fail("field_missing")
    try:
        value, missing = parse_number(row[params["field"]])
    except SourceParseError as e:
        return fail(str(e))
    return ok(value, missing, [(rid, i)])


def _period_rows(responses, sessions, allow_prefix: bool = False):
    """기간 자료 행 모음 → [(date, response_id, index, row)] 오름차순 또는 사유. 행을 제거하지 않는다.
    allow_prefix: 연속된 앞부분만 빈 경우 허용(신규상장의 증거로 해석하지 않음). E 행은 항상 필요."""
    got = []
    for r in responses:
        for i, row in enumerate(r.rows):
            try:
                got.append((_date(row["TRD_DD"], _KRX_DATE), r.response_id, i, row))
            except SourceParseError as e:
                return str(e)
    got.sort(key=lambda x: (x[0], x[1], x[2]))
    days = [g[0] for g in got]
    if len(set(days)) != len(days):
        return "duplicate_rows"
    if not set(days) <= set(sessions):
        return "date_outside_calendar"
    if set(days) != set(sessions):
        missing = [d for d in sessions if d not in set(days)]
        # 앞쪽만 비면 이력 부족(신규 상장 등) — 처리 규칙 미검증(S11)이므로 실패로 둔다
        prefix = list(sessions[:len(missing)]) == missing
        if not (allow_prefix and prefix and days):
            return "history_short_unverified" if prefix and days else "date_missing"
    if not got or got[-1][0] != sessions[-1]:
        return "date_missing"
    return got


def _code_check(row: dict) -> str | None:
    """01701 등락 구분 코드 검사 (관측 기반 — 공식 코드 사전 미확보, §13.20).
    1 양수 / 2 음수 / 3 0 / 4 양수·종가=고가 / 5 음수·종가=저가 / 0 등락률·전일대비·거래량 모두 0.
    빈 값·그 밖의 코드는 미확인(실패)."""
    tp = row.get("FLUC_TP_CD")
    if tp not in VERIFIED_FLUC_TP:
        return "unverified_status_code"
    fluc, _ = parse_number(row.get("FLUC_RT"))
    if fluc is None:
        return "observation_missing"
    if ((fluc > 0) - (fluc < 0)) != VERIFIED_FLUC_TP[tp]:
        return "status_code_inconsistent"
    if tp == "0":
        chg, _ = parse_number(row.get("CMPPREVDD_PRC"))
        vol, _ = parse_number(row.get("ACC_TRDVOL"))
        if chg != 0 or vol != 0:
            return "status_code_inconsistent"
    elif tp in ("4", "5"):
        close, _ = parse_number(row.get("TDD_CLSPRC"))
        edge, _ = parse_number(row.get("TDD_HGPRC" if tp == "4" else "TDD_LWPRC"))
        if close is None or close != edge:
            return "status_code_inconsistent"
    return None


def volatility_v4_krx_fluc(responses, params, session, calendar) -> Derived:
    """01701 행별 FLUC_RT (2026-10-21 승인 규칙, §13.20). 현행(Naver 수정종가 pct_change) 동작 재현.
    - 창: E로 끝나는 세션 252개(승인 달력). 가용 행 N개 → 첫 행을 뺀 N−1개 등락률, std(ddof=1)×√252×100, 소수 2자리.
    - 앞부분만 연속으로 빈 경우 허용(신규상장의 증거로 해석하지 않음). E 행 필수, 중간·끝 누락은 실패.
    - N < 20 → 값 없음(definition_null, 현행 len(df)<20과 같음). 사유·관측 수는 detail에 기록.
    - 코드 0(정지로 관측)은 KRX가 준 0%를 그대로 포함. 재개일 등락률도 KRX 값 그대로(기준 가정 없음).
    - 출처 연결: 종목 검색 → 요청 ISIN, E 행 종가 = Open API 종가 (v3와 같음)."""
    fail, ok = _base(responses)
    sessions = window_sessions(calendar, session, VOL_N)
    if sessions is None or len(sessions) < max(60, VOL_N // 2):     # 현행 지수 세션 최소 요건 포함
        return fail("calendar_unknown")
    if (bad := _usable(responses, ("krx.MDCSTAT01701", "krx.finder_stkisu") + OPEN_API)):
        return fail(bad)
    main = [r for r in responses if r.endpoint == "krx.MDCSTAT01701"]
    if not main:
        return fail("response_count")
    expected = request_01701(params["isin"], sessions[0], session)
    if any(r.request != expected for r in main):
        return fail("request_mismatch")
    got = _period_rows(main, sessions, allow_prefix=True)
    if isinstance(got, str):
        return fail(got)
    flucs, codes = [], {}
    for _, _, _, row in got:
        try:
            if (bad := _code_check(row)):
                return fail(bad)
            fluc, _ = parse_number(row["FLUC_RT"])
        except SourceParseError as e:
            return fail(str(e))
        flucs.append(fluc)
        codes[row["FLUC_TP_CD"]] = codes.get(row["FLUC_TP_CD"], 0) + 1
    linked = _link_and_close(responses, params["ticker"], params["isin"], None, session, got[-1][3])
    if isinstance(linked, str):
        return fail(linked)
    used = [(rid, i) for _, rid, i, _ in got] + linked
    n = len(got)
    detail = {"closes": n, "returns": n - 1, "history": "full" if n == len(sessions) else "prefix_missing",
              "first_row": got[0][0].isoformat(), "codes": dict(sorted(codes.items())),
              "last_code": got[-1][3]["FLUC_TP_CD"]}                    # 기준일 코드(0 = 거래정지, v8.6.9)
    if n < 20:
        return ok(None, "definition_null", used, dict(detail, reason="insufficient_history"))
    rets = [f / 100 for f in flucs[1:]]
    value = round(float(statistics.stdev(rets) * math.sqrt(252) * 100), 2)
    return ok(value, None, used, detail)


def kospi_disparity_v1_krx00301(responses, params, session, calendar) -> Derived:
    """00301 코스피 종가 20세션(E 포함): (마지막/평균 − 1)×100, 소수 4자리 (현행 fetch_kospi_disparity와 같음)."""
    fail, ok = _base(responses)
    sessions = window_sessions(calendar, session, DISP_N)
    if sessions is None:
        return fail("calendar_unknown")
    if (bad := _usable(responses, ("krx.MDCSTAT00301",))):
        return fail(bad)
    if any(r.request != request_00301(sessions[0], session) for r in responses):
        return fail("request_mismatch")
    got = _period_rows(responses, sessions)
    if isinstance(got, str):
        return fail("date_missing" if got == "history_short_unverified" else got)
    closes = []
    for _, _, _, row in got:
        try:
            v, missing = parse_number(row.get("CLSPRC_IDX"))
        except SourceParseError as e:
            return fail(str(e))
        if missing or v <= 0:
            return fail("observation_missing")
        closes.append(v)
    value = round(float((closes[-1] / (sum(closes) / len(closes)) - 1) * 100), 4)
    # 시장 상태 객체(KospiDisparity)의 현재 지수·평균 — 같은 행에서 계산, 저장 시 재검증 대상 (값 산식 불변)
    detail = {"last_close": closes[-1], "mean_close": round(sum(closes) / len(closes), 4)}
    return ok(value, None, [(rid, i) for _, rid, i, _ in got], detail)


def reference_date_1y(session: date) -> date:
    try:
        return session.replace(year=session.year - 1)
    except ValueError:   # 2월 29일 → 28일 (현행 _calc_dilution_pct와 같음)
        return session.replace(year=session.year - 1, day=28)


def reference_session(calendar, ref: date) -> date | None:
    """1년 전 기준 날짜 이하의 마지막 검증된 거래 세션. 기준 날짜가 거래일이면 그날.
    그 사이에 달력 unknown이 있으면 None(앞 날짜를 임의로 고르지 않는다)."""
    status = calendar.status(ref)
    if status == "session":
        return ref
    if status == "unknown":
        return None
    return calendar.previous_session(ref)


def dilution_v2_openapi_prev_session(responses, params, session, calendar) -> Derived:
    """상장주식수 E vs P(= 달력상 1년 전 날짜 이하의 마지막 검증 세션, §13.19).
    (now−prev)/prev×100 소수 2자리, |값|>50 또는 prev=0이면 값 없음(definition_null, 현행과 같음).
    2026-10-21 탐침(R=2025-10-19 일요일)에서 현행 결과와 10/10 일치 — 모든 휴일로 일반화하지 않음."""
    fail, ok = _base(responses)
    if (bad := _usable(responses, OPEN_API)):
        return fail(bad)
    ref = reference_session(calendar, reference_date_1y(session))
    if ref is None:
        return fail("calendar_unknown")
    now_resp = [r for r in responses if r.request == request_open_api(session)]
    ref_resp = [r for r in responses if r.request == request_open_api(ref)]
    if len(now_resp) + len(ref_resp) != len(responses):
        return fail("request_mismatch")
    if not now_resp or not ref_resp:
        return fail("response_missing")
    now_rows, ref_rows = _open_api_rows(now_resp, session), _open_api_rows(ref_resp, ref)
    for rows in (now_rows, ref_rows):
        if isinstance(rows, str):
            return fail(rows)
    t = params["ticker"]
    if t not in now_rows:
        return fail("ticker_not_found")
    rid, i, row = now_rows[t]
    try:
        now_v, now_missing = parse_number(row.get("LIST_SHRS"))
    except SourceParseError as e:
        return fail(str(e))
    if now_missing:
        return fail("observation_missing")
    if t not in ref_rows:
        return ok(None, "absent_at_reference", [(rid, i)])
    rid2, i2, row2 = ref_rows[t]
    try:
        prev, prev_missing = parse_number(row2.get("LIST_SHRS"))
    except SourceParseError as e:
        return fail(str(e))
    if prev_missing:
        return fail("observation_missing")
    used = [(rid, i), (rid2, i2)]
    if prev == 0:
        return ok(None, "definition_null", used)
    pct = round((now_v - prev) / prev * 100, 2)
    return ok(pct if abs(pct) <= 50 else None, None if abs(pct) <= 50 else "definition_null", used)


DEFINITIONS = {
    "krx03502_field_v2": krx03502_v2,
    "krxopen_field_v1": krxopen_field_v1,
    "volatility_v4_krx_fluc": volatility_v4_krx_fluc,
    "kospi_disparity_v1_krx00301": kospi_disparity_v1_krx00301,
    "dilution_v2_openapi_prev_session": dilution_v2_openapi_prev_session,
}


def all_definitions() -> dict:
    """시장자료 정의 + DART 재무 정의(dart_inputs). dart_inputs가 이 모듈을 import하므로 지연 결합한다."""
    from analyzer.pipeline import dart_inputs
    return {**DEFINITIONS, **dart_inputs.DEFINITIONS}


def derive(definition_id: str, responses: list[Response], params: dict, session: date, calendar) -> Derived:
    registry = all_definitions()
    if definition_id not in registry:
        raise ValueError("unknown definition")
    return registry[definition_id](sorted(responses, key=lambda r: r.response_id), params, session, calendar)


def to_market_input(name: str, definition_id: str, params: dict, session: date, derived: Derived,
                    attempted_at: datetime):
    """정의 결과 → 채점 입력 기록. 값·근거·시점을 한 객체로 묶는다.
    응답이 하나도 없으면(시간 초과 등) 조회 시도 시각을 기록한다."""
    from analyzer.pipeline.entry import MarketInput
    return MarketInput(name, derived.fetched_at or attempted_at, session, derived.status, None,
                       value=derived.value, definition_id=definition_id, params_json=_json(params),
                       missing_kind=derived.missing_kind, basis_status=derived.basis_status,
                       failure_reason=derived.failure_reason, responses=derived.responses, rows=derived.rows,
                       detail_json=None if derived.detail is None else _json(derived.detail))
