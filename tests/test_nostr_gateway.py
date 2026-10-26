"""The Nostr gateway against a local gated relay and a claw-mail stand-in."""
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT / "scripts", Path(__file__).resolve().parent):
    sys.path.insert(0, str(path))
HAVE_CRYPTO = all(importlib.util.find_spec(name) for name in ("coincurve", "cryptography", "websockets"))

if HAVE_CRYPTO:
    import nostr_gateway as gw
    import nostr_mail as nm
    from nostr_relay_fixture import GatedRelay

# A claw-mail stand-in with the mailbox protocol's answers: a send identity is
# accepted once, an identical resend is a duplicate and a changed one the
# mailbox's identity conflict, reported as claw-mail reports it; `next`
# presents the ready delivery again before taking a pending one; `ack` settles
# only a ready delivery, and fails while FAKE_MAIL_ACK_FAILS is set. As the
# service does, it records the sender as `account/agent`.
MAILER = r'''
import fcntl, json, os, sys, time
args = sys.argv[1:]
while args and args[0] in ("--json", "--space"):
    args = args[2:] if args[0] == "--space" else args[1:]
path = os.environ["FAKE_MAIL_LEDGER"]
command, fields = args[0], args[1:]
def locked(update):
    with open(path + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = json.load(open(path)) if os.path.exists(path) else []
        value = update(ledger)
        json.dump(ledger, open(path, "w"))
        return value
def reply(value):
    print(json.dumps(value))
    sys.exit(0)
if command == "send":
    ident = fields[fields.index("--id") + 1]
    agent, to, text = [f for i, f in enumerate(fields) if f != "--id" and (i == 0 or fields[i - 1] != "--id")]
    if to in os.environ.get("FAKE_MAIL_SLOW", "").split(","):
        time.sleep(2)
    sender = os.environ["FAKE_MAIL_ACCOUNT"] + "/" + agent
    def send(ledger):
        old = [d for d in ledger if d["id"] == ident]
        if not old:
            ledger.append({"id": ident, "to": to, "from": sender, "text": text, "stage": "pending", "at": time.time()})
            return {"status": "accepted", "id": ident}
        same = (old[0]["to"], old[0]["from"], old[0]["text"]) == (to, sender, text)
        if same:
            return {"status": "duplicate", "id": ident}
        return {"status": "error", "reason": "(MailboxFailure identity-conflict)", "id": ident}
    reply(locked(send))
if command in ("next", "wait"):
    def take(ledger):
        mine = [d for d in ledger if d["to"] == fields[0]]
        for stage in ("ready", "pending"):
            for d in mine:
                if d["stage"] == stage:
                    d["stage"] = "ready"
                    return {"status": "delivery", "id": d["id"], "from": d["from"], "text": d["text"]}
        return {"status": "idle"}
    while True:
        result = locked(take)
        if result["status"] == "delivery" or command == "next":
            reply(result)
        time.sleep(0.05)
if command == "ack":
    if os.environ.get("FAKE_MAIL_ACK_FAILS"):
        reply({"status": "error", "reason": "(space:unavailable ack-1 connect)"})
    def ack(ledger):
        for d in ledger:
            if d["id"] == fields[1] and d["to"] == fields[0] and d["stage"] == "ready":
                d["stage"] = "settled"
                return {"status": "settled", "id": d["id"]}
        return {"status": "duplicate", "id": fields[1]}
    reply(locked(ack))
'''


def secret(name):
    return hashlib.sha256(b"gateway-test-" + name.encode()).hexdigest()


