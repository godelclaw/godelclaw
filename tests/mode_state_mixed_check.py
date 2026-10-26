"""Alternating real Python/native writers and readers, exact every-step trees."""
import argparse
import json
import os
from pathlib import Path
import tempfile

from effect_receipts_support import IMPORTS, emit, limit, q, rows
from mode_state_native_check import CAPTURE, SNAPSHOT, captured, python, umask
from mode_state_modules_check import poison
from mode_state_support import Qualification


READS = '''import engine_modes, loop_modes, lifecycle, fuel_modes
def capture(call):
    try:return call()
    except Exception as exc:return {'error':type(exc).__name__,'message':str(exc)}
def reads():
    return [capture(engine_modes.selected_engine),capture(engine_modes.engine_view),
            capture(loop_modes.current_mode),capture(loop_modes.mode_view),capture(loop_modes.modes_view),
            capture(loop_modes.process_mode),capture(lifecycle.cognition_enabled),capture(lifecycle.view),
            capture(fuel_modes.current_fuel),capture(fuel_modes.fuel_view),
            capture(engine_modes.recycle_requested)]
'''


def settings(root):
    return {'METTACLAW_'+key:str(root/name) for key,name in (
        ('ENGINE_STATE_PATH','engine'),('ENGINE_FAILURE_PATH','failure'),('LOOP_MODE_PATH','loop'),
        ('LIFECYCLE_PATH','lifecycle'),('FUEL_MODE_PATH','fuel'),('RECYCLE_REQUEST_PATH','recycle'),
        ('WAKE_REQUEST_PATH','wake'))}


