"""Native mailbox integration through the actual agent dispatcher."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from native_mail_runtime_probe import qualify, ROOT

class NativeMailEngine(unittest.TestCase):
    def test_native_dispatch_peer_input_and_committed_ack(self):
        binary = Path(os.environ.get("CETTA_BIN", ""))
        if not binary.is_file() or not (binary.parent / "cetta-space-service").is_file():
            self.skipTest("provide a native CeTTa release bundle")
        with tempfile.TemporaryDirectory() as directory:
            qualify(ROOT, binary.parent, Path(directory))

    def test_native_mail_with_the_existing_python_channel(self):
        binary = Path(os.environ.get("CETTA_BIN", ""))
        if not binary.is_file() or not (binary.parent / "cetta-space-service").is_file():
            self.skipTest("provide a native CeTTa release bundle")
        with tempfile.TemporaryDirectory() as directory:
            qualify(ROOT, binary.parent, Path(directory), client="python")

    def test_swi_petta_reports_unavailable(self):
        petta = Path(os.environ.get("PETTA_ROOT", Path.home() / "repos/PeTTa"))
        if not (petta / "run.sh").is_file():
            self.skipTest("provide SWI-PeTTa")
        program = ROOT / ".native-mail-unavailable-qualification.metta"
        program.write_text('''!(import! &self (library lib_import))
!(import! &self ./src/mail)
!(assert (== (mail-send "reader" "hello") "MAIL_UNAVAILABLE: select the CeTTa engine"))
!(assert (== (mail-receive) ""))
!(println! MAIL_UNAVAILABLE_OK)
''')
        try:
            env = dict(os.environ, METTACLAW_ENGINE="petta", METTACLAW_SKIP_INITIALIZE="1",
                       OPENAI_API_KEY="unused-test-value")
            result = subprocess.run(["bash", "./run.sh", str(program)], cwd=ROOT, env=env,
                                    capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=45)
            self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
            self.assertIn("MAIL_UNAVAILABLE_OK", result.stdout)
        finally:
            program.unlink(missing_ok=True)
