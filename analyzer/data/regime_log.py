from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Optional

_LOG_PATH = Path(__file__).parent.parent.parent / "data" / "regime_log.jsonl"
_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

_KNOWN_EVENTS = [
    "bull",
    "bear",
    "crash",
    "recovery_start",
    "sideways",
    "circuit_breaker",
    "sidecar",
]


def log_regime_event(
    event: str,
    as_of: Optional[date] = None,
    note: str = "",
) -> None:
    if as_of is None:
        as_of = date.today()

    entry = {
        "date":  str(as_of),
        "event": event,
        "note":  note,
    }

    existing = load_regime_log()
    if any(e["date"] == str(as_of) and e["event"] == event for e in existing):
        return

    with _LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_regime_log() -> list[dict]:
    if not _LOG_PATH.exists():
        return []
    entries = []
    with _LOG_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return sorted(entries, key=lambda x: x["date"])


def get_regime_at(as_of: date) -> Optional[str]:
    """as_of 날짜 기준 가장 최근 regime 이벤트 반환."""
    entries = [e for e in load_regime_log() if e["date"] <= str(as_of)]
    return entries[-1]["event"] if entries else None
