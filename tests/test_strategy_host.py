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
        import lifecycle
        running = mock.patch.object(lifecycle, "cognition_enabled", return_value=1)
        running.start(); self.addCleanup(running.stop)
        host.begin(1, "autonomous-trigger", "")

    def capture(self):
        prompt = host.prepare("visible request")
        self.assertEqual(host.capture(prompt, 60), 1)
        return prompt

    def test_answer_status_preserves_other_arrivals_and_chat_destinations(self):
        store = host.ledger()
        store.accept("arrival", "work", kind="message")
        host._focus = "arrival"
        import task_context
        task_context.begin(store, "arrival", "operator-event", None, "room")
        store.link_inputs("arrival", ["telegram-one", "mailbox:agent:peer-ticket"])
        def status():
            with store.transaction() as db:
                return db.execute("SELECT status FROM requests WHERE id='arrival'").fetchone()[0]
        channel = SimpleNamespace(_reply_target=lambda chat="": chat or "primary")
        with mock.patch.dict(sys.modules, {"telegram": channel}):
            host._close_answered_message(["send-telegram-chat", "room", "answer"], "sent message 1")
            self.assertEqual(status(), "active")
            host._close_answered_message(["mailbox-reply", "peer-ticket", "answer"], "MAILED peer id=1")
            self.assertEqual(status(), "active")
            with store.transaction() as db:
                db.execute("DELETE FROM request_inputs WHERE input LIKE 'mailbox:%'")
            host._close_answered_message(["send", "answer"], "sent message 2")
            self.assertEqual(status(), "active")
            host._close_answered_message(["send-telegram-chat", "room", "answer"], "sent message 3")
            self.assertEqual(status(), "addressed")

    def test_selected_provider_window_is_used_without_inventing_a_limit(self):
        import synthetic_llm
        def cached(kind):
            return {"value": {"id":"claude-test","context":1000000}} if kind == "model-window" else {"value":[]}
        with mock.patch.object(synthetic_llm,"current_model",return_value="claude-test"), \
             mock.patch.object(synthetic_llm,"_cached_observation",side_effect=cached), \
             mock.patch.object(synthetic_llm,"refresh_provider_observations_async"):
            self.assertEqual(host.context_capacity(32768),967232)
        with mock.patch.object(synthetic_llm,"_cached_observation",return_value={}), \
             mock.patch.object(synthetic_llm,"refresh_provider_observations_async"):
            self.assertIsNone(host.context_capacity(32768))

    def test_correlated_reply_keeps_its_explicit_goal_parent(self):
        import mailbox_agent, task_context
        store=host.ledger()
        store.accept("goal:review:1","review a topic")
        with store.transaction() as db:
            db.execute("UPDATE requests SET status='waiting-mailbox' WHERE id='goal:review:1'")
        with mock.patch.object(mailbox_agent,"task_arrivals",return_value=[{"task":"goal:review:1","ticket":"review"}]):
            host.begin(2,"channel-event","peer review")
        self.assertEqual(task_context.projection(store,host._focus)["root"],"goal:review:1")

    def test_answered_message_resolves_public_handle_before_closing_its_input(self):
        import mailbox_agent
        store=host.ledger()
        store.accept("mail","work",kind="message")
        store.link_inputs("mail",["mailbox:agent:internal-receive"])
        host._focus="mail"
        box=SimpleNamespace(ticket_for=lambda handle:"internal-receive" if handle=="public-message" else handle)
        with mock.patch.object(mailbox_agent,"_runtime",box):
            host._close_answered_message(["mailbox-reply","public-message","answer"],"MAILED peer message=answer state=published")
        self.assertEqual(store.request_record("mail")["status"],"addressed")

    def test_real_receipt_callback_and_next_prepared_request(self):
        self.capture()
        command = ["shell", "printf witness"]
        self.assertEqual(host.broker_begin(1, [command]), 1)
        self.assertEqual(host.reserve_command(1, command), 1)
        effect_receipts.record_command(1, "returned", command, "witness")
        host.broker_end(1, [["COMMAND_RETURN:", [command, "witness"]]])
        host.begin(2, "continuation-tick", "")
        self.assertEqual(host.capture("alternate context", 60), 1)
        prompt = self.capture()
        self.assertIn("witness", prompt)
        self.assertEqual(host.ledger().projection()["actions"][0]["status"], "returned")



    def test_batch_envelope_keeps_every_emitted_command(self):
        raw = '((command-batch (pin "note") (send "message")))'
        self.assertEqual(host.normalize_response(raw), '((pin "note") (send "message"))')
        commands = [["pin", "note"], ["send", "message"]]
        self.assertEqual(host.batch_limit(5, commands), 2)
        self.capture()
        self.assertEqual(host.broker_begin(1, commands, 2), 1)
        self.assertEqual(host.reserve_command(1, commands[0]), 1)
        host.command_result(1, "returned", commands[0], "pinned")
        self.assertEqual(host.reserve_command(1, commands[1]), 1)
        action = host.ledger().projection()["actions"][0]
        self.assertEqual(json.loads(action["command"])["limit"], 2)


    def test_upstream_profile_retains_original_response(self):
        raw = '((command-batch (pin "note")))'
        with mock.patch.dict(os.environ, {"METTACLAW_OBSERVED_STRATEGY": "0"}):
            self.assertEqual(host.normalize_response(raw), raw)

    def test_count_limit_does_not_decide_dependency_admission(self):
        self.assertEqual(host.batch_limit(1, [["help", "shell"], ["vitals"]]), 2)
        self.assertEqual(host.batch_limit(1, [["shell", "mutation"], ["send", "done"]]), 2)

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
        self.assertEqual(host.capture(host.prepare("reconsider"), 60), 1)
        self.assertEqual(host.broker_begin(2, [["shell", "another mutation"]]), 1)
        self.assertEqual(host.ledger().projection()["actions"][0]["status"], "uncertain")

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


    def test_late_receipt_does_not_cancel_fresh_command(self):
        self.capture()
        old = ["recent-messages", "room", 6]
        self.assertEqual(host.broker_begin(1, [old]), 1)
        self.assertEqual(host.reserve_command(1, old), 1)
        old_ticket = host._ticket
        host._ledger = None; host._booted = False
        host.begin(2, "continuation-tick", "")
        self.capture()
        new = ["shell", "fresh mutation"]
        self.assertEqual(host.broker_begin(2, [new]), 1)
        self.assertTrue(host.ledger().report(old_ticket, "late information"))
        self.assertEqual(host.reserve_command(2, new), 1)



    def test_search_returns_raw_evidence(self):
        self.assertEqual(host.annotate_result(['query','scope'], 'result'), 'result')

    def test_metadata_edits_and_bad_calls_do_not_cancel_the_batch(self):
        self.capture()
        commands = [['read-file','missing'], ['unknown-tool'], ['shell','printf later']]
        self.assertEqual(host.broker_begin(1, commands), 1)
        for command in commands:
            host.ledger().control(router_revision='changed')
            self.assertEqual(host.reserve_command(1, command), 1)
            host.command_result(1, 'returned', command, 'result')
        with host.ledger().transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM command_attempts').fetchone()[0], 3)

    def test_receipts_page_large_results_without_losing_content(self):
        store = host.ledger()
        original = {"disposition": "returned", "result": '🧪 "quoted"\\n' * 3000}
        with store.transaction() as db:
            db.execute("CREATE TABLE command_attempts (ticket TEXT,position INTEGER,command TEXT,result TEXT,PRIMARY KEY(ticket,position))")
            db.execute("INSERT INTO command_attempts VALUES ('receipt',0,?,?)",
                       (host.strategy.encoded(["help", "shell"]), host.strategy.encoded(original)))
        cursor, pieces = "", []
        while True:
            response = host.command_receipts(cursor)
            self.assertLessEqual(len(response.encode()), 8192)
            page = json.loads(response)
            for row in page["receipts"]:
                self.assertEqual(row["id"], "receipt:0")
                pieces.append(row["chunk"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        record = json.loads("".join(pieces))
        self.assertEqual(record["result"], original)

    def test_receipt_cursor_does_not_include_newer_arrivals(self):
        store = host.ledger()
        with store.transaction() as db:
            db.execute("CREATE TABLE command_attempts (ticket TEXT,position INTEGER,command TEXT,result TEXT,PRIMARY KEY(ticket,position))")
            for index in range(2):
                db.execute("INSERT INTO command_attempts VALUES (?,0,'[\"help\"]',?)",
                    (str(index), host.strategy.encoded({"result": "x" * 10000})))
        first = json.loads(host.command_receipts())
        self.assertTrue(first["next_cursor"])
        with store.transaction() as db:
            db.execute("INSERT INTO command_attempts VALUES ('new',0,'[\"help\"]',NULL)")
        page = json.loads(host.command_receipts(first["next_cursor"]))
        self.assertTrue(all(row["id"] != "new:0" for row in page["receipts"]))

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
