"""Stage 3 official prices (design §7.4). Explicit SQLite path; no import-time writes.

- Only a date×market that passes the completeness checks is stored as official
  prices, in the same transaction as its `complete` collection record.
- Failed/partial/suspect responses are history only and never occupy a price key.
- Re-collection with identical values is allowed; different values are recorded
  as a conflict and never overwrite official prices.
- NULL / non-positive prices are stored as returned (evidence) and judged
  unusable when outcomes are filled.
The legacy `market_ohlcv` table is not read or written here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
import hashlib
import json
import os
import sqlite3
import uuid

from database.signal_store import KST, SignalStore, _json, _stamp
from database.trading_calendar import CalendarSnapshot

MARKETS = ("KOSPI", "KOSDAQ", "KOSPI_INDEX")
STOCK_MARKETS = ("KOSPI", "KOSDAQ")
INDEX_TICKER = "1001"
FIRST_ATTEMPT = time(18, 30)          # 당일 거래일 수집 허용 시작 (확정치 공개 보장 아님)
COUNT_TOLERANCE = 0.02

_STOCK_FIELDS = ("ISU_CD", "BAS_DD", "TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "TDD_CLSPRC")
_INDEX_FIELDS = ("IDX_NM", "BAS_DD", "OPNPRC_IDX", "HGPRC_IDX", "LWPRC_IDX", "CLSPRC_IDX")
_PRICE_COLS = ("open", "high", "low", "close", "volume", "mktcap")

_URLS = {
    "KOSPI": "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd",
    "KOSDAQ": "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd",
    "KOSPI_INDEX": "https://data-dbg.krx.co.kr/svc/apis/idx/kospi_dd_trd",
}


class _Unparseable(ValueError):
    pass


def _num(value) -> float | None:
    if value is None:
        return None
    s = str(value).replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError as e:
        raise _Unparseable(s) from e


@dataclass(frozen=True)
class ParsedRow:
    ticker: str
    bas_dd: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    mktcap: float | None
    raw_json: str

    @property
    def raw_hash(self) -> str:
        return hashlib.sha256(self.raw_json.encode()).hexdigest()

    def values(self) -> tuple:
        return (self.bas_dd, self.open, self.high, self.low, self.close, self.volume, self.mktcap)


def validate_response(market: str, trade_date: date, rows: list[dict] | None,
                      error: str | None, prev_count: int | None
                      ) -> tuple[str, list[str], list[ParsedRow], int]:
    """Return (status, reasons, parsed_rows, got_n). Pure; no storage."""
    if market not in MARKETS:
        raise ValueError("unknown market")
    if error is not None or rows is None:
        return "failed", [f"fetch_error:{error}"], [], 0
    got_n = len(rows)
    if got_n == 0:
        return "failed", ["empty_response"], [], 0
    want = trade_date.strftime("%Y%m%d")
    if any(not isinstance(r, dict) or str(r.get("BAS_DD", "")).strip() != want for r in rows):
        return "failed", ["bas_dd_mismatch"], [], got_n

    reasons: list[str] = []
    parsed: list[ParsedRow] = []
    fields = _INDEX_FIELDS if market == "KOSPI_INDEX" else _STOCK_FIELDS
    if any(f not in r for r in rows for f in fields):
        return "partial", ["missing_fields"], [], got_n
    try:
        if market == "KOSPI_INDEX":
            kospi = [r for r in rows if str(r["IDX_NM"]).strip() == "코스피"]
            if len(kospi) != 1:
                return "partial", ["kospi_row_missing" if not kospi else "kospi_row_duplicate"], [], got_n
            r = kospi[0]
            parsed.append(ParsedRow(INDEX_TICKER, want, _num(r["OPNPRC_IDX"]), _num(r["HGPRC_IDX"]),
                                    _num(r["LWPRC_IDX"]), _num(r["CLSPRC_IDX"]), None, None,
                                    json.dumps(r, sort_keys=True, ensure_ascii=False)))
        else:
            tickers = [str(r["ISU_CD"]).strip() for r in rows]
            if any(not t for t in tickers):
                return "partial", ["empty_ticker"], [], got_n
            if len(set(tickers)) != len(tickers):
                return "partial", ["duplicate_ticker"], [], got_n
            for t, r in zip(tickers, rows):
                parsed.append(ParsedRow(t, want, _num(r["TDD_OPNPRC"]), _num(r["TDD_HGPRC"]),
                                        _num(r["TDD_LWPRC"]), _num(r["TDD_CLSPRC"]),
                                        _num(r.get("ACC_TRDVOL")), _num(r.get("MKTCAP")),
                                        json.dumps(r, sort_keys=True, ensure_ascii=False)))
    except _Unparseable:
        return "partial", ["unparseable_value"], [], got_n

    if prev_count is None:
        reasons.append("no_count_baseline")
    elif prev_count <= 0 or abs(got_n - prev_count) / prev_count > COUNT_TOLERANCE:
        return "suspect", ["count_out_of_range"], [], got_n
    return "complete", reasons, parsed, got_n


def krx_open_api_fetch(market: str, trade_date: date) -> list[dict]:
    """Real KRX Open API call (not used by tests). Explicit timeout, no fallback source."""
    import requests
    key = os.environ.get("KRX_OPEN_API_KEY", "")
    if not key:
        raise RuntimeError("KRX_OPEN_API_KEY missing")
    resp = requests.post(_URLS[market], headers={"AUTH_KEY": key, "Content-Type": "application/json"},
                         json={"basDd": trade_date.strftime("%Y%m%d")}, timeout=(10, 60))
    resp.raise_for_status()
    return resp.json().get("OutBlock_1", [])


SCHEMA = """
CREATE TABLE IF NOT EXISTS price_collections (
 collection_id TEXT PRIMARY KEY, trade_date TEXT NOT NULL,
 market TEXT NOT NULL CHECK(market IN ('KOSPI','KOSDAQ','KOSPI_INDEX')),
 fetched_at TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('complete','partial','failed','suspect','conflict')),
 expected_n INTEGER, got_n INTEGER NOT NULL, response_hash TEXT, reasons TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS price_collections_key ON price_collections(market,trade_date,status);
