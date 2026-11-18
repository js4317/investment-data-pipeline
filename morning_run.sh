#!/bin/bash
# Morning Run 래퍼 — .env 로드 후 Python 실행
# launchd에서 호출됨. 로그는 /tmp/morning_run.log

cd /Users/jun/Desktop/investment

# .env 로드
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

/opt/homebrew/bin/python3 morning_run.py >> /Users/jun/Desktop/investment/morning_run.log 2>&1
