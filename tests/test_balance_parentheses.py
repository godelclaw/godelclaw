"""The native balance_parentheses answers as helper.balance_parentheses does.

src/balance_parentheses.metta replaces helper.balance_parentheses on CeTTa;
helper.py stays the oracle and the SWI-PeTTa path. Every case is handed to
both in one CeTTa process, twice: as a native string and as the value py-call
returns for a Python str (which is what strategy_host.normalize_response
hands the live loop). The two answers must be the same atom with the same
repr, kind and type, and must spell what helper.balance_parentheses returns
when called directly here. No string case may reach Python. The live caller,
addition-balance-response, is compared with the expression it replaced, stage
by stage down to the commands the turn reads.

Known differences (REPORT.md): text nested about a thousand deep or more,
with anything outside it, makes the oracle raise RecursionError where the
native reading answers; a Python str holding NUL reaches CeTTa cut at the NUL.

Set CETTA_BIN to a Python-enabled cetta.
"""
import contextlib
import hashlib
import io
import json
import os
import pathlib
import random
import re
import subprocess
import sys
import tempfile
import unittest

from isolated_env import isolated_environ  # noqa: E402,F401

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import helper  # noqa: E402
import skills_help  # noqa: E402,F401
from test_undelivered_prose import (  # noqa: E402
    PYTHON_SPACE, SKILLS, WORDS, cetta_env, large)

RECOVERED = re.compile(r"^\[helper\] recovered (\d+) command\(s\) from a malformed batch$")

HANDWRITTEN = [
    # tests/test_response_recovery.py's cases
    '((send "hi") (pin "state"))', '(send "hi")', 'send "hi"', '(send "hi") (pin "x")',
    '((metta (+ 1 2)) (send "x")))', '((send "a") (pin "b")', 'rest 60', 'send "hello (world)"',
    'I will rest now.', '',
    '((send-telegram-chat "42" "1) first, 2) second")) (query "q") (pin "p") (rest 7200 "r"))',
    'Sure! ((send "x") (pin "y")) ⋄⟨Cn:.8 C:.7⟩', '((send "x")) ⋄⟨Cn:.8 C:.7⟩',
    '((send "she said \\"hi (there)\\"")) (pin "p"))', '(send "ok") (pin "never closed)',
    # empty and degenerate
    '  ', '(', '((', ')', '))', '()', '(())', '((()))', ')(', '() ()', '( )', '(  ) (a)', '(((a)))',
    '"', '"(a)"', '\\', '\\(a)', '(a', 'a)', '(a))', '((a)', '(a) b)', '((a) b)', '((a) b) c',
    '( (a) b (c) )', '((a) "x" (b))', '((a) (b)) (c)', '(((a) (b)) ((c)))', '(() (a))', '(( ) (a))',
    '((a) ())', '(a ())', '(()) (a)', '() (a)', '(a) ()', '((()) (a))',
    # a command that is a list of commands, and one that only starts like one
    '((send "x"))', '(((send "x")))', '((send "x") (pin "y"))', '(( (send "x") ) junk (pin "y"))',
    '(("a" (send "x")))', '(\\(send "x"))', '((send "x") junk)', '(junk (send "x"))',
    '( \t\n (send "x") \n )', '(\u00a0(send "x")\u3000)', '(\x1c(send "x")\x1f)',
    # strings steer the reading
    '(send ")")', '(send "(")', '(send "\\"")', '(send "\\\\") (pin "x")', '(send "a\\")(b)")',
    '(send "a" "b)', '(send "unterminated', 'text "quote (pin "x")', '(a "b)" c) d', '"x" (a) "y" (b)',
    '(send "x") ) (pin "y")', ') (send "x") ) ) (pin "y") )', '(send "x"))) (pin "y")',
    '((send "x") (pin "y")', '(send "x") (pin "y"', '(send "x") (pin "y"))', '((send "x") (pin "y")))',
    '(send "x") junk (pin "y") more', 'junk (send "x")', '(send "x") junk', 'junk (send "x") junk',
    '(send "a_quote_b_newline_c_apostrophe_d")', '(send "a\r\nb")', '(send "é̀ \u200b")',
    '(send "a\x00b") (pin "c")', 'a\x00b', '\x00', 'send "a\x00b"',
    # affect trace and prose
    '⋄⟨a⟩ (send "x")', '(send "x") ⋄⟨a)⟩', '⋄⟨(send "x")⟩', 'think ⋄⟨C⟩ (send "x") ok',
    'I think (maybe) this works', 'Sure.\n\n(send "x")\n\nThanks.', 'Hello (world',
    # bare commands
    'send', 'pin', 'rest', 'send   "x"', '  rest 60  ', 'send(x)', 'send\u00a0"x"', 'send\u200b"x"',
    'SEND x', 'rest\nmore text', 'pin\u3000x', 'send "x") (pin "y")', 'send "x" (pin "y")',
    'send "a") b', 'send (a', 'send "a', 'send ) (a)',
    # what reads as values
    'True', 'False', '12', '-3', '1.5', '$x', '&self', 'Say "hi"', 'a\\nb', '(UNDELIVERED: x)',
    # outer parentheses
    '((((send "x"))))', '(((send "x") (pin "y")))', '((((send "x")) ((pin "y"))))',
    '(((((a)))))) b', '((((a) b', '(( (a) )) x', '))((a))((', '((send "x") (pin "y")) tail',
    '(send "x") (pin "y") (query "z")', '(send "x")(pin "y")', '(send "x")\n(pin "y")',
    '((send "x") (pin "y") ', '(  (send "x")  ', '(((send "x")  ', '(send "x")  )  ',
    # whitespace is Python's
    '\u00a0(send "x")\u00a0', '\u2003(send "x") (pin "y")\u3000', '\x85(send "x")\x85',
    '\x1f(send "x")\x1f', '\u200b(send "x")', '\ufeff(send "x")', '\x0b\x0c(send "x")\r\n',
]

