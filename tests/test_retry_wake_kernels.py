"""A concrete RetryWake application is rechecked by three native kernels."""

import hashlib
import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODEL_SHA256 = "6031c40a109ee667af2c27e04a1420d1406f27c4759449009f3405ed0e0bb812"
HOST_SHA256 = "688da2a801f231c618f3ba77d12613df34edd3ae05dd5557e8a6511892ebc981"
MODULE_SHA256 = "88c6713192cb30ed95013746cb627693da467b659d7fd1e9d1599c2df942d638"


class RetryWakeKernelsTest(unittest.TestCase):
    def test_host_correspondence_source_matches_the_receipt(self):
        source = ROOT / "src" / "native_lifetime.py"
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),
                         HOST_SHA256)
        module = ROOT / "verification" / "RetryWake.module"
        self.assertEqual(hashlib.sha256(module.read_bytes()).hexdigest(),
                         MODULE_SHA256)

    def test_external_lean_model_matches_when_supplied(self):
        source = os.environ.get("METTAPEDIA_RETRY_WAKE")
        if not source:
            self.skipTest("set METTAPEDIA_RETRY_WAKE to the canonical Lean model")
        self.assertEqual(hashlib.sha256(Path(source).read_bytes()).hexdigest(),
                         MODEL_SHA256)

    def test_hol_megalodon_and_mm0_check_the_application(self):
        executable = Path(os.environ.get("CETTA_BIN", ""))
        if not executable.is_file():
            self.skipTest("configure CETTA_BIN")
        source = ROOT / "verification" / "RetryWakeKernels.metta"
        expected = (
            "GodelClawCheckedTransitionV1 failedRequestHasNoActiveNativeCall "
            f'"{MODEL_SHA256}" "{HOST_SHA256}" '
            f'"{MODULE_SHA256}" '
            "(GcRequestStateV1 failed) (GcCallStateV1 submitted) "
            "(GcCallStateV1 uncertain) (kernels hol megalodon mm0)"
        )
        for language in ("he", "petta"):
            with self.subTest(language=language):
                result = subprocess.run(
                    [os.fspath(executable), "--lang", language,
                     "--import-mode", "ancestor-walk", os.fspath(source)],
                    cwd=ROOT, capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(result.returncode, 0,
                                 result.stdout + result.stderr)
                self.assertIn(expected, result.stdout)
                self.assertIn('hol-refused "REFUSED"', result.stdout)
                self.assertIn("mg-refused", result.stdout)
                self.assertIn('mm0-refused "REFUSED"', result.stdout)
                self.assertIn('mm0-refused-hol "MALFORMED"', result.stdout)
                self.assertNotIn("unexpected-", result.stdout)
                self.assertNotIn("leaked-cross-kernel-authority", result.stdout)

    def test_cogslt_module_computes_the_live_transition(self):
        executable = Path(os.environ.get("CETTA_BIN", ""))
        if not executable.is_file():
            self.skipTest("configure CETTA_BIN")
        source = ROOT / "verification" / "RetryWakeModule.metta"
        result = subprocess.run(
            [os.fspath(executable), "--lang", "petta", "--import-mode",
             "ancestor-walk", os.fspath(source)],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            f'GodelClawModuleApplicationV1 "{MODULE_SHA256}"',
            result.stdout,
        )
        self.assertIn("(CallUncertain)", result.stdout)
        self.assertIn('LangDef:RuleTrace 1 "FailedSubmitted"', result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
