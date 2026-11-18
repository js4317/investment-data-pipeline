from __future__ import annotations

_NAME_HOLDING = ("지주", "홀딩스", "holdings")

_SECTOR_RULES: list[tuple[str, str]] = [
    ("지주",      "Holding"),
    ("은행",      "Bank"),
    ("보험",      "Insurance"),
    ("증권",      "Securities"),
    ("반도체",    "Semiconductor"),
    ("IT하드웨어", "IT Hardware"),
    ("소프트웨어", "Software"),
    ("통신서비스", "Telecom"),
    ("유틸리티",  "Utility"),
    ("철강",      "Steel"),
    ("화학",      "Chemical"),
    ("에너지",    "Energy"),
    ("정유",      "Energy"),
    ("조선",      "Shipbuilding"),
    ("해운",      "Shipping"),
    ("건설",      "Construction"),
    ("음식료",    "Food & Beverage"),
    ("소매",      "Retail"),
    ("유통",      "Retail"),
    ("제약",      "Pharma"),
    ("바이오",    "Bio"),
    ("자동차",    "Auto"),
    ("기계",      "Machinery"),
    ("디스플레이", "Display"),
    ("미디어",    "Media"),
    ("전기장비",  "Electrical Equipment"),
    ("운송",      "Transportation"),
    ("부동산",    "Real Estate"),
    ("방위",      "Defense"),
    ("금융",      "Other Financial"),
]


def classify(sector: str | None, name: str | None = None) -> str:
    name_lower = (name or "").lower()
    if any(k in name_lower for k in _NAME_HOLDING):
        return "Holding"
    if sector:
        for keyword, itype in _SECTOR_RULES:
            if keyword in sector:
                return itype
        return sector
    return "Other"
