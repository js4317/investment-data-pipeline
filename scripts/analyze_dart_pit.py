"""
Stage 19: DART PIT 재분석 — Stage 17/18 병렬 비교 (재실행 v2)

collect_dart_pit.py 완료 후 실행.

비교 구조 (사전 등록):
    Naver retro (retro_calibration)  vs  DART PIT-clean (dart_pit_financials)

지표:
    Earnings proxy  → earnings_score  |  pit_op_margin (YoY 불가 시 수준값)
    Risk proxy      → risk_score      |  -pit_debt_ratio (부호 통일: 낮을수록 좋음)

ROE (사전등록):
    reprt_code = 11011 (사업보고서)만 유효. 반기/분기보고서 → NA (누적기간 불일치).

Regime (사전등록):
    kospi_exit_close / kospi_entry_open - 1 > 0 → Bull, ≤ 0 → Bear
    entry = signal_date 당일 시가, exit = 20영업일 후 종가.
    Retrospective forward-regime partition — 실시간 탐지 불가, 연구 전용.

분석:
    [A] Raw IC          : Spearman(factor, alpha_20d)
    [B] Partial IC      : valuation_score 통제 후 잔차 IC
    [C] Risk × Regime   : Δ = Bear_partial_IC - Bull_partial_IC, permutation p, bootstrap CI

사전 등록 해석 (결과 보기 전 고정):
    - DART ≈ Naver (|Δ_IC| < 0.02)          → leakage가 IC를 주도하지 않았음을 지지
    - Earnings + Risk 모두 약화              → Naver 소급 의존 또는 데이터 표현 문제
    - Earnings 소멸 + Risk interaction 유지  → 현재 가설 (Bear에서 Risk 유효) 살아남음
    - Risk interaction 소멸                  → Stage 18은 exploratory finding 수준으로 후퇴

실행:
    cd ~/Desktop/investment
    python3 scripts/analyze_dart_pit.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH    = ROOT / "database" / "research.db"
OHLCV_DB   = ROOT / "database" / "market_ohlcv.db"
KOSPI_CODE = "1001"

DIVIDER = "=" * 60


# ---------------------------------------------------------------------------
# 데이터 로드
# ---------------------------------------------------------------------------

def load_kospi_regime() -> dict[str, str]:
    """
    signal_date → 'Bull' | 'Bear'
    사전등록 레짐 (Stage 19): kospi_exit_close / kospi_entry_open - 1 > 0 → Bull, ≤ 0 → Bear
    entry = signal_date 당일 시가, exit = 20영업일 후 종가.
    Retrospective forward-regime partition: 실시간 탐지 불가. 연구 전용.
    """
    with sqlite3.connect(OHLCV_DB) as c:
        rows = c.execute(
            "SELECT trade_date, open, close FROM market_ohlcv WHERE ticker=? ORDER BY trade_date",
            (KOSPI_CODE,),
        ).fetchall()
    dates  = [r[0] for r in rows]
    opens  = {r[0]: r[1] for r in rows}
    closes = {r[0]: r[2] for r in rows}
    regime_map: dict[str, str] = {}
    for i, d in enumerate(dates):
        if i + 20 >= len(dates):
            continue
        entry_open = opens.get(d)
        exit_close = closes.get(dates[i + 20])
        if entry_open and exit_close and entry_open != 0:
            ret = exit_close / entry_open - 1
            regime_map[d] = "Bull" if ret > 0 else "Bear"
    return regime_map


def load_data(conn: sqlite3.Connection) -> pd.DataFrame:
    sql = """
    SELECT
        r.ticker,
        r.signal_date,
        r.earnings_score,
        r.valuation_score,
        r.risk_score,
        r.alpha_20d,
        d.roe             AS pit_roe,
        d.debt_ratio      AS pit_debt_ratio,
        d.op_margin       AS pit_op_margin,
        d.net_income      AS pit_net_income,
        d.total_equity    AS pit_equity,
        d.reprt_code,
        d.thstrm_nm,
        d.rcept_dt,
        d.error           AS pit_error
    FROM retro_calibration r
    LEFT JOIN dart_pit_financials d
        ON r.ticker = d.ticker AND r.signal_date = d.as_of_date
    WHERE r.alpha_20d IS NOT NULL
      AND r.signal_date >= '2026-01-02'
    """
    df = pd.read_sql(sql, conn, parse_dates=["signal_date"])

    # ROE: 사업보고서(11011)만 유효 — 반기/분기는 누적기간 불일치로 NA 처리
    # 사전등록 정의: 11011 → clean annual ROE, 나머지 → NA
    df.loc[df["reprt_code"] != "11011", "pit_roe"] = None

    # regime 부착 (사전등록: KOSPI 20D 보유기간 수익률, retrospective forward-regime partition)
    regime_map = load_kospi_regime()
    df["regime"] = df["signal_date"].dt.strftime("%Y-%m-%d").map(regime_map).fillna("Unclassified")

    return df


# ---------------------------------------------------------------------------
# 공통 IC 함수
# ---------------------------------------------------------------------------

def raw_ic(df: pd.DataFrame, factor: str, target: str = "alpha_20d") -> tuple[float, float] | tuple[None, None]:
    """Spearman IC across all rows (not per-date). Returns (ic, p)."""
    sub = df.dropna(subset=[factor, target])
    if len(sub) < 30:
        return None, None
    ic, p = stats.spearmanr(sub[factor], sub[target])
    return ic, p


def partial_ic_series(
    df: pd.DataFrame,
    signal: str,
    control: str = "valuation_score",
    target: str = "alpha_20d",
) -> tuple[list[float], list[str]]:
    """
    Per-date OLS residual → Spearman IC per date.
    Returns (ic_list, date_list).
    """
    ics, dates = [], []
    for dt, grp in df.dropna(subset=[signal, control, target]).groupby("signal_date"):
        if len(grp) < 5:
            continue
        x = grp[control].values
        y = grp[signal].values
        try:
            slope, intercept = np.polyfit(x, y, 1)
            resid = y - (slope * x + intercept)
        except Exception:
            continue
        ic, _ = stats.spearmanr(resid, grp[target].values)
        ics.append(ic)
        dates.append(str(dt)[:10])
    return ics, dates


def partial_ic_mean(df, signal, control="valuation_score", target="alpha_20d") -> float | None:
    ics, _ = partial_ic_series(df, signal, control, target)
    return float(np.mean(ics)) if ics else None


# ---------------------------------------------------------------------------
# 커버리지
# ---------------------------------------------------------------------------

def print_coverage(df: pd.DataFrame) -> None:
    total   = len(df)
    covered = df["pit_debt_ratio"].notna().sum()
    print(f"\n{DIVIDER}")
    print("PIT Coverage")
    print(DIVIDER)
    print(f"  Total rows        : {total:,}")
    print(f"  DART 성공 (부채비율 존재) : {covered:,} ({covered/total*100:.1f}%)")
    print(f"  DART 실패/없음    : {total-covered:,}")

    if "reprt_code" in df.columns:
        dist = df["reprt_code"].value_counts().to_dict()
        code_names = {"11011": "사업보고서", "11012": "반기보고서",
                      "11013": "1Q분기보고서", "11014": "3Q분기보고서"}
        print("\n  reprt_code 분포:")
        for code, cnt in sorted(dist.items()):
            print(f"    {code} ({code_names.get(code, '?')}): {cnt}")


# ---------------------------------------------------------------------------
# [A] Raw IC 비교표
# ---------------------------------------------------------------------------

def print_raw_ic_table(df: pd.DataFrame) -> None:
    print(f"\n{DIVIDER}")
    print("[A] Raw IC: Spearman(factor, alpha_20d)")
    print(DIVIDER)
    print(f"{'Factor':<35} {'Naver/retro IC':>15} {'DART PIT IC':>15}")
    print("-" * 67)

    factors = [
        ("earnings_score (Naver)", "earnings_score", None),
        ("risk_score (Naver)",     "risk_score",     None),
        ("PIT op_margin",          None,             "pit_op_margin"),
        ("PIT -debt_ratio",        None,             "pit_neg_debt"),
        ("PIT ROE",                None,             "pit_roe"),
    ]

    df["pit_neg_debt"] = -df["pit_debt_ratio"]  # risk ↑ = debt ↓

    for label, naver_col, dart_col in factors:
        n_ic, n_p = raw_ic(df, naver_col) if naver_col else (None, None)
        d_ic, d_p = raw_ic(df, dart_col)  if dart_col  else (None, None)

        def fmt(ic, p):
            if ic is None:
                return "   N/A"
            return f" {ic:+.4f} (p={p:.3f})"

        print(f"  {label:<33} {fmt(n_ic, n_p):>15} {fmt(d_ic, d_p):>15}")


# ---------------------------------------------------------------------------
# [B] Partial IC 비교표
# ---------------------------------------------------------------------------

def print_partial_ic_table(df: pd.DataFrame) -> None:
    print(f"\n{DIVIDER}")
    print("[B] Partial IC: valuation_score 통제 후 잔차 ~ alpha_20d")
    print(DIVIDER)

    df["pit_neg_debt"] = -df["pit_debt_ratio"]

    rows = [
        ("earnings_score (Naver)",  "earnings_score",  "Naver"),
        ("PIT op_margin",           "pit_op_margin",   "DART"),
        ("risk_score (Naver)",      "risk_score",      "Naver"),
        ("PIT -debt_ratio",         "pit_neg_debt",    "DART"),
        ("PIT ROE",                 "pit_roe",         "DART"),
    ]

    print(f"  {'Factor':<35} {'Overall':>10} {'Bull':>10} {'Bear':>10}")
    print("  " + "-" * 67)

    for label, col, _ in rows:
        overall = partial_ic_mean(df, col)

        bull_df = df[df["regime"] == "Bull"]
        bear_df = df[df["regime"] == "Bear"]
        bull_ic = partial_ic_mean(bull_df, col)
        bear_ic = partial_ic_mean(bear_df, col)

        def f(v):
            return f"{v:+.4f}" if v is not None else "  N/A"

        print(f"  {label:<35} {f(overall):>10} {f(bull_ic):>10} {f(bear_ic):>10}")


# ---------------------------------------------------------------------------
# [C] Risk × Regime Interaction (Stage 18 병렬)
# ---------------------------------------------------------------------------

def permutation_test(bear_ics, bull_ics, n_iter=2000, seed=42):
    rng = np.random.default_rng(seed)
    obs_delta = np.mean(bear_ics) - np.mean(bull_ics)
    combined  = bear_ics + bull_ics
    n_bear    = len(bear_ics)
    null = [np.mean(rng.permutation(combined)[:n_bear]) -
            np.mean(rng.permutation(combined)[n_bear:]) for _ in range(n_iter)]
    p = np.mean(np.array(null) >= obs_delta)
    return obs_delta, p


def bootstrap_ci(values, n_iter=2000, seed=42):
    rng = np.random.default_rng(seed)
    boot = [np.mean(rng.choice(values, size=len(values), replace=True)) for _ in range(n_iter)]
    return np.percentile(boot, [2.5, 97.5])


def print_regime_interaction(df: pd.DataFrame) -> None:
    print(f"\n{DIVIDER}")
    print("[C] Risk × Regime Interaction  (Stage 18 병렬)")
    print(DIVIDER)

    df["pit_neg_debt"] = -df["pit_debt_ratio"]

    for label, col in [("risk_score (Naver)", "risk_score"),
                        ("PIT -debt_ratio",    "pit_neg_debt")]:
        bear_ics, _ = partial_ic_series(df[df["regime"] == "Bear"], col)
        bull_ics, _ = partial_ic_series(df[df["regime"] == "Bull"], col)

        print(f"\n  [{label}]")
        if not bear_ics or not bull_ics:
            print("    데이터 부족")
            continue

        delta, p = permutation_test(bear_ics, bull_ics)
        bear_ci  = bootstrap_ci(bear_ics)

        verdict = ("Interaction 지지" if p < 0.10 and delta > 0
                   else "기각 불가 / 불충분")

        print(f"    Bear dates / Bull dates : {len(bear_ics)} / {len(bull_ics)}")
        print(f"    Bear partial IC (mean)  : {np.mean(bear_ics):+.4f}")
        print(f"    Bull partial IC (mean)  : {np.mean(bull_ics):+.4f}")
        print(f"    Δ (Bear - Bull)         : {delta:+.4f}")
        print(f"    Permutation p-value     : {p:.4f}")
        print(f"    Bear IC 95% CI          : [{bear_ci[0]:+.4f}, {bear_ci[1]:+.4f}]")
        print(f"    판정                    : {verdict}")


# ---------------------------------------------------------------------------
# 사전 등록 해석
# ---------------------------------------------------------------------------

def print_interpretation(df: pd.DataFrame) -> None:
    print(f"\n{DIVIDER}")
    print("사전 등록 해석 (Stage 19 Pre-registration)")
    print(DIVIDER)

    df["pit_neg_debt"] = -df["pit_debt_ratio"]

    earn_naver = partial_ic_mean(df, "earnings_score")
    earn_dart  = partial_ic_mean(df, "pit_op_margin")
    risk_naver = partial_ic_mean(df, "risk_score")
    risk_dart  = partial_ic_mean(df, "pit_neg_debt")

    bear_naver, _ = partial_ic_series(df[df["regime"] == "Bear"], "risk_score")
    bull_naver, _ = partial_ic_series(df[df["regime"] == "Bull"], "risk_score")
    bear_dart,  _ = partial_ic_series(df[df["regime"] == "Bear"], "pit_neg_debt")
    bull_dart,  _ = partial_ic_series(df[df["regime"] == "Bull"], "pit_neg_debt")

    def interaction_holds(bear_ics, bull_ics):
        if not bear_ics or not bull_ics:
            return False
        _, p = permutation_test(bear_ics, bull_ics)
        delta = np.mean(bear_ics) - np.mean(bull_ics)
        return p < 0.10 and delta > 0

    earn_similar   = (earn_naver is not None and earn_dart is not None and
                      abs((earn_dart or 0) - (earn_naver or 0)) < 0.02)
    risk_similar   = (risk_naver is not None and risk_dart is not None and
                      abs((risk_dart or 0) - (risk_naver or 0)) < 0.02)
    earn_collapsed = earn_dart is not None and abs(earn_dart) < 0.02
    interact_naver = interaction_holds(bear_naver, bull_naver)
    interact_dart  = interaction_holds(bear_dart, bull_dart)

    print()
    checks = [
        ("DART ≈ Naver Earnings partial IC (|Δ|<0.02)",
         earn_similar,
         "leakage가 Earnings IC를 주도하지 않았음을 지지"),
        ("DART ≈ Naver Risk partial IC (|Δ|<0.02)",
         risk_similar,
         "leakage가 Risk IC를 주도하지 않았음을 지지"),
        ("Earnings partial IC 소멸 (|DART IC|<0.02)",
         earn_collapsed,
         "Earnings 독립 신호는 Naver 데이터 의존 가능성"),
        ("Risk×Regime interaction (Naver) 유지",
         interact_naver,
         "기존 Stage 18 결론 유지"),
        ("Risk×Regime interaction (DART PIT) 유지",
         interact_dart,
         "Bear 조건부 Risk 신호가 PIT-clean에서도 살아남음"),
    ]

    for desc, result, meaning in checks:
        mark = "✓" if result else "✗"
        print(f"  {mark}  {desc}")
        print(f"       → {meaning}")
        print()

    # 종합 시나리오
    print("  [종합 시나리오]")
    if interact_dart and not earn_collapsed:
        print("  → DART에서도 동일: leakage가 핵심 구조를 만들지 않았음. 연구 지속.")
    elif interact_dart and earn_collapsed:
        print("  → 가장 흥미로운 결과: Earnings 소멸 + Risk interaction 유지.")
        print("     현재 가설(Bear에서 Risk 유효)이 PIT-clean에서도 살아남음.")
    elif not interact_dart and not interact_naver:
        print("  → Stage 18은 exploratory finding 수준. OOS 추가 관찰 필요.")
    else:
        print("  → Naver에서 interaction, DART에서 소멸: Naver 소급 의존 가능성.")
        print("     Stage 18 결론을 conditional로 후퇴시킬 근거.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    conn = sqlite3.connect(DB_PATH)

    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    if "dart_pit_financials" not in tables:
        print("ERROR: dart_pit_financials 없음. collect_dart_pit.py 먼저 실행.")
        conn.close()
        return

    df = load_data(conn)
    conn.close()

    print(f"로드: {len(df):,} rows")
    print_coverage(df)
    print_raw_ic_table(df)
    print_partial_ic_table(df)
    print_regime_interaction(df)
    print_interpretation(df)

    print(f"\n{DIVIDER}")
    print("완료 — 결과를 docs/RESEARCH.md Stage 19 섹션에 기록.")
    print(f"{DIVIDER}")


if __name__ == "__main__":
    main()
