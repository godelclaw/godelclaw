"""Loopback-only early publication; terminal command validation stays separate."""
import http.server
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import unittest

import test_native_llm_boundary as boundary
from test_provider_stream import frame
from test_responses_stream_engine import event, response
import public_worklog


class CommentaryPeer(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def do_POST(self):
        peer = self.server
        peer.requests.append((self.path, dict(self.headers), json.loads(
            self.rfile.read(int(self.headers['Content-Length'])))))
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        def write(text):
            raw = text.encode('utf-8')
            for i in range(0, len(raw), 7):
                self.wfile.write(raw[i:i+7]); self.wfile.flush()
        try:
            if peer.protocol == 'responses':
                write(event({'type':'response.reasoning_text.delta',
                             'delta':'PRIVATE REASONING {"commentary":"not public",'}))
            else:
                write(frame({'reasoning_content':'PRIVATE REASONING {"commentary":"not public",'}))
            peer.started.set()
            if not peer.allow_prefix.wait(8): return
            prefix = '{"commentary":"Checking ž 🐈, safely.", "commands":"'
            suffix = '((nop))"}'
            def delta(text):
                if peer.protocol == 'responses':
                    return event({'type':'response.output_text.delta', 'item_id':'msg',
                        'output_index':1, 'content_index':0, 'delta':text})
                return frame({'content':text})
            write(delta(prefix))
            # Credential screening may hold back a small raw suffix. Advance
            # it without adding another application event or exposing secrets.
            write(':' + ' '*128 + '\n\n')
            peer.prefix_sent.set()
            if not peer.release.wait(8): return
            if peer.ending == 'truncated': return
            write(delta(suffix))
            if peer.protocol == 'responses':
                final = response()
                final['output'][1]['content'][0]['text'] = prefix + suffix
                if peer.ending == 'failed':
                    write(event({'type':'response.failed', 'response':{'status':'failed'}}))
                else:
                    write(event({'type':'response.completed', 'response':final}))
            elif peer.ending == 'failed':
                write('event: error\ndata: {"error":"fixture failure"}\n\n')
            else:
                write(frame({}, 'stop') + 'data: [DONE]\n\n')
        except (BrokenPipeError, ConnectionResetError): pass


@unittest.skipUnless(os.environ.get('CETTA_BIN'), 'set CETTA_BIN to an incremental HTTP/Python build')
class NativeCommentaryStreamingTests(unittest.TestCase):
    def setUp(self):
        boundary.NativeBoundaryTests.setUp(self)
        self.peer.RequestHandlerClass = CommentaryPeer
        self.peer.protocol, self.peer.ending = 'chat-completions', 'complete'
        self.peer.started = threading.Event()
        self.peer.allow_prefix = threading.Event()
        self.peer.prefix_sent = threading.Event()
        self.peer.release = threading.Event()
        self.worklog = self.directory / 'public'
        public_worklog.transact(self.worklog, 'on')
        self.env.update(METTACLAW_MODEL_COMMENTARY='1', METTACLAW_LLM_STREAM='1',
            METTACLAW_PUBLIC_WORK_DIR=str(self.worklog), METTACLAW_PUBLIC_WORK_LIB='',
            METTACLAW_LLM_HARD_SECONDS='15', SYNTHETIC_TIMEOUT='10',
            METTACLAW_LLM_FIRST_BYTE_SECONDS='10', METTACLAW_LLM_IDLE_SECONDS='10')
        self.process = None

    def tearDown(self):
        self.peer.allow_prefix.set(); self.peer.release.set()
        if self.process and self.process.poll() is None:
            self.process.kill(); self.process.communicate()
        boundary.NativeBoundaryTests.tearDown(self)

    def notes(self): return public_worklog.transact(self.worklog, 'read')[1]

    def launch(self, protocol='chat-completions', lang='petta'):
        self.peer.protocol = protocol
        self.lang = lang
        model = 'fixture-model'
        if protocol == 'responses':
            model = 'openai:fixture-model'
            self.env.update(SYNTHETIC_MODEL=model, OPENAI_API_KEY='fixture-secret',
                            OPENAI_BASE_URL=self.env['SYNTHETIC_BASE_URL'])
        forms = (f'!(import! &self "{boundary.ROOT}/src/native_llm.metta")\n'
                 f'!(println! (RESULT (llm:command "{model}" 42 "low" "input")))\n')
        entry = self.directory / 'probe.metta'; entry.write_text(forms)
        self.output = self.directory / 'stdout'
        self.output_stream = self.output.open('w')
        self.addCleanup(self.output_stream.close)
        self.process = subprocess.Popen([os.environ['CETTA_BIN'], '--lang', lang,
            '--import-mode', 'ancestor-walk', '--quiet', str(entry)], cwd=boundary.ROOT,
            env=self.env, stdout=self.output_stream, stderr=subprocess.PIPE, text=True)
        self.assertTrue(self.peer.started.wait(5), 'provider request did not start')

    def early(self):
        self.peer.allow_prefix.set()
        self.assertTrue(self.peer.prefix_sent.wait(3))
        deadline = time.monotonic() + 4
        while not self.notes() and time.monotonic() < deadline: time.sleep(.02)
        self.assertEqual([n['text'] for n in self.notes()], ['Checking ž 🐈, safely.'])
        self.assertIsNone(self.process.poll(), 'provider must still be pending')
        self.assertNotIn('RESULT', self.output.read_text())
        self.assertEqual(len(self.peer.requests), 1)

    def finish(self, expected='((nop))'):
        self.peer.release.set()
        _, stderr = self.process.communicate(timeout=10)
        output = self.output.read_text()
        self.assertEqual(self.process.returncode, 0, stderr)
        self.assertNotIn('(Error ', output)
        self.assertNotIn('fixture-secret', output+stderr)
        rendered = json.dumps(expected) if self.lang == 'he' else expected
        self.assertIn('(RESULT ' + rendered + ')', output, stderr)
        self.assertEqual(len(self.peer.requests), 1)
        return output

    def test_chat_commentary_before_completion(self):
        self.launch(); self.early(); self.finish()
        self.assertEqual(len(self.notes()), 1)

    def test_responses_commentary_before_completion(self):
        self.launch('responses'); self.early(); self.finish()
        self.assertEqual(len(self.notes()), 1)

    def test_he_commentary_before_completion(self):
        self.launch(lang='he'); self.early(); self.finish()

    def test_chat_truncation_keeps_provisional_note_but_no_commands(self):
        self.peer.ending = 'truncated'
        self.launch(); self.early(); self.finish('()')

    def test_responses_failure_keeps_provisional_note_but_no_commands(self):
        self.peer.ending = 'failed'
        self.launch('responses'); self.early(); self.finish('()')

    def test_sharing_off_on_drops_call_captured_before_toggle(self):
        self.launch()
        public_worklog.transact(self.worklog, 'off')
        public_worklog.transact(self.worklog, 'on')
        self.peer.allow_prefix.set()
        self.assertTrue(self.peer.prefix_sent.wait(3))
        time.sleep(.6)
        self.assertEqual(self.notes(), [])
        self.finish()
        self.assertEqual(self.notes(), [])

    def test_sharing_off_at_start_does_not_capture_later_epoch(self):
        public_worklog.transact(self.worklog, 'off')
        self.launch()
        public_worklog.transact(self.worklog, 'on')
        self.peer.allow_prefix.set()
        self.assertTrue(self.peer.prefix_sent.wait(3))
        time.sleep(.6)
        self.assertEqual(self.notes(), [])
        self.finish()
        self.assertEqual(self.notes(), [])


if __name__ == '__main__': unittest.main()
