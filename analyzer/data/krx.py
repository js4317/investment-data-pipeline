# 데이터 없음/수집 실패는 절대 0으로 대체하지 않는다.
# None  → 값이 없음 (unknown). 호출자가 FetchResult.error로 원인 확인.
# 0     → 실제 0 (known zero). 예: 당일 외국인 순매수 0주.
# 이 원칙을 어기면 confidence / AnalysisStatus 구분이 무너진다.

import os
import time
from datetime import date, timedelta
from typing import Optional

import pandas as pd
from pykrx import stock as krx
from requests.exceptions import RequestException, ConnectionError, Timeout
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# pykrx compat shim — IndexTicker '지수명' 컬럼 누락 버그 우회.
# get_index_ohlcv_by_date 내부에서 get_index_ticker_name() 호출 시 KeyError 발생하는 이슈.
# 데이터 수집은 정상이므로 이름 조회 실패 시 ticker 코드로 fallback.
try:
    from pykrx.stock import stock_api as _pykrx_api
    _orig_index_name = _pykrx_api.get_index_ticker_name
    def _safe_index_name(ticker: str) -> str:
        try:
            return _orig_index_name(ticker)
        except Exception:
            return ticker
    _pykrx_api.get_index_ticker_name = _safe_index_name
except Exception:
    pass

from constants import DataSource
from analyzer.data.models import (
    FetchResult, FetchErrorReason,
    SupplyFlow, ForeignOwnership, MarketCap, Fundamentals, KospiDisparity,
)
from analyzer.data import cache as _cache

_RETRY_EXCEPTIONS = (RequestException, ConnectionError, Timeout, OSError)
_CACHE_MAX_SIZE = 30

# ── KIS Provider (optional) ───────────────────────────────────────────────────
# MARKET_DATA_PROVIDER=kis 일 때 per-ticker 함수들을 KIS API에 위임.
# pykrx는 bulk 스크리너 / 이름맵 / 섹터 조회용으로 유지.

_kis_prov = None
_kis_init_lock = __import__("threading").Lock()


def _get_kis():
    """KISProvider singleton. KIS 미설정이면 None 반환."""
    global _kis_prov
    if os.environ.get("MARKET_DATA_PROVIDER") != "kis":
        return None
    if _kis_prov is not None:
        return _kis_prov
    with _kis_init_lock:
        if _kis_prov is None:
            try:
                from analyzer.data.kis_provider import KISProvider
                _kis_prov = KISProvider()
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"KISProvider 초기화 실패: {e}")
                return None
    return _kis_prov


# ---------------------------------------------------------------------------
# 캐시 — 날짜 key 기반, max size 제한
# ---------------------------------------------------------------------------

_trading_dates_cache: dict[tuple[date, int], list[date]] = {}
_market_cap_cache:    dict[str, pd.DataFrame] = {}
_fundamental_cache:   dict[str, pd.DataFrame] = {}
_kospi_disp_cache:    dict[tuple[date, int], KospiDisparity] = {}
_name_cache:          dict[str, str] = {}
_sector_cache:        dict[str, str] = {}


def _trim(cache: dict) -> None:
    if len(cache) > _CACHE_MAX_SIZE:
        oldest_keys = list(cache)[:len(cache) - _CACHE_MAX_SIZE]
        for k in oldest_keys:
            del cache[k]


# ---------------------------------------------------------------------------
# 내부 유틸
# ---------------------------------------------------------------------------

def _date_str(d: date) -> str:
    return d.strftime("%Y%m%d")


def _round(v) -> Optional[float]:
    # round(float, 4) 정책 통일. Decimal 전환 시 이 함수만 수정.
    return round(float(v), 4) if v is not None else None


