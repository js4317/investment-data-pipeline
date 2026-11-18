"""
backfill_prices.py — market_db 과거 가격 backfill

실행 방법:
  cd ~/Desktop/investment
  set -a; source .env; set +a
  python3 scripts/backfill_prices.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import os
from datetime import date, timedelta
from backtests.market_db import _collect_via_open_api, insert_batch, init_db
import sqlite3

# .env 로드 (환경변수 없을 경우 fallback)
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists() and not os.getenv("KRX_OPEN_API_KEY"):
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

init_db()

DB_PATH = Path(__file__).parent.parent / "database" / "market_ohlcv.db"
conn = sqlite3.connect(str(DB_PATH))
filled = set(r[0] for r in conn.execute("SELECT DISTINCT trade_date FROM market_ohlcv").fetchall())
conn.close()
print(f"기존: {len(filled)}일치 데이터 있음")

start = date(2026, 9, 5)
end   = date(2026, 9, 29)

current      = start
total_saved  = 0
trading_days = 0
errors       = 0

while current <= end:
    ds = current.isoformat()
    if ds in filled:
        current += timedelta(days=1)
        continue

    try:
        rows = _collect_via_open_api(current)
        if rows:
            n = insert_batch(rows, source="krx_open_api")
            total_saved  += n
            trading_days += 1
            print(f"  {ds}: {n}행 저장")
        else:
            print(f"  {ds}: 비거래일")
    except Exception as e:
        errors += 1
        print(f"  {ds}: 오류 — {e}")

    current += timedelta(days=1)

print(f"\n완료: 거래일 {trading_days}일, 총 {total_saved}행 저장, 오류 {errors}건")
