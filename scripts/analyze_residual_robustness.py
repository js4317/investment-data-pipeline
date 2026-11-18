"""
Stage 20D: Naver Risk Residual — Bear Interaction Robustness

사전등록 (2026-10-01):
  "Stage 20C에서 확인된 Naver risk_score residual × Bear interaction이
   STRONG_BUY 서브그룹, 기간 분할, PBR tier, Cap tier에서도 방향이
   유지되는지 검증한다. 핵심: bear:residual_z β의 방향 일관성. 크기보다 방향 우선."

분석 구조:
  기반: residual_A (risk_score ~ debt_ratio partialled)
         residual_B (risk_score ~ debt_ratio + pbr + log_cap partialled)
  서브그룹:
    ① 전체 (Stage 20C 기준선)
    ② STRONG_BUY 서브그룹
    ③ 기간별: Q1(2026-01~03) / Q2(2026-04~06) / Q3(2026-07~)
    ④ PBR tier: Low / Mid / High
    ⑤ Cap tier: Small / Mid / Large
  각 서브그룹: alpha_z ~ bear + residual_z + bear:residual_z (clustered SE by signal_date)

판정 기준:
  방향(β 부호)이 일관적 → interaction이 특정 서브샘플에 국한되지 않음을 지지
  특정 서브그룹에서 소멸 → 해당 exposure에서 조건부 효과 가능성
  주의: n이 작은 서브그룹은 clustered SE의 신뢰도 저하

실행:
    cd ~/Desktop/investment
    python3 scripts/analyze_residual_robustness.py
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
    print("ERROR: statsmodels 없음")
    sys.exit(1)


# ---------------------------------------------------------------------------
# 레짐 (Stage 19와 동일)
# ---------------------------------------------------------------------------

def load_kospi_regime() -> dict[str, str]:
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


# ---------------------------------------------------------------------------
# 데이터 로드 (Stage 20C와 동일)
# ---------------------------------------------------------------------------

def load_data() -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as c:
        df = pd.read_sql_query(
            """
            SELECT
                r.signal_date, r.ticker, r.grade, r.alpha_20d, r.risk_score,
                u.pbr, u.market_cap,
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

    regime_map = load_kospi_regime()
    df["regime"] = df["signal_date"].map(regime_map).fillna("Unclassified")
    df = df[df["regime"].isin(["Bull", "Bear"])].copy()
    df["bear"] = (df["regime"] == "Bear").astype(int)
    df["log_cap"] = np.log(df["market_cap"])

    # signal_date별 cross-sectional z-score
    for col in ["alpha_20d", "risk_score", "debt_ratio", "pbr", "log_cap"]:
        df[f"{col}_z"] = df.groupby("signal_date")[col].transform(
            lambda x: (x - x.mean()) / (x.std() + 1e-12)
        )

    # 기간 분류
    df["month"] = df["signal_date"].str[5:7].astype(int)
    df["period"] = pd.cut(
        df["month"],
        bins=[0, 3, 6, 12],
        labels=["Q1(Jan-Mar)", "Q2(Apr-Jun)", "Q3+(Jul-)"],
    )

    # signal_date별 cross-sectional tertile (Stage 20/20A와 동일)
    def _pbr_tier(x: pd.Series) -> pd.Series:
        if x.nunique() < 3:
            return pd.Series(["Mid"] * len(x), index=x.index)
        q1, q2 = x.quantile([1/3, 2/3])
        return pd.cut(x, bins=[-np.inf, q1, q2, np.inf],
                      labels=["Low", "Mid", "High"]).astype(str)

    def _cap_tier(x: pd.Series) -> pd.Series:
        if x.nunique() < 3:
            return pd.Series(["Mid"] * len(x), index=x.index)
        q1, q2 = x.quantile([1/3, 2/3])
        return pd.cut(x, bins=[-np.inf, q1, q2, np.inf],
                      labels=["Small", "Mid", "Large"]).astype(str)

    df["pbr_tier"] = df.groupby("signal_date")["pbr"].transform(_pbr_tier)
    df["cap_tier"]  = df.groupby("signal_date")["log_cap"].transform(_cap_tier)

    return df


# ---------------------------------------------------------------------------
# Partialling-out (Stage 20C와 동일)
# ---------------------------------------------------------------------------

def compute_residuals(df: pd.DataFrame) -> pd.DataFrame:
    mask_A = df[["risk_score_z", "debt_ratio_z"]].notna().all(axis=1)
    mask_B = df[["risk_score_z", "debt_ratio_z", "pbr_z", "log_cap_z"]].notna().all(axis=1)

    # Version A
    dA = df[mask_A].copy()
    m_A = smf.ols("risk_score_z ~ debt_ratio_z", data=dA).fit()
    df.loc[mask_A, "residual_A"] = m_A.resid.values
    r2_A = m_A.rsquared

    # Version B
    dB = df[mask_B].copy()
    m_B = smf.ols("risk_score_z ~ debt_ratio_z + pbr_z + log_cap_z", data=dB).fit()
    df.loc[mask_B, "residual_B"] = m_B.resid.values
    r2_B = m_B.rsquared

    # 잔차 signal_date별 z-score
    for col in ["residual_A", "residual_B"]:
        df[f"{col}_z"] = df.groupby("signal_date")[col].transform(
            lambda x: (x - x.mean()) / (x.std() + 1e-12)
        )

    print(f"  Partialling-out R²: A(debt_ratio)={r2_A:.4f}, B(+PBR+Cap)={r2_B:.4f}")
    return df


# ---------------------------------------------------------------------------
# Bear interaction 단일 모델
# ---------------------------------------------------------------------------

