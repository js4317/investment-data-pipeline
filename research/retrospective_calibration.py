#!/usr/bin/env python3
"""
Retrospective Calibration Batch — v8.5.19
==========================================

목적: 2026년 KRX 영업일에 v8.5.19를 소급 적용, 20D Alpha 표본 확보.

저장: database/research.db (retro_calibration 테이블)
      PRIMARY KEY: (signal_date, ticker, engine_version)

Alpha 계산 방식 (next_day_open 기준):
  entry  = T+1 영업일 open
  exit   = T+20 영업일 close
  alpha  = (ticker exit/entry - 1) - (KOSPI exit/entry - 1)

실행 모드:
  --smoke   smoke test: 2026-01-02, 2026-04-01, 2026-07-01 (3일)
  --full    전체 배치: 모든 valid 영업일

Look-ahead 방지:
  signal_date T의 펀더멘털/수급 → pykrx(T) 호출 (역사적 데이터)
  T+1 open / T+20 close → market_ohlcv.db (수집 시점과 무관)
  earnings/risk raw 데이터는 Naver (현재 데이터 — 한계 있음, 기록)
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

ROOT   = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import concurrent.futures

from constants import ENGINE_VERSION
from analyzer.rules.market_regime import get_market_state
from analyzer.watchlist.store import load_all as wl_load_all
from analyzer.watchlist.models import WatchlistState
from run import analyze_ticker
import analyzer.data.krx as _krx_module

RETRO_DB  = ROOT / "database" / "research.db"
OHLCV_DB  = ROOT / "database" / "market_ohlcv.db"
KOSPI_IDX = "1001"

SMOKE_DATES = [
    date(2026, 1, 2),
    date(2026, 4, 1),
    date(2026, 7, 1),
]


# ── DB 초기화 ──────────────────────────────────────────────────────────────

def init_db() -> None:
    with sqlite3.connect(RETRO_DB) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS retro_calibration (
                signal_date      TEXT NOT NULL,
                eval_date        TEXT,
                ticker           TEXT NOT NULL,
                engine_version   TEXT NOT NULL,
                grade            TEXT,
                normalized_score REAL,
                valuation_score  REAL,
                earnings_score   REAL,
                risk_score       REAL,
                supply_score     REAL,
                alpha_20d        REAL,
                entry_open       REAL,
                exit_close       REAL,
                kospi_entry_open REAL,
                kospi_exit_close REAL,
                look_ahead_note  TEXT,
                created_at       TEXT DEFAULT (datetime('now','localtime')),
                PRIMARY KEY (signal_date, ticker, engine_version)
            );
            CREATE INDEX IF NOT EXISTS idx_rc_signal_date
                ON retro_calibration(signal_date);
            CREATE INDEX IF NOT EXISTS idx_rc_ticker
                ON retro_calibration(ticker);
        """)


# ── 가격 조회 ──────────────────────────────────────────────────────────────

def _get_trading_days_2026() -> list[date]:
    """market_ohlcv.db에서 2026년 영업일 목록 (KOSPI 인덱스 기준)."""
    with sqlite3.connect(OHLCV_DB) as conn:
        rows = conn.execute(
            "SELECT trade_date FROM market_ohlcv "
            "WHERE ticker=? AND trade_date >= '2026-01-01' "
            "ORDER BY trade_date",
            (KOSPI_IDX,),
        ).fetchall()
    return [date.fromisoformat(r[0]) for r in rows]


def _get_price(ticker: str, trade_date: date, field: str) -> float | None:
    """시가(open) 또는 종가(close) 조회."""
    with sqlite3.connect(OHLCV_DB) as conn:
        row = conn.execute(
            f"SELECT {field} FROM market_ohlcv WHERE ticker=? AND trade_date=?",
            (ticker, trade_date.isoformat()),
        ).fetchone()
    return row[0] if row and row[0] else None


def _ensure_prices(trade_date: date) -> None:
    """trade_date 데이터가 market_ohlcv.db에 없으면 수집."""
    with sqlite3.connect(OHLCV_DB) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM market_ohlcv WHERE trade_date=? AND ticker != ?",
            (trade_date.isoformat(), KOSPI_IDX),
        ).fetchone()[0]
    if n == 0:
        from backtests.market_db import init_db as mdb_init, collect_date as mdb_collect
        mdb_init()
        saved = mdb_collect(trade_date)
        print(f"    [price] {trade_date} 수집 — {saved}행")


