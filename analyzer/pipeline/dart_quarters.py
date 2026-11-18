"""5단계-4 (2): DART 원 응답으로 분기 재무 표 구성 (순수 함수). 조회·DB 쓰기 없음.

설계: docs/design/signal_pipeline.md §13.21·§13.22.
- 공시 선택(dart_select.select_periodic) 결과와 재무 응답(fnlttSinglAcntAll)으로 최근 연속 4개 분기의
  영업이익·순이익(전체·지배주주)과 분기말 재무상태(자본·지배주주 지분·부채)를 만든다.
- 계정은 계정 ID와 재무제표 구분으로 식별한다(계정명 일치 없음). 한 재무제표 안에 같은 ID가 둘 이상이면 실패.
- 손익: 손익계산서(IS)가 있으면 IS, 없으면 포괄손익계산서(CIS). 둘 다 있으면 같은 계정 값이 같아야 한다.
- 1~3분기 손익 = 해당 보고서의 당기 3개월 금액(thstrm_amount). 1분기는 누적 금액과 같아야 한다.
  4분기 손익 = 사업보고서 연간 금액 − 3분기보고서 누적 금액(thstrm_add_amount). 두 보고서 근거를 모두 쓴다.
- 연결·별도를 섞지 않는다: 필요한 보고서 전부가 연결(CFS)이면 연결, 전부 별도(OFS)이면 별도, 아니면 실패.
- 모든 재무 응답은 선택한 공시의 접수번호와 일치해야 한다(verify_fnltt). 불일치면 실패(예: [첨부정정] 원본 반환).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from analyzer.pipeline.dart_exceptions import match_exception
from analyzer.pipeline.dart_select import Filing, verify_fnltt
from analyzer.pipeline.sources import SourceParseError, parse_number

FLOW_IDS = {
    "op": ("dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"),
    "ni": ("ifrs-full_ProfitLoss",),
    "ni_ctrl": ("ifrs-full_ProfitLossAttributableToOwnersOfParent",),   # 지배주주 순이익 (포괄손익 계정과 구분)
}
BS_IDS = {
    "equity": ("ifrs-full_Equity",),
    "equity_ctrl": ("ifrs-full_EquityAttributableToOwnersOfParent",),
    "liabilities": ("ifrs-full_Liabilities",),
}
N_FLOW = 4


class QuarterError(ValueError):
    pass


@dataclass(frozen=True)
class Quarter:
    year: int
    quarter: int
    op: float | None
    ni: float | None
    ni_ctrl: float | None


@dataclass(frozen=True)
class QuarterTable:
    fs_div: str
    quarters: tuple[Quarter, ...]                 # 오래된 → 최근, 연속 N_FLOW개
    bs_end: dict                                  # 최근 분기말 {equity, equity_ctrl, liabilities}
    bs_start: dict                                # 창 시작 직전 분기말 (ROE 기초) — 보고서 없으면 {}
    responses: tuple[str, ...]                    # 사용한 응답 전체(목록 제외)
    rows: tuple[tuple[str, int], ...]             # 사용한 행 전체
    missing: dict = field(default_factory=dict)   # 값 없음 사유 {"2026Q2.op": "account_missing", ...}
    exceptions_used: tuple = ()                   # 적용한 첨부정정 승인 항목 ID (dart_exceptions)


def prev_quarter(y: int, q: int) -> tuple[int, int]:
    return (y, q - 1) if q > 1 else (y - 1, 4)


def _amount(row: dict, key: str):
    raw = row.get(key)
    if raw is None:
        return None
    v, missing = parse_number(raw)
    return None if missing else v


def _krw(row: dict) -> None:
    if row.get("currency") not in (None, "KRW"):
        raise QuarterError("currency_unsupported")


def _pick(rows, sj_divs: tuple[str, ...], ids: tuple[str, ...]):
    """한 재무제표 안에서 계정 ID 하나 → (row_index, row) 또는 None. 둘 이상이면 QuarterError."""
    hits = [(i, r) for i, r in enumerate(rows) if r.get("sj_div") in sj_divs and r.get("account_id") in ids]
    if len(hits) > 1:
        raise QuarterError("account_ambiguous")
    return hits[0] if hits else None


def _flows(resp):
    """보고서 하나의 손익 계정 → {name: (3개월 금액, 누적 금액, (rid, idx)) | None}."""
    rows = resp.rows
    has_is = any(r.get("sj_div") == "IS" for r in rows)
    main = ("IS",) if has_is else ("CIS",)
    out = {}
    for name, ids in FLOW_IDS.items():
        hit = _pick(rows, main, ids)
        if has_is:                                   # IS·CIS 둘 다 있으면 같은 계정 값 일치 확인
            other = _pick(rows, ("CIS",), ids)
            if hit and other and (hit[1].get("thstrm_amount"), hit[1].get("thstrm_add_amount")) != (
                    other[1].get("thstrm_amount"), other[1].get("thstrm_add_amount")):
                raise QuarterError("is_cis_inconsistent")
        if hit is None:
            out[name] = None
            continue
        i, r = hit
        _krw(r)
        try:
            out[name] = (_amount(r, "thstrm_amount"), _amount(r, "thstrm_add_amount"), (resp.response_id, i))
        except SourceParseError as e:
            raise QuarterError(str(e)) from None
    return out


def _bs(resp):
    out = {}
    for name, ids in BS_IDS.items():
        hit = _pick(resp.rows, ("BS",), ids)
        if hit is None:
            out[name] = None
            continue
        i, r = hit
        _krw(r)                                      # 재무상태표도 통화 검사 (손익과 섞지 않음)
        try:
            out[name] = (_amount(r, "thstrm_amount"), (resp.response_id, i))
        except SourceParseError as e:
            raise QuarterError(str(e)) from None
    return out


def plan(selection: dict, include_start: bool = False):
    """선택 결과 → (창 분기들, 기초 분기, 필요한 보고서 {(연, 보고서 코드): Filing}). 수집기와 표 구성이 같이 쓴다.
    include_start: ROE 기초 재무상태(창 직전 분기말) 보고서까지 의존 목록에 넣을지. ROE를 쓰지 않는 입력은 False —
    쓰지 않는 보고서의 검증 실패로 다른 입력이 막히지 않게 한다(의존 = 실제 사용)."""
    if not selection:
        raise QuarterError("no_periodic_reports")
    latest = max(selection)
    window, k = [], latest
    for _ in range(N_FLOW):
        window.append(k)
        k = prev_quarter(*k)
    window.reverse()
    needed: dict = {}
    for y, q in window:
        if (y, q) not in selection:
            raise QuarterError("quarter_missing")
        needed[(y, selection[(y, q)].reprt_code)] = selection[(y, q)]
        if q == 4:                                   # 4분기 = 연간 − 3분기 누적
            if (y, 3) not in selection:
                raise QuarterError("quarter_missing")
            needed[(y, "11014")] = selection[(y, 3)]
    if include_start and k in selection:
        needed.setdefault((k[0], selection[k].reprt_code), selection[k])
    return window, k, needed


def needed_reports(selection: dict, include_start: bool = False):
    """수집용: 필요한 보고서 {(연, 보고서 코드): Filing} 또는 실패 사유."""
    try:
        return plan(selection, include_start)[2]
    except QuarterError as e:
        return str(e)


def build_quarters(selection: dict, fnltt: dict, *, corp_code: str, include_start: bool = False,
                   exceptions: tuple = (), scope: tuple = (None, ())):
    """scope: (입력 정의 ID, 입력이 쓰는 계정들) — 첨부정정 예외는 이 범위 안에서만 적용."""
    """selection: {(연, 분기): Filing}, fnltt: {(연, 보고서 코드): {"CFS": Response, "OFS": Response?}}
    → QuarterTable 또는 실패 사유 문자열."""
    try:
        return _build(selection, fnltt, corp_code, include_start, exceptions, scope)
    except QuarterError as e:
        return str(e)


def _build(selection, fnltt, corp_code, include_start, exceptions, scope):
    window, start_key, needed = plan(selection, include_start)   # start_key: 창 시작 직전 분기 (ROE 기초 지분)
    latest = window[-1]
    start_filing = selection.get(start_key) if include_start else None

    def usable(fs):
        """필요한 보고서 전부를 먼저 검증한다. 검증 오류(접수번호 불일치 등)가 하나라도 있으면 다른 구분으로
        전환하지 않고 실패. 오류 없이 자료 없음·미조회만 있으면 None(전환 후보)."""
        got, absent, errors = {}, False, []
        for key, filing in sorted(needed.items()):
            r = (fnltt.get(key) or {}).get(fs)
            if r is None:
                absent = True; continue
            v = verify_fnltt(r, filing, corp_code=corp_code, fs_div=fs)
            if v == "rcept_no_mismatch" and exceptions:   # 승인 목록의 첨부정정 조합만 허용 (§13.25)
                entry, why = match_exception(exceptions, corp_code=corp_code, filing=filing, fs_div=fs, response=r,
                                             definition_id=scope[0], required_accounts=scope[1])
                if entry is not None:
                    used.append(entry.id); v = None
                elif why:
                    v = why
            if v == "statement_missing":
                absent = True
            elif v is not None:
                errors.append(v)
            else:
                got[key] = r
        if errors:
            raise QuarterError(errors[0])
        return None if absent else got

    used: list = []
    fs_div, resp = "CFS", usable("CFS")
    if resp is None:
        used.clear()
        fs_div, resp = "OFS", usable("OFS")
    if resp is None:
        raise QuarterError("fs_div_unavailable_or_mixed")

    rows, missing = set(), {}
    flows = {key: _flows(r) for key, r in resp.items()}
    quarters = []
    for y, q in window:
        rep = flows[(y, selection[(y, q)].reprt_code)]
        vals = {}
        for name in FLOW_IDS:
            label = f"{y}Q{q}.{name}"
            if q == 4:
                annual, q3 = rep[name], flows[(y, "11014")][name]
                if annual is None or q3 is None or annual[0] is None or q3[1] is None:
                    vals[name] = None; missing[label] = "account_missing"; continue
                vals[name] = annual[0] - q3[1]
                rows.update({annual[2], q3[2]})
            else:
                cur = rep[name]
                if cur is None or cur[0] is None:
                    vals[name] = None; missing[label] = "account_missing"; continue
                if q == 1 and cur[1] is not None and cur[1] != cur[0]:
                    raise QuarterError("q1_cumulative_mismatch")
                vals[name] = cur[0]
                rows.add(cur[2])
        quarters.append(Quarter(y, q, vals["op"], vals["ni"], vals["ni_ctrl"]))

    def bs_at(key):
        b = _bs(resp[(key[0], selection[key].reprt_code)])
        out = {}
        for name, v in b.items():
            if v is None or v[0] is None:
                missing[f"{key[0]}Q{key[1]}.{name}"] = "account_missing"
                out[name] = None
            else:
                out[name] = v[0]; rows.add(v[1])
        return out
    bs_end = bs_at(latest)
    bs_start = bs_at(start_key) if start_filing is not None else {}
    if include_start and start_filing is None:
        missing[f"{start_key[0]}Q{start_key[1]}.bs"] = "report_missing"
    return QuarterTable(fs_div, tuple(quarters), bs_end, bs_start,
                        tuple(sorted(r.response_id for r in resp.values())), tuple(sorted(rows)), missing,
                        tuple(sorted(set(used))))
