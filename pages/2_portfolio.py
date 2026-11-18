from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

_POSITIONS_PATH = Path(__file__).parent.parent / "positions.json"


def _load_raw() -> dict:
    if _POSITIONS_PATH.exists():
        return json.loads(_POSITIONS_PATH.read_text())
    return {"total_value": 0, "positions": {}}


def _save_raw(positions: dict[str, dict], cash: float) -> None:
    total = sum(v["shares"] * v["avg_price"] for v in positions.values()) + cash
    data  = {
        "total_value": round(total),
        "positions":   positions,
    }
    _POSITIONS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2))

import time
from datetime import date, timedelta
from pykrx import stock as krx

from analyzer.portfolio import loader as portfolio_loader
from analyzer.portfolio.rules import compute_advisory_raw
from backtests.snapshot import load_full, list_snapshot_dates
from analyzer.data.krx import display_name as _name
from constants import Grade, Confidence, MarketRegime, GRADE_EMOJI
from analyzer.rules.market_regime import get_market_state
from analyzer.data.momentum import batch_fetch_momentum, mom_strength_display
from analyzer.data.krx import find_tickers_by_name as _find_tickers


def _fetch_current_prices(tickers: list[str]) -> tuple[dict[str, float], dict[str, int]]:
    """오늘 종가 일괄 조회. DB-first: market_ohlcv.db 먼저 확인 후 없는 종목만 pykrx 조회.
    Returns (prices, stats) — stats = {"db": N, "krx": M}."""
    today    = date.today()
    date_str = today.strftime("%Y%m%d")
    prices:  dict[str, float] = {}
    stats:   dict[str, int]   = {"db": 0, "krx": 0}

    # 1단계: DB-first
    missing = []
    for ticker in tickers:
        row = _mdb_get(ticker, today)
        if row and row.get("close"):
            prices[ticker] = float(row["close"])
            stats["db"] += 1
        else:
            missing.append(ticker)

    # 2단계: DB 미보유 종목만 pykrx 조회
    for ticker in missing:
        try:
            time.sleep(0.15)
            df = krx.get_market_ohlcv_by_date(date_str, date_str, ticker)
            if df is not None and not df.empty:
                prices[ticker] = float(df["종가"].iloc[-1])
                stats["krx"] += 1
        except Exception:
            pass

    return prices, stats


from backtests.market_db import get_ohlcv as _mdb_get, init_db as _mdb_init
_mdb_init()
_KOSPI_DB_TK = "1001"


def _fetch_close_on(ticker: str, target: date) -> float | None:
    # DB-first: 최근 7일 역방향 탐색
    for _d in range(7):
        row = _mdb_get(ticker, target - timedelta(days=_d))
        if row and row.get("close"):
            return float(row["close"])
    # fallback: pykrx
    from_str = (target - timedelta(days=7)).strftime("%Y%m%d")
    to_str   = target.strftime("%Y%m%d")
    try:
        time.sleep(0.2)
        df = krx.get_market_ohlcv_by_date(from_str, to_str, ticker)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    return float(df["종가"].iloc[-1])


def _fetch_kospi_on(target: date) -> float | None:
    for _d in range(7):
        row = _mdb_get(_KOSPI_DB_TK, target - timedelta(days=_d))
        if row and row.get("close"):
            return float(row["close"])
    # fallback: pykrx
    from_str = (target - timedelta(days=7)).strftime("%Y%m%d")
    to_str   = target.strftime("%Y%m%d")
    try:
        df = krx.get_index_ohlcv_by_date(from_str, to_str, "1001")
    except Exception:
        return None
    if df is None or df.empty:
        return None
    return float(df["종가"].iloc[-1])


def _find_base_date() -> date | None:
    """DB에서 KOSPI 인덱스 + 종목 데이터가 모두 있는 가장 오래된 거래일을 반환."""
    import sqlite3
    db_path = Path(__file__).parent.parent / "database" / "market_ohlcv.db"
    if not db_path.exists():
        return None
    try:
        conn  = sqlite3.connect(str(db_path))
        # KOSPI 인덱스가 있는 가장 오래된 날짜
        row   = conn.execute(
            "SELECT trade_date FROM market_ohlcv WHERE ticker='1001' "
            "ORDER BY trade_date ASC LIMIT 1"
        ).fetchone()
        conn.close()
        return date.fromisoformat(row[0]) if row else None
    except Exception:
        return None


