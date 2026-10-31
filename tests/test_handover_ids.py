"""The exact queue-receipt regression and the foreign-value trust boundary."""
import sys
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "channels")]
import telegram
import input_ack
import mode_channel


class HandoverIdsTest(unittest.TestCase):
    def test_complete_csv_and_native_list_agree(self):
        for values in ("150402591,150402592", [150402591, 150402592],
                       ["150402591", "150402592"]):
            self.assertEqual(telegram.handover_ids(values), ["150402591", "150402592"])
        self.assertEqual(telegram.handover_ids(""), [])
        self.assertEqual(telegram.handover_ids("1,1"), ["1"])

    def test_error_atoms_and_partial_batches_cannot_authorize_cleanup(self):
        for values in (["Error", ["str:join", ["150402591"], ","], "expected separator"],
                       "['150402591', '150402592']", "1,,2", "1, 2", "01", "١", True,
                       [1, False], "-1", str(2**63)):
            with self.subTest(values=values), mock.patch.object(input_ack, "processed_update_ids") as processed:
                with self.assertRaises(ValueError):
                    telegram.handover_ack(values)
                processed.assert_not_called()

    def test_large_batches_preserve_order_and_validate_the_tail(self):
        values = [str(i) for i in range(1024)]
        self.assertEqual(telegram.handover_ids(values + values), values)
        with mock.patch.object(mode_channel, "acknowledge_handover") as acknowledge:
            with self.assertRaises(ValueError): telegram.handover_ack(values + ["bad"])
            acknowledge.assert_not_called()

    def test_unread_scan_includes_items_beyond_the_first_page(self):
        import json
        stream = [(str(n), json.dumps(["input", "42.0", "message", "operator", {"update_id": n}])) for n in range(600)]
        with mock.patch.dict('os.environ', {"METTACLAW_TELEGRAM_SERVICE_SOCKET": "fixture"}), \
                mock.patch('durable_telegram.Channel') as channel, \
                mock.patch.object(telegram, '_current_batch_update_ids', [str(n) for n in range(256)]):
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

    def test_durable_ack_precedes_notice_processing(self):
        events = []
        with mock.patch.object(mode_channel, "acknowledge_handover", side_effect=lambda ids: events.append(("ack", ids)) or True), \
             mock.patch.object(input_ack, "processed_update_ids", side_effect=lambda ids: events.append(("processed", ids))):
            self.assertTrue(telegram.handover_ack("1,2"))
        self.assertEqual(events, [("ack", ["1", "2"]), ("processed", ["1", "2"])])
        with mock.patch.object(mode_channel, "acknowledge_handover", return_value=False), \
             mock.patch.object(input_ack, "processed_update_ids") as processed:
            self.assertFalse(telegram.handover_ack("1"))
            processed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
