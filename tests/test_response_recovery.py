"""A malformed command batch keeps its complete commands.

A stray ')' or text around the command list used to make the whole model
response unreadable, so nothing in it ran.  Every complete (command) now runs;
text outside parentheses and unmatched ')' run nothing, and prose is reported
back as undelivered.  An unclosed string or form still fails the batch,
because reading it would mean guessing.
"""

import os
import pathlib
import shutil
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(ROOT / "src"))

import helper  # noqa: E402


def recover(raw):
    return helper.balance_parentheses(raw)


class ResponseRecoveryTest(unittest.TestCase):
    def test_explicit_batch_envelope_preserves_order_and_text(self):
        raw = '''((command-batch (pin "It's a note (not code)") (send "I've checked.")))'''
        self.assertEqual(helper.normalize_command_batches(raw),
                         '''((pin "It's a note (not code)") (send "I've checked."))''')

    def test_batch_normalization_does_not_unwrap_command_arguments(self):
        for raw in ['((metta (command-batch (send "data"))))',
                    '((send "(command-batch (pin note))"))',
                    '((command-batch "not a command"))',
                    '((command-batch (pin "unfinished))']:
            self.assertEqual(helper.normalize_command_batches(raw), raw)

    def test_nested_batch_envelopes_preserve_each_command_once(self):
        raw = '(command-batch (send "a") (command-batch (pin "b") (query "c")))'
        self.assertEqual(helper.normalize_command_batches(raw),
                         '((send "a") (pin "b") (query "c"))')

    def test_well_formed_batches_are_unchanged(self):
        for raw, expected in [
            ('((send "hi") (pin "state"))', '((send "hi") (pin "state"))'),
            ('(send "hi")', '((send "hi"))'),
            ('send "hi"', '((send "hi"))'),
            ('(send "hi") (pin "x")', '((send "hi") (pin "x"))'),
            ('((metta (+ 1 2)) (send "x")))', '((metta (+ 1 2)) (send "x"))'),
            ('((send "a") (pin "b")', '((send "a") (pin "b"))'),
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(recover(raw), expected)

    def test_list_closed_after_the_first_command_keeps_every_command(self):
        raw = ('((send-telegram-chat "42" "1) first, 2) second"))'
               ' (query "q") (pin "p") (rest 7200 "r"))')
        self.assertEqual(
            recover(raw),
            '((send-telegram-chat "42" "1) first, 2) second")'
            ' (query "q") (pin "p") (rest 7200 "r"))')

    def test_text_around_the_commands_is_ignored(self):
        self.assertEqual(
            recover('Sure! ((send "x") (pin "y")) ⋄⟨Cn:.8 C:.7⟩'),
            '((send "x") (pin "y"))')
        self.assertEqual(
            recover('((send "x")) ⋄⟨Cn:.8 C:.7⟩'),
            '((send "x"))')

    def test_escaped_quotes_and_parentheses_inside_strings(self):
        self.assertEqual(
            recover('((send "she said \\"hi (there)\\"")) (pin "p"))'),
            '((send "she said \\"hi (there)\\"") (pin "p"))')

    def test_open_string_still_fails_the_batch(self):
        raw = '(send "ok") (pin "never closed)'
        normalized = recover(raw)
        self.assertIn('"never closed', normalized)
        self.assertFalse(helper._top_level_forms(normalized)[1],
                         "an open string must not be silently dropped")

    def test_bare_commands_keep_the_old_reading(self):
        self.assertEqual(recover('send "hello (world)"'),
                         '((send "hello (world)"))')
        self.assertEqual(recover('rest 60'), '((rest 60))')

    def test_prose_is_not_a_command(self):
        """Text whose first word names no skill runs nothing; it is reported
        back as undelivered instead of failing as an unknown skill."""
        self.assertEqual(recover("I will rest now."), "()")
        self.assertEqual(recover(""), "()")

    def test_undelivered_prose_is_the_text_no_command_carries(self):
        prose = helper.undelivered_prose
        self.assertEqual(prose("I will rest now."), "I will rest now.")
        self.assertEqual(prose('Sure! ((send "x") (pin "y")) ⋄⟨Cn:.8 C:.7⟩'), "Sure!")
        self.assertEqual(prose('((send "x")) ⋄⟨Cn:.8 C:.7⟩'), "")
        self.assertEqual(prose('((send "x") (pin "y"))'), "")
        self.assertEqual(prose('send "hello"'), "")
        self.assertEqual(prose("I think (maybe) this works"), "I think this works")
        self.assertEqual(prose("  "), "")
        self.assertEqual(prose(()), "")
        self.assertEqual(prose("((send \"x\")))"), "")

    @unittest.skipUnless(
        shutil.which("swipl") and (pathlib.Path(os.environ.get(
            "PETTA_ROOT", os.path.expanduser("~/repos/PeTTa")))
            / "src" / "parser.pl").is_file(),
        "SWI-Prolog and PeTTa's reader are required")
    def test_recovered_batches_parse_with_the_petta_reader(self):
        parser = (pathlib.Path(os.environ.get(
            "PETTA_ROOT", os.path.expanduser("~/repos/PeTTa")))
            / "src" / "parser.pl")
        for raw in [
            '((send-telegram-chat "42" "1) first, 2) second"))'
            ' (query "q") (pin "p") (rest 7200 "r"))',
            'Sure! ((send "x") (pin "y")) ⋄⟨Cn:.8 C:.7⟩',
            '((send "she said \\"hi (there)\\"")) (pin "p"))',
        ]:
            with self.subTest(raw=raw):
                goal = ("read_term(user_input, S, []), sread(S, T), "
                        "length(T, N), format('~w', [N]), halt")
                result = subprocess.run(
                    ["swipl", "-q", "-s", os.fspath(parser), "-g", goal,
                     "-t", "halt(1)"],
                    input=repr_prolog_string(recover(raw)) + ".\n",
                    capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertGreater(int(result.stdout.strip() or 0), 0)


def repr_prolog_string(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


if __name__ == "__main__":
    unittest.main()
