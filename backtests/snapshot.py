from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from constants import ENGINE_VERSION

if TYPE_CHECKING:
    from run import AnalysisResult

_SNAPSHOT_DIR = Path(__file__).parent / "snapshots"

# 스냅샷 필드 구조가 바뀔 때마다 올린다. rule_version(엔진 로직)과 독립.
# 1: 초기 구조 (기관 세부 없음, market_cap 없음)
# 2: 기관 세부(pension/투신/금융투자/사모/보험) + market_cap + trading_value + 방향 플래그
# 3: 밸류에이션 raw(per/pbr/roe/div_yield) + foreign_ownership_pct + n_total/universe 헤더
# 4: 업종 상대평가 연구 필드 — sector(KRX업종명) + val_score_abs(절대PER 원본점수)
# 5: EQ 연구 필드 — eq_band(GOOD/WARNING/POOR/VERY_POOR) + eq_penalty(감점 크기)
# 6: Price Context — w52_high/low/position(52주 가격 위치) + acml_vol + vi_active
# 7: Risk/Earnings raw — risk_debt_ratio_pct/quarters_with_loss/dilution_pct/volatility_pct/
#                        earnings_quality_ratio + earnings_quarters_met/large_miss_pct/revision
# 8: Earnings Regime 연구 필드 — earnings_ratio/regime/history_n/confidence/source_years
#    (점수 무관, CYCLICAL-1 forward validation 전용)
SNAPSHOT_SCHEMA_VERSION = 8

# Schema 버전별 기능 게이트 — 조건 분기 시 매직 넘버 대신 이 상수를 사용
SCHEMA_HAS_VALUATION_RAW       = 3   # per/pbr/roe/div_yield + foreign_ownership_pct
SCHEMA_HAS_SECTOR_RELATIVE     = 4   # sector + val_score_abs (Hybrid 연구용)
SCHEMA_HAS_EQ_BAND             = 5   # eq_band + eq_penalty (Earnings Quality 연구용)
SCHEMA_HAS_PRICE_CONTEXT       = 6   # w52_high/low/position + acml_vol + vi_active
SCHEMA_HAS_RISK_EARNINGS_RAW   = 7   # risk_*/earnings_* 원본값 (Calibration 연구용)
SCHEMA_HAS_EARNINGS_REGIME     = 8   # earnings_ratio/regime/history_n/confidence/source_years (CYCLICAL-1 연구용)




