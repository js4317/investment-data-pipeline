from __future__ import annotations

import config
from typing import Optional
from constants import EarningsQuality, RevisionDirection
from analyzer.data.models import EarningsData, Fundamentals
from analyzer.scoring.models import ScoreComponent

_E = config.EARNINGS


def score_earnings(earnings: EarningsData | None,
                   fundamentals: Optional[Fundamentals] = None) -> ScoreComponent:
    """
    실적 채점 (0~25점).

    earnings=None → score 0 / detail에 사유 명시.
    UNKNOWN revision/quality → adj 0 (데이터 coverage bias 방지).
    large_miss_pct sign convention: negative=miss (e.g. -25.0), positive=beat.
    large_miss_pct 판단 기준은 config.EARNINGS["large_miss_threshold_pct"].
    """
    if earnings is None:
        return ScoreComponent(
        category="earnings",
            name="earnings",
            score=0,
            max_score=25,
            detail="earnings data unavailable",
        )

    if earnings.quarters_met_expectations not in _E["base_score"]:
        raise ValueError(
            f"score_earnings: quarters_met_expectations={earnings.quarters_met_expectations} "
            "not in EARNINGS.base_score — validate_config 또는 EarningsData invariant 확인"
        )

    base = _E["base_score"][earnings.quarters_met_expectations]
    adj  = 0
    lines: list[str] = []

    # 기본 실적 기준
    max_base = max(_E["base_score"].values())
    if base == max_base:
        lines.append("✓ 최근 4분기 실적 기준 만점")
    else:
        lines.append(f"✗ 최근 4분기 {earnings.quarters_met_expectations}회 충족 (base {base})")

    # Revision
    if earnings.revision == RevisionDirection.UP:
        adj += _E["revision_up_adj"]
        lines.append(f"✓ Revision 상향 (+{_E['revision_up_adj']})")
    elif earnings.revision == RevisionDirection.DOWN:
        adj += _E["revision_down_adj"]
        lines.append(f"✗ Revision 하향 ({_E['revision_down_adj']})")
    else:
        lines.append("✗ Revision 상향 보너스 없음")

    # 대형 미스
    if (
        earnings.large_miss_pct is not None
        and earnings.large_miss_pct <= _E["large_miss_threshold_pct"]
    ):
        adj += _E["large_miss_penalty"]
        lines.append(f"✗ 대형 미스 {earnings.large_miss_pct:.1f}% ({_E['large_miss_penalty']})")

    # 실적 품질
    if earnings.quality == EarningsQuality.STRUCTURAL:
        adj += _E["quality_structural_adj"]
        lines.append(f"✓ 구조적 개선 (+{_E['quality_structural_adj']})")
    elif earnings.quality == EarningsQuality.ONE_OFF:
        adj += _E["quality_one_off_adj"]
        lines.append(f"✗ 일회성 dominant ({_E['quality_one_off_adj']})")
    else:
        lines.append("✗ 구조적 개선 보너스 없음")

    # ROE 보너스 (bonus only — 흑자 기업을 탈락시키지 않는다)
    roe_bonus = 0
    roe = fundamentals.roe if fundamentals else None
    if roe is None:
        lines.append("✗ ROE 데이터 없음")
    elif roe >= _E["roe_tier3_pct"]:
        roe_bonus = _E["roe_tier3_bonus"]
        lines.append(f"✓ ROE {roe:.1f}%  20%+ 보너스 (+{roe_bonus})")
    elif roe >= _E["roe_tier2_pct"]:
        roe_bonus = _E["roe_tier2_bonus"]
        lines.append(f"✓ ROE {roe:.1f}%  15%+ 보너스 (+{roe_bonus})")
    elif roe >= _E["roe_tier1_pct"]:
        roe_bonus = _E["roe_tier1_bonus"]
        lines.append(f"✓ ROE {roe:.1f}%  10%+ 보너스 (+{roe_bonus})")
    elif roe < _E["roe_low_pct"]:
        roe_bonus = _E["roe_low_penalty"]
        lines.append(f"✗ ROE {roe:.1f}%  저ROE 패널티 ({roe_bonus})")
    else:
        lines.append(f"✗ ROE {roe:.1f}%  보너스 없음")

    return ScoreComponent(
        category="earnings",
        name="earnings",
        score=max(0, min(base + adj + roe_bonus, 25)),
        max_score=25,
        detail="\n".join(lines),
    )
