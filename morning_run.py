#!/usr/bin/env python3
"""
Standalone Morning Run — Streamlit 없이 스냅샷 수집.
launchd 자동 실행 또는 터미널 직접 실행 모두 가능.

Usage:
    python3 morning_run.py
    python3 morning_run.py --dry-run   # 분석만, 저장 안 함
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

# 네트워크 기본 timeout — pykrx import 전에 설치 (analyzer/net.py)
from analyzer.net import install_default_http_timeout
install_default_http_timeout()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def main(dry_run: bool = False, as_of: date | None = None) -> int:
    today = as_of or date.today()
    log.info("Morning Run 시작 — %s%s", today, "  [DRY RUN]" if dry_run else "")

    from run import analyze_ticker
    from analyzer.rules.market_regime import get_market_state
    from analyzer.watchlist.store import (
        load_all as wl_load_all,
        load as wl_load,
        save as wl_save,
        save_trigger,
    )
    from analyzer.watchlist.models import WatchlistState
    from analyzer.watchlist import transitions as wl_transitions
    from backtests import snapshot as bt_snapshot
    from constants import Confidence

    items   = wl_load_all()
    tickers = [i.ticker for i in items
               if i.state in (WatchlistState.ACTIVE, WatchlistState.REVIEW)]

    if not tickers:
        log.warning("워치리스트 ACTIVE/REVIEW 종목 없음 — 종료")
        return 1

    log.info("분석 대상: %d종목", len(tickers))
    market_state = get_market_state(today)

    results = []
    for idx, ticker in enumerate(tickers):
        log.info("[%d/%d] %s", idx + 1, len(tickers), ticker)
        try:
            result = analyze_ticker(ticker, today, market_state)
            results.append(result)

            if not dry_run:
                existing = wl_load(ticker)
                if existing:
                    updated, triggers = wl_transitions.apply(existing, result, today)
                    wl_save(updated)
                    for t in triggers:
                        save_trigger(t, today)
        except Exception as e:
            log.error("%s 분석 실패: %s", ticker, e)

    if not results:
        log.error("분석 결과 없음 — 종료")
        return 1

    # 모멘텀 오버레이
    log.info("모멘텀 오버레이 계산 중...")
    mom_map: dict = {}
    try:
        from analyzer.data.momentum import batch_fetch_momentum as _batch_mom
        mom_inputs = [(r.scoring.ticker, r.scoring.valuation_score) for r in results]
        mom_map    = _batch_mom(mom_inputs, today, max_workers=4)
    except Exception as e:
        log.warning("모멘텀 fetch 실패 (계속): %s", e)

    # 섹터
    log.info("섹터 수집 중...")
    sector_map: dict = {}
    try:
        from analyzer.data.krx import prefetch_all_sectors as _prefetch_sectors
        sector_map = _prefetch_sectors(today)
    except Exception as e:
        log.warning("섹터 fetch 실패 (계속): %s", e)

    # 가격 컨텍스트
    pc_map: dict = {}
    try:
        from analyzer.data.krx import fetch_price_context as _fpc
        for r in results:
            try:
                pc = _fpc(r.scoring.ticker, today)
                if pc:
                    pc_map[r.scoring.ticker] = pc
            except Exception:
                pass
    except Exception as e:
        log.warning("가격 컨텍스트 fetch 실패 (계속): %s", e)

    if dry_run:
        log.info("[DRY RUN] 저장 생략 — %d종목 분석 완료", len(results))
        return 0

    # 시장 가격 DB 수집 — 전체 KOSPI/KOSDAQ batch (3 API 호출)
    log.info("시장 가격 DB 수집 중 (전체 시장 batch)...")
    try:
        from backtests.market_db import init_db as _mdb_init, collect_date as _mdb_collect
        _mdb_init()
        n_saved = _mdb_collect(today)
        log.info("시장 가격 DB 저장 완료 — %d행", n_saved)
    except Exception as e:
        log.warning("시장 가격 DB 수집 실패 (계속): %s", e)

    # Earnings Regime — CYCLICAL-1 forward validation (점수 무관)
    er_map: dict = {}
    try:
        from analyzer.data.earnings_history import build_earnings_regime_map as _build_er
        tickers = [r.scoring.ticker for r in results]
        er_map = _build_er(tickers, today)
        log.info("earnings_regime 계산 완료 — %d종목", len(er_map))
    except Exception as e:
        log.warning("earnings_regime 계산 실패 (계속): %s", e)

    # 스냅샷 저장
    log.info("스냅샷 저장 중...")
    bt_snapshot.save(
        results, today,
        mom_map=mom_map,
        sector_map=sector_map,
        price_context_map=pc_map or None,
        earnings_regime_map=er_map or None,
        universe="WATCHLIST",
        universe_label="워치리스트 ACTIVE/REVIEW",
    )

    partial = any(r.scoring.confidence == Confidence.LOW for r in results)
    status  = "partial" if partial else "success"
    log.info("완료 — %s  %d종목  status=%s", today, len(results), status)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Investment Console — Morning Run")
    parser.add_argument("--dry-run", action="store_true", help="분석만, 저장 안 함")
    parser.add_argument("--date", metavar="YYYY-MM-DD", help="백필용 날짜 지정 (기본: 오늘)")
    args = parser.parse_args()
    as_of = date.fromisoformat(args.date) if args.date else None
    sys.exit(main(dry_run=args.dry_run, as_of=as_of))