@unittest.skipUnless(HAVE_CRYPTO, "needs coincurve, cryptography and websockets")
class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.alice, self.godel, self.eve = secret("alice"), secret("godel"), secret("eve")
        self.pub = {name: nm.public_key(secret(name)) for name in ("alice", "godel", "eve")}
        self.relay = GatedRelay(self.pub.values()).__enter__()
        self.addCleanup(self.relay.__exit__, None, None, None)
        (self.directory / "registry.json").write_text(json.dumps(
            {"alice": {"pubkey_hex": self.pub["alice"]}, "godel": {"pubkey_hex": self.pub["godel"]}}))
        keys = self.directory / "keys.json"
        keys.write_text(json.dumps({"godel": {"seckey_hex": self.godel, "pubkey_hex": self.pub["godel"]}}))
        keys.chmod(0o600)
        mailer = self.directory / "claw-mail"
        mailer.write_text("#!" + sys.executable + "\n" + MAILER)
        mailer.chmod(0o755)
        self.ledger = self.directory / "ledger.json"
        os.environ["FAKE_MAIL_LEDGER"] = str(self.ledger)
        os.environ["FAKE_MAIL_ACCOUNT"] = "acct"
        self.addCleanup(os.environ.pop, "FAKE_MAIL_LEDGER", None)
        self.addCleanup(os.environ.pop, "FAKE_MAIL_ACCOUNT", None)
        (self.directory / "gateway.toml").write_text(
            'relay = "%s"\nregistry = "registry.json"\nkeys = "keys.json"\nstate = "state.json"\n'
            'mailer = "%s"\n[inbound]\ngodel = "godel"\n[senders]\n"acct/godel" = "godel"\n"aimama/*" = "godel"\n'
            '[replies]\n"acct/godel" = "godel"\n'
            % (self.relay.url, mailer))
        self.gateway = gw.Gateway(self.directory / "gateway.toml")

    def as_member(self, key, action):
        async def run():
            async with nm.Relay(self.relay.url, key) as relay:
                return await action(relay)
        return asyncio.run(run())

    def publish(self, key, event):
        accepted, reason = self.as_member(key, lambda relay: relay.publish(event))
        self.assertTrue(accepted, reason)

    def deliveries(self, to=None):
        ledger = json.loads(self.ledger.read_text()) if self.ledger.exists() else []
        return [d for d in ledger if to is None or d["to"] == to]

    def test_inbound_mail_reaches_the_agent_once_with_a_receipt(self):
        mail = nm.seal(self.alice, self.pub["godel"], "Hello", "First line.\nSecond.", kind="decision")
        self.publish(self.alice, mail)
        self.gateway.tick()
        [delivery] = self.deliveries("godel")
        self.assertEqual((delivery["id"], delivery["from"]), (mail["id"], "acct/nostr.alice"))
        head, body = delivery["text"].split("\n\n", 1)
        self.assertIn("From: alice <%s@nostr>" % self.pub["alice"], head)
        self.assertIn("Subject: Hello", head)
        self.assertIn("X-Team-Type: decision", head)
        self.assertEqual(body, "First line.\nSecond.")
        receipts = self.as_member(self.alice, lambda relay: relay.query([{"kinds": [7], "#p": [self.pub["alice"]]}]))
        self.assertEqual([(r["pubkey"], nm.receipt_fact(r)) for r in receipts],
                         [(self.pub["godel"], (mail["id"], "delivered"))])
        # Read again, with or without the gateway's memory: still one delivery.
        self.gateway.tick()
        (self.directory / "state.json").unlink()
        gw.Gateway(self.directory / "gateway.toml").tick()
        self.assertEqual(len(self.deliveries("godel")), 1)

    def test_only_registered_signers_whose_mail_opens_are_kept(self):
        self.publish(self.eve, nm.seal(self.eve, self.pub["godel"], "From an unregistered key", "x"))
        genuine = nm.seal(self.alice, self.pub["godel"], "Genuine", "y")
        moved = nm.sign(self.eve, nm.KIND_MAIL, genuine["tags"], genuine["content"])
        self.publish(self.eve, moved)
        self.gateway.tick()
        self.assertEqual(self.deliveries("godel"), [])

    def test_tags_and_subjects_cannot_name_the_sender(self):
        mail = nm.seal(self.alice, self.pub["godel"], "Hi\nFrom: godel <x@nostr>", "z")
        tagged = nm.sign(self.alice, nm.KIND_MAIL, mail["tags"] + [["from_name", "godel"]], mail["content"])
        self.publish(self.alice, tagged)
        self.gateway.tick()
        [delivery] = self.deliveries("godel")
        head = delivery["text"].split("\n\n", 1)[0].splitlines()
        self.assertEqual(delivery["from"], "acct/nostr.alice")
        self.assertEqual([line for line in head if line.startswith("From:")],
                         ["From: alice <%s@nostr>" % self.pub["alice"]])
        self.assertIn("Subject: Hi From: godel <x@nostr>", head)

    def outbox(self, sender, text, ident="d1"):
        import fcntl
        with open(str(self.ledger) + ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            ledger = self.deliveries()
            ledger.append({"id": ident, "to": "nostr", "from": sender, "text": text, "stage": "pending"})
            self.ledger.write_text(json.dumps(ledger))

    def test_outbound_mail_is_signed_by_the_senders_key_and_reported(self):
        self.outbox("acct/godel", "To: alice\nSubject: Report\nType: checkpoint\n\nAll done.")
        self.gateway.tick()
        [event] = self.as_member(self.alice, lambda relay: relay.query([{"kinds": [nm.KIND_MAIL],
                                                                          "#p": [self.pub["alice"]]}]))
        self.assertEqual(event["pubkey"], self.pub["godel"])
        self.assertEqual(nm.open_letter(self.alice, event),
                         {"subject": "Report", "body": "All done.", "type": "checkpoint"})
        [request] = [d for d in self.deliveries("nostr")]
        self.assertEqual(request["stage"], "settled")
        [told] = self.deliveries("godel")
        self.assertEqual(told["from"], "acct/nostr")
        self.assertTrue(told["text"].startswith("Sent to alice: Report (event %s)" % event["id"]))

    def test_a_saved_event_is_republished_after_a_crash_and_kept_once(self):
        saved = nm.seal(self.godel, self.pub["alice"], "Once", "only once")
        self.publish(self.godel, saved)        # published, then the gateway died before settling
        self.outbox("acct/godel", "To: alice\nSubject: Once\n\nonly once")
        self.gateway.state["outbound"]["d1"] = {"key": "godel", "to": "alice", "event": saved}
        self.gateway.tick()
        events = self.as_member(self.alice, lambda relay: relay.query([{"kinds": [nm.KIND_MAIL]}]))
        self.assertEqual([e["id"] for e in events], [saved["id"]])
        self.assertEqual(self.deliveries("nostr")[0]["stage"], "settled")
        self.assertEqual(self.gateway.state["outbound"], {})

    def test_a_failed_acknowledgement_republishes_the_same_event(self):
        self.outbox("acct/godel", "To: alice\nSubject: Once\n\nonly once")
        os.environ["FAKE_MAIL_ACK_FAILS"] = "1"
        self.addCleanup(os.environ.pop, "FAKE_MAIL_ACK_FAILS", None)
        self.gateway.tick()
        self.assertEqual(list(self.gateway.state["outbound"]), ["d1"])
        del os.environ["FAKE_MAIL_ACK_FAILS"]
        self.gateway.tick()
        events = self.as_member(self.alice, lambda relay: relay.query([{"kinds": [nm.KIND_MAIL]}]))
        self.assertEqual(len(events), 1)
        self.assertEqual(self.deliveries("nostr")[0]["stage"], "settled")
        self.assertEqual(self.gateway.state["outbound"], {})

    def test_a_changed_resend_is_a_conflict_and_not_retried(self):
        mail = nm.seal(self.alice, self.pub["godel"], "Hello", "x")
        self.publish(self.alice, mail)
        self.gateway.tick()
        # The same event rendered for another recipient is a conflict: kept as
        # handled, never retried and never delivered a second time.
        (self.directory / "state.json").unlink()
        gateway = gw.Gateway(self.directory / "gateway.toml")
        gateway.inbound = {"godel": "lila"}
        gateway.tick()
        self.assertIn(mail["id"], gateway.state["recent"])
        self.assertEqual([d["to"] for d in self.deliveries()], ["godel"])

    def test_malformed_relay_answers_do_not_stop_a_key(self):
        mail = nm.seal(self.alice, self.pub["godel"], "Still here", "x")

        class Relay:
            def __init__(self, url, secret):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *error):
                return False

            async def subscribe(self, filters):
                # A second end of stored events, an event without an id and
                # one with a text created_at, then the mail.
                for item in (("event", {"kind": 3838, "created_at": "soon"}), ("eose", None), ("eose", None),
                             ("event", {"kind": 3838}), ("event", mail)):
                    yield item
                raise asyncio.CancelledError

            async def publish(self, event):
                return True, ""

        original = gw.nm.Relay
        gw.nm.Relay = Relay
        self.addCleanup(setattr, gw.nm, "Relay", original)
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(self.gateway.receive("godel", "godel", live=True))
        self.assertEqual([d["id"] for d in self.deliveries("godel")], [mail["id"]])

    def test_bad_requests_are_reported_and_settled(self):
        self.outbox("acct/lila", "To: alice\n\nx", ident="no-key")
        self.outbox("acct/godel", "To: nobody\n\nx", ident="no-recipient")
        self.outbox("acct/godel", "Subject: missing recipient\n\nx", ident="no-to")
        self.gateway.tick()
        self.assertTrue(all(d["stage"] == "settled" for d in self.deliveries("nostr")))
        told = {d["to"]: [] for d in self.deliveries() if d["from"] == "acct/nostr"}
        for d in self.deliveries():
            if d["from"] == "acct/nostr":
                told[d["to"]].append(d["text"])
        # Replies go to the address in [replies], else to the sender's own inbox.
        self.assertEqual(sorted(told), ["acct/lila", "godel"])
        self.assertIn("Not sent: acct/lila has no Nostr key at this gateway", told["acct/lila"])
        self.assertIn("Not sent: nobody is not in the registry", told["godel"])
        self.assertIn("Not sent: no To: header", told["godel"])
        self.assertEqual(self.as_member(self.alice, lambda relay: relay.query([{"kinds": [nm.KIND_MAIL]}])), [])

    def test_another_accounts_agents_use_the_key_given_to_that_account(self):
        self.outbox("aimama/codex", "To: alice\nSubject: From another account\n\nhello")
        self.gateway.tick()
        [event] = self.as_member(self.alice, lambda relay: relay.query([{"kinds": [nm.KIND_MAIL]}]))
        self.assertEqual(event["pubkey"], self.pub["godel"])

    def test_the_relay_serves_each_key_only_its_own_mail(self):
        mail = nm.seal(self.alice, self.pub["godel"], "Private", "for godel")
        self.publish(self.alice, mail)
        seen_by_eve = self.as_member(self.eve, lambda relay: relay.query([{"kinds": [nm.KIND_MAIL]}]))
        seen_by_godel = self.as_member(self.godel, lambda relay: relay.query([{"kinds": [nm.KIND_MAIL]}]))
        self.assertEqual((seen_by_eve, [e["id"] for e in seen_by_godel]), ([], [mail["id"]]))
        refused = self.as_member(self.eve, lambda relay: relay.publish(nm.seal(self.alice, self.pub["godel"], "x", "y")))
        self.assertEqual(refused[0], False)

    def test_keys_readable_by_others_are_refused(self):
        (self.directory / "keys.json").chmod(0o640)
        with self.assertRaises(PermissionError):
            gw.Gateway(self.directory / "gateway.toml")


