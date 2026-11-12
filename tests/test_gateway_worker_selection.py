"""Pinned gateway selection and invalidation of verified artifact versions."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from execution_gateway import cetta_worker as worker


def bundle(directory, reference):
    directory.mkdir(parents=True)
    files = {}
    for name in ('cetta-value-worker', 'cetta-value-worker.proc-scope'):
        path = directory / name
        path.write_bytes(b'#!/bin/sh\nexit 0\n')
        path.chmod(0o755)
        files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = directory / 'build-manifest.json'
    manifest.write_text(json.dumps(dict(schema=1, revision=reference, features=['ENABLE_DURABLE=1'], files=files)))
    return directory / 'cetta-value-worker'


class GatewayWorkerSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.reference = 'a' * 40
        self.here = self.home / 'runtime/src/execution_gateway'
        self.here.mkdir(parents=True)
        (self.here / 'GATEWAY_CETTA_REF').write_text(self.reference)
        self.worker = bundle(self.home / '.local/lib/pettaclaw/cetta-ours-aaaaaaaa-siwc', self.reference)
        for patch in (mock.patch.object(worker, 'HERE', self.here),
                      mock.patch.object(Path, 'home', return_value=self.home),
                      mock.patch.dict(worker._VALIDATED_WORKERS, {}, clear=True),
                      mock.patch.dict(os.environ, METTACLAW_GATEWAY_WORKER_BIN='')):
            patch.start()
            self.addCleanup(patch.stop)

    def test_siwc_bundle_is_discovered_without_using_generic_agent_overrides(self):
        with mock.patch.dict(os.environ, CETTA_BIN='/unrelated/engine', CETTA_VALUE_WORKER_BIN='/unrelated/worker'):
            selected = worker.describe_gateway_worker()
        self.assertEqual(selected['path'], self.worker)

    def test_explicit_wrong_revision_refuses_instead_of_falling_back(self):
        wrong = bundle(self.home / 'wrong', 'b' * 40)
        with self.assertRaisesRegex(worker.WorkerUnavailable, 'revision mismatch'):
            worker.describe_gateway_worker(wrong)

    def test_verified_cache_detects_same_size_edits_with_restored_mtime(self):
        for name in ('cetta-value-worker', 'cetta-value-worker.proc-scope', 'build-manifest.json'):
            with self.subTest(name=name):
                worker._VALIDATED_WORKERS.clear()
                path = self.worker.parent / name
                before = path.read_bytes()
                stamp = path.stat()
                worker.describe_gateway_worker(self.worker)
                replacement = before.replace(b'exit 0', b'exit 1') if name != 'build-manifest.json' else before.replace(b'a' * 40, b'b' * 40)
                self.assertNotEqual(replacement, before)
                self.assertEqual(len(replacement), len(before))
                path.write_bytes(replacement)
                os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                with self.assertRaises(worker.WorkerUnavailable):
                    worker.describe_gateway_worker(self.worker)
                path.write_bytes(before)


if __name__ == '__main__':
    unittest.main()
