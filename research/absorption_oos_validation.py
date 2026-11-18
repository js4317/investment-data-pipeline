#!/usr/bin/env python3
"""
Dip × Absorption — OOS 검증 (2026)
=====================================
사전등록 검증 조건 (변경 불가):
  Train : 2023–2025  (absorption_ratio 분위수 cutoff 도출 기간)
  OOS   : 2026       (스크리너 유니버스)
  Deep dip 필터: return_1d < -5%
  A 조건: foreign_positive AND pension_positive
  분위수 cutoff: train에서 도출, OOS에서 재계산 금지
    Q1/Q2 경계: 0.033
    Q2/Q3 경계: 0.080
    Q3/Q4 경계: 0.160  (Q4 = absorption_ratio > 0.160)

사전등록 성공 기준 (결과 확인 전 고정):
  PRIMARY-1: A-Q4 mean > A-Q1 mean   (흡수 강도 단조성 방향 재현)
  PRIMARY-2: A-Q4 mean > D mean      (신호 vs 무신호)
  Secondary : 크기, hit rate, median, P10 참고 지표

해석 제한:
  2026 유니버스 = 스크리너 기반 (retro_calibration + snapshots).
  전체 한국 주식시장 일반화 불가.
  "신호의 시간적 OOS 재현" 수준의 해석만 허용.
"""
from __future__ import annotations
import json, sqlite3, statistics
from pathlib import Path

ROOT     = Path(__file__).resolve().parent.parent
FLOW_DB  = ROOT / "database" / "research.db"
OHLCV_DB = ROOT / "database" / "market_ohlcv.db"
SNAP_DIR = ROOT / "backtests" / "snapshots"
KOSPI    = "1001"

OOS_START          = "2026-01-01"
DEEP_DIP_THRESHOLD = -0.05          # return_1d < -5%

# ── 사전등록 고정 cutoff (train 2023-2025에서 도출) ───────────────────────────
# absorption_ratio = (foreign_net + pension_net) / (close × volume)
Q_CUTS = [0.033, 0.080, 0.160]     # Q1|Q2 / Q2|Q3 / Q3|Q4 경계
# Q4 = absorption_ratio > 0.160

# ── 데이터 로드 (OOS 기간만) ──────────────────────────────────────────────────
def load_prices_and_tv() -> tuple[dict, dict]:
    with sqlite3.connect(OHLCV_DB) as c:
        rows = c.execute(
            "SELECT ticker, trade_date, open, close, volume FROM market_ohlcv"
        ).fetchall()
    prices, tv = {}, {}
    for ticker, td, o, cl, vol in rows:
        if cl:
            prices[(ticker, td, "close")] = float(cl)
        if o:
            prices[(ticker, td, "open")]  = float(o)
        if vol and cl and float(vol) > 0:
            tv[(ticker, td)] = float(cl) * float(vol)
    return prices, tv

def load_trading_days() -> list[str]:
    with sqlite3.connect(OHLCV_DB) as c:
        return [r[0] for r in c.execute(
            "SELECT DISTINCT trade_date FROM market_ohlcv WHERE ticker=? ORDER BY trade_date",
            (KOSPI,)
        )]

def load_flow_oos() -> dict[tuple[str, str], dict]:
    """OOS 기간 investor_flow만 로드"""
    with sqlite3.connect(FLOW_DB) as c:
        rows = c.execute(
            "SELECT signal_date, ticker, foreign_net, pension_net FROM investor_flow WHERE signal_date >= ?",
            (OOS_START,)
        ).fetchall()
    return {(d, t): {"foreign_net": fn, "pension_net": pn} for d, t, fn, pn in rows}

def load_universe_oos() -> dict[str, set[str]]:
    """OOS 기간 유니버스: retro_calibration + snapshots"""
    universe: dict[str, set[str]] = {}
    with sqlite3.connect(FLOW_DB) as c:
        for d, t in c.execute(
            "SELECT DISTINCT signal_date, ticker FROM retro_calibration WHERE signal_date >= ?",
            (OOS_START,)
        ):
            universe.setdefault(d, set()).add(t)
    retro_dates = set(universe.keys())
    for f in sorted(SNAP_DIR.glob("*.json")):
        raw = json.loads(f.read_text())
        d = raw.get("date") or f.stem
        if d and d >= OOS_START and d not in retro_dates:
            tickers = {e["ticker"] for e in raw.get("entries", []) if "ticker" in e}
            if tickers:
                universe[d] = tickers
    return universe