def check(c):
    actual_root=c.directory/'mixed';reference=c.directory/'reference'
    for root in (actual_root,reference):
        root.mkdir()
        (root/'engine').write_bytes(b'unknown\r')
        (root/'loop').write_bytes(b'{"mode":"coding","autonomy_paused":true,"extra":3}')
        (root/'loop.tmp').write_bytes(b'stale\n');(root/'loop.tmp').chmod(0o604)
        (root/'fuel').write_bytes(b'{"fuel":"decay","extra":3}')
        (root/'fuel.tmp').write_bytes(b'stale\n');(root/'fuel.tmp').chmod(0o604)
        (root/'lifecycle').write_bytes(b'{"schema":1,"state":"running","extra":0}')
        (root/'failure').write_bytes(b'engine=cetta\rreason=fixture\rstatus=3\r')
    fixture=poison(c)
    native_reads=['(engine:selected)','(engine:view)','(loop-mode:current)','(mode:view)','(mode:list-view)',
                  '(str:symbol->string (mode:process))','(lifecycle:enabled)','(lifecycle:view)',
                  '(fuel:current)','(fuel:view)']
    operations=[
        ('native','(engine:set "swi")','engine_modes.set_engine("swi")'),
        ('python',None,'engine_modes.set_engine("cetta")'),
        ('native','(mode:set "coding")','loop_modes.set_mode("coding")'),
        ('python',None,'loop_modes.set_mode("agent")'),
        ('native','(lifecycle:start)','lifecycle.start()'),
        ('python',None,'lifecycle.stop()'),
        ('native','(fuel:set "carry")','fuel_modes.set_fuel("carry")'),
        ('python',None,'fuel_modes.set_fuel("unlimited")'),
        ('python',None,'loop_modes.record_active("İ",0,"generated-state")'),
        ('native','(mode:set "iter-coding")','loop_modes.set_mode("iter-coding")'),
        ('python',None,'loop_modes.record_active("coding",2147483647,"generated-state")'),
        ('native','(engine:set "pleatta")','engine_modes.set_engine("pleatta")'),
        ('python',None,'fuel_modes.set_fuel("decay")'),
        ('native','(lifecycle:stop)','lifecycle.stop()')]
    # Existing safe SWI executable fixture is never executed by this gate.
    petta=c.directory/'petta';petta.mkdir()
    (petta/'run.sh').write_text('harmless fixture, never executed\n');(petta/'run.sh').chmod(0o700)
    shared={'PETTA_ROOT':str(petta)}
    records=[]
    with c.phase('two-way-mixed-mode-writers-readers'),umask(0o027):
        for index,(route,native,oracle) in enumerate(operations):
            env={**shared,**settings(actual_root)}
            expected=python(c,'reference-step-%02d'%index,'''import json,time,io,contextlib
time.time=lambda:1790000000.123456
'''+READS+SNAPSHOT+'''
stream=io.StringIO()
with contextlib.redirect_stdout(stream):reply=capture(lambda:eval('''+repr(oracle)+'''))
print(json.dumps({'reply':reply,'printed':stream.getvalue().splitlines(),'reads':reads(),
                  'files':snapshot('''+repr(str(reference))+''')},ensure_ascii=True))
''',{**shared,**settings(reference)})
            if route=='python':
                written=python(c,'python-writer-%02d'%index,'''import json,time,io,contextlib
time.time=lambda:1790000000.123456
'''+READS+'''
stream=io.StringIO()
with contextlib.redirect_stdout(stream):reply=capture(lambda:eval('''+repr(oracle)+'''))
print(json.dumps({'reply':reply,'printed':stream.getvalue().splitlines()},ensure_ascii=True))
''',env)
            else:
                source=IMPORTS+fixture+'!(import! &self ./src/mode_state.metta)\n!(import! &self ./src/fuel_cetta.metta)\n'+CAPTURE
                source+='!(match &self (= (system:wall-time) $prior) (remove-atom &self (= (system:wall-time) $prior)))\n(= (system:wall-time) 1790000000.123456)\n'
                output=c.run('native-writer-%02d'%index,source+emit(captured(native)),env)
                replies=rows(output);assert len(replies)==1,output
                written={'reply':replies[0],'printed':[line for line in output.splitlines() if line.startswith(('[engine-mode]','[loop-mode]','[fuel-mode]'))]}
            read=python(c,'retained-readers-%02d'%index,'import json\n'+READS+SNAPSHOT+
                        '\nprint(json.dumps({"reads":reads(),"files":snapshot('+repr(str(actual_root))+')},ensure_ascii=True))\n',env)
            before=read['files']
            source=IMPORTS+fixture+'!(import! &self ./src/mode_state.metta)\n!(import! &self ./src/fuel_cetta.metta)\n'+CAPTURE
            observed=rows(c.run('native-readers-%02d'%index,source+''.join(emit(captured(e)) for e in native_reads),env))
            after=python(c,'post-native-read-tree-%02d'%index,'import json\n'+SNAPSHOT+'\nprint(json.dumps(snapshot('+repr(str(actual_root))+'),ensure_ascii=True))\n',env)
            pair={'index':index,'writer':route,'operation':oracle,'written':written,'native_reads':observed,
                  'retained_reads':read,'expected':expected,'after_native_reads':after}
            c.save('step-%02d.json'%index,pair)
            assert written=={k:expected[k] for k in ('reply','printed')},pair
            assert observed==expected['reads'][:-1] and read['reads']==expected['reads'],pair
            assert before==after==expected['files'],pair
            records.append(pair)
        c.save('result.json',{'status':'PASS_MIXED_WRITERS_READERS','steps':records,
               'retained_writer':'actual loop_modes.record_active; actual Python engine/loop/fuel/lifecycle writers',
               'retained_reader':'actual Python selection/view and recycle_requested functions used by launcher/runtime_host/channel',
               'native_python':'ported functions poisoned; only test fixture import, no retained native control calls',
               'clock':'explicit fixed fixture for cross-process byte comparisons; wall-time API separately bracketed'})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence',type=Path,required=True)
    args=p.parse_args();limit();args.evidence.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='mode06-mixed-') as directory:
        c=Qualification(args.evidence.resolve(),Path(directory))
        c.snapshot_sources('tests/mode_state_mixed_check.py','src/loop_modes.py','src/engine_modes.py','src/lifecycle.py','src/fuel_modes.py')
        check(c)


if __name__=='__main__':main()
