"""Actual baseline/current responder error transport, with inert retained controls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from effect_receipts_support import IMPORTS, ROOT, emit, limit, q, rows
from mode_state_callers_check import CONTROL, stage
from mode_state_native_check import SNAPSHOT
from mode_state_protocol_check import normalize
from mode_state_support import Qualification


def check(c):
    with c.phase('actual-handler-native-error-transport'):
        native=stage(c,'native-package');oracle=stage(c,'oracle-package')
        command=['git','show','main:channels/telegram_control.metta']
        child=subprocess.run(command,cwd=ROOT,env=c.environment(),capture_output=True,timeout=20)
        c.record_process('baseline-handler-source',command,child,c.environment(),cwd=ROOT)
        assert child.returncode==0
        (oracle/'channels/telegram_control.metta').write_bytes(child.stdout)
        c.save('baseline-source.json',{'sha256':hashlib.sha256(child.stdout).hexdigest(),
                                     'source':'unchanged exact baseline/main responder; surrounding new facade imports shared'})
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
                source+='!(match &self (= (system:wall-time) $prior) (remove-atom &self (= (system:wall-time) $prior)))\n(= (system:wall-time) 1790000000.123456)\n'
                source+='(= (handler:text $v) (if (== (get-type $v) String) $v (str:symbol->string $v)))\n'
                source+=emit('(tc:guarded (catch (tc:answer %s %s)) %s)'%(q(verb),q(arg),q(verb)))
                source+=emit('(json:parse-python (handler:text (py-call (handler_error_fixture.summary))))')
                output=c.run(('oracle-' if poison=='0' else 'native-')+name,source,env,runtime_root=package)
                values=rows(output);assert len(values)==2,output
                # Test-side read only; all returned and partial file effects retained.
                namespace={};exec(SNAPSHOT,namespace)
                observed.append({'reply':values[0],'counts':values[1],'files':namespace['snapshot'](root),
                                 'printed':[line for line in output.splitlines() if line.startswith(('[engine-mode]','[loop-mode]'))]})
            pair={'case':name,'oracle':observed[0],'native':observed[1]}
            c.save(name+'.json',pair)
            assert normalize(observed[0])==normalize(observed[1]),pair
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
