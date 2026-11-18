"""
전체 파이프라인 통합 테스트.

validator → scorer → market_regime → execution 흐름 검증.
네트워크 호출은 전부 mock.

v8.5.x 기준: 4개 컴포넌트(valuation 25 + earnings 25 + risk 25 + supply 25) 모두 구현.
available_max = 활성 컴포넌트 합산 (earnings/risk 미전달 시 50, 전체 전달 시 100).
normalized_score = raw_total / available_max * 100 — grade 판정 기준.
"""

from __future__ import annotations

from datetime import date

import config

from constants import DataSource, Confidence, Grade, HeatLevel, MarketRegime, QuickRejectReason, EarningsQuality, RevisionDirection
from analyzer.data.models import (
    FetchResult, FetchErrorReason,
    MarketCap, Fundamentals, SupplyFlow, ForeignOwnership, KospiDisparity, EarningsData, RiskData,
)
from analyzer.validation import validator
from analyzer.scoring import scorer
from analyzer.scoring.market import score_market, MarketHeat, _heat_level
from analyzer.rules.market_regime import MarketState, _regime_from_heat
from analyzer.rules.execution import decide_execution

FIXED_TEST_DATE = date(2026, 5, 26)  # 고정 날짜 — 실제 거래일 검증과 무관
TEST_TICKER = "005930"

# heat_level별 최솟값 (config 기반) — threshold 변경 시 자동 반영
_HEAT_MIN_SCORE: dict[HeatLevel, int] = {
    HeatLevel.COOL:    0,
    HeatLevel.WARNING: config.HEAT["warning_threshold"],
    HeatLevel.CAUTION: config.HEAT["caution_threshold"],
    HeatLevel.DANGER:  config.HEAT["danger_threshold"],
}


# ---------------------------------------------------------------------------
# 팩토리 헬퍼
# ---------------------------------------------------------------------------

def make_market_cap(market_cap: int = 5_000_000_000_000) -> FetchResult[MarketCap]:
    return FetchResult.success(MarketCap(
        market_cap=market_cap,
        trading_value=50_000_000_000,
        source=DataSource.PYKRX,
        as_of_date=FIXED_TEST_DATE,
    ))


def make_fundamentals(per: float = 8.0, pbr: float = 0.9) -> FetchResult[Fundamentals]:
    return FetchResult.success(Fundamentals(
        per=per,
        pbr=pbr,
        div_yield=2.0,
        source=DataSource.PYKRX,
        as_of_date=FIXED_TEST_DATE,
    ))


def make_supply_flow(
    foreign_net: int = 2_000_000_000,
    institution_net: int = 1_500_000_000,
) -> FetchResult[SupplyFlow]:
    return FetchResult.success(SupplyFlow(
        foreign_net_buy=foreign_net,
        institution_net_buy=institution_net,
        lookback_days=7,
        source=DataSource.PYKRX,
        as_of_date=FIXED_TEST_DATE,
    ))


def make_foreign_ownership(change_pct: float = 0.3) -> FetchResult[ForeignOwnership]:
    return FetchResult.success(ForeignOwnership(
        ownership_pct=15.0,
        prev_pct=14.7,
        change_lookback_pct=change_pct,
        lookback_days=7,
        source=DataSource.PYKRX,
        as_of_date=FIXED_TEST_DATE,
    ))


def make_risk_result(
    quarters_with_loss: int = 0,
    debt_ratio_pct: float | None = None,
    dilution_pct: float | None = None,
    volatility_pct: float | None = None,
    governance_flag: bool = False,
) -> FetchResult[RiskData]:
    return FetchResult.success(RiskData(
        quarters_with_loss=quarters_with_loss,
        debt_ratio_pct=debt_ratio_pct,
        dilution_pct=dilution_pct,
        volatility_pct=volatility_pct,
        governance_flag=governance_flag,
        source=DataSource.MANUAL,
        as_of_date=FIXED_TEST_DATE,
    ))


def make_earnings_result(
    quarters_met: int = 3,
    large_miss_pct: float | None = None,
    revision: RevisionDirection = RevisionDirection.UNKNOWN,
    quality: EarningsQuality = EarningsQuality.UNKNOWN,
) -> FetchResult[EarningsData]:
    return FetchResult.success(EarningsData(
        quarters_met_expectations=quarters_met,
        large_miss_pct=large_miss_pct,
        revision=revision,
        quality=quality,
        source=DataSource.MANUAL,
        as_of_date=FIXED_TEST_DATE,
    ))