# ── 레코드 구성 (deep dip만) ─────────────────────────────────────────────────
def build_records(prices, tv, trading_days, flow, universe) -> list[dict]:
    td_idx = {d: i for i, d in enumerate(trading_days)}
    records = []

    for signal_date in sorted(universe):
        if signal_date not in td_idx:
            continue
        idx = td_idx[signal_date]
        if idx == 0 or idx + 20 >= len(trading_days):
            continue

        prev_date  = trading_days[idx - 1]
        entry_date = trading_days[idx + 1]
        exit_date  = trading_days[idx + 20]

        k_entry = prices.get((KOSPI, entry_date, "open")) or prices.get((KOSPI, entry_date, "close"))
        k_exit  = prices.get((KOSPI, exit_date, "close"))
        if not k_entry or not k_exit:
            continue
        kospi_ret = k_exit / k_entry - 1

        for ticker in universe[signal_date]:
            t_close    = prices.get((ticker, signal_date, "close"))
            prev_close = prices.get((ticker, prev_date, "close"))
            if not t_close or not prev_close:
                continue
            return_1d = t_close / prev_close - 1
            if return_1d >= DEEP_DIP_THRESHOLD:
                continue  # deep dip 필터

            t_entry = prices.get((ticker, entry_date, "open")) or prices.get((ticker, entry_date, "close"))
            t_exit  = prices.get((ticker, exit_date, "close"))
            if not t_entry or not t_exit:
                continue
            alpha_20d = (t_exit / t_entry - 1 - kospi_ret) * 100

            inv = flow.get((signal_date, ticker), {})
            fn  = inv.get("foreign_net")
            pn  = inv.get("pension_net")

            daily_tv = tv.get((ticker, signal_date))
            combined_net = (fn or 0) + (pn or 0)
            absorption_ratio = combined_net / daily_tv if daily_tv and daily_tv > 0 else None

            records.append({
                "signal_date":      signal_date,
                "ticker":           ticker,
                "return_1d":        return_1d,
                "alpha_20d":        alpha_20d,
                "foreign_positive": fn is not None and fn > 0,
                "pension_positive": pn is not None and pn > 0,
                "absorption_ratio": absorption_ratio,
            })

    return records

# ── 고정 cutoff 분위수 할당 ──────────────────────────────────────────────────
def fixed_quartile(ar: float | None) -> str | None:
    """train에서 도출한 fixed cutoff 기준 Q1~Q4 할당"""
    if ar is None:
        return None
    if ar <= Q_CUTS[0]:  return "Q1"
    if ar <= Q_CUTS[1]:  return "Q2"
    if ar <= Q_CUTS[2]:  return "Q3"
    return "Q4"

# ── 통계 ─────────────────────────────────────────────────────────────────────
def stats(alphas: list[float]) -> dict:
    if not alphas:
        return {"n": 0, "mean": None, "median": None, "hit_rate": None, "P10": None}
    s = sorted(alphas)
    p10 = s[max(0, int(len(s) * 0.1) - 1)]
    return {
        "n":        len(alphas),
        "mean":     round(statistics.mean(alphas), 2),
        "median":   round(statistics.median(alphas), 2),
        "hit_rate": round(sum(1 for a in alphas if a > 0) / len(alphas) * 100, 1),
        "P10":      round(p10, 2),
    }

def fmt_row(label: str, alphas: list[float]) -> str:
    s = stats(alphas)
    if s["n"] == 0:
        return f"  {label:<22} {'0':>6}"
    return (f"  {label:<22} {s['n']:>6} {s['mean']:>7} {s['median']:>7}"
            f" {s['hit_rate']:>6} {s['P10']:>7}")

