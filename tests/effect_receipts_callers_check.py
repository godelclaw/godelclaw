"""Real isolated dispatcher hooks/context caller, ported Python disabled."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid
from types import SimpleNamespace
from unittest import mock

from effect_receipts_native_check import HEADER, WIRE
from effect_receipts_support import IMPORTS, Qualification, emit, limit, q, rows


FREEZE = '''
!(match &self (= (system:wall-time) $body) (remove-atom &self (= (system:wall-time) $body)))
(= (system:wall-time) 1704067200.125)
!(match &self (= (system:uuid4-hex) $body) (remove-atom &self (= (system:uuid4-hex) $body)))
!(bind! &ct-uuid (new-state 0))
(= (system:uuid4-hex)
 (let* (($next (+ (get-state &ct-uuid) 1)) ($_ (change-state! &ct-uuid $next)))
  (str:concat "fixture-uuid-" (repr $next))))
'''


def check(c):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    import effect_receipts as oracle
    contents = 'generated harmless file🙂\nsecond line\n'
    input_path = c.directory / 'fixture.txt'
    input_path.write_text(contents)
    root = c.directory / 'public-work'
    root.mkdir()
    records = []
    for strategy in ('0','1'):
        receipt = c.directory / ('receipts-%s.jsonl'%strategy)
        (root/'state.json').write_text('{"enabled": true, "seq": 0, "epoch": 7}')
        (root/'notes.jsonl').unlink(missing_ok=True)
        source = IMPORTS + '!(import! &self ./tests/effect_receipts_caller_probe.py)\n'
        source += '!(py-call (effect_receipts_caller_probe.setup))\n'
        source += '!(import! &self ./src/api_registry.metta)\n!(import! &self ./src/file_io.metta)\n'
        source += '!(import! &self ./src/command_dispatch.metta)\n'
        literal = json.dumps(str(input_path))
        batch = '(run-command-batch-once 123 5 (quote ((read-file %s) (read-lines %s 1 2))))' % (literal,literal)
        source += '!(println! (Batch %s))\n!(println! (CachedBatch %s))\n' % (batch,batch)
        source += '!(import! &self ./src/context_sources_cetta.metta)\n'
        source += '!(println! (ImportOwner (size-atom (get-state &er-published)) (get-state &er-captured)))\n'
        source += emit('(context-bundle:with-views ())')
        source += '!(println! (ContextRaw (collapse (catch (ctx:build (py-call (context_source_loaders.prepare ())))))))\n'
        source += '!(println! (ProbeSummary (json:stringify-python (json:parse-python (str:symbol->string (py-call (effect_receipts_caller_probe.summary)))))))\n'
        before=time.time()
        raw = c.run('actual-dispatcher-strategy-'+strategy, source,
            {'METTACLAW_OBSERVED_STRATEGY':strategy,'METTACLAW_PUBLIC_TOOL_RECEIPTS':'1',
             'METTACLAW_EFFECT_RECEIPT_PATH':str(receipt)})
        after=time.time()
        files = {name: (root/name).read_bytes().hex() if (root/name).exists() else None
                 for name in ('state.json','notes.jsonl','lock')}
        data = receipt.read_bytes() if receipt.exists() else b''
        c.save('caller-%s-files.json'%strategy, {'receipt_hex':data.hex(),'worklog':files})
        # py-call string returns are foreign atoms; repr is a JSON quoted wire.
        line = next(line for line in raw.splitlines() if line.startswith('(ProbeSummary '))
        summary = json.loads(json.loads(line[len('(ProbeSummary '):-1]))
        entries = [json.loads(line) for line in data.splitlines()]
        bundle = rows(raw)
        records.append({'strategy':strategy,'summary':summary,'entries':entries,'bundle':bundle})
        c.save('caller-results.json', records)
        assert summary['counts'].get('forbidden',0)==0, summary
        assert '(ImportOwner 2 ((123 7)))' in raw,'duplicate/changed native publication owner after context import'
        assert summary['counts']['stream']==2 and len(entries)==2, summary
        assert all(before <= e['recorded_at'] <= after for e in entries)
        if strategy=='0':
            assert len({e['occurrence'] for e in entries})==2
            assert all(uuid.UUID(hex=e['occurrence']).version==4 for e in entries)
        assert summary['events'][0]['command']==['read-file',str(input_path)]
        assert summary['events'][0]['result']==contents
        assert [e['receipt_lines'] for e in summary['events']]==[1,2]
        assert all(e['notes'] for e in summary['events']), 'public projection must precede stream'
        assert all(str(input_path) not in e['notes'] and contents not in e['notes'] for e in summary['events'])
        if strategy=='1':
            assert summary['counts']['strategy_result']==summary['counts']['strategy_identity']==2
            assert 'Task receipts are in TASK_CONTEXT.' in bundle[0]
        else:
            assert summary['counts'].get('strategy_result',0)==0
            assert 'AUTHORITATIVE BROKER RECEIPTS:' in bundle[0] and 'command=' in bundle[0]
        # Exact oracle JSONL bytes at the same actual raw hook shapes, with only
        # fixture clock/identity/stream/publication selected for comparison.
        expected_path=c.directory/('oracle-%s.jsonl'%strategy)
        env={**c.environment(),'METTACLAW_OBSERVED_STRATEGY':'0',
             'METTACLAW_PUBLIC_TOOL_RECEIPTS':'0','METTACLAW_EFFECT_RECEIPT_PATH':str(expected_path)}
        with mock.patch.dict(os.environ,env,clear=True), mock.patch.object(oracle,'_publish_public_event',return_value=0):
            import stream_sink
            with mock.patch.object(stream_sink,'receipt',return_value=1):
                for entry,event in zip(entries,summary['events']):
                    with mock.patch.object(oracle.time,'time',return_value=entry['recorded_at']):
                        assert oracle.record_command(123,'returned',event['command'],event['result'],entry['occurrence'])==1
        assert expected_path.read_bytes()==data
    c.save('result.json',{'real_dispatcher_batches':2,'commands':4,'cached_batches':2,
                         'context_bundle_callers':2,'ported_python_calls':0})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',type=Path,required=True)
    args=parser.parse_args();limit()
    assert sys.version_info[:3]==(3,12,13)
    args.evidence.mkdir(parents=True,exist_ok=False)
    (args.evidence/'runner.py').write_bytes(Path(__file__).read_bytes())
    with tempfile.TemporaryDirectory(prefix='effect05-callers-') as temporary:
        c=Qualification(args.evidence.resolve(),Path(temporary))
        with c.phase('actual-receipt-callers'):
            c.snapshot_sources('src/command_dispatch_hooks.metta','src/context_sources_cetta.metta',
                'src/effect_receipts_cetta.metta','src/effect_receipts.metta','src/effect_receipts_native.metta',
                'src/effect_receipts_plugin.py','tests/effect_receipts_caller_probe.py')
            check(c)


if __name__=='__main__':main()
