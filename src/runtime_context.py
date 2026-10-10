"""Bounded, non-authoritative observations for the managed model context.

Only metadata is projected. Reading a record never acknowledges input,
observes a command receipt, spends a budget or dispatches an effect. Missing,
stale and unsupported sources remain unknown. The measurements are sampled
independently, not an atomic snapshot of the whole agent.
"""
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time

MAX_CHARS = 2400
RECEIPT_WINDOW = 16
_NUMBER_LIMIT = 10**12
_REASONS = frozenset({"stale", "unknown-tool", "wrong-arity", "malformed-command",
                     "no-current-capture", "paused", "uncertain", "exhausted",
                     "cooldown", "authority", "request-budget", "output-token-budget",
                     "retry-budget", "provider-cooldown", "single-action-observation-boundary",
                     "host-transition-error"})


def _nat(value):
    return value if type(value) is int and 0 <= value <= _NUMBER_LIMIT else None


def _number(value):
    if type(value) not in (int, float):
        return None
    return float(value) if 0 <= value <= _NUMBER_LIMIT and math.isfinite(value) else None


def _age(stamp, now):
    stamp = _number(stamp)
    return int(now - stamp) if stamp is not None and 0 < stamp <= now else None


def _json(path, limit=65536):
    with Path(path).open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("metadata exceeds observation bound")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("metadata must be an object")
    return value


@contextlib.contextmanager
def _database(path):
    # mode=ro cannot create the database or mutate its tables. A deadline also
    # bounds reads of large or locked stores; unavailable is a valid reading.
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro",
                                 uri=True, timeout=.02)
    try:
        deadline = time.monotonic() + .025
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        connection.execute("PRAGMA query_only=ON")
        yield connection
    finally:
        connection.close()


def _provider(now):
    import cognitive_health
    value = _json(cognitive_health._path())
    if value.get("schema") != 2:
        raise ValueError("unsupported cognitive receipt schema")
    obligation = _number(value.get("obligation_since"))
    pending = _number(value.get("in_flight_since"))
    retry = _number(value.get("retry_at"))
    if obligation is None or pending is None or retry is None:
        raise ValueError("incomplete cognitive receipt")
    if (obligation > now or pending > now):
        raise ValueError("future cognitive receipt")
    call = {"phase": "idle"}
    if pending:
        call.update(phase="waiting", ageSeconds=_age(pending, now))
        if value.get("soft_wait_elapsed") is True:
            call["softWaitElapsed"] = True
        if type(value.get("response_bytes")) is int:
            call["transportBytes"] = max(0, value["response_bytes"])
        if type(value.get("transport_quiet_ms")) is int:
            call["transportQuietSeconds"] = max(0, value["transport_quiet_ms"]) // 1000
    elif value.get("last_outcome") == "failed":
        call.update(phase="backoff" if retry > now else "failed",
                    ageSeconds=_age(value.get("last_failed_at"), now))
        label = str(value.get("last_failure_type", ""))[:64].lower()
        call["reason"] = ("timeout" if "timeout" in label else
                          "rate-limit" if "rate" in label or "quota" in label else
                          label if label in _REASONS else "unclassified-failure")
        if retry > now:
            call["retrySeconds"] = math.ceil(retry - now)
    elif value.get("last_outcome") == "completed":
        call.update(phase="returned", ageSeconds=_age(value.get("last_completed_at"), now))
    try:
        path = os.environ.get("METTACLAW_LLM_COMPLETION_PATH") or str(Path(cognitive_health._path()).with_name("llm-completion.json"))
        completion = _json(path)
        stamp = _age(completion.get("observed_at"), now)
        if stamp is not None and stamp <= 3600:
            import provider_protocol
            call["lastCache"] = provider_protocol.cache_observation(completion.get("usage"))
            layout = completion.get("prompt_layout", {})
            if _nat(layout.get("stable_chars")) is not None:
                call["stablePrefixChars"] = layout["stable_chars"]
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return call, bool(obligation)