def make_kospi_disparity(disparity_pct: float = 1.0) -> FetchResult[KospiDisparity]:
    return FetchResult.success(KospiDisparity(
        current=2500.0,
        ma_n=2475.0,
        disparity_pct=disparity_pct,
        window=20,
        source=DataSource.PYKRX,
        as_of_date=FIXED_TEST_DATE,
    ))


def make_heat(level: HeatLevel, score: int | None = None) -> MarketHeat:
    """config.HEAT threshold 기반으로 score 자동 결정 — threshold 변경 시 자동 반영.
    score 명시 시 해당 값 사용 (level 내 특정 점수 테스트용).
    """
    s = score if score is not None else _HEAT_MIN_SCORE[level]
    return MarketHeat(
        heat_score=s,
        heat_level=_heat_level(s),
        detail=f"test:{level.value}",
        as_of_date=FIXED_TEST_DATE,
    )


def make_market_state(disparity_pct: float = 1.0) -> MarketState:
    heat = score_market(
        kospi_disparity=make_kospi_disparity(disparity_pct).data,
        margin_loan=None,
        as_of_date=FIXED_TEST_DATE,
    )
    return MarketState(
        heat=heat,
        regime=_regime_from_heat(heat.heat_level),
        as_of_date=FIXED_TEST_DATE,
    )


def make_market_state_from_heat(heat: MarketHeat) -> MarketState:
    return MarketState(
        heat=heat,
        regime=_regime_from_heat(heat.heat_level),
        as_of_date=FIXED_TEST_DATE,
    )


# ---------------------------------------------------------------------------
# 테스트
# ---------------------------------------------------------------------------

class TestFullPipelineNormalData:
    """정상 데이터 → HIGH confidence, 전체 파이프라인 무결 실행."""

    def test_confidence_is_high(self):
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(), make_fundamentals(),
            make_supply_flow(), make_foreign_ownership(),
            make_kospi_disparity(),
            risk_result=make_risk_result(dilution_pct=1.0, debt_ratio_pct=80.0),
        )
        assert validated.confidence == Confidence.HIGH

    def test_no_quick_reject(self):
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(), make_fundamentals(per=8.0, pbr=0.9),
            make_supply_flow(), make_foreign_ownership(),
            make_kospi_disparity(),
        )
        assert validated.quick_reject is False
        assert validated.quick_reject_reason is None

    def test_scorer_produces_valid_result(self):
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(), make_fundamentals(per=8.0, pbr=0.9),
            make_supply_flow(), make_foreign_ownership(),
            make_kospi_disparity(),
        )
        result = scorer.score(validated)

        assert result.valuation_score == 42   # PER 8.0→22 + PBR 0.9→20 = 42점
        assert result.supply_score == 0       # supply 제거 (2026-09-23)
        assert result.total_score == 42       # raw sum (42 val only, earnings/risk None)
        assert result.available_max == 50     # earnings/risk unavailable → val 50만 (PER+PBR)
        assert result.normalized_score == 84  # 42/50 * 100 = 84 → BUY
        assert result.grade == Grade.BUY      # 84점 → BUY (70~85)

    def test_all_components_satisfy_invariant(self):
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(), make_fundamentals(),
            make_supply_flow(), make_foreign_ownership(),
            make_kospi_disparity(),
        )
        result = scorer.score(validated)
        for c in result.components:
            assert c.score <= c.max_score
            assert c.detail

    def test_execution_blocked_by_avoid_grade(self):
        """PBR 극단값 → quick reject → grade AVOID → 실행 차단."""
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(), make_fundamentals(per=6.0, pbr=6.0),
            make_supply_flow(), make_foreign_ownership(),
            make_kospi_disparity(),
        )
        result = scorer.score(validated)
        decision = decide_execution(result.grade, make_market_state(), result.confidence)

        assert result.grade == Grade.AVOID
        assert decision.executable is False


