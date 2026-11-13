#!/usr/bin/env python3
"""Replay the 2026-08-27 cross-chat routing and cleanup failure."""

import importlib
import os
import pathlib
import sys
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from channels import telegram  # noqa: E402
import action_graph  # noqa: E402


class TelegramRoutingReplayTests(unittest.TestCase):
    def setUp(self):
        self.tg = importlib.reload(telegram)

    def test_ambient_route_has_constructive_wrong_chat_witness(self):
        with mock.patch.dict(os.environ, {"METTACLAW_TELEGRAM_PRIMARY_CHAT_ID": ""}):
            self.tg._reply_chat_id = "protobots"
            self.assertEqual(self.tg._reply_target(), "protobots")

    def test_primary_binding_survives_same_cross_chat_state(self):
        with mock.patch.dict(os.environ, {"METTACLAW_TELEGRAM_PRIMARY_CHAT_ID": "operator-private"}):
            self.tg._reply_chat_id = "protobots"
            self.assertEqual(self.tg._reply_target(), "operator-private")
            self.assertEqual(
                self.tg._reply_target("protobots"), "protobots"
            )

    def test_failed_cleanup_invalidates_unobserved_done_suffix(self):
        command = ["delete-my-recent", "research-group", 3]
        result = (
            "no recorded own-sends to chat research-group — nothing to delete"
        )
        self.assertFalse(
            action_graph.result_permits_dependent_suffix(command, result)
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
