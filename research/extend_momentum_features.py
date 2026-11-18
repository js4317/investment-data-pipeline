#!/usr/bin/env python3
"""
Momentum Horizon 컬럼 확장
============================
apply_historical_features.py가 이미 1M/3M/6M을 추가했다.
이 스크립트는 추가 4개 창을 rrs_stock_year에 덧붙인다.

  mom_rel_9m   = price_Dec vs price_Mar   (9개월 상대)
  mom_rel_12m  = price_Dec vs price_Dec_prev (12개월 상대)
  mom_rel_12_1 = price_Nov vs price_Dec_prev (12-1M: 1개월 skip 12M)
  mom_rel_6_1  = price_Nov vs price_Jun   (6-1M: 1개월 skip 6M)

  * 12M / 12-1: risk_year 2016은 2015-12 스냅샷 없음 → NULL 처리
  * 6-1: Nov snap은 1M 기준 snap과 동일 (20xxNov30)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

ROOT    = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "database" / "research.db"

# 각 risk_year별 추가 스냅샷 날짜
YEAR_EXT_SNAPS: dict[int, dict[str, str | None]] = {
    yr: {
        "9m":   f"{yr}0331",
        "12m":  f"{yr-1}1231",   # 전년 12월 → 2016은 None 처리
        "12_1": f"{yr-1}1231",   # 12-1 기준도 전년 12월
        "6_1":  f"{yr}0630",     # 6월 (6M snap과 동일)
    }
    for yr in range(2016, 2024)
}
# 2016은 전년 12월(2015-12-31)이 supply_snapshot에 없음
YEAR_EXT_SNAPS[2016]["12m"]  = None
YEAR_EXT_SNAPS[2016]["12_1"] = None

# 11월 snap: 1M 기준과 같음
YEAR_NOV_SNAP: dict[int, str] = {yr: f"{yr}1130" for yr in range(2016, 2024)}


def ensure_columns(conn: sqlite3.Connection) -> None:
    existing = {r[1] for r in conn.execute("PRAGMA table_info(rrs_stock_year)")}
    new_cols = {
        "mom_rel_9m":   "REAL",
        "mom_rel_12m":  "REAL",
        "mom_rel_12_1": "REAL",
        "mom_rel_6_1":  "REAL",
    }
    for col, typedef in new_cols.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE rrs_stock_year ADD COLUMN {col} {typedef}")
    conn.commit()


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    ensure_columns(conn)

    print("supply_snapshot 로드 중...")
    snaps = pd.read_sql_query(
        "SELECT snap_date, ticker, price_snap, kospi_snap FROM supply_snapshot",
        conn,
    )
    sidx = snaps.set_index(["snap_date", "ticker"])
    print(f"  {len(snaps)}행 / 날짜 {snaps['snap_date'].nunique()}개\n")

    # rrs에서 필요한 열 — 이미 계산된 Nov/Dec 가격은 재계산 없이 snap에서 다시 읽음
    rrs = pd.read_sql_query(
        "SELECT rowid, risk_year, ticker FROM rrs_stock_year",
        conn,
    )
    print(f"RRS 패널: {len(rrs)}건\n")

    updated = 0
    for _, row in rrs.iterrows():
        yr     = int(row["risk_year"])
        ticker = row["ticker"]
        rowid  = int(row["rowid"])

        dec_snap = f"{yr}1231"
        nov_snap = YEAR_NOV_SNAP.get(yr)
        ext      = YEAR_EXT_SNAPS.get(yr, {})

        def price(snap_dt: str | None) -> float | None:
            if not snap_dt:
                return None
            try:
                return float(sidx.loc[(snap_dt, ticker), "price_snap"])
            except KeyError:
                return None

        def kospi(snap_dt: str | None) -> float | None:
            if not snap_dt:
                return None
            try:
                return float(sidx.loc[(snap_dt, ticker), "kospi_snap"])
            except KeyError:
                return None

        p_dec = price(dec_snap)
        k_dec = kospi(dec_snap)
        p_nov = price(nov_snap)
        k_nov = kospi(nov_snap)

        def rel_ret(p0: float | None, k0: float | None,
                    pt: float | None, kt: float | None) -> float | None:
            """(pt/p0 - 1)*100 - (kt/k0 - 1)*100"""
            if None in (p0, k0, pt, kt):
                return None
            if p0 == 0 or k0 == 0:
                return None
            return round((pt / p0 - 1) * 100 - (kt / k0 - 1) * 100, 4)

        # 9M: Dec vs Mar
        p9  = price(ext.get("9m"))
        k9  = kospi(ext.get("9m"))
        m9  = rel_ret(p9, k9, p_dec, k_dec)

        # 12M: Dec vs Dec_prev
        p12_base = ext.get("12m")
        p12  = price(p12_base)
        k12  = kospi(p12_base)
        m12  = rel_ret(p12, k12, p_dec, k_dec)

        # 12-1: Nov vs Dec_prev
        m12_1 = rel_ret(p12, k12, p_nov, k_nov)

        # 6-1: Nov vs Jun
        p6  = price(ext.get("6_1"))
        k6  = kospi(ext.get("6_1"))
        m6_1 = rel_ret(p6, k6, p_nov, k_nov)

        conn.execute("""
            UPDATE rrs_stock_year
            SET mom_rel_9m   = ?,
                mom_rel_12m  = ?,
                mom_rel_12_1 = ?,
                mom_rel_6_1  = ?
            WHERE rowid = ?
        """, (m9, m12, m12_1, m6_1, rowid))

        updated += 1
        if updated % 500 == 0:
            conn.commit()
            print(f"  {updated}/{len(rrs)} 처리 중...")

    conn.commit()
    conn.close()
    print(f"\n완료: {updated}건 업데이트\n")

    # 결과 확인
    conn2 = sqlite3.connect(DB_PATH)
    chk = pd.read_sql_query("""
        SELECT
            SUM(CASE WHEN mom_rel_9m   IS NOT NULL THEN 1 ELSE 0 END) AS n_9m,
            SUM(CASE WHEN mom_rel_12m  IS NOT NULL THEN 1 ELSE 0 END) AS n_12m,
            SUM(CASE WHEN mom_rel_12_1 IS NOT NULL THEN 1 ELSE 0 END) AS n_12_1,
            SUM(CASE WHEN mom_rel_6_1  IS NOT NULL THEN 1 ELSE 0 END) AS n_6_1
        FROM rrs_stock_year
    """, conn2)
    conn2.close()
    print("=== 가용 건수 ===")
    print(chk.to_string(index=False))


if __name__ == "__main__":
    main()
