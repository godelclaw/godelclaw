"""Bounded, idempotently keyed receipt notices from the control responder.

A notice does not consume input, complete work, or make a provider request.
The service owns the input and the actual delivery receipts.
"""

import hashlib
import os
import time


TEXT = "Received. Your message is queued; /activity shows whether I'm waiting, retrying, or resting."
_pending = {}
_last_notice = {}
_limit = 256


def text():
    return TEXT


def begin_scan(tasks):
    active = {str(task[0]) for task in tasks
              if isinstance(task, (list, tuple)) and len(task) == 2}
    for task in list(_pending):
        if task not in active:
            del _pending[task]
    lanes = {entry[2] for entry in _pending.values()}
    for lane in list(_last_notice):
        if lane not in lanes:
            del _last_notice[lane]
    return 1


def key(task, lane, kind, role):
    if (os.environ.get("METTACLAW_OBSERVED_STRATEGY", "0") != "1"
            or str(kind) != "message" or str(role) != "operator"):
        return ""
    try:
        delay = float(os.environ.get("METTACLAW_INPUT_ACK_SECONDS", "15"))
    except ValueError:
        delay = 15
    if not 0 < delay < float("inf"):
        return ""
    task, lane = str(task), str(lane)
    if not task or not lane or len(task) > 128 or len(lane) > 128:
        return ""
    if task not in _pending:
        if len(_pending) >= _limit:
            return ""
        _pending[task] = [time.monotonic(), False, lane]
    since, submitted, captured_lane = _pending[task]
    now = time.monotonic()
    if (submitted or lane != captured_lane or now - since < delay
            or now - _last_notice.get(lane, -float("inf")) < 60):
        return ""
    # Lost submission replies and restarts retain the same key and immutable
    # bytes. The service reconciles duplicates and enforces lane permissions.
    identity = os.environ.get("METTACLAW_INSTANCE", "default") + ":input-receipt:" + task
    number = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big")
    return lane + ".%020d" % number


def submitted(task):
    entry = _pending.get(str(task))
    if entry is not None:
        entry[1] = True
        _last_notice[entry[2]] = time.monotonic()
    return 1
