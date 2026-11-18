#!/usr/bin/env python3
"""
Supply Event Study — 상승 이벤트 직전 수급 패턴 분석
======================================================
사전등록: 2026-09-23  /  docs/RESEARCH.md

질문: 20D alpha ≥ +7%p 직전 20일의 수급 패턴은 평상시와 다른가?

사전등록 패턴 (4개 고정):
  P1: 평균 방향 점수 (T-20~T-1)
  P2: 쌍끌이매수 비율 (T-20~T-1)
  P3: 최장 연속 매수 streak (기관 or 외국인, 둘 중 max)
  P4: 후반 강화 전환 (T-15~T-10 vs T-5~T-1 방향 점수)

Discovery: 2020-2024
Validation: 2025-2026

대조군: 동일 ticker 내 |20D alpha| < 3%p 날짜
"""
from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path
from scipy import stats as scipy_stats

ROOT     = Path(__file__).resolve().parent.parent
FLOW_DB  = ROOT / "database" / "research.db"
OHLCV_DB = ROOT / "database" / "market_ohlcv.db"

KOSPI = "1001"

# ── 사전등록 고정값 (변경 금지) ────────────────────────────────────────────────
EVENT_ALPHA_THRESHOLD    = 0.07    # +7%p
CONTROL_ALPHA_MAX        = 0.03    # |alpha| < 3%p = non-event
PATTERN_WINDOW           = 20      # T-20 ~ T-1
EARLY_WINDOW             = (15, 10)  # T-15 ~ T-10 (5일)
LATE_WINDOW              = (5, 1)    # T-5  ~ T-1  (5일)
EFFECT_SIZE_THRESHOLD    = 0.10    # rank-biserial r 기준

DISCOVERY_END   = "2024-12-31"
VALIDATION_START = "2025-01-01"

# direction score (production 동일)
DUAL_BUY    = 20
FX_SINGLE   = 12
INST_SINGLE = 10
DUAL_SELL   = -5

MIN_WINDOW_DAYS = 12   # 20일 중 최소 12일 데이터 있어야 패턴 계산


# ── 데이터 로드 ───────────────────────────────────────────────────────────────

def load_prices() -> tuple[list[str], dict[str, dict[str, float]]]:
    print("  가격 데이터 로딩...", flush=True)
    with sqlite3.connect(OHLCV_DB) as c:
        rows = c.execute(
            "SELECT ticker, trade_date, open FROM market_ohlcv WHERE open > 0 ORDER BY trade_date"
        ).fetchall()
    prices: dict[str, dict[str, float]] = defaultdict(dict)
    dates_set = set()
    for ticker, date, open_ in rows:
        prices[ticker][date] = open_
        dates_set.add(date)
    trading_dates = sorted(dates_set)
    print(f"  거래일: {len(trading_dates)}일 ({trading_dates[0]} ~ {trading_dates[-1]})")
    return trading_dates, dict(prices)


def load_flow() -> dict[str, dict[str, tuple[int, int]]]:
    print("  investor_flow 로딩...", flush=True)
    with sqlite3.connect(FLOW_DB) as c:
        rows = c.execute(
            "SELECT ticker, signal_date, foreign_net, institution_net FROM investor_flow"
        ).fetchall()
    flow: dict[str, dict[str, tuple[int, int]]] = defaultdict(dict)
    for ticker, date, fnet, inet in rows:
        if fnet is not None and inet is not None:
            flow[ticker][date] = (fnet, inet)
    print(f"  tickers: {len(flow):,}")
    return dict(flow)


# ── Alpha 계산 ────────────────────────────────────────────────────────────────

