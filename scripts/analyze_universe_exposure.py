"""
Stage 20: Universe Exposure / Benchmark Decomposition

사전등록 질문:
    Universe의 상대성과가 "방어적 저베타 전략" 때문인가,
    특정 업종·시총·PBR 노출 때문인가?

분석 구조 (사전등록):
    [A] 업종별 조건부 alpha          — sector IS NOT NULL
    [B] 시총 분위별 조건부 alpha      — market_cap IS NOT NULL
    [C] PBR 분위별 조건부 alpha       — pbr IS NOT NULL
    [D] 조건부 회귀: alpha ~ 업종 + log(market_cap) + PBR
    [E] source sanity check           — snapshot_val vs pykrx 평균 alpha 비교

시총 분위:
    signal_date별 cross-sectional tertile (33.3%/66.7%).
    "해당 시점 universe에서 상대적으로 대형/중형/소형이었는가?"
    kospi200 컬럼 미사용 (universe 135종목 제한으로 KOSPI200 membership 판별 불가).

Null 처리:
    각 exposure 분석에서 해당 컬럼 IS NOT NULL 종목만 사용.
    n을 항상 표시.

Secondary:
    분기별(Q1/Q2/Q3) 동일 분해 — Q2 -10.92% excess return 원인 추적.

실행:
    cd ~/Desktop/investment
    python3 scripts/analyze_universe_exposure.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT    = Path(__file__).parent.parent
DB_PATH = ROOT / "database" / "research.db"
DIVIDER = "=" * 65


# ---------------------------------------------------------------------------
# 데이터 로드
# ---------------------------------------------------------------------------

def load_data(conn: sqlite3.Connection) -> pd.DataFrame:
    sql = """
    SELECT
        u.signal_date,
        u.ticker,
        u.grade,
        u.alpha_20d,
        u.sector,
        u.market_cap,
        u.pbr,
        u.per,
        u.source
    FROM universe_exposure u
    WHERE u.alpha_20d IS NOT NULL
    ORDER BY u.signal_date, u.ticker
    """
    df = pd.read_sql(sql, conn, parse_dates=["signal_date"])
    return df


def add_market_cap_tertile(df: pd.DataFrame) -> pd.DataFrame:
    """
    signal_date별 cross-sectional market_cap tertile.
    Small / Mid / Large (1/2/3).
    """
    df = df.copy()
    df["cap_tier"] = np.nan

    for dt, grp in df[df["market_cap"].notna()].groupby("signal_date"):
        q33 = grp["market_cap"].quantile(1 / 3)
        q67 = grp["market_cap"].quantile(2 / 3)
        tier = pd.cut(
            grp["market_cap"],
            bins=[-np.inf, q33, q67, np.inf],
            labels=["Small", "Mid", "Large"],
        )
        df.loc[grp.index, "cap_tier"] = tier.astype(str)

    return df


def add_quarter(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    month = df["signal_date"].dt.month
    df["quarter"] = pd.cut(
        month,
        bins=[0, 3, 6, 9, 12],
        labels=["Q1", "Q2", "Q3", "Q4"],
    ).astype(str)
    return df


# ---------------------------------------------------------------------------
# 공통 출력 유틸
# ---------------------------------------------------------------------------

STRONG_BUY_ONLY = True   # Primary 분석은 STRONG_BUY만

def stats_row(ser: pd.Series) -> dict:
    n = len(ser)
    return {
        "n":      n,
        "mean":   ser.mean() if n else np.nan,
        "median": ser.median() if n else np.nan,
        "std":    ser.std()  if n > 1 else np.nan,
    }


def print_exposure_table(
    df: pd.DataFrame,
    group_col: str,
    label: str,
    min_n: int = 10,
) -> None:
    """group_col별 alpha_20d 평균/중앙값/n 출력."""
    sub = df.dropna(subset=[group_col, "alpha_20d"])
    print(f"\n  {label}")
    print(f"  {'Group':<25} {'n':>5} {'Mean%':>8} {'Median%':>9} {'Std':>8}")
    print("  " + "-" * 58)
    rows = []
    for grp, g in sub.groupby(group_col):
        s = stats_row(g["alpha_20d"])
        rows.append((grp, s))
    # mean 기준 내림차순 정렬
    rows.sort(key=lambda x: x[1]["mean"] if not np.isnan(x[1]["mean"]) else -999, reverse=True)
    for grp, s in rows:
        if s["n"] < min_n:
            print(f"  {str(grp):<25} {s['n']:>5}   (n<{min_n}, 생략)")
            continue
        print(f"  {str(grp):<25} {s['n']:>5} {s['mean']:>+8.2f} {s['median']:>+9.2f} {s['std']:>8.2f}")


# ---------------------------------------------------------------------------
# CLAUDE.md §5 업종 분류
# ---------------------------------------------------------------------------

# 적용 가능 업종 (Quality + 저밸류)
IN_SCOPE_KEYWORDS = [
    "통신", "전기·가스", "전기가스", "전기,가스", "유틸", "서비스",
    "음식료", "섬유", "의복", "제약", "의료정밀", "소비",
    "은행", "보험", "증권", "기타금융", "금융",
    "유통", "도소매",
]
# 적용 범위 밖 (Cyclical)
OUT_OF_SCOPE_KEYWORDS = [
    "철강", "금속", "조선", "운송장비·부품", "화학", "정유", "에너지", "해운", "운송", "건설",
]


def classify_scope(sector: str | None) -> str:
    if not sector:
        return "Unknown"
    for kw in IN_SCOPE_KEYWORDS:
        if kw in sector:
            return "In-scope"
    for kw in OUT_OF_SCOPE_KEYWORDS:
        if kw in sector:
            return "Out-of-scope (Cyclical)"
    return "Other"


# ---------------------------------------------------------------------------
# [A] 업종별 조건부 alpha
# ---------------------------------------------------------------------------

def print_sector_analysis(df: pd.DataFrame) -> None:
    print(f"\n{DIVIDER}")
    print("[A] 업종별 조건부 alpha (sector IS NOT NULL)")
    print(DIVIDER)

    sub = df.dropna(subset=["sector", "alpha_20d"])
    print(f"  대상: {len(sub):,}행 ({sub['signal_date'].nunique()}개 날짜)")

    # STRONG_BUY 필터
    sb = sub[sub["grade"] == "strong_buy"]
    print(f"  STRONG_BUY: {len(sb):,}행")

    # §5 scope 분류
    df_sc = sub.copy()
    df_sc["scope"] = df_sc["sector"].apply(classify_scope)
    sb_sc = sb.copy()
    sb_sc["scope"] = sb_sc["sector"].apply(classify_scope)

    print_exposure_table(sb_sc, "scope", "STRONG_BUY — §5 scope 분류")
    print_exposure_table(sb, "sector", "전체 grade — 업종별 alpha", min_n=20)
    print_exposure_table(sb_sc, "sector", "STRONG_BUY — 업종별 alpha")


# ---------------------------------------------------------------------------
# [B] 시총 분위별 조건부 alpha
# ---------------------------------------------------------------------------

def print_cap_analysis(df: pd.DataFrame) -> None:
    print(f"\n{DIVIDER}")
    print("[B] 시총 분위별 조건부 alpha (signal_date별 cross-sectional tertile)")
    print(DIVIDER)

    sub = df.dropna(subset=["cap_tier", "alpha_20d"])
    sb  = sub[sub["grade"] == "strong_buy"]

    print(f"  대상: {len(sub):,}행 (cap_tier 유효)")
    print_exposure_table(sub, "cap_tier", "전체 grade — 시총 분위별 alpha", min_n=20)
    print_exposure_table(sb,  "cap_tier", "STRONG_BUY — 시총 분위별 alpha")

    # 시총 분포: STRONG_BUY에서 Large 비중
    if len(sb) > 0:
        tier_dist = sb["cap_tier"].value_counts(normalize=True) * 100
        print("\n  STRONG_BUY 시총 분위 분포:")
        for tier in ["Large", "Mid", "Small"]:
            pct = tier_dist.get(tier, 0)
            print(f"    {tier}: {pct:.1f}%")


# ---------------------------------------------------------------------------
# [C] PBR 분위별 조건부 alpha
# ---------------------------------------------------------------------------

def print_pbr_analysis(df: pd.DataFrame) -> None:
    print(f"\n{DIVIDER}")
    print("[C] PBR 분위별 조건부 alpha (pbr IS NOT NULL)")
    print(DIVIDER)

    sub = df.dropna(subset=["pbr", "alpha_20d"])
    sb  = sub[sub["grade"] == "strong_buy"]
    print(f"  대상: {len(sub):,}행 (pbr 유효), STRONG_BUY={len(sb):,}")

    # 절대값 기준 PBR 3분위 (signal_date별 cross-sectional)
    df2 = sub.copy()
    df2["pbr_tier"] = np.nan
    for dt, grp in df2.groupby("signal_date"):
        q33 = grp["pbr"].quantile(1 / 3)
        q67 = grp["pbr"].quantile(2 / 3)
        tier = pd.cut(
            grp["pbr"],
            bins=[-np.inf, q33, q67, np.inf],
            labels=["Low", "Mid", "High"],
        )
        df2.loc[grp.index, "pbr_tier"] = tier.astype(str)

    sb2 = df2[df2["grade"] == "strong_buy"]

    print_exposure_table(df2, "pbr_tier", "전체 grade — PBR 분위별 alpha", min_n=20)
    print_exposure_table(sb2, "pbr_tier", "STRONG_BUY — PBR 분위별 alpha")

    # STRONG_BUY PBR 분포 (universe 내 상대적 위치)
    if len(sb2) > 0:
        pbr_dist = sb2["pbr_tier"].value_counts(normalize=True) * 100
        print("\n  STRONG_BUY PBR 분위 분포:")
        for tier in ["Low", "Mid", "High"]:
            pct = pbr_dist.get(tier, 0)
            n = (sb2["pbr_tier"] == tier).sum()
            print(f"    {tier}: {pct:.1f}% (n={n})")


# ---------------------------------------------------------------------------
# [D] 조건부 회귀: alpha ~ log(market_cap) + pbr (업종 더미 포함)
# ---------------------------------------------------------------------------

def print_regression(df: pd.DataFrame) -> None:
    print(f"\n{DIVIDER}")
    print("[D] 조건부 회귀: alpha_20d ~ log(market_cap) + pbr + 업종")
    print(DIVIDER)
    print("  (인과효과 주장 불가 — '어떤 노출과 alpha가 함께 나타났는가' 기술적 분석)")

    sub = df.dropna(subset=["alpha_20d", "market_cap", "pbr", "sector"])
    sub = sub[sub["market_cap"] > 0].copy()
    print(f"  대상: {len(sub):,}행")

    if len(sub) < 50:
        print("  표본 부족 — 생략")
        return

    # signal_date 표준화 (날짜 효과 제거)
    sub["alpha_std"] = sub.groupby("signal_date")["alpha_20d"].transform(
        lambda x: (x - x.mean()) / x.std() if x.std() > 0 else x - x.mean()
    )
    sub["log_cap"] = np.log(sub["market_cap"])
    sub["log_cap_std"] = sub.groupby("signal_date")["log_cap"].transform(
        lambda x: (x - x.mean()) / x.std() if x.std() > 0 else x - x.mean()
    )
    sub["pbr_std"] = sub.groupby("signal_date")["pbr"].transform(
        lambda x: (x - x.mean()) / x.std() if x.std() > 0 else x - x.mean()
    )

    # 단순 회귀 (업종 더미 없이 먼저)
    X = sub[["log_cap_std", "pbr_std"]].values
    y = sub["alpha_std"].values
    X_with_const = np.column_stack([np.ones(len(X)), X])

    try:
        coefs, residuals, rank, sv = np.linalg.lstsq(X_with_const, y, rcond=None)
        intercept, b_cap, b_pbr = coefs

        # t-stat 계산
        y_hat = X_with_const @ coefs
        e     = y - y_hat
        n, k  = len(y), 3
        s2    = np.dot(e, e) / (n - k)
        try:
            cov   = s2 * np.linalg.inv(X_with_const.T @ X_with_const)
            se    = np.sqrt(np.diag(cov))
            t_cap = b_cap / se[1]
            t_pbr = b_pbr / se[2]
        except Exception:
            t_cap = t_pbr = np.nan

        print(f"\n  log(market_cap): β={b_cap:+.4f}  t={t_cap:+.2f}")
        print(f"  PBR:             β={b_pbr:+.4f}  t={t_pbr:+.2f}")
        print("  (모든 값 signal_date별 표준화, 업종 더미 없음)")

        if abs(b_cap) > 0.05 and abs(t_cap) > 2:
            direction = "대형주 편향" if b_cap > 0 else "소형주 편향"
            print(f"  → 시총 관계 유의: {direction}")
        else:
            print("  → 시총과 alpha: 명확한 방향성 없음")

        if abs(b_pbr) > 0.05 and abs(t_pbr) > 2:
            direction = "고PBR 편향" if b_pbr > 0 else "저PBR 편향"
            print(f"  → PBR 관계 유의: {direction}")
        else:
            print("  → PBR과 alpha: 명확한 방향성 없음")

    except Exception as e:
        print(f"  회귀 오류: {e}")


# ---------------------------------------------------------------------------
# [E] Source sanity check
# ---------------------------------------------------------------------------

def print_source_sanity(df: pd.DataFrame) -> None:
    print(f"\n{DIVIDER}")
    print("[E] Source sanity check: snapshot_val vs pykrx")
    print(DIVIDER)

    for src, grp in df.groupby("source"):
        s = stats_row(grp["alpha_20d"].dropna())
        sb_s = stats_row(grp[grp["grade"] == "strong_buy"]["alpha_20d"].dropna())
        print(f"\n  [{src}]  전체 n={s['n']}, mean={s['mean']:+.2f}%  |  STRONG_BUY n={sb_s['n']}, mean={sb_s['mean']:+.2f}%")
        date_range = f"{grp['signal_date'].min().date()} ~ {grp['signal_date'].max().date()}"
        print(f"    기간: {date_range}")

    # Δ 산출 (유의미한 차이 있으면 경고)
    src_means = df.groupby("source")["alpha_20d"].mean()
    if len(src_means) == 2:
        delta = abs(src_means.iloc[0] - src_means.iloc[1])
        if delta > 2.0:
            print(f"\n  ⚠️  source별 alpha 차이 {delta:.2f}%p — 원인 조사 권장")
        else:
            print(f"\n  ✓  source별 alpha 차이 {delta:.2f}%p — 혼합 편향 없음")


# ---------------------------------------------------------------------------
# [F] 분기별 분해 (Secondary)
# ---------------------------------------------------------------------------

def print_quarterly_breakdown(df: pd.DataFrame) -> None:
    print(f"\n{DIVIDER}")
    print("[F] 분기별 업종 노출 분해 (Secondary)")
    print(DIVIDER)

    sub = df.dropna(subset=["sector", "alpha_20d"])
    sub = sub[sub["grade"] == "strong_buy"].copy()
    sub["scope"] = sub["sector"].apply(classify_scope)

    print(f"\n  {'Quarter':<8} {'n':>5} {'Mean%':>8} {'In-scope%':>12} {'Out-scope%':>13}")
    print("  " + "-" * 50)

    for qtr in ["Q1", "Q2", "Q3"]:
        qdf = sub[sub["quarter"] == qtr]
        if len(qdf) < 5:
            continue
        mean_a = qdf["alpha_20d"].mean()
        n      = len(qdf)
        in_pct  = (qdf["scope"] == "In-scope").mean() * 100
        out_pct = (qdf["scope"] == "Out-of-scope (Cyclical)").mean() * 100
        print(f"  {qtr:<8} {n:>5} {mean_a:>+8.2f} {in_pct:>12.1f}% {out_pct:>13.1f}%")

    # 업종별 Q2 집중 분석
    print("\n  Q2 업종별 STRONG_BUY alpha (n≥5):")
    q2 = sub[sub["quarter"] == "Q2"]
    if len(q2) > 0:
        print_exposure_table(q2, "sector", "Q2 STRONG_BUY 업종별", min_n=5)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    conn = sqlite3.connect(DB_PATH)

    if not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='universe_exposure'"
    ).fetchone():
        print("ERROR: universe_exposure 없음. collect_universe_exposure.py 먼저 실행.")
        conn.close()
        return

    df = load_data(conn)
    conn.close()

    print(f"로드: {len(df):,}행 ({df['signal_date'].nunique()}개 날짜, {df['ticker'].nunique()}개 종목)")
    grade_dist = df["grade"].value_counts().to_dict()
    print(f"grade 분포: {grade_dist}")

    df = add_market_cap_tertile(df)
    df = add_quarter(df)

    # kospi200 미사용 안내
    print("\n  [Note] kospi200 컬럼 미사용:")
    print("  universe 135종목 → LIMIT 200으로 전부 kospi200=1. KOSPI200 membership 판별 불가.")
    print("  시총 노출은 signal_date별 cross-sectional tertile (Small/Mid/Large) 사용.")

    print_source_sanity(df)
    print_sector_analysis(df)
    print_cap_analysis(df)
    print_pbr_analysis(df)
    print_regression(df)
    print_quarterly_breakdown(df)

    print(f"\n{DIVIDER}")
    print("완료 — 결과를 docs/RESEARCH.md Stage 20 섹션에 기록.")
    print(DIVIDER)


if __name__ == "__main__":
    main()
