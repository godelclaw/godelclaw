"""Exact reached engine-save close-failure witness, not full qualification.

Faults are explicit post-close injections on both routes, NOT naturally
observed OS close failures. The underlying descriptor really closes once.
"""
import argparse
import json
from pathlib import Path
import tempfile

from effect_receipts_support import IMPORTS, emit, limit, q, rows
from mode_state_native_check import python
from mode_state_support import Qualification


CONTROL = '''import json, os
from pathlib import Path
counts={'seed':0,'snapshot':0,'blocked':0}
def blocked(*args, **kwargs):
    counts['blocked']+=1
    raise AssertionError('ported Python engine function reached')
import engine_modes
for name in ('active_engine','selected_engine','engine_view','set_engine','engine_command','request_recycle'):
    setattr(engine_modes,name,blocked)
def seed():
    counts['seed']+=1
    target=Path(os.environ['METTACLAW_ENGINE_STATE_PATH'])
    target.write_bytes(b'petta\\n'); target.chmod(0o640)
    temporary=Path(str(target)+'.tmp.'+str(os.getpid()))
    temporary.write_bytes(b'stale\\n'); temporary.chmod(0o604)
    return 1
def snapshot():
    counts['snapshot']+=1
    target=Path(os.environ['METTACLAW_ENGINE_STATE_PATH'])
    temporary=Path(str(target)+'.tmp.'+str(os.getpid()))
    return json.dumps({'target':{'hex':target.read_bytes().hex(),'mode':oct(target.stat().st_mode&0o777)},
                       'temporary':{'hex':temporary.read_bytes().hex(),'mode':oct(temporary.stat().st_mode&0o777)},
                       'actual_temporary_path':str(temporary),'counts':counts})
'''


def check(c):
    with c.phase('engine-close-before-chmod-fault-order'):
        fixture = c.directory/'mode_close_fixture.py'
        fixture.write_text(CONTROL)
        (c.evidence/fixture.name).write_bytes(fixture.read_bytes())
        source = IMPORTS + '!(import! &self %s)\n' % fixture
        source += '!(import! &self ./src/engine_modes.metta)\n'
        source += '''
!(py-call (mode_close_fixture.seed))
!(match &self (= (fs:close $owner) $prior) (remove-atom &self (= (fs:close $owner) $prior)))
; Explicit fault injection after one real native close. No handle replay.
(= (fs:close $owner)
 (let $closed (__cetta_lib_fs_close $owner)
  (let $_ (println! (InjectedActualClose $closed))
   (fs:failed fs:io close "" (system:errno-value "EIO") 0 "Input/output error"))))
(= (close-test:text $value) (if (== (get-type $value) String) $value (str:symbol->string $value)))
'''
        source += emit('(engine:set "cetta")')
        source += emit('(json:parse-python (close-test:text (py-call (mode_close_fixture.snapshot))))')
        output = c.run('native-post-close-error', source)
        native = rows(output)
        assert '(InjectedActualClose fs:closed)' in output, output
        assert '[engine-mode] save failed: OSError' in output, output
        expected = python(c, 'python-post-close-error', '''import builtins, contextlib, errno, io, json, os
from pathlib import Path
from unittest.mock import patch
import engine_modes
target=Path(os.environ['METTACLAW_ENGINE_STATE_PATH'])
target.write_bytes(b'petta\\n'); target.chmod(0o640)
temporary=Path(str(target)+'.tmp.'+str(os.getpid()))
temporary.write_bytes(b'stale\\n'); temporary.chmod(0o604)
original=builtins.open
events=[]
class PostCloseError:
    def __init__(self, stream): self.stream=stream
    def __enter__(self): return self.stream.__enter__()
    def __exit__(self,*args):
        self.stream.__exit__(*args)
        assert self.stream.closed
        events.append('underlying stream closed once')
        raise OSError(errno.EIO, os.strerror(errno.EIO))
def injected(path,*args,**kwargs):
    stream=original(path,*args,**kwargs)
    return PostCloseError(stream) if str(path)==str(temporary) else stream
printed=io.StringIO()
with patch('builtins.open',injected), contextlib.redirect_stdout(printed):
    reply=engine_modes.set_engine('cetta')
print(json.dumps({'reply':reply,'printed':printed.getvalue().splitlines(),'events':events,
 'target':{'hex':target.read_bytes().hex(),'mode':oct(target.stat().st_mode&0o777)},
 'temporary':{'hex':temporary.read_bytes().hex(),'mode':oct(temporary.stat().st_mode&0o777)},
 'actual_temporary_path':str(temporary)}))
''')
        assert native[0] == expected['reply'] == 'engine-set failed: could not persist'
        assert native[1]['target'] == expected['target']
        assert native[1]['temporary']['hex'] == expected['temporary']['hex'] == b'cetta\n'.hex()
        assert native[1]['counts'] == {'seed':1,'snapshot':1,'blocked':0}
        assert native[1]['temporary']['mode'] == '0o600'
        assert expected['temporary']['mode'] == '0o604'
        path = c.directory/'path-mode-probe'
        path.write_bytes(b'fixture\n'); path.chmod(0o604)
        probe = c.run('path-mode-api-refusal', IMPORTS +
                      '!(println! (PathMode (catch (fs:set-mode %s 384))))\n' % q(path))
        assert path.stat().st_mode & 0o777 == 0o604, probe
        c.save('result.json', {'status':'BLOCKED_ENGINE_CLOSE_CHMOD_EFFECT_ORDER',
               'full_project06_complete':False, 'native':native,'python':expected,
               'path_mode_probe_stdout':probe,
               'injection':'explicit post-close EIO fixtures; each underlying descriptor actually closed once; not an organic OS failure',
               'request':'native path-mode operation or reviewed exact protocol preserving Python write/flush/fsync/close/chmod(path)/rename order without reopen, permission-domain restriction, effect replay or foreign workaround',
               'effects':'both failed, old target unchanged, temporary cetta+LF retained; native temp0600 versus Python temp0604'})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--evidence',type=Path,required=True)
    args=p.parse_args();limit()
    args.evidence.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='mode06-close-order-') as directory:
        c=Qualification(args.evidence.resolve(),Path(directory))
        c.snapshot_sources('tests/mode_state_close_order_check.py','src/engine_modes_cetta.metta',
                           'src/mode_io_cetta.metta','src/engine_modes.py')
        check(c)


if __name__=='__main__':main()
