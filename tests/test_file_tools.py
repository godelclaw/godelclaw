"""Direct file tools: atomic writes with a backup of the previous version."""

import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import file_tools  # noqa: E402


class FileToolsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.directory.name)
        self.state = root / "state"
        self.work = root / "work"
        self.work.mkdir()
        self.env = mock.patch.dict(os.environ, {
            "XDG_STATE_HOME": str(self.state), "METTACLAW_INSTANCE": "agent"})
        self.env.start()
        self.cwd = os.getcwd()
        os.chdir(self.work)

    def tearDown(self):
        os.chdir(self.cwd)
        self.env.stop()
        self.directory.cleanup()

    def backups(self):
        directory = self.state / "agent" / "file-backups"
        return sorted(directory.iterdir()) if directory.is_dir() else []

    def test_write_replaces_at_once_and_keeps_the_previous_version(self):
        target = self.work / "src" / "loop.metta"
        target.parent.mkdir()
        target.write_text("old\n", encoding="utf-8")
        target.chmod(0o640)
        receipt = file_tools.write_file("src/loop.metta", "new\n")
        self.assertTrue(receipt.startswith("WROTE %s op=write" % target), receipt)
        self.assertEqual(target.read_text(encoding="utf-8"), "new\n")
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)
        [backup] = self.backups()
        self.assertEqual(backup.read_text(encoding="utf-8"), "old\n")
        self.assertIn("previous=%s" % backup, receipt)

    def test_write_creates_a_new_file_without_a_backup(self):
        receipt = file_tools.write_file("notes.txt", "hello")
        self.assertTrue(receipt.startswith("WROTE"), receipt)
        self.assertEqual((self.work / "notes.txt").read_text(encoding="utf-8"), "hello")
        self.assertEqual(self.backups(), [])

    def test_edit_needs_exactly_one_occurrence(self):
        target = self.work / "a.metta"
        target.write_text("(x) (x) (y)\n", encoding="utf-8")
        self.assertIn("occurs 2 times", file_tools.edit_file("a.metta", "(x)", "(z)"))
        self.assertIn("occurs 0 times", file_tools.edit_file("a.metta", "(w)", "(z)"))
        self.assertEqual(target.read_text(encoding="utf-8"), "(x) (x) (y)\n")
        self.assertEqual(self.backups(), [])
        receipt = file_tools.edit_file("a.metta", "(y)", "(z)")
        self.assertTrue(receipt.startswith("WROTE"), receipt)
        self.assertEqual(target.read_text(encoding="utf-8"), "(x) (x) (z)\n")
        self.assertEqual(len(self.backups()), 1)

    def test_append_adds_one_line_to_an_existing_file(self):
        self.assertTrue(file_tools.append_file("missing.txt", "x").startswith("append failed"))
        target = self.work / "log.txt"
        target.write_text("first\n", encoding="utf-8")
        self.assertTrue(file_tools.append_file("log.txt", "second").startswith("WROTE"))
        self.assertEqual(target.read_text(encoding="utf-8"), "first\nsecond\n")

    def test_nothing_is_left_pending(self):
        file_tools.write_file("x.txt", "1")
        self.assertFalse((self.state / "agent" / "selfmod-proposals").exists())
        self.assertEqual([p.name for p in self.work.iterdir()], ["x.txt"])


if __name__ == "__main__":
    unittest.main()