class TestQuickReject:
    """PBR 초과 → quick_reject, grade AVOID 강제, audit trail 유지."""

    def test_high_pbr_triggers_quick_reject(self):
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(), make_fundamentals(per=6.0, pbr=5.5),
            make_supply_flow(), make_foreign_ownership(),
            make_kospi_disparity(),
        )
        assert validated.quick_reject is True
        assert validated.quick_reject_reason == QuickRejectReason.HIGH_PBR

    def test_quick_reject_grade_forced_to_avoid(self):
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(), make_fundamentals(per=6.0, pbr=5.5),
            make_supply_flow(), make_foreign_ownership(),
            make_kospi_disparity(),
        )
        result = scorer.score(validated)
        assert result.grade == Grade.AVOID

    def test_quick_reject_preserves_raw_score(self):
        """audit trail: raw score는 유지, grade만 override."""
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(), make_fundamentals(per=6.0, pbr=5.5),
            make_supply_flow(), make_foreign_ownership(),
            make_kospi_disparity(),
        )
        result = scorer.score(validated)
        assert result.total_score > 0
        assert any("raw:" in n for n in result.notes)

    def test_quick_reject_note_in_scoring_result(self):
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(), make_fundamentals(per=6.0, pbr=5.5),
            make_supply_flow(), make_foreign_ownership(),
            make_kospi_disparity(),
        )
        result = scorer.score(validated)
        assert any("Quick Reject" in n for n in result.notes)

    def test_high_confidence_with_quick_reject(self):
        """HIGH confidence여도 quick_reject 조건 해당 시 grade AVOID 강제."""
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(), make_fundamentals(per=6.0, pbr=5.5),
            make_supply_flow(), make_foreign_ownership(),
            make_kospi_disparity(),
            risk_result=make_risk_result(dilution_pct=1.0, debt_ratio_pct=80.0),
        )
        result = scorer.score(validated)

        assert validated.confidence == Confidence.HIGH  # 데이터는 정상
        assert result.grade == Grade.AVOID              # 종목이 기준 미달


class TestPartialDataFailure:
    """일시적 오류 (API_TIMEOUT) → MEDIUM confidence."""

    def test_supply_timeout_medium_confidence(self):
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(), make_fundamentals(),
            FetchResult.failure(FetchErrorReason.API_TIMEOUT),
            make_foreign_ownership(),
            make_kospi_disparity(),
        )
        assert validated.confidence == Confidence.MEDIUM
        assert validated.supply_flow is None

    def test_supply_timeout_supply_score_zero(self):
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(), make_fundamentals(),
            FetchResult.failure(FetchErrorReason.API_TIMEOUT),
            make_foreign_ownership(),
            make_kospi_disparity(),
        )
        result = scorer.score(validated)
        assert result.supply_score == 0

    def test_supply_timeout_valuation_still_scored(self):
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(), make_fundamentals(per=8.0),
            FetchResult.failure(FetchErrorReason.API_TIMEOUT),
            make_foreign_ownership(),
            make_kospi_disparity(),
        )
        result = scorer.score(validated)
        assert result.valuation_score == 42  # PER 8.0→22 + PBR 0.9(default)→20 = 42


class TestRequiredFieldMissing:
    """F01: 필수 필드 결측은 오류 종류(일시/영구)와 무관하게 LOW → 실행 차단. 점수는 참고용으로 계산."""

    def _validate(self, market_cap_result, fundamentals_result=None, supply_result=None):
        return validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            market_cap_result,
            fundamentals_result or make_fundamentals(per=6.0, pbr=0.4),
            supply_result or make_supply_flow(),
            make_foreign_ownership(),
            make_kospi_disparity(),
            risk_result=make_risk_result(dilution_pct=1.0, debt_ratio_pct=80.0),
        )

    def test_market_cap_timeout_is_low_and_blocked(self):
        validated = self._validate(FetchResult.failure(FetchErrorReason.API_TIMEOUT))
        assert validated.confidence == Confidence.LOW
        result = scorer.score(validated)
        decision = decide_execution(result.grade, make_market_state(), result.confidence)
        assert decision.executable is False
        assert "LOW confidence" in decision.reason

    def test_fundamentals_timeout_is_low(self):
        validated = self._validate(make_market_cap(),
                                   fundamentals_result=FetchResult.failure(FetchErrorReason.API_TIMEOUT))
        assert validated.confidence == Confidence.LOW

    def test_market_cap_timeout_score_still_computed(self):
        """점수는 계산되지만 실행가능과 분리된다."""
        validated = self._validate(FetchResult.failure(FetchErrorReason.API_TIMEOUT))
        result = scorer.score(validated)
        assert result.valuation_score > 0
        assert result.confidence == Confidence.LOW

    def test_required_present_optional_timeout_stays_medium(self):
        """필수값 정상 + 비필수 일시 오류는 기존대로 MEDIUM (실행 차단 아님)."""
        validated = self._validate(make_market_cap(),
                                   supply_result=FetchResult.failure(FetchErrorReason.API_TIMEOUT))
        assert validated.confidence == Confidence.MEDIUM
        result = scorer.score(validated)
        decision = decide_execution(result.grade, make_market_state(), result.confidence)
        assert not (decision.executable is False and "LOW confidence" in decision.reason)

    def test_required_present_no_errors_high(self):
        validated = self._validate(make_market_cap())
        assert validated.confidence == Confidence.HIGH


