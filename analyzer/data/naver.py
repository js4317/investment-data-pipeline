# 데이터 없음/수집 실패는 절대 0으로 대체하지 않는다.
# None  → 값이 없음 (unknown). 호출자가 FetchResult.error로 원인 확인.
# 0     → 실제 0 (known zero).
# 이 원칙을 어기면 confidence / AnalysisStatus 구분이 무너진다.
#
# ⚠️  네이버 금융 HTML 구조는 변경될 수 있음.
#     PARSE_ERROR 빈도가 높아지면 셀렉터 재검증 필요.
#     fallback 용도로만 사용 — pykrx 성공 시 이 모듈 호출 불필요.
#
# All caches assume EOD snapshot semantics.
# 장중 실시간 데이터가 필요한 경우 이 모듈은 적합하지 않음.

from __future__ import annotations

import re
import time
from datetime import date
from typing import Optional

import requests
from bs4 import BeautifulSoup
from requests.exceptions import HTTPError, ConnectionError, Timeout
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from constants import DataSource
from analyzer.data.models import (
    FetchResult, FetchErrorReason,
    Fundamentals, MarginLoan,
)
from analyzer.data import cache as _cache

_NETWORK_EXCEPTIONS = (ConnectionError, Timeout, OSError)
_CACHE_MAX_SIZE = 30

# FIFO cache — dict insertion order (Python 3.7+) 보장. _trim()이 가장 오래된 항목 제거.
_fundamentals_cache: dict[str, Fundamentals] = {}
_margin_loan_cache:  dict[str, MarginLoan]   = {}
_quarterly_cache:    dict[str, dict]         = {}

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": "https://finance.naver.com/",
})


# ---------------------------------------------------------------------------
# 내부 유틸
# ---------------------------------------------------------------------------

def _trim(cache: dict) -> None:
    if len(cache) > _CACHE_MAX_SIZE:
        for k in list(cache)[: len(cache) - _CACHE_MAX_SIZE]:
            del cache[k]


def _parse_float(text: Optional[str]) -> Optional[float]:
    """Returns percent-unit float, not ratio. (e.g. '7.5%' → 7.5)"""
    if not text:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", text.strip())
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


