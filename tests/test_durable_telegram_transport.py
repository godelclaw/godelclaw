#!/usr/bin/env python3
"""The durable Telegram transport against a fake CeTTa channel service.

The fake speaks CWP1 over a private seqpacket socket. It serves delivery and
receipt tasks, records acknowledgments and keyed submissions, and can answer
a submission with receipts, as the real service does after sending.
"""
import importlib
import json
import os
import pathlib
import re
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from channels import telegram  # noqa: E402
import durable_telegram  # noqa: E402

NEXT, RESULT, SUBMIT = 1, 2, 4
IDLE, TASK, STORED, UNKNOWN, UNAVAILABLE = 64, 65, 66, 68, 71


class FakeService:
    def __init__(self, path):
        self.path = path
        self.tasks = []          # [(id, observation)] pending, oldest first
        self.events = []         # ("ack", id) / ("submit", key, body)
        self.submissions = {}
        self.results = {}        # id -> body of its result
        self.answer = None       # key, commands -> list of receipt observations
        self.unavailable = 0     # refuse this many submissions first
        self.lock = threading.Lock()
        self.serial = 0
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.sock.bind(path)
        self.sock.listen(8)
        self.running = True
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def add(self, observation):
        with self.lock:
            self.serial += 1
            task = "t%05d" % self.serial
            self.tasks.append((task, json.dumps(observation)))
            return task

    def reply(self, conn, code, name="", body=""):
        n = name.encode()
        conn.sendall(b"CWP1" + bytes([code, len(n)]) + n + body.encode())

    def serve(self):
        self.sock.settimeout(0.1)
        while self.running:
            try:
                conn, _ = self.sock.accept()
            except (socket.timeout, OSError):
                continue
            with conn:
                packet = conn.recv(70000)
                code, n = packet[4], packet[5]
                name = packet[6:6 + n].decode()
                body = packet[6 + n:].decode()
                self.handle(conn, code, name, body)

    def handle(self, conn, code, name, body):
        with self.lock:
            if code == NEXT:
                ids = [t for t, _ in self.tasks]
                if name and name not in ids:
                    return self.reply(conn, UNKNOWN, name)
                start = ids.index(name) + 1 if name else 0
                if start >= len(self.tasks):
                    return self.reply(conn, IDLE)
                task, observation = self.tasks[start]
                return self.reply(conn, TASK, task, observation)
            if code == RESULT:
                self.tasks = [(t, o) for t, o in self.tasks if t != name]
                self.events.append(("ack", name))
                self.results[name] = body
                return self.reply(conn, STORED, name)
            if code == SUBMIT:
                if self.unavailable:
                    self.unavailable -= 1
                    return self.reply(conn, UNAVAILABLE, name)
                if name in self.submissions:
                    assert self.submissions[name] == body
                    return self.reply(conn, STORED, name)
                self.submissions[name] = body
                self.events.append(("submit", name, body))
                receipts = self.answer(name, json.loads(body)) if self.answer else []
        if code == SUBMIT:
            for receipt in receipts:
                self.add(receipt)
            return self.reply(conn, STORED, name)
        return None

    def close(self):
        self.running = False
        self.thread.join(timeout=2)
        self.sock.close()


def delivered(key, commands):
    return [["delivery", key, i, len(commands), ["delivered", 70 + i]]
            for i in range(len(commands))]


class DurableTransportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        self.environ = mock.patch.dict(os.environ, {
            "METTACLAW_TELEGRAM_HEALTH_PATH": str(base / "health.json"),
            "METTACLAW_TELEGRAM_SERVICE_TIMEOUT": "3",
        })
        self.environ.start()
        self.tg = importlib.reload(telegram)
        self.log = base / "telegram.jsonl"
        self.tg._log_path = str(self.log)
        self.tg._token = "123:synthetic"  # start_telegram sets it; never sent here
        self.service = FakeService(str(base / "service.sock"))
        self.tg._transport = "durable"
        self.tg._durable = durable_telegram.Channel(self.service.path, str(base / "sequence"))
        self.tg._durable_receipts = durable_telegram.Receipts()
        self.outbox_path = str(base / "outbox")
        self.tg._durable_outbox = durable_telegram.Outbox(self.tg._durable, self.outbox_path)
        self.tg._running = True
        self.loop = threading.Thread(target=self.tg._durable_loop, daemon=True)
        self.loop.start()

    def tearDown(self):
        self.tg._running = False
        self.loop.join(timeout=5)
        self.tg._durable_outbox.stop()
        self.service.close()
        self.environ.stop()
        self.tmp.cleanup()

    def wait(self, predicate, seconds=5):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if predicate():
                return
            time.sleep(0.02)
        self.fail("condition not reached")

    def outbound(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_deliveries_are_processed_in_order_then_acknowledged(self):
        order = []
        updates = [{"update_id": 5, "message": {"text": "one"}},
                   {"update_id": 6, "message": {"text": "two"}}]
        original_ack = self.tg._durable.acknowledge

        def ack(task):
            order.append(("ack", task))
            original_ack(task)

        with mock.patch.object(self.tg, "_process_update",
                               side_effect=lambda u: order.append(("process", u["update_id"]))), \
             mock.patch.object(self.tg._durable, "acknowledge", side_effect=ack):
            first = self.service.add(["input", "42.0", "message", "operator", updates[0]])
            second = self.service.add(["input", "42.0", "message", "ordinary", updates[1]])
            self.wait(lambda: len(order) == 4)
        self.assertEqual(order, [("process", 5), ("ack", first), ("process", 6), ("ack", second)])
        self.assertEqual(self.service.tasks, [])

    def test_a_task_offered_again_is_acknowledged_without_handling_it_twice(self):
        seen = []
        refused = []
        original_ack = self.tg._durable.acknowledge

        def lost_once(task):
            if not refused:
                refused.append(task)  # the service stopped before committing
                raise OSError("service stopped")
            original_ack(task)

        with mock.patch.object(self.tg, "_process_update", side_effect=lambda u: seen.append(u["update_id"])), \
             mock.patch.object(self.tg._durable, "acknowledge", side_effect=lost_once), \
             mock.patch.object(self.tg.time, "sleep"):
            self.service.add(["input", "42.0", "message", "ordinary", {"update_id": 9, "message": {}}])
            self.wait(lambda: not self.service.tasks)
        self.assertEqual(seen, [9])
        self.assertEqual(len(refused), 1)

    def test_a_command_is_answered_by_its_own_thread(self):
        with mock.patch.object(self.tg, "_command_answer", return_value="engine: cetta") as answer:
            task = self.service.add(["command", "11", "/engine", "cetta", "42.0"])
            self.wait(lambda: task in self.service.results)
        answer.assert_called_once_with("/engine", "cetta", "42")
        self.assertEqual(self.service.results[task], '["answer","engine: cetta"]')

    def test_a_slow_command_never_holds_up_deliveries(self):
        release = threading.Event()
        seen = []

        def slow(cmd, arg, chat):
            release.wait(5)
            return "late"

        with mock.patch.object(self.tg, "_command_answer", side_effect=slow), \
             mock.patch.object(self.tg, "_process_update", side_effect=lambda u: seen.append(u["update_id"])):
            task = self.service.add(["command", "12", "/quota", "", "42.0"])
            self.service.add(["input", "42.0", "message", "operator", {"update_id": 30, "message": {}}])
            self.wait(lambda: seen == [30])
            self.assertNotIn(task, self.service.results)
            release.set()
            self.wait(lambda: task in self.service.results)
        self.assertEqual(self.service.results[task], '["answer","late"]')

    def test_a_failed_or_silent_command_still_answers(self):
        with mock.patch.object(self.tg, "_command_answer", side_effect=RuntimeError("boom")):
            failed = self.service.add(["command", "13", "/health", "", "42.0"])
            self.wait(lambda: failed in self.service.results)
        self.assertEqual(self.service.results[failed], '["answer","/health failed: RuntimeError"]')
        with mock.patch.object(self.tg, "_command_answer", return_value=None):
            woken = self.service.add(["command", "14", "/wake", "", "42.0"])
            self.wait(lambda: woken in self.service.results)
        self.assertEqual(self.service.results[woken], '["answer","Waking."]')

    def test_an_answer_is_resent_without_running_the_command_again(self):
        calls = []
        original = self.tg._durable.answer

        def lost_once(task, text):
            if not calls:
                calls.append(task)
                raise OSError("service restarting")
            original(task, text)

        with mock.patch.object(self.tg, "_command_answer", return_value="mode: loop") as answer, \
             mock.patch.object(self.tg._durable, "answer", side_effect=lost_once):
            task = self.service.add(["command", "15", "/mode", "loop", "42.0"])
            self.wait(lambda: task in self.service.results)
        answer.assert_called_once()
        self.assertEqual(self.service.results[task], '["answer","mode: loop"]')

    def test_send_waits_for_its_receipt_and_logs_the_message_id(self):
        self.service.answer = delivered
        reply = self.tg.send_message("hello", "42")
        self.assertEqual(reply, "sent message 70 to chat 42")
        (_, key, body), = [e for e in self.service.events if e[0] == "submit"]
        self.assertRegex(key, r"^42\.0\.\d{20}$")
        self.assertEqual(json.loads(body), [["send", "hello", "plain"]])
        self.wait(lambda: any(r.get("message_id") == 70 for r in self.outbound()))
        record, = self.outbound()
        self.assertEqual((record["note"], record["chat_id"], record["text"]), ("own_send", "42", "hello"))

    def test_long_text_is_one_ordered_batch(self):
        self.service.answer = delivered
        text = "a" * 4000 + "\n" + "b" * 1000
        reply = self.tg.send_message(text, "42")
        self.assertEqual(reply, "sent message 70 to chat 42 (in 2 parts: 70, 71)")
        (_, _, body), = [e for e in self.service.events if e[0] == "submit"]
        self.assertEqual(json.loads(body), [["send", "a" * 4000, "plain"], ["send", "b" * 1000, "plain"]])
        self.assertEqual(durable_telegram.chunks("x" * 4097), ["x" * 4096, "x"])

    def test_uncertain_failed_and_rejected_outcomes_are_not_reported_as_sent(self):
        cases = [
            (lambda k, c: [["delivery", k, 0, 1, ["uncertain", "transport"]]], "send uncertain (transport)"),
            (lambda k, c: [["delivery", k, 0, 1, ["failed", 400]]], "send failed: Telegram rejected 400"),
            (lambda k, c: [["delivery", k, 0, 1, ["not-sent"]]], "send failed: the message was not sent"),
            (lambda k, c: [["rejected", k, "action-not-permitted"]],
             "send failed: the Telegram service rejected it (action-not-permitted)"),
        ]
        for chat, (answer, expected) in zip((42, 84, 126, 168), cases):
            # Separate chats: an uncertain delivery holds its own chat.
            self.service.answer = answer
            self.assertTrue(self.tg.send_message("x", str(chat)).startswith(expected), expected)
        self.assertEqual(self.outbound(), [])

    def test_a_late_receipt_is_still_logged(self):
        keys = []
        self.service.answer = lambda k, c: keys.append(k) or []
        with mock.patch.dict(os.environ, {"METTACLAW_TELEGRAM_SERVICE_TIMEOUT": "1"}):
            reply = self.tg.send_message("later", "42")
        self.assertTrue(reply.startswith("send accepted by the Telegram service"), reply)
        self.service.add(["delivery", keys[0], 0, 1, ["delivered", 99]])
        self.wait(lambda: any(r.get("message_id") == 99 for r in self.outbound()))

    def test_sends_while_the_service_is_away_are_queued_then_sent_in_order(self):
        self.service.answer = delivered
        self.service.unavailable = 10**6
        with mock.patch.dict(os.environ, {"METTACLAW_TELEGRAM_SERVICE_TIMEOUT": "1"}):
            first = self.tg.send_message("first", "42")
            second = self.tg.send_message("second", "42")
        self.assertTrue(first.startswith("send queued"), first)
        self.assertTrue(second.startswith("send queued"), second)
        self.service.unavailable = 0
        self.wait(lambda: len([e for e in self.service.events if e[0] == "submit"]) == 2, 15)
        bodies = [json.loads(e[2]) for e in self.service.events if e[0] == "submit"]
        self.assertEqual(bodies, [[["send", "first", "plain"]], [["send", "second", "plain"]]])
        self.wait(lambda: [r["text"] for r in self.outbound()] == ["first", "second"], 15)

    def test_the_outbox_survives_a_client_restart(self):
        self.service.unavailable = 10**6
        key = self.tg._durable.key(42)
        self.tg._durable_outbox.enqueue(key, [["send", "kept", "plain"]], {"kind": "send", "chat": "42",
                                                                          "pieces": ["kept"], "count": 1})
        self.tg._durable_outbox.stop()          # the client process ends here
        self.service.unavailable = 0
        restarted = durable_telegram.Outbox(self.tg._durable, self.outbox_path)
        self.assertEqual(restarted.entries[key]["pieces"], ["kept"])
        self.assertIs(restarted.wait_stored(key, 10), True)
        self.assertEqual(json.loads(self.service.submissions[key]), [["send", "kept", "plain"]])
        restarted.done(key)
        self.assertEqual(durable_telegram.Outbox(self.tg._durable, self.outbox_path).entries, {})
        self.tg._durable_outbox = restarted

    def test_uncertain_delivery_does_not_block_new_messages_or_replay(self):
        self.service.answer = lambda k,c: [['delivery',k,0,1,
            ['uncertain','transport'] if c[0][1]=='lost' else ['delivered',80]]]
        self.assertTrue(self.tg.send_message('lost','42').startswith('send uncertain'))
        self.assertTrue(self.tg.send_message('later','42').startswith('sent message 80'))
        self.assertTrue(self.tg.send_message('elsewhere','84').startswith('sent message 80'))
        restarted=durable_telegram.Outbox(self.tg._durable,self.outbox_path)
        self.assertFalse(restarted.is_held('42.0'))
        restarted.stop()
        submitted=[json.loads(e[2])[0][1] for e in self.service.events if e[0]=='submit']
        self.assertEqual(submitted,['lost','later','elsewhere'])

    def test_deletion_writes_its_tombstone(self):
        self.service.answer = lambda k, c: [["delivery", k, 0, 1, ["delivered", 0]]]
        self.assertEqual(self.tg.delete_message("42", 12), "deleted message 12 from chat 42")
        self.service.answer = lambda k, c: [["delivery", k, 0, 1, ["failed", 400]]]
        self.assertTrue(self.tg.delete_message("42", 13).startswith("cannot delete message 13"))
        self.wait(lambda: len(self.outbound()) == 2)
        notes = [(r["message_id"], r["note"]) for r in self.outbound()]
        self.assertEqual(notes, [(12, "own_delete"), (13, "own_delete_terminal")])
        (_, _, body), = [e for e in self.service.events if e[0] == "submit"][:1]
        self.assertEqual(json.loads(body), [["delete", 12]])

    def test_keys_keep_increasing_across_restarts_and_retries_resend_identical_bytes(self):
        path = str(pathlib.Path(self.tmp.name) / "restart-sequence")
        first = durable_telegram.Channel(self.service.path, path).key(-100123)
        second = durable_telegram.Channel(self.service.path, path).key(-100123)
        self.assertTrue(re.match(r"^-100123\.0\.\d{20}$", first))
        self.assertLess(first, second)
        self.service.unavailable = 2
        channel = durable_telegram.Channel(self.service.path, path)
        channel.submit(second, [["send", "retry", "plain"]])
        self.assertEqual(json.loads(self.service.submissions[second]), [["send", "retry", "plain"]])


if __name__ == "__main__":
    unittest.main()
