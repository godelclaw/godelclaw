"""Native shared controls and durable terminal input, without model inference."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
from terminal_client import Controls, MailSession
import terminal_client
import test_command_dispatch_engine as dispatch


class TerminalMail(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.driver = self.root / "mail.py"
        self.driver.write_text('''import json,sys
from pathlib import Path
root=Path(__file__).parent
args=sys.argv[1:]
if args[1]=='send':
    identity,sender,to,text=args[3:7]
    data=root/'accepted.json'
    old=json.loads(data.read_text()) if data.exists() else {}
    if identity in old: assert old[identity]==text
    repeated=identity in old
    old[identity]=text;data.write_text(json.dumps(old))
    if not (root/'failed-once').exists():
        (root/'failed-once').touch()
        print(json.dumps({'status':'unavailable'}));sys.exit(2)
    print(json.dumps({'status':'duplicate' if repeated else 'accepted','id':identity}))
else:
    print(json.dumps({'status':'idle'}))
''')
        self.session = MailSession(self.root / "client", "fixture-agent", [sys.executable, str(self.driver)])

    def test_large_paste_survives_lost_acceptance_reply_and_exact_retry(self):
        body = "pasted constraint ž\n" * 50000
        record = self.session.prepare(body)
        with self.assertRaises(RuntimeError): self.session.publish(record)
        self.assertTrue((self.session.root / "pending" / (record['id'] + '.json')).is_file())
        self.assertEqual(self.session.retry()[0]['status'], 'duplicate')
        accepted = json.loads((self.root / 'accepted.json').read_text())
        self.assertEqual(accepted, {record['id']: body})
        self.assertEqual(list((self.session.root / 'pending').glob('*.json')), [])

    def test_one_refused_retry_does_not_hide_other_pending_input(self):
        first=self.session.prepare("first"); second=self.session.prepare("second")
        def publish(record):
            if record["id"]==first["id"]: raise RuntimeError("fact-limit")
            return dict(status="accepted",id=record["id"])
        with mock.patch.object(self.session,"publish",side_effect=publish):
            results=self.session.retry()
        self.assertEqual({r["status"] for r in results},{"accepted","unconfirmed"})
        self.assertEqual({r["id"] for r in results},{first["id"],second["id"]})

    def test_reply_is_durably_saved_before_acknowledgement(self):
        reply = dict(status='delivery', id='message-one', text='A real reply', **{'from':'fixture-agent'})
        def call(*args, **kwargs):
            if args[0]=='wait': return reply
            self.assertTrue(list((self.session.root / 'received').glob('*.json')))
            return dict(status='settled',id=reply['id'])
        with mock.patch.object(self.session, 'call', side_effect=call):
            self.assertEqual(self.session.receive(), reply)
        with mock.patch.object(self.session, 'call', return_value=reply) as calls, \
             mock.patch.object(terminal_client, 'store', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError): self.session.receive()
            self.assertEqual(calls.call_count, 1)


@unittest.skipUnless(os.environ.get('CETTA_BIN'), 'set CETTA_BIN to the qualified Python-enabled engine')
class NativeTerminalControls(unittest.TestCase):
    def test_shared_menus_and_failures_work_without_cognition(self):
        harness = dispatch.CommandDispatchEngineTest()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); fakes=root/'fakes'; fakes.mkdir()
            chosen=root/'chosen'
            (fakes/'synthetic_llm.py').write_text('''
def chat(*args,**kwargs): raise AssertionError('control called cognition')
def quota(): raise RuntimeError('fixture provider unavailable')
def current_model(): return 'fixture-model'
def model_ids(): return ['model-' + 'long-name-'*10 + str(i) for i in range(500)]
def model_catalog_status(): return 'fixture catalog'
def model_in_observed_catalog(name): return None
def set_model(name):
    from pathlib import Path
    Path(''' + repr(str(chosen)) + ''').write_text(name)
    return name
''')
            env=harness._cetta_env();env.update(METTACLAW_ENGINE='cetta',METTACLAW_ACTIVE_ENGINE='cetta',
                METTACLAW_ENGINE_STATE_PATH=str(root/'engine'), METTACLAW_MODEL_STATE_PATH=str(root/'model.json'),
                METTACLAW_COGNITIVE_HEALTH_PATH=str(root/'cognitive.json'))
            env['PYTHONPATH']=str(fakes)+os.pathsep+env['PYTHONPATH']
            with (root/'stderr.log').open('w') as log:
                controls=Controls([str(harness._cetta_binary()),'--lang','petta','--import-mode','ancestor-walk',
                    str(ROOT/'channels/terminal_control_main.metta')],cwd=ROOT,environment=env,log=log)
                try:
                    body=controls.request('command',name='/models',arguments='')
                    self.assertEqual(body['kind'],'menu');self.assertFalse(chosen.exists())
                    self.assertEqual(len(body['rows']),1)
                    self.assertIn('Synthetic',body['rows'][0][0][0])
                    body=controls.request('choice',value=body['rows'][0][0][1])
                    self.assertEqual(body['kind'],'menu');self.assertFalse(chosen.exists())
                    self.assertGreater(len(body['rows']),20)
                    choice=body['rows'][0][0][1]
                    controls.request('choice',value=choice)
                    self.assertTrue(chosen.read_text().startswith('model-long-name-'))
                    failure=controls.request('command',name='/quota',arguments='')
                    self.assertIn('fixture provider unavailable',failure['text'])
                    self.assertEqual(controls.request('command',name='/models',arguments='')['kind'],'menu')
                    status=controls.request('status')
                    self.assertEqual(status['schema'],1)
                    self.assertEqual(status['call']['phase'],'unknown')
                    self.assertIn('unavailable',status)
                finally: controls.close()

if __name__=='__main__': unittest.main()
