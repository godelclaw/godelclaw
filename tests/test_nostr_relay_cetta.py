"""The native relay (scripts/nostr_relay.metta) against the Python fixture relay.

ParityTests replays one session against both relays with the same signed
events and compares everything the clients see: sign-ins refused for each
reason and accepted, publishes refused for each reason and accepted,
duplicates, stored answers to filters of every kind (ids, authors, kinds,
since, until, #p and #e, limit, several filters at once) with what each key
may read, live delivery to matching subscriptions only, ephemeral events
relayed and not stored, CLOSE ending delivery, and deletions that remove only
their author's events.

NativeTests cover what the fixture does not have: stored events outlasting a
restart, an allowlist edited while the relay runs, filters and messages it
refuses, and the limits on subscriptions and filters.

Run with CETTA_BIN naming a CeTTa with WebSocket listeners, lib/crypto and
lib/sqlite, and with coincurve, cryptography and websockets installed.
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
BINARY = Path(os.environ.get("CETTA_BIN", "cetta"))

try:
    import nostr_mail as nm
    from nostr_relay_fixture import GatedRelay
    from nostr_relay_native import NativeRelay
    from websockets.sync.client import connect
    HAVE_CRYPTO = True
except ImportError:  # pragma: no cover - reported as a skip
    HAVE_CRYPTO = False

KEYS = {name: __import__("hashlib").sha256(b"relay-test-" + name.encode()).hexdigest()
        for name in ("alice", "bob", "carol", "eve")}


class Client:
    """One connection, reading the relay's messages in order."""

    def __init__(self, url, timeout=10):
        self.url, self.timeout = url, timeout
        self.socket = connect(url, max_size=1 << 22, open_timeout=timeout)
        self.waiting = []
        self.challenge = self.next(lambda m: m[0] == "AUTH")[1]

    def close(self):
        self.socket.close()

    def send(self, message):
        self.socket.send(json.dumps(message))

    def next(self, wanted, timeout=None):
        """The first message, held or new, that `wanted` accepts."""
        for i, message in enumerate(self.waiting):
            if wanted(message):
                return self.waiting.pop(i)
        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError("no matching message")
            message = json.loads(self.socket.recv(timeout=left))
            if wanted(message):
                return message
            self.waiting.append(message)

    def auth(self, secret, url=None, challenge=None, created_at=None, kind=nm.KIND_AUTH if HAVE_CRYPTO else 0,
             tamper=False):
        event = nm.sign(secret, kind, [["relay", url or self.url], ["challenge", challenge or self.challenge]], "",
                        created_at)
        if tamper:
            event["sig"] = event["sig"][:-2] + ("00" if event["sig"][-2:] != "00" else "11")
        self.send(["AUTH", event])
        answer = self.next(lambda m: m[0] == "OK" and m[1] == event["id"])
        return answer[2], answer[3]

    def publish(self, event):
        self.send(["EVENT", event])
        answer = self.next(lambda m: m[0] == "OK" and m[1] == event.get("id", ""))
        return answer[2], answer[3]

    def query(self, sub, filters, close=True):
        """The ids of the stored answer, or ("CLOSED", reason)."""
        self.send(["REQ", sub] + filters)
        ids = []
        while True:
            message = self.next(lambda m: m[0] in ("EVENT", "EOSE", "CLOSED") and m[1] == sub)
            if message[0] == "EVENT":
                ids.append(message[2]["id"])
            elif message[0] == "EOSE":
                break
            else:
                return ("CLOSED", message[2])
        if close:
            self.send(["CLOSE", sub])
        return ids

    def live(self, sub, timeout=3):
        """The id of the next live event of a subscription."""
        return self.next(lambda m: m[0] == "EVENT" and m[1] == sub, timeout)[2]["id"]


def sign(name, kind, tags, content, created_at):
    return nm.sign(KEYS[name], kind, tags, content, created_at)