def _channel(now):
    import mode_channel
    value = _json(mode_channel.state() / "control-status.json")
    age = _age(value.get("observed_at"), now)
    ready = value.get("channel_ready")
    if age is None or age > 30 or type(ready) is not bool:
        raise ValueError("channel observation unavailable or stale")
    # This is local service readiness, not evidence of Telegram reachability.
    return ready


def _inbox(now):
    import mode_channel
    control = _json(mode_channel.state() / "control-status.json")
    control_age = _age(control.get("observed_at"), now)
    value = control.get("operator_inbox")
    if control_age is None or control_age > 30 or control.get("channel_ready") is not True or not isinstance(value, dict):
        raise ValueError("input observation unavailable or stale")
    sample_age = _age(value.get("observedAt"), now)
    count = _nat(value.get("pending"))
    if sample_age is None or sample_age > 30 or count is None:
        raise ValueError("input scan unavailable or stale")
    # Dates are Telegram message timestamps. Processing means durable history
    # and input acknowledgement, not that an answer or task was completed.
    return {"pending": count, "sampleAgeSeconds": sample_age,
            "newestAgeSeconds": _age(value.get("newestMessageAt"), now) if count else None,
            "oldestAgeSeconds": _age(value.get("oldestMessageAt"), now) if count else None}


def _reason(value):
    if isinstance(value, str):
        if value in _REASONS:
            return value
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return "other"
    reason = value.get("reason") if isinstance(value, dict) else None
    return reason if isinstance(reason, str) and reason in _REASONS else "other"


def _commands():
    import effect_receipts
    path = effect_receipts._path()
    if path is None:
        raise ValueError("receipt source unconfigured")
    lines = effect_receipts._tail_lines(path, max_bytes=180000)
    records = []
    for line in lines:
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("malformed receipt")
        if "command" in value or "commands" in value:
            records.append(value)
    records = records[-RECEIPT_WINDOW:]
    counts = {name: 0 for name in ("returned", "withheld", "deferred", "uncertain", "failed")}
    reasons = set()
    for record in records:
        kind = record.get("disposition")
        if kind in ("exception", "no_result"):
            kind = "failed"
        counts[kind if kind in counts else "uncertain"] += 1
        if kind != "returned":
            reasons.add(_reason(record.get("reason")))
    return {**counts, "windowRecords": len(records), "reasons": sorted(reasons)}


def _memory():
    import memory_health
    with _database(Path(memory_health._chroma_dir()) / "chroma.sqlite3") as db:
        row = db.execute("SELECT id FROM collections WHERE name=?",
                         (memory_health._collection(),)).fetchone()
        if row is None:
            raise ValueError("memory collection absent")
        cid = row[0]
        segments = dict((scope, sid) for sid, scope in db.execute(
            "SELECT id,scope FROM segments WHERE collection=?", (cid,)))
        meta, vector = segments.get("METADATA"), segments.get("VECTOR")
        if meta is None or vector is None:
            raise ValueError("index segment unavailable")
        seqs = dict(db.execute("SELECT segment_id,seq_id FROM max_seq_id WHERE segment_id IN (?,?)",
                               (meta, vector)))
        before, after = _nat(seqs.get(meta)), _nat(seqs.get(vector))
        if before is None or after is None or after > before:
            raise ValueError("index watermarks unavailable or inconsistent")
        docs = _nat(db.execute("SELECT COUNT(*) FROM embeddings WHERE segment_id=?", (meta,)).fetchone()[0])
        threshold = None
        columns = {row[1] for row in db.execute("PRAGMA table_info(collections)")}
        for column in ("config_json_str", "schema_str"):
            if column in columns:
                raw = db.execute("SELECT " + column + " FROM collections WHERE id=?", (cid,)).fetchone()[0]
                if raw and len(raw) <= 65536:
                    found = memory_health._threshold_from_value(json.loads(raw))
                    if _nat(found) is not None:
                        threshold = found
        if threshold is None:
            raise ValueError("persisted index threshold unavailable")
        return {"documents": docs, "persistedSequenceGap": before - after, "threshold": threshold}


