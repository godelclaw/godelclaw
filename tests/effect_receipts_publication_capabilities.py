"""Probe publication guard capabilities; no runtime writer/caller activation."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
from unittest import mock

from effect_receipts_support import IMPORTS, Qualification, limit


def check(c):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    import effect_receipts as oracle
    inventory = []
    for path in sorted((c.bundle / 'lib').rglob('*.metta')):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r'thread|mutex|thread.local|thread.id|context.id', line, re.I):
                inventory.append([str(path.relative_to(c.bundle)), number, line])
    c.save('frozen-thread-context-inventory.json', inventory)
    proposed = {
        'ThreadId': '(system:thread-id)',
        'ThreadContext': '(system:thread-context)',
        'ThreadLocal': '(system:thread-local-new False)',
    }
    source = IMPORTS + '''
!(println! (MutexValue (with_mutex effect05-publication-probe 7)))
!(println! (MutexFailure (collapse (catch (with_mutex effect05-publication-probe (Error fixture failure))))))
!(println! (MutexAfter (with_mutex effect05-publication-probe 9)))
'''
    source += ''.join('!(println! (Probe%s %s))\n' % row for row in proposed.items())
    raw = c.run('publication-context-capabilities', source)
    c.save('native-context-observation.json', {'stdout': raw,
        'limit': 'proposed public operations are probes, not definitions or an exhaustive naming proof'})
    assert '(MutexValue 7)' in raw and '(MutexAfter 9)' in raw, raw
    assert '(MutexFailure ((Error fixture failure)))' in raw, raw
    for label, expression in proposed.items():
        assert '(Probe%s %s)' % (label, expression.replace('False', 'false')) in raw, raw

    entered, release = threading.Event(), threading.Event()
    calls, nested, thread_ids = [], [], []

    class Sink:
        def add(self, text, epoch):
            thread_ids.append(threading.get_ident())
            calls.append([text, epoch])
            if 'rcpt:' in text and not entered.is_set():
                # Same-thread recursive publication must be dropped, while a
                # different thread may publish a distinct receipt concurrently.
                nested.append(oracle._publish_public_event(
                    1, 'returned', ['shell', 'nested-fixture'], 'nested', 0,
                    'nested-occurrence'))
                entered.set()
                assert release.wait(10), 'fixture release timed out'
            return True

    oracle._published.clear(); oracle._in_flight.clear(); oracle._captured.clear()
    oracle._captured[1] = 7
    outcomes = {}
    with mock.patch.dict(os.environ, {**c.environment(), 'METTACLAW_PUBLIC_TOOL_RECEIPTS': '1'}, clear=True), \
            mock.patch.object(oracle, '_sink', return_value=Sink()):
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(oracle._publish_public_event, 1, 'returned',
                                ['shell', 'first-fixture'], 'first', 0, 'first-occurrence')
            try:
                assert entered.wait(10), 'fixture did not enter first sink'
                outcomes['same_id_in_flight'] = pool.submit(oracle._publish_public_event,
                    1, 'returned', ['shell', 'first-fixture'], 'first', 0,
                    'first-occurrence').result(timeout=10)
                outcomes['other_thread_distinct'] = pool.submit(oracle._publish_public_event,
                    1, 'returned', ['shell', 'second-fixture'], 'second', 0,
                    'second-occurrence').result(timeout=10)
            finally:
                release.set()
            outcomes['first'] = first.result(timeout=10)
    outcomes['same_thread_recursive'] = nested
    c.save('python-thread-context-witness.json', {'outcomes': outcomes,
        'sink_calls': calls, 'sink_thread_ids': thread_ids,
        'published': list(oracle._published), 'in_flight_after': list(oracle._in_flight),
        'environment': {**c.environment(), 'METTACLAW_PUBLIC_TOOL_RECEIPTS': '1'},
        'limit': 'generated in-process oracle sink; no native publication implementation or concurrency parity claim'})
    assert outcomes == {'same_id_in_flight': 0, 'other_thread_distinct': 1,
                        'first': 1, 'same_thread_recursive': [0]}, outcomes
    assert len(set(thread_ids)) == 2 and len(oracle._published) == 2
    assert not oracle._in_flight
    c.save('checkpoint.json', {'status': 'CAPABILITY_CHECKPOINT_PUBLICATION_THREAD_CONTEXT',
        'runtime_activated': False, 'mutex_values_observed': True,
        'missing_public_contract': 'native current thread/context identity or thread-local guard storage',
        'required': ['same-thread reentry drops before admission',
                     'distinct-thread distinct-id publication proceeds while another sink is in flight',
                     'process-shared capture/published/in-flight admission under mutex',
                     'cleanup/reset on returned errors and exceptional exits'],
        'not_implemented': ['process-global reporting flag', 'Python thread/guard implementation fallback'],
        'limit': 'frozen-library inventory plus concrete unreduced probes; parent/reviewer should identify a supported contract if one exists'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    limit()
    assert sys.version_info[:3] == (3, 12, 13)
    args.evidence.mkdir(parents=True, exist_ok=False)
    (args.evidence / 'runner.py').write_bytes(Path(__file__).read_bytes())
    with tempfile.TemporaryDirectory(prefix='effect05-publication-context-') as temporary:
        c = Qualification(args.evidence.resolve(), Path(temporary))
        with c.phase('publication-context-capabilities'):
            c.snapshot_sources('src/effect_receipts.py', 'tests/effect_receipts_support.py')
            check(c)
    print('CAPABILITY_CHECKPOINT_PUBLICATION_THREAD_CONTEXT: inspect inventory/probes; no activation')


if __name__ == '__main__':
    main()
