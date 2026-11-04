"""The console's event flow, status rows and terminal behaviour, offline.

Events come from tests/console_fixture.py (recorded event shapes, synthetic content);
the PTY test runs the real console against that fixture's local control and mail
stand-ins, so no agent, mail service or provider is involved."""
import fcntl
import json
import os
from pathlib import Path
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "tests")]
import console_fixture as fixture
from ui.worklog.inlineview.adapter import EventBlocks
from ui.worklog.inlineview.render import FAILED, OK, PROPOSED, UNCERTAIN, Renderer, classify

BOX = re.compile("[─-╿▀-▟]")
SGR = re.compile(r"\x1b\[[0-9;]*m")


def events(script=fixture.SCRIPT, producer="p1", start=1000.0):
    out = []
    for sequence, (delay, event) in enumerate(script, 1):
        start += delay
        record = dict(event, producer=producer, sequence=sequence, time=start, epoch="initial", instance="godel")
        record.setdefault("parent_session", "" if record["session"] == "godel" else "godel")
        out.append(record)
    return out


class Clock:
    def __init__(self, now):
        self.value = now

    def __call__(self):
        return self.value


class EventFlow(unittest.TestCase):
    def render(self, width=110, color=False):
        stream = events()
        clock = Clock(stream[-1]["time"] + 2)
        blocks = EventBlocks("godel", lambda: width, now=clock, color=color)
        return blocks, blocks.feed(stream), clock

    def test_blocks_are_copyable_lines_without_status_noise(self):
        _, text, _ = self.render()
        lines = text.split("\n")
        self.assertEqual([l for l in lines if l != l.rstrip()], [], "no trailing spaces")
        self.assertIsNone(BOX.search(text), "no box drawing around content")
        # State changes live in the status row, not between blocks.
        for noise in ("↳", " ready", "· since", "proposed", "Exec_command", "Reasoning summary"):
            self.assertNotIn(noise, text)
        # A shell command is its own line, verbatim (multi-line commands keep their lines).
        self.assertIn("  " + fixture.GREP, lines)
        for row in fixture.HEREDOC.split("\n"):
            self.assertIn("  " + row, lines)
        # The complete log path, never shortened.
        self.assertIn("    output: /srv/agent/memory/coding/%s/executions/x1.log" % fixture.WORK, lines)

    def test_outcomes_and_attribution(self):
        _, text, _ = self.render()
        lines = text.split("\n")
        self.assertEqual(text.count("I'll start by reading the oracle, its tests and the db fixture."), 1)
        self.assertEqual(text.count("I'm starting a coding session in the ledger worktree for task 1."), 1)
        self.assertTrue(any(l.startswith("✓ (rest)") for l in lines), "a returned MeTTa command is a success")
        self.assertTrue(any(l.startswith("✗ shell · exit 1") for l in lines))
        self.assertTrue(any(l.startswith("✓ patch · applied") for l in lines))
        sessions = [l.split(" · ")[0] for l in lines if l.startswith("◆ ")]
        self.assertEqual(sessions, ["◆ godel", "◆ " + fixture.WORK, "◆ godel", "◆ " + fixture.WORK])
        self.assertIn("started by godel", next(l for l in lines if l.startswith("◆ " + fixture.WORK)))

    def test_long_command_is_one_unfolded_line(self):
        renderer = Renderer(width=40)
        command = "cd /srv/work/a-rather-long-directory-name && grep -rn pattern src | head -20"
        out = renderer.tool("tool.finished", "exec_command", command, 1, OK, "exited", "0.1s", 0, "", "/a/b/" + "c" * 60)
        self.assertIn("  " + command, out.split("\n"))
        self.assertIn("    output: /a/b/" + "c" * 60, out.split("\n"))

    def test_classification(self):
        self.assertEqual(classify("returned", True, result="(RESTING budget-banked)"), OK)
        self.assertEqual(classify("returned", True, result='(Error command_failed "x")'), FAILED)
        for word in ("timed-out", "signaled", "not_started", "exception", "no_result", "rejected"):
            self.assertEqual(classify(word, True, 0), FAILED, word)
        self.assertEqual(classify("withheld", True), UNCERTAIN)
        self.assertEqual(classify("proposed", True), PROPOSED)
        self.assertEqual(classify("exited", True, 0), OK)
        self.assertEqual(classify("exited", True, 2), FAILED)

    def test_colour_is_sgr_only(self):
        _, text, _ = self.render(color=True)
        self.assertIn("\x1b[", text)
        self.assertEqual(SGR.sub("", text).count("\x1b"), 0)
        hostile = EventBlocks("godel", lambda: 80, color=True).feed(events([(0, dict(
            kind="tool.finished", session="godel", item="z", command="['shell', 'x']", status="returned",
            source="executor", text="\x1b]52;c;cGFzdGU=\x07\x1b[2J\x1b[31mred\x9b6n done"))]))
        self.assertEqual(SGR.sub("", hostile).count("\x1b"), 0)
        self.assertNotIn("\x9b", hostile)
        self.assertNotIn("52;c", hostile)

    def test_status_and_activity_rows(self):
        blocks, _, clock = self.render(width=140)
        facts = {"model": {"name": "fixture-model-large", "reasoningEffort": "high"}, "humanInbox": {"pending": 2},
                 "call": {"phase": "idle"}}
        status = "".join(t for _, t in blocks.status(facts))
        self.assertTrue(status.startswith("godel · fixture-model-large high · resting "), status)
        self.assertIn(fixture.WORK + " running", status)
        self.assertIn("queue 2", status)
        self.assertIn("last call ", status)
        activity = "".join(t for _, t in blocks.activity())
        self.assertIn(fixture.WORK + " running cd /srv/work/ledger && python3 -m unittest", activity)
        self.assertEqual("".join(t for _, t in blocks.activity("menu open · type 1-3 to choose")),
                         "menu open · type 1-3 to choose")
        backoff = dict(facts, call={"phase": "backoff", "retrySeconds": 30, "reason": "rate-limit"})
        self.assertIn("provider backoff · retry in 30s (rate-limit)", "".join(t for _, t in blocks.status(backoff)))
        # Narrow terminals shorten the work id first, then drop the least important facts.
        narrow = EventBlocks("godel", lambda: 64, now=clock)
        narrow.feed(events())
        row = "".join(t for _, t in narrow.status(facts))
        self.assertLessEqual(len(row), 63)
        self.assertIn("work-…-a6fe running", row)
        self.assertTrue(row.startswith("godel · "))

    def test_idle_rows_and_attach_priming(self):
        stream = events()
        done = dict(kind="tool.finished", session=fixture.WORK, item="x3", status="exited", exit_code=0,
                    source="executor", text="OK\n")
        stream += events([(1, done)], start=stream[-1]["time"])[:1]
        stream[-1]["sequence"] = len(stream)
        blocks = EventBlocks("godel", lambda: 110, now=Clock(stream[-1]["time"] + 5))
        blocks.prime(stream)
        self.assertEqual(blocks.feed([]), "", "history absorbed at attachment is not printed")
        self.assertIn("Enter sends", "".join(t for _, t in blocks.activity()))
        status = "".join(t for _, t in blocks.status())
        self.assertIn("resting", status)
        self.assertIn(fixture.WORK + " ready", status)

    def test_operator_text_is_verbatim(self):
        blocks = EventBlocks("godel", lambda: 30)
        typed = blocks.user_input("first line that is longer than thirty cells\n  indented second")
        self.assertEqual(typed, "› first line that is longer than thirty cells\n    indented second\n")
        reply = blocks.reply("godel", "Run: cd /srv/work/ledger && make test\n\nDone.")
        self.assertIn("\nRun: cd /srv/work/ledger && make test\n\nDone.\n", reply)
        self.assertTrue(reply.startswith("godel → you · "))


