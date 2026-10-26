"""Explicit counted fixtures at retained host boundaries; generated paths only."""
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
    import stream_sink
    import context_sources
    for name in ('record_command', 'record_suffix', 'capture_public_epoch', 'public_event',
                 'public_text', '_publish_public_event', '_tool_name', '_receipt_id',
                 '_text', '_append', 'view', '_tail_lines'):
        setattr(effect_receipts, name, forbidden)
    # A configured plugin is explicitly allowed to implement its transaction
    # in Python. Only UNSET/native routes forbid the oracle worklog functions.
    if not os.environ.get('METTACLAW_PUBLIC_WORK_LIB','').strip():
        public_worklog.transact = public_worklog.clean = forbidden

    def stream(identity, command, result, disposition):
        count('stream')
        if os.environ.get('EFFECT_TEST_STREAM_COUNTER_ONLY')=='1':
            return 1
        receipt = Path(os.environ['METTACLAW_EFFECT_RECEIPT_PATH'])
        notes = Path(os.environ['METTACLAW_PUBLIC_WORK_DIR']) / 'notes.jsonl'
        events.append({'identity': identity, 'command': command, 'result': result,
            'disposition': disposition, 'receipt_lines': len(receipt.read_bytes().splitlines()),
            'notes': notes.read_text() if notes.exists() else ''})
        if os.environ.get('EFFECT_TEST_STREAM_FAIL') == '1':
            raise RuntimeError('fixture stream failed')
        return 1

    def result(*args):
        count('strategy_result')
        return 1

    def identity(turn):
        count('strategy_identity')
        return 'host:%s:%s' % (turn, counts.get('reserve', 0))

    def reserve(*args):
        count('reserve')
        return 1

    def enabled():
        count('strategy_enabled')
        return int(os.environ.get('METTACLAW_OBSERVED_STRATEGY') == '1')

    stream_sink.receipt = stream
    strategy_host.command_result = result
    strategy_host.command_identity = identity
    strategy_host.reserve_command = reserve
    strategy_host.stopped = lambda: 0
    strategy_host.enabled_flag = enabled
    # Exercise the actual bundle caller with just the scoped source. The
    # all-source fixture attempt is retained separately; unrelated native
    # loaders are not replaced or reported green by this receipt qualification.
    context_sources._loaders = lambda *a, **k: tuple((spec,effect_receipts.view)
        for spec in context_sources.SOURCE_SPECS if spec.source_id == 'effect-receipts')
    return 1


def summary():
    return json.dumps({'counts': counts, 'events': events}, ensure_ascii=True)


def observe(value):
    return json.dumps({'type': type(value).__name__, 'repr': repr(value), 'str': str(value),
        'items': [[type(v).__name__, repr(v), str(v)] for v in value] if isinstance(value,list) else None},
        ensure_ascii=True)


def stopped(value):
    import strategy_host
    strategy_host.stopped=lambda:int(value)
    return 1