def _calc_recent_perf(tickers: list[str]) -> dict:
    today     = date.today()
    base_date = _find_base_date()
    if not base_date:
        return {"rows": {}, "kospi_ret": None, "base_date": None, "today": today.isoformat(), "error": "base_date 없음 — DB에 28일 이전 데이터 부족"}

    kospi_now  = _fetch_kospi_on(today)
    kospi_base = _fetch_kospi_on(base_date)
    kospi_ret  = (kospi_now - kospi_base) / kospi_base if (
        kospi_now and kospi_base and kospi_base > 0
    ) else None

    rows = {}
    for ticker in tickers:
        now  = _fetch_close_on(ticker, today)
        base = _fetch_close_on(ticker, base_date)
        if now and base and base > 0:
            ret = (now - base) / base
            rows[ticker] = {
                "ticker_ret": ret,
                "alpha": ret - kospi_ret if kospi_ret is not None else None,
            }

    return {
        "rows":       rows,
        "kospi_ret":  kospi_ret,
        "base_date":  base_date.isoformat(),
        "today":      today.isoformat(),
    }

# ---------------------------------------------------------------------------
# 유틸
# ---------------------------------------------------------------------------

_REGIME_OPTIONS = {
    "RISK_ON":      MarketRegime.RISK_ON,
    "RISK_NEUTRAL": MarketRegime.RISK_NEUTRAL,
    "RISK_OFF":     MarketRegime.RISK_OFF,
}

_STATUS_LABEL = {
    "over":  "⚠️ 초과",
    "ok":    "✅ 적정",
    "watch": "👁 관찰",
    "new":   "💡 진입가능",
    "exit":  "🚫 정리",
}




# ---------------------------------------------------------------------------
# 레이아웃
# ---------------------------------------------------------------------------

import os as _os

st.title("💼 Portfolio")

_is_broker = bool(_os.environ.get("BROKER"))
_force_refresh = bool(st.session_state.pop("broker_force_refresh", False))

portfolio = portfolio_loader.load(force_refresh=_force_refresh)
if not portfolio:
    _broker = _os.environ.get("BROKER", "")
    if _broker:
        st.error("브로커 연동 실패 — 저장된 데이터도 없습니다. Toss WTS → 트레이딩 → Open API에서 토큰 상태를 확인하세요.")
    else:
        st.error("positions.json 없음 또는 로드 실패")
    st.stop()

if portfolio.stale_since:
    st.warning(f"⚠️ 브로커 연결 불가 — 마지막 저장 데이터 표시 중 ({portfolio.stale_since})")

if _is_broker:
    from analyzer.broker import cache as _broker_cache
    _ci = _broker_cache.cache_info()
    _sync_info_col, _refresh_btn_col = st.columns([5, 1])
    with _sync_info_col:
        if _ci:
            _age = int(_ci.age_seconds)
            st.caption(
                f"🔄 마지막 동기화: **{_ci.fetched_at_display}**  ·  "
                f"{_age // 60}분 {_age % 60}초 전  ·  "
                f"캐시 {int(_ci.remaining_seconds)}초 남음"
            )
        else:
            st.caption("🔄 동기화 정보 없음")
    with _refresh_btn_col:
        if st.button("🔄 새로고침", use_container_width=True):
            st.session_state["broker_force_refresh"] = True
            st.rerun()

# --- regime 자동 계산 + 수동 override ---
_auto_state  = get_market_state(date.today())
_auto_regime = _auto_state.regime
_auto_label  = _auto_regime.value.upper()
_heat_emoji  = {"COOL": "🟢", "WARNING": "🟡", "CAUTION": "🟠", "DANGER": "🔴"}.get(
    _auto_state.heat.heat_level.value.upper(), "⚪"
)

_regime_col, _override_col = st.columns([3, 1])
_use_override = _override_col.toggle("수동 지정", value=False, key="regime_override")

if _use_override:
    _keys = list(_REGIME_OPTIONS.keys())
    _default_idx = _keys.index(_auto_label) if _auto_label in _keys else 0
    regime_label = st.selectbox("Market Regime (수동)", _keys,
                                index=_default_idx, key="regime_manual_select")
    regime = _REGIME_OPTIONS[regime_label]
    _regime_col.warning(f"⚙️ 수동 지정: **{regime_label}**  (자동 감지: {_heat_emoji} {_auto_label})")
else:
    regime = _auto_regime
    _regime_col.info(f"시장 국면 자동 감지: {_heat_emoji} **{_auto_label}**  (heat score {_auto_state.heat.heat_score})")

