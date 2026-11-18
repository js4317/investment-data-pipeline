from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzer.data.krx import display_name as _name, build_full_name_map
from analyzer.navigation import consume_nav, NAV_TICKER

@st.cache_resource(show_spinner="전체 종목 목록 로딩 중... (최초 1회, 약 30~60초)")
def _get_name_ticker_map() -> dict[str, str]:
    """앱 생애 1회 로딩. disk cache 24h로 재시작 시에도 빠름."""
    return build_full_name_map(date.today())
from analyzer.portfolio import loader as portfolio_loader
from analyzer.portfolio.rules import compute_advisory_raw
from constants import Grade, Confidence, GRADE_EMOJI, CALIBRATION_CANDIDATES


st.title("🔎 Ticker 분석")

# snapshot + watchlist에서 이름 보조 맵 구성 (검색 범위 확장)
def _build_extra_name_map() -> dict[str, str]:
    from backtests.snapshot import load_full, list_snapshot_dates
    from analyzer.watchlist.store import load_all as wl_load_all
    extra: dict[str, str] = {}
    dates = list_snapshot_dates()
    if dates:
        snap = load_full(dates[-1])
        if snap:
            for e in snap.get("entries", []):
                t = e["ticker"]
                extra[t] = _name(t)
    for item in wl_load_all():
        extra[item.ticker] = _name(item.ticker)
    return extra

_nav         = consume_nav(NAV_TICKER)
prefill      = _nav.get("ticker", "")
auto_analyze = bool(prefill)  # Dashboard/Watchlist에서 넘어온 경우 자동 실행

with st.form("ticker_form", clear_on_submit=False):
    fcol1, fcol2 = st.columns([5, 1])
    with fcol1:
        ticker_input = st.text_input(
            "",
            value=prefill,
            placeholder="종목코드 또는 종목명 (예: 005930, 삼성전자)",
            label_visibility="collapsed",
            max_chars=20,
        )
    with fcol2:
        analyze_btn = st.form_submit_button(
            "분석 실행",
            type="primary",
            disabled=st.session_state.get("ticker_running", False),
            use_container_width=True,
        )

if analyze_btn or auto_analyze:
    raw = ticker_input.strip()
    if not raw:
        st.warning("종목코드 또는 종목명을 입력하세요")
        st.stop()

    # 종목코드 (6자리 숫자) → 바로 사용
    if raw.isdigit() and len(raw) == 6:
        ticker = raw
    else:
        # 전체 종목 map 기준 exact match (이전 분석 여부 무관)
        name_map   = _get_name_ticker_map()  # @st.cache_resource — 최초 1회만 느림
        candidates = [name_map[raw]] if raw in name_map else []

        if len(candidates) == 0:
            st.error(f"'{raw}' 찾을 수 없음 — 정확한 종목명 또는 종목코드(6자리)를 입력해 주세요")
            st.stop()
        elif len(candidates) == 1:
            ticker = candidates[0]
        else:
            # 복수 일치: 선택 UI
            options = [f"{_name(t)} ({t})" for t in candidates]
            chosen  = st.selectbox(f"'{raw}' 검색 결과 — 종목 선택", options)
            ticker  = candidates[options.index(chosen)]
            if not st.button("선택 확인", type="secondary"):
                st.stop()
    st.session_state["ticker_running"] = True
    st.session_state.pop("ticker_result", None)  # 이전 결과 초기화
    try:
        from run import analyze_ticker
        from analyzer.rules.market_regime import get_market_state

        progress = st.progress(0)
        status   = st.empty()
        today    = date.today()

        status.info(f"🔍 시장 상태 조회 중...")
        progress.progress(20)
        market_state = get_market_state(today)

        status.info(f"📡 재무·수급·실적 데이터 수집 중...")
        progress.progress(45)
        result = analyze_ticker(ticker, today, market_state)

        progress.progress(100)
        status.success(f"✅ {_name(ticker)} ({ticker}) 분석 완료")

        st.session_state["ticker_result"]        = result
        st.session_state["ticker_result_ticker"] = ticker

        # 오늘 스냅샷에 merge 저장 (Portfolio 등 다른 페이지에서 읽을 수 있도록)
        from backtests import snapshot as bt_snapshot
        bt_snapshot.save([result], today)

        # watchlist에 있으면 분석 결과 메타데이터 갱신 (상태 전환은 하지 않음)
        from analyzer.watchlist.store import load as wl_load, save as wl_save
        from analyzer.watchlist.models import WatchlistItem
        existing = wl_load(ticker)
        if existing:
            wl_save(WatchlistItem(
                ticker          = existing.ticker,
                state           = existing.state,
                added_date      = existing.added_date,
                last_analyzed   = today,
                last_grade      = result.scoring.grade,
                last_score      = result.scoring.normalized_score,
                last_confidence = result.scoring.confidence,
                tag             = existing.tag,
                name            = existing.name or _name(ticker),
            ))
    except Exception as e:
        st.error(f"❌ 분석 실패: {e}")
        st.stop()
    finally:
        st.session_state["ticker_running"] = False

