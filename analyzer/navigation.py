from __future__ import annotations

from typing import Optional

import streamlit as st

_KEY = "navigation_request"

NAV_TICKER    = "ticker"
NAV_TRADE     = "trade_detail"
NAV_PERF      = "performance"
NAV_WATCHLIST = "watchlist"


def nav_to(
    target: str,
    *,
    ticker: Optional[str] = None,
    trade_id: Optional[str] = None,
    snapshot_date: Optional[str] = None,
    highlight: Optional[str] = None,
    filter_preset: Optional[list] = None,
) -> None:
    """Navigation intent를 session_state에 저장한다.
    switch_page() 호출은 반드시 이 함수 호출 직후에 한다."""
    st.session_state[_KEY] = {k: v for k, v in {
        "target":        target,
        "ticker":        ticker,
        "trade_id":      trade_id,
        "snapshot_date": snapshot_date,
        "highlight":     highlight,
        "filter_preset": filter_preset,
    }.items() if v is not None}


def consume_nav(expected_target: str) -> dict:
    """자신의 target과 일치하면 pop 후 반환. 불일치 또는 없으면 빈 dict."""
    req = st.session_state.get(_KEY, {})
    if req.get("target") == expected_target:
        st.session_state.pop(_KEY, None)
        return req
    return {}
