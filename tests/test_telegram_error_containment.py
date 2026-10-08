#!/usr/bin/env python3
"""Raised errors stay contained in Lila's Telegram MeTTa.

Under the PeTTa profile a raising foreign call (a Python exception, a type
error in arithmetic) raises, and an uncaught raise ends the program. These
processes must outlive any one bad command, tap or task:

- the command responder answers a command or tap whose Python raises with
  the failure, the exception's type and message, keeps answering, and stays
  alive; restarting it would only be
  asked the same task again;
- the energy file never raises: an unwritable file is a failed set, and a
  setting that reads as no number is the default;
- her loop's client retries a task whose handling raises, never lets it hold
  up the tasks behind it, and on the third failure writes it to her ledger and
  acknowledges it.

Faults are injected with test modules on the import path, never with hooks in
the code under test. Environment: CETTA_BIN, a cetta with Python.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CETTA = str(pathlib.Path(os.environ["CETTA_BIN"]).resolve())
sys.path[:0] = [str(ROOT / "tests")]

from test_durable_telegram_transport import FakeService  # noqa: E402

FAKE_LLM = '''
def quota():
    raise RuntimeError("provider exploded")

def set_model(name):
    raise RuntimeError("provider exploded")
'''

FAKE_TELEGRAM = '''
import importlib.util, os
_spec = importlib.util.spec_from_file_location("_telegram_real", os.environ["REAL_TELEGRAM_PY"])
_real = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_real)
for _name, _value in vars(_real).items():
    if not _name.startswith("__"):
        globals()[_name] = _value

def metta_foreign_receipt(observation):
    raise RuntimeError("injected failure")
'''


class Containment(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lila-containment-")
        self.dir = pathlib.Path(self.temp.name)
        self.fakes = self.dir / "fakes"
        self.fakes.mkdir()
        self.socket = str(self.dir / "channel.sock")
        self.service = FakeService(self.socket)
        self.env = dict(
            os.environ,
            METTACLAW_TELEGRAM_CLIENT="metta",
            METTACLAW_TELEGRAM_SERVICE_SOCKET=self.socket,
            METTACLAW_TELEGRAM_LOG_PATH=str(self.dir / "telegram.jsonl"),
            METTACLAW_TELEGRAM_HEALTH_PATH=str(self.dir / "health.json"),
            METTACLAW_TELEGRAM_OPERATOR_IDS="7",
            METTACLAW_TELEGRAM_ALLOWED_CHAT_IDS="42",
            METTACLAW_TELEGRAM_PRIMARY_CHAT_ID="42",
            METTACLAW_ENERGY_PATH=str(self.dir / "energy.json"),
            METTACLAW_LIFECYCLE_PATH=str(self.dir / "lifecycle.json"),
            METTACLAW_WAKE_REQUEST_PATH=str(self.dir / "wake.requested"),
            METTACLAW_LOOP_MODE_PATH=str(self.dir / "loop-mode"),
            METTACLAW_FUEL_MODE_PATH=str(self.dir / "fuel-mode"),
            METTACLAW_ENGINE_STATE_PATH=str(self.dir / "engine"),
            XDG_STATE_HOME=str(self.dir / "state-home"),
            PYTHONPATH=os.pathsep.join([str(self.fakes), str(ROOT / "src"), str(ROOT / "channels")]))
        (self.dir / "lifecycle.json").write_text(json.dumps({"schema": 1, "state": "running"}))
        self.processes = []

    def tearDown(self):
        for p, name in self.processes:
            if p.poll() is None:
                p.kill()
            p.communicate(timeout=10)
            os.unlink(name)
        self.service.close()
        self.temp.cleanup()

    def program(self, source):
        f = tempfile.NamedTemporaryFile("w", suffix=".metta", dir=ROOT, delete=False)
        f.write(source)
        f.close()
        return f.name

    def start(self, source, env=None):
        name = self.program(source)
        p = subprocess.Popen([CETTA, "--lang", "petta", "--import-mode", "ancestor-walk", name],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=ROOT, env=env or self.env)
        self.processes.append((p, name))
        return p

    def run_program(self, source, env=None):
        name = self.program(source)
        try:
            return subprocess.run([CETTA, "--lang", "petta", "--import-mode", "ancestor-walk", name],
                                  capture_output=True, text=True, timeout=120, cwd=ROOT, env=env or self.env)
        finally:
            os.unlink(name)

    def answered(self, task, seconds=20):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            with self.service.lock:
                if task in self.service.results:
                    return json.loads(self.service.results[task])
            time.sleep(0.05)
        self.fail("task %s never answered; events %s" % (task, self.service.events[-4:]))

    def test_responder_answers_a_raising_command_and_lives_on(self):
        (self.fakes / "synthetic_llm.py").write_text(FAKE_LLM)
        responder = self.start("!(import! &self ./channels/telegram_control.metta)\n!(tc:serve)\n")
        quota = self.service.add(["command", "1", "/quota", "", "42.0"])
        tap = self.service.add(["callback", "2", "model:x", "42.0", 900])
        mode = self.service.add(["command", "3", "/mode", "", "42.0"])
        answer = self.answered(quota)
        self.assertEqual(answer[0], "answer")
        self.assertTrue(answer[1].startswith("/quota failed:"), answer)
        self.assertIn("RuntimeError: provider exploded", answer[1])
        answer = self.answered(tap)
        self.assertTrue(answer[1].startswith("model:x failed:"), answer)
        self.assertIn("RuntimeError: provider exploded", answer[1])
        self.assertEqual(self.answered(mode)[0], "answer")
        later = self.service.add(["command", "4", "/fuel", "", "42.0"])
        self.assertEqual(self.answered(later)[0], "answer")
        self.assertIsNone(responder.poll(), "the responder ended")

    def test_receipt_notice_does_not_consume_input_and_survives_responder_restart(self):
        env = dict(self.env, METTACLAW_OBSERVED_STRATEGY="1",
                   METTACLAW_INPUT_ACK_SECONDS="0.05")
        source = "!(import! &self ./channels/telegram_control.metta)\n!(tc:serve)\n"
        responder = self.start(source, env)
        task = self.service.add(["input", "42.0", "message", "operator", {}])
        peer = self.service.add(["input", "42.0", "message", "peer", {}])
        until = time.monotonic() + 15
        while time.monotonic() < until:
            with self.service.lock:
                notices = dict(self.service.submissions)
            if notices:
                break
            time.sleep(0.05)
        self.assertEqual(len(notices), 1)
        self.assertIn("Received.", next(iter(notices.values())))
        self.assertNotIn(task, self.service.results)
        self.assertNotIn(peer, self.service.results)
        self.assertIsNone(responder.poll())
        responder.terminate(); responder.wait(timeout=10)
        responder = self.start(source, env)
        command = self.service.add(["command", "c", "/fuel", "", "42.0"])
        self.assertEqual(self.answered(command)[0], "answer")
        time.sleep(0.5)
        self.assertEqual(self.service.submissions, notices)
        self.assertNotIn(task, self.service.results)
        self.assertIsNone(responder.poll())

    def test_energy_never_raises(self):
        env = dict(self.env, METTACLAW_ENERGY_PATH=str(self.dir / "missing" / "energy.json"),
                   METTACLAW_LOOPS_FULL="many")
        p = self.run_program('!(import! &self ./channels/energy.metta)\n'
                             '!(energy:set "default" "mid")\n'
                             '!(energy:tier-loops "full")\n'
                             '!(println! still-running)\n', env)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        lines = [line for line in p.stdout.splitlines() if line]
        self.assertIn('"energy-set failed: could not persist"', lines)
        self.assertIn("50", lines)
        self.assertIn("still-running", lines)
        # The temporary file is written, and the rename onto a directory raises.
        occupied = self.dir / "occupied"
        occupied.mkdir()
        env = dict(self.env, METTACLAW_ENERGY_PATH=str(occupied))
        p = self.run_program('!(import! &self ./channels/energy.metta)\n'
                             '!(energy:set "default" "mid")\n'
                             '!(println! still-running)\n', env)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        lines = [line for line in p.stdout.splitlines() if line]
        self.assertIn('"energy-set failed: could not persist"', lines)
        self.assertIn("still-running", lines)

    def test_client_retries_then_records_a_raising_task(self):
        fake = self.fakes / "telegram.py"
        fake.write_text(FAKE_TELEGRAM)
        env = dict(self.env, REAL_TELEGRAM_PY=str(ROOT / "channels" / "telegram.py"))
        poison = self.service.add(["rejected", "99.0.00000000000000000001", "stopped"])
        behind = self.service.add(["input-too-large", "42.0", "message", "operator"])
        source = ("!(import! &self ./src/helper.py)\n!(import! &self %s)\n"
                  "!(import! &self ./channels/telegram.metta)\n!(tg:start)\n" % fake)
        # tg:start polls once and one more poll follows: the task has failed
        # twice and waits, and the task behind it is answered.
        p = self.run_program(source + "!(tg:poll)\n!(println! still-running)\n", env)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        self.assertIn("still-running", p.stdout)
        acks = [e[1] for e in self.service.events if e[0] == "ack"]
        self.assertIn(behind, acks, "a raising task held up the task behind it")
        self.assertNotIn(poison, acks, "the first failure acknowledged the task")
        p = self.run_program(source + "!(tg:poll)\n!(tg:poll)\n!(tg:poll)\n!(println! still-running)\n", env)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        acks = [e[1] for e in self.service.events if e[0] == "ack"]
        self.assertIn(poison, acks, "the third failure did not acknowledge the task")
        records = [json.loads(line) for line in (self.dir / "telegram.jsonl").read_text().splitlines()]
        errors = [r for r in records if r.get("kind") == "client_error"]
        self.assertEqual([r["task"] for r in errors], [poison])
        self.assertIn("rejected", errors[0]["observation"])


if __name__ == "__main__":
    unittest.main()
