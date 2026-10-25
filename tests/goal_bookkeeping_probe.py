"""Test observation boundary only: never used by runtime goal callers.

Native arithmetic crosses the Python bridge ONLY to observe its IEEE bits.
There is no Python conversion on a proposed runtime writer path (none exists).
"""
import json
import base64
import os
from pathlib import Path
import struct


def bits(value):
    if isinstance(value, (list, tuple)):
        return [bits(item) for item in value]
    return struct.pack(">d", float(value)).hex()


def observe(label, value):
    with Path(os.environ["GOAL_BOOKKEEPING_OBSERVATIONS"]).open("a") as stream:
        stream.write(json.dumps({"label": str(label), "bits": bits(value)}) + "\n")
    return 0


def text(label, value):
    with Path(os.environ["GOAL_BOOKKEEPING_OBSERVATIONS"]).open("a") as stream:
        stream.write(json.dumps({"label": str(label), "text": str(value)}) + "\n")
    return 0


def forbid(*args, **kwargs):
    raise AssertionError("Python goal writer forbidden in native arithmetic probe")


def disable_writers():
    import goals
    for name in ("kernel_pass", "human_wake", "goal_write", "goal_drop",
                 "_write_lines", "_retire_in_ledger"):
        setattr(goals, name, forbid)
    return 0


_fallback_calls = {}


def setup_fallback():
    import goals
    for name in ("kernel_pass", "human_wake", "goal_write", "goal_drop"):
        original = getattr(goals, name)
        def counted(*args, _name=name, _original=original, **kwargs):
            _fallback_calls[_name] = _fallback_calls.get(_name, 0) + 1
            return _original(*args, **kwargs)
        setattr(goals, name, counted)
    goals._retire_in_ledger = lambda *args: 0
    goals.time.strftime = lambda *args: "2026-01-02T03:04:05"
    return 0


def fallback_record(label, value):
    directory = Path(os.environ["METTACLAW_GOALS_PATH"]).parent
    files = {}
    for name in ("goals.metta", "goals_kernel.json", "goals_compost.log"):
        path = directory / name
        files[name] = base64.b64encode(path.read_bytes()).decode() if path.exists() else None
    row = {"label": str(label), "value": value, "files": files, "calls": dict(_fallback_calls)}
    with Path(os.environ["GOAL_BOOKKEEPING_OBSERVATIONS"]).open("a") as stream:
        stream.write(json.dumps(row) + "\n")
    return 0
