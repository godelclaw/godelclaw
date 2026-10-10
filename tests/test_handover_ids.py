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
                       [1, False], "-1", str(2**63), ["1"] * 257):
            with self.subTest(values=values), mock.patch.object(input_ack, "processed_update_ids") as processed:
                with self.assertRaises(ValueError):
                    telegram.handover_ack(values)
                processed.assert_not_called()

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
