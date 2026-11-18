from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtests.snapshot import load_full, list_snapshot_dates
from analyzer.data.krx import display_name as _name
from analyzer.data import cache as _cache
from analyzer.navigation import consume_nav, nav_to, NAV_PERF, NAV_TRADE
from constants import Grade, GRADE_EMOJI, ENGINE_VERSION
from backtests.market_db import (
    get_ohlcv  as _mdb_get,
    init_db    as _mdb_init,
    insert_batch as _mdb_insert,
)

_mdb_init()

from pykrx import stock as krx

try:
    from analyzer.trade_journal import db as _tj_db
    _tj_db.init_db()
    _traded_tickers: set[str] = {r.ticker for r in _tj_db.get_all()}
except Exception:
    _traded_tickers = set()

# ---------------------------------------------------------------------------
# Performance helpers
# ---------------------------------------------------------------------------

_GRADE_ORDER  = [Grade.STRONG_BUY, Grade.BUY, Grade.NEUTRAL, Grade.AVOID]
_TTL_CLOSE    = 60 * 60 * 24 * 365
_KOSPI_IDX    = "1001"
_KOSPI_DB_TK  = "1001"
_RELIABLE_N   = 30
_MAX_WORKERS  = 5


def _next_trading_day_approx(d: date) -> date:
    delta = 3 if d.weekday() == 4 else (2 if d.weekday() == 5 else 1)
    return d + timedelta(days=delta)


def _fetch_close(ticker: str, target_date: date) -> Optional[float]:
    cache_key = f"{ticker}:{target_date.isoformat()}"
    cached    = _cache.get("close", cache_key, _TTL_CLOSE)
    if cached is not None:
        return cached
    for delta in range(7):
        row = _mdb_get(ticker, target_date - timedelta(days=delta))
        if row and row.get("close"):
            price = float(row["close"])
            _cache.put("close", cache_key, price)
            return price
    from_str = (target_date - timedelta(days=7)).strftime("%Y%m%d")
    to_str   = target_date.strftime("%Y%m%d")
    try:
        time.sleep(0.2)
        df = krx.get_market_ohlcv_by_date(from_str, to_str, ticker)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    price    = float(df["종가"].iloc[-1])
    hit_date = df.index[-1].date() if hasattr(df.index[-1], "date") else target_date
    _cache.put("close", cache_key, price)
    _mdb_insert([{"ticker": ticker, "trade_date": hit_date.isoformat(),
                  "open": None, "high": None, "low": None,
                  "close": price, "volume": None}])
    return price


def _fetch_open(ticker: str, target_date: date) -> Optional[float]:
    cache_key = f"{ticker}:{target_date.isoformat()}:open"
    cached    = _cache.get("open", cache_key, _TTL_CLOSE)
    if cached is not None:
        return cached
    for delta in range(7):
        row = _mdb_get(ticker, target_date + timedelta(days=delta))
        if row and row.get("open"):
            price = float(row["open"])
            _cache.put("open", cache_key, price)
            return price
    from_str = target_date.strftime("%Y%m%d")
    to_str   = (target_date + timedelta(days=7)).strftime("%Y%m%d")
    try:
        time.sleep(0.2)
        df = krx.get_market_ohlcv_by_date(from_str, to_str, ticker)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    price    = float(df["시가"].iloc[0])
    hit_date = df.index[0].date() if hasattr(df.index[0], "date") else target_date
    _cache.put("open", cache_key, price)
    _mdb_insert([{"ticker": ticker, "trade_date": hit_date.isoformat(),
                  "open": price, "high": None, "low": None,
                  "close": float(df["종가"].iloc[0]), "volume": None}])
    return price


def _fetch_index_close(target_date: date) -> Optional[float]:
    cache_key = f"kospi_idx:{target_date.isoformat()}"
    cached    = _cache.get("close", cache_key, _TTL_CLOSE)
    if cached is not None:
        return cached
    for delta in range(7):
        row = _mdb_get(_KOSPI_DB_TK, target_date - timedelta(days=delta))
        if row and row.get("close"):
            price = float(row["close"])
            _cache.put("close", cache_key, price)
            return price
    from_str = (target_date - timedelta(days=7)).strftime("%Y%m%d")
    to_str   = target_date.strftime("%Y%m%d")
    try:
        df = krx.get_index_ohlcv_by_date(from_str, to_str, _KOSPI_IDX)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    price    = float(df["종가"].iloc[-1])
    hit_date = df.index[-1].date() if hasattr(df.index[-1], "date") else target_date
    _cache.put("close", cache_key, price)
    _mdb_insert([{"ticker": _KOSPI_DB_TK, "trade_date": hit_date.isoformat(),
                  "open": None, "high": None, "low": None,
                  "close": price, "volume": None}])
    return price


def _fetch_index_open(target_date: date) -> Optional[float]:
    cache_key = f"kospi_idx:{target_date.isoformat()}:open"
    cached    = _cache.get("open", cache_key, _TTL_CLOSE)
    if cached is not None:
        return cached
    for delta in range(7):
        row = _mdb_get(_KOSPI_DB_TK, target_date + timedelta(days=delta))
        if row and row.get("open"):
            price = float(row["open"])
            _cache.put("open", cache_key, price)
            return price
    from_str = target_date.strftime("%Y%m%d")
    to_str   = (target_date + timedelta(days=7)).strftime("%Y%m%d")
    try:
        df = krx.get_index_ohlcv_by_date(from_str, to_str, _KOSPI_IDX)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    price    = float(df["시가"].iloc[0])
    hit_date = df.index[0].date() if hasattr(df.index[0], "date") else target_date
    _cache.put("open", cache_key, price)
    _mdb_insert([{"ticker": _KOSPI_DB_TK, "trade_date": hit_date.isoformat(),
                  "open": price, "high": None, "low": None,
                  "close": float(df["종가"].iloc[0]), "volume": None}])
    return price


