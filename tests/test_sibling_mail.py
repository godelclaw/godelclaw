"""Sibling mail: a stamped note, peer standing, ordinary input wake and a peer route."""

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
        events = mock.patch.object(telegram, "_metta_events", [])
        events.start(); self.addCleanup(events.stop)
        request = mock.patch.object(telegram, "_request_wake")
        self.request = request.start(); self.addCleanup(request.stop)

    def test_sibling_note_is_peer_input_and_operator_mail_is_unchanged(self):
        telegram.enqueue_mailbox_event('{"from":"godel"}', "mailbox:lila:inbox-a", "godel")
        telegram.enqueue_mailbox_event('{"sender":"local"}', "mailbox:lila:inbox-b")
        peer, operator = telegram._metta_events
        self.assertEqual((peer["from_bot"], peer["wake"]), (True, "sibling mail"))
        self.assertEqual((operator["from_bot"], operator["wake"]), (False, "mailbox arrival"))

    def test_python_client_keeps_peer_standing_in_both_mixed_batch_orders(self):
        for order in (("godel", None), (None, "godel"), ("godel",)):
            with mock.patch.dict(os.environ, METTACLAW_TELEGRAM_CLIENT="python"), \
                    mock.patch.object(telegram,"_pending_messages",[]), \
                    mock.patch.object(telegram,"_prepared_messages",[]):
                for n, sibling in enumerate(order):
                    telegram.enqueue_mailbox_event("{}", "mailbox:lila:inbox-%d" % n, sibling)
                telegram.getActivityBatch()
                self.assertEqual(telegram.lastMessageIsHuman(), int(None in order))


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

    def test_an_admitted_peer_arms_idle_cognition_without_becoming_human(self):
        program = ('!(import! &self ./src/helper.py)\n!(import! &self ./channels/telegram.py)\n'
                   '!(import! &self ./src/open_assemblage.metta)\n'
                   '!(import! &self ./src/loop_policy.metta)\n'
                   '!(import! &self ./src/loop.metta)\n'
                   '!(import! &self ./channels/telegram.metta)\n'
                   '!(import! &self ./tests/peer_activity_fixture.py)\n'
                   '!(py-call (peer_activity_fixture.arm))\n'
                   '!(assert (== (loop-budget (applyPeerActivity (coordinate loops 0 coordinates-empty) True False)) 50))\n'
                   '!(assert (== (tg:get last-human 0) 0))\n'
                   '!(py-call (peer_activity_fixture.unrelated))\n'
                   '!(assert (== (loop-budget (applyPeerActivity (coordinate loops 0 coordinates-empty) True False)) 0))\n'
                   '!(py-call (peer_activity_fixture.clear))\n'
                   '!(println! PEER_IDLE_OK)\n')
        with tempfile.NamedTemporaryFile("w", suffix=".metta", dir=ROOT, delete=False) as f:
            f.write(program)
        try:
            with tempfile.TemporaryDirectory() as state:
                env = dict(os.environ, METTACLAW_MAILBOX_CONFIG="", METTACLAW_TELEGRAM_CLIENT="metta",
                           PYTHONPATH=os.pathsep.join([str(ROOT / "src"), str(ROOT / "channels")]),
                           METTACLAW_STRATEGY_PATH=str(Path(state) / "checkpoint.db"))
                result = subprocess.run([CETTA,"--lang","petta","--import-mode","ancestor-walk",f.name],
                                        cwd=ROOT,env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr+result.stdout)
            self.assertIn("PEER_IDLE_OK", result.stdout.splitlines())
        finally:
            os.unlink(f.name)


SERVER = os.environ.get("CETTA_SPACE_SERVICE")
WORKER = os.environ.get("CETTA_VALUE_WORKER")


@unittest.skipUnless(SERVER and WORKER, "set CETTA_SPACE_SERVICE and CETTA_VALUE_WORKER")
class DurablePeerBudgetTest(unittest.TestCase):
    def setUp(self):
        sys.path[:0] = [str(TOOLS), str(TOOLS.parent / "tests")]
        from test_native_mailbox import Fixture
        from native_mailbox import Mailbox
        self.Mailbox = Mailbox
        self.fixture = Fixture(); self.addCleanup(self.fixture.close)
        self.config = {"version": 1, "instance": "peer-test", "name": "lila",
                       "socket": self.fixture.socket, "space": "inbox", "tools": str(TOOLS),
                       "worker": WORKER, "channels": {"inbox": "local"},
                       "auto_receive": ["inbox"], "siblings": {"godel": "unused"},
                       "peer_wakes_per_hour": 2, "watch_ms": 100}
        self.events = []
        self.times = []

    def inbox(self):
        def offer(text, identity, **kw):
            import time
            self.events.append((identity, kw)); self.times.append(time.monotonic())
            return True
        inbox = mailbox_agent.Inbox(self.config, committed=lambda _: False,
                                    offer=offer)
        self.addCleanup(inbox.close)
        return inbox

    def send(self, *identities):
        box = self.Mailbox(self.fixture.socket, "inbox", WORKER)
        try:
            for identity in identities:
                box.send(identity, "inbox", identity, "local", "From: godel\n\nhello")
        finally:
            box.close()

    def test_more_than_six_peer_notes_arrive_without_wake_grants(self):
        self.send(*(str(i) for i in range(8)))
        inbox=self.inbox()
        for _ in range(12): inbox.scan(inbox.box)
        self.assertEqual(len(self.events),8)
        self.assertFalse(inbox.status()['backpressure'])
        identities={event[0] for event in self.events}
        inbox.close(); self.events.clear(); self.fixture.restart()
        restarted=self.inbox()
        for _ in range(12): restarted.scan(restarted.box)
        self.assertEqual({event[0] for event in self.events},identities)

    def test_old_quota_settings_do_not_block_receiving(self):
        self.config['peer_wakes_per_hour']=0
        self.send('one')
        inbox=self.inbox();inbox.scan(inbox.box)
        self.assertEqual(len(self.events),1)


if __name__ == "__main__":
    unittest.main()
