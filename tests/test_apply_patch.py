"""Patch application: distinct targets, staged writes, honest partial reports."""
import os
from pathlib import Path
import stat
import subprocess
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

    def test_new_file_permissions_follow_normal_creation_under_each_umask(self):
        code = '''import os, stat, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import apply_patch
root = Path(sys.argv[2])
os.umask(int(sys.argv[3], 8))
reference = root / "ordinary"
reference.write_text("ordinary\\n")
result = apply_patch.apply("*** Begin Patch\\n*** Add File: patched\\n+patch\\n*** End Patch", root)
assert result.startswith("Success."), result
a, b = reference.stat(), (root / "patched").stat()
assert stat.S_IMODE(a.st_mode) == stat.S_IMODE(b.st_mode), (oct(a.st_mode), oct(b.st_mode))
assert a.st_gid == b.st_gid
'''
        for mask in ('002', '022', '077'):
            with self.subTest(mask=mask):
                directory = self.root / mask; directory.mkdir()
                result = subprocess.run([sys.executable, '-c', code, str(ROOT / 'src'),
                                         str(directory), mask], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.leftovers(), [])

    def test_updates_preserve_private_and_executable_modes(self):
        target = self.root / 'a.py'
        for mode in (0o600, 0o750):
            target.write_text('one\ntwo\n'); target.chmod(mode)
            result = self.apply(patch('*** Update File: a.py', '-one', '+ONE'))
            self.assertTrue(result.startswith('Success.'), result)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), mode)
        self.assertEqual(self.leftovers(), [])

    def test_moves_preserve_source_permissions_for_new_and_existing_destinations(self):
        for mode in (0o600, 0o750):
            for existing_destination in (False, True):
                with self.subTest(mode=oct(mode), existing_destination=existing_destination):
                    source = self.root / 'a.py'
                    target = self.root / 'moved.py'
                    source.write_text('one\ntwo\n'); source.chmod(mode)
                    target.unlink(missing_ok=True)
                    if existing_destination:
                        target.write_text('old target\n'); target.chmod(0o644)
                    result = self.apply(patch('*** Update File: a.py', '*** Move to: moved.py', '-one', '+ONE'))
                    self.assertTrue(result.startswith('Success.'), result)
                    self.assertFalse(source.exists())
                    self.assertEqual(target.read_text(), 'ONE\ntwo\n')
                    self.assertEqual(stat.S_IMODE(target.stat().st_mode), mode)
        self.assertEqual(self.leftovers(), [])


if __name__ == "__main__":
    unittest.main()