def _background(now):
    import agent_checkpoint
    with _database(agent_checkpoint.path()) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(model_jobs)")}
        stamp = "finished_at" if "finished_at" in columns else "NULL"
        rows = db.execute("SELECT state,observed,COUNT(*),MIN(" + stamp + "),COUNT(" + stamp + ") "
                          "FROM model_jobs WHERE adopted=0 GROUP BY state,observed").fetchall()
    pending = review = uncertain = 0
    oldest, known = [], True
    for state, observed, count, finished, dated in rows:
        if state in ("queued", "running"):
            pending += count
        elif state == "uncertain":
            uncertain += count
        elif state in ("returned", "failed") and not observed:
            review += count
            age = _age(finished, now)
            known = known and dated == count and age is not None
            if age is not None:
                oldest.append(age)
    return {"pending": pending, "review": review, "uncertain": uncertain,
            "oldestReviewSeconds": max(oldest) if oldest and known else None}


def _budget(now):
    import llm_policy
    policy = llm_policy.configuration()
    if policy["profile"] != "managed" or not any(policy[name] for name in
            ("requests_per_hour", "output_tokens_per_day")):
        return "no-volume-caps", None
    with _database(llm_policy.ledger_path()) as db:
        hour = db.execute("SELECT COUNT(*) FROM requests WHERE started>?", (now - 3600,)).fetchone()[0]
        day = int(now // 86400) * 86400
        output = db.execute("SELECT COALESCE(SUM(COALESCE(output,reserved)),0) FROM requests WHERE started>=?",
                            (day,)).fetchone()[0]
    limits = (policy["requests_per_hour"], policy["output_tokens_per_day"])
    if not ((limits[0] and hour >= limits[0]) or (limits[1] and output >= limits[1])):
        return "within-caps", None
    numbers = [hour, output, *limits]
    if any(_nat(value) is None for value in numbers):
        raise ValueError("unsupported budget observation")
    return "at-cap", dict(zip(("attemptsHour", "accountedOutputToday", "requestsHourLimit", "outputDayLimit"), numbers))


def read(now=None):
    now = time.time() if now is None else float(now)
    facts = {"schema": 1, "sampledAt": int(now), "call": {"phase": "unknown"},
             "turnPending": None, "localChannelReady": None, "humanInbox": None,
             "commands": None, "memory": None, "background": None, "budgetStatus": "unknown"}
    unavailable = []
    failures = (OSError, ValueError, TypeError, KeyError, sqlite3.Error, RuntimeError, RecursionError)
    try:
        facts["call"], facts["turnPending"] = _provider(now)
    except failures:
        unavailable.append("provider-and-settlement")
    for name, reader in (("localChannelReady", lambda: _channel(now)),
                         ("humanInbox", lambda: _inbox(now)), ("commands", _commands),
                         ("memory", _memory), ("background", lambda: _background(now))):
        try:
            facts[name] = reader()
        except failures:
            unavailable.append(name)
    try:
        facts["budgetStatus"], budget = _budget(now)
        if budget is not None:
            facts["budget"] = budget
    except failures:
        unavailable.append("budget")
    if unavailable:
        facts["unavailable"] = unavailable
    return facts


def render(facts=None):
    facts = read() if facts is None else facts
    body = json.dumps(facts, sort_keys=True, separators=(",", ":"), allow_nan=False)
    revision = hashlib.sha256(body.encode()).hexdigest()
    text = "SOURCE[runtime-state] revision=sha256:" + revision + "\nRUNTIME_STATE " + body + (
        "\nSampled facts; unavailable means unknown. Details: vitals, command-receipts, model-jobs.")
    if len(text) > MAX_CHARS:
        # Do not clip structured facts or turn partial information into health.
        return render({"schema": 1, "unavailable": ["projection-overflow"]})
    return text
