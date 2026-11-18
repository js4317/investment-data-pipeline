from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

# 네트워크 기본 timeout — pykrx import 전에 설치 (analyzer/net.py)
from analyzer.net import install_default_http_timeout
install_default_http_timeout()

import config
from constants import DataSource
from backtests import snapshot as bt_snapshot
from analyzer.portfolio import loader as portfolio_loader
from analyzer.portfolio.rules import compute_advisory
from report import print_report, print_watchlist_update, print_delta_summary
from backtests.diff import compute_diff
from analyzer.data import krx, naver
from analyzer.data.models import (
    FetchResult, FetchErrorReason, Fundamentals,
    EarningsData, RiskData,
)
from analyzer.data.naver import fetch_quarterly_financials
from analyzer.data.krx import fetch_halt_info, fetch_risk_market_stats
from analyzer.rules.execution import ExecutionDecision, decide_execution
from analyzer.rules.market_regime import MarketState, get_market_state
from analyzer.scoring.models import ScoringResult
from analyzer.scoring.scorer import score
from analyzer.validation.models import ValidatedMarketData
from analyzer.validation.validator import validate
from analyzer.watchlist.store import load_all as _wl_load_all
from analyzer.watchlist.models import WatchlistState


@dataclass(frozen=True)
class AnalysisResult:
    scoring:   ScoringResult
    decision:  ExecutionDecision
    market:    MarketState
    validated: Optional[ValidatedMarketData] = None  # 원본 지표 접근용


# ---------------------------------------------------------------------------
# 내부 유틸
# ---------------------------------------------------------------------------

def _resolve_fundamentals(
    ticker: str,
    as_of_date: date,
    krx_result: FetchResult[Fundamentals],
) -> FetchResult[Fundamentals]:
    """
    krx PER이 None인 경우 Naver에서 PER/ROE 보완.
    PBR은 pykrx가 더 정확하므로 항상 krx 값 보존.
    krx 자체가 실패한 경우 naver로 완전 대체 시도.

    병합 시 source=PYKRX는 주 컨테이너 출처를 나타냄.
    실제 필드별 출처는 *_source 필드 참조 (per_source, pbr_source 등).
    Fundamentals.source만으로 "전체가 pykrx"라고 해석하지 말 것.

    fallback trace reporting — 병합/대체 발생 시 report에 반영 고려 (see TODO.md).
    """
    if krx_result.ok and krx_result.data.per is not None:
        # ROE가 없으면 quarterly에서 보완 시도
        if krx_result.data.roe is None:
            q = naver.fetch_quarterly_financials(ticker, as_of_date)
            if q.ok and q.data.get("roe") is not None:
                krx_data = krx_result.data
                supplemented = Fundamentals(
                    per=krx_data.per, pbr=krx_data.pbr,
                    div_yield=krx_data.div_yield,
                    roe=q.data["roe"],
                    per_source=krx_data.per_source,
                    pbr_source=krx_data.pbr_source,
                    div_yield_source=krx_data.div_yield_source,
                    roe_source=DataSource.NAVER,
                    source=krx_data.source,
                    as_of_date=as_of_date,
                )
                return FetchResult.success(supplemented)
        return krx_result

    naver_result = naver.fetch_fundamentals(ticker, as_of_date)
    if not naver_result.ok:
        return krx_result

    naver_data = naver_result.data

    if not krx_result.ok:
        return naver_result

    krx_data = krx_result.data
    # ROE: naver main 셀렉터 실패 시 quarterly 연간 API로 보완
    roe = naver_data.roe
    roe_source = naver_data.roe_source
    if roe is None:
        q = naver.fetch_quarterly_financials(ticker, as_of_date)
        if q.ok and q.data.get("roe") is not None:
            roe = q.data["roe"]
            roe_source = DataSource.NAVER

    merged = Fundamentals(
        per=naver_data.per,
        pbr=krx_data.pbr,
        div_yield=krx_data.div_yield if krx_data.div_yield is not None else naver_data.div_yield,
        roe=roe,
        per_source=DataSource.NAVER      if naver_data.per       is not None else None,
        pbr_source=DataSource.PYKRX      if krx_data.pbr         is not None else None,
        div_yield_source=(
            DataSource.PYKRX if krx_data.div_yield is not None
            else naver_data.div_yield_source
        ),
        roe_source=roe_source,
        source=DataSource.PYKRX,
        as_of_date=as_of_date,
    )
    return FetchResult.success(merged)


# ---------------------------------------------------------------------------
# earnings / risk 자동 수집
# ---------------------------------------------------------------------------

