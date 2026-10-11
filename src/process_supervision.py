"""Progress receipts and an external service-manager watchdog.

Only the loop, completed command boundaries and actual provider data advance
progress. An owned wait may set one absolute deadline; polling it does not
renew that deadline. No heartbeat thread can conceal a stuck evaluator.
"""
import json
import math
import os
from pathlib import Path
import socket
import tempfile
import time

_sequence = 0
_wait_deadline = 0.0
_provider_bytes = 0


def path():
    explicit = os.environ.get("METTACLAW_PROGRESS_PATH")
    engine = os.environ.get("METTACLAW_ENGINE_STATE_PATH")
    return Path(explicit) if explicit else Path(engine).with_name("progress.json") if engine else None


def memory(pid=None):
    """Read this process's cgroup account, rather than its historical RSS peak."""
    try:
        lines = Path("/proc", str(pid or os.getpid()), "cgroup").read_text().splitlines()
        relative = next(line[3:] for line in lines if line.startswith("0::"))
        group = Path("/sys/fs/cgroup") / relative.lstrip("/")
        current = int((group / "memory.current").read_text())
        def limit(name):
            value = (group / name).read_text().strip()
            return int(value) if value != "max" else None
        return {"bytes": current, "high": limit("memory.high"), "max": limit("memory.max")}
    except (OSError, ValueError, StopIteration):
        return {"bytes": None, "high": None, "max": None}


def notify(wait_until=0):
    address = os.environ.get("NOTIFY_SOCKET")
    base = int(os.environ.get("WATCHDOG_USEC", "0"))
    if not address or not base:
        return 0
    duration = base + math.ceil(max(0, wait_until - time.monotonic()) * 1000000)
    message = ("WATCHDOG_USEC=%d\nWATCHDOG=1" % duration).encode()
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as stream:
            stream.settimeout(.2)
            stream.sendto(message, address)
        return 1
    except OSError:
        return 0


def progress(phase, wait_seconds=0, *, deadline=None):
    global _sequence, _wait_deadline
    target = path()
    now = time.time()
    clock = time.monotonic()
    seconds = float(wait_seconds)
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("a wait needs a finite, nonnegative duration")
    _wait_deadline = (clock + seconds if seconds else 0) if deadline is None else deadline
    _sequence += 1
    import llm_policy
    account = memory()
    receipt = dict(pid=os.getpid(), owner=llm_policy._owner, sequence=_sequence,
                   phase=str(phase), progress_at=now, wait_until=_wait_deadline,
                   progress_clock=clock, memory_bytes=account["bytes"])
    temporary = None
    try:
        if target is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".progress-", dir=target.parent)
            with os.fdopen(descriptor, "w") as stream:
                json.dump(receipt, stream, separators=(",", ":"))
            os.replace(temporary, target)
    except OSError:
        pass  # Observation failure cannot undo an already committed turn.
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)
    notify(_wait_deadline)
    return 1


def provider_data(count):
    global _provider_bytes
    count = int(count)
    if count <= _provider_bytes:
        return 1
    _provider_bytes = count
    progress("provider-data", deadline=_wait_deadline)
    return 1


def provider_wait(seconds):
    global _provider_bytes
    _provider_bytes = 0
    return progress("provider-wait", seconds)


def recycle_due():
    account = memory()
    bound = account["high"] or account["max"]
    configured = os.environ.get("METTACLAW_RECYCLE_BYTES")
    threshold = int(configured) if configured else (bound * 2 // 3 if bound else 0)
    return bool(threshold and account["bytes"] is not None and account["bytes"] >= threshold)


def observation(now=None):
    clock = time.monotonic()
    supplied_now = now is not None
    now = time.time() if now is None else float(now)
    target = path()
    try:
        value = json.loads(target.read_text()) if target else {}
        import llm_policy
        if not llm_policy._owner_live(value.get("owner", "")):
            return {"state": "unknown", "age": None, "memory": None}
        age = max(0, now - float(value["progress_at"]) if supplied_now else clock - float(value["progress_clock"]))
        account = memory(int(value["pid"]))
        ceiling = account["high"] or account["max"]
        if ceiling and account["bytes"] is not None and account["bytes"] > ceiling:
            state = "memory-pressure"
        elif float(value.get("wait_until", 0)) >= clock:
            state = "waiting"
        elif age > float(os.environ.get("METTACLAW_PROGRESS_STALE_SECONDS", "180")):
            state = "stalled"
        else:
            state = "fresh"
        return {"state": state, "age": int(age), "memory": account["bytes"], "phase": value["phase"]}
    except (OSError, ValueError, TypeError, KeyError):
        return {"state": "unknown", "age": None, "memory": None}
