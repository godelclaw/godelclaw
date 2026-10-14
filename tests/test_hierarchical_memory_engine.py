"""Native runtime adapter, including automatic capture and context recovery."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HierarchicalMemoryEngineTest(unittest.TestCase):
    def test_real_dispatcher_and_prompt_use_native_memory(self):
        engine = os.environ.get('CETTA_BIN')
        server = os.environ.get('CETTA_SPACE_SERVICE')
        if not engine or not server:
            self.skipTest('set CETTA_BIN and CETTA_SPACE_SERVICE')
        linked = subprocess.run(['ldd', engine], capture_output=True, text=True)
        if 'libpython' not in linked.stdout:
            self.skipTest('the grandfathered full driver requires the Python-enabled engine')
        from test_command_dispatch_engine import CommandDispatchEngineTest
        environment = CommandDispatchEngineTest()._cetta_env()
        with tempfile.TemporaryDirectory(prefix='hierarchy-dispatch-') as directory:
            home = Path(directory)
            socket = home / 'memory.sock'
            process = subprocess.Popen([server, '--socket', str(socket), '--data', str(home / 'data')],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                until = time.monotonic() + 10
                while not socket.exists():
                    self.assertIsNone(process.poll())
                    self.assertLess(time.monotonic(), until)
                    time.sleep(.01)
                environment.update(METTACLAW_HIERARCHY_SOCKET=str(socket), METTACLAW_HIERARCHY_SPACE='driver',
                                   METTACLAW_OBSERVED_STRATEGY='0', METTACLAW_MAIL_SOCKET='',
                                   METTACLAW_HISTORY_PATH=str(home / 'history'),
                                   METTACLAW_TELEGRAM_LOG_PATH=str(home / 'updates'))
                probe = home / 'driver.metta'
                probe.write_text('''
!(println! (run-command-batch-once 424242 99 (quote
  ((memory-node "e" episode "Evidence" "literal Ω finding")
   (memory-node "t" task "Task" "Original objective" ("e"))
   (memory-summary "t" "interpretation")
   (memory-focus "t")
   (memory-open "t" 1)))))
!(println! (PROMPT (addition-protected-request "base-request" 128)))
!(println! HIERARCHY_DISPATCH_OK)
''')
                result = subprocess.run([engine, '--lang', 'petta', '--import-mode', 'ancestor-walk',
                                         str(ROOT / 'cetta_bootstrap.metta'), str(probe)],
                                        cwd=ROOT, env=environment, text=True, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                for bad in ('COMMAND_EXCEPTION:', 'COMMAND_ERROR:', '(Error ', 'HIERARCHY_ERROR'):
                    self.assertNotIn(bad, result.stdout, result.stdout)
                self.assertIn('HIERARCHY_DISPATCH_OK', result.stdout)
                self.assertIn('HIERARCHICAL_MEMORY', result.stdout)
                self.assertIn('literal Ω finding', result.stdout)
                self.assertIn('base-request', result.stdout)
            finally:
                process.terminate()
                process.communicate(timeout=10)

    def test_native_capture_interruption_focus_and_restart(self):
        engine = os.environ.get('CETTA_BIN')
        server = os.environ.get('CETTA_SPACE_SERVICE')
        if not engine or not server:
            self.skipTest('set CETTA_BIN and CETTA_SPACE_SERVICE for native qualification')
        with tempfile.TemporaryDirectory(prefix='hierarchy-runtime-') as directory:
            home = Path(directory)
            socket = home / 'memory.sock'
            environment = dict(os.environ, METTACLAW_HIERARCHY_SOCKET=str(socket),
                               METTACLAW_HIERARCHY_SPACE='qualification',
                               METTACLAW_MEMORY_PROJECT='project', METTACLAW_INSTANCE='fixture')
            def start():
                process = subprocess.Popen([server, '--socket', str(socket), '--data', str(home / 'data')],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                until = time.monotonic() + 10
                while not socket.exists():
                    if process.poll() is not None:
                        self.fail(process.stderr.read().decode())
                    self.assertLess(time.monotonic(), until)
                    time.sleep(.01)
                return process
            def run(body):
                source = home / 'probe.metta'
                source.write_text('!(import! &self ' + json.dumps(str(ROOT / 'src/hierarchical_memory_cetta.metta')) + ')\n' + body)
                result = subprocess.run([engine, '--lang', 'petta', str(source)], cwd=ROOT,
                                        env=environment, text=True, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                self.assertNotIn('HIERARCHY_ERROR', result.stdout, result.stdout)
                self.assertNotIn('(Error ', result.stdout, result.stdout)
                return result.stdout
            process = start()
            try:
                output = run('''
!(hierarchy:begin 1 operator-event "{\\"focus\\":\\"task-A\\"}")
!(hierarchy:record 1 operator-event "Original A request" "Response A" (quote ((read-source "theorem") (returned "finding-one"))))
!(memory-summary "task-A" "finding-one; waiting for review")
!(hierarchy:begin 2 operator-event "{\\"focus\\":\\"task-B\\"}")
!(hierarchy:record 2 operator-event "Short interruption" "Answer B" (quote ((returned "B"))))
!(hierarchy:begin 3 continuation-tick "{\\"focus\\":\\"task-A\\"}")
!(hierarchy:record 3 continuation-tick "" "Resume A" (quote ((returned "finding-two"))))
!(println! (BEFORE (hierarchy:context)))
!(memory-focus "project" 2)
!(memory-window 30)
!(println! (EXCERPT (hierarchy:context)))
!(memory-window 0)
!(println! (RECOVERED (memory-open "project")))
''')
                self.assertIn('HOverview stale "finding-one; waiting for review"', output)
                self.assertIn('context excerpt; expand with memory-open', output)
                self.assertIn('finding-one', output)
                self.assertIn('finding-two', output)
                self.assertIn('Short interruption', output)
                self.assertIn('Original A request', output)
                process.terminate()
                process.communicate(timeout=10)
                process = start()
                output = run('''
!(println! (RESTART (hierarchy:context)))
!(println! (ORIGINAL (memory-original "task-A" 1)))
!(println! (EXPANDED (memory-open "project")))
''')
                self.assertIn('HIERARCHICAL_MEMORY', output)
                self.assertIn('"project"', output)
                self.assertIn('Original A request', output)
                self.assertIn('finding-one', output)
                self.assertIn('finding-two', output)
            finally:
                if process.poll() is None:
                    process.terminate()
                process.communicate(timeout=10)

    def test_help_registry_advertises_the_native_commands(self):
        spec = importlib.util.spec_from_file_location('hierarchy_help', ROOT / 'src/skills_help.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for command in ('memory-node', 'memory-link', 'memory-summary', 'memory-open',
                        'memory-find', 'memory-original', 'memory-focus', 'memory-window'):
            self.assertIn(command, module.ARITY)
            self.assertIn(command, module.PAGES)


if __name__ == '__main__':
    unittest.main()
