from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import config
from analyzer.scoring.models import ScoreComponent


@dataclass
class SectorPeers:
    """업종 내 PER/PBR 분포 — Hybrid 상대평가용."""
    bucket:     str
    per_sorted: list[float]          # 오름차순 정렬 (낮을수록 저평가)
    pbr_sorted: list[float] = field(default_factory=list)


# 퍼센타일 → 점수 변환표 (절대 PER 테이블과 동일한 최대값 25점)
_PCTL_SCORE_TABLE = [
    (0.10, 25),   # 최하위 10%  (가장 저평가)
    (0.20, 22),
    (0.33, 18),
    (0.50, 14),   # 중앙값
    (0.67, 10),
    (0.80,  6),
    (1.00,  2),   # 최상위 (가장 고평가)
]


def _pctl_score(rank: float) -> int:
    """퍼센타일 rank(0=최저가, 1=최고가) → 점수."""
    for threshold, score in _PCTL_SCORE_TABLE:
        if rank <= threshold:
            return score
    return 2


def pbr_score_from_table(pbr: Optional[float]) -> int:
    """
    Stage 20H 확정 PBR 고정 점수표 → 점수 (0~25).
    pbr이 None/음수이면 0 반환 (scorer에서 avail_max도 0으로 처리).
    """
    if pbr is None or pbr <= 0:
        return 0
    for entry in config.VALUATION["pbr_score_table"]:
        if pbr <= entry["max_pbr"]:
            return entry["score"]
    return 0  # fallback (테이블 마지막 max_pbr=9999로 커버됨)


def eq_penalty_from_ratio(ratio: Optional[float]) -> int:
    """
    earnings_quality_ratio → EQ 감점 계산 (순수 함수).
    score_valuation 내부와 외부(스냅샷·UI) 양쪽에서 동일 로직 재사용.
    """
    if ratio is None:
        return 0
    _V = config.VALUATION
    if ratio < _V["eq_danger_threshold"]:
        return _V["eq_danger_penalty"]
    if ratio < _V["eq_mild_threshold"]:
        return _V["eq_mild_penalty"]
    return 0


def eq_band_from_ratio(ratio: Optional[float]) -> str:
    """
    earnings_quality_ratio → 연구용 분류 레이블.
    penalty 티어와 1:1 대응 — 6개월 뒤 밴드별 평균 알파 분석용.
    반환값: "VERY_POOR" | "POOR" | "WARNING" | "GOOD" | ""
    """
    if ratio is None:
        return ""
    _V = config.VALUATION
    if ratio < _V["eq_danger_threshold"]:
        return "VERY_POOR"
    if ratio < _V["eq_mild_threshold"]:
        return "POOR"
    if ratio < _V["eq_warn_threshold"]:
        return "WARNING"
    return "GOOD"


def score_valuation(
    per: Optional[float],
    pbr: Optional[float],
    earnings_quality_ratio: Optional[float] = None,
) -> ScoreComponent:
    """
    밸류에이션 점수 산출 (최대 25점).

    한계:
    - PER 단독 의존도 높음. cyclic 업종·일회성 이익·회계 왜곡 시 점수 신뢰도 저하.
    - cross-sector 비교 부적합. 은행·철강·플랫폼·반도체의 정상 valuation range가 다름.
      동일 업종 내 상대 비교 용도로만 사용 권장.
    - PBR은 패널티 없이 경고만 제공. 성장주 과소평가 방지 목적.

    earnings_quality_ratio (op / ni):
    - PER 입력값(순이익) 신뢰도 보정. 일회성 이익·파생상품·자산매각 왜곡 탐지.
    - 비율이 낮을수록 순이익이 영업이익보다 과대 계상 → 밸류에이션 점수 하향.
    """
    if per is None or per <= 0:
        # PER invalid 시 PSR 대안 지표 후보 (see TODO.md)
        #   적자 턴어라운드·플랫폼·AI 성장주에서 PER 붕괴 케이스 多
        return ScoreComponent(
        category="valuation",
            name="valuation",
            score=0,
            max_score=25,
            detail="PER 없음 또는 음수 (적자 종목) — 밸류에이션 산출 불가",
        )

    table = config.VALUATION["per_upper_bound_score_table"]
    raw_score = 0
    matched = False
    for entry in table:
        if per <= entry["max_per"]:
            raw_score = entry["score"]
            matched = True
            break

    if not matched:
        raise ValueError(
            f"score_valuation: PER {per:.1f} exceeds all table entries — "
            "per_upper_bound_score_table 마지막 항목이 충분히 커야 함"
        )

    notes = [f"PER {per:.1f} → {raw_score}점"]

    if pbr is not None:
        caution = config.VALUATION["pbr_caution_threshold"]
        danger  = config.VALUATION["pbr_danger_threshold"]
        if pbr > danger:
            notes.append(f"PBR {pbr:.2f}배 — extreme (기준 {danger}배 초과)")
        elif pbr > caution:
            notes.append(f"PBR {pbr:.2f}배 — elevated (기준 {caution}배 초과)")
        else:
            notes.append(f"PBR {pbr:.2f}배")

    # 영업이익 신뢰도 보정 (earnings quality adjustment)
    eq_adj = 0
    _V = config.VALUATION
    if earnings_quality_ratio is not None:
        eq = earnings_quality_ratio
        if eq < _V["eq_danger_threshold"]:
            eq_adj = _V["eq_danger_penalty"]
            notes.append(
                f"영업이익 신뢰도 낮음 (op/ni={eq:.2f} < {_V['eq_danger_threshold']}) "
                f"→ {eq_adj:+d}pt"
            )
        elif eq < _V["eq_mild_threshold"]:
            eq_adj = _V["eq_mild_penalty"]
            notes.append(
                f"영업이익 신뢰도 주의 (op/ni={eq:.2f}) → {eq_adj:+d}pt"
            )
        elif eq < _V["eq_warn_threshold"]:
            notes.append(f"영업이익 신뢰도 경고 (op/ni={eq:.2f})")

    final_score = max(0, min(raw_score + eq_adj, 25))
    return ScoreComponent(
        category="valuation",
        name="valuation",
        score=final_score,
        max_score=25,
        detail=" / ".join(notes),
    )


