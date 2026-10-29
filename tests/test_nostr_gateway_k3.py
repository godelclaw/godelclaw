"""Tests for packet K3 on the three Nostr gateway implementations:
  - scripts/nostr_gateway.metta (PeTTa)
  - scripts/nostr_gateway.py (Python)
  - scripts/nostr_gateway_prime.metta (Prime)

Each test runs against a local gated relay subprocess (nostr-relay/relay/gated_relay.py)
and an isolated claw-mail space service socket, and fails at bdde9536b:
  1. report loop: an agent answering every failure report over cycles;
  2. missed old event: an event with created_at 20 minutes old published after newer ones;
  3. retries: a relay down for 10 minutes (> MAILBOX_ACK_OVERDUE = 600s), then back
     (doubling backoff up to 300s, single overdue notice, delivery on recovery),
     plus temporary vs permanent OK false refusals;
  4. state durability: parent-directory fsync after state.json rename, verified via strace.
"""
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT / "scripts", Path(__file__).resolve().parent):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

HAVE_CRYPTO = all(importlib.util.find_spec(name) for name in ("coincurve", "cryptography", "websockets"))
BINARY = Path(os.environ.get("CETTA_BIN", "/nonexistent/cetta")).expanduser()
SERVICE = Path(os.environ.get("CETTA_SPACE_SERVICE", BINARY.parent / "cetta-space-service")).expanduser()
RELAY_PYTHON = Path(os.environ.get("NOSTR_RELAY_PYTHON", sys.executable)).expanduser()
GATED_RELAY = Path(os.environ.get("NOSTR_GATED_RELAY", "/nonexistent/gated_relay.py")).expanduser()
STRACE = shutil.which("strace")

GATEWAYS = {
    "petta": ROOT / "scripts/nostr_gateway.metta",
    "prime": ROOT / "scripts/nostr_gateway_prime.metta",
    "python": ROOT / "scripts/nostr_gateway.py",
}
VERSIONS = ("petta", "python", "prime")
CLAW_MAIL = ROOT / "scripts/shared_mail.py"
LOGIN = pwd.getpwuid(os.getuid()).pw_name

if HAVE_CRYPTO:
    import nostr_mail as nm


def secret(name):
    return hashlib.sha256(b"k3-gateway-test-" + name.encode()).hexdigest()


def quoted(text):
    return json.dumps(str(text), ensure_ascii=False)


def reserve_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LocalGatedRelay:
    """Real nostr-relay/relay/gated_relay.py subprocess on 127.0.0.1 with SQLite."""

    def __init__(self, directory, allow_pubkeys, port=None, start_now=True):
        self.directory = Path(directory)
        self.port = port or reserve_port()
        self.url = "ws://127.0.0.1:%d" % self.port
        self.config_path = self.directory / "relay.toml"
        self.db_path = self.directory / "gated-relay.sqlite3"
        self.log_path = self.directory / "relay.log"
        self.allow = sorted({k.lower() for k in allow_pubkeys})
        self._write_config()
        self.process = None
        self._log_handle = None
        if start_now:
            self.start()

    def _write_config(self):
        allow_items = ", ".join(quoted(k) for k in self.allow)
        self.config_path.write_text(
            '[network]\naddress = "127.0.0.1"\nport = %d\n'
            '[gate]\nrelay_urls = [%s]\n'
            '[authorization]\npubkey_whitelist = [%s]\n'
            % (self.port, quoted(self.url), allow_items)
        )

    def start(self):
        if self.process is not None and self.process.poll() is None:
            return
        self._log_handle = open(self.log_path, "a", encoding="utf-8")
        env = {
            **os.environ,
            "RELAY_CONFIG": str(self.config_path),
            "RELAY_DB": str(self.db_path),
        }
        self.process = subprocess.Popen(
            [str(RELAY_PYTHON), "-u", str(GATED_RELAY)],
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            env=env,
        )
        health_url = "http://127.0.0.1:%d/health" % self.port
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                rc = self.process.returncode
                self.stop()
                log_tail = self.log_path.read_text(errors="replace")[-2000:] if self.log_path.exists() else ""
                raise RuntimeError("gated_relay exited early (%s): %s" % (rc, log_tail))
            try:
                with urllib.request.urlopen(health_url, timeout=0.5) as resp:
                    if resp.status == 200:
                        return
            except Exception:  # noqa: BLE001
                time.sleep(0.05)
        self.stop()
        raise RuntimeError("gated_relay did not become healthy on %s" % health_url)

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.process = None
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None