# 버튼 클릭 때만 fetch, rerun은 session 렌더
result = st.session_state.get("ticker_result")
ticker = st.session_state.get("ticker_result_ticker", "")
if not result:
    st.stop()

today = date.today()
s = result.scoring
d = result.decision
m = result.market

# --- 헤더 (compact) ---
st.divider()
grade_emoji = GRADE_EMOJI.get(s.grade, "")
exec_str    = "✅ 실행가능" if d.executable else "🚫 차단"
score_str   = (f"{s.normalized_score}점 (raw {s.total_score}/{s.available_max})"
               if s.available_max < 100 else f"{s.normalized_score}점")

# 종목명 + 판정을 한 줄로
st.subheader(f"{grade_emoji} {_name(ticker)} ({ticker})  ·  {score_str}  ·  {exec_str}")
st.caption(f"{s.confidence.value.upper()}  |  {d.reason}  |  시장 {m.regime.value.upper()}  과열 {m.heat.heat_score}점")

if s.confidence == Confidence.LOW:
    st.warning("⚠ confidence LOW — 일부 데이터 없음. partial analysis.")

_ticker_name_lower = _name(ticker).lower()
if ticker in CALIBRATION_CANDIDATES or "지주" in _ticker_name_lower or "홀딩스" in _ticker_name_lower:
    st.info(
        "🔬 **Calibration Candidate** — 지주사 구조 특성상 PER이 낮게 산출되어 고점수 가능성 있음. "
        "진짜 저평가인지 구조적 착시인지는 성과 추적으로 판단. 추적 시작: 2026-06-09"
    )

# --- 추천 근거 요약 ---
v = result.validated

def _top_drivers() -> list[str]:
    drivers = []
    vd = result.validated
    if s.valuation_score >= 22:
        parts = []
        if vd and vd.fundamentals:
            f = vd.fundamentals
            if f.per:  parts.append(f"PER {f.per:.1f}배")
            if f.pbr:  parts.append(f"PBR {f.pbr:.2f}배")
            if f.roe:  parts.append(f"ROE {f.roe:.1f}%")
        drivers.append("저평가  " + "  ".join(parts) if parts else "저평가 (밸류 우수)")
    if s.earnings_score >= 20:
        drivers.append("안정 실적  최근 4분기 충족")
    if s.risk_score >= 22:
        risk_parts = []
        if vd and vd.risk:
            rk = vd.risk
            if rk.volatility_pct is not None:
                risk_parts.append(f"변동성 {rk.volatility_pct:.0f}% 양호" if rk.volatility_pct < 50 else f"변동성 {rk.volatility_pct:.0f}%")
            else:
                risk_parts.append("변동성 데이터 없음")
            if rk.dilution_pct is not None:
                risk_parts.append("희석 없음" if abs(rk.dilution_pct) < 3 else f"희석 {rk.dilution_pct:+.1f}%")
            else:
                risk_parts.append("희석 데이터 없음")
            if rk.debt_ratio_pct is not None:
                risk_parts.append("부채 양호" if rk.debt_ratio_pct < 150 else f"부채 {rk.debt_ratio_pct:.0f}%")
            else:
                risk_parts.append("부채 데이터 없음")
        label = "리스크 우수" if s.risk_score == 25 else "리스크 양호"
        drivers.append(label + "  " + "  ".join(risk_parts) if risk_parts else label)
    if s.supply_score >= 18:
        drivers.append("강한 수급  외인·기관 동시 순매수")
    elif s.supply_score >= 12:
        parts = []
        if vd and vd.foreign_ownership:
            parts.append(f"외인지분 {vd.foreign_ownership.ownership_pct:.1f}%")
        drivers.append("외국인 매수  " + "  ".join(parts) if parts else "외국인 매수")
    return drivers