SPECIAL = ["(", ")", '"', "\\", "(", ")", "((", "))", '\\"', "\\\\"]
SPACES = PYTHON_SPACE + ["\r\n", "\n\n\n", "  ", "\u200b", "", ""]


def atom(rng):
    r = rng.random()
    if r < 0.4:
        return rng.choice(WORDS + ["x", "1", "-2.5", "$v", "&s", "name"])
    body = "".join(rng.choice(WORDS + SPECIAL + [" ", "\n", "_quote_", "_newline_"])
                   for _ in range(rng.randint(0, 6)))
    return '"%s"' % body.replace("\\", "\\\\").replace('"', '\\"') if r < 0.8 else '"%s"' % body


def command(rng, depth=0):
    parts = [rng.choice(SKILLS + WORDS[:6])]
    for _ in range(rng.randint(0, 4)):
        if depth < 2 and rng.random() < 0.15:
            parts.append(command(rng, depth + 1))
        else:
            parts.append(atom(rng))
    return "(" + " ".join(parts) + ")"


def batch(rng):
    """A well-formed response: a list of commands, a command, or bare."""
    commands = [command(rng) for _ in range(rng.randint(0, 5))]
    body = ""
    for c in commands:
        body += c + rng.choice([" ", " ", "\n", "  ", "", rng.choice(PYTHON_SPACE)])
    kind = rng.randrange(6)
    if kind == 0:
        return "(" + body + ")"
    if kind == 1:
        return "((" + body + "))"
    if kind == 2:
        return "(" + " ".join(["(" + body.strip() + ")"] * rng.randint(1, 2)) + ")"
    if kind == 3 and commands:
        return commands[0][1:-1]  # a bare command
    if kind == 4:
        return "(" * rng.randint(1, 4) + body + ")" * rng.randint(0, 4)
    return body


def mutate(rng, text):
    """Break a response the ways models do: stray or missing parentheses,
    prose around it, a cut, a string left open, a duplicated stretch."""
    for _ in range(rng.randint(0, 3)):
        r = rng.random()
        i = rng.randint(0, len(text))
        if r < 0.2:
            text = text[:i] + rng.choice(SPECIAL) + text[i:]
        elif r < 0.35 and text:
            text = text[:i] + text[i + 1:]
        elif r < 0.45:
            text = text[:i]
        elif r < 0.6:
            text = rng.choice(["Sure! ", "Okay.\n\n", "I will ", "⋄⟨Cn:.8 C:.7⟩ ", "note: "]) + text
        elif r < 0.75:
            text = text + rng.choice([" thanks", "\n\nDone.", " ⋄⟨Cn:.8⟩", " )", " ((", " \"", " (x"])
        elif r < 0.85:
            text = text[:i] + text[i:i + rng.randint(1, 30)] + text[i:]
        elif r < 0.95:
            text = rng.choice(SPACES) + text + rng.choice(SPACES)
        else:
            text = rng.choice(SKILLS) + rng.choice(SPACES + [" "]) + text
    return text


