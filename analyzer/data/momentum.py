from __future__ import annotations

import concurrent.futures
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Optional

from pykrx import stock as krx

from analyzer.data import cache as _cache

log = logging.getLogger(__name__)

# 배치 작업 스레드별 조회 오류 기록 (fetch_momentum 내부에서 삼켜지는 오류 관측용)
_tls = threading.local()


def _note_fetch_error(e: Exception) -> None:
    errs = getattr(_tls, "errors", None)
    if errs is not None:
        errs.append(f"{type(e).__name__}: {e}")

_KOSPI_IDX  = "1001"
_LOOKBACK   = 240  # 6M(~182일) + 넉넉한 buffer (연휴 등 대비)

# STRONG 판정 임계값 — "의미 있는 초과수익"만 STRONG으로
_STRONG_1M  = 3.0   # 1M 상대수익률 > +3%p
_STRONG_3M  = 5.0   # 3M 상대수익률 > +5%p
_STRONG_6M  = 5.0   # 6M 상대수익률 > +5%p

_VALUE_TRAP_MIN_SCORE = 80   # normalized_score(0~100) 기준 — 총점 80+이면 고평가 아님에도 하락


class MomentumSignal(str, Enum):
    STRONG      = "strong"
    POSITIVE    = "positive"
    NEUTRAL     = "neutral"
    NEGATIVE    = "negative"
    UNAVAILABLE = "unavailable"


MOMENTUM_EMOJI: dict[MomentumSignal, str] = {
    MomentumSignal.STRONG:      "🔥",
    MomentumSignal.POSITIVE:    "✅",
    MomentumSignal.NEUTRAL:     "⚪",
    MomentumSignal.NEGATIVE:    "⚠️",
    MomentumSignal.UNAVAILABLE: "—",
}

MOMENTUM_LABEL: dict[MomentumSignal, str] = {
    MomentumSignal.STRONG:      "STRONG MOM",
    MomentumSignal.POSITIVE:    "POSITIVE MOM",
    MomentumSignal.NEUTRAL:     "NEUTRAL",
    MomentumSignal.NEGATIVE:    "NEGATIVE MOM",
    MomentumSignal.UNAVAILABLE: "데이터 없음",
}


@dataclass
class MomentumData:
    ret_1m:        Optional[float]
    ret_3m:        Optional[float]
    ret_6m:        Optional[float]
    kospi_1m:      Optional[float]
    kospi_3m:      Optional[float]
    kospi_6m:      Optional[float]
    signal:        MomentumSignal
    is_value_trap: bool = False
    positive_count: int = 0
    negative_count: int = 0

    @property
    def rel_1m(self) -> Optional[float]:
        if self.ret_1m is None or self.kospi_1m is None:
            return None
        return self.ret_1m - self.kospi_1m

    @property
    def rel_3m(self) -> Optional[float]:
        if self.ret_3m is None or self.kospi_3m is None:
            return None
        return self.ret_3m - self.kospi_3m

    @property
    def rel_6m(self) -> Optional[float]:
        if self.ret_6m is None or self.kospi_6m is None:
            return None
        return self.ret_6m - self.kospi_6m

    @property
    def strength(self) -> float:
        """장기 모멘텀 가중 합산 (1M×0.5 + 3M×1.0 + 6M×1.5) — 강도 랭킹용."""
        return (
            0.5 * (self.rel_1m or 0.0)
            + 1.0 * (self.rel_3m or 0.0)
            + 1.5 * (self.rel_6m or 0.0)
        )


def _pct(now: float, then: float) -> Optional[float]:
    if then == 0:
        return None
    return (now - then) / then * 100


def _date_str(d: date) -> str:
    return d.strftime("%Y%m%d")


def _series_from_df(df) -> dict[str, float]:
    result = {}
    for idx, row in df.iterrows():
        key = str(idx)[:10]
        try:
            result[key] = float(row["종가"])
        except (KeyError, ValueError, TypeError):
            pass
    return result


def _nearest_price(series: dict[str, float], target: date, max_gap: int = 7) -> Optional[float]:
    for delta in range(max_gap + 1):
        key = str(target - timedelta(days=delta))[:10]
        if key in series:
            return series[key]
    return None


def _fetch_kospi_series(start: date, end: date) -> dict[str, float]:
    cache_key = f"{_date_str(start)}_{_date_str(end)}"
    cached = _cache.get("momentum_kospi", cache_key, _cache.TTL_MARKET)
    if cached:
        return cached
    try:
        df = krx.get_index_ohlcv_by_date(_date_str(start), _date_str(end), _KOSPI_IDX)
        if df is None or df.empty:
            return {}
        result = _series_from_df(df)
        _cache.put("momentum_kospi", cache_key, result)
        return result
    except Exception as e:
        _note_fetch_error(e)
        return {}


