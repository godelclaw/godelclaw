"""The cognition side's readers on the native ledger (METTACLAW_INPUT_LEDGER_SOCKET).

task_context_cetta, task_context_views and context_compaction read the turn's records with SQL on
the SQLite path; on the native path they ask the ledger's server. The same scenario is written
through observed_strategy.Ledger on both paths (one owner on both), and the same MeTTa program reads
it back: the task and conversation projections (whole, from an offset with results cut, and the
first window's start), task-read of calls and inputs, task-history, task-note and the window
checkpoint's compare-and-set print the same text on both.
"""
import hermetic_env  # noqa: F401,E402  (first, before any src module: see hermetic_env.py)
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "src"), str(ROOT / "channels")]
import input_ack
from observed_strategy import Ledger, encoded
import task_context
from native_ledger_client import NativeRunner
from test_input_ack_native import NativeCase, SKIP
from test_input_ledger_remote import ENGINE, Oracle, available, oracle_environment
from test_turn_effect_native import ROUTER, record_attempt

OWNER = "owner-of-both"


def scenario(store, native):
    store.accept("input:a", "first message " + "x" * 300, kind="message")
    store.link_inputs("input:a", ["u1"])
    task_context.begin(store, "input:a", "operator-event", None, "chat")
    store.accept("input:b", "second é日 message", kind="message")
    store.link_inputs("input:b", ["u1", "u2"])
    task_context.begin(store, "input:b", "operator-event", "input:a", "chat")   # covers input:a
    store.accept("task-1", "child work")
    task_context.begin(store, "task-1", "continuation-tick", "input:b", "", parent="input:b")
    for number in range(3):
        snap = store.capture("task-1", "b%d" % number, ROUTER, 0)
        commands = [["shell", "ls %d" % number], {"tool": "read", "path": "fé \"q\""}]
        ticket = store.decide(snap["serial"], {"kind": "command", "tool": "batch",
                                               "command": {"commands": commands, "limit": 2}}, 0)
        task_context.proposal(store, snap["serial"], "task-1", commands, "admitted")
        long = "line %d é\n" % number * 400
        record_attempt(store, native, ticket, 0, commands[0], {"disposition": "returned", "result": long})
        record_attempt(store, native, ticket, 1, commands[1], None)
        store.report(ticket, "batch %d" % number)
    legacy = store.capture("input:b", "legacy", ROUTER, 0)
    old = store.decide(legacy["serial"], {"kind": "command", "tool": "shell", "command": ["echo", "old"]}, 0)
    record_attempt(store, native, old, 0, ["echo", "old"], {"disposition": "returned", "result": "old"})
    store.accept("input:late", "after the calls", kind="message")  # ordered after the calls by their captures
    task_context.note(store, "task-1", "a note on the child")
    task_context.focus(store, "task-1")
    return legacy["serial"]


def program(legacy):
    call = "%s:%d:0" % (OWNER, 0)
    lines = [
        '(task:projection "task-1" "continuation-tick")',
        '(task:projection "input:a" "continuation-tick")',
        '(task:projection "conversation:%s" "continuation-tick")' % OWNER,
        '(task:conversation-projection "task-1" "tick" 0 0)',
        '(task:conversation-projection "task-1" "tick" 2 1000)',
        '(task:conversation-projection "task-1" "tick" 1 40)',
        '(task:read "%s" 0 8192)' % call,
        '(task:read "%s:%d:0" 0 8192)' % (OWNER, legacy),
        '(task:read "task-input:input:b" 0 8192)',
        '(task:history "conversation:%s" 0 0)' % OWNER,
        '(task:history "conversation:%s" 3 4)' % OWNER,
        '(task:history "task-1" 0 0)',
    ]
    code = ['!(import! &self ./src/task_context_views)', '!(import! &self ./src/context_compaction)']
    for number, line in enumerate(lines):
        code.append('!(println! (OUT %d (state:encoded %s)))' % (number, line))
    code += [
        '!(println! (START (task:initial-start 100000 4.0) (task:initial-start 600 4.0) (task:initial-start 1 4.0)))',
        '!(println! (NOTE (task-note "noted from the cognition side")))',
        '!(println! (OUT-NOTES (state:encoded (task:projection "task-1" "continuation-tick"))))',
        '!(println! (CHECK-0 (context:checkpoint "task-1")))',
        '!(println! (WRITE-1 (context:write-checkpoint "task-1" sqlite:null '
        '(JsonObject ((JsonPair "version" (JsonNumber "1")) (JsonPair "start" (JsonNumber "2")) (JsonPair "summary" (JsonString "s")))))))',
        '!(println! (WRITE-2 (context:write-checkpoint "task-1" sqlite:null (JsonObject ((JsonPair "start" (JsonNumber "3")))))))',
        '!(println! (CHECK-1 (context:checkpoint "task-1")))',
        '!(println! (SAVED (task:saved-start "task-1") (state:encoded (state:meta (task:window-key "task-1")))))',
        '!(println! COGNITION_OK)',
    ]
    return "\n".join(code) + "\n"


@unittest.skipUnless(available(), SKIP)
class CognitionLedgerTest(NativeCase):
    def run_guest(self, directory, path, socket, code):
        import test_command_dispatch_engine as dispatch
        env = dispatch.CommandDispatchEngineTest()._cetta_env()
        env.update(METTACLAW_STRATEGY_PATH=str(path), METTACLAW_ENGINE_STATE_PATH=str(Path(directory) / "engine"))
        env.pop(input_ack.LEDGER_SOCKET, None)
        if socket:
            env[input_ack.LEDGER_SOCKET] = str(socket)
        source = ROOT / ("zz-cognition-%d.metta" % os.getpid())
        source.write_text("!(import! &self (library lib_import))\n!(import! &self ./src/task_context_cetta)\n" + code)
        self.addCleanup(lambda: source.unlink(missing_ok=True))
        done = subprocess.run([str(ENGINE), "--lang", "petta", "--import-mode", "ancestor-walk", str(source)],
                              cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
        self.assertEqual(done.returncode, 0, done.stdout[-3000:] + done.stderr[-3000:])
        self.assertIn("COGNITION_OK", done.stdout, done.stdout[-3000:] + done.stderr[-3000:])
        return [line for line in done.stdout.splitlines() if line.startswith("(")]

    def side(self, native):
        with oracle_environment() as directory:
            runner = NativeRunner.create(directory, self.server.socket) if native else Oracle.create(directory)
            store = Ledger(runner.path)
            with store.transaction() as db:
                db.execute("UPDATE meta SET value=? WHERE key='owner'", (encoded(OWNER),))
            store._owner = None
            legacy = scenario(store, native)
            input_ack.close_ledger()
            return self.run_guest(directory, runner.path, self.server.socket if native else None, program(legacy))

    def test_the_cognition_side_reads_the_same_on_both_paths(self):
        oracle, native = self.side(False), self.side(True)
        self.assertEqual(len(native), len(oracle), (oracle[-3:], native[-3:]))
        for want, got in zip(oracle, native):
            self.assertEqual(got, want)
        lines = {line.split(" ", 1)[0]: line for line in oracle}
        self.assertIn("ContextPublished", lines["(WRITE-1"])
        self.assertIn("context-checkpoint-changed", lines["(WRITE-2"])
        self.assertIn("TASK_NOTE_SAVED", lines["(NOTE"])


if __name__ == "__main__":
    unittest.main()
