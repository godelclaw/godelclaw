"""Isolated module parity, native ported functions poisoned; artifact guarded."""
import argparse
import json
import os
from pathlib import Path
import resource
import tempfile

from effect_receipts_capability_checkpoint import LIMIT
from effect_receipts_support import IMPORTS, emit, rows
from mode_state_support import Qualification
from mode_state_native_check import CAPTURE, FIXTURE, captured, literal, python


def poison(c):
    source = FIXTURE + '''
import loop_modes, lifecycle
for module, names in ((loop_modes, ('canonical','current_mode','process_mode','active_mode','mode_view','modes_view','set_mode','wait_timed_out')),
                      (lifecycle, ('cognition_enabled','view','start','stop'))):
    for name in names:
        setattr(module, name, blocked)
'''
    path = c.directory / 'mode_modules_fixture.py'
    path.write_text(source)
    (c.evidence / path.name).write_bytes(path.read_bytes())
    return '!(import! &self %s)\n' % path


def read_states(c):
    with c.phase('loop-lifecycle-read-parity'):
        fixture = poison(c)
        cases = [None, b'bad', b'[]', b'null', b'\xff', b'\xef\xbb\xbf{}',
                 b'{"mode":"coding","autonomy_paused":true}', b'{"mode":"iter","mode":"omega"}',
                 b'{"mode":"coding"}\r', b'{"mode":"\\ud800"}',
                 b'{"schema":1,"state":"running"}', b'{"schema":1.0,"state":"running"}',
                 b'{"schema":true,"state":"running"}', b'{"schema":1,"state":"running","extra":0}',
                 b'{"state":"running","schema":1}', b'{"schema":NaN,"state":"running"}']
        records = []
        for number, raw in enumerate(cases):
            for filename in ('loop-mode.json', 'lifecycle.json'):
                path = c.directory / filename
                if raw is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(raw)
            expected = python(c, 'reads-oracle-%02d' % number, '''import json, loop_modes, lifecycle, attention
print(json.dumps([loop_modes.process_mode(), loop_modes.mode_view(),loop_modes.modes_view(),
lifecycle.cognition_enabled(),lifecycle.view(),attention.description()], ensure_ascii=True))
''')
            source = IMPORTS + fixture + '!(import! &self ./src/mode_state.metta)\n'
            expressions = ['(str:symbol->string (mode:process))', '(mode:view)', '(mode:list-view)', '(lifecycle:enabled)',
                           '(lifecycle:view)', '(attention:description)']
            actual = rows(c.run('reads-native-%02d' % number, source + ''.join(map(emit, expressions))))
            assert actual == expected, (number, actual, expected)
            records.append({'hex': None if raw is None else raw.hex(), 'actual': actual})
        c.save('read-results.json', records)


def active_names(c):
    with c.phase('actual-active-mode-unicode-parity'):
        fixture = poison(c)
        values = ['İ', 'ΟΣ', 'AΣ\u0301', 'AΣ\u0301A', '\ud800AΣ', 'AΣ\udfff',
                  'Σ', 'ΣA', 'AΣ\u200dA', 'AΣ\u200d', ' CODING ', 0, False, [], ['ΟΣ'], {'İ': 'Σ'}]
        records = []
        (c.directory / 'loop-mode.json').write_text('{"mode":"coding"}')
        for number, value in enumerate(values):
            (c.directory / 'active-loop.json').write_text(json.dumps({'pid': 0, 'mode': value}))
            expected = python(c, 'active-mode-oracle-%02d' % number, '''import json, loop_modes
print(json.dumps([loop_modes.active_mode(),loop_modes.mode_view(),loop_modes.modes_view()], ensure_ascii=True))
''')
            source = IMPORTS + fixture + '!(import! &self ./src/mode_state.metta)\n'
            actual = rows(c.run('active-mode-native-%02d' % number, source + ''.join(map(emit,
                ['(loop-mode:active)', '(mode:view)', '(mode:list-view)']))))
            assert actual == expected, (value, actual, expected)
            records.append({'input': value, 'actual': actual})
        c.save('active-mode-results.json', records)


def writes(c):
    with c.phase('lifecycle-write-smoke'):
        fixture = poison(c)
        source = IMPORTS + fixture + '!(import! &self ./src/mode_state.metta)\n' + CAPTURE
        expressions = ['(lifecycle:start)', '(lifecycle:enabled)', '(lifecycle:view)',
                       '(lifecycle:stop)', '(lifecycle:enabled)', '(lifecycle:view)']
        actual = rows(c.run('lifecycle-native-sequence', source + ''.join(emit(captured(x)) for x in expressions)))
        expected = python(c, 'lifecycle-oracle-sequence', '''import json, lifecycle
print(json.dumps([lifecycle.start(),lifecycle.cognition_enabled(),lifecycle.view(),
lifecycle.stop(),lifecycle.cognition_enabled(),lifecycle.view()]))
''')
        assert actual == expected, (actual, expected)
        path = c.directory / 'lifecycle.json'
        assert path.read_bytes() == b'{"schema": 1, "state": "stopped"}\n'
        assert path.stat().st_mode & 0o777 == 0o600
        assert not list(c.directory.glob('.lifecycle-*'))
        c.save('write-smoke-results.json', {'actual': actual, 'hex': path.read_bytes().hex(), 'mode': '0o600',
               'limit': 'smoke only; every-step mixed-route and fault qualification follows separately'})