def _result_to_dict(
    result: AnalysisResult,
    mom=None,
    sector_map: dict | None = None,
    price_context: dict | None = None,
    earnings_regime: dict | None = None,
) -> dict:
    s  = result.scoring
    d  = result.decision
    v  = result.validated
    sf = v.supply_flow       if v else None
    mc = v.market_cap        if v else None
    f  = v.fundamentals      if v else None
    fo = v.foreign_ownership if v else None
    r  = v.risk              if v else None
    e  = v.earnings          if v else None

    entry: dict = {
        "ticker":                    s.ticker,
        "normalized_score":          s.normalized_score,
        "total_score":               s.total_score,
        "available_max":             s.available_max,
        "grade":                     s.grade.value,
        "confidence":                s.confidence.value,
        "notes":                     list(s.notes),
        "executable":                d.executable,
        "valuation_score":           s.valuation_score,
        "earnings_score":            s.earnings_score,
        "risk_score":                s.risk_score,
        "supply_score":              s.supply_score,
        # 밸류에이션 raw — 팩터 분석용. 과거 재조회 불가, 소급 복원 불가.
        "per":                       f.per       if f else None,
        "pbr":                       f.pbr       if f else None,
        "roe":                       f.roe       if f else None,
        "div_yield":                 f.div_yield if f else None,
        # 외인 지분율 — 방향 플래그(foreign_positive) 외에 절댓값
        "foreign_ownership_pct":     fo.ownership_pct if fo else None,
        # 시총·거래대금 — 비율 계산 분모 후보. 소급 복원 불가.
        "market_cap":                mc.market_cap    if mc else None,
        "trading_value":             mc.trading_value if mc else None,
        # 수급 raw (원 단위)
        "supply_lookback_days":      sf.lookback_days            if sf else None,
        "foreign_net":               sf.foreign_net_buy          if sf else None,
        "institution_net":           sf.institution_net_buy      if sf else None,
        "pension_net":               sf.pension_net              if sf else None,
        "investment_trust_net":      sf.investment_trust_net     if sf else None,
        "financial_investment_net":  sf.financial_investment_net if sf else None,
        "private_fund_net":          sf.private_fund_net         if sf else None,
        "insurance_net":             sf.insurance_net            if sf else None,
        # 방향 플래그 — 외인+연기금 동시 매수 등 groupby 분석용
        "foreign_positive":          (sf.foreign_net_buy > 0)     if sf and sf.foreign_net_buy     is not None else None,
        "institution_positive":      (sf.institution_net_buy > 0) if sf and sf.institution_net_buy is not None else None,
        "pension_positive":          (sf.pension_net > 0)         if sf and sf.pension_net         is not None else None,
        # 업종 상대평가 연구 필드 (Schema 4)
        # val_score      = Hybrid (= valuation_score, 위에서 이미 저장됨)
        # val_score_abs  = 절대 PER 기준 원본
        # val_score_rel  = 업종 상대 PER 기준 원본
        # sector_adjustment = Hybrid - Absolute (보정 크기, 음수=하향, 양수=상향)
        # peer_count     = 버킷 내 표본 수 (낮으면 상대평가 신뢰도 낮음)
        "sector":                    (sector_map or {}).get(s.ticker),
        "val_score_abs":             s.val_score_abs if s.val_score_abs else s.valuation_score,
        "val_score_rel":             s.val_score_rel,
        "sector_adjustment":         s.valuation_score - (s.val_score_abs if s.val_score_abs else s.valuation_score),
        "peer_count":                s.sector_peer_count,
        # EQ 연구 필드 (Schema 5)
        "eq_band":                   s.eq_band or None,    # "" → None (미분류 구분)
        "eq_penalty":                s.eq_penalty or None, # 0  → None (패널티 없는 경우 생략)
        # Price Context 연구 필드 (Schema 6) — KIS 모드에서만 채워짐
        # w52_position: 0(52주 저점)~100(52주 고점). "어느 가격 위치에서 신호가 좋은가?" 분석용.
        "w52_high":      (price_context or {}).get("w52_high"),
        "w52_low":       (price_context or {}).get("w52_low"),
        "w52_position":  (price_context or {}).get("w52_position"),
        "acml_vol":      (price_context or {}).get("acml_vol"),
        # vi_active/vi_code: 모닝런이 장 전에 돌면 항상 'N' → 저장 불필요. Ticker 페이지 실시간 표시만 유지.
        # Risk raw — 점수 재계산 금지. Calibration 연구용 원본값만.
        "risk_debt_ratio_pct":       r.debt_ratio_pct         if r else None,
        "risk_quarters_with_loss":   r.quarters_with_loss     if r else None,
        "risk_dilution_pct":         r.dilution_pct           if r else None,
        "risk_volatility_pct":       r.volatility_pct         if r else None,
        "risk_earnings_quality_ratio": r.earnings_quality_ratio if r else None,
        # Earnings raw — 점수 재계산 금지. Calibration 연구용 원본값만.
        "earnings_quarters_met":     e.quarters_met_expectations       if e else None,
        "earnings_large_miss_pct":   e.large_miss_pct                  if e else None,
        "earnings_revision":         e.revision.value                   if e else None,
        # 모멘텀
        "mom_signal":                None,
        "mom_rel_1m":                None,
        "mom_rel_3m":                None,
        "mom_rel_6m":                None,
        "mom_strength":              None,
        # Earnings Regime 연구 필드 (Schema 8) — 점수 무관, CYCLICAL-1 forward validation 전용
        # earnings_ratio: 당해 EPS / 정상화 EPS (5Y or 3Y 중앙값). 1.5 초과 = PEAK.
        # earnings_regime: NORMAL / ELEVATED / PEAK / INSUFFICIENT_DATA
        # earnings_history_n: 정상화 EPS 계산에 사용된 유효 연도 수
        # earnings_confidence: HIGH(≥5년) / LOW(3~4년) / INSUFFICIENT(<3년)
        # earnings_source_years: 실제 사용된 연도 목록 (예: [2020,2021,2022,2023,2024])
        "earnings_ratio":            (earnings_regime or {}).get("earnings_ratio"),
        "earnings_regime":           (earnings_regime or {}).get("earnings_regime"),
        "earnings_history_n":        (earnings_regime or {}).get("earnings_history_n"),
        "earnings_confidence":       (earnings_regime or {}).get("earnings_confidence"),
        "earnings_source_years":     (earnings_regime or {}).get("earnings_source_years"),
    }
    if mom is not None:
        entry["mom_signal"]   = mom.signal.value
        entry["mom_rel_1m"]   = mom.rel_1m
        entry["mom_rel_3m"]   = mom.rel_3m
        entry["mom_rel_6m"]   = mom.rel_6m
        entry["mom_strength"] = round(mom.strength, 1)
    return entry


def _prev_weekday(d: date) -> date:
    """전 거래일 근사값. 공휴일 미처리 — 알파 분석 시 KRX 캘린더로 재검증."""
    delta = 3 if d.weekday() == 0 else 1   # 월요일 → 금요일
    return d - timedelta(days=delta)


def _entry_meta(as_of_date: date, now: datetime) -> tuple[str, str]:
    """
    실행 시각 기준으로 entry_basis / market_data_date를 결정한다.

    16:00 이전: pykrx = 전일 종가 → entry = 당일 시가 (same_day_open)
    16:00 이후: pykrx = 당일 종가 → entry = 익일 시가 (next_day_open)
    """
    if now.hour >= 16:
        return "next_day_open", as_of_date.isoformat()
    return "same_day_open", _prev_weekday(as_of_date).isoformat()


