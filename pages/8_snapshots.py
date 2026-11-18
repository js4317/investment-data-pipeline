from __future__ import annotations

import re
import sys
from datetime import date, timedelta
from pathlib import Path
from packaging.version import Version

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtests.snapshot import (
    load_full, list_snapshot_dates,
    SCHEMA_HAS_VALUATION_RAW, SCHEMA_HAS_SECTOR_RELATIVE,
    SCHEMA_HAS_EQ_BAND, SCHEMA_HAS_PRICE_CONTEXT, SCHEMA_HAS_RISK_EARNINGS_RAW,
)
from analyzer.data.krx import display_name as _name
from analyzer.navigation import nav_to, NAV_PERF, NAV_TRADE
from constants import Grade, GRADE_EMOJI

try:
    from analyzer.trade_journal import db as _tj_db
    _tj_db.init_db()
    _traded_tickers: set[str] = {r.ticker for r in _tj_db.get_all()}
except Exception:
    _traded_tickers = set()

# ---------------------------------------------------------------------------
# Inspector helpers
# ---------------------------------------------------------------------------

_MIN_FULL_VERSION = "7.2.0"

_HEALTH_THRESHOLDS = {"mom_coverage": 0.8}

_DIFF_PRESETS = {"최근": 1, "1주": 7, "1개월": 30, "3개월": 90}

_RESEARCH_THRESHOLDS = {"momentum": 30, "institution": 30, "combo": 30}

_HEALTH_WEIGHTS = {
    "schema":        20,
    "engine":        20,
    "market_cap":    20,
    "trading_value": 20,
    "mom":           20,
}


def _pct_str(n: int, total: int) -> str:
    if total == 0:
        return "—"
    return f"{n} ({n / total * 100:.0f}%)"


def _pct_float(n: int, total: int) -> float:
    return n / total * 100 if total else 0.0


def _is_old_engine(rule_version: str) -> bool:
    try:
        return Version(rule_version) < Version(_MIN_FULL_VERSION)
    except Exception:
        return True


def _readiness(count: int, target: int) -> str:
    if count >= target:
        return "✅ 가능"
    if count > 0:
        filled = min(10, int(count / target * 10))
        pct    = count / target * 100
        bar    = "█" * filled + "░" * (10 - filled)
        return f"🟡 가능 (표본 부족)  {bar} {pct:.0f}%  n={count}/{target}"
    return "❌ 불가"


def _build_research_checks(info: dict, n: int) -> list[tuple]:
    fp    = info["foreign_positive"]
    pp    = info["pension_positive"]
    combo = info["fp_and_pp"]
    return [
        (
            "Alpha 계산",
            "kospi_close 존재 → 종목수익률 − KOSPI수익률",
            "bool", bool(info["kospi_close"]), None,
        ),
        (
            "모멘텀 검증 (Q3)",
            f"mom_available {info['mom_available']}/{n} — Strong/Positive/Negative 알파 비교",
            "count", info["mom_available"], "momentum",
        ),
        (
            "기관 세부 분석 (Q2)",
            f"pension_net {info['pension_net']}/{n} — 연기금 / 투신 / 금융투자",
            "count", info["pension_net"], "institution",
        ),
        (
            "외인+연기금 조합 (Q2)",
            f"foreign+ {fp}/{n}  pension+ {pp}/{n}  교집합(동시매수) {combo}/{n}",
            "count", combo, "combo",
        ),
        (
            "시총/거래대금 팩터",
            f"market_cap {info['market_cap']}/{n}  trading_value {info['trading_value']}/{n}",
            "bool", info["market_cap"] == n and info["trading_value"] == n, None,
        ),
    ]


_CHANGELOG_SECTIONS = {"Philosophy", "Breaking Changes", "Added", "Changed", "Fixed", "Calibration Result"}


def _parse_changelog_summaries() -> dict[str, str]:
    changelog = Path(__file__).parent.parent / "docs" / "CHANGELOG.md"
    if not changelog.exists():
        return {}
    text       = changelog.read_text(encoding="utf-8")
    result     = {}
    current    = None
    in_section = False
    for line in text.splitlines():
        m = re.match(r"^## v(\S+)", line)
        if m:
            current    = m.group(1)
            in_section = False
            continue
        if current and not in_section:
            sec = re.match(r"^### (.+)", line)
            if sec and sec.group(1).strip() in _CHANGELOG_SECTIONS:
                in_section = True
                continue
        if current and in_section and line.startswith("- "):
            clean = re.sub(r"\*\*(.+?)\*\*", r"\1", line[2:]).split(" — ")[0]
            result[current] = clean[:60] + ("…" if len(clean) > 60 else "")
            current    = None
            in_section = False
    return result