# --- snapshot에서 grade/confidence 조회 ---
dates    = list_snapshot_dates()
snap_map = {}
snap_date_label = None
if dates:
    snap = load_full(dates[-1])
    if snap:
        snap_map         = {e["ticker"]: e for e in snap.get("entries", [])}
        snap_date_label  = snap.get("date", str(dates[-1]))

if snap_date_label:
    st.caption(f"기준 스냅샷: {snap_date_label}")
elif not dates:
    st.warning("스냅샷 없음 — 등급·권장한도 표시 불가")

st.divider()

# --- Portfolio Health ---
from analyzer.portfolio.health import compute_health as _compute_health
from analyzer.risk_radar import read_cache as _rr_cache

_health = _compute_health(portfolio, snap_map, _rr_cache)
if _health:
    _hc1, _hc2, _hc3 = st.columns([1, 4, 1])
    with _hc1:
        st.metric("📊 Portfolio Health", f"{_health.score} / 100")
    with _hc2:
        _mom_emoji = {"positive": "▲", "negative": "▼", "neutral": "━", "N/A": "—"}
        st.markdown(
            f"평균 점수 **{_health.avg_score}**  ·  "
            f"STRONG_BUY **{_health.strong_buy_pct:.0f}%**  ·  "
            f"Risk **{_health.avg_risk}**  ·  "
            f"Momentum **{_mom_emoji.get(_health.avg_momentum, '—')}**  ·  "
            f"현금 **{_health.cash_pct:.1f}%**"
        )
    with _hc3:
        st.markdown(f"### {_health.grade_emoji} {_health.grade_label}")
    for _w in _health.warnings:
        st.warning(f"⚠ {_w}")

st.divider()

# --- 총 노출 요약 ---
held = set(portfolio.positions)

def _exposure(grade_set: set) -> float:
    return sum(
        p.weight_pct for t, p in portfolio.positions.items()
        if snap_map.get(t) and Grade(snap_map[t]["grade"]) in grade_set
    )

buy_exp     = _exposure({Grade.BUY, Grade.STRONG_BUY})
neutral_exp = _exposure({Grade.NEUTRAL})
avoid_exp   = _exposure({Grade.AVOID})
total_exp   = sum(p.weight_pct for p in portfolio.positions.values())

col1, col2, col3, col4 = st.columns(4)
col1.metric("총 투자 노출", f"{total_exp:.1f}%")
col2.metric("BUY+", f"{buy_exp:.1f}%")
col3.metric("NEUTRAL", f"{neutral_exp:.1f}%")
col4.metric("현금", f"{portfolio.cash_pct:.1f}%")

# 현재가 날짜 검증 — 날짜가 바뀌면 자동 무효화 (portfolio_prices_date가 설정된 경우에만)
if st.session_state.get("portfolio_prices_date") not in (None, date.today().isoformat()):
    st.session_state.pop("portfolio_prices",       None)
    st.session_state.pop("portfolio_prices_date",  None)
    st.session_state.pop("portfolio_recent_perf",  None)

# 현재가 있으면 총 수익 요약
current_prices_summary: dict = st.session_state.get("portfolio_prices", {})
if current_prices_summary:
    cost_total = sum(p.shares * p.avg_price for p in portfolio.positions.values())
    val_total  = sum(
        p.shares * current_prices_summary[t]
        for t, p in portfolio.positions.items()
        if t in current_prices_summary
    )
    pnl        = val_total - cost_total
    pnl_pct    = pnl / cost_total * 100 if cost_total > 0 else 0

    def _fmt_won(v: float) -> str:
        sign = "+" if v >= 0 else "-"
        abs_v = abs(v)
        if abs_v >= 100_000_000:
            return f"{sign}{abs_v/100_000_000:.1f}억"
        return f"{sign}{abs_v:,.0f}원"

    rc1, rc2, rc3 = st.columns(3)
    def _fmt_abs(v: float) -> str:
        if abs(v) >= 100_000_000:
            return f"{v/100_000_000:.2f}억원"
        return f"{v:,.0f}원"

    rc1.metric("평가금액",  _fmt_abs(val_total))
    rc2.metric("총 수익금", _fmt_won(pnl), delta=f"{pnl_pct:+.2f}%")
    rc3.metric("투자원금",  _fmt_abs(cost_total))

if avoid_exp > 0:
    st.warning(f"⚠️ AVOID 종목 보유 중: {avoid_exp:.1f}%")

