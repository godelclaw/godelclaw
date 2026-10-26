"""The native relay (scripts/nostr_relay.metta) behind the interface of
nostr_relay_fixture.GatedRelay, so tests can run against either:
`with NativeRelay(allow) as relay: ... relay.url ...`.

The relay runs as a CeTTa process (CETTA_BIN) in a directory of its own, on a
port chosen here so that its address can be the one AUTH events must name.
What the fixture reads from its own memory is read from the relay's store and
log: stored events from the SQLite file, a key's subscriptions from the counts
the relay logs. shut_out and let_in rewrite the allowlist, which the relay
watches, and wait until it has read it again.
"""
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELAY = ROOT / "scripts/nostr_relay.metta"


class _Allowlist(set):
    """The fixture's allow set, which tests change in place: here a change is
    written to the relay's allowlist file."""

    def __init__(self, keys, changed):
        super().__init__(keys)
        self._changed = changed

    def add(self, key):
        super().add(key)
        self._changed()

    def discard(self, key):
        super().discard(key)
        self._changed()


class NativeRelay:
    def __init__(self, allow, binary=None, idle_ms=30000, directory=None):
        self.allow = _Allowlist(allow, self._allow_changed)
        self.refused = set()
        self.binary = binary or os.environ.get("CETTA_BIN", "cetta")
        self.idle_ms = idle_ms
        self.owned = directory is None
        self.directory = Path(directory or tempfile.mkdtemp(prefix="nostr-relay-"))
        self.subs, self.lines, self.reloads = {}, [], 0
        self.changed = threading.Condition()
        self.process = self.url = None
        self.port = None

    def _write_allowlist(self):
        members = sorted(self.allow - self.refused)
        text = "[members]\n" + "".join('member%d = "%s"\n' % (i, key) for i, key in enumerate(members))
        temporary = self.directory / "allow.toml.tmp"
        temporary.write_text(text)
        os.replace(temporary, self.directory / "allow.toml")

    def start(self):
        if self.port is None:
            probe = socket.socket()
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
            probe.close()
        self.url = "ws://127.0.0.1:%d" % self.port
        self._write_allowlist()
        (self.directory / "relay.toml").write_text(
            'host = "127.0.0.1"\nport = %d\nurls = ["%s"]\nallowlist = "allow.toml"\ndatabase = "relay.sqlite"\n'
            'idle_ms = %d\n' % (self.port, self.url, self.idle_ms))
        self.process = subprocess.Popen(
            [str(self.binary), "--lang", "petta", "--quiet", str(RELAY), "--config", str(self.directory / "relay.toml")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        threading.Thread(target=self._follow_log, daemon=True).start()
        line = self.process.stdout.readline()
        if not line.startswith("(RelayListening "):
            self.process.kill()
            raise RuntimeError("the relay did not start: %r %s" % (line, "".join(self.lines[-20:])))
        return self

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(10)

    def __enter__(self):
        return self.start()

    def __exit__(self, *error):
        self.stop()
        if self.owned:
            shutil.rmtree(self.directory, ignore_errors=True)

    def _follow_log(self):
        for line in self.process.stderr:
            with self.changed:
                self.lines.append(line)
                found = re.search(r"subscriptions of ([0-9a-f]{64}): (\d+)", line)
                if found:
                    self.subs[found.group(1)] = int(found.group(2))
                if re.search(r"allowlist: \d+ keys|allowlist unchanged", line):
                    self.reloads += 1
                self.changed.notify_all()

    def log(self):
        with self.changed:
            return "".join(self.lines)

    def stored(self):
        with sqlite3.connect(self.directory / "relay.sqlite") as database:
            return [json.loads(row[0]) for row in database.execute("SELECT json FROM events")]

    def subscribers(self, key):
        """How many open subscriptions a key holds."""
        with self.changed:
            return self.subs.get(key, 0)

    def _allow_changed(self):
        if self.process and self.process.poll() is None:
            self._reload()

    def _reload(self):
        with self.changed:
            before = self.reloads
        self._write_allowlist()
        with self.changed:
            if not self.changed.wait_for(lambda: self.reloads > before, timeout=10):
                raise RuntimeError("the relay did not read its allowlist again")

    def shut_out(self, key):
        """Refuse a key's sign-ins and close its connections."""
        self.refused.add(key)
        self._reload()

    def let_in(self, key):
        self.refused.discard(key)
        self._reload()


def chosen(allow):
    """The relay tests run against: NOSTR_TEST_RELAY=native selects the native
    relay, otherwise the Python fixture."""
    if os.environ.get("NOSTR_TEST_RELAY") == "native":
        return NativeRelay(allow)
    from nostr_relay_fixture import GatedRelay
    return GatedRelay(allow)