class TestFatalDataFailure:
    """영구 오류 (PARSE_ERROR) → LOW confidence → execution 차단."""

    def test_fundamentals_parse_error_low_confidence(self):
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(),
            FetchResult.failure(FetchErrorReason.PARSE_ERROR),
            make_supply_flow(), make_foreign_ownership(),
            make_kospi_disparity(),
        )
        assert validated.confidence == Confidence.LOW

    def test_low_confidence_blocks_execution(self):
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(),
            FetchResult.failure(FetchErrorReason.PARSE_ERROR),
            make_supply_flow(), make_foreign_ownership(),
            make_kospi_disparity(),
        )
        result = scorer.score(validated)
        decision = decide_execution(result.grade, make_market_state(), result.confidence)

        assert decision.executable is False
        assert "LOW confidence" in decision.reason

    def test_market_cap_missing_low_confidence(self):
        """required_fields 중 하나라도 없으면 LOW."""
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            FetchResult.failure(FetchErrorReason.TICKER_NOT_FOUND),
            make_fundamentals(),
            make_supply_flow(), make_foreign_ownership(),
            make_kospi_disparity(),
        )
        assert validated.confidence == Confidence.LOW


class TestExecutionPriority:
    """차단 우선순위 검증 — LOW confidence > AVOID > DANGER > RISK_OFF."""

    def test_low_confidence_beats_strong_buy(self):
        """LOW confidence + STRONG_BUY + COOL → LOW confidence가 먼저 차단."""
        cool_state = make_market_state_from_heat(make_heat(HeatLevel.COOL))
        decision = decide_execution(Grade.STRONG_BUY, cool_state, Confidence.LOW)
        assert decision.executable is False
        assert "LOW confidence" in decision.reason

    def test_low_confidence_beats_danger_heat(self):
        """LOW confidence + DANGER heat → LOW confidence가 먼저 차단."""
        danger_state = make_market_state_from_heat(make_heat(HeatLevel.DANGER))
        decision = decide_execution(Grade.STRONG_BUY, danger_state, Confidence.LOW)
        assert decision.executable is False
        assert "LOW confidence" in decision.reason

    def test_avoid_grade_beats_cool_market(self):
        """AVOID grade + COOL market → AVOID grade가 차단."""
        cool_state = make_market_state_from_heat(make_heat(HeatLevel.COOL))
        decision = decide_execution(Grade.AVOID, cool_state, Confidence.HIGH)
        assert decision.executable is False
        assert "AVOID" in decision.reason

    def test_danger_heat_beats_buy_grade(self):
        """DANGER heat + BUY grade → DANGER가 차단."""
        danger_state = make_market_state_from_heat(make_heat(HeatLevel.DANGER))
        decision = decide_execution(Grade.BUY, danger_state, Confidence.HIGH)
        assert decision.executable is False
        assert "DANGER" in decision.reason

    def test_low_confidence_wins_over_all_conditions(self):
        """모든 차단 조건 동시: LOW confidence + AVOID + DANGER + RISK_OFF → LOW confidence 우선."""
        danger_risk_off = MarketState(
            heat=make_heat(HeatLevel.DANGER),
            regime=MarketRegime.RISK_OFF,
            as_of_date=FIXED_TEST_DATE,
        )
        decision = decide_execution(Grade.AVOID, danger_risk_off, Confidence.LOW)
        assert decision.executable is False
        assert "LOW confidence" in decision.reason


