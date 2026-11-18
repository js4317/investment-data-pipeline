"""5단계-5: 공식 입력 경로 통합 — 수집 → 근거 저장·재검증 → 채점 → 실패 처리 → 실행 기록 (가짜 전송, 실제 조회 없음)."""
from datetime import timedelta
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from analyzer.pipeline import official as OF
from analyzer.pipeline.collect import ENDPOINT_SPECS, Reply
from analyzer.pipeline.official_inputs import DISPARITY_ID, make_official_inputs
from analyzer.pipeline.runtime import LaunchEvidence, SourceManifest
from analyzer.pipeline.sources import reference_date_1y, window_sessions
from database.signal_store import TEST_STRATEGY, SignalStore
from database.trading_calendar import TradingCalendarStore
from tests.test_dart_inputs import list_body
from tests.test_dart_quarters import CORP, report, standard
from tests.test_signal_sources import CAL, E, FINDER_ROWS, NOW, krx_d, vol_rows

TK = "005930"
TARGET = {target: ep for ep, (_, target) in ENDPOINT_SPECS.items()}
REF = reference_date_1y(E)              # 2025-10-13 (월) — 세션


def bodies(reports=None, close03502="1,000", disp_rows=None):
    reports = standard() if reports is None else reports
    def open_rows(day, shrs):
        return [{"BAS_DD": day.strftime("%Y%m%d"), "ISU_CD": TK, "TDD_CLSPRC": "1,000", "MKTCAP": "5,000,000",
                 "ACC_TRDVAL": "12,345", "LIST_SHRS": shrs}]
    s20 = window_sessions(CAL, E, 20)

    def answer(endpoint, req):
        if endpoint == "krx.finder_stkisu":
            return {"block1": FINDER_ROWS}
        if endpoint == "krxopen.stk_bydd_trd":
            return {"OutBlock_1": open_rows(E, "1,010") if req["basDd"] == E.strftime("%Y%m%d") else open_rows(REF, "1,000")}
        if endpoint == "krxopen.ksq_bydd_trd":
            return {"OutBlock_1": []}
        if endpoint == "krx.MDCSTAT00301":
            return {"output": disp_rows if disp_rows is not None else
                    [{"TRD_DD": krx_d(d), "CLSPRC_IDX": f"{2500 + i:,.2f}"} for i, d in enumerate(s20)]}
        if endpoint == "krx.MDCSTAT03502":
            return {"output": [{"TRD_DD": krx_d(E), "TDD_CLSPRC": close03502, "PER": "8.5", "PBR": "0.85",
                                "DVD_YLD": "2.0"}]}
        if endpoint == "krx.MDCSTAT01701":
            return {"output": list(reversed(vol_rows()))}
        if endpoint == "dart.list":
            return json.loads(list_body())
        if endpoint == "dart.fnltt":
            for r in reports:
                if r.request == req:
                    return {"status": "000", "list": list(r.rows)}
            return {"status": "013"}
        raise AssertionError(endpoint)
    return answer


class FakeTransport:
    def __init__(self, answer):
        self.answer, self.sent = answer, []

    def send(self, kind, target, request):
        endpoint = TARGET[target]
        self.sent.append(endpoint)
        return Reply(200, json.dumps(self.answer(endpoint, request)).encode())

    def send_within(self, kind, target, request, limit):     # 시한 전송 형태(가짜: 지연 없음)
        return self.send(kind, target, request)


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "official.db"
    src = tmp_path / "src"; src.mkdir(); (src / "m.py").write_text("x=1\n")
    mod = ModuleType("m"); mod.__file__ = str(src / "m.py")
    store = SignalStore(db); store.initialize()
    cs = TradingCalendarStore(db); cs.save_version(CAL.version, list(CAL.days.values())); cs.save_notice_check(CAL.notice_check)
    store.register_engine(OF._engine_version(), "digest-A", "c", NOW)
    return dict(db=db, src=src, mod=mod, store=store)


def run(env, answer):
    state = {"t": NOW}
    def clock():
        state["t"] += timedelta(milliseconds=200)
        return state["t"]
    transport = FakeTransport(answer)
    never = lambda *a: (_ for _ in ()).throw(AssertionError("기존 경로 호출"))
    deps = OF.SignalDeps(
        collect_launch=lambda label: LaunchEvidence(1, label, label, NOW),
        manifest=lambda: SourceManifest("digest-A", ("m.py",)), universe=lambda: [TK],
        market_state=never, analyze=never, clock=clock, kis_active=lambda: False,
        loaded_modules=lambda: (env["mod"],), git_info=lambda root: ("commit", False),
        official_inputs=make_official_inputs(transport, corp_map={TK: CORP}, deadline=NOW + timedelta(hours=1),
                                             sleep=lambda s: None))
    out = OF.run_signal(env["db"], deps, strategy=TEST_STRATEGY, source_root=env["src"])
    return out, transport


def stored(env, out):
    import sqlite3
    with sqlite3.connect(env["db"]) as c:
        c.row_factory = sqlite3.Row
        inputs = {r["input_id"]: dict(r) for r in c.execute("SELECT * FROM run_inputs WHERE run_id=?", (out["run_id"],))}
        sig = json.loads(c.execute("SELECT payload_json FROM run_signals WHERE run_id=?", (out["run_id"],)).fetchone()[0])
        links = c.execute("SELECT count(*) FROM run_signal_inputs WHERE run_id=?", (out["run_id"],)).fetchone()[0]
    return inputs, sig, links


