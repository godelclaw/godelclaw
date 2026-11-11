"""Native mailbox integration through the actual agent dispatcher."""
from pathlib import Path
import os
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
