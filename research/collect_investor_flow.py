#!/usr/bin/env python3
"""
투자자별 순매수 수집 (전종목·날짜별)
=====================================
pykrx get_market_trading_value_and_volume_by_ticker 사용:
  - 투자자 유형별로 전종목 한 번에 반환
  - 날짜당 ~6번 호출 (per-ticker 대비 ~20배 적음)

사용법:
  python research/collect_investor_flow.py          # 전체 수집
  python research/collect_investor_flow.py --probe  # 컬럼 구조 확인 후 종료
"""

from __future__ import annotations
import argparse, json, sqlite3, time
from datetime import datetime
from pathlib import Path

from pykrx import stock as krx

ROOT     = Path(__file__).resolve().parent.parent
RETRO_DB = ROOT / "database" / "research.db"
FLOW_DB  = ROOT / "database" / "research.db"
SNAP_DIR = ROOT / "backtests" / "snapshots"

SLEEP_BETWEEN_CALLS  = 0.5  # 초 — 같은 날짜 내 투자자 유형 간
SLEEP_BETWEEN_DATES  = 1.5  # 초 — 날짜 간
RETRY_MAX            = 4    # 날짜당 최대 재시도 횟수
RETRY_BASE_SLEEP     = 10   # 초 — 재시도 기본 대기 (지수 증가)

# 수집할 투자자 유형: (field_name, [KRX label candidates])
INVESTORS = [
    ("foreign_net",              ["외국인"]),
    ("institution_net",          ["기관합계"]),
    ("pension_net",              ["연기금"]),
    ("investment_trust_net",     ["투신"]),
    ("financial_investment_net", ["금융투자"]),
    ("private_fund_net",         ["사모"]),
    ("insurance_net",            ["보험"]),
]

