from __future__ import annotations

from typing import Protocol

from analyzer.broker.models import Holding, Trade
from analyzer.data.models import FetchResult


class BrokerProvider(Protocol):
    """
    Broker 데이터 소스 공통 인터페이스.

    구현체 예시:
        TossBrokerProvider   → 토스증권 Open API
        KisBrokerProvider    → 한국투자증권 Open API
        KiwoomBrokerProvider → 키움증권 API

    원칙:
        - 분석 엔진(Score)은 이 인터페이스에 의존하지 않는다.
        - Broker API가 없어도 콘솔 전체가 동작해야 한다.
        - supports_*() 로 기능 여부를 먼저 확인하고 호출한다.
        - 반환 타입은 FetchResult — 에러 처리 일관성 유지.
    """

    def supports_holdings(self) -> bool:
        """보유종목 조회 지원 여부."""
        ...

    def supports_trades(self) -> bool:
        """체결내역 조회 지원 여부."""
        ...

    def supports_orders(self) -> bool:
        """주문 실행 지원 여부 (Write)."""
        ...

    def holdings(self) -> FetchResult[list[Holding]]:
        """현재 보유 종목 전체 조회. supports_holdings() == True 일 때만 호출."""
        ...

    def trades(self) -> FetchResult[list[Trade]]:
        """
        체결 완료된 거래 내역 전체 조회.
        supports_trades() == False 이면 FetchResult.success([]) 반환.
        """
        ...
