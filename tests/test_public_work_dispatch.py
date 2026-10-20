"""The public-work epoch is captured in the real command path.

Runs the real dispatchers (the Prolog one under PeTTa and the MeTTa one under
CeTTa) through ``run-command-batch-once`` with the real ``effect_receipts``.
Only the worklog is a stub: a tiny library that traces every call, so the
order status -> command -> add is observable.  No provider, Telegram, live
state, service or the installed worklog is used.
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import test_command_dispatch_engine as engine_fixture  # noqa: E402


def harness():
    return engine_fixture.CommandDispatchEngineTest()

TURN = 424242

STUB_WORKLOG = '''
import json, os
from pathlib import Path


def _trace(what):
    with open(os.environ["STUB_TRACE"], "a") as stream:
        stream.write(what + "\\n")


def transact(root, action, text="", expected_epoch=None):
    root = Path(root)
    state = json.loads((root / "state.json").read_text())
    if action == "status":
        _trace("status")
        if os.environ.get("STUB_STATUS_FAILS"):
            raise RuntimeError("worklog state unreadable")
        return state
    if action == "add":
        _trace("add")
        if expected_epoch != state["epoch"] or not state["enabled"]:
            _trace("add-rejected")
            return {"accepted": False}
        with (root / "notes.jsonl").open("a") as stream:
            stream.write(json.dumps({"text": text}) + "\\n")
        return {"accepted": True}
    raise ValueError(action)
'''

RAISING_CAPTURE = '''
import importlib.util, pathlib, sys
_spec = importlib.util.spec_from_file_location(
    "effect_receipts", pathlib.Path(%r) / "effect_receipts.py")
_module = importlib.util.module_from_spec(_spec)
sys.modules["effect_receipts"] = _module
_spec.loader.exec_module(_module)


def _broken(turn):
    raise RuntimeError("capture exploded")


_module.capture_public_epoch = _broken
''' % str(ROOT / "src")


class Fixture:
    """Shared scenarios; subclasses choose the engine."""

    def run_batch(self, command_tail="", enabled=True, status_fails=False,
                  raising_capture=False):
        """Run one shell command; return (stdout, trace, notes, receipts)."""
        with tempfile.TemporaryDirectory() as directory:
            d = pathlib.Path(directory)
            lib, root = d / "lib", d / "worklog"
            lib.mkdir()
            root.mkdir()
            (lib / "worklog.py").write_text(STUB_WORKLOG, encoding="utf-8")
            (root / "state.json").write_text(
                json.dumps({"enabled": enabled, "epoch": 1}))
            trace, effect = d / "trace", d / "effect.log"
            trace.write_text("")
            shell = "echo command >> %s; echo ran >> %s" % (trace, effect)
            shell += command_tail.replace("@ROOT@", str(root))
            probe = d / "probe.metta"
            probe.write_text(
                "%s!(println! (run-command-batch-once %d 5 (quote ((shell %s)))))\n"
                % (self.probe_prefix, TURN, json.dumps(shell)), encoding="utf-8")
            (d / "stimulus-frontier").write_text("1 %d 0 0\n" % TURN, "ascii")
            lifecycle = d / "lifecycle.json"
            lifecycle.write_text('{"schema":1,"state":"running"}\n',
                                 encoding="utf-8")
            env = harness()._cetta_env()
            env.update({
                "METTACLAW_SKIP_INITIALIZE": "1",
                "METTACLAW_SKIP_CONFIG": "1",
                "METTACLAW_OBSERVED_STRATEGY": "0",
                "METTACLAW_LIFECYCLE_PATH": str(lifecycle),
                "PETTA_ROOT": str(pathlib.Path.home() / "repos" / "PeTTa"),
                "PETTA_PY_ENV": str(pathlib.Path.home() / "miniforge3" /
                                     "envs" / "petta"),
                "METTACLAW_ENGINE_STATE_PATH": str(d / "engine-selection"),
                "METTACLAW_EFFECT_RECEIPT_PATH": str(d / "receipts.jsonl"),
                "METTACLAW_STIMULUS_FRONTIER_PATH": str(d / "stimulus-frontier"),
                "METTACLAW_PUBLIC_WORK_DIR": str(root),
                "METTACLAW_PUBLIC_WORK_LIB": str(lib),
                "STUB_TRACE": str(trace),
            })
            if status_fails:
                env["STUB_STATUS_FAILS"] = "1"
            if raising_capture:
                shadow = d / "shadow"
                shadow.mkdir()
                (shadow / "effect_receipts.py").write_text(RAISING_CAPTURE)
                env["PYTHONPATH"] = str(shadow) + os.pathsep + env["PYTHONPATH"]
            result = self.launch(probe, env)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            self.assertEqual(
                effect.read_text() if effect.exists() else "", "ran\n",
                "the command did not run exactly once:\n" + result.stdout[-3000:])
            notes_file = root / "notes.jsonl"
            notes = [json.loads(line)["text"] for line in
                     notes_file.read_text().splitlines()] \
                if notes_file.exists() else []
            receipts = [json.loads(line) for line in
                        (d / "receipts.jsonl").read_text().splitlines()]
            return (result.stdout, trace.read_text().split(), notes, receipts)

    def check_receipt(self, receipts):
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["turn"], TURN)
        self.assertEqual(receipts[0]["disposition"], "returned")

    def test_capture_happens_before_the_command_and_publishes_once(self):
        out, trace, notes, receipts = self.run_batch()
        self.assertEqual(trace, ["status", "command", "add"])
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith("Activity · shell\n"))
        self.assertIn("Receipt: returned", notes[0])
        self.check_receipt(receipts)

    def test_sharing_off_at_capture_publishes_nothing(self):
        out, trace, notes, receipts = self.run_batch(enabled=False)
        self.assertEqual(trace, ["status", "command"])
        self.assertEqual(notes, [])
        self.check_receipt(receipts)

    def test_off_on_cycle_after_capture_drops_the_event(self):
        out, trace, notes, receipts = self.run_batch(
            command_tail="; printf '{\"enabled\": false, \"epoch\": 2}' "
            "> @ROOT@/state.json; printf '{\"enabled\": true, \"epoch\": 3}' "
            "> @ROOT@/state.json")
        # Sharing is on again at record time, but under a new epoch.
        self.assertEqual(notes, [])
        self.assertEqual(trace, ["status", "command", "add", "add-rejected"])
        self.check_receipt(receipts)

    def test_failing_status_leaves_command_and_receipt_unchanged(self):
        out, trace, notes, receipts = self.run_batch(status_fails=True)
        self.assertEqual(notes, [])
        self.assertIn("COMMAND_RETURN:", out)
        self.check_receipt(receipts)


class PettaPrologDispatch(Fixture, unittest.TestCase):
    probe_prefix = ("!(import! &self (library lib_import))\n"
                    "!(import! &self ./src/skills)\n")

    def launch(self, probe, env):
        if not (harness()._petta_root() / "run.sh").is_file():
            self.skipTest("PeTTa checkout is unavailable")
        env = dict(env, METTACLAW_ENGINE="petta")
        env.pop("METTACLAW_ACTIVE_ENGINE", None)
        return subprocess.run(
            ["./run.sh", str(probe)], cwd=ROOT, env=env,
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=60)


class CettaMettaDispatch(Fixture, unittest.TestCase):
    probe_prefix = ""

    def launch(self, probe, env):
        petta, cetta = harness()._petta_root(), harness()._cetta_binary()
        if not cetta.is_file() or not (petta / "lib" / "lib_import.metta").is_file():
            self.skipTest("CeTTa/PeTTa checkouts are unavailable")
        return subprocess.run(
            [str(cetta), "--lang", "petta", "--import-mode", "ancestor-walk",
             str(petta / "lib" / "lib_import.metta"),
             str(ROOT / "src" / "skills.metta"), str(probe)],
            cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=60)

    # The PeTTa launcher rebuilds PYTHONPATH, so the shadow module that makes
    # capture raise cannot be injected there; the Prolog catch is covered by
    # test_command_dispatch.pl instead.
    def test_raising_capture_leaves_command_and_receipt_unchanged(self):
        reference = self.run_batch(enabled=False)
        out, trace, notes, receipts = self.run_batch(raising_capture=True)
        self.assertEqual(trace, ["command"])      # capture never reached the sink
        self.assertEqual(notes, [])
        self.assertIn("COMMAND_RETURN:", out)
        self.check_receipt(receipts)
        keep = ("turn", "disposition", "command", "result")

        def same(rows):     # the temporary directory differs per run
            return [{k: re.sub(r"/tmp/tmp\w+", "<D>", str(r[k])) for k in keep}
                    for r in rows]
        self.assertEqual(same(receipts), same(reference[3]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