st.divider()

# --- 현재가 로딩 + 20D 성과 ---
pc1, pc2, pc3 = st.columns([1, 1, 3])
with pc1:
    if st.button("📈 현재가 로딩", key="port_load_prices", use_container_width=True):
        with st.spinner("현재가 조회 중..."):
            prices, _s1 = _fetch_current_prices(list(portfolio.positions.keys()))
            _opp_all = sorted(
                [e for e in snap_map.values()
                 if e["ticker"] not in held
                 and Grade(e["grade"]) in (Grade.BUY, Grade.STRONG_BUY)
                 and e.get("executable", False)],
                key=lambda e: e["normalized_score"], reverse=True,
            )
            _sb = [e for e in _opp_all if Grade(e["grade"]) == Grade.STRONG_BUY]
            _b  = [e for e in _opp_all if Grade(e["grade"]) == Grade.BUY]
            opp_tickers = [e["ticker"] for e in _sb + _b[:5]]
            _s2 = {"db": 0, "krx": 0}
            if opp_tickers:
                _opp_prices, _s2 = _fetch_current_prices(opp_tickers)
                prices.update(_opp_prices)
        st.session_state["portfolio_prices"]        = prices
        st.session_state["portfolio_prices_date"]   = date.today().isoformat()
        st.session_state["portfolio_prices_source"] = {
            "db":  _s1["db"]  + _s2["db"],
            "krx": _s1["krx"] + _s2["krx"],
        }
        st.rerun()
with pc2:
    if st.button("📊 20D 참고 성과", key="port_calc_perf", use_container_width=True):
        with st.spinner("최근 20영업일 수익률 조회 중..."):
            try:
                _perf = _calc_recent_perf(list(portfolio.positions.keys()))
            except Exception as _e:
                _perf = {"rows": {}, "kospi_ret": None, "base_date": None,
                         "today": date.today().isoformat(), "error": str(_e)}
        st.session_state["portfolio_recent_perf"] = _perf
        st.rerun()
with pc3:
    price_date = st.session_state.get("portfolio_prices_date")
    if price_date:
        _src = st.session_state.get("portfolio_prices_source", {})
        _src_note = (
            f"  ·  DB {_src['db']}개 / pykrx {_src['krx']}개"
            if _src else ""
        )
        st.caption(
            f"조회 시각: {price_date}  ·  KRX 확정 종가 기준 (장 중에는 전일 종가 반영){_src_note}"
        )

# --- 20D Reference Performance ---
_recent_perf: dict = st.session_state.get("portfolio_recent_perf", {})
if _recent_perf:
    if _recent_perf.get("error"):
        st.warning(f"📊 20D 성과 계산 불가: {_recent_perf['error']}")
    _perf_rows = _recent_perf.get("rows", {})
    _kospi_ret = _recent_perf.get("kospi_ret")
    _base_date = _recent_perf.get("base_date", "")

    st.caption(
        "📌 **Reference Performance** — 현재 보유 종목을 동일비중으로 20영업일 전부터 보유했다면의 가상 수익률. "
        "실제 투자 성과(매수 시점·비중 반영)와 다름. 실제 성과는 v9 거래기록 기반으로 별도 계산 예정."
    )

    if _perf_rows:
        _ref_ret = sum(r["ticker_ret"] for r in _perf_rows.values()) / len(_perf_rows)
        _ref_alpha = (
            sum(r["alpha"] for r in _perf_rows.values() if r["alpha"] is not None) / len(_perf_rows)
            if _kospi_ret is not None else None
        )

        _m1, _m2, _m3 = st.columns(3)
        _m1.metric(
            "Equal-Weight 수익률 (참고)",
            f"{_ref_ret * 100:+.2f}%",
        )
        _m2.metric(
            "KOSPI (동기간)",
            f"{_kospi_ret * 100:+.2f}%" if _kospi_ret is not None else "—",
        )
        _m3.metric(
            "Relative Alpha (참고)",
            f"{_ref_alpha * 100:+.2f}%p" if _ref_alpha is not None else "—",
            delta="Outperform" if (_ref_alpha or 0) > 0 else "Underperform",
            delta_color="normal" if (_ref_alpha or 0) > 0 else "inverse",
        )

        _detail_rows = []
        for _t, _r in _perf_rows.items():
            _detail_rows.append({
                "종목":      f"{_name(_t)} ({_t})",
                "20D 수익률": f"{_r['ticker_ret'] * 100:+.2f}%",
                "KOSPI":     f"{_kospi_ret * 100:+.2f}%" if _kospi_ret is not None else "—",
                "Alpha":     f"{_r['alpha'] * 100:+.2f}%p" if _r["alpha"] is not None else "—",
            })
        st.dataframe(_detail_rows, use_container_width=True, hide_index=True)
        st.caption(f"기간: {_base_date} → {_recent_perf.get('today', '')}  ·  종가 기준  ·  n={len(_perf_rows)}종목")
    else:
        st.info("성과 데이터 조회 실패 — 종가 데이터 없음")

