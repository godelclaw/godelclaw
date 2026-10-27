"""Paired protocol fault fixtures; real descriptors/effects, no uncertain replay."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from effect_receipts_support import IMPORTS, emit, limit, q, rows
from mode_state_modules_check import poison
from mode_state_native_check import CAPTURE, SNAPSHOT, captured, python, umask
from mode_state_support import Qualification


SEED = '''
def seed(root):
    from pathlib import Path
    root=Path(root)
    for name,raw in {'engine':b'petta\\n','recycle':b'before\\n',
                     'loop':b'{"mode":"godelclaw","autonomy_paused":true}\\n',
                     'fuel':b'{"fuel":"decay","extra":1}\\n',
                     'lifecycle':b'{"schema":1,"state":"stopped"}\\n'}.items():
        path=root/name;path.write_bytes(raw);path.chmod(0o640)
        if name!='lifecycle':
            temp=Path(str(path)+('.tmp.'+str(os.getpid()) if name in ('engine','recycle') else '.tmp'))
            if os.environ.get('MODE_STATE_PROTOCOL_SYMLINK')=='1':
                backing=root/('backing-'+name);backing.write_bytes(b'stale\\n');backing.chmod(0o604)
                temp.symlink_to(backing.name)
            else:temp.write_bytes(b'stale\\n');temp.chmod(0o604)
    return 1
'''

ORACLE = '''import builtins, contextlib, errno, io, json, os, tempfile, time
from pathlib import Path
from unittest.mock import patch
import engine_modes, loop_modes, fuel_modes, lifecycle
''' + SNAPSHOT + SEED + '''
root=Path(ROOT_VALUE);seed(root)
time.time=lambda:1790000000.123456
trace=[];fdpaths={};leaked=[]
realopen=builtins.open;real_os_open=os.open;real_fdopen=os.fdopen
real_sync=os.fsync;real_fchmod=os.fchmod;real_chmod=os.chmod;real_replace=os.replace;real_unlink=os.unlink
def event(op,path): trace.append([op,str(path)])
def fails(op): return FAULT_VALUE==op or (FAULT_VALUE=='rename+unlink' and op in ('rename','unlink'))
def error(path=None,target=None):
    return OSError(errno.EIO,os.strerror(errno.EIO),str(path),None,str(target)) if target is not None else (
        OSError(errno.EIO,os.strerror(errno.EIO),str(path)) if path is not None else OSError(errno.EIO,os.strerror(errno.EIO)))
class Writer:
    def __init__(self,stream,path): self.stream=stream;self.path=str(path);self.written=False
    def __enter__(self): self.stream.__enter__();return self
    def write(self,text):
        if not self.written: event('write',self.path);self.written=True
        if fails('write'): raise error()
        if fails('partial'):
            self.stream.write(text[:2]);raise error()
        return self.stream.write(text)
    def flush(self): return self.stream.flush()
    def fileno(self): return self.stream.fileno()
    def __exit__(self,*args):
        self.stream.__exit__(*args);event('close',self.path)
        if fails('close'): raise error()
def opened(path,*args,**kwargs):
    if str(path).startswith(str(root)) and args and args[0]=='w':
        event('open',path)
        if fails('open'):raise error(path)
        stream=realopen(path,*args,**kwargs);fdpaths[stream.fileno()]=str(path)
        return Writer(stream,path)
    return realopen(path,*args,**kwargs)
def os_open(path,*args,**kwargs):
    event('open',path)
    if fails('open'):raise error(path)
    fd=real_os_open(path,*args,**kwargs);fdpaths[fd]=str(path);leaked.append(fd);return fd
def fdopen(fd,*args,**kwargs):return Writer(real_fdopen(fd,*args,**kwargs),fdpaths[fd])
def sync(fd):
    event('sync',fdpaths[fd])
    if fails('sync'):raise error()
    return real_sync(fd)
def fchmod(fd,mode):
    event('fchmod',fdpaths[fd])
    if fails('fchmod'):raise error()
    return real_fchmod(fd,mode)
def chmod(path,mode):
    event('chmod',path)
    if fails('chmod'):raise error(path)
    return real_chmod(path,mode)
def replace(source,target):
    event('rename',source)
    if fails('rename'):raise error(source,target)
    return real_replace(source,target)
def unlink(path,*args,**kwargs):
    event('unlink',path)
    if fails('unlink'):raise error(path)
    return real_unlink(path,*args,**kwargs)
printed=io.StringIO()
with patch('builtins.open',opened),patch('os.open',os_open),patch('os.fdopen',fdopen),patch('os.fsync',sync),\
     patch('os.fchmod',fchmod),patch('os.chmod',chmod),patch('os.replace',replace),patch('os.unlink',unlink),\
     contextlib.redirect_stdout(printed):
    try: reply=eval(ACTOR_VALUE)
    except Exception as exc:reply={'error':type(exc).__name__,'message':str(exc)}
files=snapshot(root)
# lifecycle's pre-fdopen fchmod failure leaks its original descriptor. Record
# it accurately, then explicit TEST cleanup after observing all oracle effects.
unclosed=[]
for fd in leaked:
    try:os.fstat(fd)
    except OSError:continue
    unclosed.append(fdpaths[fd]);os.close(fd)
print(json.dumps({'reply':reply,'printed':printed.getvalue().splitlines(),'trace':trace,
                  'files':files,'oracle_unclosed_before_test_cleanup':unclosed},ensure_ascii=True))
'''


def trace_source(fault):
    source = '''
!(bind! &protocol-trace (new-state ()))
(= (protocol:record $op $path)
 (change-state! &protocol-trace (cons-atom (JsonArray ($op $path)) (get-state &protocol-trace))))
(= (protocol:owner-path $owner)
 (case (collapse (match &self (protocol-owner $owner $path) $path)) ((($path) $path) ($other ""))))
'''
    def body(op, path, original, failure_op, after=False, prefix=''):
        test = '(or (== %s %s) (and (== %s "rename+unlink") (or (== %s "rename") (== %s "unlink"))))' % (
            q(fault),q(op),q(fault),q(op),q(op))
        failure_path='""' if op in ('write','sync','close','fchmod') else path
        failed = '(fs:failed fs:io %s %s (system:errno-value "EIO") (fs:progress %d known-position) "Input/output error")' % (
            failure_op,failure_path,2 if op=='write' and fault=='partial' else 0)
        action = '(if %s %s %s)' % (test, failed, original)
        if after: action = '(let $protocolClosedResult %s (if %s %s $protocolClosedResult))' % (original,test,failed)
        if op == 'write' and fault == 'partial':
            action = '(let $_ (__cetta_lib_fs_write_all $owner (str:byte-slice $text 0 2)) %s)' % failed
        return '(let $_ (protocol:record %s %s) %s%s)' % (q(op),path,prefix,action)

    wrappers = [
        ('(fs:open-write $path $flags $mode)', '''
 (let $_ (protocol:record "open" $path)
  (if (== FAULT "open")
   (fs:failed fs:io open-write $path (system:errno-value "EIO") (fs:progress 0 known-position) "Input/output error")
   (let $owner (__cetta_lib_fs_open_write $path $flags $mode)
    (case $owner (((NativeHandle "file-writer" $_)
      (let $_ (add-atom &self (protocol-owner $owner $path)) $owner)) ($other $other))))))'''.replace('FAULT',q(fault))),
        ('(fs:write-all $owner $text)', body('write','(protocol:owner-path $owner)',
          '(__cetta_lib_fs_write_all $owner $text)','write-all')),
        ('(fs:sync $owner)', body('sync','(protocol:owner-path $owner)',
          '(__cetta_lib_fs_sync $owner)','sync')),
        ('(fs:close $owner)', body('close','(protocol:owner-path $owner)',
          '(__cetta_lib_fs_close $owner)','close',after=True)),
        ('(fs:rename $source $target)',body('rename','$source','(__cetta_lib_fs_rename $source $target)','rename')),
        ('(fs:remove $path)',body('unlink','$path','(__cetta_lib_fs_remove $path)','remove')),
        ('(fs:set-mode $item $mode)', '(if (== (get-type $item) String) %s %s)' % (
          body('chmod','$item','(__cetta_lib_fs_set_mode $item $mode)','set-mode'),
          body('fchmod','(protocol:owner-path $item)','(__cetta_lib_fs_set_mode $item $mode)','set-mode')))]
    for head, value in wrappers:
        source += '!(match &self (= %s $prior) (remove-atom &self (= %s $prior)))\n(= %s %s)\n' % (head,head,head,value)
    return source


def normalize(value):
    text=json.dumps(value,ensure_ascii=True,sort_keys=True)
    text=re.sub(r'\.tmp\.\d+', '.tmp.PID', text)
    text=re.sub(r'\.lifecycle-[0-9a-zA-Z_\-]+', '.lifecycle-TEMP', text)
    return json.loads(text)


def check(c):
    actors={
      'engine':('(engine:set "cetta")','engine_modes.set_engine("cetta")'),
      'recycle':('(engine:recycle)','engine_modes.request_recycle()'),
      'loop':('(mio:save-json (loop-mode:path) (JsonObject ((JsonPair "mode" "coding"))) "loop")',
              'loop_modes._save({"mode":"coding"})'),
      'fuel':('(fuel:set "carry")','fuel_modes.set_fuel("carry")'),
      'lifecycle':('(lifecycle:start)','lifecycle.start()')}
    root=c.directory/"state'日";root.mkdir()
    fixture=poison(c)
    f=c.directory/'mode_modules_fixture.py'
    f.write_text(f.read_text()+SNAPSHOT+SEED+'''
def protocol_seed(root):
    counts['configure']+=1;return seed(root)
def protocol_snapshot(root):
    counts['snapshot']+=1;return json.dumps({'files':snapshot(root),'counts':counts},ensure_ascii=True)
''')
    (c.evidence/f.name).write_bytes(f.read_bytes())
    settings={'METTACLAW_'+key:str(root/name) for key,name in (
      ('ENGINE_STATE_PATH','engine'),('RECYCLE_REQUEST_PATH','recycle'),('LOOP_MODE_PATH','loop'),
      ('FUEL_MODE_PATH','fuel'),('LIFECYCLE_PATH','lifecycle'))}
    settings['PYTHONPATH']=str(c.directory)+os.pathsep+c.environment()['PYTHONPATH']
    records=[]
    with c.phase('per-protocol-operation-and-fault-order'),umask(0o027):
        for actor,(native,oracle) in actors.items():
            faults=['none','open','write','close','rename']
            if actor in ('engine','recycle'):faults+=['sync','chmod','partial']
            if actor=='lifecycle':faults+=['sync','fchmod','rename+unlink']
            else:faults+=['symlink']
            if actor in ('engine','recycle'):faults+=['symlink-chmod']
            for fault in faults:
                effective='chmod' if fault=='symlink-chmod' else fault
                env={**settings,'MODE_STATE_PROTOCOL_SYMLINK':'1' if fault.startswith('symlink') else '0'}
                for path in root.iterdir():
                    if path.is_dir():shutil.rmtree(path)
                    else:path.unlink()
                source=IMPORTS+fixture+'!(import! &self ./src/mode_state.metta)\n!(import! &self ./src/fuel_cetta.metta)\n'+CAPTURE
                source+='!(py-call (mode_modules_fixture.protocol_seed %s))\n'%q(root)
                source+='!(match &self (= (system:wall-time) $prior) (remove-atom &self (= (system:wall-time) $prior)))\n(= (system:wall-time) 1790000000.123456)\n'
                source+=trace_source(effective)
                source+='(= (protocol:text $v) (if (== (get-type $v) String) $v (str:symbol->string $v)))\n'
                source+=emit(captured(native))+emit('(JsonArray (get-state &protocol-trace))')
                source+=emit('(json:parse-python (protocol:text (py-call (mode_modules_fixture.protocol_snapshot %s))))'%q(root))
                output=c.run('native-%s-%s'%(actor,fault),source,env)
                actual=rows(output)
                assert len(actual)==3,(actor,fault,output)
                a={'reply':actual[0],'trace':list(reversed(actual[1])), 'files':actual[2]['files'],
                   'printed':[line for line in output.splitlines() if line.startswith(('[engine-mode]','[loop-mode]','[fuel-mode]'))]}
                assert actual[2]['counts']=={'configure':1,'snapshot':1,'blocked':0},actual[2]
                for path in root.iterdir():
                    if path.is_dir():shutil.rmtree(path)
                    else:path.unlink()
                expected=python(c,'oracle-%s-%s'%(actor,fault),
                    ORACLE.replace('ROOT_VALUE',repr(str(root))).replace('FAULT_VALUE',repr(effective)).replace('ACTOR_VALUE',repr(oracle)),env)
                unclosed=expected.pop('oracle_unclosed_before_test_cleanup')
                pair={'actor':actor,'fault':fault,'native':a,'python':expected,
                      'oracle_unclosed_before_explicit_test_cleanup':unclosed}
                c.save('case-%s-%s.json'%(actor,fault),pair)
                if actor=='lifecycle' and fault=='fchmod':
                    # The oracle leaks before fdopen; native explicitly owns
                    # and closes its writer. Files/return/error remain equal.
                    assert len(unclosed)==1 and [op for op,_ in a['trace']]==['open','fchmod','close','unlink']
                    assert [op for op,_ in expected['trace']]==['open','fchmod','unlink']
                    a={**a,'trace':[row for row in a['trace'] if row[0]!='close']}
                else:assert unclosed==[],pair
                assert normalize(a)==normalize(expected),pair
                records.append(pair)
        c.save('result.json',{'status':'PASS_PER_FILE_PROTOCOL_GATES','cases':records,
               'faults':'explicit EIO fixtures, including post-close and two-byte engine/recycle write effects; no organic OS-failure claim',
               'owner_disclosure':'Python lifecycle fchmod-before-fdopen fault leaks descriptor; fixture records then explicitly closes it; native closes its owned writer before unlink',
               'full_project06_complete':False})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence',type=Path,required=True)
    args=p.parse_args();limit();args.evidence.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='mode06-protocol-') as directory:
        c=Qualification(args.evidence.resolve(),Path(directory))
        c.snapshot_sources('tests/mode_state_protocol_check.py','src/mode_io_cetta.metta','src/engine_modes_cetta.metta','src/lifecycle_cetta.metta')
        check(c)
        from mode_state_lifecycle_check import check as lifecycle_mkstemp
        directory=c.directory/'lifecycle-mkstemp';directory.mkdir()
        evidence=c.evidence/'lifecycle-mkstemp';evidence.mkdir()
        sub=Qualification(evidence,directory)
        sub.snapshot_sources('tests/mode_state_lifecycle_check.py','src/lifecycle_cetta.metta')
        lifecycle_mkstemp(sub)


if __name__=='__main__':main()
