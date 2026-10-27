"""Native index layout, preserving typed values and signed source ordinals."""
import hashlib
import os
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
CETTA_ROOT = Path(os.environ.get("CETTA_CHANNEL_ROOT", ROOT.parent / "CeTTa-ours"))
WORKER = os.environ.get("CETTA_VALUE_WORKER")
if WORKER:
    sys.path.insert(0, str(CETTA_ROOT / "tools"))
    from space_service_client import E, S, encode, decode
    from value_worker_client import Worker


@unittest.skipUnless(WORKER, "set CETTA_VALUE_WORKER to a native store build")
class ProviderIndexTests(unittest.TestCase):
    def setUp(self):
        self.worker = Worker(WORKER, ROOT / "src/provider_indexes_cetta.metta",
                             "provider-index:call", language="petta")
        self.addCleanup(self.worker.close)

    @staticmethod
    def suffix(ordinal):
        return struct.pack(">Q", (ordinal & ((1 << 64) - 1)) ^ (1 << 63))

    @staticmethod
    def request(value, ordinal=1):
        return E("ProviderRequestV1", value, ordinal, 1.0, 20, "pending", S("JsonNull"),
                 "provider", "coding", S("JsonNull"), "owner", "fingerprint")

    def test_candidate_prefix_hashes_complete_typed_wire_values(self):
        values = [S("JsonNull"), "JsonNull", "", False, 0, True, S("True"), 1, 1.0,
                  "λ\0value", b"invalid-\xff-text", E("SqlBlobV1", b"invalid-\xff-text"),
                  "long-value-" + "x" * 100_000]
        prefixes = []
        for value in values:
            with self.subTest(value=repr(value)[:50]):
                answer = self.worker.call(E("Prefix", S("ProviderRequestV1"), "id", value))
                self.assertEqual(answer[0], S("ProviderIndexRangeV1"))
                prefix = b"index/v1/requests/id/" + hashlib.sha256(encode(value)).hexdigest().encode() + b"/"
                upper = prefix + b"\xff" * 8 + b"\0"
                self.assertEqual(encode(answer), encode(E("ProviderIndexRangeV1", prefix, upper)))
                prefixes.append(prefix)
                for ordinal in (-(1 << 63), -1, 0, 1, (1 << 63) - 1):
                    self.assertTrue(prefix <= prefix + self.suffix(ordinal) < upper)
        self.assertEqual(len(set(prefixes)), len(values))

    def test_every_entry_points_to_the_original_ordinal_and_field(self):
        for ordinal in (-(1 << 63), -7, 0, 1, (1 << 63) - 1):
            row = self.request("id", ordinal)
            answer = self.worker.call(E("Entries", row))
            self.assertEqual(answer[0], S("ProviderIndexEntriesV1"))
            expected = {"id": "id", "status": "pending", "provider": "provider",
                        "mode": "coding", "owner": "owner"}
            self.assertEqual(len(answer[1]), len(expected))
            primary = b"requests/" + self.suffix(ordinal)
            for entry, (column, value) in zip(answer[1], expected.items()):
                tag, key, reference = entry
                prefix = ("index/v1/requests/" + column + "/" +
                          hashlib.sha256(encode(value)).hexdigest() + "/").encode()
                self.assertEqual(tag, S("ProviderIndexEntryV1"))
                self.assertEqual(encode(key), encode(prefix + self.suffix(ordinal)))
                raw = reference.encode("utf-8") if isinstance(reference, str) else reference
                self.assertEqual(encode(decode(raw)), encode(E("ProviderIndexV1", primary)))

    def test_event_compound_identity_and_sequence_are_used(self):
        row = E("ProviderEventV1", "call", "usage", 97, 1.5, "{}")
        fields = {"identity": E("ProviderEventIdentityV1", "call", "usage"),
                  "call_id": "call", "event": "usage"}
        answer = self.worker.call(E("Entries", row))
        self.assertEqual(len(answer[1]), 3)
        for entry, (column, value) in zip(answer[1], fields.items()):
            prefix = ("index/v1/llm_call_history/" + column + "/" +
                      hashlib.sha256(encode(value)).hexdigest() + "/").encode()
            self.assertEqual(encode(entry[1]), encode(prefix + self.suffix(97)))
        for column, value in fields.items():
            self.assertEqual(self.worker.call(E("Field", row, column)), E("ProviderIndexFieldValueV1", value))

    def test_frontiers_and_bad_fields_are_explicit(self):
        self.assertEqual(self.worker.call(E("Entries", E("ProviderSequenceV1", "requests", 100))),
                         E("ProviderIndexEntriesV1", ()))
        self.assertEqual(self.worker.call(E("Entries", E("ProviderRequestV1", "short"))),
                         E("ProviderIndexRefusedV1", S("record-shape")))
        self.assertEqual(self.worker.call(E("Prefix", S("ProviderRequestV1"), "unknown", "x")),
                         E("ProviderIndexRefusedV1", S("unknown-column")))
        self.assertEqual(self.worker.call(E("Field", self.request("x"), "unknown")),
                         S("ProviderIndexFieldMissingV1"))


if __name__ == "__main__":
    unittest.main()