current_prices: dict = st.session_state.get("portfolio_prices", {})

# --- 보유 종목 테이블 ---
st.subheader("보유 종목")

rows       = []
violations = []

for ticker, pos in portfolio.positions.items():
    entry  = snap_map.get(ticker)
    name   = _name(ticker)
    cp     = current_prices.get(ticker)

    # 수익률 계산
    if cp and pos.avg_price > 0:
        ret_pct  = (cp - pos.avg_price) / pos.avg_price * 100
        ret_str  = f"{ret_pct:+.1f}%"
        cur_val  = pos.shares * cp
        cur_str  = f"{cp:,.0f}원"
    else:
        ret_str  = "—"
        cur_str  = "—"
        cur_val  = pos.shares * pos.avg_price

    if entry is None:
        rows.append({
            "종목":     f"{name} ({ticker})",
            "등급":     "—",
            "평단":     f"{pos.avg_price:,.0f}원",
            "현재가":   cur_str,
            "수익률":   ret_str,
            "비중":     f"{pos.weight_pct:.1f}%",
            "권장한도": "—",
            "상태":     "⚠️ 스냅샷 없음",
        })
        continue

    grade = Grade(entry["grade"])
    conf  = Confidence(entry["confidence"])
    adv   = compute_advisory_raw(ticker, grade, conf, regime, portfolio)
    emoji = GRADE_EMOJI.get(grade, "")

    rec_str    = f"{adv.recommended_max_pct:.1f}%" if adv else "—"
    status_str = _STATUS_LABEL.get(adv.status, adv.status) if adv else "—"

    rows.append({
        "종목":     f"{name} ({ticker})",
        "등급":     f"{emoji} {grade.value.upper()}",
        "평단":     f"{pos.avg_price:,.0f}원",
        "현재가":   cur_str,
        "수익률":   ret_str,
        "비중":     f"{pos.weight_pct:.1f}%",
        "권장한도": rec_str,
        "상태":     status_str,
    })

    if adv and adv.status == "over":
        violations.append((name, ticker, pos.weight_pct, adv))

st.dataframe(rows, use_container_width=True, hide_index=True)

# --- 비중 초과 경고 + 권장한도 맞추기 ---
if violations:
    st.divider()
    st.subheader("⚠️ 비중 초과")
    for name, ticker, current_pct, adv in violations:
        delta      = current_pct - adv.recommended_max_pct
        pos        = portfolio.positions[ticker]
        target_w   = adv.recommended_max_pct / 100
        target_sh  = int(target_w * portfolio.total_value / pos.avg_price)

        col_msg, col_btn = st.columns([4, 1])
        with col_msg:
            st.error(
                f"**{name} ({ticker})**  "
                f"현재 {current_pct:.1f}% / 권장한도 {adv.recommended_max_pct:.1f}%  "
                f"(+{delta:.1f}%p 초과)"
            )
        with col_btn:
            if st.button(f"→ {target_sh}주로 조정", key=f"adj_{ticker}"):
                raw   = _load_raw()
                raw["positions"][ticker]["shares"] = target_sh
                total = sum(v["shares"] * v["avg_price"]
                            for v in raw["positions"].values()) + max(
                    0, raw["total_value"] - sum(
                        v["shares"] * v["avg_price"]
                        for v in raw["positions"].values()
                    )
                )
                raw["total_value"] = round(total)
                _save_raw(
                    raw["positions"],
                    max(0, raw["total_value"] - sum(
                        v["shares"] * v["avg_price"]
                        for v in raw["positions"].values()
                    )),
                )
                st.success(f"✅ {name} → {target_sh}주 저장 완료")
                st.session_state.pop("edit_positions", None)
                st.rerun()

# --- 매도 체크리스트 ---
st.divider()
st.subheader("🔍 Position Status")

from analyzer.rules.sell_checklist import build_position_status as _build_position_status

