"""Explicit configured worklog plugin boundary, never a native-failure fallback.

Only called when METTACLAW_PUBLIC_WORK_LIB is nonblank. Use the oracle's loader
and status/add conversions, including arbitrary plugin mapping/value behavior.
No receipt persistence, cleaning, parsing or native publication state lives here.
"""
from effect_receipts import WorklogSink


def status(root, library):
    enabled, epoch = WorklogSink(root, library).status()
    return int(enabled), epoch


def add(root, library, text, epoch):
    return int(WorklogSink(root, library).add(text, epoch))
