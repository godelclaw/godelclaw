#!/usr/bin/env python3
"""Standalone native HTTP observation controls against loopback fixtures."""
import argparse
import collections
import http.server
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get("CETTA_SOURCE", ROOT.parent / "CeTTa-ours"))


class Peer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.requests = []
        self.lock = threading.Lock()


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        with self.server.lock:
            self.server.requests.append((self.path, body))
        length = {"/progress": 3, "/ready": 2}.get(self.path, 100)
        self.send_response(200)
        self.send_header("Content-Length", str(length))
        self.end_headers()
        self.wfile.flush()
        try:
            if self.path == "/truncated":
                self.wfile.write(b"part"); self.wfile.flush()
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
            elif self.path == "/silent":
                time.sleep(0.5)
                self.wfile.write(b"late"); self.wfile.flush()
            elif self.path == "/idle":
                self.wfile.write(b"x"); self.wfile.flush(); time.sleep(0.5)
            elif self.path == "/hard":
                for _ in range(20):
                    self.wfile.write(b"x"); self.wfile.flush(); time.sleep(0.05)
            elif self.path == "/progress":
                for byte in [b"a", b"b", b"c"]:
                    self.wfile.write(byte); self.wfile.flush(); time.sleep(0.08)
            else:
                self.wfile.write(b"ok"); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


def run_contract(sanitize=False):
    with tempfile.TemporaryDirectory(prefix="http-observation-") as raw:
        binary = Path(raw) / "http-observation"
        flags = shlex.split(subprocess.check_output(
            ["pkg-config", "--cflags", "--libs", "libcurl"], text=True))
        command = shlex.split(os.environ.get("CC", "cc")) + [
            "-std=c11", "-O1" if sanitize else "-O2", "-g", "-pthread",
            "-DCETTA_BUILD_HTTP_PROVIDER_CURL=1", "-I" + str(SOURCE / "src"),
            str(ROOT / "tests/test_http_observation_contract.c"),
            str(SOURCE / "src/http_worker.c"), *flags, "-o", str(binary)]
        if sanitize:
            command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
        subprocess.run(command, check=True, timeout=60)
        with Peer() as peer, socket.socket() as refused:
            # Holding a bound non-listening socket removes port-reuse races.
            refused.bind(("127.0.0.1", 0))
            thread = threading.Thread(target=peer.serve_forever, daemon=True)
            thread.start()
            try:
                result = subprocess.run([
                    str(binary), "http://127.0.0.1:%d" % peer.server_port,
                    "http://127.0.0.1:%d/refused" % refused.getsockname()[1]],
                    capture_output=True, text=True, timeout=20)
                assert result.returncode == 0, result.stdout + result.stderr
                assert "HTTP_OBSERVATION_CONTRACT_OK" in result.stdout
                assert not result.stderr, result.stderr
                with peer.lock:
                    requests = list(peer.requests)
                assert collections.Counter(path for path, _ in requests) == {
                    path: 1 for path in ["/progress", "/truncated", "/silent", "/hard", "/ready", "/idle"]}, requests
                assert all(body == b"one" for _, body in requests), requests
                print(result.stdout, end="")
                print("Exactly one request per case; observations and waits never resubmitted.")
            finally:
                peer.shutdown(); thread.join(timeout=5)


@unittest.skipUnless((SOURCE / 'src/http_worker.c').is_file() and shutil.which('pkg-config'),
                     'set CETTA_SOURCE to a native HTTP source checkout and install libcurl development files')
class HttpObservationContractTests(unittest.TestCase):
    def test_native_worker_observations_and_failures(self):
        run_contract()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sanitize", action="store_true")
    run_contract(parser.parse_args().sanitize)
