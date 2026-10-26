"""nostr_mail_cetta.metta against nostr_mail.py, byte for byte.

Both run the same inputs: the Python module with its randomness replaced by a
fixed sequence, the PeTTa module on CETTA_BIN, an engine with lib/crypto.
Events are compared as their compact JSON, letters, receipt facts, tag values
and registries as JSON, verification as a boolean.  Keys are test keys.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import nostr_mail  # noqa: E402

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


class NativeNostrMail(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        binary = os.environ.get("CETTA_BIN")
        if not binary or not Path(binary).is_file():
            raise unittest.SkipTest("set CETTA_BIN to an engine with lib/crypto")
        cls.binary = binary

    def run_metta(self, expressions):
        with tempfile.TemporaryDirectory() as directory:
            program = Path(directory) / "case.metta"
            program.write_text("!(import! &self %s)\n" % json.dumps(str(ROOT / "src/nostr_mail_cetta.metta"))
                               + HELPERS + "".join("!(println! (RESULT %s))\n" % e for e in expressions),
                               encoding="utf-8")
            result = subprocess.run([self.binary, "--lang", "petta", str(program)], cwd=ROOT,
                                    capture_output=True, encoding="utf-8", errors="replace", timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        lines = [line[len("(RESULT "):-1] for line in result.stdout.split("\n") if line.startswith("(RESULT ")]
        self.assertEqual(len(lines), len(expressions), result.stdout[-2000:] + result.stderr[-2000:])
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
                    "false" if options.get("ack") is False else "true",
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


if __name__ == "__main__":
    unittest.main()
