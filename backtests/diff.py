from __future__ import annotations

from dataclasses import dataclass, field

from constants import Grade

_GRADE_ORDER     = [Grade.AVOID, Grade.NEUTRAL, Grade.BUY, Grade.STRONG_BUY]
SCORE_THRESHOLD  = 5


@dataclass
class SnapshotDiff:
    grade_changes:   list[tuple[str, dict, dict]] = field(default_factory=list)
    score_changes:   list[tuple[str, dict, dict]] = field(default_factory=list)
    exec_changes:    list[tuple[str, dict, dict]] = field(default_factory=list)
    new_tickers:     list[tuple[str, dict]]       = field(default_factory=list)
    removed_tickers: list[tuple[str, dict]]       = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(
            self.grade_changes or self.score_changes
            or self.exec_changes or self.new_tickers or self.removed_tickers
        )


def grade_direction(old: str, new: str) -> str:
    try:
        old_i = _GRADE_ORDER.index(Grade(old))
        new_i = _GRADE_ORDER.index(Grade(new))
        if new_i > old_i:
            return "up"
        if new_i < old_i:
            return "down"
        return "same"
    except ValueError:
        return "same"


def is_likely_quick_reject(entry: dict) -> bool:
    # quick_reject reason을 snapshot에 명시적으로 저장하면 heuristic 불필요 (see TODO.md)
    return entry["grade"] == "avoid" and entry["normalized_score"] >= 55


def compute_diff(snap_a: dict, snap_b: dict) -> SnapshotDiff:
    map_a       = {e["ticker"]: e for e in snap_a.get("entries", [])}
    map_b       = {e["ticker"]: e for e in snap_b.get("entries", [])}
    all_tickers = sorted(set(map_a) | set(map_b))

    diff = SnapshotDiff()

    for ticker in all_tickers:
        a = map_a.get(ticker)
        b = map_b.get(ticker)

        if a is None:
            diff.new_tickers.append((ticker, b))
            continue
        if b is None:
            diff.removed_tickers.append((ticker, a))
            continue

        if a["grade"] != b["grade"]:
            diff.grade_changes.append((ticker, a, b))
            # grade change supersedes score-only change
        elif abs(b["normalized_score"] - a["normalized_score"]) >= SCORE_THRESHOLD:
            diff.score_changes.append((ticker, a, b))

        if a["executable"] != b["executable"]:
            diff.exec_changes.append((ticker, a, b))

    diff.score_changes.sort(
        key=lambda x: abs(x[2]["normalized_score"] - x[1]["normalized_score"]),
        reverse=True,
    )

    return diff