@unittest.skipUnless(HAVE_CRYPTO and BINARY.is_file(), "needs coincurve, cryptography, websockets and CETTA_BIN")
class ParityTests(unittest.TestCase):
    def setUp(self):
        self.pub = {name: nm.public_key(secret) for name, secret in KEYS.items()}
        now = int(time.time())
        p = lambda name: ["p", self.pub[name]]
        self.now = now
        self.events = {
            "note": sign("alice", 1, [p("bob")], "hello bob", now - 500),
            "mail": sign("alice", nm.KIND_MAIL, [p("bob")], "sealed", now - 400),
            "private": sign("alice", 1, [], "to myself", now - 300),
            "carol": sign("alice", 1, [p("carol")], "hello carol", now - 200),
            "same-time-a": sign("alice", 1, [p("bob")], "tie a", now - 100),
            "same-time-b": sign("alice", 1, [p("bob")], "tie b", now - 100),
            # carol's key in a tag of another name: no #p condition matches it.
            "decoy": sign("alice", 1, [p("bob"), ["x", self.pub["carol"]]], "decoy", now - 50),
            "bobs": sign("bob", 1, [p("alice")], "from bob", now - 450),
            "future": sign("alice", 1, [], "later", now + 7200),
            "auth-kind": sign("alice", nm.KIND_AUTH, [], "", now),
            "live-mail": sign("alice", nm.KIND_MAIL, [p("bob")], "live", now),
            "live-other": sign("alice", nm.KIND_MAIL, [p("carol")], "not for bob", now),
            "ephemeral": sign("alice", 20001, [p("bob")], "passing", now),
            "after-close": sign("alice", nm.KIND_MAIL, [p("bob")], "after close", now + 1),
            "marker": sign("alice", 1, [p("bob")], "marker", now + 2),
        }
        self.events["receipt"] = sign("bob", nm.KIND_RECEIPT, [["e", self.events["mail"]["id"]], p("alice")], "+",
                                      now - 350)
        self.events["deletion"] = sign("alice", 5, [["e", self.events["mail"]["id"]], ["e", self.events["bobs"]["id"]]],
                                       "", now + 3)
        self.ids = {event["id"]: name for name, event in self.events.items()}
        # Its fields no longer hash to its id, which stays note's.
        bad = dict(self.events["note"])
        bad["content"] = "changed"
        self.events["bad-id"] = bad

    def named(self, ids):
        return [self.ids.get(i, i) for i in ids] if isinstance(ids, list) else ids

    def session(self, relay):
        """What the clients of one session see, with ids named."""
        seen = []
        note = lambda what, value: seen.append((what, value))
        e = self.events
        a = Client(relay.url)
        note("before sign-in: publish", a.publish(e["note"]))
        note("before sign-in: query", a.query("s0", [{}]))
        for name, options in (("wrong challenge", {"challenge": "0" * 32}), ("wrong relay", {"url": "ws://elsewhere"}),
                              ("stale", {"created_at": self.now - 3600}), ("bad signature", {"tamper": True}),
                              ("wrong kind", {"kind": 1})):
            client = Client(relay.url)
            note("auth: " + name, client.auth(KEYS["alice"], **options))
            client.close()
        outsider = Client(relay.url)
        note("auth: not allowlisted", outsider.auth(KEYS["eve"]))
        note("outsider: query", outsider.query("s1", [{}]))
        outsider.close()
        note("auth: alice", a.auth(KEYS["alice"]))
        for name in ("note", "mail", "private", "carol", "same-time-a", "same-time-b", "decoy"):
            note("publish " + name, a.publish(e[name]))
        note("publish note again", a.publish(e["note"]))
        for name in ("bobs", "future", "auth-kind", "bad-id"):
            note("publish " + name, a.publish(e[name]))
        b = Client(relay.url)
        note("auth: bob", b.auth(KEYS["bob"]))
        note("publish bobs", b.publish(e["bobs"]))
        note("publish receipt", b.publish(e["receipt"]))
        queries = {
            "everything": [{}],
            "kinds": [{"kinds": [nm.KIND_MAIL]}],
            "p": [{"#p": [self.pub["bob"]]}],
            "e": [{"#e": [e["mail"]["id"]]}],
            "p of another tag": [{"#p": [self.pub["carol"]]}],
            "x": [{"#x": [self.pub["carol"]]}],
            "authors limit": [{"authors": [self.pub["alice"]], "limit": 2}],
            "limit zero": [{"limit": 0}],
            "unreadable ids": [{"ids": [e["private"]["id"], e["carol"]["id"]]}],
            "readable ids": [{"ids": [e["note"]["id"], e["bobs"]["id"]]}],
            "since until": [{"since": self.now - 450, "until": self.now - 350}],
            "two filters": [{"kinds": [1]}, {"authors": [self.pub["bob"]]}],
            "no match": [{"kinds": [9999]}],
        }
        for name, filters in queries.items():
            note("bob query: " + name, self.named(b.query("q", filters)))
        note("alice query: everything", self.named(a.query("q", [{}])))
        # Live delivery to matching subscriptions only.
        note("live: stored", self.named(b.query("live", [{"kinds": [nm.KIND_MAIL]}], close=False)))
        note("passing: stored", self.named(b.query("passing", [{"kinds": [20001]}], close=False)))
        note("marks: stored", self.named(b.query("marks", [{"kinds": [1], "since": self.now + 2}], close=False)))
        note("publish live-other", a.publish(e["live-other"]))
        note("publish live-mail", a.publish(e["live-mail"]))
        note("live mail", self.named([b.live("live")]))
        note("publish ephemeral", a.publish(e["ephemeral"]))
        note("live ephemeral", self.named([b.live("passing")]))
        note("ephemeral not stored", self.named(b.query("q", [{"kinds": [20001]}])))
        b.send(["CLOSE", "live"])
        # A relay reads a connection's messages in order: once this answer
        # arrives, the CLOSE before it has been read.
        note("sync", b.query("sync", [{"limit": 0}]))
        note("publish after-close", a.publish(e["after-close"]))
        note("publish marker", a.publish(e["marker"]))
        note("marker arrives", self.named([b.live("marks")]))
        note("nothing else arrived", [m for m in b.waiting if m[0] == "EVENT"])
        # Deletions remove only their author's events.
        note("publish deletion", a.publish(e["deletion"]))
        note("after deletion: bob", self.named(b.query("q", [{}])))
        note("after deletion: alice", self.named(a.query("q", [{"kinds": [nm.KIND_MAIL, 5]}])))
        a.close()
        b.close()
        return seen

    def test_native_relay_answers_as_the_fixture_does(self):
        allow = [self.pub[name] for name in ("alice", "bob", "carol")]
        with GatedRelay(allow) as fixture:
            want = self.session(fixture)
            stored_fixture = sorted(self.ids[e["id"]] for e in fixture.stored())
        with NativeRelay(allow, BINARY) as native:
            got = self.session(native)
            stored_native = sorted(self.ids[e["id"]] for e in native.stored())
        self.assertEqual(len(got), len(want))
        for (what, value), (what2, value2) in zip(got, want):
            self.assertEqual((what, value), (what2, value2))
        self.assertEqual(stored_native, stored_fixture)
        # Sanity: the session exercised refusals, answers and live delivery.
        self.assertIn(("publish note again", [True, "duplicate: already have it"]),
                      [(w, list(v)) for w, v in want if isinstance(v, tuple)])


