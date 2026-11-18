#!/usr/bin/env python3
"""
OOS Signal Recorder
====================
morning_run.py 이후 독립적으로 실행하거나, launchd/cron으로 자동화.

하는 일:
  1. 오늘(또는 --date) 스냅샷에서 STRONG_BUY Top10을 oos_signals에 기록
  2. 이미 기록된 신호 중 entry_open이 없는 것을 market_ohlcv에서 채움
  3. entry_open이 채워진 신호 중 alpha 윈도우가 지난 것을 채움

Usage:
    python3 scripts/record_oos.py
    python3 scripts/record_oos.py --date 2026-09-29
    python3 scripts/record_oos.py --fill-only       # 기록 없이 채움만
    python3 scripts/record_oos.py --summary         # 현황 출력
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

SNAP_DIR = PROJECT_ROOT / "backtests" / "snapshots"


def load_snapshot(signal_date: str) -> tuple[list[dict], dict] | None:
    path = SNAP_DIR / f"{signal_date}.json"
    if not path.exists():
        log.error("스냅샷 없음: %s", path)
        return None
    raw = json.loads(path.read_text())
    entries = raw.get("entries", [])
    meta = {k: v for k, v in raw.items() if k != "entries"}
    return entries, meta


def main() -> int:
    parser = argparse.ArgumentParser(description="OOS Signal Recorder")
    parser.add_argument("--date",      metavar="YYYY-MM-DD", help="기록할 날짜 (기본: 오늘)")
    parser.add_argument("--fill-only", action="store_true",  help="신규 기록 없이 채움만")
    parser.add_argument("--summary",   action="store_true",  help="현황 출력 후 종료")
    args = parser.parse_args()

    from database.oos_store import (
        record_signals, fill_entry_prices, fill_alphas, summary
    )

    if args.summary:
        s = summary()
        print(f"\n=== OOS Signals 현황 ===")
        print(f"기록된 signal_date 수: {s['signal_dates']}")
        for status, counts in s['by_status'].items():
            print(f"  {status:15} total={counts['total']:3}  top10={counts['top10']:3}")
        return 0

    signal_date = args.date or date.today().isoformat()

    # 1. 신규 신호 기록
    if not args.fill_only:
        result = load_snapshot(signal_date)
        if result is None:
            log.warning("신호 기록 건너뜀 — 스냅샷 없음")
        else:
            entries, meta = result
            sb_count = sum(1 for e in entries if e.get("grade") == "strong_buy")
            inserted = record_signals(signal_date, entries, meta)
            log.info("신호 기록 완료 — date=%s  STRONG_BUY=%d  신규삽입=%d",
                     signal_date, sb_count, inserted)

    # 2. Entry 가격 채움
    n_entry = fill_entry_prices()
    if n_entry:
        log.info("entry_open 채움 — %d행", n_entry)

    # 3. Alpha 채움
    n_alpha = fill_alphas()
    filled = {k: v for k, v in n_alpha.items() if v > 0}
    if filled:
        log.info("alpha 채움 — %s", filled)

    # 현황 출력
    s = summary()
    log.info("현황 — signal_dates=%d  %s",
             s['signal_dates'],
             "  ".join(f"{st}:{c['total']}" for st, c in s['by_status'].items()))

    return 0


if __name__ == "__main__":
    sys.exit(main())
