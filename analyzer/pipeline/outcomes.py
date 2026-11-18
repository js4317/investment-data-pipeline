"""Stage 3 outcome measurement (design §7.3, §7.4(b)(d), §8).

- Entry session is the confirmed `planned_entry_session`; it never moves.
- Prices come only from `official_prices` on dates where all three markets are complete.
- Only final results are stored (`filled`, `price_unavailable`); waiting states are computed.
- Costs are a fixed analysis assumption, not an estimate of actual costs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from database.price_store import INDEX_TICKER, OfficialPriceStore
from database.signal_store import _json, _stamp
from database.trading_calendar import CalendarSnapshot

PRIMARY_WINDOW = 20
OFFICIAL_WINDOWS = (20, 60, 120)
COSTS_PER_SIDE = {"rt20": 0.0010, "rt50": 0.0025, "rt100": 0.0050}   # 왕복 20 / 50 / 100bp
WAIT_WARNING_SESSIONS = 5
SLOT_WEIGHT = 0.10

OUTCOME_SCHEMA = """
CREATE TABLE IF NOT EXISTS cohort_outcomes (
 cohort_key TEXT NOT NULL, ticker TEXT NOT NULL, horizon INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('filled','price_unavailable')), reason TEXT,
 entry_session TEXT NOT NULL, exit_session TEXT NOT NULL,
 entry_open REAL, exit_close REAL, kospi_entry_open REAL, kospi_exit_close REAL,
 r_gross REAL, r_net_rt20 REAL, r_net_rt50 REAL, r_net_rt100 REAL, kospi_return REAL,
 alpha_gross REAL, alpha_net_rt20 REAL, alpha_net_rt50 REAL, alpha_net_rt100 REAL,
 calendar_version TEXT NOT NULL, filled_at TEXT NOT NULL,
 PRIMARY KEY(cohort_key,ticker,horizon),
 FOREIGN KEY(cohort_key,ticker) REFERENCES cohort_members(cohort_key,ticker)
);
CREATE TRIGGER IF NOT EXISTS immutable_cohort_outcomes_UPDATE BEFORE UPDATE ON cohort_outcomes
BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER IF NOT EXISTS immutable_cohort_outcomes_DELETE BEFORE DELETE ON cohort_outcomes
BEGIN SELECT RAISE(ABORT,'immutable record'); END;
"""


def initialize_outcomes(store: OfficialPriceStore) -> None:
    store.initialize()
    with store._connection() as conn:
        conn.executescript(OUTCOME_SCHEMA)


def net_return(entry: float, exit_: float, cost_per_side: float) -> float:
    return (1 - cost_per_side) * exit_ / ((1 + cost_per_side) * entry) - 1


def exit_session(entry: date, n: int, calendar: CalendarSnapshot) -> date | None:
    """N번째 거래일 (진입 세션 = 1번째). 달력에 unknown이 끼면 None."""
    if calendar.status(entry) != "session":
        return None
    day = entry
    for _ in range(n - 1):
        day = calendar.next_session_after(day)
        if day is None:
            return None
    return day


def _sessions_after(day: date, today: date, calendar: CalendarSnapshot) -> int:
    count, cur = 0, day
    while True:
        cur = calendar.next_session_after(cur)
        if cur is None or cur > today:
            return count
        count += 1


def _usable(value) -> bool:
    return value is not None and value > 0


@dataclass(frozen=True)
class OutcomeView:
    status: str          # filled / price_unavailable / waiting_price / price_wait_warning / calendar_unknown / not_due
    reason: str | None
    row: dict | None = None


def evaluate(store: OfficialPriceStore, entry: date, ticker: str, window: int,
             calendar: CalendarSnapshot, today: date) -> OutcomeView:
    """Compute one (member, window) outcome. Pure read; never stores."""
    if calendar.status(entry) == "unknown":
        return OutcomeView("calendar_unknown", "entry_calendar_unknown")
    if calendar.status(entry) != "session":
        return OutcomeView("price_unavailable", "entry_session_closed")
    exit_d = exit_session(entry, window, calendar)
    if exit_d is None:
        return OutcomeView("calendar_unknown", "exit_calendar_unknown")

    def wait(day: date, what: str) -> OutcomeView:
        late = _sessions_after(day, today, calendar) >= WAIT_WARNING_SESSIONS
        return OutcomeView("price_wait_warning" if late else "waiting_price", what)

    if entry > today:
        return OutcomeView("not_due", None)
    if not store.date_complete(entry):
        return wait(entry, "entry_market_incomplete")
    stock_in, idx_in = store.official_price(entry, ticker), store.official_price(entry, INDEX_TICKER)
    if stock_in is None or not _usable(stock_in["open"]):
        return OutcomeView("price_unavailable", "entry_price_unusable", _base(entry, exit_d, stock_in, idx_in))
    if idx_in is None or not _usable(idx_in["open"]):
        return OutcomeView("price_unavailable", "index_entry_unusable", _base(entry, exit_d, stock_in, idx_in))

    if exit_d > today:
        return OutcomeView("not_due", None)
    if not store.date_complete(exit_d):
        return wait(exit_d, "exit_market_incomplete")
    stock_out, idx_out = store.official_price(exit_d, ticker), store.official_price(exit_d, INDEX_TICKER)
    base = _base(entry, exit_d, stock_in, idx_in, stock_out, idx_out)
    if stock_out is None or not _usable(stock_out["close"]):
        return OutcomeView("price_unavailable", "exit_price_unusable", base)
    if idx_out is None or not _usable(idx_out["close"]):
        return OutcomeView("price_unavailable", "index_exit_unusable", base)

    e, x = stock_in["open"], stock_out["close"]
    ke, kx = idx_in["open"], idx_out["close"]
    r_k = kx / ke - 1
    row = dict(base, r_gross=x / e - 1, kospi_return=r_k)
    row["alpha_gross"] = row["r_gross"] - r_k
    for label, c in COSTS_PER_SIDE.items():
        row[f"r_net_{label}"] = net_return(e, x, c)
        row[f"alpha_net_{label}"] = row[f"r_net_{label}"] - r_k     # 벤치마크에는 비용 미적용
    return OutcomeView("filled", None, row)


def _base(entry, exit_d, stock_in=None, idx_in=None, stock_out=None, idx_out=None) -> dict:
    return dict(entry_session=entry.isoformat(), exit_session=exit_d.isoformat(),
                entry_open=stock_in["open"] if stock_in else None,
                exit_close=stock_out["close"] if stock_out else None,
                kospi_entry_open=idx_in["open"] if idx_in else None,
                kospi_exit_close=idx_out["close"] if idx_out else None)


_COLS = ("entry_session", "exit_session", "entry_open", "exit_close", "kospi_entry_open",
         "kospi_exit_close", "r_gross", "r_net_rt20", "r_net_rt50", "r_net_rt100", "kospi_return",
         "alpha_gross", "alpha_net_rt20", "alpha_net_rt50", "alpha_net_rt100")


def fill_outcomes(store: OfficialPriceStore, calendar: CalendarSnapshot, today: date,
                  now: datetime, windows=OFFICIAL_WINDOWS) -> dict[str, int]:
    """Store final outcomes only. Idempotent: existing rows are never touched."""
    counts = {"filled": 0, "price_unavailable": 0, "pending": 0, "existing": 0}
    with store._connection() as conn:
        members = conn.execute("""SELECT m.cohort_key, m.ticker, c.planned_entry_session
            FROM cohort_members m JOIN official_cohorts c ON c.cohort_key=m.cohort_key
            WHERE c.planned_entry_session IS NOT NULL ORDER BY m.cohort_key, m.rank""").fetchall()
    for m in members:
        entry = date.fromisoformat(m["planned_entry_session"])
        for w in windows:
            with store._transaction() as conn:
                if conn.execute("SELECT 1 FROM cohort_outcomes WHERE cohort_key=? AND ticker=? AND horizon=?",
                                (m["cohort_key"], m["ticker"], w)).fetchone():
                    counts["existing"] += 1
                    continue
                view = evaluate(store, entry, m["ticker"], w, calendar, today)
                if view.status not in ("filled", "price_unavailable") or view.row is None:
                    counts["pending"] += 1
                    continue
                row = {c: view.row.get(c) for c in _COLS}
                conn.execute(f"""INSERT INTO cohort_outcomes(cohort_key,ticker,horizon,status,reason,
                    {','.join(_COLS)},calendar_version,filled_at)
                    VALUES(?,?,?,?,?,{','.join('?' for _ in _COLS)},?,?)""",
                    (m["cohort_key"], m["ticker"], w, view.status, view.reason,
                     *row.values(), calendar.version, _stamp(now)))
                counts[view.status] += 1
    return counts


def cohort_performance(store: OfficialPriceStore, cohort_key: str, window: int = PRIMARY_WINDOW,
                       cost: str = "rt20") -> dict:
    """Member average and 10-slot cohort return (empty slots = cash 0). Incomplete if any member missing."""
    with store._connection() as conn:
        n_selected = conn.execute("SELECT n_selected FROM official_cohorts WHERE cohort_key=?",
                                  (cohort_key,)).fetchone()[0]
        rows = conn.execute("SELECT status, r_gross, r_net_rt20, r_net_rt50, r_net_rt100, "
                            "alpha_gross, alpha_net_rt20, alpha_net_rt50, alpha_net_rt100 "
                            "FROM cohort_outcomes WHERE cohort_key=? AND horizon=?",
                            (cohort_key, window)).fetchall()
    filled = [r for r in rows if r["status"] == "filled"]
    n_unavail = sum(r["status"] == "price_unavailable" for r in rows)
    complete = len(filled) == n_selected
    key_r, key_a = ("r_gross", "alpha_gross") if cost == "gross" else (f"r_net_{cost}", f"alpha_net_{cost}")
    out = dict(cohort_key=cohort_key, window=window, cost=cost, n_selected=n_selected,
               n_filled=len(filled), n_unavailable=n_unavail,
               n_pending=n_selected - len(rows), complete=complete)
    if complete and n_selected:
        out["member_avg_return"] = sum(r[key_r] for r in filled) / n_selected
        out["member_avg_alpha"] = sum(r[key_a] for r in filled) / n_selected
    else:
        out["member_avg_return"] = out["member_avg_alpha"] = None
    # 10개 슬롯 × 10%, 빈 슬롯은 현금(0). 결측 종목이 있으면 계산하지 않는다.
    out["cohort_return"] = sum(SLOT_WEIGHT * r[key_r] for r in filled) if complete else None
    return out
