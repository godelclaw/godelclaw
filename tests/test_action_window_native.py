"""Native proposal window and touched-file view against the Python oracle.

helper.recent_actions / helper.relevant_files stay the oracle and the SWI
backend. Every native result is compared byte for byte with the oracle's text,
and as the atom the old py-call bridge returned (count, kind, rendering and
equality). Python helpers, file reads and processes are disabled inside the
native runs. Fixtures cover the journal as appendToHistory writes it, a
journal written by the runtime itself, the older indented block form, and
adversarial bytes, boundaries, limits and Unicode.

Run with PYTHONPATH=tests:src and CETTA_BIN pointing to Python-enabled CeTTa.
Scratch files go to TMPDIR (or ACTION_WINDOW_TEST_ROOT).
"""
import contextlib
import hashlib
import io
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "channels")]

import helper  # noqa: E402
from isolated_env import isolated_environ  # noqa: E402
from test_action_window import block, journal_block  # noqa: E402

PYTHON = Path(os.environ.get("PETTA_PY_ENV", Path.home() / "miniforge3/envs/petta"))
CALLER_HEADER = ["!(import! &self (library lib_import))",
                 "!(import! &self ./src/helper.py)",
                 "!(import! &self ./tests/action_window_probe.py)"]


def q(value):
    # MeTTa has no JSON \uXXXX escape. Text with control characters is read
    # from native files by argument(), never smuggled through JSON literals.
    return json.dumps(str(value), ensure_ascii=False)


def guest_env(directory, engine="cetta", **overrides):
    env = isolated_environ(
        METTACLAW_ENGINE=engine, METTACLAW_ACTIVE_ENGINE=engine,
        METTACLAW_ENGINE_STATE_PATH=str(directory / "engine"),
        METTACLAW_HISTORY_PATH=str(directory / "history.metta"),
        METTACLAW_MEMORY_BACKEND_CONFIG=str(directory / "memory-backend.json"),
        XDG_STATE_HOME=str(directory / "state"),
        PYTHONPATH=os.pathsep.join(str(ROOT / name) for name in ("tests", "src", "channels")),
        TMPDIR=str(directory))
    if PYTHON.is_dir():
        env.update(PYTHONHOME=str(PYTHON), PYTHONNOUSERSITE="1",
                   LD_LIBRARY_PATH=str(PYTHON / "lib") + os.pathsep + env.get("LD_LIBRARY_PATH", ""))
    for name, value in overrides.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    return env


