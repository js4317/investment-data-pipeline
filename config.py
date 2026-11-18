# NOTE: This framework is optimized for quality/stability investing,
# not cyclical trough or speculative turnaround investing.

# Numeric suffix convention:
#   _pct   = percent value  (e.g. 5.0  means 5%)
#   _ratio = decimal ratio  (e.g. 0.05 means 5%)
#   _pp    = percentage point (e.g. -0.5 means -0.5%p)
# Never mix — pick the suffix that matches the stored unit.

from constants import ENGINE_VERSION
RULE_VERSION_STR = f"v{ENGINE_VERSION}"

SUPPLY = {
    "foreign_single_score":            12,
    "institution_single_score":        10,
    "dual_buying_score":               20,
    "filter1_fast_marketcap_pct":       0.5,   # percent unit (0.5% = 0.5, not 0.005) / 시총 대비 0.5% 이상이면 3거래일 인정
    "conflict_days_threshold":          5,     # 순매수 vs 지분율 충돌 감지 기준일
    "dual_selling_penalty":            -5,
    # 외인지분율 변화 보너스 (단위: %p)
    "ownership_change_tier1_pp":        0.10,  # +0.10%p 이상 → +1점
    "ownership_change_tier2_pp":        0.30,  # +0.30%p 이상 → +2점
    "ownership_change_tier3_pp":        0.50,  # +0.50%p 이상 → +3점
    "ownership_change_tier1_bonus":     1,
    "ownership_change_tier2_bonus":     2,
    "ownership_change_tier3_bonus":     3,
    "ownership_decrease_penalty":      -1,    # 감소 시 -1점
}

VALUATION = {
    "per_upper_bound_score_table": [
        {"max_per":    6, "score": 25},
        {"max_per":    8, "score": 22},
        {"max_per":   10, "score": 18},
        {"max_per":   12, "score": 14},
        {"max_per":   15, "score": 10},
        {"max_per":   20, "score":  6},
        {"max_per": 9999, "score":  0},
    ],
    # Stage 20H 확정 PBR 고정 점수표 (2026-09-23).
    # <0.5→25, 0.5-1→20, 1-2→15, 2-3→10, ≥3→0. 재최적화 금지.
    "pbr_score_table": [
        {"max_pbr": 0.5,  "score": 25},
        {"max_pbr": 1.0,  "score": 20},
        {"max_pbr": 2.0,  "score": 15},
        {"max_pbr": 3.0,  "score": 10},
        {"max_pbr": 9999, "score":  0},
    ],
    "pbr_caution_threshold": 1.5,
    "pbr_danger_threshold":  3.0,
    # Hybrid 업종 상대평가 가중치 (절대 + 상대 PER)
    "hybrid_per_abs_weight": 0.5,   # 절대 PER 가중치
    "hybrid_per_rel_weight": 0.5,   # 업종 상대 PER 가중치
    "hybrid_pbr_rel_weight": 1.0,   # 업종 상대 PBR 가중치 (후보값 1.0 — 최적값 미확정, 추후 검증)
    "hybrid_min_sector_n":   5,     # 상대평가 최소 표본 수 (미만 시 절대 fallback)
    # 영업이익 신뢰도 (earnings quality) — op/ni 비율 기반 valuation 보정
    # op/ni < warn  → 경고만 (감점 없음)
    # op/ni < mild  → mild_penalty
    # op/ni < danger → danger_penalty
    "eq_warn_threshold":   0.8,
    "eq_mild_threshold":   0.5,
    "eq_danger_threshold": 0.2,
    "eq_mild_penalty":    -2,
    "eq_danger_penalty":  -5,
}

EARNINGS = {
    "base_score": {4: 22, 3: 20, 2: 15, 1: 9, 0: 5},
    "revision_up_adj":          3,
    "revision_down_adj":        -3,
    "large_miss_threshold_pct": -20.0,  # -20% 이하면 대형 미스
    "large_miss_penalty":       -5,
    "quality_structural_adj":   3,
    "quality_one_off_adj":      -3,
    # ROE 보너스 (bonus only — 흑자 기업을 탈락시키지 않음)
    "roe_tier3_pct":   20.0,   # ROE 20%+  → +3
    "roe_tier2_pct":   15.0,   # ROE 15%+  → +2
    "roe_tier1_pct":   10.0,   # ROE 10%+  → +1
    "roe_low_pct":      5.0,   # ROE <5%   → -1
    "roe_tier3_bonus":  3,
    "roe_tier2_bonus":  2,
    "roe_tier1_bonus":  1,
    "roe_low_penalty": -1,
}

RISK = {
    # NOTE: peakout_signal_min_count, exploration_stop_loss_pct 은 position mgmt 용 — 미구현 예약
    "base_score":            25,
    "debt_ratio_danger_pct": 200.0,
    "debt_penalty":          -5,
    "loss_quarters_warning": 2,
    "loss_quarters_danger":  3,
    "loss_warning_penalty":  -4,
    "loss_danger_penalty":   -7,
    "dilution_danger_pct":   5.0,    # YoY 주식수 증가율
    "dilution_penalty":      -5,
    "volatility_danger_pct": 60.0,   # annualized volatility (연환산 기준)
    "volatility_penalty":    -3,
    "governance_penalty":    -5,
    # 이자보상배율 복합 조건 (interest coverage + debt ratio 동시 충족 시 감점)
    # interest_coverage 계산에 필요한 이자비용 데이터 소스 미확보 (see TODO.md).
    #       RiskData.interest_coverage 필드는 존재하나 현재 항상 None.
    #       데이터 소스 확보 시 아래 파라미터 활성화.
    "interest_coverage_danger":       1.0,    # EBIT / 이자비용 < 1.0
    "interest_coverage_debt_min_pct": 120.0,  # 부채비율 120%+ 동시 조건
    "interest_coverage_penalty":     -3,
}