def _weak_points() -> list[str]:
    weak = []
    vd = result.validated
    if vd and vd.risk:
        rk = vd.risk
        if rk.volatility_pct and rk.volatility_pct >= 50:
            weak.append(f"높은 변동성  {rk.volatility_pct:.0f}%")
        if rk.dilution_pct and rk.dilution_pct >= 3:
            weak.append(f"주식 희석  +{rk.dilution_pct:.1f}%")
        if rk.quarters_with_loss and rk.quarters_with_loss >= 1:
            weak.append(f"적자 분기  {rk.quarters_with_loss}/4")
    if s.supply_score < 10:
        weak.append("수급 약함")
    if s.earnings_score < 15:
        weak.append("실적 불안정")
    for note in s.notes:
        if "Quick Reject" not in note:
            weak.append(note)
    return weak[:3]

drivers = _top_drivers()
weak    = _weak_points()

if drivers or weak:
    st.divider()
    dr_col, wk_col = st.columns(2)
    with dr_col:
        st.markdown("**⭐ 강점**")
        for d in drivers:
            st.write(f"• {d}")
    with wk_col:
        if weak:
            st.markdown("**⚠️ 약점**")
            for w in weak:
                st.write(f"• {w}")

# --- Claude 분석 (BUY / STRONG_BUY 전용) ---
if s.grade in (Grade.BUY, Grade.STRONG_BUY):
    st.divider()
    st.markdown("**🤖 Claude 분석**")

    _claude_key = f"claude_analysis_{ticker}"
    if _claude_key not in st.session_state:
        if st.button("Claude 분석 실행", key="claude_btn"):
            with st.spinner("분석 중..."):
                from analyzer.claude_advisor import analyze_ticker as _claude_analyze
                from analyzer.data.momentum import fetch_momentum as _fetch_mom
                from analyzer.data.momentum import mom_strength_display as _mom_str_fn

                _mom_tmp = _fetch_mom(ticker, date.today(), value_score=s.normalized_score)
                _mom_str = _mom_str_fn(_mom_tmp.signal.value, _mom_tmp.strength)
                _input = {
                    "ticker":          ticker,
                    "name":            _name(ticker),
                    "score":           s.normalized_score,
                    "grade":           s.grade.value,
                    "valuation_score": s.valuation_score,
                    "earnings_score":  s.earnings_score,
                    "risk_score":      s.risk_score,
                    "supply_score":    s.supply_score,
                    "momentum":        _mom_str,
                    "rel_1m":          _mom_tmp.rel_1m,
                    "rel_3m":          _mom_tmp.rel_3m,
                    "rel_6m":          _mom_tmp.rel_6m,
                    "confidence":      s.confidence.value,
                    "notes":           s.notes,
                }
                if v and v.fundamentals:
                    f_ = v.fundamentals
                    _input.update({"per": f_.per, "pbr": f_.pbr, "roe": f_.roe, "div_yield": f_.div_yield})
                if v and v.risk:
                    rk_ = v.risk
                    _input.update({"volatility": rk_.volatility_pct, "debt_ratio": rk_.debt_ratio_pct})
                if v and v.foreign_ownership:
                    _input["foreign_ownership"] = v.foreign_ownership.ownership_pct

                st.session_state[_claude_key] = _claude_analyze(_input)
                st.rerun()
    else:
        _res = st.session_state[_claude_key]
        if _res.get("from_cache"):
            st.caption(f"📦 캐시 사용  |  생성: {_res.get('cached_at', '—')}  |  엔진: {_res.get('engine_version', '—')}")
        else:
            st.caption(f"✨ 새로 생성  |  {_res.get('cached_at', '—')}  |  엔진: {_res.get('engine_version', '—')}")
        st.markdown(_res["analysis"])
        if st.button("🔄 재생성", key="claude_regen_btn"):
            del st.session_state[_claude_key]
            _cf = __import__("pathlib").Path(__file__).parent.parent / "data" / "claude_analysis" / f"{ticker}.json"
            if _cf.exists():
                _cf.unlink()
            st.rerun()

# --- Risk Radar (비재무 리스크) ---
st.divider()
st.markdown("**🔭 Risk Radar** — 비재무 리스크")

