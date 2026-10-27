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
class DomainTests(unittest.TestCase):
    """Outside agents as asi/NAME: their mail arrives from that address, and
    mail written to it is sent to them, natively as in the Python gateway."""
    dialect = "petta"

    def setUp(self):
        self.s = Setup(self)
        self.s.config.write_text('domain = "asi"\n' + self.s.config.read_text())

    def claw_mail(self, s, *arguments):
        result = subprocess.run([sys.executable, str(CLAW_MAIL), "--json", *arguments],
                                capture_output=True, text=True, env=s.environment(), timeout=60)
        return json.loads(result.stdout.splitlines()[-1])

    def written(self, s, agent, to, text, ident):
        self.assertEqual(self.claw_mail(s, "send", agent, to, text, "--id", ident)["status"], "accepted")

    def test_inbound_mail_comes_from_the_domain_address(self):
        s = self.s
        mail = nm.seal(s.secret["alice"], s.pub["godel"], "Hello", "First line.")
        s.publish("alice", mail)
        s.native()
        [delivery] = s.bus("godel")
        self.assertEqual((delivery["id"], delivery["from"]), (mail["id"], "asi/alice"))
        self.assertEqual(delivery["text"], gw.render(mail, nm.open_letter(s.secret["godel"], mail), "alice", "godel"))

    def test_mail_to_a_domain_address_is_sent_and_settled(self):
        s = self.s
        self.written(s, "godel", "asi/alice", "Subject: Report\nType: checkpoint\n\nAll done.", "d1")
        s.native()
        [event] = s.query("alice", [{"kinds": [nm.KIND_MAIL], "#p": [s.pub["alice"]]}])
        self.assertEqual(event["pubkey"], s.pub["godel"])
        self.assertEqual(nm.open_letter(s.secret["alice"], event),
                         {"subject": "Report", "body": "All done.", "type": "checkpoint"})
        [told] = s.bus("godel")
        self.assertEqual(told["text"], "Sent to alice: Report (event %s)" % event["id"])
        self.assertEqual(self.claw_mail(s, "relay-next", "asi", "*")["status"], "idle")
        self.assertEqual(s.state()["outbound"], {})

    def test_every_waiting_name_is_found_however_much_mail_waits(self):
        s = self.s
        program = s.directory / "fill.metta"
        program.write_text(
            '!(import! &self mailbox)\n!(import! &self %s)\n'
            '(= (fill $n) (if (== $n 0) done (let $sent (mailbox:send %s "mail@asi" (format-args "a{}" (cons-atom $n ()))'
            ' "alice" "godel" "x") (fill (- $n 1)))))\n'
            '!(fill 300)\n!(mailbox:send %s "mail@asi" "b1" "bob" "godel" "x")\n'
            '!(println! (shared-mail:relay-names %s (mailbox:open %s "mail@asi") "*"))\n'
            % (quoted(ROOT / "scripts/shared_mail.metta"), quoted(s.socket), quoted(s.socket), quoted(s.socket),
               quoted(s.socket)))
        result = subprocess.run([str(BINARY), "--lang", "petta", "--quiet", str(program)],
                                capture_output=True, text=True, timeout=600)
        self.assertIn('(shared-mail:names ("alice" "bob"))', result.stdout, result.stderr[-2000:])

    def test_both_gateways_agree_in_a_domain(self):
        native, python = self.s, Setup(self)
        python.config.write_text('domain = "asi"\n' + python.config.read_text())
        alice, godel = native.secret["alice"], native.pub["godel"]
        first = nm.seal(alice, godel, "First", "x", created_at=int(time.time()) - 10)
        requests = [("godel", "asi/alice", "Subject: Report\nType: checkpoint\n\nAll done.", "r1"),
                    ("godel", "asi/nobody", "x", "r2"),
                    ("godel", "asi/alice", "Note: not a header\n\njust text", "r3"),
                    ("godel", "asi/alice", "Subject: Re: First\nIn-Reply-To: <%s@nostr>\n\nyes" % first["id"], "r4"),
                    ("godel", "asi/alice", "In-Reply-To: <abc@nostr>\n\nx", "r5"),
                    ("lila", "asi/alice", "x", "r6")]
        for setup, run in ((native, native.native), (python, python.python)):
            setup.publish("alice", first)
            for agent, to, text, ident in requests:
                self.written(setup, agent, to, text, ident)
            run()
        sent = lambda setup: {("sent", json.dumps(nm.open_letter(alice, e), sort_keys=True), json.dumps(e["tags"]))
                              for e in setup.query("alice", [{"kinds": [nm.KIND_MAIL], "authors": [godel]}])}
        self.assertEqual(sent(native), sent(python))
        self.assertEqual(len(sent(native)), 3)
        strip = lambda deliveries: [(d["id"], d["from"], d["text"].split(" (event ")[0]) for d in deliveries]
        native_bus, python_bus = native.bus("godel"), python.bus("godel")
        self.assertEqual(sorted(strip(native_bus)), sorted(strip(python_bus)))
        self.assertIn((first["id"], "asi/alice"), [(d[0], d[1]) for d in strip(native_bus)])
        self.assertEqual(strip(native.inbox("lila")), strip(python.inbox("lila")))
        for setup in (native, python):
            self.assertEqual(self.claw_mail(setup, "relay-next", "asi", "*")["status"], "idle")


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
        # The position moves once the relay has answered the delivered receipt.
        position = lambda: (json.loads((s.directory / "state.json").read_text()) if (s.directory / "state.json").exists()
                            else {}).get("since", {}).get("lila", 0)
        s.eventually(lambda: position() >= mail["created_at"])
        stop()
        self.assertGreaterEqual(s.state()["since"]["lila"], mail["created_at"])
        process, stop = s.serve()
        s.eventually(lambda: self.subscribed("godel", "lila"))
        time.sleep(1.0)
        self.assertEqual(len(s.bus("lila")), 1)


