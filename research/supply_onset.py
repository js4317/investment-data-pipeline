#!/usr/bin/env python3
"""
Supply Onset Effect — 수급 전환 초기 효과 연구
================================================
사전등록: 2026-09-23  /  docs/RESEARCH.md

정의 (고정 — 변경 불가):
  Pre-absorption : 직전 10 trading days 중,
                   (foreign_net ≤ 0 AND institution_net ≤ 0)인 날이 7일 이상
                   ※ 외인/기관이 같은 날 둘 다 비양수인 날을 카운트
  Onset (T0)     : Pre-absorption 충족 직후, foreign_net > 0 AND institution_net > 0
                   → T0에서 즉시 확정. 이후 수급 지속 여부와 무관.
  Mature (라벨)  : T0 이후 5 consecutive trading days 모두 양 주체 순매수 (사후 라벨)
                   → Q1 분석에는 모든 Onset 포함. Mature 여부로 T0 채택/기각 금지.

비교 그룹:
  Q1. Onset 전체 vs Baseline (비-Onset 일)
  Q2. Onset+Mature vs Onset+Non-mature

평가: 20D, 60D alpha (KOSPI 대비)
      Entry = T0 다음 거래일 open / Exit = T0+20(60) 번째 거래일 close
판정: n < 30 → Inconclusive
"""
from __future__ import annotations

import sqlite3
import statistics
import random
from collections import deque
from pathlib import Path

ROOT     = Path(__file__).resolve().parent.parent
FLOW_DB  = ROOT / "database" / "research.db"
OHLCV_DB = ROOT / "database" / "market_ohlcv.db"
KOSPI    = "1001"

# ── 사전등록 파라미터 (변경 불가) ────────────────────────────────────────────
PRE_WINDOW        = 10   # pre-absorption lookback (trading days)
PRE_THRESHOLD     = 7    # 10일 중 동시 비양수인 날 최소 수
MATURE_WINDOW     = 5    # Mature 라벨 기간 (consecutive trading days)
COOLDOWN_DAYS     = 20   # Onset 이후 다음 탐색 재개 전 휴지 기간 (중복 방지)
EVAL_WINDOWS      = [20, 60]
INCONCLUSIVE_N    = 30
BASELINE_SAMPLE_N = 5000  # baseline 표본 크기 (전체 대신 랜덤 샘플)

random.seed(42)


# ── 데이터 로드 ───────────────────────────────────────────────────────────────

def load_flow() -> dict[str, list[tuple[str, int, int]]]:
    """ticker → sorted list of (date, foreign_net, institution_net)"""
    with sqlite3.connect(FLOW_DB) as c:
        rows = c.execute("""
            SELECT ticker, signal_date, foreign_net, institution_net
            FROM investor_flow
            ORDER BY ticker, signal_date
        """).fetchall()
    result: dict[str, list] = {}
    for ticker, date, fn, ins in rows:
        result.setdefault(ticker, []).append((date, fn or 0, ins or 0))
    return result


def load_ohlcv() -> dict[str, list[tuple[str, float, float]]]:
    """ticker → sorted list of (date, open, close)"""
    with sqlite3.connect(OHLCV_DB) as c:
        rows = c.execute("""
            SELECT ticker, trade_date, open, close
            FROM market_ohlcv
            ORDER BY ticker, trade_date
        """).fetchall()
    result: dict[str, list] = {}
    for ticker, date, o, cl in rows:
        if o and cl:
            result.setdefault(ticker, []).append((date, o, cl))
    return result


def build_price_index(
    ohlcv: dict[str, list]
) -> dict[str, dict[str, int]]:
    """ticker → {date: index_in_list} (빠른 조회용)"""
    return {t: {row[0]: i for i, row in enumerate(rows)} for t, rows in ohlcv.items()}


# ── Onset 이벤트 탐지 ────────────────────────────────────────────────────────

def detect_onsets(
    flow: dict[str, list[tuple[str, int, int]]]
) -> list[dict]:
    """
    종목별 investor_flow 시계열을 순회하며 Onset 이벤트를 탐지.
    반환: list of {ticker, T0, T0_idx, flow_idx_list_for_mature}
    """
    events = []
    for ticker, series in flow.items():
        n = len(series)
        if n < PRE_WINDOW + 1:
            continue

        window: deque[int] = deque()  # 1 = both ≤ 0, 0 = otherwise
        cooldown = 0  # Onset 이후 탐색 중단 카운터

        for i, (date, fn, ins) in enumerate(series):
            if cooldown > 0:
                cooldown -= 1
                # 윈도우는 계속 업데이트 (cooldown 후 재탐색을 위해)
                flag = 1 if (fn <= 0 and ins <= 0) else 0
                window.append(flag)
                if len(window) > PRE_WINDOW:
                    window.popleft()
                continue

            # 현재 날 이전 window 업데이트는 이미 됨 — Onset 판정 먼저
            if len(window) == PRE_WINDOW:
                pre_count = sum(window)
                if pre_count >= PRE_THRESHOLD and fn > 0 and ins > 0:
                    events.append({
                        "ticker":   ticker,
                        "T0":       date,
                        "T0_idx":   i,   # flow series index
                    })
                    cooldown = COOLDOWN_DAYS

            # 현재 날 window에 추가
            flag = 1 if (fn <= 0 and ins <= 0) else 0
            window.append(flag)
            if len(window) > PRE_WINDOW:
                window.popleft()

    return events


