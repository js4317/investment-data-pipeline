from __future__ import annotations

import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import threading
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtests.snapshot import load_full, list_snapshot_dates
from analyzer.watchlist.store import load_all as wl_load_all
from analyzer.watchlist.models import WatchlistState
from analyzer.data.krx import display_name as _name, rel_time as _rel_time_util
from analyzer.navigation import nav_to, NAV_TICKER
from constants import ENGINE_VERSION
from backtests.market_db import get_ohlcv as _mdb_get, init_db as _mdb_init

_mdb_init()


# ---------------------------------------------------------------------------
# Morning Run
# ---------------------------------------------------------------------------

title_col, btn_col = st.columns([5, 1])
with title_col:
    st.title("📊 Dashboard")
    st.caption(f"v{ENGINE_VERSION}")
with btn_col:
    st.write("")
    st.write("")
    run_btn_top = st.button("☀️ Morning Run", type="primary",
                            disabled=st.session_state.get("running", False),
                            key="morning_run_top", use_container_width=True)

run_btn = run_btn_top


def _morning_run_worker(job: dict) -> None:
    """백그라운드 스레드. job dict를 직접 변경해서 UI와 상태 공유."""
    from run import analyze_ticker
    from analyzer.rules.market_regime import get_market_state
    from analyzer.watchlist.store import load as wl_load, save as wl_save, save_trigger
    from analyzer.watchlist import transitions as wl_transitions
    from backtests import snapshot as bt_snapshot
    from constants import Confidence

    try:
        today        = job["today"]
        tickers      = job["tickers"]
        total        = len(tickers)
        market_state = get_market_state(today)

        results = []
        for idx, ticker in enumerate(tickers):
            job["current"] = ticker
            job["done_n"]  = idx
            result = analyze_ticker(ticker, today, market_state)
            results.append(result)

            existing = wl_load(ticker)
            if existing:
                updated, triggers = wl_transitions.apply(existing, result, today)
                wl_save(updated)
                for t in triggers:
                    save_trigger(t, today)

        job["current"] = "📈 모멘텀 오버레이 계산 중..."
        from analyzer.data.momentum import batch_fetch_momentum as _batch_mom
        mom_inputs = [(r.scoring.ticker, r.scoring.valuation_score) for r in results]
        mom_map    = _batch_mom(mom_inputs, today, max_workers=4)

        job["current"] = "💾 스냅샷 저장 중..."
        from analyzer.data.krx import prefetch_all_sectors as _prefetch_sectors, fetch_price_context as _fpc
        sector_map = _prefetch_sectors(today)
        _pc_map: dict = {}
        for _r in results:
            try:
                _pc = _fpc(_r.scoring.ticker, today)
                if _pc:
                    _pc_map[_r.scoring.ticker] = _pc
            except Exception:
                pass
        bt_snapshot.save(results, today, mom_map=mom_map, sector_map=sector_map,
                         price_context_map=_pc_map or None,
                         universe="WATCHLIST",
                         universe_label="워치리스트 ACTIVE/REVIEW")

        partial = any(r.scoring.confidence == Confidence.LOW for r in results)
        job["results"] = results
        job["status"]  = "partial" if partial else "success"
    except Exception as e:
        job["status"] = "failed"
        job["error"]  = str(e)
    finally:
        job["done"] = True


if run_btn:
    watchlist_tickers = [i.ticker for i in wl_load_all()
                         if i.state in (WatchlistState.ACTIVE, WatchlistState.REVIEW, WatchlistState.DORMANT)]
    if not watchlist_tickers:
        st.info("Watchlist에 ACTIVE/REVIEW/DORMANT 종목 없음 — CLI로 종목 추가 후 사용")
    else:
        job = {
            "today":   date.today(),
            "tickers": watchlist_tickers,
            "current": "",
            "done_n":  0,
            "status":  "running",
            "done":    False,
            "results": None,
            "error":   None,
        }
        st.session_state["morning_job"] = job
        st.session_state["running"]     = True
        st.session_state.pop("last_run_ok", None)
        threading.Thread(target=_morning_run_worker, args=(job,), daemon=True).start()
        st.rerun()

