#!/usr/bin/env python3
"""
데모: API 키·네트워크 없이 공식 입력 경로를 끝까지 실행하고, 숫자 하나의 출처를 원 응답까지 추적한다.

- 가짜 전송(시험 고정 데이터, 20종목 · 1종목은 DART 기초 보고서 접수번호 불일치)으로 run_signal 실행
- 결과: 실행 상태, 종목 처리 상태(검증 제외 포함), 표본 범위·제외 기록
- 추적: 한 종목의 PBR 값 → 정의 ID → 사용한 원 응답(엔드포인트·요청·SHA-256) → 그 응답의 몇 번째 행 → 원문 값
- 재검증: 저장된 원 응답으로 모든 입력을 다시 계산해 일치 확인, 원 응답 수정 시도가 DB 트리거로 거부되는지 확인

사용: python3 scripts/demo_pipeline.py [--db 경로]   (기본: 임시 폴더, 실행 후 경로 출력)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests import test_validation_exclusion as F            # 시험 고정 데이터·가짜 전송 재사용
from analyzer.pipeline import official as OF


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", help="결과 DB 경로 (기본: 임시 폴더)")
    a = ap.parse_args()
    work = Path(a.db).parent if a.db else Path(tempfile.mkdtemp(prefix="pipeline_demo_"))
    work.mkdir(parents=True, exist_ok=True)
    (work / "src").mkdir(exist_ok=True)

    env = _env(work)
    faulty = F.TICKERS[0]
    out = F.run(env, {faulty: "roe_fail"})
    run_id = out["run_id"]
    db = env["db"]
    print(f"DB: {db}\n")
    print("■ 실행")
    print(f"  상태 {out['status']} / 확정 {out['outcome']} / 채점 {out['n_scored']} · 검증 제외 {out['n_excluded_validation']}"
          f" · 실패 {out['n_failed']} / 요청 {out['official_inputs']['requests']}건")

    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    scope = c.execute("SELECT * FROM cohort_scope").fetchone()
    print("\n■ 표본 범위 (확정과 같은 트랜잭션에 기록, 불변)")
    print(f"  정책 {scope['exclusion_policy']} / 범위 {scope['scope']} / 유니버스 {scope['universe_size']}"
          f" / 제외 비율 {scope['exclusion_ratio']:.0%}")
    for r in c.execute("SELECT ticker, processing_status, reason FROM cohort_exclusions"):
        print(f"  제외: {r['ticker']} {r['processing_status']} — {r['reason']}")

    tk = F.TICKERS[1]
    iid = f"{tk}:pbr"
    inp = c.execute("SELECT * FROM run_inputs WHERE run_id=? AND input_id=?", (run_id, iid)).fetchone()
    print(f"\n■ 추적: {iid}")
    print(f"  값 {json.loads(inp['value_json'])} / 상태 {inp['source_status']} / 정의 {inp['definition_id']}"
          f" / 기준 상태 {inp['basis_status']} / 조회 {inp['fetched_at']}")
    for row in c.execute("""SELECT r.response_id, r.row_index, s.endpoint, s.request_json, s.body_sha256, s.body_zlib
                            FROM run_input_rows r JOIN source_responses s
                              ON s.run_id=r.run_id AND s.response_id=r.response_id
                            WHERE r.run_id=? AND r.input_id=? ORDER BY s.endpoint""", (run_id, iid)):
        body = json.loads(zlib.decompress(row["body_zlib"]))
        rows = next(v for v in body.values() if isinstance(v, list))
        picked = rows[row["row_index"]]
        shown = {k: picked[k] for k in picked if k in ("TRD_DD", "BAS_DD", "ISU_CD", "short_code", "full_code",
                                                        "TDD_CLSPRC", "PBR", "marketCode")}
        print(f"  ← {row['endpoint']} 행 {row['row_index']} {shown}")
        print(f"     요청 {row['request_json']}  sha256 {row['body_sha256'][:16]}…")

    print("\n■ 재검증")
    audit = env["store"].audit_input_evidence(run_id)
    print(f"  저장된 원 응답으로 입력 {c.execute('SELECT count(*) FROM run_inputs WHERE run_id=?', (run_id,)).fetchone()[0]}개"
          f" 재계산: {'모두 일치' if audit is None else audit}")
    try:
        with sqlite3.connect(db) as w:
            w.execute("UPDATE source_responses SET body_sha256='0'*64 WHERE run_id=?", (run_id,))
        print("  원 응답 수정: 허용됨 (예상과 다름)")
    except sqlite3.DatabaseError as e:
        print(f"  원 응답 수정 시도: 거부됨 ({e})")
    return 0


def _env(work: Path):
    """시험 픽스처(env)와 같은 준비를 시험 밖에서 수행."""
    from types import ModuleType
    from database.signal_store import SignalStore
    from database.trading_calendar import TradingCalendarStore
    db = work / "demo.db"
    if db.exists():
        db.unlink()
    (work / "src" / "m.py").write_text("x=1\n")
    mod = ModuleType("m"); mod.__file__ = str(work / "src" / "m.py")
    store = SignalStore(db); store.initialize()
    cs = TradingCalendarStore(db)
    cs.save_version(F.CAL.version, list(F.CAL.days.values())); cs.save_notice_check(F.CAL.notice_check)
    store.register_engine(OF._engine_version(), "digest-A", "c", F.NOW)
    return dict(db=db, src=work / "src", mod=mod, store=store)


if __name__ == "__main__":
    sys.exit(main())