_rr_key   = f"risk_radar_{ticker}"
_RR_COLOR = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}

_RR_SRC_ICON = {"news": "📰", "공시": "📋", "unknown": "🔗"}

if _rr_key not in st.session_state:
    if st.button("Risk Radar 실행", key="rr_btn"):
        with st.spinner("웹 검색으로 최근 이슈 탐지 중..."):
            from analyzer.risk_radar import scan as _rr_scan
            from analyzer.data.krx import get_sector as _get_sector
            _sector = _get_sector(ticker, today)
            st.session_state[_rr_key] = _rr_scan(ticker=ticker, name=_name(ticker), sector=_sector)
            st.rerun()
else:
    _rr = st.session_state[_rr_key]
    if _rr.get("from_cache"):
        st.caption(f"📦 캐시  |  생성: {_rr.get('cached_at', '—')}")
    else:
        st.caption(f"✨ 새로 생성  |  {_rr.get('cached_at', '—')}")

    if _rr.get("error"):
        st.error(f"❌ {_rr['error']}")
    else:
        _rr_overall    = _rr.get("overall", "MEDIUM")
        _rr_confidence = _rr.get("confidence", "MEDIUM")
        _rr_conf_label = {"HIGH": "출처 다수", "MEDIUM": "출처 1~2개", "LOW": "출처 없음·일반론"}.get(_rr_confidence, "")
        st.markdown(
            f"**종합 리스크: {_RR_COLOR.get(_rr_overall, '⚪')} {_rr_overall}**"
            f"　　신뢰도: {_RR_COLOR.get(_rr_confidence, '⚪')} {_rr_confidence} ({_rr_conf_label})"
        )
        _rr_c1, _rr_c2, _rr_c3, _rr_c4 = st.columns(4)
        _rr_c1.metric("지배구조",  f"{_RR_COLOR.get(_rr.get('governance',  'MEDIUM'), '⚪')} {_rr.get('governance',  '—')}")
        _rr_c2.metric("경영진",    f"{_RR_COLOR.get(_rr.get('management',  'MEDIUM'), '⚪')} {_rr.get('management',  '—')}")
        _rr_c3.metric("규제·소송", f"{_RR_COLOR.get(_rr.get('regulatory',  'MEDIUM'), '⚪')} {_rr.get('regulatory',  '—')}")
        _rr_c4.metric("업종",      f"{_RR_COLOR.get(_rr.get('industry',    'MEDIUM'), '⚪')} {_rr.get('industry',    '—')}")
        st.write(_rr.get("summary", ""))
        _rr_sources = _rr.get("sources", [])
        if _rr_sources:
            with st.expander("📰 출처", expanded=False):
                for _src in _rr_sources:
                    _icon  = _RR_SRC_ICON.get(_src.get("type", "unknown"), "🔗")
                    _title = _src.get("title", "—")
                    st.caption(f"{_icon} {_title}")

        # Watchlist 자동 태그 제안 (HIGH → GOVERNANCE_RISK, LOW → CLEAN)
        from analyzer.watchlist.store import load as _rr_wl_load, save as _rr_wl_save
        from analyzer.watchlist.models import WatchlistItem as _RRWLItem, WATCHLIST_TAG_EMOJI as _RR_TE, WatchlistTag as _RRTag
        _rr_existing = _rr_wl_load(ticker)
        if _rr_existing:
            _rr_suggest = {
                "HIGH": _RRTag.GOVERNANCE_RISK,
                "LOW":  _RRTag.CLEAN,
            }.get(_rr_overall)
            if _rr_suggest and _rr_existing.tag != _rr_suggest:
                _rr_emoji = _RR_TE.get(_rr_suggest, "")
                if st.button(
                    f"Watchlist 태그 → {_rr_emoji} {_rr_suggest.value.upper()}",
                    key="rr_tag_btn",
                ):
                    _rr_wl_save(_RRWLItem(
                        ticker          = _rr_existing.ticker,
                        state           = _rr_existing.state,
                        added_date      = _rr_existing.added_date,
                        last_analyzed   = date.today(),
                        last_grade      = s.grade,
                        last_score      = s.normalized_score,
                        last_confidence = s.confidence,
                        tag             = _rr_suggest,
                        name            = _rr_existing.name or _name(ticker),
                    ))
                    st.success(f"태그 변경: {_rr_emoji} {_rr_suggest.value.upper()}")
                    st.rerun()

    if st.button("🔄 재생성", key="rr_regen_btn"):
        del st.session_state[_rr_key]
        from analyzer.risk_radar import clear_cache as _rr_clear
        _rr_clear(ticker)
        st.rerun()

