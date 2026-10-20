"""Durable, bounded receipts for commands crossing the effect broker.

The command transcript records what a model proposed.  This append-only
ledger records what the broker actually returned, withheld, or deferred.
``returned`` deliberately does not mean that the domain action succeeded;
the accompanying result remains the evidence for that stronger claim.
"""

from __future__ import annotations

from collections import OrderedDict
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import threading
import time


_FIELD_LIMIT = 6000
_VIEW_RECORDS = 18


def _path() -> Path | None:
    raw = os.environ.get("METTACLAW_EFFECT_RECEIPT_PATH", "").strip()
    return Path(raw) if raw else None


def _text(value, limit=_FIELD_LIMIT) -> str:
    rendered = str(value)
    if len(rendered) > limit:
        return rendered[:limit - 1] + "…"
    return rendered


def _append(entry: dict) -> int:
    path = _path()
    if path is None:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(entry, ensure_ascii=False, sort_keys=True)
               + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        with os.fdopen(descriptor, "ab", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
    return 1


# ---------------------------------------------------------------------------
# Public-work projection.  Metadata only: tool name, disposition, a receipt id
# and a timestamp.  Arguments, results, message bodies and transcripts never
# enter the event.  ``returned`` is a dispatcher fact, not task success.
# ---------------------------------------------------------------------------

PUBLIC_TOOLS = frozenset((
    "shell", "query", "read-file", "read-lines", "write-file", "edit-file",
    "grep-files", "ls-tree", "background", "jobs", "job-output", "search",
    "metta", "rest", "pin", "remember", "send", "send-file", "help",
    "tmux-peek",
))
PUBLIC_DISPOSITIONS = frozenset((
    "returned", "failed", "no_result", "exception", "uncertain",
    "withheld", "deferred",
))
_DEDUPE_LIMIT = 1024
_CAPTURE_LIMIT = 64

_publish_lock = threading.Lock()
_published = OrderedDict()   # receipt ids already displayed (bounded)
_in_flight = set()
_captured = OrderedDict()    # turn -> sharing epoch captured before the work
_reporting = threading.local()


def _tool_name(command):
    if isinstance(command, str):
        try:
            command = json.loads(command)
        except ValueError:
            return None
    if isinstance(command, (list, tuple)) and command \
            and isinstance(command[0], str):
        return command[0]
    return None


def _receipt_id(turn, disposition, command, result) -> str:
    """Stable across replays: derived from the receipt, not the clock.

    Only a truncated digest leaves this function.  Two byte-identical
    commands with identical results in one turn share an id (and so one
    event); a digest also lets someone who already guesses the exact
    command and result confirm it.
    """
    raw = json.dumps([int(turn), str(disposition), command, str(result)],
                     ensure_ascii=False, sort_keys=True, default=str)
    return "rcpt:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def public_event(turn, disposition, command, result, at):
    """Pure allowlisted projection of one command receipt, or None.

    ``at`` is supplied by the caller so the function reads no clock, file or
    environment.  Unknown tools or dispositions are dropped, not guessed.
    """
    tool = _tool_name(command)
    disposition = str(disposition)
    if tool not in PUBLIC_TOOLS or disposition not in PUBLIC_DISPOSITIONS:
        return None
    return {
        "tool": tool,
        "disposition": disposition,
        "receipt_id": _receipt_id(turn, disposition, command, result),
        "at": datetime.datetime.fromtimestamp(
            at, datetime.timezone.utc).isoformat(timespec="seconds"),
    }


def public_text(event) -> str:
    lines = [
        "Activity · %s" % event["tool"],
        "- Receipt: %s · %s" % (
            event["disposition"], event["receipt_id"]),
    ]
    if event["disposition"] == "returned":
        lines.append("- Dispatcher return only; not a success verdict.")
    elif event["disposition"] in ("withheld", "deferred"):
        lines.append("- The command did not run.")
    return "\n".join(lines)


class WorklogSink:
    """Existing godel-worklog state: sharing switch, epoch and note log."""

    def __init__(self, root, library):
        spec = importlib.util.spec_from_file_location(
            "godel_worklog_state", Path(library) / "worklog.py")
        self._module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self._module)
        self._root = Path(root)

    def status(self):
        state = self._module.transact(self._root, "status")
        return bool(state["enabled"]), int(state["epoch"])

    def add(self, text, epoch):
        """Enabled and epoch are checked under the worklog's own lock."""
        return bool(self._module.transact(
            self._root, "add", text, epoch).get("accepted", True))


def _sink():
    root = os.environ.get("METTACLAW_PUBLIC_WORK_DIR", "").strip()
    if not root:
        return None
    library = os.environ.get(
        "METTACLAW_PUBLIC_WORK_LIB",
        str(Path.home() / ".local/lib/godel-worklog-observer"))
    return WorklogSink(root, library)


def capture_public_epoch(turn):
    """Call before the work.  Remembers the sharing epoch for this turn.

    Off, no sink, or any failure remembers nothing, so the later event is
    dropped.  Never raises.
    """
    try:
        sink = _sink()
        enabled, epoch = sink.status() if sink else (False, None)
    except Exception:
        enabled, epoch = False, None
    with _publish_lock:
        _captured.pop(int(turn), None)
        if enabled:
            _captured[int(turn)] = epoch
            while len(_captured) > _CAPTURE_LIMIT:
                _captured.popitem(last=False)
    return int(enabled)


