import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import strategy_host as host
import effect_receipts


class StrategyHostTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.environment = mock.patch.dict(os.environ, {
            "METTACLAW_OBSERVED_STRATEGY": "1",
            "METTACLAW_STRATEGY_PATH": str(self.root / "strategy.sqlite3"),
            "METTACLAW_EFFECT_RECEIPT_PATH": str(self.root / "effects.jsonl"),
        })
        self.environment.start(); self.addCleanup(self.environment.stop)
        globals_patch = mock.patch.multiple(host, _ledger=None, _booted=False,
                                           _turn=None, _sample=None, _ticket=None,
                                           _focus=None, _position=0)
        globals_patch.start(); self.addCleanup(globals_patch.stop)
        channels = mock.patch.dict(sys.modules, {"telegram": SimpleNamespace(_current_batch_update_ids=set())})
        channels.start(); self.addCleanup(channels.stop)
        guard = mock.patch.object(host, "guard", return_value="captured-control")
        guard.start(); self.addCleanup(guard.stop)
        free = mock.patch.object(host, "_stimulus_free", return_value=True)
        free.start(); self.addCleanup(free.stop)
        import lifecycle
        running = mock.patch.object(lifecycle, "cognition_enabled", return_value=1)
        running.start(); self.addCleanup(running.stop)
        host.begin(1, "autonomous-trigger", "")

    def capture(self):
        prompt = host.prepare("visible request")
        self.assertEqual(host.capture(prompt, 60), 1)
        return prompt

    def test_real_receipt_callback_and_next_prepared_request(self):
        self.capture()
        command = ["shell", "printf witness"]
        self.assertEqual(host.broker_begin(1, [command]), 1)
        self.assertEqual(host.reserve_command(1, command), 1)
        effect_receipts.record_command(1, "returned", command, "witness")
        host.broker_end(1, [["COMMAND_RETURN:", [command, "witness"]]])
        host.begin(2, "continuation-tick", "")
        self.assertEqual(host.capture("removed protected context", 60), 0)
        prompt = self.capture()
        self.assertIn("witness", prompt)
        self.assertEqual(host.ledger().projection()["actions"][0]["observed"], 1)

    def test_new_input_withholds_even_nonconversation_effect(self):
        self.capture()
        with mock.patch.object(host, "_stimulus_free", return_value=False):
            self.assertEqual(host.broker_begin(1, [["shell", "mutate"]]), 0)
        self.assertEqual(host.ledger().projection()["actions"], [])

    def test_transformed_advertisement_cannot_grant_a_tool(self):
        self.capture()
        self.assertEqual(host.broker_begin(1, [["invented", "payload"]]), 0)
        self.assertEqual(host.ledger().projection()["actions"], [])

    def test_only_known_independent_observations_batch(self):
        self.assertEqual(host.batch_limit(5, [["help", "shell"], ["vitals"]]), 5)
        self.assertEqual(host.batch_limit(5, [["shell", "mutation"], ["send", "done"]]), 1)

    def test_restart_recovers_complete_saved_result_without_repeating(self):
        self.capture()
        command = ["shell", "printf witness"]
        host.broker_begin(1, [command]); host.reserve_command(1, command)
        effect_receipts.record_command(1, "returned", command, "witness")
        # Crash before broker_end: the saved per-command receipt is sufficient.
        host._ledger = None; host._booted = False
        recovered = host.ledger()
        self.assertEqual(recovered.projection()["actions"][0]["status"], "returned")
        self.assertIn("recovered_results", recovered.projection()["actions"][0]["receipt"])

    def test_restart_keeps_unreturned_effect_uncertain(self):
        self.capture(); host.broker_begin(1, [["shell", "mutation"]])
        host.reserve_command(1, ["shell", "mutation"])
        host._ledger = None; host._booted = False
        self.assertEqual(host.ledger().projection()["actions"][0]["status"], "uncertain")
        host.begin(2, "continuation-tick", "")
        self.assertEqual(host.capture(host.prepare("reconsider"), 60), 0)

    def test_exact_task_checker_finishes_only_its_observed_result(self):
        store = host.ledger()
        store.accept("exact", "specific output", "sha256:" + host.strategy.digest("expected"))
        snap = store.capture("exact", "request", {"advertised": ["help"], "executable": ["help"], "permitted": ["help"]}, 0)
        ticket = store.decide(snap["serial"], {"kind": "command", "request": "exact", "owner": snap["owner"], "tool": "help"}, 0)
        store.report(ticket, "expected")
        self.assertEqual(store.finish_exact_results(), 0)
        result = store.protected("exact")["results"][0]
        store.observe([ticket], {ticket: [result]})
        self.assertEqual(store.finish_exact_results(), 1)
        self.assertFalse(any(r["id"] == "exact" for r in store.active()))

    def test_search_result_carries_incomplete_coverage(self):
        value = json.loads(host.annotate_result(["search", "scope"], ""))
        self.assertFalse(value["complete_coverage"])
        self.assertEqual(value["absence"], "unavailable")

    def test_failed_provider_does_not_drop_presented_result(self):
        self.capture(); command = ["help", "shell"]
        host.broker_begin(1, [command]); host.reserve_command(1, command)
        effect_receipts.record_command(1, "returned", command, "durable witness")
        host.broker_end(1, "durable witness")
        host.begin(2, "continuation-tick", "")
        self.capture(); host.provider_failed()
        host.begin(3, "continuation-tick", "")
        self.assertIn("durable witness", host.prepare("retry after failure"))

    def test_host_fault_reenters_same_iteration_with_fresh_broker_identity(self):
        self.capture()
        first_turn = host.broker_turn(1)
        command = ["shell", "printf first"]
        host.broker_begin(first_turn, [command])
        host.reserve_command(first_turn, command)
        effect_receipts.record_command(first_turn, "returned", command, "first")
        with mock.patch("cognitive_health.turn_failed", return_value=1):
            host.fault()
        host.begin(1, "continuation-tick", "")
        self.assertIn("first", self.capture())
        second_turn = host.broker_turn(1)
        self.assertNotEqual(first_turn, second_turn)
        self.assertEqual(host.broker_begin(first_turn, [["help", "shell"]]), 0)
        self.assertEqual(host.broker_begin(second_turn, [["help", "shell"]]), 1)
        actions = host.ledger().projection()["actions"]
        self.assertEqual([a["serial"] for a in actions], [0, 1])
        self.assertEqual([a["status"] for a in actions], ["returned", "issued"])


if __name__ == "__main__": unittest.main()