# --- 점수 구성 (섹션별 expander) ---
st.divider()

from collections import defaultdict
by_cat = defaultdict(list)
for c in s.components:
    by_cat[c.category or "기타"].append(c)

# 카테고리별 실제 max 계산 (컴포넌트 합산)
def _cat_max(cat: str) -> int:
    comps = by_cat.get(cat, [])
    return sum(c.max_score for c in comps) if comps else 25

import config as _cfg
_V = _cfg.VALUATION; _E2 = _cfg.EARNINGS; _R2 = _cfg.RISK; _S2 = _cfg.SUPPLY

_per_min = _V["per_upper_bound_score_table"][0]["max_per"]
_per_max = _V["per_upper_bound_score_table"][-2]["max_per"]

_CAT_HELP = {
    "valuation": (
        f"PER ≤ {_per_min}: 25점  …  PER > {_per_max}: 0점\n"
        f"PBR > {_V['pbr_danger_threshold']}: Quick Reject 검토\n"
        "자세한 구간 → 📚 Scoring Guide"
    ),
    "earnings": (
        f"4분기 전부 충족: base {_E2['base_score'][4]}점\n"
        f"ROE ≥ {_E2['roe_tier3_pct']:.0f}%: +{_E2['roe_tier3_bonus']}  "
        f"≥ {_E2['roe_tier2_pct']:.0f}%: +{_E2['roe_tier2_bonus']}  "
        f"≥ {_E2['roe_tier1_pct']:.0f}%: +{_E2['roe_tier1_bonus']}\n"
        f"Revision 상향: +{_E2['revision_up_adj']}  "
        f"구조적 개선: +{_E2['quality_structural_adj']}\n"
        "자세한 기준 → 📚 Scoring Guide"
    ),
    "risk": (
        f"기본 25점 — 패널티 합산\n"
        f"부채 > {_R2['debt_ratio_danger_pct']:.0f}%: {_R2['debt_penalty']}  "
        f"적자 ≥ {_R2['loss_quarters_danger']}회: {_R2['loss_danger_penalty']}\n"
        f"희석 > {_R2['dilution_danger_pct']:.0f}%: {_R2['dilution_penalty']}  "
        f"변동성 > {_R2['volatility_danger_pct']:.0f}%: {_R2['volatility_penalty']}\n"
        "자세한 기준 → 📚 Scoring Guide"
    ),
    "supply": (
        f"쌍끌이 (외인+기관): {_S2['dual_buying_score']}점\n"
        f"외인 단독: {_S2['foreign_single_score']}점  "
        f"기관 단독: {_S2['institution_single_score']}점\n"
        f"동시 순매도: {_S2['dual_selling_penalty']}점\n"
        "자세한 기준 → 📚 Scoring Guide"
    ),
}

_CAT_INFO = {
    "valuation": ("밸류에이션", s.valuation_score),
    "earnings":  ("실적",       s.earnings_score),
    "risk":      ("리스크",     s.risk_score),
    "supply":    ("수급",       s.supply_score),
}

_EQ_BADGE = {
    "VERY_POOR": "⚠ Low Earnings Quality",
    "POOR":      "⚠ Earnings Quality: Poor",
    "WARNING":   "⚠ Earnings Quality: Warning",
}

score_cols = st.columns(4)
for col, (cat, (label, cat_score)) in zip(score_cols, _CAT_INFO.items()):
    cat_max = _cat_max(cat)
    help_txt = _CAT_HELP.get(cat)
    if cat_max < 25:
        col.metric(label, f"{cat_score}/{cat_max} (최대)", help=help_txt)
    else:
        col.metric(label, f"{cat_score}/25", help=help_txt)
    if cat == "valuation":
        badge = _EQ_BADGE.get(s.eq_band)
        if badge:
            col.caption(badge)

