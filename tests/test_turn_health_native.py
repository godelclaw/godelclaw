"""The native turn-health record (src/turn_health_cetta.metta) against Python's.

Both writers keep one cognitive-health.json: the native owner for the loop and
the native provider transport, cognitive_health for the transports and mode
hosts still in Python. The same events, at the same times and with the same
episode identities, must leave byte-identical files. Set CETTA_BIN to a
Python-enabled cetta.
"""
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

import cognitive_health

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Each case: an initial record (or None) and its events, each with its time.
CASES = {
    "expect-new": (None, [("expect", "GodelClaw", 7, 100)]),
    "expect-keeps-obligation": (None, [("expect", "godel", 7, 100), ("expect", "lila", 3, 200)]),
    "legacy-pending": ({"pending_since": 50.5, "expected_count": 4}, [("expect", "godel", 0, 100)]),
    "negative-budget": (None, [("expect", "godel", -5, 100)]),
    "full-success": (None, [
        ("expect", "godel", 7, 100), ("started", "Anthropic", "claude-x", 101),
        ("progress", 10, 5, True, 900000, 102), ("recording", 2), ("completed", 37, 103),
        ("settled", 104)]),
    "failures": (None, [
        ("expect", "godel", 7, 100), ("started", "anthropic", "claude-x", 101),
        ("failed", "ReadTimeout", 102, 15, "e1"), ("failed", "ReadTimeout", 103, 30.5, "e2"),
        ("started", "anthropic", "claude-x", 104), ("failed", "provider-rejected-429", 105, 300, "e3"),
        ("started", "anthropic", "claude-x", 106), ("failed", "provider-rejected-429", 107, 300, "e4"),
        ("completed", 64, 108), ("settled", 109), ("settled", 110)]),
    "episode-other-model": (None, [
        ("started", "kimi", "k2", 100), ("failed", "transport-error", 101, 15, "e1"),
        ("started", "kimi", "k3", 102), ("failed", "transport-error", 103, 15, "e2")]),
    "repair-cleared": ({"provider_context_repair": {"code": "x"}, "completed_count": 2,
                        "failure_episode": {"id": "old", "count": 3, "provider": "openai", "model": "m"}},
                       [("started", "openai", "m", 100), ("completed", 5, 101)]),
    "labels": (None, [("started", "  Open AI!! ", 42, 100), ("failed", "", 101, -3, "e1"),
                      ("failed", "x" * 80, 102, 0, "e2")]),
    "local-failure": (None, [("failed", "strategy-not-ready", 100, 0, "e1")]),
    "completion-without-admission": (None, [("completed", -4, 100)]),
}


def metta_string(value):
    return json.dumps(str(value), ensure_ascii=False)


def metta_number(value):
    return repr(value) if isinstance(value, float) else str(int(value))


def native_event(record, event):
    kind, args = event[0], event[1:]
    if kind == "expect":
        mode, budget, now = args
        return "(health:expected %s %s %s %s)" % (record, metta_string(mode), metta_number(budget), metta_number(now))
    if kind == "started":
        provider, model, now = args
        return "(health:started %s %s %s %s)" % (record, metta_string(provider), metta_string(model), metta_number(now))
    if kind == "progress":
        count, quiet, soft, hard, now = args
        return "(health:progressed %s %d %d %s %d %s)" % (record, count, quiet, "True" if soft else "False", hard, metta_number(now))
    if kind == "recording":
        return "(health:recording %s %d)" % (record, args[0])
    if kind == "completed":
        size, now = args
        return "(health:completed %s %d %s)" % (record, size, metta_number(now))
    if kind == "failed":
        failure, now, retry, identity = args
        return "(health:failed %s %s %s %s %s)" % (record, metta_string(failure), metta_number(now),
                                                  metta_number(retry), metta_string(identity))
    if kind == "settled":
        return "(health:settled %s %s)" % (record, metta_number(args[0]))
    raise ValueError(kind)


