"""Artifact-specific project06 dependency qualification; no release selection."""
import argparse
import json
import os
from pathlib import Path
import pwd
import resource
import tempfile
import time

from effect_receipts_capability_checkpoint import LIMIT
from effect_receipts_support import IMPORTS, emit, q, rows
from mode_state_native_check import python, literal, active, fuel_reads, fuel_numeric_boundaries, fuel_sequences
from mode_state_support import Qualification


def dependencies(c):
    with c.phase("relied-on-native-apis"):
        temporary, target = c.directory / 'temporary', c.directory / 'published'
        source = IMPORTS + f'''
!(println! (Effects (let* (($w (fs:open-write {q(temporary)} ("create" "exclusive") 384))
 ($a (fs:write-all $w "hello\n")) ($b (fs:sync $w)) ($c (fs:set-mode $w 416))
 ($d (fs:close $w)) ($e (fs:rename {q(temporary)} {q(target)}))) ($a $b $c $d $e))))
'''
        source += emit('(fs:access %s ("read"))' % q(target))
        source += emit('(system:signal-probe 0)') + emit('(system:signal-probe (system:pid))')
        source += '!(println! (Overflow (catch (system:signal-probe 2147483648))))\n'
        source += emit('(system:errno-value "EPERM")') + '!(println! (MissingErrno (system:errno-value "NOT_AN_ERRNO")))\n'
        source += emit('(str:format-fixed 123.4567895 6)') + emit('(system:wall-time)')
        source += emit('(json:stringify-python-with (json:parse-python %s) ((ensure-ascii False) (indent 2)))'
                       % literal('{"z":1,"a":2}'))
        before = time.time()
        output = c.run('owned-io-clock-signal-errno-json', source)
        after = time.time()
        actual = rows(output)
        assert '(Effects ((fs:written 6) fs:synced fs:mode-set fs:closed ()))' in output, output
        assert actual[:2] == [True, True] and actual[2] is True, actual
        import errno
        assert actual[3:5] == [errno.EPERM, '%.6f' % 123.4567895], actual
        assert '(MissingErrno ())' in output and 'expected a process ID within pid_t' in output
        assert before <= actual[5] <= after
        assert actual[6] == json.dumps({'z': 1, 'a': 2}, ensure_ascii=False, indent=2), actual
        assert target.read_bytes() == b'hello\n' and target.stat().st_mode & 0o777 == 0o640
        assert not temporary.exists()
        overflow_output = output
        output = c.run('remove-owned-output', IMPORTS + f'!(println! (Remove (fs:remove {q(target)})))\n!(println! (Missing (fs:remove {q(target)})))\n')
        assert '(Remove ())' in output and '(Missing (fs:failed fs:missing remove ' in output
        assert not target.exists()
        c.save('dependency-results.json', {'clock_bracket': [before, actual[5], after],
              'owned_effect_hex': b'hello\n'.hex(), 'mode': '0o640', 'overflow_stdout': overflow_output,
              'limit': 'supported API smoke; full module/fault gates are separate'})

    with c.phase('python-lower-domain'):
        values = ['İ', 'ΟΣ', 'Σ', 'AΣ', 'AΣA', 'AΣ\u0301', 'AΣ\u0301A',
                  'Σ\u0301', 'A\u0301Σ', 'AΣ\u200dA', 'AΣ\u200d', 'AΣ A',
                  'KȺẞ', '\ud800AΣ', 'AΣ\udfff', '日🙂ABC', '', 'CETTA']
        source = IMPORTS + ''.join(emit('(str:python-lower %s)' % literal(v)) for v in values)
        actual = rows(c.run('unicode-lower-values', source))
        assert actual == [v.lower() for v in values], actual
        malformed = c.run('unicode-lower-errors', IMPORTS + '''
!(println! (Malformed (catch (str:python-lower (str:byte-slice "é" 1 2)))))
!(println! (Literal (Error fixture literal-value)))
''')
        assert 'StrMalformedUtf8V1' in malformed and '(Literal (Error fixture literal-value))' in malformed
        c.save('lower-results.json', {'inputs': values, 'actual': actual,
               'malformed_stdout': malformed, 'literal_error_is_not_an_evaluation_failure': True})