def _fetch_ticker_series(ticker: str, start: date, end: date) -> dict[str, float]:
    cache_key = f"{ticker}_{_date_str(start)}_{_date_str(end)}"
    cached = _cache.get("momentum_ticker", cache_key, _cache.TTL_MARKET)
    if cached:
        return cached
    try:
        df = krx.get_market_ohlcv_by_date(_date_str(start), _date_str(end), ticker)
        if df is None or df.empty:
            return {}
        result = _series_from_df(df)
        _cache.put("momentum_ticker", cache_key, result)
        return result
    except Exception as e:
        _note_fetch_error(e)
        return {}


def _classify(rel_1m, rel_3m, rel_6m) -> tuple[MomentumSignal, int, int]:
    """
    Returns (signal, positive_count, negative_count).
    STRONG  : 1M/3M/6M 전부 존재 + 전부 임계값 초과
    POSITIVE: 유효 구간 중 2개 이상 양수
    NEGATIVE: 유효 구간 중 2개 이상 음수
    NEUTRAL : 그 외 혼조
    """
    rels      = (rel_1m, rel_3m, rel_6m)
    thresholds = (_STRONG_1M, _STRONG_3M, _STRONG_6M)
    valid     = [(r, t) for r, t in zip(rels, thresholds) if r is not None]

    if not valid:
        return MomentumSignal.UNAVAILABLE, 0, 0

    above = sum(1 for r, _ in valid if r > 0)
    below = sum(1 for r, _ in valid if r < 0)

    # STRONG — 3개 기간 전부 데이터 존재 + 전부 임계값 초과
    if len(valid) == 3 and all(r > t for r, t in valid):
        return MomentumSignal.STRONG, above, below

    if above >= 2:
        return MomentumSignal.POSITIVE, above, below
    if below >= 2:
        return MomentumSignal.NEGATIVE, above, below
    return MomentumSignal.NEUTRAL, above, below


_UNAVAILABLE = MomentumData(
    ret_1m=None, ret_3m=None, ret_6m=None,
    kospi_1m=None, kospi_3m=None, kospi_6m=None,
    signal=MomentumSignal.UNAVAILABLE,
)


def fetch_momentum(ticker: str, as_of_date: date,
                   value_score: Optional[int] = None) -> MomentumData:
    # 캐시 키는 ticker + date 만으로 — value_score는 분류에 안 씀
    cache_key = f"{ticker}_{as_of_date}"
    cached = _cache.get("momentum", cache_key, _cache.TTL_MARKET)
    if cached:
        if value_score is not None:
            cached.is_value_trap = (
                cached.signal == MomentumSignal.NEGATIVE
                and cached.negative_count >= 2
                and value_score >= _VALUE_TRAP_MIN_SCORE
            )
        return cached

    start   = as_of_date - timedelta(days=_LOOKBACK)
    price_s = _fetch_ticker_series(ticker, start, as_of_date)
    kospi_s = _fetch_kospi_series(start, as_of_date)

    if not price_s or not kospi_s:
        return _UNAVAILABLE

    today_p = _nearest_price(price_s, as_of_date)
    today_k = _nearest_price(kospi_s, as_of_date)
    if today_p is None or today_k is None:
        return _UNAVAILABLE

    p_1m = _nearest_price(price_s, as_of_date - timedelta(days=30))
    p_3m = _nearest_price(price_s, as_of_date - timedelta(days=91))
    p_6m = _nearest_price(price_s, as_of_date - timedelta(days=182))

    k_1m = _nearest_price(kospi_s, as_of_date - timedelta(days=30))
    k_3m = _nearest_price(kospi_s, as_of_date - timedelta(days=91))
    k_6m = _nearest_price(kospi_s, as_of_date - timedelta(days=182))

    ret_1m   = _pct(today_p, p_1m) if p_1m else None
    ret_3m   = _pct(today_p, p_3m) if p_3m else None
    ret_6m   = _pct(today_p, p_6m) if p_6m else None

    kospi_1m = _pct(today_k, k_1m) if k_1m else None
    kospi_3m = _pct(today_k, k_3m) if k_3m else None
    kospi_6m = _pct(today_k, k_6m) if k_6m else None

    rel_1m = (ret_1m - kospi_1m) if (ret_1m is not None and kospi_1m is not None) else None
    rel_3m = (ret_3m - kospi_3m) if (ret_3m is not None and kospi_3m is not None) else None
    rel_6m = (ret_6m - kospi_6m) if (ret_6m is not None and kospi_6m is not None) else None

    signal, pos_cnt, neg_cnt = _classify(rel_1m, rel_3m, rel_6m)

    is_vt = (signal == MomentumSignal.NEGATIVE
             and value_score is not None
             and value_score >= _VALUE_TRAP_MIN_SCORE
             and neg_cnt >= 2)

    result = MomentumData(
        ret_1m=ret_1m,     ret_3m=ret_3m,     ret_6m=ret_6m,
        kospi_1m=kospi_1m, kospi_3m=kospi_3m, kospi_6m=kospi_6m,
        signal=signal,
        is_value_trap=is_vt,
        positive_count=pos_cnt,
        negative_count=neg_cnt,
    )
    _cache.put("momentum", cache_key, result)
    return result