_STATE_DOT = {"alert": "🔴", "info": "🟡", "ok": "🟢", "unknown": "⚪"}

_cl_any = False
for _cl_ticker in portfolio.positions:
    _cl_entry = snap_map.get(_cl_ticker)
    if _cl_entry is None:
        continue
    _cl_any = True
    _cl      = _build_position_status(_cl_ticker, _cl_entry, dates)
    _cl_name = _name(_cl_ticker)

    _hdr_parts = []
    if _cl.critical_alerts:
        _hdr_parts.append(f"🔴 구조 변화 {_cl.critical_alerts}건")
    if _cl.context_alerts:
        _hdr_parts.append(f"🟡 맥락 변화 {_cl.context_alerts}건")
    if not _hdr_parts:
        _hdr_parts.append("🟢 이상 없음")
    _cl_header = (
        f"{_cl.verdict_emoji} **{_cl_name} ({_cl_ticker})**"
        f"  ·  {'  /  '.join(_hdr_parts)}"
        f"  ·  {_cl.verdict_label}"
    )

    with st.expander(_cl_header, expanded=(_cl.verdict != "GREEN")):
        st.markdown("**구조적 변화**")
        for _item in _cl.critical_items:
            st.markdown(f"{_STATE_DOT[_item.state]} **{_item.label}**  \n{_item.detail}")

        st.markdown("---")
        st.markdown("**시장 맥락**")
        for _item in _cl.context_items:
            st.markdown(f"{_STATE_DOT[_item.state]} **{_item.label}**  \n{_item.detail}")

if not _cl_any:
    st.info("스냅샷 데이터가 없는 종목만 보유 중입니다")

# --- 점수 변화 ---
st.divider()
st.subheader("📊 점수 변화")

from analyzer.tracking.score_tracker import batch_compute_deltas as _batch_deltas

_held_tickers = list(portfolio.positions.keys())
_delta_map    = _batch_deltas(_held_tickers, dates, lookback_days=7)

if not _delta_map:
    st.info("스냅샷 2개 이상 필요 (비교 불가)")
else:
    _from_date_label = next(iter(_delta_map.values())).from_date.strftime("%m/%d")
    _to_date_label   = next(iter(_delta_map.values())).to_date.strftime("%m/%d")
    st.caption(f"비교 기간: {_from_date_label} → {_to_date_label}")

    _delta_rows = []
    for _dt in _held_tickers:
        _d = _delta_map.get(_dt)
        if _d is None:
            continue
        _sign = f"{_d.score_delta:+d}" if _d.score_delta != 0 else "━0"
        _delta_rows.append({
            "종목":        f"{_name(_dt)} ({_dt})",
            "현재 점수":   _d.to_score,
            "이전 점수":   _d.from_score,
            "변화":        f"{_d.direction_emoji} {_sign}",
            "주요 원인":   _d.explain(),
        })

    if _delta_rows:
        st.dataframe(_delta_rows, use_container_width=True, hide_index=True)

        _grade_changed = [_name(t) for t, d in _delta_map.items() if d.grade_changed]
        if _grade_changed:
            st.warning(f"⚠️ 등급 변화 감지: {', '.join(_grade_changed)}")

# --- 미보유 BUY+ 기회 ---
st.divider()
st.subheader("💡 미보유 BUY+ 종목")

opportunities = sorted(
    [e for e in snap_map.values()
     if e["ticker"] not in held
     and Grade(e["grade"]) in (Grade.BUY, Grade.STRONG_BUY)
     and e.get("executable", False)],  # 실행 가능한 진입 후보만
    key=lambda e: e["normalized_score"],
    reverse=True,
)

if not opportunities:
    st.info("미보유 BUY+ 종목 없음")