def _compute_alpha(
    ticker: str,
    signal_date: date,
    trading_days: list[date],
) -> dict:
    """
    entry  = T+1 영업일 open
    exit   = T+20 영업일 close
    alpha  = (ticker_ret) - (KOSPI_ret)
    """
    idx = trading_days.index(signal_date) if signal_date in trading_days else -1
    if idx < 0 or idx + 20 >= len(trading_days):
        return {"eval_date": None, "alpha_20d": None,
                "entry_open": None, "exit_close": None,
                "kospi_entry_open": None, "kospi_exit_close": None}

    entry_day = trading_days[idx + 1]   # T+1
    exit_day  = trading_days[idx + 20]  # T+20

    ticker_entry = _get_price(ticker,    entry_day, "open")
    ticker_exit  = _get_price(ticker,    exit_day,  "close")
    kospi_entry  = _get_price(KOSPI_IDX, entry_day, "open")
    kospi_exit   = _get_price(KOSPI_IDX, exit_day,  "close")

    alpha = None
    if all(v and v > 0 for v in [ticker_entry, ticker_exit, kospi_entry, kospi_exit]):
        ticker_ret = ticker_exit / ticker_entry - 1
        kospi_ret  = kospi_exit / kospi_entry - 1
        alpha = round((ticker_ret - kospi_ret) * 100, 4)

    return {
        "eval_date":        exit_day.isoformat(),
        "alpha_20d":        alpha,
        "entry_open":       ticker_entry,
        "exit_close":       ticker_exit,
        "kospi_entry_open": kospi_entry,
        "kospi_exit_close": kospi_exit,
    }


# ── 이미 처리된 날짜 확인 ──────────────────────────────────────────────────

def _processed_dates() -> set[date]:
    """research.db에 이미 처리된 signal_date 집합."""
    with sqlite3.connect(RETRO_DB) as conn:
        rows = conn.execute(
            "SELECT DISTINCT signal_date FROM retro_calibration WHERE engine_version=?",
            (ENGINE_VERSION,),
        ).fetchall()
    return {date.fromisoformat(r[0]) for r in rows}


# ── 날짜 하나 처리 ─────────────────────────────────────────────────────────

def run_date(
    signal_date: date,
    tickers: list[str],
    trading_days: list[date],
) -> int:
    """signal_date 하나를 처리, 저장. 반환: 저장된 행 수."""
    date_str = signal_date.strftime("%Y%m%d")

    # ── 배치 캐시 사전 워밍 + circuit breaker ──────────────────────────────
    # KRX 차단/rate-limit 시 batch 자체가 실패 → 107종목 헛수고 방지.
    import time
    try:
        df_fund = _krx_module._cached_fundamental(date_str)
        df_cap  = _krx_module._cached_market_cap(date_str)
        if (df_fund is None or df_fund.empty) and (df_cap is None or df_cap.empty):
            print(f"  [{signal_date}] KRX 응답 없음 (차단/rate-limit) — skip, 30초 대기")
            time.sleep(30)
            return 0
    except Exception as e:
        print(f"  [{signal_date}] 배치 캐시 실패: {e} — skip, 30초 대기")
        time.sleep(30)
        return 0

    # 가격 미리 수집 (entry=T+1, exit=T+20)
    idx = trading_days.index(signal_date) if signal_date in trading_days else -1
    if idx >= 0 and idx + 20 < len(trading_days):
        _ensure_prices(trading_days[idx + 1])
        _ensure_prices(trading_days[idx + 20])

    market_state = get_market_state(signal_date)

    # 이미 처리된 ticker 제외
    with sqlite3.connect(RETRO_DB) as conn:
        done_tickers = {
            r[0] for r in conn.execute(
                "SELECT ticker FROM retro_calibration "
                "WHERE signal_date=? AND engine_version=?",
                (signal_date.isoformat(), ENGINE_VERSION),
            ).fetchall()
        }
    pending_tickers = [t for t in tickers if t not in done_tickers]
    if not pending_tickers:
        return 0

    print(f"  [{signal_date}] 스코어링 중... ({len(pending_tickers)}종목)")

    def _score_one(ticker: str) -> dict | None:
        try:
            result = analyze_ticker(ticker, signal_date, market_state)
            s = result.scoring
            # supply_score=0은 KRX rate-limit fallback — 저장 skip, 재수집 대상
            if s.supply_score == 0:
                return None
            price = _compute_alpha(ticker, signal_date, trading_days)
            return {
                "signal_date":      signal_date.isoformat(),
                "eval_date":        price["eval_date"],
                "ticker":           ticker,
                "engine_version":   ENGINE_VERSION,
                "grade":            s.grade.value,
                "normalized_score": s.normalized_score,
                "valuation_score":  s.valuation_score,
                "earnings_score":   s.earnings_score,
                "risk_score":       s.risk_score,
                "supply_score":     s.supply_score,
                "alpha_20d":        price["alpha_20d"],
                "entry_open":       price["entry_open"],
                "exit_close":       price["exit_close"],
                "kospi_entry_open": price["kospi_entry_open"],
                "kospi_exit_close": price["kospi_exit_close"],
                "look_ahead_note":  "earnings/risk=Naver(current)",
            }
        except Exception as e:
            print(f"    [{ticker}] 실패: {e}")
            return None

    # 종목 처리 — 순차 실행 (병렬 → supply_flow rate-limit 재발 확인)
    # 원래 블락 원인은 다중 프로세스 충돌이지 단일 프로세스 순차가 아님.
    rows = []
    for ticker in pending_tickers:
        row = _score_one(ticker)
        if row:
            rows.append(row)

    if rows:
        with sqlite3.connect(RETRO_DB) as conn:
            conn.executemany("""
                INSERT OR IGNORE INTO retro_calibration
                (signal_date, eval_date, ticker, engine_version,
                 grade, normalized_score, valuation_score, earnings_score,
                 risk_score, supply_score,
                 alpha_20d, entry_open, exit_close,
                 kospi_entry_open, kospi_exit_close, look_ahead_note)
                VALUES
                (:signal_date, :eval_date, :ticker, :engine_version,
                 :grade, :normalized_score, :valuation_score, :earnings_score,
                 :risk_score, :supply_score,
                 :alpha_20d, :entry_open, :exit_close,
                 :kospi_entry_open, :kospi_exit_close, :look_ahead_note)
            """, rows)

    alpha_ok = sum(1 for r in rows if r["alpha_20d"] is not None)
    print(f"    → 저장 {len(rows)}행 / alpha_20d {alpha_ok}건")
    return len(rows)