def journal_fixtures(rng):
    """The journal as appendToHistory writes it, and what can go wrong in it."""
    fixtures = [
        journal_block("2026-10-23 00:05:10", '((shell "cat src/old.py"))', message="[telegram chat=x] hi"),
        journal_block("t1", '((shell "cat src/a.py"))') + journal_block("t2", '((send "done with src/b.py"))',
                                                                       feedback='((send "x") "failed tests/c.py")'),
        journal_block("t1", "((nop))") + journal_block("t2", "(( ))") + journal_block("t3", "((nop) (nop))")
        + journal_block("t4", "((no_commands_parsed))") + journal_block("t5", "((no(nop)_commands_parsed))")
        + journal_block("t6", "( (nop) ( no_commands_parsed ) )"),
        journal_block("t1", '((shell "echo ()"))') + journal_block("t2", '((send "(nop)"))')
        + journal_block("t3", "((nop) x)") + journal_block("t4", "((n op))"),
        journal_block("t1", '((shell "cat src/a.py"))') + journal_block("t2", '((shell "cat src/b.py"))')
        + journal_block("t3", '((shell "cat src/a.py src/c.py src/a.py"))'),
        # A response ending in an escaped quote keeps it; a bare one is trimmed.
        '("t1" "\n" ((send "say \\"src/q.py\\"\n")\n',
        '("t1" "\n" ((send "say src/q.py" "\n")\n',
        '("t1" "\n" ((send "a")) "\n" ((later src/later.py)) "\n")\n',
        '("t1" "\n" ((shell "first line\nsecond src/two.py\n  ")\n" ")\n" ERROR_FEEDBACK: (x src/err.py))\n',
        '("t1" "\n" ((shell "x"))\n ERROR_FEEDBACK: no quote src/feedback.py\n',
        '("t1" "\n" ((shell "x"))\n   ")   \n trailing src/after.py\n',
        '("t1" "\n"   "   ((shell "spaced src/sp.py")) "  \n")\n',
        '("t1" "\n\u3000"\u00a0((shell "unicode space src/u.py"))\u2003"\u3000\n")\n',
        '("t1" "\n" "HUMAN_MESSAGE: " "((shell \\"inbound src/in.py\\"))" "\n" ((send "reply")) "\n")\n',
        '("t1" "\n" "HUMAN_MESSAGE: " "first\n((shell src/message-line.py))" "\n" ((send "r")) "\n")\n',
        '("t1" "\n" ""((shell "double quote src/dq.py")) "\n")\n',
        '("stamp only" "\n")\n',
        '("t1" " "\n" ((shell "stamp ends quote space quote"))\n',
        '("t1"  "  \n" ((send "stamp padding")) "\n")\n',
        '("t1" "\n" "((send \\"just a quote\\"))"\n")\n',
        '("t1" "\n" ((send "trailing quote")) ""\n")\n',
        '("t1" "\n" ((send "x")) \\"\n")\n',
        '("t1" "\n" ((shell "' + "x" * 13000 + ' src/big.py")) "\n")\n',
        journal_block("t1", '((shell "cat src/a.py"))') * 3 + journal_block("t2", "((nop))") * 400,
    ]
    for separator in ("\r\n", "\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"):
        fixtures.append((journal_block("a", '((shell "src/a.py"))', message="m")
                         + journal_block("b", '((shell "src/b.py"))', feedback="(e src/e.py)")).replace("\n", separator))
    words = ["src/a.py", "tests/b.py", "/workspace/x.py", "~/notes.txt", "./x/file", "docs/noext", "word/word",
             "src/².py", "src/日本.py", "http://site/a.py", "//x/y.py", "src/a.py...", "()", "(nop)", '\\"', '"']
    for index in range(120):
        journal = rng.choice(["", "preamble\n", 'x" "\n'])
        for turn in range(rng.randint(0, 24)):
            command = rng.choice(["((nop))", "((no_commands_parsed))", "(( ))", "()", "",
                                  '((shell "%s"))' % " ".join(rng.choices(words, k=rng.randint(1, 8))),
                                  '((send "%s") (shell "%s"))' % ("λ🙂" * rng.randint(0, 30), rng.choice(words)),
                                  '((shell "a\nb %s"))' % rng.choice(words)])
            message = rng.choice([None, None, "hi", "src/inbound.py", '((shell \\"fake\\"))'])
            feedback = rng.choice([None, None, None, '((shell "x") "failed")', "tests/feedback.py"])
            text = journal_block("g%d-%d" % (index, turn), command, message=message, feedback=feedback)
            if rng.random() < .2:
                text = text.replace('" ', rng.choice(['"', '"  ', '\t" ', ' " ']), 1)
            if rng.random() < .1:
                text = block("old%d" % turn, command, message)
            journal += text.replace("\n", rng.choice(["\n", "\n", "\r\n", "\u2028"]))
        fixtures.append(journal)
    return fixtures


