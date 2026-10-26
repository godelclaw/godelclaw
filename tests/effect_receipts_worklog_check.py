"""Native full worklog transactions versus unchanged Python, disposable files."""
import argparse
import datetime
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest import mock

from effect_receipts_primitives2 import text
from effect_receipts_support import IMPORTS, Qualification, emit, limit, q, rows


WIRE = '''
(= (wt:wire $value)
 (case $value
  (((ErFailure $class $message $detail)
    (JsonArray ("error" (str:symbol->string $class) $message)))
   ((PublicWorkRead $state $entries)
    (JsonArray ("returned" (json:stringify-python (JsonArray ($state $entries))))))
   ($other (JsonArray ("returned" (json:stringify-python $other)))))))
'''


def snapshot(root):
    return {('state.tmp.PID' if p.name.startswith('state.tmp.') else p.name): p.read_bytes().hex()
            for p in sorted(root.iterdir()) if p.is_file()}


def lock_free(root):
    with (root / 'lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(stream, fcntl.LOCK_UN)


def oracle(c, root, action, note, epoch, instant=None):
    import public_worklog
    real_datetime = datetime.datetime

    class FixtureDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime.fromisoformat(instant)

    old_tz = os.environ.get('TZ')
    with mock.patch.dict(os.environ, c.environment(), clear=True):
        time.tzset()
        with mock.patch.object(public_worklog.datetime, 'datetime',
                               FixtureDatetime if instant else real_datetime):
            try:
                value = public_worklog.transact(root, action, note, epoch)
                result = ['returned', json.dumps(value)]
            except Exception as exc:
                result = ['error', type(exc).__name__, str(exc)]
    if old_tz is None:
        os.environ.pop('TZ', None)
    else:
        os.environ['TZ'] = old_tz
    time.tzset()
    return result


def check(c):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    native_root, oracle_root = c.directory / 'native', c.directory / 'oracle'
    native_root.mkdir()
    oracle_root.mkdir()
    operations = [('status', '', None), ('add', 'off', None), ('on', '', None),
        ('add', 'A\0🙂\n\t\ue000\u0378e\u0301', 1), ('read', '', None),
        ('add', 'old epoch', 0), ('off', '', None), ('on', '', None),
        ('add', '🙂' * 1201, 3), ('read', '', None),
        ('add', 'paragraph\u2028separator\u2029fixture',3), ('read','',None), ('bad', '', None)]
    comparisons = []
    for index, (action, note, epoch) in enumerate(operations):
        source = IMPORTS + '!(import! &self ./src/public_worklog_cetta.metta)\n' + WIRE
        source += emit('(wt:wire (pw:transact %s %s %s %s))' % (
            q(native_root), q(action), text(note), 'JsonNull' if epoch is None else epoch))
        raw = c.run('worklog-%02d-%s' % (index, action), source)
        native_files = snapshot(native_root)
        c.save('worklog-%02d-native-files.json' % index, native_files)
        actual = rows(raw)
        instant = None
        if action == 'add' and (native_root / 'notes.jsonl').exists():
            entries = [line for line in (native_root / 'notes.jsonl').read_text().split('\n') if line]
            if entries:
                instant = json.loads(entries[-1])['time']
        expected = oracle(c, oracle_root, action, note, epoch, instant)
        expected_files = snapshot(oracle_root)
        comparisons.append({'action': action, 'clock_fixture': instant,
            'actual': actual, 'expected': expected,
            'native_files': native_files, 'oracle_files': expected_files})
        c.save('transaction-comparisons.json', comparisons)
        assert actual == [expected], (index, actual, expected)
        assert native_files == expected_files, index
        lock_free(native_root)
        lock_free(oracle_root)

    # Deterministic parser/type/encoding and integer failure boundaries.
    big = '9' * 4300
    states = [b'bad', b'\xff', b'{"extra":"\\ud800"}',
        b'{"enabled":false,"extra":NaN}', b'[]', b'null', b'1', b'"x"',
        b'{"epoch":"x"}', b'{"epoch":[]}', b'{"epoch":null}',
        b'{"enabled":true,"seq":"x"}', b'{"enabled":true,"seq":{}}',
        b'{"enabled":-0.0,"seq":0}', b'{"enabled":true,"seq":1.0,"epoch":7.0}',
        b'{"enabled":true,"seq":false,"epoch":true}',
        ('{"enabled":false,"epoch":' + big + '}').encode(),
        ('{"enabled":true,"seq":' + big + '}').encode()]
    faults = []
    for index, state in enumerate(states):
        nr, pr = c.directory / ('fault-n-%d' % index), c.directory / ('fault-p-%d' % index)
        nr.mkdir(); pr.mkdir()
        (nr / 'state.json').write_bytes(state)
        (pr / 'state.json').write_bytes(state)
        action = 'add' if b'"seq"' in state else 'on'
        source = IMPORTS + '!(import! &self ./src/public_worklog_cetta.metta)\n' + WIRE
        source += emit('(wt:wire (pw:transact %s %s "fixture" JsonNull))' % (q(nr), q(action)))
        raw = c.run('worklog-state-boundary-%02d' % index, source)
        native_files = snapshot(nr)
        c.save('state-boundary-%02d-native-files.json' % index, native_files)
        actual = rows(raw)
        instant = None
        if (nr/'notes.jsonl').exists() and (nr/'notes.jsonl').read_bytes():
            instant = json.loads((nr/'notes.jsonl').read_text().splitlines()[-1])['time']
        expected = oracle(c, pr, action, 'fixture', None, instant)
        expected_files = snapshot(pr)
        faults.append({'input_hex': state.hex(), 'actual': actual, 'expected': expected,
                       'native_files': native_files, 'oracle_files': expected_files})
        c.save('state-boundary-comparisons.json', faults)
        assert actual == [expected], (index, actual, expected)
        assert native_files == expected_files, index
        lock_free(nr); lock_free(pr)

    deep = '{"enabled": false, "seq": 0, "epoch": 0, "extra": ' + '[' * 1536 + '0' + ']' * 1536 + '}'
    root = c.directory / 'deep'
    root.mkdir()
    (root / 'state.json').write_text(deep)
    before_inode = (root / 'state.json').stat().st_ino
    source = IMPORTS + '!(import! &self ./src/public_worklog_cetta.metta)\n' + WIRE
    source += emit('(wt:wire (pw:transact %s "status" "" JsonNull))' % q(root))
    raw = c.run('deep-native-transaction', source)
    effects = snapshot(root)
    actual = rows(raw)
    lock_free(root)
    observed = (root / 'state.json').read_bytes()
    c.save('deep-native-transaction.json', {'actual': actual, 'files': effects,
        'bytes': len(observed), 'old_inode': before_inode,
        'new_inode': (root / 'state.json').stat().st_ino, 'lock_reacquired': True,
        'adjudication': 'mail236a8fc19ca9429bb51c659c2573b4bc; deliberate divergence, not Python equality'})
    assert actual == [['returned', deep]] and observed == deep.encode()
    assert (root / 'state.json').stat().st_ino != before_inode
    assert not any(p.name.startswith('state.tmp.') for p in root.iterdir())
    c.save('result.json', {'transaction_steps': len(operations), 'state_boundaries': len(states),
        'deep_native_bytes': len(observed), 'deep_python_equality': False,
        'not_claimed': ['mixed-process interleaving', 'fault injection', 'caller activation']})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    limit()
    assert sys.version_info[:3] == (3, 12, 13)
    args.evidence.mkdir(parents=True, exist_ok=False)
    (args.evidence / 'runner.py').write_bytes(Path(__file__).read_bytes())
    with tempfile.TemporaryDirectory(prefix='effect05-worklog-') as temporary:
        c = Qualification(args.evidence.resolve(), Path(temporary))
        with c.phase('native-worklog-transactions'):
            c.snapshot_sources('src/public_worklog_cetta.metta',
                'src/effect_receipt_io_cetta.metta', 'src/effect_receipt_values_cetta.metta',
                'src/public_worklog.py', 'tests/effect_receipts_support.py')
            check(c)


if __name__ == '__main__':
    main()
