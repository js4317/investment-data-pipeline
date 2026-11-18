"""5단계-5: 공식 입력 경로 — 수집 → 근거 있는 입력 → 채점 (격리 연결, 명시적으로 선택했을 때만).

설계: docs/design/signal_pipeline.md §13.11·§13.13·§13.28.
- 모든 채점 입력을 공식 원천 원 응답에서 정의(sources·dart_inputs)로 만든다. Naver·pykrx 경로를 쓰지 않는다
  (run·analyzer.data.krx·naver를 import하지 않음).
- 종목의 입력이 하나라도 verified가 아니면 채점하지 않고 처리 실패(대체 원천·빈 값 채점 없음, §13.4·결정 B).
- 원천 결측(verified + missing_kind)은 현행 채점과 같은 의미의 None으로 넘긴다.
- 점수 계산은 기존 validate → score → decide_execution 그대로. 수급·외국인은 조회하지 않음(not_requested, §13.15).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from types import SimpleNamespace

from analyzer.pipeline import sources as S
from analyzer.pipeline.collect import ENDPOINT_SPECS, Collector, Fetched, make_input
from analyzer.pipeline.dart_inputs import INCLUDE_START
from analyzer.pipeline.dart_quarters import needed_reports
from analyzer.pipeline.dart_select import select_periodic
from analyzer.pipeline.entry import MarketInput

DISPARITY_ID = "mkt:kospi_disparity"
F03502 = {"per": "PER", "pbr": "PBR", "div_yield": "DVD_YLD"}       # div_yield는 기록만(채점 미사용)
FOPEN = {"market_cap": "MKTCAP", "trading_value": "ACC_TRDVAL"}
FIN = {"fin_earnings": "dart_earnings_proxy_v1", "fin_loss": "dart_loss_quarters_v1",
       "fin_debt": "dart_debt_ratio_v1", "fin_eq": "dart_eq_ratio_v1", "fin_roe": "dart_roe_v1"}
PACE = {"dart": 0.2, "krx_web": 0.3}       # 요청 뒤 대기(초) — 탐침과 같은 간격, 원천 과다 요청 방지
DART_MAX_PAGES = 20


@dataclass
class OfficialResult:
    signals: list[dict]
    inputs: dict[str, MarketInput]
    links: dict[str, list[str]]
    report: dict = field(default_factory=dict)


def _fetch(col: Collector, endpoint: str, request: dict) -> Fetched:
    f = col.fetch(endpoint, request)
    col.wait(PACE.get(ENDPOINT_SPECS[endpoint][0], 0))      # 요청 간 대기도 마감 예산 안에서만
    return f


def _unfetched(name: str, params: dict, session: date, reason: str, at: datetime) -> MarketInput:
    """요청을 만들 수 없어 조회하지 않은 입력(ISIN 연결 없음·달력 구간 미확정·회사 코드 없음).
    재파싱할 응답이 없으므로 정의 ID 없이 실패 사유만 기록한다."""
    return MarketInput(name, at, session, "failed", None, params_json=S._json(params), failure_reason=reason)


def _dart_bundles(col: Collector, ticker: str, corp: str, session: date, today: date) -> dict[str, list[Fetched]]:
    """정의별 응답 묶음: 공시 목록 전 쪽 + 그 정의가 실제로 쓰는 보고서의 재무 응답(ROE만 기초 보고서 포함)."""
    lists: list[Fetched] = []
    page, total = 1, 1
    while page <= total and page <= DART_MAX_PAGES:
        f = _fetch(col, "dart.list", S.request_dart_list(corp, date(session.year - 2, 1, 1), today, page))
        lists.append(f)
        r = f.response
        if r is None or r.rows is None or r.http_status != 200:
            break
        meta = r.meta or {}
        total = int(meta.get("total_page") or 0) if meta.get("status") == "000" else 0
        page += 1
    only_lists = {did: list(lists) for did in FIN.values()}
    if any(f.response is None for f in lists):
        return only_lists
    sel = select_periodic([f.response for f in lists], ticker=ticker, corp_code=corp, checked_on=today)
    if not isinstance(sel, dict) or not sel:
        return only_lists                        # 정의가 같은 응답으로 같은 사유를 다시 판정
    need = {inc: needed_reports(sel, inc) for inc in (False, True)}
    keys = sorted(set().union(*(n for n in need.values() if isinstance(n, dict))))
    fn = {(k, "CFS"): _fetch(col, "dart.fnltt", S.request_dart_fnltt(corp, k[0], k[1], "CFS")) for k in keys}
    cfs = list(fn.values())
    if all(f.response is not None for f in cfs) and any(
            f.response.http_status == 200 and (f.response.meta or {}).get("status") == S.DART_NO_DATA for f in cfs):
        fn.update({(k, "OFS"): _fetch(col, "dart.fnltt", S.request_dart_fnltt(corp, k[0], k[1], "OFS")) for k in keys})
    out = {}
    for did in FIN.values():
        n = need[did in INCLUDE_START]
        out[did] = list(lists) + ([fn[key] for key in sorted(fn) if key[0] in n] if isinstance(n, dict) else [])
    return out


def collect(col: Collector, tickers: list[str], session: date, calendar, corp_map: dict,
            progress=lambda msg: None) -> tuple[dict[str, MarketInput], dict[str, list[str]], dict]:
    """한 실행의 공식 입력 전체. → (입력 {input_id: MarketInput}, 종목별 입력 ID, 단계별 요청 수)."""
    inputs: dict[str, MarketInput] = {}
    phases: dict[str, int] = {}
    mark = lambda name: phases.__setitem__(name, len(col.log))
    today = col.clock().date()

    finder = _fetch(col, "krx.finder_stkisu", S.request_finder())
    opens = [_fetch(col, e, S.request_open_api(session)) for e in S.OPEN_API]
    ref = S.reference_session(calendar, S.reference_date_1y(session))
    opens_ref = [_fetch(col, e, S.request_open_api(ref)) for e in S.OPEN_API] if ref else []
    s20 = S.window_sessions(calendar, session, S.DISP_N)
    if s20 is None:
        inputs[DISPARITY_ID] = _unfetched("kospi_disparity", {}, session, "calendar_unknown", col.clock())
    else:
        disp = _fetch(col, "krx.MDCSTAT00301", S.request_00301(s20[0], session))
        inputs[DISPARITY_ID] = make_input("kospi_disparity", "kospi_disparity_v1_krx00301", {}, session, calendar, [disp])
    mark("shared")
    rows = {r["short_code"]: r for r in (finder.response.rows or [])} if finder.response else {}
    s252 = S.window_sessions(calendar, session, S.VOL_N)
    links: dict[str, list[str]] = {}

    for n, tk in enumerate(tickers, 1):
        own = {}
        row = rows.get(tk)
        for name, fld in FOPEN.items():
            own[name] = make_input(name, "krxopen_field_v1", {"field": fld, "ticker": tk}, session, calendar, opens)
        own["dilution"] = make_input("dilution", "dilution_v2_openapi_prev_session", {"ticker": tk},
                                     session, calendar, opens + opens_ref)
        if row is None:
            if finder.response is None:
                reason = finder.failure                       # 조회 실패 사유 그대로
            elif finder.response.rows is None or finder.response.http_status != 200:
                reason = "isin_source_failed"
            else:
                reason = "isin_link_missing"
            for name in list(F03502) + ["volatility"]:
                own[name] = _unfetched(name, {"ticker": tk}, session, reason, col.clock())
        else:
            isin, mkt = row["full_code"], row.get("marketCode")
            f3 = _fetch(col, "krx.MDCSTAT03502", S.request_03502(isin, mkt, session))
            for name, fld in F03502.items():
                own[name] = make_input(name, "krx03502_field_v2",
                                       {"field": fld, "ticker": tk, "isin": isin, "mkt_id": mkt},
                                       session, calendar, [f3, finder] + opens)
            vp = {"isin": isin, "ticker": tk}
            if s252 is None:
                own["volatility"] = _unfetched("volatility", vp, session, "calendar_unknown", col.clock())
            else:
                f7 = _fetch(col, "krx.MDCSTAT01701", S.request_01701(isin, s252[0], session))
                own["volatility"] = make_input("volatility", "volatility_v4_krx_fluc", vp, session, calendar,
                                               [f7, finder] + opens)
        corp = corp_map.get(tk)
        fp = {"ticker": tk, "corp_code": corp}
        if not corp:
            for name in FIN:
                own[name] = _unfetched(name, fp, session, "corp_code_missing", col.clock())
        else:
            bundles = _dart_bundles(col, tk, corp, session, today)
            for name, did in FIN.items():
                own[name] = make_input(name, did, fp, session, calendar, bundles[did])
        for name, item in own.items():
            inputs[f"{tk}:{name}"] = item
        links[tk] = sorted([f"{tk}:{name}" for name in own] + [DISPARITY_ID])
        if n == 1 or n % 10 == 0 or n == len(tickers):
            progress(f"[수집 {n}/{len(tickers)}] 요청 {len(col.log)}")
    mark("total")
    return inputs, links, phases


def market_state_from(disp: MarketInput, session: date):
    """검증된 이격도 입력 → MarketState (get_market_state와 같은 score_market·regime 규칙, pykrx 조회 없음)."""
    from analyzer.data.models import KospiDisparity
    from analyzer.rules.market_regime import MarketState, _regime_from_heat
    from analyzer.scoring.market import score_market
    from constants import DataSource
    d = json.loads(disp.detail_json)
    kd = KospiDisparity(current=d["last_close"], ma_n=d["mean_close"], disparity_pct=disp.value,
                        window=S.DISP_N, source=DataSource.KRX, as_of_date=session)
    heat = score_market(kospi_disparity=kd, margin_loan=None, as_of_date=session)
    return MarketState(heat=heat, regime=_regime_from_heat(heat.heat_level), as_of_date=session), kd


def _failed(ticker: str, errors: list[str]) -> dict:
    return dict(ticker=ticker, processing_status="failed", normalized_score=None, grade=None, confidence=None,
                executable=False, error="; ".join(errors))


def score_ticker(ticker: str, own: dict[str, MarketInput], kd, market_state, session: date) -> dict:
    """모든 입력이 verified일 때만 호출. 값 → 기존 채점 객체 → validate·score·decide_execution."""
    from analyzer.data.models import (EarningsData, FetchErrorReason, FetchResult, Fundamentals, MarketCap,
                                      RiskData)
    from analyzer.pipeline.official import _classify
    from analyzer.rules.execution import decide_execution
    from analyzer.scoring.scorer import score
    from analyzer.validation.validator import validate
    from constants import DataSource
    v = {name: item.value for name, item in own.items()}
    fund = Fundamentals(per=v["per"], pbr=v["pbr"], div_yield=v["div_yield"], roe=v["fin_roe"],
                        roe_source=DataSource.DART, source=DataSource.KRX, as_of_date=session)
    if v["market_cap"] is None or v["trading_value"] is None:
        mc = FetchResult.failure(FetchErrorReason.INSUFFICIENT_DATA)      # 원천 결측 → 필수 결측(LOW)
    elif v["market_cap"] == 0:
        mc = FetchResult.failure(FetchErrorReason.INVALID_ZERO_VALUE)     # 현행과 같은 판정, 전일 대체 없음
    else:
        mc = FetchResult.success(MarketCap(market_cap=int(v["market_cap"]), trading_value=int(v["trading_value"]),
                                           source=DataSource.KRX, as_of_date=session))
    earnings = None if v["fin_earnings"] is None else EarningsData(
        quarters_met_expectations=int(v["fin_earnings"]), large_miss_pct=None,
        source=DataSource.DART, as_of_date=session)
    vdetail = json.loads(own["volatility"].detail_json) if own["volatility"].detail_json else {}
    halt_days = int((vdetail.get("codes") or {}).get("0", 0)) if vdetail else None   # 01701 코드 0 = 거래정지 행
    halted_on_session = (vdetail.get("last_code") == "0") if vdetail else None
    risk = RiskData(quarters_with_loss=int(v["fin_loss"]), debt_ratio_pct=v["fin_debt"],
                    dilution_pct=v["dilution"], volatility_pct=v["volatility"], governance_flag=False,
                    earnings_quality_ratio=v["fin_eq"], halt_days=halt_days,
                    halted_on_session=halted_on_session, source=DataSource.KRX, as_of_date=session)
    validated = validate(ticker=ticker, as_of_date=session, market_cap_result=mc,
                         fundamentals_result=FetchResult.success(fund),
                         supply_flow_result=None, foreign_ownership_result=None,
                         kospi_disparity_result=FetchResult.success(kd),
                         earnings_result=FetchResult.success(earnings) if earnings is not None else None,
                         risk_result=FetchResult.success(risk))
    sc = score(validated)
    return _classify(SimpleNamespace(scoring=sc, decision=decide_execution(sc.grade, market_state, sc.confidence)))


def signals_from(tickers: list[str], inputs: dict[str, MarketInput], links: dict[str, list[str]],
                 session: date) -> list[dict]:
    """종목별 처리. 시장 입력 실패 `input_failed: 이름:사유` → failed.
    재무 입력은 apply_financial_status(결정 B 개정): 공시 검증 사유만이면 excluded_validation, 실행 오류면 failed."""
    from analyzer.pipeline.official import apply_financial_status
    disp = inputs[DISPARITY_ID]
    state = market_state_from(disp, session) if disp.source_status == "verified" else None
    out = []
    for tk in tickers:
        own = {i.split(":", 1)[1]: inputs[i] for i in links[tk] if i != DISPARITY_ID}
        bad_mkt = sorted(f"{name}:{item.failure_reason or 'unverified'}" for name, item in own.items()
                         if item.source_status != "verified" and not name.startswith("fin_"))
        if state is None:
            bad_mkt.insert(0, f"kospi_disparity:{disp.failure_reason or 'unverified'}")
        fin_items = [item for name, item in own.items() if name.startswith("fin_")]
        if bad_mkt:                                    # 시장 입력 실패 → 처리 실패(실행 incomplete), 재무 사유도 함께 기록
            fin_part = apply_financial_status(_failed(tk, []), fin_items).get("error")
            out.append(_failed(tk, [f"input_failed: {','.join(bad_mkt)}"] + ([fin_part] if fin_part else [])))
            continue
        flagged = apply_financial_status(_failed(tk, []), fin_items)   # 결정 B 개정: 검증 제외 / 실행 오류 구분
        if flagged.get("error"):
            out.append(flagged)
            continue
        try:
            out.append(score_ticker(tk, own, state[1], state[0], session))
        except Exception as exc:
            out.append(_failed(tk, [f"{type(exc).__name__}: {exc}"]))
    return out


def make_official_inputs(transport, *, corp_map: dict, deadline: datetime, sleep, progress=lambda msg: None,
                         monotonic=None):
    """SignalDeps.official_inputs용 호출 객체. run_signal이 (store, run_id, 기준 세션, 달력)으로 부른다.
    transport는 시한 전송(send_within — 운영은 transport_proc.ProcessTransport)이어야 한다. deadline = 수집 종료 시각
    (공식: 08:47:00, 이후 확정 예비 60초·확정 마감 08:48:00 — signal_store.COLLECT_DEADLINE·CONFIRM_DEADLINE)."""
    import time as _time
    if not hasattr(transport, "send_within"):
        raise TypeError("official inputs require a bounded transport (send_within)")
    mono = monotonic or _time.monotonic
    def run(store, run_id: str, session: date, calendar, tickers: list[str], clock) -> OfficialResult:
        col = Collector(store, run_id, transport, clock=clock, deadline=deadline, sleep=sleep, monotonic=mono)
        t0 = clock()
        inputs, links, phases = collect(col, tickers, session, calendar, corp_map, progress)
        t1 = clock()
        signals = signals_from(tickers, inputs, links, session)
        report = {"requests": len(col.log), "phase_requests": phases, "collection_aborted": col.aborted,
                  "collect_deadline": deadline.isoformat(), "collect_end_remaining_s": round(col.remaining(), 1),
                  "attempt_outcomes": _count(e["outcome"] for e in col.log),
                  "collect_seconds": round((t1 - t0).total_seconds(), 1),
                  "score_seconds": round((clock() - t1).total_seconds(), 1),
                  "input_status": _count(i.source_status for i in inputs.values()),
                  "input_failures": _count(f"{i.name}:{i.failure_reason}" for i in inputs.values()
                                           if i.source_status != "verified")}
        return OfficialResult(signals, inputs, links, report)
    return run


def _count(items) -> dict:
    out: dict = {}
    for x in items:
        out[x] = out.get(x, 0) + 1
    return dict(sorted(out.items()))
