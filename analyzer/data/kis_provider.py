"""
KIS (한국투자증권) Open API Market Data Provider
- pykrx를 대체하는 실전 REST API 기반 시장 데이터 소스
- krx.py가 MARKET_DATA_PROVIDER=kis 일 때 이 모듈에 위임

인증: 24시간 JWT 토큰 (자동 갱신)
Rate limit: 18 req/sec (실전투자 기준)

의존: requests, .env (KIS_APP_KEY, KIS_APP_SECRET, KIS_IS_PAPER)
"""

from __future__ import annotations

import os
import threading
import time
from datetime import date, timedelta
from typing import Optional

import requests
import pandas as pd

from constants import DataSource
from analyzer.data.models import (
    FetchResult, FetchErrorReason,
    SupplyFlow, ForeignOwnership, MarketCap, Fundamentals, KospiDisparity,
)
from analyzer.data import cache as _cache

# ── 엔드포인트 ────────────────────────────────────────────────────────────────

_BASE_REAL  = "https://openapi.koreainvestment.com:9443"
_BASE_PAPER = "https://openapivts.koreainvestment.com:29443"


# ── Rate Limiter ──────────────────────────────────────────────────────────────

class _RateLimiter:
    """Token bucket — 초당 최대 rate건."""
    def __init__(self, rate: float = 17.0):   # 18 한도에서 1 마진
        self._interval = 1.0 / rate
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            gap = self._interval - (now - self._last)
            if gap > 0:
                time.sleep(gap)
            self._last = time.monotonic()


# ── 핵심 구현체 ───────────────────────────────────────────────────────────────