def generated(seed, count):
    rng = random.Random(seed)
    soup = SPECIAL + WORDS + PYTHON_SPACE + ["a", "b", "1", "\r\n", "send", "pin"]
    out = []
    for n in range(count):
        kind = n % 8
        if kind == 7:
            out.append("".join(rng.choice(soup) for _ in range(rng.randint(0, 40))))
        elif kind in (0, 1):
            out.append(batch(rng))
        else:
            out.append(mutate(rng, batch(rng)))
    return out


def deep(depth, tail=" x"):
    return "(" * depth + "a" + ")" * depth + tail


RECORDER = '''import json, os
import helper
_original = helper.balance_parentheses
_cases = json.load(open(os.environ["BALANCE_CASES"], encoding="utf-8"))
_current = [None]
def _record(row):
    with open(os.environ["BALANCE_RECORD"], "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\\n")
    return 1
def text(i): return _cases[i]
def codes(i): return [ord(c) for c in _cases[i]]
def begin(key):
    _current[0] = key
    return 1
def watch():
    def logged(response):
        _record(["python", _current[0]])
        return _original(response)
    helper.balance_parentheses = logged
    return 1
def forbid():
    def forbidden(*args, **kwargs):
        raise AssertionError("native balancing called the Python parser")
    for name in ("balance_parentheses", "_top_level_forms", "_commands_in", "_is_one_form",
                 "_is_bare_command"):
        setattr(helper, name, forbidden)
    return 1
def oracle(response): return _original(response)
def put(key, native, python, same, same_repr, meta, kind, out_meta, out_kind):
    return _record(["case", key, native, python, same, same_repr, str(meta), str(kind),
                    str(out_meta), str(out_kind)])
def universe():
    return "".join(chr(c) for c in range(1, 0x110000) if not 0xD800 <= c < 0xE000)
def value(key, result): return _record(["value", key, str(result)])
def stage(key, name, new, old, same, same_repr, meta, kind, old_meta, old_kind):
    return _record(["stage", key, name, str(new), str(old), same, same_repr,
                    str(meta), str(kind), str(old_meta), str(old_kind)])
'''

PROBE = r'''
(= (probe:show $v) (case $v (((Error $a $b) "<raised>") ($o $o))))
(= (probe:compare $key $x)
   (let* (($_ (py-call (balance_record.begin $key)))
          ($n (probe:show (catch (balance:parentheses $x))))
          ($_ (py-call (balance_record.begin none)))
          ($p (probe:show (catch (py-call (balance_record.oracle $x))))))
     (py-call (balance_record.put $key $n $p (== $n $p)
                (== (repr $n) (repr $p))
                (get-metatype $x) (get-type $x) (get-metatype $n) (get-type $n)))))
'''

# MeTTa values that are not text: they are the oracle's to answer.
VALUES = {
    "number": "12", "float": "1.5", "negative": "-3", "true": "True", "false": "False",
    "symbol": "foo", "skill-symbol": "send", "unit": "()", "expression": '(a "b")',
    "variable": "$free", "literal": '"literal (pin \\"x\\") text ⋄⟨a⟩"',
    "literal-batch": '"(send \\"x\\") junk (pin \\"y\\")"',
    "malformed": '(str:concat (str:byte-slice "é" 0 1) " hi")',
    "malformed-tail": "(str:concat \"(send \\\"ok\\\") \" (str:byte-slice \"é\" 0 1))",
}

# A Python str spelling a bound token comes back from py-call as a symbol that
# is evaluated, as an argument, to the space it names: no text to CeTTa, so
# Python answers it as before.
BOUND_TOKENS = {"&self"}


def is_true(value):
    """A Bool through the recorder: py-call hands Python a Bool as "true"."""
    return value is True or value == "true"


def native_kind(meta, kind):
    """Whether the native module reads a value of this kind itself: a string,
    a symbol, a Bool, or the grounded atom py-call makes of a function's name."""
    return meta == "Symbol" or (meta == "Grounded" and kind in ("String", "Bool", "%Undefined%"))


def iterative_commands_in(form):
    """helper._commands_in without its recursion: what it answers where it can."""
    found, stack = [], [form]
    while stack:
        form = stack.pop()
        inner = form[1:-1].strip()
        if not inner:
            continue
        if inner[0] != "(":
            found.append(form)
            continue
        stack.extend(reversed(helper._top_level_forms(inner)[0]))
    return found


