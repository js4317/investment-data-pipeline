from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from constants import Grade, Confidence

# component 고정 순서 — 리포트·상태판 일관성 보장
COMPONENT_ORDER = ("valuation", "earnings", "risk")


@dataclass(frozen=True)
class ScoreComponent:
    """
    단일 채점 항목의 결과.

    score   : 음수 허용 (패널티·쌍끌이매도 등 반영). total clamp는 scorer 레이어 담당.
              단, score > max_score는 invariant 위반.
    detail  : 핵심 점수 산출 근거. 반드시 non-empty. 1~2문장 이내로 제한.
              길이 폭주 금지 — 문단 수준 설명은 report 레이어에서 처리.
              예) "PER 8.0 → 22점 / PBR 0.40"
    notes   : ScoringResult.notes — 보조 경고·예외·관찰 사항.
              예) "Quick Reject → Grade forced to AVOID"

    정책:
    - 데이터 부족은 score=0 대입 금지. component 자체를 누락시키고 notes에 이유 기록.
      "0점"과 "데이터 없음"은 전혀 다른 의미 — None vs 0 철학의 scoring 레이어 버전.
    - component 누락(earnings, risk 미구현 등)은 허용.
      단, ScoringResult.notes에 누락 이유 반드시 기록.
    """

    name:      str
    score:     int
    max_score: int
    detail:    str
    category:  str = ""   # "valuation" | "earnings" | "risk" | "supply"

    def __post_init__(self) -> None:
        if self.max_score <= 0:
            raise ValueError(
                f"ScoreComponent({self.name}): max_score must be > 0, got {self.max_score}"
            )
        if self.score > self.max_score:
            raise ValueError(
                f"ScoreComponent({self.name}): score {self.score} exceeds max_score {self.max_score}"
            )
        if self.score < -self.max_score:
            raise ValueError(
                f"ScoreComponent({self.name}): score {self.score} below "
                f"-max_score({-self.max_score})"
            )
        if not self.detail:
            raise ValueError(
                f"ScoreComponent({self.name}): detail must be non-empty (Explainable principle)"
            )


@dataclass(frozen=True)
class ScoringResult:
    ticker:           str
    as_of_date:       date
    total_score:      int   # raw sum — audit trail용
    normalized_score: int   # total / available_max * 100 — grade 판정 기준
    available_max:    int   # 활성 컴포넌트 max 합산 (partial scoring 시 < 100)
    grade:            Grade
    confidence:       Confidence
    valuation_score:  int
    earnings_score:   int
    risk_score:       int
    supply_score:     int
    components:       tuple[ScoreComponent, ...]
    notes:            tuple[str, ...]
    val_score_abs:    int = 0   # 절대 PER 기준 원본 valuation 점수 (Hybrid 보정 전, research용)
    val_score_rel:    int = 0   # 업종 상대 PER 기준 점수 (Hybrid 보정 전, research용)
    sector_peer_count: int = 0  # 상대평가 버킷 내 표본 수 (신뢰도 연구용)
    eq_penalty:       int = 0   # 영업이익 신뢰도 감점 (음수 또는 0). snapshot 분해용.
    eq_band:          str = ""  # "VERY_POOR"|"POOR"|"WARNING"|"GOOD"|"". 밴드별 알파 연구용.
