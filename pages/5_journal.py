from __future__ import annotations

import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzer.trade_journal import db as tj_db
from analyzer.trade_journal.models import (
    TradeRecord, BuyReason, SellReason, Verdict,
    BUY_REASON_LABELS, SELL_REASON_LABELS, VERDICT_LABELS,
)
from backtests.snapshot import load_full, list_snapshot_dates
from analyzer.data.krx import display_name as _name, find_tickers_by_name as _find_tickers
from analyzer.navigation import consume_nav, nav_to, NAV_TRADE, NAV_TICKER
from constants import ENGINE_VERSION

tj_db.init_db()

# ── Navigation: 외부에서 trade_id를 받아 세션에 저장 ──────────────────────────
_nav = consume_nav(NAV_TRADE)
if _nav.get("trade_id"):
    st.session_state["_current_trade_id"] = _nav["trade_id"]

# ---------------------------------------------------------------------------
# 최신 스냅샷 (점수 자동 첨부용)
# ---------------------------------------------------------------------------
_snap_dates       = list_snapshot_dates()
_last_snap        = load_full(_snap_dates[-1]) if _snap_dates else None
_snap_map: dict   = {e["ticker"]: e for e in _last_snap.get("entries", [])} if _last_snap else {}
_snap_date_str: str | None  = str(_snap_dates[-1]) if _snap_dates else None
_snap_rule_version: str     = (_last_snap.get("rule_version") or ENGINE_VERSION) if _last_snap else ENGINE_VERSION
_snap_name_extra: dict[str, str] = {t: _name(t) for t in _snap_map}

st.title("📓 Trade Journal")
st.caption("거래 기록 및 투자 의사결정 메모")


def _resolve_ticker(raw: str) -> tuple[str | None, str | None]:
    raw = raw.strip()
    if not raw:
        return None, "종목명 또는 코드를 입력하세요"
    if raw.isdigit() and len(raw) == 6:
        return raw, None
    matches = _find_tickers(raw, extra=_snap_name_extra)
    if not matches:
        return None, f"'{raw}' 종목을 찾을 수 없습니다"
    if len(matches) > 1:
        candidates = "  /  ".join(f"{_name(t)} ({t})" for t in matches[:5])
        return None, f"동명 종목 {len(matches)}개 — 코드로 입력하세요:  {candidates}"
    return matches[0], None


def _score_snapshot_for(ticker: str) -> tuple[dict | None, str | None, int | None]:
    entry = _snap_map.get(ticker)
    if not entry:
        return None, _snap_date_str, None
    return (
        {
            "score":          entry.get("normalized_score"),
            "grade":          entry.get("grade"),
            "valuation":      entry.get("valuation_score"),
            "earnings":       entry.get("earnings_score"),
            "risk":           entry.get("risk_score"),
            "supply":         entry.get("supply_score"),
            "eq_band":        entry.get("eq_band"),
            "industry_type":  entry.get("industry_type"),
            "engine_version": _snap_rule_version,
        },
        _snap_date_str,
        None,
    )


def _snap_age_label(snap_date_str: str | None) -> str:
    if not snap_date_str:
        return "—"
    try:
        delta = (date.today() - date.fromisoformat(snap_date_str)).days
        if delta == 0:  return "🟢 오늘"
        if delta <= 3:  return f"🟢 {delta}일 전"
        if delta <= 7:  return f"🟡 {delta}일 전"
        if delta <= 14: return f"🟠 {delta}일 전"
        return f"🔴 {delta}일 전"
    except Exception:
        return "—"


def _gap_label(gap: int | None) -> str:
    if gap is None:
        return "—"
    if gap <= 3:  return f"🟢 {gap}일"
    if gap <= 7:  return f"🟡 {gap}일"
    if gap <= 14: return f"🟠 {gap}일"
    return f"🔴 {gap}일"


