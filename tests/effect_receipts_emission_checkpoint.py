"""Reproduce the eager-emission vs Python dump failure/effect checkpoint.

This is a diagnostic witness for the currently qualified bundle, not a full
writer qualification. It does not implement a replacement encoder or parser.
"""
import argparse
import fcntl
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest import mock

from effect_receipts_support import IMPORTS, Qualification, emit, limit, q, rows


OBSERVE = '''
(= (emission:observe $value)
 (emission:result (json:stringify-python $value)))
(= (emission:with $value $options)
 (emission:result (json:stringify-python-with $value $options)))
(= (emission:result $wire)
 (case $wire
   (((JsonPythonErrorV1 $class $message $prefix)
      (JsonArray ("error" (str:symbol->string $class) $message $prefix)))
    ((JsonPythonErrorV1 $class $message)
      (JsonArray ("error" (str:symbol->string $class) $message)))
    ((JsonFailureV1 $phase $kind $source $message)
      (JsonArray ("native-failure" (str:symbol->string $kind) $message)))
    ($other (if (== (get-type $other) String)
      (JsonArray ("emitted" $other)) (JsonArray ("unrecognized")))))))
'''


def emission(value, options):
    """Observe eager dumps and streaming dump independently, without a codec shim."""
    stream = io.StringIO()
    try:
        eager = ['emitted', json.dumps(value, **options)]
    except Exception as exc:
        eager = ['error', type(exc).__name__, str(exc)]
    try:
        json.dump(value, stream, **options)
        incremental = ['emitted', stream.getvalue()]
    except Exception as exc:
        incremental = ['error', type(exc).__name__, str(exc), stream.getvalue()]
    return eager, incremental


def integer_cases(c):
    source = IMPORTS + OBSERVE
    expected, fixtures = [], []
    for digits in (4300, 4301):
        for sign in (1, -1):
            base = '9' * 4300
            number = sign * (int(base) + (digits == 4301))
            native = ('(+ (json:parse-python %s) 1)' % q(base)
                      if digits == 4301 else '(json:parse-python %s)' % q(base))
            if sign < 0:
                native = '(- 0 %s)' % native
            shapes = [
                ('scalar', number, native),
                ('first-list', [number], '(JsonArray (%s))' % native),
                ('second-list', [1, number], '(JsonArray (1 %s))' % native),
                ('nested-list', [1, [number]], '(JsonArray (1 (JsonArray (%s))))' % native),
                ('dict', {'a': 1, 'seq': number}, '(JsonObject ((JsonPair "a" 1) (JsonPair "seq" %s)))' % native),
                ('reordered-dict', {'z': number, 'a': 1}, '(JsonObject ((JsonPair "z" %s) (JsonPair "a" 1)))' % native),
                ('nested-dict', {'a': [1, {'seq': number}]}, '(JsonObject ((JsonPair "a" (JsonArray (1 (JsonObject ((JsonPair "seq" %s))))))))' % native),
            ]
            for shape, value, expression in shapes:
                for options, metta in (
                    ({}, '()'),
                    ({'ensure_ascii': False}, '((ensure-ascii False))'),
                    ({'sort_keys': True}, '((sort-keys True))'),
                    ({'sort_keys': True, 'separators': (',', ':')},
                     '((sort-keys True) (separators "," ":"))'),
                ):
                    eager, incremental = emission(value, options)
                    expected.append(incremental)
                    fixtures.append({'digits': digits, 'sign': sign, 'shape': shape,
                                     'options': options, 'dumps': eager, 'dump': incremental})
                    source += emit('(emission:with %s %s)' % (expression, metta))
                # Exercise the no-options API separately, not just with ().
                eager, incremental = emission(value, {})
                expected.append(incremental)
                fixtures.append({'digits': digits, 'sign': sign, 'shape': shape,
                                 'api': 'stringify-python', 'dumps': eager, 'dump': incremental})
                source += emit('(emission:observe %s)' % expression)
    actual = rows(c.run('integer-emission', source))
    mismatches = [i for i, (a, b) in enumerate(zip(expected, actual)) if a != b]
    c.save('integer-emission-comparison.json', {'fixtures': fixtures, 'actual': actual,
        'expected_count': len(expected), 'actual_count': len(actual), 'mismatches': mismatches})
    assert len(actual) == len(expected) and not mismatches, mismatches
    return len(expected)


def check(c):
    integer_count = integer_cases(c)
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
        fsync, replace, open_file = os.fsync, os.replace, os.open
        with mock.patch.dict(os.environ, c.environment(), clear=True), mock.patch.object(
                public_worklog.os, 'fsync', wraps=fsync) as sync, mock.patch.object(
                public_worklog.os, 'replace', wraps=replace) as rename, mock.patch.object(
                public_worklog.os, 'open', wraps=open_file) as opened:
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
                         'oracle_fsync_calls': sync.call_count,
                         'oracle_replace_calls': rename.call_count,
                         'oracle_descriptor_opens': [
                             [Path(call.args[0]).name, *call.args[1:]]
                             for call in opened.call_args_list],
                         'lock_reacquired': True})
    c.save('oracle-failure-effects.json', {'python_recursion_limit': sys.getrecursionlimit(),
        'python_int_max_str_digits': sys.get_int_max_str_digits(), 'fixtures': fixtures})
    assert [row['result'][1] for row in fixtures] == ['ValueError', 'ValueError', 'RecursionError']
    for row in fixtures:
        assert row['after']['state.json'] == row['before']['state.json']
        assert row['oracle_fsync_calls'] == 0
        assert row['oracle_replace_calls'] == 0
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
    assert len(actual) == 3, actual
    for index in (0, 1):
        assert actual[index][:3] == fixtures[index]['result'], actual[index]
        assert len(actual[index]) == 4, actual[index]
    temporary = next(data for name, data in fixtures[0]['after'].items()
                     if name.startswith('state.tmp.'))
    assert actual[0][3].encode().hex() == temporary
    deep_prefix = next(data for name, data in fixtures[2]['after'].items()
                       if name.startswith('state.tmp.'))
    deep_expected = fixtures[2]['result'] + [bytes.fromhex(deep_prefix).decode()]
    deep_matches = actual[2] == deep_expected
    c.save('deep-state-comparison.json', {'expected': deep_expected, 'actual': actual[2],
        'exact_match': deep_matches,
        'limit': 'Python recursion boundary is call-stack-sensitive; these are captured effects, not a fixed depth rule'})
    c.save('checkpoint.json', {'status': ('EMISSION_GATE_PASSED' if deep_matches else
        'CAPABILITY_CHECKPOINT_RECURSION'), 'runtime_activated': False,
        'integer_observations_matched': integer_count, 'deep_state_exact_match': deep_matches,
        'required': ['json.dump RecursionError and exact incremental/error-prefix outcomes',
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
    print('Emission diagnostics complete: inspect checkpoint.json; no writer or caller activation')


if __name__ == '__main__':
    main()
