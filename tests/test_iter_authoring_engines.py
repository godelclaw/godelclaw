import os
from isolated_env import isolated_environ  # noqa: E402
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class IterAuthoringEngineTests(unittest.TestCase):
    def run_probe(self, directory):
        env = isolated_environ()
        env.update({
            "METTACLAW_ENGINE": "petta",
            "METTACLAW_ITER_PROCESS_DIR": str(directory),
            "METTACLAW_SKIP_INITIALIZE": "1",
        })
        command = ["./run.sh", "tests/iter_authoring_probe.metta"]
        return subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=40,
        )

    def test_direct_metta_authoring_crosses_swi_petta_boundary(self):
        source = (
            "(= (iter-transform $visible) (success $visible))\n"
        )
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw) / "transformations"
            result = self.run_probe(directory)
            self.assertEqual(result.returncode, 0, result.stderr[-3000:])
            self.assertIn("ITER_AUTHORING_OK", result.stdout)
            self.assertIn('"state":"installed"', result.stdout)
            self.assertIn('"state":"disabled"', result.stdout)
            self.assertIn('"state":"enabled"', result.stdout)
            self.assertEqual(
                (directory / "10_probe.metta").read_text(encoding="utf-8"),
                source,
            )
            self.assertFalse((directory / "_10_probe.metta").exists())


if __name__ == "__main__":
    unittest.main()
