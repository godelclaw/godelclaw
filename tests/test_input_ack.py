import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import input_ack


class InputAcknowledgementTest(unittest.TestCase):
    """The notices' pacing is native (tests/unit/input_notice.metta); what stays
    here is the committed read the loop's Telegram client still asks Python."""

    def setUp(self):
        env = mock.patch.dict(os.environ, {"METTACLAW_OBSERVED_STRATEGY": "1"})
        env.start(); self.addCleanup(env.stop)

    def test_commit_reads_report_exactly_the_processed_ids(self):
        import tempfile
        import native_ledger
        with tempfile.TemporaryDirectory() as directory:
            native_ledger.start(self, directory)
            input_ack._ledger_flag("mark-processed", (1.0, ("one",)))  # cognition's processed-input write
            self.assertEqual(input_ack.already_committed("one"), 1)
            self.assertEqual(input_ack.already_committed("two"), 0)


if __name__ == "__main__":
    unittest.main()