@unittest.skipUnless(HAVE_CRYPTO and BINARY.is_file(), "needs coincurve, cryptography, websockets and CETTA_BIN")
class NativeTests(unittest.TestCase):
    def setUp(self):
        self.pub = {name: nm.public_key(secret) for name, secret in KEYS.items()}
        self.allow = [self.pub["alice"], self.pub["bob"]]

    def test_stored_events_outlast_a_restart(self):
        directory = tempfile.mkdtemp(prefix="nostr-relay-restart-")
        self.addCleanup(__import__("shutil").rmtree, directory, True)
        event = sign("alice", nm.KIND_MAIL, [["p", self.pub["bob"]]], "kept", int(time.time()))
        relay = NativeRelay(self.allow, BINARY, directory=directory).start()
        a = Client(relay.url)
        a.auth(KEYS["alice"])
        self.assertEqual(a.publish(event), (True, ""))
        a.close()
        relay.stop()
        relay.start()
        b = Client(relay.url)
        b.auth(KEYS["bob"])
        self.assertEqual(b.query("q", [{}]), [event["id"]])
        b.close()
        relay.stop()

    def test_an_edited_allowlist_takes_effect_at_once(self):
        with NativeRelay(self.allow, BINARY) as relay:
            bob = Client(relay.url)
            self.assertEqual(bob.auth(KEYS["bob"]), (True, ""))
            relay.shut_out(self.pub["bob"])
            closed = False
            try:
                bob.next(lambda m: False, timeout=5)
            except TimeoutError:
                pass
            except Exception:  # the relay closed the connection
                closed = True
            self.assertTrue(closed)
            again = Client(relay.url)
            self.assertEqual(again.auth(KEYS["bob"]), (False, "restricted: sign-in refused"))
            again.close()
            relay.let_in(self.pub["bob"])
            back = Client(relay.url)
            self.assertEqual(back.auth(KEYS["bob"]), (True, ""))
            back.close()

    def test_refusals_of_malformed_requests(self):
        with NativeRelay(self.allow, BINARY) as relay:
            a = Client(relay.url)
            a.auth(KEYS["alice"])
            a.socket.send("not json")
            self.assertEqual(a.next(lambda m: m[0] == "NOTICE"), ["NOTICE", "invalid: not a JSON array"])
            a.send(["HELLO"])
            self.assertEqual(a.next(lambda m: m[0] == "NOTICE"), ["NOTICE", "invalid: unknown message type"])
            a.send(["REQ", "x" * 65, {}])
            self.assertEqual(a.next(lambda m: m[0] == "NOTICE"), ["NOTICE", "invalid: subscription id"])
            for bad in ({"kinds": ["one"]}, {"ids": [1]}, {"since": "now"}, {"#p": "key"}, [1]):
                self.assertEqual(a.query("bad", [bad]), ("CLOSED", "invalid: filter"))
            self.assertEqual(a.query("many", [{}] * 11), ("CLOSED", "error: too many filters"))
            for i in range(20):
                a.send(["REQ", "s%d" % i, {"kinds": [1]}])
                a.next(lambda m, i=i: m[0] == "EOSE" and m[1] == "s%d" % i)
            self.assertEqual(a.query("s20", [{}]), ("CLOSED", "error: too many subscriptions"))
            self.assertEqual(a.query("s3", [{"kinds": [2]}], close=False), [])
            a.close()


if __name__ == "__main__":
    unittest.main()
