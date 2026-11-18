from __future__ import annotations

from enum import Enum

# 버전 업 시 이 값을 직접 수정하고 docs/CHANGELOG.md에 해당 버전 섹션 추가.
ENGINE_VERSION = "8.6.9"

CALIBRATION_CANDIDATES: frozenset[str] = frozenset({"003380", "005210"})



class PositionStatus(str, Enum):
    """
    행동 상태 source of truth. Grade와 독립적.

    허용 전이:
      WATCH        → WATCH_DORMANT (비활성화)
      WATCH        → EXPLORATION   (탐색 진입)
      WATCH_DORMANT→ WATCH          (재활성화)
      EXPLORATION  → ACTIVE         (포지션 편입)
      EXPLORATION  → WATCH          (탐색 철회)
      ACTIVE       → EXITED         (청산)
    금지 전이:
      EXITED       → ACTIVE         (재진입은 WATCH부터 재시작)
      ACTIVE       → WATCH          (청산 없는 강등 금지)
    """
    WATCH         = "watch"
    WATCH_DORMANT = "watch_dormant"
    EXPLORATION   = "exploration"
    ACTIVE        = "active"
    EXITED        = "exited"


class ExitReason(str, Enum):
    INVALIDATION      = "invalidation"
    HEAT_RULE         = "heat_rule"
    THESIS_BREAK      = "thesis_break"
    TAKE_PROFIT       = "take_profit"
    MANUAL_DISCRETION = "manual_discretion"
    TIMEOUT           = "timeout"


class DataSource(str, Enum):
    PYKRX  = "pykrx"
    NAVER  = "naver"
    DART   = "dart"
    MANUAL = "manual"
    KIS    = "kis"
    KRX    = "krx"      # 공식 입력 경로: KRX 원 응답 직접 해석 (pykrx 미경유)


class Confidence(str, Enum):
    HIGH   = "high"
    MEDIUM = "medium"
    LOW    = "low"


class Grade(str, Enum):
    """scoring 결과 등급. 관찰상태(watchlist)는 PositionStatus 소관."""
    STRONG_BUY = "strong_buy"
    BUY        = "buy"
    NEUTRAL    = "neutral"
    AVOID      = "avoid"


GRADE_EMOJI: dict[Grade, str] = {
    Grade.STRONG_BUY: "🟢",
    Grade.BUY:        "🔵",
    Grade.NEUTRAL:    "🟡",
    Grade.AVOID:      "🔴",
}


class MarketRegime(str, Enum):  # 시장 방향성 — risk_on/off 판단
    RISK_ON      = "risk_on"
    RISK_NEUTRAL = "risk_neutral"
    RISK_OFF     = "risk_off"


class HeatLevel(str, Enum):  # 시장 과열도 — heat score 구간 분류 (구간 기준은 config.HEAT)
    COOL    = "cool"
    WARNING = "warning"
    CAUTION = "caution"
    DANGER  = "danger"


class QuickRejectReason(str, Enum):
    HIGH_PBR             = "high_pbr"
    HIGH_MARGIN_LOAN     = "high_margin_loan"
    CONSECUTIVE_MISS     = "consecutive_miss"
    DUAL_FOREIGN_EXIT    = "dual_foreign_exit"
    NEGATIVE_PER_DEFICIT = "negative_per_deficit"
    TRADING_HALT         = "trading_halt"     # 최근 252거래일 안에 거래정지(거래량 0) 이력 — 변동성 왜곡 방지 (v8.6.9)


class EarningsQuality(str, Enum):
    STRUCTURAL = "structural"
    MIXED      = "mixed"
    ONE_OFF    = "one_off"
    UNKNOWN    = "unknown"   # 미입력 → adj 0


class RevisionDirection(str, Enum):
    UP      = "up"
    FLAT    = "flat"
    DOWN    = "down"
    UNKNOWN = "unknown"   # coverage 없음·sparse → adj 0
