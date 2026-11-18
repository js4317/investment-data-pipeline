"""
Stage 20I — Production Score Regression
retro_calibration × pit_market_fundamentals 동일 표본에서
기존 scoring vs candidate (val_score + pbr_score) 비교.

PBR score table (20H 확정):
  <0.5→25, 0.5-1→20, 1-2→15, 2-3→10, ≥3→0

변경: available_max 75→100 (val component 25→50)
"""
import sqlite3, numpy as np
from scipy.stats import spearmanr
from collections import defaultdict

def pbr_to_score(pbr):
    if pbr is None or pbr <= 0: return None
    if pbr < 0.5:  return 25
    if pbr < 1.0:  return 20
    if pbr < 2.0:  return 15
    if pbr < 3.0:  return 10
    return 0

def grade(norm):
    if norm >= 85: return "STRONG_BUY"
    if norm >= 70: return "BUY"
    if norm >= 55: return "NEUTRAL"
    return "AVOID"

conn = sqlite3.connect("database/research.db")
rows = conn.execute("""
    SELECT rc.signal_date, rc.ticker,
           rc.valuation_score, rc.earnings_score, rc.risk_score,
           rc.normalized_score, rc.grade,
           rc.alpha_20d,
           pit.pbr,
           pit.per
    FROM retro_calibration rc
    JOIN pit_market_fundamentals pit
      ON rc.signal_date = pit.trade_date AND rc.ticker = pit.ticker
    WHERE rc.alpha_20d IS NOT NULL AND pit.pbr > 0
    ORDER BY rc.signal_date, rc.ticker
""").fetchall()
conn.close()

# KOSPI regime: market_ohlcv.db에서 별도 로드
conn_o = sqlite3.connect("database/market_ohlcv.db")
kospi_rows = conn_o.execute(
    "SELECT trade_date, close FROM market_ohlcv WHERE ticker='1001' ORDER BY trade_date"
).fetchall()
conn_o.close()

kospi_price = {r[0]: r[1] for r in kospi_rows}
all_kospi_dates = sorted(kospi_price.keys())
kospi_idx = {d: i for i, d in enumerate(all_kospi_dates)}

def kospi_ret_20d(dt):
    i = kospi_idx.get(dt)
    if i is None or i + 20 >= len(all_kospi_dates): return None
    dt2 = all_kospi_dates[i + 20]
    p1 = kospi_price.get(dt); p2 = kospi_price.get(dt2)
    if not p1 or not p2: return None
    return (p2 / p1 - 1) * 100

# 각 행에 candidate score 계산
data = []
for row in rows:
    sig_dt, ticker, val, earn, risk, norm_old, grade_old, alpha, pbr, per = row
    pbr_sc = pbr_to_score(pbr)
    if pbr_sc is None: continue
    
    # 기존: total=val+earn+risk, avail=75
    total_old = val + earn + risk
    norm_old_calc = round(total_old / 75 * 100)
    grade_old_calc = grade(norm_old_calc)
    
    # 신규: total=val+pbr+earn+risk, avail=100
    total_new = val + pbr_sc + earn + risk
    norm_new  = round(total_new / 100 * 100)
    grade_new = grade(norm_new)
    
    kr = kospi_ret_20d(sig_dt)
    
    data.append({
        "dt": sig_dt, "ticker": ticker,
        "val": val, "earn": earn, "risk": risk, "pbr_sc": pbr_sc, "pbr": pbr,
        "total_old": total_old, "norm_old": norm_old_calc, "grade_old": grade_old_calc,
        "total_new": total_new, "norm_new": norm_new, "grade_new": grade_new,
        "alpha": alpha, "kr": kr,
    })

n = len(data)
print(f"=== Stage 20I: Production Score Regression ===")
print(f"Matched sample n={n:,} ({len(set(d['dt'] for d in data))} signal-dates)\n")

# ─── 1. 신호 변화 ────────────────────────────────────
print("─── 1. Grade 분포 비교 ───")
from collections import Counter
gc_old = Counter(d["grade_old"] for d in data)
gc_new = Counter(d["grade_new"] for d in data)
for g in ["STRONG_BUY","BUY","NEUTRAL","AVOID"]:
    print(f"  {g:12s}: 기존 {gc_old[g]:5,} → 신규 {gc_new[g]:5,}  (Δ{gc_new[g]-gc_old[g]:+,})")

print()
sb_old = {(d["dt"],d["ticker"]) for d in data if d["grade_old"]=="STRONG_BUY"}
sb_new = {(d["dt"],d["ticker"]) for d in data if d["grade_new"]=="STRONG_BUY"}
print(f"  STRONG_BUY overlap: {len(sb_old & sb_new):,} / 기존 {len(sb_old):,} ({100*len(sb_old&sb_new)/len(sb_old):.0f}%)")
print(f"  신규 진입: {len(sb_new-sb_old):,}  이탈: {len(sb_old-sb_new):,}")

# ─── 2. IC 비교 (날짜별 cross-section) ───────────────
print()
print("─── 2. Cross-section IC (동일 표본) ───")
by_date = defaultdict(list)
for d in data:
    by_date[d["dt"]].append(d)