def get_alpha_20d(
    ticker: str,
    date: str,
    date_idx: dict[str, int],
    trading_dates: list[str],
    prices: dict[str, dict[str, float]],
) -> float | None:
    """T+1 open → T+21 open 초과수익률 (KOSPI 대비)"""
    idx = date_idx.get(date)
    if idx is None:
        return None
    entry_idx = idx + 1
    exit_idx  = idx + 21
    if exit_idx >= len(trading_dates):
        return None
    entry_date = trading_dates[entry_idx]
    exit_date  = trading_dates[exit_idx]

    tp = prices.get(ticker, {})
    kp = prices.get(KOSPI, {})
    entry, exit_ = tp.get(entry_date), tp.get(exit_date)
    k_entry, k_exit = kp.get(entry_date), kp.get(exit_date)
    if not all([entry, exit_, k_entry, k_exit]):
        return None
    return (exit_ / entry - 1) - (k_exit / k_entry - 1)


# ── 방향 점수 ─────────────────────────────────────────────────────────────────

def direction_score(fnet: int, inet: int) -> int:
    f, i = fnet > 0, inet > 0
    if f and i:  return DUAL_BUY
    if f:        return FX_SINGLE
    if i:        return INST_SINGLE
    return DUAL_SELL


# ── 패턴 계산 ─────────────────────────────────────────────────────────────────

def compute_patterns(
    ticker: str,
    date: str,
    date_idx: dict[str, int],
    trading_dates: list[str],
    flow: dict[str, dict[str, tuple[int, int]]],
) -> dict | None:
    """T-20 ~ T-1 구간에서 4개 패턴 계산"""
    idx = date_idx.get(date)
    if idx is None or idx < PATTERN_WINDOW:
        return None

    ticker_flow = flow.get(ticker, {})

    # T-20 ~ T-1 데이터 수집
    window_data = []
    for offset in range(1, PATTERN_WINDOW + 1):   # T-1 to T-20 (역순 수집 후 정렬)
        di = idx - offset
        if di < 0:
            break
        d = trading_dates[di]
        if d in ticker_flow:
            fnet, inet = ticker_flow[d]
            window_data.append((d, fnet, inet, direction_score(fnet, inet)))

    if len(window_data) < MIN_WINDOW_DAYS:
        return None

    # P1: 평균 방향 점수
    scores = [w[3] for w in window_data]
    p1_mean_dir = statistics.mean(scores)

    # P2: 쌍끌이매수 비율
    dual_buy_days = sum(1 for w in window_data if w[1] > 0 and w[2] > 0)
    p2_dual_ratio = dual_buy_days / len(window_data)

    # P3: 최장 연속 매수 streak (기관 or 외국인)
    def max_streak(vals: list[bool]) -> int:
        best, cur = 0, 0
        for v in vals:
            cur = cur + 1 if v else 0
            best = max(best, cur)
        return best

    # window_data는 T-1부터 T-20 순으로 수집됨. 시간 순으로 정렬
    window_sorted = sorted(window_data, key=lambda x: x[0])
    f_pos = [w[1] > 0 for w in window_sorted]
    i_pos = [w[2] > 0 for w in window_sorted]
    p3_streak = max(max_streak(f_pos), max_streak(i_pos))

    # P4: 후반 강화 전환 (T-15~T-10 vs T-5~T-1)
    # window_data: offset 1=T-1, 2=T-2, ..., 20=T-20
    early_scores, late_scores = [], []
    for offset, wd in enumerate(window_data, 1):  # offset 1=T-1
        if EARLY_WINDOW[1] <= offset <= EARLY_WINDOW[0]:  # T-10 ~ T-15
            early_scores.append(wd[3])
        if LATE_WINDOW[1] <= offset <= LATE_WINDOW[0]:   # T-1 ~ T-5
            late_scores.append(wd[3])

    p4_strengthening = None
    if early_scores and late_scores:
        early_mean = statistics.mean(early_scores)
        late_mean  = statistics.mean(late_scores)
        p4_strengthening = 1 if late_mean > early_mean else 0

    return {
        "p1_mean_dir":       p1_mean_dir,
        "p2_dual_ratio":     p2_dual_ratio,
        "p3_streak":         p3_streak,
        "p4_strengthening":  p4_strengthening,
        "n_days":            len(window_data),
    }