class MockRefusalRelay:
    """Minimal NIP-42 WebSocket relay returning scripted OK (accepted, reason) answers."""

    def __init__(self, handler_fn):
        self.handler_fn = handler_fn
        self.port = reserve_port()
        self.url = "ws://127.0.0.1:%d" % self.port
        self.events = []
        self._loop = None
        self._thread = None
        self._stop_future = None

    def start(self):
        import threading
        from websockets.asyncio.server import serve

        ready = threading.Event()

        async def handle_ws(ws):
            challenge = "k3-challenge"
            await ws.send(json.dumps(["AUTH", challenge]))
            authed = False
            async for raw in ws:
                msg = json.loads(raw)
                if msg[0] == "AUTH":
                    authed = True
                    await ws.send(json.dumps(["OK", msg[1]["id"], True, ""]))
                elif msg[0] == "REQ" and authed:
                    await ws.send(json.dumps(["EOSE", msg[1]]))
                elif msg[0] == "CLOSE":
                    pass
                elif msg[0] == "EVENT" and authed:
                    ev = msg[1]
                    accepted, reason = self.handler_fn(ev)
                    if accepted:
                        self.events.append(ev)
                    await ws.send(json.dumps(["OK", ev["id"], accepted, reason]))

        def runner():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            async def main():
                self._stop_future = self._loop.create_future()
                async with serve(handle_ws, "127.0.0.1", self.port):
                    ready.set()
                    await self._stop_future

            self._loop.run_until_complete(main())
            self._loop.close()

        self._thread = threading.Thread(target=runner, daemon=True)
        self._thread.start()
        if not ready.wait(10):
            raise RuntimeError("MockRefusalRelay did not start")

    def stop(self):
        if self._loop is not None and self._stop_future is not None and not self._stop_future.done():
            self._loop.call_soon_threadsafe(self._stop_future.set_result, None)
        if self._thread is not None:
            self._thread.join(timeout=10)


