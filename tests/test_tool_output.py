"""Output previews never erase the complete, retrievable tool evidence."""
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import claude_bridge
import mcp_bridge
import tool_output


class ToolOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        environment = mock.patch.dict(os.environ, {"METTACLAW_TOOL_OUTPUT_DIR": str(self.path),
            "METTACLAW_CLAUDE_TIMEOUT_SECONDS": "0"})
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

    def test_large_stream_retains_every_byte_and_a_verifiable_identity(self):
        raw = ("evidence λ\n" * 200000).encode()
        preview, artifact = tool_output.retain_stream(BytesIO(raw), preview_bytes=123)
        self.assertTrue(artifact["preview_truncated"])
        self.assertEqual(Path(artifact["path"]).read_bytes(), raw)
        self.assertEqual(artifact["bytes"], len(raw))
        self.assertEqual(artifact["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertLess(len(preview), 124)

    def test_mcp_preview_points_to_the_complete_structured_result(self):
        data = {"rows": list(range(1000))}
        with mock.patch.dict(os.environ, {"METTACLAW_MCP_MAX_OUTPUT_CHARS": "100"}):
            result = json.loads(mcp_bridge._json(data))
        self.assertTrue(result["truncated"])
        self.assertEqual(json.loads(Path(result["artifact"]["path"]).read_text()), data)

    def test_claude_failure_keeps_both_streams_and_reports_its_exit(self):
        completed = SimpleNamespace(returncode=7, stdout="answer" * 2000, stderr="problem" * 2000)
        with mock.patch.object(claude_bridge, "authorized", return_value=True), \
                mock.patch.object(claude_bridge, "_claude_bin", return_value="fixture"), \
                mock.patch.object(claude_bridge.subprocess, "run", return_value=completed) as run:
            result = claude_bridge.ask("fixture request")
        self.assertIn("exit 7", result)
        self.assertIn("full output:", result)
        self.assertIsNone(run.call_args.kwargs["timeout"])
        self.assertEqual({p.read_text() for p in self.path.glob("*.txt")},
                         {completed.stdout, completed.stderr})

    def test_configured_deadline_retains_partial_evidence_without_claiming_no_effect(self):
        expired = subprocess.TimeoutExpired("fixture", 1, output=b"partial stdout", stderr=b"partial stderr")
        with mock.patch.object(claude_bridge, "authorized", return_value=True), \
                mock.patch.object(claude_bridge, "_claude_bin", return_value="fixture"), \
                mock.patch.object(claude_bridge.subprocess, "run", side_effect=expired):
            result = claude_bridge.ask("fixture request")
        self.assertIn("effects are unknown", result)
        self.assertEqual({p.read_bytes() for p in self.path.glob("*.txt")},
                         {b"partial stdout", b"partial stderr"})


if __name__ == "__main__":
    unittest.main()
