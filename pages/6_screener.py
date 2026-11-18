from __future__ import annotations

import logging
import sys
import threading
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from pykrx import stock as krx
from analyzer.data.krx import display_name as _name
from analyzer.rules.market_regime import get_market_state
from analyzer.screener_state import _scan_job
from analyzer.data.momentum import mom_strength_display
from analyzer.navigation import nav_to, NAV_TICKER
from constants import Grade, GRADE_EMOJI

_SCREENER_DIR     = Path(__file__).parent.parent / "backtests" / "calibration_snapshots"
_UNIVERSE_CACHE   = Path(__file__).parent.parent / "data" / "universe_cache.json"
_CACHE_MAX_DAYS   = 5   # 5거래일 이내 캐시만 신뢰


def _notify(message: str) -> None:
    import subprocess
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{message}" with title "Investment Analyzer" sound name "Glass"'],
            check=False, timeout=3,
        )
    except Exception:
        pass

_MIN_MARKET_CAP  = 500_000_000_000   # 5000억
_MIN_TRADING_VOL = 1_000_000_000     # 일 거래대금 10억 이상
_MAX_WORKERS     = 4







def _reason(c: dict) -> str:
    """점수 구성 기반 통과 이유 한 줄 요약."""
    parts = []
    if c["val"] >= 22:        parts.append("저밸류")
    elif c["val"] >= 18:      parts.append("적정밸류")
    if c["earn"] >= 20:       parts.append("안정실적")
    if c["risk"] == 25:       parts.append("리스크 우수")
    elif c["risk"] >= 22:     parts.append("리스크 양호")
    if c["supply"] >= 18:     parts.append("강한 수급")
    elif c["supply"] >= 14:   parts.append("양호한 수급")
    return " + ".join(parts) if parts else "종합 통과"


_UNIVERSE_OPTIONS = {
    "KOSPI 5000억+":    ("KOSPI",  _MIN_MARKET_CAP, None),
    "KOSDAQ Top 100 🧪": ("KOSDAQ", 0,               100),
    "KOSDAQ Top 200 🧪": ("KOSDAQ", 0,               200),
}

# 안정적 식별자 — 통계 groupby 기준. 레이블(한글/이모지)과 독립.
_UNIVERSE_IDS = {
    "KOSPI 5000억+":    "KOSPI_500B",
    "KOSDAQ Top 100 🧪": "KOSDAQ_100",
    "KOSDAQ Top 200 🧪": "KOSDAQ_200",
}




