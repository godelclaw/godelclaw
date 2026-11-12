"""Actual native observation calls; Python is only the independent test oracle."""
import hermetic_env  # noqa: F401,E402
import hashlib
import json
import os
from pathlib import Path
import resource
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = Path(os.environ.get('CETTA_CHANNEL_ROOT', ROOT.parent / 'CeTTa-ours'))
sys.path[:0] = [str(ENGINE_ROOT / 'tools'), str(ROOT / 'src')]
from value_worker_client import Worker
from space_service_client import E, S
import coding_estate
import coding_estate_snapshot


@unittest.skipUnless(os.environ.get('CETTA_VALUE_WORKER'), 'requires a native worker')
class NativeEstateCoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='estate-native-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.program = self.root / 'probe.metta'
        self.program.write_text((ROOT / 'tests/support/coding_estate_observation_probe.metta').read_text()
                                .replace('../../src/', str(ROOT / 'src') + '/'))
        self.worker = Worker(os.environ['CETTA_VALUE_WORKER'], self.program, 'estate-probe:call',
                             language='petta', import_mode='ancestor-walk')
        self.addCleanup(self.worker.close)
        self.worker.call(S('Clear'))
        coding_estate._engine_digest_cache.clear()
        coding_estate._runtime_blocks_cache.clear()

    def blocks(self, path):
        return self.worker.call(E('Blocks', str(path)))

    def test_native_blocks_preserve_hardlinks_and_do_not_follow_internal_symlinks(self):
        tree = self.root / 'tree'
        tree.mkdir()
        (tree / 'nested').mkdir()
        (tree / 'nested/data').write_bytes(b'A' * 16384)
        os.link(tree / 'nested/data', tree / 'copy')
        (tree / 'loop').symlink_to(tree)
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'big').write_bytes(b'B' * 262144)
        (tree / 'outside-link').symlink_to(outside)
        (tree / 'dangling').symlink_to(self.root / 'missing')
        linked_root = self.root / 'linked-root'
        linked_root.symlink_to(tree)
        for path in (tree, linked_root, tree / 'copy', tree / 'dangling', self.root / 'absent'):
            with self.subTest(path=path.name):
                self.assertEqual(self.blocks(path), (coding_estate._dir_blocks(path),))

    def test_streamed_hash_and_cache_detect_same_size_write_with_restored_mtime(self):
        binary = self.root / 'engine'
        binary.write_bytes(bytes(range(256)) * 257)
        original = binary.stat()
        first = self.worker.call(E('Hash', str(binary)))
        self.assertEqual(first, hashlib.sha256(binary.read_bytes()).hexdigest())
        self.assertEqual(self.worker.call(E('Hash', str(binary))), first)
        binary.write_bytes(b'X' * original.st_size)
        os.utime(binary, ns=(original.st_atime_ns, original.st_mtime_ns))
        self.assertEqual(self.worker.call(E('Hash', str(binary))), hashlib.sha256(binary.read_bytes()).hexdigest())
        self.assertNotEqual(first, self.worker.call(E('Hash', str(binary))))
        for bad in (self.root / 'absent', self.root):
            self.assertEqual(self.worker.call(E('Hash', str(bad))), S('JsonNull'))

    def test_native_resource_failure_is_not_cached_as_empty_storage(self):
        tree = self.root / 'tree'
        tree.mkdir()
        (tree / 'data').write_bytes(b'X' * 16384)
        pid = self.worker.process.pid
        previous = resource.prlimit(pid, resource.RLIMIT_NOFILE)
        try:
            resource.prlimit(pid, resource.RLIMIT_NOFILE, (0, previous[1]))
            self.assertEqual(self.blocks(tree), ())
        finally:
            resource.prlimit(pid, resource.RLIMIT_NOFILE, previous)
        self.assertEqual(self.blocks(tree), (coding_estate._dir_blocks(tree),))
        self.assertGreater(self.blocks(tree)[0], 0)

    def test_permission_limited_traversal_keeps_partial_count_and_closes_owners(self):
        tree = self.root / 'tree'
        tree.mkdir()
        denied = tree / 'denied'
        denied.mkdir()
        (denied / 'private').write_bytes(b'X' * 16384)
        denied.chmod(0)
        self.addCleanup(denied.chmod, 0o700)
        before = len(list(Path('/proc', str(self.worker.process.pid), 'fd').iterdir()))
        for _ in range(12):
            self.worker.call(S('Clear'))
            self.assertEqual(self.blocks(tree), (coding_estate._dir_blocks(tree),))
        after = len(list(Path('/proc', str(self.worker.process.pid), 'fd').iterdir()))
        self.assertEqual(after, before)

    def test_snapshot_parity_with_python_only_in_the_harness(self):
        (self.root / 'memory/coding').mkdir(parents=True)
        (self.root / 'memory/coding-runtimes/r1').mkdir(parents=True)
        (self.root / 'memory/coding-runtimes/r1/data').write_bytes(b'R' * 12288)
        session = self.root / 'memory/coding/s1'
        session.mkdir()
        (session / 'state.json').write_text('{"status":"stopped","steps":2,"updated":1794000000}')
        (session / 'task.json').write_text('{"task":"fixture"}')
        (session / 'runs.jsonl').write_bytes(b'{"id":"old"}\nmalformed\n{"id":"current"}\n{')
        native = json.loads(self.worker.call(E('Snapshot', str(self.root), '2026-11-12')))
        expected = json.loads(coding_estate_snapshot.snapshot(self.root, '2026-11-12'))
        self.assertEqual(native, expected)


if __name__ == '__main__':
    unittest.main()