def add_mature_labels(
    events: list[dict],
    flow: dict[str, list[tuple[str, int, int]]]
) -> None:
    """사후 Mature 라벨을 events에 in-place 추가."""
    for ev in events:
        series = flow[ev["ticker"]]
        i0     = ev["T0_idx"]
        mature_rows = series[i0 + 1 : i0 + 1 + MATURE_WINDOW]
        if len(mature_rows) < MATURE_WINDOW:
            ev["mature"] = None  # 평가 기간 데이터 부족
            continue
        ev["mature"] = all(fn > 0 and ins > 0 for _, fn, ins in mature_rows)


# ── Alpha 계산 ────────────────────────────────────────────────────────────────

def calc_alpha(
    ticker: str,
    t0_date: str,
    window_days: int,
    ohlcv: dict[str, list],
    pidx:  dict[str, dict[str, int]],
) -> float | None:
    """
    Entry = T0 다음 거래일 open
    Exit  = T0 + window_days 번째 거래일 close
    Alpha = 주식 수익률 - KOSPI 수익률 (동일 window)
    """
    stock_list = ohlcv.get(ticker)
    kospi_list = ohlcv.get(KOSPI)
    if not stock_list or not kospi_list:
        return None

    t0_idx_s = pidx.get(ticker, {}).get(t0_date)
    t0_idx_k = pidx.get(KOSPI, {}).get(t0_date)
    if t0_idx_s is None or t0_idx_k is None:
        return None

    # entry: T0+1
    entry_s_idx = t0_idx_s + 1
    entry_k_idx = t0_idx_k + 1
    # exit: T0 + window_days
    exit_s_idx  = t0_idx_s + window_days
    exit_k_idx  = t0_idx_k + window_days

    if (exit_s_idx >= len(stock_list) or exit_k_idx >= len(kospi_list)
            or entry_s_idx >= len(stock_list) or entry_k_idx >= len(kospi_list)):
        return None

    s_entry = stock_list[entry_s_idx][1]  # open
    s_exit  = stock_list[exit_s_idx][2]   # close
    k_entry = kospi_list[entry_k_idx][1]
    k_exit  = kospi_list[exit_k_idx][2]

    if not s_entry or not k_entry:
        return None

    return (s_exit / s_entry - 1) - (k_exit / k_entry - 1)


# ── 통계 출력 ─────────────────────────────────────────────────────────────────

def print_stats(label: str, vals: list[float]):
    if not vals:
        print(f"  {label:<30} 데이터 없음")
        return
    n      = len(vals)
    mean   = statistics.mean(vals)
    median = statistics.median(vals)
    hit    = sum(1 for v in vals if v > 0) / n * 100
    p10    = sorted(vals)[int(n * 0.10)]
    if n < INCONCLUSIVE_N:
        flag = " ⚠️  INCONCLUSIVE (n<30)"
    else:
        flag = ""
    print(f"  {label:<30} n={n:5}  mean={mean*100:+6.2f}%  "
          f"median={median*100:+6.2f}%  hit={hit:4.0f}%  P10={p10*100:+6.1f}%{flag}")


