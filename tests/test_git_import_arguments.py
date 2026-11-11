#!/usr/bin/env python3
"""git-import!'s second argument is a build command, not a commit.

In SWI-PeTTa and CeTTa alike, (git-import! URL BUILD BASE) clones URL into
BASE and then runs BUILD in the clone.  A commit hash given there is run as
a command; it fails, and CeTTa then removes the half-made clone, so every
start clones and fails again.  CeTTa pins a commit only through a fourth
argument, which SWI-PeTTa does not have. Native agent boot needs no git
dependency; the local fixture retains this primitive's comparison contract.

Offline: the repository is a local one.  Environment: CETTA_BIN.
"""
import os
import pathlib
import re
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class GitImportArguments(unittest.TestCase):
    def test_boot_has_no_git_dependency(self):
        source = (ROOT / "lib_mettaclaw.metta").read_text(encoding="utf-8")
        calls = re.findall(r'!\(git-import!\s+"([^"]+)"\s+"([^"]*)"', source)
        self.assertEqual(calls, [], "native memory must not fetch a retired backend at boot")
        self.assertNotIn("(library petta_lib_chromadb", source)

    def test_second_argument_runs_as_a_build_command(self):
        cetta = os.environ.get("CETTA_BIN", "")
        if not cetta or not pathlib.Path(cetta).is_file():
            self.skipTest("set CETTA_BIN to a cetta with Python")
        with tempfile.TemporaryDirectory(prefix="git-import-") as tmp:
            tmp = pathlib.Path(tmp)
            origin = tmp / "petta_lib_probe"
            origin.mkdir()
            (origin / "lib_probe.metta").write_text("(= (probe-loaded) yes)\n")
            git = ["git", "-C", str(origin), "-c", "user.name=t",
                   "-c", "user.email=t@example.invalid"]
            subprocess.run(git[:3] + ["init", "-q"], check=True)
            subprocess.run(git + ["add", "lib_probe.metta"], check=True)
            subprocess.run(git + ["commit", "-qm", "probe"], check=True)
            commit = subprocess.run(git[:3] + ["rev-parse", "HEAD"], check=True,
                                    capture_output=True, text=True).stdout.strip()

            def boot(build):
                base = tmp / ("repos-" + (build or "empty"))
                program = tmp / "boot.metta"
                program.write_text(
                    '!(import! &self (library lib_import))\n'
                    '!(git-import! "file://%s" "%s" "%s")\n'
                    '!(import! &self (library petta_lib_probe lib_probe))\n'
                    '!(probe-loaded)\n' % (origin, build, base))
                run = subprocess.run([cetta, "--lang", "petta", str(program)],
                                     capture_output=True, text=True, timeout=120)
                return run.stdout + run.stderr, base / "petta_lib_probe"

            output, clone = boot(commit)
            self.assertIn("Running build: %s" % commit, output)
            self.assertIn("build step failed", output)
            self.assertFalse(clone.exists(), "the failed clone was kept")

            output, clone = boot("")
            self.assertNotIn("Running build", output)
            self.assertTrue((clone / "lib_probe.metta").is_file(), output)
            self.assertIn("yes", output)


if __name__ == "__main__":
    unittest.main()