@retry(
    retry=retry_if_exception_type(_RETRY_EXCEPTIONS),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _call_krx(fn, *args, **kwargs):
    time.sleep(0.3)
    return fn(*args, **kwargs)


def _get_trading_dates(as_of_date: date, n: int) -> list[date]:
    """
    실제 거래일 n일을 역순으로 반환. 캘린더 기준 아님.
    결과는 캐시됨 — watchlist 20종목 분석 시 반복 호출 방지.
    """
    key = (as_of_date, n)
    if key in _trading_dates_cache:
        return _trading_dates_cache[key]

    buffer_days = n * 3
    from_str = _date_str(as_of_date - timedelta(days=buffer_days))
    to_str = _date_str(as_of_date)

    try:
        df = _call_krx(krx.get_index_ohlcv_by_date, from_str, to_str, "1001")
    except _RETRY_EXCEPTIONS:
        return []

    if df is None or df.empty:
        return []

    result = [d.date() for d in df.index][-n:]
    _trading_dates_cache[key] = result
    _trim(_trading_dates_cache)
    return result


def _cached_market_cap(date_str: str) -> Optional[pd.DataFrame]:
    if date_str not in _market_cap_cache:
        try:
            df = _call_krx(krx.get_market_cap, date_str)
            _market_cap_cache[date_str] = df
            _trim(_market_cap_cache)
        except _RETRY_EXCEPTIONS:
            return None
    return _market_cap_cache.get(date_str)


def _cached_fundamental(date_str: str) -> Optional[pd.DataFrame]:
    if date_str not in _fundamental_cache:
        try:
            # pykrx 기본 시장은 KOSPI — KOSDAQ 종목 누락 방지를 위해 전체 시장을 명시 (v8.6.8)
            df = _call_krx(krx.get_market_fundamental, date_str, market="ALL")
            _fundamental_cache[date_str] = df
            _trim(_fundamental_cache)
        except _RETRY_EXCEPTIONS:
            return None
    return _fundamental_cache.get(date_str)


# ---------------------------------------------------------------------------
# 공개 API — 모든 함수는 as_of_date를 명시적으로 받는다
# ---------------------------------------------------------------------------

def get_ticker_name(ticker: str) -> str:
    """종목코드 → 종목명. 실패 시 ticker 코드 그대로 반환."""
    # KIS 모드: KISProvider.name_cache 우선 (scoring 중 side-effect로 채워짐)
    prov = _get_kis()
    if prov is not None and ticker in prov.name_cache:
        return prov.name_cache[ticker]
    if ticker not in _name_cache:
        try:
            name = krx.get_market_ticker_name(ticker)
            _name_cache[ticker] = name if name else ticker
        except Exception:
            _name_cache[ticker] = ticker
    return _name_cache[ticker]


def display_name(ticker: str) -> str:
    """종목코드 → 표시명. 조회 실패 시 코드 그대로 반환. 페이지 공통 헬퍼."""
    return get_ticker_name(ticker) or ticker


def rel_time(date_str: str) -> str:
    """ISO 날짜 문자열 → 상대 시간 표시 (오늘/어제/N일 전). 페이지 공통 헬퍼."""
    try:
        delta = date.today() - date.fromisoformat(date_str)
        if delta.days == 0: return "오늘"
        if delta.days == 1: return "어제"
        if delta.days <= 6: return f"{delta.days}일 전"
        return date_str
    except Exception:
        return date_str


def build_full_name_map(as_of_date: date) -> dict[str, str]:
    """
    KOSPI+KOSDAQ 전체 종목명→코드 매핑.
    반환: {"삼성전자": "005930", "기아": "000270", ...}

    disk cache 24h → 재시작 시 빠름.
    cache miss 시 ThreadPoolExecutor로 병렬 조회 (약 30~60초).
    이전 분석 여부·snapshot·watchlist 등록 여부 무관.
    """
    from concurrent.futures import ThreadPoolExecutor

    cached = _cache.get("full_name_map", "v1", _cache.TTL_MARKET)
    if cached:
        # in-memory 캐시도 갱신
        for name, ticker in cached.items():
            _name_cache.setdefault(ticker, name)
        return cached

    date_str    = _date_str(as_of_date)
    all_tickers: list[str] = []
    for market in ("KOSPI", "KOSDAQ"):
        try:
            tickers = _call_krx(krx.get_market_ticker_list, date_str, market=market)
            all_tickers.extend(tickers)
        except Exception:
            pass

    result: dict[str, str] = {}  # name → ticker

    def _fetch_name(ticker: str) -> tuple[str, str]:
        try:
            name = krx.get_market_ticker_name(ticker)  # 직접 호출 (sleep 없이 병렬)
            return ticker, name if name else ""
        except Exception:
            return ticker, ""

    with ThreadPoolExecutor(max_workers=10) as pool:
        for ticker, name in pool.map(_fetch_name, all_tickers):
            if name:
                result[name]         = ticker
                _name_cache[ticker]  = name

    if result:
        _cache.put("full_name_map", "v1", result)
    return result


def find_tickers_by_name(query: str, extra: dict[str, str] | None = None) -> list[str]:
    """
    종목명 → 종목코드 목록 (완전 일치만).
    검색 범위: _name_cache + extra (ticker→name 추가 매핑).
    반환: 일치하는 ticker 코드 리스트 (0개, 1개, 또는 복수).
    """
    query = query.strip()
    if not query:
        return []

    combined = dict(_name_cache)
    if extra:
        for t, n in extra.items():
            if t not in combined:
                combined[t] = n

    return [t for t, n in combined.items() if n == query]


def _pykrx_investor_breakdown(
    ticker: str, as_of_date: date, lookback_days: int
) -> Optional[dict]:
    """
    pykrx로 연기금/투신/금융투자/사모/보험 순매수 합산.
    KIS 모드에서 세부 breakdown 보완용. 실패 시 None 반환.
    """
    trading_dates = _get_trading_dates(as_of_date, lookback_days)
    if len(trading_dates) < lookback_days:
        return None
    from_str = _date_str(trading_dates[0])
    to_str   = _date_str(trading_dates[-1])
    try:
        df = _call_krx(krx.get_market_trading_value_by_investor, from_str, to_str, ticker)
    except _RETRY_EXCEPTIONS:
        return None
    if df is None or df.empty:
        return None

    def _safe_int(v) -> Optional[int]:
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    try:
        if hasattr(df.columns, "levels"):
            df = df.xs("순매수", axis=1, level=1)
            pk = next((l for l in ["연기금 등", "연기금등", "연기금"] if l in df.columns), None)
            return {
                "pension_net":              _safe_int(df[pk].sum())         if pk             else None,
                "investment_trust_net":     _safe_int(df["투신"].sum())     if "투신"         in df.columns else None,
                "financial_investment_net": _safe_int(df["금융투자"].sum()) if "금융투자"     in df.columns else None,
                "private_fund_net":         _safe_int(df["사모"].sum())     if "사모"         in df.columns else None,
                "insurance_net":            _safe_int(df["보험"].sum())     if "보험"         in df.columns else None,
            }
        else:
            pk = next((l for l in ["연기금 등", "연기금등", "연기금", "기금"] if l in df.index), None)
            return {
                "pension_net":              _safe_int(df.loc[pk, "순매수"])         if pk           else None,
                "investment_trust_net":     _safe_int(df.loc["투신",    "순매수"]) if "투신"       in df.index else None,
                "financial_investment_net": _safe_int(df.loc["금융투자","순매수"]) if "금융투자"   in df.index else None,
                "private_fund_net":         _safe_int(df.loc["사모",    "순매수"]) if "사모"       in df.index else None,
                "insurance_net":            _safe_int(df.loc["보험",    "순매수"]) if "보험"       in df.index else None,
            }
    except (KeyError, TypeError):
        return None


def fetch_supply_flow(
    ticker: str,
    as_of_date: date,
    lookback_days: int = 7,
) -> FetchResult[SupplyFlow]:
    prov = _get_kis()
    if prov is not None:
        result = prov.fetch_supply_flow(ticker, as_of_date, lookback_days)
        # KIS는 세부 breakdown 미제공 → pykrx로 연기금 등 보완
        if result.ok and result.data.pension_net is None:
            bd = _pykrx_investor_breakdown(ticker, as_of_date, lookback_days)
            if bd:
                sf = result.data
                result = FetchResult.success(SupplyFlow(
                    foreign_net_buy=sf.foreign_net_buy,
                    institution_net_buy=sf.institution_net_buy,
                    lookback_days=sf.lookback_days,
                    source=sf.source,
                    as_of_date=sf.as_of_date,
                    pension_net=bd.get("pension_net"),
                    investment_trust_net=bd.get("investment_trust_net"),
                    financial_investment_net=bd.get("financial_investment_net"),
                    private_fund_net=bd.get("private_fund_net"),
                    insurance_net=bd.get("insurance_net"),
                ))
        return result

    _cache_key = f"{ticker}:{as_of_date}:{lookback_days}"
    _cached = _cache.get("supply_flow", _cache_key, _cache.TTL_MARKET)
    if _cached is not None:
        return _cached

    trading_dates = _get_trading_dates(as_of_date, lookback_days)
    if len(trading_dates) < lookback_days:
        return FetchResult.failure(FetchErrorReason.INSUFFICIENT_DATA)

    from_str = _date_str(trading_dates[0])
    to_str = _date_str(trading_dates[-1])

    try:
        df = _call_krx(krx.get_market_trading_value_by_investor, from_str, to_str, ticker)
    except _RETRY_EXCEPTIONS:
        return FetchResult.failure(FetchErrorReason.API_TIMEOUT)

    if df is None or df.empty:
        return FetchResult.failure(FetchErrorReason.TICKER_NOT_FOUND)

    def _safe_int(series_or_scalar) -> Optional[int]:
        try:
            return int(series_or_scalar)
        except (TypeError, ValueError):
            return None

    try:
        def _col(labels: list[str], columns) -> Optional[str]:
            for label in labels:
                if label in columns:
                    return label
            return None

        if hasattr(df.columns, "levels"):
            # 구 형식: date index × MultiIndex(투자자, 매도/매수/순매수) columns
            df = df.xs("순매수", axis=1, level=1)
            foreign_net    = int(df["외국인합계"].sum())
            institution_net = int(df["기관합계"].sum())
            pk = _col(["연기금 등", "연기금등", "연기금"],df.columns)
            pension_net              = _safe_int(df[pk].sum())           if pk           else None
            investment_trust_net     = _safe_int(df["투신"].sum())       if "투신"       in df.columns else None
            financial_investment_net = _safe_int(df["금융투자"].sum())   if "금융투자"   in df.columns else None
            private_fund_net         = _safe_int(df["사모"].sum())       if "사모"       in df.columns else None
            insurance_net            = _safe_int(df["보험"].sum())       if "보험"       in df.columns else None
        else:
            # 신 형식 (pykrx ≥ 1.2.x): 투자자구분 index × [매도, 매수, 순매수] columns
            # 라벨 변경: 외국인합계 → 외국인
            foreign_key = "외국인합계" if "외국인합계" in df.index else "외국인"
            foreign_net    = int(df.loc[foreign_key, "순매수"])
            institution_net = int(df.loc["기관합계", "순매수"])
            pk = _col(["연기금 등", "연기금등", "연기금", "기금"],df.index)
            if pk is None:
                try:
                    import pathlib as _pl
                    _pl.Path.home().joinpath(".investment_cache").mkdir(exist_ok=True)
                    _pl.Path.home().joinpath(".investment_cache", "pension_debug.txt").write_text(
                        f"ticker={ticker} index_labels={list(df.index)}\n"
                    )
                except Exception:
                    pass
            pension_net              = _safe_int(df.loc[pk, "순매수"])           if pk           else None
            investment_trust_net     = _safe_int(df.loc["투신",    "순매수"]) if "투신"       in df.index else None
            financial_investment_net = _safe_int(df.loc["금융투자","순매수"]) if "금융투자"   in df.index else None
            private_fund_net         = _safe_int(df.loc["사모",    "순매수"]) if "사모"       in df.index else None
            insurance_net            = _safe_int(df.loc["보험",    "순매수"]) if "보험"       in df.index else None
    except (KeyError, TypeError):
        return FetchResult.failure(FetchErrorReason.PARSE_ERROR)

    result = FetchResult.success(SupplyFlow(
        foreign_net_buy=foreign_net,
        institution_net_buy=institution_net,
        lookback_days=lookback_days,
        source=DataSource.PYKRX,
        as_of_date=as_of_date,
        pension_net=pension_net,
        investment_trust_net=investment_trust_net,
        financial_investment_net=financial_investment_net,
        private_fund_net=private_fund_net,
        insurance_net=insurance_net,
    ))
    _cache.put("supply_flow", _cache_key, result)
    return result


def fetch_foreign_ownership(
    ticker: str,
    as_of_date: date,
    lookback_days: int = 7,
) -> FetchResult[ForeignOwnership]:
    prov = _get_kis()
    if prov is not None:
        return prov.fetch_foreign_ownership(ticker, as_of_date, lookback_days)

    _cache_key = f"{ticker}:{as_of_date}:{lookback_days}"
    _cached = _cache.get("foreign_ownership", _cache_key, _cache.TTL_MARKET)
    if _cached is not None:
        return _cached

    trading_dates = _get_trading_dates(as_of_date, lookback_days)
    if len(trading_dates) < lookback_days:
        return FetchResult.failure(FetchErrorReason.INSUFFICIENT_DATA)

    from_str = _date_str(trading_dates[0])
    to_str = _date_str(trading_dates[-1])

    try:
        df = _call_krx(krx.get_exhaustion_rates_of_foreign_investment, from_str, to_str, ticker)
    except _RETRY_EXCEPTIONS:
        return FetchResult.failure(FetchErrorReason.API_TIMEOUT)

    if df is None or df.empty:
        return FetchResult.failure(FetchErrorReason.TICKER_NOT_FOUND)

    # pykrx 컬럼: 상장주식수, 보유주식수, 지분율, 한도소진률
    try:
        ratios = df["지분율"]
        latest = _round(ratios.iloc[-1])
        prev = _round(ratios.iloc[-2]) if len(ratios) >= 2 else None
        change = _round(ratios.iloc[-1] - ratios.iloc[0]) if len(ratios) >= 2 else None
    except (KeyError, IndexError, TypeError):
        return FetchResult.failure(FetchErrorReason.PARSE_ERROR)

    result = FetchResult.success(ForeignOwnership(
        ownership_pct=latest,
        prev_pct=prev,
        change_lookback_pct=change,
        lookback_days=lookback_days,
        source=DataSource.PYKRX,
        as_of_date=as_of_date,
    ))
    _cache.put("foreign_ownership", _cache_key, result)
    return result


def _extract_market_cap_row(df: pd.DataFrame, ticker: str) -> Optional[tuple[int, int]]:
    if ticker not in df.index:
        return None
    try:
        row = df.loc[ticker]
        cap = int(row["시가총액"])
        vol = int(row["거래대금"])
        return (cap, vol) if cap > 0 else None
    except (KeyError, TypeError):
        return None


def fetch_market_cap(
    ticker: str,
    as_of_date: date,
) -> FetchResult[MarketCap]:
    prov = _get_kis()
    if prov is not None:
        return prov.fetch_market_cap(ticker, as_of_date)

    _cache_key = f"{ticker}:{as_of_date}"
    _cached = _cache.get("market_cap", _cache_key, _cache.TTL_MARKET)
    if _cached is not None:
        return _cached

    df = _cached_market_cap(_date_str(as_of_date))
    if df is None:
        return FetchResult.failure(FetchErrorReason.API_TIMEOUT)
    if ticker not in df.index:
        return FetchResult.failure(FetchErrorReason.TICKER_NOT_FOUND)

    extracted = _extract_market_cap_row(df, ticker)

    if extracted is None:
        # market_cap == 0 → 장 마감 전 미배포 데이터일 가능성 → 전 거래일 fallback
        trading_dates = _get_trading_dates(as_of_date, 2)
        if len(trading_dates) >= 2:
            prev_df = _cached_market_cap(_date_str(trading_dates[-2]))
            if prev_df is not None:
                extracted = _extract_market_cap_row(prev_df, ticker)

    if extracted is None:
        return FetchResult.failure(FetchErrorReason.INVALID_ZERO_VALUE)

    market_cap, trading_value = extracted
    result = FetchResult.success(MarketCap(
        market_cap=market_cap,
        trading_value=trading_value,
        source=DataSource.PYKRX,
        as_of_date=as_of_date,
    ))
    _cache.put("market_cap", _cache_key, result)
    return result


def fetch_fundamentals(
    ticker: str,
    as_of_date: date,
) -> FetchResult[Fundamentals]:
    prov = _get_kis()
    if prov is not None:
        return prov.fetch_fundamentals(ticker, as_of_date)

    _cache_key = f"{ticker}:{as_of_date}"
    _cached = _cache.get("fundamentals", _cache_key, _cache.TTL_FUNDAMS)
    if _cached is not None:
        return _cached

    date_str = _date_str(as_of_date)
    df = _cached_fundamental(date_str)

    if df is None:
        return FetchResult.failure(FetchErrorReason.API_TIMEOUT)
    if ticker not in df.index:
        return FetchResult.failure(FetchErrorReason.TICKER_NOT_FOUND)

    # pykrx 컬럼: BPS, PER, PBR, EPS, DIV, DPS
    try:
        row = df.loc[ticker]
        per = _round(row["PER"]) if pd.notna(row["PER"]) and row["PER"] != 0 else None
        pbr = _round(row["PBR"]) if pd.notna(row["PBR"]) and row["PBR"] != 0 else None
        div = _round(row["DIV"]) if pd.notna(row["DIV"]) and row["DIV"] != 0 else None
    except (KeyError, TypeError):
        return FetchResult.failure(FetchErrorReason.PARSE_ERROR)

    result = FetchResult.success(Fundamentals(
        per=per,
        pbr=pbr,
        div_yield=div,
        source=DataSource.PYKRX,
        as_of_date=as_of_date,
    ))
    _cache.put("fundamentals", _cache_key, result)
    return result


def fetch_risk_market_stats(
    ticker: str,
    as_of_date: date,
    price_lookback: int = 252,
) -> tuple[Optional[float], Optional[float]]:
    """
    시장 데이터 기반 리스크 지표.
    Returns: (volatility_pct_annualized, dilution_pct_yoy)
    None = 데이터 부족.

    volatility: 일간 수익률 std × √252 × 100 (연환산 %)
    dilution  : YoY 상장주식수 증가율 (%)
    """
    prov = _get_kis()
    if prov is not None:
        return prov.fetch_risk_market_stats(ticker, as_of_date, price_lookback)

    return (
        _calc_annualized_volatility(ticker, as_of_date, price_lookback),
        _calc_dilution_pct(ticker, as_of_date),
    )


_WINDOW_CACHE: dict = {}


def _window_ohlcv(ticker: str, as_of_date: date, n_days: int):
    """변동성 창(최근 n_days 거래일) 일봉. 변동성·거래정지 계산이 같은 조회를 공유한다(실행 중 메모리 캐시)."""
    key = (ticker, as_of_date, n_days)
    if key in _WINDOW_CACHE:
        return _WINDOW_CACHE[key]
    trading_dates = _get_trading_dates(as_of_date, n_days)
    if len(trading_dates) < max(60, n_days // 2):
        return None
    try:
        df = _call_krx(krx.get_market_ohlcv_by_date, _date_str(trading_dates[0]), _date_str(trading_dates[-1]), ticker)
    except _RETRY_EXCEPTIONS:
        return None                                   # 실패는 캐시하지 않음
    if len(_WINDOW_CACHE) > 2048:
        _WINDOW_CACHE.clear()
    _WINDOW_CACHE[key] = df
    return df


def fetch_halt_info(ticker: str, as_of_date: date, n_days: int = 252) -> tuple[Optional[int], Optional[bool]]:
    """(변동성 창 중 거래량 0인 날 수, 창의 마지막 거래일 거래량 0 여부). 조회 실패·자료 부족이면 (None, None)."""
    df = _window_ohlcv(ticker, as_of_date, n_days)
    if df is None or len(df) < 20 or "거래량" not in df:
        return None, None
    zero = df["거래량"] == 0
    return int(zero.sum()), bool(zero.iloc[-1])


def _calc_annualized_volatility(ticker: str, as_of_date: date, n_days: int) -> Optional[float]:
    df = _window_ohlcv(ticker, as_of_date, n_days)
    if df is None or len(df) < 20:
        return None
    try:
        returns = df["종가"].pct_change().dropna()
        return round(float(returns.std() * (252 ** 0.5) * 100), 2)
    except Exception:
        return None


def _calc_dilution_pct(ticker: str, as_of_date: date) -> Optional[float]:
    try:
        yr_ago = as_of_date.replace(year=as_of_date.year - 1)
    except ValueError:  # Feb 29 leap-year edge case
        yr_ago = as_of_date.replace(year=as_of_date.year - 1, day=28)

    df_now = _cached_market_cap(_date_str(as_of_date))
    df_yr  = _cached_market_cap(_date_str(yr_ago))
    if df_now is None or df_yr is None:
        return None
    if ticker not in df_now.index or ticker not in df_yr.index:
        return None
    try:
        shares_now = int(df_now.loc[ticker, "상장주식수"])
        shares_yr  = int(df_yr.loc[ticker, "상장주식수"])
        if shares_yr == 0:
            return None
        pct = round((shares_now - shares_yr) / shares_yr * 100, 2)
        # 50% 초과는 분사·합병 등 기업 구조 변경(corporate action)으로 인한 anomaly로 처리.
        # merger-induced dilution 예외 처리 미결 — 합병 신주 발행과 일반 희석을 구분하려면
        #       공시 데이터(합병등록일, 신주배정비율) 연동 필요. 현재는 None으로 무시.
        return pct if abs(pct) <= 50 else None
    except (KeyError, TypeError):
        return None


def fetch_kospi_disparity(
    as_of_date: date,
    window: int = 20,
) -> FetchResult[KospiDisparity]:
    """
    코스피 이격도 계산. 실제 거래일 window일 이동평균 기준.
    결과 캐시됨 — 종목별 재계산 금지, 1일 1회 호출.
    """
    prov = _get_kis()
    if prov is not None:
        return prov.fetch_kospi_disparity(as_of_date, window)

    key = (as_of_date, window)
    if key in _kospi_disp_cache:
        return FetchResult.success(_kospi_disp_cache[key])

    trading_dates = _get_trading_dates(as_of_date, window)
    if len(trading_dates) < window:
        return FetchResult.failure(FetchErrorReason.INSUFFICIENT_DATA)

    from_str = _date_str(trading_dates[0])
    to_str = _date_str(trading_dates[-1])

    try:
        df = _call_krx(krx.get_index_ohlcv_by_date, from_str, to_str, "1001")
    except _RETRY_EXCEPTIONS:
        return FetchResult.failure(FetchErrorReason.API_TIMEOUT)

    if df is None or len(df) < window:
        return FetchResult.failure(FetchErrorReason.INSUFFICIENT_DATA)

    try:
        closes = df["종가"]
        current = _round(closes.iloc[-1])
        ma = _round(closes.tail(window).mean())
        disparity = _round((closes.iloc[-1] / closes.tail(window).mean() - 1) * 100)
    except (KeyError, IndexError, TypeError, ZeroDivisionError):
        return FetchResult.failure(FetchErrorReason.PARSE_ERROR)

    result = KospiDisparity(
        current=current,
        ma_n=ma,
        disparity_pct=disparity,
        window=window,
        source=DataSource.PYKRX,
        as_of_date=as_of_date,
    )
    _kospi_disp_cache[key] = result
    _trim(_kospi_disp_cache)
    return FetchResult.success(result)


def _fetch_sector_df(date_str: str, market: str) -> Optional[pd.DataFrame]:
    """market="ALL" 은 pykrx 내부 버그(KeyError: '종가')가 있어 KOSPI/KOSDAQ 개별 호출."""
    try:
        df = _call_krx(krx.get_market_sector_classifications, date_str, market=market)
        return df if (df is not None and not df.empty) else None
    except Exception:
        return None


def prefetch_all_sectors(as_of_date: date) -> dict[str, str]:
    """
    모든 종목 업종 한 번에 조회 → _sector_cache 일괄 저장.
    screener 시작 직전 1회 호출로 per-ticker get_sector() 반복 호출 방지.
    반환: {ticker: KRX업종명}
    """
    date_str = _date_str(as_of_date)
    result: dict[str, str] = {}
    for market in ("KOSPI", "KOSDAQ"):
        df = _fetch_sector_df(date_str, market)
        if df is None:
            continue
        sector_col = next((c for c in df.columns if "업종" in str(c)), None)
        if sector_col is None:
            continue
        for idx, row in df.iterrows():
            ticker = str(idx)
            val    = row.get(sector_col)
            if pd.notna(val) and str(val).strip():
                s = str(val).strip()
                _sector_cache[ticker] = s
                result[ticker] = s
    return result


def get_sector(ticker: str, as_of_date: date) -> Optional[str]:
    """
    WICS 업종명 조회. 실패 시 None.
    KIS 모드: inquire-price의 업종 분류 사용 (WICS 비호환 가능).
    pykrx get_market_sector_classifications 래핑 — 미지원 버전에서 graceful fallback.
    """
    if ticker in _sector_cache:
        return _sector_cache[ticker]
    prov = _get_kis()
    if prov is not None:
        return prov.get_sector(ticker, as_of_date)
    date_str = _date_str(as_of_date)
    for market in ("KOSPI", "KOSDAQ"):
        df = _fetch_sector_df(date_str, market)
        if df is None:
            continue
        sector_col = next((c for c in df.columns if "업종" in str(c)), None)
        if sector_col is None:
            continue
        if ticker in df.index:
            val = df.loc[ticker, sector_col]
            if pd.notna(val) and str(val).strip():
                _sector_cache[ticker] = str(val).strip()
                return _sector_cache[ticker]
    return None


def fetch_price_context(ticker: str, as_of_date: date) -> Optional[dict]:
    """
    52주 최고/최저, 가격 위치(%), 거래량, VI 발동 여부.
    KIS 모드에서만 동작. pykrx 모드에서는 None 반환.
    """
    prov = _get_kis()
    if prov is None:
        return None
    return prov.fetch_price_context(ticker, as_of_date)
