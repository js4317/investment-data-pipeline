from __future__ import annotations

import json
from pathlib import Path

_PATH = Path(__file__).parent.parent.parent / "cash.json"


def load_cash_krw() -> float:
    if not _PATH.exists():
        return 0.0
    try:
        return float(json.loads(_PATH.read_text()).get("krw", 0) or 0)
    except Exception:
        return 0.0


def save_cash_krw(amount: float) -> None:
    _PATH.write_text(json.dumps({"krw": round(amount)}, ensure_ascii=False, indent=2))
