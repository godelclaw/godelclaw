"""Exact raw-byte diagnostics via the authorized native primitive.

Python decoding is only the test oracle. Raw native String bytes go directly
from fs:read-text-result to str:utf8-error, never through py-call/JSON transport.
"""
import json
import os
from pathlib import Path
import tempfile
import unittest

from goal_bookkeeping_support import engine, receipt
from test_goal_bookkeeping_candidates import HEADER, encoded, goal, op
import test_goal_bookkeeping_candidates as candidates
import test_goal_bookkeeping_callers as callers

INVALID = [b";; generated\n\xff\n", b"\x80", b"\xc0\xaf", b"\xe0\x80\x80",
           b"\xed\xa0\x80", b"\xf0\x80\x80\x80", b"\xf4\x90\x80\x80",
           b"\xe2", b"\xe2\x82", b"\xf0\x9f\x92", b"\xe2\x82A", b"\xc2A",
           b"valid\r\n\xf0\x9fA", "日本🙂\0".encode() + b"\xe1\x80A"]


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set authorized UTF-8 checkpoint CETTA_BIN")
class Utf8Tests(unittest.TestCase):
    def test_primitive_raw_components_and_exact_messages(self):
        for raw in INVALID + [b"", b"\0", "日本🙂 é\r\n\0".encode()]:
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                (directory / "goals.metta").write_bytes(raw)
                try:
                    raw.decode("utf-8")
                    expected = "StrUtf8WellFormedV1"
                except UnicodeDecodeError as exc:
                    reason = exc.reason.replace(" ", "-")
                    expected = "(StrUtf8ErrorV1 %d %d %d %s)" % (exc.start, exc.end, raw[exc.start], reason)
                program = HEADER + '''!(let ("ok" $raw) (fs:read-text-result (gv:path) 0 "raw")
 (py-call (goal_bookkeeping_probe.text "utf8-components" (repr (str:utf8-error $raw)))))
'''
                result = engine(directory, program)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                actual = json.loads((directory / "observations.jsonl").read_text())["text"]
                receipt("utf8-primitive", {"raw_base64": encoded(raw), "expected": expected, "actual": actual})
                self.assertEqual(actual, expected)

    def test_original_strict_witness_and_multibyte_no_write_outcomes(self):
        for raw in INVALID:
            with self.subTest(raw=raw):
                initial = {"goals.metta": encoded(raw),
                           "goals_kernel.json": encoded('{"streaks": {}, "log_offset": 0, "mood": {}}'),
                           "goals_compost.log": encoded("generated pre-existing compost\n")}
                rows, events, _ = candidates.CandidateTests().compare(initial, [op("kernel"),
                    op("wake", "invalid-utf8-wake"), op("wake", "invalid-utf8-wake"),
                    op("write", goal()), op("drop", "alpha", "retire")])
                self.assertEqual(events, [])
                self.assertTrue(all(row["files"] == rows[0]["files"] for row in rows))
                self.assertTrue(rows[3]["value"][0] == rows[4]["value"][0] == "raises")

    def test_actual_loop_and_skill_utf8_return_error_outcomes(self):
        for raw in (INVALID[0], b"prefix\n\xe2\x82", b"\xed\xa0\x80"):
            with self.subTest(raw=raw):
                rows, events = callers.RealCallerTests().compare({"goals.metta": encoded(raw)}, [
                    {"kind": "boot"}, {"kind": "heartbeat"}, {"kind": "operator", "token": "bad-text"},
                    {"kind": "operator", "token": "bad-text"}, {"kind": "write", "text": goal()},
                    {"kind": "drop", "name": "alpha", "reason": "retire"}])
                self.assertEqual(events, [])
                self.assertTrue(all(row["files"] == rows[0]["files"] for row in rows))

    def test_valid_unicode_and_nul_inventory_bytes(self):
        candidates.CandidateTests().compare({"goals.metta": encoded(";; 日本 🙂 é\0\n" + goal() + "\n")},
            [op("kernel"), op("write", goal("other")), op("kernel")])