def _run_scan_job(universe: list[str], today: date, market_state, universe_label: str) -> None:
    job = _scan_job
    import time as _time
    job["phase"]      = "scanning"
    job["total"]      = len(universe)
    job["done"]       = 0
    job["current"]    = ""
    job["error"]      = None
    job["start_time"] = _time.time()

    candidates:  list[dict]    = []
    all_results: list          = []
    dq = {"total": 0, "no_roe": 0, "no_debt": 0, "no_dilution": 0,
          "no_volatility": 0, "low_conf": 0,
          "conf_high": 0, "conf_medium": 0, "conf_low": 0,
          "src_fund_ok": 0, "src_fund_fail": 0,
          "src_risk_ok": 0, "src_risk_fail": 0,
          "executable": 0, "blocked": 0}
    low_conf_notes_list: list[dict] = []
    roe_vals:  list[float] = []
    debt_vals: list[float] = []
    vol_vals:  list[float] = []
    grade_dist    = defaultdict(int)
    grade_scores  = defaultdict(list)
    comp_sums     = {"val": 0, "earn": 0, "risk": 0, "supply": 0}
    score_buckets = defaultdict(int)
    qr_count      = 0
    fail_reasons  = Counter()
    top_driver    = {"val_max": 0, "earn_max": 0,
                     "risk_max": 0, "supply_max": 0, "buy_plus": 0}

    try:
        from run import analyze_ticker
        from constants import Confidence

        def _analyze(ticker: str):
            try:
                return ticker, analyze_ticker(ticker, today, market_state)
            except Exception:
                return ticker, None

        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            futures = {pool.submit(_analyze, t): t for t in universe}
            for future in as_completed(futures):
                ticker, result = future.result()
                job["done"]    += 1
                job["current"]  = ticker

                if result is None:
                    continue

                all_results.append(result)
                s = result.scoring
                d = result.decision

                dq["total"] += 1
                grade_dist[s.grade.value] += 1
                grade_scores[s.grade.value].append(s.normalized_score)
                comp_sums["val"]    += s.valuation_score
                comp_sums["earn"]   += s.earnings_score
                comp_sums["risk"]   += s.risk_score
                comp_sums["supply"] += s.supply_score
                if any("Quick Reject" in n for n in s.notes):
                    qr_count += 1

                if d.executable: dq["executable"] += 1
                else:            dq["blocked"] += 1

                sc = s.normalized_score
                score_buckets[f"{(sc//10)*10}~{(sc//10)*10+9}"] += 1

                vd = result.validated
                if vd:
                    f  = vd.fundamentals
                    rk = vd.risk
                    if f:  dq["src_fund_ok"] += 1
                    else:  dq["src_fund_fail"] += 1
                    if rk: dq["src_risk_ok"] += 1
                    else:  dq["src_risk_fail"] += 1

                    if f and f.roe is None:                        dq["no_roe"] += 1
                    if rk is None or rk.debt_ratio_pct is None:   dq["no_debt"] += 1
                    if rk is None or rk.dilution_pct is None:     dq["no_dilution"] += 1
                    if rk is None or rk.volatility_pct is None:   dq["no_volatility"] += 1

                    if f and f.roe is not None:              roe_vals.append(f.roe)
                    if rk and rk.debt_ratio_pct is not None: debt_vals.append(rk.debt_ratio_pct)
                    if rk and rk.volatility_pct is not None: vol_vals.append(rk.volatility_pct)

                _cv = s.confidence.value
                if _cv == "high":     dq["conf_high"]   += 1
                elif _cv == "medium": dq["conf_medium"] += 1; dq["low_conf"] += 1
                elif _cv == "low":
                    dq["conf_low"] += 1
                    dq["low_conf"] += 1
                    if s.notes:
                        low_conf_notes_list.append({"ticker": ticker, "notes": list(s.notes)})

                if s.grade.value not in ("buy", "strong_buy") or not d.executable:
                    worst = min(
                        [("밸류", s.valuation_score), ("실적", s.earnings_score),
                         ("리스크", s.risk_score), ("수급", s.supply_score)],
                        key=lambda x: x[1]
                    )
                    fail_reasons[worst[0]] += 1

                if s.grade.value in ("buy", "strong_buy"):
                    top_driver["buy_plus"] += 1
                    if s.valuation_score == 25: top_driver["val_max"]    += 1
                    if s.earnings_score  == 25: top_driver["earn_max"]   += 1
                    if s.risk_score      == 25: top_driver["risk_max"]   += 1
                    if s.supply_score    >= 18: top_driver["supply_max"] += 1

                if s.grade in (Grade.BUY, Grade.STRONG_BUY) and d.executable:
                    raw: dict = {}
                    if vd:
                        f  = vd.fundamentals
                        fo = vd.foreign_ownership
                        mc = vd.market_cap
                        rk = vd.risk
                        if f:
                            raw["per"] = f.per; raw["pbr"] = f.pbr
                            raw["roe"] = f.roe; raw["div"] = f.div_yield
                        if mc: raw["market_cap"] = mc.market_cap
                        if fo: raw["ownership"]  = fo.ownership_pct
                        if rk:
                            raw["volatility"] = rk.volatility_pct
                            raw["dilution"]   = rk.dilution_pct
                            raw["loss_q"]     = rk.quarters_with_loss
                    candidates.append({
                        "ticker": ticker, "name": _name(ticker),
                        "score": s.normalized_score, "grade": s.grade.value,
                        "confidence": s.confidence.value,
                        "val": s.valuation_score, "earn": s.earnings_score,
                        "risk": s.risk_score, "supply": s.supply_score,
                        "raw": raw,
                    })

        job["phase"]   = "post"
        job["current"] = "업종 상대평가 보정 중..."

        # ── 업종 Hybrid 보정 ───────────────────────────────────────────────
        sector_map: dict[str, str] = {}
        try:
            from analyzer.data.krx import prefetch_all_sectors
            from analyzer.scoring.scorer import build_sector_peers_map, apply_sector_correction

            sector_map  = prefetch_all_sectors(today)
            peers_map   = build_sector_peers_map(all_results, sector_map)
            all_results = [apply_sector_correction(ar, sector_map, peers_map) for ar in all_results]

            def _make_cand(ar):
                s  = ar.scoring
                d  = ar.decision
                vd = ar.validated
                if s.grade.value not in ("buy", "strong_buy") or not d.executable:
                    return None
                raw: dict = {}
                if vd:
                    f  = vd.fundamentals;  fo = vd.foreign_ownership
                    mc = vd.market_cap;    rk = vd.risk
                    if f:  raw["per"] = f.per; raw["pbr"] = f.pbr; raw["roe"] = f.roe; raw["div"] = f.div_yield
                    if mc: raw["market_cap"] = mc.market_cap
                    if fo: raw["ownership"]  = fo.ownership_pct
                    if rk:
                        raw["volatility"]  = rk.volatility_pct
                        raw["dilution"]    = rk.dilution_pct
                        raw["loss_q"]      = rk.quarters_with_loss
                        raw["eq_ratio"]    = rk.earnings_quality_ratio
                        raw["int_cov"]     = rk.interest_coverage
                raw["val_raw"]    = s.val_score_abs - s.eq_penalty
                raw["sector_adj"] = s.valuation_score - s.val_score_abs
                raw["eq_penalty"] = s.eq_penalty
                raw["eq_band"]    = s.eq_band
                raw["val_final"]  = s.valuation_score
                return {
                    "ticker": s.ticker, "name": _name(s.ticker),
                    "score": s.normalized_score, "grade": s.grade.value,
                    "confidence": s.confidence.value,
                    "val": s.valuation_score, "earn": s.earnings_score,
                    "risk": s.risk_score, "supply": s.supply_score,
                    "sector": sector_map.get(s.ticker),
                    "raw": raw,
                }

            candidates = [c for ar in all_results if (c := _make_cand(ar)) is not None]

            grade_dist.clear(); grade_scores.clear()
            for ar in all_results:
                grade_dist[ar.scoring.grade.value] += 1
                grade_scores[ar.scoring.grade.value].append(ar.scoring.normalized_score)
        except Exception as _e:
            logging.warning("screener scan error: %s", _e)
        # ──────────────────────────────────────────────────────────────────

        candidates.sort(key=lambda x: x["score"], reverse=True)
        job["current"] = "모멘텀 오버레이 계산 중..."

        # 모멘텀 오버레이 — candidates에 대해서만 병렬 fetch
        from analyzer.data.momentum import batch_fetch_momentum, MomentumSignal
        from analyzer.data.momentum_tracker import log_signal
        mom_inputs = [(c["ticker"], c["val"]) for c in candidates]
        mom_map    = batch_fetch_momentum(mom_inputs, today, max_workers=6)
        for c in candidates:
            mom = mom_map.get(c["ticker"])
            c["mom_signal"]   = mom.signal.value if mom else MomentumSignal.UNAVAILABLE.value
            c["mom_is_vt"]    = mom.is_value_trap if mom else False
            c["mom_strength"] = mom.strength if mom else 0.0
            c["mom_rel_1m"]   = mom.rel_1m if mom else None
            c["mom_rel_3m"]   = mom.rel_3m if mom else None
            c["mom_rel_6m"]   = mom.rel_6m if mom else None
            if mom:
                try:
                    _ms = market_state.regime.value if market_state else None
                    log_signal(
                        c["ticker"], today, mom,
                        market_state=_ms,
                        score=c.get("score"),
                        grade=c.get("grade"),
                    )
                except Exception as _e:
                    logging.warning("log_signal error %s: %s", c["ticker"], _e)

        n    = dq["total"] or 1
        path = _save_screener_snapshot(candidates, today, universe_label=universe_label)

        try:
            from analyzer.data.screener_snapshot import save_daily_snapshot
            _ms_str = getattr(market_state, "value", None) or str(market_state) if market_state else None
            save_daily_snapshot(candidates, today, market_state=_ms_str)
        except Exception as _e:
            logging.warning("save_daily_snapshot error: %s", _e)

        # 스크리너 결과는 calibration_snapshots/에 저장 (위 _save_screener_snapshot).
        # Morning Run snapshots/와 분리 유지 — ranking_stability 분석 시 혼용 방지.

        job["results"] = {
            "candidates":   candidates,
            "scan_date":    today,
            "saved_path":   str(path),
            "universe_label": universe_label,
            "dq": {
                **dq, "n": n,
                "grade_dist":    dict(grade_dist),
                "grade_scores":  {k: sum(v)/len(v) for k, v in grade_scores.items() if v},
                "comp_sums":     comp_sums,
                "qr_count":      qr_count,
                "score_buckets": dict(score_buckets),
                "fail_reasons":  dict(fail_reasons),
                "top_driver":    top_driver,
                "low_conf_notes": low_conf_notes_list,
                "roe_vals":      roe_vals,
                "debt_vals":     debt_vals,
                "vol_vals":      vol_vals,
            },
        }
        job["phase"] = "done"
        _notify(f"스크리너 완료 — BUY+ {len(candidates)}개")

    except Exception as e:
        job["error"] = str(e)
        job["phase"] = "error"
        _notify("스크리너 오류 발생")


