"""
Stage 20C: Naver risk_score Partialling-Out Analysis

사전등록 (2026-10-01):
  "Naver risk_score에서 DART PIT debt_ratio를 선형 partialling-out한 잔차가
   Bear interaction을 유지하는지 확인한다.
   핵심: residual × Bear β 및 p-value의 원래 risk_score × Bear 대비 변화."

risk_score 구성 (analyzer/scoring/risk.py 확인):
  25점 기준 감점 항목:
    - debt_ratio_pct : 임계값 기반 이진 감점
    - quarters_with_loss : 적자 분기 수 (최근 4분기)
    - dilution_pct : 주식 희석률 YoY
    - volatility_pct : 주가 변동성
    - governance_flag : 지배구조 이슈
  데이터 소스: Naver current (look_ahead 가능성 있음)

Partialling-out 한계:
  1. risk_score의 debt_ratio 반영이 이진(threshold-based)이므로 선형 OLS는 근사적
  2. Naver debt_ratio ≠ DART PIT debt_ratio → 잔차에 소스 차이도 포함됨
  3. 잔차 = loss_quarters + dilution + volatility + governance + Naver/DART 차이

단계:
  [1단계] Partialling-out: pooled OLS (signal_date별 z-score 표준화 후)
    residual_A: risk_score_z ~ debt_ratio_z
    residual_B: risk_score_z ~ debt_ratio_z + pbr_z + log_cap_z

  [2단계] Bear interaction 비교 (clustered SE by signal_date)
    M_orig:    alpha_z ~ bear + risk_score_z + bear:risk_score_z     (기준)
    M_debt:    alpha_z ~ bear + debt_ratio_z + bear:debt_ratio_z      (debt_ratio만)
    M_res_A:   alpha_z ~ bear + residual_A_z + bear:residual_A_z
    M_res_B:   alpha_z ~ bear + residual_B_z + bear:residual_B_z

해석:
  잔차 interaction 유의 + 크다
    → risk_score가 debt_ratio 이외 정보(loss/dilution/volatility/governance)를 포착
  잔차 interaction 약화/소멸
    → Naver risk_score×Bear의 상당 부분이 debt_ratio-related information에 기인

  단, 잔차 유의성 유지가 곧 "진짜 quality 정보"를 의미하지는 않음 —
  정보시점 차이(Naver current-data leakage)가 여전히 대안 설명으로 유효함.

실행:
    cd ~/Desktop/investment
    python3 scripts/analyze_risk_partialling.py
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
# 데이터 로드
# ---------------------------------------------------------------------------

def load_data() -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as c:
        df = pd.read_sql_query(
            """
            SELECT
                r.signal_date, r.ticker, r.alpha_20d, r.risk_score,
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

    return df


# ---------------------------------------------------------------------------
# 1단계: Partialling-out
# ---------------------------------------------------------------------------

def partial_out(df: pd.DataFrame, y_col: str, x_cols: list[str]) -> pd.Series:
    """
    pooled OLS: y_col ~ x_cols 잔차 반환.
    NaN이 있는 행은 잔차 NaN 처리.
    """
    mask = df[[y_col] + x_cols].notna().all(axis=1)
    d = df[mask].copy()

    formula = f"{y_col} ~ " + " + ".join(x_cols)
    model = smf.ols(formula, data=d).fit()

    resid = pd.Series(index=df.index, dtype=float)
    resid[mask] = model.resid.values

    r2 = model.rsquared
    n  = len(d)
    coefs = dict(zip(model.params.index, model.params.values))
    return resid, r2, n, coefs


# ---------------------------------------------------------------------------
# 2단계: Bear interaction 단일 모델
# ---------------------------------------------------------------------------