HEAT = {
    "warning_threshold":            3,    # 🟡 경고
    "caution_threshold":            6,    # 🟠 주의
    "danger_threshold":             9,    # 🔴 위험
    "kospi_disparity_warning_pct":  3.0,
    "kospi_disparity_danger_pct":   5.0,
    "margin_loan_warning_pct":      7.0,
    "margin_loan_danger_pct":      10.0,
}

GRADE = {
    "strong_buy_min_score": 85,
    "buy_min_score":        70,
    "neutral_min_score":    55,
}

POSITION = {
    "strong_buy_max_weight_pct":  35.0,
    "buy_max_weight_pct":         20.0,
    "buy_medium_confidence_pct":  10.0,  # BUY + MEDIUM confidence
    "neutral_max_weight_pct":      7.0,
    "single_stock_hard_cap_pct":  25.0,
    "regime_neutral_multiplier":   0.7,
    "regime_risk_off_multiplier":  0.3,
}

WATCHLIST = {
    "max_count":                       6,
    "dormant_days":                   90,
    "quick_review_foreign_ownership_drop_pp": -0.5,  # -0.5%p 하락 시 즉시 재평가
}

QUICK_REJECT = {
    "pbr_max":                  5.0,    # 극단 밸류 차단 (AI/반도체/플랫폼 고PBR 허용)
    "margin_loan_max_pct":     10.0,    # 투기 과열 차단
    "consecutive_miss_quarters": 3,     # 3분기 연속 미스 시 신뢰도 붕괴
    "halt_days_min":            20,     # 최근 252거래일 중 거래정지 20일(약 1개월) 이상이면 차단 — 상식 기준, 과거 데이터 최적화 아님 (v8.6.9)
}

CACHE = {
    "market_data_ttl_hours":  24,
    "fundamentals_ttl_hours": 24,
    "earnings_ttl_days":      90,
}

CONFIDENCE = {
    "fatal_error_reasons": [
        "ticker_not_found",
        "parse_error",
        "invalid_zero_value",
    ],
    "required_fields": ["market_cap", "fundamentals"],
}


def validate_config() -> None:
    per_table = VALUATION["per_upper_bound_score_table"]
    for i in range(len(per_table) - 1):
        if per_table[i]["max_per"] >= per_table[i + 1]["max_per"]:
            raise ValueError(
                f"VALUATION.per_upper_bound_score_table: "
                f"entry[{i}].max_per={per_table[i]['max_per']} >= "
                f"entry[{i+1}].max_per={per_table[i+1]['max_per']} (must be ascending)"
            )
        if per_table[i]["score"] <= per_table[i + 1]["score"]:
            raise ValueError(
                f"VALUATION.per_upper_bound_score_table: "
                f"entry[{i}].score={per_table[i]['score']} <= "
                f"entry[{i+1}].score={per_table[i+1]['score']} (must be descending)"
            )

    if SUPPLY["dual_buying_score"] < max(
        SUPPLY["foreign_single_score"], SUPPLY["institution_single_score"]
    ):
        raise ValueError(
            "SUPPLY: dual_buying_score must be >= max(foreign_single_score, institution_single_score)"
        )

    if GRADE["strong_buy_min_score"] <= GRADE["buy_min_score"]:
        raise ValueError("GRADE: strong_buy_min_score must be > buy_min_score")
    if GRADE["buy_min_score"] <= GRADE["neutral_min_score"]:
        raise ValueError("GRADE: buy_min_score must be > neutral_min_score")
    if GRADE["neutral_min_score"] <= 0:
        raise ValueError("GRADE: neutral_min_score must be > 0")

    _grade_config_keys = {f"{k}_min_score" for k in ("strong_buy", "buy", "neutral")}
    _missing_keys = _grade_config_keys - set(GRADE.keys())
    if _missing_keys:
        raise ValueError(f"GRADE config missing keys: {_missing_keys}")

    required_base_keys = {0, 1, 2, 3, 4}
    actual_base_keys = set(EARNINGS["base_score"].keys())
    if actual_base_keys != required_base_keys:
        missing = required_base_keys - actual_base_keys
        raise ValueError(f"EARNINGS.base_score missing keys: {missing}")

    if not (0 < RISK["loss_quarters_warning"] < RISK["loss_quarters_danger"] <= 4):
        raise ValueError(
            "RISK: loss_quarters_warning must be < loss_quarters_danger <= 4"
        )

    from analyzer.data.models import FetchErrorReason as _FER
    for v in CONFIDENCE["fatal_error_reasons"]:
        try:
            _FER(v)
        except ValueError:
            raise ValueError(
                f"CONFIDENCE.fatal_error_reasons: '{v}' is not a valid FetchErrorReason"
            )

    if EARNINGS["large_miss_threshold_pct"] >= 0:
        raise ValueError("EARNINGS.large_miss_threshold_pct must be negative")

    _risk_penalty_keys = [
        "debt_penalty", "loss_warning_penalty", "loss_danger_penalty",
        "dilution_penalty", "volatility_penalty", "governance_penalty",
    ]
    for k in _risk_penalty_keys:
        if RISK[k] > 0:
            raise ValueError(f"RISK.{k} must be <= 0, got {RISK[k]}")
