from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Optional

from analyzer.portfolio.models import Position, PortfolioState

_DEFAULT_PATH      = Path(__file__).parent.parent.parent / "positions.json"
_BROKER_CACHE_PATH = Path.home() / ".investment_cache" / "holdings_cache.json"


def _save_broker_cache(state: PortfolioState) -> None:
    try:
        _BROKER_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "saved_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "total_value": state.total_value,
            "positions": {
                t: {"shares": p.shares, "avg_price": p.avg_price}
                for t, p in state.positions.items()
            },
        }
        _BROKER_CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        pass


def _load_broker_cache() -> Optional[PortfolioState]:
    if not _BROKER_CACHE_PATH.exists():
        return None
    try:
        data     = json.loads(_BROKER_CACHE_PATH.read_text())
        saved_at = data.get("saved_at", "알 수 없음")
        total    = float(data["total_value"])
        positions: dict[str, Position] = {}
        for ticker, info in data.get("positions", {}).items():
            shares    = int(info["shares"])
            avg_price = float(info["avg_price"])
            weight    = round(shares * avg_price / total * 100, 1) if total > 0 else 0.0
            positions[ticker] = Position(
                ticker=ticker, shares=shares, avg_price=avg_price, weight_pct=weight
            )
        return PortfolioState(total_value=total, positions=positions, stale_since=saved_at)
    except Exception:
        return None


def _load_from_broker(force_refresh: bool = False) -> Optional[PortfolioState]:
    """
    환경변수 BROKER 에 따라 BrokerProvider를 선택하고 holdings()를 PortfolioState로 변환.

    지원 브로커: "toss"
    실패 시 None 반환 — positions.json 로 자동 폴백하지 않음 (명시적 실패).
    force_refresh=True 시 캐시를 무시하고 API 직접 호출.
    """
    broker = os.environ.get("BROKER", "").lower()
    if broker != "toss":
        return None

    try:
        from analyzer.broker.toss import TossBrokerProvider
        from analyzer.broker import cache as _broker_cache
        provider = TossBrokerProvider()
        result   = _broker_cache.get_holdings(provider, force=force_refresh)
        if not result.ok or result.data is None:
            return None

        holdings    = result.data
        from analyzer.portfolio.cash import load_cash_krw
        cash        = load_cash_krw()
        total_value = sum(h.market_value for h in holdings) + cash
        if total_value <= 0:
            return PortfolioState(total_value=0.0, positions={})

        positions: dict[str, Position] = {}
        for h in holdings:
            weight = round(h.market_value / total_value * 100, 1)
            positions[h.ticker] = Position(
                ticker=h.ticker,
                shares=h.shares,
                avg_price=h.avg_price,
                weight_pct=weight,
            )
        state = PortfolioState(total_value=round(total_value), positions=positions)
        _save_broker_cache(state)
        return state
    except Exception:
        import logging as _logging
        _logging.getLogger(__name__).exception("Broker load failed")
        return None


def load(path: Path = _DEFAULT_PATH, force_refresh: bool = False) -> Optional[PortfolioState]:
    """
    PortfolioState 로드.

    우선순위:
    1. BROKER 환경변수가 설정된 경우 → BrokerProvider (실시간 보유종목)
    2. BROKER 미설정 → positions.json (수동 입력)

    BROKER가 설정되었으나 API 호출 실패 시 None 반환.
    positions.json 로 자동 폴백하지 않는다 — 명시적 실패.

    positions.json 형식:
    {
      "total_value": 10000000,
      "positions": {
        "000270": {"shares": 20, "avg_price": 154350},
        ...
      }
    }
    """
    if os.environ.get("BROKER"):
        state = _load_from_broker(force_refresh=force_refresh)
        if state is not None:
            return state
        return _load_broker_cache()  # 연결 실패 시 마지막 저장 데이터 사용

    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        total = float(data["total_value"])
        if total <= 0:
            return None

        positions: dict[str, Position] = {}
        for ticker, info in data.get("positions", {}).items():
            shares    = int(info["shares"])
            avg_price = float(info["avg_price"])
            weight    = round(shares * avg_price / total * 100, 1)
            positions[ticker] = Position(
                ticker=ticker,
                shares=shares,
                avg_price=avg_price,
                weight_pct=weight,
            )
        return PortfolioState(total_value=total, positions=positions)
    except Exception:
        return None