class KISProvider:
    """
    KIS Open API 기반 시장 데이터 조회.
    krx.py에서 lazy-init 방식으로 단일 인스턴스 사용.
    """

    def __init__(self) -> None:
        is_paper = os.environ.get("KIS_IS_PAPER", "false").lower() == "true"
        self._base       = _BASE_PAPER if is_paper else _BASE_REAL
        self._app_key    = os.environ["KIS_APP_KEY"]
        self._app_secret = os.environ["KIS_APP_SECRET"]
        self._token:     Optional[str] = None
        self._token_exp: float         = 0.0
        self._token_lock = threading.Lock()
        self._rate       = _RateLimiter()
        # 종목명 in-memory 캐시 (scoring 중 inquire-price 부산물로 채워짐)
        self.name_cache: dict[str, str] = {}

    # ── 인증 ──────────────────────────────────────────────────────────────────

    def _get_token(self) -> str:
        with self._token_lock:
            if self._token and time.monotonic() < self._token_exp:
                return self._token
            r = requests.post(
                f"{self._base}/oauth2/tokenP",
                json={
                    "grant_type": "client_credentials",
                    "appkey":     self._app_key,
                    "appsecret":  self._app_secret,
                },
                timeout=10,
            )
            r.raise_for_status()
            d = r.json()
            self._token    = d["access_token"]
            # expires_in은 초 단위 (보통 86400). 5분 마진.
            self._token_exp = time.monotonic() + int(d.get("expires_in", 86400)) - 300
            return self._token

    def _headers(self, tr_id: str) -> dict:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._get_token()}",
            "appkey":        self._app_key,
            "appsecret":     self._app_secret,
            "tr_id":         tr_id,
        }

    # ── HTTP 래퍼 ──────────────────────────────────────────────────────────────

    def _get(self, path: str, tr_id: str, params: dict) -> dict:
        self._rate.wait()
        r = requests.get(
            f"{self._base}{path}",
            headers=self._headers(tr_id),
            params=params,
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    # ── 내부 헬퍼 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _ds(d: date) -> str:
        return d.strftime("%Y%m%d")

    @staticmethod
    def _parse_date(s: str) -> date:
        return date(int(s[:4]), int(s[4:6]), int(s[6:]))

    @staticmethod
    def _safe_int(v, default: int = 0) -> int:
        try:
            return int(str(v).replace(",", "") or 0)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _safe_float(v) -> Optional[float]:
        try:
            f = float(str(v).replace(",", "") or 0)
            return round(f, 4) if f != 0.0 else None
        except (ValueError, TypeError):
            return None

    # ── 현재가 + 기본 지표 (inquire-price) ───────────────────────────────────

    def inquire_price(self, ticker: str) -> Optional[dict]:
        """
        GET /uapi/domestic-stock/v1/quotations/inquire-price
        TR_ID: FHKST01010100

        반환 output 주요 필드:
          hts_kor_isnm  : 종목명 (한글)
          stck_prpr     : 현재가
          per           : PER
          pbr           : PBR
          hts_avls      : 시가총액 (억원 단위)
          acml_tr_pbmn  : 누적거래대금 (원 단위)
          lstg_stcn     : 상장주식수
          hts_frgn_ehrt : 외국인 소진율 (%) — 보유한도 대비. 지분율과 다름.

        캐시: 없음. 호출자가 disk cache로 관리.
        """
        try:
            d = self._get(
                "/uapi/domestic-stock/v1/quotations/inquire-price",
                "FHKST01010100",
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
            )
            if d.get("rt_cd") != "0":
                return None
            out = d.get("output") or {}
            # inquire-price 응답에 한국어 종목명 필드 없음 (POC 검증 완료).
            # name_cache는 채워지지 않음 → get_ticker_name()은 pykrx fallback 사용.
            return out
        except Exception:
            return None

    # ── 일봉 OHLCV ────────────────────────────────────────────────────────────

    def get_stock_ohlcv(
        self, ticker: str, from_date: date, to_date: date
    ) -> Optional[pd.DataFrame]:
        """
        GET /uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice
        TR_ID: FHKST03010100

        output2 필드: stck_bsop_date, stck_oprc, stck_hgpr, stck_lwpr, stck_clpr,
                      acml_vol, acml_tr_pbmn
        한 번에 최대 ~100행. 범위 초과 시 자동 페이징.
        """
        all_rows: list[dict] = []
        cur_end = to_date

        while True:
            try:
                d = self._get(
                    "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                    "FHKST03010100",
                    {
                        "FID_COND_MRKT_DIV_CODE": "J",
                        "FID_INPUT_ISCD":         ticker,
                        "FID_INPUT_DATE_1":       self._ds(from_date),
                        "FID_INPUT_DATE_2":       self._ds(cur_end),
                        "FID_PERIOD_DIV_CODE":    "D",
                        "FID_ORG_ADJ_PRC":        "1",
                    },
                )
            except Exception:
                break

            rows = d.get("output2") or []
            if not rows:
                break

            all_rows.extend(rows)

            # 마지막 행의 날짜가 from_date보다 이전이면 페이징 종료
            oldest_str = rows[-1].get("stck_bsop_date", "")
            if len(oldest_str) != 8:
                break
            oldest = self._parse_date(oldest_str)
            if oldest <= from_date or len(rows) < 30:
                break
            cur_end = oldest - timedelta(days=1)

        if not all_rows:
            return None

        records = []
        for r in all_rows:
            dt_str = r.get("stck_bsop_date", "")
            if len(dt_str) != 8:
                continue
            records.append({
                "날짜":   self._parse_date(dt_str),
                "시가":   self._safe_int(r.get("stck_oprc")),
                "고가":   self._safe_int(r.get("stck_hgpr")),
                "저가":   self._safe_int(r.get("stck_lwpr")),
                "종가":   self._safe_int(r.get("stck_clpr")),
                "거래량": self._safe_int(r.get("acml_vol")),
                "거래대금": self._safe_int(r.get("acml_tr_pbmn")),
            })

        if not records:
            return None

        df = (
            pd.DataFrame(records)
            .set_index("날짜")
            .sort_index()
        )
        # from_date ~ to_date 범위만 반환
        return df.loc[from_date:to_date]

    # ── KOSPI 지수 일봉 ───────────────────────────────────────────────────────

    def get_index_ohlcv(
        self, index_code: str, from_date: date, to_date: date
    ) -> Optional[pd.DataFrame]:
        """
        GET /uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice
        TR_ID: FHKUP03500100

        pykrx index_code "1001" (KOSPI) → KIS "0001"
        pykrx index_code "2001" (KOSDAQ) → KIS "1001"

        output2 필드: stck_bsop_date, bstp_nmix_prpr (지수 종가), acml_vol 등
        """
        _code_map = {"1001": "0001", "2001": "1001"}
        kis_code  = _code_map.get(index_code, index_code)

        try:
            d = self._get(
                "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice",
                "FHKUP03500100",
                {
                    "FID_COND_MRKT_DIV_CODE": "U",
                    "FID_INPUT_ISCD":         kis_code,
                    "FID_INPUT_DATE_1":       self._ds(from_date),
                    "FID_INPUT_DATE_2":       self._ds(to_date),
                    "FID_PERIOD_DIV_CODE":    "D",
                },
            )
        except Exception:
            return None

        if d.get("rt_cd") != "0":
            return None

        rows = d.get("output2") or []
        if not rows:
            return None

        records = []
        for r in rows:
            dt_str = r.get("stck_bsop_date") or r.get("bass_dt", "")
            if len(dt_str) != 8:
                continue
            # 지수 종가 필드 후보: bstp_nmix_prpr, stck_clpr
            close_raw = r.get("bstp_nmix_prpr") or r.get("stck_clpr") or "0"
            records.append({
                "날짜": self._parse_date(dt_str),
                "종가": float(str(close_raw).replace(",", "") or 0),
                "거래량": self._safe_int(r.get("acml_vol")),
            })

        if not records:
            return None

        df = pd.DataFrame(records).set_index("날짜").sort_index()
        return df.loc[from_date:to_date]

    # ── 거래일 목록 ───────────────────────────────────────────────────────────

    def get_trading_dates(self, as_of: date, n: int) -> list[date]:
        """n개 최근 거래일 (오름차순). KOSPI 지수 기준."""
        from_date = as_of - timedelta(days=n * 3)
        df = self.get_index_ohlcv("1001", from_date, as_of)
        if df is None or df.empty:
            return []
        return list(df.index)[-n:]

    # ── fetch_supply_flow ─────────────────────────────────────────────────────

    def fetch_supply_flow(
        self, ticker: str, as_of_date: date, lookback_days: int
    ) -> FetchResult[SupplyFlow]:
        """
        GET /uapi/domestic-stock/v1/quotations/inquire-investor
        TR_ID: FHKST01010900

        실제 확인된 output 필드 (단위: 백만원 → ×1,000,000 = 원):
          frgn_ntby_tr_pbmn : 외국인 순매수 거래대금
          orgn_ntby_tr_pbmn : 기관합계 순매수 거래대금 (연기금 포함)
          prsn_ntby_tr_pbmn : 개인 순매수 거래대금

        주의: 이 엔드포인트는 개인/외국인/기관합계 3개만 제공.
              연기금·투신·금융투자·사모·보험 세부 breakdown 없음.
              → pension_net 등은 항상 None (orgn_ntby_tr_pbmn에 포함됨).
              엔드포인트가 항상 30일치 rows를 반환 (날짜 range 파라미터 부분 무시됨).
        """
        ck = f"{ticker}:{as_of_date}:{lookback_days}"
        cached = _cache.get("supply_flow_kis", ck, _cache.TTL_MARKET)
        if cached is not None:
            return cached

        trading_dates = self.get_trading_dates(as_of_date, lookback_days)
        if len(trading_dates) < lookback_days:
            return FetchResult.failure(FetchErrorReason.INSUFFICIENT_DATA)

        try:
            d = self._get(
                "/uapi/domestic-stock/v1/quotations/inquire-investor",
                "FHKST01010900",
                {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD":          ticker,
                    "FID_INPUT_DATE_1":        self._ds(trading_dates[0]),
                    "FID_INPUT_DATE_2":        self._ds(trading_dates[-1]),
                },
            )
        except Exception:
            return FetchResult.failure(FetchErrorReason.API_TIMEOUT)

        if d.get("rt_cd") != "0":
            return FetchResult.failure(FetchErrorReason.TICKER_NOT_FOUND)

        rows = d.get("output") or []
        if not rows:
            return FetchResult.failure(FetchErrorReason.INSUFFICIENT_DATA)

        # 날짜 범위 필터링 (API가 30일치 반환하므로 요청 범위만 합산)
        from_ds = self._ds(trading_dates[0])
        to_ds   = self._ds(trading_dates[-1])
        rows = [r for r in rows if from_ds <= (r.get("stck_bsop_date") or "") <= to_ds]
        if not rows:
            return FetchResult.failure(FetchErrorReason.INSUFFICIENT_DATA)

        def _sum(field: str) -> int:
            total = 0
            for r in rows:
                v = r.get(field) or "0"
                try:
                    total += int(str(v).replace(",", "") or 0)
                except ValueError:
                    pass
            return total

        # 단위: 백만원 → 원 (* 1,000,000)
        _W = 1_000_000
        foreign_net     = _sum("frgn_ntby_tr_pbmn") * _W
        institution_net = _sum("orgn_ntby_tr_pbmn") * _W

        # 세부 breakdown 없음 — 모두 None (orgn에 포함됨)
        pension_net   = None
        inv_trust_net = None
        fin_inv_net   = None
        prv_fund_net  = None
        insure_net    = None

        result = FetchResult.success(SupplyFlow(
            foreign_net_buy=foreign_net,
            institution_net_buy=institution_net,
            lookback_days=lookback_days,
            source=DataSource.KIS,
            as_of_date=as_of_date,
            pension_net=pension_net,
            investment_trust_net=inv_trust_net,
            financial_investment_net=fin_inv_net,
            private_fund_net=prv_fund_net,
            insurance_net=insure_net,
        ))
        _cache.put("supply_flow_kis", ck, result)
        return result

    # ── fetch_foreign_ownership ───────────────────────────────────────────────

    def fetch_foreign_ownership(
        self, ticker: str, as_of_date: date, lookback_days: int
    ) -> FetchResult[ForeignOwnership]:
        """
        inquire-price의 hts_frgn_ehrt (외국인 소진율, %) 사용.

        주의: hts_frgn_ehrt = 외국인한도 대비 소진율 (한도소진률).
              pykrx의 지분율(보유주식/상장주식)과 다르다.
              통상 지분율 ≈ hts_frgn_ehrt × 외국인한도비율 (대부분 종목 100%).
              정밀도가 필요하면 get_exhaustion_rates API 별도 구현 필요.
        """
        ck = f"{ticker}:{as_of_date}:{lookback_days}"
        cached = _cache.get("foreign_ownership_kis", ck, _cache.TTL_MARKET)
        if cached is not None:
            return cached

        out = self.inquire_price(ticker)
        if not out:
            return FetchResult.failure(FetchErrorReason.TICKER_NOT_FOUND)

        raw = out.get("hts_frgn_ehrt") or out.get("frgn_hldn_qty_smtn") or "0"
        f = self._safe_float(raw)
        if f is None:
            return FetchResult.failure(FetchErrorReason.PARSE_ERROR)

        result = FetchResult.success(ForeignOwnership(
            ownership_pct=f,
            prev_pct=None,            # KIS 단건 조회 — 이전값 미제공
            change_lookback_pct=None,
            lookback_days=lookback_days,
            source=DataSource.KIS,
            as_of_date=as_of_date,
        ))
        _cache.put("foreign_ownership_kis", ck, result)
        return result

    # ── fetch_market_cap ──────────────────────────────────────────────────────

    def fetch_market_cap(
        self, ticker: str, as_of_date: date
    ) -> FetchResult[MarketCap]:
        """
        hts_avls (시가총액, 억원) × 1억 → 원.
        acml_tr_pbmn (누적거래대금, 원).
        """
        ck = f"{ticker}:{as_of_date}"
        cached = _cache.get("market_cap_kis", ck, _cache.TTL_MARKET)
        if cached is not None:
            return cached

        out = self.inquire_price(ticker)
        if not out:
            return FetchResult.failure(FetchErrorReason.TICKER_NOT_FOUND)

        cap_eok = self._safe_int(out.get("hts_avls"))
        if cap_eok == 0:
            return FetchResult.failure(FetchErrorReason.INVALID_ZERO_VALUE)

        cap           = cap_eok * 100_000_000          # 억원 → 원
        trading_value = self._safe_int(out.get("acml_tr_pbmn"))

        result = FetchResult.success(MarketCap(
            market_cap=cap,
            trading_value=trading_value,
            source=DataSource.KIS,
            as_of_date=as_of_date,
        ))
        _cache.put("market_cap_kis", ck, result)
        return result

    # ── fetch_fundamentals ────────────────────────────────────────────────────

    def fetch_fundamentals(
        self, ticker: str, as_of_date: date
    ) -> FetchResult[Fundamentals]:
        """
        inquire-price output:
          per       : PER
          pbr       : PBR
          dvdn_yield: 배당수익률 (%) — 필드명 불확실, 없으면 None

        ROE: KIS inquire-price에 직접 제공 필드 없음 → None
        """
        ck = f"{ticker}:{as_of_date}"
        cached = _cache.get("fundamentals_kis", ck, _cache.TTL_FUNDAMS)
        if cached is not None:
            return cached

        out = self.inquire_price(ticker)
        if not out:
            return FetchResult.failure(FetchErrorReason.TICKER_NOT_FOUND)

        per = self._safe_float(out.get("per"))
        pbr = self._safe_float(out.get("pbr"))

        # 배당수익률:
        # 1차: dvdn_yield 직접 필드 (POC에서 None/0 → 비어 있을 수 있음)
        # 2차: dps(주당배당금) / stck_prpr(현재가) × 100 으로 계산
        div: Optional[float] = None
        dvdn_raw = self._safe_float(out.get("dvdn_yield"))
        if dvdn_raw and dvdn_raw > 0:
            div = dvdn_raw
        else:
            dps   = self._safe_float(out.get("dps"))
            price = self._safe_float(out.get("stck_prpr"))
            if dps and dps > 0 and price and price > 0:
                div = round(dps / price * 100, 2)

        result = FetchResult.success(Fundamentals(
            per=per,
            pbr=pbr,
            div_yield=div,
            source=DataSource.KIS,
            as_of_date=as_of_date,
        ))
        _cache.put("fundamentals_kis", ck, result)
        return result

    # ── 가격 컨텍스트 (52주 범위 + 거래량 + VI) ──────────────────────────────

    def fetch_price_context(self, ticker: str, as_of_date: date) -> Optional[dict]:
        """
        52주 최고/최저, 현재 위치(%), 누적 거래량, VI 발동 여부.
        inquire-price 1회 호출로 모두 추출. Ticker 페이지 + Snapshot 저장용.

        반환 키:
          w52_high     : 52주 최고가 (원)
          w52_low      : 52주 최저가 (원)
          w52_position : 현재가의 52주 범위 내 위치 0~100% (저점=0, 고점=100)
          acml_vol     : 당일 누적 거래량
          vi_active    : VI 발동 여부 (bool)
          vi_code      : vi_cls_code 원본값
        """
        ck = f"{ticker}:{as_of_date}"
        cached = _cache.get("price_context_kis", ck, _cache.TTL_FUNDAMS)
        if cached is not None:
            return cached

        out = self.inquire_price(ticker)
        if not out:
            return None

        w52_high = self._safe_float(out.get("w52_hgpr"))
        w52_low  = self._safe_float(out.get("w52_lwpr"))
        price    = self._safe_float(out.get("stck_prpr"))
        acml_vol = self._safe_int(out.get("acml_vol"))
        vi_code  = out.get("vi_cls_code", "") or ""

        w52_pos: Optional[float] = None
        if w52_high and w52_low and price and w52_high > w52_low:
            w52_pos = round((price - w52_low) / (w52_high - w52_low) * 100, 1)

        # vi_cls_code 해석:
        # POC 결과: 삼성전자(정상) → 'N'. 'N' = Normal = VI 미발동.
        # 안전 원칙: 명확히 확인된 코드만 True. 미확인 코드는 False로 유지.
        # VI 발동 시 실제 코드값 미확인 ('Y'? '1'? 다른 값?) — see TODO.md
        _VI_INACTIVE = ("", "0", "N", None)
        result = {
            "w52_high":     w52_high,
            "w52_low":      w52_low,
            "w52_position": w52_pos,
            "acml_vol":     acml_vol or None,
            "vi_active":    vi_code not in _VI_INACTIVE,
            "vi_code":      vi_code or None,
        }
        _cache.put("price_context_kis", ck, result)
        return result

    # ── fetch_kospi_disparity ─────────────────────────────────────────────────

    def fetch_kospi_disparity(
        self, as_of_date: date, window: int
    ) -> FetchResult[KospiDisparity]:
        from_date = as_of_date - timedelta(days=window * 4)
        df = self.get_index_ohlcv("1001", from_date, as_of_date)
        if df is None or len(df) < window:
            return FetchResult.failure(FetchErrorReason.INSUFFICIENT_DATA)

        try:
            closes    = df["종가"]
            current   = round(float(closes.iloc[-1]),                    4)
            ma        = round(float(closes.tail(window).mean()),         4)
            disparity = round((closes.iloc[-1] / closes.tail(window).mean() - 1) * 100, 4)
        except (KeyError, IndexError, ZeroDivisionError):
            return FetchResult.failure(FetchErrorReason.PARSE_ERROR)

        return FetchResult.success(KospiDisparity(
            current=current,
            ma_n=ma,
            disparity_pct=disparity,
            window=window,
            source=DataSource.KIS,
            as_of_date=as_of_date,
        ))

    # ── fetch_risk_market_stats ───────────────────────────────────────────────

    def fetch_risk_market_stats(
        self, ticker: str, as_of_date: date, price_lookback: int = 252
    ) -> tuple[Optional[float], Optional[float]]:
        """(volatility_pct_annualized, dilution_pct_yoy)"""
        from_date = as_of_date - timedelta(days=price_lookback * 2)
        df        = self.get_stock_ohlcv(ticker, from_date, as_of_date)

        volatility: Optional[float] = None
        if df is not None and len(df) >= 20:
            try:
                returns    = df["종가"].pct_change().dropna()
                volatility = round(float(returns.std() * (252 ** 0.5) * 100), 2)
            except Exception:
                pass

        # 희석률: YoY 상장주식수 증가율
        # KIS inquire-price는 lstn_stcn (상장주식수, POC 검증) 제공.
        # 1년 전 값은 과거 조회 API가 없어 None 처리.
        # DART 사업보고서 기반 YoY 희석률 구현 대기 중 (see TODO.md)
        dilution: Optional[float] = None

        return volatility, dilution

    # ── get_ticker_name ───────────────────────────────────────────────────────

    def get_ticker_name(self, ticker: str) -> str:
        """
        종목명 조회. in-memory 캐시 우선.
        캐시 miss → inquire-price 호출 (rate limit 소비 주의).
        대량 이름 조회에는 쓰지 말 것.
        """
        if ticker in self.name_cache:
            return self.name_cache[ticker]
        self.inquire_price(ticker)   # side-effect로 name_cache 채움
        return self.name_cache.get(ticker, ticker)

    # ── get_sector ─────────────────────────────────────────────────────────────

    def get_sector(self, ticker: str, as_of_date: date) -> Optional[str]:
        """
        KIS inquire-price의 업종 분류.
        pykrx WICS와 1:1 매핑은 아님 — Calibration Report 섹터 분석은 pykrx 쪽 사용 권장.
        """
        out = self.inquire_price(ticker)
        if not out:
            return None
        # bstp_kor_isnm = 업종명 (예: "전기·전자") — POC 검증 완료
        return out.get("bstp_kor_isnm") or out.get("bsop_cls_name") or None

    # ── 스크리너 유니버스 ─────────────────────────────────────────────────────

    def get_screener_universe(
        self, as_of_date: date, market: str
    ) -> Optional[pd.DataFrame]:
        """
        시장 전종목 기본 데이터 (스크리너용).
        KIS 시총순위 조회로 구현. 반환 DataFrame 인덱스: ticker.
        컬럼: 시가총액(원), 거래대금(원), 종가(원).

        실패 시 None 반환 → 호출자가 pykrx fallback.

        TR_ID: FHPST01680000 (시총 순위)
        참고: fid_input_cnt_1=0~n (20개씩 페이징)
        주의: 엔드포인트/파라미터는 KIS API 실 응답 기준으로 검증 필요.
        """
        mkt_code = "J" if market.upper() == "KOSPI" else "Q"
        records: list[dict] = []

        for page_start in range(0, 2000, 20):
            try:
                d = self._get(
                    "/uapi/domestic-stock/v1/ranking/market-cap",
                    "FHPST01680000",
                    {
                        "fid_cond_mrkt_div_code": mkt_code,
                        "fid_cond_scr_div_code":  "200",
                        "fid_input_cnt_1":         str(page_start),
                        "fid_input_cnt_2":         "20",
                        "fid_input_iscd":          "0000",
                        "fid_div_cls_code":        "0",
                        "fid_blng_cls_code":       "0",
                        "fid_trgt_cls_code":       "0",
                        "fid_trgt_exls_cls_code":  "0",
                        "fid_input_price_1":       "",
                        "fid_input_price_2":       "",
                        "fid_vol_cnt":             "",
                        "fid_input_date_1":        "",
                    },
                )
            except Exception:
                break

            if d.get("rt_cd") != "0":
                break

            rows = d.get("output") or []
            if not rows:
                break

            for r in rows:
                tk = r.get("mksc_shrn_iscd") or r.get("stck_shrn_iscd", "")
                if not tk:
                    continue
                cap_eok = self._safe_int(r.get("hts_avls") or r.get("avrg_vol") or 0)
                records.append({
                    "ticker":     tk,
                    "시가총액":   cap_eok * 100_000_000,
                    "거래대금":   self._safe_int(r.get("acml_tr_pbmn")),
                    "종가":       self._safe_int(r.get("stck_prpr")),
                })

            if len(rows) < 20:
                break  # 마지막 페이지

        if not records:
            return None

        return pd.DataFrame(records).set_index("ticker")
