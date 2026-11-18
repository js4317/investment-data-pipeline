#!/usr/bin/env python3
"""
공식 장후 가격 수집 (설계 §7, 4단계).

미완료 (거래일×시장)만 KRX Open API로 수집하고, 완결성 검사를 통과한 것만 공식 가격으로 저장한 뒤
확정 결과만 성과로 채운다. 신호는 만들지 않는다. 대체 원천으로 자동 전환하지 않는다.
재시도 일정(18:30 → 19:30 → 21:00 → 다음 날 08:00)은 launchd 항목으로 둔다(설치는 별도 결정).

사용:
  python3 -B scripts/official_price_collect.py --db PATH [--result-json PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analyzer.net import install_default_http_timeout
install_default_http_timeout()


def main(argv=None) -> int:
    from analyzer.pipeline.official import collect_prices
    from database.price_store import krx_open_api_fetch
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--calendar-version")
    ap.add_argument("--result-json")
    a = ap.parse_args(argv)
    out = collect_prices(Path(a.db), krx_open_api_fetch, calendar_version=a.calendar_version)
    text = json.dumps(out, ensure_ascii=False, indent=2, default=str)
    print(text)
    if a.result_json:
        Path(a.result_json).write_text(text, encoding="utf-8")
    return 0 if out.get("outcome") == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
