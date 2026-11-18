import pickle
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

import config

_DB_PATH = Path.home() / ".investment_cache" / "cache.db"

TTL_MARKET   = config.CACHE["market_data_ttl_hours"]  * 3600
TTL_FUNDAMS  = config.CACHE["fundamentals_ttl_hours"] * 3600
TTL_EARNINGS = config.CACHE["earnings_ttl_days"]      * 86400


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cache "
        "(ns TEXT, key TEXT, val BLOB, ts REAL, PRIMARY KEY(ns, key))"
    )
    conn.commit()
    return conn


def get(ns: str, key: str, ttl_seconds: float) -> Optional[Any]:
    conn = None
    try:
        conn = _connect()
        row = conn.execute(
            "SELECT val, ts FROM cache WHERE ns=? AND key=?", (ns, key)
        ).fetchone()
        if row is None:
            return None
        val, ts = row
        if time.time() - ts > ttl_seconds:
            return None
        return pickle.loads(val)
    except Exception:
        return None
    finally:
        if conn is not None:
            conn.close()


def put(ns: str, key: str, value: Any) -> None:
    conn = None
    try:
        conn = _connect()
        conn.execute(
            "INSERT OR REPLACE INTO cache VALUES (?, ?, ?, ?)",
            (ns, key, pickle.dumps(value), time.time()),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        if conn is not None:
            conn.close()
