"""Nostr events, NIP-44 v2 and the agent mail format, against the official vectors.

Vectors: `nip44.vectors.json` is the file NIP-44 names (paulmillr/nip44, sha256
269ed0f69e4c192512cc779e78c555090cebc7c785b609e338a62afc3ce25040), and
`bip340-vectors.csv` is BIP-340's `test-vectors.csv` (bitcoin/bips, BSD-2-Clause).
"""
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
VECTORS = Path(__file__).resolve().parent / "fixtures" / "nostr"
HAVE_CRYPTO = all(importlib.util.find_spec(name) for name in ("coincurve", "cryptography", "websockets"))

if HAVE_CRYPTO:
    import nostr_mail as nm


def keys(n):
    """Deterministic test secrets."""
    return hashlib.sha256(b"nostr-mail-test-%d" % n).hexdigest()


@unittest.skipUnless(HAVE_CRYPTO, "needs coincurve, cryptography and websockets")
class Nip44(unittest.TestCase):
    def setUp(self):
        self.v2 = json.loads((VECTORS / "nip44.vectors.json").read_text())["v2"]

    def test_conversation_keys(self):
        for case in self.v2["valid"]["get_conversation_key"]:
            self.assertEqual(nm.conversation_key(case["sec1"], case["pub2"]).hex(), case["conversation_key"])

    def test_invalid_conversation_keys_are_refused(self):
        for case in self.v2["invalid"]["get_conversation_key"]:
            with self.assertRaises(Exception, msg=case["note"]):
                nm.conversation_key(case["sec1"], case["pub2"])

    def test_message_keys(self):
        vectors = self.v2["valid"]["get_message_keys"]
        conversation = bytes.fromhex(vectors["conversation_key"])
        for case in vectors["keys"]:
            key, nonce, mac = nm.message_keys(conversation, bytes.fromhex(case["nonce"]))
            self.assertEqual((key.hex(), nonce.hex(), mac.hex()),
                             (case["chacha_key"], case["chacha_nonce"], case["hmac_key"]))

    def test_padded_lengths(self):
        for length, padded in self.v2["valid"]["calc_padded_len"]:
            self.assertEqual(nm.padded_length(length), padded)

    def test_encrypt_and_decrypt(self):
        for case in self.v2["valid"]["encrypt_decrypt"]:
            conversation = nm.conversation_key(case["sec1"], nm.public_key(case["sec2"]))
            self.assertEqual(conversation.hex(), case["conversation_key"])
            self.assertEqual(nm.conversation_key(case["sec2"], nm.public_key(case["sec1"])), conversation)
            payload = nm.encrypt(case["plaintext"], conversation, bytes.fromhex(case["nonce"]))
            self.assertEqual(payload, case["payload"])
            self.assertEqual(nm.decrypt(payload, conversation), case["plaintext"])

    def test_long_messages(self):
        for case in self.v2["valid"]["encrypt_decrypt_long_msg"]:
            plaintext = case["pattern"] * case["repeat"]
            self.assertEqual(hashlib.sha256(plaintext.encode()).hexdigest(), case["plaintext_sha256"])
            conversation = bytes.fromhex(case["conversation_key"])
            payload = nm.encrypt(plaintext, conversation, bytes.fromhex(case["nonce"]))
            self.assertEqual(hashlib.sha256(payload.encode()).hexdigest(), case["payload_sha256"])
            self.assertEqual(nm.decrypt(payload, conversation), plaintext)

    def test_invalid_lengths_and_payloads_are_refused(self):
        for length in self.v2["invalid"]["encrypt_msg_lengths"]:
            with self.assertRaises(ValueError):
                nm.encrypt("x" * length, bytes(32))
        for case in self.v2["invalid"]["decrypt"]:
            with self.assertRaises(Exception, msg=case["note"]):
                nm.decrypt(case["payload"], bytes.fromhex(case["conversation_key"]))


@unittest.skipUnless(HAVE_CRYPTO, "needs coincurve, cryptography and websockets")
class Bip340(unittest.TestCase):
    def test_signing_and_verification_vectors(self):
        """Nostr signs 32-byte identifiers; vectors for other message lengths are out of scope."""
        coincurve = nm._curve()
        with (VECTORS / "bip340-vectors.csv").open() as stream:
            rows = [row for row in csv.DictReader(stream) if len(row["message"]) == 64]
        self.assertGreaterEqual(len(rows), 15)
        for row in rows:
            message, signature = bytes.fromhex(row["message"]), bytes.fromhex(row["signature"])
            expected = row["verification result"] == "TRUE"
            try:
                valid = coincurve.PublicKeyXOnly(bytes.fromhex(row["public key"])).verify(signature, message)
            except Exception:  # noqa: BLE001 - an unusable key fails verification
                valid = False
            self.assertEqual(valid, expected, row["index"] + " " + row["comment"])
            if row["secret key"]:
                key = coincurve.PrivateKey(bytes.fromhex(row["secret key"]))
                self.assertEqual(key.public_key_xonly.format().hex(), row["public key"].lower())
                self.assertEqual(key.sign_schnorr(message, bytes.fromhex(row["aux_rand"])).hex(),
                                 row["signature"].lower())


