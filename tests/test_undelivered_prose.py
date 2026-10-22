"""The native undelivered prose answers as helper.undelivered_prose does.

src/undelivered_prose.metta replaces helper.undelivered_prose on CeTTa;
helper.py stays the oracle and the SWI-PeTTa path. Every case is handed to
both in one CeTTa process, twice: as a native string (the native provider
path) and as the value py-call returns for a Python str (the Python provider
path). The two answers must be the same atom with the same repr, which is
what the loop writes to history, and must spell what helper.undelivered_prose
returns when called directly here. No string case may reach Python.

Known difference: a Python str holding NUL reaches CeTTa cut at the NUL,
while py-call still hands Python the whole str, so the native reading sees
less text. No native code can recover it; such text is not generated for
the Python delivery here. Native strings holding NUL are compared.

Set CETTA_BIN to a Python-enabled cetta.
"""
import hashlib
import json
import os
import pathlib
import random
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import helper  # noqa: E402
import skills_help  # noqa: E402

SKILLS = sorted(skills_help.ARITY)
PYTHON_SPACE = [chr(c) for c in range(0x110000) if chr(c).isspace()]

HANDWRITTEN = [
    # tests/test_response_recovery.py's cases
    "I will rest now.", 'Sure! ((send "x") (pin "y")) ⋄⟨Cn:.8 C:.7⟩',
    '((send "x")) ⋄⟨Cn:.8 C:.7⟩', '((send "x") (pin "y"))', 'send "hello"',
    "I think (maybe) this works", "  ", "", '((send "x")))',
    # a form's first occurrence is not where it was read
    '"(a)" (a)', 'note (pin "x") then (pin "x")', 'say "(pin \\"x\\")" (pin \\"x\\")',
    '(a) (a) (a)', 'x (b (a)) "(a)" (a)', '((a)) (a)', '"((a))" ((a)) end',
    # unbalanced and unclosed
    'unclosed (send "x"', '(send "unterminated', ')', ')))', ' ) a ( ', '(', '(((',
    '(a))) b ((c)', 'a ) b ) c', '())(', '"', '"a', 'a"b"c', '\\', '\\(a)', '"\\', 'a "b\\',
    '"quoted (x)" text', 'escaped \\" (a) "b\\"(c)"', '"\\\\" (a) "\\\\\\"(b)"',
    '("x)" y) tail', '(a "b)" c) d', '(a "b\\")" c) d)', '"(" ")" (z)',
    # affect trace
    '⋄⟨a⟩⋄⟨b⟩ c', '⋄⟨unclosed text', '⋄⟨x\ny⟩ z', '⋄⋄⟨x⟩y', '⟨x⟩ y', '⋄⟨⋄⟨x⟩y⟩ z',
    '⋄⟨(a)⟩ b', '(a ⋄⟨ ) ⟩ b', '⋄⟨⟩', '⋄⟨⟩word', 'pre⋄⟨mid⟩post', '⋄ ⟨x⟩', '⋄⟨x⟩⟩y',
    '"⋄⟨" (a) ⟩ b', 'w ⋄⟨)⟩ v',
    # whitespace and line boundaries
    '\u00a0hello\u2003world\u3000', 'a\u2028b\u2029c\x85d\x1ce\x1df\x1eg\x1fh\x0bi\x0cj\rk\r\nl',
    '\u200bzero width', '\ufeffbom', 'a\n\n\n\n\nb', 'a\n \n\t\n b', '\n\n\na', 'a\r\r\n\nb',
    'a\n\nb', 'a\r\n\r\n\r\nb', ' \t lead and trail \t ', 'x\u1680y\u2000z\u200aw\u202fv\u205fu',
    'a\x1f\x1f\nb', 'a\x85\x85\x85b', 'line one  \n  line two\n\n\n\n line three ',
    # letters, digits and what is neither
    'ж', '١٢٣', '²', 'Ⅻ', '日本語', 'ǅ', 'ª', '〇', '½', '😀', '— … →', '\u0301', '٫',
    '😀 (pin "x")', 'é (pin "x")', '%%% (a)', '_', '_ (a)', '\x01\x7f', 'ℵ', '𝟘', '🄋',
    # bare commands
    'send "x"', 'rest', '  pin   note', 'send(x)', 'send\u00a0"x"', 'send\u200b"x"',
    'SEND x', 'rest\nmore text', 'pin', 'pin\u3000x', '(send', 'nop ⋄⟨x⟩',
    # what reads as values
    'True', 'False', 'True (pin "x")', '(a) False', '12', '-3', '1.5', '()', '(())',
    '$x', '&self', 'Say "hi"', 'a\\nb', '(UNDELIVERED: x)',
]

