from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import date

from analyzer.watchlist.store import load_all, save as wl_save, load_triggers, save_trigger as wl_save_trigger
from analyzer.watchlist.models import WatchlistItem, WatchlistState, WatchlistTag, WATCHLIST_TAG_EMOJI, ReviewTrigger
from analyzer.data.krx import display_name as _name, build_full_name_map
from analyzer.data.momentum import mom_strength_display
from analyzer.navigation import consume_nav, nav_to, NAV_TICKER, NAV_WATCHLIST
from constants import Grade, GRADE_EMOJI


@st.cache_resource(show_spinner="전체 종목 목록 로딩 중... (최초 1회)")
def _get_name_map() -> dict[str, str]:
    return build_full_name_map(date.today())


@st.cache_data(ttl=21600, show_spinner=False)
def _load_filing_badges(tickers: tuple[str, ...]) -> dict[str, str]:
    """
    7일 이내 IMPORTANT+ 공시를 '📈 잠정실적  ⚠ CB발행' 형식으로 반환.
    DART 파일 캐시(6h)와 동일 주기.
    """
    from analyzer.market_context.filings import get_filings as _gf, get_type_label as _lbl
    from analyzer.market_context.models import FilingImportance as _FI
    result: dict[str, str] = {}
    for ticker in tickers:
        try:
            visible = sorted(
                [i for i in _gf(ticker, days=7) if i.importance >= _FI.IMPORTANT],
                reverse=True,
            )[:3]
            result[ticker] = "  ".join(f"{i.type_icon} {_lbl(i.item_type)}" for i in visible)
        except Exception:
            result[ticker] = ""
    return result


@st.cache_data(ttl=3600, show_spinner=False)
def _load_mom_data(tickers: tuple[str, ...], as_of: str) -> dict[str, dict]:
    from analyzer.data.momentum import batch_fetch_momentum
    results = batch_fetch_momentum([(t, None) for t in tickers], date.today())
    return {
        t: {
            "signal":        m.signal.value,
            "strength":      round(m.strength, 1),
            "is_value_trap": m.is_value_trap,
            "rel_1m":        m.rel_1m,
            "rel_3m":        m.rel_3m,
            "rel_6m":        m.rel_6m,
        }
        for t, m in results.items()
    }


_STATE_EMOJI = {
    WatchlistState.ACTIVE:   "🟢",
    WatchlistState.REVIEW:   "🔶",
    WatchlistState.DORMANT:  "⚫",
    WatchlistState.REJECTED: "❌",
}

_STATE_RANK = {
    WatchlistState.REVIEW:   0,
    WatchlistState.ACTIVE:   1,
    WatchlistState.DORMANT:  2,
    WatchlistState.REJECTED: 3,
}


def _mom_col_display(m: dict) -> str:
    signal   = m.get("signal")
    strength = m.get("strength", 0.0)
    is_vt    = m.get("is_value_trap", False)
    badge    = "📈 " if strength >= 50 else ("📉 " if is_vt else "")
    return badge + mom_strength_display(signal, strength)


def _passes_mom_filter(ticker: str, mom_data: dict, filt: str) -> bool:
    if filt == "전체":
        return True
    s = mom_data.get(ticker, {}).get("strength", 0.0)
    if filt == "▲▲ Strong":             return s >= 100
    if filt == "▲ Positive":            return s >= 50
    if filt == "▼ Negative":            return s < 0
    if filt == "▼▼▼ Extreme Negative":  return s <= -100
    return True


def _build_rows(items: list[WatchlistItem], sort_key: str) -> list[dict]:
    if sort_key == "점수 내림차순":
        key_fn = lambda i: -(i.last_score or 0)
    else:
        key_fn = lambda i: (_STATE_RANK[i.state], -(i.last_score or 0))

    rows = []
    for item in sorted(items, key=key_fn):
        grade_str = (
            f"{GRADE_EMOJI.get(item.last_grade, '')} {item.last_grade.value.upper()}"
            if item.last_grade else "—"
        )
        tag_emoji = WATCHLIST_TAG_EMOJI.get(item.tag, "📋")
        rows.append({
            "태그":       tag_emoji,
            "상태":       f"{_STATE_EMOJI[item.state]} {item.state.value.upper()}",
            "종목":       f"{_name(item.ticker)} ({item.ticker})",
            "등급":       grade_str,
            "점수":       item.last_score if item.last_score is not None else "—",
            "신뢰도":     item.last_confidence.value.upper() if item.last_confidence else "—",
            "마지막분석": str(item.last_analyzed) if item.last_analyzed else "—",
            "등록일":     str(item.added_date),
        })
    return rows


