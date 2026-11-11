import json
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

    def test_metta_authoring_writes_disables_and_enables_a_transformation(self):
        source = (
            "(= (iter-transform $visible) (success $visible))\n"
        )
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw) / "transformations"
            result = self.run_probe(directory)
            self.assertEqual(result.returncode, 0, result.stderr[-3000:])
            self.assertIn("ITER_AUTHORING_OK", result.stdout)
            # Each authoring call answers with its JSON receipt, printed as a
            # MeTTa string; the last one lists the transformations.
            documents = [json.loads(json.loads(line))
                         for line in result.stdout.splitlines()
                         if line.startswith('"{')]
            receipts = [doc for doc in documents if "operation" in doc]
            self.assertEqual([(doc["operation"], doc["active"]) for doc in receipts],
                             [("write", True), ("disable", False), ("enable", True)])
            self.assertEqual([entry["name"] for entry in documents[-1]["active"]],
                             ["10_probe.metta"])
            self.assertEqual(documents[-1]["disabled"], [])
            # A request prepared while the transformation is active observes
            # it; one prepared while it is disabled does not.
            prepared = [line for line in result.stdout.splitlines()
                        if line.startswith("(iter-native-result ")]
            self.assertEqual(len(prepared), 2, result.stdout[-3000:])
            self.assertIn('(iter-observation "10_probe.metta" success "")', prepared[0])
            self.assertTrue(prepared[1].endswith(" iter-observations-empty)"), prepared[1])
            self.assertEqual(
                (directory / "10_probe.metta").read_text(encoding="utf-8"),
                source,
            )
            self.assertFalse((directory / "_10_probe.metta").exists())


if __name__ == "__main__":
    unittest.main()
