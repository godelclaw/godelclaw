"""Reproduce the eager-emission vs Python dump failure/effect checkpoint.

This is a diagnostic witness for the currently qualified bundle, not a full
writer qualification. It does not implement a replacement encoder or parser.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest import mock

from effect_receipts_support import IMPORTS, Qualification, emit, limit, q, rows


OBSERVE = '''
(= (emission:observe $value)
 (let $wire (json:stringify-python $value)
  (case $wire
   (((JsonPythonErrorV1 $class $message)
      (JsonArray ("error" (str:symbol->string $class) $message)))
    ((JsonFailureV1 $phase $kind $source $message)
      (JsonArray ("native-failure" (str:symbol->string $kind) $message)))
    ($other (if (== (get-type $other) String)
      (JsonArray ("emitted" $other)) (JsonArray ("unrecognized"))))))))
'''


def check(c):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    import public_worklog
    big = '9' * 4300
    specs = [
        ('epoch-limit', '{"enabled": false, "seq": 0, "epoch": ' + big + '}', 'on'),
        ('seq-limit', '{"enabled": true, "seq": ' + big + ', "epoch": 1}', 'add'),
        ('deep-state', '{"enabled": false, "seq": 0, "epoch": 0, "extra": ' + '[' * 1536 + '0' + ']' * 1536 + '}', 'status'),
    ]
    fixtures = []
    for name, document, action in specs:
        root = c.directory / name
        root.mkdir()
        (root / 'state.json').write_text(document)
        before = {p.name: p.read_bytes().hex() for p in root.iterdir()}
        fsync = os.fsync
        with mock.patch.dict(os.environ, c.environment(), clear=True), mock.patch.object(
                public_worklog.os, 'fsync', wraps=fsync) as sync:
            try:
                public_worklog.transact(root, action, 'fixture note')
                result = ['returned']
            except Exception as exc:
                result = ['error', type(exc).__name__, str(exc)]
        after = {p.name: p.read_bytes().hex() for p in root.iterdir()}
        with (root / 'lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock, fcntl.LOCK_UN)
        fixtures.append({'name': name, 'action': action, 'input': document,
                         'result': result, 'before': before, 'after': after,
                         'oracle_fsync_calls': sync.call_count, 'lock_reacquired': True})
    c.save('oracle-failure-effects.json', {'python_recursion_limit': sys.getrecursionlimit(),
        'python_int_max_str_digits': sys.get_int_max_str_digits(), 'fixtures': fixtures})
    assert [row['result'][1] for row in fixtures] == ['ValueError', 'ValueError', 'RecursionError']
    for row in fixtures:
        assert row['after']['state.json'] == row['before']['state.json']
        assert row['oracle_fsync_calls'] == 0
    assert fixtures[1]['after']['notes.jsonl'] == ''
    for row in (fixtures[0], fixtures[2]):
        temporary = [data for name, data in row['after'].items() if name.startswith('state.tmp.')]
        assert len(temporary) == 1 and temporary[0], row['name']
    source = IMPORTS + '!(import! &self ./src/effect_receipt_values_cetta.metta)\n' + OBSERVE
    source += emit('(let $state (json:parse-python %s) (let $next (erv:put (erv:put $state "enabled" True) "epoch" (+ (erv:get $state "epoch" 0) 1)) (emission:observe $next)))' % q(specs[0][1]))
    source += emit('(let $state (json:parse-python %s) (emission:observe (JsonObject ((JsonPair "seq" (+ (erv:get $state "seq" 0) 1)) (JsonPair "time" "fixture") (JsonPair "text" "fixture note")))))' % q(specs[1][1]))
    source += emit('(emission:observe (json:parse-python %s))' % q(specs[2][1]))
    actual = rows(c.run('native-eager-emission', source))
    c.save('native-eager-emission.json', {'actual': actual,
        'emitted_bytes_hex': [row[1].encode('utf-8', 'surrogatepass').hex() if row[0] == 'emitted' else None for row in actual]})
    assert len(actual) == 3 and all(row[0] == 'emitted' for row in actual), actual
    c.save('checkpoint.json', {'status': 'CAPABILITY_CHECKPOINT_EMISSION', 'runtime_activated': False,
        'required': ['Python integer-emission digit-limit errors',
                     'json.dump incremental/error-prefix outcomes distinct from eager dumps',
                     'preserve no-fsync/no-rename, partial temporary and empty opened note effects'],
        'not_claimed': ['writer/caller parity', 'full transaction durability', 'performance', 'Lean replay']})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    assert sys.version_info[:3] == (3, 12, 13), sys.version
    limit()
    args.evidence.mkdir(parents=True, exist_ok=False)
    (args.evidence / 'runner.py').write_bytes(Path(__file__).read_bytes())
    with tempfile.TemporaryDirectory(prefix='effect05-emission-checkpoint-') as temp:
        c = Qualification(args.evidence.resolve(), Path(temp))
        with c.phase('python-emission-checkpoint'):
            check(c)
    print('CAPABILITY_CHECKPOINT_EMISSION: no writer or caller activation')


if __name__ == '__main__':
    main()
