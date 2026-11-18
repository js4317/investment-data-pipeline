#!/usr/bin/env python3
"""
과거 OHLCV 수집 (2022-12-01 ~ 2025-12-31)
==========================================
기존 _collect_via_open_api 인프라 사용 (KRX Open API, KRX_OPEN_API_KEY 필요).
시장 전체(KOSPI + KOSDAQ + KOSPI지수) 날짜당 3회 호출.

사용법:
  cd ~/Desktop/investment
  set -a; source .env; set +a
  python3 research/collect_historical_ohlcv.py
  python3 research/collect_historical_ohlcv.py --from 2023-01-01 --to 2023-06-30  # 범위 지정
"""
from __future__ import annotations
import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtests.market_db import _collect_via_open_api, insert_batch, init_db
import sqlite3

DB_PATH = Path(__file__).resolve().parent.parent / "database" / "market_ohlcv.db"

DEFAULT_FROM = date(2022, 12, 1)   # T-1 여유분 포함
DEFAULT_TO   = date(2025, 12, 31)

SLEEP_PER_DAY = 0.3  # 초 — KRX API 부하 방지


def existing_dates() -> set[str]:
    if not DB_PATH.exists():
        return set()
    with sqlite3.connect(DB_PATH) as c:
        return {r[0] for r in c.execute("SELECT DISTINCT trade_date FROM market_ohlcv")}


def iter_dates(from_d: date, to_d: date):
    d = from_d
    while d <= to_d:
        yield d
        d += timedelta(days=1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="from_date", default=None, help="시작일 YYYY-MM-DD")
    parser.add_argument("--to",   dest="to_date",   default=None, help="종료일 YYYY-MM-DD")
    args = parser.parse_args()

    from_d = date.fromisoformat(args.from_date) if args.from_date else DEFAULT_FROM
    to_d   = date.fromisoformat(args.to_date)   if args.to_date   else DEFAULT_TO

    init_db()
    done = existing_dates()

    all_days    = list(iter_dates(from_d, to_d))
    pending     = [d for d in all_days if d.isoformat() not in done]
    done_in_rng = len(all_days) - len(pending)

    print(f"수집 범위: {from_d} ~ {to_d}")
    print(f"전체 날짜수: {len(all_days)} / 완료: {done_in_rng} / 미처리: {len(pending)}")
    if not pending:
        print("이미 모두 수집됨.")
        return

    total_saved  = 0
    trading_days = 0
    errors       = 0

    for i, d in enumerate(pending):
        try:
            rows = _collect_via_open_api(d)
            if rows:
                n = insert_batch(rows, source="krx_open_api")
                total_saved  += n
                trading_days += 1
                if (i + 1) % 20 == 0 or i == 0:
                    print(f"  [{i+1}/{len(pending)}] {d}: {n}행 — 누계 {total_saved}행")
                else:
                    print(f"  {d}: {n}행", end="\r")
            else:
                pass  # 비거래일 — 출력 생략
        except Exception as e:
            errors += 1
            print(f"\n  [{i+1}/{len(pending)}] {d}: 오류 — {e}")

        if i < len(pending) - 1:
            time.sleep(SLEEP_PER_DAY)

    print(f"\n=== 완료 — 거래일 {trading_days}일, 총 {total_saved}행 저장, 오류 {errors}건 ===")


if __name__ == "__main__":
    main()
