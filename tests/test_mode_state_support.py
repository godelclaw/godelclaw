"""Static generated bundle fixtures only: mock descriptors, NEVER execute them."""
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

from effect_receipts_bundle import ENGINE_PIN, MANIFEST_PIN, digest
from mode_state_support import Qualification


class ModeStateSupportTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mode06-static-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.bundle = self.directory / "bundle"
        (self.bundle / "lib").mkdir(parents=True)
        self.binary = self.bundle / "cetta"
        self.binary.write_bytes(b"NOT an executable: static manifest fixture\n")
        self.library = self.bundle / "lib/leaf.metta"
        self.library.write_text("; static fixture\n")
        self.manifest = self.bundle / "SHA256SUMS"
        self.write_manifest()
        self.evidence = self.directory / "evidence"
        self.evidence.mkdir()

    def write_manifest(self):
        self.manifest.write_text("%s  cetta\n%s  lib/leaf.metta\n" %
                                 (digest(self.binary), digest(self.library)))

    def guard(self, **pins):
        with mock.patch.dict(os.environ, {"CETTA_BIN": str(self.binary), **pins}, clear=True):
            return Qualification(self.evidence, self.directory)

    def replace_bundle(self):
        self.library.write_text("; coherently replaced, still NOT executable\n")
        self.write_manifest()

    def test_optional_project06_pins(self):
        guard = self.guard()
        self.assertEqual(guard.pins, {ENGINE_PIN: "", MANIFEST_PIN: ""})
        guard = self.guard(MODE_STATE_ENGINE_SHA256=digest(self.binary),
                           MODE_STATE_MANIFEST_SHA256=digest(self.manifest))
        self.assertEqual(guard.identity("pins", "STATIC")["executable_sha256"], digest(self.binary))
        guard = self.guard(MODE_STATE_ENGINE_SHA256="0" * 64)
        with self.assertRaises(AssertionError):
            guard.identity("pins-rejected", "STATIC")

    def test_earlier_pins_are_not_silently_selected(self):
        guard = self.guard(EFFECT_RECEIPTS_ENGINE_SHA256="0" * 64)
        self.assertEqual(guard.pins[ENGINE_PIN], "")

    def test_all_three_descriptor_edges(self):
        guard = self.guard()
        with mock.patch.object(guard, "resolution") as resolution:
            with guard.phase("stable-static"):
                pass
        self.assertEqual(resolution.call_args_list,
                         [mock.call("stable-static", edge)
                          for edge in ("START", "PRE-END-RESOLUTION", "END")])
        for edge in ("START", "PRE-END-RESOLUTION", "PRE-FINAL-DESCRIPTOR", "END"):
            self.assertTrue((self.evidence / ("stable-static-" + edge + ".json")).is_file())

    def test_replacement_in_body_stops_before_more_descriptors(self):
        guard = self.guard()
        with mock.patch.object(guard, "resolution") as resolution:
            with self.assertRaises(AssertionError):
                with guard.phase("body-replaced"):
                    self.replace_bundle()
        self.assertEqual(resolution.call_args_list, [mock.call("body-replaced", "START")])

    def test_replacement_during_preend_stops_final_descriptor(self):
        guard = self.guard()
        def resolution(phase, edge):
            if edge == "PRE-END-RESOLUTION":
                self.replace_bundle()
        with mock.patch.object(guard, "resolution", side_effect=resolution) as observed:
            with self.assertRaises(AssertionError):
                with guard.phase("preend-replaced"):
                    pass
        self.assertEqual(observed.call_args_list, [mock.call("preend-replaced", "START"),
                                                  mock.call("preend-replaced", "PRE-END-RESOLUTION")])

    def test_replacement_during_end_is_rejected(self):
        guard = self.guard()
        def resolution(phase, edge):
            if edge == "END":
                self.replace_bundle()
        with mock.patch.object(guard, "resolution", side_effect=resolution):
            with self.assertRaises(AssertionError):
                with guard.phase("end-replaced"):
                    pass

    def test_environment_is_allowlisted_and_paths_disposable(self):
        guard = self.guard()
        with mock.patch.dict(os.environ, {"CETTA_ROOT": str(self.directory / "inherited-root"),
                                         "LD_PRELOAD": str(self.directory / "inherited-loader"),
                                         "TELEGRAM_BOT_TOKEN": "fixture-secret",
                                         "METTACLAW_ENGINE_STATE_PATH": str(self.directory / "inherited-state")}):
            env = guard.environment()
        self.assertNotIn("CETTA_ROOT", env)
        self.assertNotIn("LD_PRELOAD", env)
        self.assertNotIn("TELEGRAM_BOT_TOKEN", env)
        for key in ("HOME", "XDG_STATE_HOME", "METTACLAW_ENGINE_STATE_PATH",
                    "METTACLAW_LOOP_MODE_PATH", "METTACLAW_FUEL_MODE_PATH",
                    "METTACLAW_LIFECYCLE_PATH", "GOAL_BOOKKEEPING_COMPOST_PATH"):
            self.assertTrue(Path(env[key]).is_relative_to(self.directory), key)

    def test_home_unset_requires_explicit_test_request(self):
        guard = self.guard()
        self.assertIn("HOME", guard.environment())
        guard.unset_environment = ("HOME",)
        self.assertNotIn("HOME", guard.environment())

    def mode_sources(self):
        root = Path(__file__).resolve().parents[1] / "src"
        names = ("mode_runtime", "mode_text_cetta", "mode_values_cetta", "mode_io_cetta",
                 "mode_paths_cetta", "engine_active_cetta", "engine_modes_cetta", "attention_cetta",
                 "fuel_cetta")
        return [root / (name + ".metta") for name in names]

    def test_scoped_native_source_forms_are_balanced(self):
        # Shape check only; actual native return/effect gates remain required.
        for path in self.mode_sources():
            depth = 0
            for token in re.findall(r';[^\n]*|"(?:\\.|[^"\\])*"|[()]', path.read_text()):
                if token == "(": depth += 1
                elif token == ")": depth -= 1
                self.assertGreaterEqual(depth, 0, path.name)
            self.assertEqual(depth, 0, path.name)

    def test_scoped_native_sources_add_no_python_or_foreign_boundary(self):
        for path in self.mode_sources():
            source = re.sub(r';[^\n]*', "", path.read_text())
            self.assertNotRegex(source, r'\(py-call\b|\(import_prolog_function\b', path.name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
