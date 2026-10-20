import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ConfigEditingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "scripts").mkdir()
        for name in ("config_ctl.py", "config_to_env.py"):
            shutil.copy(ROOT / "scripts" / name, self.root / "scripts" / name)
        shutil.copy(ROOT / "config/default.toml", self.root / "config/default.toml")
        self.original = ('# Keep this comment\n[agent]\nsynthetic_model = "fixture-model"\n'
                         '[channel]\nkind = "telegram"\n')
        (self.root / "config/local.toml").write_text(self.original)
        (self.root / ".env").write_text("original environment\n")
        (self.root / "config/secrets.env").write_text("SECRET=fixture-secret\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, env=None):
        return subprocess.run([sys.executable, str(self.root / "scripts/config_ctl.py"), *args],
                              capture_output=True, text=True, env=env)

    def test_defaults_reach_an_instance_unless_it_overrides_them(self):
        defaults = self.root / "config/default.toml"
        defaults.write_text(defaults.read_text().replace("shell_timeout_seconds = 120", "shell_timeout_seconds = 77"))
        self.assertEqual(self.run_cli("render").returncode, 0)
        self.assertIn("METTACLAW_SHELL_TIMEOUT_SECONDS='77'", (self.root / ".env").read_text())
        self.assertEqual(self.run_cli("set", "agent.shell_timeout_seconds", "99").returncode, 0)
        self.assertIn("METTACLAW_SHELL_TIMEOUT_SECONDS='99'", (self.root / ".env").read_text())

    def test_show_names_the_file_each_value_comes_from(self):
        rows = {row["setting"]: row for row in map(json.loads, self.run_cli("show").stdout.splitlines())}
        self.assertEqual((rows["agent.synthetic_model"]["value"], rows["agent.synthetic_model"]["origin"]),
                         ("fixture-model", "local.toml"))
        self.assertEqual((rows["agent.shell_timeout_seconds"]["value"], rows["agent.shell_timeout_seconds"]["origin"]),
                         (120, "default.toml"))

    def test_render_says_when_the_callers_environment_is_replaced(self):
        import os
        replaced = self.run_cli("render", env={**os.environ, "METTACLAW_REASONING_EFFORT": "high"})
        self.assertEqual(replaced.returncode, 0, replaced.stderr)
        self.assertIn("METTACLAW_REASONING_EFFORT='high' from the caller's environment is replaced by 'default'",
                      replaced.stderr)
        agreeing = self.run_cli("render", env={**os.environ, "METTACLAW_REASONING_EFFORT": "default"})
        self.assertNotIn("METTACLAW_REASONING_EFFORT", agreeing.stderr)

    def test_setting_is_validated_then_survives_render_with_other_values_preserved(self):
        result = self.run_cli("set", "agent.llm_timeout_seconds", "300")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["restart_required"])
        self.assertIn("# Keep this comment", (self.root / "config/local.toml").read_text())
        self.assertIn('synthetic_model = "fixture-model"', (self.root / "config/local.toml").read_text())
        (self.root / ".env").write_text("stale environment\n")
        self.assertEqual(self.run_cli("render").returncode, 0)
        generated = (self.root / ".env").read_text()
        self.assertIn("SYNTHETIC_TIMEOUT='300'", generated)
        self.assertIn("SYNTHETIC_MODEL='fixture-model'", generated)
        self.assertNotIn("fixture-secret", generated + result.stdout + result.stderr)
        self.assertEqual((self.root / "config/secrets.env").read_text(), "SECRET=fixture-secret\n")
        self.assertEqual((self.root / ".env").stat().st_mode & 0o077, 0)

    def test_invalid_secret_and_dry_run_edits_leave_both_files_untouched(self):
        for args in (("set", "agent.llm_timeout_seconds", "0"),
                     ("set", "agent.api_key", "fixture-secret"),
                     ("--dry-run", "set", "agent.llm_timeout_seconds", "300")):
            self.run_cli(*args)
            self.assertEqual((self.root / "config/local.toml").read_text(), self.original)
            self.assertEqual((self.root / ".env").read_text(), "original environment\n")

    def test_shell_metacharacters_stay_data_and_paired_update_holds_edits(self):
        marker = self.root / "must-not-exist"
        value = '$(touch ' + str(marker) + '); `touch ' + str(marker) + '`'
        result = self.run_cli("set", "agent.synthetic_model", value)
        self.assertEqual(result.returncode, 0, result.stderr)
        subprocess.run(["bash", "-c", 'source "$1"', "config-test", str(self.root / ".env")], check=True)
        self.assertFalse(marker.exists())
        before = (self.root / "config/local.toml").read_bytes()
        (self.root / ".runtime-update-hold").write_text("held\n")
        self.assertEqual(self.run_cli("set", "agent.llm_timeout_seconds", "300").returncode, 2)
        self.assertEqual((self.root / "config/local.toml").read_bytes(), before)

    def test_concurrent_edits_preserve_both_settings(self):
        commands = [("agent.llm_timeout_seconds", "300"), ("agent.shell_timeout_seconds", "240")]
        starts = [subprocess.Popen([sys.executable, str(self.root / "scripts/config_ctl.py"),
                  "set", key, value], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                  for key, value in commands]
        for process in starts:
            _, error = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, error)
        environment = (self.root / ".env").read_text()
        self.assertIn("SYNTHETIC_TIMEOUT='300'", environment)
        self.assertIn("METTACLAW_SHELL_TIMEOUT_SECONDS='240'", environment)


if __name__ == "__main__":
    unittest.main()
