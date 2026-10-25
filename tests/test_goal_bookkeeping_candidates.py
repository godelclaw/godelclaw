"""INACTIVE native persistence candidates; no production loop/skill activation.

Only fixtures, observation and the permitted retirement boundary are Python.
No Python serializer/writer can repair native persistence or supply its bytes.
"""
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from goal_bookkeeping_support import ROOT, engine, environment, receipt
from test_goal_bookkeeping_oracle import goal, trace

sys.path.insert(0, str(ROOT / "src"))
import goals
import helper

NAMES = ("goals.metta", "goals_kernel.json", "goals_compost.log")
HEADER = '''!(import! &self (library lib_import))
!(import! &self ./src/goal_bookkeeping.metta)
!(import! &self ./tests/goal_bookkeeping_probe.py)
!(py-call (goal_bookkeeping_probe.candidate_setup))
'''
FROZEN = '''!(match &self (= (gk:stamp) $body) (remove-atom &self (= (gk:stamp) $body)))
(= (gk:stamp) "2026-01-02T03:04:05")
'''
IO_TRACE = '''!(match &self (= (gk:write $p $t) $body) (remove-atom &self (= (gk:write $p $t) $body)))
!(match &self (= (gk:rename $s $t) $body) (remove-atom &self (= (gk:rename $s $t) $body)))
!(match &self (= (gk:append $p $t) $body) (remove-atom &self (= (gk:append $p $t) $body)))
(= (gk:write $p $t) (progn (py-call (goal_bookkeeping_probe.io_event "write" $p $t))
  (if (str:ends-with $p (system:getenv-or-default "GK_TEST_WRITE_FAILURE" "no-injected-fault"))
    (GkErr "generated write failure") (gk:io-result (catch (fs:write $p $t))))))
(= (gk:rename $s $t) (progn (py-call (goal_bookkeeping_probe.io_event "rename" $s $t))
  (if (str:ends-with $t (system:getenv-or-default "GK_TEST_RENAME_FAILURE" "no-injected-fault"))
    (GkErr "generated rename failure") (gk:io-result (catch (fs:rename $s $t))))))
(= (gk:append $p $t) (progn (py-call (goal_bookkeeping_probe.io_event "append" $p $t))
  (if (== (system:getenv-or-default "GK_TEST_APPEND_FAILURE" "0") "1")
    (GkErr "generated append failure") (gk:io-result (catch (fs:append-text $p $t))))))
'''


def encoded(value):
    return base64.b64encode(value.encode() if isinstance(value, str) else value).decode()


def snapshot(directory):
    return {name: encoded((directory / name).read_bytes()) if (directory / name).exists() else None
            for name in NAMES}


def restore(directory, files):
    for name, raw in files.items():
        if raw is None:
            (directory / name).unlink(missing_ok=True)
        else:
            (directory / name).write_bytes(base64.b64decode(raw))


def wake(value):
    return ["GoalWake", value["fresh"], value["maintenance_applied"], value["message"]]