else:
    _strong = [e for e in opportunities if Grade(e["grade"]) == Grade.STRONG_BUY]
    _buy    = [e for e in opportunities if Grade(e["grade"]) == Grade.BUY]
    _opp_slice = _strong + _buy[:5]

    # 스냅샷에 모멘텀 없는 종목만 live fetch
    _need_live = [(e["ticker"], e["normalized_score"]) for e in _opp_slice
                  if e.get("mom_signal") is None]
    _live_mom  = batch_fetch_momentum(_need_live, date.today(), max_workers=5) if _need_live else {}

    opp_rows = []
    for entry in _opp_slice:
        ticker = entry["ticker"]
        grade  = Grade(entry["grade"])
        conf   = Confidence(entry["confidence"])
        adv    = compute_advisory_raw(ticker, grade, conf, regime, portfolio)
        emoji  = GRADE_EMOJI.get(grade, "")
        cp     = current_prices.get(ticker)

        if adv and cp and cp > 0:
            max_val     = portfolio.total_value * adv.recommended_max_pct / 100
            max_shares  = int(max_val / cp)
            actual_cost = max_shares * cp
            actual_pct  = actual_cost / portfolio.total_value * 100
            price_str   = f"{cp:,.0f}원"
            shares_str  = f"{max_shares:,}주  ({actual_pct:.1f}%)"
            cost_str    = (f"{actual_cost / 100_000_000:.2f}억"
                           if actual_cost >= 100_000_000
                           else f"{actual_cost:,.0f}원")
        else:
            price_str  = "—"
            shares_str = "현재가 로딩 필요" if cp is None else "—"
            cost_str   = "—"

        # 스냅샷 모멘텀 우선, 없으면 live fetch 결과 사용
        _sig = entry.get("mom_signal")
        _str = entry.get("mom_strength")
        if _sig is None:
            _live = _live_mom.get(ticker)
            if _live:
                _sig = _live.signal.value
                _str = _live.strength
                _r1  = f"{_live.rel_1m:+.1f}" if _live.rel_1m is not None else "—"
                _r3  = f"{_live.rel_3m:+.1f}" if _live.rel_3m is not None else "—"
                _r6  = f"{_live.rel_6m:+.1f}" if _live.rel_6m is not None else "—"
            else:
                _sig = _str = None
        else:
            _r1 = f"{entry['mom_rel_1m']:+.1f}" if entry.get("mom_rel_1m") is not None else "—"
            _r3 = f"{entry['mom_rel_3m']:+.1f}" if entry.get("mom_rel_3m") is not None else "—"
            _r6 = f"{entry['mom_rel_6m']:+.1f}" if entry.get("mom_rel_6m") is not None else "—"

        if _sig and _str is not None:
            mom_str = f"{mom_strength_display(_sig, _str)}  {_r1}/{_r3}/{_r6}"
        else:
            mom_str = "—"

        opp_rows.append({
            "종목":        f"{_name(ticker)} ({ticker})",
            "등급":        f"{emoji} {grade.value.upper()}",
            "점수":        entry["normalized_score"],
            "모멘텀":      mom_str,
            "현재가":      price_str,
            "최대비중":    f"{adv.recommended_max_pct:.1f}%" if adv else "—",
            "최대 매수":   shares_str,
            "예상 매입금": cost_str,
        })
    st.dataframe(opp_rows, use_container_width=True, hide_index=True)

st.caption("현금 = 100 - sum(position weight). positions.json 미기재분 포함.")

# --- 브로커 모드 현금 입력 ---
if _is_broker:
    from analyzer.portfolio.cash import load_cash_krw as _load_cash, save_cash_krw as _save_cash
    st.divider()
    st.subheader("💰 현금 잔고")
    st.caption("Toss API는 현금 조회를 미지원합니다. 토스 앱 > 계좌 > 주문가능금액을 직접 입력하세요.")
    _cash_col, _btn_col = st.columns([3, 1])
    with _cash_col:
        _cash_input = st.number_input(
            "현금 (원)",
            value=_load_cash(),
            min_value=0.0,
            step=10_000.0,
            format="%.0f",
            label_visibility="collapsed",
        )
    with _btn_col:
        if st.button("저장", use_container_width=True):
            _save_cash(_cash_input)
            st.success("저장됨")
            st.rerun()

# ---------------------------------------------------------------------------
# 포지션 편집 — BROKER 모드에서는 Toss API가 source of truth이므로 표시 안 함
# ---------------------------------------------------------------------------
if _is_broker:
    st.stop()

st.divider()
st.subheader("✏️ 포지션 편집")

raw = _load_raw()
raw_positions: dict = raw.get("positions", {})
raw_total: float    = float(raw.get("total_value", 0))
raw_cash: float     = max(0.0, raw_total - sum(
    v["shares"] * v["avg_price"] for v in raw_positions.values()
))

# session_state 초기화 — portfolio.positions 순서 기준 (보유 종목 표와 동일)
if "edit_positions" not in st.session_state:
    ordered: dict = {}
    for t in portfolio.positions:          # portfolio 뷰와 동일한 순서
        if t in raw_positions:
            ordered[t] = {"shares": raw_positions[t]["shares"],
                          "avg_price": raw_positions[t]["avg_price"]}
    for t, v in raw_positions.items():     # positions.json에만 있는 항목 추가
        if t not in ordered:
            ordered[t] = {"shares": v["shares"], "avg_price": v["avg_price"]}
    st.session_state["edit_positions"] = ordered
    st.session_state["edit_cash"]       = raw_cash

