"""Bounds, epoch changes, Unicode pre-open effects, plugins and native IDs."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest import mock
import uuid

from effect_receipts_native_check import HEADER, WIRE, value, outcome
from effect_receipts_callers_check import FREEZE
from effect_receipts_primitives2 import text
from effect_receipts_support import IMPORTS, Qualification, ROOT, emit, limit, q, rows


def bounds(c):
    source=HEADER+'''
!(match &self (= (er:sink-status) $body) (remove-atom &self (= (er:sink-status) $body)))
(= (er:sink-status) (ErSinkStatus True 7))
!(match &self (= (er:sink-add $text $epoch) $body) (remove-atom &self (= (er:sink-add $text $epoch) $body)))
(= (er:sink-add $text $epoch) 1)
'''
    source+=emit('(foldl-atom (list:range 70) 0 $total $i (+ $total (er:capture $i)))')
    source+=emit('(JsonArray ((size-atom (get-state &er-captured)) (JsonArray (car-atom (get-state &er-captured)))))')
    source+=emit('(er:capture 10)')
    source+=emit('(JsonArray (index-atom (get-state &er-captured) 63))')
    source+='!(change-state! &er-captured ((3 7)))\n'
    source+=emit('(foldl-atom (list:range 1026) 0 $total $i (+ $total (er:publish 3 "returned" (JsonArray ("shell" "private")) "private" 0 (str:concat "bound:" (repr $i)))))')
    source+=emit('(size-atom (get-state &er-published))')
    source+=emit('(er:publish 3 "returned" (JsonArray ("shell")) "r" 0 "bound:0")')
    source+=emit('(er:publish 3 "returned" (JsonArray ("shell")) "r" 0 "bound:1025")')
    actual=rows(c.run('capture-and-dedupe-bounds',source,{'METTACLAW_PUBLIC_TOOL_RECEIPTS':'1'}))
    c.save('bounds.json',{'actual':actual})
    assert actual==[70,[64,[6,7]],1,[10,7],1026,1024,1,0],actual
    root=c.directory/'epoch-work';root.mkdir();(root/'state.json').write_text('{"enabled":true,"seq":0,"epoch":7}')
    source=HEADER
    for expr in ['(er:capture 3)', '(pw:transact %s "off" "" JsonNull)'%q(root),
                 '(pw:transact %s "on" "" JsonNull)'%q(root),
                 '(er:publish 3 "returned" (JsonArray ("shell")) "r" 0 "epoch-id")',
                 '(er:capture 3)', '(er:publish 3 "returned" (JsonArray ("shell")) "r" 0 "epoch-id")']:
        source+=emit(expr)
    actual=rows(c.run('capture-work-publication-epoch',source,
        {'METTACLAW_PUBLIC_TOOL_RECEIPTS':'1','METTACLAW_PUBLIC_WORK_DIR':str(root)}))
    c.save('epoch.json',{'actual':actual,'notes_hex':(root/'notes.jsonl').read_bytes().hex()})
    assert actual[0]==1 and actual[3:]==[0,1,1],actual
    assert json.loads((root/'state.json').read_text())=={'enabled':True,'seq':1,'epoch':9}
    numeric=[]
    for i,(epoch,accepted) in enumerate(((True,1),(7.0,1),('7',0),(-0.0,1),(float('nan'),0))):
        root=c.directory/('numeric-epoch-%d'%i);root.mkdir()
        (root/'state.json').write_text(json.dumps({'enabled':True,'seq':0,'epoch':epoch}))
        source=HEADER+emit('(er:capture 3)')+emit('(er:publish 3 "returned" (JsonArray ("shell")) "r" 0 "numeric-epoch")')
        actual=rows(c.run('numeric-epoch-%d'%i,source,
            {'METTACLAW_PUBLIC_TOOL_RECEIPTS':'1','METTACLAW_PUBLIC_WORK_DIR':str(root)}))
        assert actual==[0 if i==4 else 1,accepted],actual
        numeric.append({'epoch_json':json.dumps(epoch),'actual':actual,
            'state_hex':(root/'state.json').read_bytes().hex(),
            'notes_hex':(root/'notes.jsonl').read_bytes().hex() if (root/'notes.jsonl').exists() else None})
    c.save('numeric-epochs.json',numeric)


def encoding(c):
    sys.path.insert(0,str(ROOT/'src'));import effect_receipts as oracle
    inputs=['A\ud800B','🙂\ud800\udfffB','A\0🙂B','🙂'*6001,'x'*5999+'\ud800\udfff']
    records=[]
    for i,result in enumerate(inputs):
        path=c.directory/('encoding-%d'%i)/'receipt.jsonl'
        source=HEADER+'!(import! &self ./tests/effect_receipts_caller_probe.py)\n!(py-call (effect_receipts_caller_probe.setup))\n'+FREEZE+WIRE
        source+=emit('(rt:wire (er:record 3 returned (quote (shell "fixture")) %s "encoding-id"))'%text(result))
        actual=rows(c.run('strict-encoding-%d'%i,source,{'METTACLAW_EFFECT_RECEIPT_PATH':str(path)}))
        expected_path=c.directory/('oracle-encoding-%d'%i)/'receipt.jsonl'
        env={**c.environment(),'METTACLAW_EFFECT_RECEIPT_PATH':str(expected_path)}
        import stream_sink
        with mock.patch.dict(os.environ,env,clear=True),mock.patch.object(oracle.time,'time',return_value=1704067200.125),mock.patch.object(stream_sink,'receipt',return_value=1):
            expected=outcome(oracle.record_command,3,'returned',['shell','fixture'],result,'encoding-id')
        effects=[p.read_bytes().hex() if p.exists() else None for p in (path,expected_path)]
        records.append({'points':[ord(ch) for ch in result],'actual':actual,'expected':expected,
                        'file_bytes':effects,'parent_created':path.parent.is_dir()})
        c.save('encoding.json',records)
        assert actual==[expected] and effects[0]==effects[1] and path.parent.is_dir(),i
    # Retained Python reader and raw-tail behavior, including malformed bytes,
    # Unicode splitlines and surrogate-bearing parsed values (safe JSON wire).
    path=c.directory/'tail.jsonl'
    payload=(b'bad'*70000+b'\xf0\x9f\n'+
        b'{"turn": 1, "command": "fixture", "result": "\\ud800"}\n'+
        json.dumps({'turn':2,'command':'x\u2028y','result':'r'},ensure_ascii=False).encode()+b'\n'+
        b'{"schema":1,"disposition":"withheld","commands":["shell"],"reason":"fixture"}\n')
    for index,malformed in enumerate((b'\xff',b'\xed\xa0\x80',b'\xf0\x80\x80\x80',b'\xf0\x9f',b'\xe2\x82X')):
        payload+=b'{"turn":'+str(index+10).encode()+b',"command":"fixture","result":"A'+malformed+b'B"}\n'
    path.write_bytes(payload)
    actual=rows(c.run('tail-surrogate-splitlines',HEADER+WIRE+emit('(rt:wire (er:view 18))'),
                      {'METTACLAW_EFFECT_RECEIPT_PATH':str(path)}))
    with mock.patch.dict(os.environ,{**c.environment(),'METTACLAW_EFFECT_RECEIPT_PATH':str(path)},clear=True):
        expected=outcome(oracle.view)
    c.save('tail.json',{'input_hex':payload.hex(),'actual':actual,'expected':expected})
    assert actual==[expected]


def plugin(c):
    library=c.directory/'plugin';library.mkdir()
    (library/'worklog.py').write_text('''import os
from pathlib import Path
import public_worklog
with open(os.environ["EFFECT_TEST_PLUGIN_TRACE"],"a") as stream: stream.write("load\\n")
def transact(root,action,text="",expected_epoch=None):
    with open(os.environ["EFFECT_TEST_PLUGIN_TRACE"],"a") as stream: stream.write(action+"\\n")
    fail=Path(root)/"fail-once"
    if action=="add" and fail.exists():
        fail.unlink()
        raise RuntimeError("fixture plugin failed once")
    return public_worklog.transact(root,action,text,expected_epoch)
''')
    root=c.directory/'plugin-work';root.mkdir()
    (root/'state.json').write_text('{"enabled":true,"seq":0,"epoch":7}')
    (root/'fail-once').touch()
    trace=c.directory/'plugin-trace'
    source=HEADER+'!(import! &self ./tests/effect_receipts_caller_probe.py)\n!(py-call (effect_receipts_caller_probe.setup))\n'
    source+='''
!(match &self (= (er:sink-add $text $epoch) $body)
 (progn (add-atom &self (= (pt:original $text $epoch) $body))
        (remove-atom &self (= (er:sink-add $text $epoch) $body))))
!(bind! &pt-nested (new-state -1))
(= (er:sink-add $text $epoch)
 (let $_ (change-state! &pt-nested (er:publish 3 "returned" (JsonArray ("shell")) "r" 0 "nested"))
  (pt:original $text $epoch)))
'''
    for expr in ['(er:capture 3)', '(er:publish 3 "returned" (JsonArray ("shell" "private")) "private" 0 "plugin-id")',
        '(JsonArray ((get-state &er-reporting) (size-atom (get-state &er-in-flight)) (size-atom (get-state &er-published))))',
        '(er:publish 3 "returned" (JsonArray ("shell" "private")) "private" 0 "plugin-id")',
        '(er:publish 3 "returned" (JsonArray ("shell" "private")) "private" 0 "plugin-id")',
        '(get-state &pt-nested)']:
        source+=emit(expr)
    actual=rows(c.run('configured-plugin-failure-callback-retry',source,
        {'METTACLAW_PUBLIC_TOOL_RECEIPTS':'1','METTACLAW_PUBLIC_WORK_DIR':str(root),
         'METTACLAW_PUBLIC_WORK_LIB':str(library),'EFFECT_TEST_PLUGIN_TRACE':str(trace)}))
    c.save('plugin.json',{'actual':actual,'trace':trace.read_text(),
        'files':{p.name:p.read_bytes().hex() for p in root.iterdir() if p.is_file()},
        'boundary':'native callback wrapper reenters around selected Python plugin call; not arbitrary Python-to-evaluator reentry'})
    assert actual==[1,0,[False,0,0],1,0,0],actual
    assert trace.read_text().splitlines()==['load','status','load','add','load','add']


def paths(c):
    sys.path.insert(0,str(ROOT/'src'))
    import effect_receipts as oracle
    import public_worklog
    cases=[]
    for root in (None,3,True,[],{},'a\0b'):
        with mock.patch.dict(os.environ,c.environment(),clear=True):
            expected=outcome(public_worklog.transact,root,'status')
        cases.append(('(pw:transact %s "status" "" JsonNull)'%value(root),expected))
    actual=rows(c.run('worklog-root-validation',HEADER+WIRE+''.join(
        emit('(rt:wire %s)'%expr) for expr,_ in cases)))
    c.save('root-validation.json',{'actual':actual,'expected':[e for _,e in cases]})
    assert actual==[e for _,e in cases]
    # Real denied-path effects (not simulated existence booleans).
    denied=c.directory/'denied';denied.mkdir();(denied/'receipt').write_text('fixture')
    loop=c.directory/'loop';loop.symlink_to(loop.name)
    observations=[]
    try:
        denied.chmod(0)
        for i,path in enumerate((c.directory/'absent',c.directory,loop,denied/'receipt')):
            settings={'METTACLAW_EFFECT_RECEIPT_PATH':str(path)}
            actual=rows(c.run('view-path-%d'%i,HEADER+WIRE+emit('(rt:wire (er:view 18))'),settings))[0]
            with mock.patch.dict(os.environ,{**c.environment(),**settings},clear=True):
                expected=outcome(oracle.view)
            observations.append({'actual':actual,'expected':expected})
            assert (actual==expected if expected[0]=='value' else actual[:2]==expected[:2])
        assert observations[-1]['actual'][:2]==['error','PermissionError'], 'qualification requires an unprivileged process'
    finally:
        denied.chmod(0o700)
    c.save('view-paths.json',observations)
    root=c.directory/'facade'
    source=IMPORTS+'!(import! &self ./src/effect_receipts_native.metta)\n'+WIRE
    for expr in ('(public-worklog:transact %s on)'%q(root),
                 '(public-worklog:transact %s add (quote (fixture True)))'%q(root),
                 '(public-worklog:transact %s add "not retained" JsonNull)'%q(root)):
        source+=emit('(rt:wire %s)'%expr)
    actual=rows(c.run('worklog-facade-defaults-and-crossings',source))
    c.save('facade-defaults.json',{'actual':actual,'notes_hex':(root/'notes.jsonl').read_bytes().hex(),
        'state_hex':(root/'state.json').read_bytes().hex()})
    assert actual[0][1]=={'enabled':True,'seq':0,'epoch':1}
    assert actual[1][1]=={'enabled':True,'seq':1,'epoch':1,'accepted':True}
    assert actual[2][1]=={'accepted':False,'reason':'sharing epoch changed'}
    assert json.loads((root/'notes.jsonl').read_text())['text']=="['fixture', 'true']"


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence',type=Path,required=True)
    a=p.parse_args();limit();assert sys.version_info[:3]==(3,12,13)
    a.evidence.mkdir(parents=True,exist_ok=False);(a.evidence/'runner.py').write_bytes(Path(__file__).read_bytes())
    with tempfile.TemporaryDirectory(prefix='effect05-edges-') as temporary:
        c=Qualification(a.evidence.resolve(),Path(temporary))
        with c.phase('receipt-state-edges'):
            c.snapshot_sources('src/effect_receipts_cetta.metta','src/effect_receipt_values_cetta.metta',
                'src/effect_receipts_plugin.py','tests/effect_receipts_caller_probe.py')
            bounds(c);encoding(c);plugin(c);paths(c)
            ids=rows(c.run('native-uuid-samples',HEADER+emit('(JsonArray (map-atom (list:range 500) $i (system:uuid4-hex)))')))[0]
            assert len(ids)==len(set(ids))==500
            assert all(len(v)==32 and v==v.lower() and uuid.UUID(hex=v).version==4 and v[16] in '89ab' for v in ids)
            c.save('uuid.json',{'samples':ids,'format_variant_unique':True})
            c.save('result.json',{'capture_limit':64,'dedupe_limit':1024,'encoding_cases':5,
                'epoch_change':True,'configured_plugin_retry_callback':True,'native_uuids':500,
                'root_validation':6,'view_path_cases':4,'facade_default_and_crossing_cases':3})


if __name__=='__main__':main()