# ---------------------------------------------------------------------------
# 레이아웃
# ---------------------------------------------------------------------------

st.title("📋 Watchlist")

items = load_all()

# --- 종목 추가 ---
with st.expander("➕ 종목 추가", expanded=not items):
    name_map = _get_name_map()
    search_query = st.text_input("종목명 검색", placeholder="삼성전자, 기아, KB금융 ...", key="wl_add_query")

    matched: dict[str, str] = {}
    if search_query.strip():
        q = search_query.strip().lower()
        matched = {name: ticker for name, ticker in name_map.items() if q in name.lower()}

    if matched:
        label_list = [f"{name} ({ticker})" for name, ticker in matched.items()]
        sel = st.selectbox("검색 결과", label_list, key="wl_add_sel", label_visibility="collapsed")
        sel_ticker = matched[sel.rsplit(" (", 1)[0]]
        already = any(i.ticker == sel_ticker for i in items)

        add_col1, add_col2, add_col3 = st.columns([2, 2, 1])
        with add_col1:
            init_state = st.selectbox("상태", ["ACTIVE", "REVIEW", "DORMANT"], key="wl_add_state", label_visibility="collapsed")
        with add_col2:
            tag_opts = {
                "📋 일반":        WatchlistTag.GENERAL,
                "🧪 Calibration": WatchlistTag.CALIBRATION,
                "⚠️ FP 후보":     WatchlistTag.FALSE_POSITIVE,
                "🔥 Momentum":    WatchlistTag.MOMENTUM,
                "📉 시장 외면":    WatchlistTag.MARKET_IGNORE,
                "🔍 Research":    WatchlistTag.RESEARCH,
            }
            init_tag_label = st.selectbox("태그", list(tag_opts.keys()), key="wl_add_tag", label_visibility="collapsed")
        with add_col3:
            add_btn = st.button("추가", type="primary", disabled=already, key="wl_add_btn")

        if already:
            st.caption("이미 Watchlist에 있음")
        if add_btn:
            wl_save(WatchlistItem(
                ticker=sel_ticker,
                state=WatchlistState(init_state.lower()),
                added_date=date.today(),
                last_analyzed=None, last_grade=None, last_score=None, last_confidence=None,
                tag=tag_opts[init_tag_label],
                name=sel.rsplit(" (", 1)[0],
            ))
            st.success(f"✅ {sel.rsplit(' (', 1)[0]} 추가 완료")
            st.rerun()
    elif search_query.strip():
        st.caption("검색 결과 없음")

if not items:
    st.stop()