edit_pos: dict = st.session_state["edit_positions"]

# 헤더
hc1, hc2, hc3, hc4 = st.columns([3, 2, 2, 1])
hc1.markdown("**종목**")
hc2.markdown("**주수**")
hc3.markdown("**평균단가 (원)**")
hc4.markdown("")

# --- 현재 포지션 편집 테이블 ---
to_delete = []
for ticker, vals in list(edit_pos.items()):
    c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
    with c1:
        st.markdown(f"**{_name(ticker)}**  \n`{ticker}`")
    with c2:
        new_shares = st.number_input("주수", value=int(vals["shares"]),
                                     min_value=1, step=1, key=f"s_{ticker}",
                                     label_visibility="collapsed")
        edit_pos[ticker]["shares"] = new_shares
    with c3:
        new_price = st.number_input("평균단가", value=float(vals["avg_price"]),
                                    min_value=1.0, step=100.0, key=f"p_{ticker}",
                                    label_visibility="collapsed")
        edit_pos[ticker]["avg_price"] = new_price
    with c4:
        if st.button("삭제", key=f"del_{ticker}"):
            to_delete.append(ticker)

for t in to_delete:
    del edit_pos[t]
if to_delete:
    st.rerun()

st.divider()

# --- 종목 추가 ---
with st.expander("➕ 종목 추가"):
    a1, a2, a3, a4 = st.columns([2, 2, 2, 1])
    with a1:
        new_ticker = st.text_input("종목코드 또는 종목명", placeholder="005930 또는 삼성전자", key="add_ticker")
    with a2:
        new_shares_add = st.number_input("주수", min_value=1, step=1, value=1, key="add_shares")
    with a3:
        new_price_add = st.number_input("평균단가", min_value=1.0, step=100.0,
                                        value=50000.0, key="add_price")
    with a4:
        st.write("")
        if st.button("추가", key="add_btn"):
            raw_input = new_ticker.strip()
            if not raw_input:
                st.warning("종목코드 또는 종목명을 입력하세요")
            else:
                _snap_extra = {e["ticker"]: _name(e["ticker"]) for e in snap_map.values()}
                if raw_input.isdigit() and len(raw_input) == 6:
                    resolved = raw_input
                else:
                    matches = _find_tickers(raw_input, extra=_snap_extra)
                    if len(matches) == 1:
                        resolved = matches[0]
                    elif len(matches) > 1:
                        st.warning(f"동명 종목 {len(matches)}개: {', '.join(matches)}")
                        resolved = None
                    else:
                        st.error(f"'{raw_input}' 종목을 찾을 수 없습니다")
                        resolved = None

                if resolved:
                    if resolved in edit_pos:
                        st.warning(f"이미 존재하는 종목 ({resolved})")
                    else:
                        edit_pos[resolved] = {"shares": new_shares_add, "avg_price": new_price_add}
                        st.rerun()

# --- 현금 입력 ---
edit_cash = st.number_input(
    "현금 (원)",
    value=float(st.session_state["edit_cash"]),
    min_value=0.0,
    step=10000.0,
    key="cash_input",
)
st.session_state["edit_cash"] = edit_cash

# 총자산 preview
pos_value   = sum(v["shares"] * v["avg_price"] for v in edit_pos.values())
total_value = pos_value + edit_cash
st.caption(f"총자산 (저장 전 미리보기): {total_value:,.0f}원  |  포지션 {pos_value:,.0f}원  |  현금 {edit_cash:,.0f}원")

# --- 저장 ---
save_col, reset_col = st.columns([1, 4])
with save_col:
    if st.button("💾 저장", type="primary", use_container_width=True):
        if total_value <= 0:
            st.error("총자산이 0 이하입니다")
        else:
            _save_raw(edit_pos, edit_cash)
            st.session_state.pop("edit_positions", None)  # 저장 후 세션 초기화
            st.success("✅ positions.json 저장 완료")
            st.rerun()
with reset_col:
    if st.button("↩ 초기화", use_container_width=False):
        st.session_state.pop("edit_positions", None)
        st.session_state.pop("edit_cash", None)
        st.rerun()
