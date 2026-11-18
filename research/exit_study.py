#!/usr/bin/env python3
"""
Exit Study — Score-driven 조기 매도 vs 보유 비교
===================================================
목적: STRONG_BUY/BUY 진입 후 등급 하락 시점에 팔았을 때 vs 20D 보유했을 때의
     수익률 차이를 측정. "Exit가 Entry보다 더 큰 문제인가?"를 진단.

데이터: retro_calibration (signal_date별 grade·score·entry_open·exit_close·alpha_20d)
기간:   2026-01-02 ~ 2026-07-30 (141 영업일)

이벤트 정의 (사전 고정):
  Entry: 종목별 첫 STRONG_BUY 또는 BUY signal_date (T0)
  Exit event: T0 이후 첫 NEUTRAL/AVOID/WATCH signal_date (Te)
  단, 동일 종목의 반복 등급 플립은 무시 — 첫 하락만 사용.

비교 전략 (둘 다 T0 기준):
  score_exit : T0 진입 → Te+1 open 매도 → 이후 현금 (0%)
              return = entry_open[Te] / entry_open[T0] - 1
              alpha  = score_exit_return - KOSPI[T0→Te]

  fixed_hold : T0 진입 → T0+20D close 보유
              return = exit_close[T0] / entry_open[T0] - 1
              alpha  = alpha_20d[T0] (retro_calibration 기준)

Hold − Exit > 0 → 보유가 나았음 (조기 매도가 미래 수익을 놓쳤음)
Hold − Exit < 0 → 조기 매도가 나았음 (등급 하락이 실제 위험 신호)

별도로: entry grade별 20D alpha 분포 (Entry baseline)
"""
from __future__ import annotations
import sqlite3, statistics
from pathlib import Path

ROOT    = Path(__file__).resolve().parent.parent
FLOW_DB = ROOT / "database" / "research.db"
KOSPI   = "1001"

HIGH_GRADES         = {"strong_buy", "buy"}
LOW_GRADES          = {"neutral", "avoid", "watch"}
EXIT_DELAY_MAX_DAYS = 28   # ≈ 20 trading days; Te > T0+20D 이벤트는 비교 불가로 제외

# ── 데이터 로드 ───────────────────────────────────────────────────────────────

def load_retro() -> dict[str, list[dict]]:
    """ticker → sorted list of {signal_date, grade, score, entry_open,
                                exit_close, alpha_20d, kospi_entry, kospi_exit}"""
    with sqlite3.connect(FLOW_DB) as c:
        rows = c.execute("""
            SELECT ticker, signal_date, grade, normalized_score,
                   entry_open, exit_close, alpha_20d,
                   kospi_entry_open, kospi_exit_close
            FROM retro_calibration
            ORDER BY ticker, signal_date
        """).fetchall()
    result: dict[str, list[dict]] = {}
    for ticker, sd, grade, score, e_open, e_close, a20, k_open, k_close in rows:
        result.setdefault(ticker, []).append({
            "date":       sd,
            "grade":      (grade or "").lower(),
            "score":      score,
            "entry_open": e_open,
            "exit_close": e_close,
            "alpha_20d":  a20 / 100.0 if a20 is not None else None,  # %p → decimal
            "k_open":     k_open,
            "k_close":    k_close,
        })
    return result

# ── 이벤트 추출 ───────────────────────────────────────────────────────────────

def extract_events(retro: dict[str, list[dict]]) -> dict[str, list]:
    """
    반환:
      "entries"    : {grade: [alpha_20d, ...]} — entry baseline
      "exit_events": [{"ticker", "entry_grade", "T0", "Te",
                        "hold_alpha", "exit_alpha", "delay_days"}]
    """
    entries:     dict[str, list[float]] = {}
    exit_events: list[dict] = []

    for ticker, history in retro.items():
        state      = "waiting"
        entry_rec  = None

        for rec in history:
            grade = rec["grade"]

            if state == "waiting":
                if grade in HIGH_GRADES and rec["entry_open"] and rec["alpha_20d"] is not None:
                    state     = "in_position"
                    entry_rec = rec
                    # Entry baseline
                    entries.setdefault(grade, []).append(rec["alpha_20d"])

            elif state == "in_position":
                if grade in LOW_GRADES:
                    # 첫 하락 이벤트
                    T0 = entry_rec
                    Te = rec

                    # score_exit: T0+1 진입 → Te+1 open 매도
                    delay = _day_diff(T0["date"], Te["date"])
                    if delay > EXIT_DELAY_MAX_DAYS:
                        state = "done"
                        continue  # Te > T0+20D: 비교 윈도우 불일치, 제외

                    # score_exit: T0+1 진입 → Te+1 open 매도
                    # 벤치마크: T0의 KOSPI 20D 수익률 (fixed_hold와 동일 window)
                    if (Te["entry_open"] and T0["entry_open"]
                            and T0["k_open"] and T0["k_close"]
                            and T0["alpha_20d"] is not None):
                        exit_raw       = Te["entry_open"] / T0["entry_open"] - 1
                        kospi_20d      = T0["k_close"]   / T0["k_open"]     - 1
                        exit_alpha     = exit_raw - kospi_20d   # 0% cash 이후 반영

                        hold_alpha     = T0["alpha_20d"]

                        exit_events.append({
                            "ticker":       ticker,
                            "entry_grade":  T0["grade"],
                            "T0":           T0["date"],
                            "Te":           Te["date"],
                            "entry_score":  T0["score"],
                            "exit_grade":   Te["grade"],
                            "exit_score":   Te["score"],
                            "hold_alpha":   hold_alpha,
                            "exit_alpha":   exit_alpha,
                            "diff":         hold_alpha - exit_alpha,
                            "delay_days":   delay,
                        })
                    state = "done"  # 첫 이벤트만

    return {"entries": entries, "exit_events": exit_events}


