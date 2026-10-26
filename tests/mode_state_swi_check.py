"""Actual SWI facade sequences with counted Python owners and shared native reads."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from effect_receipts_support import IMPORTS, emit, limit, q, rows
from mode_state_callers_check import stage
from mode_state_mixed_check import settings
from mode_state_modules_check import poison
from mode_state_native_check import SNAPSHOT, python, umask
from mode_state_support import Qualification


FIXTURE = '''import json,os,time
from pathlib import Path
import engine_modes,loop_modes,lifecycle,attention,fuel_modes
''' + SNAPSHOT + '''
time.time=lambda:1790000000.123456
counts={};observations=[]
for module,names in ((engine_modes,('active_engine','selected_engine','engine_view','set_engine','engine_command','request_recycle')),
                     (loop_modes,('process_mode','mode_view','modes_view','set_mode','wait_timed_out')),
                     (lifecycle,('cognition_enabled','view','start','stop')),
                     (attention,('description',)),
                     (fuel_modes,('current_fuel','set_fuel','refuel','ceiling','spend'))):
    for name in names:
        original=getattr(module,name);key=module.__name__+'.'+name
        def counted(*args,original=original,key=key,**kwargs):
            counts[key]=counts.get(key,0)+1
            return original(*args,**kwargs)
        setattr(module,name,counted)
def observe(label,value):
    observations.append({'label':str(label),'value':value,'files':snapshot(os.environ['MODE_STATE_SWI_TREE'])})
    Path(os.environ['MODE_STATE_SWI_OBSERVATIONS']).write_text(json.dumps({'observations':observations,'counts':counts},ensure_ascii=True))
    return 1
'''


def check(c,swipl,swi_root):
    with c.phase('actual-swi-mode-lifecycle-fuel-facades'),umask(0o027):
        package=stage(c,'swi-package')
        fixture=c.directory/'mode_swi_sequence.py';fixture.write_text(FIXTURE)
        (c.evidence/fixture.name).write_bytes(fixture.read_bytes())
        root=c.directory/'state';root.mkdir()
        output=c.directory/'swi-observations.json'
        env=c.environment();env.update(settings(root),METTACLAW_ENGINE='petta',METTACLAW_ACTIVE_ENGINE='petta',
            PETTA_ROOT=str(swi_root),MODE_STATE_SWI_TREE=str(root),MODE_STATE_SWI_OBSERVATIONS=str(output),
            PYTHONPATH=os.pathsep.join(map(str,(c.directory,package/'src',package/'channels'))))
        operations=[
            ('engine-view','(engine:view)','engine_modes.engine_view()'),
            ('engine-set','(engine:set "cetta")','engine_modes.set_engine("cetta")'),
            ('engine-selected','(engine:selected)','engine_modes.selected_engine()'),
            ('engine-command','(engine:command "swi")','engine_modes.engine_command("swi")'),
            ('recycle','(repr (engine:recycle))','repr(engine_modes.request_recycle())'),
            ('mode-view','(mode:view)','loop_modes.mode_view()'),
            ('mode-set','(mode:set "coding")','loop_modes.set_mode("coding")'),
            ('mode-list','(mode:list-view)','loop_modes.modes_view()'),
            ('mode-process','(mode:process)','loop_modes.process_mode()'),
            ('mode-wait','(mode:wait-timed-out "rested 5s")','loop_modes.wait_timed_out("rested 5s")'),
            ('lifecycle-view','(lifecycle:view)','lifecycle.view()'),
            ('lifecycle-start','(lifecycle:start)','lifecycle.start()'),
            ('lifecycle-enabled','(lifecycle:enabled)','lifecycle.cognition_enabled()'),
            ('lifecycle-stop','(lifecycle:stop)','lifecycle.stop()'),
            ('attention','(attention:description)','attention.description()'),
            ('fuel-set','(fuel:set "carry")','fuel_modes.set_fuel("carry")'),
            ('fuel-refuel','(fuel:refuel 20 50)','fuel_modes.refuel(20,50)'),
            ('fuel-ceiling','(fuel:ceiling 50)','fuel_modes.ceiling(50)'),
            ('fuel-spend','(fuel:spend 20)','fuel_modes.spend(20)')]
        source='!(import! &self (library lib_import))\n!(import! &self %s)\n'%fixture
        source+='!(import! &self ./src/mode_state.metta)\n!(import! &self ./src/mode_state.metta)\n!(import! &self ./src/engine_modes.metta)\n!(import! &self ./src/fuel_io.metta)\n'
        for label,expression,_ in operations:
            source+='!(py-call (mode_swi_sequence.observe %s %s))\n'%(json.dumps(label),expression)
        source+='!(println! (ModeOwners (collapse (match &self (mode-state:loaded) True))))\n'
        source+='!(println! (EngineOwners (collapse (match &self (engine-modes:loaded) True))))\n'
        program=package/'swi-probe.metta';program.write_text(source)
        (c.evidence/program.name).write_bytes(program.read_bytes())
        command=[str(swipl),'--stack_limit=1g','-q','-s',str(swi_root/'src/main.pl'),'--',str(program)]
        child=subprocess.run(command,cwd=package,env=env,capture_output=True,timeout=90,preexec_fn=limit)
        c.record_process('actual-swi-sequence',command,child,env,cwd=package)
        assert child.returncode==0 and output.is_file(),child.stdout+child.stderr
        assert b'(ModeOwners (true))' in child.stdout and b'(EngineOwners (true))' in child.stdout
        actual=json.loads(output.read_text());(c.evidence/output.name).write_bytes(output.read_bytes())
        final_files=actual['observations'][-1]['files']
        for p in root.iterdir():p.unlink()
        reference=c.directory/'oracle-observations.json'
        expected=python(c,'python-swi-reference', '''import json,mode_swi_sequence as f
import engine_modes,loop_modes,lifecycle,attention,fuel_modes
for label,expr in '''+repr([(label,oracle) for label,_,oracle in operations])+''':
    f.observe(label,eval(expr))
print(json.dumps({'observations':f.observations,'counts':f.counts},ensure_ascii=True))
''',{**settings(root),'METTACLAW_ENGINE':'petta','METTACLAW_ACTIVE_ENGINE':'petta','PETTA_ROOT':str(swi_root),
      'MODE_STATE_SWI_TREE':str(root),'MODE_STATE_SWI_OBSERVATIONS':str(reference),
      'PYTHONPATH':str(c.directory)+os.pathsep+c.environment()['PYTHONPATH']})
        # fuel_io selects via active_engine once before the observed operations.
        expected['counts']['engine_modes.active_engine']+=1
        # Actual Janus preserves Python True as (@ true), NOT MeTTa Bool True.
        # Assert that wire explicitly; compare the other domain via a declared
        # projection, never change the runtime or oracle to force Bool truth.
        recycle=next(i for i,row in enumerate(actual['observations']) if row['label']=='recycle')
        assert actual['observations'][recycle]['value']=='(@ true)'
        assert expected['observations'][recycle]['value']=='True'
        projected=json.loads(json.dumps(actual))
        projected['observations'][recycle]['value']='True'
        c.save('swi-comparison.json',{'actual':actual,'expected':expected,'projected':projected,
               'projection':'only asserted Janus (@ true) repr to Python True repr; raw result retained'})
        assert projected==expected,(actual,expected)
        assert all(actual['counts'].get(key,0)>0 for key in (
            'engine_modes.set_engine','loop_modes.set_mode','lifecycle.start','lifecycle.stop','attention.description'))
        fixture=poison(c)
        native=IMPORTS+fixture+'!(import! &self ./src/mode_state.metta)\n!(import! &self ./src/fuel_cetta.metta)\n'
        observed=rows(c.run('native-reads-swi-files',native+''.join(map(emit,
             ['(engine:selected)','(loop-mode:current)','(lifecycle:enabled)','(fuel:current)'])),settings(root)))
        assert observed==['cetta','coding',0,'carry'],observed
        namespace={};exec(SNAPSHOT,namespace)
        assert namespace['snapshot'](root)==final_files
        c.save('result.json',{'status':'PASS_ACTUAL_SWI_FACADES','steps':len(operations),'counts':actual['counts'],
               'single_owners':True,'native_reads_swi_files':observed,'final_files':final_files,
               'boolean_crossing':'recycle observed through textual repr; SWI Python bool is not assumed to be a MeTTa Bool; runtime unchanged'})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence',type=Path,required=True)
    p.add_argument('--swipl',type=Path,required=True);p.add_argument('--swi-root',type=Path,required=True)
    args=p.parse_args();limit();args.evidence.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='mode06-swi-') as directory:
        c=Qualification(args.evidence.resolve(),Path(directory));c.snapshot_sources('tests/mode_state_swi_check.py','src/mode_state.metta','src/mode_state_python.metta')
        check(c,args.swipl,args.swi_root)


if __name__=='__main__':main()