def paths(c):
    homes = [None, '', '/', '//', '///', 'relative', 'relative///', '/fixture/日///',
             '~', '~/nested/', '/tmp/home/', '//fixture///']
    account = pwd.getpwuid(os.getuid()).pw_name
    inputs = ['', 'plain', '~', '~/', '~//x', '~/.local/state', '~root', '~root/x',
              '~' + account, '~' + account + '/x', '~mode06_missing_account_9fc7/x',
              '~~/x', '~日/x', '~/日/../x', '/~', 'a/~', '~//', '~root//', '~root/../x']
    with c.phase('expanduser-and-eager-path-consumers'):
        records = []
        for number, home in enumerate(homes):
            unset = ['METTACLAW_ENGINE_STATE_PATH', 'METTACLAW_ENGINE_FAILURE_PATH',
                     'METTACLAW_LIFECYCLE_PATH', 'METTACLAW_INSTANCE', 'XDG_STATE_HOME',
                     'PETTA_ROOT', 'CETTA_ROOT', 'CETTA_BIN']
            if home is None:
                unset.append('HOME')
            c.unset_environment = tuple(unset)
            settings = {} if home is None else {'HOME': home}
            try:
                expected = python(c, 'path-oracle-%02d' % number, '''import json, os
import engine_modes, loop_modes, lifecycle
values = ''' + repr(inputs) + '''
print(json.dumps([list(map(os.path.expanduser, values)), engine_modes._state_path(),
engine_modes._failure_path(), loop_modes._active_path(), str(lifecycle._path()),
engine_modes.engine_command('swi'), engine_modes.engine_command('cetta'), engine_modes.engine_command('unknown')], ensure_ascii=True))
''', settings)
                # Expressions are native list values, not host calls.
                expressions = ['(JsonArray (' + ' '.join('(system:expanduser %s)' % literal(v) for v in inputs) + '))']
                expressions += ['(mode-path:engine)', '(mode-path:failure)', '(mode-path:active-loop)',
                                '(mode-path:lifecycle)', '(JsonArray (mode-path:commands "petta"))',
                                '(JsonArray (mode-path:commands "cetta"))', '(JsonArray (mode-path:commands "unknown"))']
                source = IMPORTS + '!(import! &self ./src/mode_paths_cetta.metta)\n'
                actual = rows(c.run('path-native-%02d' % number, source + ''.join(map(emit, expressions)), settings))
                assert actual == expected, (home, actual, expected)
                records.append({'HOME': home, 'actual': actual})
            finally:
                c.unset_environment = ()
        c.save('path-results.json', {'records': records, 'expanduser_paths': inputs,
               'limits': 'strings only, no account-derived state accessed; native NSS failure injection not performed'})

    with c.phase('eager-home-lookup-with-explicit-roots'):
        # No account-derived path is opened. Both implementations compute
        # strings only, with HOME absent and generated XDG/engine roots.
        for explicit in (False, True):
            unset=['HOME']
            if not explicit:
                unset += ['METTACLAW_ENGINE_STATE_PATH','METTACLAW_LIFECYCLE_PATH']
            c.unset_environment=tuple(unset)
            try:
                settings={'PETTA_ROOT':str(c.directory/'petta-root'),
                          'CETTA_ROOT':str(c.directory/'cetta-root')}
                expected=python(c,'eager-oracle-'+str(explicit),'''import json,os,pwd
import engine_modes,loop_modes,lifecycle
calls=[];original=os.path.expanduser
def expand(path):
    calls.append(path);return original(path)
os.path.expanduser=expand
out=[engine_modes._state_path(),loop_modes._active_path(),str(lifecycle._path()),
     list(engine_modes.engine_command('petta')),list(engine_modes.engine_command('cetta')),
     list(engine_modes.engine_command('unknown'))]
print(json.dumps([out,calls]))
''',settings)
                source=IMPORTS+'!(import! &self ./src/mode_paths_cetta.metta)\n'+'''
!(bind! &path-test-expands (new-state ()))
!(match &self (= (system:expanduser $path) $prior) (remove-atom &self (= (system:expanduser $path) $prior)))
(= (system:expanduser $path)
 (let $_ (change-state! &path-test-expands (cons-atom $path (get-state &path-test-expands)))
   (__cetta_lib_system_host expanduser $path)))
'''
                source+=emit('(JsonArray ((mode-path:engine) (mode-path:active-loop) (mode-path:lifecycle) '
                             '(JsonArray (mode-path:commands "petta")) (JsonArray (mode-path:commands "cetta")) '
                             '(JsonArray (mode-path:commands "unknown"))))')
                source+=emit('(JsonArray (get-state &path-test-expands))')
                actual=rows(c.run('eager-native-'+str(explicit),source,settings))
                actual[1].reverse()
                assert actual==expected,(explicit,actual,expected)
                assert len(actual[1])==(3 if explicit else 6)
                c.save('eager-result-'+str(explicit)+'.json',{'actual':actual,'strings_only':True,'HOME':'absent'})
            finally:
                c.unset_environment=()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--phase', choices=('dependencies', 'active-fuel'), default='dependencies')
    parser.add_argument('--swipl', type=Path)
    parser.add_argument('--swi-root', type=Path)
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (LIMIT, LIMIT))
    args.evidence.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='mode06-artifact-') as directory:
        c = Qualification(args.evidence.resolve(), Path(directory))
        c.snapshot_sources('tests/mode_state_artifact_check.py', 'tests/mode_state_support.py',
                           'src/mode_paths_cetta.metta', 'src/mode_io_cetta.metta', 'src/fuel_cetta.metta')
        if args.phase == 'dependencies':
            dependencies(c)
            paths(c)
            from mode_state_cwd_check import check as cwd_check
            cwd_check(c)
        else:
            active(c, args)
            fuel_reads(c)
            fuel_numeric_boundaries(c)
            fuel_sequences(c)
        c.save('result.json', {'status': 'PASS_ARTIFACT_SUBSET', 'full_project06_complete': False})


if __name__ == '__main__':
    main()