# ── main ─────────────────────────────────────────────────────────────────────
def main():
    print("=" * 65)
    print("Dip × Absorption OOS 검증 (2026)")
    print("=" * 65)
    print(f"OOS 기간   : {OOS_START} ~ (스크리너 유니버스)")
    print(f"Deep dip   : return_1d < {DEEP_DIP_THRESHOLD*100:.0f}%")
    print(f"A 조건     : foreign_positive AND pension_positive")
    print(f"고정 cutoff: Q1≤{Q_CUTS[0]} / Q2≤{Q_CUTS[1]} / Q3≤{Q_CUTS[2]} / Q4>{Q_CUTS[2]}")
    print()
    print("◆ 사전등록 성공 기준")
    print("  PRIMARY-1: A-Q4 mean > A-Q1 mean  (단조성 방향)")
    print("  PRIMARY-2: A-Q4 mean > D mean      (신호 vs 무신호)")
    print()

    print("데이터 로딩...")
    prices, tv    = load_prices_and_tv()
    trading_days  = load_trading_days()
    flow          = load_flow_oos()
    universe      = load_universe_oos()

    print("레코드 구성 (deep dip 필터 적용)...")
    records = build_records(prices, tv, trading_days, flow, universe)
    n_dates = len(set(r["signal_date"] for r in records))
    print(f"Deep dip 관측치: {len(records):,}건 ({n_dates}일)\n")

    if not records:
        print("⚠ OOS 기간 관측치 없음 — 데이터 확인 필요")
        return

    # ── A / D 그룹 분리 ──────────────────────────────────────────────────────
    fp = lambda r: r["foreign_positive"]
    pp = lambda r: r["pension_positive"]
    A_all = [r for r in records if fp(r) and pp(r)]
    D_all = [r for r in records if not fp(r) and not pp(r)]

    # ── A 그룹 → 고정 cutoff로 Q1~Q4 분류 ───────────────────────────────────
    q_groups: dict[str, list[float]] = {"Q1": [], "Q2": [], "Q3": [], "Q4": [], "없음": []}
    for r in A_all:
        q = fixed_quartile(r["absorption_ratio"])
        if q is None:
            q_groups["없음"].append(r["alpha_20d"])
        else:
            q_groups[q].append(r["alpha_20d"])

    # ── 출력 ─────────────────────────────────────────────────────────────────
    print("=" * 65)
    print("[ OOS 결과: Deep Dip × Absorption (2026) ]")
    print(f"고정 cutoff: train 2023-2025 도출값")
    print()
    print(f"  {'그룹':<22} {'n':>6} {'mean':>7} {'median':>7} {'hit%':>6} {'P10':>7}")
    print("  " + "-" * 55)
    for q in ["Q1", "Q2", "Q3", "Q4"]:
        label = f"A-{q} (ar>{Q_CUTS[int(q[1])-2]:.3f})" if q != "Q1" else f"A-Q1 (ar≤{Q_CUTS[0]:.3f})"
        print(fmt_row(label, q_groups[q]))
    if q_groups["없음"]:
        print(fmt_row("A-없음(tv=0)", q_groups["없음"]))
    print(fmt_row("D (수급 없음)", [r["alpha_20d"] for r in D_all]))
    print(fmt_row("A 전체", [r["alpha_20d"] for r in A_all]))

    # ── 판정 ─────────────────────────────────────────────────────────────────
    Q1_mean = statistics.mean(q_groups["Q1"]) if q_groups["Q1"] else None
    Q4_mean = statistics.mean(q_groups["Q4"]) if q_groups["Q4"] else None
    D_mean  = statistics.mean(r["alpha_20d"] for r in D_all) if D_all else None

    print()
    print("=" * 65)
    print("[ 사전등록 성공 기준 판정 ]")
    if Q4_mean is not None and Q1_mean is not None:
        p1 = Q4_mean > Q1_mean
        print(f"  PRIMARY-1 (Q4 > Q1): {'✅ PASS' if p1 else '❌ FAIL'}"
              f"  [Q4={Q4_mean:+.2f}% vs Q1={Q1_mean:+.2f}%, diff={Q4_mean-Q1_mean:+.2f}pp]")
    else:
        print("  PRIMARY-1: ⚠ 데이터 부족 (n=0)")

    if Q4_mean is not None and D_mean is not None:
        p2 = Q4_mean > D_mean
        print(f"  PRIMARY-2 (Q4 > D) : {'✅ PASS' if p2 else '❌ FAIL'}"
              f"  [Q4={Q4_mean:+.2f}% vs D={D_mean:+.2f}%, diff={Q4_mean-D_mean:+.2f}pp]")
    else:
        print("  PRIMARY-2: ⚠ 데이터 부족 (n=0)")

    print()
    print("[ 해석 제한 ]")
    print("  - 2026 유니버스는 스크리너 기반 → 전체 주식시장 일반화 불가")
    print("  - 재현 성공 = '신호의 시간적 OOS 재현' 수준의 해석만 허용")
    print("  - close × volume은 실제 거래대금의 근사값")
    print()

    # ── 참고: 월별 breakdown ─────────────────────────────────────────────────
    months = sorted(set(r["signal_date"][:7] for r in records))
    if months:
        print("[ 참고: 월별 관측치 (deep dip A / D 건수) ]")
        print(f"  {'월':>7} {'A n':>6} {'D n':>6}")
        print("  " + "-" * 22)
        for ym in months:
            m_recs = [r for r in records if r["signal_date"][:7] == ym]
            nA = sum(1 for r in m_recs if fp(r) and pp(r))
            nD = sum(1 for r in m_recs if not fp(r) and not pp(r))
            print(f"  {ym:>7} {nA:>6} {nD:>6}")


if __name__ == "__main__":
    main()
