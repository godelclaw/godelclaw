"""nostr_mail_cetta.metta and its Prime spelling against nostr_mail.py, byte for byte.

All run the same inputs: the Python module with its randomness replaced by a
fixed sequence, the PeTTa module and nostr_mail_prime.metta on CETTA_BIN, an
engine with lib/crypto.  Prime prints booleans as True and False; they are
compared in PeTTa's spelling.
Events are compared as their compact JSON, letters, receipt facts, tag values
and registries as JSON, verification as a boolean.  Keys are test keys.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import nostr_mail  # noqa: E402

sys.path.insert(0, str(ROOT / "tests"))

SECRETS = {name: "%064x" % value for name, value in
           (("alice", 0xa11ce), ("bob", 0xb0b), ("carol", 0xca401))}
TEXTS = ["hello", "", "line\nbreak\ttab\rreturn", 'quote " and back\\slash', "πλάτων ✓ 🦄 ąčę",
         "control \x01\x1f and del \x7f", "  separators  ", "x" * 300]

HELPERS = r'''
(= (compact $value) (json:stringify-python-with $value ((ensure-ascii False) (separators "," ":"))))
(= (letter-json ()) "null")
(= (letter-json (nostr:letter $subject $body $type $task))
   (compact (JsonObject (letter-pairs (("subject" $subject) ("body" $body) ("type" $type) ("task" $task))))))
(= (letter-pairs $fields)
   (if (== $fields ()) ()
     (let* ((($key $value) (car-atom $fields)) ($rest (letter-pairs (cdr-atom $fields))))
       (if (== $value ()) $rest (cons-atom (JsonPair $key $value) $rest)))))
(= (fact-json ()) "null")
(= (fact-json (nostr:receipted $message $state)) (compact (JsonArray ($message $state))))
(= (registry-json $members)
   (compact (JsonObject (registry-pairs $members))))
(= (registry-pairs $members)
   (if (== $members ()) ()
     (let (nostr:member-name $key $name) (car-atom $members)
       (cons-atom (JsonPair $key $name) (registry-pairs (cdr-atom $members))))))
'''


def literal(text):
    """A PeTTa string literal: the reader has escapes for these five and
    takes every other character, control characters included, as written."""
    escapes = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t"}
    return '"' + "".join(escapes.get(c, c) for c in text) + '"'


def term(value):
    """A JSON value as the engine's JSON terms."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "JsonNull"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return literal(value)
    if isinstance(value, list):
        return "(JsonArray (%s))" % " ".join(term(item) for item in value)
    return "(JsonObject (%s))" % " ".join("(JsonPair %s %s)" % (literal(k), term(v)) for k, v in value.items())


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class FixedRandom:
    """secrets.token_bytes for nostr_mail: a recorded sequence, so the PeTTa
    side can be given the same bytes in the same order."""

    def __init__(self):
        self.issued, self.counter = [], 0

    def __call__(self, count):
        self.counter += 1
        value = bytes([(self.counter * 37 + i) % 256 for i in range(count)])
        self.issued.append(value.hex())
        return value


MODULES = {"petta": "src/nostr_mail_cetta.metta", "prime": "src/nostr_mail_prime.metta"}


def petta_booleans(line):
    return re.sub(r"(?<![\w:\"-])(True|False)(?![\w:-])", lambda m: m.group(1).lower(), line)


def boolean(test, value):
    """A boolean literal as the test's dialect spells it."""
    return ("true" if value else "false") if test.dialect == "petta" else ("True" if value else "False")


def run_program(test, body):
    """The RESULT lines of a program in the test's dialect, booleans in PeTTa's spelling."""
    with tempfile.TemporaryDirectory() as directory:
        program = Path(directory) / "case.metta"
        program.write_text("!(import! &self %s)\n" % json.dumps(str(ROOT / MODULES[test.dialect]))
                           + HELPERS + body, encoding="utf-8")
        result = subprocess.run([test.binary, "--lang", test.dialect, str(program)], cwd=ROOT,
                                capture_output=True, encoding="utf-8", errors="replace", timeout=300)
    test.assertEqual(result.returncode, 0, result.stderr[-2000:])
    return [petta_booleans(line[len("(RESULT "):-1]) for line in result.stdout.split("\n")
            if line.startswith("(RESULT ")]