def mom_strength_display(signal: str | None, strength: float) -> str:
    """strength 값을 화살표 + 수치로 변환. screener / watchlist 공용."""
    if signal in (None, "unavailable"):
        return "—"
    if strength >= 100:   arrow = "▲▲"
    elif strength >= 50:  arrow = "▲"
    elif strength >= 0:   arrow = "·"
    elif strength > -50:  arrow = "▼"
    elif strength > -100: arrow = "▼▼"
    else:                 arrow = "▼▼▼"
    return f"{arrow} {strength:+.0f}pt"


_BATCH_TIMEOUT_SEC = 120   # 모멘텀 배치 전체 제한 (요청 timeout과 별개)


@dataclass
class MomentumBatchReport:
    """배치 미완료 분류. 결과 dict와 별도로 관측용."""
    requested:      int = 0
    returned:       int = 0
    timed_out:      list[str] = field(default_factory=list)       # 배치 제한 시점에 실행 중
    cancelled:      list[str] = field(default_factory=list)       # 시작 전 취소
    request_failed: dict[str, str] = field(default_factory=dict)  # 조회 오류 (내부 오류면 UNAVAILABLE 결과는 반환에 포함)

    @property
    def incomplete(self) -> bool:
        return bool(self.timed_out or self.cancelled or self.request_failed)


def batch_fetch_momentum_report(
    tickers_with_scores: list[tuple[str, Optional[int]]],
    as_of_date: date,
    max_workers: int = 6,
    timeout_sec: float = _BATCH_TIMEOUT_SEC,
) -> tuple[dict[str, MomentumData], MomentumBatchReport]:
    """
    병렬 모멘텀 fetch. timeout_sec 후 완료된 것만 반환하고 미완료를 분류한다.
    - 시작 전 작업은 취소(cancel_futures=True).
    - 이미 실행 중인 작업은 취소할 수 없다. 요청 timeout(analyzer.net) 안에서 끝나기를
      기대하지만 총시간을 보장하지 않으므로, 상위 실행의 제한 시간·감독이 별도로 필요하다.
    """
    results: dict[str, MomentumData] = {}
    report = MomentumBatchReport(requested=len(tickers_with_scores))
    if not tickers_with_scores:
        return results, report

    def _fetch(ticker: str, vscore: Optional[int]):
        _tls.errors = []
        try:
            return ticker, fetch_momentum(ticker, as_of_date, vscore), list(_tls.errors)
        finally:
            _tls.errors = None

    def _collect(f, ticker: str) -> None:
        try:
            _, mom, errs = f.result(timeout=1)
            results[ticker] = mom
            if errs:
                report.request_failed[ticker] = "; ".join(errs)
        except Exception as e:
            report.request_failed[ticker] = f"{type(e).__name__}: {e}"

    pool = ThreadPoolExecutor(max_workers=max_workers)
    try:
        futures = {pool.submit(_fetch, t, v): t for t, v in tickers_with_scores}
        done, not_done = concurrent.futures.wait(futures, timeout=timeout_sec)
        for f in done:
            _collect(f, futures[f])
        for f in not_done:
            ticker = futures[f]
            if f.cancel():
                report.cancelled.append(ticker)
            elif f.done():
                _collect(f, ticker)
            else:
                report.timed_out.append(ticker)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    report.returned = len(results)
    return results, report


def batch_fetch_momentum(
    tickers_with_scores: list[tuple[str, Optional[int]]],
    as_of_date: date,
    max_workers: int = 6,
) -> dict[str, MomentumData]:
    """기존 반환 형식 유지. 미완료가 있으면 WARNING으로 분류별 개수를 남긴다."""
    results, report = batch_fetch_momentum_report(tickers_with_scores, as_of_date, max_workers)
    if report.incomplete:
        log.warning(
            "모멘텀 배치 미완료 — 요청 %d / 반환 %d / 시간초과 %d %s / 취소 %d / 조회실패 %d %s",
            report.requested, report.returned,
            len(report.timed_out), report.timed_out[:10],
            len(report.cancelled),
            len(report.request_failed), list(report.request_failed)[:10],
        )
    return results
