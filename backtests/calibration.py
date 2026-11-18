from __future__ import annotations

from collections import defaultdict
from typing import Any

from analyzer.industry_type import classify
from backtests.snapshot import load_full, list_snapshot_dates
from backtests.performance import load_all, ALPHA_WINDOWS, _PERF_DIR

# ---------------------------------------------------------------------------
# Calibration Health — 엔진 검증 성숙도
# ---------------------------------------------------------------------------

_MATURITY_THRESHOLDS = {
    "mature":     100,   # 모든 윈도우 ≥ 100 → 🟢 Mature
    "early":       30,   # 20D ≥ 30          → 🟡 Early
}


def get_calibration_health() -> dict:
    """
    스냅샷 수 + 윈도우별 성과 파일 수 + 성숙도 상태 반환.

    maturity:
      🔴 Collecting — 20D 결과 < 30
      🟡 Early      — 20D 결과 ≥ 30, 아직 모든 윈도우 ≥ 100 미달
      🟢 Mature     — 20D / 60D / 120D 모두 ≥ 100
    """
    n_snapshots = len(list_snapshot_dates())

    perf_counts: dict[int, int] = {}
    for w in ALPHA_WINDOWS:
        perf_counts[w] = (
            len(list(_PERF_DIR.glob(f"*_w{w}.json")))
            if _PERF_DIR.exists() else 0
        )

    n_20d = perf_counts.get(20, 0)
    if all(perf_counts.get(w, 0) >= _MATURITY_THRESHOLDS["mature"] for w in ALPHA_WINDOWS):
        maturity = "🟢 Mature"
    elif n_20d >= _MATURITY_THRESHOLDS["early"]:
        maturity = "🟡 Early"
    else:
        maturity = "🔴 Collecting"

    return {
        "n_snapshots": n_snapshots,
        "perf_counts": perf_counts,
        "maturity":    maturity,
    }


_COMPONENTS: list[tuple[str, str]] = [
    ("valuation_score", "Valuation"),
    ("earnings_score",  "Earnings"),
    ("risk_score",      "Risk"),
]


# ---------------------------------------------------------------------------
# 공통 헬퍼
# ---------------------------------------------------------------------------

_SCORE_BANDS: list[tuple[int, int, str]] = [
    (95, 101, "95~100"),
    (90, 95,  "90~94"),
    (85, 90,  "85~89"),
    (80, 85,  "80~84"),
    (0,  80,  "< 80"),
]


def _band_label(score: int | None) -> str:
    if score is None:
        return "—"
    for lo, hi, label in _SCORE_BANDS:
        if lo <= score < hi:
            return label
    return "< 80"


def _status(n: int, avg_alpha: float | None) -> str:
    """
    4단계 Calibration Status.

    ⏳ Collecting  : alpha 데이터 없음
    📊 Small Sample: alpha 있지만 n < 30 (통계 불안정)
    ✅ Validated   : n ≥ 100 + avg_alpha ≥ 0
    ⚠ Review      : n ≥ 30 + avg_alpha < 0 (기대와 다른 결과)
    """
    if avg_alpha is None:
        return "⏳ Collecting"
    if n < 30:
        return "📊 Small Sample"
    if avg_alpha < 0:
        return "⚠ Review"
    if n >= 100:
        return "✅ Validated"
    return "📊 Small Sample"


def _agg(items: list[dict], score_key: str = "score", alpha_key: str = "alpha") -> dict:
    scores = [i[score_key] for i in items if i.get(score_key) is not None]
    alphas = [i[alpha_key] for i in items if i.get(alpha_key) is not None]
    avg_alpha = round(sum(alphas) / len(alphas) * 100, 2) if alphas else None
    return {
        "n":         len(items),
        "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
        "avg_alpha": avg_alpha,
        "status":    _status(len(items), avg_alpha),
    }


def _latest_snap(snap_date=None) -> dict:
    dates = list_snapshot_dates()
    if not dates:
        return {}
    return load_full(snap_date or dates[-1]) or {}


# ---------------------------------------------------------------------------
# ① Industry Type Calibration
# ---------------------------------------------------------------------------

