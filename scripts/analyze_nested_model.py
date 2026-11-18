"""
Stage 20B: Nested-Model Incremental Explanatory Power

사전등록 (2026-10-01):
  "Risk × Regime interaction 계수(β)가 PBR 및 Market Cap exposure를 순차적으로
   추가했을 때 얼마나 감소하는지 확인한다. Risk proxy는 Naver risk_score와
   DART PIT debt_ratio를 별도로 사용. 핵심 측정값: M0 대비 M4에서의
   bear×risk_z β 감소폭과 ΔR²."

모델 구조 (Risk proxy별로 각각 실행):
  M0: alpha_z ~ bear + risk_z + bear:risk_z
  M1: M0 + pbr_z
  M2: M0 + log_cap_z
  M3: M0 + pbr_z + log_cap_z
  M4: M3 + bear:pbr_z + bear:log_cap_z

핵심 비교:
  bear:risk_z β가 M0 → M4에서 얼마나 변하는가
  → 크게 줄면 Risk×Regime 일부가 exposure composition으로 흡수됨
  → 거의 안 변하면 Risk×Regime은 PBR/Cap exposure와 독립적

Risk proxy 방향 (주의):
  risk_score: 높을수록 낮은 위험 → bear:risk_z β > 0 예상 (Bear에서 낮은 risk가 좋음)
  debt_ratio: 높을수록 높은 위험 → bear:debt_z β < 0 예상 (Bear에서 낮은 부채가 좋음)
  출력 시 방향을 일치시켜 비교하기 위해 debt_ratio를 부호 반전하여 'safety_z'로 표시.

alpha_20d: KOSPI 대비 20D 초과수익률 (%)
SE: clustered by signal_date
표준화: signal_date별 cross-sectional z-score

실행:
    cd ~/Desktop/investment
    python3 scripts/analyze_nested_model.py
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
    print("ERROR: statsmodels 없음. pip3 install statsmodels --break-system-packages")
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
# 데이터 로드
# ---------------------------------------------------------------------------

def load_data() -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as c:
        df = pd.read_sql_query(
            """
            SELECT
                r.signal_date,
                r.ticker,
                r.grade,
                r.alpha_20d,
                r.risk_score,
                u.pbr,
                u.market_cap,
                d.debt_ratio
            FROM retro_calibration r
            JOIN universe_exposure u
              ON r.signal_date = u.signal_date AND r.ticker = u.ticker
            LEFT JOIN dart_pit_financials d
              ON r.ticker = d.ticker AND r.signal_date = d.as_of_date
            WHERE r.alpha_20d  IS NOT NULL
              AND u.pbr        IS NOT NULL AND u.pbr > 0
              AND u.market_cap IS NOT NULL AND u.market_cap > 0
              AND r.risk_score IS NOT NULL
            """,
            c,
        )

    # regime
    regime_map = load_kospi_regime()
    df["regime"] = df["signal_date"].map(regime_map).fillna("Unclassified")
    df = df[df["regime"].isin(["Bull", "Bear"])].copy()
    df["bear"] = (df["regime"] == "Bear").astype(int)

    # log cap
    df["log_cap"] = np.log(df["market_cap"])

    # safety_z용: debt_ratio 부호 반전 → 낮은 부채 = 높은 safety
    # (risk_score와 방향 일치: 높을수록 좋음)
    df["safety"] = -df["debt_ratio"]   # NaN은 NaN 유지

    # signal_date별 표준화
    for col in ["alpha_20d", "risk_score", "pbr", "log_cap", "debt_ratio", "safety"]:
        df[f"{col}_z"] = df.groupby("signal_date")[col].transform(
            lambda x: (x - x.mean()) / (x.std() + 1e-12)
        )

    return df


# ---------------------------------------------------------------------------
# Nested model 실행
# ---------------------------------------------------------------------------

# RHS 템플릿: RISK는 실행 시 실제 컬럼명으로 치환됨
MODEL_SPECS = [
    ("M0", "bear + RISK + bear:RISK"),
    ("M1", "bear + RISK + pbr_z + bear:RISK"),
    ("M2", "bear + RISK + log_cap_z + bear:RISK"),
    ("M3", "bear + RISK + pbr_z + log_cap_z + bear:RISK"),
    ("M4", "bear + RISK + pbr_z + log_cap_z + bear:RISK + bear:pbr_z + bear:log_cap_z"),
]


def run_series(df: pd.DataFrame, risk_col_z: str, series_label: str) -> None:
    """risk_col_z: 'risk_score_z' or 'safety_z' (debt_ratio 부호 반전)"""
    mask = df[risk_col_z].notna()
    d = df[mask].copy()

    print(f"\n{'='*68}")
    print(f"Series {series_label}  (risk proxy = {risk_col_z})  n={len(d)}")
    print(f"{'='*68}")

    print(f"\n{'모델':>4}  {'R²':>7}  {'bear:risk β':>12}  {'t':>7}  {'p':>7}  {'Δβ vs M0':>10}")
    print("  " + "-" * 56)

    m0_beta = None

    for m_name, rhs_template in MODEL_SPECS:
        rhs = rhs_template.replace("RISK", risk_col_z)
        formula = f"alpha_20d_z ~ {rhs}"

        try:
            model = smf.ols(formula, data=d).fit(
                cov_type="cluster",
                cov_kwds={"groups": d["signal_date"]},
            )
            # bear:risk_col_z 키 탐색 (statsmodels는 'bear:risk_col_z' 또는 'risk_col_z:bear')
            int_key = next(
                (k for k in model.params.index
                 if risk_col_z in k and "bear" in k),
                None,
            )
            if int_key is None:
                print(f"  {m_name}: interaction key 없음  params={list(model.params.index)}")
                continue

            beta = model.params[int_key]
            t    = model.tvalues[int_key]
            p    = model.pvalues[int_key]
            r2   = model.rsquared

            delta_beta = 0.0 if m0_beta is None else beta - m0_beta
            if m0_beta is None:
                m0_beta = beta

            sig = "★" if p < 0.05 else " "
            print(f"  {m_name:>4}  {r2:>7.4f}  {beta:>+12.4f}  {t:>+7.2f}  {p:>7.4f}{sig}  {delta_beta:>+10.4f}")

        except Exception as e:
            print(f"  {m_name}: OLS 오류 — {e}")

    # M4 전체 계수
    print(f"\n  --- M4 전체 계수 ---")
    rhs_m4 = MODEL_SPECS[-1][1].replace("RISK", risk_col_z)
    try:
        m4 = smf.ols(f"alpha_20d_z ~ {rhs_m4}", data=d).fit(
            cov_type="cluster",
            cov_kwds={"groups": d["signal_date"]},
        )
        for name in m4.params.index:
            b = m4.params[name]; t = m4.tvalues[name]; p = m4.pvalues[name]
            sig = " ★" if p < 0.05 else ""
            print(f"    {name:>40}  β={b:>+8.4f}  t={t:>+7.2f}  p={p:.4f}{sig}")
    except Exception as e:
        print(f"  M4 오류: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 68)
    print("Stage 20B: Nested-Model Incremental Explanatory Power")
    print("사전등록 (2026-10-01)")
    print("-" * 68)
    print("핵심: bear:risk_z β가 M0→M4에서 얼마나 변하는가")
    print("  크게 감소 → Risk×Regime 일부가 exposure composition으로 흡수")
    print("  거의 유지 → Risk×Regime은 PBR/Cap exposure와 독립적")
    print()
    print("Series A: risk_score_z (Naver current, leakage 가능성)")
    print("Series B: safety_z (= -debt_ratio_z, DART PIT-clean)")
    print("=" * 68)

    print("\n데이터 로드 중...", end=" ", flush=True)
    df = load_data()
    print(f"완료  base n={len(df)}  (dart_pit join 포함: {df['safety'].notna().sum()})")

    n_bear = (df["regime"] == "Bear").sum()
    n_bull = (df["regime"] == "Bull").sum()
    print(f"Bear={n_bear}  Bull={n_bull}  (dates: Bear={df[df['regime']=='Bear']['signal_date'].nunique()}, Bull={df[df['regime']=='Bull']['signal_date'].nunique()})")

    # Series A: Naver risk_score
    run_series(df, "risk_score_z", "A (Naver risk_score)")

    # Series B: DART PIT safety (= -debt_ratio)
    run_series(df, "safety_z", "B (DART PIT safety = -debt_ratio)")

    print("\n\n완료.")


if __name__ == "__main__":
    main()
