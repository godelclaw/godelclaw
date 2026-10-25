"""Patch application: distinct targets, staged writes, honest partial reports."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import apply_patch  # noqa: E402


def patch(*body):
    return "\n".join(("*** Begin Patch",) + body + ("*** End Patch",))


class ApplyPatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "a.py").write_text("one\ntwo\n")

    def apply(self, text):
        return apply_patch.apply(text, str(self.root))

    def leftovers(self):
        return sorted(p.name for p in self.root.rglob(".*") if p.is_file())

    def test_two_updates_of_one_file_are_refused(self):
        # Both updates would be computed from the original text, so the second
        # write would silently drop the first.
        result = self.apply(patch("*** Update File: a.py", "-one", "+ONE",
                                  "*** Update File: a.py", "-two", "+TWO"))
        self.assertEqual(result, "Patch failed: multiple operations target a.py")
        self.assertEqual((self.root / "a.py").read_text(), "one\ntwo\n")

    def test_move_onto_itself_is_refused(self):
        result = self.apply(patch("*** Update File: a.py", "*** Move to: ./a.py", "-one", "+ONE"))
        self.assertEqual(result, "Patch failed: multiple operations target a.py")
        self.assertEqual((self.root / "a.py").read_text(), "one\ntwo\n")

    @unittest.skipIf(os.geteuid() == 0, "root ignores directory permissions")
    def test_staging_failure_changes_nothing(self):
        locked = self.root / "locked"
        locked.mkdir()
        (locked / "b.py").write_text("old\n")
        locked.chmod(0o555)
        self.addCleanup(locked.chmod, 0o755)
        result = self.apply(patch("*** Update File: a.py", "-one", "+ONE",
                                  "*** Add File: fresh/c.py", "+new",
                                  "*** Update File: locked/b.py", "-old", "+new"))
        self.assertTrue(result.startswith("Patch failed: "), result)
        self.assertEqual((self.root / "a.py").read_text(), "one\ntwo\n")
        self.assertFalse((self.root / "fresh").exists())
        self.assertEqual(self.leftovers(), [])

    def test_rename_failure_reports_what_was_applied(self):
        (self.root / "b.py").write_text("old\n")
        real = os.replace
        calls = []

        def second_fails(source, target):
            calls.append(target)
            if len(calls) == 2:
                raise OSError("disk detached")
            return real(source, target)

        with mock.patch.object(apply_patch.os, "replace", side_effect=second_fails):
            result = self.apply(patch("*** Update File: a.py", "-one", "+ONE",
                                      "*** Update File: b.py", "-old", "+new"))
        self.assertEqual(result, "Patch partly applied: disk detached\n"
                                 "Applied: a.py\nNot applied: b.py")
        self.assertEqual((self.root / "a.py").read_text(), "ONE\ntwo\n")
        self.assertEqual((self.root / "b.py").read_text(), "old\n")
        self.assertEqual(self.leftovers(), [])

    def test_typographic_punctuation_matches_ascii_context(self):
        (self.root / "q.py").write_text("say(\u201chi\u201d)\u00a0\u2013 x  \nend\n")
        result = self.apply(patch("*** Update File: q.py", ' say("hi") - x', "-end", "+done"))
        self.assertEqual(result, "Success. Updated the following files:\nM q.py")
        # Context lines keep the file's own text.
        self.assertEqual((self.root / "q.py").read_text(), "say(\u201chi\u201d)\u00a0\u2013 x  \ndone\n")

    def test_success_still_writes_every_file(self):
        result = self.apply(patch("*** Update File: a.py", "-one", "+ONE",
                                  "*** Add File: sub/b.py", "+b"))
        self.assertEqual(result, "Success. Updated the following files:\nM a.py\nA sub/b.py")
        self.assertEqual((self.root / "sub/b.py").read_text(), "b\n")
        self.assertEqual(self.leftovers(), [])


if __name__ == "__main__":
    unittest.main()