class TestMarketHeat:
    """시장 과열 → execution 차단."""

    def test_danger_heat_blocks_execution(self):
        """단일 지표만으로 DANGER 도달 어려워 직접 생성."""
        danger_state = make_market_state_from_heat(make_heat(HeatLevel.DANGER))
        decision = decide_execution(Grade.BUY, danger_state, Confidence.HIGH)
        assert decision.executable is False
        assert "DANGER" in decision.reason

    def test_cool_heat_does_not_block_buy_grade(self):
        cool_state = make_market_state(disparity_pct=0.5)
        decision = decide_execution(Grade.BUY, cool_state, Confidence.HIGH)
        assert decision.executable is True

    def test_risk_off_blocks_non_strong_buy(self):
        warning_heat = make_heat(HeatLevel.WARNING)
        risk_off_state = MarketState(
            heat=warning_heat,
            regime=MarketRegime.RISK_OFF,
            as_of_date=FIXED_TEST_DATE,
        )
        decision = decide_execution(Grade.BUY, risk_off_state, Confidence.HIGH)
        assert decision.executable is False
        assert "RISK_OFF" in decision.reason

    def test_risk_off_allows_strong_buy(self):
        warning_heat = make_heat(HeatLevel.WARNING)
        risk_off_state = MarketState(
            heat=warning_heat,
            regime=MarketRegime.RISK_OFF,
            as_of_date=FIXED_TEST_DATE,
        )
        decision = decide_execution(Grade.STRONG_BUY, risk_off_state, Confidence.HIGH)
        assert decision.executable is True

    def test_caution_heat_does_not_block(self):
        """CAUTION은 차단 아님 — 비중 조절 대상."""
        caution_state = make_market_state_from_heat(make_heat(HeatLevel.CAUTION))
        decision = decide_execution(Grade.BUY, caution_state, Confidence.HIGH)
        assert decision.executable is True


