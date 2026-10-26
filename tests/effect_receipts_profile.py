"""Actual hook distributions, warm files/history, counted real fsync, no turn claim."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
from unittest import mock

from effect_receipts_support import IMPORTS,Qualification,ROOT,emit,limit,q,rows


def describe(ns):
    ordered=sorted(ns)
    return {'n':len(ns),'min_ns':min(ns),'median_ns':statistics.median(ns),
        'mean_ns':statistics.mean(ns),'p95_ns':ordered[int((len(ns)-1)*.95)],
        'p99_ns':ordered[int((len(ns)-1)*.99)],'max_ns':max(ns),'first_ns':ns[0]}


def instrument(name,arguments,label):
    args=' '.join('$'+v for v in arguments)
    return '''
!(bind! &bp-%s (new-state ()))
!(match &self (= (%s %s) $body)
 (progn (add-atom &self (= (bp:original-%s %s) $body))
        (remove-atom &self (= (%s %s) $body))))
(= (%s %s)
 (let* (($start (system:monotonic-ns)) ($value (bp:original-%s %s))
        ($end (system:monotonic-ns))
        ($_ (change-state! &bp-%s (cons-atom (- $end $start) (get-state &bp-%s))))) $value))
'''%(label,name,args,label,args,name,args,name,args,label,args,label,label)


def check(c,count):
    sys.path.insert(0,str(ROOT/'src'))
    import effect_receipts as oracle,stream_sink,strategy_host
    fixture=c.directory/'fixture.txt';contents='generated harmless profile fixture🙂\n';fixture.write_text(contents)
    warm=b''.join((json.dumps({'schema':1,'recorded_at':1700000000.0+i,'turn':i,
        'occurrence':'warm-%d'%i,'disposition':'returned','command':"['read-file', 'generated fixture']",
        'result':'generated result '*40},ensure_ascii=False,sort_keys=True)+'\n').encode() for i in range(5000))
    seeds=['rcpt:'+hashlib.sha256(('seed:%d'%i).encode()).hexdigest()[:16] for i in range(1024)]
    outcomes=[]
    for sharing,strategy in [('0','0'),('1','0'),('0','1'),('1','1')]:
        stem='sharing-%s-strategy-%s'%(sharing,strategy)
        receipt=c.directory/(stem+'.jsonl');receipt.write_bytes(warm)
        root=c.directory/(stem+'-work');root.mkdir()
        (root/'state.json').write_text('{"enabled": true, "seq": 200, "epoch": 7}')
        initial_notes=b''.join((json.dumps({'seq':i,'time':'2024-01-01T01:00:00+01:00','text':'generated warm note'},ensure_ascii=False)+'\n').encode() for i in range(1,201))
        (root/'notes.jsonl').write_bytes(initial_notes)
        source=IMPORTS+'!(import! &self ./tests/effect_receipts_caller_probe.py)\n!(py-call (effect_receipts_caller_probe.setup))\n'
        source+='!(import! &self ./src/api_registry.metta)\n!(import! &self ./src/file_io.metta)\n!(import! &self ./src/command_dispatch.metta)\n'
        source+='!(change-state! &er-published (%s))\n'%' '.join(json.dumps(v) for v in seeds)
        source+='!(change-state! &er-captured (%s))\n'%' '.join('(%d 7)'%i for i in range(64))
        for name,args,label in [('dispatch:record',['turn','status','command','result'],'hook'),
            ('er:append',['entry'],'append'),('er:publish',['turn','d','c','r','at','occ'],'projection'),
            ('fs:sync',['owner'],'fsync'),('fs:open-write',['path','flags','mode'],'open'),
            ('fs:write-all',['owner','text'],'write'),('erv:strip',['text'],'strip')]:
            source+=instrument(name,args,label)
        literal=json.dumps(str(fixture))
        commands=' '.join('(read-file %s)'%literal for _ in range(count))
        source+='!(run-command-batch-once 999 5 (quote (%s)))\n'%commands
        source+=emit('(JsonObject (%s))'%' '.join('(JsonPair "%s" (JsonArray (list:reverse (get-state &bp-%s))))'%(k,k)
            for k in ('hook','append','projection','fsync','open','write','strip')))
        source+=emit('(json:parse-python (str:symbol->string (py-call (effect_receipts_caller_probe.summary))))')
        raw=c.run('hook-profile-'+stem,source,{'METTACLAW_PUBLIC_TOOL_RECEIPTS':sharing,
            'METTACLAW_OBSERVED_STRATEGY':strategy,'METTACLAW_EFFECT_RECEIPT_PATH':str(receipt),
            'METTACLAW_PUBLIC_WORK_DIR':str(root),'EFFECT_TEST_STREAM_COUNTER_ONLY':'1'})
        profile,host=rows(raw)
        assert len(profile['hook'])==len(profile['append'])==len(profile['projection'])==count
        expected_sync=count if sharing=='0' else 3*count+1
        assert len(profile['fsync'])==expected_sync,(stem,len(profile['fsync']),expected_sync)
        assert host['counts']['stream']==count and host['counts'].get('forbidden',0)==0
        assert len(receipt.read_bytes().splitlines())==5000+count
        c.save(stem+'-native.json',{'samples':profile,'summaries':{k:describe(v) for k,v in profile.items() if v},
            'host':host,'warm_receipt_bytes':len(warm),'warm_receipt_lines':5000,
            'warm_notes':200,'initial_published':1024,'initial_captured':64,
            'final_state':json.loads((root/'state.json').read_text()),'expected_real_fsync_calls':expected_sync})
        # Direct unchanged oracle hook body reference; not a SWI full-turn cost.
        reference=c.directory/(stem+'-python.jsonl');reference.write_bytes(warm)
        pyroot=c.directory/(stem+'-python-work');pyroot.mkdir()
        (pyroot/'state.json').write_text('{"enabled": true, "seq": 200, "epoch": 7}')
        (pyroot/'notes.jsonl').write_bytes(initial_notes)
        env={**c.environment(),'METTACLAW_PUBLIC_TOOL_RECEIPTS':sharing,'METTACLAW_OBSERVED_STRATEGY':strategy,
             'METTACLAW_EFFECT_RECEIPT_PATH':str(reference),'METTACLAW_PUBLIC_WORK_DIR':str(pyroot)}
        py_samples=[];sync_count=[0];position=[0];original_sync=os.fsync
        def sync(fd):sync_count[0]+=1;return original_sync(fd)
        oracle._published.clear();oracle._published.update((v,True) for v in seeds)
        oracle._captured.clear();oracle._captured.update((i,7) for i in range(64));oracle._in_flight.clear()
        with mock.patch.dict(os.environ,env,clear=True),mock.patch.object(os,'fsync',side_effect=sync),\
            mock.patch.object(stream_sink,'receipt',return_value=1),\
            mock.patch.object(strategy_host,'command_result',return_value=1),\
            mock.patch.object(strategy_host,'command_identity',side_effect=lambda turn:'host:999:%d'%position[0]):
            time.tzset()
            before=time.perf_counter_ns();oracle.capture_public_epoch(999);capture_ns=time.perf_counter_ns()-before
            for i in range(count):
                position[0]=i+1;start=time.perf_counter_ns()
                assert oracle.record_command(999,'returned',['read-file',str(fixture)],contents)==1
                py_samples.append(time.perf_counter_ns()-start)
        time.tzset()
        assert sync_count[0]==expected_sync,(stem,sync_count[0],expected_sync)
        row={'route':stem,'native_hook':describe(profile['hook']),'python_hook_body':describe(py_samples),
             'python_samples_ns':py_samples,'python_capture_ns':capture_ns,
             'fsync_calls_each_route':expected_sync,
             'median_ratio':statistics.median(profile['hook'])/statistics.median(py_samples)}
        outcomes.append(row);c.save('profile-results.json',outcomes)
    c.save('limits.json',{'full_turn_speed_claim':False,'startup_included':False,
        'actual_native_dispatcher':True,'component_profiles':True,'all_samples_retained':True,
        'python_reference':'unchanged record_command body with counted explicit host fixtures, not SWI dispatcher timing',
        'cache_state':'warm receipt/notes files, prefilled bounded process histories; first hook sample retained',
        'fsync':'primitive calls are real; no stubs/interposer in this benchmark'})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence',type=Path,required=True);p.add_argument('--commands',type=int,default=40)
    a=p.parse_args();limit();assert sys.version_info[:3]==(3,12,13)
    a.evidence.mkdir(parents=True,exist_ok=False);(a.evidence/'runner.py').write_bytes(Path(__file__).read_bytes())
    with tempfile.TemporaryDirectory(prefix='effect05-profile-') as temporary:
        c=Qualification(a.evidence.resolve(),Path(temporary))
        with c.phase('actual-hook-profiles'):
            c.snapshot_sources('src/effect_receipts_cetta.metta','src/effect_receipt_values_cetta.metta',
                'src/effect_receipt_io_cetta.metta','tests/effect_receipts_caller_probe.py')
            check(c,a.commands)


if __name__=='__main__':main()
