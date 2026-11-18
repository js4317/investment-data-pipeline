#!/usr/bin/env python3
"""
Retroactive Research Score (RRS) Builder — v8.5.19 소급 적용
=============================================================
과거 데이터에 현재 production scoring formula(v8.5.19)를 소급 적용.
Production snapshot과 별개 — OOS/live validation을 대체하지 않음.

출력 테이블: rrs_stock_year
  year, ticker, as_of_date,
  value_score, earnings_score, risk_score, supply_score,
  total_score, normalized_score, grade,
  fwd_return, fwd_alpha, kospi_ret,
  -- 메타
  value_approx, earnings_approx, risk_approx, supply_approx

재현 정밀도:
  Value     : 완전 재현 (PER 절대 + sector peer 상대 hybrid)
  Risk      : 거의 완전 (debt 200% / dilution 5% / vol 60% production 임계값 그대로)
              loss 근사: ni_ytd<0 → loss_warning(-4pt). 분기 구분 불가.
  Earnings  : 근사 (ROE 기반 + ni_ytd 방향 반영). revision/quality 데이터 없음.
  Supply    : 중립(12.5점) — 연간 Supply proxy 설계 별도. 현재 반영 안 함.

architecture 비교 목적:
  rrs_stock_year에 component별 점수가 분리 저장되므로
  V+E+R+S / V+E+R / V 단독 / E=Gate + R=Filter 구조를 동일 패널에서 실험 가능.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

ROOT    = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "database" / "research.db"
KOSPI_IDX = "1001"

# ── v8.5.19 Production Config (config.py 기준, 변경 금지) ─────────────────────

PER_TABLE = [
    (6,  25), (8, 22), (10, 18), (12, 14), (15, 10),
    (20,  7), (30,  4), (50,  2), (9999, 1),
]

PBR_CAUTION  = 3.0
PBR_DANGER   = 5.0

RISK_BASE              = 25
DEBT_DANGER_PCT        = 200.0;  DEBT_PENALTY         = -5
LOSS_WARNING_QTR       = 2;      LOSS_WARNING_PENALTY = -4
LOSS_DANGER_QTR        = 3;      LOSS_DANGER_PENALTY  = -7
DILUTION_DANGER_PCT    = 5.0;    DILUTION_PENALTY     = -5
VOL_DANGER_PCT         = 60.0;   VOL_PENALTY          = -3

SUPPLY_NEUTRAL         = 12     # 중립 (max 25의 절반)
SUPPLY_MAX             = 25

GRADE_STRONG_BUY = 85
GRADE_BUY        = 70
GRADE_NEUTRAL    = 55

HYBRID_ABS_W   = 0.5
HYBRID_REL_W   = 0.5
HYBRID_PBR_W   = 0.0
HYBRID_MIN_N   = 5

# ── Value Scoring ─────────────────────────────────────────────────────────────

def _per_score_abs(per: float | None) -> int:
    if per is None or per <= 0:
        return 0
    for max_per, score in PER_TABLE:
        if per <= max_per:
            return score
    return 1


def _pctl_score(rank: float) -> int:
    table = [(0.10,25),(0.20,22),(0.33,18),(0.50,14),(0.67,10),(0.80,6),(1.00,2)]
    for threshold, score in table:
        if rank <= threshold:
            return score
    return 2


def score_value(per: float | None, pbr: float | None,
                peer_pers: list[float] | None = None) -> tuple[int, bool]:
    """
    returns (score, approx_flag)
    approx_flag=False → production hybrid와 동일 로직.
    sector peer 없으면 절대 PER만.
    """
    abs_score = _per_score_abs(per)
    if abs_score == 0:
        return 0, False  # PER invalid → 0

    if peer_pers and len(peer_pers) >= HYBRID_MIN_N and per and per > 0:
        rank    = sum(1 for p in peer_pers if p < per) / len(peer_pers)
        rel_scr = _pctl_score(rank)
        total_w = HYBRID_ABS_W + HYBRID_REL_W
        hybrid  = round((abs_score * HYBRID_ABS_W + rel_scr * HYBRID_REL_W) / total_w)
        return max(0, min(hybrid, 25)), False
    else:
        return max(0, min(abs_score, 25)), (peer_pers is not None and len(peer_pers) < HYBRID_MIN_N)


# ── Risk Scoring ──────────────────────────────────────────────────────────────

def score_risk(debt_ratio: float | None, ni_ytd: float | None,
               dilution_pct: float | None, vol_pct: float | None) -> tuple[int, bool]:
    """
    returns (score, approx_flag)
    approx_flag=True → ni_ytd 기반 loss 근사 사용.

    Production과 차이:
    - loss: ni_ytd<0 → loss_warning_penalty(-4). 분기별 2/3회 구분 불가.
    - 거버넌스/이자보상배율: 데이터 없음 → 적용 안 함.
    """
    penalty   = 0
    approx    = False

    if debt_ratio is not None:
        if debt_ratio >= DEBT_DANGER_PCT:
            penalty += DEBT_PENALTY

    if ni_ytd is not None:
        if ni_ytd < 0:
            penalty += LOSS_WARNING_PENALTY   # 근사: warning 레벨로 고정
            approx = True
        # ni_ytd >= 0 → loss 없음으로 처리

    if dilution_pct is not None and dilution_pct >= DILUTION_DANGER_PCT:
        penalty += DILUTION_PENALTY

    if vol_pct is not None and vol_pct >= VOL_DANGER_PCT:
        penalty += VOL_PENALTY

    return max(0, RISK_BASE + penalty), approx


# ── Earnings Scoring (근사) ────────────────────────────────────────────────────

def score_earnings_approx(ni_ytd: float | None, roe: float | None) -> tuple[int, bool]:
    """
    Production Earnings는 분기별 beat/miss + revision + quality 필요 — 미확보.
    근사: ni_ytd(연간 흑적자) + ROE 수준으로 base_score 추정.

    매핑:
      ni_ytd > 0, ROE > 10  → 4분기 충족 근사 → base 22
      ni_ytd > 0, ROE > 0   → 3분기 충족 근사 → base 20
      ni_ytd > 0, ROE <= 0  → 2분기 충족 근사 → base 15
      ni_ytd < 0            → 1분기 충족 근사 → base  9
      데이터 없음            → 0점

    revision/quality/ROE bonus: 데이터 없어서 생략 → 모두 0.
    """
    if ni_ytd is None:
        return 0, True
    if ni_ytd > 0:
        if roe is not None and roe > 10:
            return 22, True
        elif roe is not None and roe > 0:
            return 20, True
        else:
            return 15, True
    else:
        return 9, True


# ── 패널 로드 ─────────────────────────────────────────────────────────────────

def load_panel() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)

    df = pd.read_sql_query("""
        SELECT
            rh.year         AS risk_year,
            rh.ticker,
            rh.debt_ratio,
            rh.ni_ytd,
            rv.vol_ann_pct  AS volatility,
            rv.shares_sep30 AS shares_cur,
            rs.year         AS fwd_year,
            rs.per,
            rs.pbr,
            rs.roe,
            rs.sector,
            rs.return_pct   AS fwd_return,
            rs.market_cap
        FROM risk_history rh
        JOIN risk_volatility rv
            ON rh.year = rv.year AND rh.ticker = rv.ticker
        JOIN research_stock_year rs
            ON rs.year = rh.year + 1 AND rs.ticker = rh.ticker
        WHERE rh.has_data = 1
          AND rv.has_vol  = 1
          AND rs.return_pct IS NOT NULL
    """, conn)

    # 희석률: shares_cur(fwd_year 시작) vs shares_prev(fwd_year-1 시작 = risk_year 시작)
    sp = pd.read_sql_query(
        "SELECT year, ticker, shares_sep30 FROM risk_volatility WHERE has_shares=1", conn
    )
    conn.close()

    sp_prev = sp.copy()
    sp_prev["fwd_year"] = sp_prev["year"] + 1
    sp_prev = sp_prev.rename(columns={"shares_sep30": "shares_prev"}).drop(columns="year")
    df = df.merge(sp_prev, on=["fwd_year", "ticker"], how="left")
    df["dilution_pct"] = np.where(
        (df["shares_prev"] > 0) & df["shares_cur"].notna() & df["shares_prev"].notna(),
        (df["shares_cur"] / df["shares_prev"] - 1) * 100, np.nan
    )
    return df


def build_sector_peers(df: pd.DataFrame) -> dict[tuple, list[float]]:
    """(fwd_year, sector) → 해당 연도·섹터 내 유효 PER 리스트."""
    peers: dict[tuple, list[float]] = {}
    for (fy, sec), grp in df.groupby(["fwd_year", "sector"]):
        valid = grp.loc[(grp["per"] > 0) & (grp["per"] < 200), "per"].tolist()
        if valid:
            peers[(fy, sec)] = sorted(valid)
    return peers


def fetch_kospi_returns(fwd_years: list) -> dict[int, float]:
    try:
        from pykrx import stock as krx
        import time
        result = {}
        for y in fwd_years:
            try:
                s = krx.get_index_ohlcv_by_date(f"{y}0102", f"{y}0131", KOSPI_IDX)
                e = krx.get_index_ohlcv_by_date(f"{y}1201", f"{y}1231", KOSPI_IDX)
                time.sleep(0.3)
                if s is not None and not s.empty and e is not None and not e.empty:
                    result[y] = round((float(e["종가"].iloc[-1]) / float(s["종가"].iloc[0]) - 1) * 100, 2)
            except Exception:
                pass
        return result
    except ImportError:
        return {}


def _grade(score: int) -> str:
    if score >= GRADE_STRONG_BUY: return "STRONG_BUY"
    if score >= GRADE_BUY:        return "BUY"
    if score >= GRADE_NEUTRAL:    return "NEUTRAL"
    return "AVOID"


# ── DDL ───────────────────────────────────────────────────────────────────────

DDL = """
CREATE TABLE IF NOT EXISTS rrs_stock_year (
    risk_year       INTEGER NOT NULL,
    fwd_year        INTEGER NOT NULL,
    ticker          TEXT    NOT NULL,
    sector          TEXT,
    -- component 점수
    value_score     INTEGER,
    earnings_score  INTEGER,
    risk_score      INTEGER,
    supply_score    INTEGER,
    -- 합산
    total_score     INTEGER,
    normalized_score INTEGER,
    grade           TEXT,
    -- 근사 플래그
    value_approx    INTEGER DEFAULT 0,
    earnings_approx INTEGER DEFAULT 1,
    risk_approx     INTEGER DEFAULT 0,
    supply_approx   INTEGER DEFAULT 1,
    -- 수익률
    fwd_return      REAL,
    kospi_ret       REAL,
    fwd_alpha       REAL,
    PRIMARY KEY (risk_year, ticker)
)
"""


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    SEP = "=" * 60

    print("패널 로드...")
    df = load_panel()
    fwd_years = sorted(df["fwd_year"].unique())
    print(f"패널: {len(df)}건 / fwd_year {fwd_years}")

    print("Sector peer 분포 구축...")
    peers_map = build_sector_peers(df)
    print(f"  섹터×연도 조합: {len(peers_map)}개")

    print("KOSPI 연간 수익률 조회...")
    kospi = fetch_kospi_returns(fwd_years)
    if kospi:
        df["kospi_ret"] = df["fwd_year"].map(kospi)
    else:
        df["kospi_ret"] = df.groupby("fwd_year")["fwd_return"].transform("median")
        print("  [fallback] 패널 중앙값 사용")
    df["fwd_alpha"] = df["fwd_return"] - df["kospi_ret"]

    print("\nRRS 점수 계산 중...")
    rows = []
    for _, row in df.iterrows():
        key = (int(row["fwd_year"]), row["sector"])
        peer_pers = peers_map.get(key)

        v_score, v_approx = score_value(row["per"], row["pbr"], peer_pers)
        r_score, r_approx = score_risk(
            row["debt_ratio"], row["ni_ytd"], row["dilution_pct"], row["volatility"]
        )
        e_score, e_approx = score_earnings_approx(row["ni_ytd"], row["roe"])
        s_score           = SUPPLY_NEUTRAL   # 중립

        components = [v_score, e_score, r_score, s_score]
        raw_total  = max(0, min(sum(components), 100))
        avail_max  = 25 + 25 + 25 + SUPPLY_MAX
        norm_total = max(0, min(round(raw_total / avail_max * 100), 100))

        rows.append({
            "risk_year":        int(row["risk_year"]),
            "fwd_year":         int(row["fwd_year"]),
            "ticker":           row["ticker"],
            "sector":           row["sector"],
            "value_score":      v_score,
            "earnings_score":   e_score,
            "risk_score":       r_score,
            "supply_score":     s_score,
            "total_score":      raw_total,
            "normalized_score": norm_total,
            "grade":            _grade(norm_total),
            "value_approx":     int(v_approx),
            "earnings_approx":  int(e_approx),
            "risk_approx":      int(r_approx),
            "supply_approx":    1,
            "fwd_return":       row["fwd_return"],
            "kospi_ret":        row["kospi_ret"],
            "fwd_alpha":        row["fwd_alpha"],
        })

    rrs = pd.DataFrame(rows)

    # ── DB 저장 ──────────────────────────────────────────────────────────────
    conn = sqlite3.connect(DB_PATH)
    conn.execute(DDL)
    conn.execute("DELETE FROM rrs_stock_year")   # 재실행 안전
    rrs.to_sql("rrs_stock_year", conn, if_exists="append", index=False)
    conn.commit()
    conn.close()
    print(f"저장 완료: rrs_stock_year {len(rrs)}건")

    # ── 검증 출력 ─────────────────────────────────────────────────────────────
    print(f"\n\n{SEP}")
    print("RRS 점수 분포 검증")
    print(SEP)
    print(f"\n  총 {len(rrs)}건 / {rrs['risk_year'].nunique()}개 risk_year")
    print(f"\n  Grade 분포:")
    for g in ["STRONG_BUY","BUY","NEUTRAL","AVOID"]:
        n = (rrs["grade"] == g).sum()
        print(f"    {g:<12}: {n:,}건 ({n/len(rrs)*100:.1f}%)")

    print(f"\n  점수 분포 (normalized_score):")
    print(f"    mean={rrs['normalized_score'].mean():.1f}  "
          f"std={rrs['normalized_score'].std():.1f}  "
          f"min={rrs['normalized_score'].min()}  "
          f"max={rrs['normalized_score'].max()}")

    print(f"\n  근사 플래그 현황:")
    print(f"    earnings_approx=1: {rrs['earnings_approx'].sum():,}건 (전체 — Earnings 근사)")
    print(f"    risk_approx=1:     {rrs['risk_approx'].sum():,}건 (loss 분기 근사)")
    print(f"    supply_approx=1:   {rrs['supply_approx'].sum():,}건 (전체 — Supply 중립)")

    print(f"\n\n{SEP}")
    print("Value Score 검증 (Value → fwd_alpha 방향 확인)")
    print(SEP)
    valid = rrs[rrs["fwd_alpha"].notna()].copy()
    for label, mask in [
        ("Value ≤6  (최저 PER)", rrs["value_score"] == 25),
        ("Value 7~10", rrs["value_score"].between(7, 10)),
        ("Value 0   (PER 없음)", rrs["value_score"] == 0),
    ]:
        s = valid.loc[mask & valid.index.isin(valid.index), "fwd_alpha"]
        if len(s) > 30:
            print(f"  {label:<22}: n={len(s):,}  mean_alpha={s.mean():+.2f}%  P10={np.percentile(s,10):+.1f}%  hit={round((s>0).mean()*100,1)}%")


if __name__ == "__main__":
    main()
