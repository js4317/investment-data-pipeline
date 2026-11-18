"""
Stage 19: DART PIT 재무데이터 수집 스크립트 (최적화)

API 호출 최소화:
  - list.json    : ticker당 1회 (135건)
  - fnlttSinglAcntAll : (corp_code, bsns_year, reprt_code) 고유 조합만
  - 예상 소요 시간: ~10~20분

실행:
    cd ~/Desktop/investment
    set -a; source .env; set +a
    python3 scripts/collect_dart_pit.py [--dry-run] [--ticker XXXX] [--force]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from analyzer.data.dart import (
    _load_api_key, _get_corp_code_map,
    _infer_reprt_code, _infer_bsns_year,
    _fetch_fnltt, _extract_amount, _ACCOUNT_TARGETS,
    _DART_BASE, _TIMEOUT_SEC,
)
import requests

DB_PATH = ROOT / "database" / "research.db"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS dart_pit_financials (
    ticker            TEXT NOT NULL,
    as_of_date        TEXT NOT NULL,
    report_nm         TEXT,
    rcept_dt          TEXT,
    bsns_year         TEXT,
    reprt_code        TEXT,
    thstrm_nm         TEXT,
    revenue           REAL,
    op_profit         REAL,
    net_income        REAL,
    total_equity      REAL,
    total_liabilities REAL,
    roe               REAL,
    debt_ratio        REAL,
    op_margin         REAL,
    fetched_at        TEXT NOT NULL,
    error             TEXT,
    PRIMARY KEY (ticker, as_of_date)
)
"""


# ---------------------------------------------------------------------------
# Step 1: ticker당 공시 목록 1회 조회
# ---------------------------------------------------------------------------