@st.fragment
def _main_section() -> None:
    import streamlit.components.v1 as _cv1
    _cv1.html(
        """<script>
        (function(){
            try {
                var W=window.parent, D=W.document, S=W.sessionStorage, K='__wl_y';
                var el=D.querySelector('.stMainScrollable')||D.querySelector('section.main')||D.querySelector('.stMain')||D.documentElement;
                var y=parseInt(S.getItem(K)||'0',10);
                if(y>0){
                    requestAnimationFrame(function(){
                        requestAnimationFrame(function(){ el.scrollTop=y; });
                    });
                }
                if(!el.__wlBound){
                    el.__wlBound=true;
                    el.addEventListener('scroll',function(){S.setItem(K,el.scrollTop);},{passive:true});
                }
            }catch(e){}
        })();
        </script>""",
        height=0,
    )

    items = load_all()
    if not items:
        return

    # --- 요약 배너 ---
    review_count = sum(1 for i in items if i.state == WatchlistState.REVIEW)
    if review_count:
        _rbanner, _rbtn = st.columns([4, 1])
        _rbanner.warning(f"⚠️ REVIEW {review_count}개 — 재검토 필요")
        if _rbtn.button("전체 ACTIVE로", key="bulk_activate_review"):
            for _ri in items:
                if _ri.state == WatchlistState.REVIEW:
                    _ri.state = WatchlistState.ACTIVE
                    wl_save(_ri)
            st.rerun()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("ACTIVE",   sum(1 for i in items if i.state == WatchlistState.ACTIVE))
    col2.metric("REVIEW",   review_count)
    col3.metric("DORMANT",  sum(1 for i in items if i.state == WatchlistState.DORMANT))
    col4.metric("REJECTED", sum(1 for i in items if i.state == WatchlistState.REJECTED))

    st.divider()

    # --- 정렬 / 필터 ---
    sort_col, tag_filter_col, mom_filter_col = st.columns([4, 2, 2])
    with sort_col:
        sort_key = st.radio(
            "정렬",
            ["기본 (상태·점수)", "점수 내림차순", "모멘텀 ▲", "모멘텀 ▼"],
            horizontal=True,
            key="wl_sort_key",
        )
    with tag_filter_col:
        _tag_counts = {t: sum(1 for i in items if i.tag == t) for t in WatchlistTag}
        def _tc(label: str, tag: WatchlistTag) -> str:
            n = _tag_counts.get(tag, 0)
            return f"{label} ({n})" if n else label
        tag_filter = st.selectbox(
            "태그 필터",
            [
                "전체",
                _tc("⚠️ FP 후보",    WatchlistTag.FALSE_POSITIVE),
                _tc("🧪 Calibration", WatchlistTag.CALIBRATION),
                _tc("🔥 Momentum",    WatchlistTag.MOMENTUM),
                _tc("📉 시장 외면",    WatchlistTag.MARKET_IGNORE),
                _tc("🔍 Research",    WatchlistTag.RESEARCH),
                _tc("📋 일반",        WatchlistTag.GENERAL),
            ],
            key="wl_tag_filter",
            label_visibility="collapsed",
        )
    with mom_filter_col:
        mom_filter = st.selectbox(
            "모멘텀 필터",
            ["전체", "▲▲ Strong", "▲ Positive", "▼ Negative", "▼▼▼ Extreme Negative"],
            key="wl_mom_filter",
            label_visibility="collapsed",
        )

    _TAG_FILTER_MAP = {
        "⚠️ FP 후보":   WatchlistTag.FALSE_POSITIVE,
        "🧪 Calibration": WatchlistTag.CALIBRATION,
        "🔥 Momentum":   WatchlistTag.MOMENTUM,
        "📉 시장 외면":   WatchlistTag.MARKET_IGNORE,
        "🔍 Research":   WatchlistTag.RESEARCH,
        "📋 일반":        WatchlistTag.GENERAL,
    }

    # --- ACTIVE / REVIEW 테이블 ---
    main_items = [i for i in items if i.state in (WatchlistState.ACTIVE, WatchlistState.REVIEW)]

    _wl_nav = consume_nav(NAV_WATCHLIST)
    preset  = _wl_nav.get("filter_preset")
    if preset == ["REVIEW"]:
        main_items = [i for i in main_items if i.state == WatchlistState.REVIEW]
        st.caption("필터: REVIEW 전용")

    _active_tag = next((v for k, v in _TAG_FILTER_MAP.items() if tag_filter.startswith(k)), None)
    if _active_tag:
        main_items = [i for i in main_items if i.tag == _active_tag]
        st.caption(f"태그 필터: {tag_filter}")

    snap_map: dict = {}

    if main_items:
        _visible_tickers = tuple(i.ticker for i in main_items)
        mom_data      = _load_mom_data(_visible_tickers, date.today().isoformat())
        filing_badges = _load_filing_badges(_visible_tickers)

        if mom_filter != "전체":
            main_items = [i for i in main_items if _passes_mom_filter(i.ticker, mom_data, mom_filter)]

        from backtests.snapshot import load_full, list_snapshot_dates
        from constants import Grade, GRADE_EMOJI
        dates = list_snapshot_dates()
        if dates:
            snap = load_full(dates[-1])
            if snap:
                snap_map = {e["ticker"]: e for e in snap.get("entries", [])}

        import pandas as pd

        _STATE_OPTS = ["ACTIVE", "REVIEW", "DORMANT", "REJECTED"]
        _TAG_TO_ENUM = {
            "📋 일반":        WatchlistTag.GENERAL,
            "🧪 Calibration": WatchlistTag.CALIBRATION,
            "⚠️ FP 후보":     WatchlistTag.FALSE_POSITIVE,
            "🔥 Momentum":    WatchlistTag.MOMENTUM,
            "📉 시장 외면":    WatchlistTag.MARKET_IGNORE,
            "🔍 Research":    WatchlistTag.RESEARCH,
        }
        _TAG_OPTS      = list(_TAG_TO_ENUM.keys())
        _TAG_FROM_ENUM = {v: k for k, v in _TAG_TO_ENUM.items()}

        def _sort_key_fn(i):
            if sort_key == "점수 내림차순":
                return -(i.last_score or 0)
            if sort_key == "모멘텀 ▲":
                return -mom_data.get(i.ticker, {}).get("strength", -9999.0)
            if sort_key == "모멘텀 ▼":
                return mom_data.get(i.ticker, {}).get("strength", -9999.0)
            return (_STATE_RANK[i.state], -(i.last_score or 0))

        sorted_main = sorted(main_items, key=_sort_key_fn)

        # 현재 선택 종목 (렌더 전 display용)
        _prev_selected = st.session_state.get("wl_selected")
        if _prev_selected not in {i.ticker for i in sorted_main}:
            _prev_selected = sorted_main[0].ticker if sorted_main else None

        edit_rows = []
        for item in sorted_main:
            grade_str = (f"{GRADE_EMOJI.get(item.last_grade, '')} {item.last_grade.value.upper()}"
                         if item.last_grade else "—")
            m = mom_data.get(item.ticker, {})
            edit_rows.append({
                "_ticker":    item.ticker,
                "🔎":         item.ticker == _prev_selected,
                "태그":       _TAG_FROM_ENUM.get(item.tag, "📋 일반"),
                "상태":       item.state.value.upper(),
                "종목":       f"{_name(item.ticker)} ({item.ticker})",
                "등급":       grade_str,
                "점수":       item.last_score if item.last_score is not None else "—",
                "공시":       filing_badges.get(item.ticker, ""),
                "모멘텀":     _mom_col_display(m) if m else "—",
                "신뢰도":     item.last_confidence.value.upper() if item.last_confidence else "—",
                "마지막분석": str(item.last_analyzed) if item.last_analyzed else "—",
            })

        edit_df = pd.DataFrame(edit_rows)
        edited  = st.data_editor(
            edit_df.drop(columns=["_ticker"]),
            column_config={
                "🔎":  st.column_config.CheckboxColumn("🔎", width="small"),
                "태그": st.column_config.SelectboxColumn("태그",  options=_TAG_OPTS,   width="small"),
                "상태": st.column_config.SelectboxColumn("상태",  options=_STATE_OPTS, width="small"),
                "종목": st.column_config.TextColumn("종목",  disabled=True),
                "등급": st.column_config.TextColumn("등급",  disabled=True, width="small"),
                "점수": st.column_config.TextColumn("점수",  disabled=True, width="small"),
                "공시": st.column_config.TextColumn("공시",  disabled=True, width="small"),
                "모멘텀": st.column_config.TextColumn("모멘텀", disabled=True, width="small"),
                "신뢰도": st.column_config.TextColumn("신뢰도", disabled=True, width="small"),
                "마지막분석": st.column_config.TextColumn("마지막분석", disabled=True, width="small"),
            },
            hide_index=True,
            use_container_width=True,
            key="watchlist_editor",
        )

        # --- 변경 감지 & 즉시 저장 (st.rerun 없음) ---
        for i, edited_row in enumerate(edited.to_dict("records")):
            orig      = edit_rows[i]
            ticker    = orig["_ticker"]
            new_state = WatchlistState(edited_row["상태"].lower())
            new_tag   = _TAG_TO_ENUM.get(edited_row["태그"], WatchlistTag.GENERAL)

            if edited_row["상태"] != orig["상태"] or edited_row["태그"] != orig["태그"]:
                wl_item = next(x for x in main_items if x.ticker == ticker)
                wl_save(WatchlistItem(
                    ticker=wl_item.ticker, state=new_state,
                    added_date=wl_item.added_date, last_analyzed=wl_item.last_analyzed,
                    last_grade=wl_item.last_grade, last_score=wl_item.last_score,
                    last_confidence=wl_item.last_confidence, tag=new_tag,
                    name=wl_item.name,
                ))
                # REJECTED 천이 시 사유 입력 요청
                if new_state == WatchlistState.REJECTED and orig["상태"].lower() != "rejected":
                    st.session_state["pending_reject_reason"] = ticker

            if edited_row["🔎"] and not orig["🔎"]:
                st.session_state["wl_selected"] = ticker

        # --- REJECTED 사유 입력 ---
        _pending_reject = st.session_state.get("pending_reject_reason")
        if _pending_reject:
            with st.container(border=True):
                st.warning(f"❌ **{_name(_pending_reject)}** ({_pending_reject}) 를 REJECTED로 전환했습니다. 사유를 입력하세요.")
                _reason = st.text_input("거절 사유 (선택)", key="reject_reason_input", placeholder="예: 실적 악화, 섹터 제외, 테마 소멸...")
                _rcol1, _rcol2 = st.columns([1, 5])
                if _rcol1.button("확인", key="reject_reason_confirm", type="primary"):
                    if _reason.strip():
                        wl_save_trigger(
                            ReviewTrigger(
                                ticker=_pending_reject,
                                event="manual_reject",
                                detail=_reason.strip(),
                            ),
                            date.today(),
                        )
                    del st.session_state["pending_reject_reason"]
                    st.rerun()
                if _rcol2.button("건너뛰기", key="reject_reason_skip"):
                    del st.session_state["pending_reject_reason"]
                    st.rerun()

        # 선택 종목 확정 (루프 이후 → 동일 렌더에서 Quick Inspect 즉시 반영)
        selected_ticker = st.session_state.get("wl_selected", _prev_selected)
        if selected_ticker not in {i.ticker for i in sorted_main}:
            selected_ticker = sorted_main[0].ticker if sorted_main else None
            st.session_state["wl_selected"] = selected_ticker

        # --- Quick Inspect ---
        sel_item = next((x for x in sorted_main if x.ticker == selected_ticker), None)
        if sel_item:
            entry = snap_map.get(sel_item.ticker)
            with st.container(border=True):
                header_cols = st.columns([6, 1])
                header_cols[0].markdown(f"**{_name(sel_item.ticker)}** ({sel_item.ticker})")
                if header_cols[1].button("→ 상세 분석", key=f"goto_{sel_item.ticker}"):
                    nav_to(NAV_TICKER, ticker=sel_item.ticker)
                    st.switch_page("pages/3_ticker.py")

                if entry:
                    grade  = Grade(entry["grade"])
                    emoji  = GRADE_EMOJI.get(grade, "")
                    exec_s = "✅ 실행가능" if entry.get("executable") else "🚫 차단"
                    conf   = entry.get("confidence", "—").upper()

                    ic1, ic2, ic3, ic4 = st.columns(4)
                    ic1.metric("등급", f"{emoji} {grade.value.upper()}")
                    ic2.metric("점수", entry["normalized_score"])
                    ic3.metric("신뢰도", conf)
                    ic4.metric("실행가능", exec_s)

                    sc1, sc2, sc3, sc4 = st.columns(4)
                    sc1.metric("밸류", entry.get("valuation_score", "—"))
                    sc2.metric("실적", entry.get("earnings_score", "—"))
                    sc3.metric("리스크", entry.get("risk_score", "—"))
                    sc4.metric("수급", entry.get("supply_score", "—"))

                    m = mom_data.get(sel_item.ticker, {})
                    if m:
                        signal   = m.get("signal")
                        strength = m.get("strength", 0.0)
                        arrow    = mom_strength_display(signal, strength)
                        badge    = "📈 " if strength >= 50 else ("📉 " if m.get("is_value_trap") else "")
                        rel_str  = ""
                        if m.get("rel_1m") is not None:
                            rel_str = (f"  1M {m['rel_1m']:+.1f}%p"
                                       f"  3M {m['rel_3m']:+.1f}%p"
                                       f"  6M {m['rel_6m']:+.1f}%p")
                        st.caption(f"모멘텀: {badge}{arrow}{rel_str}")
                        if m.get("is_value_trap"):
                            vt_col1, vt_col2 = st.columns([5, 1])
                            vt_col1.warning("⚠️ 시장 외면 중 — 밸류 매력적이나 모멘텀 지속 부진, 구조적 원인 확인 권장")
                            if sel_item.tag != WatchlistTag.MARKET_IGNORE:
                                if vt_col2.button("📉 태그", key=f"vt_tag_{sel_item.ticker}"):
                                    wl_save(WatchlistItem(
                                        ticker=sel_item.ticker,
                                        state=sel_item.state,
                                        added_date=sel_item.added_date,
                                        last_analyzed=sel_item.last_analyzed,
                                        last_grade=sel_item.last_grade,
                                        last_score=sel_item.last_score,
                                        last_confidence=sel_item.last_confidence,
                                        tag=WatchlistTag.MARKET_IGNORE,
                                        name=sel_item.name,
                                    ))
                    else:
                        st.caption("모멘텀: —")

                    notes = entry.get("notes", [])
                    if notes and conf != "HIGH":
                        with st.expander(f"⚠ 신뢰도 {conf} 사유"):
                            for n in notes:
                                st.caption(f"• {n}")
                else:
                    st.caption("스냅샷 없음 — Morning Run 실행 후 데이터 생성됩니다")

                triggers = load_triggers(sel_item.ticker, limit=3)
                if triggers:
                    st.caption("최근 이벤트: " +
                               "  |  ".join(f"`{t['date']}` {t['detail']}" for t in triggers))
    else:
        st.info("ACTIVE/REVIEW 종목 없음")


