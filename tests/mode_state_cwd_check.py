"""Actual long cwd with minimal pure-native imports; safe-root phase guards."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from effect_receipts_support import emit, limit, rows
from mode_state_support import Qualification


def check(c):
    with c.phase('actual-long-cwd-capability'):
        root=c.directory/'deep';root.mkdir()
        parts=['level-%02d-'%n+'x'*70 for n in range(60)]
        fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
        descriptors=[fd]
        try:
            for part in parts:
                os.mkdir(part,dir_fd=fd)
                fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY,dir_fd=fd);descriptors.append(fd)

            def inventory():
                result=[]
                for n,d in enumerate(descriptors):
                    os.lseek(d,0,os.SEEK_SET)
                    result.append({'level':n,'mode':oct(os.fstat(d).st_mode&0o7777),
                                   'entries':sorted(os.listdir(d))})
                return result

            before=inventory()
            c.save('tree-START.json',before)

            def enter():
                limit()
                for part in parts:os.chdir(part)

            env=c.environment()
            oracle=[sys.executable,'-c','import json,os; print(json.dumps({"cwd":os.getcwd()}))']
            native=c.directory/'cwd.metta'
            native.write_text('!(println! BeforeCwd)\n!(import! &self (library system))\n'
                '!(import! &self (library json))\n'+
                emit('(JsonArray ((system:getcwd) (system:cwd)))')+
                '!(println! (Descriptor (library system)))\n'
                '!(println! (JsonDescriptor (library json)))\n'
                '!(println! (Literal (Error fixture literal-value)))\n')
            (c.evidence/'cwd.metta').write_bytes(native.read_bytes())
            command=[str(c.binary),'--lang','petta','--import-mode','ancestor-walk',str(native)]
            outputs={}
            for label,argv in (('python',oracle),('native',command)):
                child=subprocess.run(argv,cwd=root,env=env,capture_output=True,timeout=30,preexec_fn=enter)
                c.record_process('long-cwd-'+label,argv,child,env,cwd=root)
                outputs[label]={'exit':child.returncode,'stdout':child.stdout.decode(errors='backslashreplace'),
                                'stderr':child.stderr.decode(errors='backslashreplace')}
            expected=str(root)+'/'+('/'.join(parts))
            assert outputs['python']['exit']==0
            assert json.loads(outputs['python']['stdout'])['cwd']==expected
            assert len(expected)>os.pathconf(root,'PC_PATH_MAX')
            after=inventory();c.save('tree-END.json',after)
            actual=rows(outputs['native']['stdout'])
            assert outputs['native']['exit']==0 and actual==[[expected,expected]],outputs
            for label in ('Descriptor','JsonDescriptor'):
                import re
                match=re.search(r'\('+label+r' ([^\n]+)\)',outputs['native']['stdout']);assert match,outputs
                path=Path(match[1].strip('"')+'.metta').resolve()
                relative=path.relative_to(c.bundle).as_posix()
                assert relative in c._phase_start['library_files']
                import hashlib
                assert hashlib.sha256(path.read_bytes()).hexdigest()==dict(c._phase_start['files'])[relative]
            c.save('cwd-result.json',{'status':'PASS_NATIVE_GETCWD_LONG_SUCCESS',
                   'full_project06_complete':False,'cwd_characters':len(expected),'cwd':expected,
                   'child_chdir_components':parts,'process_initial_cwd':str(root),'outputs':outputs,
                   'tree_unchanged':before==after,
                   'limits':'minimal system/json native imports only in deep cwd, no lib_import/embedded SWI/Python fixture/bootstrap. Normal all-library guards run at safe package ROOT. No state call, cwd deletion, parent chdir, injection or process control.'})
            assert before==after,(before,after)
        finally:
            for n in range(len(parts)-1,-1,-1):
                os.close(descriptors[n+1]);os.rmdir(parts[n],dir_fd=descriptors[n])
            os.close(descriptors[0]);root.rmdir()
            c.save('cleanup.json',{'owned_descriptors_closed':len(descriptors),
                                  'owned_directories_removed':len(parts)+1,'root_absent':not root.exists()})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence',type=Path,required=True)
    args=p.parse_args();limit();args.evidence.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='mode06-cwd-') as directory:
        c=Qualification(args.evidence.resolve(),Path(directory))
        c.snapshot_sources('tests/mode_state_cwd_check.py','src/lifecycle_cetta.metta')
        check(c)


if __name__=='__main__':main()
