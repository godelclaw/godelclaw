"""Fixtures and old-policy sentinels for native working-state tests."""
import json
from pathlib import Path
import os
import signal

import helper


OLD_HELPERS = (
    "working_set_save", "rest_schedule", "rest_cancel", "rest_hold",
    "rest_release", "rest_until", "rest_holding", "rest_pending",
    "rest_due", "rest_claim", "rest_finish", "wait_seconds", "intent_save",
    "working_boot", "boot_str", "boot_int", "boot_num", "turn_commit_reset",
    "turn_commit_stage", "turn_commit_ready",
)


def forbid():
    def forbidden(*args, **kwargs):
        raise AssertionError("native working-state path used an old Python helper")
    for name in OLD_HELPERS:
        setattr(helper, name, forbidden)
    helper._working_read = forbidden
    helper._working_write = forbidden
    import working_state_host
    working_state_host.ledger_read = forbidden
    working_state_host.checkpoint = forbidden
    return 1


def record(path, value):
    Path(path).write_text(str(value), encoding="utf-8")
    return 1


def record_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return 1


def scope(ids, request):
    import strategy_host
    import telegram
    telegram._current_batch_update_ids = set(ids)
    strategy_host._message_focus = request
    return 1


def crash():
    os.kill(os.getpid(), signal.SIGKILL)
