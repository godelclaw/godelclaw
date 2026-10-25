import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import update_instances


class ReleaseMetadataTest(unittest.TestCase):
    def test_current_receipt_drops_stale_labels_and_observes_selected_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'cetta'
            binary.write_bytes(b'qualified engine fixture')
            identity = hashlib.sha256(binary.read_bytes()).hexdigest()
            (root / 'build-manifest.json').write_text(json.dumps({'revision': 'a' * 40, 'files': {'cetta': identity}}))
            prior = {'release': 'older', 'activation': 'obsolete operation label', 'model': 'old choice'}
            manifest = {'release': 'newer', 'source_commit': 'b' * 40, 'engine_revision': 'a' * 40}
            marker = update_instances.release_marker(prior, manifest, {}, '123', root / 'receipt',
                        {'required_runtime': {'CETTA_BIN': str(binary)}})
            self.assertNotIn('activation', marker)
            self.assertNotIn('model', marker)
            self.assertEqual(marker['activation_receipt'], str(root / 'receipt'))
            self.assertEqual(marker['engine_selection']['binary_sha256'], identity)
            self.assertTrue(marker['engine_selection']['manifest_matches_binary'])
            self.assertEqual(marker['source_commit'], manifest['source_commit'])
            again = update_instances.release_marker(marker, manifest, {}, '123', root / 'receipt', {})
            self.assertEqual(again['parent_release'], 'older')
            self.assertNotIn('engine_selection', again)
