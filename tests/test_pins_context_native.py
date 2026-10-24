"""Native pins.context_view() and its change marker against the Python oracle.

pins.context_view() stays the oracle and the SWI backend. Every native result
is compared byte for byte with the oracle's text. The guest runs with the
Python pins readers disabled (tests/pins_context_probe.forbid), so a silent
fallback cannot pass for the native implementation; the probe counts every
attempt to use them. The caller path (turn_additions' addition-context-sources
into context_sources.bundle_with_views) is exercised with the real bundle,
restricted to the pins source. All state is under a temporary directory: no
live pin file, ledger, control file or channel is read or written.

Run with PYTHONPATH=tests:src and CETTA_BIN pointing to Python-enabled CeTTa.
PETTA_PY_ENV can select its Python prefix; PETTA_ROOT enables the optional
SWI-PeTTa check. Scratch files go to TMPDIR.
"""
import contextlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "channels")]

import context_sources  # noqa: E402
import helper  # noqa: E402
import pins  # noqa: E402
from isolated_env import isolated_environ  # noqa: E402

PYTHON = Path(os.environ.get("PETTA_PY_ENV", sys.prefix))
HEADER = ["!(import! &self (library lib_import))",
          "!(import! &self ./src/helper.py)",
          "!(import! &self ./tests/pins_context_probe.py)"]
ERROR = "<<error>>"


def q(value):
    return json.dumps(str(value), ensure_ascii=False)


