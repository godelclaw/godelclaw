"""Explicit counted fixtures at retained host boundaries; generated paths only.

The receipt's display record and the broker's attempts are native
(src/display_cetta.metta, src/strategy_attempts_cetta.metta): no host
boundary remains for them to count."""
import json
import os
from pathlib import Path

counts = {}
events = []


def count(name):
    counts[name] = counts.get(name, 0) + 1


def forbidden(*args, **kwargs):
    count('forbidden')
    raise AssertionError('ported Python implementation reached on native route')


def setup():
    import effect_receipts
    import public_worklog
    import strategy_host
    import context_sources
    for name in ('record_command', 'record_suffix', 'capture_public_epoch', 'public_event',
                 'public_text', '_publish_public_event', '_tool_name', '_receipt_id',
                 '_text', '_append', 'view', '_tail_lines'):
        setattr(effect_receipts, name, forbidden)
    # A configured plugin is explicitly allowed to implement its transaction
    # in Python. Only UNSET/native routes forbid the oracle worklog functions.
    if not os.environ.get('METTACLAW_PUBLIC_WORK_LIB','').strip():
        public_worklog.transact = public_worklog.clean = forbidden

    def result(*args):
        count('strategy_result')
        return 1

    strategy_host.command_result = result
    # The dispatcher reads the operator latch itself: grant cognition.
    import lifecycle
    lifecycle.start()
    # Keep the full registry: Qualification provides generated paths for every
    # native source. Receipt view remains forbidden on Python, so the complete
    # context caller cannot hide a receipt fallback behind a scoped registry.
    return 1


def summary():
    return json.dumps({'counts': counts, 'events': events}, ensure_ascii=True)


def observe(value):
    return json.dumps({'type': type(value).__name__, 'repr': repr(value), 'str': str(value),
        'items': [[type(v).__name__, repr(v), str(v)] for v in value] if isinstance(value,list) else None},
        ensure_ascii=True)


def stopped(value):
    """Set the operator latch the dispatcher reads."""
    import lifecycle
    lifecycle.stop() if int(value) else lifecycle.start()
    return 1
