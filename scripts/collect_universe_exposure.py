"""
Stage 20: Universe Exposure 데이터 수집

소스 전략:
  snapshot 날짜 (retro와 겹치는 날): pbr/per/market_cap → snapshot, sector/kospi200 → pykrx
  나머지 날짜                        : 전부 pykrx

source 컬럼:
  'snapshot_val_pykrx_sector' : 수치는 snapshot, sector/kospi200은 pykrx
  'pykrx'                     : 전부 pykrx

PIT 규칙: signal_date 이전의 가장 최근 trading day 사용. 미래 데이터 절대 금지.

checkpoint: 이미 DB에 있는 signal_date는 skip.

실행:
    cd ~/Desktop/investment
    set -a; source .env; set +a
    python3 scripts/collect_universe_exposure.py [--dry-run] [--force]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH   = ROOT / "database" / "research.db"
SNAP_DIR  = ROOT / "backtests" / "snapshots"

try:
    from pykrx import stock as krx_stock
    PYKRX_OK = True
except ImportError:
    PYKRX_OK = False

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS universe_exposure (
    signal_date TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    grade       TEXT,
    alpha_20d   REAL,
    sector      TEXT,
    market_cap  REAL,
    pbr         REAL,
    per         REAL,
    kospi200    INTEGER,
    source      TEXT,
    PRIMARY KEY (signal_date, ticker)
)
"""

SLEEP_SEC = 0.8   # pykrx rate limit


# ---------------------------------------------------------------------------
# pykrx 헬퍼
# ---------------------------------------------------------------------------

def _nearest_prev_date(target_dt: str, available: list[str]) -> str | None:
    """
    target_dt 이하 (≤) 가장 최근 날짜 반환. 미래 날짜 절대 선택 안 함.
    available: 정렬된 YYYY-MM-DD 문자열 목록.
    """
    # binary search 대신 단순 역순 탐색 (141개 이하)
    for d in reversed(available):
        if d <= target_dt:
            return d
    return None


def fetch_sector_kospi200(date_str: str) -> tuple[dict[str, str], set[str]]:
    """
    pykrx로 date_str(YYYYMMDD)의 KOSPI 업종 + KOSPI200 편입 종목 조회.
    반환: ({ticker: sector}, {kospi200_tickers})
    """
    sector_map: dict[str, str] = {}
    kospi200: set[str] = set()

    try:
        df = krx_stock.get_market_sector_classifications(date_str, market="KOSPI")
        if df is not None and not df.empty:
            col = next((c for c in df.columns if "업종" in str(c)), None)
            if col:
                for ticker, row in df.iterrows():
                    v = row.get(col)
                    if v:
                        sector_map[str(ticker).zfill(6)] = str(v).strip()
        time.sleep(SLEEP_SEC)
    except Exception as e:
        print(f"    sector 오류 ({date_str}): {e}")

    try:
        tickers = krx_stock.get_index_portfolio_deposit_file(date_str, "1028")
        if tickers is not None:
            kospi200 = {str(t).zfill(6) for t in tickers}
        time.sleep(SLEEP_SEC)
    except Exception as e:
        print(f"    KOSPI200 오류 ({date_str}): {e}")

    return sector_map, kospi200


def fetch_fundamentals_cap(date_str: str) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    """
    pykrx로 date_str(YYYYMMDD)의 PBR, PER, 시가총액 조회.
    반환: ({ticker: pbr}, {ticker: per}, {ticker: market_cap})
    """
    pbr_map: dict[str, float] = {}
    per_map: dict[str, float] = {}
    cap_map: dict[str, float] = {}

    try:
        df = krx_stock.get_market_fundamental(date_str, market="KOSPI")
        if df is not None and not df.empty:
            pbr_col = next((c for c in df.columns if "PBR" in str(c).upper() or "pbr" in str(c)), None)
            per_col = next((c for c in df.columns if "PER" in str(c).upper() or "per" in str(c)), None)
            for ticker, row in df.iterrows():
                t = str(ticker).zfill(6)
                if pbr_col and row.get(pbr_col) and float(row[pbr_col]) > 0:
                    pbr_map[t] = float(row[pbr_col])
                if per_col and row.get(per_col) and float(row[per_col]) > 0:
                    per_map[t] = float(row[per_col])
        time.sleep(SLEEP_SEC)
    except Exception as e:
        print(f"    fundamental 오류 ({date_str}): {e}")

    try:
        df = krx_stock.get_market_cap(date_str, market="KOSPI")
        if df is not None and not df.empty:
            cap_col = next((c for c in df.columns if "시가총액" in str(c) or "Market" in str(c)), None)
            for ticker, row in df.iterrows():
                t = str(ticker).zfill(6)
                if cap_col and row.get(cap_col) and float(row[cap_col]) > 0:
                    cap_map[t] = float(row[cap_col])
        time.sleep(SLEEP_SEC)
    except Exception as e:
        print(f"    시가총액 오류 ({date_str}): {e}")

    return pbr_map, per_map, cap_map


# ---------------------------------------------------------------------------
# 스냅샷 로더
# ---------------------------------------------------------------------------

def load_snapshot_vals(date_str: str) -> dict[str, dict]:
    """
    backtests/snapshots/{date_str}.json → {ticker: {pbr, per, market_cap}}
    """
    path = SNAP_DIR / f"{date_str}.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        result = {}
        for e in data.get("entries", []):
            t = str(e.get("ticker", "")).zfill(6)
            result[t] = {
                "pbr":        e.get("pbr"),
                "per":        e.get("per"),
                "market_cap": e.get("market_cap"),
            }
        return result
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# DB 저장
# ---------------------------------------------------------------------------

