"""Native launcher state against the existing mode implementation."""
import hermetic_env  # noqa: F401,E402
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import loop_modes
import engine_modes


@unittest.skipUnless(os.environ.get('CETTA_BIN'), 'requires a native engine')
class NativeBootStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='boot-state-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.mode = self.root / 'mode.json'
        self.active = self.root / 'state/active-loop.json'
        self.environment = dict(PATH=os.environ.get('PATH', '/usr/bin:/bin'), HOME=str(self.root),
                                METTACLAW_LOOP_MODE_PATH=str(self.mode),
                                METTACLAW_ENGINE_STATE_PATH=str(self.root / 'state/engine'),
                                METTACLAW_RECYCLE_REQUEST_PATH=str(self.root / 'recycle'),
                                METTACLAW_INSTANCE='fixture')

    def run_native(self, operation, **fields):
        environment = dict(self.environment, METTACLAW_BOOT_OPERATION=operation)
        environment.update(fields)
        return subprocess.run([os.environ['CETTA_BIN'], '--no-results', '--lang', 'petta',
                                '--profile', 'extended', '--import-mode', 'ancestor-walk',
                                str(ROOT / 'scripts/loop_bootstrap_cetta.metta')],
                               env=environment, capture_output=True, text=True, timeout=15)

    def test_current_selection_matches_the_python_oracle_without_publishing(self):
        for value in ({}, None, [1], {'mode': 'CODING'}, {'mode': 'iter-coding'},
                      {'mode': 'omega'}, {'mode': 'iter'}, {'mode': False},
                      {'mode': 3}, {'mode': ['coding']}, {'mode': 'unknown'}):
            with self.subTest(value=value):
                self.mode.write_text(json.dumps(value))
                with mock.patch.dict(os.environ, self.environment, clear=True):
                    expected = loop_modes.current_mode()
                result = self.run_native('current')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected + '\n')
                self.assertFalse(self.active.exists())
        self.mode.write_text('{torn')
        self.assertEqual(self.run_native('current').stdout, 'godelclaw\n')

    def test_capture_records_exactly_the_selected_framework_mode_and_launcher(self):
        self.mode.write_text('{"mode":"iter-coding"}')
        pid = os.getpid()
        before = time.time()
        result = self.run_native('capture', METTACLAW_BOOT_PARENT_PID=str(pid))
        after = time.time()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'coding\n')
        native = json.loads(self.active.read_text())
        self.assertLessEqual(before, native['started_at'])
        self.assertLessEqual(native['started_at'], after)
        with mock.patch.dict(os.environ, self.environment, clear=True):
            loop_modes.record_active(loop_modes.current_mode(), pid)
        reference = json.loads(self.active.read_text())
        self.assertEqual({k: v for k, v in native.items() if k != 'started_at'},
                         {k: v for k, v in reference.items() if k != 'started_at'})

    def test_standalone_selection_preserves_the_previous_active_owner(self):
        self.active.parent.mkdir()
        self.active.write_text('previous owner\n')
        for mode in ('iter', 'omega'):
            self.mode.write_text(json.dumps({'mode': mode}))
            result = self.run_native('capture', METTACLAW_BOOT_PARENT_PID='12345')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, mode + '\n')
            self.assertEqual(self.active.read_text(), 'previous owner\n')

    def test_invalid_parent_refuses_without_a_partial_active_record(self):
        self.active.parent.mkdir()
        self.active.write_text('previous owner\n')
        result = self.run_native('capture', METTACLAW_BOOT_PARENT_PID='not-a-pid')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, '')
        self.assertEqual(self.active.read_text(), 'previous owner\n')
        self.assertEqual(list(self.active.parent.glob('*.tmp.*')), [])

    def test_publication_fault_does_not_emit_a_selected_mode(self):
        self.active.parent.write_text('not a directory')
        result = self.run_native('capture', METTACLAW_BOOT_PARENT_PID=str(os.getpid()))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, '')
        self.assertEqual(self.active.parent.read_text(), 'not a directory')

    def test_captured_policy_survives_a_new_selection_until_the_restart(self):
        self.mode.write_text('{"mode":"godelclaw"}')
        captured = self.run_native('capture', METTACLAW_BOOT_PARENT_PID=str(os.getpid()))
        self.assertEqual(captured.returncode, 0, captured.stderr)
        previous = self.active.read_bytes()
        self.mode.write_text('{"mode":"coding"}')
        result = self.run_native('recycle', METTACLAW_ACTIVE_LOOP_MODE=captured.stdout.strip())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.active.read_bytes(), previous)
        self.assertEqual(json.loads(previous)['mode'], 'godelclaw')

    def test_recycle_uses_the_file_witness_or_a_changed_captured_policy(self):
        flag = Path(self.environment['METTACLAW_RECYCLE_REQUEST_PATH'])
        for requested, active, kind in (('godelclaw', 'godelclaw', 'absent'),
                                         ('coding', 'godelclaw', 'absent'),
                                         ('coding', '', 'absent'),
                                         ('godelclaw', 'godelclaw', 'directory'),
                                         ('godelclaw', '', 'file')):
            with self.subTest(requested=requested, active=active, kind=kind):
                if flag.is_dir(): flag.rmdir()
                elif flag.exists(): flag.unlink()
                if kind == 'directory': flag.mkdir()
                if kind == 'file': flag.write_text('requested')
                self.mode.write_text(json.dumps({'mode': requested}))
                environment = dict(self.environment, METTACLAW_ACTIVE_LOOP_MODE=active)
                with mock.patch.dict(os.environ, environment, clear=True):
                    expected = engine_modes.recycle_requested()
                result = self.run_native('recycle', METTACLAW_ACTIVE_LOOP_MODE=active)
                self.assertEqual(result.returncode, 0 if expected else 1, result.stderr)
                self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
