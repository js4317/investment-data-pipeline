from __future__ import annotations

from typing import Protocol

from analyzer.market_context.models import MarketContextItem


class MarketContextProvider(Protocol):
    """
    Market Context 데이터 소스 공통 인터페이스.

    구현체 예시:
        DartFilingProvider   → DART 공시  → list[MarketContextItem]
        UpcomingEventProvider → 실적일·배당기준일 → list[MarketContextItem]
        ClaudeContextProvider → AI 요약   → list[MarketContextItem]

    사용:
        provider: MarketContextProvider = DartFilingProvider()
        items = provider.fetch("005930", days=30)
    """

    def fetch(self, ticker: str, **kwargs) -> list[MarketContextItem]:
        ...
