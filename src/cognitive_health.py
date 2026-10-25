"""Durable, privacy-safe receipts for model turns.

An outstanding obligation and an in-flight provider request are different
facts.  A failed request ends the latter while preserving the former for
retry; otherwise operator status can report a long-dead request as pending.
"""

import json
import math
import os
import tempfile
import threading
import time


_lock = threading.RLock()
_turn_outcome = "none"


def _path():
    configured = os.environ.get("METTACLAW_COGNITIVE_HEALTH_PATH", "")
    if configured:
        return configured
    state_home = os.environ.get(
        "XDG_STATE_HOME", os.path.expanduser("~/.local/state"))
    instance = os.environ.get("METTACLAW_INSTANCE", "default")
    return os.path.join(state_home, "pettaclaw", instance,
                        "cognitive-health.json")


def _read():
    try:
        with open(_path(), encoding="utf-8") as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) else {}
    except (OSError, TypeError, ValueError):
        return {}


def _write(value):
    path = _path()
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".cognitive-health-",
                                     dir=parent, text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _label(value, default="unknown"):
    text = str(value or default).strip().lower()
    safe = "".join(c for c in text if c.isalnum() or c in "-_")
    return (safe or default)[:64]


def _stamp(now):
    return time.time() if now is None else float(now)


def expect_turn(mode="unknown", budget=None, now=None):
    """Record a model-turn obligation without refreshing an older one."""
    now = _stamp(now)
    try:
        budget = None if budget is None else max(0, int(budget))
    except (TypeError, ValueError):
        budget = None
    try:
        with _lock:
            value = _read()
            is_new = not float(value.get("obligation_since", 0) or 0)
            if is_new:
                legacy = float(value.get("pending_since", 0) or 0)
                value["obligation_since"] = legacy or now
            value.update({
                "schema": 2,
                "last_expected_at": now,
                "mode": _label(mode),
            })
            if is_new:
                value["expected_count"] = int(
                    value.get("expected_count", 0)) + 1
            if budget is not None:
                value["budget_at_start"] = budget
            _write(value)
        return 1
    except Exception:
        return 0


def turn_started(provider="unknown", now=None):
    """Record the start of the current provider call.

    A persisted obligation may predate a process restart or a failed attempt.
    Once a real provider call begins, its own start is the relevant age for a
    hung-call check.
    """
    global _turn_outcome
    now = _stamp(now)
    import process_supervision
    process_supervision.progress("provider-start")
    with _lock:
        _turn_outcome = "pending"
    try:
        with _lock:
            value = _read()
            if not float(value.get("obligation_since", 0) or 0):
                value["obligation_since"] = now
            value.update({
                "schema": 2,
                "pending_since": now,
                "in_flight_since": now,
                "last_started_at": now,
                "retry_at": 0,
                "started_count": int(value.get("started_count", 0)) + 1,
                "provider": _label(provider),
                "response_bytes": 0,
                "transport_quiet_ms": 0,
                "soft_wait_elapsed": False,
                "local_commit_pending": False,
            })
            _write(value)
        return 1
    except Exception:
        return 0


def provider_progress(response_bytes, quiet_ms, soft_elapsed, hard_ms):
    """Transport observations never imply a successful cognitive turn."""
    with _lock:
        value = _read()
        if value.get("in_flight_since"):
            value.update(transport_observed_at=time.time(), response_bytes=int(response_bytes),
                         transport_quiet_ms=int(quiet_ms), soft_wait_elapsed=bool(soft_elapsed),
                         hard_deadline_ms=int(hard_ms))
            _write(value)
    return 1


def result_recording(attempt):
    """A received answer awaits local storage, not another provider call."""
    try:
        with _lock:
            value = _read()
            value.update(local_commit_pending=True, local_commit_attempts=max(0, int(attempt)))
            _write(value)
        return 1
    except Exception:
        return 0


def turn_completed(response_bytes=0, now=None):
    """End the provider request after a non-empty model answer.

    The input obligation is discharged later, after history and input
    acknowledgement have both committed.
    """
    global _turn_outcome
    now = _stamp(now)
    import process_supervision
    process_supervision.progress("provider-result")
    with _lock:
        _turn_outcome = "completed"
    try:
        size = max(0, int(response_bytes))
    except (TypeError, ValueError):
        size = 0
    try:
        with _lock:
            value = _read()
            admitted = bool(float(value.get("in_flight_since", 0) or 0))
            value.pop('provider_context_repair', None)
            value.update({
                "schema": 2,
                "pending_since": 0,
                "in_flight_since": 0,
                "last_completed_at": now,
                "retry_at": 0,
                "last_response_bytes": size,
                "last_outcome": "completed",
                "local_commit_pending": False,
                "failure_streak": 0,
            })
            field = ("completed_count" if admitted
                     else "completion_without_admission_count")
            value[field] = int(value.get(field, 0)) + 1
            _write(value)
        return 1
    except Exception:
        return 0


