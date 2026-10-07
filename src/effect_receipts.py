"""Durable, bounded receipts for commands crossing the effect broker.

The command transcript records what a model proposed.  This append-only
ledger records what the broker actually returned, withheld, or deferred.
``returned`` deliberately does not mean that the domain action succeeded;
the accompanying result remains the evidence for that stronger claim.
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
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


def record_command(turn, disposition, command, result) -> int:
    """Record one command after the dispatcher has classified its return."""
    if os.environ.get("METTACLAW_OBSERVED_STRATEGY", "0") == "1":
        import strategy_host
        strategy_host.command_result(turn, disposition, command, result)
    entry = {
        "schema": 1,
        "recorded_at": time.time(),
        "turn": int(turn),
        "disposition": _text(disposition, 80),
        "command": _text(command),
        "result": _text(result),
    }
    try:
        import action_graph
        entry["action_graph"] = action_graph.metadata(command)
    except Exception:
        pass
    return _append(entry)


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
    try:
        import action_graph
        entry["action_graph"] = [
            action_graph.metadata(command) for command in commands
        ]
    except Exception:
        pass
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
