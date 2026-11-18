from __future__ import annotations

import io
import json
import os
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

_DART_BASE      = "https://opendart.fss.or.kr/api"
_CACHE_DIR      = Path(__file__).parent.parent.parent / "data" / "dart"
_CORP_CODE_FILE = _CACHE_DIR / "corp_codes.json"
_FILINGS_DIR    = _CACHE_DIR / "filings"

_CORP_CODE_TTL_DAYS   = 30
_FILINGS_TTL_HOURS    = 6
_TIMEOUT_SEC          = 15

_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_FILINGS_DIR.mkdir(parents=True, exist_ok=True)


def get_dart_api_key() -> Optional[str]:
    """Public accessor. Use this instead of _load_api_key()."""
    return _load_api_key()


def _load_api_key() -> Optional[str]:
    key = os.getenv("DART_API_KEY")
    if key:
        return key
    env_path = Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("DART_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


def _corp_code_fresh() -> bool:
    if not _CORP_CODE_FILE.exists():
        return False
    age = (datetime.now() - datetime.fromtimestamp(_CORP_CODE_FILE.stat().st_mtime)).days
    return age < _CORP_CODE_TTL_DAYS


def _download_corp_code_map(api_key: str) -> dict[str, str]:
    resp = requests.get(
        f"{_DART_BASE}/corpCode.xml",
        params={"crtfc_key": api_key},
        timeout=_TIMEOUT_SEC,
    )
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        xml_bytes = zf.read("CORPCODE.xml")
    root   = ET.fromstring(xml_bytes)
    result = {
        (sc := (item.findtext("stock_code") or "").strip()): (item.findtext("corp_code") or "").strip()
        for item in root.findall("list")
        if (sc := (item.findtext("stock_code") or "").strip()) and len(sc) == 6
    }
    _CORP_CODE_FILE.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def _get_corp_code_map() -> dict[str, str]:
    api_key = _load_api_key()
    if not api_key:
        return {}
    if _corp_code_fresh():
        try:
            return json.loads(_CORP_CODE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    try:
        return _download_corp_code_map(api_key)
    except Exception:
        try:
            return json.loads(_CORP_CODE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}


def get_corp_code(ticker: str) -> Optional[str]:
    return _get_corp_code_map().get(ticker)


def _filing_cache_path(ticker: str) -> Path:
    return _FILINGS_DIR / f"{ticker}.json"


def _filing_cache_fresh(ticker: str) -> bool:
    path = _filing_cache_path(ticker)
    if not path.exists():
        return False
    age_sec = (datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)).total_seconds()
    return age_sec < _FILINGS_TTL_HOURS * 3600


def get_recent_filings(ticker: str, days: int = 30) -> list[dict]:
    """
    최근 days일 공시 목록 반환 (DART API). 6시간 파일 캐시.
    DART API key 없거나 오류 시 빈 리스트 반환 (graceful degradation).

    반환 필드: rcept_no, rcept_dt (YYYYMMDD), report_nm, corp_name, flr_nm
    """
    if _filing_cache_fresh(ticker):
        try:
            return json.loads(_filing_cache_path(ticker).read_text(encoding="utf-8")).get("filings", [])
        except Exception:
            pass

    api_key = _load_api_key()
    if not api_key:
        return []

    corp_code = get_corp_code(ticker)
    if not corp_code:
        return []

    end_dt   = date.today()
    start_dt = end_dt - timedelta(days=days)

    try:
        resp = requests.get(
            f"{_DART_BASE}/list.json",
            params={
                "crtfc_key":  api_key,
                "corp_code":  corp_code,
                "bgn_de":     start_dt.strftime("%Y%m%d"),
                "end_de":     end_dt.strftime("%Y%m%d"),
                "page_no":    1,
                "page_count": 40,
            },
            timeout=_TIMEOUT_SEC,
        )
        resp.raise_for_status()
        data = resp.json()
        filings = (
            [
                {
                    "rcept_no":  item.get("rcept_no", ""),
                    "rcept_dt":  item.get("rcept_dt", ""),
                    "report_nm": item.get("report_nm", ""),
                    "corp_name": item.get("corp_name", ""),
                    "flr_nm":    item.get("flr_nm", ""),
                }
                for item in data.get("list", [])
            ]
            if data.get("status") == "000"
            else []
        )
    except Exception:
        filings = []

    try:
        _filing_cache_path(ticker).write_text(
            json.dumps({"fetched_at": datetime.now().isoformat(), "filings": filings},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass
    return filings


# ---------------------------------------------------------------------------
# Stage 19: PIT 재무데이터 (접수일 기준)
# ---------------------------------------------------------------------------

_FIN_CACHE_DIR = _CACHE_DIR / "financials"
_FIN_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 분기보고서 유형 키워드 → reprt_code
_REPRT_KEYWORDS: list[tuple[str, str]] = [
    ("사업보고서",  "11011"),
    ("반기보고서",  "11012"),
    ("분기보고서",  "11013"),  # 1Q or 3Q — 구별은 접수월로 처리
    ("등록법인결산", "11011"),
]

# 추출 대상 계정명 (우선순위 순 — 첫 번째 매칭 사용)
_ACCOUNT_TARGETS: dict[str, list[str]] = {
    "revenue":    ["매출액", "수익(매출액)", "영업수익"],
    "op_profit":  ["영업이익", "영업이익(손실)"],
    "net_income": ["당기순이익", "당기순이익(손실)", "연결당기순이익"],
    "total_equity":    ["자본총계", "자본합계"],
    "total_liabilities": ["부채총계", "부채합계"],
}


def _infer_reprt_code(report_nm: str, rcept_dt: str) -> Optional[str]:
    """
    report_nm과 접수월로 reprt_code 추론.
    rcept_dt: YYYYMMDD 문자열
    """
    nm = report_nm
    month = int(rcept_dt[4:6]) if len(rcept_dt) >= 6 else 0

    if "사업보고서" in nm or "결산" in nm:
        return "11011"
    if "반기보고서" in nm:
        return "11012"
    if "분기보고서" in nm:
        # 접수월로 Q1(~6월) vs Q3(~12월) 구분
        return "11013" if month <= 8 else "11014"
    return None


def _infer_bsns_year(reprt_code: str, rcept_dt: str) -> str:
    """보고서 접수일로부터 사업연도 추론."""
    year  = int(rcept_dt[:4])
    month = int(rcept_dt[4:6])
    # 사업보고서는 다음 해 초에 접수 (1~4월 → 전년도 사업)
    if reprt_code == "11011" and month <= 4:
        return str(year - 1)
    return str(year)


def _extract_amount(items: list[dict], candidates: list[str]) -> Optional[float]:
    """
    계정명 후보 리스트에서 첫 번째 매칭 당기 금액 반환.
    DART 금액 단위: 원 (fnlttSinglAcntAll).
    """
    for candidate in candidates:
        for item in items:
            if item.get("account_nm", "").strip() == candidate:
                raw = item.get("thstrm_amount", "")
                if raw:
                    try:
                        return float(str(raw).replace(",", ""))
                    except ValueError:
                        pass
    return None


def _fin_cache_path(corp_code: str, bsns_year: str, reprt_code: str) -> Path:
    return _FIN_CACHE_DIR / f"{corp_code}_{bsns_year}_{reprt_code}.json"


def _fetch_fnltt(
    api_key: str,
    corp_code: str,
    bsns_year: str,
    reprt_code: str,
) -> Optional[list[dict]]:
    """
    DART fnlttSinglAcntAll 호출. 연결(CFS) 우선, 없으면 개별(OFS).
    결과 list 반환 (account 행 목록). 실패 시 None.
    """
    cache_path = _fin_cache_path(corp_code, bsns_year, reprt_code)
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    for fs_div in ("CFS", "OFS"):
        try:
            resp = requests.get(
                f"{_DART_BASE}/fnlttSinglAcntAll.json",
                params={
                    "crtfc_key": api_key,
                    "corp_code": corp_code,
                    "bsns_year": bsns_year,
                    "reprt_code": reprt_code,
                    "fs_div": fs_div,
                },
                timeout=_TIMEOUT_SEC,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "000" and data.get("list"):
                items = data["list"]
                try:
                    cache_path.write_text(
                        json.dumps(items, ensure_ascii=False), encoding="utf-8"
                    )
                except Exception:
                    pass
                return items
        except Exception:
            continue
    return None


def get_quarterly_financials_pit(
    ticker: str,
    as_of_date: str,
) -> Optional[dict]:
    """
    접수일 기준 PIT 재무데이터 반환.

    as_of_date: "YYYY-MM-DD" 또는 "YYYYMMDD"

    PIT 규칙:
      - cutoff = as_of_date (exclusive): rcept_dt < as_of_str 조건 사용.
        당일 공시는 장 마감 전/후 불확실이므로 보수적으로 제외.
        (next_day_open 신호라면 rcept_dt <= as_of_str로 완화 가능 — 별도 검토)
      - 정정공시: rcept_dt 기준으로 as_of_date 이전 최신본(정정 포함) 선택.
        as_of_date 이후 정정은 자동 제외됨 (filter 설계상).
        단, fnlttSinglAcntAll은 보고서 유형(bsns_year+reprt_code) 기반이므로
        as_of_date 이후 제출된 정정공시 수치가 API 응답에 반영될 수 있음 (한계사항).
        → Stage 19 결과 해석 시 명기 필요.

    반환:
      {
        "ticker": str,
        "as_of_date": str,
        "report_nm": str,
        "rcept_dt": str,              # 실제 접수일 (YYYYMMDD)
        "bsns_year": str,
        "reprt_code": str,
        # raw facts (DART 원자료)
        "revenue": float | None,      # 원 단위
        "op_profit": float | None,
        "net_income": float | None,
        "total_equity": float | None,
        "total_liabilities": float | None,
        # derived metrics (계산값)
        "roe": float | None,          # net_income / total_equity * 100
        "debt_ratio": float | None,   # total_liabilities / total_equity * 100
        "op_margin": float | None,    # op_profit / revenue * 100
      }
    None → corp_code 없음 / API 오류 / 해당 기간 보고서 없음
    """
    # as_of_date 정규화
    as_of_str = as_of_date.replace("-", "")  # YYYYMMDD

    api_key = _load_api_key()
    if not api_key:
        return None

    corp_code = get_corp_code(ticker)
    if not corp_code:
        return None

    # 1. list.json — 해당 종목 전체 공시 목록 (최대 10년치)
    #    as_of_date 이전 분기/반기/사업보고서만 필터
    try:
        resp = requests.get(
            f"{_DART_BASE}/list.json",
            params={
                "crtfc_key":  api_key,
                "corp_code":  corp_code,
                "bgn_de":     "20150101",
                "end_de":     as_of_str,
                "pblntf_ty":  "A",        # 정기공시 (사업보고서·분기보고서·반기보고서)
                "page_no":    1,
                "page_count": 100,
            },
            timeout=_TIMEOUT_SEC,
        )
        resp.raise_for_status()
        list_data = resp.json()
    except Exception:
        return None

    if list_data.get("status") != "000":
        return None

    filings = list_data.get("list", [])

    # 2. 대상 보고서 필터 + 가장 최근 것 선택
    qualifying = []
    for f in filings:
        rcept_dt  = f.get("rcept_dt", "")
        report_nm = f.get("report_nm", "")
        if rcept_dt >= as_of_str:   # 보수적 PIT: 당일 공시 제외 (exclusive)
            continue
        reprt_code = _infer_reprt_code(report_nm, rcept_dt)
        if reprt_code is None:
            continue
        qualifying.append({
            "rcept_dt":   rcept_dt,
            "report_nm":  report_nm,
            "reprt_code": reprt_code,
        })

    if not qualifying:
        return None

    # 가장 최근 접수일 기준
    latest = max(qualifying, key=lambda x: x["rcept_dt"])
    reprt_code = latest["reprt_code"]
    bsns_year  = _infer_bsns_year(reprt_code, latest["rcept_dt"])

    # 3. fnlttSinglAcntAll 호출
    items = _fetch_fnltt(api_key, corp_code, bsns_year, reprt_code)
    if items is None:
        return None

    # 4. 계정 추출
    result: dict = {
        "ticker":     ticker,
        "as_of_date": as_of_date,
        "report_nm":  latest["report_nm"],
        "rcept_dt":   latest["rcept_dt"],
        "bsns_year":  bsns_year,
        "reprt_code": reprt_code,
    }
    for key, candidates in _ACCOUNT_TARGETS.items():
        result[key] = _extract_amount(items, candidates)

    # 5. 파생 지표
    eq  = result.get("total_equity")
    lib = result.get("total_liabilities")
    ni  = result.get("net_income")
    rev = result.get("revenue")
    op  = result.get("op_profit")

    result["roe"]        = (ni / eq * 100)  if (ni is not None and eq and eq != 0) else None
    result["debt_ratio"] = (lib / eq * 100) if (lib is not None and eq and eq != 0) else None
    result["op_margin"]  = (op / rev * 100) if (op is not None and rev and rev != 0) else None

    return result
