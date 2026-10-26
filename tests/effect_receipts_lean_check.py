"""Disposable exact model compilation/replay and native broker observation check."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from effect_receipts_support import IMPORTS,Qualification,ROOT,emit,limit,rows


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def check(c):
    lean_root=Path(os.environ.get('EFFECT_RECEIPTS_LEAN_ROOT',str(ROOT.parent/'MeTTapedia/lean/mettapedia')))
    external=lean_root.parent.parent
    names=['lean/pettaclaw/EffectReceiptProjection.lean','lean/pettaclaw/EffectBrokerNoninterference.lean',
           'lean/mettapedia/Mettapedia/CogArch/Agent/EffectReceiptProjection.lean',
           'lean/mettapedia/Mettapedia/CogArch/Agent/EffectBrokerNoninterference.lean']
    before={name:sha(external/name) for name in names}
    toolchain=(lean_root/'lean-toolchain').read_text().strip()
    folder=toolchain.replace('/','--').replace(':','---')
    binary=Path(os.environ.get('EFFECT_RECEIPTS_LEAN_BIN',str(Path.home()/'.elan/toolchains'/folder/'bin/lean')))
    assert binary.is_file(),'installed matching Lean required; no downloader/build fallback'
    libraries=[lean_root/'.lake/build/lib/lean',*sorted((lean_root/'.lake/packages').glob('*/.lake/build/lib/lean'))]
    imported=lean_root/'.lake/packages/mathlib/.lake/build/lib/lean/Mathlib.olean'
    import_before=sha(imported)
    c.save('external-identities-START.json',{'sources':before,'mathlib_olean':import_before,
        'lean_binary':str(binary),'lean_sha256':sha(binary),'toolchain':toolchain})
    env=c.environment();env['LEAN_PATH']=os.pathsep.join(str(p) for p in libraries if p.is_dir())
    outputs=[]
    for i,name in enumerate(names):
        source=c.directory/('Model%d.lean'%i);source.write_bytes((external/name).read_bytes())
        (c.evidence/source.name).write_bytes(source.read_bytes())
        command=[str(binary),'-j1','-o',str(c.directory/('Model%d.olean'%i)),str(source)]
        child=subprocess.run(command,cwd=c.directory,env=env,capture_output=True,timeout=180)
        c.record_process('lean-compile-%d'%i,command,child,env)
        assert child.returncode==0,child.stdout.decode(errors='backslashreplace')+child.stderr.decode(errors='backslashreplace')
        outputs.append(sha(c.directory/('Model%d.olean'%i)))
        c.save('compiled-models.json',outputs)
    replay_specs=[(2,'Mettapedia.CogArch.Agent.EffectReceiptProjection',
        's!"[{decide (Completed successful)}, {decide (Completed withheld)}, {decide (Completed returnedFailure)}]"','[true, false, false]'),
        (3,'Mettapedia.CogArch.Agent.EffectBrokerNoninterference',
        's!"[{(repairedStep false 1 .commandsParsed ⟨[]⟩).effects.length}, {(repairedStep true 1 .commandsParsed ⟨[]⟩).effects.length}]"','[0, 1]')]
    for i,namespace,expression,expected in replay_specs:
        source=c.directory/('Replay%d.lean'%i)
        instance=('\ninstance (h : History) : Decidable (Completed h) := by\n  unfold Completed\n  infer_instance\n'
                  if i==2 else '')
        source.write_text((external/names[i]).read_text()+'\nopen '+namespace+instance+'\ndef main : IO Unit := do\n  IO.println ('+expression+')\n')
        (c.evidence/source.name).write_bytes(source.read_bytes())
        command=[str(binary),'-j1','--run',str(source)]
        child=subprocess.run(command,cwd=c.directory,env=env,capture_output=True,timeout=180)
        c.record_process('lean-replay-%d'%i,command,child,env)
        assert child.returncode==0 and expected in child.stdout.decode(),child.stdout+child.stderr
    # Actual dispatcher + observer: the withheld command is callable data, not
    # merely an unrecognized symbol. Any extra observer evaluation increments.
    root=c.directory/'public-work';root.mkdir();(root/'state.json').write_text('{"enabled":true,"seq":0,"epoch":7}')
    source=IMPORTS+'!(import! &self ./tests/effect_receipts_caller_probe.py)\n!(py-call (effect_receipts_caller_probe.setup))\n'
    source+='!(import! &self ./src/api_registry.metta)\n!(import! &self ./src/command_dispatch.metta)\n'
    source+='''
(CommandSpec 1 "replay" "shell" 1 1 "(shell fixture)" "generated harmless counter" "")
!(bind! &rp-effects (new-state 0))
(= (shell $argument)
 (let $_ (change-state! &rp-effects (+ (get-state &rp-effects) 1)) "failure evidence"))
!(py-call (effect_receipts_caller_probe.stopped 1))
!(run-command-batch-once 1 5 (quote ((shell "private-withheld-fixture"))))
'''
    source+=emit('(get-state &rp-effects)')
    source+='!(py-call (effect_receipts_caller_probe.stopped 0))\n!(run-command-batch-once 2 5 (quote ((shell "private-admitted-fixture"))))\n'
    source+=emit('(get-state &rp-effects)')
    actual=rows(c.run('native-model-replay',source,
        {'METTACLAW_PUBLIC_TOOL_RECEIPTS':'1','EFFECT_TEST_STREAM_COUNTER_ONLY':'1'}))
    notes=[json.loads(line) for line in (root/'notes.jsonl').read_bytes().splitlines()]
    c.save('native-model-replay.json',{'effect_counts':actual,'notes':notes,
        'receipt_hex':(c.directory/'receipts.jsonl').read_bytes().hex(),
        'refinement':'withholding observer noninterference; metadata disposition is not a success classifier'})
    assert actual==[0,1],actual
    assert 'The command did not run.' in notes[0]['text']
    assert 'not a success verdict' in notes[1]['text']
    assert all('private-' not in note['text'] and 'failure evidence' not in note['text'] for note in notes)
    after={name:sha(external/name) for name in names};assert before==after
    assert sha(imported)==import_before
    c.save('external-identities.json',{'sources_before':before,'sources_after':after,
        'lean_binary':str(binary),'lean_sha256':sha(binary),'toolchain':toolchain,
        'read_only_mathlib_olean_sha256':import_before,'disposable_olean_hashes':outputs,
        'external_build_invoked':False,'output_directory':str(c.directory),
        'limit':'direct Lean reads existing dependencies; all compilation/replay inputs and outputs disposable, no lake/build/cache operations'})
    c.save('result.json',{'exact_source_compilations':4,'executable_model_replays':2,'native_counter_replay':[0,1],
        'not_proved':['digest secrecy','filesystem authority','durability','multi-file atomicity']})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence',type=Path,required=True)
    a=p.parse_args();limit();assert sys.version_info[:3]==(3,12,13)
    a.evidence.mkdir(parents=True,exist_ok=False);(a.evidence/'runner.py').write_bytes(Path(__file__).read_bytes())
    with tempfile.TemporaryDirectory(prefix='effect05-lean-') as temporary:
        c=Qualification(a.evidence.resolve(),Path(temporary))
        with c.phase('lean-alignment-replay'):
            c.snapshot_sources('src/command_dispatch_core.metta','src/command_dispatch_hooks.metta',
                'src/effect_receipts_cetta.metta','src/effect_receipt_values_cetta.metta',
                'src/effect_receipt_io_cetta.metta','tests/effect_receipts_caller_probe.py')
            try:
                check(c)
            finally:
                start=c.evidence/'external-identities-START.json'
                if start.exists():
                    before=json.loads(start.read_text())
                    root=Path(os.environ.get('EFFECT_RECEIPTS_LEAN_ROOT',str(ROOT.parent/'MeTTapedia/lean/mettapedia')))
                    after={name:sha(root.parent.parent/name) for name in before['sources']}
                    imported=root/'.lake/packages/mathlib/.lake/build/lib/lean/Mathlib.olean'
                    c.save('external-identities-END.json',{'sources':after,'mathlib_olean':sha(imported)})
                    assert after==before['sources'] and sha(imported)==before['mathlib_olean']


if __name__=='__main__':main()