for cat, (label, cat_score) in _CAT_INFO.items():
    comps = by_cat.get(cat, [])
    if not comps:
        continue
    cat_max = _cat_max(cat)
    header  = f"**{label}  {cat_score}/{cat_max}점**" if cat_max < 25 else f"**{label}  {cat_score}점**"
    with st.expander(f"{header} — 상세 보기", expanded=False):
        for c in comps:
            pct = c.score / c.max_score if c.max_score else 0
            bar = "█" * int(pct * 10) + "░" * (10 - int(pct * 10))
            if "\n" in c.detail:
                st.write(f"`{bar}`  **{c.score}/{c.max_score}점**")
                for line in c.detail.split("\n"):
                    st.caption(line)
            else:
                st.write(f"`{bar}`  **{c.score}/{c.max_score}점**  {c.detail}")

        if cat == "valuation" and (s.eq_penalty != 0 or s.val_score_abs != 0):
            val_raw_per  = s.valuation_score - s.eq_penalty
            sector_adj   = (s.valuation_score - s.val_score_abs) if s.val_score_abs != 0 else None
            rows = [("PER (raw)", f"{val_raw_per:+d}pt")]
            if s.eq_penalty != 0:
                rows.append(("EQ Adj", f"{s.eq_penalty:+d}pt"))
            if sector_adj is not None:
                rows.append(("Sector Adj", f"{sector_adj:+d}pt"))
            rows.append(("Final", f"{s.valuation_score}pt"))
            st.caption("**Score 분해**")
            for row_label, row_val in rows:
                cols = st.columns([2, 1])
                cols[0].caption(row_label)
                cols[1].caption(row_val)

if s.notes:
    for note in s.notes:
        st.warning(f"⚠ {note}")

# --- 원본 지표 ---
v = result.validated
if v:
    with st.expander("📊 원본 지표", expanded=False):
        col_f, col_s, col_r = st.columns(3)

        with col_f:
            st.markdown("**밸류에이션**")
            f = v.fundamentals
            if f:
                st.write(f"PER:  `{f.per:.1f}배`" if f.per else "PER:  —")
                st.write(f"PBR:  `{f.pbr:.2f}배`" if f.pbr else "PBR:  —")
                st.write(f"ROE:  `{f.roe:.1f}%`" if f.roe else "ROE:  —")
                st.write(f"배당: `{f.div_yield:.2f}%`" if f.div_yield else "배당:  —")
            mc = v.market_cap
            if mc:
                cap     = mc.market_cap
                cap_jo  = cap // 1_000_000_000_000
                cap_eok = (cap % 1_000_000_000_000) // 100_000_000
                if cap_jo > 0:
                    st.write(f"시총: `{cap_jo}조 {cap_eok:,}억`" if cap_eok else f"시총: `{cap_jo}조`")
                else:
                    st.write(f"시총: `{cap_eok:,}억`")
            if v.as_of_date:
                st.caption(f"기준일: {v.as_of_date}")

        with col_s:
            st.markdown("**수급**")
            fo = v.foreign_ownership
            sf = v.supply_flow
            if fo:
                st.write(f"외인지분율: `{fo.ownership_pct:.2f}%`")
                if fo.change_lookback_pct is not None:
                    st.write(f"{fo.lookback_days}일 변화: `{fo.change_lookback_pct:+.2f}%p`")
            if sf:
                def _fmt_val(v):
                    abs_v = abs(v)
                    sign  = "+" if v >= 0 else "-"
                    if abs_v >= 1_000_000_000_000:
                        return f"{sign}{abs_v/1_000_000_000_000:.1f}조"
                    if abs_v >= 100_000_000:
                        return f"{sign}{abs_v/100_000_000:.0f}억"
                    return f"{sign}{abs_v:,.0f}원"
                if sf.foreign_net_buy is not None:
                    st.write(f"외인 {sf.lookback_days}일 순매수: `{_fmt_val(sf.foreign_net_buy)}`")
                if sf.institution_net_buy is not None:
                    st.write(f"기관 {sf.lookback_days}일 순매수: `{_fmt_val(sf.institution_net_buy)}`")

        with col_r:
            st.markdown("**리스크**")
            rk = v.risk
            if rk:
                st.write(f"연환산 변동성: `{rk.volatility_pct:.1f}%`" if rk.volatility_pct is not None else "변동성: —")
                st.write(f"희석률(YoY): `{rk.dilution_pct:+.1f}%`" if rk.dilution_pct is not None else "희석률: —")
                st.write(f"부채비율: `{rk.debt_ratio_pct:.0f}%`" if rk.debt_ratio_pct is not None else "부채비율: —")
                st.write(f"적자분기: `{rk.quarters_with_loss}/4`")

        # ── 52주 가격 위치 ───────────────────────────────────────────────────
        from analyzer.data.krx import fetch_price_context as _fetch_pc
        _pc = _fetch_pc(ticker, v.as_of_date or date.today())
        if _pc and _pc.get("w52_high") and _pc.get("w52_low"):
            st.divider()
            _pos  = _pc["w52_position"]
            _high = _pc["w52_high"]
            _low  = _pc["w52_low"]

            st.markdown("**52주 가격 위치**")
            if _pos is not None:
                st.progress(int(_pos))
                st.caption(
                    f"저점 `{_low:,.0f}` ── 현재 **{_pos:.1f}%** ── 고점 `{_high:,.0f}`"
                )
            else:
                st.caption(f"저점 `{_low:,.0f}` ── 고점 `{_high:,.0f}`")

