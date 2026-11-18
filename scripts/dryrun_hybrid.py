"""
Dry-run: apply_sector_correction 경로에서 PBR 점수 소멸 여부 확인.
실행: python3 scripts/dryrun_hybrid.py

확인 항목:
1. score() 결과 (CLI 경로)
2. apply_sector_correction() 후 결과
3. 두 결과의 valuation_score, available_max, normalized_score, grade 비교
"""
from datetime import date
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import analyze_ticker
from analyzer.scoring.scorer import build_sector_peers_map, apply_sector_correction
from analyzer.rules.market_regime import get_market_state

TODAY = date(2026, 10, 7)

# 테스트 종목: 일반기업, 지주사
CASES = [
    ("000270", "기아 (일반)"),
    ("028050", "삼성ENG (일반)"),
]
# 지주사 확인
try:
    from analyzer.holding_companies import HOLDING_TICKERS
    for t in list(HOLDING_TICKERS)[:1]:
        CASES.append((t, f"{t} (지주사)"))
except Exception:
    pass

market_state = get_market_state(TODAY)

print(f"{'종목':<20}  {'before val':>10}  {'after val':>10}  {'before avail':>12}  {'after avail':>11}  {'before norm':>11}  {'after norm':>10}  {'before grade':>12}  {'after grade':>10}")
print("-" * 120)

for ticker, label in CASES:
    try:
        ar = analyze_ticker(ticker, TODAY, market_state)
        s_before = ar.scoring

        # 더미 sector_map / peers_map (업종 분류 없이 절대값 fallback 유도)
        sector_map: dict = {}
        peers_map:  dict = {}
        ar_after = apply_sector_correction(ar, sector_map, peers_map)
        s_after = ar_after.scoring

        pbr = ar.validated.fundamentals.pbr if ar.validated and ar.validated.fundamentals else None
        pbr_str = f"PBR={pbr:.2f}" if pbr else "PBR=N/A"

        print(
            f"{label:<20}  "
            f"{s_before.valuation_score:>10}  "
            f"{s_after.valuation_score:>10}  "
            f"{s_before.available_max:>12}  "
            f"{s_after.available_max:>11}  "
            f"{s_before.normalized_score:>11}  "
            f"{s_after.normalized_score:>10}  "
            f"{s_before.grade.value:>12}  "
            f"{s_after.grade.value:>10}  "
            f"  ({pbr_str})"
        )
    except Exception as e:
        print(f"{label:<20}  오류: {e}")

print()
print("※ after val == before val이면 Hybrid가 동일 값 반환 (early return)")
print("※ after val < before val이면 PBR 점수 소멸 (버그 재현)")