class ResizeReflow(unittest.TestCase):
    def test_rows_added_above_the_cursor_when_narrowed(self):
        import runpy
        from types import SimpleNamespace
        from prompt_toolkit.data_structures import Point, Size
        from prompt_toolkit.layout.screen import Char, Screen
        reflowed_rows = runpy.run_path(str(ROOT / "ui/worklog/console.py"), run_name="fixture")["reflowed_rows"]
        screen = Screen()
        for row, text in enumerate(["─" * 109, "⠹ running " + "x" * 90, "› draft"]):
            for column, char in enumerate(text):
                screen.data_buffer[row][column] = Char(char)
        renderer = SimpleNamespace(_last_screen=screen, _last_size=Size(30, 110), _cursor_pos=Point(x=7, y=2))
        self.assertEqual(reflowed_rows(renderer, Size(30, 80)), 2)     # rule and activity row each split once
        self.assertEqual(reflowed_rows(renderer, Size(30, 50)), 3)     # 109 cells: +2 rows; 100 cells: +1
        self.assertEqual(reflowed_rows(renderer, Size(30, 120)), 0)    # widening re-wraps nothing of ours
        renderer._cursor_pos = Point(x=90, y=2)
        self.assertEqual(reflowed_rows(renderer, Size(30, 80)), 3)     # the cursor's own row split before it