@st.cache_data(ttl=3600, show_spinner=False)
def _calc_performance(
    ticker: str,
    entry_date_str: str,
    entry_price: Optional[float],
) -> dict[int, Optional[dict]]:
    from pykrx import stock as _px

    today = date.today().strftime("%Y%m%d")
    start = entry_date_str.replace("-", "")

    try:
        stk   = _px.get_market_ohlcv_by_date(start, today, ticker)["종가"].rename("stock")
        kospi = _px.get_index_ohlcv_by_date(start, today, "1001")["종가"].rename("kospi")
        df    = pd.concat([stk, kospi], axis=1).dropna()

        if df.empty:
            return {}

        ep       = float(entry_price) if entry_price else float(df["stock"].iloc[0])
        kospi_ep = float(df["kospi"].iloc[0])

        results: dict[int, Optional[dict]] = {}
        for w in [20, 60, 120]:
            if len(df) <= w:
                results[w] = None
            else:
                s_ret = (float(df["stock"].iloc[w]) - ep) / ep * 100
                k_ret = (float(df["kospi"].iloc[w]) - kospi_ep) / kospi_ep * 100
                results[w] = {
                    "stock_return":  round(s_ret, 2),
                    "kospi_return":  round(k_ret, 2),
                    "alpha":         round(s_ret - k_ret, 2),
                }
        return results
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# 탭
# ---------------------------------------------------------------------------
tab_list, tab_buy, tab_sell, tab_review = st.tabs([
    "📋 거래 목록", "➕ BUY", "➕ SELL", "✍️ Review"
])


