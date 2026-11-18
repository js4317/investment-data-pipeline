from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from constants import ENGINE_VERSION
from analyzer.data.momentum import _fetch_ticker_series, _nearest_price, _pct

_SNAP_DIR = Path(__file__).parent.parent.parent / "data" / "screener_snapshots"
_SNAP_DIR.mkdir(parents=True, exist_ok=True)

_LOOKBACK_FWD = 10


def _snap_path(as_of: date) -> Path:
    return _SNAP_DIR / f"{as_of}.json"


def save_daily_snapshot(
    candidates: list[dict],
    as_of: date,
    market_state: Optional[str] = None,
) -> Path:
    """스크리너 결과를 등급별로 묶어 저장. 당일 파일이 이미 있으면 덮어씀."""
    from constants import Grade

    buckets: dict[str, list[dict]] = {
        "strong_buy": [],
        "buy":        [],
        "neutral":    [],
        "avoid":      [],
    }

    for c in candidates:
        grade = c.get("grade", "").lower().replace(" ", "_")
        entry = {
            "ticker":       c["ticker"],
            "name":         c.get("name", c["ticker"]),
            "score":        c.get("score"),
            "grade":        c.get("grade"),
            "confidence":   c.get("confidence"),
            "mom_signal":   c.get("mom_signal"),
            "mom_strength": c.get("mom_strength"),
        }
        if grade in buckets:
            buckets[grade].append(entry)

    snapshot = {
        "date":           str(as_of),
        "engine_version": ENGINE_VERSION,
        "market_state":   market_state,
        "strong_buy":     buckets["strong_buy"],
        "buy":            buckets["buy"],
        "neutral":        buckets["neutral"],
        "avoid":          buckets["avoid"],
    }

    path = _snap_path(as_of)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_daily_snapshot(as_of: date) -> Optional[dict]:
    path = _snap_path(as_of)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_snapshot_dates() -> list[date]:
    dates = []
    for f in sorted(_SNAP_DIR.glob("*.json")):
        try:
            dates.append(date.fromisoformat(f.stem))
        except ValueError:
            pass
    return dates


def _fetch_price_spot(ticker: str, as_of: date) -> Optional[float]:
    start = as_of - timedelta(days=_LOOKBACK_FWD)
    series = _fetch_ticker_series(ticker, start, as_of)
    return _nearest_price(series, as_of, max_gap=_LOOKBACK_FWD)


def compute_group_returns(
    snap_date: date,
    forward_days: int = 30,
    grade_key: str = "strong_buy",
    top_n: Optional[int] = None,
) -> dict:
    """특정 날짜 스냅샷의 grade_key 그룹 forward return 계산."""
    snap = load_daily_snapshot(snap_date)
    if snap is None:
        return {"error": "snapshot not found"}

    tickers = snap.get(grade_key, [])
    if top_n:
        tickers = sorted(tickers, key=lambda x: -(x.get("score") or 0))[:top_n]

    target_date = snap_date + timedelta(days=forward_days)
    if target_date > date.today():
        return {"status": "pending", "snap_date": str(snap_date), "grade": grade_key, "count": len(tickers)}

    returns = []
    for entry in tickers:
        price_then = _fetch_price_spot(entry["ticker"], snap_date)
        price_now  = _fetch_price_spot(entry["ticker"], target_date)
        if price_then and price_now:
            returns.append(_pct(price_now, price_then))

    if not returns:
        return {"status": "unresolvable", "snap_date": str(snap_date), "grade": grade_key, "count": len(tickers)}

    returns_sorted = sorted(returns)
    n = len(returns_sorted)
    median = returns_sorted[n // 2] if n % 2 else (returns_sorted[n//2-1] + returns_sorted[n//2]) / 2

    return {
        "status":       "resolved",
        "snap_date":    str(snap_date),
        "grade":        grade_key,
        "forward_days": forward_days,
        "count":        len(tickers),
        "resolved":     n,
        "avg":          round(sum(returns) / n, 2),
        "median":       round(median, 2),
        "win_rate":     round(sum(1 for r in returns if r > 0) / n * 100, 1),
        "engine_version": snap.get("engine_version"),
        "market_state": snap.get("market_state"),
    }
