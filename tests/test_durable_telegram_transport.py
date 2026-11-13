#!/usr/bin/env python3
"""Sends through the durable Telegram transport against a fake CeTTa channel
service.

The fake speaks CWP1 over a private seqpacket socket. It serves receipt
tasks, records acknowledgments and keyed submissions, and can answer a
submission with receipts, as the real service does after sending. A relay
thread plays the MeTTa client, which hands those receipts back.
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

NEXT, RESULT, RECEIPT, SUBMIT, NEXT_CONTROL = 1, 2, 3, 4, 6
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
            if code in (NEXT, NEXT_CONTROL):
                tasks = ([(t, o) for t, o in self.tasks if json.loads(o)[0] in ("command", "callback")]
                         if code == NEXT_CONTROL else self.tasks)
                ids = [t for t, _ in tasks]
                if name and name not in ids:
                    return self.reply(conn, UNKNOWN, name)
                start = ids.index(name) + 1 if name else 0
                if start >= len(tasks):
                    return self.reply(conn, IDLE)
                task, observation = tasks[start]
                return self.reply(conn, TASK, task, observation)
            if code == RECEIPT:
                return self.reply(conn, STORED if name in self.results else 67 if any(t == name for t, _ in self.tasks) else UNKNOWN, name)
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
    """The submission keys of the Python channel client, which the mode
    channel and the health notices still use. The loop's own sends are the
    MeTTa client's (tests/telegram_send_and_intake_probe.metta and the
    end-to-end test)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = FakeService(str(pathlib.Path(self.tmp.name) / "service.sock"))

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

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
