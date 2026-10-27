"""Compare useful provider observations with the existing application.

The old ledger is an isolated reference fixture. The native side uses facts
and receipts; no SQL schema, row allocation or formatting is its contract.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_provider_domain_cetta as domain_fixture
ROOT = domain_fixture.ROOT
WORKER = domain_fixture.WORKER
if WORKER:
    sys.path.insert(0, str(ROOT / "src"))
    from space_service_client import E, S, BigInt, encode
    import llm_policy
    import native_lifetime


@unittest.skipUnless(WORKER, "set CETTA_VALUE_WORKER to the native store/math worker")
class ProviderDomainReferenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = domain_fixture.ProviderDomainTests()
        self.fixture.setUp()
        self.addCleanup(lambda: self.assertTrue(self.fixture.doCleanups()))

    @staticmethod
    def atom(value):
        if value is None:
            return S("JsonNull")
        if isinstance(value, dict):
            return E("JsonObject", tuple(E("JsonPair", key, ProviderDomainReferenceTests.atom(item))
                                         for key, item in value.items()))
        if isinstance(value, list):
            return E("JsonArray", tuple(map(ProviderDomainReferenceTests.atom, value)))
        if type(value) is int and not -(1 << 63) <= value < 1 << 63:
            return BigInt(str(value))
        return value

    def normalize(self, raw):
        answer = self.fixture.worker.call(E("Normalize", self.atom(raw)))
        self.assertEqual(answer[0], S("ProviderUsageObservationV2"), answer)
        return answer[1]

    def expected_usage(self, raw):
        evidence = llm_policy.usage_evidence(raw)
        counts = evidence["counts"]
        return self.fixture.usage(*(self.atom(value) if value is not None else None for value in
            (counts["input_tokens"], counts["output_tokens"], counts["cached_tokens"],
             evidence["reported_usage"].get("cache_creation_input_tokens"))))

    def test_existing_normalized_evidence_crosses_the_new_boundary_without_losing_meaning(self):
        cases = [None, [], {}, True, {"prompt_tokens": 0, "completion_tokens": 0},
                 {"prompt_tokens": 100, "completion_tokens": 7, "prompt_tokens_details": {"cached_tokens": 80}},
                 {"input_tokens": 10, "output_tokens": 7, "cache_read_input_tokens": 80, "cache_creation_input_tokens": 12},
                 {"input_tokens": 10, "output_tokens": 7, "cache_read_input_tokens": None},
                 {"input_tokens": 10, "prompt_tokens_details": {"cached_tokens": 11}},
                 {"input_tokens": 1 << 100, "output_tokens": 1 << 80}]
        for field in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            for invalid in (None, True, -1, 1.5, "10"):
                cases.append({"input_tokens": 10, "output_tokens": 7, field: invalid})
        for raw in cases:
            with self.subTest(raw=raw):
                self.fixture.same(self.normalize(raw), self.expected_usage(raw))

    def reference(self, raw_usage, *, uncertain=False):
        f = self.fixture
        database = f.directory / ("uncertain.sqlite3" if uncertain else "ordinary.sqlite3")
        environment = {"HOME": str(f.directory), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                       "METTACLAW_LLM_LEDGER_PATH": str(database)}
        plan = SimpleNamespace(correlation="call", epoch="attempt", cap=4096,
                               activity="", lane="", purpose="coding", guard=None)
        # This reference's unrelated strategy notification is outside the
        # provider storage contract. It must not contact any live service.
        strategy = SimpleNamespace(provider_admitted=lambda ticket: None)
        with patch.dict(os.environ, environment, clear=True), patch.dict(sys.modules, {"strategy_host": strategy}):
            with patch.object(llm_policy.uuid, "uuid4", return_value=SimpleNamespace(hex="call")):
                self.assertEqual(llm_policy.admit("provider", 4096, mode="coding"), "call")
            llm_policy.describe("call", model="model", protocol="fixture", purpose="coding")
            self.assertTrue(native_lifetime.claim(plan))
            native_lifetime.submitted(plan, 1)
            if uncertain:
                self.assertTrue(native_lifetime.settle(plan, "uncertain", reason="fixture-interruption"))
            response = dict(usage=raw_usage, choices=[dict(finish_reason="stop")])
            changed = llm_policy.complete("call", response)
            if changed:
                self.assertTrue(native_lifetime.settle(plan, "completed", "canonical result"))
            duplicate_changed = llm_policy.complete("call", response)
            with closing(sqlite3.connect(database)) as connection:
                request = connection.execute("SELECT status FROM requests WHERE id='call'").fetchone()[0]
                call = connection.execute("SELECT state,result FROM native_calls WHERE id='call'").fetchone()
                usage = connection.execute("SELECT detail FROM llm_call_history WHERE call_id='call' AND event='usage'").fetchall()
        self.assertEqual(len(usage), 1)
        return dict(changed=changed, duplicate_changed=duplicate_changed, request=request,
                    state=call[0], answer=call[1], evidence=json.loads(usage[0][0]))

    def test_ordinary_completion_and_duplicate_match_real_caller_observations(self):
        raw = {"prompt_tokens": 100, "completion_tokens": 7, "prompt_tokens_details": {"cached_tokens": 80}}
        reference = self.reference(raw)
        f = self.fixture
        usage = self.normalize(raw)
        command = f.finish(usage)
        first = f.execute(command, "first")
        duplicate = f.execute(command, "duplicate-delivery")
        first_status = f.worker.call(E("FirstCompletion", first))
        duplicate_status = f.worker.call(E("FirstCompletion", duplicate))
        self.assertEqual(reference["changed"], first_status == S("ProviderFirstCompletionV2"))
        self.assertEqual(reference["duplicate_changed"], duplicate_status == S("ProviderFirstCompletionV2"))
        self.assertEqual(reference["request"], "completed")
        self.assertEqual(reference["state"], "completed")
        # The native metadata keeps an artifact reference. This fixture's
        # explicit resolver maps that identity to the existing result value.
        body = f.value(E("ProviderAnswerKeyV2", "call"))[1]
        self.assertEqual({E("AnswerRef", "answer"): "canonical result"}[body[2]], reference["answer"])
        f.same(f.value(f.usage_key()), E("KnowledgePresentV1", usage))

    def test_late_usage_is_preserved_without_completing_an_uncertain_call(self):
        raw = {"prompt_tokens": 100, "completion_tokens": 7}
        reference = self.reference(raw, uncertain=True)
        f = self.fixture
        f.put([(E("ProviderLeaseKeyV2", "call"), E("ProviderLeaseV2", "attempt", S("ProviderLeaseUnknownV2")))])
        usage = self.normalize(raw)
        result = f.answer(f.finish(usage))
        f.same(result, E("ProviderNotOwnedV2", S("ProviderUsageAcceptedV2")))
        self.assertFalse(reference["changed"])
        self.assertEqual(reference["request"], "unknown")
        self.assertEqual(reference["state"], "uncertain")
        self.assertIsNone(reference["answer"])
        f.same(f.value(E("ProviderAnswerKeyV2", "call")), S("KnowledgeAbsentV1"))
        f.same(f.value(f.usage_key()), E("KnowledgePresentV1", usage))


if __name__ == "__main__":
    unittest.main()