WORDS = ["hello", "world", "I", "will", "Sure!", "ok.", "x", "42", "café", "Ωmega",
         "١٢٣", "日本", "Ⅻ", "²", "😀", "—", "…", "→", "\u0301", "\u200b", "\ufeff",
         "True", "False", "$v", "&self", "%", "'", "it's", "_", "\x01", "ǅ", "𝟘"]
SPECIAL = ["(", ")", '"', "\\", "⋄", "⟨", "⟩", "⋄⟨", "\\\"", "\\\\"]
SPACES = PYTHON_SPACE + ["\r\n", "\n\n\n", "  ", "\u200b"]


def string_body(rng):
    return "".join(rng.choice(WORDS + SPECIAL[:2] + ["\\\"", "\\\\", "\\", " ", "\n"])
                   for _ in range(rng.randint(0, 5)))


def structured(rng, depth=0):
    parts = []
    for _ in range(rng.randint(0, 10)):
        r = rng.random()
        if r < 0.16 and depth < 3:
            parts.append("(" + rng.choice(SKILLS + WORDS) + " " + structured(rng, depth + 1)
                         + rng.choice([")", ")", ")", ")", "", "))"]))
        elif r < 0.26:
            parts.append('"' + string_body(rng) + rng.choice(['"', '"', '"', ""]))
        elif r < 0.32:
            parts.append("⋄⟨" + rng.choice(["Cn:.8 C:.7", "", "x\ny", "(a)", '"', ")", "⋄⟨"])
                         + rng.choice(["⟩", "⟩", ""]))
        elif r < 0.55:
            parts.append(rng.choice(SPACES))
        elif r < 0.65:
            parts.append(rng.choice(SPECIAL))
        else:
            parts.append(rng.choice(WORDS + SKILLS[:20]))
    return "".join(parts)


def generated(seed, count):
    """Structured responses, with copies of a form planted earlier, and
    character soup over everything that steers the reading."""
    rng = random.Random(seed)
    soup = SPECIAL + WORDS + PYTHON_SPACE + ["a", "b", "1", "\r\n"]
    out = []
    for n in range(count):
        kind = n % 4
        if kind == 3:
            text = "".join(rng.choice(soup) for _ in range(rng.randint(0, 40)))
        else:
            text = structured(rng)
            if kind == 1:
                text = rng.choice(SKILLS) + rng.choice(SPACES) + text
            forms = helper._top_level_forms(text)[0]
            if kind == 2 and forms:
                form = rng.choice(forms)
                where = rng.choice(['"%s" ', "%s ", "x%sy ", '"a %s'])
                text = where % form + text
        out.append(text)
    return out


def large():
    return ["word " * 20000 + '(pin "x")', "(" * 3000 + ")" * 3000 + " tail",
            ('(send "a") prose ' * 2000), "é" * 50000 + " (a)",
            "\n" * 10000 + "a" + " \u3000" * 10000 + "b", "⋄⟨" + "x" * 30000 + "⟩ done"]


def as_returned(text):
    """What py-call makes of a returned str, read back by Python."""
    return {"True": "true", "False": "false"}.get(text, text)