def guest_env(directory, engine="cetta", **overrides):
    env = isolated_environ(
        METTACLAW_ENGINE=engine, METTACLAW_ACTIVE_ENGINE=engine,
        METTACLAW_ENGINE_STATE_PATH=str(directory / "engine"),
        METTACLAW_HISTORY_PATH=str(directory / "history.metta"),
        METTACLAW_PINS_PATH=str(directory / "pins.txt"),
        METTACLAW_TELEGRAM_LOG_PATH=str(directory / "updates.jsonl"),
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


def pin_lines(*lines, newline="\n", final=True):
    return (newline.join(lines) + (newline if final and lines else "")).encode("utf-8")


def fixtures(rng):
    """(name, bytes or None for a missing file)."""
    stamp = "[10-24 09:%02d] "
    out = [("missing", None), ("empty", b""), ("newline only", b"\n"), ("blank lines", b"\n\n\n"),
           ("spaces", b"   \n\t\n  \n"),
           ("one", pin_lines(stamp % 1 + "one")),
           ("no final newline", pin_lines(stamp % 1 + "one", stamp % 2 + "two", final=False)),
           ("two", pin_lines(stamp % 1 + "one", stamp % 2 + "two")),
           ("blank between", pin_lines(stamp % 1 + "one", "", "   ", stamp % 2 + "two", "")),
           ("leading and trailing spaces kept", pin_lines("  " + stamp % 1 + "padded  ", "\tx\t")),
           ("duplicates", pin_lines(*[stamp % 1 + "same"] * 5)),
           ("unicode", pin_lines(stamp % 1 + "λ café 日本語 🙂 e\u0301 \u200b\ufeff", stamp % 2 + "Σ\u00a0nbsp")),
           ("bom", b"\xef\xbb\xbf" + pin_lines(stamp % 1 + "bom")),
           ("nul", pin_lines(stamp % 1 + "a\0b", "\0")),
           ("replacement char is valid", pin_lines(stamp % 1 + "bad \ufffd text")),
           ("crlf", pin_lines(stamp % 1 + "one", stamp % 2 + "two", newline="\r\n")),
           ("cr only", pin_lines(stamp % 1 + "one", stamp % 2 + "two", newline="\r")),
           ("mixed endings", b"a\r\nb\rc\nd\r\r\n\ne\n\r"),
           ("not line breaks to a file", pin_lines("a\x0bb", "c\x0cd", "e\x1cf\x1dg\x1eh", "i\x85j", "k\u2028l\u2029m")),
           ("unicode blank lines", pin_lines("\u3000", "\x85", "\x1c\x1d\x1e\x1f", "\u2028", "\u00a0\u2003", "\u200b",
                                             "\ufeff", "\u180e", "\u2060", stamp % 1 + "kept")),
           ("whitespace only variants", "\u0009\u000b\u000c\u001c\u001f \u0085\u00a0\u1680\u2000\u200a\u2028\u2029\u202f\u205f\u3000\n"
                                        .encode("utf-8")),
           ("long 399", pin_lines(stamp % 1 + "x" * (399 - 17))),
           ("long 400", pin_lines(stamp % 1 + "x" * (400 - 17))),
           ("long 401", pin_lines(stamp % 1 + "x" * (401 - 17))),
           ("long 5000", pin_lines(stamp % 1 + "y" * 5000)),
           ("long multibyte 400", pin_lines("λ" * 400)),
           ("long multibyte 401", pin_lines("λ" * 401)),
           ("long astral 401", pin_lines("🙂" * 401)),
           ("long combining", pin_lines("e\u0301" * 300)),
           ("long surrounded", pin_lines("short", "z" * 700, "short too")),
           ("cap 12", pin_lines(*[stamp % i + "pin %d" % i for i in range(12)])),
           ("cap 13", pin_lines(*[stamp % i + "pin %d" % i for i in range(13)])),
           ("cap 30 long", pin_lines(*[stamp % i + "w" * 500 + str(i) for i in range(30)])),
           ("big many", pin_lines(*[stamp % (i % 60) + "pin %d λ" % i for i in range(3000)])),
           ("big blank tail", pin_lines("kept") + b"\n" * 20000 + b"  \n" * 2000),
           ("brackets", pin_lines("[x] ] [ ]] ", "] x")),
           ("pipe in text", pin_lines("a | b", "| |", "|")),
           ("quote and backslash", pin_lines('say "hi" \\n \\ $x (y) ; z', "'q'")),
           ("metta looking", pin_lines("(pin \"x\")", "!(println! 1)", "((", "))")),
           ("header lookalike", pin_lines("PINNED newest 1 of 1; (pins) reads the full retained list")),
           ]
    alphabet = ["a", "b", " ", "  ", "\t", "|", "[", "]", "λ", "é", "e\u0301", "日", "🙂", "\u3000", "\u00a0",
                "\x0b", "\x85", "\u2028", "(", ")", '"', "\\", "\0", "\ufeff", "…", "x" * 50]
    for index in range(150):
        lines = []
        for _ in range(rng.choice([0, 1, 2, 5, 11, 12, 13, 14, 20, 40])):
            body = "".join(rng.choices(alphabet, k=rng.choice([0, 1, 3, 10, 60, 450])))
            lines.append(body)
        newline = rng.choice(["\n", "\n", "\n", "\r\n", "\r"])
        out.append(("random %d" % index, pin_lines(*lines, newline=newline, final=rng.random() < .8)))
    return out


MALFORMED = [
    ("lone continuation", b"[10-24 09:00] a\xffb\n"),
    ("truncated sequence at end", b"[10-24 09:00] ok\n\xe2\x82"),
    ("truncated before newline", b"\xf0\x9f\x92\n"),
    ("overlong", b"\xc0\xaf\n"),
    ("surrogate", b"\xed\xa0\x80\n"),
    ("beyond U+10FFFF", b"\xf4\x90\x80\x80\n"),
    ("latin-1 text", "café\n".encode("latin-1")),
    ("only a bad byte", b"\x80"),
    ("late bad byte", b"ok\n" * 5000 + b"\xff\n"),
]


class Oracle:
    """pins.context_view() in this process, with an isolated pin file."""

    def __init__(self, path):
        self.path = path

    @contextlib.contextmanager
    def environment(self, **settings):
        with mock.patch.dict(os.environ, {"METTACLAW_PINS_PATH": str(self.path)}):
            for name, value in settings.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            yield

    def view(self):
        try:
            return pins.context_view()
        except Exception:
            return ERROR

    def body(self):
        """The oracle's text without its change marker (the native view's text)."""
        text = self.view()
        return re.sub(r"\A(?: \(changed\)| \(unchanged for \d+ turns?\))? (?=PINNED newest)", "", text)


def put(path, content):
    if content is None:
        with contextlib.suppress(FileNotFoundError):
            os.remove(path)
    else:
        Path(path).write_bytes(content)


class NativePinsContextTest(unittest.TestCase):
    def setUp(self):
        binary = os.environ.get("CETTA_BIN", "")
        self.binary = Path(binary)
        if not self.binary.is_file():
            self.skipTest("set CETTA_BIN to a Python-enabled CeTTa")
        base = os.environ.get("PINS_CONTEXT_TEST_ROOT")
        if base:
            Path(base).mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.pins = self.directory / "pins.txt"
        self.outputs = 0
        helper._change_marks.clear()
        self.addCleanup(helper._change_marks.clear)

    def run_guest(self, lines, engine="cetta", swi=False, cwd=ROOT, **env):
        with tempfile.NamedTemporaryFile("w", dir=ROOT, suffix=".metta", encoding="utf-8") as source:
            source.write("\n".join(lines) + "\n")
            source.flush()
            if swi:
                petta = Path(os.environ.get("PETTA_ROOT", ""))
                if not (petta / "run.sh").is_file() or not shutil.which("swipl"):
                    self.skipTest("set PETTA_ROOT to a SWI-PeTTa checkout")
                command = ["bash", str(petta / "run.sh"), source.name, "default"]
            else:
                command = [str(self.binary), "--lang", "petta", "--import-mode", "ancestor-walk", source.name]
            result = subprocess.run(command, cwd=cwd, env=guest_env(self.directory, engine, **env),
                                    capture_output=True, text=True, timeout=900)
        detail = result.stdout[-6000:] + result.stderr[-6000:]
        self.assertEqual(result.returncode, 0, detail)
        self.assertNotIn("AssertionError", result.stdout + result.stderr, detail)
        self.assertNotIn("uncaught", result.stderr, detail)
        return result.stdout

    # ---- view-level comparison ------------------------------------------
    def record(self, name):
        path = self.directory / ("view-%d" % self.outputs)
        self.outputs += 1
        return path, ("!(case (pinsctx:view) (((Error $call $why) (fs:update-text %s \"replace\" %s \"\" \"\")) "
                      "($text (fs:update-text %s \"replace\" $text \"\" \"\"))))" % (q(path), q(ERROR), q(path)))

    def compare_sequences(self, scenarios, **env):
        """scenarios: lists of (content, repeats). Returns (expected, actual) lists:
        the oracle's text without its marker, and the native view's text."""
        oracle = Oracle(self.pins)
        expected, lines, paths = [], list(HEADER), []
        lines += ["!(import! &self ./src/pins_context_cetta.metta)", "!(py-call (pins_context_probe.forbid))"]
        for scenario in scenarios:
            helper._change_marks.clear()
            with oracle.environment(**env):
                for content, repeats in scenario:
                    put(self.pins, content)
                    lines.append("!(py-call (pins_context_probe.write %s %s))" % (
                        q(self.pins), q("missing" if content is None else content.hex())))
                    for _ in range(repeats):
                        expected.append(oracle.body())
                        path, call = self.record("view")
                        paths.append(path)
                        lines.append(call)
        stdout = self.run_guest(lines, **env)
        actual = [path.read_bytes().decode("utf-8") for path in paths]
        return expected, actual, stdout

    def assert_equal_lists(self, expected, actual, labels):
        self.assertEqual(len(expected), len(actual))
        for index, (want, got) in enumerate(zip(expected, actual)):
            with self.subTest(case=labels[index % len(labels)] if labels else index):
                self.assertEqual(got, want)

    def test_every_fixture_matches_the_oracle(self):
        cases = fixtures(random.Random(102401))
        expected, actual, _ = self.compare_sequences([[(content, 1)] for _, content in cases])
        self.assert_equal_lists(expected, actual, [name for name, _ in cases])
        # Not only the empty answers: the fixtures reach the full rendering.
        self.assertGreater(sum("PINNED newest" in text for text in expected), 120)
        self.assertGreater(sum(text == "(no pins)" for text in expected), 5)
        self.assertTrue(any("…" in text for text in expected))
        self.assertEqual([text for text in expected if text == ERROR], [])
        print("Compared %d fixture views" % len(cases))

    def test_unicode_whitespace_and_line_rules_match_python_exhaustively(self):
        # Every Unicode scalar on a line of its own, in slices small enough
        # that the 12-line window never hides which lines were kept: the
        # header's "of N" counts the kept lines, and the text shows the tail.
        scalars = [chr(c) for c in range(0x110000) if not 0xD800 <= c <= 0xDFFF and c not in (10, 13)]
        slices = [scalars[i:i + 997] for i in range(0, len(scalars), 997)]
        cases = [[(("\n".join(part) + "\n").encode("utf-8"), 1)] for part in slices]
        expected, actual, _ = self.compare_sequences(cases)
        self.assert_equal_lists(expected, actual, None)
        kept = sum(int(text.split(" of ")[1].split(";")[0]) for text in expected if "PINNED" in text)
        self.assertEqual(kept, sum(1 for c in scalars if c.strip()))

    def test_malformed_utf8_is_an_error_where_the_oracle_raises(self):
        expected, actual, _ = self.compare_sequences([[(content, 1)] for _, content in MALFORMED])
        self.assertEqual(expected, [ERROR] * len(MALFORMED))
        self.assertEqual(actual, expected)

    def test_unreadable_paths_are_no_pins_exactly_when_the_oracle_says_so(self):
        directory = self.directory / "a-directory"
        directory.mkdir()
        denied = self.directory / "denied"
        denied.write_text("[x] secret\n")
        denied.chmod(0)
        plain = self.directory / "plain"
        plain.write_text("x")
        loop = self.directory / "loop"
        loop.symlink_to("loop")
        dangling = self.directory / "dangling"
        dangling.symlink_to(self.directory / "nowhere")
        linked = self.directory / "linked"
        linked.write_text("[x] through a link\n")
        link = self.directory / "link"
        link.symlink_to(linked)
        settings = [str(directory), str(denied), str(plain / "below"), str(loop), str(dangling), "",
                    str(self.directory / "missing" / "deeper"), str(link), str(self.directory / "space dir/λ pins.txt")]
        for path in settings:
            with self.subTest(path=path):
                put(self.pins, None)
                expected, actual, _ = self.compare_sequences([[(None, 2)]], METTACLAW_PINS_PATH=path)
                self.assertEqual(actual, expected)
        denied.chmod(0o600)
        self.assertEqual(os.geteuid() == 0, False, "permission checks are meaningless as root")

    def test_path_defaults_and_spellings(self):
        (self.directory / "sub").mkdir()
        (self.directory / "sub/pins.txt").write_text("[x] in sub\n")
        (self.directory / "pins.txt").write_text("[x] beside\n")
        (self.directory / "memory").mkdir()
        (self.directory / "memory/pins.txt").write_text("[x] default memory\n")
        (self.directory / "other").mkdir()
        (self.directory / "other/pins.txt").write_text("[x] other\n")
        cases = [
            {"METTACLAW_PINS_PATH": None, "METTACLAW_HISTORY_PATH": str(self.directory / "sub/history.metta")},
            {"METTACLAW_PINS_PATH": None, "METTACLAW_HISTORY_PATH": "history.metta"},
            {"METTACLAW_PINS_PATH": None, "METTACLAW_HISTORY_PATH": "sub/history.metta"},
            {"METTACLAW_PINS_PATH": None, "METTACLAW_HISTORY_PATH": "./sub/history.metta"},
            {"METTACLAW_PINS_PATH": None, "METTACLAW_HISTORY_PATH": "sub/"},
            {"METTACLAW_PINS_PATH": None, "METTACLAW_HISTORY_PATH": "sub//history.metta"},
            {"METTACLAW_PINS_PATH": None, "METTACLAW_HISTORY_PATH": "/"},
            {"METTACLAW_PINS_PATH": None, "METTACLAW_HISTORY_PATH": ""},
            {"METTACLAW_PINS_PATH": None, "METTACLAW_HISTORY_PATH": None},
            {"METTACLAW_PINS_PATH": None, "METTACLAW_HISTORY_PATH": "True"},
            {"METTACLAW_PINS_PATH": "", "METTACLAW_HISTORY_PATH": "sub/history.metta"},
            {"METTACLAW_PINS_PATH": "other/pins.txt", "METTACLAW_HISTORY_PATH": "sub/history.metta"},
            {"METTACLAW_PINS_PATH": str(self.directory / "other/../sub/pins.txt")},
            {"METTACLAW_PINS_PATH": "True"},
            {"METTACLAW_PINS_PATH": "~/pins.txt"},
            {"METTACLAW_PINS_PATH": "other/pins.txt\n"},
        ]
        for settings in cases:
            with self.subTest(settings=settings):
                # The file is whatever the default lookup finds under cwd=directory.
                oracle_env = {"METTACLAW_PINS_PATH": None}
                oracle_env.update(settings)
                with contextlib.ExitStack() as stack:
                    stack.enter_context(mock.patch.dict(os.environ))
                    for name, value in oracle_env.items():
                        if value is None:
                            os.environ.pop(name, None)
                        else:
                            os.environ[name] = value
                    cwd = os.getcwd()
                    os.chdir(self.directory)
                    stack.callback(os.chdir, cwd)
                    helper._change_marks.clear()
                    want = Oracle(self.pins).body()
                output = self.directory / "path-view"
                output.unlink(missing_ok=True)
                self.run_guest(HEADER + [
                    "!(import! &self ./src/pins_context_cetta.metta)", "!(py-call (pins_context_probe.forbid))",
                    "!(case (pinsctx:view) (((Error $call $why) (fs:update-text %s \"replace\" %s \"\" \"\")) "
                    "($text (fs:update-text %s \"replace\" $text \"\" \"\"))))" % (q(output), q(ERROR), q(output))],
                    cwd=self.directory, **settings)
                self.assertEqual(output.read_text(encoding="utf-8"), want)

    # ---- the caller and the real bundle ----------------------------------
    CALLER = ["!(import! &self ./src/turn_additions)",
              "(= (tg:client-metta?) False)"]

    def bundle_run(self, steps, engine="cetta", swi=False, forbid=True, prelude=(), write=True, **env):
        """Run addition-context-sources once per step; return the logged bundles."""
        log = self.directory / "bundles.jsonl"
        log.unlink(missing_ok=True)
        lines = list(HEADER) + list(self.CALLER) + list(prelude)
        lines.append("!(py-call (pins_context_probe.record_bundle %s))" % q(log))
        if forbid:
            # Warm the imports the bundle makes, then switch the Python readers off.
            lines += ["!(py-call (pins_context_probe.pins_loaders))", "!(py-call (pins_context_probe.forbid))"]
        for content in steps:
            if write:
                lines.append("!(py-call (pins_context_probe.write %s %s))" % (
                    q(self.pins), q("missing" if content is None else content.hex())))
            lines.append("!(addition-context-sources)")
        self.run_guest(lines, engine, swi, **env)
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    def oracle_bundles(self, steps):
        helper._change_marks.clear()
        projector = context_sources.Projector()
        out = []
        with Oracle(self.pins).environment():
            for content in steps:
                put(self.pins, content)
                sources = [(spec, loader) for spec, loader in context_sources._loaders()
                           if spec.source_id == "pins"]
                out.append(context_sources.layout(projector.project(sources)))
        return out

    SEQUENCE = [None, b"", pin_lines("[10-24 09:00] one λ"), pin_lines("[10-24 09:00] one λ"),
                pin_lines("[10-24 09:00] one λ"), pin_lines("[10-24 09:00] one λ", "[10-24 09:01] two"),
                b"\n \n", pin_lines("[10-24 09:00] one λ", "[10-24 09:01] two"),
                pin_lines("[10-24 09:00] one λ", "[10-24 09:01] two"),
                pin_lines(*["[10-24 09:%02d] pin %d %s" % (i, i, "x" * 450) for i in range(15)]),
                pin_lines(*["[10-24 09:%02d] pin %d %s" % (i, i, "x" * 450) for i in range(15)]),
                None, pin_lines("again")]

    def test_the_live_context_addition_reaches_the_bundle_natively(self):
        expected = self.oracle_bundles(self.SEQUENCE)
        logged = self.bundle_run(self.SEQUENCE)
        self.assertEqual([entry["text"] for entry in logged], expected)
        # The Python reader never ran: the texts above are the native ones.
        self.assertEqual([entry["python_reads"] for entry in logged], [0] * len(self.SEQUENCE))
        self.assertTrue(any("(unchanged for 2 turns)" in text for text in expected))
        self.assertTrue(any("status=known" in text and "(no pins)" in text for text in expected))

    def test_the_bundle_text_is_the_python_loaders_text_when_both_run(self):
        expected = self.oracle_bundles(self.SEQUENCE)
        logged = self.bundle_run(self.SEQUENCE, forbid=False, prelude=[
            # The oracle answers on this process only; the guest must not
            # need it, which the previous test proves.
        ])
        self.assertEqual([entry["text"] for entry in logged], expected)

    # ---- one marker history across native and fallback turns --------------
    def mixed_run(self, scenarios):
        """scenarios: lists of (content, backend), backend "native" or "fallback".

        A fallback step takes the native view away for that one turn (the
        equation is removed and put back), so the bundle's own Python loader
        answers; the Python reader is only counted, never blocked. Returns the
        oracle's bundles (one Python-only history per scenario), the logged
        bundles, and the expected cumulative Python reader counts."""
        expected, reads, total = [], [], 0
        lines = list(HEADER) + list(self.CALLER) + [
            "!(bind! &pinsctx-saved (new-state ()))",
            "!(match &self (= (pinsctx:context-views) $body) (change-state! &pinsctx-saved $body))",
            "!(py-call (pins_context_probe.record_bundle %s))" % q(self.directory / "mixed.jsonl"),
            "!(py-call (pins_context_probe.pins_loaders))", "!(py-call (pins_context_probe.spy))"]
        (self.directory / "mixed.jsonl").unlink(missing_ok=True)
        for scenario in scenarios:
            expected += self.oracle_bundles([content for content, _ in scenario])
            lines.append("!(py-call (pins_context_probe.reset_state))")
            for content, backend in scenario:
                lines.append("!(py-call (pins_context_probe.write %s %s))" % (
                    q(self.pins), q("missing" if content is None else content.hex())))
                if backend == "fallback":
                    lines.append("!(let $b (get-state &pinsctx-saved) (remove-atom &self (= (pinsctx:context-views) $b)))")
                lines.append("!(addition-context-sources)")
                if backend == "fallback":
                    lines.append("!(let $b (get-state &pinsctx-saved) (add-atom &self (= (pinsctx:context-views) $b)))")
                malformed = content is not None and not self.decodes(content)
                total += 1 if backend == "fallback" or malformed else 0
                reads.append(total)
        self.run_guest(lines)
        logged = [json.loads(line) for line in (self.directory / "mixed.jsonl").read_text(encoding="utf-8").splitlines()]
        return expected, logged, reads

    @staticmethod
    def decodes(content):
        try:
            content.decode("utf-8")
            return True
        except UnicodeDecodeError:
            return False

    A = pin_lines("[10-24 09:00] one λ")
    B = pin_lines("[10-24 09:00] one λ", "[10-24 09:01] two")

    def test_native_failure_with_python_fallback_then_native_keeps_one_streak(self):
        # The reviewed failure: native, a transient native failure answered by
        # the Python loader, native again. One streak runs through all three.
        scenario = [(self.A, "native"), (self.A, "native"), (self.A, "fallback"), (self.A, "native"),
                    (self.A, "native")]
        expected, logged, reads = self.mixed_run([scenario])
        texts = [entry["text"] for entry in logged]
        self.assertEqual(texts, expected)
        marks = [re.search(r"\n( \(.*?\))? PINNED", text).group(1) for text in texts]
        self.assertEqual(marks, [None, " (unchanged for 1 turn)", " (unchanged for 2 turns)",
                                 " (unchanged for 3 turns)", " (unchanged for 4 turns)"])
        self.assertEqual([entry["python_reads"] for entry in logged], reads)
        self.assertEqual(reads, [0, 0, 1, 1, 1])

    def test_the_reviewers_cross_route_sequence_native_fallback_native(self):
        """The reviewer's production-engine counterexample, same pin text and
        sequence: native, Python fallback, native again, must read
        [fresh, unchanged 1, unchanged 2] (the first submission read
        [fresh, fresh, unchanged 1]). Run twice: through the real boundary
        (addition-context-sources) and as the function-level three-call
        driver, whose native route is the native body marked by its single
        owner, pins.mark_context_body."""
        content = b"[01-01 00:00] inspect actual evidence\n"
        body = "PINNED newest 1 of 1; (pins) reads the full retained list\n[01-01 00:00] inspect actual evidence"
        want = [" " + body, " (unchanged for 1 turn) " + body, " (unchanged for 2 turns) " + body]
        put(self.pins, content)
        helper._change_marks.clear()
        with Oracle(self.pins).environment():
            self.assertEqual([pins.context_view() for _ in range(3)], want)

        # 1. Through the real boundary, with the native view taken away for turn 2.
        expected, logged, reads = self.mixed_run([[(content, "native"), (content, "fallback"), (content, "native")]])
        self.assertEqual([entry["text"] for entry in logged], expected)
        self.assertEqual([re.search(r"SOURCE\[pins\][^\n]*\n(.*)\Z", entry["text"], re.S).group(1)
                          for entry in logged], want)
        self.assertEqual(reads, [0, 1, 1])
        self.assertEqual([entry["python_reads"] for entry in logged], reads)

        # 2. The function-level driver on the revised module.
        record = self.directory / "cross-route.jsonl"
        record.unlink(missing_ok=True)
        self.run_guest(HEADER + [
            "!(import! &self ./src/pins_context_cetta.metta)", "!(import! &self ./src/pins.py)",
            "!(py-call (pins_context_probe.reset_state))",
            '!(py-call (pins_context_probe.log %s "native" (py-call (pins.mark_context_body (pinsctx:view)))))' % q(record),
            '!(py-call (pins_context_probe.log %s "fallback" (py-call (pins.context_view))))' % q(record),
            '!(py-call (pins_context_probe.log %s "native-again" (py-call (pins.mark_context_body (pinsctx:view)))))' % q(record)])
        actual = [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([entry["label"] for entry in actual], ["native", "fallback", "native-again"])
        self.assertEqual([entry["text"] for entry in actual], want)

    def test_mixed_backend_sequences_equal_the_python_only_oracle(self):
        a, b = self.A, self.B
        spaced = pin_lines("[10-24 09:00] one λ ", "[10-24 09:01] two")
        scenarios = [
            [(a, "native"), (a, "fallback"), (a, "native")],
            [(a, "fallback"), (a, "native"), (a, "fallback"), (a, "native")],
            [(a, "native"), (b, "fallback"), (b, "native"), (a, "fallback"), (a, "native")],   # changes
            [(a, "native"), (b"", "fallback"), (a, "native"), (b"", "native"), (a, "fallback")],  # empty
            [(a, "native"), (None, "native"), (a, "fallback"), (None, "fallback"), (a, "native")],  # missing
            [(a, "native"), (b"\n \n", "fallback"), (b"\n \n", "native"), (a, "native")],
            [(a, "native"), (b"\xff\n", "native"), (a, "native"), (b"\xc0", "fallback"), (a, "fallback"),
             (a, "native")],                                                           # malformed
            [(a, "native")] * 4 + [(a, "fallback")] * 2 + [(a, "native")] * 3 + [(b, "native")] * 3 + [(a, "fallback")] * 2,
            [(spaced, "native"), (spaced, "fallback"), (a, "native"), (spaced, "native"), (spaced, "fallback")],
        ]
        rng = random.Random(20241024)
        states = [None, b"", a, a, a, b, spaced, b"\xff", pin_lines("only"), pin_lines("[y] λ" * 3)]
        for _ in range(60):
            scenarios.append([(rng.choice(states), rng.choice(["native", "native", "fallback"]))
                              for _ in range(rng.randint(2, 12))])
        expected, logged, reads = self.mixed_run(scenarios)
        self.assertEqual(len(logged), len(expected))
        for index, (want, entry) in enumerate(zip(expected, logged)):
            with self.subTest(step=index):
                self.assertEqual(entry["text"], want)
        # Python was consulted for exactly the fallback and malformed turns.
        self.assertEqual([entry["python_reads"] for entry in logged], reads)
        marks = [m.group(1) for m in (re.search(r"\n( \(.*?\))? PINNED", e["text"]) for e in logged) if m]
        self.assertIn(" (changed)", marks)
        self.assertIn(" (unchanged for 1 turn)", marks)
        self.assertIn(" (unchanged for 2 turns)", marks)
        self.assertIn(" (unchanged for 3 turns)", marks)
        self.assertIn(" (unchanged for 8 turns)", marks)
        print("Compared %d mixed-backend bundles" % len(logged))

    def test_native_only_marker_sequences_equal_the_oracle_with_python_off(self):
        a, b = self.A, self.B
        scenarios = [
            [(a, "native")] * 6,
            [(a, "native"), (b, "native"), (a, "native"), (b, "native"), (b, "native")],
            [(a, "native"), (b"", "native"), (b"", "native"), (a, "native")],
            [(a, "native"), (None, "native"), (None, "native"), (a, "native")],
            [(b"", "native"), (a, "native"), (a, "native")],
            [(a, "native")] * 12,
        ]
        expected, logged, reads = self.mixed_run(scenarios)
        self.assertEqual([entry["text"] for entry in logged], expected)
        self.assertEqual([entry["python_reads"] for entry in logged], reads)
        self.assertEqual(reads, [0] * len(expected))

    def test_unreadable_pins_leave_the_source_to_its_own_loader(self):
        # Python raises on malformed text: the source is unavailable, then
        # stale-known with the last good projection. Native defers to that.
        good = pin_lines("[10-24 09:00] good")
        steps = [None, good, good, b"\xff\xfe", b"\xff\xfe", good, b"\xc0\xaf", None]
        expected = self.oracle_bundles(steps)
        logged = self.bundle_run(steps)
        self.assertEqual([entry["text"] for entry in logged], expected)
        reads = [entry["python_reads"] for entry in logged]
        # The Python reader was consulted exactly for the malformed files.
        self.assertEqual(reads, [0, 0, 0, 1, 2, 2, 3, 3])
        self.assertTrue(any("status=stale-known" in text for text in expected))
        self.assertTrue(any("status=unavailable" in text for text in
                            self.oracle_bundles([b"\xff"])))
        # An unreadable first observation is unavailable, not a valid empty one.
        first = self.bundle_run([b"\xff"])
        self.assertIn("status=unavailable", first[0]["text"])
        self.assertNotIn("(no pins)", first[0]["text"])
        self.assertNotIn("status=absent", first[0]["text"])

    BROKEN_VIEWS = {
        "error": '(Error pinsctx:context-views "simulated")',
        "stuck case": "(case 1 ((2 never)))",
        "python failure": '(py-call (builtins.int "not a number"))',
        "empty": "()",
        "empty text": '(("pins-body" ""))',
        "blank text": '(("pins-body" "  \\n "))',
        "number": '(("pins-body" 42))',
        "final text under the old key": '(("pins" "PINNED lie"))',
        "wrong source": '(("recent-proposals" "x"))',
        "two answers": '(("pins-body" "a") ("pins-body" "b"))',
        "bare text": '"PINNED lie"',
        "unreduced": "(pinsctx:no-such-view)",
    }

    def removal(self):
        return ["!(match &self (= (pinsctx:context-views) $body) "
                "(remove-atom &self (= (pinsctx:context-views) $body)))"]

    def test_an_unavailable_or_invalid_native_view_costs_only_its_own_source(self):
        steps = [pin_lines("[10-24 09:00] real pin"), pin_lines("[10-24 09:00] real pin")]
        expected = self.oracle_bundles(steps)
        self.assertIn("real pin", expected[0])
        for name, broken in self.BROKEN_VIEWS.items():
            with self.subTest(view=name):
                # Without the native view the bundle's own loader answers
                # (no pins module-level fallback is invented): the Python
                # reader is left on and must produce the oracle's text.
                logged = self.bundle_run(steps, forbid=False, prelude=self.removal() + [
                    "(= (pinsctx:context-views) %s)" % broken])
                self.assertEqual([entry["text"] for entry in logged], expected)
                # And with the Python reader off, the source is unavailable
                # rather than empty, and nothing else breaks.
                logged = self.bundle_run(steps[:1], prelude=self.removal() + [
                    "(= (pinsctx:context-views) %s)" % broken])
                self.assertIn("status=unavailable", logged[0]["text"])
                self.assertEqual(logged[0]["python_reads"], 1)

    def test_other_sources_survive_when_the_pins_view_or_action_views_fail(self):
        # An action view that is not a list of pairs must not take the pins
        # view down with it (the bundle ignores only the bad entry).
        steps = [pin_lines("[10-24 09:00] real pin")]
        logged = self.bundle_run(steps, prelude=[
            "(= (action-window:context-views) (Error action-window:context-views simulated))"])
        self.assertIn("real pin", logged[0]["text"])
        self.assertEqual(logged[0]["python_reads"], 0)
        logged = self.bundle_run(steps, prelude=["(= (action-window:context-views) (garbage))"])
        self.assertIn("real pin", logged[0]["text"])
        self.assertEqual(logged[0]["python_reads"], 0)
        # Without memory.metta the action views are an unreduced call.
        logged = self.bundle_run(steps)
        self.assertIn("real pin", logged[0]["text"])
        self.assertEqual(logged[0]["python_reads"], 0)

    def test_the_pins_view_and_the_action_views_travel_together(self):
        history = self.directory / "history.metta"
        history.write_text('("t1" "\n" ((shell "cat src/a.py")) "\n")\n', encoding="utf-8")
        log = self.directory / "views.json"
        lines = list(HEADER) + ["!(import! &self ./src/memory)", "!(import! &self ./src/turn_additions)",
                                "!(py-call (pins_context_probe.write %s %s))" % (
                                    q(self.pins), q(pin_lines("[x] together").hex())),
                                "!(fs:update-text %s \"replace\" (repr (addition-context-views)) \"\" \"\")" % q(log)]
        self.run_guest(lines)
        views = log.read_text(encoding="utf-8")
        self.assertIn('"recent-proposals"', views)
        self.assertIn('"relevant-files"', views)
        self.assertIn('"pins-body"', views)
        self.assertIn("together", views)

    def test_engine_selection_python_and_swi_keep_the_python_view(self):
        steps = [pin_lines("[10-24 09:00] one"), pin_lines("[10-24 09:00] one"), pin_lines("[10-24 09:00] two")]
        expected = self.oracle_bundles(steps)
        for engine, swi in (("petta", False), ("petta", True)):
            with self.subTest(engine=engine, swi=swi):
                logged = self.bundle_run(steps, engine, swi, forbid=False)
                self.assertEqual([entry["text"] for entry in logged], expected)
        # And the native view is simply not asked off CeTTa.
        output = self.directory / "views"
        for swi in (False, True):
            output.unlink(missing_ok=True)
            self.run_guest(HEADER + self.CALLER + [
                "!(py-call (pins_context_probe.write %s %s))" % (q(self.pins), q(pin_lines("[x] y").hex())),
                "!(py-call (pins_context_probe.note %s (repr (collapse (addition-pins-views)))))" % q(output)],
                "petta", swi)
            self.assertEqual(output.read_text(encoding="utf-8"), "(())")

    def test_production_boot_import_order_reaches_the_native_view(self):
        """The launch's own program (cetta_bootstrap.metta, then a channel
        config), not a hand-picked list of imports: the turn's context
        addition must still be served natively, with the Python reader off."""
        library = next((path for path in (ROOT / "repos/petta_lib_chromadb",
                                          Path.home() / "repos/petta_lib_chromadb")
                        if (path / "lib_chromadb.metta").is_file()), None)
        if library is None:
            self.skipTest("petta_lib_chromadb checkout unavailable")
        repos = self.directory / "repos"
        shutil.copytree(library, repos / "petta_lib_chromadb", ignore=shutil.ignore_patterns("__pycache__"))
        channel = self.directory / "channel.metta"
        channel.write_text(subprocess.run(
            [sys.executable, str(ROOT / "scripts/render_channel_config.py"), "telegram"],
            check=True, capture_output=True, text=True).stdout, encoding="utf-8")
        steps = [pin_lines("[10-24 09:00] boot λ"), pin_lines("[10-24 09:00] boot λ"),
                 pin_lines("[10-24 09:00] boot λ", "[10-24 09:01] two"), b"\xff", None]
        expected = self.oracle_bundles(steps)
        log = self.directory / "boot.jsonl"
        probe = self.directory / "probe.metta"
        lines = ["!(import! &self %s)" % (ROOT / "tests/pins_context_probe.py"),
                 "!(py-call (pins_context_probe.record_bundle %s))" % q(log),
                 "!(py-call (pins_context_probe.pins_loaders))", "!(py-call (pins_context_probe.forbid))"]
        for content in steps:
            lines += ["!(py-call (pins_context_probe.write %s %s))" % (
                q(self.pins), q("missing" if content is None else content.hex())),
                "!(addition-context-sources)"]
        probe.write_text("\n".join(lines) + "\n", encoding="utf-8")
        env = guest_env(self.directory, "cetta", METTACLAW_SKIP_INITIALIZE="1",
                        METTACLAW_REPOS_DIR=str(repos), METTACLAW_CHROMA_DIR=str(self.directory / "chroma"),
                        OPENAI_API_KEY="test-only-unused")
        env["PYTHONPATH"] = os.pathsep.join([env["PYTHONPATH"], str(repos / "petta_lib_chromadb")])
        result = subprocess.run([str(self.binary), "--lang", "petta", "--import-mode", "ancestor-walk",
                                 str(ROOT / "cetta_bootstrap.metta"), str(channel), str(probe)],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=600,
                                stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 0, result.stdout[-4000:] + result.stderr[-4000:])
        logged = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([entry["text"] for entry in logged], expected)
        self.assertEqual([entry["python_reads"] for entry in logged], [0, 0, 0, 1, 1])

    def test_the_pins_command_and_storage_are_unchanged(self):
        # The native view only reads: pin/unpin/(pins) still go through the
        # same backends, and the stored file is byte-identical afterwards.
        content = pin_lines("[10-24 09:00] keep", "[10-24 09:01] me λ")
        self.pins.write_bytes(content)
        before = self.pins.stat()
        logged = self.bundle_run([None] * 3, forbid=True, write=False)
        self.assertEqual(len(logged), 3)
        self.assertIn("(unchanged for 2 turns)", logged[2]["text"])
        self.assertEqual(self.pins.read_bytes(), content)
        self.assertEqual(sorted(p.name for p in self.directory.iterdir() if p.name.startswith("pins")), ["pins.txt"])
        self.assertEqual(before.st_mtime_ns, self.pins.stat().st_mtime_ns)


class SuppliedViewsTest(unittest.TestCase):
    """context_sources' side of the contract, in this process."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.pins = Path(self.directory.name) / "pins.txt"
        patcher = mock.patch.dict(os.environ, {
            "METTACLAW_PINS_PATH": str(self.pins),
            "METTACLAW_HISTORY_PATH": str(Path(self.directory.name) / "history.metta"),
            "METTACLAW_TELEGRAM_LOG_PATH": str(Path(self.directory.name) / "updates.jsonl")})
        patcher.start()
        self.addCleanup(patcher.stop)
        helper._change_marks.clear()
        self.addCleanup(helper._change_marks.clear)

    def render(self, views):
        loaders = [(spec, loader) for spec, loader in context_sources._loaders(None, views)
                   if spec.source_id == "pins"]
        return context_sources.Projector().render(loaders)

    BODY = "PINNED newest 1 of 1; (pins) reads the full retained list\n[x] on disk"

    def test_a_supplied_body_replaces_the_loader_and_is_marked_here(self):
        self.pins.write_text("[x] on disk\n")
        with mock.patch.object(pins, "context_view", side_effect=AssertionError("loader used")):
            first = self.render([["pins-body", self.BODY]])
            second = self.render([["pins-body", self.BODY]])
        self.assertIn("SOURCE[pins] status=known", first)
        self.assertTrue(first.endswith("\n " + self.BODY))            # first sight: "" + " "
        self.assertTrue(second.endswith("\n (unchanged for 1 turn) " + self.BODY))

    def test_a_supplied_body_and_the_python_loader_share_one_history(self):
        self.pins.write_text("[x] on disk\n")
        order = ["body", "loader", "body", "loader", "loader", "body"]
        texts = []
        for kind in order:
            texts.append(self.render([["pins-body", self.BODY]] if kind == "body" else None))
        self.assertEqual([t.split("\n", 1)[1].split(" PINNED")[0] for t in texts],
                         ["", " (unchanged for 1 turn)", " (unchanged for 2 turns)", " (unchanged for 3 turns)",
                          " (unchanged for 4 turns)", " (unchanged for 5 turns)"])
        # The same history as the loader alone gives.
        helper._change_marks.clear()
        self.assertEqual(texts, [self.render(None) for _ in order])

    def test_no_pins_is_never_marked_and_leaves_the_streak(self):
        self.pins.write_text("[x] on disk\n")
        self.render([["pins-body", self.BODY]])
        none = self.render([["pins-body", pins.NO_PINS]])
        self.assertTrue(none.endswith("\n" + pins.NO_PINS))
        self.assertIn("(unchanged for 1 turn)", self.render([["pins-body", self.BODY]]))

    def test_the_old_final_text_key_is_not_used(self):
        self.pins.write_text("[x] on disk\n")
        text = self.render([["pins", "SUPPLIED LIE"]])
        self.assertNotIn("SUPPLIED LIE", text)
        self.assertIn("on disk", text)

    def test_without_a_supplied_body_the_loader_answers_as_before(self):
        self.pins.write_text("[x] on disk\n")
        for views in (None, [], [["recent-proposals", "x"]], "garbage", 7, [["pins-body"]], [["pins-body", None]],
                      [["pins-body", 5]], [[7, "x"]], ["pins-body"], [("pins-body", "")], [("pins-body", " \n\t ")]):
            with self.subTest(views=views):
                helper._change_marks.clear()
                self.assertIn("on disk", self.render(views))

    def test_a_blank_text_is_never_a_valid_empty_observation(self):
        self.pins.write_text("[x] on disk\n")
        for blank in ("", " ", "\n", "\u3000\u00a0"):
            helper._change_marks.clear()
            text = self.render([["pins-body", blank]])
            self.assertNotIn("status=absent", text)
            self.assertIn("on disk", text)

    def test_one_bad_entry_does_not_discard_the_valid_ones(self):
        supplied = context_sources._supplied(
            ["garbage", ["pins-body", "p"], ["x"], ["recent-proposals", None], ["relevant-files", "f"], 3])
        self.assertEqual(supplied, {"pins-body": "p", "relevant-files": "f"})
        for bad in ("garbage", 7, None, [], ["Error", "action-window:context-views", "simulated"]):
            self.assertEqual(context_sources._supplied(bad), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