def test_official_path_end_to_end_completes_with_evidence(env):
    out, tr = run(env, bodies())
    assert out["as_of"] == E.isoformat()
    assert (out["status"], out["n_scored"], out["n_failed"]) == ("completed", 1, 0), out
    # 입력이 모두 verified여도 구분 기록이 없는 달력(미승인)은 공식 적격이 아니다
    assert out["outcome"] == "confirmed" and out["eligible"] == 0
    assert json.loads(out["exclusion_reasons"]) == ["calendar_not_official"]
    import sqlite3
    with sqlite3.connect(env["db"]) as c:
        assert c.execute("SELECT strategy_id FROM official_cohorts").fetchall() == [(TEST_STRATEGY,)]
    assert env["store"].audit_input_evidence(out["run_id"]) is None        # 저장 후 원 응답 재검증
    inputs, sig, links = stored(env, out)
    assert len(inputs) == 13 and links == 13 and all(i["source_status"] == "verified" for i in inputs.values())
    assert {i["definition_id"] for i in inputs.values()} == {
        "krx03502_field_v2", "krxopen_field_v1", "volatility_v4_krx_fluc", "dilution_v2_openapi_prev_session",
        "kospi_disparity_v1_krx00301", "dart_earnings_proxy_v1", "dart_loss_quarters_v1", "dart_debt_ratio_v1",
        "dart_eq_ratio_v1", "dart_roe_v1"}
    assert json.loads(inputs[f"{TK}:dilution"]["value_json"]) == 1.0      # 1,000 → 1,010
    assert sig["processing_status"] == "scored" and sig["confidence"] == "high"   # 수급 미요청은 조회 실패가 아님
    assert "dart.fnltt" in tr.sent and out["official_inputs"]["requests"] == len(tr.sent)


def test_roe_only_failure_is_validation_exclusion(env):
    """결정 B 개정: ROE만 공시 검증 실패 → excluded_validation. 1종목 유니버스라 제외 100% > 5% → degraded_inputs."""
    reps = standard(); reps[0] = report(2025, 2, 20, 10, 9, eq=80, eq_ctrl=70, no="20990101000009")  # 기초 보고서만 불일치
    out, _ = run(env, bodies(reps))
    assert (out["status"], out["n_failed"], out["n_excluded_validation"]) == ("incomplete", 0, 1)
    assert env["store"]._run_row(out["run_id"])["abort_reason"] == "degraded_inputs"
    inputs, sig, _ = stored(env, out)
    assert sig["processing_status"] == "excluded_validation" and sig["grade"] is None
    assert sig["error"] == "financial_validation_excluded: fin_rcept_no_mismatch"
    assert inputs[f"{TK}:fin_roe"]["failure_reason"] == "rcept_no_mismatch"
    assert all(inputs[f"{TK}:{n}"]["source_status"] == "verified"
               for n in ("fin_earnings", "fin_loss", "fin_debt", "fin_eq"))          # ROE 외 입력은 막히지 않음
    assert env["store"].audit_input_evidence(out["run_id"]) is None


def test_market_input_failure_fails_ticker_without_fallback(env):
    out, _ = run(env, bodies(close03502="1,001"))                                     # 03502 종가 ≠ Open API
    inputs, sig, _ = stored(env, out)
    assert out["status"] == "incomplete" and sig["processing_status"] == "failed"
    assert sig["error"] == "input_failed: div_yield:close_mismatch,pbr:close_mismatch,per:close_mismatch"
    assert inputs[f"{TK}:per"]["failure_reason"] == "close_mismatch"


def test_disparity_failure_fails_all_tickers(env):
    s20 = window_sessions(CAL, E, 20)
    out, _ = run(env, bodies(disp_rows=[{"TRD_DD": krx_d(d), "CLSPRC_IDX": "2,500.00"} for d in s20[:-1]]))
    inputs, sig, _ = stored(env, out)
    assert inputs[DISPARITY_ID]["failure_reason"] == "date_missing"
    assert sig["error"] == "input_failed: kospi_disparity:date_missing"


def test_official_path_does_not_import_pykrx_or_current_path():
    code = ("import sys; sys.dont_write_bytecode=True\n"
            "import analyzer.pipeline.official, analyzer.pipeline.official_inputs, analyzer.rules.execution, "
            "analyzer.scoring.scorer, analyzer.validation.validator, analyzer.scoring.market\n"
            "bad=[m for m in sys.modules if m.split('.')[0] in ('pykrx','run') or m in ('analyzer.data.krx','analyzer.data.naver')]\n"
            "assert not bad, bad")
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, "-B", "-c", code], cwd=root, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_validator_not_requested_is_not_a_fetch_error():
    from analyzer.data.models import FetchResult, Fundamentals, KospiDisparity, MarketCap
    from analyzer.validation.validator import validate
    from constants import Confidence, DataSource
    v = validate(ticker=TK, as_of_date=E,
                 market_cap_result=FetchResult.success(MarketCap(market_cap=1, trading_value=1, source=DataSource.KRX, as_of_date=E)),
                 fundamentals_result=FetchResult.success(Fundamentals(per=8.0, pbr=0.8, div_yield=None, source=DataSource.KRX, as_of_date=E)),
                 supply_flow_result=None, foreign_ownership_result=None,
                 kospi_disparity_result=FetchResult.success(KospiDisparity(current=1.0, ma_n=1.0, disparity_pct=0.0, window=20, source=DataSource.KRX, as_of_date=E)))
    assert v.confidence == Confidence.HIGH and v.supply_flow is None