def _day_diff(d1: str, d2: str) -> int:
    from datetime import date as dt
    return (dt.fromisoformat(d2) - dt.fromisoformat(d1)).days

# ── 통계 출력 ─────────────────────────────────────────────────────────────────

def print_stats(label: str, vals: list[float]):
    if not vals:
        print(f"  {label}: 데이터 없음")
        return
    mean   = statistics.mean(vals)
    median = statistics.median(vals)
    pos    = sum(1 for v in vals if v > 0)
    win    = pos / len(vals) * 100
    print(f"  {label:<22} n={len(vals):3}  mean={mean*100:+6.2f}%  median={median*100:+6.2f}%  승률={win:.0f}%")


def main():
    print("=" * 68)
    print("Exit Study — Score-driven 조기 매도 vs 20D 보유 비교")
    print("데이터: retro_calibration 2026-01-02~07-30")
    print("=" * 68)

    retro  = load_retro()
    result = extract_events(retro)
    entries     = result["entries"]
    exit_events = result["exit_events"]

    # ── ① Entry Baseline ────────────────────────────────────────────────────
    print("\n[ ① Entry Baseline — 등급별 20D Alpha ]")
    for g in ["strong_buy", "buy"]:
        vals = entries.get(g, [])
        print_stats(g.upper(), vals)

    # ── ② Exit Study (전체) ──────────────────────────────────────────────────
    print(f"\n[ ② Exit Study — 첫 등급 하락 이벤트 (n={len(exit_events)}) ]")
    if not exit_events:
        print("  이벤트 없음")
        return

    hold_alphas = [e["hold_alpha"]  for e in exit_events]
    exit_alphas = [e["exit_alpha"]  for e in exit_events]
    diffs       = [e["diff"]        for e in exit_events]
    delays      = [e["delay_days"]  for e in exit_events]

    print_stats("Fixed Hold (20D)", hold_alphas)
    print_stats("Score Exit",       exit_alphas)
    print_stats("Hold − Exit",      diffs)
    print(f"  평균 등급 하락 감지 지연: {statistics.mean(delays):.0f}일 (중앙값 {statistics.median(delays):.0f}일)")

    # ── ③ Entry Grade별 분리 ─────────────────────────────────────────────────
    print("\n[ ③ Entry Grade별 분리 ]")
    for eg in ["strong_buy", "buy"]:
        ev = [e for e in exit_events if e["entry_grade"] == eg]
        if not ev:
            continue
        print(f"\n  Entry={eg.upper()} (n={len(ev)})")
        print_stats("  Fixed Hold", [e["hold_alpha"] for e in ev])
        print_stats("  Score Exit", [e["exit_alpha"] for e in ev])
        print_stats("  Hold − Exit", [e["diff"] for e in ev])

    # ── ④ 주요 이벤트 목록 ───────────────────────────────────────────────────
    print("\n[ ④ 개별 이벤트 (Hold−Exit 큰 순) ]")
    print(f"  {'종목':<8} {'진입':<12} {'등급하락':<12} {'지연':>5}  {'Hold':>7}  {'Exit':>7}  {'차이':>7}")
    print("  " + "-" * 66)
    for e in sorted(exit_events, key=lambda x: abs(x["diff"]), reverse=True)[:20]:
        print(f"  {e['ticker']:<8} {e['T0']:<12} {e['Te']:<12} {e['delay_days']:>4}일"
              f"  {e['hold_alpha']*100:>+6.1f}%  {e['exit_alpha']*100:>+6.1f}%  {e['diff']*100:>+6.1f}%")

    # ── ⑤ 해석 제한 ─────────────────────────────────────────────────────────
    print("\n[ 해석 제한 ]")
    print("  - retro_calibration은 v8.5.20 기준, look_ahead: earnings/risk=Naver(current)")
    print("  - entry = 종목별 첫 HIGH grade 날. 데이터 시작일(2026-01-02)부터 등급이 높았던")
    print("    종목은 T0가 실제 진입 시점과 다를 수 있음.")
    print("  - 등급 하락 감지 = 다음 signal_date (일별 데이터라 최대 1~2일 지연).")
    print("  - score_exit 이후 현금 보유 가정 (재투자 없음).")


if __name__ == "__main__":
    main()
