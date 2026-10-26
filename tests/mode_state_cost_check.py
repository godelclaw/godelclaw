"""Cold application-state costs at real callers, not a full provider turn."""
import argparse
import json
import os
from pathlib import Path
import statistics
import tempfile
import time

from effect_receipts_support import IMPORTS, ROOT, emit, limit, rows
from mode_state_callers_check import CONTROL, stage
from mode_state_support import Qualification


FIXTURE = CONTROL + '''
import builtins, io, native_llm_host, strategy_host
counts.update(read_open=0,llm_selection=0,context_input=0)
real_open=builtins.open
real_io_open=io.open
def opened(path,*args,**kwargs):
    if str(path).startswith(os.environ['MODE_STATE_COST_ROOT']) and (not args or 'r' in args[0]):
        counts['read_open']+=1
    return real_open(path,*args,**kwargs)
builtins.open=opened
def io_opened(path,*args,**kwargs):
    if str(path).startswith(os.environ['MODE_STATE_COST_ROOT']) and (not args or 'r' in args[0]):
        counts['read_open']+=1
    return real_io_open(path,*args,**kwargs)
io.open=io_opened
def no_provider():
    counts['llm_selection']+=1;return 0
def context_input(name,*args):
    assert name=='enabled';counts['context_input']+=1;return 0
native_llm_host.enabled=no_provider
strategy_host.context_input=context_input
def io_snapshot():
    with real_open('/proc/self/io') as stream:
        return json.dumps({k:int(v) for k,v in (line.split(':') for line in stream)})
'''


