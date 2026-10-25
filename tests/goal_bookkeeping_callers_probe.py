"""Read-only caller observations and explicit non-bookkeeping fixtures.

No provider/channel transport is started. Python never writes native goal,
kernel or compost output; only fixture setup does so before an operation.
"""
import base64
import json
import os
from pathlib import Path
import sys
import time
import types

import goal_bookkeeping_probe as base

messages = []
batch = ""


def setup(native):
    if int(native):
        base.candidate_setup()
    else:
        base.setup_fallback()
        import goals
        goals._retire_in_ledger = lambda *args: 2
        # SWI faults exercise the actual Python route, not native fs stubs.
        import builtins
        real_open, real_replace = builtins.open, os.replace
        def guarded_open(path, mode="r", *args, **kwargs):
            if mode == "w" and str(path).endswith(os.environ.get("GK_TEST_WRITE_FAILURE", "never-injected")):
                raise OSError("generated write failure")
            if mode == "a" and os.environ.get("GK_TEST_APPEND_FAILURE") == "1":
                raise OSError("generated append failure")
            return real_open(path, mode, *args, **kwargs)
        def guarded_replace(source, target):
            if str(target).endswith(os.environ.get("GK_TEST_RENAME_FAILURE", "never-injected")):
                raise OSError("generated rename failure")
            return real_replace(source, target)
        builtins.open, os.replace = guarded_open, guarded_replace
    # Only the identity boundary is used by applyHumanActivity. Do not import
    # or initialize the actual Telegram transport just to supply test batches.
    channel = types.ModuleType("telegram")
    channel.current_batch_identity = lambda: batch
    channel.lastMessageArmLoops = lambda: 7
    sys.modules["telegram"] = channel
    return 0


def begin(label, token=""):
    global messages, batch
    messages, batch = [], str(token)
    return 0


def note(value):
    messages.append(value)
    return 0


def record(label, value):
    import goals
    directory = Path(os.environ["METTACLAW_GOALS_PATH"]).parent
    row = {"label": str(label), "value": value, "messages": messages,
           "files": {name: base64.b64encode((directory / name).read_bytes()).decode()
                     if (directory / name).is_file() else None for name in
                     ("goals.metta", "goals_kernel.json", "goals_compost.log")},
           "calls": dict(base._fallback_calls), "oracle_module": goals.__file__}
    with Path(os.environ["GOAL_BOOKKEEPING_OBSERVATIONS"]).open("a") as stream:
        stream.write(json.dumps(row) + "\n")
    return 0


def clock():
    return time.perf_counter_ns()


def bench(start, end):
    directory = Path(os.environ["METTACLAW_GOALS_PATH"]).parent
    state = json.loads((directory / "goals_kernel.json").read_bytes())
    assert state["log_offset"] == (directory / "updates.jsonl").stat().st_size
    assert len(messages) == 1 and str(messages[0]).startswith("goal-kernel: "), messages
    with (directory / "caller-bench.jsonl").open("a") as stream:
        stream.write(json.dumps({"ns": int(end) - int(start), "message": messages[0]}) + "\n")
    return 0
