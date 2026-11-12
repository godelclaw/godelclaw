"""Native health observations against the separate Python implementation."""
import hermetic_env  # noqa: F401,E402
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = Path(os.environ.get('CETTA_CHANNEL_ROOT', ROOT.parent / 'CeTTa-ours'))
sys.path[:0] = [str(ENGINE_ROOT / 'tools'), str(ROOT / 'src')]
from value_worker_client import Worker
from space_service_client import E, S
import memory_health


@unittest.skipUnless(os.environ.get('CETTA_VALUE_WORKER'), 'requires a native worker')
class NativeMemoryHealthTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='mh-native-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / 'backend.json'
        environment = mock.patch.dict(os.environ, METTACLAW_MEMORY_BACKEND_CONFIG=str(self.config))
        environment.start()
        self.addCleanup(environment.stop)
        program = self.root / 'probe.metta'
        program.write_text('!(import! &self ' + json.dumps(str(ROOT / 'src/memory_health_cetta.metta')) + ')\n'
                           '(= (health-probe:call (Backend $root)) (json:stringify-python (memory-health:backend $root)))\n'
                           '(= (health-probe:call (Report $root)) (memory-health:report $root))\n')
        self.worker = Worker(os.environ['CETTA_VALUE_WORKER'], program, 'health-probe:call',
                             language='petta', import_mode='ancestor-walk')
        self.addCleanup(self.worker.close)

    def parity(self):
        self.assertEqual(json.loads(self.worker.call(E('Backend', str(self.root)))), memory_health.backend())
        self.assertEqual(self.worker.call(E('Report', str(self.root))), memory_health.report())

    def test_missing_malformed_nonobject_and_invalid_utf8_configuration(self):
        self.parity()
        for contents in (b'{not JSON', b'[]', b'null', b'{"mode":"\xff"}'):
            with self.subTest(contents=contents):
                self.config.write_bytes(contents)
                self.parity()

    def test_native_backend_absent_socket_and_non_native_modes(self):
        for value in ({'mode': 'native', 'name': 'fixture'},
                      {'mode': 'native', 'socket': str(self.root / 'absent')},
                      {'mode': 'other'}, {'mode': None}, {'mode': ['other']}, {}):
            with self.subTest(value=value):
                self.config.write_text(json.dumps(value))
                self.parity()

    def test_native_boot_diagnostic_uses_the_actual_installed_entry(self):
        self.config.write_text('{"mode":"native","name":"fixture"}')
        environment = dict(os.environ, METTACLAW_HEALTH_RUNTIME_ROOT=str(self.root))
        result = subprocess.run([os.environ['CETTA_BIN'], '--no-results', '--lang', 'petta',
                                 '--profile', 'extended', '--import-mode', 'ancestor-walk',
                                 str(ROOT / 'scripts/memory_health_cetta.metta')],
                                env=environment, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '[memory_health] ' + memory_health.report() + '\n')

    def test_listening_service_receives_no_protocol_request_and_owners_close(self):
        path = self.root / 'service.sock'
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(path))
        listener.listen(16)
        listener.settimeout(.1)
        self.addCleanup(listener.close)
        received = []
        condition = threading.Condition()
        done = threading.Event()
        def accept():
            while not done.is_set():
                try:
                    client, _ = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    return
                with client:
                    client.settimeout(2)
                    payload = client.recv(32)
                    with condition:
                        received.append(payload)
                        condition.notify_all()
        thread = threading.Thread(target=accept, daemon=True)
        thread.start()
        self.addCleanup(done.set)
        self.config.write_text(json.dumps({'mode': 'native', 'name': 'fixture', 'socket': str(path)}))
        self.worker.call(E('Backend', str(self.root)))
        before = len(list(Path('/proc', str(self.worker.process.pid), 'fd').iterdir()))
        for _ in range(8):
            self.parity()
        after = len(list(Path('/proc', str(self.worker.process.pid), 'fd').iterdir()))
        self.assertEqual(before, after)
        with condition:
            self.assertTrue(condition.wait_for(lambda: len(received) == 16, timeout=2))
        done.set()
        thread.join(timeout=2)
        self.assertEqual(len(received), 16)
        self.assertEqual(set(received), {b''})


if __name__ == '__main__':
    unittest.main()
