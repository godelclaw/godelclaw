"""Existing state formats survive native writes, restarts and larger pins."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import test_command_dispatch_engine as dispatch
ROOT = dispatch.ROOT

class NativeState(unittest.TestCase):
    def guest(self, directory, code):
        harness = dispatch.CommandDispatchEngineTest(); binary = harness._cetta_binary()
        if not binary.is_file(): self.skipTest('set CETTA_BIN')
        env = harness._cetta_env()
        env.update(METTACLAW_PINS_PATH=str(directory / 'pins.txt'),
                   METTACLAW_ENGINE_STATE_PATH=str(directory / 'engine'))
        with tempfile.NamedTemporaryFile('w', dir=ROOT, suffix='.metta') as source:
            source.write('!(import! &self (library lib_import))\n!(import! &self ./src/state_cetta)\n'+code)
            source.flush()
            result = subprocess.run([str(binary), '--lang', 'petta', '--import-mode',
                                     'ancestor-walk', source.name], cwd=ROOT, env=env,
                                    capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('STATE_OK', result.stdout, result.stdout + result.stderr)
        self.assertNotIn('Assertion failed', result.stderr)
        return result.stdout

    def test_full_pins_and_agent_selected_removal_survive_restart(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            original = '[01-01 00:00] old evidence λ'
            (directory / 'pins.txt').write_text(original + '\n')
            long = 'retained ' + 'λ' * 700
            code = '\n'.join('!(pin '+json.dumps('pin-%02d'%i)+')' for i in range(20))
            code += '\n!(pin '+json.dumps(long, ensure_ascii=False)+')\n!(println! STATE_OK)'
            self.guest(directory, code)
            lines = (directory / 'pins.txt').read_text().splitlines()
            self.assertEqual(len(lines), 22)
            self.assertEqual(lines[0], original)
            self.assertIn(long, lines[-1])
            output = self.guest(directory, '!(println! (pins))\n!(unpin "pin-00")\n!(println! STATE_OK)')
            self.assertIn(long, output)
            lines = (directory / 'pins.txt').read_text().splitlines()
            self.assertEqual(len(lines), 21)
            self.assertEqual(lines[0], original)

    @unittest.skipIf(os.geteuid() == 0, 'root bypasses the unreadable-file fixture')
    def test_unreadable_pins_are_not_replaced_by_pin_or_unpin(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            path = directory / 'pins.txt'
            original = '[01-01 00:00] retain existing evidence\n'
            for command in ('pin', 'unpin'):
                with self.subTest(command=command):
                    path.write_text(original)
                    path.chmod(0o200)
                    try:
                        output = self.guest(directory,
                            '!(println! (pins))\n!(println! (' + command +
                            ' "new note"))\n!(println! STATE_OK)')
                    finally:
                        path.chmod(0o600)
                    self.assertIn('pins unavailable:', output)
                    self.assertIn(command + ' failed:', output)
                    self.assertEqual(path.read_text(), original)

    def test_missing_pin_file_can_be_initialized(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            output = self.guest(directory,
                '!(println! (pins))\n!(println! (pin "first pin"))\n!(println! STATE_OK)')
            self.assertIn('(no pins)', output)
            self.assertIn('pinned 1', output)
            self.assertIn('first pin', (directory / 'pins.txt').read_text())

    def test_context_target_changes_preserve_other_settings(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            path = directory / 'context-policy.json'
            path.write_text(json.dumps(dict(task_tokens=32768, retained_field='λ')))
            # Zero, like "full", returns to the whole model window; a count opts into a budget.
            self.guest(directory, '!(println! (context-policy 0))\n!(println! STATE_OK)')
            self.assertEqual(json.loads(path.read_text()), dict(mode='full', retained_field='λ'))
            self.guest(directory, '!(println! (context-policy 120000))\n!(println! STATE_OK)')
            self.assertEqual(json.loads(path.read_text()), dict(mode='budget', task_tokens=120000, retained_field='λ'))
            self.guest(directory, '!(println! (context-policy "full"))\n!(println! STATE_OK)')
            self.assertEqual(json.loads(path.read_text()), dict(mode='full', retained_field='λ'))

    def test_invalid_existing_settings_are_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d); path = directory / 'context-policy.json'
            path.write_text('retained but invalid JSON')
            self.guest(directory, '!(println! (catch (context-policy 42)))\n!(println! STATE_OK)')
            self.assertEqual(path.read_text(), 'retained but invalid JSON')

if __name__ == '__main__': unittest.main()