class NativeNostrMail:
    dialect = "petta"

    @classmethod
    def setUpClass(cls):
        binary = os.environ.get("CETTA_BIN")
        if not binary or not Path(binary).is_file():
            raise unittest.SkipTest("set CETTA_BIN to an engine with lib/crypto")
        cls.binary = binary

    def run_metta(self, expressions):
        lines = run_program(self, "".join("!(println! (RESULT %s))\n" % e for e in expressions))
        self.assertEqual(len(lines), len(expressions), lines[-3:])
        return [json.loads(line) if line.startswith('"') else line for line in lines]

    def test_event_ids(self):
        pubkey = nostr_mail.public_key(SECRETS["alice"])
        cases = [(pubkey, 1700000000 + i, kind, [["p", pubkey], ["t", text]], text)
                 for i, text in enumerate(TEXTS) for kind in (1, 3838)]
        got = self.run_metta(["(nostr:event-id %s %d %d %s %s)" % (literal(p), t, k, term(tags), literal(c))
                              for p, t, k, tags, c in cases])
        want = [nostr_mail.event_id({"pubkey": p, "created_at": t, "kind": k, "tags": tags, "content": c})
                for p, t, k, tags, c in cases]
        self.assertEqual(got, want)

    def test_signed_events_byte_for_byte(self):
        random = FixedRandom()
        with mock.patch.object(nostr_mail.secrets, "token_bytes", random):
            events = [nostr_mail.sign(SECRETS["bob"], kind, [["p", "ab" * 32], ["x", text]], text, 1761470400 + kind)
                      for kind in (1, 7, 3838) for text in TEXTS[:5]]
        got = self.run_metta(["(compact (nostr:sign-with %s %d %s %s %d %s))" % (
            literal(SECRETS["bob"]), e["kind"], term(e["tags"]), literal(e["content"]), e["created_at"], literal(aux))
            for e, aux in zip(events, random.issued)])
        self.assertEqual(got, [compact(e) for e in events])

    def test_verification(self):
        with mock.patch.object(nostr_mail.secrets, "token_bytes", FixedRandom()):
            good = nostr_mail.sign(SECRETS["carol"], 1, [["t", "x"]], "content", 1761470400)
        variants = [good]
        for key, value in (("content", "changed"), ("created_at", 1761470401), ("created_at", 1761470400.0),
                           ("created_at", True), ("created_at", "1761470400"), ("kind", 2), ("tags", [["t", 1]]),
                           ("tags", "x"), ("pubkey", nostr_mail.public_key(SECRETS["alice"])),
                           ("id", good["id"].upper()), ("sig", "00" * 64), ("sig", good["sig"][:-2] + "00")):
            variants.append(dict(good, **{key: value}))
        variants.append({k: v for k, v in good.items() if k != "sig"})
        got = self.run_metta(["(nostr:verify %s)" % term(e) for e in variants])
        self.assertEqual(got, ["true" if nostr_mail.verify(e) else "false" for e in variants])
        self.assertEqual(got[0], "true")

    def test_tag_values(self):
        event = {"tags": [["e", "a"], ["p", "b"], ["e"], ["e", "c", "", "reply"], ["E", "d"], ["e", "f"]]}
        got = self.run_metta(["(compact (JsonArray (nostr:tag-values %s %s)))" % (term(event), literal(name))
                              for name in ("e", "p", "q")])
        self.assertEqual(got, [compact(nostr_mail.tag_values(event, name)) for name in ("e", "p", "q")])

    def test_letters_sealed_and_opened(self):
        bob = nostr_mail.public_key(SECRETS["bob"])
        random = FixedRandom()
        variants = [dict(), dict(kind="task"), dict(task="t-1"), dict(kind="", task=""),
                    dict(reply_to="ef" * 32), dict(ack=False), dict(kind="note", task="t-2", reply_to="12" * 32)]
        sealed, expressions = [], []
        with mock.patch.object(nostr_mail.secrets, "token_bytes", random):
            for i, options in enumerate(variants):
                text = TEXTS[i % len(TEXTS)]
                nonce = bytes([i + 1] * 32)
                event = nostr_mail.seal(SECRETS["alice"], bob, "subject " + text, "body " + text,
                                        created_at=1761470400 + i, nonce=nonce, **options)
                sealed.append(event)
                expressions.append("(compact (nostr:seal-with %s %s %s %s %s %s %s %s %d %s %s))" % (
                    literal(SECRETS["alice"]), literal(bob), literal("subject " + text), literal("body " + text),
                    literal(options["kind"]) if "kind" in options else "()",
                    literal(options["task"]) if "task" in options else "()",
                    literal(options["reply_to"]) if "reply_to" in options else "()",
                    boolean(self, options.get("ack") is not False),
                    1761470400 + i, literal(nonce.hex()), literal(random.issued[-1])))
        got = self.run_metta(expressions)
        self.assertEqual(got, [compact(e) for e in sealed])

        readers = ("bob", "alice", "carol")
        tampered = dict(sealed[1], content=sealed[1]["content"][:-4] + "AAAA")
        untagged = dict(sealed[2], tags=[t for t in sealed[2]["tags"] if t[0] != "encrypted"])
        wrong_kind = dict(sealed[3], kind=1)
        events = sealed + [tampered, untagged, wrong_kind]
        got = self.run_metta(["(letter-json (nostr:open-letter %s %s))" % (literal(SECRETS[r]), term(e))
                              for e in events for r in readers])
        want = [compact(nostr_mail.open_letter(SECRETS[r], e)) if nostr_mail.open_letter(SECRETS[r], e) else "null"
                for e in events for r in readers]
        self.assertEqual(got, want)

    def test_receipts(self):
        bob = nostr_mail.public_key(SECRETS["bob"])
        random = FixedRandom()
        with mock.patch.object(nostr_mail.secrets, "token_bytes", random):
            message = nostr_mail.seal(SECRETS["alice"], bob, "s", "b", created_at=1761470400, nonce=b"\x07" * 32)
            receipts = [nostr_mail.receipt(SECRETS["bob"], message, state, 1761470500 + i)
                        for i, state in enumerate(("delivered", "read"))]
        got = self.run_metta(["(compact (nostr:receipt-with %s %s %s %d %s))" % (
            literal(SECRETS["bob"]), term(message), literal(state), 1761470500 + i, literal(aux))
            for i, (state, aux) in enumerate(zip(("delivered", "read"), random.issued[1:]))])
        self.assertEqual(got, [compact(r) for r in receipts])
        self.assertEqual(self.run_metta(["(nostr:receipt-with %s %s \"seen\" 1 %s)" % (
            literal(SECRETS["bob"]), term(message), literal("00" * 32))]), ["()"])
        facts = receipts + [message, dict(receipts[0], tags=[["e", "x"], ["ack", "seen"]]),
                            dict(receipts[0], tags=[["ack", "read"]]), dict(receipts[0], kind=1)]
        got = self.run_metta(["(fact-json (nostr:receipt-fact %s))" % term(e) for e in facts])
        self.assertEqual(got, [compact(list(nostr_mail.receipt_fact(e))) if nostr_mail.receipt_fact(e) else "null"
                               for e in facts])

    def test_registry(self):
        alice, bob = nostr_mail.public_key(SECRETS["alice"]), nostr_mail.public_key(SECRETS["bob"])
        registry = {"alice": {"pubkey_hex": alice}, "bob": {"pubkey_hex": bob}, "bob-again": {"pubkey_hex": bob},
                    "broken": {"pubkey_hex": "xyz"}, "upper": {"pubkey_hex": alice.upper()},
                    "flat": "nope", "missing": {}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_text(json.dumps(registry))
            got = self.run_metta(["(registry-json (nostr:load-registry %s))" % literal(str(path))])
            self.assertEqual(got, [compact(nostr_mail.load_registry(path))])


class PrimeSpelling(unittest.TestCase):
    def test_prime_spelling_is_current(self):
        root, binary = os.environ.get("CETTA_ROOT"), os.environ.get("CETTA_BIN")
        if not root or not binary:
            self.skipTest("set CETTA_ROOT to a CeTTa checkout and CETTA_BIN to its engine")
        result = subprocess.run([sys.executable, str(ROOT / "scripts/nostr_prime_spelling.py"), "--cetta-root", root,
                                 "--binary", binary, "--check"], capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class NativeNostrMailPeTTa(NativeNostrMail, unittest.TestCase):
    dialect = "petta"


class NativeNostrMailPrime(NativeNostrMail, unittest.TestCase):
    dialect = "prime"


class NativeRelay:
    """The native relay client against the gated relay fixture, beside nostr_mail.Relay."""

    @classmethod
    def setUpClass(cls):
        binary = os.environ.get("CETTA_BIN")
        if not binary or not Path(binary).is_file():
            raise unittest.SkipTest("set CETTA_BIN to an engine with lib/crypto and WebSockets")
        try:
            import websockets  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("the relay fixture needs the websockets package")
        cls.binary = binary

    def run_metta(self, body):
        return run_program(self, body + "\n")

    def test_sign_in_publish_and_query(self):
        from nostr_relay_native import chosen
        import asyncio
        alice, bob = (nostr_mail.public_key(SECRETS[n]) for n in ("alice", "bob"))
        foreign = nostr_mail.sign(SECRETS["bob"], 1, [], "not alice's", None)
        with chosen({alice, bob}) as relay:
            got = self.run_metta(
                "!(let* (($relay (nostr:relay-open %s %s 5000))"
                " ($sealed (nostr:seal %s %s \"subject\" \"body\" \"task\" () () %s))"
                " ($ok (nostr:publish $relay $sealed 5000)) ($stolen (nostr:publish $relay %s 5000))"
                " ($again (nostr:publish $relay $sealed 5000))"
                " ($closed (nostr:relay-close $relay)))"
                " (println! (RESULT $ok $stolen $again)))"
                % (literal(relay.url), literal(SECRETS["alice"]), literal(SECRETS["alice"]), literal(bob),
                   boolean(self, True), term(foreign)))
            self.assertEqual(got, ['(nostr:ok true "") (nostr:ok false "restricted: only your own events")'
                                   ' (nostr:ok true "duplicate: already have it")'])
            query = [{"kinds": [3838], "#p": [bob]}]

            async def python_query():
                async with nostr_mail.Relay(relay.url, SECRETS["bob"]) as connection:
                    return await connection.query(query)
            want = asyncio.run(python_query())
            self.assertEqual(len(want), 1)
            got = self.run_metta(
                "!(let* (($relay (nostr:relay-open %s %s 5000)) ($events (nostr:query $relay (%s) 5000))"
                " ($closed (nostr:relay-close $relay)))"
                " (println! (RESULT (compact (JsonArray $events)) (letter-json (nostr:open-letter %s (car-atom $events))))))"
                % (literal(relay.url), literal(SECRETS["bob"]), " ".join(term(q) for q in query),
                   literal(SECRETS["bob"])))
            self.assertEqual(got, ["%s %s" % (json.dumps(compact(want)),
                                              json.dumps(compact(nostr_mail.open_letter(SECRETS["bob"], want[0]))))])
            got = self.run_metta("!(println! (RESULT (nostr:relay-open %s %s 5000)))"
                                 % (literal(relay.url), literal(SECRETS["carol"])))
            self.assertEqual(got, ['(nostr:relay-failed refused "restricted: sign-in refused")'])


class NativeRelayPeTTa(NativeRelay, unittest.TestCase):
    dialect = "petta"


class NativeRelayPrime(NativeRelay, unittest.TestCase):
    dialect = "prime"


if __name__ == "__main__":
    unittest.main()
