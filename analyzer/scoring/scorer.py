from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import TYPE_CHECKING

import config
from constants import Grade
from analyzer.validation.models import ValidatedMarketData
from analyzer.scoring.models import ScoringResult
from analyzer.scoring.valuation import score_valuation, score_valuation_hybrid, score_valuation_relative, SectorPeers, eq_penalty_from_ratio, eq_band_from_ratio, pbr_score_from_table
from analyzer.scoring.earnings import score_earnings
from analyzer.scoring.risk import score_risk

if TYPE_CHECKING:
    from run import AnalysisResult


def _determine_grade(score: int) -> Grade:
    g = config.GRADE
    thresholds = sorted([
        (g["strong_buy_min_score"], Grade.STRONG_BUY),
        (g["buy_min_score"],        Grade.BUY),
        (g["neutral_min_score"],    Grade.NEUTRAL),
    ], key=lambda x: x[0], reverse=True)
    for min_score, grade in thresholds:
        if score >= min_score:
            return grade
    return Grade.AVOID


def score(validated: ValidatedMarketData) -> ScoringResult:
    fund = validated.fundamentals
    per  = fund.per if fund else None
    pbr  = fund.pbr if fund else None
    eq_ratio = validated.risk.earnings_quality_ratio if validated.risk else None

    per_val_comp   = score_valuation(per, pbr, earnings_quality_ratio=eq_ratio)
    pbr_sc         = pbr_score_from_table(pbr)
    pbr_available  = pbr is not None and pbr > 0
    if pbr_available:
        valuation_comp = replace(
            per_val_comp,
            score     = per_val_comp.score + pbr_sc,
            max_score = 50,
            detail    = f"{per_val_comp.detail} / PBR→{pbr_sc}pt",
        )
    else:
        valuation_comp = per_val_comp

    earnings_comp  = score_earnings(validated.earnings, validated.fundamentals)
    risk_comp      = score_risk(validated.risk)

    # supply_score 제거 (2026-09-23): 5개 연구 전 방향 기각 → earn+val+risk 3-factor
    components = (valuation_comp, earnings_comp, risk_comp)

    if any(c.score > c.max_score for c in components):
        raise ValueError(
            "ScoreComponent invariant violation: "
            + ", ".join(
                f"{c.name}={c.score}/{c.max_score}"
                for c in components if c.score > c.max_score
            )
        )

    raw_total = max(0, min(sum(c.score for c in components), 100))

    # partial scoring normalization — UNKNOWN → neutral 철학 구현.
    # unavailable 컴포넌트는 available_max에서 제외 → grade 판정 시 implicit penalty 제거.
    available_max = (
        valuation_comp.max_score
        + (earnings_comp.max_score if validated.earnings is not None else 0)
        + (risk_comp.max_score    if validated.risk     is not None else 0)
    )
    normalized_total = (
        max(0, min(round(raw_total / available_max * 100), 100))
        if available_max > 0 else 0
    )

    # quick_reject: 점수는 유지(audit trail), grade만 AVOID 강제
    if validated.quick_reject:
        grade = Grade.AVOID
    else:
        grade = _determine_grade(normalized_total)

    # notes 중복 제거 미결 — validation/scoring/override notes 합산 시 동일 경고 반복 가능 (see TODO.md)
    notes = list(validated.validation_notes)
    if validated.earnings is None:
        notes.append("earnings unavailable — excluded from normalization")
    if validated.risk is None:
        notes.append("risk unavailable — excluded from normalization")
    if validated.quick_reject:
        notes.append(
            f"Quick Reject({validated.quick_reject_reason.value}) "
            f"→ Grade forced to AVOID (normalized: {normalized_total}, raw: {raw_total}/{available_max})"
        )

    return ScoringResult(
        ticker=validated.ticker,
        as_of_date=validated.as_of_date,
        total_score=raw_total,
        normalized_score=normalized_total,
        available_max=available_max,
        grade=grade,
        confidence=validated.confidence,
        valuation_score=valuation_comp.score,
        earnings_score=earnings_comp.score,
        risk_score=risk_comp.score,
        supply_score=0,   # deprecated: 2026-09-23 제거
        components=components,
        notes=tuple(notes),
        eq_penalty=eq_penalty_from_ratio(eq_ratio),
        eq_band=eq_band_from_ratio(eq_ratio),
    )


def build_sector_peers_map(
    results: list[AnalysisResult],
    sector_map: dict[str, str],
) -> dict[str, SectorPeers]:
    """
    scan 결과 전체에서 버킷별 PER/PBR 분포 구축.
    screener post 단계에서 1회 호출.
    """
    from analyzer.scoring.sector_buckets import krx_to_bucket

    bucket_pers: dict[str, list[float]] = defaultdict(list)
    bucket_pbrs: dict[str, list[float]] = defaultdict(list)

    for ar in results:
        f   = ar.validated.fundamentals if ar.validated else None
        per = f.per if f else None
        pbr = f.pbr if f else None
        bucket = krx_to_bucket(sector_map.get(ar.scoring.ticker))
        if per and 0 < per < 200:
            bucket_pers[bucket].append(per)
        if pbr and pbr > 0:
            bucket_pbrs[bucket].append(pbr)

    return {
        bucket: SectorPeers(
            bucket=bucket,
            per_sorted=sorted(pers),
            pbr_sorted=sorted(bucket_pbrs.get(bucket, [])),
        )
        for bucket, pers in bucket_pers.items()
    }


