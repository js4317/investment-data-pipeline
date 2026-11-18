"""
Stage 20E-2: Bear Intensity Conditionality

사전등록 (2026-10-01):
  "KOSPI 20D 하락 강도에 따라 residual risk × Bear interaction의 크기가
   달라지는가?"

  Primary:  KOSPI 20D return 연속형 × residual_risk_z
            alpha_z ~ residual_z + kospi_ret_z + residual_z:kospi_ret_z
            핵심 항: residual_z:kospi_ret_z 의 방향 (음 → 하락 강도 클수록 방어)

  Secondary: Bear를 강도별 categorical 분류 후 interaction 비교
             Shallow Bear: 0 > KOSPI 20D return > -10%
             Deep    Bear: KOSPI 20D return ≤ -10%
             → -10% threshold는 20E-1 관찰 후 설정된 exploratory cut

  대상: 전체 표본 / PBR-High subgroup (Stage 20D 핵심 subgroup)

  주의: -10% categorical 결과는 secondary inference. primary는 연속형.

실행:
    cd ~/Desktop/investment
    python3 scripts/analyze_bear_intensity.py
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
except ImportError:
    print("ERROR: statsmodels 없음 — pip3 install statsmodels --break-system-packages")
    sys.exit(1)


# ---------------------------------------------------------------------------
# 레짐 + KOSPI 20D 수익률 (같은 루프에서 동시 추출)
# ---------------------------------------------------------------------------

def load_kospi_regime() -> tuple[dict[str, str], dict[str, float]]:
    """
    returns:
      regime_map:   signal_date -> "Bull" | "Bear"
      kospi_ret_map: signal_date -> KOSPI 20D return (소수, e.g. -0.063)
    """
    with sqlite3.connect(OHLCV_DB) as c:
        rows = c.execute(
            "SELECT trade_date, open, close FROM market_ohlcv "
            "WHERE ticker=? ORDER BY trade_date",
            (KOSPI_CODE,),
        ).fetchall()

    dates  = [r[0] for r in rows]
    opens  = {r[0]: r[1] for r in rows}
    closes = {r[0]: r[2] for r in rows}

    regime_map:    dict[str, str]   = {}
    kospi_ret_map: dict[str, float] = {}

    for i, d in enumerate(dates):
        if i + 20 >= len(dates):
            continue
        entry_open = opens.get(d)
        exit_close = closes.get(dates[i + 20])
        if entry_open and exit_close and entry_open != 0:
            ret = exit_close / entry_open - 1
            regime_map[d]    = "Bull" if ret > 0 else "Bear"
            kospi_ret_map[d] = ret

    return regime_map, kospi_ret_map


# ---------------------------------------------------------------------------
# 데이터 로드
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

    regime_map, kospi_ret_map = load_kospi_regime()

    df["regime"]    = df["signal_date"].map(regime_map).fillna("Unclassified")
    df["kospi_ret"] = df["signal_date"].map(kospi_ret_map)  # 소수 (e.g. -0.063)

    # Bull/Bear 모두 포함 — primary analysis는 연속형이므로 Bear-only 필요 없음
    df = df[df["regime"].isin(["Bull", "Bear"])].copy()
    df["bear"] = (df["regime"] == "Bear").astype(int)
    df["log_cap"] = np.log(df["market_cap"])

    # Cross-sectional z-score (signal_date별)
    for col in ["alpha_20d", "risk_score", "debt_ratio", "pbr", "log_cap"]:
        df[f"{col}_z"] = df.groupby("signal_date")[col].transform(
            lambda x: (x - x.mean()) / (x.std() + 1e-12)
        )

    # kospi_ret_z: date-level 변수이므로 전체 날짜 단위 z-score
    # (signal_date별로 하면 같은 날 모든 종목이 0이 되어 의미 없음)
    date_ret = df.drop_duplicates("signal_date").set_index("signal_date")["kospi_ret"]
    ret_mean = date_ret.mean()
    ret_std  = date_ret.std() + 1e-12
    df["kospi_ret_z"] = df["signal_date"].map(
        lambda d: (kospi_ret_map.get(d, np.nan) - ret_mean) / ret_std
    )

    # PBR tier (signal_date별 3등분)
    def _pbr_tier(x: pd.Series) -> pd.Series:
        if x.nunique() < 3:
            return pd.Series(["Mid"] * len(x), index=x.index)
        q1, q2 = x.quantile([1/3, 2/3])
        return pd.cut(x, bins=[-np.inf, q1, q2, np.inf],
                      labels=["Low", "Mid", "High"]).astype(str)

    df["pbr_tier"] = df.groupby("signal_date")["pbr"].transform(_pbr_tier)

    # Secondary: Bear 강도 분류
    # kospi_ret은 소수 (e.g. -0.063 = -6.3%)
    df["bear_type"] = "Bull"
    df.loc[(df["kospi_ret"] < 0) & (df["kospi_ret"] > -0.10), "bear_type"] = "Shallow_Bear"
    df.loc[ df["kospi_ret"] <= -0.10,                          "bear_type"] = "Deep_Bear"

    return df


# ---------------------------------------------------------------------------
# Partialling-out (Stage 20C/D와 동일)
# ---------------------------------------------------------------------------

def compute_residuals(df: pd.DataFrame) -> pd.DataFrame:
    mask_A = df[["risk_score_z", "debt_ratio_z"]].notna().all(axis=1)
    mask_B = df[["risk_score_z", "debt_ratio_z", "pbr_z", "log_cap_z"]].notna().all(axis=1)

    m_A = smf.ols("risk_score_z ~ debt_ratio_z", data=df[mask_A]).fit()
    df.loc[mask_A, "residual_A"] = m_A.resid.values
    r2_A = m_A.rsquared

    m_B = smf.ols("risk_score_z ~ debt_ratio_z + pbr_z + log_cap_z", data=df[mask_B]).fit()
    df.loc[mask_B, "residual_B"] = m_B.resid.values
    r2_B = m_B.rsquared

    for col in ["residual_A", "residual_B"]:
        df[f"{col}_z"] = df.groupby("signal_date")[col].transform(
            lambda x: (x - x.mean()) / (x.std() + 1e-12)
        )

    print(f"  Partialling R²: A(debt_ratio)={r2_A:.4f}, B(+PBR+Cap)={r2_B:.4f}")
    return df


# ---------------------------------------------------------------------------
# Primary: 연속형 intensity interaction
# ---------------------------------------------------------------------------

def run_continuous(sub: pd.DataFrame, residual_col: str, label: str) -> None:
    """
    alpha_z ~ residual_z + kospi_ret_z + residual_z:kospi_ret_z
    핵심: residual_z:kospi_ret_z 계수
      음수(β < 0) → KOSPI 하락이 클수록(kospi_ret_z 더 음수) residual_risk 높을수록 alpha 높음
                    = Bear intensity conditional defensiveness
    clustered SE by signal_date.
    """
    mask = sub[[residual_col, "kospi_ret_z", "alpha_20d_z"]].notna().all(axis=1)
    d = sub[mask].copy()

    n_dates = d["signal_date"].nunique()
    n       = len(d)

    formula = f"alpha_20d_z ~ {residual_col} + kospi_ret_z + {residual_col}:kospi_ret_z"

    try:
        model = smf.ols(formula, data=d).fit(
            cov_type="cluster",
            cov_kwds={"groups": d["signal_date"]},
        )
    except Exception as e:
        print(f"  [{label}] 오류: {e}")
        return

    # interaction 항 키
    int_key = next(
        (k for k in model.params.index if residual_col in k and "kospi_ret_z" in k),
        None,
    )
    if int_key is None:
        print(f"  [{label}] interaction 항을 찾을 수 없음. params: {list(model.params.index)}")
        return

    β = model.params[int_key]
    t = model.tvalues[int_key]
    p = model.pvalues[int_key]
    sig = "★" if p < 0.05 else ("−" if p < 0.10 else " ")

    # 주효과도 함께 출력
    β_res  = model.params.get(residual_col, np.nan)
    β_kret = model.params.get("kospi_ret_z", np.nan)

    print(f"\n  [{label}]  n={n}, dates={n_dates}")
    print(f"    {residual_col}:  β={β_res:+.4f}")
    print(f"    kospi_ret_z:      β={β_kret:+.4f}")
    print(f"    interaction:      β={β:+.4f}  t={t:+.2f}  p={p:.4f}  {sig}")
    print(f"    해석: {'음수→Bear강도 클수록 residual risk 방어력 증가' if β < 0 else '양수→예상 방향과 반대'}")


# ---------------------------------------------------------------------------
# Secondary: categorical Shallow vs Deep Bear
# ---------------------------------------------------------------------------

def run_categorical(sub: pd.DataFrame, residual_col: str, label: str) -> dict[str, dict]:
    """
    Shallow Bear / Deep Bear 구간별로
      alpha_z ~ residual_z + bear + bear:residual_z
    를 각각 실행 (Bear=1, Bull=0).
    반환: {'Shallow': {...}, 'Deep': {...}}
    """
    results = {}
    for btype, bear_label in [("Shallow_Bear", "Shallow Bear (−0~−10%)"),
                               ("Deep_Bear",    "Deep    Bear (≤ −10%) ")]:
        # 해당 구간 Bear + 전체 Bull
        mask_bear = sub["bear_type"] == btype
        mask_bull = sub["bear_type"] == "Bull"
        d = sub[mask_bear | mask_bull].copy()
        d["bear_local"] = mask_bear[d.index].astype(int)

        mask = d[[residual_col, "alpha_20d_z"]].notna().all(axis=1)
        d = d[mask]

        n        = len(d)
        n_bear   = d["bear_local"].sum()
        n_dates  = d["signal_date"].nunique()

        if n < 30 or n_bear < 10:
            results[btype] = {"β": np.nan, "t": np.nan, "p": np.nan,
                              "n": n, "n_bear": n_bear}
            continue

        formula = f"alpha_20d_z ~ bear_local + {residual_col} + bear_local:{residual_col}"
        try:
            if n_dates >= 10:
                model = smf.ols(formula, data=d).fit(
                    cov_type="cluster",
                    cov_kwds={"groups": d["signal_date"]},
                )
            else:
                model = smf.ols(formula, data=d).fit(cov_type="HC3")
        except Exception as e:
            results[btype] = {"β": np.nan, "t": np.nan, "p": np.nan,
                              "n": n, "n_bear": n_bear, "error": str(e)}
            continue

        int_key = next(
            (k for k in model.params.index if "bear_local" in k and residual_col in k),
            None,
        )
        if int_key is None:
            results[btype] = {"β": np.nan, "t": np.nan, "p": np.nan,
                              "n": n, "n_bear": n_bear}
            continue

        results[btype] = {
            "β": model.params[int_key],
            "t": model.tvalues[int_key],
            "p": model.pvalues[int_key],
            "n": n, "n_bear": n_bear, "n_dates": n_dates,
            "label": bear_label,
        }
    return results


def print_categorical(results: dict, label: str, stage20d_full_beta: float | None = None) -> None:
    print(f"\n  [{label}]")
    print(f"  {'Bear type':>30}  {'n':>6}  {'Bear':>5}  {'β':>8}  {'t':>7}  {'p':>7}  sig")
    print("  " + "-" * 65)
    for btype in ["Shallow_Bear", "Deep_Bear"]:
        r = results.get(btype, {})
        bl = r.get("label", btype)
        β  = r.get("β", np.nan)
        t  = r.get("t", np.nan)
        p  = r.get("p", np.nan)
        n  = r.get("n", 0)
        nb = r.get("n_bear", 0)
        sig = "★" if (not np.isnan(p) and p < 0.05) else ("−" if (not np.isnan(p) and p < 0.1) else " ")
        β_s = f"{β:>+8.4f}" if not np.isnan(β) else f"{'NaN':>8}"
        t_s = f"{t:>+7.2f}" if not np.isnan(t) else f"{'NaN':>7}"
        p_s = f"{p:>7.4f}" if not np.isnan(p) else f"{'NaN':>7}"
        print(f"  {bl:>30}  {n:>6}  {nb:>5}  {β_s}  {t_s}  {p_s}  {sig}")

    if stage20d_full_beta is not None:
        print(f"\n  [참고] Stage 20D 전체 Bear 기준 β ≈ {stage20d_full_beta:+.4f}")
        d_beta = results.get("Deep_Bear", {}).get("β", np.nan)
        s_beta = results.get("Shallow_Bear", {}).get("β", np.nan)
        if not np.isnan(d_beta) and not np.isnan(s_beta):
            print(f"  Δ(Deep − Shallow) = {d_beta - s_beta:+.4f}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 72)
    print("Stage 20E-2: Bear Intensity Conditionality")
    print("사전등록 (2026-10-01)")
    print("-" * 72)
    print("Primary:   KOSPI 20D return 연속형 × residual_z interaction")
    print("Secondary: Shallow(-0~-10%) vs Deep(≤-10%) Bear categorical")
    print("-10% threshold는 exploratory cut (20E-1 관찰 후 설정). Primary가 주된 추론.")
    print("=" * 72)

    print("\n데이터 로드 중...", end=" ", flush=True)
    df = load_data()
    n_bull    = (df["bear"] == 0).sum()
    n_bear    = (df["bear"] == 1).sum()
    n_shallow = (df["bear_type"] == "Shallow_Bear").sum()
    n_deep    = (df["bear_type"] == "Deep_Bear").sum()
    n_dates_bear = df[df["bear"] == 1]["signal_date"].nunique()

    print(f"완료  n={len(df)}  (Bull={n_bull}, Bear={n_bear} [{n_dates_bear}dates])")
    print(f"      Shallow Bear={n_shallow}, Deep Bear={n_deep}")

    # KOSPI 20D return 분포
    bear_df = df[df["bear"] == 1].drop_duplicates("signal_date")
    shallow_df = df[df["bear_type"] == "Shallow_Bear"].drop_duplicates("signal_date")
    deep_df    = df[df["bear_type"] == "Deep_Bear"].drop_duplicates("signal_date")
    print(f"\n  KOSPI 20D return 분포 (Bear dates, %):")
    print(f"    전체 Bear:   mean={bear_df['kospi_ret'].mean()*100:+.2f}%  "
          f"std={bear_df['kospi_ret'].std()*100:.2f}%  n={len(bear_df)}dates")
    print(f"    Shallow Bear: mean={shallow_df['kospi_ret'].mean()*100:+.2f}%  "
          f"n={len(shallow_df)}dates  (threshold: 0~-10%)")
    print(f"    Deep    Bear: mean={deep_df['kospi_ret'].mean()*100:+.2f}%  "
          f"n={len(deep_df)}dates  (threshold: ≤-10%)")

    print("\n잔차 계산 중...")
    df = compute_residuals(df)

    pbr_high = df[df["pbr_tier"] == "High"]

    # Stage 20D 기준선 (전체 bear, residual_A_z — 비교용)
    stage20d_full_beta_A = 0.2058   # Stage 20D 전체 residual_A_z 결과 (기록값)
    stage20d_full_beta_B = 0.1866   # Stage 20D 전체 residual_B_z 결과

    # ────────────────────────────────────────────────────────────────────────
    # [Primary] 연속형 intensity interaction
    # ────────────────────────────────────────────────────────────────────────
    print("\n\n" + "=" * 72)
    print("[Primary] 연속형 KOSPI return × residual_z interaction")
    print("  핵심 β: residual_z:kospi_ret_z")
    print("  음수 → KOSPI 하락 강할수록 residual risk 높은 종목이 더 방어적")
    print("=" * 72)

    for residual_col, label_prefix in [
        ("residual_A_z", "Version A: debt_ratio partialled"),
        ("residual_B_z", "Version B: debt+PBR+Cap partialled"),
    ]:
        print(f"\n── {label_prefix} ──")
        run_continuous(df,       residual_col, "전체")
        run_continuous(pbr_high, residual_col, "PBR-High")

    # ────────────────────────────────────────────────────────────────────────
    # [Secondary] Shallow vs Deep Bear categorical
    # ────────────────────────────────────────────────────────────────────────
    print("\n\n" + "=" * 72)
    print("[Secondary] Shallow vs Deep Bear categorical interaction")
    print("  각 구간 Bear vs 전체 Bull. bear:residual_z β 비교.")
    print("  -10% threshold = exploratory cut (primary가 주된 추론)")
    print("=" * 72)

    for residual_col, label_prefix, ref_beta in [
        ("residual_A_z", "Version A: debt_ratio partialled", stage20d_full_beta_A),
        ("residual_B_z", "Version B: debt+PBR+Cap partialled", stage20d_full_beta_B),
    ]:
        print(f"\n── {label_prefix} ──")

        res_all  = run_categorical(df,       residual_col, "전체")
        res_high = run_categorical(pbr_high, residual_col, "PBR-High")

        print_categorical(res_all,  "전체",    ref_beta)
        print_categorical(res_high, "PBR-High", None)

    # ────────────────────────────────────────────────────────────────────────
    # 판정 기준 요약
    # ────────────────────────────────────────────────────────────────────────
    print("\n\n[사전등록 판정 기준]")
    print("  Primary (연속형):")
    print("    residual_z:kospi_ret_z β < 0, p < 0.05 → Bear intensity conditionality 지지")
    print("    β 방향 불일치 또는 미유의 → 기간 효과(Q1/Q2+)를 intensity만으로 설명하기 어려움")
    print()
    print("  Secondary (categorical):")
    print("    Deep Bear β >> Shallow Bear β → threshold-adjacent pattern 지지")
    print("    두 구간 β 유사 → intensity gradient보다 단순 Bear presence 효과")
    print()
    print("  종합:")
    print("    Primary + Secondary 모두 일치 → Bear intensity conditionality robust")
    print("    불일치 → 조건 특정 어려움, 추가 분석 필요")
    print("\n완료.")


if __name__ == "__main__":
    main()