def _fetch_kospi_close(as_of_date: date) -> float | None:
    try:
        from analyzer.data.krx import fetch_kospi_disparity
        r = fetch_kospi_disparity(as_of_date)
        return float(r.data.current) if r.ok else None
    except Exception:
        return None



def save(
    results: list[AnalysisResult],
    as_of_date: date,
    mom_map: dict | None = None,
    universe: str | None = None,
    universe_label: str | None = None,
    sector_map: dict[str, str] | None = None,
    price_context_map: dict[str, dict] | None = None,
    earnings_regime_map: dict[str, dict] | None = None,
) -> Path:
    """
    결과를 backtests/snapshots/YYYY-MM-DD.json에 저장.

    같은 날짜 파일이 존재하면 ticker 단위로 병합 (기존 ticker 업데이트, 신규 추가).
    수익률·성과 계산 없음 — 판단 기록 전용.
    kospi_close: 알파(종목 수익률 − KOSPI 수익률) 계산을 위한 기준선.

    universe:       안정적 식별자 (예: "KOSPI_500B"). 통계 groupby 기준.
    universe_label: 표시용 문자열 (예: "KOSPI 5000억+"). 없으면 universe로 대체.
    n_total:        병합 후 실제 entries 수. 같은 날 두 번 저장해도 entries와 일치 보장.
    """
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = _SNAPSHOT_DIR / f"{as_of_date.isoformat()}.json"

    existing_meta: dict = {}
    existing: dict[str, dict] = {}
    if path.exists():
        raw = json.loads(path.read_text())
        existing_meta = {k: v for k, v in raw.items() if k != "entries"}
        existing = {entry["ticker"]: entry for entry in raw["entries"]}

    for result in results:
        mom = mom_map.get(result.scoring.ticker) if mom_map else None
        pc  = (price_context_map or {}).get(result.scoring.ticker)
        er  = (earnings_regime_map or {}).get(result.scoring.ticker)
        entry = _result_to_dict(result, mom, sector_map=sector_map, price_context=pc, earnings_regime=er)
        # 기존 모멘텀 보존: 새 entry에 모멘텀 없고 기존 데이터 있으면 유지
        prev = existing.get(entry["ticker"])
        if prev and entry.get("mom_signal") is None and prev.get("mom_signal") is not None:
            for _k in ("mom_signal", "mom_strength", "mom_rel_1m", "mom_rel_3m", "mom_rel_6m"):
                entry[_k] = prev[_k]
        existing[entry["ticker"]] = entry

    kospi_close    = existing_meta.get("kospi_close") or _fetch_kospi_close(as_of_date)
    merged_entries = sorted(existing.values(), key=lambda x: x["ticker"])

    now = datetime.now()
    if not existing_meta.get("analysis_timestamp"):
        entry_basis, market_data_date = _entry_meta(as_of_date, now)
    else:
        entry_basis      = existing_meta.get("entry_basis", "")
        market_data_date = existing_meta.get("market_data_date", "")

    snapshot = {
        "date":               as_of_date.isoformat(),
        "analysis_timestamp": existing_meta.get("analysis_timestamp") or now.isoformat(timespec="seconds"),
        "entry_basis":        entry_basis,
        "market_data_date":   market_data_date,
        "rule_version":       ENGINE_VERSION,
        "snapshot_schema":    SNAPSHOT_SCHEMA_VERSION,
        "kospi_close":        kospi_close,
        "n_total":            len(merged_entries),
        "universe":           universe       or existing_meta.get("universe"),
        "universe_label":     universe_label or existing_meta.get("universe_label") or universe,
        "entries":            merged_entries,
    }
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return path


def load(as_of_date: date) -> list[dict]:
    path = _SNAPSHOT_DIR / f"{as_of_date.isoformat()}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())["entries"]


def load_full(as_of_date: date) -> dict:
    """date, rule_version, entries 포함한 전체 스냅샷 반환. 파일 없으면 빈 dict."""
    path = _SNAPSHOT_DIR / f"{as_of_date.isoformat()}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def list_snapshot_dates() -> list[date]:
    if not _SNAPSHOT_DIR.exists():
        return []
    return sorted(
        date.fromisoformat(p.stem)
        for p in _SNAPSHOT_DIR.glob("*.json")
    )


def get_neutral_streak(ticker: str, n: int = 3) -> int:
    """
    최근 n개 스냅샷에서 ticker의 NEUTRAL 연속 횟수를 반환.

    - 스냅샷에 ticker가 없으면 연속 중단.
    - 호출자가 n을 제어. 기본값은 천이 기준 N=3.
    """
    dates = list_snapshot_dates()
    streak = 0
    for snap_date in reversed(dates[-n:]):
        entries = load(snap_date)
        entry   = next((e for e in entries if e.get("ticker") == ticker), None)
        if entry is None:
            break
        if entry.get("grade") == "neutral":
            streak += 1
        else:
            break
    return streak
