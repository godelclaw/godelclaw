"""Disposable exact-source Lean compilation/replay; existing caches read only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from effect_receipts_support import IMPORTS, ROOT, emit, limit, rows
from mode_state_native_check import q
from mode_state_support import Qualification


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024): h.update(block)
    return h.hexdigest()


def check(c):
    root = Path(os.environ.get('MODE_STATE_LEAN_ROOT', str(ROOT.parent/'MeTTapedia/lean/mettapedia')))
    models = root.parent/'pettaclaw'
    toolchain = (root/'lean-toolchain').read_text().strip()
    folder = toolchain.replace('/','--').replace(':','---')
    binary = Path(os.environ.get('MODE_STATE_LEAN_BIN', str(Path.home()/'.elan/toolchains'/folder/'bin/lean')))
    assert binary.is_file(), 'matching installed Lean required; no downloads or build fallback'
    names = ('FuelPolicy','LifecycleAuthority','ChannelOperatorControl','RestEnergy','ControlFastPath')
    sources = {name: sha(models/(name+'.lean')) for name in names}
    libraries = sorted((root/'.lake/packages').glob('*/.lake/build/lib/lean'))
    assert libraries and any((p/'Mathlib.olean').is_file() for p in libraries)

    def inventory():
        # These are the only external cache roots provided through LEAN_PATH.
        # No external project build/cache root is supplied or written.
        return {str(p): sha(p) for lib in libraries for p in sorted(lib.rglob('*')) if p.is_file()}

    with c.phase('disposable-lean-models-and-fuel-replay'):
        before = inventory()
        c.save('external-identities-START.json', {'sources': sources, 'cache_files': before,
               'lean': str(binary), 'lean_sha256': sha(binary), 'toolchain': toolchain,
               'scope': 'all files in external LEAN_PATH package-library roots; unrelated project build cache excluded'})
        env = c.environment()
        env['LEAN_PATH'] = os.pathsep.join(map(str,libraries))
        compiled = []
        for name in names:
            source = c.directory/(name+'.lean')
            source.write_bytes((models/(name+'.lean')).read_bytes())
            (c.evidence/source.name).write_bytes(source.read_bytes())
            target = c.directory/(name+'.olean')
            command = [str(binary), '-j1', '-o', str(target), str(source)]
            child = subprocess.run(command, cwd=c.directory, env=env, capture_output=True,
                                   timeout=180, preexec_fn=limit)
            c.record_process('compile-'+name, command, child, env)
            compiled.append({'name':name,'exit':child.returncode,'olean_sha256':sha(target) if target.is_file() else None})
        c.save('compilation-results.json', compiled)
        fuel = (models/'FuelPolicy.lean').read_text()
        replay = c.directory/'Replay.lean'
        held = [0,1,2,3,4,49,50,51,199,200,201,1000]
        grants = [0,1,2,3,50,51]
        replay.write_text(fuel + '''
open FuelPolicy
-- Runtime ceiling correspondence wrapper: the source model has ceiling
-- theorems, not a function named ceiling. This wrapper is explicitly added.
def runtimeCeiling : Fuel → Nat → Nat
 | .accumulate, _ => 0
 | .unlimited, _ => 0
 | .carry, grant => carryMultiple * grant
 | .decay, grant => carryMultiple * grant
 | .saturate, grant => grant
def main : IO Unit := do
 for (name, f) in [("saturate", Fuel.saturate), ("accumulate", Fuel.accumulate),
                   ("carry", Fuel.carry), ("decay", Fuel.decay), ("unlimited", Fuel.unlimited)] do
  for held in ''' + str(held) + ''' do
   for grant in ''' + str(grants) + ''' do
    IO.println s!"REPLAY,{name},{held},{grant},{refuel f held grant},{spend f held},{runtimeCeiling f grant}"
''')
        (c.evidence/replay.name).write_bytes(replay.read_bytes())
        command = [str(binary), '-j1', '--run', str(replay)]
        child = subprocess.run(command, cwd=c.directory, env=env, capture_output=True,
                               timeout=180, preexec_fn=limit)
        c.record_process('fuel-lean-replay', command, child, env)
        assert child.returncode == 0, child.stdout + child.stderr
        expected = []
        for line in child.stdout.decode().splitlines():
            if line.startswith('REPLAY,'):
                _, name, *numbers = line.split(',')
                expected.append([name, *map(int,numbers)])
        assert len(expected) == 5*len(held)*len(grants)
        actual = []
        for name in ('saturate','accumulate','carry','decay','unlimited'):
            (c.directory/'fuel-mode.json').write_text(json.dumps({'fuel':name}))
            source = IMPORTS + '!(import! &self ./src/fuel_cetta.metta)\n'
            for h in held:
                for g in grants:
                    source += emit('(JsonArray (%s %d %d (fuel:refuel %d %d) (fuel:spend %d) (fuel:ceiling %d)))'
                                   % (q(name), h,g,h,g,h,g))
            actual.extend(rows(c.run('fuel-native-grid-'+name, source)))
        assert actual == expected
        after = inventory()
        assert before == after, 'external dependency cache identities changed; no repair/rebuild permitted'
        assert sources == {name:sha(models/(name+'.lean')) for name in names}
        c.save('external-identities-END.json', {'sources':sources, 'cache_files':after,
                                               'lean_sha256':sha(binary), 'external_build_invoked':False})
        c.save('result.json', {'compilations':compiled,'native_replay_rows':len(actual),'actual':actual,
               'all_compilations_passed':all(x['exit']==0 for x in compiled),
               'ceiling': 'explicit correspondence wrapper, not an existing model function',
               'lifecycle': 'runtime latch-only; watcher model does not refine current runtime',
               'RestEnergy': 'stale autonomy_paused mapping; no oracle/model source changed'})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--evidence', type=Path, required=True)
    args = p.parse_args()
    limit()
    args.evidence.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='mode06-lean-') as directory:
        c = Qualification(args.evidence.resolve(),Path(directory))
        c.snapshot_sources('tests/mode_state_lean_check.py','src/fuel_cetta.metta')
        check(c)


if __name__ == '__main__': main()