class TestApplySectorCorrection:
    """apply_sector_correction PBR 소멸 버그 수정 회귀 테스트 (Stage 20K).

    수정 전 버그:
    - 일반기업: hybrid_score(PBR 덮어씀) → PBR 20pt 소멸
    - 지주사 peers 없음: fallback score 0 → PBR 20pt 소멸
    - available_max / components 미갱신

    수정 후 기대 동작:
    - 일반기업 peers 있음: val = per_hybrid + pbr_score_table, max=50 유지
    - 지주사 peers 없음: val == 보정 전 그대로 (early return)
    - components[0].max_score, available_max 갱신
    """

    def _make_ar(self, ticker=TEST_TICKER, per: float = 8.0, pbr=0.9):
        from run import AnalysisResult
        from analyzer.scoring.market import score_market
        from analyzer.rules.market_regime import MarketState, _regime_from_heat
        from analyzer.data.models import Fundamentals as F
        mc  = make_market_cap()
        f   = FetchResult.success(F(per=per, pbr=pbr, div_yield=2.0,
                                   source=DataSource.PYKRX, as_of_date=FIXED_TEST_DATE))
        kd  = make_kospi_disparity()
        validated = validator.validate(
            ticker, FIXED_TEST_DATE,
            mc, f, make_supply_flow(), make_foreign_ownership(), kd,
        )
        scoring = scorer.score(validated)
        heat = score_market(kospi_disparity=kd.data, margin_loan=None, as_of_date=FIXED_TEST_DATE)
        ms   = MarketState(heat=heat, regime=_regime_from_heat(heat.heat_level), as_of_date=FIXED_TEST_DATE)
        return AnalysisResult(
            scoring=scoring, decision=decide_execution(scoring.grade, ms, scoring.confidence),
            market=ms, validated=validated,
        )

    def test_general_no_peers_preserves_original(self):
        """일반기업 peers 없음 → val 동일 (early return)."""
        ar   = self._make_ar(pbr=0.70)
        ar2  = scorer.apply_sector_correction(ar, {}, {})
        assert ar2.scoring.valuation_score == ar.scoring.valuation_score
        assert ar2.scoring.available_max   == ar.scoring.available_max

    def test_general_peers_pbr_score_preserved(self):
        """일반기업 peers 있음 → val = per_hybrid + pbr_sc, max=50 유지."""
        from analyzer.scoring.scorer import SectorPeers
        from analyzer.scoring.valuation import score_valuation_hybrid, pbr_score_from_table

        bucket = "기타"
        peers  = SectorPeers(bucket=bucket, per_sorted=[5, 8, 12, 15, 20], pbr_sorted=[])
        ar     = self._make_ar(per=8.0, pbr=0.70)
        ar2    = scorer.apply_sector_correction(ar, {TEST_TICKER: None}, {bucket: peers})

        per_hybrid = score_valuation_hybrid(8.0, None, peers, earnings_quality_ratio=None, is_holding=False).score
        pbr_sc     = pbr_score_from_table(0.70)
        assert ar2.scoring.valuation_score == per_hybrid + pbr_sc, \
            f"PBR 소멸: expected {per_hybrid}+{pbr_sc}={per_hybrid+pbr_sc}, got {ar2.scoring.valuation_score}"
        assert ar2.scoring.components[0].max_score == 50
        assert ar2.scoring.available_max == 50  # earnings/risk 없음 → val only

    def test_general_peers_pbr_none_max25(self):
        """일반기업 PBR=None → max=25, pbr_sc=0."""
        from analyzer.scoring.scorer import SectorPeers
        from analyzer.scoring.valuation import score_valuation_hybrid

        bucket = "기타"
        peers  = SectorPeers(bucket=bucket, per_sorted=[5, 8, 12, 15, 20], pbr_sorted=[])
        ar     = self._make_ar(per=8.0, pbr=None)
        ar2    = scorer.apply_sector_correction(ar, {TEST_TICKER: None}, {bucket: peers})

        per_hybrid = score_valuation_hybrid(8.0, None, peers, earnings_quality_ratio=None, is_holding=False).score
        assert ar2.scoring.valuation_score == per_hybrid
        assert ar2.scoring.components[0].max_score == 25
        assert ar2.scoring.available_max == 25

    def test_holding_no_peers_preserves_original(self):
        """지주사 peers 없음 → 보정 전 val 그대로 (0으로 덮어쓰기 금지)."""
        from analyzer.holding_companies import HOLDING_TICKERS
        holding = next(iter(HOLDING_TICKERS))
        ar  = self._make_ar(ticker=holding, per=8.0, pbr=0.85)
        ar2 = scorer.apply_sector_correction(ar, {}, {})
        assert ar2.scoring.valuation_score == ar.scoring.valuation_score, \
            f"지주사 val 소멸: {ar.scoring.valuation_score} → {ar2.scoring.valuation_score}"
        assert ar2.scoring.available_max == ar.scoring.available_max

    def test_components_invariant_after_correction(self):
        """보정 후 모든 components: score <= max_score."""
        from analyzer.scoring.scorer import SectorPeers
        bucket = "기타"
        peers  = SectorPeers(bucket=bucket, per_sorted=[5, 8, 12, 15, 20], pbr_sorted=[])
        ar     = self._make_ar(per=8.0, pbr=0.70)
        ar2    = scorer.apply_sector_correction(ar, {TEST_TICKER: None}, {bucket: peers})
        for c in ar2.scoring.components:
            assert c.score <= c.max_score, f"{c.name}: score={c.score} > max={c.max_score}"

    def test_general_peers_per_rel_differs_correction_applied(self):
        """per_rel ≠ abs 시 보정 실제 발생 — components/available_max 갱신 확인.

        per=10.0, peers=[5,10,20,30,40]:
          abs=18, per_rel=22(rank=0.2), hybrid=(18*0.5+22*0.5)/1.0=20
          pbr_sc(0.70)=20 → new_val=40, abs_score=38 → 보정 발생.
        """
        from analyzer.scoring.scorer import SectorPeers
        from analyzer.scoring.valuation import score_valuation_hybrid, pbr_score_from_table

        bucket = "기타"
        peers  = SectorPeers(bucket=bucket, per_sorted=[5, 10, 20, 30, 40], pbr_sorted=[])
        ar     = self._make_ar(per=10.0, pbr=0.70)
        ar2    = scorer.apply_sector_correction(ar, {TEST_TICKER: None}, {bucket: peers})

        per_hybrid = score_valuation_hybrid(10.0, None, peers, earnings_quality_ratio=None, is_holding=False).score
        pbr_sc     = pbr_score_from_table(0.70)
        expected   = per_hybrid + pbr_sc
        assert ar2.scoring.valuation_score != ar.scoring.valuation_score, "보정 발생해야 함"
        assert ar2.scoring.valuation_score == expected, \
            f"expected {expected}, got {ar2.scoring.valuation_score}"
        assert ar2.scoring.components[0].max_score == 50
        assert ar2.scoring.available_max == 50

    def test_holding_pbr_none_with_sufficient_peers_preserves(self):
        """지주사 + pbr=None + peers 충분: abs fallback → abs_score와 동일 → early return."""
        from analyzer.scoring.scorer import SectorPeers
        from analyzer.holding_companies import HOLDING_TICKERS
        holding = next(iter(HOLDING_TICKERS))
        bucket  = "기타"
        # pbr_sorted 충분히 있어도 종목 pbr=None → abs fallback (early return)
        peers   = SectorPeers(bucket=bucket, per_sorted=[5,8,12,15,20],
                              pbr_sorted=[0.5, 0.6, 0.7, 0.8, 0.9])
        ar      = self._make_ar(ticker=holding, per=8.0, pbr=None)
        ar2     = scorer.apply_sector_correction(ar, {holding: None}, {bucket: peers})
        assert ar2.scoring.valuation_score == ar.scoring.valuation_score, \
            f"지주사 pbr=None val 소멸: {ar.scoring.valuation_score} → {ar2.scoring.valuation_score}"

    def test_double_correction_prevented(self):
        """동일 ar에 두 번 보정해도 멱등 (한 번과 같은 결과)."""
        from analyzer.scoring.scorer import SectorPeers
        bucket = "기타"
        peers  = SectorPeers(bucket=bucket, per_sorted=[5, 10, 20, 30, 40], pbr_sorted=[])
        ar     = self._make_ar(per=10.0, pbr=0.70)
        ar2    = scorer.apply_sector_correction(ar, {TEST_TICKER: None}, {bucket: peers})
        ar3    = scorer.apply_sector_correction(ar2, {TEST_TICKER: None}, {bucket: peers})
        assert ar3.scoring.valuation_score == ar2.scoring.valuation_score
        assert ar3.scoring.available_max   == ar2.scoring.available_max