def oracle(text):
    """What helper.balance_parentheses returns, what it printed, and whether
    it raised RecursionError (then the value is the iterative reading's)."""
    err = io.StringIO()
    raised = False
    with contextlib.redirect_stderr(err):
        try:
            value = helper.balance_parentheses(text)
        except RecursionError:
            raised = True
            saved = helper._commands_in
            helper._commands_in = iterative_commands_in
            try:
                value = helper.balance_parentheses(text)
            finally:
                helper._commands_in = saved
    printed = err.getvalue().splitlines()
    assert all(RECOVERED.match(line) for line in printed), printed
    return value, printed, raised


class CettaCase(unittest.TestCase):
    def setUp(self):
        self.binary = pathlib.Path(os.environ.get("CETTA_BIN", ""))
        if not self.binary.is_file():
            self.skipTest("set CETTA_BIN to a Python-enabled cetta")

    def run_cetta(self, lines, cases=(), engine="cetta", extra_env=None, timeout=900):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            (directory / "balance_record.py").write_text(RECORDER)
            (directory / "cases.json").write_text(json.dumps(list(cases)), encoding="utf-8")
            env = cetta_env(directory, engine)
            env.update(BALANCE_RECORD=str(directory / "record.jsonl"),
                       BALANCE_CASES=str(directory / "cases.json"))
            env.update(extra_env or {})
            with tempfile.NamedTemporaryFile("w", dir=ROOT, suffix=".metta",
                                             encoding="utf-8") as source:
                source.write("\n".join(lines) + "\n")
                source.flush()
                result = subprocess.run(
                    [str(self.binary), "--lang", "petta", "--import-mode", "ancestor-walk",
                     source.name], cwd=ROOT, env=env, capture_output=True, text=True,
                    timeout=timeout)
            record = directory / "record.jsonl"
            rows = ([json.loads(line) for line in
                     record.read_text(encoding="utf-8").splitlines()]
                    if record.exists() else [])
        detail = result.stdout[-3000:] + result.stderr[-3000:]
        self.assertEqual(result.returncode, 0, detail)
        return rows, result, detail

    def header(self):
        return ["!(import! &self ./src/helper.py)", "!(import! &self balance_record.py)",
                "!(import! &self ./src/balance_parentheses.metta)",
                "!(py-call (balance_record.watch))", PROBE]


