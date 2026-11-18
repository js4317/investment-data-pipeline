"""
collect_pit_fundamentals.py — pykrx PIT PBR/PER 수집기

설계 원칙:
  - get_market_fundamental_by_ticker(date, market) → 1 call로 전 종목 수집
  - KOSPI + KOSDAQ 각 1 call/거래일 → 총 ~3,300 calls (약 30분)
  - 이미 수집된 날짜 자동 skip (재실행 안전)
  - market_ohlcv.db 거래일 캘린더 기준 (비거래일 자동 제외)

실행:
    cd ~/Desktop/investment
    python3 scripts/collect_pit_fundamentals.py
    python3 scripts/collect_pit_fundamentals.py --from 2020-01-01 --to 2022-12-31
    python3 scripts/collect_pit_fundamentals.py --summary
"""
from __future__ import annotations

import argparse
import sqlite3
import time
from datetime import date
from pathlib import Path

ROOT     = Path(__file__).parent.parent
DB_PATH  = ROOT / "database" / "research.db"
OHLCV_DB = ROOT / "database" / "market_ohlcv.db"

CALL_DELAY = 0.5   # 초 — KRX 부하 방지
MARKETS    = ["KOSPI", "KOSDAQ"]


# ---------------------------------------------------------------------------
# DB 초기화
# ---------------------------------------------------------------------------

def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pit_market_fundamentals (
            trade_date  TEXT NOT NULL,
            ticker      TEXT NOT NULL,
            market      TEXT NOT NULL,   -- KOSPI | KOSDAQ
            pbr         REAL,
            per         REAL,
            div         REAL,            -- 배당수익률
            collected_at TEXT NOT NULL,
            PRIMARY KEY (trade_date, ticker)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_pmf_date
        ON pit_market_fundamentals(trade_date)
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# 거래일 목록 (market_ohlcv.db KOSPI 기준)
# ---------------------------------------------------------------------------

def get_trading_calendar(from_date: str, to_date: str) -> list[str]:
    with sqlite3.connect(str(OHLCV_DB)) as c:
        rows = c.execute(
            "SELECT DISTINCT trade_date FROM market_ohlcv "
            "WHERE ticker='1001' AND trade_date BETWEEN ? AND ? "
            "ORDER BY trade_date",
            (from_date, to_date),
        ).fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# 이미 수집된 날짜 조회
# ---------------------------------------------------------------------------

def get_collected_dates(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT DISTINCT trade_date FROM pit_market_fundamentals"
    ).fetchall()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# 단일 날짜 수집
# ---------------------------------------------------------------------------

def collect_date(date_str: str, conn: sqlite3.Connection) -> int:
    """
    KOSPI + KOSDAQ 전 종목 PBR/PER 수집.
    반환: 삽입된 행 수.
    """
    from pykrx import stock

    krx_date = date_str.replace("-", "")
    now = date.today().isoformat()
    inserted = 0

    for market in MARKETS:
        try:
            df = stock.get_market_fundamental_by_ticker(krx_date, market=market)
            time.sleep(CALL_DELAY)
        except Exception as e:
            err = str(e)
            # 구버전 API 포맷 오류만 조용히 skip (과거 날짜 한계)
            if "are in the [columns]" not in err:
                print(f"    [{market}] {date_str} 오류: {err[:150]}")
            time.sleep(CALL_DELAY)
            continue

        if df is None or df.empty:
            continue

        # 컬럼 정규화: 영문/한글/구버전 혼용 대응
        # pykrx 구버전: 한글 컬럼 (주가수익비율, 주가순자산비율 등)
        # pykrx 신버전: 영문 (PER, PBR, DIV)
        KR_MAP = {
            "주가순자산비율": "pbr",
            "주가수익비율":   "per",
            "배당수익률":     "div",
        }
        col_map = {}
        for c in df.columns:
            cl = c.upper().strip()
            if cl == "PBR":
                col_map[c] = "pbr"
            elif cl == "PER":
                col_map[c] = "per"
            elif cl == "DIV":
                col_map[c] = "div"
            elif c in KR_MAP:
                col_map[c] = KR_MAP[c]
        df = df.rename(columns=col_map)

        # 필요한 컬럼만 보존
        keep = [c for c in ["pbr", "per", "div"] if c in df.columns]
        if not keep:
            continue
        df = df[keep].copy()
        df.index = df.index.astype(str)
        df = df[(df.index.str.len() == 6)]       # 유효 ticker만
        df = df.replace(0, None)                  # 0 → NULL (미집계)

        rows = []
        for ticker, row in df.iterrows():
            rows.append((
                date_str,
                ticker,
                market,
                row.get("pbr"),
                row.get("per"),
                row.get("div"),
                now,
            ))

        if rows:
            conn.executemany("""
                INSERT OR IGNORE INTO pit_market_fundamentals
                (trade_date, ticker, market, pbr, per, div, collected_at)
                VALUES (?,?,?,?,?,?,?)
            """, rows)
            inserted += len(rows)

    conn.commit()
    return inserted


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="from_date", default="2020-01-01",
                        metavar="YYYY-MM-DD", help="수집 시작일 (기본: 2020-01-01)")
    parser.add_argument("--to",   dest="to_date",   default=date.today().isoformat(),
                        metavar="YYYY-MM-DD", help="수집 종료일 (기본: 오늘)")
    parser.add_argument("--summary", action="store_true", help="현황 출력 후 종료")
    args = parser.parse_args()

    conn = init_db()

    if args.summary:
        rows = conn.execute("""
            SELECT MIN(trade_date), MAX(trade_date), COUNT(DISTINCT trade_date),
                   COUNT(*), COUNT(CASE WHEN pbr IS NOT NULL THEN 1 END)
            FROM pit_market_fundamentals
        """).fetchone()
        print(f"\n=== pit_market_fundamentals 현황 ===")
        print(f"  날짜 범위   : {rows[0]} ~ {rows[1]}")
        print(f"  거래일 수   : {rows[2]}")
        print(f"  총 행 수    : {rows[3]}")
        print(f"  PBR 비율    : {rows[4]}/{rows[3]} ({rows[4]/rows[3]*100:.1f}%)" if rows[3] else "  데이터 없음")
        conn.close()
        return

    calendar = get_trading_calendar(args.from_date, args.to_date)
    collected = get_collected_dates(conn)
    pending   = [d for d in calendar if d not in collected]

    print(f"수집 범위   : {args.from_date} ~ {args.to_date}")
    print(f"거래일 수   : {len(calendar)}일  (이미 수집: {len(collected)}일  남은 것: {len(pending)}일)")
    if not pending:
        print("모두 수집 완료.")
        conn.close()
        return

    total_inserted = 0
    errors = 0

    for i, d in enumerate(pending, 1):
        n = collect_date(d, conn)
        total_inserted += n
        if n == 0:
            errors += 1
        # 진행률 출력 (10일마다 또는 마지막)
        if i % 10 == 0 or i == len(pending):
            pct = i / len(pending) * 100
            print(f"  [{i:4d}/{len(pending)}  {pct:5.1f}%]  {d}  +{n}행  누계={total_inserted}행  오류={errors}")

    print(f"\n완료: {len(pending)}일 처리  {total_inserted}행 삽입  오류={errors}일")
    conn.close()


if __name__ == "__main__":
    main()