class TestRiskScoring:
    """risk 채점 정책 검증 — baseline 25점, 감점 전용."""

    def test_risk_missing_pipeline_survives(self):
        """risk_result 미전달 → risk_score=0, pipeline 정상 동작."""
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(), make_fundamentals(),
            make_supply_flow(), make_foreign_ownership(),
            make_kospi_disparity(),
        )
        result = scorer.score(validated)
        assert result.risk_score == 0
        assert any("risk unavailable" in n for n in result.notes)

    def test_risk_no_flags_baseline(self):
        """모든 필드 기본값 → 감점 없음 → score=25."""
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(), make_fundamentals(),
            make_supply_flow(), make_foreign_ownership(),
            make_kospi_disparity(),
            risk_result=make_risk_result(),
        )
        result = scorer.score(validated)
        assert result.risk_score == 25

    def test_risk_debt_penalty(self):
        """부채비율 240% → -5."""
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(), make_fundamentals(),
            make_supply_flow(), make_foreign_ownership(),
            make_kospi_disparity(),
            risk_result=make_risk_result(debt_ratio_pct=240.0),
        )
        result = scorer.score(validated)
        assert result.risk_score == 20   # 25 - 5

    def test_risk_debt_none_no_penalty(self):
        """debt_ratio_pct=None → 평가 제외, 감점 없음 (금융주 처리)."""
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(), make_fundamentals(),
            make_supply_flow(), make_foreign_ownership(),
            make_kospi_disparity(),
            risk_result=make_risk_result(debt_ratio_pct=None),
        )
        result = scorer.score(validated)
        assert result.risk_score == 25

    def test_risk_loss_danger_penalty(self):
        """3분기 적자 → loss_danger_penalty -7."""
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(), make_fundamentals(),
            make_supply_flow(), make_foreign_ownership(),
            make_kospi_disparity(),
            risk_result=make_risk_result(quarters_with_loss=3),
        )
        result = scorer.score(validated)
        assert result.risk_score == 18   # 25 - 7

    def test_risk_loss_warning_penalty(self):
        """2분기 적자 → loss_warning_penalty -4."""
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(), make_fundamentals(),
            make_supply_flow(), make_foreign_ownership(),
            make_kospi_disparity(),
            risk_result=make_risk_result(quarters_with_loss=2),
        )
        result = scorer.score(validated)
        assert result.risk_score == 21   # 25 - 4

    def test_risk_multiple_penalties_clamped(self):
        """모든 risk flag → 합산 -25 이상 → clamp(0)."""
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(), make_fundamentals(),
            make_supply_flow(), make_foreign_ownership(),
            make_kospi_disparity(),
            risk_result=make_risk_result(
                debt_ratio_pct=250.0,
                quarters_with_loss=3,
                dilution_pct=8.0,
                volatility_pct=70.0,
                governance_flag=True,
            ),
        )
        result = scorer.score(validated)
        # 25 - 5 - 7 - 5 - 3 - 5 = 0
        assert result.risk_score == 0

    def test_risk_governance_flag_penalty(self):
        """governance_flag=True → -5."""
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(), make_fundamentals(),
            make_supply_flow(), make_foreign_ownership(),
            make_kospi_disparity(),
            risk_result=make_risk_result(governance_flag=True),
        )
        result = scorer.score(validated)
        assert result.risk_score == 20   # 25 - 5