_job = st.session_state.get("morning_job")
if _job:
    total = len(_job["tickers"])
    if not _job["done"]:
        done_n  = _job["done_n"]
        ticker  = _job["current"]
        name    = _name(ticker) if ticker and ticker.isdigit() else ticker
        st.progress(done_n / total if total else 0)
        st.info(f"📊 {name} 분석 중...  ({done_n + 1} / {total})")
        import time; time.sleep(0.8)
        st.rerun()
    else:
        st.session_state["running"] = False
        if _job["status"] == "success":
            st.session_state["last_results"]  = _job["results"]
            st.session_state["last_run_date"] = _job["today"]
            st.session_state["last_run_ok"]   = "success"
            st.session_state.pop("morning_job", None)
            st.rerun()
        elif _job["status"] == "partial":
            st.session_state["last_results"]  = _job["results"]
            st.session_state["last_run_date"] = _job["today"]
            st.session_state["last_run_ok"]   = "partial"
            st.session_state.pop("morning_job", None)
            st.rerun()
        else:
            st.error(f"❌ 분석 실패: {_job.get('error', '알 수 없는 오류')}")
            st.session_state.pop("morning_job", None)

if "last_run_ok" in st.session_state:
    status = st.session_state["last_run_ok"]
    if status == "success":
        st.success(f"✅ 분석 완료 — {st.session_state['last_run_date']}")
    elif status == "partial":
        st.warning("⚠ Partial failure — 일부 종목 confidence LOW")

# ---------------------------------------------------------------------------
# 데이터 로드
# ---------------------------------------------------------------------------

import json as _json
from analyzer.rules.sell_checklist import build_sell_checklist as _build_cl
from analyzer.risk_radar import read_cache as _rr_read_cache
from analyzer.portfolio import loader as _port_loader
from analyzer.portfolio.health import compute_health as _compute_health

dates     = list_snapshot_dates()
last_snap = load_full(dates[-1]) if dates else None
items     = wl_load_all()
active    = [i for i in items if i.state == WatchlistState.ACTIVE]
review    = [i for i in items if i.state == WatchlistState.REVIEW]

_pos_path  = Path(__file__).parent.parent / "positions.json"
_pos_raw   = _json.loads(_pos_path.read_text()) if _pos_path.exists() else {}
_portfolio = _port_loader.load()
_held      = list(_portfolio.positions.keys()) if _portfolio else list(_pos_raw.get("positions", {}).keys())

_snap_map_d: dict = {e["ticker"]: e for e in last_snap.get("entries", [])} if last_snap else {}

# ---------------------------------------------------------------------------
# ⚡ 오늘 할 일
# ---------------------------------------------------------------------------

# 보유 종목 매도 체크리스트 판정
_cl_red:    list[tuple[str, object]] = []
_cl_yellow: list[tuple[str, object]] = []
for _ht in _held:
    _cl_entry = _snap_map_d.get(_ht)
    if _cl_entry is None:
        continue
    _cl = _build_cl(_ht, _cl_entry, dates)
    if _cl.verdict == "RED":
        _cl_red.append((_ht, _cl))
    elif _cl.verdict in ("YELLOW", "WATCH"):
        _cl_yellow.append((_ht, _cl))

# 매수 후보 — STRONG_BUY, executable, 미보유
_buy_cands = sorted(
    [e for e in _snap_map_d.values()
     if e["ticker"] not in _held
     and e.get("grade") == "strong_buy"
     and e.get("executable", False)],
    key=lambda e: e.get("normalized_score", 0), reverse=True,
)[:3]

# Risk Radar 갱신 필요 (캐시 없음)
_rr_stale = [t for t in _held if _rr_read_cache(t) is None]

