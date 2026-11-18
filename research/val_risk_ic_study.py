"""
val_score / risk_score (PBR 기반) IC 연구 — 2020~2026 전체 기간
- val_score: PER → 점수 테이블 직접 적용
- risk_proxy: PBR로 대리 (낮을수록 낮은 레버리지 기업 경향 — 임시)
- alpha_20d = (stock_ret_20d - kospi_ret_20d) * 100

데이터:
  pit_market_fundamentals (research.db) : PER, PBR 일별
  market_ohlcv (market_ohlcv.db)        : 가격 일별

샘플링: 4주 간격 (매 20거래일 첫 날) — 비중복 alpha 윈도우
"""
import sqlite3, numpy as np, sys
from scipy.stats import spearmanr
from collections import defaultdict

PER_SCORE = [(6,25),(8,22),(10,18),(12,14),(15,10),(20,6)]

def per_to_val(per):
    if per is None or per <= 0: return None
    for threshold, score in PER_SCORE:
        if per <= threshold: return score
    return 0   # >20 → 0

# ── 데이터 로드 ──────────────────────────────────────────
print("Loading pit_market_fundamentals...", flush=True)
conn_r  = sqlite3.connect("database/research.db")
conn_o  = sqlite3.connect("database/market_ohlcv.db")

# PER/PBR: per > 0 필터 (적자·N/A 제외)
pit = conn_r.execute("""
    SELECT trade_date, ticker, per, pbr
    FROM pit_market_fundamentals
    WHERE per > 0 AND per < 100
      AND market IN ('KOSPI','KOSDAQ')
""").fetchall()
print(f"  pit rows: {len(pit):,}", flush=True)

# OHLCV: close만
print("Loading market_ohlcv...", flush=True)
ohlcv_raw = conn_o.execute("SELECT ticker, trade_date, close FROM market_ohlcv").fetchall()
conn_r.close(); conn_o.close()

# 가격 인덱스: {ticker: {date: close}}
print("Building price index...", flush=True)
price = defaultdict(dict)
for ticker, dt, close in ohlcv_raw:
    price[ticker][dt] = close
del ohlcv_raw

# 거래일 정렬
all_dates = sorted({row[0] for row in pit})
date_idx  = {d: i for i, d in enumerate(all_dates)}

# ── KOSPI 지수 (069500 ETF 또는 005930 대신 '0001' 코드 확인) ──
# KOSPI 지수 코드 후보
kospi_price = price.get('1001', {})
print(f"  KOSPI index '1001': dates={len(kospi_price)}", flush=True)
if not kospi_price:
    print("ERROR: KOSPI index '1001' not found"); sys.exit(1)

# ── 샘플링: 20거래일 간격 ──────────────────────────────
# pit를 {date: [(ticker, val_score, pbr)]} 로 변환
pit_by_date = defaultdict(list)
for dt, ticker, per, pbr in pit:
    vs = per_to_val(per)
    if vs is None: continue
    pit_by_date[dt].append((ticker, vs, pbr))

sample_dates = all_dates[::20]   # 20거래일 간격 비중복
print(f"  sample dates: {len(sample_dates)} ({sample_dates[0]}~{sample_dates[-1]})", flush=True)

# ── alpha_20d 계산 ────────────────────────────────────
results = []   # (date, ticker, val_score, pbr, alpha_20d)

for dt in sample_dates:
    i = date_idx[dt]
    if i + 20 >= len(all_dates):
        continue
    dt_exit = all_dates[i + 20]

    kospi_entry = kospi_price.get(dt)
    kospi_exit  = kospi_price.get(dt_exit)
    if not kospi_entry or not kospi_exit or kospi_entry <= 0:
        continue
    kospi_ret = kospi_exit / kospi_entry - 1

    for ticker, vs, pbr in pit_by_date.get(dt, []):
        if pbr is None or pbr <= 0:
            continue
        p_entry = price[ticker].get(dt)
        p_exit  = price[ticker].get(dt_exit)
        if not p_entry or not p_exit or p_entry <= 0:
            continue
        stock_ret = p_exit / p_entry - 1
        alpha     = (stock_ret - kospi_ret) * 100
        results.append((dt, ticker, vs, pbr, alpha))

print(f"\nTotal observations: {len(results):,}", flush=True)
if not results:
    print("No results — check data."); sys.exit(1)

val_arr   = np.array([r[2] for r in results], dtype=float)
pbr_arr   = np.array([r[3] for r in results], dtype=float)
alpha_arr = np.array([r[4] for r in results], dtype=float)

# ── 날짜별 cross-section IC ──────────────────────────
by_date = defaultdict(list)
for row in results:
    by_date[row[0]].append(row[2:])  # (val, pbr, alpha)

date_ics_val, date_ics_pbr = [], []
for dt, items in sorted(by_date.items()):
    if len(items) < 15: continue
    v = [x[0] for x in items]
    p = [x[1] for x in items]
    a = [x[2] for x in items]
    ic_v, _ = spearmanr(v, a)
    ic_p, _ = spearmanr(p, a)
    date_ics_val.append(ic_v)
    date_ics_pbr.append(ic_p)

def summary(name, ics):
    arr = np.array(ics)
    t = arr.mean() / (arr.std() / np.sqrt(len(arr))) if arr.std() > 0 else 0
    pct_pos = (arr > 0).mean() * 100
    return f"{name}: mean_IC={arr.mean():.4f}, std={arr.std():.4f}, t={t:.2f}, pct_pos={pct_pos:.0f}%, n={len(arr)}"

print("\n=== Cross-section IC (날짜별, 20거래일 비중복 샘플) ===")
print(summary("val_score (PER)", date_ics_val))
print(summary("pbr (낮을수록↑)", [-x for x in date_ics_pbr]))  # PBR 낮을수록 좋으면 IC 음수여야 함
print(summary("pbr raw IC     ", date_ics_pbr))

# ── val_score 분위별 alpha ────────────────────────────
print("\n=== val_score 분위별 평균 alpha_20d ===")
for label, lo, hi in [(0,0,0),(6,1,6),(10,7,10),(14,11,14),(18,15,18),(22,19,22),(25,23,25)]:
    mask = (val_arr >= lo) & (val_arr <= hi)
    if mask.sum() < 10: continue
    n = mask.sum()
    a = alpha_arr[mask]
    print(f"  val={lo:2d}~{hi:2d}: n={n:6,}, avg_alpha={a.mean():.2f}%, median={np.median(a):.2f}%")

# ── PBR 분위별 alpha ─────────────────────────────────
print("\n=== PBR 분위별 평균 alpha_20d ===")
for label, lo, hi in [("<0.5",0,0.5),("0.5-1",0.5,1.0),("1-2",1.0,2.0),("2-3",2.0,3.0),(">3",3.0,999)]:
    mask = (pbr_arr >= lo) & (pbr_arr < hi)
    if mask.sum() < 10: continue
    a = alpha_arr[mask]
    print(f"  PBR {label}: n={mask.sum():6,}, avg_alpha={a.mean():.2f}%")
