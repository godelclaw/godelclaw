"""Provider usage accounting and transport retry deadlines. No work quotas."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import struct
import time
import uuid


def _process_owner():
    pid = os.getpid()
    try:
        started = Path("/proc", str(pid), "stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        started = "portable"
    return str(pid) + ":" + started


_owner = _process_owner()


def _owner_live(owner):
    try:
        pid, started = owner.split(":")
        pid = int(pid)
        if pid <= 0:
            return False
        if started == "portable":
            if os.name != "posix":
                return True  # No safe cross-process probe on this platform.
            try:
                os.kill(pid, 0)
            except PermissionError:
                return True
            return True
        fields = Path("/proc", str(pid), "stat").read_text().rsplit(")", 1)[1].split()
        return fields[19] == started and fields[0] not in ("Z", "X")
    except (OSError, ValueError, IndexError):
        return False


# The native ledger (src/llm_ledger.metta, in the agent's ledger store) answers each operation
# once the LLM ledger has been migrated there. Until then it answers llm-legacy and the SQLite
# code below runs as before; without a ledger socket nothing is asked. A configured server that
# cannot be reached raises (input_ack.LedgerUnavailable), never a fallback to SQLite.
LEGACY = object()


def _request():
    return "rq1." + os.urandom(16).hex()


def _detail(detail):
    return json.dumps(detail, sort_keys=True, separators=(",", ":"))


def _argument(value):
    from execution_gateway.cetta_worker import S
    if value is None:
        return S("none")
    if isinstance(value, (tuple, list)):
        return tuple(_argument(item) for item in value)
    return value


def plain(value):
    """A native answer with true/false/none as Python values and exact floats as floats."""
    from execution_gateway.cetta_worker import FloatBits, Symbol
    if isinstance(value, FloatBits):
        return struct.unpack("<d", struct.pack("<Q", value.bits))[0]
    if isinstance(value, Symbol):
        return {"true": True, "false": False, "none": None}.get(value.name, value)
    if isinstance(value, tuple):
        return tuple(plain(item) for item in value)
    return value


def native(verb, arguments):
    """One operation on the native LLM ledger: its answer, or LEGACY while SQLite is the ledger."""
    if not os.environ.get("METTACLAW_INPUT_LEDGER_SOCKET"):
        return LEGACY
    import input_ack
    from execution_gateway.cetta_worker import S
    value = input_ack._ledger_call(verb, tuple(_argument(item) for item in arguments), 30)
    if value == S("llm-legacy"):
        return LEGACY
    return plain(value)


def _name(value):
    return getattr(value, "name", None)


def _refuse(verb, answer):
    if isinstance(answer, tuple) and len(answer) == 2 and _name(answer[0]) == "LedgerError":
        error = answer[1]
        if isinstance(error, tuple) and len(error) == 3 and _name(error[0]) == "PythonException":
            kind = {"IntegrityError": sqlite3.IntegrityError, "ValueError": ValueError,
                    "OverflowError": OverflowError}.get(_name(error[1]), RuntimeError)
            raise kind(error[2])
    import input_ack
    raise input_ack.LedgerUnavailable("LLM ledger %s: %r" % (verb, answer))


def changed(verb, answer):
    """The flag of a (LedgerChanged flag) answer; any other answer raises."""
    if isinstance(answer, tuple) and len(answer) == 2 and _name(answer[0]) == "LedgerChanged":
        return bool(answer[1])
    _refuse(verb, answer)


def fingerprint(request):
    return hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode()).hexdigest()


class AdmissionDenied(RuntimeError):
    def __init__(self, reason, retry_after):
        super().__init__(reason)
        self.reason = reason
        self.retry_after = max(0, float(retry_after))


def active_mode():
    return os.environ.get("METTACLAW_ACTIVE_LOOP_MODE", "godelclaw")


def configuration(mode=None):
    mode = mode or active_mode()
    path = os.environ.get("METTACLAW_LLM_POLICY_PATH")
    config = {}
    if path:
        # A configured but broken policy is an operator error, not permission
        # to silently remove a cap.
        config = json.loads(Path(path).read_text())
        if not isinstance(config, dict):
            raise ValueError("LLM policy must be an object")
    default = "upstream" if mode in ("iter", "omega") else "managed"
    profile = os.environ.get("METTACLAW_LLM_POLICY", config.get("profile", default))
    overrides = config.get("modes", {}).get(mode, {})
    profile = overrides.get("profile", profile)
    if profile not in ("upstream", "managed"):
        raise ValueError("LLM profile must be upstream or managed")
    result = {"profile": profile}
    fields = {"quota_cooldown_seconds": 300}
    for name, default_value in fields.items():
        raw = os.environ.get("METTACLAW_LLM_" + name.upper(),
                             overrides.get(name, config.get(name, default_value)))
        value = int(raw)
        if value < 0:
            raise ValueError("LLM policy limits must be nonnegative")
        result[name] = value
    return result


def ledger_path():
    explicit = os.environ.get("METTACLAW_LLM_LEDGER_PATH")
    if explicit:
        return Path(explicit)
    health = os.environ.get("METTACLAW_COGNITIVE_HEALTH_PATH")
    if health:
        return Path(health).with_name("llm-budget.sqlite3")
    home = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    return home / os.environ.get("METTACLAW_INSTANCE", "default") / "llm-budget.sqlite3"


def history_event(conn, ticket, event, detail, *, now=None):
    """Append one fact per call/event, in the transaction that owns the fact.

    This history has no admission authority and is not pruned with requests.
    Callers supply metadata/counters, never prompts, credentials or responses.
    """
    return conn.execute(
        "INSERT OR IGNORE INTO llm_call_history(call_id,event,observed,detail) VALUES(?,?,?,?)",
        (ticket, event, time.time() if now is None else now,
         json.dumps(detail, sort_keys=True, separators=(",", ":")))).rowcount == 1


def usage_evidence(usage):
    """Keep reported token counts; an absent counter is unknown, not zero."""
    def count(value):
        return value if type(value) is int and value >= 0 else None

    usage = usage if isinstance(usage, dict) else {}
    fields = ("prompt_tokens", "input_tokens", "completion_tokens", "output_tokens",
              "total_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
              "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "reasoning_tokens")
    reported = {key: value for key in fields
                if (value := count(usage.get(key))) is not None}
    for key in ("input_tokens_details", "prompt_tokens_details",
                "output_tokens_details", "completion_tokens_details"):
        if isinstance(usage.get(key), dict):
            details = {name: value for name in ("cached_tokens", "reasoning_tokens",
                                               "audio_tokens", "accepted_prediction_tokens",
                                               "rejected_prediction_tokens")
                       if (value := count(usage[key].get(name))) is not None}
            if details:
                reported[key] = details
    incoming = reported.get("prompt_tokens", reported.get("input_tokens"))
    details = reported.get("prompt_tokens_details", reported.get("input_tokens_details", {}))
    cached = details.get("cached_tokens", reported.get("cache_read_input_tokens",
                                                       reported.get("prompt_cache_hit_tokens")))
    if "cache_read_input_tokens" in usage:
        read = reported.get("cache_read_input_tokens")
        write = count(usage.get("cache_creation_input_tokens", 0))
        incoming = (incoming + read + write
                    if incoming is not None and read is not None and write is not None else None)
    if incoming is not None and cached is not None and cached > incoming:
        cached = None  # Keep the original counters, but do not value an inconsistent split.
    counts = dict(input_tokens=incoming, cached_tokens=cached,
                  output_tokens=reported.get("completion_tokens", reported.get("output_tokens")))
    status = ("reported" if all(value is not None for value in counts.values()) else
              "partial" if any(value is not None for value in counts.values()) else "unknown")
    return dict(counts=counts, usage_status=status, reported_usage=reported)


def _request_detail(model, protocol, purpose, transport, origin):
    detail = dict(model=str(model), protocol=str(protocol), purpose=str(purpose), transport=str(transport))
    if origin is not None:
        detail.update(trigger=origin.get("trigger", "unspecified"), turn_id=origin.get("turn_id", ""))
        if "cache_layout" in origin:
            detail["cache_layout"] = origin["cache_layout"]
    return detail


def describe(ticket, *, model, protocol, purpose="unspecified", transport="native", origin=None):
    """Pin the metadata of the actual wire request before it is submitted."""
    if os.environ.get("METTACLAW_INPUT_LEDGER_SOCKET"):
        detail = _detail(_request_detail(model, protocol, purpose, transport, origin))
        answer = native("llm-describe", (ticket, detail, time.time(), _request()))
        if answer is not LEGACY:
            return changed("llm-describe", answer)
    with transaction() as conn:
        if not conn.execute("SELECT 1 FROM requests WHERE id=?", (ticket,)).fetchone():
            return False
        return history_event(conn, ticket, "request", _request_detail(model, protocol, purpose, transport, origin))


def submitted(ticket):
    answer = native("llm-request-submitted", (ticket, time.time(), _request()))
    if answer is not LEGACY:
        return changed("llm-request-submitted", answer)
    with transaction() as conn:
        if not conn.execute("SELECT 1 FROM requests WHERE id=? AND status='pending'", (ticket,)).fetchone():
            return False
        return history_event(conn, ticket, "submitted", {})


@contextlib.contextmanager
def transaction():
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, started REAL NOT NULL, "
                     "reserved INTEGER NOT NULL, status TEXT NOT NULL, output INTEGER, "
                     "provider TEXT NOT NULL, mode TEXT NOT NULL, finish TEXT)")
        conn.execute("CREATE INDEX IF NOT EXISTS llm_requests_started ON requests(started)")
        conn.execute("CREATE INDEX IF NOT EXISTS llm_requests_state ON requests(status)")
        conn.execute("CREATE TABLE IF NOT EXISTS cooldowns (provider TEXT PRIMARY KEY, until REAL NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS llm_call_history (sequence INTEGER PRIMARY KEY, "
                     "call_id TEXT NOT NULL, event TEXT NOT NULL, observed REAL NOT NULL, "
                     "detail TEXT NOT NULL, UNIQUE(call_id,event))")
        conn.execute("CREATE INDEX IF NOT EXISTS llm_call_history_time ON llm_call_history(event,observed)")
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        history_event(conn, "", "recording-started", {"schema": 1})
        columns = {row[1] for row in conn.execute("PRAGMA table_info(requests)")}
        for name in ("owner", "fingerprint"):
            if name not in columns:
                conn.execute("ALTER TABLE requests ADD COLUMN " + name + " TEXT NOT NULL DEFAULT ''")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def recover_interrupted():
    """A dead owner's pending request has an unknown remote outcome.

    Other live process owners may have legitimate concurrent calls. PID and
    process-start pairing prevents a reused PID from inheriting their calls.
    """
    answer = native("llm-recover-requests", (time.time(), _request()))
    if answer is not LEGACY:
        return
    with transaction() as conn:
        for ticket, owner in conn.execute("SELECT id,owner FROM requests WHERE status='pending'").fetchall():
            if owner != _owner and not _owner_live(owner):
                conn.execute("UPDATE requests SET status='unknown' WHERE id=?", (ticket,))
                history_event(conn, ticket, "unknown", {"reason": "owner-exited"})


def unknown_context():
    answer = native("llm-unknown", ())
    if answer is not LEGACY:
        rows = [dict(id=row[0], provider=row[1], mode=row[2], outcome="unknown") for row in answer]
        return "" if not rows else "PROVIDER_RECOVERY " + json.dumps(rows, separators=(",", ":"))
    with transaction() as conn:
        rows = [dict(id=row[0], provider=row[1], mode=row[2], outcome="unknown") for row in conn.execute(
            "SELECT id,provider,mode FROM requests WHERE status='unknown' ORDER BY started DESC LIMIT 5")]
    return "" if not rows else "PROVIDER_RECOVERY " + json.dumps(rows, separators=(",", ":"))


def admit(provider, max_output, *, now=None, mode=None, request_fingerprint="",
          reserved_output=0, reserved_requests=0):
    now = time.time() if now is None else float(now)
    mode = mode or active_mode()
    policy = configuration(mode)
    maximum = int(max_output)
    reserved_output, reserved_requests = int(reserved_output), int(reserved_requests)
    if reserved_output < 0 or reserved_requests < 0:
        raise ValueError("reserved capacity must be nonnegative")
    if maximum <= 0:
        raise ValueError("max output tokens must be positive")
    ticket = uuid.uuid4().hex
    detail = dict(provider=str(provider), mode=mode,
                  instance=os.environ.get("METTACLAW_INSTANCE", "default"),
                  session=os.environ.get("METTACLAW_ACTIVITY_CALL_SCOPE", ""))
    answer = native("llm-admit", (ticket, provider, maximum, request_fingerprint, policy["profile"], mode, _owner,
                                  _detail(detail), now, _request()))
    if answer is not LEGACY:
        if isinstance(answer, tuple) and len(answer) == 3 and _name(answer[0]) == "LedgerDenied":
            raise AdmissionDenied(answer[1], answer[2])
        if not (isinstance(answer, tuple) and len(answer) == 2 and _name(answer[0]) == "LedgerAdmitted"):
            _refuse("llm-admit", answer)
    else:
        recover_interrupted()
        with transaction() as conn:
            if policy["profile"] == "managed":
                cooldown = conn.execute("SELECT until FROM cooldowns WHERE provider=?", (provider,)).fetchone()
                if cooldown and cooldown[0] > now:
                    raise AdmissionDenied("provider-cooldown", cooldown[0] - now)
            conn.execute("INSERT INTO requests(id,started,reserved,status,output,provider,mode,finish,owner,fingerprint) VALUES(?,?,?,'pending',NULL,?,?,NULL,?,?)",
                         (ticket, now, maximum, provider, mode, _owner, request_fingerprint))
            history_event(conn, ticket, "admitted", detail, now=now)
            # Accounting windows filter admission; they do not erase durable receipts.
    try:
        import strategy_host
        strategy_host.provider_admitted(ticket)
    except BaseException:
        fail(ticket, sent=False, now=now)
        raise
    return ticket


def _completion(result):
    evidence = usage_evidence(result.get("usage", {}))
    # Accounting cannot strand valid model work on a malformed counter.
    output = evidence["counts"]["output_tokens"]
    # The compatibility INTEGER column is narrower than the JSON receipt.
    # Preserve every reported digit there, without overflowing this column.
    if output is not None and output > 2**63 - 1:
        output = None
    finish = str(result["choices"][0].get("finish_reason", "unknown"))[:64]
    return evidence, output, finish


def complete_native(ticket, result, *, epoch=None, canonical=None, soft=0, purpose="turn"):
    """The native completion: usage, the request, its completed event and (with an epoch) the
    call's settlement with `canonical`, in one transition. LEGACY while SQLite is the ledger."""
    evidence, output, finish = _completion(result)
    claimed = None if epoch is None else _detail({"epoch": epoch})
    answer = native("llm-complete", (ticket, epoch, _detail(evidence), output, finish, _detail({"finish": finish}),
                                     claimed, canonical, int(soft), purpose, time.time(), _request()))
    return answer if answer is LEGACY else changed("llm-complete", answer)