def _ret_str(pct: float) -> str:
    return f"+{pct:.1f}%" if pct >= 0 else f"{pct:.1f}%"


def _alpha_str(pct: float) -> str:
    return f"+{pct:.1f}%p" if pct >= 0 else f"{pct:.1f}%p"


# ---------------------------------------------------------------------------
# Navigation (module level so it applies regardless of active tab)
# ---------------------------------------------------------------------------

_perf_nav = consume_nav(NAV_PERF)

# ---------------------------------------------------------------------------
# Page layout
# ---------------------------------------------------------------------------

st.title("📈 Research")
st.caption(f"Engine v{ENGINE_VERSION}")

_dates     = list_snapshot_dates()
_date_strs = [str(d) for d in _dates]

tab_perf, tab_mom, tab_calib = st.tabs([
    "📈 Performance", "📉 Momentum", "🔬 Calibration"
])


# ── Tab 1: Performance ──────────────────────────────────────────────────────
with tab_perf:
    def _render_performance() -> None:
        st.info("판단 성과 관찰용 — 미래 수익률을 예측하지 않습니다.")

        if not _dates:
            st.warning("스냅샷 없음 — 분석 실행 후 사용하세요")
            return

        if len(_dates) < 10:
            st.warning(f"⚠ 스냅샷 {len(_dates)}개 — 샘플 부족, 참고용 only (10개 이상 권장)")

        _snap_ver   = {d: load_full(d).get("rule_version", "?") for d in _dates}
        date_labels = [f"{d}  ({_snap_ver[d]})" for d in _dates]

        col1, col2 = st.columns([2, 1])
        with col1:
            prefill_date = _perf_nav.get("snapshot_date")
            if prefill_date in _date_strs:
                default_idx = _date_strs.index(prefill_date)
            else:
                _usable = [i for i, d in enumerate(_dates) if (date.today() - d).days >= 20]
                default_idx = _usable[-1] if _usable else 0
            snap_label    = st.selectbox("스냅샷 날짜", date_labels, index=default_idx)
            snap_date_str = _date_strs[date_labels.index(snap_label)]
        with col2:
            snap_date   = _dates[_date_strs.index(snap_date_str)]
            max_horizon = (date.today() - snap_date).days
            if max_horizon < 1:
                st.info("오늘 스냅샷입니다 — 수익률 조회는 내일부터 가능합니다")
                return
            horizon = st.number_input("Horizon (일)", min_value=1, max_value=max_horizon,
                                      value=min(30, max_horizon))

        run_btn = st.button("조회", type="primary")
        if not run_btn:
            return

        snap = load_full(snap_date)
        if not snap:
            st.error("스냅샷 로드 실패")
            return

        horizon_date  = snap_date + timedelta(days=int(horizon))
        entries       = snap["entries"]
        _entry_basis  = snap.get("entry_basis", "same_day_open")
        entry_date    = _next_trading_day_approx(snap_date) if _entry_basis == "next_day_open" else snap_date

        entry_kospi   = _fetch_index_open(entry_date)
        horizon_kospi = _fetch_index_close(horizon_date)

        if entry_kospi and horizon_kospi and entry_kospi > 0:
            kospi_return: Optional[float] = round((horizon_kospi / entry_kospi - 1) * 100, 2)
        else:
            kospi_return = None

        def _fetch_entry(entry: dict) -> dict:
            ticker   = entry["ticker"]
            start_px = _fetch_open(ticker, entry_date)
            end_px   = _fetch_close(ticker, horizon_date)
            if start_px is not None and end_px is not None and start_px > 0:
                ret = round((end_px / start_px - 1) * 100, 2)
            else:
                ret = None
            alpha = round(ret - kospi_return, 2) if (ret is not None and kospi_return is not None) else None
            return {**entry, "return_pct": ret, "alpha_pct": alpha}

        with st.spinner(f"시가/종가 조회 중... ({len(entries)}개 종목 × 2 시점)"):
            results = [None] * len(entries)
            with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
                future_to_idx = {pool.submit(_fetch_entry, e): i for i, e in enumerate(entries)}
                for future in as_completed(future_to_idx):
                    results[future_to_idx[future]] = future.result()

        grade_buckets: dict[Grade, list[dict]] = {g: [] for g in _GRADE_ORDER}
        for r in results:
            if r["return_pct"] is not None:
                grade_buckets[Grade(r["grade"])].append(r)

        st.divider()
        st.subheader(f"{snap_date_str} → +{horizon}일 ({horizon_date})")

        if kospi_return is not None:
            st.caption(
                f"📊 KOSPI  {_ret_str(kospi_return)} (동기간)  |  "
                f"entry 시가 {entry_kospi:,.0f} → horizon 종가 {horizon_kospi:,.0f}  "
                f"·  entry_basis: {_entry_basis}"
            )
        elif entry_kospi is None:
            st.caption(f"⚠ KOSPI entry 시가 조회 실패 ({entry_date}) — 알파 계산 불가, 수익률만 표시")
        else:
            st.caption("⚠ KOSPI horizon 종가 조회 실패 — 알파 계산 불가, 수익률만 표시")

        _dist_cols = st.columns(len(_GRADE_ORDER))
        for _ci, _g in enumerate(_GRADE_ORDER):
            _n     = len(grade_buckets[_g])
            _emoji = GRADE_EMOJI.get(_g, "")
            _note  = "" if _n >= _RELIABLE_N else " ⚠"
            _dist_cols[_ci].metric(f"{_emoji} {_g.value.upper()}", f"n={_n}{_note}")

        if any(len(grade_buckets[g]) < _RELIABLE_N for g in _GRADE_ORDER if grade_buckets[g]):
            st.caption("⚠ 표본 부족 등급 (n<30): 수치는 참고용 — CLAUDE.md 통계 원칙")

        _CONF_ORDER = ["high", "medium", "low"]
        _CONF_LABEL = {"high": "HIGH", "medium": "MEDIUM", "low": "LOW"}

        def _grade_summary_header(emoji: str, grade: Grade, items: list[dict], suffix: str) -> None:
            rets   = [r["return_pct"] for r in items]
            alphas = [r["alpha_pct"] for r in items if r["alpha_pct"] is not None]
            avg    = sum(rets) / len(rets)
            wins   = sum(1 for r in rets if r > 0)
            if alphas:
                avg_alpha  = sum(alphas) / len(alphas)
                alpha_wins = sum(1 for a in alphas if a > 0)
                alpha_part = f"알파 {_alpha_str(avg_alpha)} ({alpha_wins}/{len(alphas)})"
            else:
                alpha_part = "알파 —"
            st.subheader(
                f"{emoji} {grade.value.upper()}  —  "
                f"{alpha_part}  |  수익률 {_ret_str(avg)}  승률 {wins}/{len(items)}"
                f"{suffix}"
            )

        def _build_rows(items: list[dict], has_alpha: bool) -> list[dict]:
            sort_key = (lambda r: r["alpha_pct"] if r["alpha_pct"] is not None else float("-inf")) \
                       if has_alpha else (lambda r: r["return_pct"])
            rows = []
            for r in sorted(items, key=sort_key, reverse=True):
                ret   = r["return_pct"]
                alpha = r["alpha_pct"]
                arrow = "▲" if ret > 0 else ("▼" if ret < 0 else "—")
                rows.append({
                    "종목":       f"{_name(r['ticker'])} ({r['ticker']})",
                    "거래":       "📓" if r["ticker"] in _traded_tickers else "",
                    "알파":       _alpha_str(alpha) if alpha is not None else "—",
                    "수익률":     f"{arrow} {_ret_str(ret)}",
                    "점수(당시)": r["normalized_score"],
                })
            return rows

        def _build_csv_rows(items: list[dict], has_alpha: bool, grade_label: str) -> list[dict]:
            sort_key = (lambda r: r["alpha_pct"] if r["alpha_pct"] is not None else float("-inf")) \
                       if has_alpha else (lambda r: r["return_pct"])
            rows = []
            for r in sorted(items, key=sort_key, reverse=True):
                rows.append({
                    "등급":       grade_label,
                    "종목명":     _name(r["ticker"]),
                    "티커":       r["ticker"],
                    "알파(%p)":  r["alpha_pct"],
                    "수익률(%)": r["return_pct"],
                    "점수(당시)": r["normalized_score"],
                    "신뢰도":     r.get("confidence", "high"),
                })
            return rows

        has_alpha    = kospi_return is not None
        all_csv_rows: list[dict] = []

        for grade in _GRADE_ORDER:
            items = grade_buckets[grade]
            if not items:
                continue
            emoji    = GRADE_EMOJI.get(grade, "")
            reliable = len(items) >= _RELIABLE_N
            suffix   = "" if reliable else "  *(표본 부족)*"
            _grade_summary_header(emoji, grade, items, suffix)
            all_csv_rows.extend(_build_csv_rows(items, has_alpha, grade.value.upper()))

            confs_present = {r.get("confidence", "high") for r in items}
            split_by_conf = len(confs_present) > 1

            if split_by_conf:
                conf_buckets: dict[str, list] = {"high": [], "medium": [], "low": []}
                for r in items:
                    conf_buckets.setdefault(r.get("confidence", "high"), []).append(r)
                for conf in _CONF_ORDER:
                    c_items = conf_buckets.get(conf, [])
                    if not c_items:
                        continue
                    c_rets   = [r["return_pct"] for r in c_items]
                    c_alphas = [r["alpha_pct"] for r in c_items if r["alpha_pct"] is not None]
                    c_avg    = sum(c_rets) / len(c_rets)
                    c_wins   = sum(1 for r in c_rets if r > 0)
                    alpha_note = f"  알파 {_alpha_str(sum(c_alphas)/len(c_alphas))}" if c_alphas else ""
                    st.caption(
                        f"신뢰도 {_CONF_LABEL[conf]}  —  수익률 {_ret_str(c_avg)}  "
                        f"승률 {c_wins}/{len(c_items)}{alpha_note}"
                    )
                    st.dataframe(_build_rows(c_items, has_alpha), use_container_width=True, hide_index=True)
            else:
                st.dataframe(_build_rows(items, has_alpha), use_container_width=True, hide_index=True)

        missing = [r["ticker"] for r in results if r["return_pct"] is None]
        if missing:
            st.caption(f"⚠ 가격 데이터 없음: {', '.join(_name(t) for t in missing)}")

        st.caption("Primary KPI = 알파 (종목수익률 − KOSPI수익률). 알파 없음 = kospi_close 미수집 구형 스냅샷.")

        if all_csv_rows:
            import csv, io
            buf    = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=list(all_csv_rows[0].keys()))
            writer.writeheader()
            writer.writerows(all_csv_rows)
            kospi_str = f"{kospi_return:+.2f}%" if kospi_return is not None else "N/A"
            st.divider()
            st.download_button(
                label="📋 CSV 다운로드",
                data=buf.getvalue().encode("utf-8-sig"),
                file_name=f"performance_{snap_date_str}_+{horizon}d.csv",
                mime="text/csv",
                help=f"스냅샷: {snap_date_str}  |  Horizon: +{horizon}일  |  KOSPI: {kospi_str}",
            )

        traded_in_results = [
            r for r in results
            if r.get("return_pct") is not None and r["ticker"] in _traded_tickers
        ]
        if traded_in_results:
            st.divider()
            st.subheader("거래 연결")
            st.caption(f"이 성과 결과에서 거래 기록이 있는 종목: {len(traded_in_results)}개")
            for r in traded_in_results:
                trades = _tj_db.get_by_ticker(r["ticker"])
                if not trades:
                    continue
                most_recent = trades[0]
                side_emoji  = "🟢" if most_recent.side == "BUY" else "🔴"
                alpha_note  = f"  ·  알파 {r['alpha_pct']:+.1f}%p" if r.get("alpha_pct") is not None else ""
                col_a, col_b = st.columns([4, 1])
                col_a.markdown(
                    f"{side_emoji} **{_name(r['ticker'])}** ({r['ticker']})"
                    f"  ·  {most_recent.filled_at or '—'} {most_recent.side}"
                    + alpha_note
                )
                if col_b.button("열기", key=f"perf_td_{r['ticker']}"):
                    nav_to(NAV_TRADE, trade_id=most_recent.trade_id)
                    st.switch_page("pages/5_journal.py")

    _render_performance()


