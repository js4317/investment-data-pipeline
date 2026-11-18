from __future__ import annotations

import datetime
import time
from dataclasses import dataclass
from typing import Optional

from analyzer.broker.models import Holding
from analyzer.data.models import FetchResult, FetchErrorReason

HOLDINGS_TTL = 300  # 5분

_ERROR_LABELS = {
    FetchErrorReason.API_TIMEOUT: "인증 오류 또는 네트워크 타임아웃",
    FetchErrorReason.PARSE_ERROR: "응답 파싱 오류",
}


@dataclass
class CacheEntry:
    data:       list[Holding]
    fetched_at: float
    ttl:        int = HOLDINGS_TTL

    def is_valid(self) -> bool:
        return time.time() - self.fetched_at < self.ttl

    @property
    def age_seconds(self) -> float:
        return time.time() - self.fetched_at

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.ttl - self.age_seconds)

    @property
    def fetched_at_display(self) -> str:
        return datetime.datetime.fromtimestamp(self.fetched_at).strftime("%H:%M:%S")


@dataclass
class BrokerHealth:
    last_success:         Optional[float] = None
    last_failure:         Optional[float] = None
    consecutive_failures: int             = 0
    last_error:           Optional[str]   = None

    @property
    def status(self) -> str:
        if self.last_success is None and self.consecutive_failures == 0:
            return "unknown"
        if self.consecutive_failures >= 3:
            return "error"
        if self.consecutive_failures > 0:
            return "warning"
        return "connected"

    @property
    def status_emoji(self) -> str:
        return {"connected": "🟢", "warning": "🟡", "error": "🔴", "unknown": "⚪"}.get(self.status, "⚪")

    @property
    def status_label(self) -> str:
        if self.status == "connected":
            return "Connected"
        if self.status == "warning":
            return f"일시 오류 ({self.consecutive_failures}회) — {self.last_error or ''}"
        if self.status == "error":
            return f"연결 실패 — {self.last_error or ''}"
        return "미확인"


_cache:  Optional[CacheEntry] = None
_health: BrokerHealth         = BrokerHealth()


def get_holdings(provider, *, force: bool = False) -> FetchResult[list[Holding]]:
    global _cache, _health
    if not force and _cache is not None and _cache.is_valid():
        return FetchResult.success(_cache.data)
    result = provider.holdings()
    if result.ok and result.data is not None:
        _cache                       = CacheEntry(data=result.data, fetched_at=time.time())
        _health.last_success         = time.time()
        _health.consecutive_failures = 0
        _health.last_error           = None
    else:
        _health.last_failure          = time.time()
        _health.consecutive_failures += 1
        _health.last_error            = _ERROR_LABELS.get(result.error, str(result.error))
    return result


def cache_info() -> Optional[CacheEntry]:
    return _cache


def health_info() -> BrokerHealth:
    return _health


def invalidate() -> None:
    global _cache
    _cache = None