# ── Tab 1: 거래 목록 + 인라인 상세 ──────────────────────────────────────────
with tab_list:
    records = tj_db.get_all()
    if not records:
        st.info("기록된 거래가 없습니다. BUY 또는 SELL 탭에서 추가하세요.")
    else:
        for r in records:
            col_info, col_btn = st.columns([5, 1])
            with col_info:
                snap       = r.score_snapshot or {}
                label_map  = BUY_REASON_LABELS if r.side == "BUY" else SELL_REASON_LABELS
                reasons    = (r.buy_reasons or r.sell_reasons)[:2]
                r_labels   = [label_map.get(v, v) for v in reasons]
                score_str  = f"{snap.get('score')}점" if snap.get("score") else "점수없음"
                grade_str  = (snap.get("grade") or "").upper()
                verdict_str = f"  ·  {VERDICT_LABELS.get(r.verdict, '')}" if r.verdict else ""
                reason_str  = f"  ·  {', '.join(r_labels)}" if r_labels else ""
                display = r.ticker_name or _name(r.ticker)
                st.markdown(
                    f"{'🟢' if r.side == 'BUY' else '🔴'} "
                    f"**{r.filled_at or '—'}**  "
                    f"{display} ({r.ticker})  "
                    f"·  {score_str} {grade_str}"
                    + reason_str + verdict_str
                )
            with col_btn:
                is_open = st.session_state.get("_current_trade_id") == r.trade_id
                btn_label = "닫기" if is_open else "열기"
                if st.button(btn_label, key=f"open_{r.trade_id}", use_container_width=True):
                    if is_open:
                        st.session_state.pop("_current_trade_id", None)
                    else:
                        st.session_state["_current_trade_id"] = r.trade_id
                    st.rerun()
        st.caption(f"총 {len(records)}건  ·  스냅샷 기준일: {_snap_date_str or '없음'}")

    # ── 인라인 상세 뷰 ─────────────────────────────────────────────────────
    trade_id = st.session_state.get("_current_trade_id")
    if trade_id:
        record = tj_db.get_by_trade_id(trade_id)
        if not record:
            st.error(f"거래 기록을 찾을 수 없습니다: {trade_id}")
            st.session_state.pop("_current_trade_id", None)
        else:
            st.divider()
            ticker = record.ticker
            name   = _name(ticker)
            snap   = record.score_snapshot or {}

            side_emoji = "🟢" if record.side == "BUY" else "🔴"
            st.subheader(f"{side_emoji} {name}  ·  {record.side}  ·  {record.filled_at or '날짜 없음'}")
            st.caption(f"{ticker}  ·  Engine {record.engine_version}")

            # 1. Decision Context
            st.markdown("**Decision Context**")
            st.caption(
                f"Immutable  ·  스냅샷 {record.snapshot_date or '—'}"
                f"  ·  Snapshot Gap {_gap_label(record.snapshot_gap_days)}"
            )
            if snap:
                c1, c2, c3, c4, c5, c6 = st.columns(6)
                c1.metric("Score",     snap.get("score",     "—"))
                c2.metric("Grade",     (snap.get("grade") or "—").upper())
                c3.metric("Valuation", snap.get("valuation", "—"))
                c4.metric("Earnings",  snap.get("earnings",  "—"))
                c5.metric("Risk",      snap.get("risk",      "—"))
                c6.metric("Supply",    snap.get("supply",    "—"))
                if snap.get("eq_band"):
                    st.caption(f"EQ Band: {snap['eq_band'].upper()}")
            else:
                st.caption("⚪ score_snapshot 없음")

            if record.buy_reasons:
                labels = [BUY_REASON_LABELS.get(v, v) for v in record.buy_reasons]
                st.markdown(f"**매수 이유:** {', '.join(labels)}")
            if record.sell_reasons:
                labels = [SELL_REASON_LABELS.get(v, v) for v in record.sell_reasons]
                st.markdown(f"**매도 이유:** {', '.join(labels)}")

            trade_parts = []
            if record.filled_price:
                trade_parts.append(f"체결가 {record.filled_price:,.0f}원")
            if record.filled_quantity:
                trade_parts.append(f"{record.filled_quantity:,}주")
            if record.filled_price and record.filled_quantity:
                trade_parts.append(f"합계 {record.filled_price * record.filled_quantity:,.0f}원")
            if trade_parts:
                st.caption("  ·  ".join(trade_parts))
            if record.memo:
                st.info(f"📝 {record.memo}")

            # 2. Current Status
            st.divider()
            st.markdown("**Current Status**")
            current   = _snap_map.get(ticker)
            age_label = _snap_age_label(_snap_date_str)
            st.caption(f"Latest Official Snapshot  ·  {_snap_date_str or '없음'}  ·  {age_label}")
            if current:
                cs1, cs2, cs3, cs4, cs5, cs6 = st.columns(6)
                cs1.metric("Score",     current.get("normalized_score", "—"))
                cs2.metric("Grade",     (current.get("grade") or "—").upper())
                cs3.metric("Valuation", current.get("valuation_score",  "—"))
                cs4.metric("Earnings",  current.get("earnings_score",   "—"))
                cs5.metric("Risk",      current.get("risk_score",       "—"))
                cs6.metric("Supply",    current.get("supply_score",     "—"))
                try:
                    delta = int(current["normalized_score"]) - int(snap["score"])
                    arrow = "▲" if delta > 0 else ("▼" if delta < 0 else "—")
                    sign  = "+" if delta > 0 else ""
                    st.caption(f"Decision Context 대비: {arrow} {sign}{delta}점")
                except (KeyError, ValueError, TypeError) as _e:
                    logging.debug("score delta display error: %s", _e)
            else:
                st.caption("⚪ Latest Snapshot unavailable")

            # 3. Performance
            st.divider()
            st.markdown("**Performance**")
            if record.side != "BUY" or not record.filled_at:
                st.caption("BUY 거래만 Alpha를 계산합니다." if record.side != "BUY" else "체결일 정보 없음")
            else:
                with st.spinner("Alpha 계산 중…"):
                    perf = _calc_performance(ticker, record.filled_at, record.filled_price)
                if not perf:
                    st.caption("⚪ 가격 데이터를 가져올 수 없습니다")
                else:
                    p1, p2, p3 = st.columns(3)
                    for col, w in zip([p1, p2, p3], [20, 60, 120]):
                        result = perf.get(w)
                        if result is None:
                            col.metric(f"{w}D Alpha", "계산 중", help=f"{w} 영업일 미경과")
                        else:
                            alpha = result["alpha"]
                            s_ret = result["stock_return"]
                            k_ret = result["kospi_return"]
                            col.metric(
                                f"{w}D Alpha",
                                f"{alpha:+.1f}%",
                                delta=f"수익률 {s_ret:+.1f}%  vs KOSPI {k_ret:+.1f}%",
                                delta_color="normal",
                            )

            # 4. Review
            st.divider()
            st.markdown("**Review**")
            verdict_values = [v.value for v in Verdict]
            current_idx    = verdict_values.index(record.verdict) if record.verdict in verdict_values else 0
            new_verdict = st.radio(
                "결과",
                options=verdict_values,
                format_func=lambda v: VERDICT_LABELS.get(v, v),
                index=current_idx,
                horizontal=True,
                key="detail_verdict",
            )
            new_note = st.text_area(
                "Review 메모", value=record.review_note or "", height=80, key="detail_review_note"
            )
            if st.button("✍️ Review 저장", type="primary"):
                tj_db.update(
                    record.trade_id,
                    verdict     = new_verdict,
                    review_note = new_note.strip() or None,
                    reviewed_at = datetime.now().isoformat(),
                )
                st.toast("Review 저장 완료", icon="✅")
                st.rerun()

            # 5. History
            st.divider()
            st.markdown("**History**")
            history: list[tuple[str, str]] = []
            if record.created_at:
                history.append(("Created", record.created_at))
            if record.reviewed_at:
                history.append(("Review Added", record.reviewed_at))
            if record.updated_at and record.updated_at != record.created_at:
                history.append(("Last Updated", record.updated_at))
            for label, ts in history:
                st.caption(f"**{label}**  —  {ts}")
            if not history:
                st.caption("기록 없음")

            # 6. Related
            st.divider()
            st.markdown("**Related**")
            rel1, rel2, rel3 = st.columns(3)
            with rel1:
                if st.button("🔎 Ticker", use_container_width=True):
                    nav_to(NAV_TICKER, ticker=ticker)
                    st.switch_page("pages/3_ticker.py")
            with rel2:
                if st.button("🗂 Snapshots", use_container_width=True):
                    st.switch_page("pages/8_snapshots.py")
            with rel3:
                if st.button("📈 Research", use_container_width=True):
                    st.switch_page("pages/7_research.py")


