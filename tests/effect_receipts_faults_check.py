"""Real + injected POSIX file faults; Linux interposer is a test fixture only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from effect_receipts_native_check import HEADER
from effect_receipts_callers_check import FREEZE
from effect_receipts_support import Qualification, ROOT, emit, limit, q, rows


WIRE='''
(= (ft:wire $value)
 (case $value
  (((ErFailure $class $message $detail)
     (JsonArray ("error" (str:symbol->string $class) $message (repr $detail))))
   ($other (JsonArray ("value" $other))))))
'''


def snapshot(root):
    return {('state.tmp.PID' if p.name.startswith('state.tmp.') else p.name):p.read_bytes().hex()
            for p in root.iterdir() if p.is_file()}


def check(c):
    if sys.platform!='linux':
        raise RuntimeError('This syscall-fault qualification fixture requires Linux LD_PRELOAD; no runtime dependency')
    cc=shutil.which('cc',path=c.environment()['PATH'])
    assert cc,'C fixture compiler unavailable; no fault qualification claimed'
    shim=c.directory/'fault-fixture.so'
    command=[cc,'-shared','-fPIC','-O2',str(ROOT/'tests/effect_receipts_fault_shim.c'),'-ldl','-o',str(shim)]
    env=c.environment();compiled=subprocess.run(command,cwd=c.directory,env=env,capture_output=True,timeout=60)
    c.record_process('compile-test-fixture-not-engine',command,compiled,env)
    assert compiled.returncode==0,compiled.stderr
    c.save('fault-fixture-identity.json',{'fixture_only':True,'platform':sys.platform,'compiler':cc,
        'source_sha256':hashlib.sha256((ROOT/'tests/effect_receipts_fault_shim.c').read_bytes()).hexdigest(),
        'shared_object_sha256':hashlib.sha256(shim.read_bytes()).hexdigest(),
        'engine_rebuilt_or_modified':False,'fsync_successes':'always invoke real libc fsync'})
    specs=[('receipt',role,fault) for role in ('receipt',) for fault in ('none','open','lock','write','partial','short','eintr','sync','close')]
    specs += [('add','notes',fault) for fault in ('write','partial','short','eintr','sync','close')]
    specs += [('add','state',fault) for fault in ('write','partial','short','eintr','sync','close','rename')]
    specs += [('status','lock','lock'),('status','state','open')]
    specs += [('receipt','receipt','open-eintr'),('add','notes','open-eintr'),
              ('status','state','open-eintr'),('status','lock','open-eintr')]
    results=[]
    for index,(action,role,fault) in enumerate(specs):
        paths=[];outcomes=[];traces=[];files=[]
        for route in ('native','python'):
            root=c.directory/('%02d-%s'%(index,route));root.mkdir()
            work=root/'work';work.mkdir()
            (work/'state.json').write_text('{"enabled": true, "seq": 0, "epoch": 7}')
            receipt=root/'receipt.jsonl'
            target={'receipt':receipt,'notes':work/'notes.jsonl','state':work/'state.tmp.','lock':work/'lock'}[role]
            log=root/'syscalls.log'
            settings={'LD_PRELOAD':str(shim),'EFFECT_FAULT_MODE':fault,'EFFECT_FAULT_TARGET':str(target),
                'EFFECT_FAULT_LOG':str(log),'METTACLAW_PUBLIC_WORK_DIR':str(work),
                'METTACLAW_EFFECT_RECEIPT_PATH':str(receipt)}
            child_env=c.environment();child_env.update(settings)
            if route=='native':
                source=HEADER+FREEZE+WIRE
                source+='!(println! (FaultLibrary (library fs)))\n'
                expression='(er:append (JsonObject ((JsonPair "schema" 1) (JsonPair "command" "fixture") (JsonPair "result" "r"))))' if action=='receipt' else '(pw:transact %s %s "fixture note" 7)'%(q(work),q(action))
                source+=emit('(ft:wire %s)'%expression)
                # Reacquire the common lock before process exit, while retained
                # owner values cannot be released merely by process teardown.
                if fault!='open':
                    lock=receipt if action=='receipt' else work/'lock'
                    source+=emit('(let $owner (erio:open %s ("append" "create") 438) (let $locked (erio:lock $owner) (erio:finish $owner (not (erv:failed? $locked)) (if (erv:failed? $locked) $locked 1))))'%q(lock))
                raw=c.run('fault-%02d-native'%index,source,settings)
                native_files={'work':snapshot(work),'receipt':receipt.read_bytes().hex() if receipt.exists() else None}
                c.save('fault-%02d-native-files.json'%index,native_files)
                parsed=rows(raw);assert parsed,raw
                if fault!='open':assert parsed[1]==1,parsed
                outcomes.append(parsed[0])
                descriptor=next(line for line in raw.splitlines() if line.startswith('(FaultLibrary '))
                assert str(c.bundle/'lib') in descriptor,descriptor
            else:
                command=[sys.executable,'-I','-B',str(ROOT/'tests/effect_receipts_fault_oracle.py'),action]
                child=subprocess.run(command,cwd=c.directory,env=child_env,capture_output=True,timeout=60)
                c.record_process('fault-%02d-python'%index,command,child,child_env)
                assert child.returncode==0,child.stderr
                outcomes.append(json.loads(child.stdout))
            files.append({'work':snapshot(work),'receipt':receipt.read_bytes().hex() if receipt.exists() else None})
            traces.append(log.read_text() if log.exists() else '')
        equal_files=files[0]==files[1]
        equal_outcome=outcomes[0][:2]==outcomes[1][:2] if outcomes[0][0]=='error' else outcomes[0]==outcomes[1]
        results.append({'action':action,'role':role,'fault':fault,'outcomes':outcomes,'files':files,
            'syscall_traces':traces,'files_equal':equal_files,'outcome_classes_equal':equal_outcome,
            'diagnostic_boundary':'native OS messages omit Python errno/filename formatting; full raw messages/progress retained'})
        c.save('fault-results.json',results)
        assert equal_files and equal_outcome,(index,action,role,fault,outcomes,equal_files)
        if fault in ('short','eintr','none'):
            assert 'fsync-real' in traces[0] and 'result=0' in traces[0],traces[0]
        if fault=='open-eintr':
            assert all('injected' in trace and 'errno=4' in trace and
                       ('open-real' in trace or 'openat-real' in trace) and 'close-real' in trace
                       for trace in traces),traces
    c.save('result.json',{'fault_cases':len(specs),'native_immediate_reacquisition':len(specs)-2,
        'uncertain_outcomes_replayed':False,'fixture':'Linux syscall interposition, no fake fsync success',
        'interrupted_open_cases':4,
        'interruption_limit':'one-shot syscall EINTR injection, not evaluator interruption or an external signal test',
        'not_claimed':['crash exactly once','multi-file atomicity','directory fsync']})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence',type=Path,required=True)
    a=p.parse_args();limit();assert sys.version_info[:3]==(3,12,13)
    a.evidence.mkdir(parents=True,exist_ok=False);(a.evidence/'runner.py').write_bytes(Path(__file__).read_bytes())
    with tempfile.TemporaryDirectory(prefix='effect05-faults-') as temporary:
        c=Qualification(a.evidence.resolve(),Path(temporary))
        with c.phase('native-python-faults'):
            c.snapshot_sources('tests/effect_receipts_fault_shim.c','tests/effect_receipts_fault_oracle.py',
                'src/effect_receipt_io_cetta.metta','src/public_worklog_cetta.metta','src/effect_receipts_cetta.metta')
            check(c)


if __name__=='__main__':main()
