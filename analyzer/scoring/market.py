# 역할 경계: 시장 상태 판단(score)만. 행동 결정("신규매수 금지" 등)은 rules/execution.py 소관.
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

import config
from constants import HeatLevel
from analyzer.data.models import KospiDisparity, MarginLoan

_HEAT = config.HEAT


@dataclass(frozen=True)
class MarketHeat:
    """
    시장 과열 경보 결과.

    heat_score : 0~12 (DB CHECK constraint 동일 범위).
    detail     : 항목별 점수 내역 — Explainable 원칙, non-empty 필수.
    """
    heat_score: int
    heat_level: HeatLevel
    detail:     str
    as_of_date: date

    def __post_init__(self) -> None:
        if not (0 <= self.heat_score <= 12):
            raise ValueError(
                f"MarketHeat: heat_score {self.heat_score} out of range [0, 12]"
            )
        if not self.detail:
            raise ValueError("MarketHeat: detail must be non-empty")
        expected_level = _heat_level(self.heat_score)
        if self.heat_level != expected_level:
            raise ValueError(
                f"MarketHeat: heat_level {self.heat_level} inconsistent with "
                f"heat_score {self.heat_score} (expected {expected_level})"
            )


def _heat_level(score: int) -> HeatLevel:
    if score >= _HEAT["danger_threshold"]:
        return HeatLevel.DANGER
    if score >= _HEAT["caution_threshold"]:
        return HeatLevel.CAUTION
    if score >= _HEAT["warning_threshold"]:
        return HeatLevel.WARNING
    return HeatLevel.COOL


def _disparity_points(disparity_pct: float) -> tuple[int, str]:
    if disparity_pct >= _HEAT["kospi_disparity_danger_pct"]:
        return 2, f"KOSPI 이격도 {disparity_pct:+.1f}% → +2"
    if disparity_pct >= _HEAT["kospi_disparity_warning_pct"]:
        return 1, f"KOSPI 이격도 {disparity_pct:+.1f}% → +1"
    return 0, f"KOSPI 이격도 {disparity_pct:+.1f}% → +0"


def _margin_loan_points(ratio_pct: float) -> tuple[int, str]:
    # market-wide 신용잔고율. ticker-level 신용잔고는 risk.py 소관.
    if ratio_pct >= _HEAT["margin_loan_danger_pct"]:
        return 2, f"신용잔고율 {ratio_pct:.1f}% → +2"
    if ratio_pct >= _HEAT["margin_loan_warning_pct"]:
        return 1, f"신용잔고율 {ratio_pct:.1f}% → +1"
    return 0, f"신용잔고율 {ratio_pct:.1f}% → +0"


def score_market(
    kospi_disparity: Optional[KospiDisparity],
    margin_loan: Optional[MarginLoan],
    as_of_date: date,
) -> MarketHeat:
    """
    시장 과열 경보 점수 산출 (0~12점).

    v1 구현 항목:
      - KOSPI 이격도 : 0/+1/+2점 (음수 이격도는 과열 아님 → 0점)
      - 신용잔고율   : 0/+1/+2점 (Optional — 미전달 시 항목 제외)

    음수 이격도(시장 과매도)는 과열 경보 대상 아님.
    market_regime.py에서 1일 1회 호출 권장.

    경계: 외국인 수급·종목 volatility·섹터 모멘텀 등 ticker-level 데이터 금지.
          이 함수는 시장 전체(market-wide) 지표만 다룸.

    추가 지표 후보 — 거래대금 급등, 상승 가속 캔들, VIX 등 (see TODO.md)
    """
    notes: list[str] = []
    score = 0

    if kospi_disparity is not None:
        pts, note = _disparity_points(kospi_disparity.disparity_pct)
        score += pts
        notes.append(note)
    else:
        notes.append("KOSPI disparity unavailable — COOL fallback")

    if margin_loan is not None:
        pts, note = _margin_loan_points(margin_loan.ratio_pct)
        score += pts
        notes.append(note)

    if not notes:
        notes.append("시장 과열 지표 없음")

    return MarketHeat(
        heat_score=score,
        heat_level=_heat_level(score),
        detail=" / ".join(notes),
        as_of_date=as_of_date,
    )