# ── DB 초기화 ─────────────────────────────────────────────────────────────────
def init_db(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS investor_flow (
            signal_date              TEXT NOT NULL,
            ticker                   TEXT NOT NULL,
            foreign_net              INTEGER,
            institution_net          INTEGER,
            pension_net              INTEGER,
            investment_trust_net     INTEGER,
            financial_investment_net INTEGER,
            private_fund_net         INTEGER,
            insurance_net            INTEGER,
            created_at               TEXT NOT NULL,
            PRIMARY KEY (signal_date, ticker)
        )
    """)
    conn.commit()

# ── 대상 날짜 ─────────────────────────────────────────────────────────────────
def target_dates() -> list[str]:
    # 실제 거래일만 (market_ohlcv.db 기준)
    ohlcv_db = ROOT / "database" / "market_ohlcv.db"
    trading_days: set[str] = set()
    if ohlcv_db.exists():
        with sqlite3.connect(ohlcv_db) as c:
            for row in c.execute(
                "SELECT DISTINCT trade_date FROM market_ohlcv WHERE ticker='1001'"
            ):
                trading_days.add(row[0])

    dates: set[str] = set()
    if RETRO_DB.exists():
        with sqlite3.connect(RETRO_DB) as c:
            for row in c.execute("SELECT DISTINCT signal_date FROM retro_calibration"):
                dates.add(row[0])
    for f in SNAP_DIR.glob("*.json"):
        raw = json.loads(f.read_text())
        d = raw.get("date") or f.stem
        if d:
            dates.add(d)

    # 거래일 필터
    return sorted(d for d in dates if d in trading_days)

def done_dates(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT DISTINCT signal_date FROM investor_flow")}

# ── 날짜별 수집 ───────────────────────────────────────────────────────────────
def _safe_int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

def collect_date(signal_date: str) -> dict[str, dict]:
    """
    반환: {ticker: {field_name: value, ...}}
    """
    date_str = signal_date.replace("-", "")
    result: dict[str, dict] = {}

    for field_name, label_candidates in INVESTORS:
        # 실제 KRX 레이블 찾기 (첫 번째 성공한 것 사용)
        df = None
        used_label = None
        for label in label_candidates:
            try:
                df = krx.get_market_net_purchases_of_equities_by_ticker(
                    date_str, date_str, "KOSPI", label
                )
                if df is not None and not df.empty:
                    used_label = label
                    break
            except Exception:
                pass

        if df is None or df.empty:
            print(f"  [{field_name}] 데이터 없음")
            time.sleep(SLEEP_BETWEEN_CALLS)
            continue

        # 순매수 컬럼 찾기
        net_col = None
        for candidate in ["순매수거래대금", "순매수대금", "순매수금액", "순매수"]:
            if candidate in df.columns:
                net_col = candidate
                break
        if net_col is None:
            print(f"  [{field_name}] 순매수 컬럼 없음: {df.columns.tolist()}")
            time.sleep(SLEEP_BETWEEN_CALLS)
            continue

        for ticker, row in df.iterrows():
            ticker_str = str(ticker)
            if ticker_str not in result:
                result[ticker_str] = {}
            result[ticker_str][field_name] = _safe_int(row[net_col])

        # KOSDAQ도 동일하게
        try:
            df_daq = krx.get_market_net_purchases_of_equities_by_ticker(
                date_str, date_str, "KOSDAQ", used_label
            )
            if df_daq is not None and not df_daq.empty and net_col in df_daq.columns:
                for ticker, row in df_daq.iterrows():
                    ticker_str = str(ticker)
                    if ticker_str not in result:
                        result[ticker_str] = {}
                    result[ticker_str][field_name] = _safe_int(row[net_col])
        except Exception:
            pass

        time.sleep(SLEEP_BETWEEN_CALLS)

    return result

# ── probe ─────────────────────────────────────────────────────────────────────
def probe():
    """실제 컬럼 구조 + 유효한 investor label 확인"""
    test_date = "20260102"  # pykrx 포맷 버그로 최신 날짜 불가 → 구형 날짜 사용
    print(f"probe 날짜: {test_date}")

    all_labels = ["외국인", "기관합계", "기관", "연기금", "연기금 등", "연기금등",
                  "투신", "금융투자", "사모", "보험", "개인"]
    for label in all_labels:
        try:
            df = krx.get_market_net_purchases_of_equities_by_ticker(
                test_date, test_date, "KOSPI", label
            )
            if df is not None and not df.empty:
                print(f"  ✓ label='{label}' → shape={df.shape}, columns={df.columns.tolist()}")
                if label == "외국인":
                    print(f"    sample:\n{df.head(2)}")
            else:
                print(f"  ✗ label='{label}' → 빈 응답")
        except Exception as e:
            print(f"  ✗ label='{label}' → 오류: {e}")

# ── main ──────────────────────────────────────────────────────────────────────
def all_trading_days(from_date: str, to_date: str) -> list[str]:
    """pykrx 삼성전자 OHLCV 기반 거래일 목록 (2026 이전도 지원)"""
    fd = from_date.replace("-", "")
    td = to_date.replace("-", "")
    df = krx.get_market_ohlcv_by_date(fd, td, "005930")  # 삼성전자
    if df is None or df.empty:
        return []
    return sorted(d.strftime("%Y-%m-%d") for d in df.index)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--from", dest="from_date", default=None,
                        help="수집 시작일 (YYYY-MM-DD). 지정 시 해당 날짜부터 오늘까지 전체 거래일 수집")
    parser.add_argument("--to", dest="to_date", default=None,
                        help="수집 종료일 (YYYY-MM-DD). --from 없이는 무시됨")
    args = parser.parse_args()

    if args.probe:
        probe()
        return

    with sqlite3.connect(FLOW_DB) as conn:
        init_db(conn)

        if args.from_date:
            # 날짜 범위 직접 지정 — 전체 거래일 수집
            to_date = args.to_date or datetime.now().strftime("%Y-%m-%d")
            all_dates = all_trading_days(args.from_date, to_date)
            print(f"범위 지정 모드: {args.from_date} ~ {to_date} ({len(all_dates)}거래일)")
        else:
            all_dates = target_dates()

        done    = done_dates(conn)
        pending       = [d for d in all_dates if d not in done]
        done_in_range = len(all_dates) - len(pending)
        print(f"전체: {len(all_dates)}일 / 완료: {done_in_range}일 / 미처리: {len(pending)}일")

        total_saved = 0
        now_str = datetime.now().isoformat()

        for i, d in enumerate(pending):
            print(f"\n[{i+1}/{len(pending)}] {d}")

            tickers = {}
            for attempt in range(RETRY_MAX):
                try:
                    tickers = collect_date(d)
                    break
                except Exception as e:
                    wait = RETRY_BASE_SLEEP * (2 ** attempt)
                    print(f"  오류({attempt+1}/{RETRY_MAX}): {e}  — {wait}초 후 재시도")
                    time.sleep(wait)
            else:
                print(f"  {RETRY_MAX}회 실패 — {d} 건너뜀")
                continue

            if not tickers:
                print("  데이터 없음 — skip")
                continue

            fields = ["foreign_net","institution_net","pension_net",
                      "investment_trust_net","financial_investment_net",
                      "private_fund_net","insurance_net"]
            conn.executemany(
                """INSERT OR REPLACE INTO investor_flow
                   (signal_date,ticker,foreign_net,institution_net,pension_net,
                    investment_trust_net,financial_investment_net,private_fund_net,
                    insurance_net,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                [(d, t) + tuple(v.get(f) for f in fields) + (now_str,)
                 for t, v in tickers.items()]
            )
            conn.commit()
            print(f"  {len(tickers)}개 종목 저장")
            total_saved += len(tickers)

            if i < len(pending) - 1:
                time.sleep(SLEEP_BETWEEN_DATES)

        print(f"\n=== 완료 — 총 {total_saved}행 저장 ===")

if __name__ == "__main__":
    main()
