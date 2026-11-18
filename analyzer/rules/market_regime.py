from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from constants import HeatLevel, MarketRegime
from analyzer.scoring.market import MarketHeat, score_market


def __getattr__(name):
    """`krx`(pykrx import 시 KRX 로그인)는 처음 쓸 때 불러온다(PEP 562).
    공식 입력 경로는 MarketState·_regime_from_heat만 쓰므로 pykrx 로그인을 만들지 않는다(§13.13)."""
    if name == "krx":
        from analyzer.data import krx
        globals()["krx"] = krx
        return krx
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# FIFO cache — dict insertion order (Python 3.7+) 보장.
# All caches assume EOD snapshot semantics.
_state_cache: dict[date, "MarketState"] = {}
_CACHE_MAX_SIZE = 5  # 최근 5일치만 유지


@dataclass(frozen=True)
class MarketState:
    """
    전역 시장 상태 — 1일 1회 계산, 전 종목 공유.

    원칙: 코스피 이격도·과열 경보는 종목마다 재계산 금지.
          get_market_state() 1회 호출 → 동일 날짜 이후 호출은 캐시 반환.
    """
    heat:       MarketHeat
    regime:     MarketRegime
    as_of_date: date


def _trim_cache() -> None:
    if len(_state_cache) > _CACHE_MAX_SIZE:
        for k in list(_state_cache)[: len(_state_cache) - _CACHE_MAX_SIZE]:
            del _state_cache[k]


def _regime_from_heat(heat_level: HeatLevel) -> MarketRegime:
    """
    v1 단순화: HeatLevel → MarketRegime 직접 매핑.
    주의: 현재 regime은 heat의 alias처럼 동작함 — 과열 ≠ 시장 방향이므로 추후 분리 필요.

    고도화 후보: 코스피 추세·채권금리·공포지수(VIX) 복합 판단 (see TODO.md).
    """
    if heat_level == HeatLevel.DANGER:
        return MarketRegime.RISK_OFF
    if heat_level == HeatLevel.CAUTION:
        return MarketRegime.RISK_NEUTRAL
    return MarketRegime.RISK_ON


def get_market_state(
    as_of_date: date,
    disparity_window: int = 20,
    force_refresh: bool = False,
) -> MarketState:
    """
    시장 상태 반환. 같은 날짜 재호출 시 캐시 반환.

    force_refresh=True — 동일 날짜의 캐시를 교체(replace)하고 재계산.
                         append가 아닌 overwrite. 테스트·장중 갱신용.
    disparity_window   — KOSPI 이동평균 기준일수 (기본 20일).

    KospiDisparity 조회 실패 시 heat_score=0 (COOL)으로 안전 폴백.
    ⚠️  폴백 시 MarketHeat.detail에 "KOSPI disparity unavailable" 명시됨.
        데이터 부재를 시장 안전 신호로 해석하지 말 것.
    이유: 데이터 오류가 전 종목 분석 차단으로 이어지면 안 됨.
    """
    if not force_refresh and as_of_date in _state_cache:
        return _state_cache[as_of_date]

    krx = globals().get("krx") or __getattr__("krx")
    disparity_result = krx.fetch_kospi_disparity(as_of_date, window=disparity_window)
    kospi_disparity  = disparity_result.data if disparity_result.ok else None

    heat   = score_market(
        kospi_disparity=kospi_disparity,
        margin_loan=None,  # 시장 전체 신용잔고 미구현 (v1) — ticker-level은 별도 처리
        as_of_date=as_of_date,
    )
    regime = _regime_from_heat(heat.heat_level)
    state  = MarketState(heat=heat, regime=regime, as_of_date=as_of_date)

    _state_cache[as_of_date] = state  # insert-then-trim: 정확한 FIFO 보장
    _trim_cache()
    return state
