"""Paired mkstemp path/owner/collision fixtures, with unmodified candidate RNG."""
import argparse
import errno
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from effect_receipts_support import IMPORTS, emit, limit, q, rows
from mode_state_callers_check import stage
from mode_state_modules_check import poison
from mode_state_native_check import CAPTURE, SNAPSHOT, captured, python, umask
from mode_state_protocol_check import trace_source
from mode_state_support import Qualification


ORACLE='''import contextlib,errno,io,json,os,tempfile
from pathlib import Path
from unittest.mock import patch
import lifecycle
'''+SNAPSHOT+'''
trace=[];fdpaths={};attempts=0;depth=0
real_open=os.open;real_write=os.write;real_close=os.close;real_fchmod=os.fchmod
real_fdopen=os.fdopen;real_sync=os.fsync;real_replace=os.replace;real_unlink=os.unlink
real_mkdir=Path.mkdir;real_cwd=os.getcwd;real_names=tempfile._get_candidate_names
def names():
    for name in real_names():
        trace.append(['candidate',name]);yield name
def mkdir(self,*args,**kwargs):
    global depth
    if depth==0:trace.append(['mkdir',str(self)])
    depth+=1
    try:return real_mkdir(self,*args,**kwargs)
    finally:depth-=1
def cwd():
    trace.append(['getcwd',''])
    if CASE_VALUE in ('cwd-error','absolute-no-cwd'):raise PermissionError(errno.EACCES,os.strerror(errno.EACCES))
    return real_cwd()
def opened(path,flags,mode=0o777,*args,**kwargs):
    global attempts
    attempts+=1;trace.append(['open',str(path)])
    if CASE_VALUE=='collision-success' and attempts==1:
        seed=real_open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        try:real_write(seed,b'collision fixture\\n');real_fchmod(seed,0o604)
        finally:real_close(seed)
    if CASE_VALUE=='threshold-conflict':
        if attempts<=10000:raise FileExistsError(errno.EEXIST,os.strerror(errno.EEXIST),str(path))
        raise PermissionError(errno.EACCES,os.strerror(errno.EACCES),str(path))
    if CASE_VALUE=='open-denied':raise PermissionError(errno.EACCES,os.strerror(errno.EACCES),str(path))
    fd=real_open(path,flags,mode,*args,**kwargs);fdpaths[fd]=str(path);return fd
def fchmod(fd,mode):trace.append(['fchmod',fdpaths[fd]]);return real_fchmod(fd,mode)
def sync(fd):
    trace.append(['sync',fdpaths[fd]])
    if CASE_VALUE=='sync-exists':raise FileExistsError(errno.EEXIST,os.strerror(errno.EEXIST))
    return real_sync(fd)
class Writer:
    def __init__(self,stream,path):self.stream=stream;self.path=path;self.written=False
    def __enter__(self):self.stream.__enter__();return self
    def write(self,text):
        if not self.written:trace.append(['write',self.path]);self.written=True
        if CASE_VALUE=='write-exists':raise FileExistsError(errno.EEXIST,os.strerror(errno.EEXIST))
        return self.stream.write(text)
    def flush(self):return self.stream.flush()
    def fileno(self):return self.stream.fileno()
    def __exit__(self,*args):
        value=self.stream.__exit__(*args);trace.append(['close',self.path])
        if CASE_VALUE=='close-exists':raise FileExistsError(errno.EEXIST,os.strerror(errno.EEXIST))
        return value
def fdopen(fd,*args,**kwargs):return Writer(real_fdopen(fd,*args,**kwargs),fdpaths[fd])
def replace(source,target):
    trace.append(['rename',str(source)])
    if CASE_VALUE=='rename-exists':raise FileExistsError(errno.EEXIST,os.strerror(errno.EEXIST),str(source),None,str(target))
    return real_replace(source,target)
def unlink(path,*args,**kwargs):trace.append(['unlink',str(path)]);return real_unlink(path,*args,**kwargs)
printed=io.StringIO()
with patch.object(Path,'mkdir',mkdir),patch('os.getcwd',cwd),patch('tempfile._get_candidate_names',names),\
     patch('os.open',opened),patch('os.fchmod',fchmod),patch('os.fdopen',fdopen),patch('os.fsync',sync),\
     patch('os.replace',replace),patch('os.unlink',unlink),contextlib.redirect_stdout(printed):
    try:result=lifecycle.start()
    except Exception as exc:result={'error':type(exc).__name__,'message':str(exc)}
print(json.dumps({'reply':result,'printed':printed.getvalue().splitlines(),'trace':trace,
                  'attempts':attempts,'python_tmp_max':tempfile.TMP_MAX,'files':snapshot(ROOT_VALUE)},ensure_ascii=True))
'''