def block_fixtures(rng):
    """The older indented block form, which the oracle also reads."""
    fixtures = [
        "", "unframed ((shell src/ignored.py))\n", b"no header\n ((shell src/ignored.py))\n",
        block("empty", ""), block("t1", '((shell "cat src/a.py"))') + block("t2", "((nop))"),
        block("t1", '((send "Atomspace/Chroma vericore/openclaw"))'),
        block("t1", '((shell "cat /dev/null /tmp http://host/x.py //host/x.py"))'),
        block("t1", '((shell "cat /a/b ./foo/bar ~/foo/bar src/x docs/a a/b.py foo/bar"))'),
        block("t1", '((shell "src/a.py tests/b.py src/a.py"))') + block("t2", '((shell "tests/b.py src/c.py src/a.py"))'),
        block("t1", '((shell "src/café.py src/².py src/¼.py src/́.py src/𐐀.py λ/src/a.py"))'),
        block("t1", '((shell "\u1c89src/new.py src/\u1c89.py src/\U0001e4d0.py src/\U00010d40.py"))'),
        block("t1", '((shell "src/a.py... ./a/b........ /a/"))'),
        block("t1", '((shell "src/name... src/one.py, ./two.py; ~/three.py: /last/file.py\' src/a+b-c@d.py"))'),
        block("t1", '((shell "./abc/def ~/abc/def /abc/def docs/readme tests/noext httpfile/a.py '
                    'https://example.com/a.py a/b/c/d/e/f/g/h/i/j/k/l/m/n.py"))'),
        block("t1", '((shell "src/český.py 中/文件.py src/²Ⅷ.py ²src/name.py é/src/end.py"))'),
        block("t1", "((nop))" + "x" * 31), block("t1", "((nop))" + "x" * 33), block("t1", "(( ) (\t ))"),
        block("t1", '((shell "λ🙂' + "x" * 13000 + '"))'),
        block("t1", '((shell "before\x00after src/nul.py"))'),
        '("t"\n ((shell "x"))\nERROR_FEEDBACK: src/receipt.py\n',
        '("t"\n HUMAN_MESSAGE: ((shell "src/inbound.py"))\n no response\n',
        '("t" ((shell "src/same-line.py"))\n', 'preamble\n ("indented"\n ((shell "ignored.py"))\n',
        '("header"\nprose ((shell "ignored"))\n\t((shell "src/chosen.py"))\nTAIL\n',
        block("t1", '((shell "' + "/".join(["a"] * 13) + '/x.py"))'),
        block("space", '\u00a0\u001f((shell "src/space.py"))\u3000'),
        block("escapes", '((shell "_quote_ _newline_ $v \\n src/quote.py"))'),
        block("large", '((shell "%s src/last.py"))' % ("é🚀" * 7000)),
        block("older", '((shell "src/old.py"))') + block("new", '((shell "%s"))' % ("x" * 12000)),
        b'("bad-utf8"\n ((shell "src/a.py \xff\xe2\x82\xed\xa0\x80\xf4\x90\x80\x80 end"))\n',
        b"prefix" * 50000 + block("tail", '((shell "src/tail.py"))').encode(),
        block("cut", '((shell "%s"))' % ("x" * 270000)),
        b"x" * 262144 + b'("partial-tail"\n ((shell "src/partial.py"))',
        b"\xf0\x9f\x9a\x80" * 65536 + block("cut-utf8", '((shell "src/end.py"))').encode(),
        block("old", '((shell "src/old.py"))') + block("noop", "((nop))") * 5000,
        block("dense", '((shell "%s"))' % " ".join("src/f%d.py" % n for n in range(3000))),
    ]
    for separator in ("\n", "\r\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"):
        fixtures.append(separator.join(['("t1"  ', '\u001f\u00a0((shell "src/a.py"))\u3000', '("t2"',
                                        '\t((send "ok"))', ""]))
    words = ["src/a.py", "tests/b.py", "/workspace/c.metta", "~/notes.txt", "./x/file", "docs/noext", "a/b",
             "src/².py", "src/č.py", "httpx/a.py", "//server/file.py", "_newline_", "()", "é/src/a.py"]
    for index in range(100):
        turns = ["preamble\n"]
        for turn in range(rng.randrange(22)):
            contents = " ".join(rng.choices(words, k=rng.randrange(1, 10)))
            command = rng.choice(["((nop))", "((no_commands_parsed))", "((()))", "",
                                  '((shell "%s"))' % contents, '((send "%s"))' % contents])
            if rng.randrange(5) == 0:
                command += "\n" + rng.choice(["ERROR_FEEDBACK: observed", " ((later src/later.py))", " tail ()", " " * 40])
            text = block("g%d-%d" % (index, turn), command, '"src/inbound.py"' if rng.randrange(2) else None)
            turns.append(text.replace("\n", rng.choice(["\n", "\r\n", "\x85", "\u2028"])))
        fixtures.append("".join(turns))
    alphabet = "abcXYZ019_./~@+-:;'\"(),%\\|" + "λ²¼\u0301\u1c89🙂日本"
    for _ in range(20):
        text = " ".join("".join(rng.choices(alphabet, k=rng.randint(1, 50))) for _ in range(100))
        fixtures.append(block("fuzz", '((shell "%s"))' % text))
    for _ in range(32):
        payload = rng.randbytes(rng.randrange(1, 700)).replace(b"\x00", b" ")
        fixtures.append(b'("bytes"\n ((shell "src/bytes.py ' + payload + b'"))\n')
    return fixtures


