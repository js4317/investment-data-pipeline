#!/usr/bin/env python3
"""
auto_run.py — launchd 자동 실행 진입점

bash 래퍼(morning_run.sh)를 대체한다. Python을 직접 호출하므로
macOS launchd의 shell script 실행 제한을 우회할 수 있다.

실행 순서:
  1. .env 로드 (KRX API 키 등 환경변수)
  2. morning_run.py (스냅샷 수집 + 시장 가격 DB 갱신)
  3. record_oos.py  (OOS 신호 기록 + entry/alpha 채움)

로그: /Users/jun/Desktop/investment/morning_run.log (기존과 동일)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 네트워크 기본 timeout — pykrx import 전에 설치 (analyzer/net.py)
from analyzer.net import install_default_http_timeout
install_default_http_timeout()

# ── 1. .env 로드
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

# ── 2. morning_run
import morning_run as _mr
ret = _mr.main()

# ── 3. record_oos (morning_run 성공 여부 무관하게 fill 시도)
try:
    from scripts.record_oos import main as _oos_main
    _oos_main()
except Exception as e:
    print(f"[auto_run] record_oos 실패 (계속): {e}", flush=True)

sys.exit(ret)