def native_trace(case):
    later=case in ('write-exists','sync-exists','close-exists','rename-exists')
    base=trace_source(case.split('-')[0] if later else 'none')
    if later:base=base.replace('"EIO"','"EEXIST"').replace('Input/output error','File exists')
    source=base+'''
!(bind! &mk-attempts (new-state 0))
!(match &self (= (system:uuid4-hex) $prior) (remove-atom &self (= (system:uuid4-hex) $prior)))
(= (system:uuid4-hex)
 (let $uuid (__cetta_lib_system_host uuid4-hex)
  (let $_ (protocol:record "candidate" (str:byte-slice $uuid 0 8)) $uuid)))
!(match &self (= (fs:make-directories $path $mode) $prior) (remove-atom &self (= (fs:make-directories $path $mode) $prior)))
(= (fs:make-directories $path $mode)
 (let $_ (protocol:record "mkdir" $path) (__cetta_lib_fs_make_directories $path $mode)))
!(match &self (= (system:getcwd) $prior) (remove-atom &self (= (system:getcwd) $prior)))
(= (system:getcwd) (let $_ (protocol:record "getcwd" "") CWD_VALUE))
!(match &self (= (fs:open-write $path $flags $mode) $prior) (remove-atom &self (= (fs:open-write $path $flags $mode) $prior)))
(= (fs:open-write $path $flags $mode)
 (let* (($_ (protocol:record "open" $path))
        ($n (+ (get-state &mk-attempts) 1))
        ($_ (change-state! &mk-attempts $n))
        ($_ (if (and (== CASE "collision-success") (== $n 1))
              (let* (($seed (__cetta_lib_fs_open_write $path ("create" "exclusive") 384))
                     ($_ (__cetta_lib_fs_write_all $seed "collision fixture\\n"))
                     ($_ (__cetta_lib_fs_set_mode $seed 388))) (__cetta_lib_fs_close $seed)) ())))
   (if (and (== CASE "threshold-conflict") (<= $n 10000))
     (fs:failed fs:exists open-write $path (system:errno-value "EEXIST") (fs:progress 0 known-position) "File exists")
     (if (or (== CASE "open-denied") (== CASE "threshold-conflict"))
       (fs:failed fs:denied open-write $path (system:errno-value "EACCES") (fs:progress 0 known-position) "Permission denied")
       (let $owner (__cetta_lib_fs_open_write $path $flags $mode)
        (case $owner (((NativeHandle "file-writer" $_)
          (let $_ (add-atom &self (protocol-owner $owner $path)) $owner)) ($other $other))))))))
'''
    cwd='(system:os-error (system:errno-value "EACCES") "Permission denied")' if case in ('cwd-error','absolute-no-cwd') else '(__cetta_lib_system_host getcwd)'
    return source.replace('CWD_VALUE',cwd).replace('CASE',q(case))


RANDOM=re.compile(r'(?<=\.lifecycle-)[a-z0-9_]{8}(?![a-z0-9_])')


def projection(value):
    def norm(text):return RANDOM.sub('random00',text)
    # Preserve rows even if their normalized names are equal; no dict-key
    # collapse is used to conceal multiple colliding files.
    return {'reply':{k:norm(v) for k,v in value['reply'].items()} if isinstance(value['reply'],dict) else value['reply'],
            'printed':value['printed'],
            'trace':[[op,'random00' if op=='candidate' else norm(path)] for op,path in value['trace']],
            'attempts':value['attempts'],
            'files':sorted([[norm(path),row] for path,row in value['files'].items()],key=lambda x:x[0])}


