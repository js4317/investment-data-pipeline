"""
OOS (Out-of-Sample) Signal Store
=================================
v8.6.0 이후 실제 운영 신호를 PIT(Point-in-Time) 원칙으로 기록·채움.

원칙:
- 신호 기록 시점에 미래 정보 없음.  alpha 필드는 NULL로 시작.
- 한 번 complete 된 행은 수정하지 않는다.
- retro_calibration과 분리 — 동일 research.db의 별도 테이블 사용.

스키마 변경 시 OOS_SCHEMA_VERSION을 올리고 이 파일 상단에 changelog 추가.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

_DB_PATH = Path(__file__).parent / "research.db"

# 스키마 버전 — 필드 추가·삭제 시 올린다
OOS_SCHEMA_VERSION = 2

# N-day 윈도우: entry_date 이후 N번째 거래일 종가로 측정
ALPHA_WINDOWS = [5, 10, 20, 60]


# ---------------------------------------------------------------------------
# DB 초기화
# ---------------------------------------------------------------------------

def init_db(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or _DB_PATH
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oos_signals (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_date         TEXT NOT NULL,
            ticker              TEXT NOT NULL,
            formula_version     TEXT NOT NULL,
            oos_schema          INTEGER NOT NULL DEFAULT 1,

            -- 신호 당시 점수 (PIT — 저장 후 수정 금지)
            normalized_score    REAL NOT NULL,
            grade               TEXT NOT NULL,
            earnings_score      REAL,
            valuation_score     REAL,
            risk_score          REAL,

            -- 선정 컨텍스트
            n_strong_buy        INTEGER,          -- 당일 전체 STRONG_BUY 수
            rank_in_strong_buy  INTEGER,          -- 1 = 최고점
            is_top10            INTEGER NOT NULL, -- 1 | 0

            -- entry 메타 (CLAUDE.md §6)
            entry_basis         TEXT,             -- same_day_open | next_day_open
            market_data_date    TEXT,             -- pykrx 실제 반환 날짜
            entry_date          TEXT,             -- 실제 진입 날짜
            entry_open          REAL,             -- 진입 가격 (다음 날 채움 가능)
            kospi_entry_open    REAL,             -- KOSPI 진입 가격

            -- alpha (나중에 채움 — 미래 결과)
            alpha_5d            REAL,
            alpha_10d           REAL,
            alpha_20d           REAL,
            alpha_60d           REAL,

            -- OOS 분류 (v2)
            oos_type            TEXT NOT NULL DEFAULT 'primary',
                                                  -- primary | catchup
            -- 완성 상태
            status              TEXT NOT NULL DEFAULT 'pending',
                                                  -- pending | entry_filled | alpha_20d | complete
            recorded_at         TEXT NOT NULL,
            updated_at          TEXT,

            UNIQUE(signal_date, ticker)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_oos_status
        ON oos_signals(status, signal_date)
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# 신호 기록
# ---------------------------------------------------------------------------

def record_signals(
    signal_date: str,
    entries: list[dict],        # 스냅샷의 전체 entries
    snapshot_meta: dict,        # entry_basis, market_data_date, rule_version 등
    db_path: Path | None = None,
    oos_type: str = "primary",  # "primary" | "catchup"
) -> int:
    """
    당일 STRONG_BUY Top10을 oos_signals에 append.

    이미 동일 (signal_date, ticker)가 있으면 IGNORE (PIT 보존).
    반환값: 새로 삽입된 행 수.
    """
    conn = init_db(db_path)

    sb = [e for e in entries if e.get("grade") == "strong_buy"]
    sb_sorted = sorted(sb, key=lambda x: x.get("normalized_score", 0), reverse=True)
    n_sb = len(sb_sorted)

    entry_basis       = snapshot_meta.get("entry_basis", "")
    market_data_date  = snapshot_meta.get("market_data_date", "")
    formula_version   = snapshot_meta.get("rule_version", "unknown")

    # entry_date 결정 (공휴일 미처리 — 나중에 market_ohlcv 실제 날짜로 재확인)
    if entry_basis == "next_day_open":
        from datetime import timedelta
        sig = date.fromisoformat(signal_date)
        # 간단 근사: 다음 평일 (공휴일은 market_ohlcv fill 단계에서 보정)
        delta = 3 if sig.weekday() == 4 else 1  # 금요일 → 월요일
        entry_date = (sig + timedelta(days=delta)).isoformat()
    else:
        entry_date = signal_date

    now = datetime.now().isoformat(timespec="seconds")
    inserted = 0

    for rank, e in enumerate(sb_sorted[:10], start=1):
        cursor = conn.execute("""
            INSERT OR IGNORE INTO oos_signals
            (signal_date, ticker, formula_version, oos_schema,
             normalized_score, grade, earnings_score, valuation_score, risk_score,
             n_strong_buy, rank_in_strong_buy, is_top10,
             entry_basis, market_data_date, entry_date,
             oos_type, recorded_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            signal_date,
            e["ticker"],
            formula_version,
            OOS_SCHEMA_VERSION,
            e.get("normalized_score"),
            e.get("grade"),
            e.get("earnings_score"),
            e.get("valuation_score"),
            e.get("risk_score"),
            n_sb,
            rank,
            1,
            entry_basis,
            market_data_date,
            entry_date,
            oos_type,
            now,
        ))
        inserted += cursor.rowcount

    # Top10 외 STRONG_BUY도 기록 (rank 11+, is_top10=0)
    for rank, e in enumerate(sb_sorted[10:], start=11):
        cursor = conn.execute("""
            INSERT OR IGNORE INTO oos_signals
            (signal_date, ticker, formula_version, oos_schema,
             normalized_score, grade, earnings_score, valuation_score, risk_score,
             n_strong_buy, rank_in_strong_buy, is_top10,
             entry_basis, market_data_date, entry_date,
             oos_type, recorded_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            signal_date, e["ticker"], formula_version, OOS_SCHEMA_VERSION,
            e.get("normalized_score"), e.get("grade"),
            e.get("earnings_score"), e.get("valuation_score"), e.get("risk_score"),
            n_sb, rank, 0,
            entry_basis, market_data_date, entry_date,
            oos_type, now,
        ))
        inserted += cursor.rowcount

    conn.commit()
    conn.close()
    return inserted


# ---------------------------------------------------------------------------
# Entry 가격 채움 (market_ohlcv 사용)
# ---------------------------------------------------------------------------

def fill_entry_prices(db_path: Path | None = None) -> int:
    """
    entry_open / kospi_entry_open이 NULL인 pending 행을 market_ohlcv에서 채움.
    반환값: 업데이트된 행 수.
    """
    conn = init_db(db_path)
    ohlcv = sqlite3.connect(str(Path(__file__).parent / "market_ohlcv.db"))

    rows = conn.execute("""
        SELECT id, ticker, entry_date
        FROM oos_signals
        WHERE entry_open IS NULL AND entry_date IS NOT NULL
    """).fetchall()

    updated = 0
    now = datetime.now().isoformat(timespec="seconds")

    for row_id, ticker, entry_date in rows:
        stock_open = ohlcv.execute(
            "SELECT open FROM market_ohlcv WHERE ticker=? AND trade_date=?",
            (ticker, entry_date)
        ).fetchone()
        kospi_open = ohlcv.execute(
            "SELECT open FROM market_ohlcv WHERE ticker='1001' AND trade_date=?",
            (entry_date,)
        ).fetchone()

        if stock_open and kospi_open:
            conn.execute("""
                UPDATE oos_signals
                SET entry_open=?, kospi_entry_open=?,
                    status=CASE WHEN status='pending' THEN 'entry_filled' ELSE status END,
                    updated_at=?
                WHERE id=?
            """, (stock_open[0], kospi_open[0], now, row_id))
            updated += 1

    conn.commit()
    conn.close()
    ohlcv.close()
    return updated


# ---------------------------------------------------------------------------
# Alpha 채움 (market_ohlcv 사용)
# ---------------------------------------------------------------------------

def _get_trading_calendar(ohlcv: sqlite3.Connection) -> list[str]:
    rows = ohlcv.execute(
        "SELECT DISTINCT trade_date FROM market_ohlcv WHERE ticker='1001' ORDER BY trade_date"
    ).fetchall()
    return [r[0] for r in rows]


def _exit_date(entry_date: str, n: int, calendar: list[str]) -> str | None:
    try:
        idx = calendar.index(entry_date)
    except ValueError:
        # entry_date가 캘린더에 없으면 다음 거래일로
        idx = next((i for i, d in enumerate(calendar) if d >= entry_date), None)
        if idx is None:
            return None
    target = idx + n
    return calendar[target] if target < len(calendar) else None


def fill_alphas(db_path: Path | None = None) -> dict[str, int]:
    """
    entry_open이 채워진 행에 대해 각 alpha 윈도우를 채움.
    반환값: {'alpha_5d': n, 'alpha_10d': n, 'alpha_20d': n, 'alpha_60d': n}
    """
    conn = init_db(db_path)
    ohlcv = sqlite3.connect(str(Path(__file__).parent / "market_ohlcv.db"))
    calendar = _get_trading_calendar(ohlcv)

    rows = conn.execute("""
        SELECT id, ticker, entry_date, entry_open, kospi_entry_open,
               alpha_5d, alpha_10d, alpha_20d, alpha_60d
        FROM oos_signals
        WHERE entry_open IS NOT NULL AND status != 'complete'
    """).fetchall()

    counts: dict[str, int] = {f"alpha_{n}d": 0 for n in ALPHA_WINDOWS}
    now = datetime.now().isoformat(timespec="seconds")

    for (row_id, ticker, entry_date, entry_open, kospi_entry_open,
         a5, a10, a20, a60) in rows:
        if not entry_date or not entry_open or not kospi_entry_open:
            continue

        updates: dict[str, float] = {}
        for n, current_alpha in zip(ALPHA_WINDOWS, [a5, a10, a20, a60]):
            if current_alpha is not None:
                continue  # 이미 채워짐
            exit_d = _exit_date(entry_date, n, calendar)
            if not exit_d:
                continue
            stock_close = ohlcv.execute(
                "SELECT close FROM market_ohlcv WHERE ticker=? AND trade_date=?",
                (ticker, exit_d)
            ).fetchone()
            kospi_close = ohlcv.execute(
                "SELECT close FROM market_ohlcv WHERE ticker='1001' AND trade_date=?",
                (exit_d,)
            ).fetchone()
            if stock_close and kospi_close:
                alpha = ((stock_close[0] / entry_open) - (kospi_close[0] / kospi_entry_open)) * 100
                updates[f"alpha_{n}d"] = round(alpha, 4)
                counts[f"alpha_{n}d"] += 1

        if not updates:
            continue

        # status 계산
        a5_new  = updates.get("alpha_5d",  a5)
        a10_new = updates.get("alpha_10d", a10)
        a20_new = updates.get("alpha_20d", a20)
        a60_new = updates.get("alpha_60d", a60)
        if a60_new is not None:
            new_status = "complete"
        elif a20_new is not None:
            new_status = "alpha_20d"
        else:
            new_status = "entry_filled"

        set_clause = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [new_status, now, row_id]
        conn.execute(
            f"UPDATE oos_signals SET {set_clause}, status=?, updated_at=? WHERE id=?",
            vals
        )

    conn.commit()
    conn.close()
    ohlcv.close()
    return counts


# ---------------------------------------------------------------------------
# 현황 요약
# ---------------------------------------------------------------------------

def summary(db_path: Path | None = None) -> dict:
    conn = init_db(db_path)
    rows = conn.execute("""
        SELECT status, COUNT(*), COUNT(CASE WHEN is_top10=1 THEN 1 END)
        FROM oos_signals GROUP BY status
    """).fetchall()
    total = conn.execute("SELECT COUNT(DISTINCT signal_date) FROM oos_signals").fetchone()[0]
    primary_dates = conn.execute(
        "SELECT COUNT(DISTINCT signal_date) FROM oos_signals WHERE oos_type='primary'"
    ).fetchone()[0]
    catchup_dates = conn.execute(
        "SELECT COUNT(DISTINCT signal_date) FROM oos_signals WHERE oos_type='catchup'"
    ).fetchone()[0]
    conn.close()
    return {
        "signal_dates": total,
        "primary_dates": primary_dates,
        "catchup_dates": catchup_dates,
        "by_status": {r[0]: {"total": r[1], "top10": r[2]} for r in rows},
    }
