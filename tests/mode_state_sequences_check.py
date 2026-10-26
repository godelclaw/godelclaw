"""Every-step mode file/print parity, isolated generated trees and counted controls."""
import argparse
import json
import os
from pathlib import Path
import re
import resource
import shutil
import tempfile

from effect_receipts_capability_checkpoint import LIMIT
from effect_receipts_support import IMPORTS, emit, q, rows
from mode_state_support import Qualification
from mode_state_native_check import CAPTURE, SNAPSHOT, captured, python, umask
from mode_state_modules_check import poison


SNAP = SNAPSHOT + '''
def normalized_snapshot(root):
    import re
    return {re.sub(r'\\.tmp\\.'+str(os.getpid())+r'$', '.tmp.PID', k):v
            for k,v in snapshot(root).items()}
'''

SEED = '''
def seed_pid(root, case):
    from pathlib import Path
    root = Path(root)
    for name in ('engine', 'recycle'):
        path = root / (name + '.tmp.' + str(os.getpid()))
        if case == 'temporary-directory': path.mkdir()
        else:
            path.write_bytes(b'stale\\n'); path.chmod(0o604)
            if case == 'unwritable-temporary': path.chmod(0o400)
    return 1
'''


def check(c):
    root = c.directory / 'state'
    fixture = poison(c)
    fixture_path = c.directory / 'mode_modules_fixture.py'
    fixture_path.write_text(fixture_path.read_text() + SNAP + SEED + '''
def seed(root, case):
    counts['seed'] = counts.get('seed', 0) + 1
    return seed_pid(root, case)
def state_snapshot(root):
    counts['snapshot'] += 1
    return json.dumps(normalized_snapshot(root), ensure_ascii=True)
''')
    (c.evidence / fixture_path.name).write_bytes(fixture_path.read_bytes())
    settings = {'METTACLAW_ENGINE_STATE_PATH': str(root / 'engine'),
                'METTACLAW_ENGINE_FAILURE_PATH': str(root / 'failure'),
                'METTACLAW_LOOP_MODE_PATH': str(root / 'loop.json'),
                'METTACLAW_LIFECYCLE_PATH': str(root / 'lifecycle.json'),
                'METTACLAW_RECYCLE_REQUEST_PATH': str(root / 'recycle'),
                'METTACLAW_WAKE_REQUEST_PATH': str(root / 'wake'),
                'PETTA_ROOT': str(root / 'petta'), 'CETTA_BIN': str(root / 'cetta')}
    operations = [('(engine:set "swi")', 'engine_modes.set_engine("swi")'),
                  ('(engine:set "cetta")', 'engine_modes.set_engine("cetta")'),
                  ('(engine:set "pleatta")', 'engine_modes.set_engine("pleatta")'),
                  ('(engine:set "unknown")', 'engine_modes.set_engine("unknown")'),
                  ('(mode:set "coding")', 'loop_modes.set_mode("coding")'),
                  ('(mode:set "agent")', 'loop_modes.set_mode("agent")'),
                  ('(mode:set "unknown")', 'loop_modes.set_mode("unknown")'),
                  ('(lifecycle:start)', 'lifecycle.start()'),
                  ('(lifecycle:stop)', 'lifecycle.stop()')]

    def seed(case):
        if root.exists():
            root.chmod(0o700)
            for p in root.rglob('*'):
                if p.is_dir(): p.chmod(0o700)
            shutil.rmtree(root)
        root.mkdir()
        (root / 'petta').mkdir()
        (root / 'petta/run.sh').write_text('fixture, never executed\n')
        (root / 'petta/run.sh').chmod(0o700)
        (root / 'cetta').write_text('fixture, never executed\n')
        (root / 'cetta').chmod(0o700)
        (root / 'engine').write_bytes(b'petta\n')
        (root / 'loop.json').write_bytes(b'{"mode":"godelclaw","autonomy_paused":true,"extra":7}\n')
        (root / 'loop.json.tmp').write_bytes(b'stale\n')
        (root / 'loop.json.tmp').chmod(0o604)
        (root / 'lifecycle.json').write_bytes(b'{"schema":1,"state":"stopped"}\n')
        if case == 'target-directory':
            for name in ('engine', 'loop.json', 'lifecycle.json'):
                (root / name).unlink(); (root / name).mkdir()
        elif case == 'temporary-directory':
            (root / 'loop.json.tmp').unlink(); (root / 'loop.json.tmp').mkdir()
        elif case == 'unwritable-temporary':
            (root / 'loop.json.tmp').chmod(0o400)
        elif case == 'unavailable':
            (root / 'petta/run.sh').chmod(0o600)
            (root / 'cetta').unlink()
        elif case == 'unwritable-directory':
            root.chmod(0o500)

    with c.phase('engine-loop-lifecycle-every-step-sequences'), umask(0o027):
        results = []
        for case in ('normal', 'target-directory', 'temporary-directory', 'unwritable-temporary', 'unavailable'):
            seed(case)
            source = IMPORTS + fixture + '!(import! &self ./src/mode_state.metta)\n' + CAPTURE
            source += '(= (seq:text $v) (if (== (get-type $v) String) $v (str:symbol->string $v)))\n'
            source += '!(remove-atom &self (= (system:wall-time) (__cetta_lib_system_host wall-time)))\n'
            source += '!(add-atom &self (= (system:wall-time) 1790000000.123456))\n'
            source += '!(py-call (mode_modules_fixture.seed %s "%s"))\n' % (q(root), case)
            for index, (native, _) in enumerate(operations):
                source += '!(println! (ModeStep %d))\n' % index
                source += emit('(JsonObject ((JsonPair "reply" %s) (JsonPair "engine" %s) '
                               '(JsonPair "mode" %s) (JsonPair "lifecycle" %s) '
                               '(JsonPair "files" (json:parse-python (seq:text '
                               '(py-call (mode_modules_fixture.state_snapshot %s)))))))' %
                               (captured(native), captured('(engine:view)'), captured('(mode:view)'),
                                captured('(lifecycle:view)'), q(root)))
            source += emit('(json:parse-python (seq:text (py-call (mode_modules_fixture.counters))))')
            output = c.run('native-sequence-' + case, source, settings)
            actual = rows(output); counts = actual.pop()
            assert counts == {'configure': 0, 'snapshot': len(operations), 'blocked': 0, 'seed': 1}, counts
            printed = [[] for _ in operations]
            index = None
            for line in output.splitlines():
                if line.startswith('(ModeStep '): index = int(line[len('(ModeStep '):-1])
                elif line.startswith(('[engine-mode]', '[loop-mode]')):
                    assert index is not None
                    printed[index].append(line)
            seed(case)
            expected = python(c, 'oracle-sequence-' + case, '''import json, io, contextlib, time, os
import engine_modes, loop_modes, lifecycle
''' + SNAP + SEED + '''
time.time = lambda: 1790000000.123456
root = ''' + repr(str(root)) + '''
seed_pid(root, ''' + repr(case) + ''')
def capture(call):
    try: return call()
    except Exception as exc: return {'error':type(exc).__name__,'message':str(exc)}
out=[]
for expression in ''' + repr([p for _, p in operations]) + ''':
    stream=io.StringIO()
    with contextlib.redirect_stdout(stream):
        reply=capture(lambda:eval(expression))
        value={'reply':reply, 'engine':capture(engine_modes.engine_view),
               'mode':capture(loop_modes.mode_view),'lifecycle':capture(lifecycle.view),
               'files':normalized_snapshot(root)}
    out.append({'value':value,'printed':stream.getvalue().splitlines()})
print(json.dumps(out, ensure_ascii=True))
''', settings)
            # mkstemp suffixes intentionally differ. Preserve raw receipts;
            # normalize only these allowed temporary names in error text.
            def norm(value):
                raw = json.dumps(value, ensure_ascii=True, sort_keys=True)
                raw = re.sub(r'\.lifecycle-[0-9a-zA-Z_\-]+', '.lifecycle-TEMP', raw)
                return json.loads(raw)
            for index, (a, e) in enumerate(zip(actual, expected)):
                c.save('comparison-%s-%02d.json' % (case, index), {'actual': a, 'expected': e, 'printed': printed[index]})
                assert norm(a) == norm(e['value']) and printed[index] == e['printed'], (case, index, a, e, printed[index])
            results.append({'case': case, 'steps': len(operations), 'actual': actual, 'printed': printed,
                            'expected': expected, 'allowed_control_counts': counts})
        c.save('sequence-results.json', {'results': results,
               'clock': 'explicit fixed clock fixture on both routes; real clock bracket qualified separately',
               'temporary_normalization': 'only .tmp.actualPID keys and random lifecycle suffixes; raw receipts retained'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (LIMIT, LIMIT))
    args.evidence.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='mode06-sequences-') as directory:
        c = Qualification(args.evidence.resolve(), Path(directory))
        c.snapshot_sources('tests/mode_state_sequences_check.py', 'src/engine_modes_cetta.metta',
                           'src/loop_modes_cetta.metta', 'src/lifecycle_cetta.metta', 'src/mode_io_cetta.metta')
        check(c)


if __name__ == '__main__':
    main()