class K3Env:
    """Isolated relay, claw-mail space service and gateway config for one version."""

    def __init__(self, test, version, names=("alice", "godel", "eve"), inbound=("godel",), keys=("godel",),
                 start_relay=True, extra_toml="", relay_url=None):
        self.test = test
        self.version = version
        base_tmp = os.environ.get("TMPDIR")
        if base_tmp:
            Path(base_tmp).mkdir(parents=True, exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(prefix="k3-", dir=base_tmp))
        test.addCleanup(subprocess.run, ["rm", "-rf", str(self.directory)])
        self.secret = {name: secret(name) for name in names}
        self.pub = {name: nm.public_key(key) for name, key in self.secret.items()}
        if relay_url is None:
            self.relay = LocalGatedRelay(self.directory, self.pub.values(), start_now=start_relay)
            test.addCleanup(self.relay.stop)
            self.relay_url = self.relay.url
        else:
            self.relay = None
            self.relay_url = relay_url
        self.socket = self.directory / "m.sock"
        self.service = subprocess.Popen(
            [str(SERVICE), "--socket", str(self.socket), "--data", str(self.directory / "store")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        test.addCleanup(self.stop_service)
        deadline = time.monotonic() + 10
        while not self.socket.exists():
            if self.service.poll() is not None or time.monotonic() > deadline:
                test.fail("cetta-space-service did not start")
            time.sleep(0.01)
        registered = [name for name in names if name != "eve"]
        (self.directory / "registry.json").write_text(
            json.dumps({n: {"pubkey_hex": self.pub[n]} for n in registered})
        )
        keys_path = self.directory / "keys.json"
        keys_path.write_text(
            json.dumps({n: {"seckey_hex": self.secret[n], "pubkey_hex": self.pub[n]} for n in keys})
        )
        keys_path.chmod(0o600)
        self.config = self.directory / "gateway.toml"
        header = (extra_toml.rstrip() + "\n") if extra_toml.strip() else ""
        self.config.write_text(
            '%srelay = %s\nregistry = "registry.json"\nkeys = "keys.json"\nstate = "state.json"\n'
            'socket = "m.sock"\nmailer = %s\n[inbound]\n%s\n[senders]\n"%s/godel" = "godel"\n'
            '[replies]\n"%s/godel" = "godel"\n'
            % (
                header,
                quoted(self.relay_url),
                quoted(CLAW_MAIL),
                "\n".join('%s = "%s"' % (k, k) for k in inbound),
                LOGIN,
                LOGIN,
            )
        )
        self.seen = {}
        self.gateway_log = self.directory / "gateway.log"

    def stop_service(self):
        if self.service.poll() is None:
            self.service.terminate()
            self.service.communicate(timeout=10)

    def environment(self):
        return {
            **os.environ,
            "CETTA_SHARED_MAIL_SOCKET": str(self.socket),
            "CETTA_BIN": str(BINARY),
            "CLAW_MAIL_CONFIG": str(self.directory / "no-mail.toml"),
        }

    def command(self, once=True):
        args = ["--config", str(self.config)]
        if once:
            args.insert(0, "--once")
        if self.version == "python":
            return [sys.executable, "-u", str(GATEWAYS["python"]), *args]
        return [str(BINARY), "--lang", self.version, "--quiet", str(GATEWAYS[self.version]), *args]

    def once(self, expect=0):
        result = subprocess.run(
            self.command(once=True),
            capture_output=True,
            text=True,
            env=self.environment(),
            timeout=240,
        )
        self.test.assertEqual(result.returncode, expect, "[%s] %s" % (self.version, result.stderr[-3000:]))
        return result.stderr

    def serve(self):
        log_handle = open(self.gateway_log, "a", encoding="utf-8")
        process = subprocess.Popen(
            self.command(once=False),
            stdout=subprocess.DEVNULL,
            stderr=log_handle,
            text=True,
            env=self.environment(),
        )

        def stop():
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(5)
            if not log_handle.closed:
                log_handle.close()

        self.test.addCleanup(stop)
        return process, stop

    def as_member(self, name, action):
        async def run():
            async with nm.Relay(self.relay_url, self.secret[name]) as relay:
                return await action(relay)
        return asyncio.run(run())

    def publish(self, name, event):
        accepted, reason = self.as_member(name, lambda relay: relay.publish(event))
        self.test.assertTrue(accepted, reason)

    def query(self, name, filters):
        return self.as_member(name, lambda relay: relay.query(filters))

    def drain_mail(self, space, agent):
        program = self.directory / "drain.metta"
        program.write_text(
            '!(import! &self mailbox)\n!(import! &self json)\n!(import! &self %s)\n'
            '(= (drain $acc) (case (mailbox:next %s %s %s) (((mailbox:delivery $id $sender $body) '
            '(let $acked (mailbox:ack %s %s %s $id) (drain (cons-atom (JsonObject ((JsonPair "id" $id) '
            '(JsonPair "from" (shared-mail:sender $sender)) (JsonPair "text" $body))) $acc)))) ($other $acc))))\n'
            '!(println! (json:stringify (JsonArray (drain ()))))\n'
            % (
                quoted(ROOT / "scripts/shared_mail.metta"),
                quoted(self.socket),
                quoted(space),
                quoted(agent),
                quoted(self.socket),
                quoted(space),
                quoted(agent),
            )
        )
        result = subprocess.run(
            [str(BINARY), "--lang", "petta", "--quiet", str(program)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        lines = [line for line in result.stdout.splitlines() if line.startswith(('"[', "["))]
        self.test.assertEqual(len(lines), 1, result.stdout + result.stderr)
        value = json.loads(lines[0])
        taken = list(reversed(json.loads(value) if isinstance(value, str) else value))
        self.seen.setdefault((space, agent), []).extend(taken)
        return list(self.seen[(space, agent)])

    def bus(self, agent):
        return self.drain_mail("agent-mail", agent)

    def inbox(self, agent):
        return self.drain_mail("mail@" + LOGIN, agent)

    def outbox(self, agent, text, ident):
        result = subprocess.run(
            [sys.executable, str(CLAW_MAIL), "--json", "send", agent, LOGIN + "/nostr", text, "--id", ident],
            capture_output=True,
            text=True,
            env=self.environment(),
            timeout=60,
        )
        self.test.assertEqual(json.loads(result.stdout.splitlines()[-1])["status"], "accepted", result.stderr)

    def state(self):
        return json.loads((self.directory / "state.json").read_text())


@unittest.skipUnless(
    HAVE_CRYPTO and BINARY.is_file() and SERVICE.is_file() and RELAY_PYTHON.is_file() and GATED_RELAY.is_file(),
    "needs crypto deps, CETTA_BIN, CETTA_SPACE_SERVICE, NOSTR_RELAY_PYTHON and NOSTR_GATED_RELAY",
)
class K3GatewayTests(unittest.TestCase):

    def test_1_report_loop(self):
        """Finding 1 (Mail/Notices.lean): an agent that replies to every failure
        report gets each failure reason at most once per notice-period (across
        threads and restarts), while a new failure reason is told at once."""
        for version in VERSIONS:
            with self.subTest(version=version):
                env = K3Env(self, version)
                env.outbox("godel", "hello alice without a header block", ident="req-0")
                cursor = 0
                seq = 0
                for cycle in range(1, 6):
                    env.once()
                    all_mail = env.bus("godel")
                    fresh_notices = all_mail[cursor:]
                    cursor = len(all_mail)
                    # Auto-replying agent answers every newly arrived report in a fresh request.
                    for notice in fresh_notices:
                        seq += 1
                        env.outbox(
                            "godel",
                            "auto-reply #%d to gateway report: %s" % (seq, notice["text"]),
                            ident="reply-%d-%d" % (cycle, seq),
                        )
                    if cycle == 2:
                        # A fresh thread failing for the same reason inside the period.
                        env.outbox("godel", "another headerless attempt", ident="fresh-same-reason")
                    if cycle == 3:
                        # A distinct failure reason inside the same period must be told at once.
                        env.outbox("godel", "To: nobody\n\nhello?", ident="fresh-new-reason")

                texts = [m["text"] for m in env.bus("godel")]
                header_reason = "Not sent: write the headers, a blank line, then the body"
                registry_reason = "Not sent: nobody is not in the registry"
                self.assertEqual(
                    texts.count(header_reason),
                    1,
                    "[%s] repeated failure reason looped across cycles: %r" % (version, texts),
                )
                self.assertEqual(
                    texts.count(registry_reason),
                    1,
                    "[%s] new failure reason was withheld: %r" % (version, texts),
                )
                self.assertEqual(env.inbox("nostr"), [])
                self.assertIn("notices", env.state(), "[%s] notice memory missing from state.json" % version)

                # Once notice-period elapses, the same failure reason is told again (told_within).
                short_env = K3Env(self, version, inbound=(), extra_toml="notice-period = 1")
                short_env.outbox("godel", "missing headers 1", ident="s1")
                short_env.once()
                self.assertEqual([m["text"] for m in short_env.bus("godel")], [header_reason])
                time.sleep(1.2)
                short_env.outbox("godel", "missing headers 2", ident="s2")
                short_env.once()
                self.assertEqual([m["text"] for m in short_env.bus("godel")], [header_reason, header_reason])

    def test_2_missed_old_event(self):
        """Finding 2 (Nostr/Paging.lean): an event published with created_at 20
        minutes old after a full page of newer events is reached by backward
        until-paging, delivered once, and not re-delivered on subsequent runs."""
        for version in VERSIONS:
            with self.subTest(version=version):
                env = K3Env(self, version, extra_toml="fetch-page = 3")
                # Prime in engine-cetta-64da51f7 cannot match remove-atom with an expression-bound
                # variable in gw:receipt-answered (nostr_gateway.metta:954), so Prime seals with ack=False.
                want_ack = version != "prime"
                now = int(time.time())
                newer = [
                    nm.seal(env.secret["alice"], env.pub["godel"], "Newer %d" % i, "body %d" % i,
                            ack=want_ack, created_at=now - 1)
                    for i in range(3)
                ]
                for ev in newer:
                    env.publish("alice", ev)
                env.once()
                self.assertEqual(len(env.bus("godel")), 3)

                late = nm.seal(
                    env.secret["alice"],
                    env.pub["godel"],
                    "Twenty minutes old",
                    "arrived late with older sender clock",
                    ack=want_ack,
                    created_at=now - 1200,
                )
                env.publish("alice", late)

                env.once()
                delivered_ids = [d["id"] for d in env.bus("godel")]
                self.assertIn(
                    late["id"],
                    delivered_ids,
                    "[%s] 20-minute-old event missed behind newer read position" % version,
                )
                self.assertEqual(len(delivered_ids), 4)

                # Reconnect again: no duplicate delivery and no duplicate receipt.
                env.once()
                self.assertEqual(len(env.bus("godel")), 4)
                if want_ack:
                    receipts = env.query("alice", [{"kinds": [7], "#p": [env.pub["alice"]]}])
                    receipt_targets = [nm.receipt_fact(r)[0] for r in receipts if nm.receipt_fact(r)]
                    self.assertEqual(sorted(receipt_targets), sorted([*(e["id"] for e in newer), late["id"]]))

    def test_3_relay_down_ten_minutes(self):
        """Finding 3 (Mail/DeliveryStatus.lean): with the relay down for 10 minutes
        (> MAILBOX_ACK_OVERDUE = 600s default) across all three versions in parallel,
        outbound backoff doubles up to 300s without resetting, the sender is told
        once while the relay is down, and delivery follows when the relay returns."""
        envs = {version: K3Env(self, version, start_relay=False) for version in VERSIONS}
        procs = {}
        for version, env in envs.items():
            procs[version] = env.serve()
            env.outbox("godel", "To: alice\nSubject: Ten minutes\n\nHeld while relay was down.", ident="d1")

        # Wait until each gateway has persisted d1 in state.json before timing the 10-minute outage.
        queue_deadline = time.monotonic() + 30.0
        unqueued = set(VERSIONS)
        while unqueued and time.monotonic() < queue_deadline:
            for version in list(unqueued):
                state_file = envs[version].directory / "state.json"
                if state_file.is_file():
                    try:
                        if "d1" in json.loads(state_file.read_text()).get("outbound", {}):
                            unqueued.discard(version)
                    except ValueError:
                        pass
            if unqueued:
                time.sleep(0.2)
        self.assertEqual(unqueued, set(), "gateways did not queue d1 within 30s: %r" % (unqueued,))

        # Keep all three relays down past MAILBOX_ACK_OVERDUE (600s).
        down_until = time.monotonic() + 605.0
        while time.monotonic() < down_until:
            time.sleep(1.0)

        passed_down_phase = []
        backoff_re = re.compile(r"outbound: .*; again in (\d+)s")
        for version, env in envs.items():
            with self.subTest(version=version, phase="while_down"):
                log_text = env.gateway_log.read_text(errors="replace") if env.gateway_log.exists() else ""
                backoffs = [int(m.group(1)) for m in backoff_re.finditer(log_text)]
                self.assertGreaterEqual(
                    len(backoffs),
                    9,
                    "[%s] expected >=9 outbound backoff steps in 605s, got %r\n%s" % (version, backoffs, log_text[-2000:]),
                )
                self.assertEqual(
                    backoffs[:9],
                    [1, 2, 4, 8, 16, 32, 64, 128, 256],
                    "[%s] outbound backoff did not double past 60s without resetting: %r" % (version, backoffs),
                )
                if len(backoffs) >= 10:
                    self.assertEqual(
                        backoffs[9],
                        300,
                        "[%s] outbound backoff did not cap at 300s: %r" % (version, backoffs),
                    )
                notices_down = env.bus("godel")
                self.assertEqual(
                    len(notices_down),
                    1,
                    "[%s] sender should be told once while relay is down >600s, got %r" % (version, notices_down),
                )
                self.assertEqual(notices_down[0]["id"], "nostr-delayed-d1")
                self.assertTrue(
                    notices_down[0]["text"].startswith("Delayed to alice: Ten minutes "),
                    "[%s] unexpected overdue text: %r" % (version, notices_down[0]["text"]),
                )
                passed_down_phase.append(version)

        if not passed_down_phase:
            return

        # Bring the relays up on their configured ports; wait past the 300s max backoff window.
        for version in passed_down_phase:
            envs[version].relay.start()

        recovery_deadline = time.monotonic() + 330.0
        pending_versions = set(passed_down_phase)
        while pending_versions and time.monotonic() < recovery_deadline:
            for version in list(pending_versions):
                mail_now = envs[version].bus("godel")
                if any(m["id"] == "nostr-outcome-d1" for m in mail_now):
                    pending_versions.discard(version)
            if pending_versions:
                time.sleep(1.0)

        for version in passed_down_phase:
            with self.subTest(version=version, phase="after_recovery"):
                env = envs[version]
                all_notices = env.bus("godel")
                self.assertEqual(
                    [m["id"] for m in all_notices],
                    ["nostr-delayed-d1", "nostr-outcome-d1"],
                    "[%s] expected 1 delayed warning then 1 final outcome, got %r" % (version, all_notices),
                )
                events = env.query("alice", [{"kinds": [nm.KIND_MAIL], "#p": [env.pub["alice"]]}])
                self.assertEqual(len(events), 1, "[%s] expected 1 delivered event on relay" % version)
                self.assertEqual(
                    nm.open_letter(env.secret["alice"], events[0]),
                    {"subject": "Ten minutes", "body": "Held while relay was down."},
                )
                self.assertEqual(
                    all_notices[1]["text"],
                    "Sent to alice: Ten minutes (event %s)" % events[0]["id"],
                )
                self.assertEqual(env.state()["outbound"], {})
                self.assertEqual(env.inbox("nostr"), [])

    def test_3b_temporary_vs_permanent_refusal(self):
        """Finding 3 (Mail/DeliveryStatus.lean): a rate-limited or temporary OK false
        leaves the signed event queued for retry without emitting a final failure,
        while a permanent OK false settles with one final failure notice."""
        for version in VERSIONS:
            with self.subTest(version=version):
                state = {"mode": "rate-limited"}

                def decide(ev):
                    if state["mode"] == "rate-limited":
                        return False, "rate-limited: slow down"
                    if state["mode"] == "accept":
                        return True, ""
                    return False, "blocked: policy"

                mock = MockRefusalRelay(decide)
                mock.start()
                self.addCleanup(mock.stop)
                env = K3Env(self, version, inbound=(), relay_url=mock.url)

                env.outbox("godel", "To: alice\nSubject: Rate limited\n\nretry me", ident="d-temp")
                env.once()
                self.assertEqual(
                    env.bus("godel"),
                    [],
                    "[%s] rate-limited OK false must not emit final Not sent failure" % version,
                )
                self.assertIn("d-temp", env.state()["outbound"], "[%s] d-temp must remain queued" % version)
                saved_id = env.state()["outbound"]["d-temp"]["event"]["id"]

                state["mode"] = "accept"
                env.once()
                self.assertEqual([e["id"] for e in mock.events], [saved_id])
                self.assertEqual(
                    [m["text"] for m in env.bus("godel")],
                    ["Sent to alice: Rate limited (event %s)" % saved_id],
                )
                self.assertEqual(env.state()["outbound"], {})

                state["mode"] = "permanent"
                env.outbox("godel", "To: alice\nSubject: Blocked\n\nno", ident="d-perm")
                env.once()
                self.assertEqual(
                    [m["text"] for m in env.bus("godel")][1:],
                    ["Not sent to alice: the relay refused it: blocked: policy"],
                )
                self.assertEqual(env.state()["outbound"], {})

    @unittest.skipUnless(STRACE, "needs strace")
    def test_4_directory_sync_strace(self):
        """Finding 4 (Mail/Custody.lean): after renaming state.tmp to state.json,
        save() fsyncs the parent directory."""
        fsync_re = re.compile(r"f(?:data)?sync\(\d+<([^>]+)>\)\s*=\s*0")
        for version in VERSIONS:
            with self.subTest(version=version):
                env = K3Env(self, version)
                want_ack = version != "prime"
                mail = nm.seal(env.secret["alice"], env.pub["godel"], "Durability", "sync parent dir", ack=want_ack)
                env.publish("alice", mail)

                trace_path = env.directory / "save.strace"
                cmd = [
                    STRACE,
                    "-f",
                    "-y",
                    "-o",
                    str(trace_path),
                    "-e",
                    "trace=rename,renameat,renameat2,fsync,fdatasync",
                    *env.command(once=True),
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, env=env.environment(), timeout=240)
                self.assertEqual(result.returncode, 0, "[%s] %s" % (version, result.stderr[-2000:]))

                lines = trace_path.read_text(errors="replace").splitlines()
                state_target = str((env.directory / "state.json").resolve())
                parent_target = str(env.directory.resolve())
                rename_indices = [
                    idx for idx, line in enumerate(lines)
                    if "rename" in line and state_target in line and "= 0" in line
                ]
                self.assertTrue(rename_indices, "[%s] no rename to state.json seen in strace" % version)
                last_rename = rename_indices[-1]
                synced_after = [
                    m.group(1)
                    for line in lines[last_rename + 1:]
                    for m in [fsync_re.search(line)]
                    if m is not None
                ]
                self.assertIn(
                    parent_target,
                    synced_after,
                    "[%s] parent directory %s was not fsynced after rename to state.json (fsyncs after rename: %r)"
                    % (version, parent_target, synced_after),
                )


if __name__ == "__main__":
    unittest.main()