@retry(
    retry=retry_if_exception_type(_NETWORK_EXCEPTIONS),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _get(url: str) -> requests.Response:
    time.sleep(0.5)  # rate limiting throttle — tenacity backoff와 독립적
    resp = _SESSION.get(url, timeout=10)
    resp.raise_for_status()
    return resp


def _fetch_page(url: str) -> tuple[Optional[BeautifulSoup], Optional[FetchErrorReason]]:
    """
    HTTP GET + BeautifulSoup 파싱.
    404 → TICKER_NOT_FOUND, 그 외 HTTP 오류 → API_TIMEOUT, 네트워크 오류 → API_TIMEOUT.
    """
    try:
        resp = _get(url)
    except HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return None, FetchErrorReason.TICKER_NOT_FOUND
        return None, FetchErrorReason.API_TIMEOUT
    except _NETWORK_EXCEPTIONS:
        return None, FetchErrorReason.API_TIMEOUT
    return BeautifulSoup(resp.text, "html.parser"), None


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

def fetch_fundamentals(
    ticker: str,
    as_of_date: date,
) -> FetchResult[Fundamentals]:
    """
    fallback-only minimal fundamentals fetcher (네이버 금융).

    pykrx PER=None인 경우에만 호출 권장.
    PBR은 pykrx가 더 정확하므로 항상 None 반환.
    이 함수에 EPS·BPS·매출 등을 추가하지 말 것 — 범위 확장은 별도 모듈로.

    HTML 셀렉터:
      PER       : em#_per
      배당수익률 : em#_dvr
      ROE       : em#_roe  # NOTE: selector unstable — 별도 재무제표 영역에 있을 수 있음
    """
    cache_key = f"{ticker}:{as_of_date}"
    if cache_key in _fundamentals_cache:
        return FetchResult.success(_fundamentals_cache[cache_key])

    url = f"https://finance.naver.com/item/main.naver?code={ticker}"
    soup, err = _fetch_page(url)
    if err is not None:
        return FetchResult.failure(err)

    if soup.select_one("div.h_company") is None:
        return FetchResult.failure(FetchErrorReason.TICKER_NOT_FOUND)

    try:
        # PARSE_ERROR includes selector drift, AttributeError, ValueError
        per_tag = soup.select_one("em#_per")
        dvr_tag = soup.select_one("em#_dvr")
        roe_tag = soup.select_one("em#_roe")  # NOTE: selector unstable

        per       = _parse_float(per_tag.get_text() if per_tag else None)
        div_yield = _parse_float(dvr_tag.get_text() if dvr_tag else None)
        roe       = _parse_float(roe_tag.get_text() if roe_tag else None)
    except Exception:
        return FetchResult.failure(FetchErrorReason.PARSE_ERROR)

    if per is None and div_yield is None and roe is None:
        return FetchResult.failure(FetchErrorReason.PARSE_ERROR)

    result = Fundamentals(
        per=per if per is not None and per != 0 else None,  # 음수 PER 보존 (적자기업 정보)
        pbr=None,
        div_yield=div_yield if div_yield is not None and div_yield >= 0 else None,
        roe=roe,
        per_source=DataSource.NAVER       if per is not None and per != 0       else None,
        div_yield_source=DataSource.NAVER if div_yield is not None and div_yield >= 0 else None,
        roe_source=DataSource.NAVER       if roe is not None                    else None,
        source=DataSource.NAVER,
        as_of_date=as_of_date,
    )
    _fundamentals_cache[cache_key] = result  # insert-then-trim: 정확한 FIFO 보장
    _trim(_fundamentals_cache)
    return FetchResult.success(result)


def fetch_quarterly_financials(
    ticker: str,
    as_of_date: date,
) -> FetchResult[dict]:
    """
    분기별 재무 데이터 (영업이익, 당기순이익, 부채비율).

    소스 우선순위:
      1. Naver Finance mobile API (JSON)
      2. wisereport AJAX HTML fallback

    Returns dict:
        op_quarters : list[Optional[float]] — 영업이익, oldest→newest, actual only (up to 8Q)
        ni_quarters : list[Optional[float]] — 당기순이익
        debt_ratio  : Optional[float]       — 부채비율 (%)

    단위: 소스마다 다름 (mobile=원, wisereport=억원).
    Scoring은 부호·YoY 비교만 사용 — 단위 단독 소스 내 일관성만 요구.
    """
    cache_key = f"{ticker}:{as_of_date}"
    if cache_key in _quarterly_cache:
        return FetchResult.success(_quarterly_cache[cache_key])

    _disk_cached = _cache.get("quarterly", cache_key, _cache.TTL_EARNINGS)
    if _disk_cached is not None:
        _quarterly_cache[cache_key] = _disk_cached
        return FetchResult.success(_disk_cached)

    data = _fetch_quarterly_mobile_api(ticker)
    if data is None:
        data = _fetch_quarterly_wisereport(ticker)

    if data is None:
        return FetchResult.failure(FetchErrorReason.PARSE_ERROR)

    # ni_quarters / debt_ratio / roe 없으면 분기 API → 연간 API 순으로 보완
    ni_missing   = not data.get("ni_quarters") or all(v is None for v in data["ni_quarters"])
    debt_missing = data.get("debt_ratio") is None
    roe_missing  = data.get("roe") is None
    if ni_missing or debt_missing or roe_missing:
        quarter = _fetch_quarter_naver_api(ticker)
        if quarter:
            if ni_missing and quarter.get("ni_quarters"):
                data["ni_quarters"] = quarter["ni_quarters"]
                ni_missing = False
            if debt_missing and quarter.get("debt_ratio") is not None:
                data["debt_ratio"] = quarter["debt_ratio"]
                debt_missing = False
            if roe_missing and quarter.get("roe") is not None:
                data["roe"] = quarter["roe"]
                roe_missing = False

    # 그래도 없으면 연간 API로 2차 보완
    if ni_missing or debt_missing or roe_missing:
        annual = _fetch_annual_naver_api(ticker)
        if annual:
            if ni_missing and annual.get("ni_annual"):
                data["ni_quarters"] = annual["ni_annual"]
            if debt_missing and annual.get("debt_ratio") is not None:
                data["debt_ratio"] = annual["debt_ratio"]
            if roe_missing and annual.get("roe") is not None:
                data["roe"] = annual["roe"]

    _quarterly_cache[cache_key] = data
    _trim(_quarterly_cache)
    _cache.put("quarterly", cache_key, data)
    return FetchResult.success(data)


def _fetch_quarterly_mobile_api(ticker: str) -> Optional[dict]:
    """
    Naver Finance mobile JSON API.

    실제 응답 구조:
      raw["chartIncomeStatement"]["quarter"]["columns"] = [
          ["x",    date1, date2, ...],
          ["매출액", val1,  val2,  ...],
          ["영업이익", val1, val2, ...],   ← 당기순이익 없음
      ]
      raw["chartIncomeStatement"]["quarter"]["trTitleList"] = [
          {"isConsensus": "N" or "Y", ...},  ← N=actual, Y=estimate
      ]

    제한사항:
      - 영업이익(OP)만 제공 (당기순이익 없음) → ni_quarters 전부 None
      - 최근 3~4분기만 노출 (8분기 히스토리 없음) → YoY 비교 불가
      - 부채비율 없음 → debt_ratio None
    """
    try:
        resp = _get(f"https://m.stock.naver.com/api/stock/{ticker}/finance/summary")
        raw = resp.json()

        q_data = raw.get("chartIncomeStatement", {}).get("quarter", {})
        columns = q_data.get("columns", [])
        tr_list = q_data.get("trTitleList", [])

        if len(columns) < 2 or not tr_list:
            return None

        # 실제(isConsensus=N) 분기 인덱스
        actual_indices = [
            i for i, entry in enumerate(tr_list)
            if entry.get("isConsensus", "N") == "N"
        ]
        if not actual_indices:
            return None

        # 영업이익 행 탐색
        op_col: Optional[list] = None
        for col in columns[1:]:
            if col and col[0] == "영업이익":
                op_col = col[1:]  # skip label
                break
        if op_col is None:
            return None

        op_actual = [
            float(op_col[i]) if i < len(op_col) and op_col[i] is not None else None
            for i in actual_indices
        ]
        if not op_actual:
            return None

        return {
            "op_quarters": op_actual,           # oldest→newest, 보통 2~4개
            "ni_quarters": [None] * len(op_actual),  # 이 API에서 제공하지 않음
            "debt_ratio": None,                  # 이 API에서 제공하지 않음
        }
    except Exception:
        return None


def _fetch_quarter_naver_api(ticker: str) -> Optional[dict]:
    """
    Naver Finance 모바일 분기 재무 API.
    당기순이익(분기별), 부채비율(최근), ROE(최근) 제공.
    URL: https://m.stock.naver.com/api/stock/{ticker}/finance/quarter
    """
    try:
        resp = _get(f"https://m.stock.naver.com/api/stock/{ticker}/finance/quarter")
        raw  = resp.json()
        fi   = raw.get("financeInfo", {})

        tr_list  = fi.get("trTitleList", [])
        row_list = fi.get("rowList", [])

        # 실제(isConsensus=N) period key 목록 — 시간순 정렬
        actual_keys = sorted(
            [t["key"] for t in tr_list if t.get("isConsensus", "N") == "N"]
        )
        if not actual_keys:
            return None

        def _row_vals(title: str) -> list[Optional[float]]:
            for row in row_list:
                if row.get("title") == title:
                    cols = row.get("columns", {})
                    return [_parse_float(cols.get(k, {}).get("value")) for k in actual_keys]
            return []

        def _latest(vals: list) -> Optional[float]:
            for v in reversed(vals):
                if v is not None:
                    return v
            return None

        ni_vals   = _row_vals("당기순이익")
        debt_vals = _row_vals("부채비율")
        roe_vals  = _row_vals("ROE")

        return {
            "ni_quarters": ni_vals if any(v is not None for v in ni_vals) else [],
            "debt_ratio":  _latest(debt_vals),
            "roe":         _latest(roe_vals),
        }
    except Exception:
        return None


def _fetch_annual_naver_api(ticker: str) -> Optional[dict]:
    """
    Naver Finance 모바일 연간 재무 API.
    당기순이익(연간), 부채비율, ROE 제공.
    URL: https://m.stock.naver.com/api/stock/{ticker}/finance/annual
    """
    try:
        resp = _get(f"https://m.stock.naver.com/api/stock/{ticker}/finance/annual")
        raw  = resp.json()
        fi   = raw.get("financeInfo", {})

        tr_list = fi.get("trTitleList", [])
        row_list = fi.get("rowList", [])

        # 실제(isConsensus=N) period key 목록 — 최신순
        actual_keys = [
            t["key"] for t in tr_list
            if t.get("isConsensus", "N") == "N"
        ]
        if not actual_keys:
            return None

        def _row_vals(title: str) -> list[Optional[float]]:
            for row in row_list:
                if row.get("title") == title:
                    cols = row.get("columns", {})
                    return [_parse_float(cols.get(k, {}).get("value")) for k in actual_keys]
            return []

        ni_vals   = _row_vals("당기순이익")
        debt_vals = _row_vals("부채비율")
        roe_vals  = _row_vals("ROE")

        def _latest(vals: list) -> Optional[float]:
            for v in vals:
                if v is not None:
                    return v
            return None

        return {
            "ni_annual":  ni_vals if any(v is not None for v in ni_vals) else [],
            "debt_ratio": _latest(debt_vals),
            "roe":        _latest(roe_vals),
        }
    except Exception:
        return None


def _fetch_quarterly_wisereport(ticker: str) -> Optional[dict]:
    """wisereport AJAX 분기 테이블 HTML 파싱. None on any failure."""
    url = (
        f"https://navercomp.wisereport.co.kr/v2/company/ajax/c1010001.aspx"
        f"?cmp_cd={ticker}&frq=Q&rpt=1&finGubun=CONSOLIDATED&frqTyp=Q&cn="
    )
    try:
        soup, err = _fetch_page(url)
        if err or soup is None:
            return None
        table = soup.find("table")
        if not table:
            return None

        header_row = table.find("tr")
        if not header_row:
            return None
        header_cells = header_row.find_all(["th", "td"])

        # estimate 여부: 'E'/'P' 접미사 or 'forecast' CSS class
        is_estimate = [
            cell.get_text(strip=True).endswith(("E", "P"))
            or "forecast" in " ".join(cell.get("class", []))
            for cell in header_cells[1:]  # label 컬럼 제외
        ]
        actual_indices = [i for i, est in enumerate(is_estimate) if not est]

        op_vals: list[Optional[float]] = []
        ni_vals: list[Optional[float]] = []
        debt_val: Optional[float] = None

        for tr in table.find_all("tr")[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if not cells:
                continue
            label = cells[0]
            data = cells[1:]

            def actual_vals(data_cells: list[str]) -> list[Optional[float]]:
                return [
                    _parse_float(data_cells[i]) if i < len(data_cells) else None
                    for i in actual_indices
                ]

            if "영업이익" in label and not op_vals:
                op_vals = actual_vals(data)
            elif "당기순이익" in label and not ni_vals:
                ni_vals = actual_vals(data)
            elif "부채비율" in label and debt_val is None:
                for c in reversed(data):
                    v = _parse_float(c)
                    if v is not None and v > 0:
                        debt_val = v
                        break

        if not op_vals and not ni_vals:
            return None
        return {
            "op_quarters": op_vals[-8:],
            "ni_quarters": ni_vals[-8:],
            "debt_ratio": debt_val,
        }
    except Exception:
        return None


def fetch_margin_loan(
    ticker: str,
    as_of_date: date,
) -> FetchResult[MarginLoan]:
    """
    ticker-level margin loan ratio 수집 (네이버 금융 frgn.naver).

    시장 전체 신용잔고와 다름 — 개별 종목 기준.
    HEAT 과열 경보 #2 및 QUICK_REJECT 기준으로 사용.
    ratio_pct: percent unit (e.g. 7.5 means 7.5%)

    HTML 셀렉터:
      신용잔고율 : table.type2 내 '신용잔고율' 행 — 변경 잦음. PARSE_ERROR 시 재검증 필요.
    """
    cache_key = f"{ticker}:{as_of_date}"
    if cache_key in _margin_loan_cache:
        return FetchResult.success(_margin_loan_cache[cache_key])

    url = f"https://finance.naver.com/item/frgn.naver?code={ticker}"
    soup, err = _fetch_page(url)
    if err is not None:
        return FetchResult.failure(err)

    ratio_pct: Optional[float] = None
    try:
        for row in soup.select("table.type2 tr"):
            header = row.select_one("th")
            if header and "신용잔고율" in header.get_text():
                td = row.select_one("td")
                ratio_pct = _parse_float(td.get_text() if td else None)
                break
    except Exception:
        return FetchResult.failure(FetchErrorReason.PARSE_ERROR)

    if ratio_pct is None:
        return FetchResult.failure(FetchErrorReason.PARSE_ERROR)

    if ratio_pct <= 0:
        return FetchResult.failure(FetchErrorReason.INVALID_ZERO_VALUE)

    result = MarginLoan(
        ratio_pct=ratio_pct,
        source=DataSource.NAVER,
        as_of_date=as_of_date,
    )
    _margin_loan_cache[cache_key] = result  # insert-then-trim: 정확한 FIFO 보장
    _trim(_margin_loan_cache)
    return FetchResult.success(result)