def run_interaction(df: pd.DataFrame, risk_z_col: str, label: str) -> tuple[float, float, float, int]:
    """
    alpha_20d_z ~ bear + risk_z_col + bear:risk_z_col
    clustered SE by signal_date.
    반환: (β_interaction, t, p, n)
    """
    mask = df[[risk_z_col, "alpha_20d_z"]].notna().all(axis=1)
    d = df[mask].copy()

    formula = f"alpha_20d_z ~ bear + {risk_z_col} + bear:{risk_z_col}"
    model = smf.ols(formula, data=d).fit(
        cov_type="cluster",
        cov_kwds={"groups": d["signal_date"]},
    )

    int_key = next(
        (k for k in model.params.index if "bear" in k and risk_z_col in k),
        None,
    )
    if int_key is None:
        return float("nan"), float("nan"), float("nan"), len(d)

    beta = model.params[int_key]
    t    = model.tvalues[int_key]
    p    = model.pvalues[int_key]
    return beta, t, p, len(d)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 70)
    print("Stage 20C: Naver risk_score Partialling-Out Analysis")
    print("사전등록 (2026-10-01)")
    print("-" * 70)
    print("risk_score 구성: debt_ratio(이진감점) + loss_quarters + dilution")
    print("                + volatility + governance  [Naver current-data]")
    print("한계: 이진 감점 → 선형 partialling-out은 근사. Naver≠DART 차이 포함.")
    print("=" * 70)

    print("\n데이터 로드 중...", end=" ", flush=True)
    df = load_data()
    n_base = len(df)
    n_with_dart = df["debt_ratio"].notna().sum()
    print(f"완료  n={n_base}  (DART debt_ratio 있음: {n_with_dart})")

    # ── 1단계: partialling-out ──────────────────────────────────────────────
    print("\n[1단계] Partialling-out")

    # Version A: risk_score_z ~ debt_ratio_z
    resid_A, r2_A, n_A, coef_A = partial_out(df, "risk_score_z", ["debt_ratio_z"])
    df["residual_A"] = resid_A
    df["residual_A_z"] = df.groupby("signal_date")["residual_A"].transform(
        lambda x: (x - x.mean()) / (x.std() + 1e-12)
    )
    print(f"\n  Version A: risk_score_z ~ debt_ratio_z")
    print(f"    n={n_A}, R²={r2_A:.4f}")
    for name, coef in coef_A.items():
        print(f"    {name}: {coef:+.4f}")
    print(f"  → R²={r2_A:.4f}: risk_score 분산의 {r2_A*100:.1f}%가 debt_ratio_z로 설명됨")

    # Version B: risk_score_z ~ debt_ratio_z + pbr_z + log_cap_z
    resid_B, r2_B, n_B, coef_B = partial_out(
        df, "risk_score_z", ["debt_ratio_z", "pbr_z", "log_cap_z"]
    )
    df["residual_B"] = resid_B
    df["residual_B_z"] = df.groupby("signal_date")["residual_B"].transform(
        lambda x: (x - x.mean()) / (x.std() + 1e-12)
    )
    print(f"\n  Version B: risk_score_z ~ debt_ratio_z + pbr_z + log_cap_z")
    print(f"    n={n_B}, R²={r2_B:.4f}")
    for name, coef in coef_B.items():
        print(f"    {name}: {coef:+.4f}")
    print(f"  → R²={r2_B:.4f}: risk_score 분산의 {r2_B*100:.1f}%가 debt_ratio+PBR+Cap으로 설명됨")

    # ── 2단계: Bear interaction 비교 ────────────────────────────────────────
    print("\n\n[2단계] Bear × Risk Interaction 비교")
    print()
    print(f"  {'모델':>12}  {'risk proxy':>30}  {'n':>6}  {'bear:risk β':>12}  {'t':>7}  {'p':>7}  {'Δβ vs orig':>12}")
    print("  " + "-" * 90)

    models = [
        ("M_orig",  "risk_score_z",  "Naver risk_score (원래)"),
        ("M_debt",  "debt_ratio_z",  "DART PIT debt_ratio만"),
        ("M_res_A", "residual_A_z",  "잔차A (debt_ratio partialled)"),
        ("M_res_B", "residual_B_z",  "잔차B (debt+PBR+Cap partialled)"),
    ]

    orig_beta = None
    for m_name, risk_col, desc in models:
        try:
            beta, t, p, n = run_interaction(df, risk_col, m_name)
            if orig_beta is None:
                orig_beta = beta
                delta = 0.0
            else:
                delta = beta - orig_beta
            sig = " ★" if p < 0.05 else "  "
            print(f"  {m_name:>12}  {desc:>30}  {n:>6}  {beta:>+12.4f}  {t:>+7.2f}  {p:>7.4f}{sig}  {delta:>+12.4f}")
        except Exception as e:
            print(f"  {m_name}: 오류 — {e}")

    # ── 해석 요약 ────────────────────────────────────────────────────────────
    print("\n\n[해석 요약]")
    print("  M_orig vs M_res_A  : debt_ratio partialling 후 interaction 변화")
    print("  M_orig vs M_res_B  : debt_ratio+PBR+Cap partialling 후 interaction 변화")
    print("  M_debt             : debt_ratio 단독 Bear interaction (비교 기준)")
    print()
    print("  잔차 interaction이 유의하게 남는다")
    print("    → risk_score가 debt_ratio 이외 정보를 포착 (loss/dilution/volatility/governance)")
    print("      단, Naver current-data leakage 가능성은 여전히 대안 설명으로 유효함")
    print("  잔차 interaction이 약화/소멸한다")
    print("    → Naver risk_score×Bear의 상당 부분이 debt_ratio-related information에 기인")
    print()
    print("완료.")


if __name__ == "__main__":
    main()