def _publish_public_event(turn, disposition, command, result, at) -> int:
    """Best-effort display; never raises and never reports itself."""
    if getattr(_reporting, "active", False):
        return 0
    _reporting.active = True
    receipt_id = None
    try:
        event = public_event(turn, disposition, command, result, at)
        if event is None:
            return 0
        receipt_id = event["receipt_id"]
        with _publish_lock:
            epoch = _captured.pop(int(turn), None)
            if epoch is None or receipt_id in _published \
                    or receipt_id in _in_flight:
                return 0
            _in_flight.add(receipt_id)
        accepted = False
        try:
            sink = _sink()
            accepted = bool(sink and sink.add(public_text(event), epoch))
        finally:
            with _publish_lock:
                _in_flight.discard(receipt_id)
                if accepted:
                    _published[receipt_id] = True
                    while len(_published) > _DEDUPE_LIMIT:
                        _published.popitem(last=False)
        return int(accepted)
    except Exception:
        return 0
    finally:
        _reporting.active = False


def record_command(turn, disposition, command, result) -> int:
    """Record one command after the dispatcher has classified its return."""
    if os.environ.get("METTACLAW_OBSERVED_STRATEGY", "0") == "1":
        import strategy_host
        strategy_host.command_result(turn, disposition, command, result)
    now = time.time()
    entry = {
        "schema": 1,
        "recorded_at": now,
        "turn": int(turn),
        "disposition": _text(disposition, 80),
        "command": _text(command),
        "result": _text(result),
    }
    committed = _append(entry)
    if committed:
        # After the durable append, outside every transaction: a sink
        # failure cannot fail or undo the receipt.
        _publish_public_event(turn, disposition, command, result, now)
    return committed


def record_suffix(turn, disposition, commands, reason) -> int:
    """Record a suffix which the broker explicitly did not execute."""
    entry = {
        "schema": 1,
        "recorded_at": time.time(),
        "turn": int(turn),
        "disposition": _text(disposition, 80),
        "commands": _text(commands),
        "reason": _text(reason, 240),
    }
    return _append(entry)


def record_atlas_result(server, tool, result) -> int:
    """Record an exact Atlas view/revision identity returned by MCP.

    This witnesses transport of an Atlas result, not the truth of its claims.
    Results lacking the paired Atlas receipt and output identities are ignored
    rather than upgraded from generic command text.
    """

    if not isinstance(result, dict):
        return 0
    receipt = result.get("receipt")
    if not isinstance(receipt, dict):
        return 0
    receipt_id = str(receipt.get("receipt_id", ""))
    tool = str(tool)
    if tool == "atlas_query":
        output_id = str(result.get("view_id", ""))
        state_revision = str(result.get("state_revision", ""))
        if not output_id or not state_revision or not receipt_id:
            return 0
        identity = {
            "kind": "view",
            "view_id": output_id,
            "state_revision": state_revision,
            "receipt_id": receipt_id,
            "status": str(result.get("status", "unknown")),
        }
    elif tool == "atlas_revise":
        output_id = str(result.get("revision_id", ""))
        if not output_id or not receipt_id:
            return 0
        identity = {
            "kind": "revision",
            "revision_id": output_id,
            "receipt_id": receipt_id,
            "conflicts_witnessed": bool(
                result.get("conflicts_witnessed", False)
            ),
        }
    else:
        return 0
    return _append({
        "schema": 1,
        "recorded_at": time.time(),
        "disposition": "atlas-result-returned",
        "server": _text(server, 200),
        "tool": tool,
        "atlas": identity,
    })


def _tail_lines(path: Path, max_bytes=180_000) -> list[str]:
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - max_bytes))
        data = stream.read()
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if size > max_bytes and lines:
        lines = lines[1:]
    return lines


def view(max_records=_VIEW_RECORDS) -> str:
    """Render the recent authoritative broker receipts for model context."""
    path = _path()
    if path is None or not path.is_file():
        return ""
    entries = []
    for line in _tail_lines(path):
        try:
            entry = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    entries = entries[-max(1, int(max_records)):]
    if not entries:
        return ""

    rendered = [
        "AUTHORITATIVE BROKER RECEIPTS: proposals are not actions. "
        "returned means only that the dispatcher returned; inspect result "
        "before claiming success. withheld/deferred commands did not run. "
        "A proposal without a matching receipt is not witnessed completion."
    ]
    for entry in entries:
        head = "turn=%s disposition=%s" % (
            entry.get("turn", "?"), entry.get("disposition", "unknown")
        )
        if "atlas" in entry:
            rendered.append(
                "disposition=%s server=%s tool=%s atlas=%s" % (
                    entry.get("disposition", "unknown"),
                    entry.get("server", ""),
                    entry.get("tool", ""),
                    json.dumps(
                        entry.get("atlas", {}),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
            )
        elif "command" in entry:
            rendered.append(
                "%s command=%s result=%s" % (
                    head, entry.get("command", ""), entry.get("result", "")
                )
            )
        else:
            rendered.append(
                "%s reason=%s commands=%s" % (
                    head, entry.get("reason", ""), entry.get("commands", "")
                )
            )
    return "\n".join(rendered)