def python_event(event):
    kind, args = event[0], event[1:]
    if kind == "expect":
        mode, budget, now = args
        cognitive_health.expect_turn(mode, budget, now=now)
    elif kind == "started":
        provider, model, now = args
        cognitive_health.turn_started(provider, now=now, model=model)
    elif kind == "progress":
        count, quiet, soft, hard, now = args
        with mock.patch.object(cognitive_health.time, "time", return_value=float(now)):
            cognitive_health.provider_progress(count, quiet, soft, hard)
    elif kind == "recording":
        cognitive_health.result_recording(args[0])
    elif kind == "completed":
        size, now = args
        cognitive_health.turn_completed(size, now=now)
    elif kind == "failed":
        failure, now, retry, identity = args
        with mock.patch.object(cognitive_health.uuid, "uuid4", return_value=mock.Mock(hex=identity)):
            cognitive_health.turn_failed(failure, now=now, retry_after=retry)
    elif kind == "settled":
        cognitive_health.turn_settled(now=args[0])
    else:
        raise ValueError(kind)


class NativeTurnHealthRecord(unittest.TestCase):
    def setUp(self):
        self.binary = pathlib.Path(os.environ.get("CETTA_BIN", ""))
        if not self.binary.is_file():
            self.skipTest("set CETTA_BIN to a Python-enabled cetta")
        self.directory = pathlib.Path(tempfile.mkdtemp(prefix="turn-health-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.directory)], check=False))

    def native_records(self, cases):
        lines = ["!(import! &self %s)" % json.dumps(str(ROOT / "src/turn_health_cetta.metta"))]
        for name, (initial, events) in cases.items():
            start = self.directory / ("native-initial-" + name + ".json")
            start.write_text(json.dumps(initial) if initial is not None else "", encoding="utf-8")
            steps = ["($r0 (sf:native-object %s))" % json.dumps(str(start))]
            for index, event in enumerate(events):
                steps.append("($r%d %s)" % (index + 1, native_event("$r%d" % index, event)))
            out = self.directory / ("native-" + name + ".json")
            lines.append("!(let* (%s) (fs:write %s (sf:python-json $r%d)))"
                         % (" ".join(steps), json.dumps(str(out)), len(events)))
        program = self.directory / "program.metta"
        program.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = subprocess.run([str(self.binary), "--lang", "petta", "--import-mode", "ancestor-walk",
                                 str(program)], cwd=ROOT, capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-3000:])
        return {name: (self.directory / ("native-" + name + ".json")).read_bytes() for name in cases}

    def python_record(self, name, initial, events):
        path = self.directory / ("python-" + name + ".json")
        if initial is not None:
            path.write_text(json.dumps(initial), encoding="utf-8")
        with mock.patch.dict(os.environ, {"METTACLAW_COGNITIVE_HEALTH_PATH": str(path)}):
            for event in events:
                python_event(event)
        return path.read_bytes()

    def test_same_events_leave_the_same_bytes(self):
        native = self.native_records(CASES)
        for name, (initial, events) in CASES.items():
            with self.subTest(case=name):
                self.assertEqual(native[name].decode(), self.python_record(name, initial, events).decode())

    def test_the_writers_share_one_record(self):
        """Native and Python updates interleave on one file, each reading the other's."""
        path = self.directory / "shared.json"
        program = self.directory / "shared.metta"
        program.write_text("\n".join([
            "!(import! &self %s)" % json.dumps(str(ROOT / "src/turn_health_cetta.metta")),
            "!(import! &self %s)" % json.dumps(str(ROOT / "src/cognitive_health.py")),
            '!(health:expect godelclaw 9)',
            '!(py-call (cognitive_health.turn_started "anthropic"))',
            '!(health:progress 4096 10 False 600000)',
            '!(health:complete 512)',
            '!(println! (COMPLETIONS (health:completions)))',
            '!(health:settle)',
            '!(py-call (cognitive_health.turn_failed "transport-error"))',
            '!(health:fail "strategy-not-ready" 15)']) + "\n", encoding="utf-8")
        env = dict(os.environ, METTACLAW_COGNITIVE_HEALTH_PATH=str(path),
                   PYTHONPATH=os.pathsep.join([str(ROOT / "src"), os.environ.get("PYTHONPATH", "")]))
        result = subprocess.run([str(self.binary), "--lang", "petta", "--import-mode", "ancestor-walk",
                                 str(program)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-3000:])
        self.assertIn("(COMPLETIONS 1)", result.stdout)
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["expected_count"], 1)
        self.assertEqual(record["budget_at_start"], 9)
        self.assertEqual(record["started_count"], 1)
        self.assertEqual(record["completed_count"], 1)
        self.assertEqual(record["settled_count"], 1)
        self.assertEqual(record["last_response_bytes"], 512)
        self.assertEqual(record["hard_deadline_ms"], 600000)
        self.assertEqual(record["local_failure_count"], 2)
        self.assertEqual(record["failure_streak"], 1)
        self.assertEqual(record["last_failure_type"], "strategy-not-ready")
        self.assertEqual(record["failure_episode"]["count"], 2)
        self.assertGreaterEqual(record["retry_at"], record["last_failed_at"] + 15)
        self.assertEqual(oct(path.stat().st_mode & 0o777), oct(0o600))
        self.assertEqual(path.read_bytes(),
                         (json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n").encode())

    def run_program(self, lines, env=None):
        program = self.directory / ("program-%d.metta" % len(list(self.directory.iterdir())))
        program.write_text("\n".join(
            ["!(import! &self %s)" % json.dumps(str(ROOT / "src/turn_health_cetta.metta"))] + lines) + "\n",
            encoding="utf-8")
        result = subprocess.run([str(self.binary), "--lang", "petta", "--import-mode", "ancestor-walk",
                                 str(program)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-3000:])
        return result

    def test_pacing_is_the_same_function(self):
        """health:failure-wait against synthetic_llm.pacing over a grid of facts."""
        import synthetic_llm
        grid = [(transient, streak, retry, status, blocked, base, cap)
                for transient in (False, True) for streak in (0, 1, 5, 25)
                for retry in (-1.0, 0.0, 2.5) for status in (0, 400, 404, 408, 429, 503)
                for blocked in (False, True) for base, cap in ((15.0, 300.0), (0.0, 300.0), (7.0, 30.0))]
        lines = ['!(println! (PACED %d (health:failure-wait %s %d %s %s %s %d %s)))'
                 % (index, "True" if transient else "False", streak, repr(base), repr(cap), repr(retry),
                    status, "True" if blocked else "False")
                 for index, (transient, streak, retry, status, blocked, base, cap) in enumerate(grid)]
        native = {}
        for line in self.run_program(lines).stdout.splitlines():
            if line.startswith("(PACED "):
                index, wait, streak = line[len("(PACED "):].rstrip(")").replace("(", " ").split()
                native[int(index)] = (float(wait), int(streak))
        self.assertEqual(len(native), len(grid))
        for index, (transient, streak, retry, status, blocked, base, cap) in enumerate(grid):
            with mock.patch.dict(os.environ, {"SYNTHETIC_EMPTY_BACKOFF": repr(base),
                                              "SYNTHETIC_EMPTY_BACKOFF_CAP": repr(cap)}):
                wait, following = synthetic_llm.pacing(transient, streak, retry, status, blocked)
            self.assertEqual(native[index], (float(wait), following), grid[index])

    def test_retry_wait_is_the_same_function(self):
        """gate:retry-wait-at against cognitive_health.retry_wait_seconds where no block is possible.

        Every writer stores retry_at as a number. A numeric string, which none
        writes, is no backoff natively (health:stamp) but a float to Python.
        """
        records = {"missing": None, "torn": "{broken", "list": "[1, 2]", "empty": "{}",
                   "null": {"retry_at": None}, "zero": {"retry_at": 0}, "past": {"retry_at": 99.5},
                   "now": {"retry_at": 100}, "tiny": {"retry_at": 100.0001},
                   "fraction": {"retry_at": 107.25}, "whole": {"retry_at": 110},
                   "far": {"retry_at": 1.0e9}, "word": {"retry_at": "soon"}, "flag": {"retry_at": True},
                   "not-a-block": {"retry_at": 104.5, "provider_rejection": None}}
        nows = (100, 100.5)
        lines = ["!(import! &self %s)" % json.dumps(str(ROOT / "src/rejection_gate_cetta.metta"))]
        for name, record in records.items():
            path = self.directory / ("retry-%s.json" % name)
            if record is not None:
                path.write_text(record if isinstance(record, str) else json.dumps(record), encoding="utf-8")
            for now in nows:
                lines.append("!(println! (WAIT %s %r (gate:retry-wait-at %s %r)))"
                             % (json.dumps(name), now, json.dumps(str(path)), now))
        native = {}
        for line in self.run_program(lines).stdout.splitlines():
            if line.startswith("(WAIT "):
                name, now, wait = line[len("(WAIT "):].rstrip(")").split()
                native[(json.loads(name), float(now))] = wait
        self.assertEqual(len(native), len(records) * len(nows))
        for name in records:
            path = self.directory / ("retry-%s.json" % name)
            for now in nows:
                with mock.patch.dict(os.environ, {"METTACLAW_COGNITIVE_HEALTH_PATH": str(path)}):
                    expected = cognitive_health.retry_wait_seconds(now)
                self.assertEqual(native[(name, float(now))], str(expected), (name, now))

    def test_a_possible_block_is_the_hosts_to_decide(self):
        """A retained block is decided by provider_rejection, and the native wait keeps its answer.

        A changed model releases the block and its backoff; an unchanged context
        holds the loop at 1 s; a requested context repair answers 0.
        """
        cases = {"released": ({"model": "no-such-model"}, None, 0),
                 "blocked": ({}, None, 1), "repair": ({}, "requested", 0)}
        for name, (context, repair, expected) in cases.items():
            with self.subTest(case=name):
                path = self.directory / ("hold-%s" % name) / "cognitive-health.json"
                path.parent.mkdir()
                block = dict(id=name, status=400, code="", message="x", context=context, reason="invalid-request")
                if repair:
                    block["repair"] = repair
                path.write_text(json.dumps({"schema": 2, "retry_at": 1.0e12, "provider_rejection": block}),
                                encoding="utf-8")
                env = {key: value for key, value in os.environ.items()
                       if key not in ("METTACLAW_INPUT_LEDGER_SOCKET", "METTACLAW_MODE_RUNTIME",
                                      "METTACLAW_MODE_STATE_ROOT")}
                env.update(METTACLAW_COGNITIVE_HEALTH_PATH=str(path),
                           PYTHONPATH=os.pathsep.join([str(ROOT / "src"), os.environ.get("PYTHONPATH", "")]))
                result = self.run_program([
                    "!(import! &self %s)" % json.dumps(str(ROOT / "src/rejection_gate_cetta.metta")),
                    "!(import! &self %s)" % json.dumps(str(ROOT / "src/native_llm_host.py")),
                    "!(println! (HOLD (gate:retry-wait)))"], env=env)
                self.assertIn("(HOLD %d)" % expected, result.stdout)
                record = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual("provider_rejection" in record, name != "released")
                self.assertEqual(record["retry_at"], 0 if name == "released" else 1.0e12)

    def test_a_paced_failure_is_recorded_and_reported(self):
        path = self.directory / "paced.json"
        env = dict(os.environ, METTACLAW_COGNITIVE_HEALTH_PATH=str(path),
                   SYNTHETIC_EMPTY_BACKOFF="7", SYNTHETIC_EMPTY_BACKOFF_CAP="20")
        result = self.run_program([
            '!(health:begin-turn)',
            '!(println! (FIRST (health:paced ("failed" "TimeoutError" 0 True -1.0 False False))))',
            '!(println! (SECOND (health:paced ("failed" "TimeoutError" 0 True -1.0 False False))))',
            '!(println! (STREAK (health:streak) (health:turn-succeeded)))',
            '!(println! (THIRD (health:paced ("failed" "provider-rejected-401" 401 False -1.0 False False))))',
            '!(println! (FOURTH (health:paced ("failed" "context-x" 0 False -1.0 True True))))',
            '!(println! (OTHER (health:paced "((nop))")))'], env=env)
        self.assertIn('(FIRST "()")', result.stdout)
        self.assertIn("(STREAK 2 0)", result.stdout)
        self.assertIn('(OTHER "((nop))")', result.stdout)
        self.assertEqual(result.stderr.splitlines(), [
            "[synthetic_llm] failed provider turn: TimeoutError; retry in 7s",
            "[synthetic_llm] failed provider turn: TimeoutError; retry in 14s",
            "[synthetic_llm] failed provider turn: provider-rejected-401; retry in 20s",
            "[context] preparation withheld: context-x; awaiting corrective input or configuration"])
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["local_failure_count"], 4)
        self.assertEqual(record["last_failure_type"], "context-x")
        self.assertEqual(record["retry_at"], record["last_failed_at"])


class RejectionHold(unittest.TestCase):
    def test_the_hold_names_the_hosts_decision(self):
        import native_llm_host
        import provider_rejection
        for blocked, repair, hold in ((False, False, "clear"), (False, True, "clear"),
                                      (True, False, "blocked"), (True, True, "repair")):
            with mock.patch.object(provider_rejection, "blocked", return_value=blocked), \
                    mock.patch.object(provider_rejection, "repair_pending", return_value=repair):
                self.assertEqual(native_llm_host.rejection_hold(), hold)


if __name__ == "__main__":
    unittest.main()
