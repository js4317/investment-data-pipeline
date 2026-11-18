"""결정 B 개정 (validation_exclusion_v1): 재무 검증 실패 = 제외, 실행 오류 = incomplete, 제외 상한 5%(합산),
선정·기록 — 다종목 가짜 전송으로 run_signal 전 구간 (실제 조회 없음)."""
from datetime import timedelta
import json
import sqlite3
from types import ModuleType

import pytest

from analyzer.pipeline import official as OF
from analyzer.pipeline.collect import ENDPOINT_SPECS, Reply, TransportTimeout
from analyzer.pipeline.official_inputs import make_official_inputs
from analyzer.pipeline.runtime import LaunchEvidence, SourceManifest
from analyzer.pipeline.sources import reference_date_1y, window_sessions
from database.signal_store import EXCLUSION_POLICY, TEST_STRATEGY, SignalStore
from database.trading_calendar import TradingCalendarStore
from tests.test_dart_inputs import NAME
from tests.test_dart_quarters import KEYS, filing, report, standard
from tests.test_signal_sources import CAL, E, NOW, krx_d, vol_rows

TARGET = {target: ep for ep, (_, target) in ENDPOINT_SPECS.items()}
REF = reference_date_1y(E)
N = 20                                                    # 5% = 1종목
TICKERS = [f"{100000 + i}" for i in range(N)]
CORPS = {t: f"C{t}" for t in TICKERS}
TICKER_OF = {c: t for t, c in CORPS.items()}
ROE_FAIL = report(2025, 2, 20, 10, 9, eq=80, eq_ctrl=70, no="20990101000009")   # 기초 보고서만 접수번호 불일치


def answer_for(faults: dict):
    s20 = window_sessions(CAL, E, 20)

    def answer(endpoint, req):
        if endpoint == "krx.finder_stkisu":
            return {"block1": [{"short_code": t, "full_code": f"KR7{t}003", "marketCode": "STK", "codeName": t}
                               for t in TICKERS]}
        if endpoint == "krxopen.stk_bydd_trd":
            day = E if req["basDd"] == E.strftime("%Y%m%d") else REF
            return {"OutBlock_1": [{"BAS_DD": day.strftime("%Y%m%d"), "ISU_CD": t, "TDD_CLSPRC": "1,000",
                                    "MKTCAP": "-" if faults.get(t) == "mktcap_dash" and day == E else "5,000,000",
                                    "ACC_TRDVAL": "12,345", "LIST_SHRS": "1,000"} for t in TICKERS]}
        if endpoint == "krxopen.ksq_bydd_trd":
            return {"OutBlock_1": []}
        if endpoint == "krx.MDCSTAT00301":
            return {"output": [{"TRD_DD": krx_d(d), "CLSPRC_IDX": f"{2500 + i:,.2f}"} for i, d in enumerate(s20)]}
        if endpoint == "krx.MDCSTAT03502":
            t = req["isuCd"][3:9]
            return {"output": [{"TRD_DD": krx_d(E), "TDD_CLSPRC": "1,001" if faults.get(t) == "close_mismatch" else "1,000",
                                "PER": "8.5", "PBR": "0.85", "DVD_YLD": "2.0"}]}
        if endpoint == "krx.MDCSTAT01701":
            return {"output": list(reversed(vol_rows()))}
        t = TICKER_OF[req["corp_code"]]
        if endpoint == "dart.list":
            if faults.get(t) == "rate_limit":
                return {"status": "020", "message": "요청 제한을 초과하였습니다."}
            rows = [{"corp_code": req["corp_code"], "stock_code": t, "report_nm": f"{NAME[q]} ({y}.{q * 3:02d})",
                     "rcept_no": filing(y, q).rcept_no,
                     "rcept_dt": f"{y if q < 4 else y + 1}{'05' if q == 1 else '08' if q == 2 else '11' if q == 3 else '03'}10"}
                    for y, q in KEYS]
            return {"status": "000", "page_no": 1, "total_page": 1, "total_count": len(rows), "list": rows}
        if endpoint == "dart.fnltt":
            if faults.get(t) == "fetch_fail":
                raise TransportTimeout("ReadTimeout")
            reps = standard()
            if faults.get(t) == "roe_fail":
                reps[0] = ROE_FAIL
            for r in reps:
                if {k: v for k, v in r.request.items() if k != "corp_code"} == {k: v for k, v in req.items() if k != "corp_code"}:
                    return {"status": "000", "list": [dict(x, corp_code=req["corp_code"]) for x in r.rows]}
            return {"status": "013"}
        raise AssertionError(endpoint)
    return answer


class Fake:
    def __init__(self, answer):
        self.answer = answer

    def send(self, kind, target, request):
        return Reply(200, json.dumps(self.answer(TARGET[target], request)).encode())

    def send_within(self, kind, target, request, limit):     # 시한 전송 형태(가짜: 지연 없음)
        return self.send(kind, target, request)


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "x.db"
    src = tmp_path / "src"; src.mkdir(); (src / "m.py").write_text("x=1\n")
    mod = ModuleType("m"); mod.__file__ = str(src / "m.py")
    store = SignalStore(db); store.initialize()
    cs = TradingCalendarStore(db); cs.save_version(CAL.version, list(CAL.days.values())); cs.save_notice_check(CAL.notice_check)
    store.register_engine(OF._engine_version(), "digest-A", "c", NOW)
    return dict(db=db, src=src, mod=mod, store=store)