def _inspect(snap: dict) -> dict:
    entries = snap.get("entries", [])
    n       = len(entries)
    if n == 0:
        return {}

    schema      = snap.get("snapshot_schema", 1)
    kospi_close = snap.get("kospi_close")

    def _count(field: str) -> int:
        return sum(1 for e in entries if e.get(field) is not None)

    def _count_val(field: str, val) -> int:
        return sum(1 for e in entries if e.get(field) == val)

    mom_available = sum(
        1 for e in entries
        if e.get("mom_signal") is not None and e.get("mom_signal") != "unavailable"
    )

    grade_dist   = {g: _count_val("grade", g) for g in ("strong_buy", "buy", "neutral", "avoid")}
    eq_band_dist = {b: _count_val("eq_band", b) for b in ("GOOD", "WARNING", "POOR", "VERY_POOR")}
    fp_and_pp    = sum(
        1 for e in entries
        if e.get("foreign_positive") is True and e.get("pension_positive") is True
    )

    return {
        "date":                       snap.get("date"),
        "rule_version":               snap.get("rule_version", "?"),
        "snapshot_schema":            schema,
        "n":                          n,
        "n_total":                    snap.get("n_total"),
        "universe":                   snap.get("universe"),
        "universe_label":             snap.get("universe_label"),
        "kospi_close":                kospi_close,
        "mom_available":              mom_available,
        "market_cap":                 _count("market_cap"),
        "trading_value":              _count("trading_value"),
        "supply_lookback_days":       _count("supply_lookback_days"),
        "foreign_net":                _count("foreign_net"),
        "institution_net":            _count("institution_net"),
        "pension_net":                _count("pension_net"),
        "investment_trust_net":       _count("investment_trust_net"),
        "financial_investment_net":   _count("financial_investment_net"),
        "private_fund_net":           _count("private_fund_net"),
        "insurance_net":              _count("insurance_net"),
        "foreign_positive":           _count_val("foreign_positive", True),
        "institution_positive":       _count_val("institution_positive", True),
        "pension_positive":           _count_val("pension_positive", True),
        "per":                        _count("per"),
        "pbr":                        _count("pbr"),
        "roe":                        _count("roe"),
        "div_yield":                  _count("div_yield"),
        "foreign_ownership_pct":      _count("foreign_ownership_pct"),
        "grade_dist":                 grade_dist,
        "eq_band_dist":               eq_band_dist,
        "fp_and_pp":                  fp_and_pp,
    }


def _health_score(info: dict, max_schema: int) -> int:
    n     = info["n"]
    score = 0
    if info["snapshot_schema"] >= max_schema:
        score += _HEALTH_WEIGHTS["schema"]
    if not _is_old_engine(info["rule_version"]):
        score += _HEALTH_WEIGHTS["engine"]
    if n > 0 and info["market_cap"] / n >= 0.95:
        score += _HEALTH_WEIGHTS["market_cap"]
    if n > 0 and info["trading_value"] / n >= 0.95:
        score += _HEALTH_WEIGHTS["trading_value"]
    buy_plus = info["grade_dist"].get("buy", 0) + info["grade_dist"].get("strong_buy", 0)
    if buy_plus > 0 and info["mom_available"] / buy_plus >= _HEALTH_THRESHOLDS["mom_coverage"]:
        score += _HEALTH_WEIGHTS["mom"]
    return score


# ---------------------------------------------------------------------------
# Page layout
# ---------------------------------------------------------------------------

st.title("🗂 Snapshots")

dates = list_snapshot_dates()
if not dates:
    st.info("스냅샷 없음 — 분석 실행 후 사용하세요")
    st.stop()

tab_browser, tab_inspector = st.tabs(["🗂 Browser", "🔬 Inspector"])