def native_kind(meta, kind):
    """Whether the native module reads a value of this kind itself."""
    return meta == "Symbol" or (meta == "Grounded" and kind in ("String", "Number", "Bool"))


# A Python str spelling a bound token comes back from py-call as that token
# and is evaluated to the space it names, which is no text to CeTTa; Python
# answers it as before.
BOUND_TOKENS = {"&self"}


RECORDER = '''import json, os
import helper
_original = helper.undelivered_prose
_cases = json.load(open(os.environ["PROSE_CASES"], encoding="utf-8"))
_current = [None]
def _record(row):
    with open(os.environ["PROSE_RECORD"], "a", encoding="utf-8") as f:
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
    helper.undelivered_prose = logged
    return 1
def oracle(response): return _original(response)
def put(key, native, python, same, same_repr, meta, kind):
    return _record(["case", key, native, python, same, same_repr, str(meta), str(kind)])
def universe():
    return "".join(chr(c) for c in range(1, 0x110000) if not 0xD800 <= c < 0xE000)
def value(key, result): return _record(["value", key, result])
'''

PROBE = r'''
(= (probe:show $v) (case $v (((Error $a $b) "<raised>") ($o $o))))
(= (probe:compare $key $x)
   (let* (($_ (py-call (prose_record.begin $key)))
          ($n (probe:show (catch (prose:undelivered $x))))
          ($_ (py-call (prose_record.begin none)))
          ($p (probe:show (catch (py-call (prose_record.oracle $x))))))
     (py-call (prose_record.put $key $n $p (== $n $p)
                (== (repr (UNDELIVERED: $n NOT_DELIVERED)) (repr (UNDELIVERED: $p NOT_DELIVERED)))
                (get-metatype $x) (get-type $x)))))
'''

# MeTTa values that are not text, and text that is not UTF-8.
VALUES = {
    "number": "12", "float": "1.5", "negative": "-3", "true": "True", "false": "False",
    "symbol": "foo", "skill-symbol": "send", "unit": "()", "expression": '(a "b")',
    "variable": "$free", "literal": '"literal (pin \\"x\\") text ⋄⟨a⟩"',
    "malformed": '(str:concat (str:byte-slice "é" 0 1) " hi")',
    "malformed-tail": "(str:concat \"ok \" (str:byte-slice \"é\" 0 1))",
}


def cetta_env(directory, engine="cetta"):
    env = dict(os.environ)
    env.update(METTACLAW_ENGINE=engine, METTACLAW_ACTIVE_ENGINE=engine,
               PROSE_RECORD=str(directory / "record.jsonl"),
               PROSE_CASES=str(directory / "cases.json"),
               PYTHONPATH=os.pathsep.join((str(directory), str(ROOT / "src"),
                                           str(ROOT / "channels"))))
    env.setdefault("OPENAI_API_KEY", "test-only-unused")
    python = pathlib.Path(os.environ.get(
        "PETTA_PY_ENV", pathlib.Path.home() / "miniforge3" / "envs" / "petta"))
    if python.is_dir():
        env.update(PYTHONHOME=str(python), PYTHONNOUSERSITE="1",
                   LD_LIBRARY_PATH=os.pathsep.join((str(python / "lib"),
                                                    env.get("LD_LIBRARY_PATH", ""))))
    return env


