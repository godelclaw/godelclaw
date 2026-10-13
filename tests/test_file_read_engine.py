"""The same file-read contract runs on CeTTa and SWI-PeTTa."""
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def metta(value):
    return json.dumps(value, ensure_ascii=False)


class FileReadEngineTests(unittest.TestCase):
    def run_contract(self, engine):
        petta = Path(os.environ.get("PETTA_ROOT", Path.home() / "repos/PeTTa"))
        cetta = Path(os.environ.get("CETTA_BIN", ""))
        if not (petta / "run.sh").is_file():
            self.skipTest("PeTTa checkout is unavailable")
        if engine == "cetta" and not cetta.is_file():
            self.skipTest("set CETTA_BIN to a CeTTa executable")
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            assertions = []
            def check(path, expected, budget=None):
                call = f"(read-file {metta(str(path))}"
                call += ")" if budget is None else f" {metta(budget)})"
                digest = hashlib.sha256(expected.encode("utf-8")).hexdigest()
                assertions.append(f"!(println! FILE_CASE_{len(assertions) // 2})")
                assertions.append(f"!(let $text {call} (py-call (file_read_probe.check $text {metta(digest)})))")

            fixtures = {
                "unicode": "Gödel — λ, 🌸\r\n".encode(),
                "long-line": b"x" * 10000 + b"\nend\n",
                "empty": b"",
                "nul": b"before\0after\0\n",
                "malformed": b"\xc0\xaf\xed\xa0\x80\xf0\x80\x80\x80\xe2\x82!\xf0\x9f\x92",
                "all-byte-pairs": b"".join(bytes((x, y)) + b"|" for x in range(256) for y in range(256)),
                "random": random.Random(7).randbytes(65536),
                "large": b"x" * (4 * 1024 * 1024),
            }
            for name, data in fixtures.items():
                path = directory / name
                path.write_bytes(data)
                if name == "empty":
                    expected = f"READ_OK_EMPTY_FILE: {path} exists and holds zero bytes"
                else:
                    expected = data.decode("utf-8", errors="replace")
                check(path, expected, 0 if name == "large" else None)
                if name == "large":
                    check(path, expected, -1)
                    check(path, f"read failed: {path} exceeds 2000000 bytes — use read-lines for a window, or supply a larger max_bytes (0 means unlimited)")

            boundary = directory / "boundary"
            boundary.write_bytes(b"abc")
            for budget in (3, "3", 3.9, 0):
                check(boundary, "abc", budget)
            check(boundary, f"read failed: {boundary} exceeds 2 bytes — use read-lines for a window, or supply a larger max_bytes (0 means unlimited)", 2)
            missing = directory / "missing"
            check(missing, f"read failed: no such file: {missing} (cwd {ROOT})")
            check(directory, f"read failed: {directory} is a directory — use (ls-tree {metta(str(directory))} 1)")
            if os.geteuid() != 0:
                denied = directory / "denied"
                denied.write_text("unreadable")
                denied.chmod(0)
                check(denied, f"read failed: permission denied: {denied}")

            probe = directory / "read.metta"
            probe.write_text(
                "!(import! &self (library lib_import))\n"
                "!(import! &self ./tests/file_read_probe.py)\n"
                "!(import! &self ./src/file_io)\n" +
                ("!(py-call (file_read_probe.forbid_python_reads))\n" if engine == "cetta" else "") +
                "\n".join(assertions) + "\n!(println! FILE_READ_CONTRACT_OK)\n", encoding="utf-8")
            env = dict(os.environ)
            python = Path(os.environ.get("PETTA_PY_ENV", Path.home() / "miniforge3/envs/petta"))
            env.update({
                "METTACLAW_ACTIVE_ENGINE": engine,
                "METTACLAW_ENGINE_STATE_PATH": str(directory / "engine"),
                "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(ROOT / "tests"))),
                "PYTHONHOME": str(python),
                "LD_LIBRARY_PATH": str(python / "lib") + os.pathsep + env.get("LD_LIBRARY_PATH", ""),
                "PATH": str(python / "bin") + os.pathsep + env.get("PATH", ""),
            })
            # SWI-PeTTa resolves imports relative to its top-level program.
            env["PETTA_PATH"] = str(petta)
            local = ROOT / (".file-read-probe-" + directory.name + ".metta")
            local.write_text(probe.read_text(), encoding="utf-8")
            self.addCleanup(local.unlink, missing_ok=True)
            if engine == "cetta":
                command = [str(cetta), "--lang", "petta", "--import-mode", "ancestor-walk",
                           str(petta / "lib/lib_import.metta"), str(local)]
            else:
                # Keep the top-level file in the runtime root for PeTTa's imports.
                command = ["bash", str(petta / "run.sh"), str(local), "default"]
            result = subprocess.run(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stderr[-3000:] + result.stdout[-3000:])
            self.assertIn("FILE_READ_CONTRACT_OK", result.stdout)
            self.assertNotIn("Error", result.stdout)
            self.assertNotIn("AssertionError", result.stderr)

    def test_native_cetta_contract_without_python_reader(self):
        self.run_contract("cetta")

    def test_swi_petta_contract(self):
        self.run_contract("petta")
