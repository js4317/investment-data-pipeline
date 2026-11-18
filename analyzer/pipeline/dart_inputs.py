"""5단계-4 (3): DART 분기 표 → 채점 입력 정의 (순수 함수). sources.DEFINITIONS에 등록된다.

설계: docs/design/signal_pipeline.md §13.22.
응답 묶음 = 공시 목록 전 쪽(dart.list) + 필요한 재무 응답(dart.fnltt). 저장 시 같은 함수로 재검증된다.
- 공시 선택 확인일 = 목록 응답 중 최신 조회일(KST). 신선도 판정도 이 날짜 기준.
- 신선도(제출기한): 확인일 전에 제출기한이 지난 가장 최근 보고기간보다 오래된 자료만 있으면 financial_stale.
  기한 당일은 미제출로 보지 않는다. 기한이 회사별로 달라질 수 있는 구간(분·반기 45~60일)에서 해당 기간이
  아직 없으면 filing_deadline_unknown. 둘 다 입력 실패(공식 적격성 차단).
- 채점 계산은 현행(run._auto_earnings / _auto_risk의 Naver 경로) 정의를 최근 4분기에만 적용한다.
  8분기가 있어도 전년 동기 비교로 바꾸지 않는다(계산 방식 ID 기록).
"""
from __future__ import annotations

from datetime import date, timedelta

from analyzer.pipeline.dart_exceptions import APPROVED as APPROVED_EXCEPTIONS, EXCEPTIONS_VERSION
from analyzer.pipeline.dart_quarters import QuarterTable, build_quarters, prev_quarter
from analyzer.pipeline.dart_select import select_periodic
from analyzer.pipeline.sources import Derived, _base

# 제출기한 표 (판정 버전). (표준 기한 일수, 알려진 예외 포함 최대 일수)
# 근거: DART 정기공시 제출기한 안내(https://dart.fss.or.kr/info/main.do?menu=210, 2026-10-22 확인) —
# 사업보고서 사업연도 경과 후 90일, 분·반기보고서 분기·반기 경과 후 45일(최초 연결 공시 등 60일 예외).
# 회사별 예외 확인 근거가 없으므로 표준~최대 사이 구간은 "확인 불가"로 다룬다.
DEADLINE_TABLE_ID = "dart_deadlines_v1_2026-10-22"
DEADLINES = {"annual": (90, 90), "quarterly": (45, 60)}
_MAX_LOOKBACK = 8


def period_end(y: int, q: int) -> date:
    return date(y, q * 3, 30 if q in (2, 3) else 31)


