"""Exhaustive native help/arity parity with the executable Python oracle."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from isolated_env import isolated_environ
from test_undelivered_prose import cetta_env

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import command_arity  # noqa: E402
import helper  # noqa: E402
import skills_help  # noqa: E402


RECORDER = '''import json, os
def put(key, value):
    with open(os.environ["HELP_ARITY_RECORD"], "a", encoding="utf-8") as stream:
        stream.write(json.dumps([key, value], ensure_ascii=False) + "\\n")
    return 1
def none(): return None
'''

PROBE = r'''
(= (probe:arity-text $rows)
   (str:join "\n" (map-atom $rows $row
     (let (ArityRowV1 $name $lo $hi) $row
       (str:join "\t" ($name (str:number->string $lo) (str:number->string $hi)))))))
(= (probe:equation-text $rows)
   (str:join "\n" (map-atom $rows $row
     (let (ArityEquationV1 $name $counts) $row
       (str:concat $name
         (str:concat "\t"
           (str:join "," (map-atom $counts $count (str:number->string $count)))))))))
(= (probe:split-text $rendered)
   (let $tokens (arity:split-top $rendered)
     (if (== $tokens arity:not-expression) "None"
       (str:concat "["
         (str:concat
           (str:join ", " (map-atom $tokens $token (help:repr-string $token))) "]")))))
'''


def q(text):
    return json.dumps(text, ensure_ascii=False)


def command_expression(name, argc):
    return "(quote (" + " ".join([name] + ['"x"'] * argc) + "))"


class NativeHelpArityParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binary = pathlib.Path(os.environ.get("CETTA_BIN", ""))
        if not cls.binary.is_file():
            raise unittest.SkipTest("set CETTA_BIN to a Python-enabled cetta")
        cls.help_names = ["", " ", '""', '"pins"', "pins", "unknown",
                          "unknown'name", 'unknown"name', "both'\"", "a\\b",
                          "line\nbreak", "tab\tname", "é", "\x00", "\x1f",
                          "task-resolve", "context-policy"]
        cls.help_names += sorted(set(skills_help.PAGES) | set(command_arity.ARITY))
        cls.verdict_cases = [
            "", "atom", "()", "(pins)", "(pins extra)", "(shell ls)",
            '(shell "ls /tmp")', '(shell "echo hi" 2)',
            '(shell echo hi there)', '(unknown)', '(unknown "a b")',
            '(send "a b" (nested x))', '(send "unterminated)',
            '(send "a\\"b")', '(send "a\\\\b")',
            '(send "paren ) and ( inside")',
            '(send "a\\")(b")', '(send (nested (more)) "x")',
            '(pins\u3000)', '(pins\t)', '(pins\n)',
            '(Error syntax_error bad)', '(syntax_error bad)', '(parse error bad)',
            '(ERROR other)', '(SyNtAx_ErRoR bad)', 'x' * 120,
            '(' + 'x' * 120 + ')', '(unknown\'name)', '(é)',
        ]
        for name, (lo, hi) in sorted(command_arity.ARITY.items()):
            for argc in sorted({0, lo, hi, max(0, lo - 1), hi + 1}):
                cls.verdict_cases.append(
                    "(" + " ".join([name] + ['"x"'] * argc) + ")")
        cls.verdict_cases = list(dict.fromkeys(cls.verdict_cases))
        cls.error_cases = [
            (None, "(py-call (help_arity_record.none))"),
            (1, "1"), ("", '""'), ([], "(quote ())"), ([1], "(quote (1))"),
            ([1.5], "(quote (1.5))"), ([True], "(quote (True))"),
            ([[]], "(quote ((nested)))"), (["pins", "extra"], "(quote (pins extra))"),
            (["pins", "extra"], "(quote (\"pins\" \"extra\"))"),
            (["unknown"], "(quote (unknown))"),
            (["rest-command"], "(quote (rest-command))"),
        ]
        for name, (lo, hi) in sorted(command_arity.ARITY.items()):
            for argc in sorted({0, lo, hi, max(0, lo - 1), hi + 1}):
                cls.error_cases.append(
                    ([name] + ["x"] * argc, command_expression(name, argc)))
        cls.bare_cases = ["", " ", "unknown", "pinsx", "PINS", '"pins"',
                          "(pins)", "rest-command now", "hello there"]
        for name in sorted(command_arity.ARITY):
            cls.bare_cases += [name, name + ' "x"', name + "\u3000(a)",
                               name + "\targ", name + "\nmore", name + "x",
                               name.upper(), name + ")", " " + name + " 1 2 "]
        cls.bare_cases = list(dict.fromkeys(cls.bare_cases))
        cls.rows = cls.run_native()

    @classmethod
    def run_native(cls):
        with tempfile.TemporaryDirectory(prefix="help-arity-") as directory:
            directory = pathlib.Path(directory)
            (directory / "help_arity_record.py").write_text(RECORDER, encoding="utf-8")
            record = directory / "record.jsonl"
            lines = [
                '!(import! &self ./src/helper.py)',
                '!(import! &self help_arity_record.py)',
                '!(import! &self ./src/undelivered_prose.metta)',
                '!(import! &self ./src/skills_help.metta)',
                PROBE,
                '!(py-call (help_arity_record.put "arity" (probe:arity-text (arity:rows))))',
                '!(py-call (help_arity_record.put "equations" (probe:equation-text (arity:equation-arities))))',
                '!(py-call (help_arity_record.put "names" (help:command-names-text)))',
            ]
            for i, name in enumerate(cls.help_names):
                lines.append('!(py-call (help_arity_record.put "help:%d" (help:lookup %s)))'
                             % (i, q(name)))
            for i, rendered in enumerate(cls.verdict_cases):
                lines.append('!(py-call (help_arity_record.put "verdict:%d" (help:verdict %s %s)))'
                             % (i, q(rendered), q(rendered)))
                lines.append('!(py-call (help_arity_record.put "split:%d" (probe:split-text %s)))'
                             % (i, q(rendered)))
            for i, (_, expression) in enumerate(cls.error_cases):
                lines.append('!(py-call (help_arity_record.put "error:%d" (help:command-error %s)))'
                             % (i, expression))
            for i, text in enumerate(cls.bare_cases):
                lines.append('!(py-call (help_arity_record.put "bare:%d" (prose:bare-command? %s)))'
                             % (i, q(text)))
            with tempfile.NamedTemporaryFile("w", dir=ROOT, suffix=".metta",
                                             encoding="utf-8") as source:
                source.write("\n".join(lines).replace(
                    "(import! &self help_arity_record.py)",
                    "(import! &self " + q(str(directory / "help_arity_record.py")) + ")"))
                source.flush()
                env = cetta_env(directory)
                env["HELP_ARITY_RECORD"] = str(record)
                result = subprocess.run(
                    [str(cls.binary), "--lang", "petta", "--import-mode", "ancestor-walk",
                     source.name], cwd=ROOT, env=env, capture_output=True, text=True,
                    timeout=180)
            detail = result.stdout[-4000:] + result.stderr[-4000:]
            if result.returncode != 0:
                raise AssertionError("native parity process failed: %s\n%s" %
                                     (result.returncode, detail))
            rows = ([json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]
                    if record.exists() else [])
        keyed = {}
        for key, value in rows:
            if key in keyed:
                raise AssertionError("duplicate native record " + key)
            keyed[key] = value
        return keyed

    def test_all_pages_aliases_and_help_lookup_match(self):
        self.assertEqual(len(skills_help.PAGES), 82)
        for i, name in enumerate(self.help_names):
            with self.subTest(name=name):
                self.assertEqual(self.rows.get("help:%d" % i), skills_help.help(name))
        for alias, target in (("unpin", "pin"), ("write-file", "read-file"),
                              ("append-file", "read-file"), ("send-image", "send-file"),
                              ("send-telegram-chat", "send"), ("energy-set", "energy"),
                              ("sibling-on", "sibling-off"), ("sibling-status", "sibling-off"),
                              ("mcp-servers", "mcp-call"), ("mcp-tools", "mcp-call"),
                              ("mcp-tool-info", "mcp-call")):
            self.assertEqual(skills_help.help(alias), skills_help.help(target))

    def test_all_arities_and_raw_equation_discovery_match(self):
        expected = "\n".join("%s\t%d\t%d" % (name, lo, hi)
                             for name, (lo, hi) in sorted(command_arity.ARITY.items()))
        self.assertEqual(self.rows.get("arity"), expected)
        self.assertEqual(len(command_arity.ARITY), 102)
        equations = command_arity.equation_arities()
        expected = "\n".join("%s\t%s" % (name, ",".join(map(str, sorted(counts))))
                             for name, counts in sorted(equations.items()))
        self.assertEqual(self.rows.get("equations"), expected)
        self.assertEqual(self.rows.get("names"), command_arity.names_text())

    def test_verdicts_and_split_top_match(self):
        for i, rendered in enumerate(self.verdict_cases):
            with self.subTest(rendered=rendered):
                self.assertEqual(self.rows.get("verdict:%d" % i),
                                 skills_help.verdict(rendered, rendered))
                self.assertEqual(self.rows.get("split:%d" % i),
                                 repr(command_arity._split_top(rendered)))

    def test_command_errors_match(self):
        for i, (command, _) in enumerate(self.error_cases):
            with self.subTest(command=command):
                self.assertEqual(self.rows.get("error:%d" % i),
                                 skills_help.command_error(command))

    def test_bare_command_recognition_matches(self):
        for i, text in enumerate(self.bare_cases):
            with self.subTest(text=text):
                self.assertEqual(self.rows.get("bare:%d" % i), helper._is_bare_command(text))

    def test_generated_pages_are_current(self):
        result = subprocess.run([sys.executable, "scripts/generate_skills_help_pages.py", "--check"],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_target_paths_have_no_python_help_or_arity_dependency(self):
        targets = ["lib_mettaclaw.metta", "src/skills.metta",
                   "src/command_dispatch_hooks.metta", "src/undelivered_prose.metta",
                   "src/command_arity.metta", "src/skills_help.metta",
                   "src/skills_help_pages.metta"]
        for name in targets:
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("skills_help.py", text, name)
            self.assertNotIn("command_arity.py", text, name)
            self.assertNotRegex(text, r"py-call[^\n]*(?:skills_help|command_arity)", name)
        dispatch = (ROOT / "src/command_dispatch_hooks.metta").read_text(encoding="utf-8")
        prose = (ROOT / "src/undelivered_prose.metta").read_text(encoding="utf-8")
        skills = (ROOT / "src/skills.metta").read_text(encoding="utf-8")
        self.assertIn("!(import! &self ./src/skills_help.metta)", skills)
        self.assertIn("!(import! &self ./src/skills_help.metta)", dispatch)
        self.assertIn("(help:command-error $command)", dispatch)
        self.assertIn("(arity:names-text)", prose)
        with mock.patch.dict(sys.modules, {"skills_help": None}):
            for name in command_arity.ARITY:
                self.assertTrue(helper._is_bare_command(name))
            self.assertFalse(helper._is_bare_command("not-a-command"))


if __name__ == "__main__":
    unittest.main()
