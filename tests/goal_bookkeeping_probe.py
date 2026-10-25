"""Test observation boundary only: never used by runtime goal callers.

Native arithmetic crosses the Python bridge ONLY to observe its IEEE bits.
There is no Python conversion or serializer in the native writer path.
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


def nonfinite_fixture(name):
    """Test-only DOUBLE input, not a formatter/serializer or runtime boundary."""
    return float(str(name))


def forbid(*args, **kwargs):
    raise AssertionError("Python goal bookkeeping forbidden in native candidate probe")


def disable_writers():
    import goals
    import helper
    for name in ("kernel_pass", "human_wake", "goal_write", "goal_drop",
                 "_write_lines", "_retire_in_ledger", "_parse", "_emit", "_read_lines",
                 "_load_kernel_state", "_mood_samples", "_normalize_goal_text", "_goal_dict"):
        setattr(goals, name, forbid)
    helper.decode_safe_text = forbid
    return 0


def disable_affect_view():
    disable_writers()
    import goals
    import helper
    goals.affect_view = forbid
    helper.change_mark = forbid
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


def candidate_setup():
    disable_writers()
    import strategy_host
    strategy_host.retire_goal = candidate_retire
    return 0


def candidate_retire(name, reason):
    _fallback_calls["retire_goal"] = _fallback_calls.get("retire_goal", 0) + 1
    return 2


def candidate_record(label, value):
    # Native GoalWake is an ordinary atom, not a Python dict bridge contract.
    return fallback_record(label, value)


def apply_fixture(path):
    directory = Path(os.environ["METTACLAW_GOALS_PATH"]).parent
    for name, raw in json.loads(Path(path).read_text()).items():
        target = directory / name
        if raw is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(base64.b64decode(raw))
    return 0


def io_event(operation, first, second):
    directory = Path(os.environ["METTACLAW_GOALS_PATH"]).parent
    if str(operation) == "rename":
        key = "goal_renames" if Path(str(second)).name == "goals.metta" else "kernel_renames"
        _fallback_calls[key] = _fallback_calls.get(key, 0) + 1
    row = {"operation": str(operation), "first": str(first), "second": str(second),
           "files": {name: base64.b64encode((directory / name).read_bytes()).decode()
                     if (directory / name).is_file() else None for name in
                     ("goals.metta", "goals.metta.tmp", "goals_kernel.json",
                      "goals_kernel.json.tmp", "goals_compost.log")}}
    with (directory / "io-events.jsonl").open("a") as stream:
        stream.write(json.dumps(row) + "\n")
    return 0
