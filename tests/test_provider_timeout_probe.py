"""Resource-lifetime witness through the real SWI-PeTTa/Janus boundary."""

import http.server
from isolated_env import isolated_environ  # noqa: E402
import os
import pathlib
import re
import socketserver
import subprocess
import threading
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class SlowHeaderServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class SlowHeaders(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        if length:
            self.rfile.read(length)
        time.sleep(2.0)

    def log_message(self, *_args):
        pass


class ProviderTimeoutProbeTest(unittest.TestCase):
    def test_repeated_timeouts_remain_bounded_through_petta(self):
        if not pathlib.Path("/proc/self/fd").is_dir():
            self.skipTest("descriptor census requires procfs")
        petta_root = pathlib.Path(os.environ.get(
            "PETTA_ROOT", pathlib.Path.home() / "repos" / "PeTTa"))
        if not (petta_root / "run.sh").is_file():
            self.skipTest("PeTTa checkout is unavailable")

        server = SlowHeaderServer(("127.0.0.1", 0), SlowHeaders)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        env = isolated_environ()
        env.update({
            "METTACLAW_SKIP_INITIALIZE": "1",
            "METTACLAW_SKIP_CONFIG": "1",
            "PETTA_ROOT": os.fspath(pathlib.Path.home() / "repos" / "PeTTa"),
            "PETTA_PY_ENV": os.fspath(pathlib.Path.home() / "miniforge3" /
                                      "envs" / "petta"),
            "SYNTHETIC_BASE_URL": (
                f"http://127.0.0.1:{server.server_address[1]}/openai/v1"),
            "SYNTHETIC_API_KEY": "local-test-key",
            "SYNTHETIC_MODEL": "local-test-model",
            "SYNTHETIC_TIMEOUT": "1",
            "SYNTHETIC_EMPTY_BACKOFF": "0",
            "SYNTHETIC_EMPTY_BACKOFF_CAP": "0",
        })
        try:
            result = subprocess.run(
                ["./run.sh", "tests/provider_timeout_recursive_probe.metta"],
                cwd=ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=25,
            )
        finally:
            server.shutdown()
            server.server_close()

        diagnostic = (result.stdout + "\n" + result.stderr)[-5000:]
        self.assertEqual(result.returncode, 0, diagnostic)
        self.assertEqual(result.stderr.count("failed provider turn: TimeoutError"), 12)
        witnessed = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", result.stdout)
        match = re.search(
            r"^\(PROVIDER_TIMEOUTS_COMPLETE\s+(\d+)\)$",
            witnessed,
            re.MULTILINE,
        )
        self.assertIsNotNone(match, diagnostic)
        self.assertLessEqual(int(match.group(1)), 8, diagnostic)


if __name__ == "__main__":
    unittest.main(verbosity=2)