# ── 통계 비교 ─────────────────────────────────────────────────────────────────

def rank_biserial_r(group1: list[float], group2: list[float]) -> float:
    """Mann-Whitney rank-biserial correlation = effect size"""
    n1, n2 = len(group1), len(group2)
    if n1 == 0 or n2 == 0:
        return float("nan")
    u, _ = scipy_stats.mannwhitneyu(group1, group2, alternative="two-sided")
    r = 1 - (2 * u) / (n1 * n2)
    return r


def compare_groups(
    label: str,
    event_vals: list[float],
    control_vals: list[float],
    threshold: float | None = None,
) -> dict:
    if not event_vals or not control_vals:
        return {"label": label, "n_event": len(event_vals), "n_control": len(control_vals)}

    _, p_val = scipy_stats.mannwhitneyu(event_vals, control_vals, alternative="two-sided")
    r = rank_biserial_r(event_vals, control_vals)
    e_med = statistics.median(event_vals)
    c_med = statistics.median(control_vals)
    diff = e_med - c_med
    flag = "✓" if abs(r) > EFFECT_SIZE_THRESHOLD else "—"

    return {
        "label":     label,
        "n_event":   len(event_vals),
        "n_control": len(control_vals),
        "event_med": e_med,
        "ctrl_med":  c_med,
        "diff_med":  diff,
        "r":         r,
        "p":         p_val,
        "flag":      flag,
    }


def print_comparison(res: dict):
    if "event_med" not in res:
        print(f"  {res['label']:<24}  n_event={res.get('n_event',0)}  n_ctrl={res.get('n_control',0)}  데이터 부족")
        return
    print(f"  {res['label']:<24}  "
          f"n_evt={res['n_event']:>5} / n_ctl={res['n_control']:>5}  "
          f"evt_med={res['event_med']:>+7.3f}  ctl_med={res['ctrl_med']:>+7.3f}  "
          f"diff={res['diff_med']:>+7.3f}  r={res['r']:>+.3f}  {res['flag']}")


# ── Discovery / Validation 실행 ───────────────────────────────────────────────

def run_phase(
    phase_name: str,
    date_range: tuple[str, str],
    tickers: list[str],
    date_idx: dict[str, int],
    trading_dates: list[str],
    prices: dict[str, dict[str, float]],
    flow: dict[str, dict[str, tuple[int, int]]],
) -> dict[str, list]:
    """
    각 phase에서 event / non-event 케이스 수집 및 패턴 값 추출
    Returns: {pattern_name: {"event": [...], "control": [...]}}
    """
    start, end = date_range
    pattern_data: dict[str, dict[str, list]] = {
        "P1_mean_dir":      {"event": [], "control": []},
        "P2_dual_ratio":    {"event": [], "control": []},
        "P3_streak":        {"event": [], "control": []},
        "P4_strengthen":    {"event": [], "control": []},
    }
    n_events, n_controls, n_skip = 0, 0, 0

    for ticker in tickers:
        ticker_flow_dates = sorted(d for d in flow.get(ticker, {}) if start <= d <= end)

        for date in ticker_flow_dates:
            alpha = get_alpha_20d(ticker, date, date_idx, trading_dates, prices)
            if alpha is None:
                n_skip += 1
                continue

            is_event   = alpha >= EVENT_ALPHA_THRESHOLD
            is_control = abs(alpha) < CONTROL_ALPHA_MAX

            if not is_event and not is_control:
                continue

            pats = compute_patterns(ticker, date, date_idx, trading_dates, flow)
            if pats is None:
                n_skip += 1
                continue

            group = "event" if is_event else "control"
            if is_event:
                n_events += 1
            else:
                n_controls += 1

            pattern_data["P1_mean_dir"][group].append(pats["p1_mean_dir"])
            pattern_data["P2_dual_ratio"][group].append(pats["p2_dual_ratio"])
            pattern_data["P3_streak"][group].append(float(pats["p3_streak"]))
            if pats["p4_strengthening"] is not None:
                pattern_data["P4_strengthen"][group].append(float(pats["p4_strengthening"]))

    print(f"\n  [{phase_name}] {start} ~ {end}")
    print(f"    Events (alpha≥+7%p): {n_events:,}")
    print(f"    Controls (|alpha|<3%p): {n_controls:,}")
    print(f"    Skipped: {n_skip:,}")

    # 이벤트 비율 (base rate)
    total = n_events + n_controls
    if total > 0:
        print(f"    Event base rate: {n_events/total*100:.1f}%")

    return pattern_data