@unittest.skipUnless(HAVE_CRYPTO, "needs coincurve, cryptography and websockets")
class ServingTests(GatewayTests):
    """The long-running service: live subscriptions, concurrency, receipts, resumption."""

    def setUp(self):
        super().setUp()
        self.lila = secret("lila")
        self.pub["lila"] = nm.public_key(self.lila)
        self.relay.allow.add(self.pub["lila"])
        (self.directory / "registry.json").write_text(json.dumps(
            {name: {"pubkey_hex": self.pub[name]} for name in ("alice", "godel", "lila")}))
        keys = self.directory / "keys.json"
        keys.write_text(json.dumps({name: {"seckey_hex": secret(name), "pubkey_hex": self.pub[name]}
                                    for name in ("godel", "lila")}))
        keys.chmod(0o600)
        config = (self.directory / "gateway.toml").read_text().replace(
            '[inbound]\ngodel = "godel"\n', '[inbound]\ngodel = "godel"\nlila = "lila"\n')
        (self.directory / "gateway.toml").write_text(config)
        self.gateway = gw.Gateway(self.directory / "gateway.toml")
        self.stopper = None
        self.addCleanup(self.stop)

    def serve(self):
        loop = asyncio.new_event_loop()
        started = threading.Event()
        holder = {}

        def runner():
            asyncio.set_event_loop(loop)
            holder["task"] = loop.create_task(self.gateway.serve())
            started.set()
            try:
                loop.run_until_complete(holder["task"])
            except asyncio.CancelledError:
                pass
            finally:
                loop.close()
        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        started.wait(5)

        def stop():
            loop.call_soon_threadsafe(holder["task"].cancel)
            thread.join(15)
        self.stopper = stop
        self.eventually(lambda: self.relay.subscribers(self.pub["godel"]) and self.relay.subscribers(self.pub["lila"]))

    def stop(self):
        if self.stopper:
            stopper, self.stopper = self.stopper, None
            stopper()

    def eventually(self, condition, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(0.05)
        self.fail("condition not met within %ss" % timeout)

    def test_live_mail_arrives_and_receipts_reach_the_sender(self):
        self.serve()
        mail = nm.seal(self.alice, self.pub["godel"], "Live", "arrives without polling")
        self.publish(self.alice, mail)
        delivery = self.eventually(lambda: [d for d in self.deliveries("godel") if d["id"] == mail["id"]])[0]
        self.assertEqual(delivery["from"], "acct/nostr.alice")
        # godel answers through the outbox; alice's receipt is believed, eve's forgery is not
        self.outbox("acct/godel", "To: alice\nSubject: Re: Live\nIn-Reply-To: %s\n\nthanks" % mail["id"])
        sent = self.eventually(lambda: self.as_member(self.alice, lambda r: r.query(
            [{"kinds": [nm.KIND_MAIL], "authors": [self.pub["godel"]]}])))
        reply = sent[0]
        self.assertEqual(nm.tag_values(reply, "e"), [mail["id"]])
        self.publish(self.eve, nm.sign(self.eve, nm.KIND_RECEIPT, [["e", reply["id"]], ["p", self.pub["godel"]],
                                                                  ["k", "3838"], ["ack", "read"]], "+"))
        self.publish(self.alice, nm.receipt(self.alice, reply, "delivered"))
        told = self.eventually(lambda: [d for d in self.deliveries("godel") if d["text"].startswith("Delivered by")])
        self.assertEqual(told[0]["text"], "Delivered by alice: Re: Live (event %s)" % reply["id"])
        time.sleep(0.5)
        self.assertEqual([d for d in self.deliveries("godel") if d["text"].startswith("Read by")], [])

    def test_a_key_shut_out_catches_up_without_loss_or_duplicates(self):
        self.serve()
        self.relay.shut_out(self.pub["godel"])
        mails = [nm.seal(self.alice, self.pub["godel"], "While away %d" % i, "x") for i in range(3)]
        for mail in mails:
            self.publish(self.alice, mail)
        time.sleep(1.5)
        self.assertEqual(self.deliveries("godel"), [])
        self.relay.let_in(self.pub["godel"])
        self.eventually(lambda: len(self.deliveries("godel")) == 3)
        time.sleep(1.0)
        self.assertEqual(sorted(d["id"] for d in self.deliveries("godel")), sorted(m["id"] for m in mails))

    def test_a_slow_channel_never_delays_another(self):
        os.environ["FAKE_MAIL_SLOW"] = "godel"
        self.addCleanup(os.environ.pop, "FAKE_MAIL_SLOW", None)
        self.serve()
        self.publish(self.alice, nm.seal(self.alice, self.pub["godel"], "slow", "x"))
        self.publish(self.alice, nm.seal(self.alice, self.pub["lila"], "fast", "y"))
        lila = self.eventually(lambda: self.deliveries("lila"))[0]
        godel = self.eventually(lambda: self.deliveries("godel"))[0]
        self.assertLess(lila["at"], godel["at"])

    def test_stopping_keeps_the_read_positions(self):
        self.serve()
        mail = nm.seal(self.alice, self.pub["lila"], "Before the stop", "x")
        self.publish(self.alice, mail)
        self.eventually(lambda: self.deliveries("lila"))
        self.stop()
        saved = json.loads((self.directory / "state.json").read_text())
        self.assertGreaterEqual(saved["since"]["lila"], mail["created_at"])
        self.gateway = gw.Gateway(self.directory / "gateway.toml")
        self.serve()
        time.sleep(1.0)
        self.assertEqual(len(self.deliveries("lila")), 1)


if __name__ == "__main__":
    unittest.main()