def turn_failed(kind="provider-error", now=None, retry_after=0):
    """End the request while leaving its input obligation outstanding."""
    global _turn_outcome
    now = _stamp(now)
    with _lock:
        _turn_outcome = "failed"
    try:
        with _lock:
            value = _read()
            admitted = bool(float(value.get("in_flight_since", 0) or 0))
            failure = _label(kind, "provider-error")
            previous = value.get("last_failure_type")
            obligation = float(value.get("obligation_since", 0) or 0)
            if not obligation:
                obligation = float(value.get("pending_since", 0) or 0) or now
            value.update({
                "schema": 2,
                "pending_since": 0,
                "in_flight_since": 0,
                "obligation_since": obligation,
                "last_failed_at": now,
                "retry_at": now + max(0, float(retry_after)),
                "last_failure_type": failure,
                "failure_streak": (int(value.get("failure_streak", 0)) + 1
                                   if previous == failure else 1),
                "last_outcome": "failed",
            })
            field = "failed_count" if admitted else "local_failure_count"
            value[field] = int(value.get(field, 0)) + 1
            _write(value)
        return 1
    except Exception:
        return 0


def retry_wait_seconds(now=None):
    """Remaining provider backoff for the loop's interruptible wait.

    The adapter returns immediately after recording failure. Operator input
    can interrupt the ordinary channel wait; no provider sleep hides it.
    """
    import provider_rejection
    if provider_rejection.blocked():
        return 0 if provider_rejection.repair_pending() else 1
    try:
        with _lock:
            retry_at = float(_read().get("retry_at", 0) or 0)
        return max(0, math.ceil(retry_at - _stamp(now)))
    except (TypeError, ValueError, OverflowError):
        return 0


def wait_for_retry(seconds=None):
    """Honor provider backoff without letting retained input cancel it.

    Telegram control remains in its independent process.  The cognitive loop
    checks the operator latch and recycle request once per second, but ordinary
    queued input cannot turn its own failed attempt into a busy retry loop.
    """
    try:
        delay = (retry_wait_seconds() if seconds is None
                 else max(0.0, float(seconds)))
    except (TypeError, ValueError, OverflowError):
        delay = 0.0
    deadline = time.monotonic() + delay
    next_progress = 0.0
    while True:
        import lifecycle
        if not lifecycle.cognition_enabled():
            return "operator stopped during provider retry"
        import helper
        if helper.recycle_requested():
            return "recycle requested"
        now = time.monotonic()
        remaining = deadline - now
        import provider_rejection
        blocked = provider_rejection.blocked()
        if blocked and provider_rejection.repair_pending():
            return "provider context repair requested"
        if blocked:
            provider_rejection.notify()
        if remaining <= 0 and not blocked:
            return "rested %ds provider retry" % math.ceil(delay)
        if now >= next_progress:
            import process_supervision
            window = 10.0 if blocked else min(10.0, remaining)
            process_supervision.progress("provider-rejected" if blocked else "provider-backoff", window)
            next_progress = now + max(1.0, window)
        time.sleep(1.0 if blocked else min(1.0, remaining))


def turn_succeeded():
    """Numeric refinement witness for the serial PeTTa bridge.

    The loop asks only after its one model call.  A failed provider returns a
    paced empty action, so response syntax alone cannot distinguish failure
    from a legitimate empty command list.
    """
    with _lock:
        return 1 if _turn_outcome == "completed" else 0


def turn_settled(now=None):
    """Discharge the obligation after history and input acknowledgement."""
    now = _stamp(now)
    try:
        with _lock:
            value = _read()
            outstanding = bool(float(value.get("obligation_since", 0) or 0))
            value.update({
                "schema": 2,
                "obligation_since": 0,
                "last_settled_at": now,
            })
            if outstanding:
                value["settled_count"] = int(
                    value.get("settled_count", 0)) + 1
            _write(value)
        return 1
    except Exception:
        return 0


def snapshot():
    """Return the receipt state; it contains no prompt or response text."""
    with _lock:
        return dict(_read())