# ── Tab 2: BUY 기록 ─────────────────────────────────────────────────────────
with tab_buy:
    _buy_ok = st.session_state.pop("_buy_saved", None)
    if _buy_ok:
        st.success(_buy_ok)

    with st.form("buy_form"):
        b1, b2 = st.columns([2, 1])
        with b1:
            buy_input = st.text_input("종목명 또는 코드", placeholder="삼성전자  또는  005930")
        with b2:
            buy_date = st.date_input("매수일", value=date.today())

        b3, b4 = st.columns(2)
        with b3:
            buy_price = st.number_input("체결가 (원)", min_value=0.0, step=100.0, value=0.0)
        with b4:
            buy_qty = st.number_input("수량 (주)", min_value=0, step=1, value=0)

        buy_reasons = st.multiselect(
            "매수 이유 (복수 선택 가능)",
            options=[r.value for r in BuyReason],
            format_func=lambda v: BUY_REASON_LABELS.get(v, v),
        )
        buy_memo = st.text_area("메모 (자유)", height=60)
        buy_submit = st.form_submit_button("💾 BUY 저장", type="primary", use_container_width=True)

    if buy_submit:
        ticker, err = _resolve_ticker(buy_input)
        if err:
            st.error(err)
        else:
            snap_data, snap_date, _ = _score_snapshot_for(ticker)
            gap = (buy_date - date.fromisoformat(snap_date)).days if snap_date else None
            tj_db.insert(TradeRecord(
                ticker            = ticker,
                ticker_name       = _name(ticker) if _name(ticker) != ticker else None,
                side              = "BUY",
                engine_version    = _snap_rule_version,
                filled_price      = buy_price  if buy_price > 0 else None,
                filled_quantity   = buy_qty    if buy_qty   > 0 else None,
                filled_at         = buy_date.isoformat(),
                snapshot_date     = snap_date,
                snapshot_gap_days = gap,
                score_snapshot    = snap_data,
                buy_reasons       = buy_reasons,
                memo              = buy_memo.strip() or None,
            ))
            name = _name(ticker)
            if snap_data:
                sc = snap_data
                msg = (
                    f"BUY 저장 완료 — {name} ({ticker})  "
                    f"점수 {sc.get('score')} / {(sc.get('grade') or '').upper()} / "
                    f"V {sc.get('valuation')} S {sc.get('supply')}"
                )
            else:
                msg = f"BUY 저장 완료 — {name} ({ticker})  ⚠️ 스냅샷 없음"
            st.session_state["_buy_saved"] = msg
            st.rerun()


