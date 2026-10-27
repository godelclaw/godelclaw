"""C2/C3 enforcement witnesses; generated clone/compiler fixtures, not runtime."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from effect_receipts_support import ROOT, limit
from mode_state_checkpoint_check import source_names
from mode_state_lean_check import compile_model, external_identities, sha
from mode_state_support import Qualification


def child_environment(c):
    env=c.environment()
    for key in ('MODE_STATE_ENGINE_SHA256','MODE_STATE_MANIFEST_SHA256'):
        if key in os.environ:env[key]=os.environ[key]
    return env


def run(c,label,command,env,cwd=ROOT):
    child=subprocess.run(list(map(str,command)),cwd=cwd,env=env,capture_output=True,
                         timeout=120,preexec_fn=limit)
    c.record_process(label,command,child,env,cwd=cwd)
    return child


def baseline(c):
    with c.phase('immutable-baseline-pin-enforcement-and-fresh-clone'):
        script=ROOT/'tests/mode_state_handler_errors_check.py'
        rejects=[('unset',None),('empty',''),('moving-ref','main'),('abbreviated','1234567'),
                 ('invalid','z'*40),('unresolvable','0'*40)]
        outcomes={}
        for label,pin in rejects:
            env=child_environment(c)
            if pin is None:env.pop('MODE_STATE_BASELINE_COMMIT',None)
            else:env['MODE_STATE_BASELINE_COMMIT']=pin
            target=c.evidence/('rejected-'+label)
            child=run(c,'reject-'+label,[sys.executable,script,'--evidence',target],env)
            assert child.returncode==1 and b'MODE_STATE_BASELINE_COMMIT' in child.stderr,(label,child.stderr)
            for edge in ('START','PRE-END-RESOLUTION','PRE-FINAL-DESCRIPTOR','END'):
                assert (target/('actual-handler-native-error-transport-'+edge+'.json')).is_file(),(label,edge)
            assert not (target/'baseline-handler-source.json').exists()
            outcomes[label]=child.returncode
        env=child_environment(c);clone=c.directory/'fresh-clone'
        command=['git','clone','--no-local','--single-branch','--branch','port/06-mode-state',str(ROOT),str(clone)]
        child=run(c,'clone-local-checkout',command,env)
        assert child.returncode==0,child.stderr
        child=run(c,'no-local-main',['git','show-ref','--verify','--quiet','refs/heads/main'],env,cwd=clone)
        assert child.returncode==1,child.stdout
        # Clone identity is recorded separately from the pending source overlay.
        # This exercises final authored source before its scoped commit; it
        # does not claim the clone's old HEAD contains that overlay.
        child=run(c,'clone-head',['git','rev-parse','HEAD'],env,cwd=clone);assert child.returncode==0
        copied={}
        for name in source_names():
            source=ROOT/name;target=clone/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,target);copied[name]=sha(source)
        c.save('clone-source-overlay.json',{'clone_head':child.stdout.decode().strip(),'files':copied,
               'scope':'exact current authored sources over disposable clone; no local main, no canonical/ref edits'})
        target=c.evidence/'clone-handler-errors'
        child=run(c,'actual-handler-test-without-main',
                  [sys.executable,clone/'tests/mode_state_handler_errors_check.py','--evidence',target],env,cwd=clone)
        assert child.returncode==0,child.stderr
        observed=json.loads((target/'baseline-source.json').read_text())
        assert observed['requested']==observed['resolved']==os.environ['MODE_STATE_BASELINE_COMMIT']
        c.save('baseline-enforcement-result.json',{'status':'PASS_BASELINE_PIN_ENFORCEMENT',
               'rejected_actual_exits':outcomes,'fresh_clone_handler_exit':child.returncode,'baseline':observed,
               'local_main_absent':True})


def compiler(c):
    names=('FuelPolicy','LifecycleAuthority','ChannelOperatorControl','RestEnergy','ControlFastPath')
    results=[]
    for failure_index,name in enumerate(names):
        directory=c.directory/('compiler-fixture-%d'%failure_index);directory.mkdir()
        evidence=c.evidence/('compiler-fixture-%d'%failure_index);evidence.mkdir()
        sub=Qualification(evidence,directory)
        models=directory/'models';models.mkdir()
        for model in names:(models/(model+'.lean')).write_text('-- generated failure-enforcement fixture, not a proof\n')
        tool=directory/'injected-compiler'
        tool.write_text('#!'+sys.executable+'\n'+'''import os,sys
from pathlib import Path
name=Path(sys.argv[-1]).stem
print('FIXTURE compile '+name)
if name==os.environ['MODE_STATE_INJECTED_MODEL']:
    print('FIXTURE deliberate compiler exit19',file=sys.stderr);sys.exit(19)
Path(sys.argv[sys.argv.index('-o')+1]).write_bytes(b'fixture, not Lean output')
''')
        tool.chmod(0o700)
        cache=directory/'read-only-fixture-cache';cache.write_text('unchanged fixture cache\n')
        inventory=lambda:{str(cache):sha(cache)}
        sources={model:sha(models/(model+'.lean')) for model in names}
        env=sub.environment();env['MODE_STATE_INJECTED_MODEL']=name
        compiled=[];replay_reached=False
        try:
            with sub.phase('injected-compiler-failure'),external_identities(sub,inventory,sources,models,tool,'TEST FIXTURE, not external Lean'):
                for model in names:
                    compile_model(sub,tool,models/(model+'.lean'),directory/(model+'.olean'),env,model,compiled)
                replay_reached=True
        except AssertionError as exc:
            assert exc.args and exc.args[0]==('exact model compilation failed',name,19),exc
        else:raise AssertionError('a failed model compilation was accepted')
        assert not replay_reached and len(compiled)==failure_index+1,compiled
        assert compiled[-1]['exit']==19 and all(row['exit']==0 for row in compiled[:-1]),compiled
        assert json.loads((evidence/'external-identities-START.json').read_text())['cache_files']==json.loads((evidence/'external-identities-END.json').read_text())['cache_files']
        for edge in ('START','PRE-END-RESOLUTION','PRE-FINAL-DESCRIPTOR','END'):
            assert (evidence/('injected-compiler-failure-'+edge+'.json')).is_file(),edge
        results.append({'failed_model':name,'attempted_models':[x['name'] for x in compiled],
                        'actual_failure_exit':19,'later_compilation_or_replay_reached':replay_reached})
    c.save('compiler-enforcement-result.json',{'status':'PASS_EVERY_MODEL_FAILURE_ENFORCEMENT','cases':results,
           'fixture':'five generated compiler failure subprocesses, not five failed real Lean compilations; no external model/cache/toolchain touched'})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence',type=Path,required=True)
    p.add_argument('--phase',choices=('baseline','compiler'),required=True)
    args=p.parse_args();limit();args.evidence.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='mode06-corrections-') as directory:
        c=Qualification(args.evidence.resolve(),Path(directory))
        c.snapshot_sources('tests/mode_state_corrections_check.py','tests/mode_state_handler_errors_check.py',
                           'tests/mode_state_lean_check.py','tests/mode_state_support.py')
        (baseline if args.phase=='baseline' else compiler)(c)


if __name__=='__main__':main()
