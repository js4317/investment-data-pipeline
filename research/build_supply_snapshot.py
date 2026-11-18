#!/usr/bin/env python3
"""
단기 수급 스냅샷 수집 — 20D / 60D 외국인+기관 순매수 + 선행 수익률
=====================================================================
연구 목적: Production Supply score와 유사한 단기 수급 신호 검증
  "최근 수급이 강한 종목이 이후 20D/60D에서 어떻게 움직이는가?"

수집 구조:
  스냅샷 날짜 T (매월 말 거래일, 2016-01 ~ 2024-06):
    supply_20d : T-28d ~ T 기간 외국인/기관 순매수 거래대금
    supply_60d : T-91d ~ T 기간 외국인/기관 순매수 거래대금
    price_T    : T 종가 + 시가총액 (ohlcv_by_ticker)
    price_fwd20: T+30d 근처 종가 (≈ 20 영업일 후)
    price_fwd60: T+91d 근처 종가 (≈ 60 영업일 후)
    kospi_T / fwd20 / fwd60: KOSPI 지수 (alpha 계산용)

API 호출 예상: ~108 스냅샷 × 8회 ≈ 864회
소요 시간: ~1.5시간 (딜레이 0.5s × 864 + 처리)

체크포인트:
  이미 수집된 snap_date는 건너뜀 (재실행 안전).
  --force-dates YYYYMM[,YYYYMM,...] 로 특정 월 강제 재수집.

실행:
  python build_supply_snapshot.py
  python build_supply_snapshot.py --force-dates 202001,202002
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT    = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "database" / "research.db"

CALL_DELAY  = 0.5   # 초, KRX 봇 탐지 방지
KOSPI_IDX   = "1001"

# 스냅샷 범위: 2016-01 ~ 2024-06 (60D fwd 데이터 확보 가능한 마지막 시점)
SNAP_START  = date(2016, 1, 1)
SNAP_END    = date(2024, 6, 30)

# ── DDL ───────────────────────────────────────────────────────────────────────

DDL = """
CREATE TABLE IF NOT EXISTS supply_snapshot (
    snap_date       TEXT    NOT NULL,   -- 스냅샷 날짜 YYYYMMDD
    ticker          TEXT    NOT NULL,
    -- 20D 수급 (snap_date 기준 약 28 캘린더일)
    foreign_20d     REAL,               -- 외국인 순매수 거래대금 (원)
    inst_20d        REAL,               -- 기관합계 순매수 거래대금 (원)
    -- 60D 수급 (snap_date 기준 약 91 캘린더일)
    foreign_60d     REAL,
    inst_60d        REAL,
    -- 가격 & 시총
    market_cap      REAL,               -- 시총 at snap_date
    price_snap      REAL,               -- 종가 at snap_date
    price_fwd20     REAL,               -- 종가 at snap_date + ~30d
    price_fwd60     REAL,               -- 종가 at snap_date + ~91d
    -- KOSPI 지수 (alpha 계산용)
    kospi_snap      REAL,
    kospi_fwd20     REAL,
    kospi_fwd60     REAL,
    PRIMARY KEY (snap_date, ticker)
)
"""

DDL_META = """
CREATE TABLE IF NOT EXISTS supply_snapshot_meta (
    snap_date   TEXT PRIMARY KEY,
    status      TEXT,   -- 'complete' | 'partial' | 'failed'
    n_tickers   INTEGER,
    collected_at TEXT
)
"""


# ── 날짜 유틸 ─────────────────────────────────────────────────────────────────

def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _nearest_trading_day(target: date, krx) -> str | None:
    """target에서 최대 7일 이내 가장 가까운 거래일 반환 (후방 탐색)."""
    for delta in range(8):
        dt = target - timedelta(days=delta)
        try:
            df = krx.get_market_ohlcv_by_ticker(_d(dt), market="KOSPI")
            if df is not None and not df.empty:
                return _d(dt)
        except Exception:
            pass
        time.sleep(0.1)
    return None


def _month_end_dates(start: date, end: date) -> list[date]:
    """start ~ end 사이 매월 마지막 날짜 목록 (캘린더일 기준 — 실제 거래일은 수집 시 탐색)."""
    result = []
    cur = date(start.year, start.month, 1)
    while cur <= end:
        # 해당 월의 마지막 날
        if cur.month == 12:
            last = date(cur.year + 1, 1, 1) - timedelta(days=1)
        else:
            last = date(cur.year, cur.month + 1, 1) - timedelta(days=1)
        if last <= end:
            result.append(last)
        cur = date(cur.year + (cur.month // 12), (cur.month % 12) + 1, 1)
    return result


# ── 이미 수집된 날짜 ──────────────────────────────────────────────────────────

def _done_dates(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT snap_date FROM supply_snapshot_meta WHERE status='complete'"
    ).fetchall()
    return {r[0] for r in rows}


# ── 수급 데이터 수집 ──────────────────────────────────────────────────────────

def _fetch_supply(krx, fromdate: str, todate: str, investor: str) -> pd.DataFrame | None:
    """투자자별 순매수 거래대금 전 종목 반환. 실패 시 None."""
    try:
        df = krx.get_market_net_purchases_of_equities_by_ticker(
            fromdate, todate, market="KOSPI", investor=investor
        )
        time.sleep(CALL_DELAY)
        return df if (df is not None and not df.empty) else None
    except Exception as e:
        print(f"    [수급 오류] {investor} {fromdate}~{todate}: {e}")
        time.sleep(CALL_DELAY)
        return None


def _fetch_ohlcv(krx, dt: str, max_back: int = 5) -> pd.DataFrame | None:
    """전 종목 OHLCV + 시가총액. 공휴일이면 최대 max_back 영업일 전 날 탐색. 실패 시 None."""
    target = date(int(dt[:4]), int(dt[4:6]), int(dt[6:8]))
    for delta in range(max_back + 1):
        d = _d(target - timedelta(days=delta))
        try:
            df = krx.get_market_ohlcv_by_ticker(d, market="KOSPI")
            time.sleep(CALL_DELAY)
            if df is None or df.empty:
                continue
            # 공휴일이면 종가가 전부 0 → 실제 거래일 아님
            if (df["종가"] > 0).sum() < 10:
                continue
            return df
        except Exception as e:
            print(f"    [OHLCV 오류] {d}: {e}")
            time.sleep(CALL_DELAY)
    return None


def _fetch_kospi(krx, dt: str, max_back: int = 7) -> float | None:
    """KOSPI 종가. 단일 날짜 호출 대신 3일 범위로 조회 후 마지막 거래일 종가 반환."""
    target = date(int(dt[:4]), int(dt[4:6]), int(dt[6:8]))
    # 공휴일 대비: fromdate를 max_back일 당기고, todate=target 으로 범위 조회
    fromdate = _d(target - timedelta(days=max_back))
    try:
        df = krx.get_index_ohlcv_by_date(fromdate, dt, KOSPI_IDX)
        time.sleep(CALL_DELAY)
        if df is not None and not df.empty:
            valid = df[df["종가"] > 0]
            if not valid.empty:
                return float(valid["종가"].iloc[-1])
    except Exception as e:
        print(f"    [KOSPI 오류] {fromdate}~{dt}: {e}")
        time.sleep(CALL_DELAY)
    return None


# ── 스냅샷 1건 수집 ──────────────────────────────────────────────────────────

def collect_snapshot(krx, snap: date, conn: sqlite3.Connection) -> str:
    """snap 날짜의 스냅샷 수집. 반환: 'complete' | 'partial' | 'failed'"""
    import datetime as _dt

    snap_str = _d(snap)
    d20_from = _d(snap - timedelta(days=28))
    d60_from = _d(snap - timedelta(days=91))
    fwd20    = _d(snap + timedelta(days=30))
    fwd60    = _d(snap + timedelta(days=91))

    print(f"  스냅샷 {snap_str}: 20D({d20_from}~{snap_str}), 60D({d60_from}~{snap_str})")
    print(f"    선행: fwd20={fwd20}, fwd60={fwd60}")

    # ── 수급 수집 ────────────────────────────────────────────────────────────
    sup20_f = _fetch_supply(krx, d20_from, snap_str, "외국인")
    sup20_i = _fetch_supply(krx, d20_from, snap_str, "기관합계")
    sup60_f = _fetch_supply(krx, d60_from, snap_str, "외국인")
    sup60_i = _fetch_supply(krx, d60_from, snap_str, "기관합계")

    if sup20_f is None and sup60_f is None:
        print(f"    [실패] 수급 데이터 없음")
        return "failed"

    # ── 가격 수집 ────────────────────────────────────────────────────────────
    ohlcv_snap  = _fetch_ohlcv(krx, snap_str)
    ohlcv_fwd20 = _fetch_ohlcv(krx, fwd20)
    ohlcv_fwd60 = _fetch_ohlcv(krx, fwd60)

    kospi_snap  = _fetch_kospi(krx, snap_str)
    kospi_fwd20 = _fetch_kospi(krx, fwd20)
    kospi_fwd60 = _fetch_kospi(krx, fwd60)

    # ── 종목 목록: 수급 데이터가 있는 종목 기준 ──────────────────────────────
    tickers = set()
    for df in [sup20_f, sup20_i, sup60_f, sup60_i]:
        if df is not None:
            tickers |= set(df.index.astype(str))

    if not tickers:
        return "failed"

    # ── 컬럼 추출 헬퍼 ───────────────────────────────────────────────────────
    def net_buy(df: pd.DataFrame | None, ticker: str) -> float | None:
        if df is None or ticker not in df.index:
            return None
        try:
            val = df.loc[ticker, "순매수거래대금"]
            return float(val)
        except Exception:
            return None

    def close_price(ohlcv: pd.DataFrame | None, ticker: str) -> float | None:
        if ohlcv is None or ticker not in ohlcv.index:
            return None
        try:
            return float(ohlcv.loc[ticker, "종가"])
        except Exception:
            return None

    def mktcap(ohlcv: pd.DataFrame | None, ticker: str) -> float | None:
        if ohlcv is None or ticker not in ohlcv.index:
            return None
        for col in ["시가총액", "MKT-CAP", "Mkt Cap"]:
            try:
                return float(ohlcv.loc[ticker, col])
            except Exception:
                pass
        return None

    # ── DB 적재 ──────────────────────────────────────────────────────────────
    rows_written = 0
    for ticker in tickers:
        f20 = net_buy(sup20_f, ticker)
        i20 = net_buy(sup20_i, ticker)
        f60 = net_buy(sup60_f, ticker)
        i60 = net_buy(sup60_i, ticker)

        p_snap  = close_price(ohlcv_snap,  ticker)
        p_fwd20 = close_price(ohlcv_fwd20, ticker)
        p_fwd60 = close_price(ohlcv_fwd60, ticker)
        mc      = mktcap(ohlcv_snap, ticker)

        conn.execute("""
            INSERT OR REPLACE INTO supply_snapshot
            (snap_date, ticker, foreign_20d, inst_20d, foreign_60d, inst_60d,
             market_cap, price_snap, price_fwd20, price_fwd60,
             kospi_snap, kospi_fwd20, kospi_fwd60)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (snap_str, ticker,
              f20, i20, f60, i60,
              mc, p_snap, p_fwd20, p_fwd60,
              kospi_snap, kospi_fwd20, kospi_fwd60))
        rows_written += 1

    conn.execute("""
        INSERT OR REPLACE INTO supply_snapshot_meta (snap_date, status, n_tickers, collected_at)
        VALUES (?, 'complete', ?, ?)
    """, (snap_str, rows_written, _dt.datetime.now().isoformat()))
    conn.commit()

    print(f"    완료: {rows_written}개 종목 적재")
    return "complete"


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force-dates", default="",
                        help="강제 재수집 월 (YYYYMM, 콤마 구분). 예: 202001,202002")
    args = parser.parse_args()

    force_months = set(args.force_dates.split(",")) if args.force_dates else set()

    try:
        from pykrx import stock as krx
    except ImportError:
        print("pykrx가 설치되지 않았습니다: pip install pykrx")
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(DDL)
    conn.execute(DDL_META)
    conn.commit()

    snap_dates = _month_end_dates(SNAP_START, SNAP_END)
    done = _done_dates(conn)

    total = len(snap_dates)
    print(f"스냅샷 대상: {total}개 ({_d(snap_dates[0])} ~ {_d(snap_dates[-1])})")
    print(f"이미 완료: {len(done)}개 / 수집 대상: {total - len(done)}개\n")

    for i, snap in enumerate(snap_dates, 1):
        snap_str = _d(snap)
        month_key = snap_str[:6]

        if snap_str in done and month_key not in force_months:
            print(f"[{i}/{total}] {snap_str} — 건너뜀 (완료)")
            continue

        print(f"\n[{i}/{total}] {snap_str}")
        try:
            status = collect_snapshot(krx, snap, conn)
        except KeyboardInterrupt:
            print("\n중단됨. 지금까지 저장된 데이터는 보존됩니다.")
            break
        except Exception as e:
            print(f"  [오류] {snap_str}: {e}")
            conn.execute("""
                INSERT OR REPLACE INTO supply_snapshot_meta (snap_date, status, n_tickers, collected_at)
                VALUES (?, 'failed', 0, datetime('now'))
            """, (snap_str,))
            conn.commit()

    conn.close()

    # 간단 검증
    conn = sqlite3.connect(str(DB_PATH))
    print("\n[검증] supply_snapshot — 월별 집계")
    rows = conn.execute("""
        SELECT substr(snap_date,1,6) AS ym,
               COUNT(DISTINCT snap_date) AS dates,
               COUNT(*) AS rows
        FROM supply_snapshot
        GROUP BY ym
        ORDER BY ym
    """).fetchall()
    for r in rows:
        print(f"  {r[0]}: {r[1]}스냅샷 / {r[2]}건")
    conn.close()


if __name__ == "__main__":
    main()