def upsert_rows(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(
        """INSERT OR REPLACE INTO universe_exposure
           (signal_date, ticker, grade, alpha_20d, sector, market_cap, pbr, per, kospi200, source)
           VALUES (:signal_date, :ticker, :grade, :alpha_20d,
                   :sector, :market_cap, :pbr, :per, :kospi200, :source)""",
        rows,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force",   action="store_true", help="이미 수집된 날짜도 재수집")
    args = parser.parse_args()

    if not PYKRX_OK:
        print("ERROR: pykrx 없음")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.execute(CREATE_TABLE_SQL)
    conn.commit()

    # retro_calibration 전체 로드
    retro_rows = conn.execute("""
        SELECT signal_date, ticker, grade, alpha_20d
        FROM retro_calibration
        WHERE signal_date >= '2026-01-02'
        ORDER BY signal_date, ticker
    """).fetchall()

    # 날짜별로 묶기
    from collections import defaultdict
    by_date: dict[str, list[tuple]] = defaultdict(list)
    for sd, ticker, grade, alpha in retro_rows:
        by_date[sd].append((ticker, grade, alpha))

    # 스냅샷 날짜 목록
    snap_date_set = {f.stem for f in SNAP_DIR.glob("*.json")}

    # 이미 수집된 날짜
    done_dates: set[str] = set()
    if not args.force:
        done_rows = conn.execute(
            "SELECT DISTINCT signal_date FROM universe_exposure"
        ).fetchall()
        done_dates = {r[0] for r in done_rows}

    signal_dates = sorted(by_date.keys())
    # pykrx available trading days (KRX close 기준)
    # 수집 전에 KRX 날짜 목록을 market_ohlcv에서 가져옴
    with sqlite3.connect(ROOT / "database" / "market_ohlcv.db") as mc:
        krx_dates = [r[0] for r in mc.execute(
            "SELECT DISTINCT trade_date FROM market_ohlcv WHERE ticker='1001' ORDER BY trade_date"
        ).fetchall()]

    print(f"대상: {len(signal_dates)}개 날짜, {len(retro_rows)}개 (date, ticker) 쌍")
    print(f"skip (이미 수집): {len(done_dates)}개 날짜")
    if args.dry_run:
        print("[DRY-RUN] 첫 2개 날짜만 처리, DB 저장 없음")

    total_ok = total_skip = 0

    work_dates = signal_dates if not args.dry_run else signal_dates[:2]

    for i, sd in enumerate(work_dates, 1):
        if sd in done_dates:
            total_skip += len(by_date[sd])
            if i % 20 == 0:
                print(f"  [{i}/{len(work_dates)}] {sd}: SKIP (이미 수집)")
            continue

        is_snap = sd in snap_date_set
        # PIT: signal_date 이하 가장 최근 KRX trading day
        krx_dt = _nearest_prev_date(sd, krx_dates)
        if krx_dt is None:
            print(f"  [{i}/{len(work_dates)}] {sd}: KRX 날짜 없음 → skip")
            continue
        krx_str = krx_dt.replace("-", "")  # YYYYMMDD

        print(f"  [{i}/{len(work_dates)}] {sd} (krx={krx_dt}) {'[snapshot_val]' if is_snap else '[pykrx_all]'}", end=" ", flush=True)

        # sector + KOSPI200: 항상 pykrx
        sector_map, kospi200_set = fetch_sector_kospi200(krx_str)
        print(f"sector={len(sector_map)} kospi200={len(kospi200_set)}", end=" ", flush=True)

        # PBR/PER/시총: snapshot 있으면 snapshot, 없으면 pykrx
        if is_snap:
            snap_vals = load_snapshot_vals(sd)
            pbr_map  = {t: v["pbr"]        for t, v in snap_vals.items() if v["pbr"]        is not None}
            per_map  = {t: v["per"]        for t, v in snap_vals.items() if v["per"]        is not None}
            cap_map  = {t: v["market_cap"] for t, v in snap_vals.items() if v["market_cap"] is not None}
            source   = "snapshot_val_pykrx_sector"
            print(f"pbr_snap={len(pbr_map)}", end=" ", flush=True)
        else:
            pbr_map, per_map, cap_map = fetch_fundamentals_cap(krx_str)
            source = "pykrx"
            print(f"pbr_pykrx={len(pbr_map)}", end=" ", flush=True)

        # 행 구성
        rows = []
        for ticker, grade, alpha in by_date[sd]:
            t = ticker.zfill(6)
            rows.append({
                "signal_date": sd,
                "ticker":      t,
                "grade":       grade,
                "alpha_20d":   alpha,
                "sector":      sector_map.get(t),
                "market_cap":  cap_map.get(t),
                "pbr":         pbr_map.get(t),
                "per":         per_map.get(t),
                "kospi200":    1 if t in kospi200_set else 0,
                "source":      source,
            })

        if not args.dry_run:
            upsert_rows(conn, rows)
        else:
            # dry-run: 샘플 출력
            for r in rows[:2]:
                print(f"\n    {r['ticker']}: sector={r['sector']}, pbr={r['pbr']}, cap={r['market_cap']}, kospi200={r['kospi200']}")

        total_ok += len(rows)
        print(f"→ {len(rows)}행")

    conn.close()
    print(f"\n완료: OK={total_ok} SKIP={total_skip}")
    if not args.dry_run:
        print("다음: python3 scripts/analyze_universe_exposure.py")


if __name__ == "__main__":
    main()