def check(c, samples):
    with c.phase('cold-real-switch-callers-gate-and-renewal'):
        package=stage(c,'cost-package')
        fixture=c.directory/'cost_fixture.py';fixture.write_text(FIXTURE)
        (c.evidence/fixture.name).write_bytes(fixture.read_bytes())
        all_samples={lane:[] for lane in ('python','native')}
        for number in range(samples):
            for lane in all_samples:
                root=c.directory/('%s-%02d'%(lane,number));root.mkdir()
                files={'lifecycle.json':'{"schema": 1, "state": "running"}\n',
                       'fuel-mode.json':'{\n  "fuel": "decay"\n}\n',
                       'loop-mode.json':'{\n  "mode": "coding"\n}\n',
                       'pins.metta':'Turn context fixture: finish the isolated proof replay.\n', 'engine-state':'cetta\n'}
                advice=[]
                for name,raw in files.items():
                    path=root/name
                    with path.open('w') as stream:
                        stream.write(raw);stream.flush();os.fsync(stream.fileno())
                        if hasattr(os,'posix_fadvise'):
                            os.posix_fadvise(stream.fileno(),0,0,os.POSIX_FADV_DONTNEED);advice.append(name)
                env={'MODE_STATE_POISON':'1' if lane=='native' else '0','MODE_STATE_COST_ROOT':str(root),
                     'PYTHONPATH':os.pathsep.join(map(str,(c.directory,package/'src',package/'channels',ROOT/'tests'))),
                     'METTACLAW_ACTIVE_LOOP_MODE':'coding',
                     **{'METTACLAW_'+key:str(root/file) for key,file in (
                         ('LIFECYCLE_PATH','lifecycle.json'),('FUEL_MODE_PATH','fuel-mode.json'),
                         ('LOOP_MODE_PATH','loop-mode.json'),('PINS_PATH','pins.metta'),('ENGINE_STATE_PATH','engine-state'))}}
                source=IMPORTS+'!(import! &self %s)\n'%fixture
                source+='!(import! &self ./src/mode_state.metta)\n'
                # Python oracle functions on the SAME evaluator, explicitly
                # substituted at the facade for a component-cost comparison.
                # This is not a SWI timing and not a native fallback.
                if lane=='python':
                    for head,body in [('(engine:active)','(py-call (engine_modes.active_engine))'),
                                      ('(mode:process)','(py-call (loop_modes.process_mode))'),
                                      ('(lifecycle:enabled)','(py-call (lifecycle.cognition_enabled))')]:
                        source+='!(match &self (= %s $prior) (remove-atom &self (= %s $prior)))\n(= %s %s)\n'%(head,head,head,body)
                source+='!(import! &self ./src/state_io.metta)\n!(import! &self ./src/mail.metta)\n'
                source+='!(import! &self ./src/turn_additions.metta)\n!(import! &self ./src/open_assemblage.metta)\n'
                source+='!(import! &self ./src/loop_policy.metta)\n!(import! &self ./src/loop.metta)\n'
                if lane=='python':
                    source+='!(match &self (= (fuel:refuel $h $g) $prior) (remove-atom &self (= (fuel:refuel $h $g) $prior)))\n'
                    source+='(= (fuel:refuel $h $g) (py-call (fuel_modes.refuel $h $g)))\n'
                source+='''
!(bind! &cost-active (new-state 0))
!(match &self (= (engine:active) $prior)
 (progn (remove-atom &self (= (engine:active) $prior))
        (add-atom &self (= (cost:active-original) $prior))))
(= (engine:active) (let $_ (change-state! &cost-active (+ (get-state &cost-active) 1)) (cost:active-original)))
!(bind! &cost-opens (new-state 0))
!(match &self (= (fs:open-read $path) $prior) (remove-atom &self (= (fs:open-read $path) $prior)))
(= (fs:open-read $path) (let $_ (change-state! &cost-opens (+ (get-state &cost-opens) 1)) (__cetta_lib_fs_open_read $path)))
!(match &self (= (fs:read-text-result $path $max $errors) $prior) (remove-atom &self (= (fs:read-text-result $path $max $errors) $prior)))
(= (fs:read-text-result $path $max $errors) (let $_ (change-state! &cost-opens (+ (get-state &cost-opens) 1)) (__cetta_lib_fs_read_text_result $path $max $errors)))
!(match &self (= (work:reap) $prior) (remove-atom &self (= (work:reap) $prior)))
(= (work:reap) 0)
!(match &self (= (enabledTurnCandidate $p) $prior) (remove-atom &self (= (enabledTurnCandidate $p) $prior)))
(= (enabledTurnCandidate $p) (candidate $p))
!(match &self (= (sleepInterval) $prior) (remove-atom &self (= (sleepInterval) $prior)))
(= (sleepInterval) 1)
(= (get_time_as_string) "generated-cost-clock")
(= (cost:periphery) (coordinate active-policy coding (coordinate loops 20 (coordinate autonomous-ready 1 coordinates-empty))))
(= (cost:text $v) (if (== (get-type $v) String) $v (str:symbol->string $v)))
'''
                source+=emit('(json:parse-python (cost:text (py-call (cost_fixture.io_snapshot))))')
                expressions=[('active-callers','(progn (pins:view) (addition-pins-views) (addition-reap-work) (state:prepare-context "generated production-shaped prompt" 2048) (mail:cetta?))'),
                             ('gate','(let (candidate $p) (turnCandidate (cost:periphery)) (loop-budget $p))'),
                             ('renewal','(loop-budget (applyAutonomousBurst (cost:periphery) True))')]
                # Extra independent step profiles explain evaluator overhead;
                # they occur AFTER the cold caller measurements.
                expressions += [('profile-lifecycle-path','(mode-path:lifecycle)'),
                                ('profile-lifecycle-read','(lifecycle-native:read)'),
                                ('profile-fuel-load','(fuel:current-result)'),
                                ('profile-budget','(fuel:count 20)'),
                                ('profile-active','(cost:text (engine:active))'),
                                ('profile-text-read','(mio:read (mode-path:lifecycle))'),
                                ('profile-json','(mio:parse "{\\\"schema\\\":1,\\\"state\\\":\\\"running\\\"}")')]
                for label,expression in expressions:
                    source+=emit('(let* (($s (system:monotonic-ns)) ($v %s) ($e (system:monotonic-ns))) (JsonArray ("%s" (- $e $s) $v)))'%(expression,label))
                source+=emit('(JsonArray ((get-state &cost-active) (get-state &cost-opens)))')
                source+=emit('(json:parse-python (cost:text (py-call (cost_fixture.summary))))')
                source+=emit('(json:parse-python (cost:text (py-call (cost_fixture.io_snapshot))))')
                start=time.perf_counter_ns()
                output=c.run('cost-%02d-%s'%(number,lane),source,env,runtime_root=package)
                process_ns=time.perf_counter_ns()-start
                values=rows(output);assert len(values)==len(expressions)+4,(values,output)
                before,*timings,counters,counts,after=values
                assert timings[0][2] is True and timings[1][2]==20 and timings[2][2]==65,values
                assert counters[0]==6 and counters[1]>=3 and counts['blocked']==0,values
                assert counts['read_open']==(2 if lane=='python' else 0),values
                row={'sample':number,'timings':timings,'process_ns':process_ns,'counters':counters,'counts':counts,
                     'proc_io_delta':{k:after[k]-before[k] for k in after},'page_cache_advice':advice}
                all_samples[lane].append(row);c.save('sample-%02d-%s.json'%(number,lane),row)
        summary={}
        for lane,records in all_samples.items():
            summary[lane]={}
            for i,(label,_) in enumerate(expressions):
                values=[r['timings'][i][1] for r in records]
                summary[lane][label]={'samples_ns':values,'first_ns':values[0],
                    'median_ns':statistics.median(values),'min_ns':min(values),'max_ns':max(values)}
            summary[lane]['process_ns']=[r['process_ns'] for r in records]
        c.save('result.json',{'status':'PASS_COLD_COMPONENT_MEASUREMENTS','samples':all_samples,'summary':summary,
               'limits':'fresh process and fresh production-shaped state per sample; fsynced with DONTNEED advice where available, not a guaranteed physical disk cache flush. Actual real per-use callers/gate/renewal; inner candidate/work reap inert, context disabled. Profile steps follow cold steps. proc IO includes observer overhead. Same CeTTa evaluator Python-facade comparator, NOT SWI or a full turn. Process wall includes startup, imports and observation; component times exclude them.'})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence',type=Path,required=True)
    p.add_argument('--samples',type=int,default=7);args=p.parse_args();assert 1<=args.samples<=20
    limit();args.evidence.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='mode06-cost-') as directory:
        c=Qualification(args.evidence.resolve(),Path(directory))
        c.snapshot_sources('tests/mode_state_cost_check.py','src/loop.metta','src/engine_active_cetta.metta')
        check(c,args.samples)


if __name__=='__main__':main()
