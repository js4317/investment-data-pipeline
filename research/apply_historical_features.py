#!/usr/bin/env python3
"""
Historical Feature Application — supply_snapshot → rrs_stock_year
===================================================================
목적:
  supply_snapshot 테이블(2016-01-31 ~ 2024-06-30, 매월 말)에는
  foreign_20d / inst_20d / foreign_60d / inst_60d / price_snap / kospi_snap
  이 이미 수집되어 있다.

  build_rrs.py가 SUPPLY_NEUTRAL=12로 하드코딩한 것은 설계 누락이었다.
  이 스크립트는:
    1. rrs_stock_year의 각 (risk_year, ticker)에 대해
       해당 연도 12월 말 supply_snapshot을 조인
    2. supply_score 재계산 (production과 동일 로직)
    3. 전전월/전분기/전반기 스냅샷으로 모멘텀 파생
    4. rrs_stock_year 업데이트 (supply_score, mom_rel_1m/3m/6m 컬럼 추가)

매칭 기준:
  risk_year → snap_date = '{risk_year}1231' (또는 실제 존재하는 가장 가까운 날짜)

  모멘텀:
    1M  = price_{Dec} vs price_{Nov}  (직전 스냅샷)
    3M  = price_{Dec} vs price_{Sep}  (3개월 전 스냅샷)
    6M  = price_{Dec} vs price_{Jun}  (6개월 전 스냅샷)

Supply Scoring Logic (production과 동일):
  base:
    외+기관 쌍끌이매수  → 20
    외국인 단독          → 16
    기관 단독            → 14
    쌍끌이매도           → 6
  filter1 bonus:
    순매수 / 시총 >= 0.5%  → +2
  최종: max(0, min(25, base + bonus))
  단, supply_approx 플래그는 0으로 갱신 (실제 데이터 사용)

출력:
  rrs_stock_year에 컬럼 추가/갱신:
    supply_score  (INTEGER)  — 재계산된 실제 수급 점수
    supply_approx (INTEGER)  — 0: 실데이터, 1: 중립 추정
    mom_rel_1m   (REAL)     — 1M KOSPI 대비 상대수익률 (%)
    mom_rel_3m   (REAL)     — 3M KOSPI 대비 상대수익률 (%)
    mom_rel_6m   (REAL)     — 6M KOSPI 대비 상대수익률 (%)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

ROOT    = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "database" / "research.db"

# ── Supply scoring 상수 (config.py와 동기화) ──────────────────────────────
DUAL_BUYING       = 20
FOREIGN_SINGLE    = 16
INST_SINGLE       = 14
DUAL_SELLING      = 6
FILTER1_PCT       = 0.5    # 순매수/시총 >= 0.5% → bonus +2
FILTER1_BONUS     = 2
SUPPLY_MAX        = 25
SUPPLY_MIN        = 0

# risk_year → 12월 스냅샷 날짜 매핑
YEAR_DEC_SNAP: dict[int, str] = {
    2016: "20161231",
    2017: "20171231",
    2018: "20181231",
    2019: "20191231",
    2020: "20201231",
    2021: "20211231",
    2022: "20221231",
    2023: "20231231",
}

# risk_year → 모멘텀 기준 스냅샷 날짜 (1M=Nov, 3M=Sep, 6M=Jun)
YEAR_MOM_SNAPS: dict[int, dict[str, str]] = {
    yr: {
        "1m": f"{yr}1130",
        "3m": f"{yr}0930",
        "6m": f"{yr}0630",
    }
    for yr in range(2016, 2024)
}


def load_supply_snapshots(conn: sqlite3.Connection) -> pd.DataFrame:
    """supply_snapshot 전체 로드 (snap_date 문자열 그대로)."""
    return pd.read_sql_query(
        """
        SELECT snap_date, ticker,
               foreign_20d, inst_20d, foreign_60d, inst_60d,
               market_cap, price_snap, kospi_snap
        FROM supply_snapshot
        """,
        conn,
    )


def compute_supply_score(f_net: float | None, i_net: float | None,
                         market_cap: float | None) -> int:
    """Production supply scoring logic 재현."""
    if f_net is None or i_net is None or np.isnan(f_net) or np.isnan(i_net):
        return 12   # 데이터 없으면 중립

    f_pos = f_net > 0
    i_pos = i_net > 0

    if f_pos and i_pos:
        base = DUAL_BUYING
    elif f_pos:
        base = FOREIGN_SINGLE
    elif i_pos:
        base = INST_SINGLE
    else:
        base = DUAL_SELLING

    # Filter1 bonus (20D 기준, 시총 대비)
    bonus = 0
    if market_cap and market_cap > 0:
        f_pct = f_net / market_cap * 100 if f_pos else 0
        i_pct = i_net / market_cap * 100 if i_pos else 0
        if max(f_pct, i_pct) >= FILTER1_PCT:
            bonus = FILTER1_BONUS

    return int(max(SUPPLY_MIN, min(SUPPLY_MAX, base + bonus)))


def ensure_columns(conn: sqlite3.Connection) -> None:
    """rrs_stock_year에 필요한 컬럼이 없으면 추가."""
    existing = {r[1] for r in conn.execute("PRAGMA table_info(rrs_stock_year)")}
    cols_to_add = {
        "supply_score_real": "INTEGER",
        "supply_approx":     "INTEGER DEFAULT 1",
        "mom_rel_1m":        "REAL",
        "mom_rel_3m":        "REAL",
        "mom_rel_6m":        "REAL",
    }
    for col, typedef in cols_to_add.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE rrs_stock_year ADD COLUMN {col} {typedef}")
    conn.commit()


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    ensure_columns(conn)

    print("supply_snapshot 로드 중...")
    snaps = load_supply_snapshots(conn)
    snaps_idx = snaps.set_index(["snap_date", "ticker"])
    print(f"  {len(snaps)}행 / 날짜: {snaps['snap_date'].nunique()}개\n")

    rrs = pd.read_sql_query(
        "SELECT rowid, risk_year, ticker, supply_score FROM rrs_stock_year",
        conn,
    )
    print(f"RRS 패널: {len(rrs)}건\n")

    updated = 0
    no_snap = 0

    for _, row in rrs.iterrows():
        yr     = int(row["risk_year"])
        ticker = row["ticker"]
        rowid  = int(row["rowid"])

        dec_snap = YEAR_DEC_SNAP.get(yr)
        if dec_snap is None:
            no_snap += 1
            continue

        # ── Supply score ─────────────────────────────────────────────
        try:
            s = snaps_idx.loc[(dec_snap, ticker)]
            f20  = s["foreign_20d"]
            i20  = s["inst_20d"]
            mcap = s["market_cap"]
            supply_real  = compute_supply_score(f20, i20, mcap)
            supply_approx = 0
            price_dec  = s["price_snap"]
            kospi_dec  = s["kospi_snap"]
        except KeyError:
            supply_real   = 12   # 해당 연도 스냅샷 없음 → 중립
            supply_approx = 1
            price_dec = kospi_dec = None
            no_snap += 1

        # ── Momentum ─────────────────────────────────────────────────
        mom_snaps = YEAR_MOM_SNAPS.get(yr, {})

        def _rel_ret(window_key: str) -> float | None:
            snap_dt = mom_snaps.get(window_key)
            if not snap_dt or price_dec is None or kospi_dec is None:
                return None
            try:
                prev = snaps_idx.loc[(snap_dt, ticker)]
                p0   = prev["price_snap"]
                k0   = prev["kospi_snap"]
                if not p0 or not k0 or p0 == 0 or k0 == 0:
                    return None
                t_ret = (price_dec / p0 - 1) * 100
                k_ret = (kospi_dec / k0 - 1) * 100
                return round(float(t_ret - k_ret), 4)
            except KeyError:
                return None

        m1 = _rel_ret("1m")
        m3 = _rel_ret("3m")
        m6 = _rel_ret("6m")

        # ── 업데이트 ──────────────────────────────────────────────────
        conn.execute("""
            UPDATE rrs_stock_year
            SET supply_score_real = ?,
                supply_approx     = ?,
                mom_rel_1m        = ?,
                mom_rel_3m        = ?,
                mom_rel_6m        = ?
            WHERE rowid = ?
        """, (supply_real, supply_approx, m1, m3, m6, rowid))
        updated += 1

        if updated % 500 == 0:
            conn.commit()
            print(f"  {updated}/{len(rrs)} 처리 중...")

    conn.commit()
    conn.close()

    print(f"\n완료: {updated}건 업데이트 / {no_snap}건 스냅샷 없음")
    print()

    # 결과 확인
    conn2 = sqlite3.connect(DB_PATH)
    check = pd.read_sql_query("""
        SELECT
            supply_approx,
            COUNT(*) as n,
            AVG(supply_score_real) as avg_supply,
            SUM(CASE WHEN mom_rel_1m IS NOT NULL THEN 1 ELSE 0 END) as has_mom
        FROM rrs_stock_year
        GROUP BY supply_approx
    """, conn2)
    conn2.close()

    print("=== 업데이트 결과 ===")
    print(check.to_string(index=False))
    print()
    print("다음 단계:")
    print("  1. research/architecture_counterfactual.py 에서")
    print("     supply_score → supply_score_real 으로 교체 후 재실행")
    print("  2. research/objective_validation.py 에서")
    print("     mom_rel_1m/3m/6m IC 분석 추가")


if __name__ == "__main__":
    main()
