"""Real branch loop/skills entry points, isolated non-bookkeeping boundaries.

This loads the real definitions, not extracted/reimplemented bookkeeping
callers. Boot initialization/continuation and clock/channel inputs are fixtures;
no full-shadow, provider loop, Telegram or installed instance is started.
"""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from goal_bookkeeping_support import ROOT, engine, environment, receipt, run
from test_goal_bookkeeping_candidates import FROZEN, IO_TRACE, encoded, goal, restore, snapshot, wake
import goals
import helper
import fuel_modes


def q(value):
    return json.dumps(str(value), ensure_ascii=False)


def caller_prefix(native=True, trace_io=True):
    text = '''!(import! &self (library lib_import))
!(import! &self ./src/helper.py)
!(import! &self ./tests/goal_bookkeeping_callers_probe.py)
!(py-call (goal_bookkeeping_callers_probe.setup %d))
!(import! &self ./src/open_assemblage.metta)
!(import! &self ./src/loop_policy.metta)
!(import! &self ./src/skills.metta)
!(import! &self ./src/loop.metta)
''' % native
    if native:
        text += FROZEN + (IO_TRACE if trace_io else "")
    text += '''
; Observe the real selected writer result without replacing its computation.
!(match &self (= (gk:kernel-pass) $body)
  (progn (add-atom &self (= (caller:kernel-original) $body))
         (remove-atom &self (= (gk:kernel-pass) $body))))
(= (gk:kernel-pass) (let $caller-msg (caller:kernel-original)
  (progn (py-call (goal_bookkeeping_callers_probe.note $caller-msg)) $caller-msg)))
!(match &self (= (gk:human-wake $token) $body)
  (progn (add-atom &self (= (caller:wake-original $token) $body))
         (remove-atom &self (= (gk:human-wake $token) $body))))
(= (gk:human-wake $token) (let $caller-result (caller:wake-original $token)
  (progn (py-call (goal_bookkeeping_callers_probe.note $caller-result)) $caller-result)))
; Only non-bookkeeping boot work is replaced. mettaclaw itself is untouched.
!(match &self (= (initLoop) $body) (remove-atom &self (= (initLoop) $body)))
!(match &self (= (processLoop $state) $body) (remove-atom &self (= (processLoop $state) $body)))
!(match &self (= (sleepInterval) $body) (remove-atom &self (= (sleepInterval) $body)))
!(match &self (= (get_time) $body) (remove-atom &self (= (get_time) $body)))
!(match &self (= (get_time_as_string) $body) (remove-atom &self (= (get_time_as_string) $body)))
(= (sleepInterval) 1)
(= (initLoop) (rooted fixture (caller:base)))
(= (processLoop $state) BootStopped)
(= (initMemory) FixtureMemoryNoop)
(= (initChannels) FixtureChannelsNoop)
(= (get_time) 1000)
(= (get_time_as_string) "2026-01-02T03:04:05")
(= (tg:client-metta?) True)
(= (tg:last-message-arm-loops) 7)
(= (caller:base) (coordinate loops 0 (coordinate last-heartbeat 0
  (coordinate sleep-interval 60 (coordinate burst-end idle
  (coordinate active-policy godelclaw coordinates-empty))))))
!(bind! &caller-periphery (new-state (caller:base)))
(= (caller:fields $p) (CallerState (loop-budget $p) (loop-last-heartbeat $p)
  (loop-sleep $p) (coordinate-get-or burst-end idle $p)))
'''
    return text