CREATE TABLE IF NOT EXISTS official_prices (
 trade_date TEXT NOT NULL, market TEXT NOT NULL, ticker TEXT NOT NULL,
 open REAL, high REAL, low REAL, close REAL, volume REAL, mktcap REAL,
 bas_dd TEXT NOT NULL, raw_json TEXT NOT NULL, raw_hash TEXT NOT NULL,
 collection_id TEXT NOT NULL, PRIMARY KEY(trade_date,market,ticker),
 FOREIGN KEY(collection_id) REFERENCES price_collections(collection_id)
);
CREATE INDEX IF NOT EXISTS official_prices_ticker ON official_prices(ticker,trade_date);
CREATE TABLE IF NOT EXISTS price_conflicts (
 conflict_id INTEGER PRIMARY KEY, collection_id TEXT NOT NULL, trade_date TEXT NOT NULL,
 market TEXT NOT NULL, ticker TEXT NOT NULL, existing_json TEXT, incoming_json TEXT,
 FOREIGN KEY(collection_id) REFERENCES price_collections(collection_id)
);
CREATE TRIGGER IF NOT EXISTS official_prices_only_complete BEFORE INSERT ON official_prices
WHEN (SELECT status FROM price_collections WHERE collection_id=NEW.collection_id) IS NOT 'complete'
BEGIN SELECT RAISE(ABORT,'prices require complete collection'); END;
"""


class OfficialPriceStore(SignalStore):
    """Shares the stage-1 production path guard; signal tables are created too (FK targets)."""

    def initialize(self) -> None:
        super().initialize()
        with self._connection() as conn:
            conn.executescript(SCHEMA)
            for table in ("price_collections", "official_prices", "price_conflicts"):
                for action in ("UPDATE", "DELETE"):
                    conn.execute(f"""CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action}
                    BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'immutable record'); END""")

    # ── 수집 ────────────────────────────────────────────────────────────────
    def collect(self, trade_date: date, market: str, fetcher, calendar: CalendarSnapshot,
                now: datetime) -> tuple[str, str]:
        """Fetch and record one date×market. Returns (collection_id, status)."""
        if calendar.status(trade_date) != "session":
            raise ValueError("not_a_verified_session")
        local = datetime.fromisoformat(_stamp(now))
        if trade_date > local.date() or (trade_date == local.date() and local.time() < FIRST_ATTEMPT):
            raise ValueError("too_early_for_trade_date")
        try:
            rows, error = fetcher(market, trade_date), None
        except Exception as e:                     # recorded as failed history
            rows, error = None, f"{type(e).__name__}: {e}"
        return self.record_collection(trade_date, market, rows, error, now)

    def record_collection(self, trade_date: date, market: str, rows: list[dict] | None,
                          error: str | None, now: datetime) -> tuple[str, str]:
        cid = uuid.uuid4().hex
        td = trade_date.isoformat()
        response_hash = None if rows is None else hashlib.sha256(
            json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self._transaction() as conn:
            prev = conn.execute("""SELECT got_n FROM price_collections WHERE market=? AND status='complete'
                AND trade_date<? ORDER BY trade_date DESC, fetched_at DESC LIMIT 1""", (market, td)).fetchone()
            prev_n = prev[0] if prev else None
            status, reasons, parsed, got_n = validate_response(market, trade_date, rows, error, prev_n)
            conflicts: list[tuple] = []
            if status == "complete":
                existing = {r["ticker"]: r for r in conn.execute(
                    "SELECT * FROM official_prices WHERE trade_date=? AND market=?", (td, market))}
                if existing:
                    incoming = {p.ticker: p for p in parsed}
                    for t in sorted(existing.keys() | incoming.keys()):
                        old, new = existing.get(t), incoming.get(t)
                        old_v = None if old is None else (old["bas_dd"], *(old[c] for c in _PRICE_COLS))
                        new_v = None if new is None else new.values()
                        if old_v != new_v:
                            conflicts.append((t, old_v, new_v))
                    if conflicts:
                        status = "conflict"
                    else:
                        reasons.append("recollected_identical")
                    parsed = []          # 기존 공식 가격이 있으면 행을 추가하지 않는다
            conn.execute("INSERT INTO price_collections VALUES(?,?,?,?,?,?,?,?,?)",
                         (cid, td, market, _stamp(now), status, prev_n, got_n, response_hash,
                          _json(sorted(set(reasons)))))
            for t, old_v, new_v in conflicts:
                conn.execute("""INSERT INTO price_conflicts(collection_id,trade_date,market,ticker,
                    existing_json,incoming_json) VALUES(?,?,?,?,?,?)""",
                    (cid, td, market, t, _json(old_v), _json(new_v)))
            if status == "complete" and parsed:
                self._insert_prices(conn, td, market, cid, parsed)
        return cid, status

    def _insert_prices(self, conn: sqlite3.Connection, td: str, market: str, cid: str,
                       parsed: list[ParsedRow]) -> None:
        conn.executemany("INSERT INTO official_prices VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         [(td, market, p.ticker, p.open, p.high, p.low, p.close, p.volume, p.mktcap,
                           p.bas_dd, p.raw_json, p.raw_hash, cid) for p in parsed])

    # ── 조회 ────────────────────────────────────────────────────────────────
    def market_complete(self, trade_date: date, market: str) -> bool:
        with self._connection() as conn:
            return conn.execute("""SELECT 1 FROM price_collections WHERE trade_date=? AND market=?
                AND status='complete' LIMIT 1""", (trade_date.isoformat(), market)).fetchone() is not None

    def date_complete(self, trade_date: date) -> bool:
        return all(self.market_complete(trade_date, m) for m in MARKETS)

    def pending(self, calendar: CalendarSnapshot, start: date, end: date) -> list[tuple[date, str]]:
        """Verified sessions in [start, end] whose market is not complete yet (retry targets)."""
        out = []
        day = start
        while day <= end:
            if calendar.status(day) == "session":
                out.extend((day, m) for m in MARKETS if not self.market_complete(day, m))
            day = date.fromordinal(day.toordinal() + 1)
        return out

    def official_price(self, trade_date: date, ticker: str) -> sqlite3.Row | None:
        """Official stock/index row for a complete date; None if absent."""
        with self._connection() as conn:
            if ticker == INDEX_TICKER:
                return conn.execute("""SELECT * FROM official_prices WHERE trade_date=? AND market='KOSPI_INDEX'
                    AND ticker=?""", (trade_date.isoformat(), ticker)).fetchone()
            rows = conn.execute("""SELECT * FROM official_prices WHERE trade_date=? AND ticker=?
                AND market IN ('KOSPI','KOSDAQ')""", (trade_date.isoformat(), ticker)).fetchall()
        if len(rows) > 1:
            raise ValueError("ticker present in both stock markets")
        return rows[0] if rows else None
