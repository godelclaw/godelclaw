"""Actual baseline/current responder error transport, with inert retained controls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from effect_receipts_support import IMPORTS, ROOT, emit, limit, q, rows
from mode_state_callers_check import CONTROL, stage
from mode_state_native_check import SNAPSHOT
from mode_state_support import Qualification


def baseline_source(c):
    requested=os.environ.get('MODE_STATE_BASELINE_COMMIT','')
    c.save('baseline-source.json',{'requested':requested,'status':'UNVALIDATED'})
    assert re.fullmatch(r'[0-9a-fA-F]{40}|[0-9a-fA-F]{64}',requested), 'MODE_STATE_BASELINE_COMMIT must be an explicit full commit ID'
    env=c.environment()
    command=['git','rev-parse','--verify',requested+'^{commit}']
    child=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,timeout=20)
    c.record_process('baseline-resolve',command,child,env,cwd=ROOT)
    assert child.returncode==0,'MODE_STATE_BASELINE_COMMIT is not resolvable as a commit'
    resolved=child.stdout.decode().strip()
    assert resolved==requested.lower(),'MODE_STATE_BASELINE_COMMIT must identify the commit itself'
    command=['git','show',resolved+':channels/telegram_control.metta']
    child=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,timeout=20)
    c.record_process('baseline-handler-source',command,child,env,cwd=ROOT)
    assert child.returncode==0,child.stderr
    c.save('baseline-source.json',{'requested':requested,'resolved':resolved,
           'sha256':hashlib.sha256(child.stdout).hexdigest(),'status':'VALIDATED_IMMUTABLE_COMMIT',
           'source':'explicit pinned original responder; surrounding facade imports shared'})
    return child.stdout


RANDOM=re.compile(r'(?<=\.lifecycle-)[a-z0-9_]{8}(?![a-z0-9_])')


def normalize(value):
    if isinstance(value,str):return RANDOM.sub('random00',value)
    if isinstance(value,list):return [normalize(v) for v in value]
    if isinstance(value,dict):return {normalize(k):normalize(v) for k,v in value.items()}
    return value


def project(observation):
    result=normalize(observation)
    if observation['cuts']:
        assert len(observation['cuts'])==1,observation
        text,limit=observation['cuts'][0]
        assert observation['reply']==text[:limit],observation
        # First assert the ACTUAL raw cut, then mask only the random character
        # positions that survived it. Never replace the whole basename or
        # normalize a longer/shorter string before truncation.
        reply=list(observation['reply'])
        for match in RANDOM.finditer(text):
            for position in range(match.start(),min(match.end(),len(reply))):
                reply[position]='random00'[position-match.start()]
        result['reply']=''.join(reply)
    return result


CUT_TRACE='''
!(bind! &handler-cuts (new-state ()))
!(match &self (= (tc:cut $text $n) $prior)
 (progn (remove-atom &self (= (tc:cut $text $n) $prior))
        (add-atom &self (= (handler:original-cut $text $n) $prior))))
(= (tc:cut $text $n)
 (let $_ (change-state! &handler-cuts (cons-atom (JsonArray ($text $n)) (get-state &handler-cuts)))
   (handler:original-cut $text $n)))
'''


def check(c):
    with c.phase('actual-handler-native-error-transport'):
        baseline=baseline_source(c)
        native=stage(c,'native-package');oracle=stage(c,'oracle-package')
        (oracle/'channels/telegram_control.metta').write_bytes(baseline)
        fixture=c.directory/'handler_error_fixture.py'
        fixture.write_text(CONTROL+'''
counts['exception_text']=0
original_exception_text=helper.exception_text
def exception_text(value):
    counts['exception_text']+=1
    return original_exception_text(value)
helper.exception_text=exception_text
''')
        (c.evidence/fixture.name).write_bytes(fixture.read_bytes())
        root=c.directory/'state'
        cases=[('engine-decode','/engine',''),('failure-decode','/engine',''),
               ('pid-overflow','/mode',''),('start-target-directory','/start',''),
               ('stop-target-directory','/stop',''),('mode-wake-directory','/mode','coding'),
               ('start-parent-blocked','/start',''),('active-invalid-utf8','/mode','')]
        results=[]
        for name,verb,arg in cases:
            observed=[]
            for package,poison in ((oracle,'0'),(native,'1')):
                if root.exists():shutil.rmtree(root)
                root.mkdir()
                (root/'engine').write_bytes(b'cetta\n');(root/'loop').write_text('{"mode":"godelclaw"}')
                (root/'lifecycle').write_text('{"schema":1,"state":"stopped"}')
                env={ 'MODE_STATE_POISON':poison,
                      'PYTHONPATH':os.pathsep.join(map(str,(c.directory,package/'src',package/'channels',ROOT/'tests'))),
                      **{'METTACLAW_'+key:str(root/file) for key,file in (
                          ('ENGINE_STATE_PATH','engine'),('ENGINE_FAILURE_PATH','failure'),('LOOP_MODE_PATH','loop'),
                          ('LIFECYCLE_PATH','lifecycle'),('RECYCLE_REQUEST_PATH','recycle'),('WAKE_REQUEST_PATH','wake'))}}
                if name=='engine-decode':(root/'engine').write_bytes(b'\xff')
                elif name=='failure-decode':(root/'failure').write_bytes(b'engine=cetta\nreason=\xff')
                elif name=='pid-overflow':(root/'active-loop.json').write_text('{"mode":"coding","pid":Infinity}')
                elif name in ('start-target-directory','stop-target-directory'):
                    (root/'lifecycle').unlink();(root/'lifecycle').mkdir()
                elif name=='mode-wake-directory':(root/'wake').mkdir()
                elif name=='start-parent-blocked':
                    (root/'blocker').write_text('not a directory');env['METTACLAW_LIFECYCLE_PATH']=str(root/'blocker/lifecycle')
                elif name=='active-invalid-utf8':(root/'active-loop.json').write_bytes(b'\xff')
                source=IMPORTS+'!(import! &self %s)\n'%fixture
                source+='!(import! &self %s)\n'%(package/'channels/telegram_control.metta')
                source+=CUT_TRACE
                source+='!(match &self (= (system:wall-time) $prior) (remove-atom &self (= (system:wall-time) $prior)))\n(= (system:wall-time) 1790000000.123456)\n'
                source+='(= (handler:text $v) (if (== (get-type $v) String) $v (str:symbol->string $v)))\n'
                source+=emit('(tc:guarded (catch (tc:answer %s %s)) %s)'%(q(verb),q(arg),q(verb)))
                source+=emit('(json:parse-python (handler:text (py-call (handler_error_fixture.summary))))')
                source+=emit('(JsonArray (get-state &handler-cuts))')
                output=c.run(('oracle-' if poison=='0' else 'native-')+name,source,env,runtime_root=package)
                values=rows(output);assert len(values)==3,output
                # Test-side read only; all returned and partial file effects retained.
                namespace={};exec(SNAPSHOT,namespace)
                observed.append({'reply':values[0],'counts':values[1],'cuts':values[2],'files':namespace['snapshot'](root),
                                 'printed':[line for line in output.splitlines() if line.startswith(('[engine-mode]','[loop-mode]'))]})
            pair={'case':name,'oracle':observed[0],'native':observed[1]}
            pair['projected']=[project(o) for o in observed]
            if name=='start-target-directory':
                lengths=[len(o['cuts'][0][0]) for o in observed]
                pair['uncut_lengths']=lengths
                pair['raw_reply_lengths']=[len(o['reply']) for o in observed]
                if os.environ.get('MODE_STATE_REQUIRE_LONG_REPLY')=='1':
                    assert all(n>300 for n in lengths),pair
                    assert pair['raw_reply_lengths']==[300,300],pair
                c.save('long-truncation-witness.json',pair)
            c.save(name+'.json',pair)
            assert pair['projected'][0]==pair['projected'][1],pair
            assert observed[1]['counts']['blocked']==0,pair
            results.append(pair)
        c.save('result.json',{'status':'PASS_ACTUAL_HANDLER_ERROR_TRANSPORT','cases':results,
               'retained':'adopt/metta_value/resume and existing exception_text formatting explicitly counted',
               'semantics':'baseline genuine Python bridge errors versus native structured Error values; downstream effects must stop identically'})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence',type=Path,required=True)
    args=p.parse_args();limit();args.evidence.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='mode06-handler-errors-') as directory:
        c=Qualification(args.evidence.resolve(),Path(directory))
        c.snapshot_sources('tests/mode_state_handler_errors_check.py','channels/telegram_control.metta','src/mode_values_cetta.metta')
        check(c)


if __name__=='__main__':main()