_main_section()

# snap_map / sort_key — fragment 바깥 섹션용
from backtests.snapshot import load_full, list_snapshot_dates as _ls_dates
_snap_dates = _ls_dates()
snap_map: dict = {}
if _snap_dates:
    _snap_data = load_full(_snap_dates[-1])
    if _snap_data:
        snap_map = {e["ticker"]: e for e in _snap_data.get("entries", [])}
sort_key: str = st.session_state.get("wl_sort_key", "기본 (상태·점수)")

# --- Research / Calibration 관찰 종목 ---
_RESEARCH_TAGS = (WatchlistTag.CALIBRATION, WatchlistTag.FALSE_POSITIVE, WatchlistTag.RESEARCH)
cal_items = [i for i in items if i.tag in _RESEARCH_TAGS]
if cal_items:
    st.divider()
    with st.expander(f"🧪 Research / Calibration ({len(cal_items)}개)", expanded=False):
        st.caption("엔진 검증용 추적 종목 — 이 종목들이 어떻게 되는지가 로직 개선의 단서가 됨")
        for item in sorted(cal_items, key=lambda i: (i.tag.value, i.ticker)):
            tag_emoji = WATCHLIST_TAG_EMOJI.get(item.tag, "📋")
            entry     = snap_map.get(item.ticker, {})
            score_str = str(entry.get("normalized_score", item.last_score or "—"))
            grade_val = entry.get("grade") or (item.last_grade.value if item.last_grade else None)
            grade_e   = GRADE_EMOJI.get(Grade(grade_val), "") if grade_val else ""
            st.write(f"{tag_emoji} **{_name(item.ticker)}** ({item.ticker})  {grade_e} {score_str}점  등록 {item.added_date}")

