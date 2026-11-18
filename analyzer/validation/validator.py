from __future__ import annotations

from datetime import date
from typing import Optional, TypedDict, TypeVar

import config
from constants import Confidence, QuickRejectReason
from analyzer.data.models import (
    FetchResult, FetchErrorReason,
    MarketCap, Fundamentals, SupplyFlow, ForeignOwnership, KospiDisparity, EarningsData, RiskData,
)
from analyzer.validation.models import ValidatedMarketData

T = TypeVar("T")


class ResolvedData(TypedDict):
    market_cap:        Optional[MarketCap]
    fundamentals:      Optional[Fundamentals]
    supply_flow:       Optional[SupplyFlow]
    foreign_ownership: Optional[ForeignOwnership]
    kospi_disparity:   Optional[KospiDisparity]
    earnings:          Optional[EarningsData]
    risk:              Optional[RiskData]


def _fatal_errors() -> frozenset[FetchErrorReason]:
    return frozenset(
        FetchErrorReason(v) for v in config.CONFIDENCE["fatal_error_reasons"]
    )


def _resolve(
    result: FetchResult[T],
) -> tuple[Optional[T], Optional[FetchErrorReason]]:
    if result.ok:
        return result.data, None
    return None, result.error


def _determine_confidence(
    fetch_errors: dict[str, FetchErrorReason],
    resolved: ResolvedData,
    notes: list[str],
) -> Confidence:
    """
    HIGH   : fetch_errors 없음, required_fields 모두 존재.
    MEDIUM : fetch_errors 있으나 fatal 없음, required_fields 충족.
    LOW    : fatal error(CONFIDENCE.fatal_error_reasons) 발생 또는 required_fields 누락
             (누락 원인이 일시 오류(API_TIMEOUT 등)여도 LOW).

    earnings/risk 등 optional 필드가 None이어도 confidence는 영향 없음.
    이는 의도된 보수적 설계 — optional 데이터 미수집을 오류로 간주하지 않음.
    """
    fatal = _fatal_errors()
    has_fatal = False

    for field_name, err in fetch_errors.items():
        if err in fatal:
            notes.append(f"{field_name} 영구 오류({err.value}) → LOW confidence")
            has_fatal = True

    has_missing_required = False
    # 필수 여부는 오류 종류와 무관하게 resolved 값으로 판정 (일시 오류여도 필수 결측이면 LOW)
    for field_name in config.CONFIDENCE["required_fields"]:
        if resolved.get(field_name) is None:
            err = fetch_errors.get(field_name)
            reason = f" ({err.value})" if err is not None else ""
            notes.append(f"{field_name} 없음{reason} → LOW confidence")
            has_missing_required = True

    if has_fatal or has_missing_required:
        return Confidence.LOW

    if fetch_errors:
        for field_name, err in fetch_errors.items():
            notes.append(f"{field_name} 일시 오류({err.value}) → MEDIUM confidence")
        return Confidence.MEDIUM

    return Confidence.HIGH


def _check_quick_reject(
    fundamentals: Optional[Fundamentals],
    notes: list[str],
    risk: Optional[RiskData] = None,
    # supply_flow, foreign_ownership 기반 reject 조건 후보 (see TODO.md)
    #   - 외국인+기관 쌍끌이 매도 지속 → QuickRejectReason.DUAL_FOREIGN_EXIT
    #   - 연속 미스 3분기 → QuickRejectReason.CONSECUTIVE_MISS
) -> tuple[bool, Optional[QuickRejectReason]]:
    qr = config.QUICK_REJECT

    # 거래정지 이력: 정지 기간의 0% 등락이 변동성을 낮춰 리스크 점수를 부풀린다(서희건설 사례, v8.6.9)
    if risk is not None and (risk.halted_on_session or (risk.halt_days or 0) >= qr["halt_days_min"]):
        why = "기준일 거래정지" if risk.halted_on_session else f"최근 252거래일 중 거래정지 {risk.halt_days}일"
        notes.append(f"{why} → quick reject")
        return True, QuickRejectReason.TRADING_HALT

    if fundamentals is not None and fundamentals.pbr is not None:
        if fundamentals.pbr > qr["pbr_max"]:
            notes.append(f"PBR {fundamentals.pbr:.2f} > {qr['pbr_max']} → quick reject")
            return True, QuickRejectReason.HIGH_PBR

    return False, None