def build_snapshot_distribution(
    sector_map: dict[str, str],
    name_map: dict[str, str],
    snap_date=None,
) -> dict[str, dict]:
    """
    최근(또는 지정) 스냅샷 기준 industry type별 분포.
    반환: {type: {n, avg_score, sb_pct}}
    """
    snap = _latest_snap(snap_date)
    if not snap:
        return {}

    buckets: dict[str, list[dict]] = defaultdict(list)
    for entry in snap["entries"]:
        ticker = entry["ticker"]
        sector = entry.get("sector") or sector_map.get(ticker)
        name   = name_map.get(ticker, "")
        itype  = classify(sector, name)
        buckets[itype].append({
            "score": entry.get("normalized_score"),
            "grade": entry.get("grade", ""),
        })

    result = {}
    for itype, items in sorted(buckets.items(), key=lambda x: -len(x[1])):
        scores = [i["score"] for i in items if i["score"] is not None]
        sb_n   = sum(1 for i in items if i["grade"] == "strong_buy")
        result[itype] = {
            "n":         len(items),
            "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
            "sb_pct":    round(sb_n / len(items) * 100) if items else 0,
        }
    return result


def build_alpha_report(
    sector_map: dict[str, str],
    name_map: dict[str, str],
    window: int = 20,
) -> list[dict]:
    """performance 결과 기반 industry type별 Alpha 집계."""
    buckets: dict[str, list[dict]] = defaultdict(list)

    for perf_data in load_all(window):
        for rec in perf_data.get("records", []):
            ticker = rec["ticker"]
            sector = rec.get("sector") or sector_map.get(ticker)
            name   = name_map.get(ticker, "")
            itype  = classify(sector, name)
            buckets[itype].append({"score": rec.get("normalized_score"), "alpha": rec.get("alpha")})

    rows = []
    for itype, items in sorted(buckets.items(), key=lambda x: -len(x[1])):
        agg = _agg(items)
        rows.append({"type": itype, **agg})
    return rows


# ---------------------------------------------------------------------------
# ② Score Band Calibration
# ---------------------------------------------------------------------------

def build_score_band_distribution(snap_date=None) -> list[dict]:
    """최근 스냅샷 기준 Score Band별 분포 (alpha 없어도 즉시 사용 가능)."""
    snap = _latest_snap(snap_date)
    if not snap:
        return []

    buckets: dict[str, list[dict]] = defaultdict(list)
    for entry in snap["entries"]:
        label = _band_label(entry.get("normalized_score"))
        buckets[label].append({
            "score": entry.get("normalized_score"),
            "grade": entry.get("grade", ""),
        })

    rows = []
    for _, _, label in _SCORE_BANDS:
        items = buckets.get(label, [])
        if not items:
            continue
        scores = [i["score"] for i in items if i["score"] is not None]
        sb_n   = sum(1 for i in items if i["grade"] == "strong_buy")
        rows.append({
            "band":      label,
            "n":         len(items),
            "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
            "sb_pct":    round(sb_n / len(items) * 100) if items else 0,
        })
    return rows


def build_score_band_report(window: int = 20) -> list[dict]:
    """performance 결과 기반 Score Band별 Alpha 집계."""
    buckets: dict[str, list[dict]] = defaultdict(list)

    for perf_data in load_all(window):
        for rec in perf_data.get("records", []):
            label = _band_label(rec.get("normalized_score"))
            buckets[label].append({"score": rec.get("normalized_score"), "alpha": rec.get("alpha")})

    rows = []
    for _, _, label in _SCORE_BANDS:
        items = buckets.get(label, [])
        if not items:
            continue
        agg = _agg(items)
        rows.append({"band": label, **agg})
    return rows


# ---------------------------------------------------------------------------
# ③ Factor Calibration (generic)
# ---------------------------------------------------------------------------

def build_factor_distribution(factor_key: str, snap_date=None) -> list[dict]:
    """
    최근 스냅샷 기준 특정 팩터 값별 분포.
    factor_key: 'eq_band', 'mom_signal', 'supply_type' 등 entry 필드명.
    """
    snap = _latest_snap(snap_date)
    if not snap:
        return []

    buckets: dict[Any, list[dict]] = defaultdict(list)
    for entry in snap["entries"]:
        val = entry.get(factor_key) or "—"
        buckets[val].append({
            "score": entry.get("normalized_score"),
            "grade": entry.get("grade", ""),
        })

    rows = []
    for val, items in sorted(buckets.items(), key=lambda x: -len(x[1])):
        scores = [i["score"] for i in items if i["score"] is not None]
        sb_n   = sum(1 for i in items if i["grade"] == "strong_buy")
        rows.append({
            "value":     str(val),
            "n":         len(items),
            "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
            "sb_pct":    round(sb_n / len(items) * 100) if items else 0,
        })
    return rows


def build_factor_report(factor_key: str, window: int = 20) -> list[dict]:
    """performance 결과 기반 팩터 값별 Alpha 집계."""
    buckets: dict[Any, list[dict]] = defaultdict(list)

    for perf_data in load_all(window):
        for rec in perf_data.get("records", []):
            val = rec.get(factor_key) or "—"
            buckets[val].append({"score": rec.get("normalized_score"), "alpha": rec.get("alpha")})

    rows = []
    for val, items in sorted(buckets.items(), key=lambda x: -len(x[1])):
        agg = _agg(items)
        rows.append({"value": str(val), **agg})
    return rows


