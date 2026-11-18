"""
Research Dataset Builder
========================
database/research.db 를 생성하고 2015~2024년 종목-연도 원본 데이터를 적재한다.

테이블:
  research_stock_year  — 종목 × 연도 원본 관측치 (불변)
  research_year_meta   — 연도별 KOSPI 수익률 / 시장 국면 (파생)

원칙:
  - 이 스크립트만 DB를 쓴다(write). 분석 코드는 읽기 전용.
  - 재실행 시 해당 연도 데이터를 교체(upsert)한다.
  - per_group / phase 등 파생 변수는 저장하지 않는다.
"""

import sqlite3
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import pandas as pd
from pykrx import stock

# KRX 봇 탐지 방지 — API 호출 사이 최소 대기
_CALL_DELAY = 2.0   # 초. 너무 빠른 연속 호출 방지

# ── 경로 ──────────────────────────────────────────────────────
DB_PATH = Path(__file__).parent.parent / "database" / "research.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# ── 파라미터 ──────────────────────────────────────────────────
YEARS        = list(range(2015, 2026))
FORCE_YEARS  = []   # 강제 재적재할 연도 목록. 예: [2024] → 해당 연도만 재처리
PER_MAX = 100   # 이상치 제거 (음수·초고PER 제외)

PHASE_BULL     =  10.0   # KOSPI 연간 수익률 > +10% → Bull
PHASE_BEAR     = -10.0   # KOSPI 연간 수익률 < -10% → Bear


# ── DDL ───────────────────────────────────────────────────────
DDL_STOCK_YEAR = """
CREATE TABLE IF NOT EXISTS research_stock_year (
    year          INTEGER NOT NULL,
    ticker        TEXT    NOT NULL,
    sector        TEXT,
    per           REAL,
    pbr           REAL,
    market_cap    REAL,
    p_start       REAL,
    p_end         REAL,
    return_pct    REAL,
    status_at_end TEXT    NOT NULL DEFAULT 'NORMAL',
    PRIMARY KEY (year, ticker)
)
"""

DDL_YEAR_META = """
CREATE TABLE IF NOT EXISTS research_year_meta (
    year         INTEGER PRIMARY KEY,
    kospi_return REAL,
    phase        TEXT
)
"""


def _first_trading_day(year: int) -> str:
    """해당 연도 첫 실제 거래일 (sector 데이터 기준)."""
    for d in range(2, 10):
        dt = f"{year}01{d:02d}"
        try:
            df = stock.get_market_sector_classifications(dt, market="KOSPI")
            if not df.empty:
                return dt
        except Exception:
            pass
    return f"{year}0104"


def _last_trading_day(year: int) -> str:
    """해당 연도 마지막 실제 거래일 (sector 데이터 기준)."""
    for d in range(31, 24, -1):
        dt = f"{year}12{d:02d}"
        try:
            df = stock.get_market_sector_classifications(dt, market="KOSPI")
            if not df.empty:
                return dt
        except Exception:
            pass
    return f"{year}1229"


def _kospi_annual_return(start: str, end: str) -> float | None:
    """KOSPI 지수 연간 수익률 (소수 아닌 %)."""
    try:
        ki = stock.get_index_ohlcv_by_date(start, end, "1001")
        if ki.empty:
            return None
        p0, p1 = ki.iloc[0]["종가"], ki.iloc[-1]["종가"]
        return round((p1 - p0) / p0 * 100, 2)
    except Exception:
        return None


def _phase(kospi_ret: float | None) -> str:
    if kospi_ret is None:
        return "Unknown"
    if kospi_ret > PHASE_BULL:
        return "Bull"
    if kospi_ret < PHASE_BEAR:
        return "Bear"
    return "Sideways"


