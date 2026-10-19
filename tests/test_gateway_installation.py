"""A running gateway must also remain installable after a source checkout goes away."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
import install_execution_gateway as installer  # noqa: E402
from execution_gateway import Client  # noqa: E402


class GatewayInstallationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base / "retired-source"
        (self.source / "systemd").mkdir(parents=True)
        self.template = ROOT / "systemd/pettaclaw-gateway@.service"
        shutil.copy2(self.template, self.source / "systemd" / self.template.name)
        self.runtime = self.base / "deployed runtime"
        (self.runtime / "src").mkdir(parents=True)
        shutil.copytree(ROOT / "src/execution_gateway", self.runtime / "src/execution_gateway",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(ROOT / "src/fsops.py", self.runtime / "src/fsops.py")
        self.config = self.base / "configuration"
        self.config.mkdir()
        self.units = self.base / "units"
        (self.units / "default.target.wants").mkdir(parents=True)
        for path in (self.units / self.template.name, self.units / "pettaclaw-gateway@alpha.service",
                     self.units / "default.target.wants/pettaclaw-gateway@alpha.service"):
            path.symlink_to(self.source / "systemd" / self.template.name)
        self.original = {"agent": "alpha", "state_directory": str(self.base / "state"), "tools": {
            "read-lines": {"command": [sys.executable, "-m", "execution_gateway.read_tools", "read-lines"],
                           "effect": "read", "result_format": "json", "cwd": str(self.runtime),
                           "environment": {"PYTHONPATH": str(self.source / "src"),
                                           "METTACLAW_GATEWAY_AGENT": "alpha"}},
            "custom": {"command": [sys.executable, "-c", "print('custom')"], "effect": "read",
                       "environment": {"PYTHONPATH": "custom-modules"}}}}
        (self.runtime / "public.txt").write_text("fixture line\n")
        (self.config / "gateway-alpha.json").write_text(json.dumps(self.original))
        (self.config / "gateway-alpha.env").write_text("PRESERVED=yes\nPYTHONPATH=retired\n")

    def plan(self):
        with mock.patch.object(installer, "ROOT", self.source):
            return installer.prepare("alpha", self.runtime, self.config, self.units)

    def test_deployed_copy_and_tool_imports_survive_source_removal(self):
        plan = self.plan()
        # Merely preparing an installation does not rewrite trusted policy.
        self.assertEqual(json.loads((self.config / "gateway-alpha.json").read_text()), self.original)
        installer.apply(plan, self.base / "backup")
        shutil.rmtree(self.source)
        self.assertFalse((self.units / self.template.name).is_symlink())
        for path in plan["links"]:
            self.assertTrue(path.is_file(), path)
        installed = json.loads((self.config / "gateway-alpha.json").read_text())
        self.assertEqual(installed["tools"]["custom"], self.original["tools"]["custom"])
        self.assertEqual(installed["state_directory"], self.original["state_directory"])
        env = (self.config / "gateway-alpha.env").read_text()
        self.assertIn("PRESERVED=yes", env)
        pythonpath = json.loads(next(line.split("=", 1)[1] for line in env.splitlines()
                                    if line.startswith("PYTHONPATH=")))
        with open(self.base / "gateway.log", "wb") as log:
            process = subprocess.Popen([sys.executable, "-m", "execution_gateway", "--config",
                                        str(self.config / "gateway-alpha.json")],
                                       env=dict(os.environ, PYTHONPATH=pythonpath), stdout=log, stderr=log)
            try:
                client = Client(self.base / "state/gateway.sock", "alpha")
                until = time.monotonic() + 5
                while time.monotonic() < until:
                    self.assertIsNone(process.poll())
                    try:
                        client.tools()
                        break
                    except OSError:
                        time.sleep(.01)
                client.submit("installed", "read-lines", input={"path": "public.txt", "count": 1})
                until = time.monotonic() + 5
                while time.monotonic() < until:
                    receipt = client.status("installed")
                    if receipt["state"] not in ("queued", "running"):
                        break
                    time.sleep(.01)
                self.assertEqual(receipt["state"], "succeeded", receipt)
                self.assertEqual(receipt["result"]["agent"], "alpha")
                self.assertIn("fixture line", receipt["result"]["value"])
            finally:
                process.terminate()
                process.wait(timeout=5)

    def test_wrong_identity_or_missing_runtime_rejected_before_changes(self):
        path = self.config / "gateway-alpha.json"
        wrong = self.original | {"agent": "beta"}
        path.write_text(json.dumps(wrong))
        original = path.read_bytes()
        with self.assertRaises(ValueError):
            self.plan()
        (self.runtime / "src/fsops.py").unlink()
        with self.assertRaises(ValueError):
            self.plan()
        self.assertEqual(path.read_bytes(), original)

    def test_custom_unit_is_preserved(self):
        path = self.units / "pettaclaw-gateway@alpha.service"
        path.unlink()
        path.write_text("[Service]\nExecStart=custom\n")
        with self.assertRaises(ValueError):
            self.plan()
        self.assertEqual(path.read_text(), "[Service]\nExecStart=custom\n")


if __name__ == "__main__":
    unittest.main()