def freshness(latest: tuple[int, int], checked_on: date) -> str | None:
    """None(신선) / 'financial_stale' / 'filing_deadline_unknown'."""
    y, q = (checked_on.year, (checked_on.month - 1) // 3 + 1)
    key = prev_quarter(y, q)                      # 확인일 직전에 끝난 분기부터 거슬러 올라간다
    ambiguous = None
    for _ in range(_MAX_LOOKBACK):
        end = period_end(*key)
        std, mx = DEADLINES["annual" if key[1] == 4 else "quarterly"]
        if checked_on > end + timedelta(days=mx):          # 어떤 기한을 적용해도 지남 → 이 기간 이상 필요
            if latest < key:
                return "financial_stale"
            # 더 최근 기간이 '표준 기한은 지났지만 예외 기한 안'인데 아직 없으면 판정 불가
            return "filing_deadline_unknown" if ambiguous and latest < ambiguous else None
        if checked_on > end + timedelta(days=std) and ambiguous is None:
            ambiguous = key                                 # 표준 기한은 지났지만 예외 기한 안
        key = prev_quarter(*key)
    return "filing_deadline_unknown"


def _split(responses):
    lists = [r for r in responses if r.endpoint == "dart.list"]
    fn = {}
    for r in responses:
        if r.endpoint == "dart.fnltt":
            key = (int(r.request["bsns_year"]), r.request["reprt_code"])
            if r.request["fs_div"] in fn.setdefault(key, {}):
                return lists, None
            fn[key][r.request["fs_div"]] = r
    return lists, fn


# 입력 정의별 사용 계정 (첨부정정 예외의 검증 범위 판정용, §13.25)
DEFINITION_ACCOUNTS = {
    "dart_earnings_proxy_v1": ("op",),
    "dart_loss_quarters_v1": ("ni",),
    "dart_debt_ratio_v1": ("equity", "liabilities"),
    "dart_eq_ratio_v1": ("op", "ni"),
    # ROE: 연결이면 지배주주 순이익·지분, 별도면 전체 순이익·자본 — 예외는 네 계정 모두 검증된 항목만 (보수적)
    "dart_roe_v1": ("ni", "ni_ctrl", "equity", "equity_ctrl"),
}
INCLUDE_START = {"dart_roe_v1"}                       # 기초 재무상태(창 직전 분기말) 보고서까지 의존

# 결정 B 개정(validation_exclusion_v1, §13.29): 공시 내용이 검증 조건을 만족하지 못한 경우만 "데이터 검증 제외".
# 여기에 없는 사유(통신·요청 한도·응답 형식·페이지 불일치·회사 코드 없음 등)는 실행 오류 → 실행 incomplete.
FIN_EXCLUSION_REASONS = frozenset({
    "rcept_no_mismatch", "report_identity_mismatch", "statement_missing", "exception_evidence_changed",
    "fs_div_unavailable_or_mixed", "currency_unsupported", "account_ambiguous", "is_cis_inconsistent",
    "q1_cumulative_mismatch", "quarter_missing", "no_periodic_reports", "fiscal_year_unsupported",
    "financial_stale", "filing_deadline_unknown",
})


def _table(responses, params, include_start: bool = False, definition_id: str | None = None):
    """→ (QuarterTable, 사용 목록 행, 상세) 또는 실패 사유."""
    lists, fn = _split(responses)
    if fn is None:
        return "duplicate_responses"
    if not lists:
        return "response_missing"
    if any(r.endpoint not in ("dart.list", "dart.fnltt") for r in responses):
        return "endpoint_mismatch"
    checked_on = max(r.fetched_at for r in lists).date()
    sel = select_periodic(lists, ticker=params["ticker"], corp_code=params["corp_code"], checked_on=checked_on)
    if isinstance(sel, str):
        return sel
    if not sel:
        return "no_periodic_reports"
    fresh = freshness(max(sel), checked_on)
    if fresh:
        return fresh
    table = build_quarters(sel, fn, corp_code=params["corp_code"], include_start=include_start,
                           exceptions=APPROVED_EXCEPTIONS,
                           scope=(definition_id, DEFINITION_ACCOUNTS.get(definition_id, ())))
    if isinstance(table, str):
        return table
    used_keys = {(q.year, q.quarter) for q in table.quarters} | {max(sel)}
    used_keys |= {(y, 3) for y, q in used_keys if q == 4}
    start = prev_quarter(table.quarters[0].year, table.quarters[0].quarter)
    if include_start and start in sel:
        used_keys.add(start)
    list_rows = [sel[k].ref for k in sorted(used_keys) if k in sel]
    detail = {"fs_div": table.fs_div, "latest_period": "%dQ%d" % max(sel), "checked_on": checked_on.isoformat(),
              "deadline_table": DEADLINE_TABLE_ID,
              "rcept_nos": {"%dQ%d" % k: sel[k].rcept_no for k in sorted(used_keys) if k in sel}}
    if table.missing:
        detail["missing"] = dict(sorted(table.missing.items()))
    if table.exceptions_used:                         # 예외 사용 기록 (응답의 원본 접수번호는 응답 근거에 그대로 남는다)
        detail["exceptions"] = {"version": EXCEPTIONS_VERSION, "used": list(table.exceptions_used),
                                "definition": definition_id, "accounts": list(DEFINITION_ACCOUNTS.get(definition_id, ()))}
    return table, list_rows, detail


def _wrap(definition_id, calc_id, compute):
    def definition(responses, params, session, calendar) -> Derived:
        fail, ok = _base(responses)
        got = _table(responses, params, include_start=definition_id in INCLUDE_START, definition_id=definition_id)
        if isinstance(got, str):
            return fail(got)
        table, list_rows, detail = got
        value, missing, extra = compute(table)
        return ok(value, missing, list(list_rows) + list(table.rows), dict(detail, calc=calc_id, **extra))
    return definition


def _earnings_proxy(t: QuarterTable):
    """현행 _auto_earnings의 '히스토리 부족' 분기 그대로: 최근 4분기 OP>0 비율 × 4, 반올림(half-up), 최대 4."""
    op = [q.op for q in t.quarters]
    available = [v for v in op if v is not None]
    if not available:
        return None, "definition_null", {"op_quarters": op}
    met = int(sum(1 for v in available if v > 0) / len(available) * 4 + 0.5)
    return float(min(met, 4)), None, {"op_quarters": op}


def _loss_quarters(t: QuarterTable):
    ni = [q.ni for q in t.quarters]
    return float(sum(1 for v in ni if v is not None and v < 0)), None, {"ni_quarters": ni}


def _debt_ratio(t: QuarterTable):
    liab, eq = t.bs_end.get("liabilities"), t.bs_end.get("equity")
    if liab is None or eq is None or eq <= 0:
        return None, "definition_null", {"liabilities": liab, "equity": eq}
    return round(liab / eq * 100, 4), None, {"liabilities": liab, "equity": eq}


def _eq_ratio(t: QuarterTable):
    """현행 _auto_risk: 4분기 OP 합 / 4분기 NI 합, 둘 다 자료가 있고 NI 합 > 0일 때만."""
    op = [q.op for q in t.quarters]
    ni = [q.ni for q in t.quarters]
    op_sum = sum(v for v in op if v is not None)
    ni_sum = sum(v for v in ni if v is not None)
    if any(v is not None for v in op) and any(v is not None for v in ni) and ni_sum > 0:
        return op_sum / ni_sum, None, {"op_sum": op_sum, "ni_sum": ni_sum}
    return None, "definition_null", {"op_sum": op_sum, "ni_sum": ni_sum}


def _roe(t: QuarterTable):
    """§13.21 ROE 비교시험 정의 — 운영 보너스 반영은 보류.
    연결: 최근 4분기 지배주주 순이익 합 ÷ 기초·기말 평균 지배주주 지분 × 100.
    별도: 최근 4분기 전체 순이익 합 ÷ 기초·기말 평균 전체 자본 × 100 (보고기업 단위 ROE, 명시적 허용).
    필요한 값이 하나라도 없거나 기초·기말 지분 ≤ 0이면 값 없음. 다른 계정으로 대체하지 않는다
    (보통주주 귀속 순이익 ID 포함 안 함)."""
    if t.fs_div == "CFS":
        basis, ni, eq_key = "consolidated_controlling", [q.ni_ctrl for q in t.quarters], "equity_ctrl"
    else:
        basis, ni, eq_key = "separate_reporting_entity", [q.ni for q in t.quarters], "equity"
    start, end = t.bs_start.get(eq_key), t.bs_end.get(eq_key)
    extra = {"basis": basis, "ni_quarters": ni, "equity_start": start, "equity_end": end}
    if any(v is None for v in ni) or start is None or end is None or start <= 0 or end <= 0:
        return None, "definition_null", extra
    return round(sum(ni) / ((start + end) / 2) * 100, 4), None, extra


DEFINITIONS = {
    "dart_earnings_proxy_v1": _wrap("dart_earnings_proxy_v1", "positive_op_quarters_4q_v1", _earnings_proxy),
    "dart_loss_quarters_v1": _wrap("dart_loss_quarters_v1", "loss_quarters_4q_v1", _loss_quarters),
    "dart_debt_ratio_v1": _wrap("dart_debt_ratio_v1", "debt_ratio_latest_bs_v1", _debt_ratio),
    "dart_eq_ratio_v1": _wrap("dart_eq_ratio_v1", "op_over_ni_4q_v1", _eq_ratio),
    "dart_roe_v1": _wrap("dart_roe_v1", "roe_4q_avg_equity_v1", _roe),
}
