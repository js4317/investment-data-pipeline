#!/usr/bin/env python3
"""
2025 ROE 수집 및 research_stock_year 업데이트
=============================================
pykrx get_market_fundamental_by_ticker(2025-01-02) 에서
EPS / BPS 를 받아 ROE = EPS / BPS × 100 으로 계산 후
research_stock_year.roe (year=2025) 에 upsert.

이후 build_rrs.py 를 재실행하면 risk_year=2024 (fwd_year=2025) 에
earnings_score 가 정상 계산됨.
"""
from __future__ import annotations
import sqlite3, time
from pathlib import Path

ROOT    = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "database" / "research.db"
TARGET_YEAR = 2025

def first_trading_day(year: int) -> str:
    from pykrx import stock
    for d in range(2, 10):
        dt = f"{year}01{d:02d}"
        try:
            df = stock.get_market_sector_classifications(dt, market="KOSPI")
            if not df.empty:
                return dt
        except Exception:
            pass
    return f"{year}0102"

def main():
    from pykrx import stock

    start = first_trading_day(TARGET_YEAR)
    print(f"기준일: {start}")

    print("pykrx fundamentals 조회 중...")
    try:
        fund = stock.get_market_fundamental_by_ticker(start, market="KOSPI")
        time.sleep(1.0)
    except Exception as e:
        print(f"조회 실패: {e}")
        return

    print(f"  반환 컬럼: {list(fund.columns)}")
    fund.index = fund.index.astype(str)

    if "EPS" not in fund.columns or "BPS" not in fund.columns:
        print("EPS / BPS 컬럼 없음 — ROE 계산 불가")
        return

    # ROE = EPS / BPS × 100
    valid = fund[(fund["BPS"] > 0) & fund["EPS"].notna()].copy()
    valid["roe"] = (valid["EPS"] / valid["BPS"] * 100).round(4)
    print(f"  ROE 계산 가능: {len(valid)}종목 / 전체: {len(fund)}종목")
    print(f"  ROE 분포: mean={valid['roe'].mean():.1f}  min={valid['roe'].min():.1f}  max={valid['roe'].max():.1f}")

    # DB 업데이트
    with sqlite3.connect(DB_PATH) as c:
        updated = 0
        for ticker, row in valid.iterrows():
            cur = c.execute(
                "UPDATE research_stock_year SET roe=? WHERE year=? AND ticker=?",
                (float(row["roe"]), TARGET_YEAR, str(ticker))
            )
            updated += cur.rowcount
        c.commit()

    print(f"  research_stock_year.roe 업데이트: {updated}행 (year={TARGET_YEAR})")
    print()
    print("완료. 다음 단계:")
    print("  python3 research/build_rrs.py")
    print("  python3 research/pit_universe_analysis.py")

if __name__ == "__main__":
    main()