def run_ols(sub: pd.DataFrame, risk_col: str) -> dict:
    """alpha_z ~ bear + risk_col + bear:risk_col (clustered SE)"""
    mask = sub[[risk_col, "alpha_20d_z"]].notna().all(axis=1)
    d = sub[mask].copy()

    n_bear  = (d["bear"] == 1).sum()
    n_bull  = (d["bear"] == 0).sum()
    n_dates = d["signal_date"].nunique()

    if len(d) < 30 or n_bear < 10 or n_bull < 10:
        return {"β": float("nan"), "t": float("nan"), "p": float("nan"),
                "n": len(d), "n_bear": n_bear, "n_bull": n_bull, "n_dates": n_dates}

    formula = f"alpha_20d_z ~ bear + {risk_col} + bear:{risk_col}"
    try:
        # cluster 수가 충분할 때만 clustered SE
        if n_dates >= 10:
            model = smf.ols(formula, data=d).fit(
                cov_type="cluster",
                cov_kwds={"groups": d["signal_date"]},
            )
        else:
            model = smf.ols(formula, data=d).fit(cov_type="HC3")

        int_key = next(
            (k for k in model.params.index if "bear" in k and risk_col in k),
            None,
        )
        if int_key is None:
            return {"β": float("nan"), "t": float("nan"), "p": float("nan"),
                    "n": len(d), "n_bear": n_bear, "n_bull": n_bull, "n_dates": n_dates}

        return {
            "β": model.params[int_key],
            "t": model.tvalues[int_key],
            "p": model.pvalues[int_key],
            "n": len(d),
            "n_bear": n_bear,
            "n_bull": n_bull,
            "n_dates": n_dates,
        }
    except Exception as e:
        return {"β": float("nan"), "t": float("nan"), "p": float("nan"),
                "n": len(d), "n_bear": n_bear, "n_bull": n_bull, "n_dates": n_dates,
                "error": str(e)}


# ---------------------------------------------------------------------------
# 서브그룹 표 출력
# ---------------------------------------------------------------------------

def print_robustness_table(
    df: pd.DataFrame,
    residual_col: str,
    label: str,
    subgroups: list[tuple[str, pd.DataFrame]],
) -> None:
    print(f"\n{'='*72}")
    print(f"{label}  (residual = {residual_col})")
    print(f"{'='*72}")
    print(f"  {'서브그룹':>22}  {'n':>6}  {'Bear':>5}  {'Bull':>5}  {'β':>8}  {'t':>7}  {'p':>7}  {'sig':>4}")
    print("  " + "-" * 68)

    for name, sub in subgroups:
        res = run_ols(sub, residual_col)
        β   = res["β"]
        t   = res["t"]
        p   = res["p"]
        sig = "★" if (not np.isnan(p) and p < 0.05) else ("-" if (not np.isnan(p) and p < 0.1) else " ")
        β_s = f"{β:>+8.4f}" if not np.isnan(β) else f"{'NaN':>8}"
        t_s = f"{t:>+7.2f}" if not np.isnan(t) else f"{'NaN':>7}"
        p_s = f"{p:>7.4f}" if not np.isnan(p) else f"{'NaN':>7}"
        print(f"  {name:>22}  {res['n']:>6}  {res['n_bear']:>5}  {res['n_bull']:>5}  {β_s}  {t_s}  {p_s}  {sig:>4}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 72)
    print("Stage 20D: Naver Risk Residual — Bear Interaction Robustness")
    print("사전등록 (2026-10-01)")
    print("-" * 72)
    print("핵심: bear:residual_z β의 방향 일관성. 크기보다 방향 우선.")
    print("=" * 72)

    print("\n데이터 로드 중...", end=" ", flush=True)
    df = load_data()
    print(f"완료  n={len(df)}")

    print("\n잔차 계산 중...")
    df = compute_residuals(df)

    # 서브그룹 목록 정의
    # universe_exposure의 pbr_tier, cap_tier 컬럼 사용 (Stage 20과 동일한 기준)
    subgroups_base = [
        ("① 전체",                    df),
        ("② STRONG_BUY",             df[df["grade"] == "strong_buy"]),
        ("③ Q1(Jan-Mar)",             df[df["period"] == "Q1(Jan-Mar)"]),
        ("③ Q2(Apr-Jun)",             df[df["period"] == "Q2(Apr-Jun)"]),
        ("③ Q3+(Jul-)",               df[df["period"] == "Q3+(Jul-)"]),
        ("④ PBR-Low",                 df[df["pbr_tier"] == "Low"]),
        ("④ PBR-Mid",                 df[df["pbr_tier"] == "Mid"]),
        ("④ PBR-High",                df[df["pbr_tier"] == "High"]),
        ("⑤ Cap-Small",               df[df["cap_tier"] == "Small"]),
        ("⑤ Cap-Mid",                 df[df["cap_tier"] == "Mid"]),
        ("⑤ Cap-Large",               df[df["cap_tier"] == "Large"]),
    ]

    for residual_col, label in [
        ("residual_A_z", "Version A: debt_ratio partialled"),
        ("residual_B_z", "Version B: debt_ratio + PBR + Cap partialled"),
    ]:
        print_robustness_table(df, residual_col, label, subgroups_base)

    print("\n\n[방향 일관성 기준]")
    print("  모든(또는 대부분) 서브그룹에서 β > 0 → Bear에서 residual risk가 방어적")
    print("  특정 서브그룹에서 β < 0 또는 소멸 → 해당 exposure에서 조건부 효과")
    print("\n완료.")


if __name__ == "__main__":
    main()