def two_relays(s, reads):
    """A second relay for a Setup: [[relays]] one and two, in that order, and
    the registry's `relays` lists from `reads`."""
    s.relays, s.stopped = [s.relay, chosen(s.pub.values()).__enter__()], set()
    s.test.addCleanup(lambda: [stop_relay(s, i) for i in (1,)])
    s.urls = [relay.url for relay in s.relays]
    registered = [name for name in s.pub if name != "eve"]
    (s.directory / "registry.json").write_text(json.dumps(
        {n: dict({"pubkey_hex": s.pub[n]}, **({"relays": reads[n]} if reads.get(n) else {})) for n in registered}))
    text = s.config.read_text().replace('relay = "%s"\n' % s.urls[0], "")
    s.config.write_text(text + '[[relays]]\nname = "one"\nurl = "%s"\n[[relays]]\nname = "two"\nurl = "%s"\n' % tuple(s.urls))


def stop_relay(s, index):
    if index not in s.stopped:
        s.stopped.add(index)
        s.relays[index].__exit__(None, None, None)


@unittest.skipUnless(HAVE_CRYPTO and BINARY.is_file() and SERVICE.is_file(),
                     "needs coincurve, cryptography, websockets, CETTA_BIN and its space service")
class NativeTwoRelayTests(unittest.TestCase):
    """The native gateway with [[relays]], as test_nostr_gateway's TwoRelayTests
    drive the Python one, and both gateways on the same two relays."""
    dialect = "petta"
    names = ("alice", "carol", "dora", "godel", "eve")
    reads = {"carol": ["two"], "dora": ["elsewhere"]}

    def setUp(self):
        self.s = Setup(self, names=self.names)
        two_relays(self.s, self.reads)

    def on(self, index, name, action):
        async def run():
            async with nm.Relay(self.s.urls[index], self.s.secret[name]) as relay:
                return await action(relay)
        return asyncio.run(run())

    def publish(self, index, name, event):
        accepted, reason = self.on(index, name, lambda relay: relay.publish(event))
        self.assertTrue(accepted, reason)

    def query(self, index, name, filters):
        return self.on(index, name, lambda relay: relay.query(filters))

    def told(self):
        return [d["text"] for d in self.s.bus("godel") if d["id"].startswith("nostr-outcome-")]

    def test_mail_on_either_relay_arrives_once_with_a_position_per_relay(self):
        s = self.s
        first = nm.seal(s.secret["alice"], s.pub["godel"], "Via one", "x")
        second = nm.seal(s.secret["alice"], s.pub["godel"], "Via two", "y")
        self.publish(0, "alice", first)
        self.publish(1, "alice", second)
        s.native()
        s.native()
        self.assertEqual(sorted(d["id"] for d in s.bus("godel")), sorted([first["id"], second["id"]]))
        self.assertEqual(s.state()["since"], {"godel": {s.urls[0]: first["created_at"], s.urls[1]: second["created_at"]}})
        receipts = lambda index: sorted(nm.receipt_fact(r)[0] for r in self.query(index, "alice", [{"kinds": [7]}]))
        self.assertEqual(receipts(0), sorted([first["id"], second["id"]]))
        self.assertEqual(receipts(1), [])

    def test_a_receipt_goes_to_a_relay_its_author_reads(self):
        s = self.s
        two_relays_reads = dict(self.reads, alice=["two"])
        (s.directory / "registry.json").write_text(json.dumps(
            {n: dict({"pubkey_hex": s.pub[n]}, **({"relays": two_relays_reads[n]} if two_relays_reads.get(n) else {}))
             for n in s.pub if n != "eve"}))
        mail = nm.seal(s.secret["alice"], s.pub["godel"], "Read on one", "x")
        self.publish(0, "alice", mail)
        s.native()
        self.assertEqual([d["id"] for d in s.bus("godel")], [mail["id"]])
        self.assertEqual(self.query(0, "alice", [{"kinds": [7]}]), [])
        [receipt] = self.query(1, "alice", [{"kinds": [7]}])
        self.assertEqual(nm.receipt_fact(receipt), (mail["id"], "delivered"))
        self.assertEqual(list(s.state()["receipted"]), [mail["id"]])

    def test_mail_goes_to_the_first_relay_its_recipient_reads(self):
        s = self.s
        s.outbox("godel", "To: alice\nSubject: To all\n\nx", "a1")
        s.outbox("godel", "To: carol\nSubject: To two\n\ny", "c1")
        s.native()
        mine = lambda index, name: [e["id"] for e in self.query(index, name, [{"kinds": [nm.KIND_MAIL]}])]
        self.assertEqual((len(mine(0, "alice")), mine(1, "alice")), (1, []))
        self.assertEqual((mine(0, "carol"), len(mine(1, "carol"))), ([], 1))
        self.assertEqual(sorted(t.split(":")[0] for t in self.told()), ["Sent to alice", "Sent to carol"])

    def test_a_relay_that_refuses_the_key_hands_the_mail_to_the_next(self):
        s = self.s
        s.relays[0].shut_out(s.pub["godel"])
        s.outbox("godel", "To: alice\nSubject: Fallback\n\nx", "f1")
        s.native()
        [event] = self.query(1, "alice", [{"kinds": [nm.KIND_MAIL]}])
        self.assertEqual(self.told(), ["Sent to alice: Fallback (event %s)" % event["id"]])

    def test_no_relay_in_common_is_reported_and_settled(self):
        s = self.s
        s.outbox("godel", "To: dora\nSubject: Nowhere\n\nx", "n1")
        s.native()
        self.assertEqual(self.told(), ["Not sent to dora: none of our relays is one dora reads"])
        self.assertEqual(s.state()["outbound"], {})

    def test_a_relay_down_does_not_stop_the_other(self):
        s = self.s
        mail = nm.seal(s.secret["alice"], s.pub["godel"], "Via two", "y")
        self.publish(1, "alice", mail)
        stop_relay(s, 0)
        s.outbox("godel", "To: alice\nSubject: Around the gap\n\nx", "g1")
        s.native()
        self.assertIn(mail["id"], [d["id"] for d in s.bus("godel")])
        # Its receipt was for relay one, out of reach, so relay two took it.
        [receipt] = self.query(1, "alice", [{"kinds": [7]}])
        self.assertEqual(nm.receipt_fact(receipt), (mail["id"], "delivered"))
        [event] = self.query(1, "alice", [{"kinds": [nm.KIND_MAIL], "authors": [s.pub["godel"]]}])
        self.assertEqual(self.told(), ["Sent to alice: Around the gap (event %s)" % event["id"]])

    def test_with_every_relay_down_the_request_waits(self):
        s = self.s
        for index in (0, 1):
            stop_relay(s, index)
        s.outbox("godel", "To: alice\nSubject: Later\n\nx", "w1")
        s.native()
        self.assertEqual(self.told(), [])
        self.assertEqual(list(s.state()["outbound"]), ["w1"])

    def test_a_late_letter_on_another_relay_is_not_skipped(self):
        s = self.s
        now = int(time.time())
        self.publish(0, "alice", nm.seal(s.secret["alice"], s.pub["godel"], "Recent", "x", created_at=now))
        s.native()
        late = nm.seal(s.secret["alice"], s.pub["godel"], "Late", "y", created_at=now - 3600)
        self.publish(1, "alice", late)
        s.native()
        self.assertIn(late["id"], [d["id"] for d in s.bus("godel")])

    def test_live_mail_arrives_from_either_relay(self):
        s = self.s
        process, stop = s.serve()
        s.eventually(lambda: all(relay.subscribers(s.pub["godel"]) for relay in s.relays))
        mails = [nm.seal(s.secret["alice"], s.pub["godel"], "Live %d" % i, "z") for i in range(2)]
        self.publish(1, "alice", mails[1])
        self.publish(0, "alice", mails[0])
        s.eventually(lambda: sorted(d["id"] for d in s.bus("godel")) == sorted(m["id"] for m in mails))
        stop()

    def test_both_gateways_agree_on_two_relays(self):
        native, python = self.s, Setup(self, names=self.names)
        two_relays(python, self.reads)
        now = int(time.time())
        events = [(0, nm.seal(native.secret["alice"], native.pub["godel"], "One", "x", created_at=now - 20)),
                  (1, nm.seal(native.secret["alice"], native.pub["godel"], "Two", "y", created_at=now - 10)),
                  (1, nm.seal(native.secret["carol"], native.pub["godel"], "Carol", "z", created_at=now - 5))]
        requests = [("To: alice\nSubject: A\n\na", "r1"), ("To: carol\nSubject: C\n\nc", "r2"),
                    ("To: dora\nSubject: D\n\nd", "r3")]
        for setup, run in ((native, native.native), (python, python.python)):
            for index, event in events:
                accepted, reason = asyncio.run(self._publish(setup, index, event))
                self.assertTrue(accepted, reason)
            for text, ident in requests:
                setup.outbox("godel", text, ident)
            run()
        strip = lambda setup: sorted((d["id"], d["from"], d["text"].split(" (event ")[0]) for d in setup.bus("godel"))
        self.assertEqual(strip(native), strip(python))
        a, b = native.state(), python.state()
        for field in ("since", "recent", "receipted", "outbound"):
            self.assertEqual(json.loads(json.dumps(a[field]).replace(native.urls[0], "ONE").replace(native.urls[1], "TWO")),
                             json.loads(json.dumps(b[field]).replace(python.urls[0], "ONE").replace(python.urls[1], "TWO")),
                             field)

    async def _publish(self, setup, index, event):
        name = next(n for n, k in setup.pub.items() if k == event["pubkey"])
        async with nm.Relay(setup.urls[index], setup.secret[name]) as relay:
            return await relay.publish(event)


if __name__ == "__main__":
    unittest.main()