class ConsoleScreen(unittest.TestCase):
    """The real console in a pseudo-terminal, against the fixture's control and mail stand-ins."""

    def test_editor_rows_input_echo_and_no_lost_output(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = fixture.build(Path(folder))
            rows, cols = 30, 110
            master, slave = pty.openpty()
            fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            environment = dict(os.environ, PATH=os.path.dirname(sys.executable) + os.pathsep + "/usr/bin:/bin",
                               LANG="C.UTF-8", LC_ALL="C.UTF-8", TERM="xterm-256color")
            environment.pop("NO_COLOR", None)
            process = subprocess.Popen(fixture.console_args(paths), cwd=ROOT, env=environment, stdin=slave,
                                       stdout=slave, stderr=slave, start_new_session=True)
            os.close(slave)
            transcript = bytearray()

            def pump(until, seconds=20):
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    if until():
                        return True
                    if select.select([master], [], [], .05)[0]:
                        try:
                            block = os.read(master, 65536)
                        except OSError:
                            break
                        transcript.extend(block)
                        # Answer cursor-position queries: the editor sits on the last rows.
                        for _ in range(block.count(b"\x1b[6n")):
                            os.write(master, b"\x1b[%d;1R" % rows)
                    if process.poll() is not None:
                        break
                return until()

            def seen(text):
                return lambda: text in SGR.sub("", transcript.decode(errors="replace"))

            try:
                self.assertTrue(pump(seen("Activity: one work session running")), transcript[-2000:])
                feeder = threading.Thread(target=fixture.feed, args=(paths["display"],), kwargs=dict(speed=4))
                feeder.start()
                self.assertTrue(pump(seen("I'll start by reading the oracle")), transcript[-2000:])
                os.write(master, b"hello there\r")     # submitted while events keep arriving
                feeder.join(timeout=20)
                # Scrollback, and the editor rows as drawn in full after output (later updates
                # only rewrite changed cells, so they are checked in EventFlow instead).
                for text in ("› hello there", "accepted · ", "godel → you", "Noted: hello there",
                             "Running the oracle comparison tests", "godel · fixture-model-large high · "):
                    self.assertTrue(pump(seen(text)), "missing %r; screen text %r" % (
                        text, SGR.sub("", transcript.decode(errors="replace"))[-3000:]))
                plain = SGR.sub("", transcript.decode(errors="replace"))
                self.assertIn("─" * 20, plain)
                self.assertEqual(plain.count("I'll start by reading the oracle, its tests and the db fixture."), 1)
                os.write(master, b"first\x1b\rsecond\r")  # Alt-Enter starts a new line; Enter sends
                sent = Path(paths["base"]) / "outbox"
                self.assertTrue(pump(lambda: len(list(sent.glob("*.json"))) == 2))
                texts = sorted(json.loads(p.read_text())["text"] for p in sent.glob("*.json"))
                self.assertEqual(texts, ["first\nsecond", "hello there"])
                os.write(master, b"/detach\r")
                process.wait(timeout=10)
                self.assertEqual(process.returncode, 0)
                for mode in (b"\x1b[?1049h", b"\x1b[?1000h", b"\x1b[?1002h", b"\x1b[?1006h"):
                    self.assertNotIn(mode, transcript, "no alternate screen, no mouse capture")
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                os.close(master)


if __name__ == "__main__":
    unittest.main()
