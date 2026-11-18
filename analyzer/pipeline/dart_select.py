"""5단계-4 (1): DART 정기공시 선택과 재무 응답의 접수번호 연결 (순수 함수). 조회·DB 쓰기 없음.

설계: docs/design/signal_pipeline.md §2.3·§13.21.
- 공시 목록(list.json, 정기공시 A, 정정 포함 전 버전)을 이 실행에서 끝 쪽까지 받아, 보고 기간마다 최신 접수번호를 고른다.
- 보고 기간은 보고서명의 기준 연월에서 읽는다(접수월 추정 없음). 12월 결산만 지원 — 그 밖은 차단.
- 재무 API(fnlttSinglAcntAll)는 접수번호를 지정할 수 없으므로, 응답 행의 접수번호·회사·연도·보고서 코드가
  선택한 공시와 일치하는지 검증한다. 불일치하면 공식 입력으로 쓰지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re

from analyzer.pipeline.sources import request_dart_fnltt

# 정기공시 보고서명: 앞의 [기재정정]·[첨부정정] 등 꼬리표 0개 이상 + 종류 + (YYYY.MM)
_REPORT = re.compile(r"^((?:\[[^\]]+\]\s*)*)(사업보고서|반기보고서|분기보고서)\s*\((\d{4})\.(\d{2})\)\s*$")
_KIND_MONTH = {("분기보고서", 3): (1, "11013"), ("반기보고서", 6): (2, "11012"),
               ("분기보고서", 9): (3, "11014"), ("사업보고서", 12): (4, "11011")}


@dataclass(frozen=True)
class Filing:
    """한 보고 기간의 선택된(최신) 공시."""
    year: int
    quarter: int
    reprt_code: str
    rcept_no: str
    rcept_dt: date
    report_nm: str
    is_amendment: bool          # 꼬리표가 있거나 같은 기간에 이전 버전이 있음
    versions: int               # 같은 기간 공시 수(정정 포함)
    ref: tuple[str, int]        # (response_id, row_index) — 목록 응답의 해당 행

    @property
    def period_end(self) -> date:
        return date(self.year, self.quarter * 3, 30 if self.quarter in (2, 3) else 31)


def parse_report_nm(report_nm: str):
    """(종류, 연, 월, 꼬리표 유무) 또는 None(정기보고서 아님)."""
    m = _REPORT.match(report_nm.strip()) if isinstance(report_nm, str) else None
    if not m:
        return None
    return m[2], int(m[3]), int(m[4]), bool(m[1].strip())


def _ymd(raw: str) -> date | None:
    if not (isinstance(raw, str) and re.fullmatch(r"\d{8}", raw)):
        return None
    try:
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))
    except ValueError:
        return None


def select_periodic(responses, *, ticker: str, corp_code: str, checked_on: date):
    """공시 목록 응답(쪽 전체) → {(연, 분기): Filing} 또는 실패 사유 문자열.

    검사: 모든 쪽이 같은 조건의 요청이고 1..total_page가 빠짐없이 있음, 건수 = total_count, 접수번호 중복 없음,
    모든 행의 회사 고유번호·종목코드 일치, 접수일 ≤ checked_on, 결산월 12월(아니면 차단)."""
    if not responses:
        return "response_missing"
    base = None
    pages, rows, total_page, total_count = {}, [], None, None
    for r in responses:
        if r.endpoint != "dart.list":
            return "endpoint_mismatch"
        if r.http_status != 200:
            return "http_error"
        if r.rows is None:
            return "parse_failed"
        req = dict(r.request)
        page = req.pop("page_no", None)
        if req.get("corp_code") != corp_code or req.get("pblntf_ty") != "A" or req.get("last_reprt_at") != "N":
            return "request_mismatch"
        if base is None:
            base = req
        elif req != base:
            return "request_mismatch"
        meta = r.meta or {}
        if meta.get("status") == "013":
            tp, tc = 0, 0
        else:
            try:
                tp, tc = int(meta["total_page"]), int(meta["total_count"])
                if int(meta["page_no"]) != int(page):
                    return "page_mismatch"
            except (KeyError, TypeError, ValueError):
                return "page_meta_missing"
        if (total_page, total_count) not in ((None, None), (tp, tc)):
            return "page_meta_inconsistent"
        total_page, total_count = tp, tc
        if page in pages:
            return "duplicate_page"
        pages[page] = r
        rows += [(r.response_id, i, row) for i, row in enumerate(r.rows)]
    if total_page == 0:
        if len(pages) != 1 or rows:
            return "page_meta_inconsistent"
    elif sorted(int(p) for p in pages) != list(range(1, total_page + 1)):
        return "pages_incomplete"
    if len(rows) != total_count:
        return "count_mismatch"
    seen = set()
    groups: dict = {}
    for rid, i, row in rows:
        if row["rcept_no"] in seen:
            return "duplicate_rows"
        seen.add(row["rcept_no"])
        if row.get("corp_code") != corp_code or row.get("stock_code") != ticker:
            return "corp_link_mismatch"
        dt = _ymd(row.get("rcept_dt"))
        if dt is None:
            return "invalid_date"
        if dt > checked_on:
            return "filed_after_check"
        parsed = parse_report_nm(row.get("report_nm"))
        if parsed is None:
            continue                                      # 정기보고서가 아닌 정기공시(예: 등록법인결산서류)
        kind, year, month, tagged = parsed
        if (kind, month) not in _KIND_MONTH:
            return "fiscal_year_unsupported"              # 비12월 결산·결산기 변경 등 — 연월만으로 확정하지 않음
        quarter, code = _KIND_MONTH[(kind, month)]
        groups.setdefault((year, quarter), []).append((dt, row["rcept_no"], tagged, code, row, rid, i))
    out = {}
    for (year, quarter), items in groups.items():
        items.sort(key=lambda x: (x[0], x[1]))
        dt, no, tagged, code, row, rid, i = items[-1]
        out[(year, quarter)] = Filing(year, quarter, code, no, dt, row["report_nm"],
                                      tagged or len(items) > 1, len(items), (rid, i))
    return out


def verify_fnltt(response, filing: Filing, *, corp_code: str, fs_div: str) -> str | None:
    """재무 응답이 선택한 공시에서 나온 것인지 검증. 문제 없으면 None."""
    if response.endpoint != "dart.fnltt":
        return "endpoint_mismatch"
    if response.http_status != 200:
        return "http_error"
    if response.rows is None:
        return "parse_failed"
    if response.request != request_dart_fnltt(corp_code, filing.year, filing.reprt_code, fs_div):
        return "request_mismatch"
    if not response.rows:
        return "statement_missing"                        # 해당 구분(연결/별도)의 재무제표 없음
    for row in response.rows:
        if row.get("rcept_no") != filing.rcept_no:
            return "rcept_no_mismatch"                    # 목록의 최신 버전과 다른 공시의 수치
        if (row.get("corp_code"), row.get("bsns_year"), row.get("reprt_code")) != (
                corp_code, str(filing.year), filing.reprt_code):
            return "report_identity_mismatch"
    return None