def fetch_filing_list(api_key: str, corp_code: str) -> list[dict]:
    """
    ticker 전체 공시 목록 (정기공시 pblntf_ty=A, 2015~현재).
    이미 dart.py의 파일 캐시와 독립적으로 메모리 캐시만 사용.
    """
    try:
        resp = requests.get(
            f"{_DART_BASE}/list.json",
            params={
                "crtfc_key":  api_key,
                "corp_code":  corp_code,
                "bgn_de":     "20150101",
                "end_de":     datetime.now().strftime("%Y%m%d"),
                "pblntf_ty":  "A",
                "page_no":    1,
                "page_count": 100,
            },
            timeout=_TIMEOUT_SEC,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "000":
            return data.get("list", [])
    except Exception:
        pass
    return []


def pick_report_for_date(filings: list[dict], as_of_str: str) -> dict | None:
    """
    as_of_str(YYYYMMDD) 이전 (exclusive) 최신 정기보고서 선택.
    """
    qualifying = []
    for f in filings:
        rcept_dt  = f.get("rcept_dt", "")
        report_nm = f.get("report_nm", "")
        if rcept_dt >= as_of_str:   # 보수적 PIT: 당일 제외
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
    return max(qualifying, key=lambda x: x["rcept_dt"])


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def upsert(conn, ticker, signal_date, result, err):
    now = datetime.now().isoformat()
    if result:
        eq  = result.get("total_equity")
        lib = result.get("total_liabilities")
        ni  = result.get("net_income")
        rev = result.get("revenue")
        op  = result.get("op_profit")
        roe        = (ni  / eq  * 100) if (ni  is not None and eq  and eq  != 0) else None
        debt_ratio = (lib / eq  * 100) if (lib is not None and eq  and eq  != 0) else None
        op_margin  = (op  / rev * 100) if (op  is not None and rev and rev != 0) else None

        conn.execute(
            """INSERT OR REPLACE INTO dart_pit_financials
               (ticker,as_of_date,report_nm,rcept_dt,bsns_year,reprt_code,thstrm_nm,
                revenue,op_profit,net_income,total_equity,total_liabilities,
                roe,debt_ratio,op_margin,fetched_at,error)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ticker, signal_date,
             result.get("report_nm"), result.get("rcept_dt"),
             result.get("bsns_year"), result.get("reprt_code"),
             result.get("thstrm_nm"),
             result.get("revenue"), result.get("op_profit"),
             result.get("net_income"), result.get("total_equity"),
             result.get("total_liabilities"),
             roe, debt_ratio, op_margin, now, None),
        )
    else:
        conn.execute(
            """INSERT OR REPLACE INTO dart_pit_financials
               (ticker,as_of_date,fetched_at,error)
               VALUES (?,?,?,?)""",
            (ticker, signal_date, now, err or "no_data"),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ticker",  default=None)
    parser.add_argument("--force",   action="store_true")
    args = parser.parse_args()

    api_key = _load_api_key()
    if not api_key:
        print("ERROR: DART_API_KEY 없음")
        return

    corp_code_map = _get_corp_code_map()

    conn = sqlite3.connect(DB_PATH)
    conn.execute(CREATE_TABLE_SQL)
    # 기존 테이블에 thstrm_nm 컬럼이 없으면 추가 (migration)
    existing_cols = [r[1] for r in conn.execute("PRAGMA table_info(dart_pit_financials)").fetchall()]
    if "thstrm_nm" not in existing_cols:
        conn.execute("ALTER TABLE dart_pit_financials ADD COLUMN thstrm_nm TEXT")
        print("Migration: thstrm_nm 컬럼 추가됨")
    conn.commit()

    # 수집 대상 (ticker, signal_date) 목록
    if args.ticker:
        rows = conn.execute(
            "SELECT DISTINCT ticker, signal_date FROM retro_calibration WHERE ticker=? ORDER BY signal_date",
            (args.ticker,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT DISTINCT ticker, signal_date FROM retro_calibration ORDER BY ticker, signal_date"
        ).fetchall()

    # ticker별 그룹핑
    by_ticker: dict[str, list[str]] = defaultdict(list)
    for ticker, signal_date in rows:
        by_ticker[ticker].append(signal_date)

    total_tickers = len(by_ticker)
    print(f"대상: {total_tickers}개 ticker, {len(rows)}개 (ticker, date) 쌍")
    if args.dry_run:
        print("[DRY-RUN] 첫 3개 ticker만 처리, DB 저장 없음")

    ok = skip = fail = 0
    ticker_list = list(by_ticker.items())
    if args.dry_run:
        ticker_list = ticker_list[:3]

    # (corp_code, bsns_year, reprt_code) → financial items 캐시
    fnltt_cache: dict[tuple, list | None] = {}

    for t_idx, (ticker, signal_dates) in enumerate(ticker_list, 1):
        corp_code = corp_code_map.get(ticker)
        if not corp_code:
            print(f"  [{t_idx}/{total_tickers}] {ticker}: corp_code 없음 → 전체 skip")
            for sd in signal_dates:
                if not args.dry_run:
                    upsert(conn, ticker, sd, None, "no_corp_code")
                fail += len(signal_dates)
            continue

        # Step 1: ticker당 공시 목록 1회
        print(f"  [{t_idx}/{total_tickers}] {ticker} — 공시 목록 조회...", end=" ", flush=True)
        filings = fetch_filing_list(api_key, corp_code)
        print(f"{len(filings)}건")
        time.sleep(0.5)   # list.json rate limit

        for signal_date in signal_dates:
            as_of_str = signal_date.replace("-", "")

            # 이미 수집됐으면 skip
            if not args.force and not args.dry_run:
                existing = conn.execute(
                    "SELECT 1 FROM dart_pit_financials WHERE ticker=? AND as_of_date=?",
                    (ticker, signal_date),
                ).fetchone()
                if existing:
                    skip += 1
                    continue

            # Step 2: 해당 날짜에 적용할 보고서 선택 (메모리 연산)
            report = pick_report_for_date(filings, as_of_str)
            if report is None:
                if args.dry_run:
                    print(f"    {signal_date}: no_report")
                else:
                    upsert(conn, ticker, signal_date, None, "no_report")
                fail += 1
                continue

            bsns_year  = _infer_bsns_year(report["reprt_code"], report["rcept_dt"])
            cache_key  = (corp_code, bsns_year, report["reprt_code"])

            # Step 3: fnlttSinglAcntAll — 고유 조합만 호출 (캐시 활용)
            if cache_key not in fnltt_cache:
                items = _fetch_fnltt(api_key, corp_code, bsns_year, report["reprt_code"])
                fnltt_cache[cache_key] = items
                time.sleep(0.7)   # fnlttSinglAcntAll rate limit

            items = fnltt_cache[cache_key]
            if items is None:
                if args.dry_run:
                    print(f"    {signal_date}: fnltt_failed ({report['report_nm'][:20]})")
                else:
                    upsert(conn, ticker, signal_date, None, "fnltt_failed")
                fail += 1
                continue

            # Step 4: 계정 추출 + 기간명 보존
            thstrm_nm = next(
                (item.get("thstrm_nm") for item in items if item.get("thstrm_nm")),
                None,
            )
            result = {
                "report_nm":  report["report_nm"],
                "rcept_dt":   report["rcept_dt"],
                "bsns_year":  bsns_year,
                "reprt_code": report["reprt_code"],
                "thstrm_nm":  thstrm_nm,
            }
            for key, candidates in _ACCOUNT_TARGETS.items():
                result[key] = _extract_amount(items, candidates)

            if args.dry_run:
                eq  = result.get("total_equity")
                ni  = result.get("net_income")
                lib = result.get("total_liabilities")
                roe = round(ni / eq * 100, 2) if (ni and eq and eq != 0) else None
                dr  = round(lib / eq * 100, 2) if (lib and eq and eq != 0) else None
                print(f"    {signal_date}: {report['report_nm'][:20]}, ROE={roe}, D/E={dr}")
                ok += 1
                continue

            upsert(conn, ticker, signal_date, result, None)
            ok += 1

        if t_idx % 10 == 0 or t_idx == total_tickers:
            print(f"    → 진행: {t_idx}/{total_tickers} tickers | OK={ok} SKIP={skip} FAIL={fail}")

    conn.close()
    print(f"\n완료: OK={ok} SKIP={skip} FAIL={fail}")
    if not args.dry_run:
        print("다음: python3 scripts/analyze_dart_pit.py")


if __name__ == "__main__":
    main()