# ── Tab 3: SELL 기록 ────────────────────────────────────────────────────────
with tab_sell:
    _sell_ok = st.session_state.pop("_sell_saved", None)
    if _sell_ok:
        st.success(_sell_ok)

    with st.form("sell_form"):
        s1, s2 = st.columns([2, 1])
        with s1:
            sell_input = st.text_input("종목명 또는 코드", placeholder="삼성전자  또는  005930", key="sell_tk")
        with s2:
            sell_date = st.date_input("매도일", value=date.today(), key="sell_dt")

        s3, s4 = st.columns(2)
        with s3:
            sell_price = st.number_input("체결가 (원)", min_value=0.0, step=100.0, value=0.0, key="sell_px")
        with s4:
            sell_qty = st.number_input("수량 (주)", min_value=0, step=1, value=0, key="sell_q")

        sell_reasons = st.multiselect(
            "매도 이유 (복수 선택 가능)",
            options=[r.value for r in SellReason],
            format_func=lambda v: SELL_REASON_LABELS.get(v, v),
        )
        sell_memo = st.text_area("메모 (자유)", height=60, key="sell_memo")
        sell_submit = st.form_submit_button("💾 SELL 저장", type="primary", use_container_width=True)

    if sell_submit:
        ticker, err = _resolve_ticker(sell_input)
        if err:
            st.error(err)
        else:
            snap_data, snap_date, _ = _score_snapshot_for(ticker)
            gap = (sell_date - date.fromisoformat(snap_date)).days if snap_date else None
            tj_db.insert(TradeRecord(
                ticker            = ticker,
                ticker_name       = _name(ticker) if _name(ticker) != ticker else None,
                side              = "SELL",
                engine_version    = _snap_rule_version,
                filled_price      = sell_price if sell_price > 0 else None,
                filled_quantity   = sell_qty   if sell_qty   > 0 else None,
                filled_at         = sell_date.isoformat(),
                snapshot_date     = snap_date,
                snapshot_gap_days = gap,
                score_snapshot    = snap_data,
                sell_reasons      = sell_reasons,
                memo              = sell_memo.strip() or None,
            ))
            st.session_state["_sell_saved"] = f"SELL 저장 완료 — {_name(ticker)} ({ticker})"
            st.rerun()


# ── Tab 4: Review ───────────────────────────────────────────────────────────
with tab_review:
    all_records = tj_db.get_all()
    if not all_records:
        st.info("기록된 거래가 없습니다.")
    else:
        options_map = {
            r.trade_id: (
                f"{'🟢' if r.side == 'BUY' else '🔴'} "
                f"{r.filled_at or '날짜없음'}  "
                f"{_name(r.ticker)} ({r.ticker})"
                f"{'  ✍️' if r.verdict else ''}"
            )
            for r in all_records
        }
        selected_id = st.selectbox(
            "거래 선택",
            options=list(options_map.keys()),
            format_func=lambda tid: options_map[tid],
        )
        sel = tj_db.get_by_trade_id(selected_id)

        if sel:
            st.divider()
            if sel.score_snapshot:
                snap = sel.score_snapshot
                rc1, rc2, rc3, rc4, rc5 = st.columns(5)
                rc1.metric("총점",      snap.get("score",    "—"))
                rc2.metric("등급",      (snap.get("grade") or "—").upper())
                rc3.metric("Valuation", snap.get("valuation","—"))
                rc4.metric("Supply",    snap.get("supply",   "—"))
                rc5.metric("Risk",      snap.get("risk",     "—"))
            if sel.buy_reasons:
                labels = [BUY_REASON_LABELS.get(v, v) for v in sel.buy_reasons]
                st.markdown(f"**매수 이유:** {', '.join(labels)}")
            if sel.sell_reasons:
                labels = [SELL_REASON_LABELS.get(v, v) for v in sel.sell_reasons]
                st.markdown(f"**매도 이유:** {', '.join(labels)}")
            if sel.memo:
                st.caption(f"메모: {sel.memo}")
            st.divider()

            verdict_values = [v.value for v in Verdict]
            current_idx    = verdict_values.index(sel.verdict) if sel.verdict in verdict_values else 0
            new_verdict = st.radio(
                "Review 결과",
                options=verdict_values,
                format_func=lambda v: VERDICT_LABELS.get(v, v),
                index=current_idx,
                horizontal=True,
            )
            new_note = st.text_area("Review 메모", value=sel.review_note or "", height=80)
            if st.button("✍️ Review 저장", type="primary"):
                tj_db.update(
                    sel.trade_id,
                    verdict     = new_verdict,
                    review_note = new_note.strip() or None,
                    reviewed_at = datetime.now().isoformat(),
                )
                st.success("Review 저장 완료")
                st.rerun()