# ── Tab 1: Browser ──────────────────────────────────────────────────────────
with tab_browser:
    st.subheader(f"스냅샷 목록 ({len(dates)}개)")

    meta_rows = []
    for d in reversed(dates):
        snap = load_full(d)
        if not snap:
            continue
        entries    = snap.get("entries", [])
        grade_dist = {}
        for e in entries:
            g = e.get("grade", "?")
            grade_dist[g] = grade_dist.get(g, 0) + 1
        meta_rows.append({
            "날짜":       snap["date"],
            "룰 버전":    snap.get("rule_version", "?"),
            "종목 수":    len(entries),
            "BUY+":      grade_dist.get("strong_buy", 0) + grade_dist.get("buy", 0),
            "NEUTRAL":   grade_dist.get("neutral", 0),
            "AVOID":     grade_dist.get("avoid", 0),
            "실행가능":  sum(1 for e in entries if e.get("executable")),
        })

    st.dataframe(meta_rows, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("스냅샷 상세")

    date_map      = {str(d): d for d in reversed(dates)}
    date_strs     = list(date_map.keys())
    selected_str  = st.selectbox("날짜 선택", date_strs, index=0, key="browser_date")
    selected_date = date_map[selected_str]

    snap = load_full(selected_date)
    if not snap:
        st.error("스냅샷 로드 실패")
    else:
        entries = snap.get("entries", [])
        st.caption(f"룰 버전: {snap.get('rule_version', '?')}  |  종목 {len(entries)}개")

        grade_filter = st.radio(
            "등급 필터",
            ["전체", "BUY+", "NEUTRAL", "AVOID"],
            horizontal=True,
        )

        _GRADE_FILTER = {
            "전체":    None,
            "BUY+":   {Grade.STRONG_BUY, Grade.BUY},
            "NEUTRAL": {Grade.NEUTRAL},
            "AVOID":   {Grade.AVOID},
        }
        grade_set = _GRADE_FILTER[grade_filter]

        filtered        = [e for e in entries if grade_set is None or Grade(e["grade"]) in grade_set]
        filtered_sorted = sorted(filtered, key=lambda e: e["normalized_score"], reverse=True)

        detail_rows = []
        for e in filtered_sorted:
            grade = Grade(e["grade"])
            emoji = GRADE_EMOJI.get(grade, "")
            detail_rows.append({
                "종목":     f"{_name(e['ticker'])} ({e['ticker']})",
                "등급":     f"{emoji} {e['grade'].upper()}",
                "거래":     "📓" if e["ticker"] in _traded_tickers else "",
                "점수":     e["normalized_score"],
                "신뢰도":   e.get("confidence", "—").upper(),
                "실행가능": "✅" if e.get("executable") else "🚫",
                "밸류":     e.get("valuation_score", "—"),
                "실적":     e.get("earnings_score", "—"),
                "리스크":   e.get("risk_score", "—"),
                "수급":     e.get("supply_score", "—"),
            })

        if detail_rows:
            st.dataframe(detail_rows, use_container_width=True, hide_index=True)
        else:
            st.info("해당 등급 종목 없음")

        st.divider()
        st.subheader("Quick Actions")

        st.markdown("**이 날짜 성과 보기**")
        if st.button("→ Research / Performance로 이동", use_container_width=True):
            nav_to(NAV_PERF, snapshot_date=selected_str)
            st.switch_page("pages/7_research.py")

        traded_here = [e for e in filtered_sorted if e["ticker"] in _traded_tickers]
        if traded_here:
            st.divider()
            st.markdown("**이 스냅샷 종목 중 거래 기록 있는 종목**")
            ticker_map = {e["ticker"]: f"{_name(e['ticker'])} ({e['ticker']})" for e in traded_here}
            sel_t = st.selectbox(
                "종목 선택",
                list(ticker_map.keys()),
                format_func=lambda t: ticker_map[t],
                key="browser_trade_ticker",
            )
            if st.button("📓 Journal에서 열기", use_container_width=True, key="browser_trade_open"):
                trades = _tj_db.get_by_ticker(sel_t)
                if trades:
                    nav_to(NAV_TRADE, trade_id=trades[0].trade_id)
                    st.switch_page("pages/5_journal.py")


# ── Tab 2: Inspector ────────────────────────────────────────────────────────
with tab_inspector:
    st.caption("스냅샷 데이터 품질 및 필드 커버리지 현황.")

    with st.spinner(f"스냅샷 {len(dates)}개 로딩 중..."):
        snapshots  = {d: load_full(d) for d in dates}
        inspected  = {d: _inspect(snapshots[d]) for d in dates if snapshots[d]}
        cl_summary = _parse_changelog_summaries()

    max_schema = max((v.get("snapshot_schema", 1) for v in inspected.values()), default=1)

    # 전체 요약
    st.subheader("전체 요약")
    summary_rows = []
    for d in sorted(inspected, reverse=True):
        info  = inspected[d]
        n     = info["n"]
        score = _health_score(info, max_schema)
        summary_rows.append({
            "날짜":          info["date"],
            "버전":          info["rule_version"],
            "Schema":        info["snapshot_schema"],
            "Health":        f"{score}/100",
            "종목수":        n,
            "Alpha 가능":    "✅" if info["kospi_close"] else "❌",
            "KOSPI 기준":    f"{info['kospi_close']:,.0f}" if info["kospi_close"] else "—",
            "MOM 포함":      _pct_str(info["mom_available"], n),
            "연기금 포함":   _pct_str(info["pension_net"], n),
            "시총 포함":     _pct_str(info["market_cap"], n),
            "거래대금 포함": _pct_str(info["trading_value"], n),
        })

    st.dataframe(summary_rows, use_container_width=True, hide_index=True)
    st.caption(
        f"Health = Schema 최신 +{_HEALTH_WEIGHTS['schema']} "
        f"| Engine ≥ {_MIN_FULL_VERSION} +{_HEALTH_WEIGHTS['engine']} "
        f"| 시총 100% +{_HEALTH_WEIGHTS['market_cap']} "
        f"| 거래대금 100% +{_HEALTH_WEIGHTS['trading_value']} "
        f"| MOM ≥ {_HEALTH_THRESHOLDS['mom_coverage']*100:.0f}% (BUY+ 대비) +{_HEALTH_WEIGHTS['mom']}"
    )

    # Snapshot Evolution
    st.divider()
    st.subheader("📅 Snapshot Evolution")
    st.caption("날짜별 필드 구조 변화. ✅=100% | ➖=0% | ~X%=부분")

    _EVOLUTION_FIELDS: list[tuple[str, str, bool]] = [
        ("per",                      "PER",       False),
        ("pbr",                      "PBR",       False),
        ("roe",                      "ROE",       False),
        ("div_yield",                "배당",      False),
        ("foreign_ownership_pct",    "외인지분율", False),
        ("market_cap",               "시총",      False),
        ("trading_value",            "거래대금",  False),
        ("supply_lookback_days",     "수급기준일", False),
        ("foreign_net",              "외인",      False),
        ("institution_net",          "기관",      False),
        ("pension_net",              "연기금",    False),
        ("investment_trust_net",     "투신",      False),
        ("financial_investment_net", "금융투자",  False),
        ("private_fund_net",         "사모",      False),
        ("insurance_net",            "보험",      False),
        ("foreign_positive",         "외인+",     False),
        ("institution_positive",     "기관+",     False),
        ("pension_positive",         "연기금+",   False),
        ("mom_available",            "모멘텀",    False),
        ("kospi_close",              "Alpha가능", True),
    ]

    evo_rows = []
    for d in sorted(inspected, reverse=True):
        info = inspected[d]
        n    = info["n"]
        row  = {"날짜": info["date"], "Schema": info["snapshot_schema"], "버전": info["rule_version"]}
        for key, label, is_scalar in _EVOLUTION_FIELDS:
            if is_scalar:
                row[label] = "✅" if info.get(key) else "➖"
            else:
                cnt = info.get(key, 0)
                row[label] = "✅" if cnt == n else ("➖" if cnt == 0 else f"~{cnt / n * 100:.0f}%")
        evo_rows.append(row)

    st.dataframe(evo_rows, use_container_width=True, hide_index=True)

    # Snapshot Diff
    st.divider()
    st.subheader("🔀 Snapshot Diff")
    st.caption("두 스냅샷 간 변화 비교.")

    insp_date_strs = [str(d) for d in sorted(dates, reverse=True)]

    if len(insp_date_strs) < 2:
        st.info("Diff를 보려면 스냅샷이 2개 이상 필요합니다.")
    else:
        latest_date = max(dates)
        preset      = st.radio("빠른 선택", list(_DIFF_PRESETS.keys()), horizontal=True,
                               key="diff_preset", label_visibility="collapsed")
        target_base = latest_date - timedelta(days=_DIFF_PRESETS[preset])
        preset_base = min(dates, key=lambda d: abs((d - target_base).days))
        preset_str  = str(preset_base)

        if st.session_state.get("_prev_diff_preset") != preset:
            st.session_state["diff_base"]         = preset_str
            st.session_state["_prev_diff_preset"] = preset

        diff_col1, diff_col2 = st.columns(2)
        with diff_col1:
            base_str = st.selectbox("기준 (이전)", insp_date_strs, key="diff_base")
        with diff_col2:
            comp_str = st.selectbox("비교 (이후)", insp_date_strs, index=0, key="diff_comp")

        base_d = date.fromisoformat(base_str)
        comp_d = date.fromisoformat(comp_str)
        base   = inspected.get(base_d)
        comp   = inspected.get(comp_d)

        if base and comp and base_d != comp_d:
            base_score  = _health_score(base, max_schema)
            comp_score  = _health_score(comp, max_schema)

            mc1, mc2, mc3, mc4 = st.columns(4)
            score_delta = comp_score - base_score
            mc1.metric("Health", f"{base_score} → {comp_score}",
                       delta=f"{score_delta:+d}", delta_color="normal")
            mc2.metric("Schema", f"{base['snapshot_schema']} → {comp['snapshot_schema']}")

            base_ver = base["rule_version"]
            comp_ver = comp["rule_version"]
            if base_ver != comp_ver:
                comp_summary = cl_summary.get(comp_ver, "")
                engine_label = f"{base_ver} → {comp_ver}"
                engine_help  = f"v{comp_ver}: {comp_summary}" if comp_summary else None
            else:
                engine_label = base_ver
                engine_help  = None
            mc3.metric("Engine", engine_label, help=engine_help)
            mc4.metric("종목수", f"{base['n']} → {comp['n']}",
                       delta=f"{comp['n'] - base['n']:+d}")

            st.markdown("**필드 커버리지 변화**")

            _DIFF_FIELDS = [
                ("PER",        "per"),
                ("PBR",        "pbr"),
                ("ROE",        "roe"),
                ("배당수익률", "div_yield"),
                ("외인지분율", "foreign_ownership_pct"),
                ("모멘텀",     "mom_available"),
                ("시총",       "market_cap"),
                ("거래대금",   "trading_value"),
                ("수급기준일", "supply_lookback_days"),
                ("외인",       "foreign_net"),
                ("기관",       "institution_net"),
                ("연기금",     "pension_net"),
                ("투신",       "investment_trust_net"),
                ("금융투자",   "financial_investment_net"),
                ("사모",       "private_fund_net"),
                ("보험",       "insurance_net"),
                ("Alpha가능",  "kospi_close"),
            ]

            diff_rows = []
            for label, key in _DIFF_FIELDS:
                if key == "kospi_close":
                    b_val = 100.0 if base.get(key) else 0.0
                    c_val = 100.0 if comp.get(key) else 0.0
                else:
                    b_val = _pct_float(base.get(key, 0), base["n"])
                    c_val = _pct_float(comp.get(key, 0), comp["n"])
                delta     = c_val - b_val
                delta_str = ("—" if delta == 0
                             else f"▲ +{delta:.1f}%p" if delta > 0
                             else f"▼ {delta:.1f}%p")
                diff_rows.append({
                    "필드":   label,
                    "기준":   f"{b_val:.0f}%",
                    "비교":   f"{c_val:.0f}%",
                    "변화":   delta_str,
                    "_delta": abs(delta),
                })

            diff_rows.sort(key=lambda r: r["_delta"], reverse=True)
            for r in diff_rows:
                del r["_delta"]

            changed   = [r for r in diff_rows if r["변화"] != "—"]
            unchanged = [r for r in diff_rows if r["변화"] == "—"]

            if changed:
                st.dataframe(changed, use_container_width=True, hide_index=True)
            if unchanged:
                with st.expander(f"변화 없음 ({len(unchanged)}개 필드)"):
                    st.dataframe(unchanged, use_container_width=True, hide_index=True)

            st.markdown("**등급 분포 변화**")
            gd_base = base["grade_dist"]
            gd_comp = comp["grade_dist"]
            bn, cn  = base["n"], comp["n"]
            gd_rows = []
            for key, emoji, label in [
                ("strong_buy", "🟢", "STRONG_BUY"),
                ("buy",        "🔵", "BUY"),
                ("neutral",    "🟡", "NEUTRAL"),
                ("avoid",      "🔴", "AVOID"),
            ]:
                b = gd_base.get(key, 0)
                c = gd_comp.get(key, 0)
                gd_rows.append({
                    "등급": f"{emoji} {label}",
                    "기준": f"{b} ({b / bn * 100:.0f}%)" if bn else str(b),
                    "비교": f"{c} ({c / cn * 100:.0f}%)" if cn else str(c),
                    "변화": f"{c - b:+d}",
                })
            st.dataframe(gd_rows, use_container_width=True, hide_index=True)

        elif base_d == comp_d:
            st.info("같은 날짜를 선택했습니다.")

    # 날짜별 드릴다운
    st.divider()
    st.subheader("날짜별 상세")

    detail_date_str = st.selectbox("스냅샷 날짜", insp_date_strs, key="detail_date")
    detail_d        = date.fromisoformat(detail_date_str)
    info            = inspected.get(detail_d)

    if info:
        n     = info["n"]
        score = _health_score(info, max_schema)

        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("종목수",       n)
        col2.metric("Schema",       info["snapshot_schema"])
        col3.metric("Rule Version", info["rule_version"])
        _univ_display = info.get("universe_label") or info.get("universe") or "—"
        col4.metric("KOSPI 기준",   f"{info['kospi_close']:,.0f}" if info["kospi_close"] else "—",
                    help=f"Universe: {_univ_display}")
        _health_tooltip = (
            f"Schema 최신 ({max_schema}) +{_HEALTH_WEIGHTS['schema']}  |  "
            f"Engine ≥ {_MIN_FULL_VERSION} +{_HEALTH_WEIGHTS['engine']}  |  "
            f"시총 100% +{_HEALTH_WEIGHTS['market_cap']}  |  "
            f"거래대금 100% +{_HEALTH_WEIGHTS['trading_value']}  |  "
            f"MOM ≥ {_HEALTH_THRESHOLDS['mom_coverage']*100:.0f}% (BUY+ 대비) +{_HEALTH_WEIGHTS['mom']}"
        )
        col5.metric("Health Score", f"{score}/100", help=_health_tooltip)

        st.divider()
        st.markdown("**등급 분포**")
        gd = info["grade_dist"]
        gc1, gc2, gc3, gc4 = st.columns(4)
        for col, key, emoji, label in [
            (gc1, "strong_buy", "🟢", "STRONG_BUY"),
            (gc2, "buy",        "🔵", "BUY"),
            (gc3, "neutral",    "🟡", "NEUTRAL"),
            (gc4, "avoid",      "🔴", "AVOID"),
        ]:
            cnt = gd.get(key, 0)
            col.metric(f"{emoji} {label}", f"{cnt} ({cnt / n * 100:.0f}%)" if n else "—")

        st.divider()
        st.markdown("**🔭 Research Readiness**")
        _thr = _RESEARCH_THRESHOLDS
        st.caption(
            f"✅ 가능  |  🟡 가능(표본 부족)  |  ❌ 불가  —  "
            f"기준: 모멘텀 n≥{_thr['momentum']} / 기관 n≥{_thr['institution']} / 조합 n≥{_thr['combo']}"
        )

        rr_rows = []
        for name, detail, kind, value, threshold_key in _build_research_checks(info, n):
            if kind == "bool":
                status = "✅ 가능" if value else "❌ 불가"
            else:
                status = _readiness(value, _RESEARCH_THRESHOLDS[threshold_key])
            rr_rows.append({"연구": name, "상태": status, "비고": detail})

        st.dataframe(rr_rows, use_container_width=True, hide_index=True)

        st.divider()
        st.markdown("**📊 Financial Quality (eq_band)**")
        st.caption("밴드별 종목 수 — 6개월 후 알파 비교 기반 EQ 패널티 유효성 검증용")
        _eq_dist  = info.get("eq_band_dist", {})
        _eq_total = sum(_eq_dist.values())
        if _eq_total == 0:
            st.caption("⚪ eq_band 데이터 없음 (Schema 5 이전 스냅샷)")
        else:
            _eqc1, _eqc2, _eqc3, _eqc4 = st.columns(4)
            for _col, _band, _label in [
                (_eqc1, "GOOD",      "✅ GOOD"),
                (_eqc2, "WARNING",   "🟡 WARNING"),
                (_eqc3, "POOR",      "🟠 POOR"),
                (_eqc4, "VERY_POOR", "🔴 VERY_POOR"),
            ]:
                cnt = _eq_dist.get(_band, 0)
                _col.metric(_label, cnt)

        st.divider()
        _FIELDS = [
            ("PER (per)",                           "per"),
            ("PBR (pbr)",                           "pbr"),
            ("ROE (roe)",                           "roe"),
            ("배당수익률 (div_yield)",              "div_yield"),
            ("외인지분율 (foreign_ownership_pct)",  "foreign_ownership_pct"),
            ("모멘텀 (unavailable 제외)",           "mom_available"),
            ("시총 (market_cap)",                   "market_cap"),
            ("거래대금 (trading_value)",             "trading_value"),
            ("수급 기준일 (supply_lookback_days)",  "supply_lookback_days"),
            ("외인 순매수 (foreign_net)",           "foreign_net"),
            ("기관 순매수 (institution_net)",       "institution_net"),
            ("연기금 (pension_net)",                "pension_net"),
            ("투신 (investment_trust_net)",         "investment_trust_net"),
            ("금융투자 (financial_investment_net)", "financial_investment_net"),
            ("사모 (private_fund_net)",             "private_fund_net"),
            ("보험 (insurance_net)",                "insurance_net"),
        ]

        _hdr_col, _sort_col, _filter_col = st.columns([3, 1, 1])
        _hdr_col.markdown("**연구 데이터 커버리지**")
        sort_by         = _sort_col.selectbox("정렬", ["커버리지 %", "필드명"], label_visibility="collapsed")
        show_incomplete = _filter_col.checkbox("100% 미만만", value=False)

        buy_plus_n = info["grade_dist"].get("buy", 0) + info["grade_dist"].get("strong_buy", 0)

        coverage_rows = []
        for label, key in _FIELDS:
            cnt        = info.get(key, 0)
            denom      = buy_plus_n if key == "mom_available" else n
            disp_label = f"{label} (BUY+ 대비)" if key == "mom_available" else label
            pct        = _pct_float(cnt, denom)
            filled     = min(20, int(pct / 5))
            bar        = "█" * filled + "░" * (20 - filled)
            cnt_str    = f"{cnt}/{denom}" if key == "mom_available" else str(cnt)
            coverage_rows.append({"필드": disp_label, "수량": cnt_str, "%": f"{pct:.0f}%", "커버리지": bar, "_pct": pct})

        if show_incomplete:
            coverage_rows = [r for r in coverage_rows if r["_pct"] < 100]

        coverage_rows.sort(
            key=lambda r: r["_pct"] if sort_by == "커버리지 %" else r["필드"],
            reverse=(sort_by == "커버리지 %"),
        )
        for r in coverage_rows:
            del r["_pct"]

        if coverage_rows:
            st.dataframe(coverage_rows, use_container_width=True, hide_index=True)
        else:
            st.success("모든 필드 100% 완성.")

    # 구형 스냅샷 경고
    old_schema = [d for d in dates if inspected.get(d, {}).get("snapshot_schema", 1) < max_schema]
    old_engine = [d for d in dates if _is_old_engine(inspected.get(d, {}).get("rule_version", "?"))]

    if old_schema or old_engine:
        st.divider()
        if old_schema:
            missing = {
                SCHEMA_HAS_VALUATION_RAW - 1:  "기관 세부 / market_cap / trading_value 없음",
                SCHEMA_HAS_VALUATION_RAW:       "PER / PBR / ROE / 배당 / 외인지분율 없음",
                SCHEMA_HAS_SECTOR_RELATIVE:     "sector / 업종 상대평가 없음",
                SCHEMA_HAS_EQ_BAND:             "eq_band / eq_penalty 없음",
                SCHEMA_HAS_PRICE_CONTEXT:       "52주 위치 / acml_vol 없음",
                SCHEMA_HAS_RISK_EARNINGS_RAW:   "risk_*/earnings_* 원본값 없음",
            }
            st.warning(
                f"⚠ 구형 Schema (< {max_schema}) 스냅샷 {len(old_schema)}개 — "
                f"{missing.get(max_schema - 1, '일부 필드 없음')}: "
                f"{', '.join(str(d) for d in old_schema)}"
            )
        if old_engine:
            st.warning(
                f"⚠ 구형 엔진 ({_MIN_FULL_VERSION} 미만 또는 버전 불명) 스냅샷 {len(old_engine)}개 — "
                f"스코어링 로직 상이, 성과 비교 시 주의: "
                f"{', '.join(str(d) for d in old_engine)}"
            )
