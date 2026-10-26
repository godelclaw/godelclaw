"""Staged actual command handlers and loop gates; no transport/provider/live boot."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import tempfile

from effect_receipts_capability_checkpoint import LIMIT
from effect_receipts_support import IMPORTS, ROOT, emit, q, rows
from mode_state_native_check import CAPTURE, captured
from mode_state_support import Qualification


CONTROL = '''import json, os, time
import engine_modes, loop_modes, lifecycle, fuel_modes, attention, helper, sibling, telegram
counts = {'blocked':0,'adopt':0,'metta_value':0,'resume':0,'snapshot':0}
def blocked(*args, **kwargs):
    counts['blocked'] += 1
    raise AssertionError('ported Python reached on native caller')
if os.environ['MODE_STATE_POISON'] == '1':
    for module,names in ((engine_modes,('active_engine','selected_engine','engine_view','set_engine','engine_command','request_recycle')),
                         (loop_modes,('process_mode','mode_view','modes_view','set_mode','wait_timed_out')),
                         (lifecycle,('cognition_enabled','view','start','stop')),
                         (attention,('description',)),
                         (fuel_modes,('refuel','spend','ceiling','current_fuel','set_fuel'))):
        for name in names: setattr(module,name,blocked)
original_adopt=helper.adopt_active_engine
def adopt():
    counts['adopt'] += 1
    return original_adopt()
def metta_value(name, default=''):
    counts['metta_value'] += 1
    assert name == 'active_engine'
    return 'cetta'
def resume():
    counts['resume'] += 1
    return 'fixture supervisor resumed'
helper.adopt_active_engine=adopt
telegram.metta_value=metta_value
sibling.resume_self=resume
time.time=lambda:1790000000.123456
def summary(): return json.dumps(counts,sort_keys=True)
'''

TRANSPORT = '''
!(match &self (= (tc:call $code $name $body) $prior)
  (remove-atom &self (= (tc:call $code $name $body) $prior)))
!(bind! &mode-test-transport (new-state ()))
(= (tc:call $code $name $body)
 (let $_ (change-state! &mode-test-transport
   (cons-atom (JsonArray ($code $name $body)) (get-state &mode-test-transport)))
   (cwp:reply 66 "fixture-disabled-transport" "")))
(= (caller:text $value) (if (== (get-type $value) String) $value (str:symbol->string $value)))
'''


def stage(c, name):
    package = c.directory / name
    package.mkdir()
    hashes = {}
    for part in ('src', 'channels', 'verification'):
        shutil.copytree(ROOT / part, package / part, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        for path in sorted((package / part).rglob('*')):
            if path.is_file():
                hashes[path.relative_to(package).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    c.save(name + '-source-hashes.json', hashes)
    return package


def check(c):
    with c.phase('actual-handlers-and-loop-gate'):
        package = stage(c, 'package')
        fixture = c.directory / 'mode_caller_fixture.py'
        fixture.write_text(CONTROL)
        (c.evidence / fixture.name).write_bytes(fixture.read_bytes())
        root = c.directory / 'state'
        root.mkdir()
        petta = root / 'petta'; petta.mkdir()
        (petta / 'run.sh').write_text('harmless fixture, never executed\n'); (petta / 'run.sh').chmod(0o700)
        settings = {'MODE_STATE_POISON': '1', 'PETTA_ROOT': str(petta),
                    'PYTHONPATH': os.pathsep.join(map(str, (c.directory, package/'src', package/'channels', ROOT/'tests'))),
                    'METTACLAW_ACTIVE_LOOP_MODE': 'coding'}
        source = IMPORTS + '!(import! &self %s)\n' % fixture
        source += '!(import! &self %s)\n' % (package / 'channels/telegram_control.metta') + TRANSPORT + CAPTURE
        source += '!(match &self (= (system:wall-time) $prior) (remove-atom &self (= (system:wall-time) $prior)))\n'
        source += '(= (system:wall-time) 1790000000.123456)\n'
        commands = [('/engine', ''), ('/engine', 'swi'), ('/engine', 'pleatta'),
                    ('/mode', ''), ('/mode', 'coding'), ('/stop', ''), ('/start', '')]
        for number, (command, argument) in enumerate(commands):
            body = json.dumps(['command', 'fixture-key', command, argument, 'operator'])
            task = 'fixture-task-%d' % number
            source += emit('(tc:handle ((%s %s)))' % (q(task), q(body)))
        source += emit('(JsonArray (get-state &mode-test-transport))')
        source += emit('(json:parse-python (caller:text (py-call (mode_caller_fixture.summary))))')
        output = c.run('actual-telegram-handlers', source, settings, runtime_root=package)
        actual = rows(output)
        counts = actual[-1]
        assert actual[:len(commands)] == [1] * len(commands), actual
        assert counts == {'blocked':0,'adopt':3,'metta_value':3,'resume':1,'snapshot':0}, counts
        replies = list(reversed(actual[-2]))
        assert len(replies) == len(commands)
        decoded = [json.loads(row[2]) for row in replies]
        assert decoded[0][0] == 'answer' and 'active engine: cetta' in decoded[0][1], decoded
        assert "engine set to 'petta' (from alias 'swi'); recycling into it" == decoded[1][1]
        assert decoded[2][1].startswith('engine-set failed: PLeaTTa')
        assert decoded[3][1].startswith('selected mode: godelclaw')
        assert decoded[4][1] == "mode set to 'coding'; persists across restarts"
        assert decoded[5][1] == 'Stopped. Cognition is paused and nothing is sent until /start.'
        assert decoded[6][1] == 'Started. Cognition is enabled; fixture supervisor resumed.'
        c.save('handler-results.json', {'replies': decoded, 'transport_calls': replies, 'counts': counts,
               'transport': 'test replacement, no socket opened; actual tc:handle/tc:answer and file writers',
               'files': {name: (c.directory/name).read_bytes().hex() if (c.directory/name).is_file() else None
                         for name in ('engine-state','loop-mode.json','lifecycle.json','wake','recycle')}})

        (c.directory / 'fuel-mode.json').write_text('{"fuel":"decay"}')
        source = IMPORTS + '!(import! &self %s)\n' % fixture
        source += '!(import! &self ./src/open_assemblage.metta)\n!(import! &self ./src/loop_policy.metta)\n'
        source += '!(import! &self ./src/loop.metta)\n' + CAPTURE
        source += '''
!(match &self (= (enabledTurnCandidate $p) $prior)
  (remove-atom &self (= (enabledTurnCandidate $p) $prior)))
!(bind! &mode-test-enabled (new-state 0))
(= (enabledTurnCandidate $p)
 (let $_ (change-state! &mode-test-enabled (+ (get-state &mode-test-enabled) 1)) (candidate $p)))
!(match &self (= (sleepInterval) $prior) (remove-atom &self (= (sleepInterval) $prior)))
(= (sleepInterval) 1)
(= (get_time_as_string) "fixture-clock")
(= (caller:periphery) (coordinate active-policy (mode:process) (coordinate loops 20
 (coordinate autonomous-ready 1 coordinates-empty))))
(= (caller:gate)
 (let (candidate $p) (turnCandidate (caller:periphery))
  (JsonArray ((loop-budget $p) (loop-autonomous-ready $p) (get-state &mode-test-enabled)))))
'''
        source += emit(captured('(policy-burst-budget (mode:process))'))
        source += '!(lifecycle:stop)\n' + emit('(caller:gate)')
        source += '!(lifecycle:start)\n' + emit('(caller:gate)')
        source += emit(captured('(loop-budget (applyAutonomousBurst (caller:periphery) True))'))
        source += '''
!(match &self (= (lifecycle-native:read) $prior) (remove-atom &self (= (lifecycle-native:read) $prior)))
(= (lifecycle-native:read) (ErFailure RuntimeError "injected native latch failure" fixture))
(= (caller:error-gate $value)
 (case $value
  (((Error $call (PythonException $class $message $detail))
    (JsonObject ((JsonPair "error" (str:symbol->string $class)) (JsonPair "message" $message))))
   ((candidate $p) (JsonArray ((loop-budget $p) (loop-autonomous-ready $p) (get-state &mode-test-enabled)))))))
'''
        source += emit('(caller:error-gate (turnCandidate (caller:periphery)))')
        source += emit('(caller:error-gate (waitCandidate (caller:periphery)))')
        output = c.run('actual-loop-gate-and-renewal', source, settings, runtime_root=package)
        actual = rows(output)
        c.save('loop-results.json', {'actual': actual,
               'limits': 'actual outer cognition gate with counted inert inner candidate; actual autonomous renewal, no provider turn'})
        assert actual == [50, [0,0,0], [20,1,1], 65,
                          {'error':'RuntimeError','message':'injected native latch failure'},
                          {'error':'RuntimeError','message':'injected native latch failure'}], actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (LIMIT, LIMIT))
    args.evidence.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='mode06-callers-') as directory:
        c = Qualification(args.evidence.resolve(), Path(directory))
        c.snapshot_sources('tests/mode_state_callers_check.py', 'channels/telegram_control.metta', 'src/loop.metta')
        check(c)


if __name__ == '__main__':
    main()
