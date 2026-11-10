"""The real SWI burst writes receipts without stale Janus lookup exceptions."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import test_runtime_probes


class SwiReceiptBoundaryTests(unittest.TestCase):
    def test_burst_records_once_without_a_stale_module_lookup_exception(self):
        probe = test_runtime_probes.RuntimeProbeTest(
            'test_each_probe_reaches_its_success_witness')
        probe.PROBES = (('tests/burst_end_and_prose_probe.metta', 'BURST_END_AND_PROSE_OK'),)
        original_run = subprocess.run
        with tempfile.TemporaryDirectory() as directory:
            receipts = Path(directory) / 'receipts.jsonl'
            captured = []
            def run(command, **kwargs):
                watched = command[:1] == ['./run.sh']
                if watched:
                    kwargs['env']['METTACLAW_EFFECT_RECEIPT_PATH'] = str(receipts)
                    kwargs['env']['METTACLAW_PUBLIC_TOOL_RECEIPTS'] = '0'
                result = original_run(command, **kwargs)
                if watched:
                    captured.append(result)
                return result
            result = unittest.TestResult()
            with mock.patch.object(test_runtime_probes.subprocess, 'run', side_effect=run):
                probe.run(result)
            if result.skipped:
                self.skipTest(result.skipped[0][1])
            self.assertTrue(result.wasSuccessful(), result.failures + result.errors)
            self.assertEqual(len(captured), 1)
            self.assertEqual(captured[0].stderr, '')
            self.assertIn('BURST_END_AND_PROSE_OK', captured[0].stdout)
            rows = [json.loads(line) for line in receipts.read_text().splitlines()]
            self.assertEqual([row['turn'] for row in rows], [2, 3])
            self.assertEqual([row['disposition'] for row in rows], ['returned', 'returned'])
            self.assertEqual([row['command'] for row in rows], ["['nop']", "['nop']"])
            self.assertEqual(len({row['occurrence'] for row in rows}), 2)


if __name__ == '__main__':
    unittest.main()
