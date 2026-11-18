from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from constants import Grade

_SCORE_DROP_THRESHOLD = 15
_EARNINGS_WEAK_THRESH = 15


@dataclass(frozen=True)
class PositionCheckItem:
    label:  str
    tier:   str   # "critical" | "context"
    state:  str   # "alert" | "info" | "ok" | "unknown"
    detail: str


@dataclass(frozen=True)
class PositionStatus:
    """
    포지션 상태 진단.

    "팔아라" 판정이 아니라 "무슨 변화가 생겼는가" 를 두 계층으로 분리:
    - critical_items : 엔진이 자동 판단하는 구조적 변화 (점수 하락, 등급 악화)
    - context_items  : 변화의 맥락 (수급, 실적, 모멘텀, Risk Radar)

    verdict:
      RED    = critical ≥ 2 → 매도 검토
      YELLOW = critical = 1 → 주의
      WATCH  = critical = 0, context_alert ≥ 2 → 관찰
      GREEN  = 이상 없음
    """
    ticker:         str
    critical_items: tuple[PositionCheckItem, ...]
    context_items:  tuple[PositionCheckItem, ...]

    @property
    def critical_alerts(self) -> int:
        return sum(1 for i in self.critical_items if i.state == "alert")

    @property
    def context_alerts(self) -> int:
        return sum(1 for i in self.context_items if i.state == "alert")

    @property
    def verdict(self) -> str:
        ca = self.critical_alerts
        if ca >= 2:
            return "RED"
        if ca >= 1:
            return "YELLOW"
        if self.context_alerts >= 2:
            return "WATCH"
        return "GREEN"

    @property
    def verdict_emoji(self) -> str:
        return {"GREEN": "🟢", "WATCH": "🟡", "YELLOW": "🟡", "RED": "🔴"}[self.verdict]

    @property
    def verdict_label(self) -> str:
        return {"GREEN": "유지", "WATCH": "관찰", "YELLOW": "주의", "RED": "매도 검토"}[self.verdict]


def _fmt_eok(won: int) -> str:
    eok = won / 1e8
    sign = "+" if eok >= 0 else ""
    return f"{sign}{eok:.1f}억"


def _peak_score_from_snapshots(ticker: str, snapshot_dates: list[date]) -> Optional[int]:
    from backtests import snapshot as bt_snapshot
    peak: Optional[int] = None
    for d in snapshot_dates:
        for e in bt_snapshot.load(d):
            if e["ticker"] == ticker:
                s = e.get("normalized_score")
                if s is not None and (peak is None or s > peak):
                    peak = s
                break
    return peak


def _supply_item(snap_entry: dict) -> PositionCheckItem:
    fn  = snap_entry.get("foreign_net")
    ins = snap_entry.get("institution_net")

    if fn is None or ins is None:
        return PositionCheckItem("👋 수급", "context", "unknown", "수급 데이터 없음")

    f_pos = fn > 0
    i_pos = ins > 0
    raw   = f"외인 {_fmt_eok(fn)} / 기관 {_fmt_eok(ins)}"

    if f_pos and i_pos:
        return PositionCheckItem("👋 쌍끌이 매수",      "context", "ok",    raw)
    if f_pos and not i_pos:
        return PositionCheckItem("👋 외국인 단독 매수", "context", "info",  raw)
    if not f_pos and i_pos:
        return PositionCheckItem("👋 기관 단독 매수",   "context", "info",  raw)
    return PositionCheckItem(    "👋 동반 순매도",      "context", "alert", raw)


def _risk_radar_item(ticker: str) -> PositionCheckItem:
    try:
        from analyzer.risk_radar import read_cache
        cached = read_cache(ticker)
        if cached is None:
            return PositionCheckItem("🚨 Risk Radar", "context", "unknown", "미실행")
        overall = cached.get("overall", "?")
        conf    = cached.get("confidence", "?")
        if overall == "HIGH":
            return PositionCheckItem("🚨 Risk Radar", "context", "alert", f"HIGH — 신뢰도 {conf}")
        return PositionCheckItem("🚨 Risk Radar", "context", "ok", f"{overall} — 신뢰도 {conf}")
    except Exception:
        return PositionCheckItem("🚨 Risk Radar", "context", "unknown", "조회 실패")