def backfill_missing_alpha(trading_days: list[date]) -> int:
    """alpha_20d IS NULL인 기존 행을 재계산해서 UPDATE."""
    with sqlite3.connect(RETRO_DB) as conn:
        nulls = conn.execute(
            "SELECT signal_date, ticker FROM retro_calibration "
            "WHERE alpha_20d IS NULL AND engine_version=?",
            (ENGINE_VERSION,),
        ).fetchall()

    if not nulls:
        return 0

    # 날짜별로 묶어서 가격 수집
    dates_needed: set[date] = set()
    day_set = set(trading_days)
    for sd, _ in nulls:
        sig = date.fromisoformat(sd)
        idx = trading_days.index(sig) if sig in day_set else -1
        if idx >= 0 and idx + 20 < len(trading_days):
            dates_needed.add(trading_days[idx + 1])
            dates_needed.add(trading_days[idx + 20])

    for d in sorted(dates_needed):
        _ensure_prices(d)

    updated = 0
    for sd, ticker in nulls:
        sig = date.fromisoformat(sd)
        price = _compute_alpha(ticker, sig, trading_days)
        if price["alpha_20d"] is None:
            continue
        with sqlite3.connect(RETRO_DB) as conn:
            conn.execute(
                "UPDATE retro_calibration SET "
                "eval_date=?, alpha_20d=?, entry_open=?, exit_close=?, "
                "kospi_entry_open=?, kospi_exit_close=? "
                "WHERE signal_date=? AND ticker=? AND engine_version=?",
                (price["eval_date"], price["alpha_20d"],
                 price["entry_open"], price["exit_close"],
                 price["kospi_entry_open"], price["kospi_exit_close"],
                 sd, ticker, ENGINE_VERSION),
            )
        updated += 1

    print(f"  [backfill] alpha_20d NULL {len(nulls)}건 → {updated}건 업데이트")
    return updated