def validate(
    ticker: str,
    as_of_date: date,
    market_cap_result:        FetchResult[MarketCap],
    fundamentals_result:      FetchResult[Fundamentals],
    supply_flow_result:       Optional[FetchResult[SupplyFlow]],
    foreign_ownership_result: Optional[FetchResult[ForeignOwnership]],
    kospi_disparity_result:   FetchResult[KospiDisparity],
    earnings_result:          Optional[FetchResult[EarningsData]] = None,
    risk_result:              Optional[FetchResult[RiskData]]    = None,
) -> ValidatedMarketData:
    """supply_flow·foreign_ownership에 None = 조회하지 않음(not_requested, 채점 미사용 — §13.15).
    조회 실패가 아니므로 fetch_errors(confidence)에 넣지 않는다."""
    market_cap,        e_mc   = _resolve(market_cap_result)
    fundamentals,      e_fund = _resolve(fundamentals_result)
    supply_flow,       e_sf   = _resolve(supply_flow_result) if supply_flow_result is not None else (None, None)
    foreign_ownership, e_fo   = (_resolve(foreign_ownership_result) if foreign_ownership_result is not None
                                 else (None, None))
    kospi_disparity,   e_kd   = _resolve(kospi_disparity_result)

    earnings: Optional[EarningsData]     = None
    e_earn:   Optional[FetchErrorReason] = None
    if earnings_result is not None:
        earnings, e_earn = _resolve(earnings_result)

    risk:   Optional[RiskData]          = None
    e_risk: Optional[FetchErrorReason]  = None
    if risk_result is not None:
        risk, e_risk = _resolve(risk_result)

    fetch_errors: dict[str, FetchErrorReason] = {}
    if e_mc:    fetch_errors["market_cap"]        = e_mc
    if e_fund:  fetch_errors["fundamentals"]      = e_fund
    if e_sf:    fetch_errors["supply_flow"]       = e_sf
    if e_fo:    fetch_errors["foreign_ownership"] = e_fo
    if e_kd:    fetch_errors["kospi_disparity"]   = e_kd
    if e_earn:  fetch_errors["earnings"]          = e_earn
    if e_risk:  fetch_errors["risk"]              = e_risk

    notes: list[str] = []
    for field_name, result in (("supply_flow", supply_flow_result), ("foreign_ownership", foreign_ownership_result)):
        if result is None:
            notes.append(f"{field_name} not_requested (채점 미사용)")

    resolved: ResolvedData = {
        "market_cap":        market_cap,
        "fundamentals":      fundamentals,
        "supply_flow":       supply_flow,
        "foreign_ownership": foreign_ownership,
        "kospi_disparity":   kospi_disparity,
        "earnings":          earnings,
        "risk":              risk,
    }

    confidence = _determine_confidence(fetch_errors, resolved, notes)
    quick_reject, qr_reason = _check_quick_reject(fundamentals, notes, risk)

    return ValidatedMarketData(
        ticker=ticker,
        as_of_date=as_of_date,
        confidence=confidence,
        quick_reject=quick_reject,
        quick_reject_reason=qr_reason,
        market_cap=market_cap,
        fundamentals=fundamentals,
        supply_flow=supply_flow,
        foreign_ownership=foreign_ownership,
        kospi_disparity=kospi_disparity,
        earnings=earnings,
        risk=risk,
        fetch_errors=fetch_errors,
        validation_notes=tuple(notes),
    )
