"""Read-only accounting projections of the provider call history.

Usage is measured separately from a turn's success. Missing usage and pricing
remain unknown; this projection never changes admission or agent settings.
"""
from collections import Counter
from datetime import datetime
import json
import time
from zoneinfo import ZoneInfo


def _grade(call):
    result = Counter(calls=1)
    status = call.get("status", "pending")
    result[status + "_calls"] = 1
    usage = call.get("usage", {})
    counts = usage.get("counts", {})
    if status == "failed" and call.get("failure", {}).get("sent") is False and not usage:
        counts = dict.fromkeys(("input_tokens", "cached_tokens", "output_tokens", "cache_write_tokens"), 0)
        result["not_submitted_calls"] = 1
    else:
        counts = dict(counts, cache_write_tokens=usage.get("reported_usage", {}).get("cache_creation_input_tokens"))
    for name in ("input_tokens", "cached_tokens", "output_tokens", "cache_write_tokens"):
        value = counts.get(name)
        if type(value) is int and value >= 0:
            result["known_" + name] = value
        else:
            result[name + "_unknown_calls"] = 1
    return result


def _window(since, until, zone):
    until = time.time() if until is None else float(until)
    since = float(since)
    if until < since:
        raise ValueError("until precedes since")
    tz = ZoneInfo(zone)
    value = dict(schema=1, scope="local call history; not account-wide billing",
                 since=since, until=until, timezone=zone, window="admission time",
                 billing_reconciled=False, monetary_cost=None, groups=[], totals={})
    return since, until, tz, value


def report_native(*, since=0, until=None, zone="UTC"):
    """report() over the native LLM ledger of METTACLAW_INPUT_LEDGER_SOCKET (one snapshot per page),
    or None while the SQLite file is still the ledger."""
    import llm_policy
    since, until, tz, value = _window(since, until, zone)
    answer = llm_policy.native("llm-usage", (since, until))
    started, legacy, rows = answer[1], answer[2], answer[3]
    value.update(history_available=True, recording_started=started,
                 legacy_request_rows_without_history=legacy,
                 coverage_complete=bool(started is not None and since >= started and not legacy))
    return _graded(value, rows, tz)


def _graded(value, rows, tz):
    """The groups and totals of `value` from (call, event, detail, admitted) rows in history order."""
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
               call.get("purpose", "unspecified"), call.get("trigger", "unrecorded"))
        grade = _grade(call)
        groups.setdefault(key, Counter()).update(grade)
        totals.update(grade)
    fields = ("day", "instance", "session", "provider", "model", "purpose", "trigger")
    value["groups"] = [dict(zip(fields, key), **counts) for key, counts in sorted(groups.items())]
    value["totals"] = dict(totals)
    return value