# ── 메인 ──────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Retrospective Calibration Batch — v8.5.19"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--smoke", action="store_true",
                      help="Smoke test: 2026-01-02, 2026-04-01, 2026-07-01")
    mode.add_argument("--full",  action="store_true",
                      help="전체 배치: 모든 valid 영업일")
    parser.add_argument("--from-date", metavar="YYYY-MM-DD",
                        help="시작일 (--full 전용, 기본: 2026-01-02)")
    parser.add_argument("--to-date",   metavar="YYYY-MM-DD",
                        help="종료일 (--full 전용, 기본: 자동 — T+20D 가능 마지막 날)")
    args = parser.parse_args()

    init_db()

    # 워치리스트 ACTIVE/REVIEW 종목
    tickers = [
        i.ticker for i in wl_load_all()
        if i.state in (WatchlistState.ACTIVE, WatchlistState.REVIEW)
    ]
    if not tickers:
        print("워치리스트 ACTIVE/REVIEW 종목 없음 — 종료")
        sys.exit(1)
    print(f"대상 종목: {len(tickers)}개  {tickers}")

    # 영업일 목록 (market_ohlcv.db 기준)
    trading_days = _get_trading_days_2026()
    if not trading_days:
        print("market_ohlcv.db에 2026년 데이터 없음 — 종료")
        sys.exit(1)
    print(f"2026년 영업일: {len(trading_days)}일 "
          f"({trading_days[0]} ~ {trading_days[-1]})")

    # valid signal_date: T+20 close가 DB에 있어야 함 (idx+20 < len)
    valid_days = trading_days[: len(trading_days) - 20]
    print(f"Valid signal_date 범위: {valid_days[0]} ~ {valid_days[-1]} "
          f"({len(valid_days)}일)")

    # 처리 날짜 결정
    if args.smoke:
        # smoke: SMOKE_DATES 중 valid_days에 있는 것만
        target_days = [d for d in SMOKE_DATES if d in valid_days]
        missing = [d for d in SMOKE_DATES if d not in valid_days]
        if missing:
            # 가장 가까운 valid day로 대체
            for d in missing:
                for vd in valid_days:
                    if vd >= d:
                        target_days.append(vd)
                        print(f"  smoke: {d} → {vd} (대체)")
                        break
        target_days = sorted(set(target_days))
    else:
        from_date = date.fromisoformat(args.from_date) if args.from_date else valid_days[0]
        to_date   = date.fromisoformat(args.to_date)   if args.to_date   else valid_days[-1]
        target_days = [d for d in valid_days if from_date <= d <= to_date]

    # 이미 처리된 날짜 제외
    done = _processed_dates()
    pending = [d for d in target_days if d not in done]
    done_in_target = len([d for d in target_days if d in done])
    print(f"\n처리 대상: {len(target_days)}일 / 이미 완료: {done_in_target}일 "
          f"/ 미처리: {len(pending)}일")

    if not pending:
        print("모두 처리 완료 — 새로 실행할 날짜 없음")
    else:
        total_saved = 0
        for i, d in enumerate(pending):
            print(f"\n[{i+1}/{len(pending)}] {d}")
            saved = run_date(d, tickers, trading_days)
            total_saved += saved
            if saved > 0 and i < len(pending) - 1:
                print(f"  [KRX 회복 대기 90초...]")
                time.sleep(90)
        print(f"\n=== 배치 완료 — 총 {total_saved}행 저장 ===")

    # alpha_20d NULL 자동 복구 (가격 뒤늦게 수집된 경우)
    print("\nalpha_20d NULL 자동 복구 중...")
    backfill_missing_alpha(trading_days)

    _print_summary()


def print_progress() -> None:
    """현재 DB 진행 상황 출력."""
    with sqlite3.connect(RETRO_DB) as conn:
        n_total  = conn.execute("SELECT COUNT(*) FROM retro_calibration WHERE engine_version=?", (ENGINE_VERSION,)).fetchone()[0]
        n_dates  = conn.execute("SELECT COUNT(DISTINCT signal_date) FROM retro_calibration WHERE engine_version=?", (ENGINE_VERSION,)).fetchone()[0]
        n_alpha  = conn.execute("SELECT COUNT(*) FROM retro_calibration WHERE engine_version=? AND alpha_20d IS NOT NULL", (ENGINE_VERSION,)).fetchone()[0]
        last     = conn.execute("SELECT MAX(signal_date) FROM retro_calibration WHERE engine_version=?", (ENGINE_VERSION,)).fetchone()[0]
    target = 141
    print(f"진행: {n_dates}/{target}일 ({n_dates/target*100:.0f}%) | 총 {n_total}건 | alpha {n_alpha}건 | 마지막: {last}")


def _print_summary() -> None:
    with sqlite3.connect(RETRO_DB) as conn:
        n_total  = conn.execute(
            "SELECT COUNT(*) FROM retro_calibration WHERE engine_version=?",
            (ENGINE_VERSION,),
        ).fetchone()[0]
        n_dates  = conn.execute(
            "SELECT COUNT(DISTINCT signal_date) FROM retro_calibration WHERE engine_version=?",
            (ENGINE_VERSION,),
        ).fetchone()[0]
        n_alpha  = conn.execute(
            "SELECT COUNT(*) FROM retro_calibration "
            "WHERE engine_version=? AND alpha_20d IS NOT NULL",
            (ENGINE_VERSION,),
        ).fetchone()[0]
        avg_alpha = conn.execute(
            "SELECT AVG(alpha_20d) FROM retro_calibration "
            "WHERE engine_version=? AND alpha_20d IS NOT NULL",
            (ENGINE_VERSION,),
        ).fetchone()[0]

    print(f"\n── Retro Calibration DB 현황 ({ENGINE_VERSION}) ──")
    print(f"  signal_date 수: {n_dates}일")
    print(f"  총 행:          {n_total}건")
    print(f"  alpha_20d 있음: {n_alpha}건")
    if avg_alpha is not None:
        print(f"  평균 alpha_20d: {avg_alpha:+.2f}pp")


if __name__ == "__main__":
    main()