# ── Tab 2: Momentum Stats ───────────────────────────────────────────────────
with tab_mom:
    def _render_momentum() -> None:
        import pandas as pd
        from analyzer.data.momentum_tracker import (
            get_log_dir, get_signal_stats, compute_forward_returns,
            get_transition_stats, get_market_state_distribution,
        )
        from analyzer.data.momentum import MomentumSignal, MOMENTUM_EMOJI, MOMENTUM_LABEL
        from analyzer.data.krx import display_name as _get_name
        from analyzer.data.regime_log import (
            log_regime_event, load_regime_log, get_regime_at, _KNOWN_EVENTS,
        )
        from analyzer.data.screener_snapshot import (
            list_snapshot_dates as _ss_dates, compute_group_returns,
        )

        _SIGNAL_ORDER = [
            MomentumSignal.STRONG,
            MomentumSignal.POSITIVE,
            MomentumSignal.NEUTRAL,
            MomentumSignal.NEGATIVE,
        ]
        _SIG_EMOJI = {s.value: MOMENTUM_EMOJI[s] for s in MomentumSignal}

        _log_dir        = get_log_dir()
        log_files       = sorted(_log_dir.glob("*.jsonl"))
        tracked_tickers = [f.stem for f in log_files]

        if not tracked_tickers:
            st.info("아직 로그가 없습니다. 스크리너를 한 번 실행하면 데이터가 쌓이기 시작합니다.")
            return

        st.caption(f"추적 종목 {len(tracked_tickers)}개 · 로그 경로: `{_log_dir}`")

        ctrl1, ctrl2 = st.columns([2, 2])
        with ctrl1:
            forward_days = st.select_slider(
                "Forward 기간", options=[5, 10, 20, 30, 60, 90], value=30,
            )
        with ctrl2:
            min_sample = st.select_slider(
                "최소 표본 수", options=[1, 3, 5, 10, 20], value=5,
                help="이 값 미만인 조합은 '표본 부족'으로 회색 처리",
            )

        def _reliable(n: int) -> bool:
            return n >= min_sample

        st.divider()

        # 신호별 수익률
        stats        = get_signal_stats(tracked_tickers, forward_days=forward_days)
        combo_stats  = stats.pop("_combo", {})
        bucket_stats = stats.pop("_score_bucket", {})
        mi_stat      = stats.pop("_market_ignore", {})

        total_unresolvable = sum(s.get("unresolvable", 0) for s in stats.values())
        if total_unresolvable > 0:
            st.warning(
                f"⚠️ **Survivorship Bias 주의** — {total_unresolvable}건이 forward 기간 도달 후 가격 미조회. "
                "해당 건은 통계에서 제외됩니다."
            )

        has_any_data = any(s.get("count", 0) > 0 for s in stats.values())

        if has_any_data:
            st.subheader(f"신호별 {forward_days}일 수익률")
            cols = st.columns(len(_SIGNAL_ORDER))
            for col, sig in zip(cols, _SIGNAL_ORDER):
                s     = stats.get(sig.value, {})
                emoji = MOMENTUM_EMOJI[sig]
                label = MOMENTUM_LABEL[sig]
                n     = s.get("count", 0)
                na    = s.get("alpha_count", 0)
                unres = s.get("unresolvable", 0)
                rate  = s.get("unresolvable_rate", 0.0)
                if n > 0 and _reliable(n):
                    avg_alpha = s.get("avg_alpha")
                    col.metric(
                        f"{emoji} {label}",
                        f"평균 {s['avg']:+.1f}%",
                        delta=f"α {avg_alpha:+.1f}%p" if avg_alpha is not None else None,
                        delta_color="normal" if (avg_alpha or 0) >= 0 else "inverse",
                    )
                    sample_line = f"**표본 {n}건**"
                    if na > 0 and na != n:
                        sample_line += f" · α표본 {na}건"
                    sample_line += f" · 승률 {s['win_rate']:.0f}%"
                    col.markdown(sample_line)
                    bias_note = f"  ⚠ 미해소 {unres}건({rate:.0f}%)" if unres else ""
                    col.caption(
                        f"중앙값 {s['median']:+.1f}%"
                        + (f"  α중앙값 {s['median_alpha']:+.1f}%p" if s.get("median_alpha") is not None else "")
                        + f"\n최고 {s['best']:+.1f}%  최저 {s['worst']:+.1f}%"
                        + bias_note
                    )
                elif n > 0:
                    col.metric(f"{emoji} {label}", "표본 부족")
                    col.caption(f"n={n} (최소 {min_sample}건 필요)" + (f"\n미해소 {unres}건" if unres else ""))
                else:
                    col.metric(f"{emoji} {label}", "—")
                    bias = f" (미해소 {unres}건, {rate:.0f}%)" if unres else ""
                    col.caption(f"데이터 없음{bias}")

        # 시장 외면 중 전용
        if mi_stat.get("count", 0) > 0 or mi_stat.get("unresolvable", 0) > 0:
            st.divider()
            st.subheader("🚨 시장 외면 중 (Value 고점 + Momentum 부진)")
            mi_n   = mi_stat.get("count", 0)
            mi_unr = mi_stat.get("unresolvable", 0)
            mi_rate = mi_stat.get("unresolvable_rate", 0.0)
            if _reliable(mi_n):
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("평균 수익률", f"{mi_stat['avg']:+.1f}%" if mi_stat.get("avg") is not None else "—")
                m2.metric("평균 알파",   f"{mi_stat['avg_alpha']:+.1f}%p" if mi_stat.get("avg_alpha") is not None else "—")
                m3.metric("승률",        f"{mi_stat['win_rate']:.0f}%" if mi_stat.get("win_rate") is not None else "—")
                m4.metric("표본",        f"{mi_n}건",
                          delta=f"미해소 {mi_unr}건({mi_rate:.0f}%)" if mi_unr else None,
                          delta_color="off")
                if mi_stat.get("avg") is not None and mi_stat["avg"] > 0:
                    st.info("ℹ️ 시장 외면 중 신호가 양수 수익률 — Value Trap 정의 재검토 필요")
            else:
                st.caption(f"표본 부족 (n={mi_n}, 최소 {min_sample}건 필요)")

        # 신호 전환 성과
        st.divider()
        st.subheader("🔄 신호 전환 성과")
        transition_stats = get_transition_stats(tracked_tickers, forward_days=forward_days)

        if not transition_stats:
            st.caption("아직 신호 전환 데이터가 없습니다.")
        else:
            tr_rows = []
            for key, s in transition_stats.items():
                parts = key.split("→")
                label = f"{_SIG_EMOJI.get(parts[0],'')}{parts[0]} → {_SIG_EMOJI.get(parts[1],'')}{parts[1]}" if len(parts) == 2 else key
                n     = s["count"]
                tr_rows.append({
                    "전환":       label,
                    "표본":       n,
                    "평균수익률": f"{s['avg']:+.1f}%" if (s.get("avg") is not None and _reliable(n)) else ("표본 부족" if not _reliable(n) else "—"),
                    "승률":       f"{s['win_rate']:.0f}%" if (s.get("win_rate") is not None and _reliable(n)) else "—",
                    "평균알파":   f"{s['avg_alpha']:+.1f}%p" if (s.get("avg_alpha") is not None and _reliable(n)) else "—",
                })
            tr_df = pd.DataFrame(tr_rows).sort_values("표본", ascending=False)
            st.dataframe(tr_df, use_container_width=True, hide_index=True)

        # 점수 구간 × 모멘텀
        if bucket_stats:
            st.divider()
            st.subheader("📊 점수 구간 × 모멘텀 신호 수익률")
            _BUCKET_ORDER = ["90+", "80~89", "70~79", "<70"]
            brows = []
            for key, v in bucket_stats.items():
                parts = key.split("+", 1)
                sig, bucket = parts if len(parts) == 2 else (key, "")
                n = v["count"]
                brows.append({
                    "신호":       f"{_SIG_EMOJI.get(sig,'')}{sig}",
                    "점수구간":   bucket,
                    "표본":       n,
                    "평균수익률": f"{v['avg']:+.1f}%" if (v.get("avg") is not None and _reliable(n)) else ("표본 부족" if not _reliable(n) else "—"),
                })
            bdf = pd.DataFrame(brows)
            bdf["_sort_sig"] = bdf["신호"].apply(lambda x: [s.value for s in _SIGNAL_ORDER].index(x.lstrip("🔥✅⚪⚠️").strip()) if x.lstrip("🔥✅⚪⚠️").strip() in [s.value for s in _SIGNAL_ORDER] else 99)
            bdf["_sort_bkt"] = bdf["점수구간"].apply(lambda x: _BUCKET_ORDER.index(x) if x in _BUCKET_ORDER else 99)
            bdf = bdf.sort_values(["_sort_sig", "_sort_bkt"]).drop(columns=["_sort_sig", "_sort_bkt"])
            st.dataframe(bdf, use_container_width=True, hide_index=True)

        # 등급 × 모멘텀 조합
        if combo_stats:
            st.divider()
            st.subheader("🏅 등급 × 모멘텀 조합 수익률")
            combo_df = pd.DataFrame([
                {
                    "조합":       k,
                    "표본":       v["count"],
                    "평균수익률": f"{v['avg']:+.1f}%" if (v.get("avg") is not None and _reliable(v["count"])) else ("표본 부족" if not _reliable(v["count"]) else "—"),
                }
                for k, v in sorted(combo_stats.items(), key=lambda x: -(x[1].get("avg") or -999))
            ])
            st.dataframe(combo_df, use_container_width=True, hide_index=True)

        # 시장 국면 × 신호 교차 성과
        st.divider()
        st.subheader("📌 시장 국면 × 모멘텀 신호 교차 수익률")
        ms_dist = get_market_state_distribution(tracked_tickers, forward_days=forward_days)

        if not ms_dist:
            st.caption("market_state 로그가 없습니다.")
        else:
            pivot_rows = []
            for ms, sig_data in sorted(ms_dist.items()):
                total_n = sum(v["total"] for v in sig_data.values())
                row = {"국면": ms, "총건수": total_n}
                for sig in _SIGNAL_ORDER:
                    d     = sig_data.get(sig.value)
                    emoji = MOMENTUM_EMOJI[sig]
                    if d:
                        n   = d["total"]
                        avg = d.get("avg")
                        if _reliable(n) and avg is not None:
                            row[f"{emoji}"] = f"{avg:+.1f}% (n={n})"
                        elif not _reliable(n):
                            row[f"{emoji}"] = f"n={n} (표본 부족)" if n > 0 else "—"
                        else:
                            row[f"{emoji}"] = f"n={n} (집계 중)"
                    else:
                        row[f"{emoji}"] = "—"
                pivot_rows.append(row)

            st.dataframe(pd.DataFrame(pivot_rows), use_container_width=True, hide_index=True)

            for ms, sig_data in sorted(ms_dist.items()):
                total_n = sum(v["total"] for v in sig_data.values())
                with st.expander(f"📌 {ms}  (총 {total_n}건)", expanded=False):
                    mc = st.columns(len(_SIGNAL_ORDER))
                    for col, sig in zip(mc, _SIGNAL_ORDER):
                        emoji = MOMENTUM_EMOJI[sig]
                        d     = sig_data.get(sig.value)
                        if d:
                            n = d["total"]
                            col.metric(
                                f"{emoji}",
                                f"{n}건 ({n/total_n*100:.0f}%)",
                                delta=f"avg {d['avg']:+.1f}%" if (d.get("avg") is not None and _reliable(n)) else ("표본 부족" if not _reliable(n) else "집계 중"),
                                delta_color="off",
                            )
                        else:
                            col.metric(f"{emoji}", "0건")

        # 종목별 로그
        st.divider()
        st.subheader("종목별 로그")
        selected = st.selectbox(
            "종목 선택",
            tracked_tickers,
            format_func=lambda t: f"{_get_name(t) or t} ({t})",
        )

        if selected:
            records = compute_forward_returns(selected, forward_days=forward_days)
            if not records:
                st.info("로그 없음")
            else:
                _STATUS_ICON = {
                    "resolved":       "✅",
                    "pending":        "⏳",
                    "unresolvable":   "🚨",
                    "no_entry_price": "❓",
                }
                rows = []
                for r in records:
                    prev = r.get("prev_signal") or "—"
                    days_p = r.get("days_since_prev")
                    transition = f"{prev} → {r['signal']}" + (f" ({days_p}d)" if days_p is not None else "")
                    rows.append({
                        "날짜":          r["date"],
                        "신호전환":      transition,
                        "강도":          r.get("strength"),
                        "점수":          r.get("score"),
                        "구간":          r.get("score_bucket") or "—",
                        "등급":          r.get("grade") or "—",
                        "시장국면":      r.get("market_state") or "—",
                        "진입가":        r.get("price_at_signal"),
                        f"{forward_days}일수익률": r.get("forward_return"),
                        "알파":          r.get("forward_alpha"),
                        "상태":          _STATUS_ICON.get(r.get("forward_status", ""), ""),
                    })

                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

                transitions = [r for r in records if r.get("prev_signal") and r["prev_signal"] != r["signal"]]
                if transitions:
                    st.caption(f"신호 전환 {len(transitions)}회")
                    for t in transitions[-5:]:
                        fwd  = f"{t['forward_return']:+.1f}%" if t.get("forward_return") is not None else "—"
                        days = f" ({t['days_since_prev']}d)" if t.get("days_since_prev") else ""
                        st.markdown(f"- {t['date']}  `{t.get('prev_signal','—')} → {t['signal']}`{days}  →  {forward_days}일 후 {fwd}")

        # Daily Screener Snapshot 수익률
        st.divider()
        st.subheader("📦 Daily Screener Snapshot 성과")
        snap_dates = _ss_dates()

        if not snap_dates:
            st.caption("아직 스크리너 스냅샷이 없습니다. 스크리너를 실행하면 자동 저장됩니다.")
        else:
            sg1, sg2, sg3 = st.columns(3)
            with sg1:
                snap_fwd = st.select_slider("Forward 기간", options=[5, 10, 20, 30, 60, 90], value=30, key="snap_fwd")
            with sg2:
                grade_key = st.selectbox("등급", ["strong_buy", "buy", "neutral"], key="snap_grade")
            with sg3:
                top_n_snap = st.select_slider("Top N", options=[5, 10, 20, 30, 0], value=10, key="snap_topn",
                                               format_func=lambda v: "전체" if v == 0 else f"Top {v}")

            snap_rows = []
            for sd in sorted(snap_dates, reverse=True)[:20]:
                r      = compute_group_returns(sd, forward_days=snap_fwd, grade_key=grade_key,
                                               top_n=top_n_snap if top_n_snap > 0 else None)
                regime = get_regime_at(sd)
                snap_rows.append({
                    "날짜":         r["snap_date"],
                    "국면":         r.get("market_state") or regime or "—",
                    "종목수":       r.get("count", "—"),
                    "집계":         r.get("resolved", "—"),
                    f"{snap_fwd}일 avg": f"{r['avg']:+.1f}%" if r.get("avg") is not None else r.get("status", "—"),
                    "승률":         f"{r['win_rate']:.0f}%" if r.get("win_rate") is not None else "—",
                    "엔진":         r.get("engine_version", "—"),
                })

            st.dataframe(pd.DataFrame(snap_rows), use_container_width=True, hide_index=True)

        # Regime Change Log
        st.divider()
        st.subheader("📋 Regime Change Log")

        regime_entries = load_regime_log()
        if regime_entries:
            rdf = pd.DataFrame(regime_entries)[["date", "event", "note"]]
            rdf.columns = ["날짜", "이벤트", "메모"]
            st.dataframe(rdf, use_container_width=True, hide_index=True)
        else:
            st.caption("기록된 시장 국면 이벤트가 없습니다.")

        with st.expander("+ 이벤트 기록", expanded=False):
            rc1, rc2, rc3, rc4 = st.columns([1, 2, 3, 1])
            with rc1:
                reg_date = st.date_input("날짜", value=date.today(), key="reg_date")
            with rc2:
                reg_event = st.selectbox("이벤트", _KNOWN_EVENTS, key="reg_event")
            with rc3:
                reg_note = st.text_input("메모", placeholder="코스피 -8%, 사이드카 발동", key="reg_note")
            with rc4:
                st.write("")
                st.write("")
                if st.button("저장", key="reg_save"):
                    log_regime_event(reg_event, as_of=reg_date, note=reg_note)
                    st.success(f"{reg_date} / {reg_event} 저장됨")
                    st.rerun()

    _render_momentum()