# --- 포트폴리오 어드바이저리 ---
portfolio = portfolio_loader.load()
if portfolio:
    adv = compute_advisory_raw(ticker, s.grade, s.confidence, m.regime, portfolio)
    if adv:
        st.divider()
        st.subheader("포트폴리오 어드바이저리")
        _LABEL = {
            "over":  "⚠️ 비중 초과",
            "ok":    "✅ 비중 적정",
            "watch": "👁 관찰 유지",
            "new":   "💡 진입 가능",
            "exit":  "🚫 비중 정리",
        }
        adv_col1, adv_col2, adv_col3 = st.columns(3)
        adv_col1.metric("상태",     _LABEL.get(adv.status, adv.status))
        adv_col2.metric("현재 비중", f"{adv.current_pct:.1f}%" if adv.current_pct else "미보유")
        adv_col3.metric("권장 한도", f"{adv.recommended_max_pct:.1f}%")

# --- 모멘텀 오버레이 ---
st.divider()
from analyzer.data.momentum import fetch_momentum, mom_strength_display, MomentumSignal

with st.spinner("모멘텀 데이터 로딩 중..."):
    mom = fetch_momentum(ticker, today, value_score=s.normalized_score)

_total_periods = sum(1 for r in (mom.rel_1m, mom.rel_3m, mom.rel_6m) if r is not None)
_signal_detail = (
    f"({mom.positive_count}/{_total_periods})"
    if mom.signal in (MomentumSignal.STRONG, MomentumSignal.POSITIVE)
    else f"({mom.negative_count}/{_total_periods})"
    if mom.signal == MomentumSignal.NEGATIVE
    else ""
)
_mom_display = mom_strength_display(mom.signal.value, mom.strength)

mom_head, mom_goto = st.columns([5, 1])
mom_head.markdown(f"**📈 모멘텀**  {_mom_display} {_signal_detail}")

mc1, mc2, mc3 = st.columns(3)
mc1.metric("1개월",
           f"{mom.ret_1m:+.1f}%" if mom.ret_1m is not None else "—",
           delta=f"{mom.rel_1m:+.1f}%p vs KOSPI" if mom.rel_1m is not None else None)
mc2.metric("3개월",
           f"{mom.ret_3m:+.1f}%" if mom.ret_3m is not None else "—",
           delta=f"{mom.rel_3m:+.1f}%p vs KOSPI" if mom.rel_3m is not None else None)
mc3.metric("6개월",
           f"{mom.ret_6m:+.1f}%" if mom.ret_6m is not None else "—",
           delta=f"{mom.rel_6m:+.1f}%p vs KOSPI" if mom.rel_6m is not None else None)

if mom.is_value_trap:
    st.warning(
        "⚠️ **시장 외면 중** — 밸류에이션은 매력적이나 시장이 지속적으로 반응하지 않음. "
        "구조적 원인(업황 악화, 경쟁 심화, 이익 질 저하 등) 별도 확인 권장."
    )

# --- 최근 공시 (Market Context Layer 1) ---
st.divider()
st.markdown("**📄 최근 공시**  — Market Context")

from analyzer.market_context.filings import get_filings as _get_filings, get_seen_tracker as _get_seen_tracker

from analyzer.data.krx import rel_time as _rel_time

_highlight_id = _nav.get("highlight")
_mc_filings   = _get_filings(ticker, days=30)

