"""Read-only accounting projections of the provider call history.

Usage is measured separately from a turn's success. Missing usage and pricing
remain unknown; this projection never changes admission or agent settings.
"""
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import time
from zoneinfo import ZoneInfo


def _grade(call):
    result = Counter(calls=1)
    status = call.get("status", "pending")
    result[status + "_calls"] = 1
    usage = call.get("usage", {})
    counts = usage.get("counts", {})
    if status == "failed" and call.get("failure", {}).get("sent") is False and not usage:
        counts = dict.fromkeys(("input_tokens", "cached_tokens", "output_tokens"), 0)
        result["not_submitted_calls"] = 1
    for name in ("input_tokens", "cached_tokens", "output_tokens"):
        value = counts.get(name)
        if type(value) is int and value >= 0:
            result["known_" + name] = value
        else:
            result[name + "_unknown_calls"] = 1
    return result


def report(path, *, since=0, until=None, zone="UTC"):
    """Group calls by admission time; use a consistent, non-writing snapshot."""
    until = time.time() if until is None else float(until)
    since = float(since)
    if until < since:
        raise ValueError("until precedes since")
    tz = ZoneInfo(zone)
    value = dict(schema=1, scope="local call history; not account-wide billing",
                 since=since, until=until, timezone=zone, window="admission time",
                 billing_reconciled=False, monetary_cost=None, groups=[], totals={})
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        conn.execute("BEGIN")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "llm_call_history" not in tables:
            value.update(history_available=False, coverage_complete=False,
                         note="No call history: historical usage is unrecorded, not zero.")
            return value
        started = conn.execute("SELECT observed FROM llm_call_history "
                               "WHERE call_id='' AND event='recording-started'").fetchone()
        legacy = (conn.execute("SELECT COUNT(*) FROM requests r WHERE r.started>=? AND r.started<? "
                  "AND NOT EXISTS (SELECT 1 FROM llm_call_history h "
                  "WHERE h.call_id=r.id AND h.event='admitted')", (since, until))
                  .fetchone()[0] if "requests" in tables else 0)
        value.update(history_available=True, recording_started=started[0] if started else None,
                     legacy_request_rows_without_history=legacy,
                     coverage_complete=bool(started and since >= started[0] and not legacy))
        rows = conn.execute(
            "SELECT h.call_id,h.event,h.detail,a.observed FROM llm_call_history h "
            "JOIN llm_call_history a ON a.call_id=h.call_id AND a.event='admitted' "
            "WHERE a.observed>=? AND a.observed<? ORDER BY h.sequence", (since, until))
        calls = {}
        for identity, event, detail, admitted in rows:
            call = calls.setdefault(identity, {"admitted": admitted})
            detail = json.loads(detail)
            if event in ("admitted", "request"):
                call.update(detail)
            elif event == "usage":
                call["usage"] = detail
            elif event in ("completed", "failed", "unknown"):
                call["status"] = event
                if event == "failed":
                    call["failure"] = detail
        groups = {}
        totals = Counter()
        for call in calls.values():
            key = (datetime.fromtimestamp(call["admitted"], tz).date().isoformat(),
                   call.get("instance", "unrecorded"), call.get("session", ""),
                   call.get("provider", "unrecorded"), call.get("model", "unrecorded"),
                   call.get("purpose", "unspecified"))
            grade = _grade(call)
            groups.setdefault(key, Counter()).update(grade)
            totals.update(grade)
        fields = ("day", "instance", "session", "provider", "model", "purpose")
        value["groups"] = [dict(zip(fields, key), **counts) for key, counts in sorted(groups.items())]
        value["totals"] = dict(totals)
        return value
    finally:
        conn.close()
