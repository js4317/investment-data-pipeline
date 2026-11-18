from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

_COMPONENT_KEYS = ("valuation_score", "earnings_score", "risk_score")

_COMPONENT_LABEL: dict[str, str] = {
    "valuation_score": "밸류",
    "earnings_score":  "실적",
    "risk_score":      "리스크",
}

_FLAT_THRESHOLD = 2   # 이 범위 내 변화는 FLAT으로 처리


@dataclass(frozen=True)
class ScoreDelta:
    ticker:           str
    from_date:        date
    to_date:          date
    from_score:       int
    to_score:         int
    score_delta:      int     # to − from (양수=상승, 음수=하락)
    from_grade:       str
    to_grade:         str
    grade_changed:    bool
    component_deltas: dict[str, int]   # e.g. {"supply_score": -8, "earnings_score": +2}

    @property
    def direction(self) -> str:
        if self.score_delta > _FLAT_THRESHOLD:   return "UP"
        if self.score_delta < -_FLAT_THRESHOLD:  return "DOWN"
        return "FLAT"

    @property
    def direction_emoji(self) -> str:
        return {"UP": "▲", "DOWN": "▼", "FLAT": "━"}[self.direction]

    def explain(self, top_n: int = 2) -> str:
        """변화 원인 요약 문자열. 가장 큰 컴포넌트 변화 top_n개 + 등급 변화."""
        changed = {k: v for k, v in self.component_deltas.items() if v != 0}
        if not changed and not self.grade_changed:
            return "변화 없음"
        parts: list[str] = []
        for k, v in sorted(changed.items(), key=lambda x: abs(x[1]), reverse=True)[:top_n]:
            arrow = "▲" if v > 0 else "▼"
            parts.append(f"{_COMPONENT_LABEL.get(k, k)} {arrow}{abs(v)}")
        result = " / ".join(parts) if parts else "점수 동일"
        if self.grade_changed:
            result += f"  [{self.from_grade.upper()} → {self.to_grade.upper()}]"
        return result


def _find_comparison_date(
    snapshot_dates: list[date],
    newest:         date,
    lookback_days:  int,
) -> date:
    """lookback_days 이전에 해당하는 스냅샷 날짜 반환. 없으면 직전 스냅샷."""
    cutoff = newest - timedelta(days=lookback_days)
    for d in reversed(snapshot_dates[:-1]):
        if d <= cutoff:
            return d
    return snapshot_dates[-2]


def compute_delta(
    ticker:         str,
    snapshot_dates: list[date],
    lookback_days:  int = 7,
) -> Optional[ScoreDelta]:
    """
    최신 스냅샷과 lookback_days 이내 직전 스냅샷을 비교해 ScoreDelta 반환.
    스냅샷이 2개 미만이거나 ticker가 두 스냅샷 중 하나에 없으면 None.
    """
    from backtests.snapshot import load

    if len(snapshot_dates) < 2:
        return None

    newest_date   = snapshot_dates[-1]
    from_date     = _find_comparison_date(snapshot_dates, newest_date, lookback_days)

    new_map = {e["ticker"]: e for e in load(newest_date)}
    old_map = {e["ticker"]: e for e in load(from_date)}

    new_e = new_map.get(ticker)
    old_e = old_map.get(ticker)
    if new_e is None or old_e is None:
        return None

    component_deltas = {
        k: (new_e.get(k) or 0) - (old_e.get(k) or 0)
        for k in _COMPONENT_KEYS
    }
    from_grade = old_e.get("grade", "?")
    to_grade   = new_e.get("grade", "?")

    return ScoreDelta(
        ticker           = ticker,
        from_date        = from_date,
        to_date          = newest_date,
        from_score       = old_e.get("normalized_score", 0),
        to_score         = new_e.get("normalized_score", 0),
        score_delta      = new_e.get("normalized_score", 0) - old_e.get("normalized_score", 0),
        from_grade       = from_grade,
        to_grade         = to_grade,
        grade_changed    = from_grade != to_grade,
        component_deltas = component_deltas,
    )


def batch_compute_deltas(
    tickers:        list[str],
    snapshot_dates: list[date],
    lookback_days:  int = 7,
) -> dict[str, ScoreDelta]:
    """여러 종목 델타를 한 번에 계산. 스냅샷 로드를 2회로 최소화."""
    from backtests.snapshot import load

    if len(snapshot_dates) < 2:
        return {}

    newest_date = snapshot_dates[-1]
    from_date   = _find_comparison_date(snapshot_dates, newest_date, lookback_days)

    new_map = {e["ticker"]: e for e in load(newest_date)}
    old_map = {e["ticker"]: e for e in load(from_date)}

    result: dict[str, ScoreDelta] = {}
    for ticker in tickers:
        new_e = new_map.get(ticker)
        old_e = old_map.get(ticker)
        if new_e is None or old_e is None:
            continue

        component_deltas = {
            k: (new_e.get(k) or 0) - (old_e.get(k) or 0)
            for k in _COMPONENT_KEYS
        }
        from_grade = old_e.get("grade", "?")
        to_grade   = new_e.get("grade", "?")

        result[ticker] = ScoreDelta(
            ticker           = ticker,
            from_date        = from_date,
            to_date          = newest_date,
            from_score       = old_e.get("normalized_score", 0),
            to_score         = new_e.get("normalized_score", 0),
            score_delta      = new_e.get("normalized_score", 0) - old_e.get("normalized_score", 0),
            from_grade       = from_grade,
            to_grade         = to_grade,
            grade_changed    = from_grade != to_grade,
            component_deltas = component_deltas,
        )
    return result