class NativeActionWindowTest(unittest.TestCase):
    def setUp(self):
        self.binary = Path(os.environ.get("CETTA_BIN", ""))
        if not self.binary.is_file():
            self.skipTest("set CETTA_BIN to a Python-enabled CeTTa")
        base = os.environ.get("ACTION_WINDOW_TEST_ROOT")
        if base:
            Path(base).mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.arguments = 0

    def argument(self, value):
        if isinstance(value, str):
            path = self.directory / ("argument-%d" % self.arguments)
            self.arguments += 1
            path.write_text(value, encoding="utf-8")
            return "(fs:read-text %s)" % q(path)
        if isinstance(value, bool):
            return "True" if value else "False"
        if value is None or isinstance(value, list):
            # Python receives an expression as a list, so int() rejects it.
            return "()"
        if isinstance(value, float) and not math.isfinite(value):
            # Native parsing rejects nonfinite literals. Supply the Number atom
            # through the bridge, solely as a test input constructor.
            return "(py-call (builtins.float %s))" % q(value)
        return repr(value)

    def run_guest(self, lines, engine="cetta", swi=False, cwd=ROOT, **env):
        with tempfile.NamedTemporaryFile("w", dir=ROOT, suffix=".metta", encoding="utf-8") as source:
            source.write("\n".join(lines) + "\n")
            source.flush()
            if swi:
                petta = Path(os.environ.get("PETTA_ROOT", Path.home() / "repos/PeTTa"))
                if not (petta / "run.sh").is_file() or not shutil.which("swipl"):
                    self.skipTest("SWI-PeTTa unavailable")
                command = ["bash", str(petta / "run.sh"), source.name, "default"]
            else:
                command = [str(self.binary), "--lang", "petta", "--import-mode", "ancestor-walk", source.name]
            result = subprocess.run(command, cwd=cwd, env=guest_env(self.directory, engine, **env),
                                    capture_output=True, text=True, timeout=600)
        detail = result.stdout[-8000:] + result.stderr[-8000:]
        self.assertEqual(result.returncode, 0, detail)
        self.assertNotIn("AssertionError", result.stdout + result.stderr, detail)
        self.assertNotIn("uncaught", result.stderr, detail)
        return result.stdout

    def history(self, name, content):
        path = self.directory / name
        path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
        return path

    def compare(self, cases):
        """cases: (oracle function name, path or MeTTa path expression, args)."""
        expected, calls = [], []
        for kind, path, args, *native_path in cases:
            with contextlib.redirect_stdout(io.StringIO()):
                expected.append(getattr(helper, kind)(str(path), *args))
            arguments = [native_path[0] if native_path else self.argument(str(path))]
            arguments += [self.argument(value) for value in args]
            calls.append("(aw:%s %s)" % (kind.replace("_", "-"), " ".join(arguments)))
        oracle = self.directory / "expected.json"
        oracle.write_text(json.dumps(expected), encoding="utf-8")
        lines = ["!(import! &self ./src/helper.py)", "!(import! &self ./tests/action_window_probe.py)",
                 "!(import! &self ./src/action_window_cetta)",
                 "!(py-call (action_window_probe.load %s))" % q(oracle),
                 "!(py-call (action_window_probe.forbid))"]
        for index, expression in enumerate(calls):
            lines.append(
                "!(let* (($answers (collapse %s)) ($text (car-atom $answers)) "
                "($atom (aw:legacy-atom $text)) "
                "($oracle (py-call (action_window_probe.expected %d)))) "
                "(progn (fs:update-text %s \"replace\" $text \"\" \"\") "
                "(println! (Check %d (size-atom $answers) "
                "(== (get-type $text) String) (== $atom $oracle) "
                "(== (get-metatype $atom) (get-metatype $oracle)) "
                "(== (repr $atom) (repr $oracle))))))" %
                (expression, index, q(self.directory / ("output-%d" % index)), index))
        stdout = self.run_guest(lines)
        for index, case in enumerate(cases):
            with self.subTest(index=index, case=case[:2]):
                self.assertEqual((self.directory / ("output-%d" % index)).read_bytes(),
                                 expected[index].encode("utf-8"))
        # The bridge's Symbol keeps a hidden Python payload after NUL that no
        # native Symbol can hold: identity is checked separately below.
        self.assertEqual(re.findall(r"\(Check (\d+) (\d+) (true|false) (true|false) (true|false) (true|false)\)", stdout),
                         [(str(i), "1", "true", "false" if "\0" in expected[i] else "true", "true", "true")
                          for i in range(len(cases))], stdout[-8000:])
        print("Compared %d full-text results and legacy atoms (%d NUL identity gaps)" %
              (len(cases), sum("\0" in text for text in expected)))
        return expected

    def journal_cases(self, fixtures, rng, prefix):
        cases = []
        for index, content in enumerate(fixtures):
            path = self.history("%s-%d_č \"q\" \\b.metta" % (prefix, index), content)
            cases += [(kind, path, ()) for kind in ("recent_actions", "relevant_files")]
            cases += [("recent_actions", path, (rng.choice([-3, 0, 1, 3, 8, 50, 2.9, " 3 ", "1_0", "٢", "bad"]),
                                                rng.choice([0, 499, 500, 501, 800, 12000, 60000]))),
                      ("relevant_files", path, (rng.choice([1, 3, 8, 20, 60, "bad"]),
                                                rng.choice([0, 1, 3, 25, -5, "2", "bad"])))]
        return cases

    def test_journal_as_the_runtime_writes_it(self):
        rng = random.Random(102301)
        expected = self.compare(self.journal_cases(journal_fixtures(rng), rng, "journal"))
        # The fixtures must exercise the new reader, not only its empty answers.
        self.assertGreater(sum(text not in ("(no prior actions recorded)", "(none)") for text in expected), 300)

    def test_block_form_and_adversarial_journals(self):
        rng = random.Random(73219)
        self.compare(self.journal_cases(block_fixtures(rng), rng, "block"))

    def test_turns_written_by_the_runtime(self):
        history = self.directory / "written.metta"
        responses = ['((fixture-skill "cat src/written.py"))', "((nop))",
                     '((shell "echo \\"quoted\\" > tests/q.py") (send "two\\nlines src/two.py"))',
                     '((send "after an error docs/err.md"))', "()", '((shell "echo ()"))',
                     '((write-file "src/λ/文件.py" "x"))']
        lines = ["!(import! &self %s)" % (ROOT / "src/helper.py"), "!(import! &self %s)" % (ROOT / "src/utils"),
                 "!(import! &self %s)" % (ROOT / "src/file_io"), "!(import! &self %s)" % (ROOT / "src/memory.metta"),
                 "!(bind! &error (new-state ()))"]
        for index, response in enumerate(responses):
            if index == 3:
                lines.append("!(change-state! &error (fixture-error src/feedback.py))")
            lines.append('!(addToHistory "%s" %s %s)' % ("hello src/inbound.py" if index % 2 else "", response,
                                                         "True" if index % 2 else "False"))
        self.run_guest(lines, METTACLAW_HISTORY_PATH=str(history))
        window = helper.recent_actions(str(history), limit=8)
        self.assertIn('((fixture-skill "cat src/written.py"))', window)
        self.assertNotIn("HUMAN_MESSAGE", window)
        self.assertNotIn("fixture-error", window)
        cases = [(kind, history, args) for kind in ("recent_actions", "relevant_files")
                 for args in ((), (8,), (2, 500), (12,))]
        self.compare(cases)

    def test_byte_tail_filesystem_and_exact_inputs(self):
        data = [b"\xc0\xaf\xed\xa0\x80\xf0\x80\x80\x80\xe2\x82!\xf0\x9f\x92",
                random.Random(9).randbytes(2000),
                b"".join(bytes((x, y)) for x in range(256) for y in range(256))]
        paths = [self.directory / "missing", self.directory]
        for i, raw in enumerate(data):
            paths.append(self.history("malformed-%d" % i, b'("t" "\n" ((shell "' + raw + b' src/x.py")) "\n")\n'))
        for tail in (block("kept", '((shell "src/kept.py"))').encode(),
                     journal_block("kept", '((shell "src/kept.py"))').encode()):
            # The tail starts in a UTF-8 scalar, in a block, on a header, and just past one.
            for offset in [-2, -1, 0, 1, 2]:
                raw = journal_block("old", '((shell "src/old.py"))').encode()
                raw += b"x" * (262144 - len(tail) + offset) + tail
                paths.append(self.history("boundary-%d-%d" % (len(tail), offset), raw))
            for offset in range(4):
                paths.append(self.history("utf8-tail-%d-%d" % (len(tail), offset),
                                          b"preamble\n" + "🙂".encode() * 70000 + b"x" * offset + b"\n" + tail))
        denied = self.history("denied", journal_block("t", '((shell "src/denied.py"))'))
        denied.chmod(0)
        paths.append(denied)
        linked = self.directory / "linked"
        linked.symlink_to(paths[-2])
        paths.append(linked)
        paths.append(self.history('spaces "quotes" \\slashes λ\n.metta', journal_block("t", '((shell "src/exact.py"))')))
        cases = [(kind, path, ()) for path in paths for kind in ("recent_actions", "relevant_files")]
        exact = paths[-1]
        # A path delivered as the Symbol a Python call returns, as on the old bridge.
        cases += [(kind, exact, (), "(py-call (builtins.str %s))" % self.argument(str(exact)))
                  for kind in ("recent_actions", "relevant_files")]
        cases += [(kind, str(exact) + "\x00ignored", ()) for kind in ("recent_actions", "relevant_files")]
        self.compare(cases)
        # Text arguments must not be reinterpreted by MeTTa escaping or the bridge.
        exact = "λ\x00\x1f\u0085\r\n\t\\n\"$v()🙂"
        argument = self.argument(exact)
        digest = hashlib.sha256(exact.encode()).hexdigest()
        stdout = self.run_guest(["!(import! &self ./src/action_window_cetta)",
            "!(println! (Exact (== (str:sha256 %s) %s) (str:char-length %s)))" % (argument, q(digest), argument)])
        self.assertIn("(Exact true %d)" % len(exact), stdout)

    def test_defaults_limits_and_invalid_values(self):
        path = self.history("limits", "".join(journal_block("t%d" % i, '((shell "src/f%d.py %s"))' % (i, "x" * 80))
                                              for i in range(30)))
        values = [0, -8, 1, 2, 3.9, -2.9, 1.9, -1.9, 100, True, False, 10 ** 40, -(10 ** 30), 1e50,
                  float("nan"), float("inf"), -float("inf"),
                  "8", "8.0", "8junk", " +3 ", "  +0002\t", "1_0", "2_0", "_2", "2__0", "١٢", "１２", "٢", "𝟠",
                  "\x1f3\u3000", "", " ", "1.5", "1e2", "1e3", "1__0", "+ 3", "²", "0x10", "nan", "inf", "3\x00",
                  None, "9" * 4300, "9" * 4301, "0" * 4301, "-" + "0" * 4301]
        values += [chr(c) + "3" + chr(c) for c in range(0x110000) if chr(c).isspace()]
        values += ["\u3000+" + chr(c) + "\u00a0" for c in range(0x110000) if unicodedata.decimal(chr(c), -1) == 2]
        cases = []
        for value in values:
            oracle_value = [] if value is None else value
            for kind, args in [("recent_actions", (oracle_value,)), ("recent_actions", (8, oracle_value)),
                               ("relevant_files", (oracle_value,)), ("relevant_files", (8, oracle_value))]:
                cases.append((kind, path, args))
        for value in (-10, 0, 1, 2, 8, 100, True, "2_0", "٢", "bad"):
            for maximum in (1, 500, 501, "bad"):
                cases += [("recent_actions", path, (value, maximum)), ("relevant_files", path, (value, maximum))]
        self.compare(cases)

    @unittest.expectedFailure
    def test_nul_foreign_symbol_identity_is_an_engine_carrier_limitation(self):
        argument = self.argument('("t" ((shell "before\0after"))')
        stdout = self.run_guest(["!(import! &self ./src/action_window_cetta)",
            "!(let* (($text %s) ($oracle (py-call (builtins.str $text))) "
            "($native (aw:legacy-atom $text))) (println! (Identity (== $native $oracle))))" % argument])
        self.assertIn("(Identity true)", stdout)

    def test_unicode_properties_equal_the_oracle_exhaustively(self):
        # No guess about the native regex Unicode version: verify every scalar.
        text = "".join(chr(c) for c in range(0x110000) if not 0xD800 <= c <= 0xDFFF)
        source = self.history("unicode-scalars", text)
        expected = "".join(c for c in text if c.isalnum())
        digest = hashlib.sha256(expected.encode()).hexdigest()
        actual = self.directory / "unicode-actual"
        stdout = self.run_guest(["!(import! &self ./src/action_window_cetta)",
            '!(let $letters (re:replace-literal (re:compile "[^\\\\p{L}\\\\p{N}]+") '
            '(aw:python-alnum-text (fs:read-text %s)) "") (progn (fs:update-text %s "replace" $letters "" "") '
            '(println! (Unicode (== (str:sha256 $letters) %s)))))' % (q(source), q(actual), q(digest))])
        native = actual.read_text(encoding="utf-8")
        missing = sorted(set(expected) - set(native))
        extra = sorted(set(native) - set(expected))
        self.assertIn("(Unicode true)", stdout, "missing=%r extra=%r" %
                      ([hex(ord(c)) for c in missing[:100]], [hex(ord(c)) for c in extra[:100]]))

    def test_checked_in_unicode_tables(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/generate_action_window_unicode.py"), "--check"],
                                cwd=ROOT, env=guest_env(self.directory), capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def caller_lines(self, table, file_io=True, forbid=False):
        lines = CALLER_HEADER + (["!(import! &self ./src/file_io)"] if file_io else []) + [
            "!(import! &self ./src/memory)",
            "!(remove-atom &self (= (maxRecentTurns) (empty)))",
            "!(add-atom &self (= (maxRecentTurns) 8))",
            "!(py-call (action_window_probe.load %s))" % q(table)]
        if forbid:
            lines.append("!(py-call (action_window_probe.forbid))")
        for i, name in enumerate(["recentActions", "relevantFiles"]):
            lines.append("!(let* (($answers (collapse (%s))) ($actual (car-atom $answers)) "
                         "($expected (py-call (action_window_probe.expected %d)))) "
                         "(println! (Caller %d (size-atom $answers) (get-metatype $actual) "
                         "(== $actual $expected) (== (repr $actual) (repr $expected)))))" % (name, i, i))
        return lines

    def assert_callers(self, stdout):
        self.assertEqual(re.findall(r"\(caller (\d+) (\d+) (\w+) (true|false) (true|false)\)", stdout.lower()),
                         [(str(i), "1", "symbol", "true", "true") for i in range(2)], stdout[-8000:])

    def test_memory_callers_and_engine_selection(self):
        path = self.history("history.metta", journal_block("t1", '((shell "src/old.py"))', message="hi")
                            + journal_block("t2", '((shell "tests/now.py"))', feedback="(e src/err.py)"))
        table = self.directory / "expected.json"
        table.write_text(json.dumps([helper.recent_actions(path, 8), helper.relevant_files(path, 8)]))
        # Native is chosen on CeTTa even when file_io was not imported first.
        for engine, swi, file_io in (("cetta", False, True), ("cetta", False, False),
                                     ("petta", False, True), ("petta", True, True)):
            with self.subTest(engine=engine, swi=swi, file_io=file_io):
                stdout = self.run_guest(self.caller_lines(table, file_io, forbid=engine == "cetta"), engine, swi)
                self.assert_callers(stdout)

    def test_caller_history_path_spellings(self):
        """The native history-path lookup opens what the old bridge opened.

        path_from_env's text crossed py-call as a Symbol, so "True" reached
        the Python view as "true"; the empty value stays empty."""
        odd = 'history_"_quote_\\newline_\n_č.metta'
        for name in ("true", "false", "memory/history.metta", odd):
            path = self.directory / name
            path.parent.mkdir(exist_ok=True)
            path.write_text(journal_block(name, '((shell "src/env-%d.py"))' % len(name)))
        for configured, opened in (("True", "true"), ("False", "false"), ("", ""),
                                   (None, "memory/history.metta"), (odd, odd)):
            with self.subTest(configured=configured):
                table = self.directory / "expected.json"
                table.write_text(json.dumps([helper.recent_actions(str(self.directory / opened) if opened else "", 8),
                                             helper.relevant_files(str(self.directory / opened) if opened else "", 8)]))
                stdout = self.run_guest(self.caller_lines(table, forbid=True), cwd=self.directory,
                                        METTACLAW_HISTORY_PATH=configured)
                self.assert_callers(stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