def pids(c):
    with c.phase('active-mode-int-and-pid-boundaries'):
        fixture = poison(c)
        values = [0, os.getpid(), 2147483647, 1, True, False, None, [], {}, '', '０',
                  ' +0_0 ', '\x1c0', 0.9, float('nan'), float('inf'),
                  2147483648, -2147483649, 2**63, -(2**63)-1, 10**100, -(10**100)]
        records = []
        witness = python(c, 'safe-other-user-probe', '''import json, os
try:
    os.kill(1, 0)
    value = {'pid':1,'result':True}
except OSError as exc:
    value = {'pid':1,'class':type(exc).__name__,'errno':exc.errno,'message':str(exc)}
print(json.dumps(value))
''')
        (c.directory / 'loop-mode.json').write_text('{"mode":"coding"}')
        for number, value in enumerate(values):
            (c.directory / 'active-loop.json').write_text(json.dumps({'pid': value, 'mode': 'İ'}))
            expected = python(c, 'pid-oracle-%02d' % number, '''import json, loop_modes
def capture(call):
    try: return call()
    except Exception as exc: return {'error':type(exc).__name__, 'message':str(exc)}
print(json.dumps([capture(loop_modes.active_mode),capture(loop_modes.mode_view)], ensure_ascii=True))
''')
            source = IMPORTS + fixture + '!(import! &self ./src/mode_state.metta)\n' + CAPTURE
            actual = rows(c.run('pid-native-%02d' % number, source + ''.join(emit(captured(x)) for x in
                          ['(loop-mode:active)', '(mode:view)'])))
            c.save('pid-case-%02d.json' % number, {'input': value, 'actual': actual, 'expected': expected})
            assert actual == expected, (value, actual, expected)
            records.append({'input': value, 'actual': actual})
        c.save('pid-results.json', {'records': records, 'other_user_signal_zero_observation': witness,
                                  'limits': 'kill0 only; native errors are explicit Error values consumed by catch, not Python exceptions'})


def engine_reads(c):
    with c.phase('engine-readline-and-failure-text'):
        fixture = poison(c)
        cases = [None, b'', b'CETTA\rpetta\n', b'unknown\n', b'\xef\xbb\xbfcetta\n', b'\xff',
                 b'cetta\n\xff', b'cetta\n' + b'a' * 8190 + b'\xff',
                 b'cetta\r\npetta', b'cetta\n' + b'a' * 8185 + b'\xc3',
                 b'cetta\n' + b'a' * 8186 + b'\xc3', b' \xc2\xa0CeTTa\xe3\x80\x80\n']
        failures = [None, b'engine=cetta\rreason=failed\rstatus=3\r',
                    b'engine=petta\nreason=first\nreason=last=a\n', b'engine=unknown\nreason=x\n',
                    b'engine=cetta\nreason=\n', b'engine=cetta\nreason=\xff']
        records = []
        for number, raw in enumerate(cases):
            path = c.directory / 'engine-state'
            if raw is None: path.unlink(missing_ok=True)
            else: path.write_bytes(raw)
            fail = failures[number % len(failures)]
            path = c.directory / 'engine-failure'
            if fail is None: path.unlink(missing_ok=True)
            else: path.write_bytes(fail)
            expected = python(c, 'engine-read-oracle-%02d' % number, '''import json, engine_modes
def capture(call):
    try: return call()
    except Exception as exc: return {'error':type(exc).__name__, 'message':str(exc)}
print(json.dumps([capture(engine_modes.selected_engine),capture(engine_modes.engine_view)], ensure_ascii=True))
''')
            source = IMPORTS + fixture + '!(import! &self ./src/engine_modes.metta)\n' + CAPTURE
            actual = rows(c.run('engine-read-native-%02d' % number, source + ''.join(emit(captured(x)) for x in
                          ['(engine:selected)', '(engine:view)'])))
            c.save('engine-read-case-%02d.json' % number, {'hex': None if raw is None else raw.hex(),
                                                        'actual': actual, 'expected': expected})
            assert actual == expected, (number, actual, expected)
            records.append({'hex': None if raw is None else raw.hex(), 'actual': actual})
        c.save('engine-read-results.json', records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--phase', choices=('basic', 'boundaries'), default='basic')
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (LIMIT, LIMIT))
    args.evidence.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='mode06-modules-') as directory:
        c = Qualification(args.evidence.resolve(), Path(directory))
        c.snapshot_sources('src/loop_modes_cetta.metta', 'src/lifecycle_cetta.metta', 'src/mode_state.metta',
                           'src/mode_state_python.metta', 'tests/mode_state_modules_check.py')
        if args.phase == 'basic':
            read_states(c)
            active_names(c)
            writes(c)
        else:
            pids(c)
            engine_reads(c)
        c.save('result.json', {'status': 'PASS_MODULE_SUBSET', 'full_project06_complete': False})


if __name__ == '__main__':
    main()
