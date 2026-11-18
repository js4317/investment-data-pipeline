"""
Stage 20E-1: Q1 Bear vs Q2/Q3+ Bear 날짜 특성 분석

사전등록 (2026-10-01):
  "Q1 Bear에서 residual risk × Bear interaction이 나타나지 않은 원인 파악.
   검증 순서: ① PBR-High Q1 Bear 표본 수(power 문제) → ② KOSPI 하락폭/변동성 차이
   → ③ Exposure composition 차이.
   각 단계 결과가 다음 방향 결정."

갈림길:
  Q1/Q2+ Bear 시장 환경이 확연히 다름
    → "Bear" 단일 regime이 너무 거친 정의일 가능성
  환경은 비슷하나 Q1만 interaction 없음
    → residual risk signal의 시간 안정성 문제
  Q1 PBR-High 표본이 매우 작음
    → statistical power 부족 가능성

분석 구조:
  [1] Bear 날짜 분포: Q1 vs Q2/Q3+ 날짜 수 및 KOSPI 20D return 분포
  [2] 시장 환경: KOSPI 하락 강도, 변동성
  [3] Cross-sectional dispersion: 종목별 alpha 분산 (Q1 vs Q2/Q3+ Bear)
  [4] PBR-High × Q1 표본 수 (power 점검)
  [5] Exposure composition: Q1 vs Q2/Q3+ Bear에서 PBR/Cap tier 분포 비교
  [6] Residual risk 분포: Q1 vs Q2/Q3+ Bear에서 residual의 평균/분산

실행:
    cd ~/Desktop/investment
    python3 scripts/analyze_q1_bear_characteristics.py
"""

from __future__ import annotations

import sqlite3
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT     = Path(__file__).parent.parent
DB_PATH  = ROOT / "database" / "research.db"
OHLCV_DB = ROOT / "database" / "market_ohlcv.db"
KOSPI_CODE = "1001"

try:
    import statsmodels.formula.api as smf
    SM_OK = True
except ImportError:
    SM_OK = False
    print("statsmodels 없음 → interaction 검증 스킵")


# ---------------------------------------------------------------------------
# 레짐 + KOSPI 데이터
# ---------------------------------------------------------------------------

def load_kospi_data() -> tuple[dict[str, str], pd.DataFrame]:
    """(regime_map, kospi_df) 반환. kospi_df: date, open, high, low, close."""
    with sqlite3.connect(OHLCV_DB) as c:
        rows = c.execute(
            "SELECT trade_date, open, high, low, close "
            "FROM market_ohlcv WHERE ticker=? ORDER BY trade_date",
            (KOSPI_CODE,),
        ).fetchall()

    kospi = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close"])
    kospi["date"] = kospi["date"].astype(str)
    kospi = kospi.sort_values("date").reset_index(drop=True)

    dates  = kospi["date"].tolist()
    opens  = dict(zip(kospi["date"], kospi["open"]))
    closes = dict(zip(kospi["date"], kospi["close"]))

    # 20D 보유기간 수익률 + regime
    kospi["ret_20d"] = np.nan
    regime_map: dict[str, str] = {}
    for i, d in enumerate(dates):
        if i + 20 >= len(dates):
            continue
        entry = opens.get(d)
        exit_ = closes.get(dates[i + 20])
        if entry and exit_ and entry != 0:
            ret = exit_ / entry - 1
            kospi.loc[i, "ret_20d"] = ret
            regime_map[d] = "Bull" if ret > 0 else "Bear"

    # daily log return
    kospi["daily_ret"] = np.log(kospi["close"] / kospi["close"].shift(1))

    # 20D realized volatility (rolling, annualized ×√252)
    kospi["vol_20d"] = kospi["daily_ret"].rolling(20).std() * np.sqrt(252)

    return regime_map, kospi


# ---------------------------------------------------------------------------
# universe_exposure + residual
# ---------------------------------------------------------------------------

