#!/usr/bin/env python3
"""
ROE Structural Inversion — 원인 분해
=========================================
핵심 가설:
  흑자 기업 내에서 ROE Q5(고ROE)가 ROE Q1(저ROE)보다 forward alpha가 낮다.
  (objective_validation.py: mean -8.72%, median -4.69% — STRUCTURAL 판정)

  이것이 lottery로 설명되지 않는다면, 세 가지 원인 후보:
  A. Valuation premium 반영: 고ROE 기업은 이미 고PBR에 거래됨 → 기대수익률 낮음
  B. Sector 구성 편향: 고ROE 섹터(IT, 바이오 등)가 체계적으로 낮은 alpha
  C. ROE 추세 효과: 수준(level)이 아니라 변화율(Δ)이 alpha를 설명함
     → ROE가 높아도 하락 중이면 낮은 alpha; ROE가 낮아도 상승 중이면 높은 alpha

검증 설계 (사전 고정):
  Test 1 — Valuation 통제: PBR quintile 내에서 ROE quintile sort → 역전이 남는가?
  Test 2 — Sector 중립: 섹터 내 ROE rank → 역전이 남는가?
  Test 3 — ROE level vs Δ: 전년 ROE 대비 YoY 변화 (ΔROE = ROE_t - ROE_{t-1})
            ΔROE 분위 × forward alpha → level 효과 vs change 효과 분리
  Test 4 — ROE × PBR 교차: 저PBR+고ROE(Value+Quality) vs 고PBR+고ROE(Growth+Quality) 비교

설계 원칙:
  새로운 factor 탐색이 아닌 "기존에 관찰된 역전의 원인 분해"이므로
  researcher degrees of freedom 최소화.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT    = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "database" / "research.db"


def load() -> pd.DataFrame:
    """
    흑자 기업(ni_ytd>0) 한정. 전년도 ROE를 self-join으로 추가해 ΔROE 계산.
    """
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT
            rrs.risk_year, rrs.fwd_year, rrs.ticker, rrs.sector,
            rrs.fwd_alpha, rrs.risk_score,
            rs.roe, rs.pbr, rs.per, rs.market_cap,
            rh.ni_ytd,
            rs_prev.roe AS roe_prev
        FROM rrs_stock_year rrs
        JOIN research_stock_year rs
            ON rs.year = rrs.fwd_year AND rs.ticker = rrs.ticker
        JOIN risk_history rh
            ON rh.year = rrs.risk_year AND rh.ticker = rrs.ticker
        LEFT JOIN research_stock_year rs_prev
            ON rs_prev.year = rrs.fwd_year - 1 AND rs_prev.ticker = rrs.ticker
        WHERE rrs.fwd_alpha IS NOT NULL
          AND rh.ni_ytd > 0          -- 흑자 기업 한정
          AND rs.roe IS NOT NULL
    """, conn)
    conn.close()

    df["d_roe"] = df["roe"] - df["roe_prev"]   # ΔROE (전년 없으면 NaN)
    return df


def robust_block(s: pd.Series) -> dict:
    s = s.dropna()
    n = len(s)
    if n < 10:
        return {}
    sorted_s = np.sort(s.values)
    k_trim = int(n * 0.10)
    trimmed = sorted_s[k_trim: n - k_trim] if k_trim > 0 else sorted_s
    neg = s[s < 0]
    return {
        "n":      n,
        "mean":   round(float(s.mean()), 2),
        "median": round(float(np.median(s)), 2),
        "tmean":  round(float(trimmed.mean()), 2),
        "P10":    round(float(np.percentile(s, 10)), 2),
        "hit%":   round(float((s > 0).mean() * 100), 1),
        "dd":     round(float(neg.std()), 2) if len(neg) >= 5 else float("nan"),
    }


def prow(label: str, s: pd.Series, w: int = 34) -> None:
    st = robust_block(s)
    if not st:
        print(f"  {label:<{w}} 표본부족(n<10)")
        return
    print(f"  {label:<{w}} {st['n']:>5}  {st['mean']:>+7.2f}  "
          f"{st['median']:>+7.2f}  {st['tmean']:>+7.2f}  "
          f"{st['P10']:>+7.2f}  {st['hit%']:>5.1f}%")


def header(w: int = 34) -> None:
    print(f"  {'Group':<{w}} {'n':>5}  {'mean':>7}  {'median':>7}  "
          f"{'tmean':>7}  {'P10':>7}  {'hit%':>6}")
    print("  " + "-" * (w + 44))


