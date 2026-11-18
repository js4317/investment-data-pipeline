#!/bin/bash
cat > /tmp/investment_console.applescript << 'ASEOF'
on run
    set portInUse to do shell script "lsof -ti tcp:8501 >/dev/null 2>&1 && echo yes || echo no"
    if portInUse is "no" then
        do shell script "cd '/Users/jun/Desktop/investment' && nohup /opt/homebrew/bin/streamlit run app.py --server.headless true >> /tmp/investment_console.log 2>&1 &"
        delay 4
    end if
    do shell script "open 'http://localhost:8501'"
end run
ASEOF

rm -rf "/Users/jun/Desktop/investment/Investment Console.app"
osacompile -o "/Users/jun/Desktop/investment/Investment Console.app" /tmp/investment_console.applescript
echo "완료"
