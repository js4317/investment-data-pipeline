"""
10_calibration.py — Calibration Report

엔진 검증 현황 + 20D Alpha 계산 + 결과 집계 표시.

v8.5.19 Engine Freeze (2026-08-21 ~) 기간 전용 모니터링 페이지.
  - v8.5.19 era 진행률 (n / 100 목표)
  - 20D Alpha 계산 트리거 (old snapshots → performance/)
  - Grade별 / Component별 / Momentum Signal별 Alpha 결과
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtests import calibration as cal
from backtests import performance as perf
from backtests.snapshot import list_snapshot_dates

# ── 상수 ────────────────────────────────────────────────────────────────────
FREEZE_DATE    = date(2026, 8, 21)
TARGET_N       = 100
CALIB_SNAP_DIR = Path(__file__).parent.parent / "backtests" / "calibration_snapshots"
PERF_DIR       = Path(__file__).parent.parent / "backtests" / "performance"

# ── 헬퍼 ────────────────────────────────────────────────────────────────────

def _v8519_snapshots() -> list[date]:
    """v8.5.19 era calibration_snapshots 날짜 목록."""
    if not CALIB_SNAP_DIR.exists():
        return []
    return sorted(
        date.fromisoformat(p.stem)
        for p in CALIB_SNAP_DIR.glob("*.json")
        if date.fromisoformat(p.stem) >= FREEZE_DATE
    )


def _perf_count(window: int = 20) -> int:
    if not PERF_DIR.exists():
        return 0
    return len(list(PERF_DIR.glob(f"*_w{window}.json")))


def _alpha_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:+.2f}%"


def _status_color(status: str) -> str:
    if "Validated" in status:
        return "🟢"
    if "Review" in status:
        return "🔴"
    if "Small" in status:
        return "🟡"
    return "⏳"


# ── 레이아웃 ─────────────────────────────────────────────────────────────────

st.title("📊 Calibration Report")
st.caption("v8.5.19 엔진 검증 현황 | Engine Freeze 기간 모니터링")

# ── ① v8.5.19 진행률 ────────────────────────────────────────────────────────
st.subheader("🔒 v8.5.19 Calibration 진행률")

v8519_dates = _v8519_snapshots()
n_v8519     = len(v8519_dates)
pct         = min(n_v8519 / TARGET_N, 1.0)

col1, col2, col3 = st.columns(3)
col1.metric("v8.5.19 스냅샷", f"{n_v8519} / {TARGET_N}")
col2.metric("20D Alpha 계산 완료", f"{_perf_count(20)}건")

# 해제 예상일 — 1영업일 = 5/7일 근사
remaining  = max(TARGET_N - n_v8519, 0)
est_days   = int(remaining * 7 / 5)
est_unlock = date.today() + timedelta(days=est_days) if remaining > 0 else date.today()
col3.metric("해제 예상", est_unlock.strftime("%Y-%m") if remaining > 0 else "✅ 달성")

st.progress(pct, text=f"Progress: {n_v8519}/{TARGET_N} ({pct*100:.0f}%)")

if n_v8519 < TARGET_N:
    st.info(
        f"🔒 **Engine Freeze** — 점수 공식 고정 중.  \n"
        f"v8.5.19 스냅샷 {n_v8519}개 수집됨. 해제 조건: n ≥ 100 + 세 윈도우(20D/60D/120D) 검토 완료.",
        icon=None,
    )

if v8519_dates:
    with st.expander("v8.5.19 스냅샷 날짜 목록"):
        st.write(", ".join(str(d) for d in v8519_dates))

st.divider()

# ── ② 20D Alpha 계산 ────────────────────────────────────────────────────────
st.subheader("⚡ 20D Alpha 계산")

snap_dates = list_snapshot_dates()
n_perf_done = _perf_count(20)

# 빠른 추정: KRX 호출 없이 캘린더 기준으로 대기 추정
_WINDOW_DAYS = int(20 * 1.3 * 7 / 5)  # 20 영업일 ≈ 37 calendar days
today = date.today()
n_approx_ready = sum(
    1 for d in snap_dates
    if (today - d).days >= _WINDOW_DAYS
    and not (PERF_DIR / f"{d.isoformat()}_w20.json").exists()
)

col_a, col_b, col_c = st.columns(3)
col_a.metric("전체 스냅샷", f"{len(snap_dates)}개")
col_b.metric("계산 대기 (추정)", f"{n_approx_ready}개")
col_c.metric("계산 완료", f"{n_perf_done}개")

st.caption("KRX 데이터를 사용하므로 장 시간(09:00~16:00) 중 실행하면 정확도가 높습니다.")

if st.button("▶ 20D Alpha 계산 실행", type="primary"):
    with st.status("Alpha 계산 중... (KRX 데이터 조회)", expanded=True) as status:
        try:
            calculable = perf.list_calculable(20)
            if not calculable:
                st.write("계산 가능한 스냅샷이 없습니다.")
            else:
                for i, snap_date in enumerate(calculable):
                    st.write(f"[{i+1}/{len(calculable)}] {snap_date} ...")
                    try:
                        data = perf.calc_performance(snap_date, window=20)
                        if data:
                            perf.save(snap_date, data, window=20)
                            st.write(f"  ✓ {data['n_records']}종목, KOSPI {data['kospi_return']*100:+.2f}%")
                        else:
                            st.write("  ⚠ 데이터 부족 (건너뜀)")
                    except Exception as e:
                        st.write(f"  ❌ {e}")
            status.update(label="완료", state="complete")
        except Exception as e:
            status.update(label=f"오류: {e}", state="error")
    st.rerun()

st.divider()

# ── ③ 결과 섹션 ─────────────────────────────────────────────────────────────
n_perf = _perf_count(20)

if n_perf == 0:
    st.subheader("📋 Calibration 결과")
    st.info("20D Alpha 데이터가 없습니다. 위에서 계산을 실행하세요.", icon="ℹ️")
    st.stop()

st.subheader(f"📋 Calibration 결과 — 20D Alpha ({n_perf}개 스냅샷)")

# v8.3.x era 참고 데이터 경고
st.warning(
    "⚠ 현재 결과는 **v8.3.x era (참고 데이터)**입니다.  \n"
    "v8.5.19 Calibration 기준 데이터는 2026-09-17 이후 20D Alpha 계산 가능.",
    icon=None,
)

# ─── 핵심 KPI: Score-Alpha 상관계수 ─────────────────────────────────────────
corr = cal.calc_score_alpha_correlation(window=20)
rho  = corr.get("rho")
n_corr = corr.get("n", 0)

st.markdown("#### 🎯 Score-Alpha 상관계수 ρ")
kpi_col1, kpi_col2 = st.columns(2)

if rho is not None:
    rho_label = "🟢 양의 상관 (엔진 유효)" if rho > 0.05 else (
                "🔴 역상관 (재검토 필요)" if rho < -0.05 else "🟡 상관 미약")
    kpi_col1.metric("ρ (score vs alpha)", f"{rho:+.3f}", help="양수 = 점수 높을수록 알파 큰 경향")
    kpi_col2.metric("표본 수", f"{n_corr}개")
    st.caption(rho_label)
else:
    st.info(f"상관계수 계산 불가 (n={n_corr}, 최소 5개 필요)")

st.divider()

# ─── Grade별 Alpha ───────────────────────────────────────────────────────────
st.markdown("#### 📊 Grade별 평균 Alpha")

from collections import defaultdict
all_recs = [
    rec
    for pd in perf.load_all(20)
    for rec in pd.get("records", [])
    if rec.get("alpha") is not None
]

GRADE_ORDER = ["strong_buy", "buy", "hold", "watch", "neutral"]
grade_buckets: dict[str, list[float]] = defaultdict(list)
for rec in all_recs:
    g = (rec.get("grade") or "").lower()
    if g:
        grade_buckets[g].append(rec["alpha"])

grade_rows = []
for g in GRADE_ORDER:
    alphas = grade_buckets.get(g, [])
    if not alphas:
        continue
    avg = sum(alphas) / len(alphas)
    win = sum(1 for a in alphas if a > 0)
    grade_rows.append({
        "Grade":       g.upper().replace("_", " "),
        "n":           len(alphas),
        "평균 Alpha":  _alpha_pct(avg * 100),
        "양수 비율":   f"{win/len(alphas)*100:.0f}%",
    })

if grade_rows:
    st.dataframe(grade_rows, use_container_width=True, hide_index=True)
    # 단조성 체크
    avgs = []
    for g in GRADE_ORDER:
        a = grade_buckets.get(g, [])
        if a:
            avgs.append((g, sum(a)/len(a)))
    mono = all(avgs[i][1] >= avgs[i+1][1] for i in range(len(avgs)-1))
    st.caption(f"단조성 (SB ≥ BUY ≥ HOLD ≥ WATCH ≥ NEUTRAL): {'✅' if mono else '❌'}")
else:
    st.info("Grade 데이터 없음")

st.divider()

# ─── Component별 기여도 ──────────────────────────────────────────────────────
st.markdown("#### 🔩 Component별 Alpha 기여도 (High vs Low)")

comp_rows = cal.build_component_report(window=20)
if comp_rows and any(r.get("spread") is not None for r in comp_rows):
    display = []
    for r in comp_rows:
        display.append({
            "Component":   r["component"],
            "n (High)":    r["n_high"],
            "n (Low)":     r["n_low"],
            "High Alpha":  _alpha_pct(r.get("high_alpha")),
            "Low Alpha":   _alpha_pct(r.get("low_alpha")),
            "Spread":      _alpha_pct(r.get("spread")),
            "상태":        _status_color(r.get("status", "")) + " " + r.get("status", ""),
        })
    st.dataframe(display, use_container_width=True, hide_index=True)
    st.caption("Spread > 0 = 해당 컴포넌트 점수가 높을수록 Alpha 큰 경향 (컴포넌트 유효)")
else:
    st.info("Component 데이터 수집 중 (⏳)")

st.divider()

# ─── Momentum Signal별 ───────────────────────────────────────────────────────
st.markdown("#### 📈 Momentum Signal별 Alpha")

mom_data = cal.build_factor_report("mom_signal", window=20)
mom_with_data = [r for r in mom_data if r.get("n", 0) > 0]

if mom_with_data:
    display = []
    for r in mom_with_data:
        display.append({
            "Signal":      r["value"],
            "n":           r["n"],
            "평균 Alpha":  _alpha_pct(r.get("avg_alpha")),
            "상태":        _status_color(r.get("status", "")) + " " + r.get("status", ""),
        })
    st.dataframe(display, use_container_width=True, hide_index=True)
    st.caption(
        "Q1 가설: STRONG_BUY + positive MOM의 평균 Alpha > STRONG_BUY + negative MOM  \n"
        "(검증 시 Momentum 팩터 편입 근거 확보)"
    )
else:
    st.info("Momentum Signal 데이터 없음 (스냅샷에 mom_signal 필드 없거나 데이터 수집 중)")

st.divider()

# ─── Score Band별 ────────────────────────────────────────────────────────────
st.markdown("#### 🎯 Score Band별 Alpha")

band_rows = cal.build_score_band_report(window=20)
band_with_data = [r for r in band_rows if r.get("n", 0) > 0]

if band_with_data:
    display = []
    for r in band_with_data:
        display.append({
            "Score Band":  r["band"],
            "n":           r["n"],
            "평균 Alpha":  _alpha_pct(r.get("avg_alpha")),
            "상태":        _status_color(r.get("status", "")) + " " + r.get("status", ""),
        })
    st.dataframe(display, use_container_width=True, hide_index=True)
else:
    st.info("Score Band 데이터 수집 중 (⏳)")

st.divider()

# ─── EQ Band별 ──────────────────────────────────────────────────────────────
st.markdown("#### 🏦 EQ Band별 Alpha (Earnings Quality)")

eq_rows = cal.build_factor_report("eq_band", window=20)
eq_with_data = [r for r in eq_rows if r.get("n", 0) > 0]

if eq_with_data:
    display = []
    for r in eq_with_data:
        display.append({
            "EQ Band":     r["value"],
            "n":           r["n"],
            "평균 Alpha":  _alpha_pct(r.get("avg_alpha")),
            "상태":        _status_color(r.get("status", "")) + " " + r.get("status", ""),
        })
    st.dataframe(display, use_container_width=True, hide_index=True)
else:
    st.info("EQ Band 데이터 수집 중 (⏳)")

# ─── 하단 메모 ────────────────────────────────────────────────────────────────
st.divider()
st.caption(
    "**해석 주의**: n < 30 결과는 통계 불안정. 결론 보류.  \n"
    "팩터 삭제 기준: n ≥ 100 + 20D / 60D / 120D 세 윈도우 모두 지속 음수일 때만 'Calibration Failed' 판정."
)