# ---------------------------------------------------------------------------
# ④ Component Calibration
# ---------------------------------------------------------------------------

def build_component_report(window: int = 20) -> list[dict]:
    """
    Valuation / Earnings / Risk / Supply 컴포넌트별 High vs Low 그룹 Alpha 비교.

    Median-split boundary rule:
      High = 컴포넌트 점수 > 중앙값   (중앙값 초과)
      Low  = 컴포넌트 점수 ≤ 중앙값   (중앙값 이하)

    `> med / <= med` 규칙을 쓰는 이유:
      zero-heavy component (예: valuation_score에서 0점이 50% 이상)에서
      `>= med / < med` 를 사용하면 median = 0이 되어 low 버킷이 비어버린다.
      `> med / <= med` 는 중앙값과 동일한 값을 low 쪽에 넣어 항상 양쪽 버킷을 채운다.

    spread = High Alpha − Low Alpha (양수면 해당 컴포넌트가 실제 알파 기여)

    Regression baseline (v8.3.x era, 수정 적용 시점 2026-09-08):
      Valuation : 수정 전 high=2075/low=0(spread=None) → 수정 후 high≈880/low≈1195
      Earnings  : 수정 전 high=1348/low=727(spread=+8.34pp) → 수정 후 실질 동일
      Risk      : 수정 전 high=1248/low=827(spread=+2.56pp) → 수정 후 실질 동일
      Supply    : 수정 전 high=1052/low=1023(spread=−1.08pp) → 수정 후 실질 동일
    """
    all_recs = [
        rec
        for perf_data in load_all(window)
        for rec in perf_data.get("records", [])
        if rec.get("alpha") is not None
    ]
    if not all_recs:
        return [{"component": label, "n_high": 0, "n_low": 0,
                 "high_alpha": None, "low_alpha": None, "spread": None,
                 "status": "⏳ Collecting"}
                for _, label in _COMPONENTS]

    rows = []
    for field, label in _COMPONENTS:
        vals = [r[field] for r in all_recs if r.get(field) is not None]
        if not vals:
            rows.append({"component": label, "n_high": 0, "n_low": 0,
                         "high_alpha": None, "low_alpha": None, "spread": None,
                         "status": "⏳ Collecting"})
            continue

        med  = sorted(vals)[len(vals) // 2]
        # > med / <= med: zero-heavy 분포에서 low 버킷이 비지 않도록 중앙값 이하를 low에 포함
        high = [r["alpha"] for r in all_recs if r.get(field) is not None and r[field] > med]
        low  = [r["alpha"] for r in all_recs if r.get(field) is not None and r[field] <= med]

        high_alpha = round(sum(high) / len(high) * 100, 2) if high else None
        low_alpha  = round(sum(low)  / len(low)  * 100, 2) if low  else None
        spread     = round(high_alpha - low_alpha, 2) if (
            high_alpha is not None and low_alpha is not None
        ) else None

        rows.append({
            "component":  label,
            "n_high":     len(high),
            "n_low":      len(low),
            "high_alpha": high_alpha,
            "low_alpha":  low_alpha,
            "spread":     spread,
            "status":     _status(len(high) + len(low), spread),
        })
    return rows


# ---------------------------------------------------------------------------
# 엔진 건강 KPI — Score vs Alpha 상관계수
# ---------------------------------------------------------------------------

def calc_score_alpha_correlation(window: int = 20) -> dict:
    """
    전체 (normalized_score, alpha) 쌍의 Pearson 상관계수 ρ.
    ρ > 0   : 점수가 높을수록 Alpha가 큰 경향 (엔진 유효)
    ρ ≈ 0   : 점수와 Alpha 무관
    ρ < 0   : 역상관 (엔진 재검토 필요)
    반환: {n, rho}  —  n < 5 이면 rho = None
    """
    pairs = [
        (rec["normalized_score"], rec["alpha"])
        for perf_data in load_all(window)
        for rec in perf_data.get("records", [])
        if rec.get("normalized_score") is not None and rec.get("alpha") is not None
    ]
    n = len(pairs)
    if n < 5:
        return {"n": n, "rho": None}

    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    xb = sum(xs) / n
    yb = sum(ys) / n

    num   = sum((x - xb) * (y - yb) for x, y in pairs)
    denom = (sum((x - xb) ** 2 for x in xs) * sum((y - yb) ** 2 for y in ys)) ** 0.5
    rho   = round(num / denom, 3) if denom > 0 else 0.0
    return {"n": n, "rho": rho}