def _save_universe_cache(cap_df: object, market: str, trading_date: date) -> None:
    """유니버스 cap_df를 JSON 캐시에 저장 (장 마감 후 fallback용)."""
    import json
    _UNIVERSE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing: dict = {}
        if _UNIVERSE_CACHE.exists():
            existing = json.loads(_UNIVERSE_CACHE.read_text(encoding="utf-8"))
        existing["saved_at"]     = trading_date.isoformat()
        existing["trading_date"] = trading_date.isoformat()
        existing[market] = {
            str(tk): int(cap)
            for tk, cap in cap_df["시가총액"].items()  # type: ignore[union-attr]
            if cap and cap > 0
        }
        _UNIVERSE_CACHE.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logging.getLogger(__name__).warning(f"universe cache 저장 실패: {e}")


def _load_universe_cache(market: str) -> "tuple[date, object] | tuple[None, None]":
    """캐시에서 cap_df 복원. 캐시 없거나 _CACHE_MAX_DAYS 초과 시 (None, None)."""
    import json
    import pandas as pd
    if not _UNIVERSE_CACHE.exists():
        return None, None
    try:
        data = json.loads(_UNIVERSE_CACHE.read_text(encoding="utf-8"))
        trading_date = date.fromisoformat(data["trading_date"])
        if (date.today() - trading_date).days > _CACHE_MAX_DAYS:
            return None, None
        market_data = data.get(market)
        if not market_data:
            return None, None
        cap_df = pd.DataFrame({"시가총액": {tk: int(cap) for tk, cap in market_data.items()}})
        cap_df.index.name = "ticker"
        return trading_date, cap_df
    except Exception as e:
        logging.getLogger(__name__).warning(f"universe cache 로드 실패: {e}")
        return None, None


def _resolve_trading_date(start: date, market: str, lookback: int = 10) -> tuple[date, object, object]:
    """start부터 최대 lookback일 이전까지 데이터가 있는 가장 최근 거래일 반환.
    pykrx 전부 실패 시 universe_cache.json fallback → 장 마감 후에도 동작.
    """
    from datetime import timedelta
    last_exc: Exception | None = None
    for offset in range(lookback + 1):
        d = start - timedelta(days=offset)
        date_str = d.strftime("%Y%m%d")
        try:
            cap_df = krx.get_market_cap(date_str, market=market)
        except Exception as e:
            last_exc = e
            continue
        if not (cap_df is not None and not cap_df.empty
                and "시가총액" in cap_df.columns
                and cap_df["시가총액"].sum() > 0):
            continue
        try:
            ohlcv = krx.get_market_ohlcv_by_ticker(date_str, market=market)
        except Exception:
            ohlcv = None
        # 성공 → 캐시 갱신
        try:
            _save_universe_cache(cap_df, market, d)
        except Exception:
            pass
        return d, cap_df, ohlcv

    # ── pykrx 전부 실패 → universe_cache fallback ─────────────────────────
    cached_date, cached_df = _load_universe_cache(market)
    if cached_df is not None:
        logging.getLogger(__name__).info(
            f"pykrx 실패 — universe_cache 사용 ({market}, {cached_date})"
        )
        return cached_date, cached_df, None  # ohlcv=None → 거래대금 필터 스킵

    exc_hint = f"\n마지막 오류: {type(last_exc).__name__}: {last_exc}" if last_exc else ""
    raise RuntimeError(
        f"{start} 기준 {lookback}일 이내 거래 데이터 없음\n"
        f"시도한 날짜: {', '.join(str(start - timedelta(days=i)) for i in range(lookback + 1))}"
        f"{exc_hint}"
    )


def _get_universe(as_of_date: date, market: str, min_cap: int, top_n: int | None) -> tuple[list[str], date]:
    try:
        trading_date, cap_df, ohlcv = _resolve_trading_date(as_of_date, market)
    except RuntimeError as e:
        raise RuntimeError(f"universe 로딩 실패: {e}") from e

    # 시총 정렬 (내림차순)
    cap_df = cap_df.sort_values("시가총액", ascending=False)

    if min_cap > 0:
        cap_df = cap_df[cap_df["시가총액"] >= min_cap]

    if top_n:
        cap_df = cap_df.head(top_n)

    tickers = cap_df.index.tolist()

    # 거래대금 필터 (KOSPI만 — KOSDAQ은 top_n으로 이미 제한)
    # liquid가 비면 장 중 / 집계 전 상태이므로 필터 스킵
    if ohlcv is not None and not ohlcv.empty and "거래대금" in ohlcv.columns and not top_n:
        liquid = set(ohlcv[ohlcv["거래대금"] >= _MIN_TRADING_VOL].index.tolist())
        if liquid:
            tickers = [t for t in tickers if t in liquid]

    return tickers, trading_date