# ── Validation IC 계산 ────────────────────────────────────────────────────────

def compute_validation_ic(
    best_pattern_key: str,
    date_range: tuple[str, str],
    tickers: list[str],
    date_idx: dict[str, int],
    trading_dates: list[str],
    prices: dict[str, dict[str, float]],
    flow: dict[str, dict[str, tuple[int, int]]],
) -> dict:
    """Discovery의 최강 패턴을 연속 신호로 변환, validation IC 계산"""
    start, end = date_range
    obs_by_date: dict[str, list[tuple[float, float]]] = defaultdict(list)

    for ticker in tickers:
        for date in sorted(d for d in flow.get(ticker, {}) if start <= d <= end):
            alpha = get_alpha_20d(ticker, date, date_idx, trading_dates, prices)
            if alpha is None:
                continue
            pats = compute_patterns(ticker, date, date_idx, trading_dates, flow)
            if pats is None:
                continue

            key_map = {
                "P1_mean_dir":   "p1_mean_dir",
                "P2_dual_ratio": "p2_dual_ratio",
                "P3_streak":     "p3_streak",
                "P4_strengthen": "p4_strengthening",
            }
            val = pats.get(key_map.get(best_pattern_key, ""))
            if val is None:
                continue
            obs_by_date[date].append((float(val), alpha))

    ics = []
    for date in sorted(obs_by_date):
        pairs = obs_by_date[date]
        if len(pairs) < 10:
            continue
        sigs  = [p[0] for p in pairs]
        alphs = [p[1] for p in pairs]
        if len(set(sigs)) < 2:
            continue
        rho, _ = scipy_stats.spearmanr(sigs, alphs)
        if rho == rho:
            ics.append(rho)

    if not ics:
        return {"n": 0}
    return {
        "n":       len(ics),
        "mean":    statistics.mean(ics),
        "median":  statistics.median(ics),
        "pos_pct": sum(1 for v in ics if v > 0) / len(ics) * 100,
    }


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 72)
    print("Supply Event Study — 상승 이벤트 직전 수급 패턴 분석")
    print("사전등록: 2026-09-23")
    print(f"  Event:    20D alpha ≥ +{EVENT_ALPHA_THRESHOLD*100:.0f}%p")
    print(f"  Control:  |20D alpha| < {CONTROL_ALPHA_MAX*100:.0f}%p (동일 ticker)")
    print(f"  Discovery:  2020-01-01 ~ {DISCOVERY_END}")
    print(f"  Validation: {VALIDATION_START} ~ 2026-09-30")
    print("=" * 72)

    trading_dates, prices = load_prices()
    flow = load_flow()
    date_idx = {d: i for i, d in enumerate(trading_dates)}
    tickers = [t for t in flow if t != KOSPI and t in prices]
    print(f"  분석 tickers: {len(tickers):,}")

    # ── Discovery ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("[ Phase 1: Discovery (2020-2024) ]")
    disc_data = run_phase(
        "Discovery", ("2020-01-01", DISCOVERY_END),
        tickers, date_idx, trading_dates, prices, flow
    )

    print("\n[ Discovery 패턴 비교 — Event vs Non-event ]")
    print(f"  {'패턴':<24}  {'n_event':>7} / {'n_ctrl':>7}  "
          f"{'evt_med':>8}  {'ctl_med':>8}  {'diff':>7}  {'r':>6}  판정")
    print("  " + "-" * 80)

    disc_results = {}
    for pname, groups in disc_data.items():
        res = compare_groups(pname, groups["event"], groups["control"])
        disc_results[pname] = res
        print_comparison(res)

    # ── Discovery 결론: 최강 패턴 선택 ────────────────────────────────────────
    print("\n[ Discovery 후보 패턴 요약 ]")
    candidates = [(k, abs(v.get("r", 0))) for k, v in disc_results.items() if "r" in v]
    candidates.sort(key=lambda x: -x[1])

    for k, r_abs in candidates:
        res = disc_results[k]
        candidate_flag = "✓ 후보" if r_abs > EFFECT_SIZE_THRESHOLD else "— 미달"
        print(f"  {k:<20}  |r|={r_abs:.3f}  {candidate_flag}")

    if not candidates:
        print("  유효한 패턴 없음")
        return

    best_pattern = candidates[0][0]
    print(f"\n  → Validation에 사용할 패턴: {best_pattern} (|r|={candidates[0][1]:.3f})")
    print("  ⚠️ 이 선택은 Discovery 결과 기반. Validation 후 재선택/재조정 금지.")

    # ── Validation ────────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("[ Phase 2: Validation (2025-2026) ]")
    val_data = run_phase(
        "Validation", (VALIDATION_START, "2026-09-30"),
        tickers, date_idx, trading_dates, prices, flow
    )

    print("\n[ Validation 패턴 비교 (Discovery의 4개 패턴 그대로) ]")
    print("  (기준 패턴만 봐도 되지만 전체 기록)")
    print(f"  {'패턴':<24}  {'n_event':>7} / {'n_ctrl':>7}  "
          f"{'evt_med':>8}  {'ctl_med':>8}  {'diff':>7}  {'r':>6}  판정")
    print("  " + "-" * 80)
    for pname, groups in val_data.items():
        res = compare_groups(pname, groups["event"], groups["control"])
        print_comparison(res)

    # Prospective IC (최강 패턴)
    print(f"\n[ Validation Prospective IC — {best_pattern} ]")
    ic_result = compute_validation_ic(
        best_pattern, (VALIDATION_START, "2026-09-30"),
        tickers, date_idx, trading_dates, prices, flow
    )
    if ic_result.get("n", 0) == 0:
        print("  데이터 부족 — IC 계산 불가")
    else:
        ic_ok  = ic_result["mean"] > 0.05
        pos_ok = ic_result["pos_pct"] > 55.0
        print(f"  n_days={ic_result['n']}  "
              f"mean_IC={ic_result['mean']:>+.4f}  "
              f"median_IC={ic_result['median']:>+.4f}  "
              f"pos%={ic_result['pos_pct']:.1f}%")
        print(f"  IC mean > +0.05: {'✅' if ic_ok else '❌'}  "
              f"pos% > 55%: {'✅' if pos_ok else '❌'}")
        verdict = "✅ Prospective signal 지지" if (ic_ok and pos_ok) else "❌ Prospective signal 미지지"
        print(f"\n  최종 판정: {verdict}")

    # ── 종합 요약 ────────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("[ 종합 요약 ]")
    print("  Discovery에서 효과 크기(|r|>0.10)를 넘은 패턴:")
    for k, r_abs in candidates:
        if r_abs > EFFECT_SIZE_THRESHOLD:
            res = disc_results[k]
            diff_pct = res.get("diff_med", 0)
            print(f"    {k}: event_med − ctrl_med = {diff_pct:>+.3f}  |r|={r_abs:.3f}")
    if all(r_abs <= EFFECT_SIZE_THRESHOLD for _, r_abs in candidates):
        print("    없음 — 모든 패턴 효과 크기 미달")
    print()
    print("  ⚠️ Discovery와 동일 데이터로 결론 내리지 않음")
    print("     최종 판단은 Validation IC (위) 기준")


if __name__ == "__main__":
    main()
