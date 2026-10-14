"""Real native memory controls, recovery and retrieval. Python owns fixtures only."""
import functools
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
CETTA_ROOT = Path(os.environ.get('CETTA_CHANNEL_ROOT', ROOT.parent / 'CeTTa-ours'))
sys.path.insert(0, str(CETTA_ROOT / 'tools'))
from memory_collection import NativeCollection
from space_service_client import E, S, Var


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = os.environ.get('CETTA_BIN')
        self.server_bin = os.environ.get('CETTA_SPACE_SERVICE')
        self.worker_bin = os.environ.get('CETTA_VALUE_WORKER')
        if not all((self.engine, self.server_bin, self.worker_bin)):
            self.skipTest('set the native engine, space service and value worker')
        self.temp = tempfile.TemporaryDirectory(prefix='native-memory-controls-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.socket = self.home / 'memory.sock'
        self.server = subprocess.Popen([self.server_bin, '--socket', str(self.socket),
                                        '--data', str(self.home / 'data')],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.addCleanup(self.stop_store)
        until = time.monotonic() + 10
        while not self.socket.exists():
            self.assertIsNone(self.server.poll())
            self.assertLess(time.monotonic(), until)
            time.sleep(.01)
        self.collection = NativeCollection(self.socket, 'controls', {'embedding_output_dim': 3},
                                           self.worker_bin, CETTA_ROOT / 'lib/petta/lib_memory_collection.metta')
        self.addCleanup(self.collection.close)
        self.collection.add(['a', 'b'], [[1., 0., 0.], [0., 1., 0.]],
                            ['old alpha', 'beta reference'], [{'time': 'original'}, {'time': 'original'}])
        self.failed = False
        self.delay = 0
        self.failures_left = 0
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                time.sleep(fixture.delay)
                failing = fixture.failed or fixture.failures_left > 0
                if fixture.failures_left > 0: fixture.failures_left -= 1
                if failing:
                    body = b'{"error":"fixture unavailable"}'; self.send_response(503)
                else:
                    vectors = [[0., 1., 0.] if 'beta' in t else [1., 0., 0.]
                               for t in request['texts']]
                    body = json.dumps({'protocol': 'fixture-v1', 'dim': 3, 'embeddings': vectors}).encode()
                    self.send_response(200)
                self.send_header('Content-Length', str(len(body))); self.end_headers()
                try: self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError): pass

            def log_message(self, *_): pass

        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.http.daemon_threads = True
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        config = self.home / 'backend.json'
        # No qualification files: runtime access is independent of migration reports.
        config.write_text(json.dumps({'mode': 'native', 'socket': str(self.socket), 'name': 'controls'}))
        self.env = dict(os.environ, METTACLAW_MEMORY_BACKEND_CONFIG=str(config),
                        METTACLAW_EMBED_ENDPOINT='http://127.0.0.1:%d' % self.http.server_port,
                        METTACLAW_EMBED_PROTOCOL='fixture-v1', METTACLAW_MEMORY_RETRY_SECONDS='.05')

    def stop_store(self):
        if self.server.poll() is None:
            self.server.terminate()
        self.server.communicate(timeout=10)

    def run_metta(self, body, timeout=30):
        path = self.home / ('probe-%d.metta' % time.monotonic_ns())
        path.write_text('!(import! &self ' + json.dumps(str(ROOT / 'src/semantic_memory_cetta.metta')) + ')\n'
                       '(= (fixture:drain $n) (let $_ (semantic:maintain) '
                       '  (if (== (memory-pending) ()) True '
                       '    (if (== $n 0) (FixturePending (memory-pending)) '
                       '      (let (io:pending $h) (io:submit (timer:after 10)) '
                       '        (let $_ (io:wait $h 1000) (fixture:drain (- $n 1))))))))\n' + body)
        result = subprocess.run([self.engine, '--lang', 'petta', str(path)], cwd=ROOT,
                                env=self.env, text=True, capture_output=True, timeout=timeout)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('(Error ', result.stdout, result.stdout)
        return result.stdout

    def test_ids_correction_forget_and_history_survive_fresh_evaluator(self):
        output = self.run_metta('''
!(println! (READ (memory-read "a")))
!(println! (QUERY (semantic:query "alpha" 2)))
!(println! (EDIT (memory-edit "a" "corrected beta Ω")))
!(println! (DRAIN (fixture:drain 100)))
!(println! (UPDATED (memory-read "a")))
!(println! (QUERY_UPDATED (semantic:query "alpha" 2)))
!(println! (HISTORY (memory-history "a")))
!(println! (FORGOT (forget "b")))
!(println! (HEALTH (semantic:health)))
''')
        self.assertIn('(MemoryHit "a"', output, output)
        self.assertIn('(MemoryQueued "a"', output, output)
        self.assertIn('(DRAIN true)', output, output)
        self.assertIn('"corrected beta Ω"', output, output)
        self.assertIn('(MemoryRevision "a"', output, output)
        self.assertIn('"old alpha"', output, output)
        self.assertIn('(MemoryForgotten "b")', output, output)
        self.assertIn('(MemoryHealth healthy native', output, output)
        actual = self.collection.get(ids=['a', 'b'], include=['documents', 'embeddings', 'metadatas'])
        self.assertEqual(actual['ids'], ['a'])
        self.assertEqual(actual['documents'], ['corrected beta Ω'])
        self.assertEqual([list(v) for v in actual['embeddings']], [[0., 1., 0.]])
        self.assertEqual(actual['metadatas'][0]['time'], 'original')
        self.assertIn('updated_at', actual['metadatas'][0])
        self.stop_store()
        self.server = subprocess.Popen([self.server_bin, '--socket', str(self.socket),
                                        '--data', str(self.home / 'data')],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        time.sleep(.1)
        restored = self.run_metta('!(println! (memory-read "a"))\n!(println! (memory-read "b"))\n')
        self.assertIn('"corrected beta Ω"', restored)
        self.assertIn('(MemoryCurrent "b" absent', restored)

    def test_failed_write_recovers_after_restart_without_manual_remember(self):
        self.failed = True
        output = self.run_metta('!(println! (memory-edit "a" "saved through outage Ω"))\n'
                                '!(fixture:drain 10)\n!(println! (PENDING (memory-pending)))\n')
        self.assertIn('(MemoryQueued "a"', output, output)
        self.assertIn('"saved through outage Ω"', output, output)
        self.assertIn('MemoryUnavailable embedding', output, output)
        self.assertEqual(self.collection.get(ids=['a'], include=['documents'])['documents'], ['old alpha'])
        self.stop_store()
        self.server = subprocess.Popen([self.server_bin, '--socket', str(self.socket),
                                        '--data', str(self.home / 'data')],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        time.sleep(.1)
        self.failed = False
        output = self.run_metta('!(println! (RECOVERED (fixture:drain 100)))\n'
                                '!(println! (memory-read "a"))\n')
        self.assertIn('(RECOVERED true)', output, output)
        self.assertIn('"saved through outage Ω"', output, output)
        self.assertEqual(self.collection.get(ids=['a'], include=['documents'])['documents'], ['saved through outage Ω'])

    def test_forget_and_new_edit_win_over_inflight_embedding(self):
        self.delay = .1
        output = self.run_metta('''
!(memory-edit "a" "obsolete in-flight alpha")
!(println! (forget "a" "withdrawn"))
!(memory-edit "b" "first superseded alpha")
!(memory-edit "b" "newest beta Ω")
!(println! (DRAIN (fixture:drain 100)))
!(println! (A (memory-read "a")))
!(println! (B (memory-read "b")))
''')
        self.assertIn('(MemoryCurrent "a" absent', output, output)
        self.assertIn('"newest beta Ω"', output, output)
        self.assertIn('(DRAIN true)', output, output)
        actual = self.collection.get(ids=['a', 'b'], include=['documents'])
        self.assertEqual(actual['ids'], ['b'])
        self.assertEqual(actual['documents'], ['newest beta Ω'])

    def test_retry_wakes_native_idle_without_model_polling(self):
        self.failures_left = 1
        self.env['METTACLAW_MEMORY_RETRY_SECONDS'] = '.12'
        output = self.run_metta('''
!(import! &self "''' + str(ROOT / 'src/idle_cetta.metta') + '''")
(= (mail:enabled?) False)
(= (fixture:idle-drain $n)
   (let $_ (semantic:maintain)
     (if (== (memory-pending) ()) $n
       (if (>= $n 8) (FixtureIdleFailed (memory-pending))
         (let* (($subscriptions (idle:arm 1000 True))
                ($_ (idle:wait $subscriptions 1000 True))
                ($_ (idle:finish $subscriptions)))
           (fixture:idle-drain (+ $n 1)))))))
!(memory-edit "a" "idle recovered beta")
!(println! (IDLE_WAKES (fixture:idle-drain 0)))
!(println! (memory-read "a"))
''')
        self.assertIn('"idle recovered beta"', output, output)
        self.assertNotIn('FixtureIdleFailed', output, output)
        self.assertRegex(output, r'\(IDLE_WAKES [123]\)')
        self.assertEqual(self.collection.get(ids=['a'], include=['documents'])['documents'],
                         ['idle recovered beta'])

    @unittest.skipIf(os.environ.get('CETTA_TEST_NO_PYTHON') == '1',
                     'full agent bootstrap still has unrelated legacy Python modules')
    def test_public_skills_through_full_agent_bootstrap(self):
        from test_command_dispatch_engine import CommandDispatchEngineTest
        env = CommandDispatchEngineTest()._cetta_env()
        env.update(self.env)
        # Keep the native selection from the real launch environment.
        env['METTACLAW_ENGINE'] = env['METTACLAW_ACTIVE_ENGINE'] = 'cetta'
        env['OPENAI_API_KEY'] = 'test-only-unused'
        probe = self.home / 'public.metta'
        probe.write_text('''
!(println! (PUBLIC_QUERY (query "alpha" 2)))
!(println! (PUBLIC_EDIT (memory-edit "a" "public corrected beta Ω")))
!(println! (PUBLIC_READ (memory-read "a")))
!(println! (PUBLIC_FORGET (forget "a" "fixture finished")))
!(println! (PUBLIC_HEALTH (memory-health)))
!(println! (PUBLIC_HELP (help "memory-edit")))
''')
        result = subprocess.run([self.engine, '--lang', 'petta', '--import-mode',
                                 'ancestor-walk', str(ROOT / 'cetta_bootstrap.metta'), str(probe)],
                                cwd=ROOT, env=env, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout[-3500:] + result.stderr[-2500:])
        self.assertIn('(MemoryHit "a"', result.stdout)
        self.assertIn('(MemoryQueued "a"', result.stdout)
        self.assertIn('public corrected beta Ω', result.stdout)
        self.assertIn('(MemoryForgotten "a")', result.stdout)
        self.assertIn('(MemoryHealth healthy native', result.stdout)
        self.assertIn('Revise semantic memory under the same ID', result.stdout)

    def test_disconnected_store_is_explicit_without_losing_records(self):
        self.stop_store()
        output = self.run_metta('''
!(println! (memory-edit "a" "uncommitted"))
!(println! (forget "a"))
!(println! (semantic:query "alpha" 2))
!(println! (semantic:health))
''')
        self.assertIn('MemoryUnavailable store', output, output)
        self.assertNotIn('MemoryQueued', output, output)
        self.assertNotIn('MemoryForgotten', output, output)

    def test_health_checks_embedding_and_accepts_an_empty_working_collection(self):
        output = self.run_metta('!(println! (semantic:health))\n')
        self.assertIn('(MemoryHealth healthy native 1', output, output)
        self.failed = True
        output = self.run_metta('!(println! (semantic:health))\n')
        self.assertIn('(MemoryHealth unavailable', output, output)
        self.assertNotIn('MemoryHealth healthy', output, output)
        self.failed = False
        output = self.run_metta('!(forget "a")\n!(forget "b")\n!(println! (semantic:health))\n')
        self.assertIn('(MemoryHealth healthy native 0', output, output)


if __name__ == '__main__':
    unittest.main()
