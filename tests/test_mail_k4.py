"""Packet K4 tests: automatic-mail mark and skill text, reply identity across crash/redelivery, conflict report, and per-turn ordinals."""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
BINARY = Path(os.environ.get("CETTA_BIN", "/nonexistent/cetta")).expanduser()
SERVICE = BINARY.parent / "cetta-space-service"


def expected_reply_id(incoming_id: str, agent: str, ordinal: int) -> str:
    incoming_bytes = incoming_id.encode("utf-8")
    agent_bytes = agent.encode("utf-8")
    material = (
        f"{len(incoming_bytes)}:{incoming_id}:"
        f"{len(agent_bytes)}:{agent}:"
        f"{ordinal}"
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:32]


@unittest.skipUnless(
    BINARY.is_file() and SERVICE.is_file(),
    "provide CETTA_BIN in a native release bundle",
)
class K4AgentMailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="k4-")
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.socket = self.work / "s.sock"
        self.service = subprocess.Popen(
            [
                str(SERVICE),
                "--socket",
                str(self.socket),
                "--data",
                str(self.work / "store"),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self.addCleanup(self._stop_service)
        deadline = time.monotonic() + 10.0
        while not self.socket.exists():
            if self.service.poll() is not None:
                self.fail(self.service.stderr.read().decode())
            if time.monotonic() >= deadline:
                self.fail("cetta-space-service did not start")
            time.sleep(0.01)

    def _stop_service(self):
        if self.service is not None and self.service.poll() is None:
            self.service.terminate()
            self.service.communicate(timeout=10)
        self.socket.unlink(missing_ok=True)

    def _quote(self, value) -> str:
        return json.dumps(str(value), ensure_ascii=False)

    def _raw_mailbox(self, body_metta: str) -> str:
        probe = self.work / "raw.metta"
        probe.write_text(
            "!(import! &self mailbox)\n!(import! &self json)\n!(import! &self str)\n" + body_metta,
            encoding="utf-8",
        )
        res = subprocess.run(
            [str(BINARY), "--lang", "petta", "--quiet", str(probe)],
            cwd=self.work,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return res.stdout

    def _send_to_agent(self, msg_id: str, body: str, sender: str = "sender", recipient: str = "receiver"):
        out = self._raw_mailbox(
            f"!(mailbox:send {self._quote(self.socket)} \"agent-mail\" "
            f"{self._quote(msg_id)} {self._quote(recipient)} {self._quote(sender)} "
            f"{self._quote(body)})\n"
        )
        self.assertIn("accepted", out, out)

    def _drain_recipient(self, owner: str = "sender"):
        items = []
        while True:
            out = self._raw_mailbox(
                f"!(case (mailbox:next {self._quote(self.socket)} \"agent-mail\" {self._quote(owner)})\n"
                f"   (((mailbox:idle) (println! (json-stringify (JsonObject ((JsonPair \"status\" (JsonString \"idle\")))))))\n"
                f"    ((mailbox:delivery $id $from $body)\n"
                f"     (let $_ (mailbox:ack {self._quote(self.socket)} \"agent-mail\" {self._quote(owner)} $id)\n"
                f"       (println! (json-stringify (JsonObject ("
                f"(JsonPair \"status\" (JsonString \"delivery\")) "
                f"(JsonPair \"id\" (JsonString $id)) "
                f"(JsonPair \"from\" (JsonString (repr $from))) "
                f"(JsonPair \"body\" (JsonString $body))))))))))\n"
            )
            record = None
            for ln in out.splitlines():
                if ln.startswith(("{", '"{')):
                    val = json.loads(ln)
                    record = json.loads(val) if isinstance(val, str) else val
                    break
            self.assertIsNotNone(record, out)
            if record["status"] == "idle":
                break
            self.assertEqual(record["status"], "delivery", record)
            items.append(record)
        return items

    def _agent_env(self, agent_name: str = "receiver"):
        return dict(
            os.environ,
            XDG_STATE_HOME=str(self.work / "state"),
            OPENAI_API_KEY="test-only-unused",
            METTACLAW_ENGINE="cetta",
            METTACLAW_ACTIVE_ENGINE="cetta",
            CETTA_BIN=str(BINARY),
            METTACLAW_SKIP_INITIALIZE="1",
            METTACLAW_SKIP_CONFIG="1",
            METTACLAW_LLM_TRANSPORT="python",
            METTACLAW_TELEGRAM_CLIENT="metta",
            METTACLAW_OBSERVED_STRATEGY="0",
            METTACLAW_MAIL_SOCKET=str(self.socket),
            METTACLAW_MAIL_NAME=agent_name,
            METTACLAW_MAIL_SPACE="agent-mail",
            TMPDIR=str(self.work),
            PYTHONDONTWRITEBYTECODE="1",
        )

    def _run_agent_program(self, code: str, agent_name: str = "receiver") -> str:
        program = ROOT / f".k4-probe-{os.getpid()}-{time.monotonic_ns()}.metta"
        program.write_text(code, encoding="utf-8")
        try:
            res = subprocess.run(
                ["bash", "./run.sh", str(program)],
                cwd=ROOT,
                env=self._agent_env(agent_name),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=60,
            )
        finally:
            program.unlink(missing_ok=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return res.stdout

    def test_crash_between_reply_and_ack_redelivers_once(self):
        """Kill the agent process (SIGKILL) after mail-send and before mail:ack,
        then restart and redeliver. The recipient holds exactly one reply."""
        self._send_to_agent("inc-crash-1", "please inspect")
        replied_flag = self.work / "replied.flag"
        crash_prog = ROOT / f".k4-crash-{os.getpid()}-{time.monotonic_ns()}.metta"
        crash_prog.write_text(
            "!(import! &self (library lib_import))\n"
            "!(import! &self ./lib_mettaclaw)\n"
            "!(remove-atom &self (= (commchannel) (empty)))\n"
            "!(add-atom &self (= (commchannel) telegram))\n"
            "!(let $msg (receive)\n"
            "   (let $_ (println! (RECEIVED_FIRST $msg))\n"
            "     (let $sent (run-command-batch-once 101 1 (quote ((mail-send \"sender\" \"deterministic answer\"))))\n"
            "       (let $_ (println! (SENT_FIRST $sent))\n"
            f"         (let $_ (fs:update-text {self._quote(replied_flag)} \"write\" \"1\" \"\" \"\")\n"
            "           (mail:pause 30000))))))\n",
            encoding="utf-8",
        )
        proc = subprocess.Popen(
            ["bash", "./run.sh", str(crash_prog)],
            cwd=ROOT,
            env=self._agent_env("receiver"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 30.0
            while not replied_flag.exists():
                if proc.poll() is not None:
                    out, err = proc.communicate(timeout=5)
                    self.fail(f"agent exited before setting replied flag:\n{out}\n{err}")
                if time.monotonic() >= deadline:
                    self.fail("timed out waiting for first reply before crash")
                time.sleep(0.02)
            # Kill the entire agent process group between reply and ack.
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
            if proc.stdout is not None:
                proc.stdout.close()
            if proc.stderr is not None:
                proc.stderr.close()
            crash_prog.unlink(missing_ok=True)

        # Restart a fresh agent process: incoming mail was never acked, so it is
        # redelivered, answered again, and settled.
        second_out = self._run_agent_program(
            "!(import! &self (library lib_import))\n"
            "!(import! &self ./lib_mettaclaw)\n"
            "!(remove-atom &self (= (commchannel) (empty)))\n"
            "!(add-atom &self (= (commchannel) telegram))\n"
            "!(let $msg (receive)\n"
            "   (let $_ (println! (RECEIVED_SECOND $msg))\n"
            "     (let $sent (run-command-batch-once 102 1 (quote ((mail-send \"sender\" \"deterministic answer\"))))\n"
            "       (let $_ (println! (SENT_SECOND $sent))\n"
            "         (assert (== (settleActivityBatch) 1))))))\n"
            "!(assert (not (mail:pending?)))\n"
            "!(println! CRASH_RECOVERY_OK)\n",
            agent_name="receiver",
        )
        self.assertIn("CRASH_RECOVERY_OK", second_out, second_out)
        self.assertIn("id=inc-crash-1", second_out, second_out)

        deliveries = self._drain_recipient("sender")
        self.assertEqual(len(deliveries), 1, f"expected 1 reply, got {deliveries}")
        self.assertEqual(deliveries[0]["body"], "deterministic answer")
        expected_id = expected_reply_id("inc-crash-1", "receiver", 1)
        self.assertRegex(deliveries[0]["id"], r"^[0-9a-f]{32}$")
        self.assertEqual(deliveries[0]["id"], expected_id)

    def test_conflict_on_redelivery_reports_already_answered(self):
        """If a crashed turn sent a reply and the redelivered turn attempts a
        different reply body under the same derived ID, claw-mail refuses the
        conflict and the turn reports 'already answered', leaving the first reply."""
        self._send_to_agent("inc-conflict-1", "question?")
        first_out = self._run_agent_program(
            "!(import! &self (library lib_import))\n"
            "!(import! &self ./lib_mettaclaw)\n"
            "!(remove-atom &self (= (commchannel) (empty)))\n"
            "!(add-atom &self (= (commchannel) telegram))\n"
            "!(let $msg (receive)\n"
            "   (println! (FIRST_SEND (mail-send \"sender\" \"first wording\"))))\n",
            agent_name="receiver",
        )
        self.assertIn("MAILED sender id=", first_out, first_out)

        # Redelivery (no settleActivityBatch ran in first_out) with different wording:
        second_out = self._run_agent_program(
            "!(import! &self (library lib_import))\n"
            "!(import! &self ./lib_mettaclaw)\n"
            "!(remove-atom &self (= (commchannel) (empty)))\n"
            "!(add-atom &self (= (commchannel) telegram))\n"
            "!(let $msg (receive)\n"
            "   (let $res (mail-send \"sender\" \"different wording after crash\")\n"
            "     (let $_ (println! (SECOND_RESULT $res))\n"
            "       (assert (== (settleActivityBatch) 1)))))\n",
            agent_name="receiver",
        )
        self.assertIn("already answered", second_out, second_out)
        deliveries = self._drain_recipient("sender")
        self.assertEqual(len(deliveries), 1, deliveries)
        self.assertEqual(deliveries[0]["body"], "first wording")

    def test_ordinal_two_replies_in_one_turn_and_fresh_unprompted_ids(self):
        """Two replies to one incoming mail in one turn receive distinct derived
        32-hex SHA-256 IDs; mail sent without an active incoming mail keeps fresh IDs."""
        self._send_to_agent("inc-ord-1", "two-part request")
        out = self._run_agent_program(
            "!(import! &self (library lib_import))\n"
            "!(import! &self ./lib_mettaclaw)\n"
            "!(remove-atom &self (= (commchannel) (empty)))\n"
            "!(add-atom &self (= (commchannel) telegram))\n"
            "!(let $msg (receive)\n"
            "   (let $r1 (mail-send \"sender\" \"part one\")\n"
            "     (let $r2 (mail-send \"sender\" \"part two\")\n"
            "       (let $_ (println! (ORDINAL_REPLIES $r1 $r2))\n"
            "         (assert (== (settleActivityBatch) 1))))))\n"
            "!(let $u1 (mail-send \"sender\" \"unprompted one\")\n"
            "   (let $u2 (mail-send \"sender\" \"unprompted two\")\n"
            "     (println! (UNPROMPTED_SENDS $u1 $u2))))\n",
            agent_name="receiver",
        )
        deliveries = self._drain_recipient("sender")
        self.assertEqual(len(deliveries), 4, deliveries)
        id1 = expected_reply_id("inc-ord-1", "receiver", 1)
        id2 = expected_reply_id("inc-ord-1", "receiver", 2)
        self.assertNotEqual(id1, id2)
        by_body = {d["body"]: d["id"] for d in deliveries}
        self.assertEqual(by_body["part one"], id1)
        self.assertEqual(by_body["part two"], id2)
        self.assertRegex(by_body["unprompted one"], r"^[0-9a-f]{32}$")
        self.assertRegex(by_body["unprompted two"], r"^[0-9a-f]{32}$")
        self.assertNotEqual(by_body["unprompted one"], by_body["unprompted two"])
        self.assertNotIn(by_body["unprompted one"], {id1, id2})
        self.assertNotIn(by_body["unprompted two"], {id1, id2})

    def test_automatic_mail_mark_and_skill_text(self):
        """Mail with Auto-Submitted != no (case-insensitive) in a leading
        Field-Name: value header block enters the turn labelled
        '[automatic mail from WHO; id=ID]', remains answerable when useful,
        and skill text tells the agent automatic mail is information to answer
        only when an answer is useful."""
        gw_report = "Auto-Submitted: auto-generated\n\nSent to alpha (<id@nostr>)."
        nostr_auto = (
            "From: alpha <" + ("a" * 64) + "@nostr>\r\n"
            "Auto-Submitted: Auto-Replied; reason=status\r\n"
            "Subject: Re: status\r\n\r\n"
            "All relays connected."
        )
        unmarked_cases = [
            ("unmark-no", "Auto-Submitted: NO\n\nHuman mail"),
            ("unmark-nonhdr", "Hello there\nAuto-Submitted: auto-generated\n\nBody"),
            ("unmark-space", "Quick note: check this\nAuto-Submitted: auto-generated\n\nBody"),
            ("unmark-body", "Subject: hi\n\nAuto-Submitted: auto-generated\n\nBody"),
            ("unmark-noblank", "Auto-Submitted: auto-generated"),
        ]

        def send_expr(msg_id: str, body: str) -> str:
            return (
                f"!(mailbox:send {self._quote(self.socket)} \"agent-mail\" "
                f"{self._quote(msg_id)} \"receiver\" \"sender\" {self._quote(body)})"
            )

        checks = [
            "!(import! &self (library lib_import))",
            "!(import! &self ./lib_mettaclaw)",
            "!(remove-atom &self (= (commchannel) (empty)))",
            "!(add-atom &self (= (commchannel) telegram))",
            send_expr("mark-gw-1", gw_report),
            f"!(assert (== (receive) {self._quote('[automatic mail from sender; id=mark-gw-1] ' + gw_report)}))",
            "!(assert (== (settleActivityBatch) 1))",
            send_expr("mark-nostr-1", nostr_auto),
            f"!(assert (== (receive) {self._quote('[automatic mail from sender; id=mark-nostr-1] ' + nostr_auto)}))",
            "!(println! (AUTO_REPLY (mail-send \"sender\" \"useful follow-up\")))",
            "!(assert (== (settleActivityBatch) 1))",
        ]
        for msg_id, body in unmarked_cases:
            checks.append(send_expr(msg_id, body))
            checks.append(
                f"!(assert (== (receive) {self._quote(f'[mail from sender; id={msg_id}] ' + body)}))"
            )
            checks.append("!(assert (== (settleActivityBatch) 1))")
        checks.append("!(println! (SKILLS_TEXT (repr (getLegacySkills))))")
        checks.append("!(println! (MAIL_HELP (help \"mail-send\")))")
        checks.append("!(println! K4_MARK_OK)")

        out = self._run_agent_program("\n".join(checks) + "\n", agent_name="receiver")
        self.assertIn("K4_MARK_OK", out, out)
        self.assertIn("[automatic mail from ...]", out, out)
        self.assertIn("information; answer it only when", out, out)

        deliveries = self._drain_recipient("sender")
        self.assertEqual(len(deliveries), 1, deliveries)
        self.assertEqual(deliveries[0]["body"], "useful follow-up")
        self.assertEqual(deliveries[0]["id"], expected_reply_id("mark-nostr-1", "receiver", 1))


if __name__ == "__main__":
    unittest.main()
