"""Static disposable manifest fixtures, NEVER executed as engine artifacts."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from effect_receipts_bundle import (ENGINE_PIN, MANIFEST_PIN, assert_same_bundle,
                                    bundle_identity, digest)
from effect_receipts_capability_checkpoint import Checkpoint


class BundleIdentityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="effect05-manifest-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.root = self.directory / "bundle"
        (self.root / "lib").mkdir(parents=True)
        self.binary = self.root / "cetta"
        self.binary.write_bytes(b"static manifest fixture, not an executable\n")
        self.leaf = self.root / "lib" / "leaf.metta"
        self.leaf.write_text("; generated manifest fixture\n")
        self.listed = {"cetta": digest(self.binary), "lib/leaf.metta": digest(self.leaf)}
        self.manifest = self.root / "SHA256SUMS"
        self.write_sums()

    def write_sums(self, pairs=None):
        self.manifest.write_text("".join("%s  %s\n" % (value, key)
                                         for key, value in (pairs or self.listed.items())))

    def identity(self, pins=None):
        return bundle_identity(self.binary, pins or {})

    def test_sha_manifest_no_pins_and_own_membership(self):
        result = self.identity()
        self.assertEqual(result["library_files"], ["lib/leaf.metta"])
        self.assertEqual(dict(result["files"]), self.listed)

    def test_pins_come_from_supplied_environment_mapping(self):
        pins = {ENGINE_PIN: digest(self.binary), MANIFEST_PIN: digest(self.manifest)}
        self.assertEqual(self.identity(pins)["executable_sha256"], pins[ENGINE_PIN])
        for key in pins:
            with self.subTest(key=key), self.assertRaises(AssertionError):
                self.identity({**pins, key: "0" * 64})

    def test_invalid_pin_rejected(self):
        with self.assertRaises(AssertionError):
            self.identity({ENGINE_PIN: "not-a-sha256"})

    def test_build_manifest_files_map_and_listed_extra_files(self):
        self.manifest.unlink()
        extra = self.root / "README"
        extra.write_text("generated fixture")
        listed = {**self.listed, "README": digest(extra)}
        (self.root / "build-manifest.json").write_text(json.dumps({"files": listed}))
        result = self.identity()
        self.assertEqual(result["manifest_name"], "build-manifest.json")
        self.assertEqual(dict(result["files"]), listed)

    def test_duplicate_build_manifest_keys_rejected(self):
        self.manifest.unlink()
        (self.root / "build-manifest.json").write_text('{"files": {}, "files": {}}')
        with self.assertRaises(AssertionError):
            self.identity()

    def test_duplicate_sha_names_rejected(self):
        pairs = list(self.listed.items()) + [next(iter(self.listed.items()))]
        self.write_sums(pairs)
        with self.assertRaises(AssertionError):
            self.identity()

    def test_unlisted_library_file_rejected(self):
        (self.root / "lib" / "extra.metta").write_text("; unlisted")
        with self.assertRaises(AssertionError):
            self.identity()

    def test_missing_listed_library_file_rejected(self):
        self.leaf.unlink()
        with self.assertRaises((AssertionError, FileNotFoundError)):
            self.identity()

    def test_changed_library_hash_rejected(self):
        self.leaf.write_text("; changed")
        with self.assertRaises(AssertionError):
            self.identity()

    def test_executable_must_be_listed(self):
        self.listed.pop("cetta")
        self.write_sums()
        with self.assertRaises(AssertionError):
            self.identity()

    def test_unsafe_paths_rejected(self):
        for path in ("../outside", str(self.directory / "outside"),
                     "lib/../outside", "lib//leaf.metta", "lib/./leaf.metta", "lib\\leaf.metta"):
            with self.subTest(path=path):
                self.write_sums([*self.listed.items(), (path, digest(self.leaf))])
                with self.assertRaises(AssertionError):
                    self.identity()

    def test_symlinked_library_file_rejected(self):
        original = self.directory / "original"
        self.leaf.rename(original)
        self.leaf.symlink_to(original)
        with self.assertRaises(AssertionError):
            self.identity()

    def test_symlinked_library_ancestor_rejected(self):
        original = self.directory / "original-lib"
        (self.root / "lib").rename(original)
        (self.root / "lib").symlink_to(original, target_is_directory=True)
        with self.assertRaises(AssertionError):
            self.identity()

    def test_manifest_symlink_rejected(self):
        original = self.directory / "original-manifest"
        self.manifest.rename(original)
        self.manifest.symlink_to(original)
        with self.assertRaises(AssertionError):
            self.identity()

    def test_executable_symlink_rejected(self):
        original = self.directory / "original-binary"
        self.binary.rename(original)
        self.binary.symlink_to(original)
        with self.assertRaises(AssertionError):
            self.identity()

    def test_missing_manifest_rejected(self):
        self.manifest.unlink()
        with self.assertRaises(FileNotFoundError):
            self.identity()

    def test_invalid_hash_rejected(self):
        self.listed["lib/leaf.metta"] = "bad"
        self.write_sums()
        with self.assertRaises(AssertionError):
            self.identity()

    def test_manifest_only_change_is_a_phase_change(self):
        start = self.identity()
        self.write_sums(list(reversed(list(self.listed.items()))))
        end = self.identity()
        self.assertEqual(start["files"], end["files"])
        with self.assertRaises(AssertionError):
            assert_same_bundle(start, end)

    def static_guard(self):
        # Bypass constructor/environment selection; this is a control-flow
        # fixture. resolution is mocked, and no fixture binary is executed.
        guard = Checkpoint.__new__(Checkpoint)
        guard.evidence = self.directory / "evidence"
        guard.evidence.mkdir()
        guard.directory = self.directory
        guard.binary = self.binary
        guard.bundle = self.root
        guard.pins = {}
        guard._phase_start = None
        return guard

    def test_stable_phase_checks_all_edges(self):
        guard = self.static_guard()
        with mock.patch.object(guard, "resolution") as resolution:
            with guard.phase("static-stable"):
                pass
        self.assertEqual(resolution.call_args_list,
                         [mock.call("static-stable", "START"), mock.call("static-stable", "END")])
        for edge in ("START", "PRE-END-RESOLUTION", "END"):
            self.assertTrue((guard.evidence / ("static-stable-" + edge + ".json")).is_file())

    def test_coherent_midphase_replacement_stops_before_final_process(self):
        guard = self.static_guard()
        with mock.patch.object(guard, "resolution") as resolution:
            with self.assertRaises(AssertionError):
                with guard.phase("static-replaced"):
                    self.leaf.write_text("; coherent but different fixture")
                    self.listed["lib/leaf.metta"] = digest(self.leaf)
                    self.write_sums()
        self.assertEqual(resolution.call_args_list, [mock.call("static-replaced", "START")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