def _auto_earnings(ticker: str, as_of_date: date) -> Optional[EarningsData]:
    """
    분기별 영업이익 데이터로 EarningsData 자동 생성.

    데이터 소스 제한:
      - Naver mobile API: 최근 2~4분기 OP만 제공, 당기순이익 없음
      - YoY 비교 불가 (8Q 히스토리 없음) → OP > 0 분기 수로 대체
      - large_miss_pct, revision, quality: UNKNOWN

    OP > 0 proxy 한계:
      - Cyclical 바닥 (OP < 0 구조적 적자) 감지 가능
      - 수익 악화(YoY 감소)는 감지 불가 → scoring 보수적으로 읽을 것
    """
    result = fetch_quarterly_financials(ticker, as_of_date)
    if not result.ok:
        return None

    op = result.data.get("op_quarters", [])
    if len(op) < 2:  # 최소 2분기 이상이어야 의미 있음
        return None

    recent = op[-4:]  # 최대 4분기
    prev   = op[-8:-4] if len(op) >= 8 else None

    if prev is not None and len(prev) == 4:
        met = sum(
            1 for cur, pv in zip(recent, prev)
            if cur is not None and pv is not None and cur >= pv
        )
        large_miss: Optional[float] = None
        for cur, pv in zip(recent, prev):
            if cur is not None and pv is not None and pv > 0:
                pct = (cur - pv) / abs(pv) * 100
                if pct <= config.EARNINGS["large_miss_threshold_pct"]:
                    large_miss = pct if large_miss is None else min(large_miss, pct)
    else:
        # 히스토리 부족: OP > 0 분기 수 (4점 만점 기준으로 정규화)
        available = [v for v in recent if v is not None]
        if not available:
            return None
        positive = sum(1 for v in available if v > 0)
        # 보유 분기 수를 4분기 기준으로 정규화 (3분기 데이터에서 3개 흑자 → 4점)
        met = int(positive / len(available) * 4 + 0.5)  # round-half-up (avoid banker's rounding)
        large_miss = None

    return EarningsData(
        quarters_met_expectations=min(met, 4),
        large_miss_pct=large_miss,
        source=DataSource.NAVER,
        as_of_date=as_of_date,
    )


def _auto_risk(ticker: str, as_of_date: date) -> Optional[RiskData]:
    """
    KRX 가격 데이터 + Naver 재무 데이터로 RiskData 자동 생성.

    volatility / dilution: pykrx (신뢰도 높음)
    quarters_with_loss / debt_ratio_pct: Naver quarterly 스크래핑
    governance_flag: 수동 입력 전용 — 항상 False

    volatility, dilution, quarterly 셋 다 없으면 None 반환.
    """
    quarterly = fetch_quarterly_financials(ticker, as_of_date)
    volatility, dilution = fetch_risk_market_stats(ticker, as_of_date)
    halt_days, halted_on_session = fetch_halt_info(ticker, as_of_date)

    if volatility is None and dilution is None and not quarterly.ok:
        return None

    quarters_with_loss = 0
    debt_ratio:           Optional[float] = None
    earnings_quality_ratio: Optional[float] = None

    if quarterly.ok:
        ni_q = quarterly.data.get("ni_quarters", [])
        op_q = quarterly.data.get("op_quarters", [])
        quarters_with_loss = sum(1 for v in ni_q[-4:] if v is not None and v < 0)
        debt_ratio = quarterly.data.get("debt_ratio")

        # 영업이익 신뢰도 = trailing 4Q 영업이익 합 / trailing 4Q 당기순이익 합
        # ni > 0 일 때만 계산 (적자 구간은 기존 loss_quarters 패널티로 처리)
        annual_op = sum(v for v in op_q[-4:] if v is not None)
        annual_ni = sum(v for v in ni_q[-4:] if v is not None)
        op_has_data = any(v is not None for v in op_q[-4:])
        ni_has_data = any(v is not None for v in ni_q[-4:])
        if op_has_data and ni_has_data and annual_ni > 0:
            earnings_quality_ratio = annual_op / annual_ni

    return RiskData(
        quarters_with_loss=quarters_with_loss,
        debt_ratio_pct=debt_ratio,
        dilution_pct=dilution,
        volatility_pct=volatility,
        governance_flag=False,
        earnings_quality_ratio=earnings_quality_ratio,
        halt_days=halt_days,
        halted_on_session=halted_on_session,
        # volatility/dilution=pykrx, debt/loss=naver 혼합 — 필드별 source 분리 고려 (see TODO.md)
        source=DataSource.PYKRX if volatility is not None else DataSource.NAVER,
        as_of_date=as_of_date,
    )


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

