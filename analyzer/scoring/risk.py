from __future__ import annotations

import config
from analyzer.data.models import RiskData
from analyzer.scoring.models import ScoreComponent

_R = config.RISK


def score_risk(risk: RiskData | None) -> ScoreComponent:
    """
    리스크 채점 (0~25점). baseline 25점에서 감점만.

    risk=None → score 0 / 데이터 미제공, 보수적 처리.
    개별 필드 None → 해당 항목 평가 제외 (neutral, 감점 없음).
    debt_ratio_pct=None: 금융주 등 D/E 무의미 업종은 None으로 호출.
    """
    if risk is None:
        return ScoreComponent(
        category="risk",
            name="risk",
            score=0,
            max_score=_R["base_score"],
            detail="risk data unavailable",
        )

    total_penalty = 0
    lines: list[str] = []

    if risk.debt_ratio_pct is not None:
        if risk.debt_ratio_pct >= _R["debt_ratio_danger_pct"]:
            total_penalty += _R["debt_penalty"]
            lines.append(f"✗ 부채비율 높음 ({risk.debt_ratio_pct:.0f}%) ({_R['debt_penalty']})")
        else:
            lines.append(f"✓ 부채비율 양호 ({risk.debt_ratio_pct:.0f}%)")

    if risk.quarters_with_loss >= _R["loss_quarters_danger"]:
        total_penalty += _R["loss_danger_penalty"]
        lines.append(f"✗ 적자 분기 많음 (최근 4분기 중 {risk.quarters_with_loss}회) ({_R['loss_danger_penalty']})")
    elif risk.quarters_with_loss >= _R["loss_quarters_warning"]:
        total_penalty += _R["loss_warning_penalty"]
        lines.append(f"✗ 적자 분기 있음 (최근 4분기 중 {risk.quarters_with_loss}회) ({_R['loss_warning_penalty']})")
    else:
        lines.append("✓ 적자 분기 없음")

    if risk.dilution_pct is not None:
        if risk.dilution_pct >= _R["dilution_danger_pct"]:
            total_penalty += _R["dilution_penalty"]
            lines.append(f"✗ 주식 희석 ({risk.dilution_pct:.1f}% YoY) ({_R['dilution_penalty']})")
        else:
            lines.append(f"✓ 희석률 양호 ({risk.dilution_pct:.1f}% YoY)")

    if risk.volatility_pct is not None:
        if risk.volatility_pct >= _R["volatility_danger_pct"]:
            total_penalty += _R["volatility_penalty"]
            lines.append(f"✗ 주가 변동성 높음 ({risk.volatility_pct:.0f}%) ({_R['volatility_penalty']})")
        else:
            lines.append(f"✓ 주가 변동성 양호 ({risk.volatility_pct:.0f}%)")

    if risk.governance_flag:
        # governance_reason 필드 후보 — 리포트에서 "왜 governance 위험?" 설명용 (see TODO.md)
        total_penalty += _R["governance_penalty"]
        lines.append(f"✗ 지배구조 이슈 ({_R['governance_penalty']})")

    # DISABLED: interest_coverage is always None — 데이터 소스 미확보 (CLAUDE.md 참고)
    # 활성화 조건: DART EBIT / 이자비용 데이터 소스 확보 후 models.py interest_coverage 채워야 함
    if (
        risk.interest_coverage is not None
        and risk.interest_coverage < _R["interest_coverage_danger"]
        and risk.debt_ratio_pct is not None
        and risk.debt_ratio_pct > _R["interest_coverage_debt_min_pct"]
    ):
        total_penalty += _R["interest_coverage_penalty"]
        lines.append(
            f"✗ 이자보상배율 {risk.interest_coverage:.1f}x + 부채비율 {risk.debt_ratio_pct:.0f}% "
            f"({_R['interest_coverage_penalty']})"
        )

    score = max(0, _R["base_score"] + total_penalty)
    detail = "\n".join(lines) if lines else f"리스크 이상 없음 (base {_R['base_score']})"

    return ScoreComponent(
        category="risk",
        name="risk",
        score=score,
        max_score=_R["base_score"],
        detail=detail,
    )