@unittest.skipUnless(HAVE_CRYPTO, "needs coincurve, cryptography and websockets")
class Events(unittest.TestCase):
    def test_signed_events_verify_and_any_change_breaks_them(self):
        event = nm.sign(keys(1), 1, [["t", "x"]], "hello", created_at=1700000000)
        self.assertTrue(nm.verify(event))
        for field, value in (("content", "hellp"), ("tags", [["t", "y"]]), ("created_at", 1700000001),
                             ("kind", 2), ("pubkey", nm.public_key(keys(2)))):
            self.assertFalse(nm.verify({**event, field: value}), field)
        other = nm.sign(keys(2), 1, [["t", "x"]], "hello", created_at=1700000000)
        self.assertFalse(nm.verify({**event, "sig": other["sig"]}))
        self.assertFalse(nm.verify({**event, "created_at": True}))

    def test_signing_is_randomized_and_the_identifier_is_not(self):
        first = nm.sign(keys(1), 1, [], "same", created_at=1)
        second = nm.sign(keys(1), 1, [], "same", created_at=1)
        self.assertEqual(first["id"], second["id"])
        self.assertNotEqual(first["sig"], second["sig"])
        self.assertTrue(nm.verify(first) and nm.verify(second))


@unittest.skipUnless(HAVE_CRYPTO, "needs coincurve, cryptography and websockets")
class Mail(unittest.TestCase):
    def setUp(self):
        self.alice, self.bob, self.eve = keys(1), keys(2), keys(3)
        self.message = nm.seal(self.alice, nm.public_key(self.bob), "Hello", "Body.", kind="limits",
                               task="t1", reply_to="ab" * 32, created_at=1700000000)

    def test_the_recipient_and_the_author_open_it_and_no_one_else(self):
        letter = {"subject": "Hello", "body": "Body.", "type": "limits", "task": "t1"}
        self.assertTrue(nm.verify(self.message))
        self.assertEqual(nm.open_letter(self.bob, self.message), letter)
        self.assertEqual(nm.open_letter(self.alice, self.message), letter)
        self.assertIsNone(nm.open_letter(self.eve, self.message))
        self.assertEqual(nm.tag_values(self.message, "e"), ["ab" * 32])
        self.assertIn(["ack", "request"], self.message["tags"])

    def test_tags_name_nothing(self):
        """A type, task or sender name in a tag is the signer's claim and is ignored."""
        tagged = nm.sign(self.alice, nm.KIND_MAIL,
                         self.message["tags"] + [["type", "reset"], ["from_name", "parent"]],
                         self.message["content"], created_at=1700000000)
        self.assertEqual(nm.open_letter(self.bob, tagged)["type"], "limits")

    def test_a_ciphertext_moved_to_another_sender_does_not_open(self):
        """Eve re-signs Alice's ciphertext to Bob under her own key: Bob cannot open it as Eve's."""
        moved = nm.sign(self.eve, nm.KIND_MAIL, self.message["tags"], self.message["content"])
        self.assertTrue(nm.verify(moved))
        self.assertIsNone(nm.open_letter(self.bob, moved))

    def test_receipts(self):
        delivered = nm.receipt(self.bob, self.message, "delivered")
        self.assertTrue(nm.verify(delivered))
        self.assertEqual(nm.receipt_fact(delivered), (self.message["id"], "delivered"))
        self.assertEqual(nm.tag_values(delivered, "p"), [nm.public_key(self.alice)])
        with self.assertRaises(ValueError):
            nm.receipt(self.bob, self.message, "seen")

    def test_registry_names_each_key_by_its_first_entry(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_text(json.dumps({"alice": {"pubkey_hex": nm.public_key(self.alice)},
                                        "alias": {"pubkey_hex": nm.public_key(self.alice)},
                                        "broken": {"pubkey_hex": "XYZ"}}))
            self.assertEqual(nm.load_registry(path), {nm.public_key(self.alice): "alice"})


if __name__ == "__main__":
    unittest.main()
