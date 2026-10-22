import os
from isolated_env import isolated_environ  # noqa: E402
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PETTA_ROOT = Path(
    os.environ.get("PETTA_ROOT", Path.home() / "repos" / "PeTTa")
)
PETTA_ENV = Path(
    os.environ.get("PETTA_PY_ENV", Path.home() / "miniforge3/envs/petta")
)


APPEND = """\
(= (iter-transform
      (coding-view $messages $advertised))
   (success
     (coding-view
       (coding-message-append-one
         $messages (coding-message user %s))
       $advertised)))
"""


@unittest.skipUnless(
    (PETTA_ROOT / "run.sh").is_file(), "SWI-PeTTa checkout is unavailable"
)
class NativeIterProcessTests(unittest.TestCase):
    def run_request(self, directory: Path, timeout: str = "2") -> str:
        environment = isolated_environ()
        environment.update({
            "LD_LIBRARY_PATH": os.pathsep.join(filter(None, (
                str(PETTA_ENV / "lib"), environment.get("LD_LIBRARY_PATH")
            ))),
            "PYTHONHOME": str(PETTA_ENV),
            "PYTHONNOUSERSITE": "1",
            "PATH": os.pathsep.join((
                str(PETTA_ENV / "bin"), environment.get("PATH", "")
            )),
            "METTACLAW_PETTA_RUNNER": str(PETTA_ROOT / "run.sh"),
            "METTACLAW_ITER_PROCESS_DIR": str(directory),
            "METTACLAW_ITER_PROCESS_TIMEOUT_SECONDS": timeout,
        })
        result = subprocess.run(
            ["/bin/sh", str(PETTA_ROOT / "run.sh"),
             "tests/native_iter_request_probe.metta", "--silent"],
            cwd=ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_ordered_replace_or_stutter_fold(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            (directory / "10_first.metta").write_text(
                APPEND % "first", encoding="utf-8"
            )
            (directory / "20_failure.metta").write_text(
                "(= (iter-transform $visible) failure)\n", encoding="utf-8"
            )
            (directory / "30_second.metta").write_text(
                APPEND % "second", encoding="utf-8"
            )

            output = self.run_request(directory)

        self.assertLess(output.index("user: first"), output.index("user: second"))
        self.assertIn("20_failure.metta failure", output)
        self.assertIn("30_second.metta success", output)

    def test_next_capture_reloads_metta_source(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            transform = directory / "10_value.metta"
            transform.write_text(APPEND % "before", encoding="utf-8")
            before = self.run_request(directory)
            transform.write_text(APPEND % "after", encoding="utf-8")
            after = self.run_request(directory)

        self.assertIn("user: before", before)
        self.assertNotIn("user: after", before)
        self.assertIn("user: after", after)
        self.assertNotIn("user: before", after)

    def test_nonterminating_transform_times_out_and_stutters(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            (directory / "10_spin.metta").write_text(
                "(= (native-iter-spin) (native-iter-spin))\n"
                "(= (iter-transform $visible) (native-iter-spin))\n",
                encoding="utf-8",
            )

            output = self.run_request(directory, timeout="0.2")

        self.assertIn("10_spin.metta failure", output)
        self.assertIn("timeout", output)
        self.assertNotIn("user: before", output)

    def test_runtime_path_has_no_python_iter_executor(self):
        policy = (ROOT / "src" / "iter_process_policy.metta").read_text(
            encoding="utf-8"
        )
        library = (ROOT / "lib_mettaclaw.metta").read_text(encoding="utf-8")
        self.assertNotIn("py-call", policy)
        self.assertNotIn("iter_process_adapter.py", library)


if __name__ == "__main__":
    unittest.main()