ic_old_list, ic_new_list, delta_list = [], [], []
for dt, items in sorted(by_date.items()):
    if len(items) < 10: continue
    norm_o = np.array([x["norm_old"] for x in items], float)
    norm_n = np.array([x["norm_new"] for x in items], float)
    alpha  = np.array([x["alpha"]    for x in items], float)
    ic_o = spearmanr(norm_o, alpha)[0]
    ic_n = spearmanr(norm_n, alpha)[0]
    ic_old_list.append(ic_o); ic_new_list.append(ic_n); delta_list.append(ic_n - ic_o)

ao = np.array(ic_old_list); an = np.array(ic_new_list); ad = np.array(delta_list)
to = ao.mean()/(ao.std()/np.sqrt(len(ao))); tn = an.mean()/(an.std()/np.sqrt(len(an)))
td = ad.mean()/(ad.std()/np.sqrt(len(ad)))
print(f"  기존 IC: {ao.mean():.4f} (t={to:.2f}, pos={100*(ao>0).mean():.0f}%)")
print(f"  신규 IC: {an.mean():.4f} (t={tn:.2f}, pos={100*(an>0).mean():.0f}%)")
print(f"  ΔIC:     {ad.mean():.4f} (t={td:.2f}, pos={100*(ad>0).mean():.0f}%)")

# ─── 3. STRONG_BUY alpha 비교 ────────────────────────
print()
print("─── 3. STRONG_BUY 선택 alpha_20d 비교 ───")
alpha_sb_old = np.array([d["alpha"] for d in data if d["grade_old"]=="STRONG_BUY"])
alpha_sb_new = np.array([d["alpha"] for d in data if d["grade_new"]=="STRONG_BUY"])
print(f"  기존 SB alpha: mean={alpha_sb_old.mean():.2f}%, median={np.median(alpha_sb_old):.2f}%, n={len(alpha_sb_old):,}")
print(f"  신규 SB alpha: mean={alpha_sb_new.mean():.2f}%, median={np.median(alpha_sb_new):.2f}%, n={len(alpha_sb_new):,}")

# ─── 4. PBR tier별 구성 (STRONG_BUY 기준) ────────────
print()
print("─── 4. STRONG_BUY PBR tier 구성 비교 ───")
tier_label = lambda pbr: ("<0.5" if pbr<0.5 else ("0.5-1" if pbr<1 else ("1-2" if pbr<2 else ("2-3" if pbr<3 else "≥3"))))
tc_old = Counter(tier_label(d["pbr"]) for d in data if d["grade_old"]=="STRONG_BUY")
tc_new = Counter(tier_label(d["pbr"]) for d in data if d["grade_new"]=="STRONG_BUY")
for t in ["<0.5","0.5-1","1-2","2-3","≥3"]:
    no=tc_old[t]; nn=tc_new[t]
    pct_o = 100*no/len(alpha_sb_old) if len(alpha_sb_old) else 0
    pct_n = 100*nn/len(alpha_sb_new) if len(alpha_sb_new) else 0
    print(f"  PBR {t:6s}: 기존 {no:4,}({pct_o:.0f}%) → 신규 {nn:4,}({pct_n:.0f}%)")

# ─── 5. Bear/Bull 구간 분리 ──────────────────────────
print()
print("─── 5. Bear/Bull 구간 STRONG_BUY alpha ───")
# kospi 20d ret 기준: <-3% = Bear
bear_sb_old = [d["alpha"] for d in data if d["grade_old"]=="STRONG_BUY" and d["kr"] is not None and d["kr"]<-3]
bull_sb_old = [d["alpha"] for d in data if d["grade_old"]=="STRONG_BUY" and d["kr"] is not None and d["kr"]>= -3]
bear_sb_new = [d["alpha"] for d in data if d["grade_new"]=="STRONG_BUY" and d["kr"] is not None and d["kr"]<-3]
bull_sb_new = [d["alpha"] for d in data if d["grade_new"]=="STRONG_BUY" and d["kr"] is not None and d["kr"]>= -3]

def fmt_a(arr): return f"mean={np.mean(arr):.2f}%, n={len(arr):,}" if arr else "n=0"
print(f"  Bear(<-3%) | 기존 SB: {fmt_a(bear_sb_old)}")
print(f"             | 신규 SB: {fmt_a(bear_sb_new)}")
print(f"  Bull(≥-3%) | 기존 SB: {fmt_a(bull_sb_old)}")
print(f"             | 신규 SB: {fmt_a(bull_sb_new)}")

# ─── 6. 점수 분포 변화 ───────────────────────────────
print()
print("─── 6. normalized_score 분포 변화 ───")
no_arr = np.array([d["norm_old"] for d in data]); nn_arr = np.array([d["norm_new"] for d in data])
print(f"  기존: mean={no_arr.mean():.1f}, std={no_arr.std():.1f}, min={no_arr.min()}, max={no_arr.max()}")
print(f"  신규: mean={nn_arr.mean():.1f}, std={nn_arr.std():.1f}, min={nn_arr.min()}, max={nn_arr.max()}")