# CRITICAL 미확인 공시 — 보유 + ACTIVE 워치리스트, 최근 7일, 최대 5건 표시
from analyzer.market_context.filings import get_filings as _get_filings, get_seen_tracker as _get_seen_tracker
from analyzer.market_context.models import FilingImportance as _FilingImportance
_FILING_DAYS    = 7   # 대시보드 알림 윈도우
_FILING_MAX     = 5   # 배너 최대 표시 수
_seen_tr        = _get_seen_tracker()
# 보유 종목 전체 + ACTIVE 중 STRONG_BUY만
_sb_active = [i.ticker for i in active
              if (_snap_map_d.get(i.ticker) or {}).get("grade") == "strong_buy"]
_alert_tickers  = list(dict.fromkeys(_held + _sb_active))
_filing_cutoff   = (date.today() - timedelta(days=_FILING_DAYS)).isoformat()
_critical_alerts: list[tuple[str, object]] = []
for _at in _alert_tickers:
    _filings = _get_filings(_at, days=30)  # 캐시는 30일, 날짜 필터는 직접 적용
    _unseen_critical = [f for f in _filings
                        if f in _seen_tr.unseen(_at, _filings)
                        and f.importance == _FilingImportance.CRITICAL
                        and f.date >= _filing_cutoff]
    for _uf in _unseen_critical:
        _critical_alerts.append((_at, _uf))

# Portfolio Health
_health = _compute_health(_portfolio, _snap_map_d, _rr_read_cache) if _portfolio else None

st.divider()
st.subheader("⚡ 오늘 할 일")

# ── Health 카드
if _health:
    _hrow1, _hrow2 = st.columns([1, 5])
    with _hrow1:
        st.metric("📊 Portfolio Health", f"{_health.score} / 100  {_health.grade_emoji}")
    with _hrow2:
        _mom_arrow = {"positive": "▲", "negative": "▼", "neutral": "━", "N/A": "—"}
        st.markdown(
            f"평균 점수 **{_health.avg_score}**  ·  "
            f"STRONG_BUY **{_health.strong_buy_pct:.0f}%**  ·  "
            f"Risk **{_health.avg_risk}**  ·  "
            f"Momentum **{_mom_arrow.get(_health.avg_momentum, '—')}**  ·  "
            f"현금 **{_health.cash_pct:.1f}%**  ·  "
            f"**{_health.grade_label}**"
        )
    for _hw in _health.warnings:
        st.warning(f"⚠ {_hw}")

# ── 요약 지표
_chip_cols = st.columns(5)
_chip_cols[0].metric("매도 검토",       f"🔴 {len(_cl_red)}건"              if _cl_red          else "🟢 없음")
_chip_cols[1].metric("확인 필요",       f"🟡 {len(_cl_yellow)}건"           if _cl_yellow        else "🟢 없음")
_chip_cols[2].metric("매수 후보",       f"🟢 {len(_buy_cands)}건"           if _buy_cands        else "없음")
_chip_cols[3].metric("미확인 공시",     f"⚠️ {len(_critical_alerts)}건"     if _critical_alerts  else "✅ 없음")
_chip_cols[4].metric("Risk Radar 갱신", f"🔄 {len(_rr_stale)}건"            if _rr_stale         else "✅ 최신")

# ── ⚠️ CRITICAL 미확인 공시 (최대 5건)
for _at, _uf in _critical_alerts[:_FILING_MAX]:
    _ac1, _ac2 = st.columns([9, 1])
    with _ac1:
        st.warning(f"⚠️ **{_name(_at)}** — {_uf.type_icon} {_uf.title}  ·  {_uf.date}")
    with _ac2:
        st.write("")
        if st.button("공시 →", key=f"filing_goto_{_at}_{_uf.source_id}", use_container_width=True):
            nav_to(NAV_TICKER, ticker=_at, highlight=_uf.source_id)
            st.switch_page("pages/3_ticker.py")