def complete(ticket, result):
    answer = complete_native(ticket, result)
    if answer is not LEGACY:
        return answer
    evidence, output, finish = _completion(result)
    with transaction() as conn:
        if conn.execute("SELECT 1 FROM llm_call_history WHERE call_id=? AND event='admitted'",
                        (ticket,)).fetchone():
            # A late response can supply usage even when its commands cannot
            # commit. Replayed observations still contribute only once.
            history_event(conn, ticket, "usage", evidence)
        changed = conn.execute("UPDATE requests SET status='completed',output=?,finish=? WHERE id=? AND status='pending'",
                               (output, finish, ticket)).rowcount
        if changed:
            history_event(conn, ticket, "completed", {"finish": finish})
    return changed == 1


def fail_native(ticket, *, status=None, retry_after=None, sent=True, now=None, epoch=None,
                state="failed", reason=None, soft=0, purpose="turn"):
    """The native failure: the request, its failed event, a managed provider's cooldown and
    (with an epoch) the call's settlement as `state`, in one transition. The pending request's
    mode chooses the policy, as below. False when the request is not pending; LEGACY while
    SQLite is the ledger."""
    now = time.time() if now is None else float(now)
    mode = native("llm-pending-mode", (ticket,))
    if mode is LEGACY:
        return LEGACY
    if mode is None:
        return False
    policy = configuration(mode)
    until = None
    if policy["profile"] == "managed" and status in (402, 429):
        delay = policy["quota_cooldown_seconds"]
        if retry_after is not None:
            delay = max(0, float(retry_after))
        until = now + delay
    failed = _detail({"sent": bool(sent), "http_status": status if type(status) is int else None})
    answer = native("llm-fail", (ticket, epoch, mode, None if sent else 0, failed, until, state, reason,
                                 int(soft), purpose, now, _request()))
    return changed("llm-fail", answer)


