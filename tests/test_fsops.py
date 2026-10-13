"""Filesystem-awareness skills: errors are instructions, results verify."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import fsops  # noqa: E402
import goals  # noqa: E402


class FsopsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = os.path.join(self.tmp.name, "page.md")
        with open(self.f, "w", encoding="utf-8") as fh:
            fh.write("alpha\nbeta\ngamma\nbeta\ndelta\n")

    def test_read_lines_numbers_and_bounds(self):
        out = fsops.read_lines(self.f, 2, 2)
        self.assertIn("showing 2-3", out)
        self.assertIn("    2\tbeta", out)
        self.assertIn("no lines at or after", fsops.read_lines(self.f, 99, 5))

    def test_whole_file_reads_are_bounded(self):
        huge = os.path.join(self.tmp.name, "huge.txt")
        with open(huge, "wb") as fh:
            fh.write(b"x" * (fsops.MAX_FILE_BYTES + 1))
        self.assertIn("exceeds", fsops.read_file(huge))
        self.assertEqual(len(fsops.read_file(huge, 0)), fsops.MAX_FILE_BYTES + 1)
        self.assertEqual(fsops.read_lines(huge, 1, 1).split("\t",1)[1].count("x"), fsops.MAX_FILE_BYTES + 1)

    def test_ls_tree_and_grep_bounded(self):
        os.makedirs(os.path.join(self.tmp.name, "sub"))
        with open(os.path.join(self.tmp.name, "sub", "b.txt"), "w") as fh:
            fh.write("needle here\n")
        tree = fsops.ls_tree(self.tmp.name, 2)
        self.assertIn("sub/", tree)
        self.assertIn("page.md", tree)
        hits = fsops.grep_files("needle", self.tmp.name)
        self.assertIn("b.txt:1: needle here", hits)
        self.assertIn("no matches", fsops.grep_files("absent", self.tmp.name))

    def test_goal_write_tolerates_spaces_after_colons(self):
        path = os.path.join(self.tmp.name, "goals.metta")
        atom = ("(goal wiki Work sti: 0.8 lti: 0.3 vibes: (SenseMaking) "
                "blocked-by: none last-verified: never note: distill reports)")
        self.assertIn("added", goals.goal_write(atom, path))
        self.assertIn("sti:0.8", goals.goals_view(path))


if __name__ == "__main__":
    unittest.main()