def _build_year(year: int, conn: sqlite3.Connection) -> int:
    """한 연도 데이터를 수집해서 DB에 upsert. 적재 행수 반환."""
    start = _first_trading_day(year)
    end   = _last_trading_day(year)
    print(f"  기간: {start} → {end}")

    # 연말 sector set (DELISTED 판별용)
    try:
        end_sector_df = stock.get_market_sector_classifications(end, market="KOSPI")
        end_tickers   = set(end_sector_df.index.astype(str))
    except Exception:
        end_tickers = set()
    time.sleep(_CALL_DELAY)

    # 연초 sector
    try:
        sector_df = stock.get_market_sector_classifications(start, market="KOSPI")[["업종명"]]
        sector_df.index = sector_df.index.astype(str)
    except Exception:
        print("  ⚠ sector 데이터 없음 — 건너뜀")
        return 0
    time.sleep(_CALL_DELAY)

    # 펀더멘털 (PER, PBR — 연초 기준)
    try:
        fund_df = stock.get_market_fundamental_by_ticker(start, market="KOSPI")[["PER", "PBR"]]
        fund_df.index = fund_df.index.astype(str)
    except Exception:
        fund_df = pd.DataFrame()
    time.sleep(_CALL_DELAY)

    # 시가총액 (연초 기준)
    try:
        cap_df = stock.get_market_cap_by_ticker(start, market="KOSPI")[["시가총액"]]
        cap_df.index = cap_df.index.astype(str)
        cap_df = cap_df.rename(columns={"시가총액": "market_cap"})
    except Exception:
        cap_df = pd.DataFrame()
    time.sleep(_CALL_DELAY)

    # 가격 (연초)
    try:
        ps = stock.get_market_ohlcv_by_ticker(start, market="KOSPI")[["종가"]]
        ps.index = ps.index.astype(str)
        ps = ps.rename(columns={"종가": "p_start"})
    except Exception:
        ps = pd.DataFrame()
    time.sleep(_CALL_DELAY)

    # 가격 (연말)
    try:
        pe = stock.get_market_ohlcv_by_ticker(end, market="KOSPI")[["종가"]]
        pe.index = pe.index.astype(str)
        pe = pe.rename(columns={"종가": "p_end"})
    except Exception:
        pe = pd.DataFrame()
    time.sleep(_CALL_DELAY)

    # 병합
    df = sector_df.copy()
    for extra in [fund_df, cap_df, ps, pe]:
        if not extra.empty:
            df = df.join(extra, how="left")

    df = df.rename(columns={"업종명": "sector", "PER": "per", "PBR": "pbr"})
    df["year"] = year
    df.index.name = "ticker"
    df = df.reset_index()

    # 컬럼 누락 방어 (fetch 실패 시 해당 컬럼이 없을 수 있음)
    for col in ["per", "pbr", "market_cap", "p_start", "p_end"]:
        if col not in df.columns:
            df[col] = None

    # status_at_end 결정
    def _status(row):
        t = str(row["ticker"])
        if t not in end_tickers:
            return "DELISTED"
        p_end_val = row.get("p_end")
        if pd.isna(p_end_val) or p_end_val == 0:
            return "NO_PRICE"
        return "NORMAL"

    df["status_at_end"] = df.apply(_status, axis=1)

    # return_pct: NORMAL만 계산
    df["return_pct"] = None
    mask = (
        (df["status_at_end"] == "NORMAL") &
        df["p_start"].notna() & df["p_end"].notna() &
        (df["p_start"] > 0) & (df["p_end"] > 0)
    )
    df.loc[mask, "return_pct"] = (
        (df.loc[mask, "p_end"] - df.loc[mask, "p_start"]) / df.loc[mask, "p_start"] * 100
    )

    # upsert
    rows = []
    for _, r in df.iterrows():
        rows.append((
            int(year),
            str(r["ticker"]),
            r.get("sector"),
            float(r["per"])        if pd.notna(r.get("per"))        else None,
            float(r["pbr"])        if pd.notna(r.get("pbr"))        else None,
            float(r["market_cap"]) if pd.notna(r.get("market_cap")) else None,
            float(r["p_start"])    if pd.notna(r.get("p_start"))    else None,
            float(r["p_end"])      if pd.notna(r.get("p_end"))      else None,
            float(r["return_pct"]) if pd.notna(r.get("return_pct")) else None,
            r["status_at_end"],
        ))

    conn.executemany(
        """INSERT OR REPLACE INTO research_stock_year
           (year, ticker, sector, per, pbr, market_cap, p_start, p_end, return_pct, status_at_end)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    return len(rows)


def main():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(DDL_STOCK_YEAR)
    conn.execute(DDL_YEAR_META)
    conn.commit()

    # 이미 적재된 연도 확인
    existing = {r[0] for r in conn.execute("SELECT DISTINCT year FROM research_stock_year")}

    total = 0
    for year in YEARS:
        if year in existing and year not in FORCE_YEARS:
            print(f"\n=== {year}년 === (이미 적재됨 — 건너뜀)")
            continue
        print(f"\n=== {year}년 ===")
        start = _first_trading_day(year)
        end   = _last_trading_day(year)

        # KOSPI 수익률 / 시장 국면
        kr  = _kospi_annual_return(start, end)
        ph  = _phase(kr)
        kr_s = f"{kr:+.1f}%" if kr is not None else "N/A"
        print(f"  KOSPI {kr_s} → {ph}")

        conn.execute(
            "INSERT OR REPLACE INTO research_year_meta (year, kospi_return, phase) VALUES (?,?,?)",
            (year, kr, ph),
        )
        conn.commit()

        n = _build_year(year, conn)
        print(f"  적재: {n}행")
        total += n

    conn.close()
    print(f"\n완료 — 총 {total}행 → {DB_PATH}")

    # 간단 검증
    conn = sqlite3.connect(str(DB_PATH))
    print("\n[검증] research_year_meta")
    for row in conn.execute("SELECT year, kospi_return, phase FROM research_year_meta ORDER BY year"):
        kr_str = f"{row[1]:+.1f}%" if row[1] is not None else "N/A"
        print(f"  {row[0]}: KOSPI {kr_str}  {row[2]}")

    print("\n[검증] research_stock_year — 연도별 status 집계")
    for row in conn.execute("""
        SELECT year, status_at_end, COUNT(*) as n
        FROM research_stock_year
        GROUP BY year, status_at_end
        ORDER BY year, status_at_end
    """):
        print(f"  {row[0]}  {row[1]:<12} {row[2]}행")

    conn.close()


if __name__ == "__main__":
    main()