def run(env, faults):
    state = {"t": NOW}
    def clock():
        state["t"] += timedelta(milliseconds=50)
        return state["t"]
    never = lambda *a: (_ for _ in ()).throw(AssertionError("기존 경로 호출"))
    deps = OF.SignalDeps(
        collect_launch=lambda label: LaunchEvidence(1, label, label, NOW),
        manifest=lambda: SourceManifest("digest-A", ("m.py",)), universe=lambda: list(TICKERS),
        market_state=never, analyze=never, clock=clock, kis_active=lambda: False,
        loaded_modules=lambda: (env["mod"],), git_info=lambda root: ("commit", False),
        official_inputs=make_official_inputs(Fake(answer_for(faults)), corp_map=CORPS,
                                             deadline=NOW + timedelta(hours=1), sleep=lambda s: None))
    out = OF.run_signal(env["db"], deps, strategy=TEST_STRATEGY, source_root=env["src"])
    out["abort_reason"] = env["store"]._run_row(out["run_id"])["abort_reason"]
    return out


def q(env, sql, a=()):
    with sqlite3.connect(env["db"]) as c:
        return c.execute(sql, a).fetchall()


def statuses(env, out):
    return {t: (st, json.loads(p).get("error")) for t, st, p in
            q(env, "SELECT ticker, processing_status, payload_json FROM run_signals WHERE run_id=?", (out["run_id"],))}


def test_exactly_five_percent_completes_and_records_scope(env):
    """1/20 = 5%: 허용 → 완료·확정. 제외 종목(정렬상 선두)은 선정되지 않고 다음 종목이 같은 규칙으로 채운다."""
    out = run(env, {TICKERS[0]: "roe_fail"})
    assert (out["status"], out["outcome"], out["n_excluded_validation"], out["n_failed"]) == ("completed", "confirmed", 1, 0)
    st = statuses(env, out)
    assert st[TICKERS[0]] == ("excluded_validation", "financial_validation_excluded: fin_rcept_no_mismatch")
    assert q(env, "SELECT ticker FROM cohort_members ORDER BY rank") == [(t,) for t in TICKERS[1:11]]
    scope = q(env, "SELECT exclusion_policy, scope, universe_size, n_scored, n_excluded_required_missing, "
                   "n_excluded_validation, exclusion_ratio FROM cohort_scope")
    assert scope == [(EXCLUSION_POLICY, "validated_subset", 20, 19, 0, 1, 0.05)]
    assert q(env, "SELECT ticker, processing_status, reason FROM cohort_exclusions") == [
        (TICKERS[0], "excluded_validation", "financial_validation_excluded: fin_rcept_no_mismatch")]
    assert env["store"].audit_input_evidence(out["run_id"]) is None        # 제외 종목의 실패 근거(사용 공시)도 보존·재검증
    assert q(env, "SELECT count(*) FROM run_input_responses WHERE run_id=? AND input_id=?",
             (out["run_id"], f"{TICKERS[0]}:fin_roe"))[0][0] > 0


def test_over_five_percent_is_incomplete(env):
    out = run(env, {TICKERS[0]: "roe_fail", TICKERS[1]: "roe_fail"})        # 2/20 = 10%
    assert (out["status"], out["abort_reason"], out["n_excluded_validation"]) == ("incomplete", "degraded_inputs", 2)
    assert q(env, "SELECT count(*) FROM official_cohorts") == [(0,)]


def test_cap_combines_required_missing_and_validation(env):
    out = run(env, {TICKERS[0]: "roe_fail", TICKERS[1]: "mktcap_dash"})     # 1 + 1 = 10%
    st = statuses(env, out)
    assert st[TICKERS[1]][0] == "excluded_required_missing" and st[TICKERS[0]][0] == "excluded_validation"
    assert (out["status"], out["abort_reason"]) == ("incomplete", "degraded_inputs")


@pytest.mark.parametrize("fault,reason", [
    ("fetch_fail", "financial_input_failed: fin_request_timeout"),        # 통신 실패
    ("rate_limit", "financial_input_failed: fin_parse_failed"),           # 요청 한도 초과(DART 020)
])
def test_execution_errors_are_not_exclusions(env, fault, reason):
    out = run(env, {TICKERS[3]: fault})
    st = statuses(env, out)
    assert st[TICKERS[3]] == ("failed", reason)
    assert (out["status"], out["abort_reason"], out["n_excluded_validation"]) == ("incomplete", "processing_failed", 0)


def test_market_verification_failure_stays_failed(env):
    out = run(env, {TICKERS[2]: "close_mismatch"})
    assert statuses(env, out)[TICKERS[2]][0] == "failed" and out["abort_reason"] == "processing_failed"


def test_mislabeled_exclusion_is_rejected(env, monkeypatch):
    """실행 오류를 제외로 표시하면(실행기 결함) finish_run이 financial_status_inconsistent로 막는다."""
    real = OF.apply_financial_status
    def mislabel(signal, fin_inputs):
        out = real(signal, fin_inputs)
        return dict(out, processing_status="excluded_validation") if out.get("error") else out
    monkeypatch.setattr(OF, "apply_financial_status", mislabel)
    out = run(env, {TICKERS[3]: "fetch_fail"})
    assert (out["status"], out["abort_reason"]) == ("incomplete", "financial_status_inconsistent")


def test_old_signal_schema_is_reported_not_rebuilt(tmp_path):
    db = tmp_path / "old.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE run_signals (run_id TEXT, ticker TEXT, processing_status TEXT CHECK(processing_status IN "
                  "('scored','excluded_required_missing','excluded_rule','failed')))")
    with pytest.raises(ValueError, match="pipeline_schema_upgrade_required"):
        SignalStore(db).initialize()