class BalanceParity(CettaCase):
    def compare(self, texts):
        lines = self.header()
        for i, text in enumerate(texts):
            lines.append('!(probe:compare "n%d" (str:from-codepoints '
                         '(py-call (balance_record.codes %d))))' % (i, i))
            if "\x00" not in text:
                lines.append('!(probe:compare "p%d" (py-call (balance_record.text %d)))' % (i, i))
        rows, _, detail = self.run_cetta(lines, texts)
        cases = {row[1]: row[2:] for row in rows if row[0] == "case"}
        reached = {row[1] for row in rows if row[0] == "python"}
        compared = 0
        beyond = 0
        for i, text in enumerate(texts):
            expected, _, raised = oracle(text)
            beyond += raised
            for key in ("n%d" % i, "p%d" % i):
                if key.startswith("p") and "\x00" in text:
                    continue
                self.assertIn(key, cases, "%s %r: no answer\n%s" % (key, text[:200], detail))
                native, python, same, same_repr, meta, kind, out_meta, out_kind = cases[key]
                label = "%s %r" % (key, text[:200])
                self.assertEqual(native, expected, label)
                if raised:
                    # Python raises where the native reading answers.
                    self.assertEqual(python, "<raised>", label)
                    self.assertFalse(is_true(same), label)
                else:
                    self.assertEqual(python, expected, label)
                    self.assertTrue(is_true(same) and is_true(same_repr), label)
                # Python answers only what is not text to CeTTa and answers
                # whose NUL py-call cuts.
                self.assertEqual(key in reached,
                                 not native_kind(meta, kind) or "\x00" in expected
                                 or (key.startswith("p") and text in BOUND_TOKENS),
                                 "%s (%s %s)" % (label, meta, kind))
                # The answer is the symbol py-call makes of Python's str.
                self.assertEqual((out_meta, out_kind), ("Symbol", "%Undefined%"), label)
                compared += 1
        self.beyond_python = beyond
        return compared, len(reached)

    def test_handwritten_cases_match_python(self):
        compared, reached = self.compare(HANDWRITTEN)
        self.assertGreater(compared, 2 * len(HANDWRITTEN) - 20)
        self.assertLessEqual(reached, 6)

    def test_every_skill_name_is_a_bare_command_as_in_python(self):
        texts = []
        for name in SKILLS:
            texts += [name, name + ' "x"', name + "\u3000(a)", name + "x", name.upper(),
                      name + ")", "(" + name + ' "x")', " " + name + " 1 2 "]
        texts += [name[:-1] for name in SKILLS[:40] if len(name) > 1]
        self.assertGreater(len(SKILLS), 50)
        compared, reached = self.compare(texts)
        self.assertGreater(compared, 2 * len(texts) - 20)

    def test_generated_cases_match_python(self):
        texts = generated(20261023, 3200)
        results = [oracle(t) for t in texts]
        values = [r[0] for r in results]
        # Every branch of the reading occurs in what is compared.
        self.assertGreater(sum(v == "()" for v in values), 200)
        self.assertGreater(sum(bool(r[1]) for r in results), 200)
        self.assertGreater(sum(not helper._top_level_forms(v)[1] for v in values), 100)
        self.assertGreater(sum(not helper._top_level_forms(t)[0] and not t.strip().startswith("(")
                               and helper._is_bare_command(t.strip()) for t in texts), 100)
        self.assertGreater(sum(not helper._top_level_forms(t)[1] for t in texts), 400)
        self.assertGreater(sum(not r[1] and v != "()" and helper._is_one_form(v)
                               for v, r in zip(values, results)), 300)
        # A form that is a list of commands, with something other than
        # commands in it or around it.
        self.assertGreater(sum(bool(r[1]) and any(f[1:-1].strip().startswith("(")
                                                  for f in helper._top_level_forms(t)[0])
                               for t, r in zip(texts, results)), 20)
        compared, reached = self.compare(texts)
        self.assertGreater(compared, 6000)
        self.assertLess(reached, 10)

    def test_generated_cases_from_the_prose_port_match_python(self):
        from test_undelivered_prose import generated as prose_generated
        self.compare(prose_generated(20261022, 800))

    def test_large_responses_match_python(self):
        texts = large() + [
            ''.join('(send "a%d") prose ' % i for i in range(3000)),
            "(" * 8000, "(" * 4000 + ")" * 4000, ")" * 8000 + "(",
            "((" * 3000 + "a" + "))" * 3000 + " x",
            '"' * 5001, "\\" * 5001 + "(a)", " " * 20000 + "(a)" + " " * 20000,
            "(send " + '"x" ' * 20000 + ") tail", deep(800), "é" * 30000 + ' (send "é")',
        ]
        self.compare(texts)
        # prose's large() holds text nested 3000 deep, and one more is
        # nested 6000 deep, where Python gives up.
        self.assertEqual(self.beyond_python, 2)

    def test_values_that_are_not_text_answer_as_python(self):
        lines = self.header() + ['!(let $x %s (probe:compare "%s" $x))' % (v, k)
                                 for k, v in VALUES.items()]
        rows, _, detail = self.run_cetta(lines)
        cases = {row[1]: row[2:] for row in rows if row[0] == "case"}
        reached = {row[1] for row in rows if row[0] == "python"}
        for key in VALUES:
            self.assertIn(key, cases, "%s: no answer\n%s" % (key, detail))
            native, python, same, same_repr = cases[key][:4]
            self.assertEqual(native, python, key)
            self.assertTrue(is_true(same) and is_true(same_repr), key)
        self.assertEqual(cases["symbol"][0], "()")
        self.assertEqual(cases["skill-symbol"][0], "((send))")
        self.assertEqual(cases["literal-batch"][0], '((send "x") junk (pin "y"))')
        # Text that is not UTF-8 is handed to Python, whose conversion of
        # the argument raises before helper.balance_parentheses runs.
        self.assertEqual(cases["malformed"][0], "<raised>")
        self.assertEqual(cases["malformed-tail"][0], "<raised>")
        # A number has no strip(): Python raises. Numbers, expressions and
        # variables are Python's to answer; text, symbols and Bools are read
        # natively.
        self.assertEqual(cases["number"][0], "<raised>")
        # (Text that is not UTF-8 never gets as far as Python's function.)
        self.assertEqual(reached, {"number", "float", "negative", "unit", "expression", "variable"})

    def test_the_diagnostic_is_written_to_stderr_as_python_prints_it(self):
        texts = generated(77, 1200) + HANDWRITTEN
        expected = []
        for text in texts:
            expected += oracle(text)[1]
        self.assertGreater(len(expected), 100)
        lines = self.header()[:3]
        for i, text in enumerate(texts):
            lines.append('!(balance:parentheses (str:from-codepoints '
                         '(py-call (balance_record.codes %d))))' % i)
        _, result, detail = self.run_cetta(lines, texts)
        self.assertEqual([line for line in result.stderr.splitlines()
                          if line.startswith("[helper]")], expected, detail)
        # It is no part of the value: nothing of it reaches stdout.
        self.assertNotIn("recovered", result.stdout)

    def test_nesting_deeper_than_python_can_follow_is_the_oracles_defect(self):
        """helper._commands_in recurses once per level of nesting, so text
        nested about a thousand deep, with anything outside it, raises
        RecursionError and yields no batch. The native reading is iterative:
        it answers what the same reading gives where Python can."""
        self.assertEqual(oracle(deep(800))[0], "((a))")
        for depth in (3000, 20000):
            with self.assertRaises(RecursionError):
                helper.balance_parentheses(deep(depth))
        texts = [deep(800), deep(3000), deep(20000), deep(3000, ' (send "x") junk')]
        lines = self.header()[:3]
        for i, text in enumerate(texts):
            lines.append('!(println! (Deep %d (balance:parentheses (str:from-codepoints '
                         '(py-call (balance_record.codes %d))))))' % (i, i))
        _, result, detail = self.run_cetta(lines, texts)
        self.assertEqual(re.findall(r"^\(Deep (\d+) (.*)\)$", result.stdout, re.M),
                         [("0", "((a))"), ("1", "((a))"), ("2", "((a))"),
                          ("3", '((a) (send "x"))')], detail)

    def test_oracle_quirks_are_kept_not_redefined(self):
        """Behaviour of helper.balance_parentheses that looks wrong and is
        kept as it is (REPORT.md): text between commands survives when the
        response opens with "(" and ends with ")", and a space between the
        redundant outer parentheses keeps one of them."""
        kept = {
            '(send "x") junk (pin "y")': '((send "x") junk (pin "y"))',
            '( (send "x") )': '(((send "x")))',
            '((send "x"))': '((send "x"))',
            'Sure (send "x") junk (pin "y")': '((send "x") (pin "y"))',
        }
        for text, expected in kept.items():
            self.assertEqual(oracle(text)[0], expected, text)
        self.compare(list(kept))

    def test_unicode_whitespace_class_is_pythons_at_every_code_point(self):
        universe = "".join(chr(c) for c in range(1, 0x110000) if not 0xD800 <= c < 0xE000)
        lines = ["!(import! &self balance_record.py)",
                 "!(import! &self ./src/balance_parentheses.metta)",
                 "!(bind! &universe (new-state (str:concat \"\" (py-call (balance_record.universe)))))",
                 '!(py-call (balance_record.value "space" (str:sha256 (re:replace-literal '
                 '(re:compile (prose:pattern space)) (get-state &universe) ""))))']
        rows, _, detail = self.run_cetta(lines)
        values = {row[1]: row[2] for row in rows if row[0] == "value"}
        expected = hashlib.sha256("".join(c for c in universe if not c.isspace()).encode()).hexdigest()
        self.assertEqual(values.get("space"), expected, detail)

    def test_generated_tables_are_current(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/generate_prose_unicode.py"),
                                 "--check"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class LiveCaller(CettaCase):
    """addition-balance-response, the caller the loop and the shadow use, hands
    the native code what strategy_host.normalize_response returned and gives
    back what the Python call gave back, at every stage the turn reads."""
    IMPORTS = [
        "!(import! &self (library lib_import))",
        "!(import! &self ./src/helper.py)", "!(import! &self ./src/strategy_host.py)",
        "!(import! &self balance_record.py)",
        "!(import! &self ./src/utils)", "!(import! &self ./src/skills)",
        "!(import! &self ./src/command_pipeline)", "!(import! &self ./src/turn_additions)",
    ]
    PROGRAM = IMPORTS + ["!(py-call (balance_record.watch))", r'''
(= (live:stage $key $name $new $old)
   (py-call (balance_record.stage $key $name (repr $new) (repr $old) (== $new $old)
              (== (repr $new) (repr $old)) (get-metatype $new) (get-type $new)
              (get-metatype $old) (get-type $old))))
(= (live:run $key $raw)
   (let* (($_ (py-call (balance_record.begin $key)))
          ($input (py-call (strategy_host.normalize_response $raw)))
          ($_ (live:stage $key input $input $input))
          ($new (addition-balance-response $raw))
          ($_ (py-call (balance_record.begin old)))
          ($old (py-call (helper.balance_parentheses (py-call (strategy_host.normalize_response $raw)))))
          ($_ (live:stage $key balanced $new $old))
          ($newEnvelope (addition-envelope-response $new))
          ($oldEnvelope (model-response-envelope $old))
          ($_ (live:stage $key envelope $newEnvelope $oldEnvelope))
          ($newRead (addition-read-response $newEnvelope))
          ($oldRead (model-response-read $oldEnvelope))
          ($_ (live:stage $key read $newRead $oldRead))
          ($newCommands (addition-command-sequence $newRead))
          ($oldCommands (model-response-commands $oldRead)))
     (live:stage $key commands $newCommands $oldCommands)))
''']

    def run_live(self, texts, extra_env=None):
        lines = list(self.PROGRAM)
        for i, text in enumerate(texts):
            if "\x00" in text:
                continue
            lines.append('!(live:run "n%d" (str:from-codepoints (py-call (balance_record.codes %d))))'
                         % (i, i))
            lines.append('!(live:run "p%d" (py-call (balance_record.text %d)))' % (i, i))
        rows, result, detail = self.run_cetta(lines, texts, extra_env=extra_env)
        self.stderr = result.stderr
        stages = {}
        for row in rows:
            if row[0] == "stage":
                stages.setdefault(row[1], {})[row[2]] = row[3:]
        reached = {row[1] for row in rows if row[0] == "python"}
        compared = 0
        for key, got in stages.items():
            self.assertEqual(set(got), {"input", "balanced", "envelope", "read", "commands"}, key)
            for name, (new, old, same, same_repr, meta, kind, old_meta, old_kind) in got.items():
                label = "%s %s %r" % (key, name, texts[int(key[1:])][:200])
                # Two readings of the same text are one atom, but for the fresh
                # variables each reading makes.
                self.assertTrue(is_true(same_repr) and (is_true(same) or "$V" in new),
                                label + "\n%s\n%s" % (new, old))
                self.assertEqual((meta, kind), (old_meta, old_kind), label)
                compared += 1
            # What the loop is handed is the symbol py-call makes of a str.
            self.assertEqual(got["balanced"][4], "Symbol", key)
            # Python answers only what is no text to CeTTa.
            meta, kind = got["input"][4:6]
            text = texts[int(key[1:])]
            self.assertEqual(key in reached, not native_kind(meta, kind) or "\x00" in got["balanced"][1]
                             or text in BOUND_TOKENS,
                             "%s %r (%s %s)" % (key, texts[int(key[1:])][:200], meta, kind))
        return stages, reached, compared, detail

    def test_live_caller_gives_back_what_python_gave_back(self):
        texts = HANDWRITTEN + generated(424242, 500)
        wanted = 2 * sum("\x00" not in t for t in texts)
        for observed in ("0", "1"):
            with self.subTest(observed_strategy=observed):
                stages, reached, compared, detail = self.run_live(
                    texts, {"METTACLAW_OBSERVED_STRATEGY": observed})
                self.assertEqual(len(stages), wanted, detail)
                self.assertEqual(compared, 5 * wanted)
                if observed == "0":
                    # Each case is balanced four times (native and python
                    # delivery, each by the live caller and by the oracle),
                    # and each recovery is reported once per balancing.
                    printed = sum(len(oracle(t)[1]) for t in texts if "\x00" not in t)
                    self.assertGreater(printed, 40)
                    self.assertEqual(self.stderr.count("[helper] recovered"), 4 * printed)
                # Only what is no text to CeTTa reaches Python (run_live).
                self.assertLess(len(reached - {"old"}), 12, detail)

    def test_text_that_py_call_turns_into_other_atoms_is_read_as_before(self):
        texts = ["True", "False", "12", "1.5", "-3", "$x", "&self", "()", "(())", "x y",
                 '"quoted"', "foo", "send", "(send)", "'x", "!(a)", "; c", "#t", "a\\b", "\u00a0"]
        stages, reached, compared, detail = self.run_live(texts)
        self.assertEqual(len(stages), 2 * len(texts), detail)

    def test_a_lone_function_name_is_read_natively(self):
        """A response that is one word naming a function the program defines
        comes back from py-call as that function's grounded atom."""
        names = SKILLS + ["not", "and", "or", "+", "-", "if", "let", "case", "println!", "eval",
                          "quote", "collapse", "car-atom", "py-call", "repr", "help", "true", "false"]
        stages, reached, compared, detail = self.run_live(names)
        self.assertEqual(len(stages), 2 * len(names), detail)
        grounded = [k for k, got in stages.items() if got["input"][4] == "Grounded"]
        self.assertGreater(len(grounded), 50)
        self.assertEqual(len(grounded), len({k for k in grounded}))
        self.assertEqual(set(grounded) & reached, set(), detail)

    def test_pipeline_selects_native_balance_only_on_cetta(self):
        program = ["!(import! &self ./src/helper.py)", "!(import! &self balance_record.py)",
                   "!(import! &self ./src/command_pipeline.metta)",
                   "!(py-call (balance_record.watch))", '!(py-call (balance_record.begin "loop"))',
                   '!(py-call (balance_record.value "loop" (model-response-balance '
                   '"Sure! ((send \\"x\\")) ⋄⟨C:.7⟩")))']
        for engine, native in (("cetta", True), ("petta", False)):
            with self.subTest(engine=engine):
                rows, _, detail = self.run_cetta(program, engine=engine)
                self.assertIn(["value", "loop", '((send "x"))'], rows, detail)
                self.assertEqual(["python", "loop"] in rows, not native, detail)
        pipeline = (ROOT / "src/command_pipeline.metta").read_text(encoding="utf-8")
        self.assertIn("(= (model-response-balance $raw)\n   (balance:parentheses $raw))", pipeline)
        self.assertNotIn("helper.balance_parentheses", pipeline)
        turn = (ROOT / "src/turn_additions.metta").read_text(encoding="utf-8")
        self.assertIn("(model-response-balance (py-call (strategy_host.normalize_response $raw)))",
                      turn)

    def test_native_balance_never_calls_the_python_parser(self):
        """With every Python parsing function made to fail, the live caller
        still answers, and answers what the oracle does."""
        texts = ['((send "x") (pin "y"))', 'Sure! (send "x") junk (pin "y")', "rest 60", "prose",
                 '(send "unterminated', "((send \"a\") (list (b)))", "", "  ( ) "]
        lines = list(self.IMPORTS) + ["!(py-call (balance_record.forbid))"] + [
            '!(py-call (balance_record.value "n%d" (addition-balance-response '
            '(py-call (balance_record.text %d)))))' % (i, i) for i in range(len(texts))] + [
            '!(py-call (balance_record.value "s%d" (addition-balance-response '
            '(str:from-codepoints (py-call (balance_record.codes %d))))))' % (i, i)
            for i in range(len(texts))]
        rows, _, detail = self.run_cetta(lines, texts)
        values = {row[1]: row[2] for row in rows if row[0] == "value"}
        expected = [oracle(t)[0] for t in texts]
        self.assertEqual([values.get("n%d" % i) for i in range(len(texts))], expected, detail)
        self.assertEqual([values.get("s%d" % i) for i in range(len(texts))], expected, detail)

    def test_swi_petta_keeps_the_python_oracle(self):
        """On SWI-PeTTa the pipeline loads the Python fallback, and the live
        caller gives the oracle's answers."""
        petta = pathlib.Path(os.environ.get("PETTA_ROOT", pathlib.Path.home() / "repos" / "PeTTa"))
        if not (petta / "run.sh").is_file():
            self.skipTest("PeTTa checkout is unavailable")
        texts = ['Sure! ((send "x") (pin "y")) tail', "I will rest", "rest 60", '(send "x"',
                 '( (send "x") )']
        imports = [line for line in self.IMPORTS if "balance_record" not in line]
        probe = "\n".join(imports + [
            "!(println! (SWI %d (addition-balance-response %s)))" % (i, json.dumps(t))
            for i, t in enumerate(texts)]) + "\n"
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            env = isolated_environ(
                METTACLAW_SKIP_INITIALIZE="1", METTACLAW_SKIP_CONFIG="1", METTACLAW_ENGINE="petta",
                PETTA_ROOT=str(petta),
                PETTA_PY_ENV=str(pathlib.Path.home() / "miniforge3" / "envs" / "petta"),
                METTACLAW_ENGINE_STATE_PATH=str(directory / "engine"),
                METTACLAW_WORKING_SET_PATH=str(directory / "working-set.json"),
                METTACLAW_HISTORY_PATH=str(directory / "history.metta"))
            with tempfile.NamedTemporaryFile("w", dir=ROOT, suffix=".metta", encoding="utf-8") as source:
                source.write(probe)
                source.flush()
                result = subprocess.run(["./run.sh", source.name], cwd=ROOT, env=env,
                                        stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                        timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        out = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", result.stdout)
        answers = dict(re.findall(r"^\(SWI (\d+) (.*)\)$", out, re.M))
        self.assertEqual([answers.get(str(i)) for i in range(len(texts))],
                         [oracle(t)[0] for t in texts], out[-3000:])


if __name__ == "__main__":
    unittest.main()