def build_baseline(
    events: list[dict],
    flow: dict[str, list],
    ohlcv: dict[str, list],
    pidx:  dict[str, dict[str, int]],
    window_days: int,
) -> list[float]:
    """비-Onset 일의 alpha 샘플 (랜덤 BASELINE_SAMPLE_N개)."""
    onset_set = {(ev["ticker"], ev["T0"]) for ev in events}
    candidates = []
    for ticker, series in flow.items():
        for date, _, _ in series:
            if (ticker, date) not in onset_set:
                candidates.append((ticker, date))

    sample = random.sample(candidates, min(BASELINE_SAMPLE_N, len(candidates)))
    result = []
    for ticker, date in sample:
        a = calc_alpha(ticker, date, window_days, ohlcv, pidx)
        if a is not None:
            result.append(a)
    return result


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 68)
    print("Supply Onset Effect 연구 — 수급 전환 초기 효과")
    print("사전등록: 2026-09-23  /  파라미터 고정")
    print(f"  Pre-absorption: {PRE_WINDOW}일 중 동시 비양수 ≥{PRE_THRESHOLD}일")
    print(f"  Onset: 양 주체 동시 양전환  /  Mature: 이후 {MATURE_WINDOW}일 연속 양수 (라벨만)")
    print("=" * 68)

    print("\n[1] 데이터 로딩 중...", flush=True)
    flow  = load_flow()
    ohlcv = load_ohlcv()
    pidx  = build_price_index(ohlcv)
    print(f"    investor_flow: {sum(len(v) for v in flow.values()):,}행  "
          f"/{len(flow):,}종목")
    print(f"    market_ohlcv:  {sum(len(v) for v in ohlcv.values()):,}행  "
          f"/{len(ohlcv):,}종목")

    print("\n[2] Onset 이벤트 탐지 중...", flush=True)
    events = detect_onsets(flow)
    add_mature_labels(events, flow)
    mature_true  = [e for e in events if e.get("mature") is True]
    mature_false = [e for e in events if e.get("mature") is False]
    mature_na    = [e for e in events if e.get("mature") is None]
    print(f"    전체 Onset: {len(events)}건")
    print(f"    Mature=True: {len(mature_true)}건 / False: {len(mature_false)}건 / NA: {len(mature_na)}건")

    if len(events) < INCONCLUSIVE_N:
        print(f"\n⚠️  n={len(events)} < {INCONCLUSIVE_N} → INCONCLUSIVE. 파라미터 변경 없이 종료.")
        return

    print("\n[3] Alpha 계산 중...", flush=True)
    for ev in events:
        for w in EVAL_WINDOWS:
            ev[f"alpha_{w}d"] = calc_alpha(
                ev["ticker"], ev["T0"], w, ohlcv, pidx
            )

    # ── Q1: Onset 전체 vs Baseline ─────────────────────────────────────────
    for w in EVAL_WINDOWS:
        key = f"alpha_{w}d"
        print(f"\n[ Q1 — Onset vs Baseline ({w}D alpha) ]")

        onset_vals = [ev[key] for ev in events if ev[key] is not None]
        print_stats(f"Onset (전체)", onset_vals)

        baseline_vals = build_baseline(events, flow, ohlcv, pidx, w)
        print_stats(f"Baseline (비-Onset 샘플)", baseline_vals)

        diff = [o - b for o, b in zip(
            sorted(onset_vals), sorted(baseline_vals, reverse=True)
        )]  # 방향성 요약만 (정확한 차이는 매칭이 아님)
        if onset_vals and baseline_vals:
            print(f"  → Onset mean {statistics.mean(onset_vals)*100:+.2f}%  /  "
                  f"Baseline mean {statistics.mean(baseline_vals)*100:+.2f}%  /  "
                  f"차이 {(statistics.mean(onset_vals)-statistics.mean(baseline_vals))*100:+.2f}%p")

    # ── Q2: Mature vs Non-mature ──────────────────────────────────────────
    for w in EVAL_WINDOWS:
        key = f"alpha_{w}d"
        print(f"\n[ Q2 — Mature vs Non-mature ({w}D alpha) ]")
        m_vals  = [ev[key] for ev in mature_true  if ev[key] is not None]
        nm_vals = [ev[key] for ev in mature_false if ev[key] is not None]
        print_stats("Onset + Mature",     m_vals)
        print_stats("Onset + Non-mature", nm_vals)

    # ── 연도별 분해 (참고용) ──────────────────────────────────────────────
    print("\n[ 연도별 Onset count 및 20D alpha (참고) ]")
    for year in range(2020, 2027):
        yr_ev = [ev for ev in events if ev["T0"].startswith(str(year))]
        yr_al = [ev["alpha_20d"] for ev in yr_ev if ev.get("alpha_20d") is not None]
        if not yr_ev:
            continue
        mean_s = f"{statistics.mean(yr_al)*100:+.2f}%" if yr_al else "N/A"
        hit_s  = f"{sum(1 for v in yr_al if v>0)/len(yr_al)*100:.0f}%" if yr_al else "N/A"
        print(f"  {year}: n={len(yr_ev):4}  mean={mean_s}  hit={hit_s}")

    # ── 판정 ─────────────────────────────────────────────────────────────
    print("\n[ 판정 기준 적용 ]")
    onset_20d = [ev["alpha_20d"] for ev in events if ev.get("alpha_20d") is not None]
    baseline_20d = build_baseline(events, flow, ohlcv, pidx, 20)
    n = len(onset_20d)
    if n < INCONCLUSIVE_N:
        print(f"  n={n} < {INCONCLUSIVE_N} → INCONCLUSIVE")
    elif n < 60:
        print(f"  n={n}: 30≤n<60 → 방향 참고 / 최종 판정 유보")
        hit = sum(1 for v in onset_20d if v > 0) / n
        print(f"  Onset 20D hit rate={hit*100:.1f}% (기준: >60%)")
    else:
        hit    = sum(1 for v in onset_20d if v > 0) / n
        median = statistics.median(onset_20d)
        b_med  = statistics.median(baseline_20d) if baseline_20d else 0
        print(f"  n={n} ≥ 60 → Primary criterion 적용")
        print(f"  Onset 20D: median={median*100:+.2f}%  hit={hit*100:.1f}%")
        print(f"  Baseline 20D: median={b_med*100:+.2f}%")
        onset_pos  = median > 0 and hit > 0.60
        onset_beat = median > b_med
        if onset_pos:
            print("  → 가설 지지 (median>0 AND hit>60%)")
        else:
            print("  → 가설 미지지")
        if onset_beat:
            print("  → Onset이 Baseline보다 우수")
        else:
            print("  → Onset이 Baseline과 유사하거나 열위")


if __name__ == "__main__":
    main()
