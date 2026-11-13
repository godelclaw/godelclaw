"""The unread scan reads every pending item, and an incomplete scan establishes
nothing. Handover ids are validated and settled natively
(src/activity_source_cetta.metta, tests/unit/activity_source.metta)."""
import sys
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "channels")]
import telegram


class HandoverIdsTest(unittest.TestCase):
    def test_unread_scan_includes_items_beyond_the_first_page(self):
        import json
        stream = [(str(n), json.dumps(["input", "42.0", "message", "operator", {"update_id": n}])) for n in range(600)]
        with mock.patch.dict('os.environ', {"METTACLAW_TELEGRAM_SERVICE_SOCKET": "fixture"}), \
                mock.patch('durable_telegram.Channel') as channel, \
                mock.patch.object(telegram, '_current_batch_update_ids', [str(n) for n in range(256)], create=True):
            channel.return_value.iter_pending.return_value = iter(stream)
            self.assertEqual(telegram._metta_unread_chats(), {"42"})

    def test_incomplete_scan_cannot_establish_an_empty_inbox(self):
        import durable_telegram
        def interrupted():
            raise durable_telegram.ChannelError("cursor invalidated")
            yield
        with mock.patch.dict('os.environ', {"METTACLAW_TELEGRAM_SERVICE_SOCKET": "fixture"}), \
                mock.patch('durable_telegram.Channel') as channel:
            channel.return_value.iter_pending.return_value = interrupted()
            self.assertIsNone(telegram._metta_unread_chats())

if __name__ == "__main__":
    unittest.main()
