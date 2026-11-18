from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from analyzer.data.momentum import (
    MomentumData, MomentumSignal,
    _fetch_kospi_series, _fetch_ticker_series, _nearest_price, _pct,
    _LOOKBACK,
)
from constants import ENGINE_VERSION as _ENGINE_VERSION

_LOG_DIR = Path(__file__).parent.parent.parent / "data" / "momentum_log"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

def _log_path(ticker: str) -> Path:
    return _LOG_DIR / f"{ticker}.jsonl"


def get_log_dir() -> Path:
    return _LOG_DIR


def load_raw_log(ticker: str) -> list[dict]:
    return _load_raw(ticker)


def _score_bucket(score: Optional[int]) -> Optional[str]:
    if score is None:
        return None
    if score >= 90:
        return "90+"
    if score >= 80:
        return "80~89"
    if score >= 70:
        return "70~79"
    return "<70"


def _median(sorted_list: list[float]) -> float:
    n = len(sorted_list)
    if n % 2:
        return sorted_list[n // 2]
    return (sorted_list[n // 2 - 1] + sorted_list[n // 2]) / 2


def _load_raw(ticker: str) -> list[dict]:
    log_file = _log_path(ticker)
    if not log_file.exists():
        return []
    entries = []
    with log_file.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries


def _fetch_price_spot(ticker: str, as_of: date) -> Optional[float]:
    start = as_of - timedelta(days=_LOOKBACK)
    series = _fetch_ticker_series(ticker, start, as_of)
    return _nearest_price(series, as_of, max_gap=7)


def _fetch_kospi_spot(as_of: date) -> Optional[float]:
    start = as_of - timedelta(days=_LOOKBACK)
    series = _fetch_kospi_series(start, as_of)
    return _nearest_price(series, as_of, max_gap=7)


def log_signal(
    ticker: str,
    as_of: date,
    mom: MomentumData,
    price: Optional[float] = None,
    market_state: Optional[str] = None,
    score: Optional[int] = None,
    grade: Optional[str] = None,
    engine_version: Optional[str] = None,
) -> None:
    if mom.signal == MomentumSignal.UNAVAILABLE:
        return

    existing = _load_raw(ticker)
    if any(e["date"] == str(as_of) for e in existing):
        return

    if price is None:
        price = _fetch_price_spot(ticker, as_of)

    prev_entry = existing[-1] if existing else None
    prev_signal = prev_entry["signal"] if prev_entry else None
    days_since_prev = (
        (as_of - date.fromisoformat(prev_entry["date"])).days
        if prev_entry else None
    )

    entry = {
        "date":             str(as_of),
        "logged_at":        datetime.now().isoformat(timespec="seconds"),
        "signal":           mom.signal.value,
        "prev_signal":      prev_signal,
        "days_since_prev":  days_since_prev,
        "strength":         round(mom.strength, 3),
        "positive_count":   mom.positive_count,
        "negative_count":   mom.negative_count,
        "rel_1m":           round(mom.rel_1m, 3) if mom.rel_1m is not None else None,
        "rel_3m":           round(mom.rel_3m, 3) if mom.rel_3m is not None else None,
        "rel_6m":           round(mom.rel_6m, 3) if mom.rel_6m is not None else None,
        "score":            score,
        "score_bucket":     _score_bucket(score),
        "grade":            grade,
        "price_at_signal":  price,
        "market_state":     market_state,
        "is_market_ignore": mom.is_value_trap,
        "engine_version":   engine_version or _ENGINE_VERSION,
    }

    with _log_path(ticker).open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _build_stat(returns: list[float], alphas: list[float], unres: int) -> dict:
    returns = sorted(returns)
    alphas  = sorted(alphas)
    n  = len(returns)
    na = len(alphas)
    total = n + unres
    return {
        "count":              n,
        "alpha_count":        na,
        "unresolvable":       unres,
        "unresolvable_rate":  round(unres / total * 100, 1) if total > 0 else 0.0,
        "avg":                round(sum(returns) / n, 2)                          if n  else None,
        "median":             round(_median(returns), 2)                          if n  else None,
        "win_rate":           round(sum(1 for r in returns if r > 0) / n * 100, 1) if n else None,
        "best":               round(max(returns), 2)                              if n  else None,
        "worst":              round(min(returns), 2)                              if n  else None,
        "avg_alpha":          round(sum(alphas) / na, 2)                          if na else None,
        "median_alpha":       round(_median(alphas), 2)                           if na else None,
    }


def compute_forward_returns(ticker: str, forward_days: int = 30) -> list[dict]:
    entries = _load_raw(ticker)
    if not entries:
        return []

    results = []
    for entry in entries:
        price_then = entry.get("price_at_signal")
        if price_then is None:
            results.append({**entry, "forward_return": None, "forward_alpha": None,
                            "forward_status": "no_entry_price", "forward_days": forward_days})
            continue

        signal_date = date.fromisoformat(entry["date"])
        target_date = signal_date + timedelta(days=forward_days)

        if target_date > date.today():
            results.append({**entry, "forward_return": None, "forward_alpha": None,
                            "forward_status": "pending", "forward_days": forward_days})
            continue

        price_now  = _fetch_price_spot(ticker, target_date)
        kospi_then = _fetch_kospi_spot(signal_date)
        kospi_now  = _fetch_kospi_spot(target_date)

        if price_now is None:
            results.append({**entry, "forward_return": None, "forward_alpha": None,
                            "forward_status": "unresolvable", "forward_days": forward_days})
            continue

        fwd_return   = _pct(price_now, price_then)
        kospi_return = _pct(kospi_now, kospi_then) if (kospi_now and kospi_then) else None
        fwd_alpha    = (fwd_return - kospi_return) if (fwd_return is not None and kospi_return is not None) else None

        results.append({
            **entry,
            "forward_return": round(fwd_return, 2)  if fwd_return  is not None else None,
            "forward_alpha":  round(fwd_alpha, 2)   if fwd_alpha   is not None else None,
            "forward_status": "resolved",
            "forward_days":   forward_days,
        })

    return results


def _iter_resolved(tickers: list[str], forward_days: int):
    for ticker in tickers:
        yield from compute_forward_returns(ticker, forward_days)


def get_signal_stats(tickers: list[str], forward_days: int = 30) -> dict[str, dict]:
    sig_returns:    dict[str, list[float]] = defaultdict(list)
    sig_alphas:     dict[str, list[float]] = defaultdict(list)
    sig_unres:      dict[str, int]         = defaultdict(int)
    combo_returns:  dict[tuple, list[float]] = defaultdict(list)  # (signal, grade)
    bucket_returns: dict[tuple, list[float]] = defaultdict(list)  # (signal, score_bucket)
    mi_returns:     list[float] = []   # is_market_ignore == True
    mi_alphas:      list[float] = []
    mi_unres:       int = 0

    for r in _iter_resolved(tickers, forward_days):
        sig = r["signal"]

        if r["forward_status"] == "unresolvable":
            sig_unres[sig] += 1
            if r.get("is_market_ignore"):
                mi_unres += 1
            continue

        fwd = r.get("forward_return")
        alp = r.get("forward_alpha")

        if fwd is not None:
            sig_returns[sig].append(fwd)
            if r.get("grade"):
                combo_returns[(sig, r["grade"])].append(fwd)
            if r.get("score_bucket"):
                bucket_returns[(sig, r["score_bucket"])].append(fwd)
            if r.get("is_market_ignore"):
                mi_returns.append(fwd)
        if alp is not None:
            sig_alphas[sig].append(alp)
            if r.get("is_market_ignore"):
                mi_alphas.append(alp)

    stats: dict[str, dict] = {}
    for sig in set(sig_returns) | set(sig_unres):
        stats[sig] = _build_stat(sig_returns[sig], sig_alphas.get(sig, []), sig_unres[sig])

    stats["_market_ignore"] = _build_stat(mi_returns, mi_alphas, mi_unres)

    stats["_combo"] = {
        f"{sig}+{grade}": {"count": len(r), "avg": round(sum(r)/len(r), 2) if r else None}
        for (sig, grade), r in combo_returns.items() if r
    }

    stats["_score_bucket"] = {
        f"{sig}+{bucket}": {"count": len(r), "avg": round(sum(r)/len(r), 2) if r else None}
        for (sig, bucket), r in bucket_returns.items() if r
    }

    return stats


def get_transition_stats(tickers: list[str], forward_days: int = 30) -> dict[str, dict]:
    transitions: dict[str, list[float]] = defaultdict(list)
    alphas:      dict[str, list[float]] = defaultdict(list)

    for r in _iter_resolved(tickers, forward_days):
        prev = r.get("prev_signal")
        sig  = r["signal"]
        if not prev or prev == sig:
            continue
        key = f"{prev}→{sig}"
        if r.get("forward_return") is not None:
            transitions[key].append(r["forward_return"])
        if r.get("forward_alpha") is not None:
            alphas[key].append(r["forward_alpha"])

    result = {}
    for key, rets in transitions.items():
        n  = len(rets)
        na = len(alphas.get(key, []))
        alp = sorted(alphas.get(key, []))
        result[key] = {
            "count":     n,
            "avg":       round(sum(rets) / n, 2)                          if n  else None,
            "win_rate":  round(sum(1 for r in rets if r > 0) / n * 100, 1) if n else None,
            "avg_alpha": round(sum(alp) / na, 2)                          if na else None,
        }

    return dict(sorted(result.items(), key=lambda x: -(x[1]["count"])))


def get_market_state_distribution(tickers: list[str], forward_days: int = 30) -> dict[str, dict]:
    ms_sig_returns: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    ms_sig_count:   dict[str, dict[str, int]]          = defaultdict(lambda: defaultdict(int))

    for r in _iter_resolved(tickers, forward_days):
        ms = r.get("market_state")
        if not ms:
            continue
        sig = r["signal"]
        ms_sig_count[ms][sig] += 1
        if r.get("forward_return") is not None:
            ms_sig_returns[ms][sig].append(r["forward_return"])

    result = {}
    for ms in ms_sig_count:
        result[ms] = {}
        for sig in ms_sig_count[ms]:
            rets = ms_sig_returns[ms].get(sig, [])
            result[ms][sig] = {
                "total": ms_sig_count[ms][sig],
                "avg":   round(sum(rets) / len(rets), 2) if rets else None,
            }
    return result
