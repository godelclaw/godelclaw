"""Small durable state boundary for deliberately public work notes.

This module owns only the on/off epoch and append-only notes. It never reads
private transcripts, command arguments or results. Presentation is a separate
consumer of ``notes.jsonl``.
"""

import datetime
import fcntl
import json
import os
from pathlib import Path
import unicodedata


def clean(text):
    """Keep list/paragraph newlines and remove every other control character."""
    return "".join(
        char if char == "\n" or not unicodedata.category(char).startswith("C")
        else " "
        for char in str(text)
    )[:1200]


def transact(root, action, text="", expected_epoch=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state_path = root / "state.json"
        try:
            state = json.loads(state_path.read_text())
        except (OSError, ValueError):
            state = {"enabled": False, "seq": 0, "epoch": 0}
        state.setdefault("enabled", False)
        state.setdefault("seq", 0)
        state.setdefault("epoch", 0)
        log = root / "notes.jsonl"
        result = state
        if action in ("on", "off"):
            state["enabled"] = action == "on"
            state["epoch"] += 1
        elif action == "add":
            if expected_epoch is not None and state["epoch"] != expected_epoch:
                return {"accepted": False, "reason": "sharing epoch changed"}
            if not state["enabled"]:
                return {"accepted": False, "reason": "display off; note not retained"}
            state["seq"] += 1
            entry = {
                "seq": state["seq"],
                "time": datetime.datetime.now().astimezone().isoformat(
                    timespec="seconds"),
                "text": clean(text),
            }
            descriptor = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(descriptor, "a") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            result = {**state, "accepted": True}
        elif action == "read":
            entries = ([json.loads(line) for line in log.read_text().splitlines()]
                       if state["enabled"] and log.exists() else [])
            return state, entries
        elif action != "status":
            raise ValueError("unknown public-work action")
        temporary = root / ("state.tmp." + str(os.getpid()))
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(state, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, state_path)
        return result
