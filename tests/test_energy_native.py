"""Energy settings use the native writer and retain their existing JSON format."""
import concurrent.futures
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import test_command_dispatch_engine as dispatch
ROOT = dispatch.ROOT


class NativeEnergy(unittest.TestCase):
    def setUp(self):
        harness = dispatch.CommandDispatchEngineTest()
        self.binary = harness._cetta_binary()
        if not self.binary.is_file():
            self.skipTest("set CETTA_BIN")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.path = self.work / "memory" / "energy.json"
        self.env = harness._cetta_env()
        self.env.update(METTACLAW_ENERGY_PATH=str(self.path),
                        XDG_STATE_HOME=str(self.work / "state"),
                        METTACLAW_ENGINE_STATE_PATH=str(self.work / "engine"))

    def run_energy(self, expression):
        with tempfile.NamedTemporaryFile("w", dir=ROOT, suffix=".metta") as source:
            source.write('!(import! &self (library lib_import))\n'
                         '!(import! &self ./channels/energy)\n'
                         '!(import! &self ./tests/helper_port_probe.py)\n'
                         '!(py-call (helper_port_probe.forbid_python_helpers))\n'
                         f'!(let $answers (collapse {expression}) '
                         '(println! (EnergyResult $answers)))\n'
                         '!(println! ENERGY_NATIVE_OK)\n')
            source.flush()
            result = subprocess.run([str(self.binary), "--lang", "petta", "--import-mode",
                                     "ancestor-walk", source.name], env=self.env, cwd=ROOT,
                                    text=True, capture_output=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ENERGY_NATIVE_OK", result.stdout)
        self.assertNotIn("Error", result.stdout + result.stderr)
        return result.stdout

    def test_settings_preserve_unknown_fields_and_mode_across_restarts(self):
        self.path.parent.mkdir()
        original = dict(default="full", senders={"7": "light"}, names={"7": "λ 🌸"},
                        tier_loops={"mid": 37}, extension={"kept": [1, 2]})
        self.path.write_text(json.dumps(original))
        self.path.chmod(0o640)
        self.assertIn('(EnergyResult ("default arming set to mid(37)"))',
                      self.run_energy('(energy:set "default" "mid")'))
        self.assertEqual(json.loads(self.path.read_text()), dict(original, default="mid"))
        self.assertIn('(EnergyResult ("λ 🌸 arming set to full(50)"))',
                      self.run_energy('(energy:set "7" "full")'))
        original["default"] = "mid"
        original["senders"]["7"] = "full"
        self.assertEqual(json.loads(self.path.read_text()), original)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])
        self.assertFalse((self.work / "state").exists())

    def test_missing_parent_is_created(self):
        self.assertIn('(EnergyResult ("default arming set to mid(30)"))',
                      self.run_energy('(energy:set "default" "mid")'))
        self.assertEqual(json.loads(self.path.read_text())["default"], "mid")
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_sender_names_and_negative_ids_use_byte_compatibility(self):
        self.env["METTACLAW_TELEGRAM_SENDER_NAMES"] = "-7:λ 🌸"
        self.assertIn('(EnergyResult ("λ 🌸 arming set to light(10)"))',
                      self.run_energy('(energy:set "λ 🌸" "light")'))
        self.assertEqual(json.loads(self.path.read_text())["senders"], {"-7": "light"})
        self.assertIn('(EnergyResult ("λ 🌸 arming set to mid(30)"))',
                      self.run_energy('(energy:set "-7" "mid")'))
        self.assertEqual(json.loads(self.path.read_text())["senders"], {"-7": "mid"})

    def test_unwritable_parent_is_a_failed_save(self):
        self.path.parent.write_text("retained evidence")
        self.assertIn('(EnergyResult ("energy-set failed: could not persist"))',
                      self.run_energy('(energy:set "default" "mid")'))
        self.assertEqual(self.path.parent.read_text(), "retained evidence")

    def test_concurrent_whole_state_writes_have_no_shared_temporary_file(self):
        # Atomic replacement is last-writer-wins, not a read/modify/write lock.
        payloads = [dict(writer=i, retained="λ" * 4096) for i in range(4)]
        expressions = ['(energy:save (json-parse ' + json.dumps(json.dumps(p)) + '))'
                       for p in payloads]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(self.run_energy, expressions))
        for output in results:
            self.assertIn('(energyresult (true))', output.lower())
        self.assertIn(json.loads(self.path.read_text()), payloads)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])


if __name__ == "__main__":
    unittest.main()
