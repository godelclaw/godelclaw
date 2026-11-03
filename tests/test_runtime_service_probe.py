import socket
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from probe_runtime_services import telegram_ready


class TransportReadinessTests(unittest.TestCase):
    def probe(self, reply):
        received = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "peer.sock"
            with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as listener:
                listener.bind(str(path))
                listener.listen()

                def serve():
                    peer, _ = listener.accept()
                    with peer:
                        received.append(peer.recv(1024))
                        peer.sendall(reply)
                        received.append(peer.recv(1024))

                worker = threading.Thread(target=serve)
                worker.start()
                try:
                    result = telegram_ready(path)
                finally:
                    worker.join(timeout=5)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(received, [b"CWP1\x01\x00", b""])
        return result

    def test_idle_and_large_pending_task_are_non_consuming_observations(self):
        self.assertEqual(self.probe(b"CWP1\x40\x00"), {"ready": True, "pending": False})
        self.assertEqual(self.probe(b"CWP1\x41\x00" + b"x" * 131072),
                         {"ready": True, "pending": True})

    def test_unavailable_or_malformed_reply_is_not_readiness(self):
        for reply in (b"CWP1\x47\x00", b"CWP1\x41\x05", b"invalid"):
            with self.subTest(reply=reply), self.assertRaises(RuntimeError):
                self.probe(reply)