if len(_critical_alerts) > _FILING_MAX:
    st.caption(f"외 {len(_critical_alerts) - _FILING_MAX}건 더 — Ticker 페이지에서 확인")

# ── 🔴 매도 검토
for _ht, _cl in _cl_red:
    _alert_labels = [i.label for i in _cl.critical_items if i.state == "alert"]
    _summary = "  /  ".join(_alert_labels) if _alert_labels else "구조 변화 감지"
    _rc1, _rc2 = st.columns([9, 1])
    with _rc1:
        st.error(
            f"🔴 **{_name(_ht)}** — {_cl.verdict_label}  ·  구조 변화 {_cl.critical_alerts}건  \n"
            f"{_summary}"
        )
    with _rc2:
        st.write("")
        if st.button("Portfolio →", key=f"sell_goto_{_ht}", use_container_width=True):
            st.switch_page("pages/2_portfolio.py")

# ── 🟡 확인 필요
for _ht, _cl in _cl_yellow:
    _alert_labels = [i.label for i in _cl.critical_items if i.state == "alert"]
    _ctx_labels   = [i.label for i in _cl.context_items  if i.state == "alert"]
    _summary = "  /  ".join(_alert_labels + _ctx_labels) if (_alert_labels or _ctx_labels) else "맥락 변화 감지"
    _yc1, _yc2 = st.columns([9, 1])
    with _yc1:
        _hdr_parts = []
        if _cl.critical_alerts: _hdr_parts.append(f"구조 변화 {_cl.critical_alerts}건")
        if _cl.context_alerts:  _hdr_parts.append(f"맥락 변화 {_cl.context_alerts}건")
        st.warning(
            f"🟡 **{_name(_ht)}** — {_cl.verdict_label}  ·  {'  /  '.join(_hdr_parts) or '변화 감지'}  \n"
            f"{_summary}"
        )
    with _yc2:
        st.write("")
        if st.button("Portfolio →", key=f"check_goto_{_ht}", use_container_width=True):
            st.switch_page("pages/2_portfolio.py")

# ── 🟢 매수 후보
_MOM_DISPLAY = {
    "strong":   "🔥 Strong",
    "positive": "✅ Positive",
    "neutral":  "⚪ Neutral",
    "negative": "⚠️ Negative",
}

for _bc in _buy_cands:
    _bc_t  = _bc["ticker"]
    _score = _bc.get("normalized_score", "—")

    # 현재가 — DB-only 최근 7일 역방향 탐색
    _cur_price  = None
    _price_label = "—"
    for _d in range(7):
        _price_row = _mdb_get(_bc_t, date.today() - timedelta(days=_d))
        if _price_row and _price_row.get("close"):
            _cur_price   = float(_price_row["close"])
            _price_label = "오늘" if _d == 0 else f"{_d}일 전"
            break

    if _cur_price is not None:
        _price_str = f"{int(_cur_price):,}원 ({_price_label})"
    else:
        _price_str = "—"

    # 추세 — mom_signal 풀 표시
    _mom_sig = _bc.get("mom_signal") or ""
    _mom_str = _MOM_DISPLAY.get(_mom_sig, "—")

    # 추천 이유 — 우선순위 기반 한 줄 요약
    _fp  = _bc.get("foreign_positive")
    _pp  = _bc.get("pension_positive")
    _ip  = _bc.get("institution_positive")
    _v   = _bc.get("valuation_score") or 0
    _s   = _bc.get("supply_score")    or 0
    _str = (_bc.get("mom_signal") or "") == "strong"

    if _fp and _pp:      _reason_str = "외인·연기금 쌍끌이 매수"
    elif _fp and _ip:    _reason_str = "외인·기관 쌍끌이 매수"
    elif _fp:            _reason_str = "외국인 집중 매수"
    elif _pp:            _reason_str = "연기금 순매수"
    elif _v >= 23:       _reason_str = "극저평가"
    elif _s >= 22 and _str: _reason_str = "강한 모멘텀 + 수급"
    else:                _reason_str = "종합 우수"

    _gc1, _gc2 = st.columns([9, 1])
    with _gc1:
        st.success(
            f"🟢 **{_name(_bc_t)}** — {_score}점  ·  종가 {_price_str}  ·  추세 {_mom_str}  \n"
            f"{_reason_str}"
        )
    with _gc2:
        st.write("")
        if st.button("분석 →", key=f"buy_goto_{_bc_t}", use_container_width=True):
            nav_to(NAV_TICKER, ticker=_bc_t)
            st.switch_page("pages/3_ticker.py")