def assign_q(df: pd.DataFrame, col: str, scope_col: str | None = None,
             q: int = 5) -> pd.Series:
    """연도별 (+ scope_col 내) q분위 정렬."""
    result = pd.Series(np.nan, index=df.index)
    gcols = ["fwd_year"] if scope_col is None else ["fwd_year", scope_col]
    for _, grp in df.groupby(gcols, dropna=True):
        valid = grp[col].dropna()
        if len(valid) < q * 2:
            continue
        try:
            # labels=False → integer codes 0..n_bins-1, avoids label count mismatch
            codes, bins = pd.qcut(valid, q, retbins=True,
                                  labels=False, duplicates="drop")
            n_bins = len(bins) - 1
            if n_bins < 2:
                continue
            # rescale codes to 1..q range proportionally
            scaled = (codes / (n_bins - 1) * (q - 1)).round().astype(float) + 1
            scaled = scaled.clip(1, q)
            result.loc[scaled.index] = scaled
        except Exception:
            continue
    return result


def annual_ic(df_sub: pd.DataFrame, x_col: str, y_col: str = "fwd_alpha") -> tuple:
    ics = []
    for _, grp in df_sub.groupby("fwd_year"):
        sub = grp[[x_col, y_col]].dropna()
        if len(sub) < 10:
            continue
        r, _ = stats.spearmanr(sub[x_col], sub[y_col])
        ics.append(r)
    if not ics:
        return float("nan"), float("nan"), []
    t, p = stats.ttest_1samp(ics, 0)
    return float(np.mean(ics)), float(p), ics


def direction_check(mean_diff: float, median_diff: float) -> str:
    if mean_diff > 0 and median_diff > 0:
        return "CONSISTENT ↑"
    elif mean_diff < 0 and median_diff < 0:
        return "STRUCTURAL ↓"
    elif mean_diff > 0 and median_diff < 0:
        return "LOTTERY (mean↑/median↓)"
    else:
        return "LOTTERY (mean↓/median↑)"