class UndeliveredProseParity(unittest.TestCase):
    def setUp(self):
        self.binary = pathlib.Path(os.environ.get("CETTA_BIN", ""))
        if not self.binary.is_file():
            self.skipTest("set CETTA_BIN to a Python-enabled cetta")

    def run_cetta(self, lines, cases=(), engine="cetta"):
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            (directory / "prose_record.py").write_text(RECORDER)
            (directory / "cases.json").write_text(json.dumps(list(cases)), encoding="utf-8")
            with tempfile.NamedTemporaryFile("w", dir=ROOT, suffix=".metta",
                                             encoding="utf-8") as source:
                source.write("\n".join(lines) + "\n")
                source.flush()
                result = subprocess.run(
                    [str(self.binary), "--lang", "petta", "--import-mode", "ancestor-walk",
                     source.name], cwd=ROOT, env=cetta_env(directory, engine),
                    capture_output=True, text=True, timeout=900)
            record = directory / "record.jsonl"
            rows = ([json.loads(line) for line in
                     record.read_text(encoding="utf-8").splitlines()]
                    if record.exists() else [])
        detail = result.stdout[-3000:] + result.stderr[-3000:]
        self.assertEqual(result.returncode, 0, detail)
        return rows, result.stdout, detail

    def header(self):
        return ["!(import! &self ./src/helper.py)", "!(import! &self prose_record.py)",
                "!(import! &self ./src/undelivered_prose.metta)",
                "!(py-call (prose_record.watch))", PROBE]

    def compare(self, texts):
        lines = self.header()
        for i, text in enumerate(texts):
            lines.append('!(probe:compare "n%d" (str:from-codepoints '
                         '(py-call (prose_record.codes %d))))' % (i, i))
            if "\x00" not in text:
                lines.append('!(probe:compare "p%d" (py-call (prose_record.text %d)))' % (i, i))
        rows, _, detail = self.run_cetta(lines, texts)
        cases = {row[1]: row[2:] for row in rows if row[0] == "case"}
        reached = {row[1] for row in rows if row[0] == "python"}
        compared = 0
        for i, text in enumerate(texts):
            expected = as_returned(helper.undelivered_prose(text))
            for key in ("n%d" % i, "p%d" % i):
                if key.startswith("p") and "\x00" in text:
                    continue
                self.assertIn(key, cases, "%s %r: no answer\n%s" % (key, text[:200], detail))
                native, python, same, same_repr, meta, kind = cases[key]
                label = "%s %r" % (key, text[:200])
                self.assertEqual(native, expected, label)
                self.assertEqual(python, expected, label)
                self.assertTrue(same and same_repr, label)
                # Python answers only what is not text to CeTTa and prose
                # holding NUL, which makes no symbol.
                self.assertEqual(key in reached,
                                 not native_kind(meta, kind) or "\x00" in expected
                                 or (key.startswith("p") and text in BOUND_TOKENS),
                                 "%s (%s %s)" % (label, meta, kind))
                self.assertEqual(key.startswith("n"), (meta, kind) == ("Grounded", "String"), label)
                compared += 1
        return compared, len(reached)

    def test_handwritten_cases_match_python(self):
        texts = HANDWRITTEN + ["\x00", "a\x00b (pin \"x\x00\")", "\x00 send"]
        compared, reached = self.compare(texts)
        self.assertGreater(compared, 2 * len(HANDWRITTEN))
        self.assertLessEqual(reached, 3)

    def test_generated_cases_match_python(self):
        texts = generated(20261022, 2400)
        # Every branch of the reading occurs in what is compared.
        answers = [helper.undelivered_prose(t) for t in texts]
        self.assertGreater(sum(a == "" for a in answers), 200)
        self.assertGreater(sum(a != "" for a in answers), 200)
        self.assertGreater(sum(helper._is_bare_command(t) and not helper._top_level_forms(t.strip())[0]
                               for t in texts), 100)
        self.assertGreater(sum(not helper._top_level_forms(t)[1] for t in texts), 200)
        compared, reached = self.compare(texts)
        self.assertGreater(compared, 4500)
        self.assertLess(reached, 10)

    def test_large_responses_match_python(self):
        self.compare(large())

    def test_values_that_are_not_text_answer_as_python(self):
        lines = self.header() + ['!(let $x %s (probe:compare "%s" $x))' % (v, k)
                                 for k, v in VALUES.items()]
        rows, _, detail = self.run_cetta(lines)
        cases = {row[1]: row[2:] for row in rows if row[0] == "case"}
        reached = {row[1] for row in rows if row[0] == "python"}
        for key in VALUES:
            self.assertIn(key, cases, "%s: no answer\n%s" % (key, detail))
            native, python, same, same_repr, meta, kind = cases[key]
            self.assertEqual(native, python, key)
            self.assertTrue(same and same_repr, key)
        self.assertEqual(cases["true"][0], "true")
        self.assertEqual(cases["symbol"][0], "foo")
        self.assertEqual(cases["skill-symbol"][0], "")
        self.assertEqual(cases["literal"][0], "literal text")
        # Text that is not UTF-8 is handed to Python, whose conversion of
        # the argument raises before helper.undelivered_prose runs.
        self.assertEqual(cases["malformed"][0], "<raised>")
        self.assertEqual(cases["malformed-tail"][0], "<raised>")
        # Text, symbols, Bools and numbers stay native; the rest is Python's.
        self.assertEqual(reached, {"unit", "expression", "variable"})

    def test_unicode_classes_are_pythons_at_every_code_point(self):
        universe = "".join(chr(c) for c in range(1, 0x110000) if not 0xD800 <= c < 0xE000)
        breaks = {c for c in PYTHON_SPACE if len(("a" + c + "b").splitlines()) == 2}
        kept = {
            "line-break": lambda c: c not in breaks,
            "blank-run": lambda c: not c.isspace() or c in breaks,
            "word": str.isspace,
            "alnum": lambda c: not c.isalnum(),
        }
        lines = ["!(import! &self prose_record.py)",
                 "!(import! &self ./src/undelivered_prose.metta)",
                 "!(bind! &universe (new-state (str:concat \"\" (py-call (prose_record.universe)))))"]
        for name in kept:
            lines.append('!(py-call (prose_record.value "%s" (str:sha256 (re:replace-literal '
                         '(re:compile (prose:pattern %s)) (get-state &universe) ""))))' % (name, name))
            # U+0000 cannot cross from Python; it is in no class but word.
            lines.append('!(py-call (prose_record.value "nul-%s" (re:is-match '
                         '(re:compile (prose:pattern %s)) (str:from-codepoints (0)))))' % (name, name))
        rows, _, detail = self.run_cetta(lines)
        values = {row[1]: row[2] for row in rows if row[0] == "value"}
        for name, keep in kept.items():
            expected = hashlib.sha256("".join(c for c in universe if keep(c)).encode()).hexdigest()
            self.assertEqual(values.get(name), expected, "%s\n%s" % (name, detail))
            self.assertEqual(values.get("nul-" + name) in ("true", True), name == "word", name)

    def test_generated_tables_are_current(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/generate_prose_unicode.py"),
                                 "--check"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_pipeline_selects_native_prose_only_on_cetta(self):
        program = ["!(import! &self ./src/helper.py)", "!(import! &self prose_record.py)",
                   "!(import! &self ./src/command_pipeline.metta)",
                   "!(py-call (prose_record.watch))", '!(py-call (prose_record.begin "loop"))',
                   '!(py-call (prose_record.value "loop" (model-response-undelivered-prose '
                   '"Sure! ((send \\"x\\")) ⋄⟨C:.7⟩")))']
        for engine, native in (("cetta", True), ("petta", False)):
            with self.subTest(engine=engine):
                rows, _, detail = self.run_cetta(program, engine=engine)
                self.assertIn(["value", "loop", "Sure!"], rows, detail)
                self.assertEqual(["python", "loop"] in rows, not native, detail)
        loop = (ROOT / "src/loop.metta").read_text(encoding="utf-8")
        self.assertIn("($prose (model-response-undelivered-prose $respi))", loop)
        self.assertNotIn("helper.undelivered_prose", loop)


if __name__ == "__main__":
    unittest.main()