# ── Tab 3: Calibration ──────────────────────────────────────────────────────
with tab_calib:
    from backtests.calibration import get_calibration_health as _get_calib_health
    from constants import ENGINE_VERSION as _EV

    _ch         = _get_calib_health()
    _freeze_end = date(2026, 10, 1)
    _frozen_label = " 🔒 Frozen" if date.today() < _freeze_end else ""

    _vs1, _vs2, _vs3, _vs4, _vs5 = st.columns(5)
    _vs1.metric("Engine",      f"{_EV}{_frozen_label}")
    _vs2.metric("Snapshots",   _ch["n_snapshots"])
    _vs3.metric("20D Results", _ch["perf_counts"].get(20, 0))
    _vs4.metric("60D Results", _ch["perf_counts"].get(60, 0))
    _vs5.metric("Maturity",    _ch["maturity"])

    st.caption(
        f"120D Results: {_ch['perf_counts'].get(120, 0)}  ·  "
        "Mature 조건: 20D / 60D / 120D 각 n ≥ 100  ·  "
        f"Freeze 해제: {_freeze_end}"
    )

    st.divider()

    _cc1, _cc2 = st.columns([1, 4])
    with _cc1:
        if st.button("📊 Calibration 조회", key="calib_fetch", use_container_width=True):
            with st.spinner("데이터 집계 중..."):
                from analyzer.data.krx import prefetch_all_sectors, get_ticker_name as _gtn
                from backtests.calibration import (
                    build_snapshot_distribution, build_alpha_report,
                    build_score_band_distribution, build_score_band_report,
                    build_factor_distribution, build_factor_report,
                    build_component_report, calc_score_alpha_correlation,
                )
                _calib_today = date.today()
                _sector_map  = prefetch_all_sectors(_calib_today)
                _snap_dates  = list_snapshot_dates()
                _ls          = load_full(_snap_dates[-1]) if _snap_dates else {}
                _all_tickers = list({e["ticker"] for e in _ls.get("entries", [])})
                _name_map    = {t: _gtn(t) or "" for t in _all_tickers}

                st.session_state.update({
                    "calib_industry_dist":  build_snapshot_distribution(_sector_map, _name_map),
                    "calib_industry_alpha": build_alpha_report(_sector_map, _name_map),
                    "calib_band_dist":      build_score_band_distribution(),
                    "calib_band_alpha":     build_score_band_report(),
                    "calib_eq_dist":        build_factor_distribution("eq_band"),
                    "calib_eq_alpha":       build_factor_report("eq_band"),
                    "calib_component":      build_component_report(),
                    "calib_correlation":    calc_score_alpha_correlation(),
                    "calib_date":           _calib_today.isoformat(),
                })
            st.rerun()
    with _cc2:
        _calib_date = st.session_state.get("calib_date")
        if _calib_date:
            st.caption(f"기준일: {_calib_date}  ·  최근 스냅샷 분포 + 20D Alpha 집계")
        else:
            st.caption("조회 버튼 클릭 시 Industry / Score Band / Factor 3개 섹션 표시")

    _CALIB_LOADED = bool(st.session_state.get("calib_date"))

    def _render_calib_table(dist_rows, alpha_rows, key_col: str) -> None:
        alpha_map = {r[key_col]: r for r in alpha_rows}
        rows = []
        for item in (dist_rows if isinstance(dist_rows, list) else
                     [{"_k": k, **v} for k, v in dist_rows.items()]):
            key   = item.get(key_col) or item.get("_k")
            dist  = item if key_col in item else dist_rows.get(key, {})
            ar    = alpha_map.get(key, {})
            alpha = ar.get("avg_alpha")
            rows.append({
                "":          key,
                "n":         dist.get("n", 0),
                "Avg Score": dist.get("avg_score"),
                "SB%":       f"{dist.get('sb_pct', 0)}%",
                "20D Alpha": f"{alpha:+.2f}%" if alpha is not None else "—",
                "Status":    ar.get("status", "⏳ Collecting"),
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)

    with st.expander("① Industry Type", expanded=_CALIB_LOADED):
        if not _CALIB_LOADED:
            st.caption("Calibration 조회 버튼을 누르면 업종별 분포가 표시됩니다.")
        else:
            _ind_dist  = st.session_state.get("calib_industry_dist", {})
            _ind_alpha = st.session_state.get("calib_industry_alpha", [])
            if _ind_dist:
                _render_calib_table(
                    [{"type": k, **v} for k, v in _ind_dist.items()],
                    _ind_alpha, key_col="type",
                )
            else:
                st.caption("데이터 없음")

    with st.expander("② Score Band  — 핵심 KPI: 점수가 높을수록 Alpha가 큰가?", expanded=_CALIB_LOADED):
        if not _CALIB_LOADED:
            st.caption("Calibration 조회 버튼을 누르면 Score Band별 분포가 표시됩니다.")
        else:
            _band_dist  = st.session_state.get("calib_band_dist", [])
            _band_alpha = st.session_state.get("calib_band_alpha", [])
            if _band_dist:
                _render_calib_table(_band_dist, _band_alpha, key_col="band")
            else:
                st.caption("데이터 없음")

    with st.expander("③ Factor — EQ Band (Earnings Quality)", expanded=_CALIB_LOADED):
        if not _CALIB_LOADED:
            st.caption("Calibration 조회 버튼을 누르면 EQ Band별 분포가 표시됩니다.")
        else:
            _eq_dist  = st.session_state.get("calib_eq_dist", [])
            _eq_alpha = st.session_state.get("calib_eq_alpha", [])
            if _eq_dist:
                _render_calib_table(_eq_dist, _eq_alpha, key_col="value")
            else:
                st.caption("데이터 없음")

    with st.expander("④ Component — Valuation / Earnings / Risk / Supply", expanded=_CALIB_LOADED):
        if not _CALIB_LOADED:
            st.caption("Calibration 조회 버튼을 누르면 컴포넌트별 Alpha 기여도가 표시됩니다.")
        else:
            _comp_rows = st.session_state.get("calib_component", [])
            if _comp_rows and any(r["high_alpha"] is not None for r in _comp_rows):
                _comp_table = []
                for r in _comp_rows:
                    _comp_table.append({
                        "Component":  r["component"],
                        "n":          r["n_high"] + r["n_low"],
                        "High Alpha": f"{r['high_alpha']:+.2f}%" if r["high_alpha"] is not None else "—",
                        "Low Alpha":  f"{r['low_alpha']:+.2f}%"  if r["low_alpha"]  is not None else "—",
                        "Spread":     f"{r['spread']:+.2f}%p"    if r["spread"]     is not None else "—",
                        "Status":     r["status"],
                    })
                st.dataframe(_comp_table, use_container_width=True, hide_index=True)
                st.caption("High = 해당 컴포넌트 점수 ≥ 중앙값  ·  Low = 중앙값 미만  ·  Spread = High − Low")
            else:
                _comp_table = [{"Component": r["component"], "Status": "⏳ Collecting"} for r in _comp_rows]
                st.dataframe(_comp_table, use_container_width=True, hide_index=True)

    if _CALIB_LOADED:
        _corr = st.session_state.get("calib_correlation", {})
        _rho  = _corr.get("rho")
        _cn   = _corr.get("n", 0)
        if _rho is not None:
            _rho_label = (
                "✅ Positive — 점수↑ Alpha↑ 경향" if _rho >= 0.2
                else "⚠ 거의 없음 — 엔진 재검토 필요" if _rho <= 0.05
                else f"📊 ρ = {_rho}"
            )
            st.info(f"**Engine KPI** — Score vs 20D Alpha 상관계수  ρ = **{_rho}**  ({_rho_label},  n={_cn})")
        else:
            st.caption(f"Engine KPI (ρ): alpha 데이터 부족 — 현재 n={_cn}")

    st.caption(
        "Status: ⏳ Collecting = alpha 없음  ·  📊 Small Sample = n < 30  ·  "
        "✅ Validated = n ≥ 100 + alpha ≥ 0  ·  ⚠ Review = n ≥ 30 + alpha < 0  "
        "— 팩터 보정 결정은 n ≥ 100 + 20D/60D/120D 3개 윈도우 모두 확인 후."
    )
