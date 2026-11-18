from __future__ import annotations

from dataclasses import dataclass

from constants import Grade, Confidence, HeatLevel, MarketRegime
from analyzer.rules.market_regime import MarketState

# structured decision code 후보 — UI·logging·analytics 필터링용 (see TODO.md)
#   예) DecisionCode.BLOCKED_LOW_CONFIDENCE, DecisionCode.BLOCKED_HEAT_DANGER 등
# suggested_position_limit_pct 후보 — 등급·regime·heat 조합 권고 비중 상한 (see TODO.md)

_HEAT_EMOJI: dict[HeatLevel, str] = {
    HeatLevel.COOL:    "🟢",
    HeatLevel.WARNING: "🟡",
    HeatLevel.CAUTION: "🟠",
    HeatLevel.DANGER:  "🔴",
}


@dataclass(frozen=True)
class ExecutionDecision:
    """
    등급 × 시장 상태 → 실행 가능 여부.

    executable=False 시 reason에 차단 근거 반드시 명시.
    """
    executable: bool
    reason:     str
    grade:      Grade
    heat_level: HeatLevel
    regime:     MarketRegime

    def __post_init__(self) -> None:
        if not self.reason:
            raise ValueError("ExecutionDecision: reason must be non-empty")


def decide_execution(
    grade: Grade,
    market_state: MarketState,
    confidence: Confidence,
) -> ExecutionDecision:
    """
    실행 가능 상태 판정.

    차단 우선순위:
      1. LOW confidence  — 데이터 신뢰 불가 (점수와 무관하게 차단)
         v1 정책: LOW confidence는 무조건 차단. 일부 데이터 누락이더라도 예외 없음.
      2. Grade.AVOID     — 점수 미달
      3. HEAT DANGER     — 시장 과열 극단 (전 등급 차단)
      4. RISK_OFF + not STRONG_BUY — 방어 모드 (STRONG_BUY만 허용)

    HEAT CAUTION은 차단하지 않음.
    과열주의는 비중 조절 대상이지 진입 금지 상태는 아님.
    """
    heat_level = market_state.heat.heat_level
    regime     = market_state.regime
    heat_score = market_state.heat.heat_score
    emoji      = _HEAT_EMOJI[heat_level]

    if confidence == Confidence.LOW:
        return ExecutionDecision(
            executable=False,
            reason="LOW confidence — 데이터 신뢰 불가, 수동 확인 필요",
            grade=grade,
            heat_level=heat_level,
            regime=regime,
        )

    if grade == Grade.AVOID:
        return ExecutionDecision(
            executable=False,
            reason=f"등급 AVOID (시장 {emoji}, 과열 {heat_score}점)",
            grade=grade,
            heat_level=heat_level,
            regime=regime,
        )

    if heat_level == HeatLevel.DANGER:
        return ExecutionDecision(
            executable=False,
            reason=f"시장 과열 DANGER ({heat_score}점) — 전 등급 진입 차단",
            grade=grade,
            heat_level=heat_level,
            regime=regime,
        )

    if regime == MarketRegime.RISK_OFF and grade != Grade.STRONG_BUY:
        return ExecutionDecision(
            executable=False,
            reason=f"RISK_OFF 시장 — STRONG_BUY만 진입 허용 (현재 등급: {grade.value})",
            grade=grade,
            heat_level=heat_level,
            regime=regime,
        )

    return ExecutionDecision(
        executable=True,
        reason=f"실행 가능 (시장 {emoji}, 과열 {heat_score}점)",
        grade=grade,
        heat_level=heat_level,
        regime=regime,
    )
