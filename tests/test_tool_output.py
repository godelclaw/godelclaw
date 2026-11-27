"""The Python process spool keeps what it captured when storage runs out.

The other retention claims are native: tests/unit/tool_output.metta and
tests/unit/claude_client.metta."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import tool_output


class ToolOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        environment = mock.patch.dict(os.environ, {"METTACLAW_TOOL_OUTPUT_DIR": str(self.path)})
        environment.start(); self.addCleanup(environment.stop)

    def test_backpressure_spool_keeps_a_partial_prefix_when_storage_is_exhausted(self):
        read, write = os.pipe()
        reader = os.fdopen(read, "rb", buffering=0)
        with tool_output.ProcessOutput(self.path / "capture", reserve=1) as output:
            output.attach(SimpleNamespace(stdout=reader, stderr=None))
            os.write(write, b"retained prefix")
            output.pump()
            os.write(write, b"cannot store this block")
            full = SimpleNamespace(f_bavail=0, f_frsize=1)
            with mock.patch.object(tool_output.os, "fstatvfs", return_value=full):
                with self.assertRaises(tool_output.StorageExhausted): output.pump()
            os.close(write)
            artifacts = output.artifacts(self.path / "objects", partial=True)
            artifact = artifacts["stdout"][1]
            self.assertEqual(Path(artifact["path"]).read_bytes(), b"retained prefix")
            self.assertFalse(artifact["complete"])
            self.assertEqual(artifact["sha256"], hashlib.sha256(b"retained prefix").hexdigest())
            self.assertEqual(os.stat(artifact["path"]).st_ino,
                             os.stat(self.path / "capture/stdout.bin").st_ino)


if __name__ == "__main__":
    unittest.main()
