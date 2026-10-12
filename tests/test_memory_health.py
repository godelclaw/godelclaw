"""Memory-health actions report evidence, never inferred persistence."""

import os
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import memory_health  # noqa: E402


class MemoryHealthTests(unittest.TestCase):
    def fake_chroma(self):
        collection = mock.Mock()
        collection.count.return_value = 10
        return types.SimpleNamespace(COLLECTION=collection, CLIENT=object())

    def test_no_drift_needs_no_attempt(self):
        with mock.patch.object(memory_health, "drift", return_value=(10, 10, 0)):
            self.assertIn("nothing to do", memory_health.compact())

    def test_compact_reports_measured_progress(self):
        fake = self.fake_chroma()
        with mock.patch.object(
                memory_health, "drift",
                side_effect=[(10, 5, 5), (10, 9, 1), (10, 9, 1)]), \
             mock.patch.dict(sys.modules, {"lib_chromadb": fake}):
            result = memory_health.compact()
        self.assertIn("measured progress 5 -> 1", result)
        fake.COLLECTION.count.assert_called_once_with()

    def test_compact_does_not_claim_success_without_progress(self):
        fake = self.fake_chroma()
        with mock.patch.object(
                memory_health, "drift",
                side_effect=[(10, 5, 5), (10, 5, 5), (10, 5, 5)]), \
             mock.patch.dict(sys.modules, {"lib_chromadb": fake}):
            result = memory_health.compact()
        self.assertIn("NO VERIFIED PROGRESS (5 -> 5 behind)", result)

    def test_compact_surfaces_chroma_failure(self):
        fake = self.fake_chroma()
        fake.COLLECTION.count.side_effect = RuntimeError("unavailable")
        with mock.patch.object(memory_health, "drift", return_value=(10, 5, 5)), \
             mock.patch.dict(sys.modules, {"lib_chromadb": fake}):
            result = memory_health.compact()
        self.assertIn("FAILED: RuntimeError", result)

    def test_threshold_prefers_persisted_configuration_over_stale_metadata(self):
        with tempfile.TemporaryDirectory() as chroma_dir:
            db = sqlite3.connect(os.path.join(chroma_dir, "chroma.sqlite3"))
            db.execute(
                "create table collections "
                "(id text, name text, config_json_str text, schema_str text)"
            )
            db.execute(
                "create table collection_metadata "
                "(collection_id text, key text, str_value text, "
                "int_value integer, float_value real)"
            )
            schema = '{"vector_index":{"hnsw":{"sync_threshold":20}}}'
            db.execute(
                "insert into collections values (?, ?, ?, ?)",
                ("collection-id", "memories", "{}", schema),
            )
            db.execute(
                "insert into collection_metadata values (?, ?, ?, ?, ?)",
                ("collection-id", "hnsw:sync_threshold", None, 100, None),
            )
            db.commit()
            db.close()
            with mock.patch.dict(
                os.environ,
                {
                    "METTACLAW_CHROMA_DIR": chroma_dir,
                    "METTACLAW_CHROMA_SYNC_THRESHOLD": "999",
                },
            ):
                self.assertEqual(memory_health.threshold(), 20)

    def test_engine_probe_reports_shape_without_memory_content(self):
        qwen = types.SimpleNamespace(embed_query=lambda _text: [0.1, 0.2])
        chroma = types.SimpleNamespace(query_with_ids=lambda _vector, _k: [
            ["id-1", "time-1", "private content one"],
            ["id-2", "time-2", "private content two"],
        ])
        with mock.patch.dict(sys.modules, {
            "qwen_embed": qwen,
            "lib_chromadb": chroma,
        }):
            result = memory_health.engine_query_probe()
        self.assertEqual(result, "memory-engine-probe: ok (2 unique results)")
        self.assertNotIn("private content", result)


if __name__ == "__main__":
    unittest.main()


class NativeBackendHealthTest(unittest.TestCase):
    def test_native_mode_checks_the_service_not_chroma(self):
        import json, socket, tempfile, threading
        with tempfile.TemporaryDirectory() as directory:
            sock_path = os.path.join(directory, "memory.sock")
            config = os.path.join(directory, "memory_backend.json")
            with open(config, "w", encoding="utf-8") as handle:
                json.dump({"mode": "native", "name": "memories-test", "socket": sock_path}, handle)
            with mock.patch.dict(os.environ, {"METTACLAW_MEMORY_BACKEND_CONFIG": config}):
                self.assertEqual(memory_health.report(),
                                 "memory-health: native backend memories-test — service UNREACHABLE")
                server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                server.bind(sock_path); server.listen(1)
                try:
                    self.assertEqual(memory_health.report(),
                                     "memory-health: native backend memories-test — service reachable")
                finally:
                    server.close()

    def test_without_a_backend_config_chroma_is_measured(self):
        with mock.patch.dict(os.environ, {"METTACLAW_MEMORY_BACKEND_CONFIG": "/nonexistent/backend.json"}):
            self.assertEqual(memory_health.backend(), {"mode": "chroma"})