def _save_screener_snapshot(results: list[dict], as_of_date: date, universe_label: str = "KOSPI 5000억+") -> Path:
    import json
    from datetime import datetime, date as _date
    from backtests.snapshot import _entry_meta
    from constants import ENGINE_VERSION
    _SCREENER_DIR.mkdir(parents=True, exist_ok=True)
    run_date = _date.today()                           # 실행일 (파일명 기준)
    path = _SCREENER_DIR / f"{run_date.isoformat()}.json"
    now = datetime.now()
    entry_basis, market_data_date = _entry_meta(as_of_date, now)
    data = {
        "date":               run_date.isoformat(),    # 실행일
        "analysis_timestamp": now.isoformat(timespec="seconds"),
        "entry_basis":        entry_basis,
        "market_data_date":   market_data_date,        # pykrx 데이터 날짜 (as_of)
        "engine_version":     ENGINE_VERSION,          # v8.6.x 이후 추가 — baseline 버전 구분용
        "type":               "screener",
        "universe":           universe_label,
        "candidates":         sorted(results, key=lambda x: x["score"], reverse=True),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return path


def _load_screener_history() -> list[dict]:
    """이전 screener snapshot 목록 로드."""
    import json
    if not _SCREENER_DIR.exists():
        return []
    snaps = []
    for p in sorted(_SCREENER_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(p.read_text())
            snaps.append({
                "date":  data["date"],
                "count": len(data.get("candidates", [])),
                "tickers": {c["ticker"] for c in data.get("candidates", [])},
            })
        except Exception as _e:
            logging.warning("screener snapshot parse error %s: %s", p.name, _e)
    return snaps


# ---------------------------------------------------------------------------
# 헬퍼 — Calibration Report
# ---------------------------------------------------------------------------

def _dist_str(vals: list[float], edges: list[float]) -> str:
    if not vals:
        return "데이터 없음"
    parts = []
    for i in range(len(edges)):
        lo = edges[i]
        hi = edges[i + 1] if i + 1 < len(edges) else float("inf")
        cnt = sum(1 for v in vals if lo <= v < hi)
        if not cnt:
            continue
        if i == len(edges) - 1:
            label = f"{int(lo)}+"
        elif lo <= -999:
            label = f"<{int(hi)}"   # -999는 "음수" 의미 sentinel — <0 으로 표시
        else:
            label = f"{int(lo)}~{int(hi)}"
        parts.append(f"{label}: {cnt}")
    return "  ".join(parts)


def _calibration_report(candidates: list[dict], dq: dict, scan_date) -> None:
    n   = dq["n"]
    gd  = dq.get("grade_dist", {})
    cs  = dq.get("comp_sums", {})
    sb  = dq.get("score_buckets", {})
    ch  = dq.get("conf_high", 0)
    cm  = dq.get("conf_medium", dq.get("low_conf", 0))
    cl  = dq.get("conf_low", 0)
    if ch == 0 and cm == 0 and cl == 0:  # 이전 스캔 데이터 호환
        ch = n - cm - cl

    roe_vals  = dq.get("roe_vals", [])
    debt_vals = dq.get("debt_vals", [])
    vol_vals  = dq.get("vol_vals", [])
    low_notes = dq.get("low_conf_notes", [])
    top20     = candidates[:20]

    pers  = [c["raw"]["per"]  for c in candidates if c.get("raw", {}).get("per")]
    pbrs  = [c["raw"]["pbr"]  for c in candidates if c.get("raw", {}).get("pbr")]
    roes  = [c["raw"]["roe"]  for c in candidates if c.get("raw", {}).get("roe")]
    avg_score = sum(c["score"] for c in candidates) / len(candidates) if candidates else 0
    avg_per   = sum(pers) / len(pers) if pers else None
    avg_pbr   = sum(pbrs) / len(pbrs) if pbrs else None
    avg_roe   = sum(roes) / len(roes) if roes else None

    # ── 렌더 ──────────────────────────────────────────────────────────────
    st.subheader(f"🔬 Calibration Report  —  {scan_date}")

    st.markdown("**1. 스캔 요약**")
    r1c1, r1c2, r1c3, r1c4, r1c5, r1c6 = st.columns(6)
    r1c1.metric("분석",        f"{n}개")
    r1c2.metric("BUY+",        f"{len(candidates)}개")
    r1c3.metric("STRONG_BUY",  gd.get("strong_buy", 0))
    r1c4.metric("평균 점수 (BUY+)",   f"{avg_score:.1f}")
    r1c5.metric("평균 PER",    f"{avg_per:.1f}" if avg_per else "—")
    r1c6.metric("평균 PBR",    f"{avg_pbr:.2f}" if avg_pbr else "—")
    _extra = f"평균 ROE: {avg_roe:.1f}%  ·  " if avg_roe else ""
    st.caption(f"{_extra}통과율: {len(candidates)/n*100:.1f}%  ·  Quick Reject: {dq.get('qr_count',0)}개")

    st.divider()

    st.markdown("**2. Top 20**")
    st.dataframe(
        [{"종목": f"{c['name']} ({c['ticker']})", "점수": c["score"],
          "신뢰도": c["confidence"].upper(),
          "밸류": c["val"], "실적": c["earn"], "리스크": c["risk"], "수급": c["supply"]}
         for c in top20],
        use_container_width=True, hide_index=True,
    )

    st.divider()

    st.markdown("**3. Confidence**")
    cd1, cd2, cd3 = st.columns(3)
    cd1.metric("HIGH",   f"{ch}  ({ch/n*100:.0f}%)")
    cd2.metric("MEDIUM", f"{cm}  ({cm/n*100:.0f}%)")
    cd3.metric("LOW",    f"{cl}  ({cl/n*100:.0f}%)")
    if low_notes:
        with st.expander(f"LOW 사유 ({cl}개)", expanded=True):
            for item in low_notes:
                notes_str = "  ·  ".join(item["notes"][:3])
                st.caption(f"**{_name(item['ticker'])}** — {notes_str}")

    st.divider()

    st.markdown("**4. Calibration Dashboard**")
    cal_l, cal_r = st.columns(2)
    with cal_l:
        if roe_vals:
            st.caption(f"ROE 분포  (있음 {len(roe_vals)}/{n},  없음 {n-len(roe_vals)})")
            st.code(_dist_str(roe_vals, [-999, 0, 5, 10, 15, 20, 30]))
        if debt_vals:
            st.caption(f"부채비율 분포  (있음 {len(debt_vals)}/{n},  없음 {n-len(debt_vals)})")
            st.code(_dist_str(debt_vals, [0, 50, 100, 150, 200, 300, 500]))
    with cal_r:
        if vol_vals:
            st.caption(f"변동성 분포  (있음 {len(vol_vals)}/{n},  없음 {n-len(vol_vals)})")
            st.code(_dist_str(vol_vals, [0, 20, 30, 40, 50, 60, 80, 100]))
        if sb:
            st.caption("점수 구간 분포")
            buckets_sorted = sorted(sb.items(), key=lambda x: -int(x[0].split("~")[0]))
            st.code("  ".join(f"{k}: {v}" for k, v in buckets_sorted))
    st.caption(
        f"평균 컴포넌트 — "
        f"밸류 {cs.get('val',0)/n:.1f}  실적 {cs.get('earn',0)/n:.1f}  "
        f"리스크 {cs.get('risk',0)/n:.1f}  수급 {cs.get('supply',0)/n:.1f}"
    )

    # ── Markdown 다운로드 ─────────────────────────────────────────────────
    md_lines = [
        f"# 🔬 Calibration Report — {scan_date}\n",
        "## 1. 스캔 요약",
        f"- 분석: {n}개  BUY+: {len(candidates)}개  통과율: {len(candidates)/n*100:.1f}%",
        f"- STRONG_BUY: {gd.get('strong_buy',0)}  BUY: {gd.get('buy',0)}  "
        f"NEUTRAL: {gd.get('neutral',0)}  AVOID: {gd.get('avoid',0)}",
        f"- 평균 점수: {avg_score:.1f}  PER: {f'{avg_per:.1f}' if avg_per else '—'}  "
        f"PBR: {f'{avg_pbr:.2f}' if avg_pbr else '—'}  ROE: {f'{avg_roe:.1f}%' if avg_roe else '—'}",
        "",
        "## 2. Top 20",
        "| 종목 | 점수 | 신뢰도 | 밸류 | 실적 | 리스크 | 수급 |",
        "|------|------|--------|------|------|--------|------|",
        *[f"| {c['name']} ({c['ticker']}) | {c['score']} | {c['confidence'].upper()} "
          f"| {c['val']} | {c['earn']} | {c['risk']} | {c['supply']} |" for c in top20],
        "",
        "## 3. Confidence",
        f"- HIGH: {ch} ({ch/n*100:.0f}%)  MEDIUM: {cm} ({cm/n*100:.0f}%)  LOW: {cl} ({cl/n*100:.0f}%)",
        *(f"- LOW: {_name(item['ticker'])} — " + " / ".join(item["notes"][:3])
          for item in low_notes),
        "",
        "## 4. Calibration Dashboard",
        f"ROE 분포 (n={len(roe_vals)}): {_dist_str(roe_vals, [-999, 0, 5, 10, 15, 20, 30])}",
        f"부채비율 분포 (n={len(debt_vals)}): {_dist_str(debt_vals, [0, 50, 100, 150, 200, 300, 500])}",
        f"변동성 분포 (n={len(vol_vals)}): {_dist_str(vol_vals, [0, 20, 30, 40, 50, 60, 80, 100])}",
        f"점수 구간: " + "  ".join(f"{k}: {v}" for k, v in
                                   sorted(sb.items(), key=lambda x: -int(x[0].split("~")[0]))),
        f"평균 컴포넌트 — 밸류 {cs.get('val',0)/n:.1f}  실적 {cs.get('earn',0)/n:.1f}  "
        f"리스크 {cs.get('risk',0)/n:.1f}  수급 {cs.get('supply',0)/n:.1f}",
    ]
    st.download_button(
        "⬇ Markdown 저장",
        "\n".join(md_lines),
        file_name=f"calibration_report_{scan_date}.md",
        mime="text/plain",
    )


# ---------------------------------------------------------------------------
# 레이아웃
# ---------------------------------------------------------------------------

st.title("🔭 Universe Screener")

st.info(
    "현재 규율 기준(BUY+ / 실행가능)을 통과한 종목입니다.  "
    "미래 수익을 예측하지 않습니다.  "
    "Weekly 실행 권장."
)

universe_choice = st.radio(
    "Universe",
    list(_UNIVERSE_OPTIONS.keys()),
    horizontal=True,
    index=0,
)
_mkt, _min_cap, _top_n = _UNIVERSE_OPTIONS[universe_choice]

col_run, col_info, col_reset = st.columns([1, 3, 0.5])
with col_run:
    run_btn = st.button("▶ Universe Scan", type="primary",
                        disabled=_scan_job["phase"] in ("loading", "scanning", "post"),
                        use_container_width=True)
with col_reset:
    if st.button("🔄", help="스캔 상태 초기화 (버튼이 반응 없을 때)", use_container_width=True):
        _scan_job["phase"] = "idle"
        st.rerun()
with col_info:
    if "KOSDAQ" in universe_choice:
        st.caption(f"🧪 실험 모드: {universe_choice}  |  현재 규칙이 코스닥에서도 작동하는지 검증용\n\n"
                   "필터: BUY 이상 · 실행가능 · Quick Reject 통과")
    else:
        st.caption("대상: KOSPI · 시총 5000억 이상 · 거래대금 10억 이상\n\n"
                   "필터: BUY 이상 · 실행가능 · Quick Reject 통과")

if run_btn:
    try:
        with st.spinner("📡 Universe 로딩 중..."):
            today           = date.today()
            universe, as_of = _get_universe(today, _mkt, _min_cap, _top_n)
            market_state    = get_market_state(as_of)
        date_note = " (오늘 데이터 미집계 → 최근 거래일 기준)" if as_of != today else ""
        if not universe:
            st.warning(f"⚠️ {as_of}{date_note} 기준 유니버스가 비어있습니다.\n\n"
                       f"pykrx 데이터 수집 완료 후 다시 시도해주세요.")
        else:
            _scan_job.update({
                "phase": "scanning", "done": 0, "total": len(universe),
                "current": "", "results": None, "error": None,
            })
            threading.Thread(
                target=_run_scan_job,
                args=(universe, as_of, market_state, universe_choice),
                daemon=True,
            ).start()
            st.rerun()
    except Exception as e:
        st.error(f"❌ Universe 로딩 실패: {e}")

# --- 진행 중 표시 ---
if _scan_job["phase"] in ("scanning", "post"):
    import time as _poll_time
    done  = _scan_job["done"]
    total = _scan_job["total"] or 1

    # 전체 타임아웃 감지 (10분 초과 시 강제 error)
    _start = _scan_job.get("start_time")
    if _start and (_poll_time.time() - _start) > 600:
        _scan_job["phase"] = "error"
        _scan_job["error"] = "타임아웃 (10분 초과) — 🔄 버튼으로 초기화 후 재시도"
        st.rerun()

    if _scan_job["phase"] == "post":
        st.progress(1.0)
        st.info(f"📊 후처리 중... (모멘텀 오버레이 + 저장)  |  {_scan_job.get('current', '')}")
    else:
        st.progress(done / total)
        st.info(
            f"📊 분석 중... **{done} / {total}**  |  "
            f"{_name(_scan_job['current']) if _scan_job['current'] else '준비 중'}"
        )
    _poll_time.sleep(0.7)
    st.rerun()

if _scan_job["phase"] == "error":
    st.error(f"❌ 오류: {_scan_job['error']}")
    _scan_job["phase"] = "idle"

# 완료 시 session_state 동기화
if _scan_job["phase"] == "done" and _scan_job["results"]:
    r = _scan_job["results"]
    st.session_state["screener_results"] = r["candidates"]
    st.session_state["screener_date"]    = r["scan_date"]
    st.session_state["screener_saved"]   = r["saved_path"]
    st.session_state["screener_dq"]      = r["dq"]
    _scan_job["phase"] = "idle"

# --- 결과 표시 ---
candidates = st.session_state.get("screener_results")
if not candidates:
    st.divider()
    st.caption("결과 없음 — Universe Scan 실행 후 표시됩니다")
    st.stop()

scan_date  = st.session_state.get("screener_date", "")
total_pass = len(candidates)

st.divider()

# --- 히스토리 (이전 스캔 vs 이번 스캔) ---
history = _load_screener_history()
prev_tickers: set[str] = set()
if len(history) >= 2:
    prev_tickers = history[1]["tickers"]  # 직전 스캔 ticker set
    hrows = [{"날짜": h["date"], "통과 수": h["count"]} for h in history[:6]]
    with st.expander(f"📅 스캔 히스토리 ({len(history)}회)", expanded=False):
        st.dataframe(hrows, use_container_width=True, hide_index=True)
elif len(history) == 1:
    st.caption("이전 스캔 없음 — 다음 스캔부터 신규/탈락 비교 가능")

# --- 압축 필터 ---
fc1, fc2, fc3, fc4 = st.columns(4)
with fc1:
    min_score = st.slider("최소 점수", 70, 90, 80, step=5)
with fc2:
    high_only = st.checkbox("HIGH confidence만", value=False)
with fc3:
    top_n = st.number_input("상위 N개", min_value=5, max_value=100, value=30, step=5)
with fc4:
    sort_by = st.selectbox("정렬", ["점수순", "밸류순", "리스크순", "수급순", "모멘텀순"])

fc5, fc6 = st.columns([2, 2])
with fc5:
    _MOM_FILTER_OPTS = {
        "전체": None,
        "🔥 STRONG만":      "strong",
        "✅ POSITIVE만":     "positive",
        "⚪ NEUTRAL만":     "neutral",
        "⚠️ NEGATIVE만":    "negative",
        "🚨 시장 외면 중만": "market_ignore",
    }
    mom_filter_label = st.selectbox("모멘텀 필터", list(_MOM_FILTER_OPTS.keys()))
    mom_filter = _MOM_FILTER_OPTS[mom_filter_label]
with fc6:
    min_strength = st.select_slider(
        "모멘텀 강도 최소값",
        options=[0, 3, 5, 8, 10, 15, 20],
        value=0,
        format_func=lambda v: "제한 없음" if v == 0 else f">= {v}",
    )

# 필터 적용
filtered = [c for c in candidates if c["score"] >= min_score]
if high_only:
    filtered = [c for c in filtered if c["confidence"] == "high"]
if mom_filter == "market_ignore":
    filtered = [c for c in filtered if c.get("mom_is_vt")]
elif mom_filter is not None:
    filtered = [c for c in filtered if c.get("mom_signal") == mom_filter]
if min_strength > 0:
    filtered = [c for c in filtered if c.get("mom_strength", 0.0) >= min_strength]

# 정렬
if sort_by == "모멘텀순":
    filtered = sorted(filtered, key=lambda x: x.get("mom_strength", 0.0), reverse=True)[:top_n]
else:
    _sort_key = {"점수순": "score", "밸류순": "val", "리스크순": "risk", "수급순": "supply"}[sort_by]
    filtered = sorted(filtered, key=lambda x: x[_sort_key], reverse=True)[:top_n]

# 신규 진입 파악
new_tickers = {c["ticker"] for c in filtered} - prev_tickers if prev_tickers else set()

st.subheader(f"📋 통과 종목  ({scan_date}  /  필터 후 {len(filtered)}개 / 전체 {total_pass}개)")
if new_tickers:
    st.success(f"🆕 이번 스캔 신규 진입: {len(new_tickers)}개")

# --- 3번: Universe breadth summary ---
if candidates:
    avg_score = sum(c["score"] for c in filtered) / len(filtered) if filtered else 0
    high_conf = sum(1 for c in filtered if c["confidence"] == "high")
    low_risk  = sum(1 for c in filtered if c["risk"] >= 22)

    # raw data 기반 PER/PBR 통계
    pers = [c["raw"]["per"]  for c in filtered if c.get("raw", {}).get("per")]
    pbrs = [c["raw"]["pbr"]  for c in filtered if c.get("raw", {}).get("pbr")]
    avg_per = sum(pers) / len(pers) if pers else None
    avg_pbr = sum(pbrs) / len(pbrs) if pbrs else None

    # 스타일 레이블
    if avg_pbr is not None:
        if avg_pbr < 0.7:    style = "🔵 Deep Value"
        elif avg_pbr < 1.2:  style = "🔵 Value"
        elif avg_pbr < 2.0:  style = "🟡 Blend"
        else:                style = "🟠 Growth Tilt"
    else:
        style = "—"

    with st.expander("📊 이번 스캔 요약", expanded=True):
        bc1, bc2, bc3, bc4, bc5 = st.columns(5)
        bc1.metric("통과 종목",    f"{len(filtered)}개")
        bc2.metric("평균 점수",    f"{avg_score:.1f}")
        bc3.metric("평균 PER",    f"{avg_per:.1f}배" if avg_per else "—")
        bc4.metric("평균 PBR",    f"{avg_pbr:.2f}배" if avg_pbr else "—")
        bc5.metric("스타일",       style)
        st.caption(
            f"HIGH confidence {high_conf}/{len(filtered)}개  ·  "
            f"리스크 우수(22점+) {low_risk}/{len(filtered)}개  ·  "
            f"PBR 1.0 미만 {sum(1 for p in pbrs if p < 1.0)}/{len(pbrs)}개"
        )

    # Data Quality + 등급분포 + 컴포넌트 + Quick Reject
    dq = st.session_state.get("screener_dq")
    if dq:
        n  = dq["n"]
        gd = dq.get("grade_dist", {})
        cs = dq.get("comp_sums", {})
        qr = dq.get("qr_count", 0)

        sb   = dq.get("score_buckets", {})
        fr   = dq.get("fail_reasons", {})
        td   = dq.get("top_driver", {})
        gs   = dq.get("grade_scores", {})
        bp   = td.get("buy_plus", 1)

        with st.expander("🔍 모델 검증 통계 (전체 스캔 기준)", expanded=False):

            # 1. Executable 비율
            st.markdown("**✅ Executable 비율**")
            e1, e2, e3 = st.columns(3)
            e1.metric("분석 종목",  f"{n}개")
            e2.metric("Executable", f"{dq['executable']} ({dq['executable']/n*100:.0f}%)")
            e3.metric("Blocked",    f"{dq['blocked']} ({dq['blocked']/n*100:.0f}%)")

            # 2. 데이터 소스 성공률
            st.markdown("**📡 데이터 소스 성공률**")
            s1, s2, s3, s4 = st.columns(4)
            s1.metric("Fundamentals 성공", f"{dq['src_fund_ok']} ({dq['src_fund_ok']/n*100:.0f}%)")
            s2.metric("Risk 성공",         f"{dq['src_risk_ok']} ({dq['src_risk_ok']/n*100:.0f}%)")
            s3.metric("ROE 없음",          f"{dq['no_roe']} ({dq['no_roe']/n*100:.0f}%)")
            s4.metric("부채비율 없음",      f"{dq['no_debt']} ({dq['no_debt']/n*100:.0f}%)")
            st.caption(f"희석률 없음: {dq['no_dilution']} ({dq['no_dilution']/n*100:.0f}%)  ·  변동성 없음: {dq['no_volatility']} ({dq['no_volatility']/n*100:.0f}%)")

            # 3. 점수 구간 분포
            st.markdown("**📊 점수 구간 분포**")
            buckets_sorted = sorted(sb.items(), key=lambda x: -int(x[0].split("~")[0]))
            bucket_str = "  ".join(f"`{k}`: {v}" for k, v in buckets_sorted)
            st.write(bucket_str)

            # 4. 탈락 이유
            if fr:
                st.markdown("**❌ 탈락 이유 (BUY 미달 · Blocked)**")
                fr_str = "  ".join(f"{cat} {cnt}({cnt/(n or 1)*100:.0f}%)" for cat, cnt in sorted(fr.items(), key=lambda x: -x[1]))
                st.write(fr_str)

            # 5. 컴포넌트 Top Driver (BUY+ 중 만점 비율)
            if bp > 0:
                st.markdown(f"**⭐ 컴포넌트 만점 비율** (BUY+ {bp}개 기준)")
                td1, td2, td3, td4 = st.columns(4)
                td1.metric("밸류 만점",  f"{td['val_max']/bp*100:.0f}%")
                td2.metric("실적 만점",  f"{td['earn_max']/bp*100:.0f}%")
                td3.metric("리스크 만점", f"{td['risk_max']/bp*100:.0f}%")
                td4.metric("수급 강세",  f"{td['supply_max']/bp*100:.0f}%")

            # 등급 분포 + 평균 컴포넌트
            st.markdown("**등급 분포 + 평균 점수**")
            gd_cols = st.columns(4)
            for col, grade_key in zip(gd_cols, ["strong_buy", "buy", "neutral", "avoid"]):
                cnt = gd.get(grade_key, 0)
                avg = gs.get(grade_key)
                label = grade_key.upper().replace("_", " ")
                delta = f"평균 {avg:.0f}점" if avg else None
                col.metric(label, f"{cnt} ({cnt/n*100:.0f}%)", delta=delta, delta_color="off")
            st.caption(f"Quick Reject: {qr}개 ({qr/n*100:.0f}%)  ·  MEDIUM conf: {dq['low_conf']} ({dq['low_conf']/n*100:.0f}%)")

            st.markdown("**평균 컴포넌트 점수**")
            cc1, cc2, cc3, cc4 = st.columns(4)
            cc1.metric("밸류",  f"{cs.get('val',0)/n:.1f}/25")
            cc2.metric("실적",  f"{cs.get('earn',0)/n:.1f}/25")
            cc3.metric("리스크", f"{cs.get('risk',0)/n:.1f}/25")
            cc4.metric("수급",  f"{cs.get('supply',0)/n:.1f}/25")

# --- 4번: Watchlist / 보유 종목 연결 ---
from analyzer.watchlist.store import load_all as _wl_all
from analyzer.portfolio import loader as _portfolio_loader

wl_tickers  = {i.ticker for i in _wl_all()}
portfolio   = _portfolio_loader.load()
held        = set(portfolio.positions) if portfolio else set()

import pandas as pd
from analyzer.watchlist.store import save as wl_save
from analyzer.watchlist.models import WatchlistItem, WatchlistState
from constants import Confidence

# 보유 중인데 탈락한 종목 경고
if held:
    all_pass      = {c["ticker"] for c in candidates}
    held_dropped  = held - all_pass
    if held_dropped:
        st.warning(f"⚠️ 보유 중이지만 이번 스캔 탈락: {', '.join(_name(t) for t in held_dropped)}")

# 체크박스 통합 테이블
_mom_strong_set = {c["ticker"] for c in filtered if c.get("mom_signal") == "strong"}
_mom_vt_set     = {c["ticker"] for c in filtered if c.get("mom_is_vt")}

def _status_tag(ticker: str) -> str:
    tags = []
    if ticker in new_tickers:    tags.append("🆕")
    if ticker in _mom_vt_set:    tags.append("🚨")
    elif ticker in _mom_strong_set: tags.append("🔥")
    if ticker in wl_tickers:     tags.append("📋")
    if ticker in held:           tags.append("💼")
    return " ".join(tags)

value_traps = [c for c in filtered if c.get("mom_is_vt")]
if value_traps:
    names = ", ".join(f"{c['name']}({c['ticker']})" for c in value_traps[:5])
    st.warning(f"⚠️ **시장 외면 중** {len(value_traps)}개 — {names}  |  밸류 매력적이나 모멘텀 지속 부진, 구조적 원인 확인 권장")

df_rows = []
for c in filtered:
    emoji  = GRADE_EMOJI.get(Grade(c["grade"]), "")
    status = _status_tag(c["ticker"])
    is_new = c["ticker"] not in wl_tickers and c["ticker"] not in held
    df_rows.append({
        "추가":   is_new,
        "종목":   f"{emoji} {c['name']} ({c['ticker']}) {status}",
        "점수":   c["score"],
        "모멘텀": mom_strength_display(c.get("mom_signal"), c.get("mom_strength", 0.0)),
        "이유":   _reason(c),
        "신뢰도": c["confidence"].upper(),
        "밸류":   c["val"],
        "실적":   c["earn"],
        "리스크": c["risk"],
        "수급":   c["supply"],
        "_ticker": c["ticker"],
    })

df = pd.DataFrame(df_rows)
if df.empty:
    st.info("스크리너 조건을 만족하는 종목이 없습니다.")
    st.stop()
edited = st.data_editor(
    df.drop(columns=["_ticker"]),
    column_config={
        "추가": st.column_config.CheckboxColumn("추가", width="small"),
    },
    hide_index=True,
    use_container_width=True,
)
st.caption("📋 Watchlist 등록  💼 현재 보유  🆕 이번 스캔 신규")

# 선택된 신규 종목만 추가 가능
checked_idx   = edited[edited["추가"] == True].index.tolist()
to_add        = [df_rows[i] for i in checked_idx if df_rows[i]["_ticker"] not in wl_tickers]
already_in_wl = [df_rows[i] for i in checked_idx if df_rows[i]["_ticker"] in wl_tickers]

add_col, drill_col = st.columns([1, 2])
with add_col:
    if to_add:
        from analyzer.watchlist.models import WatchlistTag
        _add_tag_options = {
            "📋 일반":                WatchlistTag.GENERAL,
            "🧪 Calibration":         WatchlistTag.CALIBRATION,
            "⚠️ False Positive 후보": WatchlistTag.FALSE_POSITIVE,
            "🔥 Momentum":            WatchlistTag.MOMENTUM,
        }
        add_tag_label = st.selectbox(
            "태그", list(_add_tag_options.keys()),
            label_visibility="collapsed",
        )
        if st.button(f"➕ {len(to_add)}개 Watchlist 추가", type="primary", use_container_width=True):
            add_tag = _add_tag_options[add_tag_label]
            added = []
            for row in to_add:
                t = row["_ticker"]
                c = next(x for x in filtered if x["ticker"] == t)
                wl_save(WatchlistItem(
                    ticker=t, state=WatchlistState.ACTIVE,
                    added_date=date.today(), last_analyzed=date.today(),
                    last_grade=Grade(c["grade"]), last_score=c["score"],
                    last_confidence=Confidence(c["confidence"]),
                    tag=add_tag,
                ))
                added.append(_name(t))
            st.success(f"✅ {', '.join(added)} 추가됨  [{add_tag_label}]")
            if st.button("→ Watchlist로 이동"):
                st.switch_page("pages/4_watchlist.py")
    if already_in_wl:
        st.caption(f"이미 Watchlist: {', '.join(_name(r['_ticker']) for r in already_in_wl)}")

# Ticker 드릴다운 + Quick Facts
with drill_col:
    drill_options = [f"{c['name']} ({c['ticker']})  {c['score']}점" for c in filtered]
    drill_map     = {f"{c['name']} ({c['ticker']})  {c['score']}점": c for c in filtered}
    dc1, dc2 = st.columns([4, 1])
    with dc1:
        drill_sel = st.selectbox("", drill_options, label_visibility="collapsed",
                                 placeholder="종목 선택 → Quick Facts / Ticker 분석")
    with dc2:
        if st.button("🔎", use_container_width=True):
            nav_to(NAV_TICKER, ticker=drill_map[drill_sel]["ticker"])
            st.switch_page("pages/3_ticker.py")

    # Quick Facts
    sel_c = drill_map.get(drill_sel)
    if sel_c:
        raw = sel_c.get("raw", {})
        with st.expander(f"📊 Quick Facts — {sel_c['name']}  |  {_reason(sel_c)}", expanded=True):
            qf1, qf2, qf3 = st.columns(3)
            with qf1:
                st.markdown("**밸류**")
                st.write(f"PER `{raw['per']:.1f}배`" if raw.get("per") else "PER —")
                st.write(f"PBR `{raw['pbr']:.2f}배`" if raw.get("pbr") else "PBR —")
                st.write(f"ROE `{raw['roe']:.1f}%`" if raw.get("roe") else "ROE —")
                st.write(f"배당 `{raw['div']:.2f}%`" if raw.get("div") else "배당 —")
                if raw.get("market_cap"):
                    cap = raw["market_cap"]
                    jo  = cap // 1_000_000_000_000
                    eok = (cap % 1_000_000_000_000) // 100_000_000
                    st.write(f"시총 `{jo}조 {eok:,}억`" if jo else f"시총 `{eok:,}억`")
            with qf2:
                st.markdown("**수급**")
                st.write(f"외인지분율 `{raw['ownership']:.2f}%`" if raw.get("ownership") else "외인지분율 —")
            with qf3:
                st.markdown("**리스크**")
                st.write(f"변동성 `{raw['volatility']:.1f}%`" if raw.get("volatility") else "변동성 —")
                st.write(f"희석률 `{raw['dilution']:+.1f}%`" if raw.get("dilution") is not None else "희석률 —")
                lq = raw.get("loss_q")
                st.write(f"적자분기 `{lq}/4`" if lq is not None else "적자분기 —")

# Export
st.divider()
exp1, exp2 = st.columns(2)
with exp1:
    csv_lines = ["종목,코드,점수,이유,신뢰도,밸류,실적,리스크,수급"]
    for c in filtered:
        csv_lines.append(f"{c['name']},{c['ticker']},{c['score']},{_reason(c)},"
                         f"{c['confidence']},{c['val']},{c['earn']},{c['risk']},{c['supply']}")
    st.download_button("⬇ CSV 저장", "\n".join(csv_lines),
                       file_name=f"screener_{scan_date}.csv", mime="text/csv")
with exp2:
    md_lines = [f"# Screener 결과  {scan_date}\n",
                "| 종목 | 점수 | 이유 |", "|------|------|------|"]
    for c in filtered:
        md_lines.append(f"| {c['name']} ({c['ticker']}) | {c['score']} | {_reason(c)} |")
    st.download_button("⬇ Markdown 저장", "\n".join(md_lines),
                       file_name=f"screener_{scan_date}.md", mime="text/plain")

if st.session_state.get("screener_saved"):
    st.caption(f"저장됨: {st.session_state['screener_saved']}")

# --- Calibration Report ---
st.divider()
if st.button("🔬 Calibration Report", type="secondary"):
    st.session_state["show_cal_report"] = not st.session_state.get("show_cal_report", False)

if st.session_state.get("show_cal_report"):
    dq_data = st.session_state.get("screener_dq")
    if dq_data:
        _calibration_report(candidates, dq_data, scan_date)
    else:
        st.caption("데이터 없음 — Universe Scan 실행 후 사용 가능")