def guest(directory, program, native=True, runtime_root=ROOT):
    if native:
        return engine(directory, program, runtime_root=runtime_root)
    # SWI uses the actual run.sh and the actual branch/staged runtime sources.
    with tempfile.NamedTemporaryFile("w", dir=runtime_root, suffix=".metta") as source:
        source.write(program)
        source.flush()
        receipt("swi-real-caller-program", {"program": program})
        return run(["bash", str(Path(os.environ["PETTA_ROOT"]) / "run.sh"), source.name, "default"],
                   directory, cwd=runtime_root, extra_env={"METTACLAW_ENGINE": "petta", "METTACLAW_ACTIVE_ENGINE": "petta",
                   "PYTHONPATH": os.pathsep.join((str(ROOT / "tests"), str(runtime_root / "src"), str(runtime_root / "channels")))})


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set authorized CETTA_BIN")
class RealCallerTests(unittest.TestCase):
    def compare(self, initial, steps, native=True, faults=None, runtime_root=ROOT):
        faults = faults or {}
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            initial = {**{n: None for n in ("goals.metta", "goals_kernel.json", "goals_compost.log", "updates.jsonl")}, **initial}
            restore(directory, initial)
            env = environment(directory)
            env.update(METTACLAW_ENGINE="petta", METTACLAW_ACTIVE_ENGINE="petta")
            expected, printed = [], []
            held, last, sleep, burst = 0, 0, 60, "idle"
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
                 mock.patch("builtins.open", side_effect=fault_open), mock.patch.object(goals.os, "replace", side_effect=fault_replace):
                for i, step in enumerate(steps):
                    restore(directory, step.get("files", {}))
                    kind, messages = step["kind"], []
                    try:
                        if kind in ("boot", "heartbeat"):
                            if kind == "boot" or step.get("due", True):
                                message = goals.kernel_pass()
                                messages.append(message)
                                printed.append(message)
                            if kind == "heartbeat" and step.get("due", True):
                                held, last, sleep, burst = fuel_modes.refuel(held, 50), 1000, 1, "budget"
                            value = "BootStopped" if kind == "boot" else ["CallerState", held, last, sleep, burst]
                        elif kind == "operator":
                            if step.get("activity", True) and step.get("human", True):
                                result = goals.human_wake(step.get("token", ""))
                                messages.append(wake(result))
                                if result["fresh"]:
                                    held, last, sleep, burst = max(held, 7), 1000, 1, "budget"
                                    printed.append(result["message"])
                            value = ["CallerState", held, last, sleep, burst]
                        elif kind == "write":
                            value = goals.goal_write(helper.decode_safe_text(step["text"]))
                        else:
                            value = goals.goal_drop(step["name"], step["reason"])
                    except (OSError, UnicodeDecodeError) as exc:
                        value = ["raises", str(exc)]
                    expected.append({"label": str(i), "value": value, "messages": messages, "files": snapshot(directory)})
            restore(directory, initial)
            for name in ("goals.metta.tmp", "goals_kernel.json.tmp"):
                (directory / name).unlink(missing_ok=True)
            program = caller_prefix(native)
            for i, step in enumerate(steps):
                fixture = directory / ("caller-fixture-%d.json" % i)
                fixture.write_text(json.dumps(step.get("files", {})))
                program += '!(py-call (goal_bookkeeping_probe.apply_fixture %s))\n' % q(fixture)
                program += '!(py-call (goal_bookkeeping_callers_probe.begin "%d" %s))\n' % (i, q(step.get("token", "")))
                kind = step["kind"]
                if kind == "boot":
                    expression = "(mettaclaw)"
                elif kind == "heartbeat":
                    expression = "(progn (change-state! &caller-periphery (applyHeartbeat (get-state &caller-periphery) 1000 %s)) (caller:fields (get-state &caller-periphery)))" % ("True" if step.get("due", True) else "False")
                elif kind == "operator":
                    expression = "(progn (change-state! &caller-periphery (applyHumanActivity (get-state &caller-periphery) %s %s)) (caller:fields (get-state &caller-periphery)))" % tuple("True" if step.get(k, True) else "False" for k in ("activity", "human"))
                elif kind == "write":
                    expression = "(goal-write %s)" % step.get("atom", q(step["text"]))
                else:
                    expression = "(goal-drop %s %s)" % (q(step["name"]), q(step["reason"]))
                if isinstance(expected[i]["value"], list) and expected[i]["value"][0] == "raises":
                    expression = '(case (catch %s) (((Error $_ $why) ("raises" $why)) ($value $value)))' % expression
                program += '!(py-call (goal_bookkeeping_callers_probe.record "%d" %s))\n' % (i, expression)
            with mock.patch.dict(os.environ, {"GK_TEST_WRITE_FAILURE": faults.get("write", "never-injected"),
                 "GK_TEST_RENAME_FAILURE": faults.get("rename", "never-injected"), "GK_TEST_APPEND_FAILURE": "1" if faults.get("append") else "0"}):
                result = guest(directory, program, native, runtime_root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            path = directory / "observations.jsonl"
            self.assertTrue(path.exists(), result.stdout + result.stderr)
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            actual = [{k: row[k] for k in ("label", "value", "messages", "files")} for row in rows]
            events = [json.loads(line) for line in (directory / "io-events.jsonl").read_text().splitlines()] if (directory / "io-events.jsonl").exists() else []
            receipt("real-caller-parity", {"native": native, "expected": expected, "actual": actual,
                "steps": steps, "faults": faults, "events": events, "rows": rows, "printed_messages": printed,
                "scope": "actual branch loop/skills definitions; boot init/continuation, clock, channel inputs are explicit fixtures"})
            self.assertEqual(actual, expected)
            for message in printed:
                self.assertIn(message, result.stdout)
            if native:
                self.assertFalse(any(key in rows[-1]["calls"] for key in ("kernel_pass", "human_wake", "goal_write", "goal_drop")))
            return rows, events

    def test_boot_heartbeat_operator_real_skill_atom_paths(self):
        text = goal("native-atom", verified="2026-01-02").replace('note:"generated 日本 🙂 é"', 'note:generated')
        rows, _ = self.compare({"goals.metta": encoded(goal() + "\n")}, [
            {"kind": "boot"}, {"kind": "write", "text": text, "atom": text},
            {"kind": "operator", "token": "operator-1"}, {"kind": "operator", "token": "operator-1"},
            {"kind": "operator", "token": "ignored", "human": False}, {"kind": "heartbeat", "due": False},
            {"kind": "heartbeat"}, {"kind": "write", "text": "(goal-budget 0.5)", "atom": "(goal-budget 0.5)"},
            {"kind": "drop", "name": "native-atom", "reason": "solved"},
            {"kind": "write", "text": "(goal broken)"}, {"kind": "drop", "name": "missing", "reason": "retire"}])
        self.assertEqual(rows[-1]["calls"], {"goal_renames": 6, "kernel_renames": 3, "retire_goal": 1})

    def test_100_real_heartbeats_filter_mood_with_saved_offsets_and_shrink(self):
        from test_goal_bookkeeping_domain import affect_cases, log_record
        state = {"streaks": {}, "log_offset": 0,
                 "mood": {d: -1.0 if i % 2 else 1.0 for i, d in enumerate(goals.MOOD_DIMS)}}
        log, steps = b"", [{"kind": "boot"}]
        cases = affect_cases()
        for i in range(100):
            if i == 50:
                log = b""  # deterministic shrink below saved byte offset
            log += log_record(cases[i % len(cases)][1])
            steps.append({"kind": "heartbeat", "files": {"updates.jsonl": encoded(log)}})
        rows, _ = self.compare({"goals.metta": encoded(goal(blocked="wait") + "\n"),
                               "goals_kernel.json": encoded(json.dumps(state))}, steps)
        self.assertEqual(rows[-1]["calls"]["kernel_renames"], 101)

    def test_absent_empty_malformed_at_real_boot_and_skills(self):
        for raw in (None, encoded(""), encoded(goal() + "\n(goal broken)\n")):
            with self.subTest(raw=raw):
                self.compare({"goals.metta": raw}, [{"kind": "boot"}, {"kind": "heartbeat"},
                    {"kind": "operator", "token": "fixture-batch"}, {"kind": "operator", "token": "fixture-batch"},
                    {"kind": "write", "text": goal()}, {"kind": "drop", "name": "missing", "reason": "retire"}])

    def test_real_skill_validation_budget_endpoints_and_unicode_atom(self):
        unverified = goal("unicode-atom").replace('note:"generated 日本 🙂 é"', 'note:日本🙂')
        verified = unverified.replace("last-verified:never", "last-verified:2026-01-02")
        steps = [{"kind": "write", "text": text, "atom": text} for text in
                 ("(goal-budget 0.49)", "(goal-budget 0.5)", "(goal-budget 10)",
                  "(goal-budget 10.01)", "(goal broken)", unverified)]
        steps += [{"kind": "drop", "name": "unicode-atom", "reason": ""},
                  {"kind": "drop", "name": "unicode-atom", "reason": "solved"},
                  {"kind": "write", "text": verified, "atom": verified},
                  {"kind": "drop", "name": "unicode-atom", "reason": "solved"},
                  {"kind": "drop", "name": "unicode-atom", "reason": "retire"}]
        self.compare({}, steps)

    def test_partial_wake_retry_and_real_skill_write_drop_faults(self):
        initial = {"goals.metta": encoded(goal(verified="2026-01-02") + "\n")}
        rows, events = self.compare(initial, [{"kind": "operator", "token": "failed-wake"},
            {"kind": "operator", "token": "failed-wake"}], faults={"write": "goals_kernel.json.tmp"})
        self.assertEqual(rows[0]["files"], rows[1]["files"])
        self.assertEqual([e["operation"] for e in events], ["write", "rename", "write"])
        for faults in ({"append": True}, {"rename": "goals.metta"}):
            self.compare(initial, [{"kind": "drop", "name": "alpha", "reason": "solved"}], faults=faults)
        self.compare(initial, [{"kind": "boot"}], faults={"rename": "goals_kernel.json"})
        for faults in ({"write": "goals.metta.tmp"}, {"rename": "goals.metta"}):
            self.compare(initial, [{"kind": "boot"}, {"kind": "heartbeat"}], faults=faults)

    @unittest.skipUnless(os.environ.get("PETTA_ROOT"), "set PETTA_ROOT for actual SWI route")
    def test_real_swi_loop_skills_and_legacy_list_string(self):
        literal = repr(["goal", "list-input", "Work", "sti:0.8", "lti:0.3", "vibes:", ["care"],
                        "blocked-by:none", "last-verified:never", "note:generated"])
        self.compare({}, [{"kind": "boot"}, {"kind": "write", "text": literal},
            {"kind": "operator", "token": "swi-1"}, {"kind": "operator", "token": "swi-1"},
            {"kind": "heartbeat"}, {"kind": "drop", "name": "list-input", "reason": "retire"}], native=False)
        rows, _ = self.compare({"goals.metta": encoded(goal() + "\n")},
            [{"kind": "operator", "token": "failed-swi"}, {"kind": "operator", "token": "failed-swi"}],
            native=False, faults={"write": "goals_kernel.json.tmp"})
        self.assertEqual(rows[-1]["calls"]["kernel_pass"], 1)

    def test_manifest_staging_without_deployment(self):
        with tempfile.TemporaryDirectory() as temp:
            stage = Path(temp) / "package"
            for name in json.loads((ROOT / "config/runtime-files.json").read_text()):
                source, target = ROOT / name, stage / name
                self.assertTrue(source.is_file(), name)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            rows, _ = self.compare({}, [{"kind": "boot"}, {"kind": "write", "text": goal()}], runtime_root=stage)
            self.assertTrue(all(Path(row["oracle_module"]).is_relative_to(stage) for row in rows))
