"""Stage 3 — official prices and outcome measurement (design §7.3, §7.4, §8, T20~T27).

Isolated: temporary SQLite only, synthetic KRX-shaped responses, no network.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import sqlite3

import pytest

from analyzer.pipeline.entry import RunContext, planned_entry
from analyzer.pipeline import outcomes as O
from database.price_store import (INDEX_TICKER, MARKETS, OfficialPriceStore, validate_response)
from database.signal_store import KST, STRATEGY
from database.trading_calendar import CalendarDay, CalendarNoticeCheck, CalendarSnapshot

from test_signal_entry_calendar import CONFIRM, REF, START, calendar as entry_calendar, market, save_calendar

ENTRY = date(2026, 10, 14)
EVENING = datetime(2026, 12, 31, 19, 0, tzinfo=KST)
HOLIDAY = date(2026, 10, 20)


def long_calendar(version="outcome-v1", unknown_after=date(2026, 12, 31), extra_holidays=()):
    days, d = {}, date(2026, 10, 7)
    while d <= unknown_after:
        status = "holiday" if d.weekday() >= 5 or d in (date(2026, 10, 9), HOLIDAY, *extra_holidays) else "session"
        days[d] = CalendarDay(d, status, "KRX_EQUITY", REF, START - timedelta(minutes=10))
        d += timedelta(days=1)
    return CalendarSnapshot(version, days, CalendarNoticeCheck(version + "-check", version,
                            START - timedelta(minutes=5), "clear", (REF,)))


def stock_rows(day: date, prices: dict[str, tuple], n_extra=0):
    dd = day.strftime("%Y%m%d")
    rows = [dict(BAS_DD=dd, ISU_CD=t, ISU_NM=t, TDD_OPNPRC=o, TDD_HGPRC=o, TDD_LWPRC=o,
                 TDD_CLSPRC=c, ACC_TRDVOL="1,000", MKTCAP="1000000") for t, (o, c) in prices.items()]
    rows += [dict(BAS_DD=dd, ISU_CD=f"X{i:05}", ISU_NM="x", TDD_OPNPRC="10", TDD_HGPRC="10",
                  TDD_LWPRC="10", TDD_CLSPRC="10", ACC_TRDVOL="1", MKTCAP="1") for i in range(n_extra)]
    return rows


def index_rows(day: date, o="2,500", c="2,550"):
    dd = day.strftime("%Y%m%d")
    return [dict(BAS_DD=dd, IDX_NM="코스피", OPNPRC_IDX=o, HGPRC_IDX=o, LWPRC_IDX=o, CLSPRC_IDX=c),
            dict(BAS_DD=dd, IDX_NM="코스피 200", OPNPRC_IDX="330", HGPRC_IDX="330", LWPRC_IDX="330", CLSPRC_IDX="331")]


@pytest.fixture
def store(tmp_path):
    s = OfficialPriceStore(tmp_path / "isolated.db")
    O.initialize_outcomes(s)
    s.register_engine("test", "hash", "test-only", START)
    return s


def make_cohort(store, tickers):
    from analyzer.pipeline.runtime import LaunchEvidence
    from tests.evidence_fixture import attach
    cal = entry_calendar()
    save_calendar(store.path, cal)
    ctx = RunContext.start(START, cal)
    run = store.start_run(signal_date="2026-10-14", strategy_id=STRATEGY, trigger="scheduled",
                          version="test", digest="hash", loaded_digest="test", git_commit="test-only",
                          git_dirty=False, python_executable="test", pid=1, process_started_at=START,
                          now=START, context=ctx, universe_rule="test", tickers=tickers,
                          launch=LaunchEvidence(1, "com.investment.signal", "com.investment.signal", START))
    store.finish_run(run, [dict(ticker=t, processing_status="scored", normalized_score=90 - i,
                                grade="strong_buy", confidence="high", executable=True)
                           for i, t in enumerate(tickers)], "hash", START + timedelta(minutes=5),
                     inputs=attach(store, run, {"m1": market()}), signal_inputs={t: ["m1"] for t in tickers})
    decision = planned_entry(ctx, CONFIRM, store.decision_inputs(run), cal)
    return store.confirm(run, now=CONFIRM, decision=decision)


def collect_day(store, day, prices, idx=("2,500", "2,550"), kosdaq=None):
    """Complete all three markets for a session."""
    out = []
    out.append(store.record_collection(day, "KOSPI", stock_rows(day, prices), None, EVENING))
    out.append(store.record_collection(day, "KOSDAQ", stock_rows(day, kosdaq or {"900001": ("5", "5")}), None, EVENING))
    out.append(store.record_collection(day, "KOSPI_INDEX", index_rows(day, *idx), None, EVENING))
    return out


def q(store, sql, args=()):
    with sqlite3.connect(store.path) as c:
        return c.execute(sql, args).fetchall()


# ── 수집 완결성 (§7.4(a)) ──────────────────────────────────────────────────

def test_T22_bas_dd_mismatch_failed_no_prices(store):
    rows = stock_rows(ENTRY, {"000001": ("100", "110")})
    rows[0]["BAS_DD"] = "20261013"
    cid, status = store.record_collection(ENTRY, "KOSPI", rows, None, EVENING)
    assert status == "failed"
    assert q(store, "SELECT count(*) FROM official_prices")[0][0] == 0


def test_T21_index_without_kospi_row_is_partial_not_holiday(store):
    rows = [r for r in index_rows(ENTRY) if r["IDX_NM"] != "코스피"]
    assert store.record_collection(ENTRY, "KOSPI_INDEX", rows, None, EVENING)[1] == "partial"
    assert not store.market_complete(ENTRY, "KOSPI_INDEX")


def test_T23_one_market_success_others_retried_only(store):
    cal = long_calendar()
    store.record_collection(ENTRY, "KOSPI", stock_rows(ENTRY, {"000001": ("100", "110")}), None, EVENING)
    store.record_collection(ENTRY, "KOSDAQ", None, "timeout", EVENING)
    pend = store.pending(cal, ENTRY, ENTRY)
    assert sorted(m for _, m in pend) == ["KOSDAQ", "KOSPI_INDEX"]
    assert not store.date_complete(ENTRY)


def test_T23b_duplicate_ticker_not_complete(store):
    rows = stock_rows(ENTRY, {"000001": ("100", "110")}) * 2
    assert store.record_collection(ENTRY, "KOSPI", rows, None, EVENING)[1] == "partial"


def test_T23c_count_outside_two_percent_suspect(store):
    d0 = date(2026, 10, 13)
    assert store.record_collection(d0, "KOSPI", stock_rows(d0, {}, n_extra=100), None, EVENING)[1] == "complete"
    assert store.record_collection(ENTRY, "KOSPI", stock_rows(ENTRY, {}, n_extra=102), None, EVENING)[1] == "complete"
    d2 = date(2026, 10, 15)
    assert store.record_collection(d2, "KOSPI", stock_rows(d2, {}, n_extra=105), None, EVENING)[1] == "suspect"
    assert q(store, "SELECT count(*) FROM official_prices WHERE trade_date=?", (d2.isoformat(),))[0][0] == 0


def test_first_collection_records_no_baseline(store):
    store.record_collection(ENTRY, "KOSPI", stock_rows(ENTRY, {"000001": ("1", "1")}), None, EVENING)
    assert '"no_count_baseline"' in q(store, "SELECT reasons FROM price_collections")[0][0]


def test_failed_partial_do_not_occupy_key_then_complete(store):
    store.record_collection(ENTRY, "KOSPI", None, "boom", EVENING)
    store.record_collection(ENTRY, "KOSPI", stock_rows(ENTRY, {"000001": ("100", "110")}) * 2, None, EVENING)
    assert q(store, "SELECT count(*) FROM official_prices")[0][0] == 0
    cid, status = store.record_collection(ENTRY, "KOSPI", stock_rows(ENTRY, {"000001": ("100", "110")}), None, EVENING)
    assert status == "complete"
    assert q(store, "SELECT collection_id FROM official_prices")[0][0] == cid


def test_identical_recollection_allowed_different_is_conflict(store):
    rows = stock_rows(ENTRY, {"000001": ("100", "110"), "000002": ("50", "51")})
    first, _ = store.record_collection(ENTRY, "KOSPI", rows, None, EVENING)
    assert store.record_collection(ENTRY, "KOSPI", rows, None, EVENING)[1] == "complete"
    changed = stock_rows(ENTRY, {"000001": ("100", "999"), "000002": ("50", "51")})
    cid, status = store.record_collection(ENTRY, "KOSPI", changed, None, EVENING)
    assert status == "conflict"
    assert q(store, "SELECT close, collection_id FROM official_prices WHERE ticker='000001'") == [(110.0, first)]
    assert q(store, "SELECT ticker FROM price_conflicts WHERE collection_id=?", (cid,)) == [("000001",)]
    extra = stock_rows(ENTRY, {"000001": ("100", "110"), "000002": ("50", "51"), "000003": ("1", "1")})
    assert store.record_collection(ENTRY, "KOSPI", extra, None, EVENING)[1] == "conflict"


def test_price_insert_failure_rolls_back_complete(store, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk")
    monkeypatch.setattr(store, "_insert_prices", boom)
    with pytest.raises(RuntimeError):
        store.record_collection(ENTRY, "KOSPI", stock_rows(ENTRY, {"000001": ("100", "110")}), None, EVENING)
    assert q(store, "SELECT count(*) FROM price_collections")[0][0] == 0
    assert q(store, "SELECT count(*) FROM official_prices")[0][0] == 0


def test_price_records_immutable_and_direct_insert_requires_complete(store):
    cid, _ = store.record_collection(ENTRY, "KOSPI", None, "boom", EVENING)
    with sqlite3.connect(store.path) as c:
        with pytest.raises(sqlite3.DatabaseError):
            c.execute("INSERT INTO official_prices VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("2026-10-14", "KOSPI", "Z", 1, 1, 1, 1, 1, 1, "20261014", "{}", "h", cid))
    store.record_collection(ENTRY, "KOSPI", stock_rows(ENTRY, {"000001": ("100", "110")}), None, EVENING)
    for sql in ("UPDATE official_prices SET close=1", "DELETE FROM official_prices",
                "UPDATE price_collections SET status='complete'", "DELETE FROM price_collections"):
        with sqlite3.connect(store.path) as c, pytest.raises(sqlite3.DatabaseError):
            c.execute(sql)


def test_collect_guards_calendar_and_same_day_time(store):
    cal = long_calendar()
    fetch = lambda m, d: stock_rows(d, {"000001": ("1", "1")}) if m != "KOSPI_INDEX" else index_rows(d)
    with pytest.raises(ValueError, match="not_a_verified_session"):
        store.collect(HOLIDAY, "KOSPI", fetch, cal, EVENING)
    with pytest.raises(ValueError, match="not_a_verified_session"):
        store.collect(date(2027, 1, 4), "KOSPI", fetch, cal, EVENING)          # 달력 밖 = unknown
    with pytest.raises(ValueError, match="too_early"):
        store.collect(ENTRY, "KOSPI", fetch, cal, datetime(2026, 10, 14, 18, 29, tzinfo=KST))
    assert store.collect(ENTRY, "KOSPI", fetch, cal, datetime(2026, 10, 14, 18, 30, tzinfo=KST))[1] == "complete"
    _, status = store.collect(date(2026, 10, 15), "KOSPI", lambda m, d: (_ for _ in ()).throw(TimeoutError("t")), cal, EVENING)
    assert status == "failed"


def test_T25b_zero_and_blank_prices_stored_as_evidence():
    status, _, parsed, _ = validate_response("KOSPI", ENTRY, stock_rows(ENTRY, {"000001": ("0", "100"), "000002": ("", "")}), None, None)
    assert status == "complete"
    assert {p.ticker: (p.open, p.close) for p in parsed} == {"000001": (0.0, 100.0), "000002": (None, None)}


# ── 성과 (§7.3, §8) ─────────────────────────────────────────────────────────

def test_exit_session_counts_entry_as_day_one_and_skips_holidays():
    cal = long_calendar()
    assert O.exit_session(ENTRY, 1, cal) == ENTRY
    assert O.exit_session(ENTRY, 3, cal) == date(2026, 10, 16)
    assert O.exit_session(ENTRY, 5, cal) == date(2026, 10, 21)           # 10/17·18 주말, 10/20 휴장
    assert O.exit_session(ENTRY, 20, cal) == date(2026, 11, 11)
    assert O.exit_session(date(2026, 12, 30), 3, cal) is None            # 달력 밖은 unknown


def test_T27_hand_calculated_returns(store):
    key = make_cohort(store, ["000001"])
    cal = long_calendar()
    exit_d = O.exit_session(ENTRY, 3, cal)
    collect_day(store, ENTRY, {"000001": ("100", "105")}, idx=("2,000", "2,010"))
    collect_day(store, date(2026, 10, 15), {"000001": ("106", "107")}, idx=("2,010", "2,020"))
    collect_day(store, exit_d, {"000001": ("108", "110")}, idx=("2,030", "2,040"))
    counts = O.fill_outcomes(store, cal, exit_d, EVENING, windows=(3,))
    assert counts["filled"] == 1
    row = dict(zip(["entry_open", "exit_close", "kospi_entry_open", "kospi_exit_close", "r_gross",
                    "r_net_rt20", "r_net_rt50", "r_net_rt100", "kospi_return", "alpha_gross", "alpha_net_rt20"],
                   q(store, """SELECT entry_open, exit_close, kospi_entry_open, kospi_exit_close, r_gross,
                       r_net_rt20, r_net_rt50, r_net_rt100, kospi_return, alpha_gross, alpha_net_rt20
                       FROM cohort_outcomes""")[0]))
    assert row["entry_open"] == 100 and row["exit_close"] == 110          # 진입 시가 → 3번째 거래일 종가
    assert row["kospi_entry_open"] == 2000 and row["kospi_exit_close"] == 2040
    assert row["r_gross"] == pytest.approx(0.10)
    assert row["r_net_rt20"] == pytest.approx(0.999 * 110 / (1.001 * 100) - 1)
    assert row["r_net_rt50"] == pytest.approx(0.9975 * 110 / (1.0025 * 100) - 1)
    assert row["r_net_rt100"] == pytest.approx(0.995 * 110 / (1.005 * 100) - 1)
    assert row["kospi_return"] == pytest.approx(0.02)
    assert row["alpha_gross"] == pytest.approx(0.08)
    assert row["alpha_net_rt20"] == pytest.approx(row["r_net_rt20"] - 0.02)  # 벤치마크 비용 미적용
    perf = O.cohort_performance(store, key, window=3, cost="gross")
    assert perf["complete"] and perf["n_selected"] == 1
    assert perf["cohort_return"] == pytest.approx(0.1 * 0.10)             # 9개 슬롯은 현금
    assert perf["member_avg_return"] == pytest.approx(0.10)


def test_T20_real_session_collection_failure_never_moves_entry(store):
    make_cohort(store, ["000001"])
    cal = long_calendar()
    store.record_collection(ENTRY, "KOSPI", None, "timeout", EVENING)    # 진입일 수집 실패
    for d in (date(2026, 10, 15), date(2026, 10, 16)):
        collect_day(store, d, {"000001": ("120", "121")})
    view = O.evaluate(store, ENTRY, "000001", 3, cal, date(2026, 10, 16))
    assert view.status == "waiting_price" and view.reason == "entry_market_incomplete"
    assert O.fill_outcomes(store, cal, date(2026, 10, 16), EVENING, windows=(3,))["pending"] == 1
    collect_day(store, ENTRY, {"000001": ("100", "105")})                # 재수집 성공
    O.fill_outcomes(store, cal, date(2026, 10, 16), EVENING, windows=(3,))
    assert q(store, "SELECT entry_session, entry_open FROM cohort_outcomes") == [("2026-10-14", 100.0)]


def test_T20b_wait_warning_after_five_sessions(store):
    cal = long_calendar()
    # 진입 이후 세션: 10/15, 16, 19, 21(10/20 휴장), 22 → 5번째가 10/22
    assert O.evaluate(store, ENTRY, "000001", 20, cal, date(2026, 10, 21)).status == "waiting_price"
    assert O.evaluate(store, ENTRY, "000001", 20, cal, date(2026, 10, 22)).status == "price_wait_warning"


def test_T19_T24_holiday_and_unknown_calendar(store):
    cal = long_calendar(unknown_after=date(2026, 10, 15))
    assert O.evaluate(store, ENTRY, "000001", 3, cal, date(2026, 10, 30)).status == "calendar_unknown"
    closed = long_calendar(extra_holidays=(ENTRY,))                       # 사후 임시 휴장
    v = O.evaluate(store, ENTRY, "000001", 3, closed, date(2026, 10, 30))
    assert v.status == "price_unavailable" and v.reason == "entry_session_closed"


def test_T25_halted_member_only_unavailable(store):
    key = make_cohort(store, ["000001", "000002"])
    cal = long_calendar()
    collect_day(store, ENTRY, {"000001": ("100", "100")})                 # 000002 행 없음(거래정지)
    collect_day(store, date(2026, 10, 15), {"000001": ("100", "100")})
    collect_day(store, date(2026, 10, 16), {"000001": ("100", "110")})
    O.fill_outcomes(store, cal, date(2026, 10, 16), EVENING, windows=(3,))
    got = dict(q(store, "SELECT ticker, status FROM cohort_outcomes"))
    assert got == {"000001": "filled", "000002": "price_unavailable"}
    perf = O.cohort_performance(store, key, window=3, cost="gross")
    assert not perf["complete"] and perf["cohort_return"] is None and perf["n_unavailable"] == 1


def test_T25b_zero_open_unusable(store):
    make_cohort(store, ["000001"])
    cal = long_calendar()
    collect_day(store, ENTRY, {"000001": ("0", "100")})
    v = O.evaluate(store, ENTRY, "000001", 3, cal, date(2026, 10, 16))
    assert v.status == "price_unavailable" and v.reason == "entry_price_unusable"


def test_T26_outcomes_never_change_eligibility_and_are_immutable(store):
    key = make_cohort(store, ["000001"])
    before = q(store, "SELECT oos_type, eligible, exclusion_reasons FROM official_cohorts")
    cal = long_calendar()
    for d, p in ((ENTRY, ("100", "100")), (date(2026, 10, 15), ("100", "100")), (date(2026, 10, 16), ("100", "110"))):
        collect_day(store, d, {"000001": p})
    O.fill_outcomes(store, cal, date(2026, 10, 16), EVENING, windows=(3,))
    assert q(store, "SELECT oos_type, eligible, exclusion_reasons FROM official_cohorts") == before
    cols = [r[1] for r in q(store, "PRAGMA table_info(cohort_outcomes)")]
    assert not {"eligible", "oos_type"} & set(cols)
    second = O.fill_outcomes(store, cal, date(2026, 10, 16), EVENING, windows=(3,))
    assert second["existing"] == 1 and second["filled"] == 0
    with sqlite3.connect(store.path) as c, pytest.raises(sqlite3.DatabaseError):
        c.execute("UPDATE cohort_outcomes SET r_gross=1")


def test_not_due_and_no_market_ohlcv_dependency(store):
    cal = long_calendar()
    assert O.evaluate(store, ENTRY, "000001", 20, cal, date(2026, 10, 13)).status == "not_due"
    names = {r[0] for r in q(store, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "market_ohlcv" not in names
