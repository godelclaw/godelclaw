"""Actual native/Python writers, same inode/lock; witnessed order, not launch order."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time

from effect_receipts_native_check import HEADER
from effect_receipts_support import Qualification, ROOT, limit, q, rows, emit


def native_entry(i):
    return {'schema':1,'recorded_at':1704067200.0+i,'turn':i,'occurrence':'native:%d'%i,
            'disposition':'returned','command':'fixture-native','result':'fixture-result'}


def python_entry(i):
    return {'schema':1,'recorded_at':1704067200.0+i,'disposition':'atlas-result-returned',
        'server':'fixture-server','tool':'atlas_query','atlas':{'kind':'view','view_id':'view:%d'%i,
            'state_revision':'fixture-revision','receipt_id':'python:%d'%i,'status':'fixture'}}


def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True).encode()+b'\n'


def overlap(c,mode,count):
    root=c.directory/('work-'+mode);root.mkdir()
    (root/'state.json').write_text('{"enabled": true, "seq": 0, "epoch": 7}')
    receipt=c.directory/('receipt-'+mode+'.jsonl');receipt.touch()
    trace=c.directory/('trace-'+mode)
    lock_path=receipt if mode=='receipts' else root/'lock'
    settings={'METTACLAW_EFFECT_RECEIPT_PATH':str(receipt),'METTACLAW_PUBLIC_WORK_DIR':str(root)}
    source=HEADER+'''
!(bind! &mx-index (new-state 0))
!(match &self (= (erio:lock $owner) $body)
 (progn (add-atom &self (= (mx:original-lock $owner) $body))
        (remove-atom &self (= (erio:lock $owner) $body))))
'''
    source+='''
(= (erio:lock $owner)
 (let $answer (mx:original-lock $owner)
  (if (== $answer fs:locked)
   (let $_ (fs:append-text %s (str:join "" ("native:" (repr (get-state &mx-index)) "\\n"))) $answer)
   $answer)))
!(system:write stdout "READY\\n")
'''%q(trace)
    for i in range(count):
        source+='!(change-state! &mx-index %d)\n'%i
        if mode=='receipts':
            document=json.dumps(native_entry(i),ensure_ascii=False)
            expression='(er:append (json:parse-python %s))'%q(document)
        elif mode=='worklog':
            expression='(pw:transact %s "add" %s 7)'%(q(root),q('native:%d'%i))
        else:
            expression='(pw:transact %s %s "" JsonNull)'%(q(root),q('on' if i%2==0 else 'off'))
        source+=emit('(JsonArray ("operation" %d %s))'%(i,expression))
    source=source.replace('./src/',str(ROOT/'src')+'/')
    program=c.directory/('mixed-'+mode+'.metta');program.write_text(source)
    (c.evidence/program.name).write_text(source)
    commands=[
        [str(c.binary),'--lang','petta','--import-mode','ancestor-walk',str(program)],
        [sys.executable,'-I','-B',str(ROOT/'tests/effect_receipts_writer_process.py'),mode,str(count),str(trace)],
    ]
    env=c.environment();env.update(settings)
    workers=[];outputs=[];recorded=set();operation_results={}
    started=time.monotonic()
    with lock_path.open('a') as gate:
        fcntl.flock(gate,fcntl.LOCK_EX)
        try:
            for command in commands:
                workers.append(subprocess.Popen(command,cwd=c.directory,env=env,
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE))
            for worker in workers:
                assert select.select([worker.stdout],[],[],30)[0], 'readiness timeout'
                ready=worker.stdout.readline()
                outputs.append(ready)
                assert ready==b'READY\n',ready
            assert not trace.exists(), 'writer passed the held common lock'
            fcntl.flock(gate,fcntl.LOCK_UN)
            for i,(worker,command) in enumerate(zip(workers,commands)):
                out,err=worker.communicate(timeout=90)
                result=subprocess.CompletedProcess(command,worker.returncode,outputs[i]+out,err)
                c.record_process('overlap-%s-%s'%(mode,('native','python')[i]),command,result,env)
                recorded.add(i)
                assert result.returncode==0,err
                if i==0:
                    observed=rows(result.stdout.decode())
                    assert len(observed)==count and all(row[:2]==['operation',j] for j,row in enumerate(observed)),observed
                    operation_results['native']=[row[2] for row in observed]
                else:
                    operation_results['python']=[json.loads(line[len('RESULT '):])
                        for line in result.stdout.decode().splitlines() if line.startswith('RESULT ')]
                    assert len(operation_results['python'])==count
        finally:
            for i,worker in enumerate(workers):
                if worker.poll() is None:
                    worker.terminate()
                    try:worker.wait(timeout=5)
                    except subprocess.TimeoutExpired:worker.kill();worker.wait()
                if i not in recorded:
                    out,err=worker.communicate()
                    result=subprocess.CompletedProcess(commands[i],worker.returncode,
                        (outputs[i] if i<len(outputs) else b'')+out,err)
                    c.record_process('overlap-%s-%s-aborted'%(mode,('native','python')[i]),commands[i],result,env)
    order=trace.read_text().splitlines()
    files={'receipt':receipt.read_bytes().hex(),
           **{name:(root/name).read_bytes().hex() if (root/name).exists() else None
              for name in ('state.json','notes.jsonl','lock')}}
    c.save('overlap-%s-effects.json'%mode,{'lock_acquisition_order':order,'files':files,
        'operation_results':operation_results,
        'pids':[w.pid for w in workers],'elapsed_seconds':time.monotonic()-started})
    assert len(order)==2*count and len(set(order))==2*count,order
    assert {s.split(':')[0] for s in order}=={'native','python'}
    transitions=sum(a.split(':')[0]!=b.split(':')[0] for a,b in zip(order,order[1:]))
    assert transitions>=2,('no observed interleaving',order)
    for position,key in enumerate(order,1):
        writer,index=key.split(':');result=operation_results[writer][int(index)]
        if mode=='receipts':assert result==1
        elif mode=='worklog':assert result['accepted'] and result['seq']==position and result['epoch']==7
        else:assert result['epoch']==7+position and result['seq']==0 and result['enabled']==(int(index)%2==0)
    if mode=='receipts':
        expected=b''.join(encoded(native_entry(int(key.split(':')[1])) if key.startswith('native:')
                                  else python_entry(int(key.split(':')[1]))) for key in order)
        assert receipt.read_bytes()==expected
    elif mode=='worklog':
        data=(root/'notes.jsonl').read_bytes()
        notes=[json.loads(line) for line in data.splitlines()]
        assert [note['text'] for note in notes]==order
        assert [note['seq'] for note in notes]==list(range(1,2*count+1))
        expected=b''.join((json.dumps({'seq':i+1,'time':note['time'],'text':key},ensure_ascii=False)+'\n').encode()
                         for i,(key,note) in enumerate(zip(order,notes)))
        assert data==expected
        assert (root/'state.json').read_bytes()==json.dumps({'enabled':True,'seq':2*count,'epoch':7}).encode()
        assert not any(p.name.startswith('state.tmp.') for p in root.iterdir())
    else:
        assert (root/'state.json').read_bytes()==json.dumps({'enabled':int(order[-1].split(':')[1])%2==0,
                                                           'seq':0,'epoch':7+2*count}).encode()
        assert not (root/'notes.jsonl').exists()
        assert not any(p.name.startswith('state.tmp.') for p in root.iterdir())
    return {'mode':mode,'operations':2*count,'writer_transitions':transitions,'ordered_exact_bytes':True}


def check(c):
    # Alternating operations against the very same receipt file and exact bytes
    # checked after each observed operation, using retained Atlas Python writer.
    sys.path.insert(0,str(ROOT/'src'))
    import effect_receipts as oracle
    from unittest import mock
    expected=b'';alternating=[]
    path=c.directory/'receipts.jsonl'
    for i in range(6):
        source=HEADER+emit('(er:append (json:parse-python %s))'%q(json.dumps(native_entry(i))))
        actual=rows(c.run('alternating-native-%d'%i,source))
        expected+=encoded(native_entry(i))
        assert actual==[1] and path.read_bytes()==expected
        alternating.append({'writer':'native','index':i,'bytes_hex':path.read_bytes().hex()})
        with mock.patch.dict(os.environ,c.environment(),clear=True),mock.patch.object(oracle.time,'time',return_value=1704067200.0+i):
            assert oracle.record_atlas_result('fixture-server','atlas_query',
                {'receipt':{'receipt_id':'python:%d'%i},'view_id':'view:%d'%i,
                 'state_revision':'fixture-revision','status':'fixture'})==1
        expected+=encoded(python_entry(i));assert path.read_bytes()==expected
        alternating.append({'writer':'python','index':i,'bytes_hex':path.read_bytes().hex()})
    c.save('alternating-receipts.json',alternating)
    import model_commentary
    root=c.directory/'alternating-work';root.mkdir()
    state={'enabled':True,'seq':0,'epoch':7,'extra':'\ud800','unicode':'日🙂'}
    (root/'state.json').write_text(json.dumps(state))
    notes=[];work_steps=[]
    for i in range(4):
        for route in ('native','python'):
            authored=route+':'+str(i)
            if route=='native':
                source=HEADER+emit('(pw:transact %s "add" %s 7)'%(q(root),q(authored)))
                actual=rows(c.run('alternating-native-worklog-%d'%i,source))
                assert actual[0]['accepted']
            else:
                with mock.patch.dict(os.environ,{**c.environment(),'METTACLAW_PUBLIC_WORK_DIR':str(root)},clear=True):
                    assert model_commentary.Publication().publish(authored)==1
            latest=json.loads((root/'notes.jsonl').read_bytes().splitlines()[-1])
            assert latest['seq']==len(notes)+1 and latest['text']==authored
            notes.append(latest);state['seq']=len(notes)
            expected=b''.join((json.dumps(note,ensure_ascii=False)+'\n').encode() for note in notes)
            assert (root/'notes.jsonl').read_bytes()==expected
            assert (root/'state.json').read_bytes()==json.dumps(state).encode()
            work_steps.append({'route':route,'state_hex':(root/'state.json').read_bytes().hex(),
                               'notes_hex':expected.hex(),'lock_hex':(root/'lock').read_bytes().hex()})
    c.save('alternating-worklog.json',work_steps)
    results=[overlap(c,'receipts',40),overlap(c,'worklog',40),overlap(c,'epochs',40)]
    c.save('result.json',{'alternating_receipt_operations':12,'alternating_worklog_operations':8,'overlap':results,
        'limit':'per-file durability primitives exercised; no directory fsync/multi-file atomicity/crash-exactly-once proof'})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence',type=Path,required=True)
    a=p.parse_args();limit();assert sys.version_info[:3]==(3,12,13)
    a.evidence.mkdir(parents=True,exist_ok=False);(a.evidence/'runner.py').write_bytes(Path(__file__).read_bytes())
    with tempfile.TemporaryDirectory(prefix='effect05-mixed-') as temporary:
        c=Qualification(a.evidence.resolve(),Path(temporary))
        with c.phase('mixed-native-python-writers'):
            c.snapshot_sources('src/effect_receipts_cetta.metta','src/public_worklog_cetta.metta',
                'src/effect_receipt_io_cetta.metta','tests/effect_receipts_writer_process.py')
            check(c)


if __name__=='__main__':main()