def main() -> None:
    SEP = "=" * 82

    df = load()
    print(f"패널: {len(df)}건 (흑자 한정) / fwd_year {sorted(df['fwd_year'].unique())}")
    print(f"ΔROE 가용 표본: {df['d_roe'].notna().sum()}건\n")

    # 공통 분위 계산
    df["roe_q"]        = assign_q(df, "roe")           # 전체 단면 ROE Q
    df["pbr_q"]        = assign_q(df[df["pbr"] > 0], "pbr")
    df.loc[df["pbr"] <= 0, "pbr_q"] = np.nan
    df["roe_in_pbr"]   = assign_q(df, "roe", scope_col="pbr_q")
    df["roe_in_sec"]   = assign_q(df, "roe", scope_col="sector")
    df["d_roe_q"]      = assign_q(df[df["d_roe"].notna()].copy(), "d_roe")

    # ── 기준선 확인 ────────────────────────────────────────────────────────
    print(SEP)
    print("기준선 — 전체 흑자 패널 ROE Q1~Q5 (objective_validation 재확인)")
    print(SEP)
    header()
    for q in [1, 2, 3, 4, 5]:
        prow(f"ROE Q{q}" + (" (Low)" if q == 1 else " (High)" if q == 5 else ""),
             df[df["roe_q"] == q]["fwd_alpha"])

    ic, p, ics = annual_ic(df, "roe_q")
    pos = sum(1 for x in ics if x > 0)
    sig = "**" if p < 0.01 else ("*" if p < 0.05 else "  ")
    print(f"\n  ROE_q IC = {ic:+.3f}{sig}  p={p:.3f}  양수연도={pos}/{len(ics)}")

    q5 = df[df["roe_q"] == 5]["fwd_alpha"]
    q1 = df[df["roe_q"] == 1]["fwd_alpha"]
    s5, s1 = robust_block(q5), robust_block(q1)
    dm, dd = s5["mean"] - s1["mean"], s5["median"] - s1["median"]
    print(f"  Q5-Q1 diff: mean={dm:+.2f}  median={dd:+.2f}  → {direction_check(dm, dd)}")

    # ── Test 1: Valuation 통제 ────────────────────────────────────────────
    print(f"\n\n{SEP}")
    print("Test 1 — PBR Quintile 내 ROE Q5 vs Q1")
    print("  고PBR 내에서도 고ROE 역전이 유지되면 valuation premium으로 설명 안 됨")
    print(SEP)

    pbr_label = {1: "PBR Q1 (저PBR)", 3: "PBR Q3", 5: "PBR Q5 (고PBR)"}
    results_t1 = {}
    for pq in [1, 2, 3, 4, 5]:
        sub = df[df["pbr_q"] == pq]
        r5 = sub[sub["roe_in_pbr"] == 5]["fwd_alpha"]
        r1 = sub[sub["roe_in_pbr"] == 1]["fwd_alpha"]
        s5_, s1_ = robust_block(r5), robust_block(r1)
        if not s5_ or not s1_:
            continue
        dm_ = s5_["mean"] - s1_["mean"]
        dd_ = s5_["median"] - s1_["median"]
        lbl = pbr_label.get(pq, f"PBR Q{pq}")
        verdict = direction_check(dm_, dd_)
        print(f"  {lbl:<20}  n(Q5)={s5_['n']:>4}  "
              f"mean diff={dm_:>+7.2f}  median diff={dd_:>+7.2f}  → {verdict}")
        results_t1[pq] = (dm_, dd_)

    # PBR Q1 (저PBR) 내 상세
    print()
    sub_low_pbr = df[df["pbr_q"] == 1]
    print("  [PBR Q1(저PBR) 내 ROE Q1~Q5 상세]")
    header(22)
    for q in [1, 2, 3, 4, 5]:
        prow(f"  ROE Q{q}", sub_low_pbr[sub_low_pbr["roe_in_pbr"] == q]["fwd_alpha"], 22)

    # ── Test 2: Sector 중립 ────────────────────────────────────────────────
    print(f"\n\n{SEP}")
    print("Test 2 — Sector-neutral ROE (섹터 내 ROE rank)")
    print("  섹터 편향 제거 후에도 ROE 역전이 남는가?")
    print(SEP)

    r5_sn = df[df["roe_in_sec"] == 5]["fwd_alpha"]
    r1_sn = df[df["roe_in_sec"] == 1]["fwd_alpha"]
    s5_sn, s1_sn = robust_block(r5_sn), robust_block(r1_sn)
    dm_sn = s5_sn["mean"] - s1_sn["mean"]
    dd_sn = s5_sn["median"] - s1_sn["median"]
    print(f"  Sector-neutral Q5 vs Q1:")
    print(f"  mean diff = {dm_sn:+.2f}  median diff = {dd_sn:+.2f}  → {direction_check(dm_sn, dd_sn)}")

    ic_sn, p_sn, ics_sn = annual_ic(df, "roe_in_sec")
    ic_raw, p_raw, ics_raw = annual_ic(df, "roe_q")
    print(f"\n  IC 비교:")
    print(f"    Raw ROE_q IC      = {ic_raw:+.3f}  p={p_raw:.3f}")
    print(f"    Sector-neutral IC = {ic_sn:+.3f}  p={p_sn:.3f}")

    print("\n  [섹터별 ROE IC — 역전이 특정 섹터 집중인지 확인]")
    print(f"  {'섹터':<20} {'n':>5}  {'IC':>7}  {'p':>7}  {'양수연도':>8}")
    print("  " + "-" * 52)
    for sec, sub in df.groupby("sector"):
        ic_s, p_s, ics_s = annual_ic(sub, "roe_q")
        if not ics_s or len(ics_s) < 4:
            continue
        pos_s = sum(1 for x in ics_s if x > 0)
        sig_s = "**" if p_s < 0.01 else ("*" if p_s < 0.05 else "  ")
        print(f"  {str(sec):<20} {len(sub):>5}  {ic_s:>+7.3f}{sig_s}  "
              f"p={p_s:.3f}  {pos_s}/{len(ics_s)}")

    # ── Test 3: ROE level vs ΔROE ─────────────────────────────────────────
    print(f"\n\n{SEP}")
    print("Test 3 — ROE Level vs ΔROE (변화율)")
    print("  ROE 절대 수준 vs 전년 대비 변화율 중 어느 쪽이 alpha를 설명하는가?")
    print(SEP)

    df_d = df[df["d_roe"].notna()].copy()
    print(f"  ΔROE 가용 표본: {len(df_d)}건\n")

    # ΔROE IC
    ic_d, p_d, ics_d = annual_ic(df_d, "d_roe")
    ic_l, p_l, ics_l = annual_ic(df_d, "roe")
    pos_d = sum(1 for x in ics_d if x > 0)
    pos_l = sum(1 for x in ics_l if x > 0)
    print(f"  ROE level IC  = {ic_l:+.3f}  p={p_l:.3f}  양수연도={pos_l}/{len(ics_l)}")
    print(f"  ΔROE IC       = {ic_d:+.3f}  p={p_d:.3f}  양수연도={pos_d}/{len(ics_d)}")

    print("\n  [ΔROE Q1~Q5 alpha 분포]")
    header()
    for q in [1, 2, 3, 4, 5]:
        lbl = {1: "ΔROE Q1 (하락)", 5: "ΔROE Q5 (상승)"}.get(q, f"ΔROE Q{q}")
        prow(lbl, df_d[df_d["d_roe_q"] == q]["fwd_alpha"])

    # 2×2: ROE level (H/L) × ΔROE (H/L)
    print("\n  [2×2 교차: ROE level (High/Low) × ΔROE (Rising/Falling)]")
    df_d["roe_hl"]   = df_d["roe_q"].map(lambda x: "High" if x == 5 else ("Low" if x == 1 else None))
    df_d["d_roe_hl"] = df_d["d_roe_q"].map(lambda x: "Rising" if x == 5 else ("Falling" if x == 1 else None))
    df_d2 = df_d.dropna(subset=["roe_hl", "d_roe_hl"])

    print(f"  {'조합':<28} {'n':>5}  {'mean':>7}  {'median':>7}  {'P10':>7}")
    print("  " + "-" * 60)
    for rl, dl in [("High", "Rising"), ("High", "Falling"),
                   ("Low",  "Rising"), ("Low",  "Falling")]:
        sub = df_d2[(df_d2["roe_hl"] == rl) & (df_d2["d_roe_hl"] == dl)]["fwd_alpha"]
        st = robust_block(sub)
        if not st:
            print(f"  {rl} ROE × {dl:<12}  표본부족")
            continue
        print(f"  {rl} ROE × {dl:<12}  {st['n']:>5}  {st['mean']:>+7.2f}  "
              f"{st['median']:>+7.2f}  {st['P10']:>+7.2f}")

    # ── Test 4: ROE × PBR 교차 (Value+Quality) ───────────────────────────
    print(f"\n\n{SEP}")
    print("Test 4 — ROE × PBR 교차")
    print("  저PBR+고ROE(Value+Quality) vs 고PBR+고ROE(Growth+Quality)")
    print("  '싼 고ROE'와 '비싼 고ROE'의 alpha가 다른가?")
    print(SEP)

    combos = [
        ("Low PBR + High ROE", df[(df["pbr_q"] == 1) & (df["roe_q"] == 5)]["fwd_alpha"]),
        ("Low PBR + Low  ROE", df[(df["pbr_q"] == 1) & (df["roe_q"] == 1)]["fwd_alpha"]),
        ("Mid PBR + High ROE", df[(df["pbr_q"] == 3) & (df["roe_q"] == 5)]["fwd_alpha"]),
        ("High PBR + High ROE", df[(df["pbr_q"] == 5) & (df["roe_q"] == 5)]["fwd_alpha"]),
        ("High PBR + Low  ROE", df[(df["pbr_q"] == 5) & (df["roe_q"] == 1)]["fwd_alpha"]),
    ]
    header(24)
    for lbl, s in combos:
        prow(lbl, s, 24)

    # ── 전체 요약 ──────────────────────────────────────────────────────────
    print(f"\n\n{SEP}")
    print("전체 요약 — ROE 역전의 원인 후보 판정")
    print(SEP)

    print(f"""
  Test 1 (Valuation 통제):
    PBR Q별 ROE Q5-Q1 median diff 방향:
    {', '.join(f"PBR-Q{pq}: {results_t1[pq][1]:+.1f}" for pq in sorted(results_t1))}
    → 저PBR/고PBR 모두 역전이면: Valuation premium 단독 설명 불충분
    → 저PBR에서만 사라지면: 저평가 고ROE는 실제 유효한 Value+Quality 신호

  Test 2 (Sector 중립):
    Sector-neutral median diff = {dd_sn:+.2f}
    → 역전이 남으면: Sector 편향으로 설명 안 됨 → 종목 수준 구조

  Test 3 (ROE Level vs ΔROE):
    Level IC = {ic_l:+.3f} (p={p_l:.3f})
    ΔROE IC  = {ic_d:+.3f} (p={p_d:.3f})
    → ΔROE IC가 더 유의하면: "방향"이 "수준"보다 중요
    → 2×2에서 "High ROE + Rising"이 "High ROE + Falling"보다 높으면: 추세 효과 존재

  Test 4 (ROE × PBR):
    → "Low PBR + High ROE"가 "High PBR + High ROE"보다 높으면:
       valuation이 ROE 효과의 방향을 결정하는 조절변수
""")


if __name__ == "__main__":
    main()
