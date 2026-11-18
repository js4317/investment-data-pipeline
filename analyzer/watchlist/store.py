from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Optional

from constants import Grade, Confidence
from analyzer.watchlist.models import WatchlistItem, WatchlistState, WatchlistTag, ReviewTrigger

_DB_PATH = Path.home() / ".investment_cache" / "watchlist.db"

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS watchlist (
    ticker          TEXT PRIMARY KEY,
    state           TEXT NOT NULL,
    added_date      TEXT NOT NULL,
    last_analyzed   TEXT,
    last_grade      TEXT,
    last_score      INTEGER,
    last_confidence TEXT,
    tag             TEXT NOT NULL DEFAULT 'general',
    name            TEXT
);
CREATE TABLE IF NOT EXISTS review_triggers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker     TEXT NOT NULL,
    event      TEXT NOT NULL,
    detail     TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    for stmt in _CREATE_SQL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()
    # 기존 DB 마이그레이션 — tag 컬럼 없으면 추가
    for col_sql in [
        "ALTER TABLE watchlist ADD COLUMN tag TEXT NOT NULL DEFAULT 'general'",
        "ALTER TABLE watchlist ADD COLUMN name TEXT",
    ]:
        try:
            conn.execute(col_sql)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # 이미 존재
    return conn


def _row_to_item(row: tuple) -> WatchlistItem:
    ticker, state, added, analyzed, grade, score, conf = row[:7]
    tag  = row[7] if len(row) > 7 else "general"
    name = row[8] if len(row) > 8 else None
    return WatchlistItem(
        ticker=ticker,
        state=WatchlistState(state),
        added_date=date.fromisoformat(added),
        last_analyzed=date.fromisoformat(analyzed) if analyzed else None,
        last_grade=Grade(grade) if grade else None,
        last_score=score,
        last_confidence=Confidence(conf) if conf else None,
        tag=WatchlistTag(tag) if tag else WatchlistTag.GENERAL,
        name=name,
    )


def load_all() -> list[WatchlistItem]:
    conn = None
    try:
        conn = _connect()
        rows = conn.execute("SELECT * FROM watchlist ORDER BY ticker").fetchall()
        return [_row_to_item(r) for r in rows]
    finally:
        if conn:
            conn.close()


def load(ticker: str) -> Optional[WatchlistItem]:
    conn = None
    try:
        conn = _connect()
        row = conn.execute("SELECT * FROM watchlist WHERE ticker=?", (ticker,)).fetchone()
        return _row_to_item(row) if row else None
    finally:
        if conn:
            conn.close()


def save(item: WatchlistItem) -> None:
    conn = None
    try:
        conn = _connect()
        conn.execute(
            "INSERT OR REPLACE INTO watchlist VALUES (?,?,?,?,?,?,?,?,?)",
            (
                item.ticker,
                item.state.value,
                item.added_date.isoformat(),
                item.last_analyzed.isoformat() if item.last_analyzed else None,
                item.last_grade.value if item.last_grade else None,
                item.last_score,
                item.last_confidence.value if item.last_confidence else None,
                item.tag.value,
                item.name,
            ),
        )
        conn.commit()
    finally:
        if conn:
            conn.close()


def save_trigger(trigger: ReviewTrigger, as_of_date: date) -> None:
    conn = None
    try:
        conn = _connect()
        conn.execute(
            "INSERT INTO review_triggers (ticker, event, detail, created_at) VALUES (?,?,?,?)",
            (trigger.ticker, trigger.event, trigger.detail, as_of_date.isoformat()),
        )
        conn.commit()
    finally:
        if conn:
            conn.close()


def load_triggers(ticker: str, limit: int = 10) -> list[dict]:
    conn = None
    try:
        conn = _connect()
        rows = conn.execute(
            "SELECT event, detail, created_at FROM review_triggers "
            "WHERE ticker=? ORDER BY created_at DESC, id DESC LIMIT ?",
            (ticker, limit),
        ).fetchall()
        return [{"event": r[0], "detail": r[1], "date": r[2]} for r in rows]
    finally:
        if conn:
            conn.close()


def remove(ticker: str) -> None:
    conn = None
    try:
        conn = _connect()
        conn.execute("DELETE FROM watchlist WHERE ticker=?", (ticker,))
        conn.commit()
    finally:
        if conn:
            conn.close()