class TestEarningsScoring:
    """earnings 채점 정책 검증."""

    def test_earnings_missing_pipeline_survives(self):
        """earnings_result 미전달 → earnings_score=0, pipeline 정상 동작."""
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(), make_fundamentals(),
            make_supply_flow(), make_foreign_ownership(),
            make_kospi_disparity(),
        )
        result = scorer.score(validated)
        assert result.earnings_score == 0
        assert any("earnings unavailable" in n for n in result.notes)

    def test_earnings_unknown_quality_neutral(self):
        """quality=UNKNOWN, revision=UNKNOWN → adj 0 (coverage bias 방지)."""
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(), make_fundamentals(),
            make_supply_flow(), make_foreign_ownership(),
            make_kospi_disparity(),
            make_earnings_result(quarters_met=3, quality=EarningsQuality.UNKNOWN, revision=RevisionDirection.UNKNOWN),
        )
        result = scorer.score(validated)
        assert result.earnings_score == 20   # base(3회)=20, adj=0

    def test_earnings_revision_down_penalty(self):
        """revision=DOWN → -3 패널티."""
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(), make_fundamentals(),
            make_supply_flow(), make_foreign_ownership(),
            make_kospi_disparity(),
            make_earnings_result(quarters_met=3, revision=RevisionDirection.DOWN),
        )
        result = scorer.score(validated)
        assert result.earnings_score == 17   # base 20 - 3

    def test_earnings_large_miss_penalty(self):
        """large_miss_pct=-25 → -5 패널티."""
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(), make_fundamentals(),
            make_supply_flow(), make_foreign_ownership(),
            make_kospi_disparity(),
            make_earnings_result(quarters_met=3, large_miss_pct=-25.0),
        )
        result = scorer.score(validated)
        assert result.earnings_score == 15   # base 20 - 5

    def test_earnings_structural_bonus(self):
        """quality=STRUCTURAL + revision=UP → 최대값 clamp."""
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(), make_fundamentals(),
            make_supply_flow(), make_foreign_ownership(),
            make_kospi_disparity(),
            make_earnings_result(
                quarters_met=4,
                revision=RevisionDirection.UP,
                quality=EarningsQuality.STRUCTURAL,
            ),
        )
        result = scorer.score(validated)
        # base 22 + rev +3 + quality +3 = 28 → clamp → 25
        assert result.earnings_score == 25

    def test_earnings_fetch_failure_medium_confidence(self):
        """earnings API_TIMEOUT → earnings=None, MEDIUM confidence (일시적 오류)."""
        validated = validator.validate(
            TEST_TICKER, FIXED_TEST_DATE,
            make_market_cap(), make_fundamentals(),
            make_supply_flow(), make_foreign_ownership(),
            make_kospi_disparity(),
            FetchResult.failure(FetchErrorReason.API_TIMEOUT),
        )
        assert validated.confidence == Confidence.MEDIUM
        assert validated.earnings is None


class TestScoreMarket:
    """score_market 단위 검증."""

    def test_high_disparity_adds_heat(self):
        heat = score_market(
            kospi_disparity=make_kospi_disparity(disparity_pct=5.5).data,
            margin_loan=None,
            as_of_date=FIXED_TEST_DATE,
        )
        assert heat.heat_score == 2

    def test_negative_disparity_no_heat(self):
        """음수 이격도(과매도)는 과열 경보 0점."""
        heat = score_market(
            kospi_disparity=make_kospi_disparity(disparity_pct=-4.0).data,
            margin_loan=None,
            as_of_date=FIXED_TEST_DATE,
        )
        assert heat.heat_score == 0

    def test_no_data_fallback_cool(self):
        heat = score_market(
            kospi_disparity=None,
            margin_loan=None,
            as_of_date=FIXED_TEST_DATE,
        )
        assert heat.heat_score == 0
        assert heat.heat_level == HeatLevel.COOL
        assert "unavailable" in heat.detail

    def test_heat_level_consistent_with_score(self):
        """heat_level은 항상 heat_score에서 파생 — 불일치 시 ValueError."""
        for score in range(13):
            expected = _heat_level(score)
            heat = MarketHeat(
                heat_score=score,
                heat_level=expected,
                detail="test",
                as_of_date=FIXED_TEST_DATE,
            )
            assert heat.heat_level == expected