# --- DORMANT / REJECTED ---
inactive = [i for i in items if i.state in (WatchlistState.DORMANT, WatchlistState.REJECTED)]
if inactive:
    with st.expander(f"DORMANT / REJECTED ({len(inactive)}개)", expanded=False):
        st.dataframe(_build_rows(inactive, sort_key), use_container_width=True, hide_index=True)
        # 천이 사유 표시 — DORMANT / REJECTED
        from backtests.snapshot import get_neutral_streak as _get_neutral_streak
        from analyzer.watchlist.transitions import NEUTRAL_STREAK_THRESHOLD as _NST
        for _di in inactive:
            if _di.state == WatchlistState.DORMANT:
                _streak = _get_neutral_streak(_di.ticker, n=_NST)
                if _streak >= _NST:
                    st.caption(f"⚫ {_name(_di.ticker)} — NEUTRAL {_streak}회 연속으로 자동 전환")
            elif _di.state == WatchlistState.REJECTED:
                _trigs = load_triggers(_di.ticker, limit=5)
                _reject_trigs = [t for t in _trigs if t["event"] in ("manual_reject", "quick_reject")]
                if _reject_trigs:
                    _t = _reject_trigs[0]
                    st.caption(f"❌ {_name(_di.ticker)} — ({_t['date']}) {_t['detail']}")

