"""Relocated runtime bundles retain owned-process support."""
from pathlib import Path
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_cetta


class BuildCettaTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cetta-bundle-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.prefix = self.root / "source", self.root / "bundle"
        self.source.mkdir()
        self.artifacts = []
        for index in range(5):
            name = "runtime/resolved-" + str(index)
            path = self.source / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(("artifact-" + str(index)).encode())
            path.chmod(0o755)
            self.artifacts.append(name)

    def test_every_relocated_executable_gets_the_resolved_executable_helper(self):
        # These stale siblings must not replace Make's explicitly resolved
        # helper, whose source name may differ from installed names.
        (self.source / (self.artifacts[0] + ".proc-scope")).write_bytes(b"stale helper")
        build_cetta.install_runtime_artifacts(self.source, self.prefix, self.artifacts)
        expected = set(build_cetta.RUNTIME_NAMES) | {name + ".proc-scope" for name in build_cetta.RUNTIME_NAMES}
        self.assertEqual({path.name for path in self.prefix.iterdir()}, expected)
        for index, name in enumerate(build_cetta.RUNTIME_NAMES):
            self.assertEqual((self.prefix / name).read_bytes(), ("artifact-" + str(index)).encode())
            helper = self.prefix / (name + ".proc-scope")
            self.assertEqual(helper.read_bytes(), b"artifact-4")
            self.assertEqual(stat.S_IMODE(helper.stat().st_mode), 0o755)

    def test_missing_helper_is_reported_before_an_existing_bundle_is_modified(self):
        self.prefix.mkdir()
        marker = self.prefix / "cetta"
        marker.write_bytes(b"prior installed engine")
        (self.source / self.artifacts[-1]).unlink()
        with self.assertRaisesRegex(RuntimeError, "missing or not executable"):
            build_cetta.install_runtime_artifacts(self.source, self.prefix, self.artifacts)
        self.assertEqual(marker.read_bytes(), b"prior installed engine")
        self.assertEqual(list(self.prefix.iterdir()), [marker])

    def test_nonexecutable_helper_is_not_silently_installed(self):
        (self.source / self.artifacts[-1]).chmod(0o644)
        with self.assertRaisesRegex(RuntimeError, "not executable"):
            build_cetta.install_runtime_artifacts(self.source, self.prefix, self.artifacts)
        self.assertFalse(self.prefix.exists())


if __name__ == "__main__":
    unittest.main()