def load_universe_data(regime_map: dict[str, str]) -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as c:
        df = pd.read_sql_query(
            """
            SELECT
                r.signal_date, r.ticker, r.grade, r.alpha_20d, r.risk_score,
                u.pbr, u.market_cap, u.sector,
                d.debt_ratio
            FROM retro_calibration r
            JOIN universe_exposure u
              ON r.signal_date = u.signal_date AND r.ticker = u.ticker
            LEFT JOIN dart_pit_financials d
              ON r.ticker = d.ticker AND r.signal_date = d.as_of_date
            WHERE r.alpha_20d  IS NOT NULL
              AND r.risk_score IS NOT NULL
              AND u.pbr        IS NOT NULL AND u.pbr > 0
              AND u.market_cap IS NOT NULL AND u.market_cap > 0
            """,
            c,
        )

    df["regime"] = df["signal_date"].map(regime_map).fillna("Unclassified")
    df = df[df["regime"].isin(["Bull", "Bear"])].copy()
    df["bear"] = (df["regime"] == "Bear").astype(int)
    df["log_cap"] = np.log(df["market_cap"])
    df["month"] = df["signal_date"].str[5:7].astype(int)
    df["quarter"] = df["month"].map(lambda m: "Q1" if m <= 3 else "Q2+" )

    # signal_date별 z-score
    for col in ["alpha_20d", "risk_score", "debt_ratio", "pbr", "log_cap"]:
        df[f"{col}_z"] = df.groupby("signal_date")[col].transform(
            lambda x: (x - x.mean()) / (x.std() + 1e-12)
        )

    # PBR / Cap tier (cross-sectional tertile, Stage 20과 동일)
    def _tier(x: pd.Series, labels: list[str]) -> pd.Series:
        if x.nunique() < 3:
            return pd.Series([labels[1]] * len(x), index=x.index)
        q1, q2 = x.quantile([1/3, 2/3])
        return pd.cut(x, bins=[-np.inf, q1, q2, np.inf], labels=labels).astype(str)

    df["pbr_tier"] = df.groupby("signal_date")["pbr"].transform(
        lambda x: _tier(x, ["Low", "Mid", "High"])
    )
    df["cap_tier"] = df.groupby("signal_date")["log_cap"].transform(
        lambda x: _tier(x, ["Small", "Mid", "Large"])
    )

    # partialling-out (Stage 20C Version A: risk_score ~ debt_ratio)
    mask_A = df[["risk_score_z", "debt_ratio_z"]].notna().all(axis=1)
    if SM_OK and mask_A.sum() > 100:
        m_A = smf.ols("risk_score_z ~ debt_ratio_z", data=df[mask_A]).fit()
        df.loc[mask_A, "residual_A"] = m_A.resid.values
        df["residual_A_z"] = df.groupby("signal_date")["residual_A"].transform(
            lambda x: (x - x.mean()) / (x.std() + 1e-12)
        )
    else:
        df["residual_A"] = np.nan
        df["residual_A_z"] = np.nan

    return df


# ---------------------------------------------------------------------------
# 출력 헬퍼
# ---------------------------------------------------------------------------

def pct(x: float) -> str:
    return f"{x*100:+.2f}%" if not np.isnan(x) else "NaN"


