#!/usr/bin/env python3
"""
공식 장전 신호 실행 (설계 §4~§5, 4단계).

- 매번 새 프로세스로 실행한다. 예정 실행 근거(부모 PID 1, 서비스 라벨, OS 시작 시각 60초 이내)가
  모두 충족될 때만 scheduled, 아니면 manual_cli로 기록한다. 시작 시각을 못 읽으면 중단한다.
- KIS 경로를 끄고 실행한다. 4단계에서는 입력 시점을 증명할 수 없어 표본은 eligible=0으로 확정된다.
- 운영 research.db는 저장소가 거부한다(운영 DB 허용은 별도 결정).

사용:
  python3 -B scripts/official_signal_run.py --db PATH --strategy test [--result-json PATH]
  python3 -B scripts/official_signal_run.py --db PATH --register-engine     # 격리 DB 검사용
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analyzer.net import install_default_http_timeout   # pykrx import 전
install_default_http_timeout()

from analyzer.pipeline.official import OFFICIAL_LABEL, SignalDeps, disable_kis, run_signal
disable_kis()

ENTRY_POINTS = ["scripts/official_signal_run.py"]


def _official_imports():
    """등록과 실행이 같은 import 상태를 갖도록 한 곳에서만 import한다."""
    import run as _run
    from analyzer.data import krx
    from analyzer.rules.market_regime import get_market_state
    from analyzer.watchlist.models import WatchlistState
    from analyzer.watchlist.store import load_all
    return _run, krx, get_market_state, WatchlistState, load_all


def build_deps(collect_launch=None, git_info=None) -> SignalDeps:
    from analyzer.pipeline.runtime import collect_launch_evidence, source_manifest
    _run, krx, get_market_state, WatchlistState, load_all = _official_imports()
    kw = {}
    if git_info is not None:
        kw["git_info"] = git_info
    return SignalDeps(
        collect_launch=collect_launch or collect_launch_evidence,
        manifest=lambda: source_manifest(ROOT, ENTRY_POINTS),
        universe=lambda: sorted(i.ticker for i in load_all()
                                if i.state in (WatchlistState.ACTIVE, WatchlistState.REVIEW)),
        market_state=get_market_state,
        analyze=_run.analyze_ticker,
        kis_active=lambda: krx._get_kis() is not None,
        **kw)


def register_engine(db: Path) -> str:
    import constants
    from analyzer.pipeline.official import _git_commit
    from analyzer.pipeline.runtime import source_manifest
    from database.signal_store import KST, SignalStore
    from datetime import datetime
    _official_imports()
    store = SignalStore(db)
    store.initialize()
    manifest = source_manifest(ROOT, ENTRY_POINTS)
    return store.register_engine(constants.ENGINE_VERSION, manifest.digest, _git_commit(ROOT), datetime.now(KST))


def main(argv=None) -> int:
    from database.signal_store import STRATEGY, TEST_STRATEGY
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--strategy", choices=["official", "test"])
    ap.add_argument("--register-engine", action="store_true")
    ap.add_argument("--result-json")
    a = ap.parse_args(argv)
    db = Path(a.db)
    if a.register_engine:
        print(json.dumps({"engine_id": register_engine(db)}))
        return 0
    if not a.strategy:
        ap.error("--strategy 필요 (official|test)")
    out = run_signal(db, build_deps(), strategy=STRATEGY if a.strategy == "official" else TEST_STRATEGY,
                     source_root=ROOT, expected_label=OFFICIAL_LABEL)
    text = json.dumps(out, ensure_ascii=False, indent=2, default=str)
    print(text)
    if a.result_json:
        Path(a.result_json).write_text(text, encoding="utf-8")
    return 0 if out.get("outcome") == "confirmed" else 1


if __name__ == "__main__":
    sys.exit(main())
