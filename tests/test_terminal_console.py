"""A real terminal composer stays usable while input publication is blocked."""
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]

@unittest.skipUnless(os.environ.get('CETTA_BIN'), 'qualified Python-enabled CeTTa required')
class TerminalConsole(unittest.TestCase):
    def test_a_suppressed_redraw_still_publishes_the_buffered_reply(self):
        from ui.worklog.console import EditorProxy
        buffer, published = [], []
        loop = object()

        def flush():
            if buffer:
                published.append(''.join(buffer))
                buffer.clear()

        output = SimpleNamespace(write_raw=buffer.append, flush=flush)
        renderer = SimpleNamespace(waiting_for_cpr=False, erase=lambda: None,
                                   request_absolute_cursor_position=lambda: None)
        app = SimpleNamespace(loop=loop, is_running=True, _running_in_terminal=False,
                              renderer=renderer, _redraw=lambda: None)

        class Base:
            def __init__(self, **kwargs):
                self._output = output
                self.app_session = SimpleNamespace(app=app)

        proxy = EditorProxy(Base)
        proxy.pending.append('reply')
        proxy.loop = loop
        proxy._drain(loop, time.monotonic())
        self.assertEqual(published, ['reply'])
        self.assertFalse(buffer)

    def test_bracketed_paste_and_controls_share_the_session_without_blocking(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder); instance=base/'instance';instance.mkdir();state=base/'state'
            fakes=base/'fakes';fakes.mkdir()
            (fakes/'synthetic_llm.py').write_text('''
def chat(*a,**kw): raise AssertionError('no model calls')
def current_model(): return 'offline'
def model_ids():
    from pathlib import Path
    import time
    r=Path(__file__).parent.parent
    if (r/'menu-gate').exists():
        (r/'menu-started').touch()
        while not (r/'menu-release').exists(): time.sleep(.01)
    return ['offline','other']
def model_catalog_status(): return 'fixture'
def model_in_observed_catalog(name): return None
''')
            fuel=instance/'memory'/'fuel_mode.json';fuel.parent.mkdir()
            fuel.write_text('{"fuel": "carry"}')
            runner=instance/'run.sh'
            runner.write_text('#!/bin/sh\nexec '+json.dumps(os.environ['CETTA_BIN'])+
                ' --lang petta --import-mode ancestor-walk '+json.dumps(str(ROOT/'channels/terminal_control_main.metta'))+'\n')
            runner.chmod(0o700)
            mail=base/'mail.py';mail.write_text('''import json,sys,time
from pathlib import Path
r=Path(__file__).parent;a=sys.argv[1:]
if a[1]=='send':
    (r/'publishing').touch()
    while not (r/'release').exists(): time.sleep(.02)
    (r/'submitted.json').write_text(json.dumps({'id':a[3],'text':a[6]}))
    print(json.dumps({'status':'accepted','id':a[3]}))
else:
    if (r/'hold-read').exists():
        (r/'read-held').touch()
        while True: time.sleep(.02)
    time.sleep(.2);print(json.dumps({'status':'idle'}));sys.exit(3)
''')
            mail.chmod(0o700)
            commands=base/'bin';commands.mkdir();(commands/'claw-mail').symlink_to(mail)
            environment=dict(os.environ,PATH=str(commands),TERM='xterm-256color',
                CETTA_SHARED_MAIL_SOCKET=str(base/'no-service.sock'),CLAW_MAIL_CONFIG=str(base/'isolated-mail.toml'))
            (base/'isolated-mail.toml').write_text('socket = '+json.dumps(str(base/'no-service.sock'))+'\ncetta = '+json.dumps(os.environ['CETTA_BIN'])+'\n')
            profile=base/'instances.json';profile.write_text(json.dumps({'instances':[{
                'name':'fixture','root':str(instance),'state_dir':str(state),
                'environment':{'PYTHONHOME':sys.prefix,'LD_LIBRARY_PATH':str(Path(sys.prefix)/'lib')},
                'required_runtime':{'CETTA_BIN':os.environ['CETTA_BIN'],
                    'PYTHONPATH':os.pathsep.join((str(fakes),str(ROOT/'src'),str(ROOT/'channels')))}}]}))
            master,slave=pty.openpty()
            fcntl.ioctl(master,termios.TIOCSWINSZ,struct.pack('HHHH',30,110,0,0))
            process=subprocess.Popen([sys.executable,str(ROOT/'scripts/agent_console.py'),
                '--agent','fixture','--instances',str(profile),'--dir',str(base/'display')],
                cwd=ROOT,env=environment,stdin=slave,stdout=slave,stderr=slave,start_new_session=True)
            os.close(slave);transcript=bytearray()
            cursor_tail = b''

            def drain(timeout=.05):
                nonlocal cursor_tail
                if not select.select([master], [], [], timeout)[0]:
                    return False
                try:
                    block = os.read(master, 65536)
                except OSError:
                    return False
                if not block:
                    return False
                transcript.extend(block)
                # A terminal answers every cursor query, including queries
                # batched in one read or split across reads.
                queries = cursor_tail + block
                count = queries.count(b'\x1b[6n')
                cursor_tail = queries[-3:]
                if count:
                    os.write(master, b'\x1b[1;1R' * count)
                return True

            def expect(text):
                deadline=time.monotonic()+15
                while time.monotonic()<deadline:
                    if text.encode() in transcript:return
                    drain()
                    if process.poll() is not None:break
                log = state / 'terminal-client/controls.log'
                detail = log.read_text(errors='replace')[-2400:] if log.exists() else 'no control log'
                self.fail('missing '+text+'; tail: '+transcript[-1800:].decode(errors='replace')+
                          '; controls: '+detail)
            try:
                expect('activity: ');expect('fuel=carry')
                os.write(master,b'/models\r');expect('tap to switch')
                expect('Synthetic (2)')
                os.write(master,b'1\r');expect('Providers')
                os.write(master,b'99999\r');expect('Choose a displayed number')
                self.assertFalse((base/'publishing').exists())
                (base/'menu-gate').touch();os.write(master,b'/models\r')
                deadline=time.monotonic()+5
                while not (base/'menu-started').exists() and time.monotonic()<deadline:time.sleep(.01)
                self.assertTrue((base/'menu-started').exists())
                os.write(master,b'/close-menu\r');expect('Menu closed.')
                (base/'menu-release').touch();expect('This menu is no longer active.')
                paste='hello\ncentral constraint ž\nlast line'
                os.write(master,b'\x1b[200~'+paste.encode()+b'\x1b[201~\r')
                deadline=time.monotonic()+5
                while not (base/'publishing').exists() and time.monotonic()<deadline:time.sleep(.01)
                self.assertTrue((base/'publishing').exists())
                fuel.write_text('{"fuel": "saturate"}')
                os.write(master,b'/activity\r')
                expect('fuel=saturate')
                self.assertFalse((base/'submitted.json').exists())
                (base/'release').touch();expect('accepted ·')
                submitted=json.loads((base/'submitted.json').read_text())
                self.assertEqual(submitted['text'],paste)
                (base/'hold-read').touch()
                deadline=time.monotonic()+5
                while not (base/'read-held').exists() and time.monotonic()<deadline:time.sleep(.01)
                self.assertTrue((base/'read-held').exists())
                os.write(master,b'/detach\r')
                until = time.monotonic() + 5
                while process.poll() is None and time.monotonic() < until:
                    drain()
                process.wait(timeout=max(.01, until-time.monotonic()))
                while select.select([master],[],[],.05)[0]:
                    try: block=os.read(master,65536)
                    except OSError:break
                    if not block:break
                    transcript.extend(block)
                self.assertEqual(process.returncode,0)
                self.assertNotIn(b'Reply channel unavailable',transcript)
                self.assertNotIn(b'\x1b[?1049h',transcript)
                self.assertNotIn(b'\x1b[?1000h',transcript)
            finally:
                if process.poll() is None:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)
                os.close(master)

if __name__=='__main__':unittest.main()
