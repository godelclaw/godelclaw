"""Loopback peer, clock and trace for a native-I/O scheduling experiment.

The scheduling policy lives in scheduler.metta. This module supplies no task
selection, continuation, effect dispatch or real model provider.
"""
import http.server
import json
import os
from pathlib import Path
import threading
import time

_start = None
_peer = None
_lock = threading.Lock()
_trace = None
_slow_ms = None
_fast_ms = None
_pause_ms = None


def elapsed_ms():
    return round((time.monotonic() - _start) * 1000)


def record(name, value=0):
    event = {"event": str(name), "at_ms": elapsed_ms(), "value": value}
    with _lock:
        with _trace.open("a") as target:
            target.write(json.dumps(event) + "\n")
    return True


def completed(label, handle, body):
    expected = {"slow-completed": "slow", "short-completed": "short"}[str(label)]
    if str(body) != expected:
        raise AssertionError(f"response body does not belong to {label}: {body}")
    return record(label, handle)


class Peer(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        name = self.path.lstrip("/")
        record("request-started", name)
        time.sleep((_slow_ms if name == "slow" else _fast_ms) / 1000)
        body = name.encode("ascii")
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        record("request-finished", name)


def start(mode):
    global _start, _peer, _trace, _slow_ms, _fast_ms, _pause_ms
    _trace = Path(os.environ["RESPONSIVENESS_TRACE"])
    _trace.write_text("")
    _slow_ms = int(os.environ.get("RESPONSIVENESS_SLOW_MS", "16000"))
    _fast_ms = int(os.environ.get("RESPONSIVENESS_FAST_MS", "100"))
    _pause_ms = int(os.environ.get("RESPONSIVENESS_PAUSE_MS", "5"))
    _peer = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Peer)
    _start = time.monotonic()
    threading.Thread(target=_peer.serve_forever, daemon=True).start()
    record("started", mode)
    return True


def url(name):
    return f"http://127.0.0.1:{_peer.server_port}/{name}"


def mode():
    return os.environ["RESPONSIVENESS_MODE"]


def input_ms():
    return int(os.environ.get("RESPONSIVENESS_INPUT_MS", "1000"))


def period_ms():
    return int(os.environ.get("RESPONSIVENESS_PERIOD_MS", "5000"))


def pause():
    # The experiment samples readiness at a bounded interval. A production
    # driver should instead arm the native event wake and next absolute timer.
    time.sleep(_pause_ms / 1000)
    return True


def fail(value):
    raise AssertionError(f"unexpected I/O outcome: {value}")


def finish():
    record("finished")
    _peer.shutdown()
    _peer.server_close()
    return True
