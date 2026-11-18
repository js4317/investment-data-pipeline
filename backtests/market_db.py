"""
backtests/market_db.py — 로컬 시장 가격 DB (SQLite).

역할:
  - 개별 종목 + KOSPI 인덱스 OHLCV를 로컬에 저장.
  - Performance / Calibration 페이지가 KRX를 직접 호출하지 않도록 한다.
  - KOSPI 인덱스는 ticker='1001'로 동일 테이블에 저장.

수집 계층 (KRX 접근은 여기서만):
  collect_date(trade_date)  — 해당 날짜 전체 시장 OHLCV batch 수집 후 저장.
                              KOSPI 2번 + KOSDAQ 1번 = 3 API 호출.

조회 계층 (KRX 호출 없음):
  get_ohlcv(ticker, date)   — 단일 조회.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Optional

_DB_PATH = Path(__file__).parent.parent / "database" / "market_ohlcv.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS market_ohlcv (
    ticker      TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL NOT NULL,
    volume      INTEGER,
    source      TEXT NOT NULL DEFAULT 'krx',
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (ticker, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_market_ohlcv_date   ON market_ohlcv(trade_date);
CREATE INDEX IF NOT EXISTS idx_market_ohlcv_ticker ON market_ohlcv(ticker);
"""

_KOSPI_IDX_TICKER = "1001"


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """테이블 생성 (없을 때만)."""
    with _connect() as conn:
        conn.executescript(_CREATE_TABLE)


# ---------------------------------------------------------------------------
# 저장
# ---------------------------------------------------------------------------

