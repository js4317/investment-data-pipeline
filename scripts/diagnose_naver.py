"""
Naver PARSE_ERROR 진단 스크립트.
실행: python3 scripts/diagnose_naver.py

확인 항목:
1. 세 API URL 각각 상태코드 + 응답 구조
2. 파싱 포인트별 실패 위치 출력
3. 캐시 DB 상태
"""
import json, time, sqlite3
from pathlib import Path
import requests

TICKER = "000270"  # 기아 (테스트 종목)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": "https://finance.naver.com/",
})

APIS = {
    "summary (OP quarterly)": f"https://m.stock.naver.com/api/stock/{TICKER}/finance/summary",
    "quarter (NI/부채/ROE)":   f"https://m.stock.naver.com/api/stock/{TICKER}/finance/quarter",
    "annual  (NI/부채/ROE)":   f"https://m.stock.naver.com/api/stock/{TICKER}/finance/annual",
}

print(f"=== Naver API 진단 ({TICKER}) ===\n")

for label, url in APIS.items():
    print(f"--- {label} ---")
    print(f"URL: {url}")
    try:
        resp = SESSION.get(url, timeout=10)
        print(f"Status: {resp.status_code}")
        print(f"Content-Type: {resp.headers.get('Content-Type','')}")

        if resp.status_code != 200:
            print(f"응답 body 첫 300자: {resp.text[:300]}")
            print()
            continue

        try:
            raw = resp.json()
        except Exception as e:
            print(f"JSON 파싱 실패: {e}")
            print(f"응답 body 첫 300자: {resp.text[:300]}")
            print()
            continue

        # summary API 구조 검사
        if "summary" in label:
            q_data  = raw.get("chartIncomeStatement", {}).get("quarter", {})
            columns = q_data.get("columns", [])
            tr_list = q_data.get("trTitleList", [])
            print(f"chartIncomeStatement.quarter.columns: {len(columns)}개")
            print(f"chartIncomeStatement.quarter.trTitleList: {len(tr_list)}개")
            if columns:
                print(f"  첫 column 레이블: {[c[0] if c else None for c in columns[:5]]}")
            actual_idx = [i for i, e in enumerate(tr_list) if e.get("isConsensus","N") == "N"]
            print(f"  isConsensus=N 인덱스: {actual_idx}")
            # OP 행 탐색
            op_found = any(c and c[0] == "영업이익" for c in columns[1:] if c)
            print(f"  '영업이익' 행 존재: {op_found}")

        # quarter/annual API 구조 검사
        else:
            fi       = raw.get("financeInfo", {})
            tr_list  = fi.get("trTitleList", [])
            row_list = fi.get("rowList", [])
            print(f"financeInfo.trTitleList: {len(tr_list)}개")
            print(f"financeInfo.rowList: {len(row_list)}개")
            actual_keys = [t["key"] for t in tr_list if t.get("isConsensus","N") == "N"]
            print(f"  actual keys: {actual_keys[:6]}")
            titles = [r.get("title") for r in row_list]
            print(f"  row titles: {titles[:10]}")
            found_ni   = "당기순이익" in titles
            found_debt = "부채비율"   in titles
            found_roe  = "ROE"       in titles
            print(f"  당기순이익: {found_ni} / 부채비율: {found_debt} / ROE: {found_roe}")

        print()
        time.sleep(0.5)

    except Exception as e:
        print(f"요청 자체 실패: {type(e).__name__}: {e}")
        print()

# 캐시 DB 상태
print("=== 캐시 DB 상태 ===")
db_path = Path.home() / ".investment_cache" / "cache.db"
if not db_path.exists():
    print(f"캐시 DB 없음 ({db_path})")
    print("→ quarterly 데이터 한 번도 disk 저장된 적 없음")
else:
    conn = sqlite3.connect(str(db_path))
    total = conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
    q_cnt = conn.execute("SELECT COUNT(*) FROM cache WHERE ns='quarterly'").fetchone()[0]
    now   = time.time()
    print(f"총 항목: {total}개  /  quarterly: {q_cnt}개")
    if q_cnt:
        rows = conn.execute(
            "SELECT key, ts FROM cache WHERE ns='quarterly' ORDER BY ts DESC LIMIT 5"
        ).fetchall()
        for key, ts in rows:
            age_d = (now - ts) / 86400
            print(f"  {key} — {age_d:.1f}일 전")
    conn.close()
