"""The native gateway (scripts/nostr_gateway.metta) against a local gated relay
and a real claw-mail service, and against the Python gateway on the same mail.

The tests run the PeTTa program; a class's dialect selects the program, so
the generated Prime spelling (scripts/nostr_gateway_prime.metta) gets the same
classes once Prime matches a pattern holding a parameter bound to an
expression (an engine defect, reported). Set CETTA_BIN to an engine with
lib/crypto, lib/toml and WebSockets, and CETTA_SPACE_SERVICE to its space
service (default: cetta-space-service beside the engine).
"""
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT / "scripts", Path(__file__).resolve().parent):
    sys.path.insert(0, str(path))
HAVE_CRYPTO = all(importlib.util.find_spec(name) for name in ("coincurve", "cryptography", "websockets"))
BINARY = Path(os.environ.get("CETTA_BIN", "/nonexistent/cetta")).resolve()
SERVICE = Path(os.environ.get("CETTA_SPACE_SERVICE", BINARY.parent / "cetta-space-service")).resolve()
GATEWAYS = {"petta": ROOT / "scripts/nostr_gateway.metta", "prime": ROOT / "scripts/nostr_gateway_prime.metta"}
CLAW_MAIL = ROOT / "scripts/shared_mail.py"
LOGIN = pwd.getpwuid(os.getuid()).pw_name

if HAVE_CRYPTO:
    import nostr_gateway as gw
    import nostr_mail as nm
    from nostr_relay_native import chosen


def secret(name):
    return hashlib.sha256(b"gateway-test-" + name.encode()).hexdigest()


def quoted(text):
    return json.dumps(str(text), ensure_ascii=False)


