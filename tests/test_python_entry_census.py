"""The census hook counts program work and stops at Python's shutdown.

Each case runs a small program under the hook, installed as sitecustomize the
way scripts/census_python_entries_cetta.metta installs it. The program enters
Python natively once, as its main module, and registers an atexit handler that
stands for the runtime's own (stream_sink.flush, input_ack.close_ledger). At
exit Py_FinalizeEx enters threading._shutdown, then runs that handler and
logging's shutdown.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "tests/support/python_entry_census.py"
PROGRAM = """import atexit
import logging
import work

atexit.register(work.flush)
logging.getLogger("census-test").addHandler(logging.NullHandler())
work.step()
"""
WORK = """def step():
    return "program work"


def flush():
    return "exit-time work"
"""
STOP = "            finished = True\n"
KEEP_COUNTING = "            finished = False\n"


def exit_entries(records):
    """The entries only Python's shutdown makes in this program."""
    return [record for record in records
            if record == ("project", "work.py", "flush") or record[0] == "other" and (
                record[1].endswith("/threading.py") and record[2] == "_shutdown" or
                record[1].endswith("/logging/__init__.py") and record[2] == "shutdown")]


class PythonEntryCensusTest(unittest.TestCase):
    def census(self, hook):
        with tempfile.TemporaryDirectory(prefix="entry-census-") as temporary:
            base = Path(temporary)
            root, site, records = base / "root", base / "site", base / "records"
            for directory in (root, site, records):
                directory.mkdir()
            (site / "sitecustomize.py").write_text(hook)
            (root / "work.py").write_text(WORK)
            (root / "program.py").write_text(PROGRAM)
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(base),
                   "PYTHONPATH": str(site), "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
                   "PYTHON_ENTRY_CENSUS_DIR": str(records), "PYTHON_ENTRY_CENSUS_ROOT": str(root)}
            run = subprocess.run([sys.executable, str(root / "program.py")], cwd=root, env=env,
                                 capture_output=True, text=True, timeout=60)
            self.assertEqual((run.returncode, run.stderr), (0, ""))
            lines = [line for path in sorted(records.iterdir())
                     for line in path.read_text().splitlines()]
        return [tuple(line.split("\t")) for line in lines]

    def test_program_work_is_counted_and_shutdown_is_not(self):
        records = self.census(HOOK.read_text())
        self.assertIn(("project", "program.py", "<module>"), records)
        self.assertEqual(exit_entries(records), [])

    def test_mutant_that_keeps_counting_after_shutdown_begins_is_caught(self):
        source = HOOK.read_text()
        self.assertEqual(source.count(STOP), 1)
        records = self.census(source.replace(STOP, KEEP_COUNTING))
        self.assertIn(("project", "program.py", "<module>"), records)
        self.assertIn(("project", "work.py", "flush"), exit_entries(records))


if __name__ == "__main__":
    unittest.main()