def apply_sector_correction(
    ar: AnalysisResult,
    sector_map: dict[str, str],
    peers_map: dict[str, SectorPeers],
) -> AnalysisResult:
    """
    Hybrid valuation score 적용.
    val_score_abs에 절대 PER 원본 점수 보존 (research용).
    표본 부족 / 업종 미분류 → 절대 기준 그대로.

    경로별 동작:
    - 일반기업: PER Hybrid(PBR 제외) + 고정 PBR 점수 보존, max_score=50 유지
    - 지주사 peers 없음: 보정 불가, 원본 그대로 반환
    - 지주사 상대평가 성공: PBR-only Hybrid 25pt 정책 유지 (고정 PBR 추가 없음)
    """
    from analyzer.scoring.sector_buckets import krx_to_bucket
    from analyzer.rules.execution import decide_execution
    from analyzer.holding_companies import HOLDING_TICKERS

    f      = ar.validated.fundamentals if ar.validated else None
    per    = f.per if f else None
    pbr    = f.pbr if f else None
    bucket = krx_to_bucket(sector_map.get(ar.scoring.ticker))
    peers  = peers_map.get(bucket)

    eq_ratio     = ar.validated.risk.earnings_quality_ratio if ar.validated and ar.validated.risk else None
    abs_score    = ar.scoring.valuation_score
    rel_score    = score_valuation_relative(per, peers)
    peer_count   = len(peers.per_sorted) if peers else 0
    is_holding   = ar.scoring.ticker in HOLDING_TICKERS
    min_n        = config.VALUATION.get("hybrid_min_sector_n", 5)

    # ── 지주사: PBR 상대평가 불가 → 원본 유지 ──────────────────────
    if is_holding and (peers is None or len(peers.pbr_sorted) < min_n):
        return replace(ar, scoring=replace(
            ar.scoring,
            val_score_abs     = abs_score,
            val_score_rel     = 0,
            sector_peer_count = peer_count,
            eq_penalty        = ar.scoring.eq_penalty,
            eq_band           = ar.scoring.eq_band,
        ))

    # ── 일반기업: PER Hybrid(PBR 제외) + 고정 PBR 보존 ─────────────
    if not is_holding:
        hybrid_per_comp  = score_valuation_hybrid(
            per, None, peers, earnings_quality_ratio=eq_ratio, is_holding=False
        )
        hybrid_per_score = hybrid_per_comp.score           # 0~25, PBR 없음
        pbr_sc           = pbr_score_from_table(pbr)
        new_val_score    = hybrid_per_score + pbr_sc
        new_val_max      = 25 + (25 if pbr is not None and pbr > 0 else 0)
        new_val_detail   = (
            f"{hybrid_per_comp.detail} / PBR→{pbr_sc}pt"
            if pbr_sc else hybrid_per_comp.detail
        )
    # ── 지주사 상대평가 성공: PBR-only Hybrid 유지 ───────────────────
    else:
        hybrid_comp    = score_valuation_hybrid(
            per, pbr, peers, earnings_quality_ratio=eq_ratio, is_holding=True
        )
        new_val_score  = hybrid_comp.score                 # PBR 상대 0~25
        new_val_max    = 25
        new_val_detail = hybrid_comp.detail

    # 변화 없으면 조기 반환
    if (new_val_score == abs_score
            and new_val_max == ar.scoring.components[0].max_score):
        return replace(ar, scoring=replace(
            ar.scoring,
            val_score_abs     = abs_score,
            val_score_rel     = rel_score,
            sector_peer_count = peer_count,
            eq_penalty        = ar.scoring.eq_penalty,
            eq_band           = ar.scoring.eq_band,
        ))

    # ── 전체 갱신 ────────────────────────────────────────────────────
    earn_max  = ar.scoring.components[1].max_score if ar.validated and ar.validated.earnings else 0
    risk_max  = ar.scoring.components[2].max_score if ar.validated and ar.validated.risk     else 0
    new_avail = new_val_max + earn_max + risk_max

    rest      = ar.scoring.earnings_score + ar.scoring.risk_score
    new_raw   = max(0, min(new_val_score + rest, 100))
    new_norm  = max(0, min(round(new_raw / new_avail * 100), 100)) if new_avail > 0 else 0

    is_rejected = any("Quick Reject" in n for n in ar.scoring.notes)
    new_grade   = Grade.AVOID if is_rejected else _determine_grade(new_norm)

    new_val_comp  = replace(
        ar.scoring.components[0],
        score     = new_val_score,
        max_score = new_val_max,
        detail    = new_val_detail,
    )
    new_components = (new_val_comp,) + ar.scoring.components[1:]

    new_scoring = replace(
        ar.scoring,
        valuation_score   = new_val_score,
        total_score       = new_raw,
        normalized_score  = new_norm,
        available_max     = new_avail,
        grade             = new_grade,
        val_score_abs     = abs_score,
        val_score_rel     = rel_score,
        sector_peer_count = peer_count,
        components        = new_components,
        eq_penalty        = ar.scoring.eq_penalty,
        eq_band           = ar.scoring.eq_band,
    )
    new_decision = decide_execution(new_grade, ar.market, new_scoring.confidence)
    return replace(ar, scoring=new_scoring, decision=new_decision)