class Setup:
    """A relay, a claw-mail service and a gateway configuration in one directory."""

    def __init__(self, test, names=("alice", "godel", "eve"), inbound=("godel",), keys=("godel",)):
        self.test = test
        self.dialect = test.dialect
        self.directory = Path(tempfile.mkdtemp(prefix="nostr-native-"))
        test.addCleanup(subprocess.run, ["rm", "-rf", str(self.directory)])
        self.secret = {name: secret(name) for name in names}
        self.pub = {name: nm.public_key(key) for name, key in self.secret.items()}
        self.relay = chosen(self.pub.values()).__enter__()
        test.addCleanup(self.relay.__exit__, None, None, None)
        self.socket = self.directory / "mail.sock"
        self.service = subprocess.Popen([str(SERVICE), "--socket", str(self.socket), "--data", str(self.directory / "store")],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        test.addCleanup(self.stop_service)
        deadline = time.monotonic() + 5
        while not self.socket.exists():
            if self.service.poll() is not None or time.monotonic() > deadline:
                test.fail("the mail service did not start")
            time.sleep(0.01)
        registered = [name for name in names if name != "eve"]
        (self.directory / "registry.json").write_text(json.dumps({n: {"pubkey_hex": self.pub[n]} for n in registered}))
        path = self.directory / "keys.json"
        path.write_text(json.dumps({n: {"seckey_hex": self.secret[n], "pubkey_hex": self.pub[n]} for n in keys}))
        path.chmod(0o600)
        self.config = self.directory / "gateway.toml"
        self.config.write_text(
            'relay = "%s"\nregistry = "registry.json"\nkeys = "keys.json"\nstate = "state.json"\n'
            'socket = "mail.sock"\nmailer = "%s"\n[inbound]\n%s\n[senders]\n"%s/godel" = "godel"\n"%s/codex" = "godel"\n'
            '[replies]\n"%s/godel" = "godel"\n'
            % (self.relay.url, CLAW_MAIL, "\n".join('%s = "%s"' % (k, k) for k in inbound), LOGIN, LOGIN, LOGIN))
        self.seen = {}

    def stop_service(self):
        if self.service.poll() is None:
            self.service.terminate()
            self.service.communicate(timeout=10)

    # ── the relay, as a member ──────────────────────────────────────────────
    def as_member(self, name, action):
        async def run():
            async with nm.Relay(self.relay.url, self.secret[name]) as relay:
                return await action(relay)
        return asyncio.run(run())

    def publish(self, name, event):
        accepted, reason = self.as_member(name, lambda relay: relay.publish(event))
        self.test.assertTrue(accepted, reason)

    def query(self, name, filters):
        return self.as_member(name, lambda relay: relay.query(filters))

    # ── claw-mail ───────────────────────────────────────────────────────────
    def environment(self):
        return {**os.environ, "CETTA_SHARED_MAIL_SOCKET": str(self.socket), "CETTA_BIN": str(BINARY),
                "CLAW_MAIL_CONFIG": str(self.directory / "no-mail.toml")}

    def mail(self, space, agent):
        """Every delivery for an agent so far, taking and settling new ones."""
        program = self.directory / "drain.metta"
        program.write_text(
            '!(import! &self mailbox)\n!(import! &self json)\n!(import! &self %s)\n'
            '(= (drain $acc) (case (mailbox:next %s %s %s) (((mailbox:delivery $id $sender $body) '
            '(let $acked (mailbox:ack %s %s %s $id) (drain (cons-atom (JsonObject ((JsonPair "id" $id) '
            '(JsonPair "from" (shared-mail:sender $sender)) (JsonPair "text" $body))) $acc)))) ($other $acc))))\n'
            '!(println! (json:stringify (JsonArray (drain ()))))\n'
            % (quoted(ROOT / "scripts/shared_mail.metta"), quoted(self.socket), quoted(space), quoted(agent),
               quoted(self.socket), quoted(space), quoted(agent)))
        result = subprocess.run([str(BINARY), "--lang", "petta", "--quiet", str(program)],
                                capture_output=True, text=True, timeout=60)
        lines = [line for line in result.stdout.splitlines() if line.startswith(('"[', "["))]
        self.test.assertEqual(len(lines), 1, result.stdout + result.stderr)
        value = json.loads(lines[0])
        taken = list(reversed(json.loads(value) if isinstance(value, str) else value))
        self.seen.setdefault((space, agent), []).extend(taken)
        return self.seen[(space, agent)]

    def bus(self, agent):
        return self.mail("agent-mail", agent)

    def inbox(self, agent):
        return self.mail("mail@" + LOGIN, agent)

    def outbox(self, agent, text, ident="d1"):
        result = subprocess.run([sys.executable, str(CLAW_MAIL), "--json", "send", agent, LOGIN + "/nostr", text,
                                 "--id", ident], capture_output=True, text=True, env=self.environment(), timeout=60)
        self.test.assertEqual(json.loads(result.stdout.splitlines()[-1])["status"], "accepted", result.stderr)

    # ── the gateways ────────────────────────────────────────────────────────
    def native(self, expect=0):
        result = subprocess.run([str(BINARY), "--lang", self.dialect, "--quiet", str(GATEWAYS[self.dialect]), "--once", "--config",
                                 str(self.config)], capture_output=True, text=True, timeout=240)
        self.test.assertEqual(result.returncode, expect, result.stderr[-3000:])
        return result.stderr

    def python(self):
        saved = os.environ.copy()
        os.environ.update(self.environment())
        try:
            gw.Gateway(self.config).tick()
        finally:
            os.environ.clear()
            os.environ.update(saved)

    def serve(self):
        process = subprocess.Popen([str(BINARY), "--lang", self.dialect, "--quiet", str(GATEWAYS[self.dialect]), "--config",
                                    str(self.config)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

        def stop():
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                process.wait(15)
        self.test.addCleanup(stop)
        return process, stop

    def state(self):
        return json.loads((self.directory / "state.json").read_text())

    def eventually(self, condition, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(0.1)
        self.test.fail("condition not met within %ss" % timeout)


@unittest.skipUnless(HAVE_CRYPTO and BINARY.is_file() and SERVICE.is_file(),
                     "needs coincurve, cryptography, websockets, CETTA_BIN and its space service")
class NativeGatewayTests(unittest.TestCase):
    dialect = "petta"

    def setUp(self):
        self.s = Setup(self)

    def test_inbound_mail_reaches_the_agent_once_with_a_receipt(self):
        s = self.s
        mail = nm.seal(s.secret["alice"], s.pub["godel"], "Hello", "First line.\nSecond.", kind="decision")
        s.publish("alice", mail)
        s.native()
        [delivery] = s.bus("godel")
        self.assertEqual((delivery["id"], delivery["from"]), (mail["id"], LOGIN + "/nostr.alice"))
        self.assertEqual(delivery["text"], gw.render(mail, nm.open_letter(s.secret["godel"], mail), "alice", "godel"))
        receipts = s.query("alice", [{"kinds": [7], "#p": [s.pub["alice"]]}])
        self.assertEqual([(r["pubkey"], nm.receipt_fact(r)) for r in receipts],
                         [(s.pub["godel"], (mail["id"], "delivered"))])
        state = s.state()
        self.assertEqual((state["since"], list(state["recent"]), list(state["receipted"])),
                         ({"godel": mail["created_at"]}, [mail["id"]], [mail["id"]]))
        # Read again, with or without the gateway's memory: still one delivery.
        s.native()
        (s.directory / "state.json").unlink()
        s.native()
        self.assertEqual(len(s.bus("godel")), 1)
        self.assertEqual(list(s.state()["receipted"]), [mail["id"]])

    def test_only_registered_signers_whose_mail_opens_are_kept(self):
        s = self.s
        s.publish("eve", nm.seal(s.secret["eve"], s.pub["godel"], "From an unregistered key", "x"))
        genuine = nm.seal(s.secret["alice"], s.pub["godel"], "Genuine", "y")
        s.publish("eve", nm.sign(s.secret["eve"], nm.KIND_MAIL, genuine["tags"], genuine["content"]))
        # The agent's own key writing to itself is not mail from anyone.
        s.publish("godel", nm.seal(s.secret["godel"], s.pub["godel"], "From myself", "z"))
        log = s.native()
        self.assertEqual(s.bus("godel"), [])
        self.assertEqual(log.count("not kept:"), 3)

    def test_headers_render_as_the_python_gateway_renders_them(self):
        s = self.s
        first = nm.seal(s.secret["alice"], s.pub["godel"], "First", "x", created_at=int(time.time()) - 5)
        mails = [first,
                 nm.seal(s.secret["alice"], s.pub["godel"], "Hi\nFrom: godel <x@nostr>\x07 ",
                         "body\r\nwith CRLF", kind="　decision ", task="t-1\x00", reply_to=first["id"]),
                 nm.seal(s.secret["alice"], s.pub["godel"], "   ", "", kind="", reply_to="not-an-id")]
        tagged = nm.sign(s.secret["alice"], nm.KIND_MAIL, mails[0]["tags"] + [["from_name", "godel"]], mails[0]["content"])
        for mail in mails[1:] + [tagged]:
            s.publish("alice", mail)
        s.publish("alice", mails[0])
        s.native()
        letters = {m["id"]: m for m in mails + [tagged]}
        got = s.bus("godel")
        self.assertEqual(len(got), 4)
        for delivery in got:
            event = letters[delivery["id"]]
            want = gw.render(event, nm.open_letter(s.secret["godel"], event), "alice", "godel")
            self.assertEqual(delivery["text"], want)
        # Oldest first within the key.
        self.assertEqual(got[0]["id"], first["id"])

    def test_outbound_mail_is_signed_by_the_senders_key_and_reported(self):
        s = self.s
        s.outbox("godel", "To: alice\nSubject: Report\nType: checkpoint\n\nAll done.")
        s.native()
        [event] = s.query("alice", [{"kinds": [nm.KIND_MAIL], "#p": [s.pub["alice"]]}])
        self.assertEqual(event["pubkey"], s.pub["godel"])
        self.assertEqual(nm.open_letter(s.secret["alice"], event),
                         {"subject": "Report", "body": "All done.", "type": "checkpoint"})
        [told] = s.bus("godel")
        self.assertEqual((told["id"], told["from"]), ("nostr-outcome-d1", LOGIN + "/nostr"))
        self.assertEqual(told["text"], "Sent to alice: Report (event %s)" % event["id"])
        self.assertEqual(s.inbox("nostr"), [])   # settled: the outbox is empty
        state = s.state()
        self.assertEqual(state["outbound"], {})
        self.assertEqual({k: v for k, v in state["sent"][event["id"]].items() if k != "at"},
                         {"sender": LOGIN + "/godel", "to": "alice", "to_key": s.pub["alice"], "subject": "Report"})
        # A receipt from the addressee is believed; one from another key is not.
        s.publish("eve", nm.sign(s.secret["eve"], nm.KIND_RECEIPT, [["e", event["id"]], ["p", s.pub["godel"]],
                                                                    ["k", "3838"], ["ack", "read"]], "+"))
        s.publish("alice", nm.receipt(s.secret["alice"], event, "delivered"))
        s.native()
        self.assertEqual([d["text"] for d in s.bus("godel")][1:],
                         ["Delivered by alice: Report (event %s)" % event["id"]])

    def test_a_saved_event_is_republished_after_a_crash_and_kept_once(self):
        s = self.s
        saved = nm.seal(s.secret["godel"], s.pub["alice"], "Once", "only once")
        s.publish("godel", saved)        # published, then the gateway died before settling
        s.outbox("godel", "To: alice\nSubject: Once\n\nonly once")
        (s.directory / "state.json").write_text(json.dumps(
            {"since": {}, "recent": {}, "receipted": {}, "sent": {},
             "outbound": {"d1": {"key": "godel", "to": "alice", "to_key": s.pub["alice"], "event": saved}}}))
        s.native()
        events = s.query("alice", [{"kinds": [nm.KIND_MAIL]}])
        self.assertEqual([e["id"] for e in events], [saved["id"]])
        self.assertEqual(s.state()["outbound"], {})
        self.assertEqual([d["text"] for d in s.bus("godel")], ["Sent to alice: Once (event %s)" % saved["id"]])

    def test_bad_requests_are_reported_and_settled(self):
        s = self.s
        requests = {"no-key": ("lila", "To: alice\n\nx"),
                    "no-recipient": ("godel", "To: nobody\n\nx"),
                    "no-to": ("godel", "Subject: missing recipient\n\nx"),
                    "no-blank": ("godel", "To: alice"),
                    "unknown": ("godel", "To: alice\nX-B: 1\nX-A: 2\nx-b: 3\n\nx"),
                    "not-header": ("godel", "To: alice\n : no name\n\nx"),
                    "parent": ("godel", "To: alice\nIn-Reply-To: <abc@nostr>\n\nx")}
        for ident, (agent, text) in requests.items():
            s.outbox(agent, text, ident=ident)
        s.native()
        self.assertEqual(s.inbox("nostr"), [])
        told = {d["id"]: d["text"] for d in s.bus("godel") + s.inbox("lila")}
        self.assertEqual(told["nostr-outcome-no-key"], "Not sent: %s/lila has no Nostr key at this gateway" % LOGIN)
        self.assertEqual([d["id"] for d in s.inbox("lila")], ["nostr-outcome-no-key"])
        for ident, (agent, text) in requests.items():
            if agent != "godel":
                continue
            try:
                fields, _ = gw.parse_outgoing(text)
                problem = "%s is not in the registry" % fields["to"]
            except ValueError as error:
                problem = str(error)
            self.assertEqual(told["nostr-outcome-" + ident], "Not sent: " + problem)
        self.assertEqual(s.query("alice", [{"kinds": [nm.KIND_MAIL]}]), [])

    def test_a_header_name_lowers_as_python_lowers_it(self):
        s = self.s
        s.outbox("godel", "TO: alice\nTASK: t-7\nSubject:  Kelvin \n\nx")
        s.native()
        [event] = s.query("alice", [{"kinds": [nm.KIND_MAIL]}])
        self.assertEqual(nm.open_letter(s.secret["alice"], event), {"subject": "Kelvin", "body": "x", "task": "t-7"})

    def test_an_account_agent_uses_its_own_entry_in_senders(self):
        s = self.s
        s.outbox("codex", "To: alice\nSubject: From codex\n\nhello")
        s.native()
        [event] = s.query("alice", [{"kinds": [nm.KIND_MAIL]}])
        self.assertEqual(event["pubkey"], s.pub["godel"])
        self.assertEqual([d["text"] for d in s.inbox("codex")], ["Sent to alice: From codex (event %s)" % event["id"]])

    def test_an_accounts_wildcard_gives_its_agents_a_key(self):
        s = self.s
        s.config.write_text(s.config.read_text().replace('"%s/codex" = "godel"' % LOGIN, '"%s/*" = "godel"' % LOGIN))
        s.outbox("lila", "To: alice\nSubject: Any agent\n\nhello")
        s.native()
        [event] = s.query("alice", [{"kinds": [nm.KIND_MAIL]}])
        self.assertEqual(event["pubkey"], s.pub["godel"])

    def test_a_changed_resend_is_a_conflict_and_not_retried(self):
        s = self.s
        mail = nm.seal(s.secret["alice"], s.pub["godel"], "Hello", "x")
        s.publish("alice", mail)
        s.native()
        # The same event rendered for another recipient reuses its identity
        # for other text: a conflict, kept as handled and never delivered twice.
        (s.directory / "state.json").unlink()
        s.config.write_text(s.config.read_text().replace('godel = "godel"\n[senders]', 'godel = "lila"\n[senders]'))
        log = s.native()
        self.assertIn("conflict on %s" % mail["id"], log)
        self.assertEqual(list(s.state()["recent"]), [mail["id"]])
        self.assertEqual((len(s.bus("godel")), s.bus("lila")), (1, []))

    def test_keys_readable_by_others_are_refused(self):
        s = self.s
        (s.directory / "keys.json").chmod(0o640)
        log = s.native(expect=1)
        self.assertIn("is readable by others; make it 0600", log)

    def test_the_two_gateways_take_over_each_others_state(self):
        s = self.s
        first = nm.seal(s.secret["alice"], s.pub["godel"], "One", "x", created_at=int(time.time()) - 3)
        s.publish("alice", first)
        s.native()
        second = nm.seal(s.secret["alice"], s.pub["godel"], "Two", "y")
        s.publish("alice", second)
        s.python()
        s.native()
        s.python()
        self.assertEqual([d["id"] for d in s.bus("godel")], [first["id"], second["id"]])
        self.assertEqual(len(s.query("alice", [{"kinds": [7]}])), 2)
        self.assertEqual(s.state()["since"], {"godel": second["created_at"]})


@unittest.skipUnless(HAVE_CRYPTO and BINARY.is_file() and SERVICE.is_file(),
                     "needs coincurve, cryptography, websockets, CETTA_BIN and its space service")
class ParityTests(unittest.TestCase):
    """The same events through the Python gateway, with real claw-mail, and through the native one."""
    dialect = "petta"

    def test_the_native_gateway_delivers_and_reports_as_the_python_one(self):
        native, python = Setup(self), Setup(self)
        alice, godel, eve = native.secret["alice"], native.pub["godel"], native.secret["eve"]
        now = int(time.time())
        first = nm.seal(alice, godel, "First", "x", kind="decision", created_at=now - 10)
        events = [first,
                  nm.seal(alice, godel, "Re:\tfirst ", "body", task="t", reply_to=first["id"], created_at=now - 5),
                  nm.seal(eve, godel, "unregistered", "x"),
                  nm.sign(eve, nm.KIND_MAIL, first["tags"], first["content"]),
                  nm.seal(alice, godel, "same second", "a", created_at=now - 5)]
        requests = [("godel", "To: alice\nSubject: Report\nType: checkpoint\n\nAll done.", "r1"),
                    ("godel", "To: nobody\n\nx", "r2"), ("lila", "To: alice\n\nx", "r3"),
                    ("godel", "To: alice\nIn-Reply-To: %s@nostr\nTask: t\n\nreply" % first["id"], "r4")]
        for setup, run in ((native, native.native), (python, python.python)):
            for event in events:
                setup.publish("eve" if event["pubkey"] == native.pub["eve"] else "alice", event)
            for agent, text, ident in requests:
                setup.outbox(agent, text, ident)
            run()
        strip = lambda deliveries: [(d["id"], d["from"], d["text"]) for d in deliveries]
        sent = lambda setup: {(e["id"] if e["pubkey"] != native.pub["godel"] else "sent", json.dumps(
            nm.open_letter(alice, e), sort_keys=True), json.dumps([t for t in e["tags"]]))
            for e in setup.query("alice", [{"kinds": [nm.KIND_MAIL], "authors": [native.pub["godel"]]}])}
        self.assertEqual(sent(native), sent(python))
        # Inbound deliveries are byte for byte the same; outcome reports differ
        # only in the event identifiers of what each gateway signed.
        native_bus, python_bus = native.bus("godel"), python.bus("godel")
        inbound = lambda deliveries: [d for d in strip(deliveries) if not d[0].startswith("nostr-")]
        self.assertEqual(inbound(native_bus), inbound(python_bus))
        self.assertEqual(len(inbound(native_bus)), 3)
        reports = lambda deliveries: sorted((d[0], d[1], d[2].split(" (event ")[0])
                                            for d in strip(deliveries) if d[0].startswith("nostr-"))
        self.assertEqual(reports(native_bus), reports(python_bus))
        self.assertEqual(strip(native.inbox("lila")), strip(python.inbox("lila")))
        a, b = native.state(), python.state()
        for field in ("since", "recent", "receipted", "outbound"):
            self.assertEqual(a[field], b[field], field)
        clean = lambda state: sorted(tuple(sorted((k, v) for k, v in r.items() if k != "at")) for r in state["sent"].values())
        self.assertEqual(clean(a), clean(b))


@unittest.skipUnless(HAVE_CRYPTO and BINARY.is_file() and SERVICE.is_file(),
                     "needs coincurve, cryptography, websockets, CETTA_BIN and its space service")
class ServingTests(unittest.TestCase):
    """The long-running native service: live subscriptions, the outbox watch, resumption."""
    dialect = "petta"

    def setUp(self):
        self.s = Setup(self, names=("alice", "godel", "lila", "eve"), inbound=("godel", "lila"), keys=("godel", "lila"))

    def subscribed(self, *names):
        return all(self.s.relay.subscribers(self.s.pub[name]) for name in names)

    def test_live_mail_arrives_and_receipts_reach_the_sender(self):
        s = self.s
        process, stop = s.serve()
        s.eventually(lambda: self.subscribed("godel", "lila"))
        mail = nm.seal(s.secret["alice"], s.pub["godel"], "Live", "arrives without polling")
        s.publish("alice", mail)
        delivery = s.eventually(lambda: [d for d in s.bus("godel") if d["id"] == mail["id"]])[0]
        self.assertEqual(delivery["from"], LOGIN + "/nostr.alice")
        # godel answers through the outbox; alice's receipt is believed, eve's forgery is not.
        s.outbox("godel", "To: alice\nSubject: Re: Live\nIn-Reply-To: %s\n\nthanks" % mail["id"])
        sent = s.eventually(lambda: s.query("alice", [{"kinds": [nm.KIND_MAIL], "authors": [s.pub["godel"]]}]))
        reply = sent[0]
        self.assertEqual(nm.tag_values(reply, "e"), [mail["id"]])
        s.publish("eve", nm.sign(s.secret["eve"], nm.KIND_RECEIPT, [["e", reply["id"]], ["p", s.pub["godel"]],
                                                                     ["k", "3838"], ["ack", "read"]], "+"))
        s.publish("alice", nm.receipt(s.secret["alice"], reply, "delivered"))
        told = s.eventually(lambda: [d for d in s.bus("godel") if d["text"].startswith("Delivered by")])
        self.assertEqual(told[0]["text"], "Delivered by alice: Re: Live (event %s)" % reply["id"])
        time.sleep(0.5)
        self.assertEqual([d for d in s.bus("godel") if d["text"].startswith("Read by")], [])
        stop()
        self.assertEqual(process.returncode, -signal.SIGTERM)

    def test_a_key_shut_out_catches_up_without_loss_or_duplicates_and_never_delays_another(self):
        s = self.s
        process, stop = s.serve()
        s.eventually(lambda: self.subscribed("godel", "lila"))
        s.relay.shut_out(s.pub["godel"])
        mails = [nm.seal(s.secret["alice"], s.pub["godel"], "While away %d" % i, "x") for i in range(3)]
        for mail in mails:
            s.publish("alice", mail)
        other = nm.seal(s.secret["alice"], s.pub["lila"], "Meanwhile", "y")
        s.publish("alice", other)
        s.eventually(lambda: s.bus("lila"))
        self.assertEqual(s.bus("godel"), [])
        s.relay.let_in(s.pub["godel"])
        s.eventually(lambda: len(s.bus("godel")) == 3, timeout=40)
        time.sleep(1.0)
        self.assertEqual(sorted(d["id"] for d in s.bus("godel")), sorted(m["id"] for m in mails))
        stop()
        log = process.stderr.read()
        self.assertIn("inbound for godel: ", log)
        self.assertIn("; again in 1s", log)

    def test_stopping_keeps_the_read_positions(self):
        s = self.s
        process, stop = s.serve()
        s.eventually(lambda: self.subscribed("godel", "lila"))
        mail = nm.seal(s.secret["alice"], s.pub["lila"], "Before the stop", "x")
        s.publish("alice", mail)
        s.eventually(lambda: s.bus("lila"))
        stop()
        self.assertGreaterEqual(s.state()["since"]["lila"], mail["created_at"])
        process, stop = s.serve()
        s.eventually(lambda: self.subscribed("godel", "lila"))
        time.sleep(1.0)
        self.assertEqual(len(s.bus("lila")), 1)


if __name__ == "__main__":
    unittest.main()