def run_interaction(sub: pd.DataFrame, risk_col: str) -> dict:
    if not SM_OK or len(sub) < 30:
        return {"β": float("nan"), "t": float("nan"), "p": float("nan"), "n": len(sub)}
    mask = sub[[risk_col, "alpha_20d_z"]].notna().all(axis=1)
    d = sub[mask].copy()
    n_dates = d["signal_date"].nunique()
    formula = f"alpha_20d_z ~ bear + {risk_col} + bear:{risk_col}"
    try:
        cov = "cluster" if n_dates >= 10 else "HC3"
        kw  = {"groups": d["signal_date"]} if cov == "cluster" else {}
        m = smf.ols(formula, data=d).fit(cov_type=cov, cov_kwds=kw)
        int_key = next((k for k in m.params.index if "bear" in k and risk_col in k), None)
        if int_key is None:
            return {"β": float("nan"), "t": float("nan"), "p": float("nan"), "n": len(d)}
        return {
            "β": m.params[int_key], "t": m.tvalues[int_key],
            "p": m.pvalues[int_key], "n": len(d),
        }
    except Exception:
        return {"β": float("nan"), "t": float("nan"), "p": float("nan"), "n": len(d)}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 70)
    print("Stage 20E-1: Q1 Bear vs Q2/Q3+ Bear 환경 특성 분석")
    print("사전등록 (2026-10-01)")
    print("=" * 70)

    print("\n데이터 로드 중...", end=" ", flush=True)
    regime_map, kospi = load_kospi_data()
    df = load_universe_data(regime_map)
    print(f"완료  n={len(df)}")

    # Bear 날짜 목록
    bear_dates = df[df["regime"] == "Bear"]["signal_date"].unique()
    bear_dates_q1  = [d for d in bear_dates if 1 <= int(d[5:7]) <= 3]
    bear_dates_q2p = [d for d in bear_dates if int(d[5:7]) > 3]

    print(f"\n[Bear 날짜] Q1: {len(bear_dates_q1)}개  Q2+: {len(bear_dates_q2p)}개  합계: {len(bear_dates)}개")

    # ── [1] KOSPI 20D return 분포 ─────────────────────────────────────────
    print("\n" + "─"*60)
    print("[1] KOSPI 20D 보유기간 수익률 분포")
    print("─"*60)
    kospi_bear = kospi[kospi["date"].isin(bear_dates)].copy()
    kospi_bear_q1  = kospi_bear[kospi_bear["date"].apply(lambda d: 1 <= int(d[5:7]) <= 3)]
    kospi_bear_q2p = kospi_bear[kospi_bear["date"].apply(lambda d: int(d[5:7]) > 3)]

    for label, sub in [("Q1 Bear", kospi_bear_q1), ("Q2+ Bear", kospi_bear_q2p)]:
        r = sub["ret_20d"].dropna()
        if len(r) == 0:
            print(f"  {label}: 데이터 없음")
            continue
        print(f"  {label}  n={len(r)}")
        print(f"    mean={pct(r.mean())}  median={pct(r.median())}  "
              f"min={pct(r.min())}  max={pct(r.max())}  std={pct(r.std())}")

    # ── [2] KOSPI 변동성 ──────────────────────────────────────────────────
    print("\n" + "─"*60)
    print("[2] KOSPI 20D Realized Volatility (각 signal_date 기준)")
    print("─"*60)
    kospi_vol = kospi.set_index("date")["vol_20d"]

    for label, dates in [("Q1 Bear", bear_dates_q1), ("Q2+ Bear", bear_dates_q2p)]:
        vols = pd.Series([kospi_vol.get(d, np.nan) for d in dates]).dropna()
        if len(vols) == 0:
            print(f"  {label}: 데이터 없음")
            continue
        print(f"  {label}  n={len(vols)}")
        print(f"    mean={vols.mean()*100:.1f}%  median={vols.median()*100:.1f}%  "
              f"min={vols.min()*100:.1f}%  max={vols.max()*100:.1f}%")

    # ── [3] Cross-sectional alpha 분산 ───────────────────────────────────
    print("\n" + "─"*60)
    print("[3] Cross-sectional Alpha 분산 (종목 간 성과 dispersion)")
    print("─"*60)
    bear_df = df[df["regime"] == "Bear"].copy()
    bear_df["q"] = bear_df["signal_date"].apply(
        lambda d: "Q1" if 1 <= int(d[5:7]) <= 3 else "Q2+"
    )
    for label, sub in [("Q1 Bear", bear_df[bear_df["q"]=="Q1"]),
                        ("Q2+ Bear", bear_df[bear_df["q"]=="Q2+"])]:
        dispersions = sub.groupby("signal_date")["alpha_20d"].std()
        print(f"  {label}  날짜={sub['signal_date'].nunique()}, 종목·날짜={len(sub)}")
        print(f"    signal_date별 alpha std — mean={dispersions.mean():.2f}%  "
              f"median={dispersions.median():.2f}%")

    # ── [4] PBR-High Q1 Bear 표본 수 (power 점검) ────────────────────────
    print("\n" + "─"*60)
    print("[4] PBR-High 표본 수 — Q1 Bear vs Q2+ Bear (power 점검)")
    print("─"*60)
    for qlab, sub in [("Q1 Bear", bear_df[bear_df["q"]=="Q1"]),
                       ("Q2+ Bear", bear_df[bear_df["q"]=="Q2+"])]:
        for tier in ["Low", "Mid", "High"]:
            n = (sub["pbr_tier"] == tier).sum()
            n_dates = sub[sub["pbr_tier"] == tier]["signal_date"].nunique()
            print(f"  {qlab} × PBR-{tier}: {n}행 ({n_dates}개 날짜)")

    # ── [5] Exposure composition 비교 ─────────────────────────────────────
    print("\n" + "─"*60)
    print("[5] Exposure Composition: PBR / Cap tier 분포 비교")
    print("─"*60)
    for qlab, sub in [("Q1 Bear", bear_df[bear_df["q"]=="Q1"]),
                       ("Q2+ Bear", bear_df[bear_df["q"]=="Q2+"])]:
        total = len(sub)
        pbr_dist = sub["pbr_tier"].value_counts(normalize=True).reindex(["Low","Mid","High"])
        cap_dist = sub["cap_tier"].value_counts(normalize=True).reindex(["Small","Mid","Large"])
        print(f"\n  {qlab}  (n={total})")
        print(f"    PBR: Low={pbr_dist.get('Low',0)*100:.1f}%  "
              f"Mid={pbr_dist.get('Mid',0)*100:.1f}%  "
              f"High={pbr_dist.get('High',0)*100:.1f}%")
        print(f"    Cap: Small={cap_dist.get('Small',0)*100:.1f}%  "
              f"Mid={cap_dist.get('Mid',0)*100:.1f}%  "
              f"Large={cap_dist.get('Large',0)*100:.1f}%")

    # ── [6] Residual risk 분포 ────────────────────────────────────────────
    print("\n" + "─"*60)
    print("[6] Residual Risk 분포 (Q1 vs Q2+ Bear)")
    print("─"*60)
    for qlab, sub in [("Q1 Bear", bear_df[bear_df["q"]=="Q1"]),
                       ("Q2+ Bear", bear_df[bear_df["q"]=="Q2+"])]:
        r = sub["residual_A"].dropna()
        print(f"  {qlab}  n={len(r)}")
        if len(r) > 0:
            print(f"    mean={r.mean():.4f}  std={r.std():.4f}  "
                  f"min={r.min():.3f}  max={r.max():.3f}")

    # ── [7] PBR-High 내에서 Q1 vs Q2+ Bear interaction ───────────────────
    print("\n" + "─"*60)
    print("[7] PBR-High에서 Q1 vs Q2+ Bear interaction 재확인")
    print("─"*60)
    pbr_high = df[df["pbr_tier"] == "High"].copy()
    for qlab, sub in [("Q1 Bear+Bull", pbr_high[pbr_high["month"].le(3)]),
                       ("Q2+ Bear+Bull", pbr_high[pbr_high["month"].gt(3)])]:
        r = run_interaction(sub, "residual_A_z")
        sig = "★" if (not np.isnan(r["p"]) and r["p"] < 0.05) else ""
        print(f"  {qlab}  n={r['n']}  "
              f"β={r['β']:+.4f}  t={r['t']:+.2f}  p={r['p']:.4f}{sig}")

    print("\n\n완료.")


if __name__ == "__main__":
    main()