def fail(ticket, *, status=None, retry_after=None, sent=True, now=None):
    now = time.time() if now is None else float(now)
    answer = fail_native(ticket, status=status, retry_after=retry_after, sent=sent, now=now)
    if answer is not LEGACY:
        return answer
    with transaction() as conn:
        row = conn.execute("SELECT provider,mode FROM requests WHERE id=? AND status='pending'", (ticket,)).fetchone()
        if not row:
            return False
        # A sent request without authoritative usage keeps its reservation:
        # a network timeout cannot prove the provider did no billable work.
        conn.execute("UPDATE requests SET status='failed',output=? WHERE id=?",
                     (None if sent else 0, ticket))
        history_event(conn, ticket, "failed", {
            "sent": bool(sent), "http_status": status if type(status) is int else None}, now=now)
        policy = configuration(row[1])
        if policy["profile"] == "managed" and status in (402, 429):
            delay = policy["quota_cooldown_seconds"]
            if retry_after is not None:
                delay = max(0, float(retry_after))
            until = now + delay
            conn.execute("INSERT INTO cooldowns VALUES(?,?) ON CONFLICT(provider) DO UPDATE SET until=MAX(until,excluded.until)", (row[0], until))
    return True


def snapshot():
    answer = native("llm-requests", ())
    if answer is not LEGACY:
        counts = {status: count for status, count in answer[1]}
        return {"policy": configuration(), "requests": counts, "accounted_output_tokens": answer[2]}
    with transaction() as conn:
        counts = dict(conn.execute("SELECT status,COUNT(*) FROM requests GROUP BY status"))
        output = conn.execute("SELECT COALESCE(SUM(COALESCE(output,reserved)),0) FROM requests").fetchone()[0]
    return {"policy": configuration(), "requests": counts, "accounted_output_tokens": output}
