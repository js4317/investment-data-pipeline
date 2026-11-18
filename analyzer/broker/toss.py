from __future__ import annotations

import os
import time
from typing import Optional

import requests

from analyzer.broker.models import Holding, Trade
from analyzer.data.models import FetchResult, FetchErrorReason

_BASE               = "https://openapi.tossinvest.com"
_TOKEN_URL          = f"{_BASE}/oauth2/token"
_HOLDINGS_URL       = f"{_BASE}/api/v1/holdings"
_ACCOUNTS_URL       = f"{_BASE}/api/v1/accounts"
_TOKEN_REFRESH_MARGIN_SEC = 300  # 만료 5분 전 갱신


class TossBrokerProvider:
    """
    토스증권 Open API BrokerProvider 구현체.

    필수 환경변수:
        TOSS_CLIENT_ID      — WTS 설정 > Open API에서 발급
        TOSS_CLIENT_SECRET  — WTS 설정 > Open API에서 발급
        TOSS_ACCOUNT_SEQ    — GET /api/v1/accounts 의 accountSeq (미설정 시 자동 조회)

    trades() 현황:
        Toss Open API v1은 GET /api/v1/orders?status=CLOSED 를 지원하지 않는다.
        호출 시 400 closed-not-supported 반환.
        따라서 trades()는 빈 리스트를 반환하는 stub 상태다.
        Toss가 CLOSED 주문 조회를 공식 지원하면 구현한다.
        → Trade Journal / Orders Sync 는 해당 시점까지 보류.
    """

    def __init__(self) -> None:
        self._client_id     = os.environ["TOSS_CLIENT_ID"]
        self._client_secret = os.environ["TOSS_CLIENT_SECRET"]
        self._account_seq:      Optional[str]   = os.environ.get("TOSS_ACCOUNT_SEQ")
        self._token:            Optional[str]   = None
        self._token_expires_at: float           = 0.0

    def _refresh_token(self) -> None:
        resp = requests.post(
            _TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(self._client_id, self._client_secret),
            timeout=10,
        )
        resp.raise_for_status()
        body                   = resp.json()
        self._token            = body["access_token"]
        self._token_expires_at = time.monotonic() + body["expires_in"] - _TOKEN_REFRESH_MARGIN_SEC

    def _auth_headers(self, *, with_account: bool = False) -> dict[str, str]:
        if not self._token or time.monotonic() >= self._token_expires_at:
            self._refresh_token()
        headers: dict[str, str] = {"Authorization": f"Bearer {self._token}"}
        if with_account:
            headers["X-Tossinvest-Account"] = self._resolve_account_seq()
        return headers

    def _resolve_account_seq(self) -> str:
        if self._account_seq:
            return self._account_seq
        resp = requests.get(
            _ACCOUNTS_URL,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=10,
        )
        resp.raise_for_status()
        accounts = resp.json().get("result", [])
        if not accounts:
            raise RuntimeError("TossBrokerProvider: 계좌가 없습니다")
        self._account_seq = str(accounts[0]["accountSeq"])
        return self._account_seq

    def supports_holdings(self) -> bool:
        return True

    def supports_trades(self) -> bool:
        return False

    def supports_orders(self) -> bool:
        return False

    def holdings(self) -> FetchResult[list[Holding]]:
        try:
            resp = requests.get(
                _HOLDINGS_URL,
                headers=self._auth_headers(with_account=True),
                timeout=10,
            )
            resp.raise_for_status()
            raw_items = resp.json().get("result", {}).get("items", [])
            return FetchResult.success([_parse_holding(item) for item in raw_items])
        except requests.HTTPError:
            return FetchResult.failure(FetchErrorReason.API_TIMEOUT)
        except Exception:
            return FetchResult.failure(FetchErrorReason.PARSE_ERROR)

    def trades(self) -> FetchResult[list[Trade]]:
        return FetchResult.success([])


def _parse_holding(item: dict) -> Holding:
    pl = item.get("profitLoss") or {}
    mv = item.get("marketValue") or {}
    return Holding(
        ticker             = item["symbol"],
        name               = item["name"],
        shares             = int(float(item["quantity"])),
        avg_price          = float(item["averagePurchasePrice"]),
        current_price      = float(item["lastPrice"]),
        unrealized_pnl     = float(pl.get("amount", 0)),
        unrealized_pnl_pct = float(pl.get("rate", 0)),
        market_value       = float(mv.get("amount", 0)),
        currency           = str(item.get("currency", "KRW")),
    )