def insert_batch(rows: list[dict], source: str = "krx") -> int:
    """
    bulk insert (기존 데이터 보존 — INSERT OR IGNORE).
    rows = list of dict with keys: ticker, trade_date, open, high, low, close, volume.
    단일 transaction. 반환: 삽입된 행 수.

    정책: 동일 (ticker, trade_date)가 이미 있으면 무시.
    Alpha 재현성 보장 — 한번 기록된 가격은 명시적 repair 없이 덮어쓰지 않는다.
    """
    if not rows:
        return 0
    records = [
        (
            r["ticker"],
            r["trade_date"],
            r.get("open"),
            r.get("high"),
            r.get("low"),
            r["close"],
            r.get("volume"),
            source,
        )
        for r in rows
    ]
    sql = """
        INSERT OR IGNORE INTO market_ohlcv
            (ticker, trade_date, open, high, low, close, volume, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
    with _connect() as conn:
        before = conn.execute("SELECT total_changes()").fetchone()[0]
        conn.executemany(sql, records)
        after  = conn.execute("SELECT total_changes()").fetchone()[0]
    return after - before



# ---------------------------------------------------------------------------
# 조회
# ---------------------------------------------------------------------------

def get_ohlcv(ticker: str, trade_date: date) -> Optional[dict]:
    """단일 종목/날짜 조회. 없으면 None."""
    sql = "SELECT * FROM market_ohlcv WHERE ticker=? AND trade_date=?"
    with _connect() as conn:
        row = conn.execute(sql, (ticker, trade_date.isoformat())).fetchone()
    return dict(row) if row else None



# ---------------------------------------------------------------------------
# 수집 (KRX 접근은 이 함수에서만)
# ---------------------------------------------------------------------------

def _parse_price(val: str) -> Optional[float]:
    """KRX Open API 가격 문자열 → float. 빈 문자열이면 None."""
    if not val:
        return None
    try:
        return float(str(val).replace(",", ""))
    except Exception:
        return None


def _collect_via_open_api(trade_date: date) -> list[dict]:
    """
    KRX Open API (data-dbg.krx.co.kr) 를 통해 전체 시장 OHLCV 수집.
    인증키: 환경변수 KRX_OPEN_API_KEY.
    API 호출 3회: KOSPI 종목 1 + KOSDAQ 종목 1 + KOSPI 지수 1.
    """
    import os
    import requests

    key = os.getenv("KRX_OPEN_API_KEY", "")
    if not key:
        raise RuntimeError("KRX_OPEN_API_KEY 환경변수 없음")

    headers = {"AUTH_KEY": key}
    ds      = trade_date.strftime("%Y%m%d")
    td      = trade_date.isoformat()
    rows: list[dict] = []

    # ── KOSPI + KOSDAQ 개별 종목
    for url in (
        "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd",
        "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd",
    ):
        try:
            resp  = requests.post(url, headers=headers, json={"basDd": ds}, timeout=30)
            resp.raise_for_status()
            items = resp.json().get("OutBlock_1", [])
            for item in items:
                close = _parse_price(item.get("TDD_CLSPRC", ""))
                if close is None:
                    continue
                rows.append({
                    "ticker":     item["ISU_CD"],
                    "trade_date": td,
                    "open":       _parse_price(item.get("TDD_OPNPRC", "")),
                    "high":       _parse_price(item.get("TDD_HGPRC", "")),
                    "low":        _parse_price(item.get("TDD_LWPRC", "")),
                    "close":      close,
                    "volume":     int(item["ACC_TRDVOL"].replace(",", "")) if item.get("ACC_TRDVOL") else None,
                })
        except Exception as e:
            print(f"[market_db] Open API 종목 수집 실패 ({url}): {e}")

    # ── KOSPI 종합지수
    try:
        resp  = requests.post(
            "https://data-dbg.krx.co.kr/svc/apis/idx/kospi_dd_trd",
            headers=headers, json={"basDd": ds}, timeout=30,
        )
        resp.raise_for_status()
        for item in resp.json().get("OutBlock_1", []):
            if item.get("IDX_NM", "").strip() == "코스피":
                close = _parse_price(item.get("CLSPRC_IDX", ""))
                if close is None:
                    continue
                rows.append({
                    "ticker":     _KOSPI_IDX_TICKER,
                    "trade_date": td,
                    "open":       _parse_price(item.get("OPNPRC_IDX", "")),
                    "high":       _parse_price(item.get("HGPRC_IDX", "")),
                    "low":        _parse_price(item.get("LWPRC_IDX", "")),
                    "close":      close,
                    "volume":     None,
                })
                break
    except Exception as e:
        print(f"[market_db] Open API 지수 수집 실패: {e}")

    return rows


def collect_date(trade_date: date) -> int:
    """
    trade_date 기준 전체 시장 OHLCV 수집 후 저장.

    우선순위:
      1. KRX Open API (data-dbg.krx.co.kr) — 공식 API, 차단 없음
      2. pykrx fallback — Open API 실패 시

    API 호출: 3회 (KOSPI 종목 1 + KOSDAQ 종목 1 + KOSPI 지수 1)
    반환: 저장된 행 수.
    """
    # ── 1차: KRX Open API
    try:
        rows = _collect_via_open_api(trade_date)
        if rows:
            return insert_batch(rows, source="krx_open_api")
        print("[market_db] Open API 결과 없음 — pykrx fallback 시도")
    except Exception as e:
        print(f"[market_db] Open API 실패: {e} — pykrx fallback 시도")

    # ── 2차: pykrx fallback
    from analyzer.data.krx import _call_krx
    from pykrx import stock as krx

    ds   = trade_date.strftime("%Y%m%d")
    rows = []

    for market in ("KOSPI", "KOSDAQ"):
        try:
            df = _call_krx(krx.get_market_ohlcv_by_ticker, ds, market=market)
            if df is None or df.empty:
                continue
            for ticker, row in df.iterrows():
                try:
                    rows.append({
                        "ticker":     str(ticker),
                        "trade_date": trade_date.isoformat(),
                        "open":       float(row.get("시가", row.get("Open", 0)) or 0) or None,
                        "high":       float(row.get("고가", row.get("High", 0)) or 0) or None,
                        "low":        float(row.get("저가", row.get("Low",  0)) or 0) or None,
                        "close":      float(row.get("종가", row.get("Close", 0))),
                        "volume":     int(row.get("거래량", row.get("Volume", 0)) or 0) or None,
                    })
                except Exception:
                    continue
        except Exception as e:
            print(f"[market_db] pykrx {market} 수집 실패: {e}")

    try:
        df_idx = _call_krx(krx.get_index_ohlcv_by_date, ds, ds, "1001")
        if df_idx is not None and not df_idx.empty:
            row = df_idx.iloc[-1]
            rows.append({
                "ticker":     _KOSPI_IDX_TICKER,
                "trade_date": trade_date.isoformat(),
                "open":       float(row.get("시가", row.get("Open",  0)) or 0) or None,
                "high":       float(row.get("고가", row.get("High",  0)) or 0) or None,
                "low":        float(row.get("저가", row.get("Low",   0)) or 0) or None,
                "close":      float(row.get("종가", row.get("Close", 0))),
                "volume":     None,
            })
    except Exception as e:
        print(f"[market_db] pykrx 인덱스 수집 실패: {e}")

    return insert_batch(rows)