# --- REVIEW 상세 ---
review_items = [i for i in items if i.state == WatchlistState.REVIEW]
if review_items:
    st.divider()
    st.subheader("🔶 REVIEW 상세")
    for item in review_items:
        triggers = load_triggers(item.ticker, limit=5)
        grade_str = (
            f"{GRADE_EMOJI.get(item.last_grade, '')} {item.last_grade.value.upper()}"
            if item.last_grade else "—"
        )
        with st.expander(f"{_name(item.ticker)} ({item.ticker})  {grade_str}", expanded=True):
            if triggers:
                # 가장 최근 트리거 = 현재 REVIEW 진입 원인
                st.write(f"⚠️ **REVIEW 진입 이유** (`{triggers[0]['date']}`): {triggers[0]['detail']}")
                if len(triggers) > 1:
                    st.caption("이전 이력: " + "  |  ".join(
                        f"`{t['date']}` {t['detail']}" for t in triggers[1:]
                    ))
            else:
                st.caption("이력 없음 — Morning Run 실행 후 자동 기록됩니다")

st.divider()

# --- Watchlist 커버리지 ---
st.divider()
with st.expander("📊 Watchlist 커버리지", expanded=False):
    from constants import Grade, GRADE_EMOJI

    wc1, wc2, wc3, wc4 = st.columns(4)
    wc1.metric("ACTIVE",   sum(1 for i in items if i.state == WatchlistState.ACTIVE))
    wc2.metric("REVIEW",   sum(1 for i in items if i.state == WatchlistState.REVIEW))
    wc3.metric("DORMANT",  sum(1 for i in items if i.state == WatchlistState.DORMANT))
    wc4.metric("REJECTED", sum(1 for i in items if i.state == WatchlistState.REJECTED))

    grade_counts = {}
    for item in items:
        if item.last_grade:
            g = item.last_grade.value
            grade_counts[g] = grade_counts.get(g, 0) + 1

    if grade_counts:
        st.markdown("**등급 분포**")
        gc_cols = st.columns(4)
        for col, gk in zip(gc_cols, ["strong_buy", "buy", "neutral", "avoid"]):
            cnt = grade_counts.get(gk, 0)
            emoji = GRADE_EMOJI.get(Grade(gk), "") if cnt else ""
            col.metric(gk.upper().replace("_", " "), f"{emoji} {cnt}")

    scored = [i.last_score for i in items if i.last_score is not None]
    if scored:
        avg_score = sum(scored) / len(scored)
        analyzed  = sum(1 for i in items if i.last_analyzed)
        st.caption(f"평균 점수: {avg_score:.1f}점  ·  마지막 분석 완료: {analyzed}/{len(items)}개")

    _TAG_DISPLAY_ORDER = [
        (WatchlistTag.GENERAL,        "📋 일반"),
        (WatchlistTag.CALIBRATION,    "🧪 Calibration"),
        (WatchlistTag.FALSE_POSITIVE, "⚠️ FP 후보"),
        (WatchlistTag.MOMENTUM,       "🔥 Momentum"),
        (WatchlistTag.MARKET_IGNORE,  "📉 시장 외면"),
        (WatchlistTag.RESEARCH,       "🔍 Research"),
    ]
    tag_dist = [(label, sum(1 for i in items if i.tag == tag))
                for tag, label in _TAG_DISPLAY_ORDER]
    tag_dist = [(label, n) for label, n in tag_dist if n > 0]
    if tag_dist:
        st.markdown("**태그 분포**")
        td_cols = st.columns(len(tag_dist))
        for col, (label, n) in zip(td_cols, tag_dist):
            col.metric(label, n)
