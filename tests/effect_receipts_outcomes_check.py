"""Raw hook crossings, falsey occurrences and postcommit failure ordering."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock

from effect_receipts_callers_check import FREEZE
from effect_receipts_native_check import HEADER, WIRE, outcome
from effect_receipts_support import IMPORTS, Qualification, ROOT, emit, limit, q, rows


def check(c):
    sys.path.insert(0,str(ROOT/'src'))
    import effect_receipts as oracle
    import stream_sink
    # These are native arguments at the real facade boundary, not already
    # decoded JSON values: Bool and JsonNull atoms cross as Python strings.
    fixtures=[('',None,'fixture-uuid-1'),(' 0',0,'fixture-uuid-2'),
              (' ""','','fixture-uuid-3'),(' JsonNull','JsonNull','JsonNull'),
              (' False','false','false'),(' (quote ())',[], 'fixture-uuid-4'),
              (' (quote (fixture 3))',['fixture',3],['fixture',3]),
              (' (quote (fixture (nested 4)))',['fixture',['nested',4]],['fixture',['nested',4]])]
    source=IMPORTS+'!(import! &self ./src/effect_receipts.metta)\n'
    source+='!(import! &self ./tests/effect_receipts_caller_probe.py)\n!(py-call (effect_receipts_caller_probe.setup))\n'+FREEZE+WIRE
    for suffix,_,_ in fixtures:
        source+=emit('(rt:wire (effect-receipts:record-command 3 returned (quote (shell "fixture")) (quote (answer True 3))%s))'%suffix)
    source+='!(println! (Summary (py-call (effect_receipts_caller_probe.summary))))\n'
    raw=c.run('raw-occurrences-and-stream-failure',source,{'EFFECT_TEST_STREAM_FAIL':'1'})
    actual=rows(raw);data=(c.directory/'receipts.jsonl').read_bytes()
    entries=[json.loads(line) for line in data.splitlines()]
    summary=json.loads(next(s[len('(Summary '):-1] for s in raw.splitlines() if s.startswith('(Summary ')))
    expected_path=c.directory/'oracle.jsonl'
    with mock.patch.dict(os.environ,{**c.environment(),'METTACLAW_EFFECT_RECEIPT_PATH':str(expected_path)},clear=True),\
            mock.patch.object(oracle.time,'time',return_value=1704067200.125),\
            mock.patch.object(oracle.uuid,'uuid4',side_effect=[SimpleNamespace(hex='fixture-uuid-%d'%i) for i in range(1,5)]),\
            mock.patch.object(stream_sink,'receipt',side_effect=RuntimeError('fixture stream failed')):
        for _,identity,_ in fixtures:
            assert oracle.record_command(3,'returned',['shell','fixture'],['answer','true',3],identity)==1
    c.save('raw-occurrences.json',{'actual':actual,'entries':entries,'expected_occurrences':[v[2] for v in fixtures],
        'stream_observations':summary,'native_hex':data.hex(),'oracle_hex':expected_path.read_bytes().hex()})
    assert actual==[['value',1]]*len(fixtures)
    assert [e['occurrence'] for e in entries]==[v[2] for v in fixtures]
    assert data==expected_path.read_bytes()
    assert [e['identity'] for e in summary['events']]==[v[2] for v in fixtures],summary

    # No configured receipt means no projection/stream; postcommit projection
    # failure, off and absent sinks must never undo or fail the durable receipt.
    outcomes=[]
    for index,mode in enumerate(('no-path','off','no-sink','sink-failed')):
        root=c.directory/('work-'+mode);root.mkdir()
        (root/'state.json').write_text('{"enabled": false, "seq": 0, "epoch": 7}')
        path=c.directory/('receipt-'+mode+'.jsonl')
        settings={'METTACLAW_EFFECT_RECEIPT_PATH':'' if mode=='no-path' else str(path),
                  'METTACLAW_PUBLIC_TOOL_RECEIPTS':'1','METTACLAW_PUBLIC_WORK_DIR':'' if mode=='no-sink' else str(root)}
        source=HEADER+'!(import! &self ./tests/effect_receipts_caller_probe.py)\n!(py-call (effect_receipts_caller_probe.setup))\n'+WIRE
        source+='!(change-state! &er-captured ((3 7)))\n'
        if mode=='sink-failed':
            source+='''!(match &self (= (er:sink-add $text $epoch) $body) (remove-atom &self (= (er:sink-add $text $epoch) $body)))
(= (er:sink-add $text $epoch) (Error fixture sink-failed))
'''
        source+=emit('(rt:wire (er:record 3 returned (quote (shell "fixture")) "result" "outcome-id"))')
        source+=emit('(JsonArray ((get-state &er-reporting) (size-atom (get-state &er-in-flight)) (size-atom (get-state &er-published))))')
        source+='!(println! (Summary (py-call (effect_receipts_caller_probe.summary))))\n'
        raw=c.run('postcommit-'+mode,source,settings);actual=rows(raw)
        summary=json.loads(next(s[len('(Summary '):-1] for s in raw.splitlines() if s.startswith('(Summary ')))
        outcomes.append({'mode':mode,'actual':actual,'summary':summary,'receipt_hex':path.read_bytes().hex() if path.exists() else None})
        assert actual==[['value',0 if mode=='no-path' else 1],[False,0,0]]
        assert summary['counts'].get('stream',0)==(0 if mode=='no-path' else 1)
        assert not (root/'notes.jsonl').exists()
    c.save('postcommit-outcomes.json',outcomes)

    source=HEADER+WIRE
    expected=[]
    for expression,argument in (('"bad"','bad'),('JsonNull','JsonNull'),('(quote ())',[])):
        source+=emit('(rt:wire (er:capture %s))'%expression)
        with mock.patch.dict(os.environ,{**c.environment(),'METTACLAW_PUBLIC_WORK_DIR':'',
                'METTACLAW_PUBLIC_TOOL_RECEIPTS':'1'},clear=True):
            expected.append(outcome(oracle.capture_public_epoch,argument))
    actual=rows(c.run('capture-conversion-errors',source,{'METTACLAW_PUBLIC_TOOL_RECEIPTS':'1',
        'METTACLAW_PUBLIC_WORK_DIR':''}))
    c.save('capture-conversion.json',{'actual':actual,'expected':expected})
    assert actual==expected

    # Actual dispatcher normalizes structured/atom returns before its hook.
    secret='generated-private-'+secrets.token_hex(24)+'-🙂\\nbody'
    root=c.directory/'batch-work';root.mkdir()
    (root/'state.json').write_text('{"enabled": true, "seq": 0, "epoch": 7}')
    path=c.directory/'batch-receipts.jsonl'
    source=IMPORTS+'!(import! &self ./tests/effect_receipts_caller_probe.py)\n!(py-call (effect_receipts_caller_probe.setup))\n'
    source+='!(import! &self ./src/api_registry.metta)\n!(import! &self ./src/command_dispatch.metta)\n'
    source+='''(CommandSpec 1 "fixture" "query" 1 1 "(query fixture)" "harmless generated return" "")
(= (query $value) (quote (answer True 3)))
'''
    source+='!(run-command-batch-once 41 5 (quote ((query %s))))\n'%json.dumps(secret,ensure_ascii=False)
    source+='!(println! (Summary (py-call (effect_receipts_caller_probe.summary))))\n'
    raw=c.run('actual-structured-result-batch',source,{'METTACLAW_EFFECT_RECEIPT_PATH':str(path),
        'METTACLAW_PUBLIC_TOOL_RECEIPTS':'1','METTACLAW_PUBLIC_WORK_DIR':str(root)})
    summary=json.loads(next(s[len('(Summary '):-1] for s in raw.splitlines() if s.startswith('(Summary ')))
    data=path.read_bytes();entry=json.loads(data);event=summary['events'][0]
    expected_path=c.directory/'oracle-batch.jsonl'
    with mock.patch.dict(os.environ,{**c.environment(),'METTACLAW_EFFECT_RECEIPT_PATH':str(expected_path)},clear=True),\
            mock.patch.object(oracle.time,'time',return_value=entry['recorded_at']),mock.patch.object(stream_sink,'receipt',return_value=1):
        assert oracle.record_command(41,'returned',event['command'],event['result'],entry['occurrence'])==1
    assert expected_path.read_bytes()==data and summary['counts']['stream']==1
    assert secret not in (root/'notes.jsonl').read_text()
    c.save('structured-result-batch.json',{'summary':summary,'entry':entry,'native_hex':data.hex(),
        'notes_hex':(root/'notes.jsonl').read_bytes().hex(),'generated_secret':secret,'oracle_hex':expected_path.read_bytes().hex(),
        'limit':'absence of generated literal sentinel, not digest-inference secrecy'})
    import public_worklog
    denied=c.directory/'denied-writer';denied.mkdir()
    non_directory=c.directory/'not-a-directory';non_directory.write_text('generated fixture')
    real_failures=[]
    try:
        denied.chmod(0)
        specs=[('receipt-directory','receipt',c.directory),
               ('receipt-parent-file','receipt',non_directory/'receipt'),
               ('receipt-denied','receipt',denied/'receipt'),
               ('worklog-root-file','status',non_directory),
               ('worklog-denied','status',denied/'child')]
        for label,action,path in specs:
            expression='(er:append (JsonObject ((JsonPair "fixture" True))))' if action=='receipt' else '(pw:transact %s "status" "" JsonNull)'%q(path)
            actual=rows(c.run(label,HEADER+WIRE+emit('(rt:wire %s)'%expression),
                {'METTACLAW_EFFECT_RECEIPT_PATH':str(path)}))[0]
            with mock.patch.dict(os.environ,{**c.environment(),'METTACLAW_EFFECT_RECEIPT_PATH':str(path)},clear=True):
                expected=outcome(oracle._append,{'fixture':True}) if action=='receipt' else outcome(public_worklog.transact,path,'status')
            real_failures.append({'case':label,'actual':actual,'expected':expected})
            assert actual[:2]==expected[:2] and actual[0]=='error'
    finally:
        denied.chmod(0o700)
    assert not any(denied.iterdir()) and non_directory.read_text()=='generated fixture'
    c.save('real-path-failures.json',real_failures)
    c.save('result.json',{'raw_occurrence_cases':len(fixtures),'postcommit_outcomes':4,
        'capture_conversion_errors':3,'actual_structured_return_batch':1,'stream_failure_receipts':len(fixtures),
        'real_path_failure_cases':len(real_failures)})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence',type=Path,required=True)
    a=p.parse_args();limit();assert sys.version_info[:3]==(3,12,13)
    a.evidence.mkdir(parents=True,exist_ok=False);(a.evidence/'runner.py').write_bytes(Path(__file__).read_bytes())
    with tempfile.TemporaryDirectory(prefix='effect05-outcomes-') as temporary:
        c=Qualification(a.evidence.resolve(),Path(temporary))
        with c.phase('raw-hook-and-postcommit-outcomes'):
            c.snapshot_sources('src/effect_receipts_cetta.metta','src/effect_receipts_native.metta',
                'src/command_dispatch_hooks.metta','tests/effect_receipts_caller_probe.py')
            check(c)


if __name__=='__main__':main()