def check(c,threshold=False):
    with c.phase('lifecycle-mkstemp-paths-and-collisions' if not threshold else 'actual-tempfile-retry-threshold-counterexample'),umask(0o027):
        package=stage(c,'lifecycle-package');root=package/'fixture'
        fixture=poison(c)
        cases=['threshold-conflict'] if threshold else ['relative-ok','relative-rename','relative-dotdot','absolute-ok',
            'absolute-rename','symlink-dotdot','double-slash','triple-slash','cwd-error','absolute-no-cwd',
            'collision-success','open-denied','write-exists','sync-exists','close-exists','rename-exists']
        records=[]
        for case in cases:
            observed=[]
            for lane in ('oracle','native'):
                if root.exists():shutil.rmtree(root)
                root.mkdir();(root/'state').mkdir()
                path='fixture/state/lifecycle.json'
                if case in ('absolute-ok','absolute-rename','absolute-no-cwd','collision-success','open-denied','threshold-conflict'):
                    path=str(root/'state/lifecycle.json')
                if case in ('relative-ok','absolute-ok'):
                    path=(str(root) if case=='absolute-ok' else 'fixture')+'/state/new/sub/lifecycle.json'
                if case in ('relative-rename','absolute-rename'):(root/'state/lifecycle.json').mkdir()
                if case=='relative-dotdot':
                    (root/'state/sub').mkdir();path='fixture/state/sub/../next/lifecycle.json'
                if case=='symlink-dotdot':
                    (root/'base/real/sub').mkdir(parents=True);(root/'base/link').symlink_to('real/sub')
                    path=str(root)+'/base/link/../state/lifecycle.json'
                if case in ('double-slash','triple-slash'):
                    path=('/'*(2 if case=='double-slash' else 3))+str(root/'state/lifecycle.json').lstrip('/')
                env={'METTACLAW_LIFECYCLE_PATH':path,
                     'PYTHONPATH':str(c.directory)+os.pathsep+c.environment()['PYTHONPATH']}
                if lane=='oracle':
                    result=python(c,'mkstemp-'+case+'-oracle',ORACLE.replace('CASE_VALUE',repr(case)).replace('ROOT_VALUE',repr(str(root))),env,cwd=package)
                else:
                    source=IMPORTS+fixture+'!(import! &self ./src/mode_state.metta)\n'+CAPTURE+native_trace(case)
                    if threshold:
                        source+='''
; Stream the bounded threshold trace rather than retaining a growing native
; state list. This changes TEST observation only, not candidate generation.
!(match &self (= (protocol:record $op $path) $prior) (remove-atom &self (= (protocol:record $op $path) $prior)))
(= (protocol:record $op $path)
 (println! (MkEvent (json:stringify-python-with (JsonArray ($op $path)) ((ensure-ascii True))))))
'''
                    source+=emit(captured('(lifecycle:start)'))+emit('(JsonArray (get-state &protocol-trace))')+emit('(get-state &mk-attempts)')
                    output=c.run('mkstemp-'+case+'-native',source,env,runtime_root=package,timeout=180)
                    values=rows(output);assert len(values)==3,output
                    namespace={};exec(SNAPSHOT,namespace)
                    result={'reply':values[0],'trace':list(reversed(values[1])),'attempts':values[2],
                            'files':namespace['snapshot'](root),'printed':[x for x in output.splitlines() if x.startswith('[')]}
                    if threshold:
                        result['trace']=[json.loads(json.loads(line[len('(MkEvent '):-1]))
                                         for line in output.splitlines() if line.startswith('(MkEvent ')]
                candidates=[v for op,v in result['trace'] if op=='candidate']
                opens=[v for op,v in result['trace'] if op=='open']
                assert len(opens)==result['attempts']==len(candidates),result
                for candidate,opened in zip(candidates,opens):
                    assert re.fullmatch('[0-9a-f]{8}' if lane=='native' else '[a-z0-9_]{8}',candidate),candidate
                    assert Path(opened).name=='.lifecycle-'+candidate,(candidate,opened)
                if not threshold:
                    assert len([x for x in result['trace'] if x[0]=='getcwd'])==(0 if path.startswith('/') else 1),result
                observed.append(result)
            pair={'case':case,'oracle':observed[0],'native':observed[1],
                  'projections':[projection(o) for o in observed],
                  'fixture':'actual RNG/candidate semantics unchanged; collision seeding and EACCES/threshold errors explicitly injected'}
            c.save('mkstemp-'+case+'.json',pair)
            if threshold:
                assert observed[0]['python_tmp_max']>10000
                assert observed[0]['attempts']==10001 and observed[0]['reply']['error']=='PermissionError'
                assert observed[1]['attempts']==10000 and observed[1]['reply']=={
                    'error':'FileExistsError','message':'[Errno %d] No usable temporary file name found'%errno.EEXIST}
                assert all(op in ('mkdir','candidate','open') for op,_ in observed[1]['trace'])
            else:
                assert pair['projections'][0]==pair['projections'][1],pair
                assert observed[1]['attempts']==(2 if case=='collision-success' else 0 if case=='cwd-error' else 1),pair
                if case=='collision-success':
                    files=[v for k,v in observed[1]['files'].items() if Path(k).name.startswith('.lifecycle-')]
                    assert files==[{'mode':'0o604','kind':'file','hex':b'collision fixture\n'.hex()}],files
            records.append(pair)
        c.save('mkstemp-result.json',{'status':'BLOCKED_TMP_MAX_CONTRACT_CONFLICT' if threshold else 'PASS_LIFECYCLE_MKSTEMP_CASES',
               'cases':len(records),'threshold':'native10000 per requested contract, actual unmodified Python limit recorded, never patched',
               'traces':'public mkdir operation (not recursive internal syscalls), cwd/candidate/exclusive open and owned I/O order; raw names retained'})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence',type=Path,required=True)
    p.add_argument('--threshold',action='store_true');args=p.parse_args();limit();args.evidence.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='mode06-mkstemp-') as directory:
        c=Qualification(args.evidence.resolve(),Path(directory));c.snapshot_sources('tests/mode_state_lifecycle_check.py','src/lifecycle_cetta.metta')
        check(c,args.threshold)


if __name__=='__main__':main()