if not _mc_filings:
    from analyzer.data.dart import get_dart_api_key as _dart_key_fn
    if not _dart_key_fn():
        st.caption("📄 DART_API_KEY 미설정 — .env에 DART_API_KEY를 추가하면 공시 조회가 활성화됩니다.")
    else:
        st.caption("최근 30일 공시 없음")
else:
    _seen   = _get_seen_tracker()
    _unseen = _seen.unseen(ticker, _mc_filings)

    if _unseen:
        st.caption(f"🔵 새 공시 {len(_unseen)}건  ·  전체 {len(_mc_filings)}건 (최근 30일)")
    else:
        st.caption(f"전체 {len(_mc_filings)}건 (최근 30일)  ·  모두 확인함")

    for _f in _mc_filings:
        _is_new       = _f in _unseen
        _is_highlight = _f.source_id == _highlight_id
        _new_dot      = "🔵 " if _is_new else ""
        _dart_link    = f"  [DART 원문]({_f.url})" if _f.url else ""
        _time_str     = _rel_time(_f.date)

        if _is_highlight:
            st.markdown('<div id="filing-highlight-anchor"></div>', unsafe_allow_html=True)
            with st.container(border=True):
                st.write(f"👉 {_f.importance_emoji} {_f.type_icon} **{_f.title}**{_dart_link}")
                st.caption(_time_str)
        else:
            st.write(
                f"{_new_dot}{_f.importance_emoji} {_f.type_icon} **{_f.title}**"
                f"  {_dart_link}  {_time_str}"
            )

    _seen.mark_seen(ticker, _mc_filings)

    if _highlight_id:
        import streamlit.components.v1 as _ticker_cv1
        _ticker_cv1.html(
            """<script>
            (function(){
                setTimeout(function(){
                    var el = window.parent.document.getElementById('filing-highlight-anchor');
                    if(el){ el.scrollIntoView({behavior:'smooth', block:'center'}); }
                }, 600);
            })();
            </script>""",
            height=0,
        )

# --- Watchlist 추가 / 상태 변경 ---
st.divider()
from analyzer.watchlist.store import load as wl_load, save as wl_save
from analyzer.watchlist.models import WatchlistItem, WatchlistState

existing = wl_load(ticker)
if existing:
    _STATE_LABEL = {
        WatchlistState.ACTIVE:   "🟢 ACTIVE",
        WatchlistState.REVIEW:   "🟡 REVIEW",
        WatchlistState.DORMANT:  "💤 DORMANT",
        WatchlistState.REJECTED: "⛔ REJECTED",
    }
    wl_col1, wl_col2 = st.columns([3, 1])
    wl_col1.info(
        f"📋 Watchlist 등록됨 — {_STATE_LABEL.get(existing.state, existing.state.value.upper())} "
        f"(등록일 {existing.added_date})"
    )
    _new_state = wl_col2.selectbox(
        "상태 변경",
        options=[ws for ws in WatchlistState],
        index=[ws for ws in WatchlistState].index(existing.state),
        format_func=lambda ws: _STATE_LABEL.get(ws, ws.value),
        key=f"wl_state_{ticker}",
        label_visibility="collapsed",
    )
    if _new_state != existing.state:
        wl_save(WatchlistItem(
            ticker          = existing.ticker,
            state           = _new_state,
            added_date      = existing.added_date,
            last_analyzed   = date.today(),
            last_grade      = s.grade,
            last_score      = s.normalized_score,
            last_confidence = s.confidence,
            tag             = existing.tag,
            name            = existing.name or _name(ticker),
        ))
        st.success(f"✅ 상태 변경: {_STATE_LABEL.get(_new_state, _new_state.value)}")
        st.rerun()
else:
    if st.button("📋 Watchlist에 추가", type="secondary"):
        wl_save(WatchlistItem(
            ticker          = ticker,
            state           = WatchlistState.ACTIVE,
            added_date      = date.today(),
            last_analyzed   = date.today(),
            last_grade      = s.grade,
            last_score      = s.normalized_score,
            last_confidence = s.confidence,
            name            = _name(ticker),
        ))
        st.success(f"✅ {_name(ticker)} ({ticker}) Watchlist에 추가됨")
        st.rerun()

st.caption(f"기준일: {date.today()}  |  분석 시점 결과이며 미래 수익률을 예측하지 않습니다.")
