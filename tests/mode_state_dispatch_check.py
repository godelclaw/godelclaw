"""Actual skill equation and dispatcher presentation, without skill boot effects."""
import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile

from effect_receipts_support import IMPORTS, ROOT, emit, limit, rows
from mode_state_callers_check import CONTROL
from mode_state_support import Qualification


def check(c):
    with c.phase('skill-dispatch-error-presentation'):
        skill = next(line for line in (ROOT/'src/skills.metta').read_text().splitlines()
                     if line.startswith('(= (mode-set $name)'))
        assert skill == '(= (mode-set $name) (mode:set $name))'
        fixture=c.directory/'dispatch_fixture.py';fixture.write_text(CONTROL)
        (c.evidence/fixture.name).write_bytes(fixture.read_bytes())
        root=c.directory/'state'
        outputs=[]
        for lane in ('python','native'):
            if root.exists():shutil.rmtree(root)
            root.mkdir();(root/'wake').mkdir()
            (root/'loop').write_text('{"mode":"godelclaw"}')
            env={'MODE_STATE_POISON':'1' if lane=='native' else '0',
                 'PYTHONPATH':str(c.directory)+os.pathsep+c.environment()['PYTHONPATH'],
                 **{'METTACLAW_'+key:str(root/name) for key,name in
                    (('LOOP_MODE_PATH','loop'),('RECYCLE_REQUEST_PATH','recycle'),('WAKE_REQUEST_PATH','wake'))}}
            source=IMPORTS+'!(import! &self %s)\n'%fixture
            source+='!(import! &self ./src/mode_state.metta)\n!(import! &self ./src/command_dispatch_hooks.metta)\n'
            if lane=='python':
                source+='!(match &self (= (mode:set $name) $prior) (remove-atom &self (= (mode:set $name) $prior)))\n'
                source+='(= (mode:set $name) (py-call (loop_modes.set_mode $name)))\n'
            source+=skill+'\n'+emit('(dispatch:normalize (catch (mode-set "coding")))')
            output=c.run('dispatch-'+lane,source,env);values=rows(output)
            assert len(values)==1,output
            outputs.append(values[0])
        c.save('result.json',{'outputs':outputs,'skill_equation':skill,
               'boundary':'actual authored skill equation and actual dispatch:normalize; no full skills/bootstrap initializer'})
        assert outputs[0]==outputs[1],outputs


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence',type=Path,required=True)
    args=p.parse_args();limit();args.evidence.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='mode06-dispatch-') as directory:
        c=Qualification(args.evidence.resolve(),Path(directory))
        c.snapshot_sources('tests/mode_state_dispatch_check.py','src/command_dispatch_hooks.metta','src/skills.metta')
        check(c)


if __name__=='__main__':main()
