from __future__ import annotations

from typing import Optional

import config
from analyzer.data.models import SupplyFlow, ForeignOwnership, MarketCap
from analyzer.scoring.models import ScoreComponent

_SUPPLY = config.SUPPLY


def _supply_type(foreign_net: int, institution_net: int) -> tuple[int, str]:
    f_pos = foreign_net > 0
    i_pos = institution_net > 0

    if f_pos and i_pos:
        return _SUPPLY["dual_buying_score"], "쌍끌이매수"
    if f_pos and not i_pos:
        return _SUPPLY["foreign_single_score"], "외국인단독"
    if not f_pos and i_pos:
        return _SUPPLY["institution_single_score"], "기관단독"
    return _SUPPLY["dual_selling_penalty"], "쌍끌이매도"


def _filter1_bonus(
    foreign_net: int,
    institution_net: int,
    market_cap: int,
) -> tuple[int, str]:
    threshold_pct = _SUPPLY["filter1_fast_marketcap_pct"]

    f_ratio_pct = foreign_net / market_cap * 100 if market_cap > 0 else 0
    i_ratio_pct = institution_net / market_cap * 100 if market_cap > 0 else 0

    f_qualifies = foreign_net > 0 and f_ratio_pct >= threshold_pct
    i_qualifies = institution_net > 0 and i_ratio_pct >= threshold_pct

    if f_qualifies or i_qualifies:
        qualifier = "외국인" if f_qualifies else "기관"
        return 2, f"필터1 통과 ({qualifier} 시총 대비 {max(f_ratio_pct, i_ratio_pct):.2f}%)"
    return 0, f"필터1 미달 (외국인 {f_ratio_pct:.2f}%, 기관 {i_ratio_pct:.2f}%)"


def _conflict_detected(
    supply: SupplyFlow,
    ownership: Optional[ForeignOwnership],
) -> bool:
    if ownership is None or ownership.change_lookback_pct is None:
        return False
    if supply.foreign_net_buy is None:
        return False
    return (
        supply.foreign_net_buy > 0
        and ownership.change_lookback_pct < 0
        and supply.lookback_days >= _SUPPLY["conflict_days_threshold"]
    )


def score_supply(
    supply: Optional[SupplyFlow],
    ownership: Optional[ForeignOwnership],
    market_cap_data: Optional[MarketCap],
) -> ScoreComponent:
    if supply is None or supply.foreign_net_buy is None or supply.institution_net_buy is None:
        return ScoreComponent(
        category="supply",
            name="supply",
            score=0,
            max_score=25,
            detail="수급 데이터 없음",
        )

    foreign_net = supply.foreign_net_buy
    institution_net = supply.institution_net_buy

    base_score, supply_type = _supply_type(foreign_net, institution_net)

    # bonus 구조 개선 후보 — bonus_applied, bonus_reason 필드로 추적 가능 (see TODO.md)
    notes = [f"{supply_type} {base_score}점"]
    conflict = _conflict_detected(supply, ownership)

    if conflict:
        halved = base_score // 2
        notes.append(
            f"외국인 순매수↑ vs 지분율↓ 충돌 {supply.lookback_days}일 "
            f"→ 기본점 {base_score}→{halved}점 / 보너스 적용 금지"
        )
        base_score = halved
        bonus = 0
    elif market_cap_data is not None and market_cap_data.market_cap > 0:
        f1_bonus, f1_note = _filter1_bonus(
            foreign_net, institution_net, market_cap_data.market_cap
        )
        bonus = f1_bonus
        notes.append(f1_note)
    else:
        bonus = 0

    # 외인지분율 변화 보너스
    ownership_bonus = 0
    if ownership is not None and ownership.change_lookback_pct is not None:
        chg = ownership.change_lookback_pct
        t3  = _SUPPLY["ownership_change_tier3_pp"]
        t2  = _SUPPLY["ownership_change_tier2_pp"]
        t1  = _SUPPLY["ownership_change_tier1_pp"]
        if chg >= t3:
            ownership_bonus = _SUPPLY["ownership_change_tier3_bonus"]
            notes.append(f"지분율 {chg:+.2f}%p (+{ownership_bonus})")
        elif chg >= t2:
            ownership_bonus = _SUPPLY["ownership_change_tier2_bonus"]
            notes.append(f"지분율 {chg:+.2f}%p (+{ownership_bonus})")
        elif chg >= t1:
            ownership_bonus = _SUPPLY["ownership_change_tier1_bonus"]
            notes.append(f"지분율 {chg:+.2f}%p (+{ownership_bonus})")
        elif chg < 0:
            ownership_bonus = _SUPPLY["ownership_decrease_penalty"]
            notes.append(f"지분율 {chg:+.2f}%p ({ownership_bonus})")
        else:
            notes.append(f"지분율 {chg:+.2f}%p")

    total = max(base_score + bonus + ownership_bonus, _SUPPLY["dual_selling_penalty"])

    return ScoreComponent(
        category="supply",
        name="supply",
        score=total,
        max_score=25,
        detail=" / ".join(notes),
    )
