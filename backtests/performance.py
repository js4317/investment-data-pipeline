from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

_PERF_DIR = Path(__file__).parent / "performance"

ALPHA_WINDOWS = [20, 60, 120]


def _date_str(d: date) -> str:
    return d.strftime("%Y%m%d")


def _next_trading_day_approx(d: date) -> date:
    """다음 거래일 근사값. 공휴일 미처리 — 알파 분석 시 KRX 캘린더로 검증."""
    if d.weekday() >= 4:
        return d + timedelta(days=7 - d.weekday())
    return d + timedelta(days=1)


def _entry_date_from_snap(snap: dict) -> date:
    snap_date = date.fromisoformat(snap["date"])
    if snap.get("entry_basis", "same_day_open") == "next_day_open":
        return _next_trading_day_approx(snap_date)
    return snap_date


def _trading_dates_range(start: date, end: date) -> list[date]:
    """start~end 사이 KRX 거래일 목록 (양 끝 포함)."""
    from analyzer.data.krx import _call_krx
    from pykrx import stock as krx
    try:
        df = _call_krx(krx.get_index_ohlcv_by_date, _date_str(start), _date_str(end), "1001")
        if df is not None and not df.empty:
            return [d.date() for d in df.index]
    except Exception:
        pass
    result, cur = [], start
    while cur <= end:
        if cur.weekday() < 5:
            result.append(cur)
        cur += timedelta(days=1)
    return result


def _exit_date(entry: date, window: int) -> Optional[date]:
    """entry 기준 window 영업일 후 날짜."""
    far = entry + timedelta(days=int(window * 2.5) + 10)
    dates = _trading_dates_range(entry, far)
    after = [d for d in dates if d > entry]
    return after[window - 1] if len(after) >= window else None


def _ohlcv_stock(ticker: str, d: date) -> Optional[dict]:
    from analyzer.data.krx import _call_krx
    from pykrx import stock as krx
    ds = _date_str(d)
    try:
        df = _call_krx(krx.get_market_ohlcv_by_date, ds, ds, ticker)
        if df is not None and not df.empty:
            row = df.iloc[-1]
            return {"open": float(row["시가"]), "close": float(row["종가"])}
    except Exception:
        pass
    return None


def _ohlcv_kospi(d: date) -> Optional[dict]:
    from analyzer.data.krx import _call_krx
    from pykrx import stock as krx
    ds = _date_str(d)
    try:
        df = _call_krx(krx.get_index_ohlcv_by_date, ds, ds, "1001")
        if df is not None and not df.empty:
            row = df.iloc[-1]
            return {"open": float(row["시가"]), "close": float(row["종가"])}
    except Exception:
        pass
    return None


def list_calculable(window: int = 20) -> list[date]:
    """
    알파 계산 준비 완료된 스냅샷 날짜 목록.

    조건:
      - 아직 계산된 파일이 없을 것
      - exit_date < today (당일 종가 미확정 제외)
    """
    from backtests.snapshot import list_snapshot_dates, load_full

    today = date.today()
    result = []
    for snap_date in list_snapshot_dates():
        if (_PERF_DIR / f"{snap_date.isoformat()}_w{window}.json").exists():
            continue
        snap = load_full(snap_date)
        if not snap:
            continue
        entry = _entry_date_from_snap(snap)
        if today - entry < timedelta(days=int(window * 1.3)):
            continue
        xd = _exit_date(entry, window)
        if xd and xd < today:
            result.append(snap_date)
    return sorted(result)


def calc_performance(snap_date: date, window: int = 20) -> Optional[dict]:
    """
    snap_date 스냅샷 기준 window 영업일 알파 계산.

    entry price  = entry_date 시가 (Look-ahead Bias 방지)
    exit  price  = exit_date  종가
    alpha        = ticker_return - kospi_return (동기간)

    반환: 계산 결과 dict / 데이터 미달 시 None
    """
    from backtests.snapshot import load_full

    snap = load_full(snap_date)
    if not snap or not snap.get("entries"):
        return None

    entry_date = _entry_date_from_snap(snap)
    xd = _exit_date(entry_date, window)
    if xd is None or xd >= date.today():
        return None

    kospi_e = _ohlcv_kospi(entry_date)
    kospi_x = _ohlcv_kospi(xd)
    if not kospi_e or not kospi_x:
        return None

    ep_k = kospi_e["open"]
    xp_k = kospi_x["close"]
    if ep_k <= 0:
        return None

    kospi_ret = (xp_k - ep_k) / ep_k

    records = []
    for e in snap["entries"]:
        ticker = e["ticker"]
        st_e = _ohlcv_stock(ticker, entry_date)
        st_x = _ohlcv_stock(ticker, xd)
        if not st_e or not st_x or st_e["open"] <= 0:
            continue
        ret = (st_x["close"] - st_e["open"]) / st_e["open"]
        records.append({
            "ticker":           ticker,
            "grade":            e.get("grade"),
            "normalized_score": e.get("normalized_score"),
            "valuation_score":  e.get("valuation_score"),
            "earnings_score":   e.get("earnings_score"),
            "risk_score":       e.get("risk_score"),
            "supply_score":     e.get("supply_score"),
            "eq_band":          e.get("eq_band"),
            "sector":           e.get("sector"),
            "mom_signal":       e.get("mom_signal"),
            "entry_price":      round(st_e["open"], 2),
            "exit_price":       round(st_x["close"], 2),
            "ticker_return":    round(ret, 6),
            "alpha":            round(ret - kospi_ret, 6),
        })

    if not records:
        return None

    return {
        "snap_date":    snap_date.isoformat(),
        "entry_date":   entry_date.isoformat(),
        "exit_date":    xd.isoformat(),
        "entry_basis":  snap.get("entry_basis", "same_day_open"),
        "window":       window,
        "computed_at":  datetime.now().isoformat(timespec="seconds"),
        "kospi_entry":  round(ep_k, 2),
        "kospi_exit":   round(xp_k, 2),
        "kospi_return": round(kospi_ret, 6),
        "n_records":    len(records),
        "records":      records,
    }


def save(snap_date: date, data: dict, window: int = 20) -> Path:
    _PERF_DIR.mkdir(parents=True, exist_ok=True)
    path = _PERF_DIR / f"{snap_date.isoformat()}_w{window}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return path


def load(snap_date: date, window: int = 20) -> Optional[dict]:
    path = _PERF_DIR / f"{snap_date.isoformat()}_w{window}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def load_all(window: int = 20) -> list[dict]:
    """저장된 모든 performance 결과 로드 (날짜 오름차순)."""
    if not _PERF_DIR.exists():
        return []
    files = sorted(_PERF_DIR.glob(f"*_w{window}.json"))
    results = []
    for f in files:
        try:
            results.append(json.loads(f.read_text()))
        except Exception:
            pass
    return results


def run_batch(window: int = 20) -> list[Path]:
    """계산 가능한 모든 스냅샷 알파 계산 및 저장."""
    saved = []
    for snap_date in list_calculable(window):
        data = calc_performance(snap_date, window)
        if data:
            saved.append(save(snap_date, data, window))
    return saved