def op(kind, *args, files=None, atom=None):
    return {"kind": kind, "args": args, "files": files or {}, "atom": atom}


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set checkpoint CETTA_BIN")
class CandidateTests(unittest.TestCase):
    def compare(self, initial, steps, prelude="", faults=None):
        faults = faults or {}
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            env = environment(directory)
            env.update(METTACLAW_ENGINE="petta", METTACLAW_ACTIVE_ENGINE="petta")
            initial = {**{name: None for name in (*NAMES, "updates.jsonl")}, **initial}
            restore(directory, initial)
            expected = []
            real_open, real_replace = open, os.replace
            def fault_open(path, mode="r", *args, **kwargs):
                if mode == "w" and str(path).endswith(faults.get("write", "never-injected")):
                    raise OSError("generated write failure")
                if mode == "a" and faults.get("append"):
                    raise OSError("generated append failure")
                return real_open(path, mode, *args, **kwargs)
            def fault_replace(source, target):
                if str(target).endswith(faults.get("rename", "never-injected")):
                    raise OSError("generated rename failure")
                return real_replace(source, target)
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(goals, "_runtime_human_wake", None), \
                 mock.patch.object(goals, "_retire_in_ledger", return_value=2), \
                 mock.patch.object(goals.time, "strftime", return_value="2026-01-02T03:04:05"), \
                 mock.patch("builtins.open", side_effect=fault_open), \
                 mock.patch.object(goals.os, "replace", side_effect=fault_replace):
                for i, step in enumerate(steps):
                    restore(directory, step["files"])
                    try:
                        if step["kind"] == "durable":
                            applied, message = goals.kernel_pass(wake_token=step["args"][0], _with_status=True)
                            result = ["GoalWake", int(applied), int(applied), message]
                        else:
                            # The actual skill decodes safe text before goals.goal_write.
                            result = {"kernel": goals.kernel_pass, "wake": goals.human_wake,
                                      "write": lambda atom: goals.goal_write(helper.decode_safe_text(atom)),
                                      "drop": goals.goal_drop}[step["kind"]](*step["args"])
                        if step["kind"] == "wake":
                            result = wake(result)
                    except OSError as exc:
                        result = ["raises", str(exc)]
                    expected.append({"label": str(i), "value": result, "files": snapshot(directory)})
            restore(directory, initial)
            for name in ("goals.metta.tmp", "goals_kernel.json.tmp"):
                (directory / name).unlink(missing_ok=True)
            program = HEADER + FROZEN + IO_TRACE + prelude
            for i, step in enumerate(steps):
                fixture = directory / ("fixture-%d.json" % i)
                fixture.write_text(json.dumps(step["files"]))
                program += '!(py-call (goal_bookkeeping_probe.apply_fixture %s))\n' % json.dumps(str(fixture))
                # MeTTa source strings do not interpret JSON's \u0000 escape.
                # Native JSON parses the test INPUT envelope; it never supplies
                # expected writer output or reformats persisted numeric values.
                args = " ".join("(json:parse %s)" % json.dumps(json.dumps(arg, ensure_ascii=False), ensure_ascii=False)
                                if isinstance(arg, str) else json.dumps(arg) for arg in step["args"])
                if step["kind"] == "write" and step["atom"]:
                    args = step["atom"]
                if step["kind"] == "durable":
                    expression = ('(let (GkStatus $applied $message) (gk:kernel-status (gv:path) (gk:default-log) %s) '
                                  '(GoalWake (if $applied 1 0) (if $applied 1 0) $message))' % args)
                else:
                    name = {"kernel": "kernel-pass", "wake": "human-wake", "write": "goal-write", "drop": "goal-drop"}[step["kind"]]
                    expression = "(gk:%s%s)" % (name, " " + args if args else "")
                # Exception effects are compared separately from OS diagnostic spelling.
                if expected[i]["value"] and isinstance(expected[i]["value"], list) and expected[i]["value"][0] == "raises":
                    expression = '(case (catch %s) (((Error $_ $why) ("raises" $why)) ($value $value)))' % expression
                program += '!(py-call (goal_bookkeeping_probe.candidate_record "%d" %s))\n' % (i, expression)
            # Fault controls are test-only and are never imported by runtime.
            with mock.patch.dict(os.environ, {"GK_TEST_WRITE_FAILURE": faults.get("write", "never-injected"),
                 "GK_TEST_RENAME_FAILURE": faults.get("rename", "never-injected"),
                 "GK_TEST_APPEND_FAILURE": "1" if faults.get("append") else "0"}):
                result = engine(directory, program)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            observations = directory / "observations.jsonl"
            self.assertTrue(observations.exists(), result.stdout + result.stderr)
            rows = [json.loads(line) for line in observations.read_text().splitlines()]
            actual = [{k: v for k, v in row.items() if k != "calls"} for row in rows]
            events = [json.loads(line) for line in (directory / "io-events.jsonl").read_text().splitlines()] \
                if (directory / "io-events.jsonl").exists() else []
            receipt("candidate-parity", {"test": self.id(), "steps": steps, "faults": faults,
                "expected": expected, "actual": actual, "events": events, "calls": rows[-1]["calls"] if rows else {},
                "scope": "inactive candidate; native fs and json:stringify-python, Python writers/ported helpers forbidden; oracle skill safe-text decode included"})
            self.assertEqual(actual, expected)
            return actual, events, rows

    def test_native_atom_writes_migration_duplicates_flags_and_validation(self):
        text = "\n".join([";; generated", goal("a"),
            "(goal legacy Old sti:0.2 lti:0.4 vibes:(care) blocked-by:none)",
            goal("a", sti="0.9"), "(goal a Ignored sti:0.1 lti:0.1 vibes:() blocked-by:none)",
            "(goal-flag a saturated)", "(goal-budget 3)"]) + "\n"
        new = '(goal b Work sti:0.8 lti:0.3 vibes:(care sense) blocked-by:none last-verified:never note:generated)'
        self.compare({"goals.metta": encoded(text)}, [
            op("write", new, atom=new), op("write", "(goal-budget 0.5)", atom="(goal-budget 0.5)"),
            op("write", goal("a", verified="2026-01-02")), op("write", "(goal-budget 0.49)"),
            op("write", "(goal-budget 10.01)"), op("write", "(goal broken)"),
            op("write", '(goal plain Work sti:0.2 lti:0.1 blocked-by:none last-verified:never note:generated)'),
            op("kernel"), op("kernel"), op("kernel")])

    def test_mood_bytes_offsets_shrink_invalid_json_unicode_wakes_and_drop(self):
        first = b'not json\n{"kind":"outbound","text":"bad \xff"}\n' + (json.dumps(
            {"kind": "outbound", "text": "日本 🙂 " + trace(".1", 5) + trace(".5", 6) + trace(".8")},
            ensure_ascii=False) + "\n").encode()
        smaller = (json.dumps({"kind": "outbound", "text": trace(".2", 6)}) + "\n").encode()
        self.compare({"goals.metta": encoded(goal("a", blocked="wait") + "\n"), "updates.jsonl": encoded(first)}, [
            op("kernel"), op("kernel"), op("wake", "batch-日本"), op("wake", "batch-日本"),
            op("kernel", files={"updates.jsonl": encoded(smaller)}),
            op("write", goal("a", verified="2026-01-02")), op("drop", "missing", "retire"),
            op("drop", "a", ""), op("drop", "a", "solved"),
            op("write", goal("again")), op("drop", "again", "solved"), op("drop", "again", "retire")])

    def test_partial_kernel_state_failure_repeat_makes_no_more_writes(self):
        initial = {"goals.metta": encoded(goal() + "\n")}
        actual, events, _ = self.compare(initial, [op("wake", "failed-batch"), op("wake", "failed-batch")],
                                          faults={"write": "goals_kernel.json.tmp"})
        self.assertEqual(actual[0]["files"], actual[1]["files"])
        self.assertEqual([event["operation"] for event in events], ["write", "rename", "write"])

    def test_durable_repeat_without_runtime_digest_does_not_write(self):
        _, events, rows = self.compare({"goals.metta": encoded(goal() + "\n")},
            [op("wake", "durable-batch"), op("durable", "durable-batch"), op("wake", "durable-batch")])
        self.assertEqual(len(events), 4)
        self.assertEqual(rows[-1]["value"], ["GoalWake", 0, 0, "goal-kernel: human wake already applied"])

    def test_per_file_atomic_rename_and_append_faults_without_replay(self):
        initial = {"goals.metta": encoded(goal(verified="2026-01-02") + "\n")}
        for faults, steps in [({"rename": "goals.metta"}, [op("kernel")]),
                              ({"rename": "goals_kernel.json"}, [op("kernel")]),
                              ({"write": "goals.metta.tmp"}, [op("kernel")]),
                              ({"append": True}, [op("drop", "alpha", "solved")]),
                              ({"rename": "goals.metta"}, [op("drop", "alpha", "solved")])]:
            with self.subTest(faults=faults, steps=steps):
                _, events, rows = self.compare(initial, steps, faults=faults)
                self.assertNotIn("retire_goal", rows[-1]["calls"])
                for event in events:
                    if event["operation"] == "rename":
                        target = Path(event["second"]).name
                        self.assertIsNotNone(event["files"][target + ".tmp"])

    def test_malformed_records_and_wrong_shape_log_messages(self):
        self.compare({"goals.metta": encoded(goal() + "\n(goal broken)\n")}, [
            op("kernel"), op("write", goal("b")), op("drop", "alpha", "retire")])
        self.compare({"goals.metta": encoded(goal() + "\n"), "updates.jsonl": encoded("[]\n")}, [op("kernel")])

    def test_100_full_native_kernel_passes_bytes_and_mood_reparse(self):
        initial = "\n".join([";; generated 100-pass fixture", "(goal-budget 0.5)",
                            goal("a", blocked="wait"), goal("b", sti="0.9", lti="0.5"),
                            goal("tiny", sti="0.00001", lti="0.0")]) + "\n"
        log = b""
        steps = []
        for index in range(100):
            log += (json.dumps({"kind": "outbound", "text": trace(".2" if index % 2 else ".8", 6 + index % 4)},
                               ensure_ascii=False) + "\n").encode()
            steps.append(op("kernel", files={"updates.jsonl": encoded(log)}))
        self.compare({"goals.metta": encoded(initial)}, steps)

    def test_actual_filesystem_failure_effects_and_native_diagnostics(self):
        for fault in ("goals.metta.tmp", "goals_kernel.json", "goals_compost.log"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                initial = (goal(verified="2026-01-02") + "\n").encode()
                (directory / "goals.metta").write_bytes(initial)
                (directory / fault).mkdir()
                expression = '(gk:goal-drop "alpha" "solved")' if fault.endswith(".log") else '(gk:kernel-pass)'
                program = HEADER + FROZEN + '!(py-call (goal_bookkeeping_probe.text "real-os-error" %s))\n' % expression
                result = engine(directory, program)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                actual = json.loads((directory / "observations.jsonl").read_text())["text"]
                goal_bytes = (directory / "goals.metta").read_bytes()
                receipt("native-os-fault", {"fault": fault, "message": actual,
                    "goal_bytes": encoded(goal_bytes), "original_goal_bytes": encoded(initial),
                    "scope": "real native OS failure effects; diagnostics documented, not faked Python text"})
                self.assertIn("failed" if fault.endswith(".log") else "error", actual)
                self.assertEqual(goal_bytes == initial, fault != "goals_kernel.json")
                self.assertTrue((directory / fault).is_dir())

    def test_drop_real_permitted_retirement_boundary_disabled_strategy(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            text = goal(verified="2026-01-02") + "\n"
            (directory / "goals.metta").write_text(text)
            program = HEADER.replace("goal_bookkeeping_probe.candidate_setup", "goal_bookkeeping_probe.disable_writers") + FROZEN
            program += '!(py-call (goal_bookkeeping_probe.candidate_record "real-retire" (gk:goal-drop "alpha" "retire")))\n'
            result = engine(directory, program)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            actual = json.loads((directory / "observations.jsonl").read_text())
            expected_compost = "2026-01-02T03:04:05 retire " + goals._fmt_goal(goals._parse(text.splitlines())[0][0]) + "\n"
            self.assertEqual(actual["value"], "goal alpha composted (retire) — remember a compost note")
            self.assertEqual((directory / "goals.metta").read_bytes(), b"(goal-budget 3)\n")
            self.assertEqual((directory / "goals_compost.log").read_bytes(), expected_compost.encode())
            self.assertFalse((directory / "strategy.sqlite3").exists())
            self.assertEqual(actual["calls"], {})
            receipt("real-retire-boundary", {"actual": actual, "scope": "real strategy_host.retire_goal, disabled strategy, no ledger created"})

    def test_middle_of_utf8_byte_offset_and_missing_log(self):
        log = "🙂\n".encode() + (json.dumps({"kind": "outbound", "text": trace(".2")}) + "\n").encode()
        state = {"streaks": {}, "log_offset": 2, "mood": {}}
        self.compare({"goals.metta": encoded(goal() + "\n"), "updates.jsonl": encoded(log),
                      "goals_kernel.json": encoded(json.dumps(state))}, [op("kernel"),
                      op("kernel", files={"updates.jsonl": None})])

    def test_ordered_streak_updates_and_unicode_keys(self):
        state = {"streaks": {"b": {"blocked": "wait", "streak": 2},
                            "gone": {"blocked": "none", "streak": 9},
                            "日本🙂": {"blocked": "wait", "streak": 1}},
                 "log_offset": 0, "mood": {d: 0.1 for d in reversed(goals.MOOD_DIMS)},
                 "last_human_wake": "previous-digest"}
        self.compare({"goals.metta": encoded(goal("日本🙂", blocked="wait") + "\n" + goal("b", blocked="wait") + "\n"),
                      "goals_kernel.json": encoded(json.dumps(state))}, [op("kernel"), op("kernel")])

    def test_unicode_nul_notes_clamps_and_safe_text_caller(self):
        unusual = goal("日本", sti="9" * 400, lti="-0.4").replace("generated 日本 🙂 é", "generated\0日本 🙂 é\u3000")
        self.compare({}, [op("write", unusual), op("kernel"),
                          op("write", goal("safe").replace("generated 日本 🙂 é", "generated_apostrophe_unicode"))])

    def test_json_record_type_messages(self):
        for value in (None, 7, 0.5, "text", True, {"kind": "outbound", "text": None}):
            with self.subTest(value=value):
                self.compare({"goals.metta": encoded(goal() + "\n"),
                              "updates.jsonl": encoded(json.dumps(value) + "\n")}, [op("kernel")])

    def test_log_size_race_is_explicit_refusal_not_shifted_data(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            original = (goal() + "\n").encode()
            (directory / "goals.metta").write_bytes(original)
            program = HEADER + '''!(match &self (= (fs:metadata $p $m) $body) (remove-atom &self (= (fs:metadata $p $m) $body)))
!(match &self (= (fs:tail-text $p $n) $body) (remove-atom &self (= (fs:tail-text $p $n) $body)))
(= (fs:metadata $p $m) (fs:info regular 10 (fs:identity "fixture" "fixture")))
(= (fs:tail-text $p $n) (fs:tail 11 True "{}\\n"))
!(py-call (goal_bookkeeping_probe.text "race" (gk:kernel-pass)))
'''
            result = engine(directory, program)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            actual = json.loads((directory / "observations.jsonl").read_text())["text"]
            self.assertEqual(actual, "goal-kernel error: log size changed during read; absolute-offset read primitive required")
            self.assertEqual((directory / "goals.metta").read_bytes(), original)
            self.assertFalse((directory / "goals_kernel.json").exists())
            receipt("log-race-gap", {"actual": actual, "scope": "fixture-only size race; explicit native refusal, NOT Python parity"})

    def test_production_entrypoints_remain_unwired(self):
        for path in (ROOT / "src/loop.metta", ROOT / "src/skills.metta"):
            self.assertNotIn("goal_bookkeeping", path.read_text())


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set checkpoint CETTA_BIN")
class CandidateGapTests(unittest.TestCase):
    # Strict expected results: these are ordinary failures on missing engine /
    # literal-input capabilities, never expectedFailure or successful parity.
    compare = CandidateTests.compare

    def test_python_nonfinite_json_state_input(self):
        state = {"streaks": {}, "log_offset": 0,
                 "mood": {d: float("nan") if d == "Cn" else 0.1 for d in goals.MOOD_DIMS}}
        self.compare({"goals.metta": encoded(goal() + "\n"),
                      "goals_kernel.json": encoded(json.dumps(state))}, [op("kernel")])

    def test_literal_python_list_string_goal_input(self):
        literal = repr(["goal", "list-input", "Work", "sti:0.8", "lti:0.3", "vibes:", ["care"],
                        "blocked-by:none", "last-verified:never", "note:generated"])
        self.compare({}, [op("write", literal)])

    def test_invalid_goal_utf8_codec_error_message(self):
        self.compare({"goals.metta": encoded(b";; generated\n\xff\n")}, [op("kernel")])


if __name__ == "__main__":
    unittest.main()
