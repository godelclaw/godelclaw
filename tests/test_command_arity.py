"""The bare-command vocabulary has one owner, command_arity: skills_help checks
commands against it and helper recognizes a bare command by it, so neither
depends on the other."""
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import command_arity  # noqa: E402


class CommandArityTest(unittest.TestCase):
    def test_equations_refine_the_named_commands(self):
        # A command's arity follows its equations; one named only by its
        # equations joins; retired names and the internal rest-command do not.
        self.assertEqual(command_arity.ARITY["request-page"], (1, 2))
        self.assertEqual(command_arity.ARITY["recent-messages"], (2, 3))
        for name in command_arity.EQUATION_COMMANDS:
            self.assertIn(name, command_arity.ARITY)
        for name in ("rest-command", "background-model", "model-adopt", "task-resolve"):
            self.assertNotIn(name, command_arity.ARITY)
        self.assertEqual(command_arity.ARITY["shell"], (1, 2))
        self.assertEqual(command_arity.names_text().split("\n"), sorted(command_arity.ARITY))

    def test_helper_recognizes_bare_commands_without_skills_help(self):
        import helper
        with mock.patch.dict(sys.modules, {"skills_help": None}):
            self.assertTrue(helper._is_bare_command("pins"))
            self.assertTrue(helper._is_bare_command("memory-health now"))
            self.assertFalse(helper._is_bare_command("hello there"))
            self.assertEqual(helper.balance_parentheses("pins"), "((pins))")
            self.assertEqual(helper.balance_parentheses("hello there"), "()")

    def test_skills_help_checks_against_the_same_table(self):
        import skills_help
        self.assertIs(skills_help.ARITY, command_arity.ARITY)
        self.assertEqual(skills_help.command_names_text(), command_arity.names_text())
        self.assertEqual(skills_help.command_error(["pins", "extra"]).split(".")[0],
                         "COMMAND_ERROR: wrong number of arguments")


if __name__ == "__main__":
    unittest.main()