def build_position_status(
    ticker:         str,
    snap_entry:     dict,
    snapshot_dates: list[date],
) -> PositionStatus:
    """
    snap_entry      : snap_map[ticker] (최신 스냅샷 엔트리)
    snapshot_dates  : list_snapshot_dates() 결과 — 점수 이력 피크 계산용
    """
    critical: list[PositionCheckItem] = []
    context:  list[PositionCheckItem] = []

    # ── Critical 1: 점수 하락 ────────────────────────────────────────────
    current = snap_entry.get("normalized_score", 0)
    peak    = _peak_score_from_snapshots(ticker, snapshot_dates)
    if peak is not None:
        drop = peak - current
        if drop >= _SCORE_DROP_THRESHOLD:
            critical.append(PositionCheckItem(
                "📉 점수 하락", "critical", "alert",
                f"이력 최고 {peak}점 → 현재 {current}점 (−{drop}점)",
            ))
        else:
            critical.append(PositionCheckItem(
                "📉 점수 유지", "critical", "ok",
                f"이력 최고 {peak}점 → 현재 {current}점",
            ))
    else:
        critical.append(PositionCheckItem("📉 점수", "critical", "unknown", "이력 데이터 없음"))

    # ── Context: 등급 변화 (Context only — Supply-driven grade flip은 alpha 기여 미확인)
    # Calibration v8.3.x: Supply spread +0.31% → 등급 변화를 즉시 매도 근거로 쓰지 않음.
    # 별도 검증된 조건이 생기기 전까지 Context로 유지.
    grade = Grade(snap_entry.get("grade", "avoid"))
    if grade in (Grade.NEUTRAL, Grade.AVOID):
        context.append(PositionCheckItem(
            "⬇️ 등급 악화", "context", "alert",
            f"현재 등급: {grade.value.upper()} — 매도 판단은 Valuation/Risk 변화 기준으로 별도 확인",
        ))
    else:
        context.append(PositionCheckItem(
            "⬇️ 등급 유지", "context", "ok",
            f"현재 등급: {grade.value.upper()}",
        ))

    # ── Context 1: 수급 (4상태 + raw 금액) ──────────────────────────────
    context.append(_supply_item(snap_entry))

    # ── Context 2: 실적 ──────────────────────────────────────────────────
    earn = snap_entry.get("earnings_score", 0)
    if earn < _EARNINGS_WEAK_THRESH:
        context.append(PositionCheckItem("📊 실적 약화", "context", "alert", f"실적 점수 {earn}/25"))
    else:
        context.append(PositionCheckItem("📊 실적 정상", "context", "ok",    f"실적 점수 {earn}/25"))

    # ── Context 3: 모멘텀 ────────────────────────────────────────────────
    mom_signal = snap_entry.get("mom_signal")
    if mom_signal == "negative":
        supply = snap_entry.get("supply_score", 0)
        val    = snap_entry.get("valuation_score", 0)
        detail = "시장 외면 중 (고밸류+하락)" if (val >= 18 and supply < 5) else "NEGATIVE"
        context.append(PositionCheckItem("⚠️ 모멘텀 부정", "context", "alert", detail))
    elif mom_signal in (None, "unavailable"):
        context.append(PositionCheckItem("⚠️ 모멘텀", "context", "unknown", "데이터 없음"))
    else:
        context.append(PositionCheckItem("⚠️ 모멘텀", "context", "ok", mom_signal.upper()))

    # ── Context 4: Risk Radar (HIGH → alert, 미실행 → unknown) ──────────
    context.append(_risk_radar_item(ticker))

    return PositionStatus(
        ticker=ticker,
        critical_items=tuple(critical),
        context_items=tuple(context),
    )


build_sell_checklist = build_position_status
