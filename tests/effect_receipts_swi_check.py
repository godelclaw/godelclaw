"""Disposable package, explicit SWI oracle route and shared Python consumers."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest import mock

from effect_receipts_native_check import HEADER
from effect_receipts_support import Qualification,ROOT,emit,limit,q,rows


def check(c):
    stage=c.directory/'package';stage.mkdir()
    selected=json.loads((ROOT/'config/runtime-files.json').read_text())
    packaged={}
    for name in selected:
        source=ROOT/name
        assert source.is_file(),name
        target=stage/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        packaged[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    probe=stage/'tests/effect_receipts_swi_probe.py';probe.parent.mkdir();shutil.copy2(ROOT/'tests/effect_receipts_swi_probe.py',probe)
    petta=Path(os.environ.get('EFFECT_RECEIPTS_SWI_ROOT',str(Path.home()/'repos/PeTTa')))
    assert (petta/'src/main.pl').is_file()
    executable=os.environ.get('EFFECT_RECEIPTS_SWIPL') or shutil.which('swipl',path=c.environment()['PATH'])
    assert executable,'set EFFECT_RECEIPTS_SWIPL to the installed SWI executable'
    receipt=c.directory/'swi-receipts.jsonl';root=c.directory/'public-work'
    result=c.directory/'swi-observation.json'
    literal=lambda value:json.dumps(str(value),ensure_ascii=False)
    program=stage/'probe.metta'
    program.write_text('!(import! &self (library lib_import))\n'
        '!(import! &self ./src/effect_receipts.metta)\n'
        '!(import! &self ./tests/effect_receipts_swi_probe.py)\n'
        '!(py-call (effect_receipts_swi_probe.setup))\n'
        '!(effect-receipts:record-command 7 returned (quote (shell "fixture")) "r" "swi-explicit")\n'
        '!(effect-receipts:record-suffix 7 withheld (quote ((shell "not-run"))) "fixture suffix")\n'
        '!(public-worklog:transact '+literal(root)+' "on" "" 0)\n'
        '!(import! &self ./src/command_dispatch.metta)\n'
        '(= (query $value) "harmless SWI return")\n'
        '!(println! (run-command-batch-once 8 5 (quote ((query "fixture")))))\n'
        '!(let $view (effect-receipts:view) (py-call (effect_receipts_swi_probe.record '+literal(result)+' $view)))\n')
    (c.evidence/'swi-probe.metta').write_bytes(program.read_bytes())
    env=c.environment();env.update(METTACLAW_ENGINE='petta',METTACLAW_ACTIVE_ENGINE='petta',
        PETTA_ROOT=str(petta),METTACLAW_SKIP_CONFIG='1',METTACLAW_SKIP_INITIALIZE='1',
        PYTHONPATH=os.pathsep.join((str(stage/'src'),str(stage/'tests'),str(stage/'channels'))),
        METTACLAW_EFFECT_RECEIPT_PATH=str(receipt))
    command=[executable,'--stack_limit=8g','-q','-s',str(petta/'src/main.pl'),'--',str(program)]
    child=subprocess.run(command,cwd=stage,env=env,capture_output=True,timeout=90)
    c.record_process('actual-swi-package',command,child,env)
    c.save('swi-files.json',{'receipt_hex':receipt.read_bytes().hex() if receipt.exists() else None,
                          'result':result.read_text() if result.exists() else None})
    assert child.returncode==0,child.stderr
    assert result.exists(),child.stdout.decode(errors='backslashreplace')
    observed=json.loads(result.read_text());entries=[json.loads(line) for line in receipt.read_bytes().splitlines()]
    assert observed['counts']['record_command']==2 and observed['counts']['record_suffix']==1,observed
    assert len(entries)==3 and entries[0]['occurrence']=='swi-explicit'
    assert entries[-1]['disposition']=='returned' and entries[-1]['result']=='harmless SWI return',entries[-1]
    assert 'AUTHORITATIVE BROKER RECEIPTS:' in observed['view']
    # Native writes continue the exact SWI worklog state and receipt inode.
    source=HEADER+'!(import! &self ./tests/effect_receipts_caller_probe.py)\n!(py-call (effect_receipts_caller_probe.setup))\n'
    source+=emit('(pw:transact %s "add" "native-after-swi" 1)'%q(root))
    source+=emit('(er:append (JsonObject ((JsonPair "schema" 1) (JsonPair "disposition" "returned") (JsonPair "command" "native fixture") (JsonPair "result" "r"))))')
    actual=rows(c.run('native-after-swi',source,{'METTACLAW_EFFECT_RECEIPT_PATH':str(receipt)}))
    assert actual[0]['accepted'] and actual[1]==1,actual
    sys.path.insert(0,str(ROOT/'src'))
    import model_commentary,runtime_context
    with mock.patch.dict(os.environ,{**c.environment(),'METTACLAW_EFFECT_RECEIPT_PATH':str(receipt)},clear=True):
        publication=model_commentary.Publication()
        assert publication.publish('python-commentary-after-native')==1
        reader=runtime_context._commands()
    notes=[json.loads(line) for line in (root/'notes.jsonl').read_bytes().splitlines()]
    assert [n['text'] for n in notes]==['native-after-swi','python-commentary-after-native']
    assert [n['seq'] for n in notes]==[1,2]
    assert reader['returned']==3 and reader['withheld']==1 and reader['windowRecords']==4,reader
    c.save('shared-consumers.json',{'runtime_context':reader,'notes':notes,
        'files':{name:(root/name).read_bytes().hex() for name in ('state.json','notes.jsonl','lock')},
        'final_receipt_hex':receipt.read_bytes().hex()})
    c.save('package-membership.json',packaged)
    c.save('result.json',{'swi_oracle_records':3,'actual_prolog_batch':True,
        'native_after_swi':True,'python_commentary_after_native':True,'runtime_context_reader':True})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence',type=Path,required=True)
    a=p.parse_args();limit();assert sys.version_info[:3]==(3,12,13)
    a.evidence.mkdir(parents=True,exist_ok=False);(a.evidence/'runner.py').write_bytes(Path(__file__).read_bytes())
    with tempfile.TemporaryDirectory(prefix='effect05-swi-') as temporary:
        c=Qualification(a.evidence.resolve(),Path(temporary))
        with c.phase('swi-package-and-shared-consumers'):
            c.snapshot_sources('src/effect_receipts_python.metta','src/effect_receipts.metta',
                'src/command_dispatch_hooks.metta','tests/effect_receipts_swi_probe.py','config/runtime-files.json')
            check(c)


if __name__=='__main__':main()