def score_valuation_relative(
    per: Optional[float],
    peers: Optional[SectorPeers],
) -> int:
    """업종 내 상대 PER 점수만 (0-25). 표본 부족 / invalid시 0."""
    min_n = config.VALUATION.get("hybrid_min_sector_n", 5)
    if peers is None or per is None or per <= 0 or len(peers.per_sorted) < min_n:
        return 0
    rank = sum(1 for p in peers.per_sorted if p < per) / len(peers.per_sorted)
    return _pctl_score(rank)


def score_valuation_hybrid(
    per: Optional[float],
    pbr: Optional[float],
    peers: Optional[SectorPeers],
    earnings_quality_ratio: Optional[float] = None,
    is_holding: bool = False,
) -> ScoreComponent:
    """
    Hybrid 밸류에이션 점수 (최대 25점).

    = per_abs_weight × 절대PER점수 + per_rel_weight × 업종내퍼센타일점수

    peers가 None이거나 표본 수 < hybrid_min_sector_n이면 절대 PER만 사용 (fallback).
    PBR 상대평가는 hybrid_pbr_rel_weight > 0일 때만 활성화.

    is_holding=True (지주사):
      PER contribution을 제외하고 PBR 업종내 상대평가만 사용.
      근거: HOLD-2(저PER 효과 비유의), HOLD-3(저PBR ★+), counterfactual replay.
      PBR 데이터 부족 시 절대 PER fallback.
    """
    min_n = config.VALUATION.get("hybrid_min_sector_n", 5)

    # ── 지주사: PBR 상대평가만 적용 (PER contribution 제외) ──────────
    if is_holding:
        if pbr and pbr > 0 and peers and len(peers.pbr_sorted) >= min_n:
            pbr_rank  = sum(1 for b in peers.pbr_sorted if b < pbr) / len(peers.pbr_sorted)
            pbr_score = _pctl_score(pbr_rank)
            return ScoreComponent(
                category="valuation",
                name="valuation",
                score=pbr_score,
                max_score=25,
                detail=(
                    f"지주사 — PBR 상대평가 (PER 제외): "
                    f"PBR {pbr:.2f}배 업종내 하위{pbr_rank * 100:.0f}%"
                    f"({peers.bucket}, n={len(peers.pbr_sorted)}) → {pbr_score}pt"
                ),
            )
        # PBR 데이터 부족 → 절대 PER fallback
        abs_comp = score_valuation(per, pbr, earnings_quality_ratio=earnings_quality_ratio)
        return ScoreComponent(
            category="valuation",
            name="valuation",
            score=abs_comp.score,
            max_score=25,
            detail=f"지주사 — PBR 데이터 부족, 절대PER fallback: {abs_comp.detail}",
        )

    # ── 일반기업: 기존 Hybrid 로직 ───────────────────────────────────
    abs_comp  = score_valuation(per, pbr, earnings_quality_ratio=earnings_quality_ratio)
    abs_w     = config.VALUATION.get("hybrid_per_abs_weight", 0.5)
    rel_w     = config.VALUATION.get("hybrid_per_rel_weight", 0.5)
    pbr_rel_w = config.VALUATION.get("hybrid_pbr_rel_weight", 0.0)

    if peers is None or per is None or per <= 0 or len(peers.per_sorted) < min_n:
        return abs_comp

    per_rank  = sum(1 for p in peers.per_sorted if p < per) / len(peers.per_sorted)
    per_rel   = _pctl_score(per_rank)

    pbr_rel          = 0
    effective_pbr_w  = 0.0          # 실제 사용된 PBR 가중치 (pbr 없으면 분모에서 제외)
    if pbr_rel_w > 0 and pbr and pbr > 0 and len(peers.pbr_sorted) >= min_n:
        pbr_rank        = sum(1 for b in peers.pbr_sorted if b < pbr) / len(peers.pbr_sorted)
        pbr_rel         = _pctl_score(pbr_rank)
        effective_pbr_w = pbr_rel_w

    total_w   = abs_w + rel_w + effective_pbr_w
    hybrid    = round(
        (abs_comp.score * abs_w + per_rel * rel_w + pbr_rel * effective_pbr_w) / total_w
    )
    hybrid    = max(0, min(hybrid, 25))

    pbr_part = f" / PBR상대 {pbr_rel}pt" if effective_pbr_w > 0 else ""
    eq_parts = [p for p in abs_comp.detail.split(" / ") if "op/ni" in p]
    eq_suffix = f" / {eq_parts[0]}" if eq_parts else ""
    return ScoreComponent(
        category="valuation",
        name="valuation",
        score=hybrid,
        max_score=25,
        detail=(
            f"PER {per:.1f} → 절대 {abs_comp.score}pt"
            f" / 업종내({peers.bucket}, n={len(peers.per_sorted)}) {per_rel}pt"
            f"{pbr_part} → Hybrid {hybrid}pt{eq_suffix}"
        ),
    )