# ── 🔄 Risk Radar 갱신 (접이식 — 유지보수 항목, 트레이딩 시그널 아님)
if _rr_stale:
    with st.expander(f"🔄 Risk Radar 갱신 필요 ({len(_rr_stale)}건)", expanded=False):
        _stale_names = ", ".join(_name(t) for t in _rr_stale)
        _rrc1, _rrc2 = st.columns([9, 1])
        with _rrc1:
            st.info(f"{_stale_names}")
        with _rrc2:
            if st.button("Portfolio →", key="rr_goto_portfolio", use_container_width=True):
                st.switch_page("pages/2_portfolio.py")

if not _cl_red and not _cl_yellow and not _buy_cands and not _rr_stale:
    st.success("✅ 오늘 할 일 없음")

# ---------------------------------------------------------------------------
# 📊 Morning Run 변경 요약 (직전 스냅샷 대비 등급 변화 / 점수 ±5+)
# ---------------------------------------------------------------------------
if "last_run_ok" in st.session_state and "last_results" in st.session_state:
    _diff_dates = list_snapshot_dates()
    if len(_diff_dates) >= 2:
        _prev_snap = load_full(_diff_dates[-2])
        if _prev_snap:
            _prev_map = {e["ticker"]: e for e in _prev_snap.get("entries", [])}
            _GR = {"strong_buy": 3, "buy": 2, "neutral": 1, "avoid": 0}
            _GL = {"strong_buy": "STRONG_BUY", "buy": "BUY",
                   "neutral": "NEUTRAL", "avoid": "AVOID"}
            _held_s = set(_held)

            _changes: list[dict] = []
            for _r in st.session_state["last_results"]:
                _tk = _r.scoring.ticker
                _ng = _r.scoring.grade.value
                _ns = _r.scoring.normalized_score
                _pe = _prev_map.get(_tk)
                if _pe is None:
                    continue
                _pg = _pe.get("grade", "")
                _ps = int(_pe.get("normalized_score") or 0)
                _sd = _ns - _ps
                if _ng != _pg or abs(_sd) >= 5:
                    _changes.append({
                        "ticker":        _tk,
                        "prev_grade":    _pg,
                        "new_grade":     _ng,
                        "prev_score":    _ps,
                        "new_score":     _ns,
                        "score_delta":   _sd,
                        "grade_changed": _ng != _pg,
                        "is_held":       _tk in _held_s,
                    })

            if _changes:
                def _dsort(c: dict):
                    gd = _GR.get(c["prev_grade"], 0) - _GR.get(c["new_grade"], 0)
                    return (-int(c["is_held"]), -abs(gd), -abs(c["score_delta"]))
                _changes.sort(key=_dsort)

                _held_cnt  = sum(1 for c in _changes if c["is_held"])
                _grade_cnt = sum(1 for c in _changes if c["grade_changed"])
                _exp_lbl   = f"📊 Morning Run 변경 요약 — {len(_changes)}건"
                if _held_cnt:
                    _exp_lbl += f"  (보유 {_held_cnt}건 포함)"

                with st.expander(_exp_lbl, expanded=bool(_held_cnt or _grade_cnt)):
                    _d_prev = _prev_snap.get("date", "이전")
                    _d_new  = str(st.session_state.get("last_run_date", "오늘"))
                    st.caption(
                        f"비교 기간: {_d_prev} → {_d_new}  ·  "
                        f"등급 변화 {_grade_cnt}건  ·  🔵 = 보유 종목"
                    )
                    _diff_rows = []
                    for _c in _changes:
                        _dir = ""
                        if _c["grade_changed"]:
                            _gd = _GR.get(_c["new_grade"], 0) - _GR.get(_c["prev_grade"], 0)
                            _dir = "▲ 등급 상승" if _gd > 0 else "▼ 등급 하락"
                        _diff_rows.append({
                            "종목": f"{'🔵 ' if _c['is_held'] else ''}{_name(_c['ticker'])} ({_c['ticker']})",
                            "이전": f"{_GL.get(_c['prev_grade'], _c['prev_grade'])}  {_c['prev_score']}점",
                            "현재": f"{_GL.get(_c['new_grade'], _c['new_grade'])}  {_c['new_score']}점",
                            "변화": f"{_c['score_delta']:+d}점  {_dir}".strip(),
                        })
                    st.dataframe(_diff_rows, use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
# 📄 새 공시
# ---------------------------------------------------------------------------

try:
    from analyzer.market_context.filings import get_filings as _dash_filings, get_seen_tracker as _dash_seen_tracker
    from analyzer.market_context.models import FilingImportance as _FI

    _seen_tr     = _dash_seen_tracker()
    _wl_tickers  = [i.ticker for i in (active + review)]
    _all_targets = list(dict.fromkeys(_held + _wl_tickers))
    _held_set    = set(_held)

    _dash_cutoff = (date.today() - timedelta(days=7)).isoformat()
    _all_unseen: list[tuple[str, object]] = []
    for _ht in _all_targets:
        _filings_7d = [f for f in _dash_filings(_ht, days=30) if f.date >= _dash_cutoff]
        for _uf in _seen_tr.unseen(_ht, _filings_7d):
            _all_unseen.append((_ht, _uf))

    _crit = [(_ht, _uf) for _ht, _uf in _all_unseen if _uf.importance == _FI.CRITICAL]
    _imp  = [(_ht, _uf) for _ht, _uf in _all_unseen if _uf.importance == _FI.IMPORTANT]

    if _crit or _imp:
        from analyzer.market_context.filings import get_type_label as _filing_lbl
        st.divider()
        _badge_parts = []
        if _crit: _badge_parts.append(f"🔴 {len(_crit)}건")
        if _imp:  _badge_parts.append(f"🟡 {len(_imp)}건")
        st.markdown(f"**📄 새 공시  {'  ·  '.join(_badge_parts)}** (최근 7일)")

        _all_items = _crit + _imp
        _preview   = _all_items[:3]
        _overflow  = _all_items[3:]

        _rel_time = _rel_time_util

        def _render_filing_row(ht: str, uf, key_suffix: str) -> None:
            _held_dot = "🔵 " if ht in _held_set else ""
            _lbl      = _filing_lbl(uf.item_type)
            _fc1, _fc2 = st.columns([9, 1])
            with _fc1:
                st.write(f"{_held_dot}{uf.type_icon} **{_name(ht)}**")
                st.caption(f"{_lbl}  ·  {_rel_time(uf.date)}")
            with _fc2:
                if st.button("보기", key=f"filing_goto_{uf.source_id}{key_suffix}"):
                    nav_to(NAV_TICKER, ticker=ht, highlight=uf.source_id)
                    st.switch_page("pages/3_ticker.py")

        for _ht, _uf in _preview:
            _render_filing_row(_ht, _uf, "")

        if _overflow:
            with st.expander(f"+ {len(_overflow)}건 더 보기"):
                for _ht, _uf in _overflow:
                    _render_filing_row(_ht, _uf, "_more")
except Exception as _e:
    logging.warning("filing section render error: %s", _e)


