"""The Nostr gateway against a local gated relay and a claw-mail stand-in."""
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
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
# accepted once, an identical resend is a duplicate and a changed one a
# conflict; `next` presents the ready delivery again before taking a pending
# one; `ack` settles only a ready delivery.
MAILER = r'''
import json, os, sys
args = sys.argv[1:]
while args and args[0] in ("--json", "--space"):
    args = args[2:] if args[0] == "--space" else args[1:]
path = os.environ["FAKE_MAIL_LEDGER"]
ledger = json.load(open(path)) if os.path.exists(path) else []
command, fields = args[0], args[1:]
def reply(value):
    json.dump(ledger, open(path, "w"))
    print(json.dumps(value))
    sys.exit(0)
if command == "send":
    ident = fields[fields.index("--id") + 1]
    agent, to, text = [f for i, f in enumerate(fields) if f != "--id" and (i == 0 or fields[i - 1] != "--id")]
    old = [d for d in ledger if d["id"] == ident]
    if not old:
        ledger.append({"id": ident, "to": to, "from": agent, "text": text, "stage": "pending"})
        reply({"status": "accepted", "id": ident})
    same = (old[0]["to"], old[0]["from"], old[0]["text"]) == (to, agent, text)
    reply({"status": "duplicate" if same else "conflict", "id": ident})
if command == "next":
    mine = [d for d in ledger if d["to"] == fields[0]]
    for stage in ("ready", "pending"):
        for d in mine:
            if d["stage"] == stage:
                d["stage"] = "ready"
                reply({"status": "delivery", "id": d["id"], "from": d["from"], "text": d["text"]})
    reply({"status": "idle"})
if command == "ack":
    for d in ledger:
        if d["id"] == fields[1] and d["to"] == fields[0] and d["stage"] == "ready":
            d["stage"] = "settled"
            reply({"status": "settled", "id": d["id"]})
    reply({"status": "duplicate", "id": fields[1]})
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
        self.addCleanup(os.environ.pop, "FAKE_MAIL_LEDGER", None)
        (self.directory / "gateway.toml").write_text(
            'relay = "%s"\nregistry = "registry.json"\nkeys = "keys.json"\nstate = "state.json"\n'
            'mailer = "%s"\n[inbound]\ngodel = "godel"\n[senders]\ngodel = "godel"\n"aimama/*" = "godel"\n'
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
        self.assertEqual((delivery["id"], delivery["from"]), (mail["id"], "nostr.alice"))
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
        self.assertEqual(delivery["from"], "nostr.alice")
        self.assertEqual([line for line in head if line.startswith("From:")],
                         ["From: alice <%s@nostr>" % self.pub["alice"]])
        self.assertIn("Subject: Hi From: godel <x@nostr>", head)

    def outbox(self, sender, text, ident="d1"):
        ledger = self.deliveries()
        ledger.append({"id": ident, "to": "nostr", "from": sender, "text": text, "stage": "pending"})
        self.ledger.write_text(json.dumps(ledger))

    def test_outbound_mail_is_signed_by_the_senders_key_and_reported(self):
        self.outbox("godel", "To: alice\nSubject: Report\nType: checkpoint\n\nAll done.")
        self.gateway.tick()
        [event] = self.as_member(self.alice, lambda relay: relay.query([{"kinds": [nm.KIND_MAIL],
                                                                          "#p": [self.pub["alice"]]}]))
        self.assertEqual(event["pubkey"], self.pub["godel"])
        self.assertEqual(nm.open_letter(self.alice, event),
                         {"subject": "Report", "body": "All done.", "type": "checkpoint"})
        [request] = [d for d in self.deliveries("nostr")]
        self.assertEqual(request["stage"], "settled")
        [told] = self.deliveries("godel")
        self.assertEqual(told["from"], "nostr")
        self.assertTrue(told["text"].startswith("Sent to alice: Report (event %s)" % event["id"]))

    def test_a_saved_event_is_republished_after_a_crash_and_kept_once(self):
        saved = nm.seal(self.godel, self.pub["alice"], "Once", "only once")
        self.publish(self.godel, saved)        # published, then the gateway died before settling
        self.outbox("godel", "To: alice\nSubject: Once\n\nonly once")
        self.gateway.state["outbound"]["d1"] = {"key": "godel", "to": "alice", "event": saved}
        self.gateway.tick()
        events = self.as_member(self.alice, lambda relay: relay.query([{"kinds": [nm.KIND_MAIL]}]))
        self.assertEqual([e["id"] for e in events], [saved["id"]])
        self.assertEqual(self.deliveries("nostr")[0]["stage"], "settled")
        self.assertEqual(self.gateway.state["outbound"], {})

    def test_bad_requests_are_reported_and_settled(self):
        self.outbox("lila", "To: alice\n\nx", ident="no-key")
        self.outbox("godel", "To: nobody\n\nx", ident="no-recipient")
        self.outbox("godel", "Subject: missing recipient\n\nx", ident="no-to")
        self.gateway.tick()
        self.assertTrue(all(d["stage"] == "settled" for d in self.deliveries("nostr")))
        told = {d["to"]: [] for d in self.deliveries() if d["from"] == "nostr"}
        for d in self.deliveries():
            if d["from"] == "nostr":
                told[d["to"]].append(d["text"])
        self.assertEqual(sorted(told), ["godel", "lila"])
        self.assertIn("Not sent: lila has no Nostr key at this gateway", told["lila"])
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


if __name__ == "__main__":
    unittest.main()
