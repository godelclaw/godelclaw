"""Sibling mail: a stamped note, peer standing, a capped wake and a peer route."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "channels")]

import mailbox_agent  # noqa: E402
import telegram  # noqa: E402

TOOLS = Path(os.environ.get("CETTA_MAILBOX_ROOT", ROOT.parent / "CeTTa-ours")) / "tools"
CETTA = os.environ.get("MODE_TEST_CETTA_BIN") or os.environ.get("CETTA_BIN")


@unittest.skipUnless((TOOLS / "mailbox_bridge.py").exists(), "set CETTA_MAILBOX_ROOT")
class SendTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.inbox = self.root / "lila" / "inbox"
        config = self.root / "mailboxes.json"
        config.write_text(json.dumps({"version": 1, "instance": "pettaclaw-godel", "name": "godel", "tools": str(TOOLS),
                                      "siblings": {"lila": str(self.inbox)}}))
        patch = mock.patch.dict(os.environ, {"METTACLAW_MAILBOX_CONFIG": str(config)})
        patch.start(); self.addCleanup(patch.stop)

    def test_note_is_delivered_with_the_sender_stamped(self):
        receipt = mailbox_agent.send("lila", "hello\nsister")
        self.assertTrue(receipt.startswith("MAILED lila id="), receipt)
        [path] = list(self.inbox.glob("*.json"))
        envelope = json.loads(path.read_text())
        # The receiving bridge requires its binding's sender; the name is in the body.
        self.assertEqual(envelope["sender"], "local")
        self.assertEqual(envelope["body"], "From: godel\n\nhello\nsister")
        self.assertEqual(mailbox_agent._from.match(envelope["body"]).group(1), "godel")

    def test_only_configured_siblings_and_bounded_notes(self):
        self.assertTrue(mailbox_agent.send("stranger", "hi").startswith("MAILBOX_SEND_REFUSED"))
        self.assertTrue(mailbox_agent.send("lila", "  ").startswith("MAILBOX_SEND_REFUSED"))
        self.assertTrue(mailbox_agent.send("lila", "x" * (mailbox_agent.SEND_CHARS + 1))
                        .startswith("MAILBOX_SEND_REFUSED"))
        self.assertFalse(list(self.inbox.glob("*.json")) if self.inbox.exists() else [])


class PeerQueueTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.dict(os.environ, {"METTACLAW_INSTANCE": "lila", "METTACLAW_TELEGRAM_CLIENT": "metta"})
        patch.start(); self.addCleanup(patch.stop)
        wakes = mock.patch.object(mailbox_agent, "_sibling_wakes", {})
        wakes.start(); self.addCleanup(wakes.stop)
        events = mock.patch.object(telegram, "_metta_events", [])
        events.start(); self.addCleanup(events.stop)
        request = mock.patch.object(telegram, "_request_wake")
        self.request = request.start(); self.addCleanup(request.stop)

    def test_wakes_are_capped_per_sibling_per_hour(self):
        allowed = [mailbox_agent.sibling_wake("godel", now=1000 + i) for i in range(8)]
        self.assertEqual(allowed, [True] * mailbox_agent.SIBLING_WAKES_PER_HOUR + [False] * 2)
        self.assertTrue(mailbox_agent.sibling_wake("other", now=1010))
        self.assertTrue(mailbox_agent.sibling_wake("godel", now=1000 + 3600))

    def test_sibling_note_is_peer_input_and_operator_mail_is_unchanged(self):
        telegram.enqueue_mailbox_event('{"from":"godel"}', "mailbox:lila:inbox-a", "godel")
        telegram.enqueue_mailbox_event('{"sender":"local"}', "mailbox:lila:inbox-b")
        peer, operator = telegram._metta_events
        self.assertEqual((peer["from_bot"], peer["wake"]), (True, "sibling mail"))
        self.assertEqual((operator["from_bot"], operator["wake"]), (False, "mailbox arrival"))

    def test_a_note_over_the_cap_waits_without_a_wake(self):
        for i in range(mailbox_agent.SIBLING_WAKES_PER_HOUR + 1):
            telegram.enqueue_mailbox_event("{}", "mailbox:lila:inbox-%d" % i, "godel")
        self.assertEqual([e["wake"] for e in telegram._metta_events][-2:], ["sibling mail", ""])
        self.assertEqual(self.request.call_count, mailbox_agent.SIBLING_WAKES_PER_HOUR)


@unittest.skipUnless(CETTA, "set MODE_TEST_CETTA_BIN")
class PeerRouteTest(unittest.TestCase):
    def last_human(self, items):
        program = ("!(import! &self ./src/helper.py)\n!(import! &self ./channels/telegram.py)\n"
                   "!(import! &self ./channels/telegram.metta)\n"
                   "!(tg:apply-route (tg:route-batch %s () () ()))\n!(tg:get last-human 9)\n" % items)
        with tempfile.NamedTemporaryFile("w", suffix=".metta", dir=ROOT, delete=False) as f:
            f.write(program)
        try:
            run = subprocess.run([CETTA, "--lang", "petta", "--import-mode", "ancestor-walk", f.name],
                                 capture_output=True, text=True, timeout=120, cwd=ROOT,
                                 env=dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT / "src"), str(ROOT / "channels")])))
        finally:
            os.unlink(f.name)
        return [line for line in run.stdout.splitlines() if line][-1]

    def test_a_batch_of_sibling_mail_is_not_operator_input(self):
        peer = '(1 "" "runtime" "[mailbox] a" True "light" "mailbox:lila:inbox-a")'
        operator = '(2 "" "runtime" "[mailbox] b" False "light" "mailbox:lila:inbox-b")'
        self.assertEqual(self.last_human("(%s)" % peer), "0")
        self.assertEqual(self.last_human("(%s)" % operator), "1")
        self.assertEqual(self.last_human("(%s %s)" % (peer, operator)), "1")
        self.assertEqual(self.last_human("(%s %s)" % (operator, peer)), "1")


if __name__ == "__main__":
    unittest.main()
