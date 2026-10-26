"""Dynamic census of every reached active-engine site in staged real callers."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

from effect_receipts_support import IMPORTS, ROOT, emit, limit, q, rows
from mode_state_callers_check import CONTROL, stage
from mode_state_support import Qualification


def check(c):
    with c.phase('all-import-and-per-use-engine-switch-sites'):
        package=stage(c,'switch-package')
        sites={}
        for part in ('src','channels'):
            for original in sorted((ROOT/part).glob('*.metta')):
                if original.name=='engine_modes.metta':continue
                lines=[]
                for number,line in enumerate(original.read_text().splitlines(keepends=True),1):
                    if '(engine:active)' in line:
                        label=original.relative_to(ROOT).as_posix()+':'+str(number)
                        assert label not in sites
                        sites[label]={'file':str(original.relative_to(ROOT)),'line':number}
                        line=line.replace('(engine:active)','(switch-test:active %s)'%json.dumps(label))
                    lines.append(line)
                target=package/original.relative_to(ROOT)
                target.write_text(''.join(lines))
        c.save('static-sites.json',sites)
        fixture=c.directory/'switch_fixture.py'
        fixture.write_text(CONTROL+'''
import native_llm_host,strategy_host
counts.update(llm_selection=0,context_input=0,active_test_sets=0)
def no_provider():
    counts['llm_selection']+=1;return 0
def disabled_context(name,*args):
    counts['context_input']+=1
    assert name=='enabled'
    return 0
def set_active(name):
    counts['active_test_sets']+=1;os.environ['METTACLAW_ACTIVE_ENGINE']=name;return 1
native_llm_host.enabled=no_provider
strategy_host.context_input=disabled_context
''')
        (c.evidence/fixture.name).write_bytes(fixture.read_bytes())
        source=IMPORTS+'!(import! &self %s)\n'%fixture
        source+='!(import! &self ./src/engine_modes.metta)\n'
        source+='''
!(bind! &switch-site-log (new-state ()))
(= (switch-test:active $label)
 (let $engine (engine:active)
  (let $_ (change-state! &switch-site-log
      (cons-atom (JsonArray ($label $engine)) (get-state &switch-site-log))) $engine)))
'''
        # These are real caller files. Loading all of them covers transitive
        # sites too; no native memory/provider initializer or tool is invoked.
        order=['src/file_io.metta','src/state_io.metta','src/utility_io.metta','src/shell_io.metta',
               'src/tmux_io.metta','src/job_io.metta','src/fuel_io.metta','src/effect_receipts.metta',
               'src/goal_bookkeeping.metta','src/goal_views.metta','src/command_pipeline.metta',
               'src/command_dispatch.metta','src/iter_process_io.metta','src/hierarchical_memory.metta',
               'src/memory.metta','src/mail.metta','src/turn_additions.metta',
               'channels/telegram.metta','channels/telegram_control.metta']
        for name in order:source+='!(import! &self %s)\n'%(package/name)
        source+='''
; Do not reap real coding sessions. This is the one unrelated native effect
; stub; the surrounding real per-use switch still runs.
!(match &self (= (work:reap) $prior) (remove-atom &self (= (work:reap) $prior)))
!(bind! &switch-reaps (new-state 0))
(= (work:reap) (let $_ (change-state! &switch-reaps (+ (get-state &switch-reaps) 1)) 0))
'''
        expressions=['(pin "generated switch fixture")','(unpin "absent fixture")','(pins:view)',
                     '(state:prepare-context "generated prompt" 2048)','(mail:cetta?)',
                     '(addition-pins-views)','(addition-reap-work)','(tc:switch-engine "pleatta")']
        for index,expression in enumerate(expressions):source+='!(println! (SwitchUse %d %s))\n'%(index,expression)
        source+='!(py-call (switch_fixture.set_active "petta"))\n'+emit('(mail:cetta?)')
        source+='!(py-call (switch_fixture.set_active "cetta"))\n'+emit('(mail:cetta?)')
        source+=emit('(JsonArray (get-state &switch-site-log))')
        source+=emit('(get-state &switch-reaps)')
        source+=emit('(size-atom (collapse (match &self (engine-modes:loaded) True)))')
        source+='(= (switch-test:text $v) (if (== (get-type $v) String) $v (str:symbol->string $v)))\n'
        source+=emit('(json:parse-python (switch-test:text (py-call (switch_fixture.summary))))')
        env={'MODE_STATE_POISON':'1','PYTHONPATH':os.pathsep.join(map(str,(c.directory,package/'src',package/'channels',ROOT/'tests')))}
        output=c.run('actual-all-switch-callers',source,env,runtime_root=package)
        actual=rows(output);assert len(actual)==6,output
        false,true,trace,reaps,owners,counts=actual
        assert false is False and true is True and reaps==1 and owners==1,actual
        covered={label for label,_ in trace}
        c.save('coverage.json',{'sites':sites,'trace':trace,'counts':counts,'missing':sorted(set(sites)-covered),
                              'unexpected':sorted(covered-set(sites)),'real_per_use_stdout':output})
        assert covered==set(sites),(set(sites)-covered,covered-set(sites),output)
        assert counts['blocked']==0 and counts['context_input']==1 and counts['active_test_sets']==2,counts
        assert counts['adopt']==counts['metta_value']==1 and counts['resume']==0,counts
        for index in range(len(expressions)):assert '(SwitchUse %d '%index in output
        c.save('result.json',{'status':'PASS_ALL_DYNAMIC_ENGINE_SWITCH_SITES','sites':len(sites),
               'trace_calls':len(trace),'counts':counts,'one_facade_owner':owners,
               'instrumentation':'only each staged call expression is wrapped in a label; the one real engine:active computes every value',
               'effects':'real generated pin I/O; context disabled via counted retained input, work reap inert; no provider/mail/tmux/network operation'})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence',type=Path,required=True)
    args=p.parse_args();limit();args.evidence.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='mode06-switches-') as directory:
        c=Qualification(args.evidence.resolve(),Path(directory));c.snapshot_sources('tests/mode_state_switches_check.py','src/engine_active_cetta.metta','src/engine_modes.metta')
        check(c)


if __name__=='__main__':main()
