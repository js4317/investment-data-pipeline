import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True

# 네트워크 기본 timeout — pykrx import 전에 설치 (analyzer/net.py)
from analyzer.net import install_default_http_timeout
install_default_http_timeout()

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env", override=False)

import streamlit as st

_PAGES_DIR = Path(__file__).parent / "pages"

_PAGE_META: dict[int, tuple[str, str, str]] = {
    # num → (group, title, icon)  — 선언 순서가 사이드바 표시 순서
    1: ("운용", "Dashboard",     "📊"),
    2: ("운용", "Portfolio",     "💼"),
    3: ("운용", "Ticker",        "🔎"),
    4: ("운용", "Watchlist",     "📋"),
    5: ("운용", "Journal",       "📓"),
    6: ("분석", "Screener",      "🔭"),
    7: ("분석", "Research",      "📈"),
    8: ("분석", "Snapshots",     "🗂"),
    9: ("도구", "Scoring Guide", "📚"),
}

_GROUP_ORDER   = ["운용", "분석", "도구"]
_DEFAULT_GROUP = "도구"
_DEFAULT_ICON  = "📄"

_NUM_TO_PATH   = {
    int(re.match(r"(\d+)_", p.name).group(1)): p
    for p in _PAGES_DIR.glob("[0-9]*.py")
    if re.match(r"(\d+)_", p.name)
}


def _build_navigation() -> dict[str, list]:
    groups: dict[str, list] = {g: [] for g in _GROUP_ORDER}

    # _PAGE_META 선언 순서대로 추가
    for num, (group, title, icon) in _PAGE_META.items():
        path = _NUM_TO_PATH.get(num)
        if path:
            groups.setdefault(group, []).append(st.Page(str(path), title=title, icon=icon))

    # _PAGE_META에 없는 신규 파일은 기본 그룹에 추가
    for num, path in sorted(_NUM_TO_PATH.items()):
        if num not in _PAGE_META:
            m = re.match(r"\d+_(.+)\.py", path.name)
            title = m.group(1).replace("_", " ").title() if m else path.stem
            groups.setdefault(_DEFAULT_GROUP, []).append(
                st.Page(str(path), title=title, icon=_DEFAULT_ICON)
            )

    return {g: pages for g, pages in groups.items() if pages}


st.set_page_config(page_title="투자 운용 콘솔", page_icon="📊", layout="wide")
st.navigation(_build_navigation()).run()
