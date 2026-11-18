from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from analyzer.trade_journal.models import TradeRecord

_DB_PATH = Path(__file__).parent.parent.parent / "data" / "trade_journal.db"

_DDL = """
CREATE TABLE IF NOT EXISTS trade_records (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id          TEXT    NOT NULL UNIQUE,
    broker_trade_id   TEXT,
    source            TEXT    NOT NULL DEFAULT 'manual',
    engine_version    TEXT    NOT NULL,
    ticker            TEXT    NOT NULL,
    side              TEXT    NOT NULL CHECK (side IN ('BUY','SELL')),
    filled_price      REAL,
    filled_quantity   INTEGER,
    filled_at         TEXT,
    snapshot_date     TEXT,
    snapshot_gap_days INTEGER,
    score_snapshot    TEXT,
    ticker_name       TEXT,
    buy_reasons       TEXT    NOT NULL DEFAULT '[]',
    sell_reasons      TEXT    NOT NULL DEFAULT '[]',
    memo              TEXT,
    reviewed_at       TEXT,
    verdict           TEXT    CHECK (verdict IN ('thesis_correct','thesis_wrong','unclear') OR verdict IS NULL),
    review_note       TEXT,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at        TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_trade_ticker ON trade_records(ticker);
CREATE INDEX IF NOT EXISTS idx_trade_side   ON trade_records(side);
CREATE INDEX IF NOT EXISTS idx_trade_filled ON trade_records(filled_at);
"""


@contextmanager
def _conn():
    con = sqlite3.connect(_DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _migrate(con: sqlite3.Connection) -> None:
    cols = {row[1] for row in con.execute("PRAGMA table_info(trade_records)").fetchall()}
    if "buy_reasons" not in cols:
        con.execute("ALTER TABLE trade_records ADD COLUMN buy_reasons TEXT NOT NULL DEFAULT '[]'")
    if "ticker_name" not in cols:
        con.execute("ALTER TABLE trade_records ADD COLUMN ticker_name TEXT")


def _to_record(row: sqlite3.Row) -> TradeRecord:
    d = dict(row)
    d["score_snapshot"] = json.loads(d["score_snapshot"]) if d.get("score_snapshot") else None
    d["buy_reasons"]    = json.loads(d["buy_reasons"])    if d.get("buy_reasons")    else []
    d["sell_reasons"]   = json.loads(d["sell_reasons"])   if d.get("sell_reasons")   else []
    for key in ("id", "thesis"):
        d.pop(key, None)
    return TradeRecord(**d)


def init_db() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as con:
        con.executescript(_DDL)
        _migrate(con)


def insert(record: TradeRecord) -> None:
    with _conn() as con:
        con.execute(
            """
            INSERT INTO trade_records
                (trade_id, broker_trade_id, source, engine_version,
                 ticker, ticker_name, side, filled_price, filled_quantity, filled_at,
                 snapshot_date, snapshot_gap_days, score_snapshot,
                 buy_reasons, sell_reasons, memo)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                record.trade_id,
                record.broker_trade_id,
                record.source,
                record.engine_version,
                record.ticker,
                record.ticker_name,
                record.side,
                record.filled_price,
                record.filled_quantity,
                record.filled_at,
                record.snapshot_date,
                record.snapshot_gap_days,
                json.dumps(record.score_snapshot, ensure_ascii=False) if record.score_snapshot else None,
                json.dumps(record.buy_reasons,  ensure_ascii=False),
                json.dumps(record.sell_reasons, ensure_ascii=False),
                record.memo,
            ),
        )


def update(trade_id: str, **fields) -> None:
    if not fields:
        return
    for json_field in ("score_snapshot", "buy_reasons", "sell_reasons"):
        if json_field in fields and fields[json_field] is not None:
            fields[json_field] = json.dumps(fields[json_field], ensure_ascii=False)
    fields["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    set_clause = ", ".join(f"{k}=?" for k in fields)
    with _conn() as con:
        con.execute(
            f"UPDATE trade_records SET {set_clause} WHERE trade_id=?",
            (*fields.values(), trade_id),
        )


def get_all() -> list[TradeRecord]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM trade_records ORDER BY filled_at DESC, created_at DESC"
        ).fetchall()
    return [_to_record(r) for r in rows]


def get_by_ticker(ticker: str) -> list[TradeRecord]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM trade_records WHERE ticker=? ORDER BY filled_at DESC",
            (ticker,),
        ).fetchall()
    return [_to_record(r) for r in rows]


def get_by_trade_id(trade_id: str) -> Optional[TradeRecord]:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM trade_records WHERE trade_id=?", (trade_id,)
        ).fetchone()
    return _to_record(row) if row else None