def analyze_ticker(
    ticker: str,
    as_of_date: date,
    market_state: MarketState,
    earnings: Optional[EarningsData] = None,
    risk: Optional[RiskData] = None,
) -> AnalysisResult:
    if earnings is None:
        earnings = _auto_earnings(ticker, as_of_date)
    if risk is None:
        risk = _auto_risk(ticker, as_of_date)

    _fetch_tasks = {
        "market_cap":        lambda: krx.fetch_market_cap(ticker, as_of_date),
        "supply_flow":       lambda: krx.fetch_supply_flow(ticker, as_of_date),
        "foreign_ownership": lambda: krx.fetch_foreign_ownership(ticker, as_of_date),
        "kospi_disparity":   lambda: krx.fetch_kospi_disparity(as_of_date),
        "fundamentals":      lambda: _resolve_fundamentals(
            ticker, as_of_date, krx.fetch_fundamentals(ticker, as_of_date)
        ),
    }
    with ThreadPoolExecutor(max_workers=4) as _pool:  # intentional throttle: 5 tasks on 4 workers
        _futures = {_pool.submit(fn): name for name, fn in _fetch_tasks.items()}
        _results: dict = {}
        for _f in as_completed(_futures):
            _name = _futures[_f]
            try:
                _results[_name] = _f.result()
            except Exception:
                # orchestration boundary: fetch exceptions collapse into API_TIMEOUT
                _results[_name] = FetchResult.failure(FetchErrorReason.API_TIMEOUT)

    market_cap_result        = _results["market_cap"]
    supply_flow_result       = _results["supply_flow"]
    foreign_ownership_result = _results["foreign_ownership"]
    kospi_disparity_result   = _results["kospi_disparity"]
    fundamentals_result      = _results["fundamentals"]

    earnings_result: Optional[FetchResult[EarningsData]] = (
        FetchResult.success(earnings) if earnings is not None else None
    )
    risk_result: Optional[FetchResult[RiskData]] = (
        FetchResult.success(risk) if risk is not None else None
    )

    validated = validate(
        ticker=ticker,
        as_of_date=as_of_date,
        market_cap_result=market_cap_result,
        fundamentals_result=fundamentals_result,
        supply_flow_result=supply_flow_result,
        foreign_ownership_result=foreign_ownership_result,
        kospi_disparity_result=kospi_disparity_result,
        earnings_result=earnings_result,
        risk_result=risk_result,
    )

    scoring  = score(validated)
    decision = decide_execution(scoring.grade, market_state, scoring.confidence)

    return AnalysisResult(scoring=scoring, decision=decision, market=market_state,
                          validated=validated)


def analyze_watchlist(
    tickers: list[str],
    as_of_date: date,
    earnings_map: Optional[dict[str, EarningsData]] = None,
    risk_map: Optional[dict[str, RiskData]] = None,
) -> list[AnalysisResult]:
    """
    종목 리스트 분석. market_state는 1회 계산 후 전 종목 공유.

    earnings_map / risk_map: ticker → 데이터 매핑.
                             없는 종목은 None 처리 (partial scoring).
    """
    market_state = get_market_state(as_of_date)
    em = earnings_map or {}
    rm = risk_map or {}
    unique_tickers = list(dict.fromkeys(tickers))
    return [
        analyze_ticker(t, as_of_date, market_state, em.get(t), rm.get(t))
        for t in unique_tickers
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_date(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"날짜 형식 오류: '{s}' (YYYY-MM-DD 필요)")


def main() -> None:
    config.validate_config()

    parser = argparse.ArgumentParser(
        description=f"투자 분석 프레임워크 {config.RULE_VERSION_STR}"
    )
    parser.add_argument(
        "--ticker",
        metavar="CODE[,CODE,...]",
        default=None,
        help="종목코드 쉼표 구분 (예: 005930,035720). 생략 시 watchlist ACTIVE/REVIEW 전체 실행",
    )
    parser.add_argument(
        "--date", dest="as_of_date", type=_parse_date,
        default=date.today(),
        metavar="YYYY-MM-DD",
        help="기준일 (기본: 오늘)",
    )
    parser.add_argument(
        "--watchlist", action="store_true",
        help="분석 결과를 watchlist DB에 저장하고 상태 변화를 출력",
    )
    parser.add_argument(
        "--snapshot", action="store_true",
        help="분석 결과를 backtests/snapshots/YYYY-MM-DD.json에 저장",
    )
    args = parser.parse_args()

    if args.ticker:
        tickers = [t.strip() for t in args.ticker.split(",") if t.strip()]
    else:
        items   = _wl_load_all()
        tickers = [
            item.ticker for item in items
            if item.state in (WatchlistState.ACTIVE, WatchlistState.REVIEW)
        ]
        if not tickers:
            print("watchlist에 ACTIVE/REVIEW 종목 없음. --ticker로 종목 지정 필요.")
            sys.exit(0)

    results  = analyze_watchlist(tickers, args.as_of_date)
    portfolio = portfolio_loader.load()
    advisories = (
        {r.scoring.ticker: adv
         for r in results
         if (adv := compute_advisory(r, portfolio)) is not None}
        if portfolio else None
    )
    print_report(results, advisories)

    if args.watchlist:
        print_watchlist_update(
            {r.scoring.ticker: r for r in results},
            args.as_of_date,
        )

    if args.snapshot:
        from analyzer.data.momentum import batch_fetch_momentum
        mom_inputs = [(r.scoring.ticker, r.scoring.normalized_score) for r in results]
        mom_map    = batch_fetch_momentum(mom_inputs, args.as_of_date)
        path = bt_snapshot.save(results, args.as_of_date, mom_map=mom_map)
        print(f"  📸 Snapshot saved → {path}")
        dates      = bt_snapshot.list_snapshot_dates()
        prev_dates = [d for d in dates if d < args.as_of_date]
        if prev_dates:
            prev_snap = bt_snapshot.load_full(prev_dates[-1])
            curr_snap = bt_snapshot.load_full(args.as_of_date)
            if prev_snap and curr_snap:
                print_delta_summary(compute_diff(prev_snap, curr_snap))

    if all(not r.decision.executable for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
