from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from analyzer.portfolio.models import PortfolioState

_GRADE_QUALITY: dict[str, float] = {
    "strong_buy": 1.00,
    "buy":        0.75,
    "neutral":    0.30,
    "avoid":      0.00,
}
_RR_QUALITY: dict[str, float] = {
    "LOW":    1.0,
    "MEDIUM": 0.5,
    "HIGH":   0.0,
}
_SECTOR_WARN_PCT  = 25.0   # 단일 업종 비중 이 이상 → 경고
_CASH_LOW_WARN    = 5.0    # 현금 이 미만 → 경고
_CASH_HIGH_WARN   = 40.0   # 현금 이 초과 → 기회비용 경고


@dataclass(frozen=True)
class PortfolioHealth:
    score:          int              # 0-100 종합 건강도
    avg_score:      float            # 보유 종목 평균 normalized_score
    strong_buy_pct: float            # STRONG_BUY 종목 수 비율 (포지션 수 기준, %)
    cash_pct:       float            # 현금 비중 (%)
    avg_risk:       str              # "LOW" | "MEDIUM" | "HIGH" | "N/A"
    avg_momentum:   str              # "positive" | "negative" | "neutral" | "N/A"
    warnings:       tuple[str, ...]  # 경고 메시지 목록

    @property
    def grade(self) -> str:
        if self.score >= 80: return "GREEN"
        if self.score >= 60: return "YELLOW"
        return "RED"

    @property
    def grade_emoji(self) -> str:
        return {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}[self.grade]

    @property
    def grade_label(self) -> str:
        return {"GREEN": "양호", "YELLOW": "주의", "RED": "점검 필요"}[self.grade]


def compute_health(
    portfolio:  PortfolioState,
    snap_map:   dict[str, dict],
    rr_read_fn: Callable[[str], Optional[dict]],
) -> Optional[PortfolioHealth]:
    """
    포트폴리오 건강도 계산.

    score 구성:
      50% 평균 normalized_score (종목 선택 품질)
      20% 등급 분포 품질        (grade_quality 가중 평균)
      20% Risk Radar 평균       (캐시 없으면 MEDIUM 가정)
      10% 현금 적정성            (5-30% → 만점, 그 외 감점)
    """
    if not portfolio.positions:
        return None

    held_entries = [(t, snap_map[t]) for t in portfolio.positions if t in snap_map]
    if not held_entries:
        return None

    n = len(held_entries)

    # ── 평균 점수 컴포넌트 (0-100)
    score_comp = sum(e.get("normalized_score", 0) for _, e in held_entries) / n

    # ── 등급 품질 컴포넌트 (0-100)
    grade_comp = sum(
        _GRADE_QUALITY.get(e.get("grade", "neutral"), 0.3)
        for _, e in held_entries
    ) / n * 100

    # ── Risk Radar 컴포넌트 (0-100)
    rr_vals:   list[float] = []
    rr_high:   list[str]   = []
    for t, _ in held_entries:
        cached = rr_read_fn(t)
        if cached:
            lvl = cached.get("overall", "MEDIUM")
            rr_vals.append(_RR_QUALITY.get(lvl, 0.5))
            if lvl == "HIGH":
                rr_high.append(t)
        else:
            rr_vals.append(0.5)   # 캐시 없음 → MEDIUM 가정
    rr_comp = sum(rr_vals) / len(rr_vals) * 100 if rr_vals else 50.0

    # ── 현금 컴포넌트 (0-100)
    cash_pct = portfolio.cash_pct
    if _CASH_LOW_WARN <= cash_pct <= 30:  cash_comp = 100.0
    elif cash_pct > 30:                   cash_comp = 70.0   # 현금 과다
    else:                                 cash_comp = 20.0   # 현금 부족

    # ── 종합 Health Score
    health_score = round(
        score_comp * 0.50
        + grade_comp * 0.20
        + rr_comp    * 0.20
        + cash_comp  * 0.10
    )
    health_score = max(0, min(health_score, 100))

    # ── 지표 계산
    sb_count    = sum(1 for _, e in held_entries if e.get("grade") == "strong_buy")
    sb_pct      = round(sb_count / n * 100, 1)
    avg_risk_str = _avg_risk_label(rr_vals)

    mom_signals = [e.get("mom_signal") for _, e in held_entries if e.get("mom_signal") not in (None, "unavailable")]
    pos_n = sum(1 for m in mom_signals if m == "positive")
    neg_n = sum(1 for m in mom_signals if m == "negative")
    if not mom_signals:         avg_mom = "N/A"
    elif pos_n > neg_n:         avg_mom = "positive"
    elif neg_n > pos_n:         avg_mom = "negative"
    else:                       avg_mom = "neutral"

    # ── 경고 생성
    warnings = _build_warnings(
        held_entries, portfolio, rr_high, cash_pct, snap_map
    )

    return PortfolioHealth(
        score          = health_score,
        avg_score      = round(score_comp, 1),
        strong_buy_pct = sb_pct,
        cash_pct       = round(cash_pct, 1),
        avg_risk       = avg_risk_str,
        avg_momentum   = avg_mom,
        warnings       = tuple(warnings),
    )


def _avg_risk_label(rr_vals: list[float]) -> str:
    if not rr_vals:
        return "N/A"
    avg = sum(rr_vals) / len(rr_vals)
    if avg >= 0.80: return "LOW"
    if avg >= 0.40: return "MEDIUM"
    return "HIGH"


def _build_warnings(
    held_entries:    list[tuple[str, dict]],
    portfolio:       PortfolioState,
    rr_high_tickers: list[str],
    cash_pct:        float,
    snap_map:        dict[str, dict],
) -> list[str]:
    from analyzer.data.krx import get_ticker_name as _name

    warnings: list[str] = []

    avoid_or_neutral = [
        t for t, e in held_entries
        if e.get("grade") in ("neutral", "avoid")
    ]
    if avoid_or_neutral:
        labels = ", ".join(_name(t) or t for t in avoid_or_neutral)
        warnings.append(f"NEUTRAL/AVOID 종목 보유: {labels}")

    if rr_high_tickers:
        labels = ", ".join(_name(t) or t for t in rr_high_tickers)
        warnings.append(f"Risk Radar HIGH: {labels}")

    if cash_pct < _CASH_LOW_WARN:
        warnings.append(f"현금 {cash_pct:.1f}% — 긴급 대응 여력 부족 (권장 {_CASH_LOW_WARN:.0f}%+)")

    # 섹터 집중도 (Schema 4 이상 스냅샷에 sector 데이터 있을 때만 동작)
    sector_weights: dict[str, float] = {}
    for t, pos in portfolio.positions.items():
        entry = snap_map.get(t)
        if entry and entry.get("sector"):
            s = entry["sector"]
            sector_weights[s] = sector_weights.get(s, 0.0) + pos.weight_pct

    for sector, weight in sorted(sector_weights.items(), key=lambda x: -x[1]):
        if weight > _SECTOR_WARN_PCT:
            warnings.append(
                f"{sector} 비중 {weight:.1f}% (권장 {_SECTOR_WARN_PCT:.0f}% 이하)"
            )

    return warnings
