"""Native coding-estate observation parity, sentinel, read-only, and cache tests."""

import hashlib
import json
import os
from pathlib import Path
import socket as sockets
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from isolated_env import isolated_environ

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "src"), str(ROOT / "channels")]

import coding_estate  # noqa: E402
import coding_estate_snapshot  # noqa: E402
from test_input_ledger_remote import DbServer, WORKER, available as ledger_available  # noqa: E402


SENTINEL_SOURCE = """\
import json
from pathlib import Path
import sys

import coding_estate
import coding_estate_snapshot
import input_ack

FORBIDDEN_FILES = ("/coding_estate_snapshot.py", "/coding_estate.py")
PROFILE_EVENTS = []

# Capture original callables BEFORE forbid() replaces module attributes,
# so positive-control tests can prove sys.setprofile catches retained callables.
CAPTURED_SNAPSHOT = coding_estate_snapshot.snapshot
CAPTURED_CLASSIFY = coding_estate.classify


def forbidden(*args, **kwargs):
    raise AssertionError("forbidden Python coding-estate helper invoked")


def _profile(frame, event, arg):
    if event != "call":
        return
    code = frame.f_code
    filename = code.co_filename.replace("\\\\", "/")
    if filename.endswith(FORBIDDEN_FILES) and code.co_name != "<module>":
        entry = "%s:%s" % (Path(filename).name, code.co_name)
        PROFILE_EVENTS.append(entry)
        raise AssertionError("native path entered Python %s" % entry)
    if filename.endswith("/input_ack.py") and code.co_name == "_ledger_call":
        if frame.f_locals.get("op") == "llm-selection":
            PROFILE_EVENTS.append("input_ack.py:_ledger_call(llm-selection)")
            raise AssertionError("native path called input_ack._ledger_call('llm-selection')")


def arm():
    for attr in ("snapshot", "_session", "_date"):
        setattr(coding_estate_snapshot, attr, forbidden)
    for attr in (
        "classify", "listed", "context_view", "_read_json", "read_runs",
        "read_lifecycle", "_engine_sha256", "_dir_blocks", "llm_ledger_selection",
        "_matched_retirements", "snapshot_world", "snapshot_session_world",
        "_status_date", "launching_date", "work_setting", "_runtimes_summary",
    ):
        setattr(coding_estate, attr, forbidden)
    sys.setprofile(_profile)
    return 1


def call_captured_snapshot(root):
    return CAPTURED_SNAPSHOT(root, "2026-10-25")


def get_events():
    return json.dumps(PROFILE_EVENTS)


def write_text(path, text):
    Path(path).write_text(str(text), encoding="utf-8")
    return len(PROFILE_EVENTS)


def write_json(path, payload):
    Path(path).write_text(json.dumps(payload), encoding="utf-8")
    return len(PROFILE_EVENTS)
"""


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _tree_manifest(root):
    """Capture cryptographic, permission, inode, and mtime state of a directory tree."""
    entries = []
    base = Path(root)
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames.sort()
        filenames.sort()
        current = Path(dirpath)
        for candidate in [current] + [current / name for name in filenames]:
            rel = "." if candidate == base else str(candidate.relative_to(base))
            st = os.lstat(candidate)
            kind = (
                "symlink" if stat.S_ISLNK(st.st_mode)
                else "dir" if stat.S_ISDIR(st.st_mode)
                else "file" if stat.S_ISREG(st.st_mode)
                else "other"
            )
            digest = None
            target = None
            if kind == "file":
                try:
                    digest = _sha256_bytes(candidate.read_bytes())
                except OSError:
                    digest = "unreadable"
            elif kind == "symlink":
                target = os.readlink(candidate)
            entries.append({
                "rel": rel,
                "kind": kind,
                "mode": stat.S_IMODE(st.st_mode),
                "ino": st.st_ino,
                "size": st.st_size,
                "mtime_ns": st.st_mtime_ns,
                "sha256": digest,
                "target": target,
            })
    entries.sort(key=lambda item: item["rel"])
    return entries


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set CETTA_BIN to run native coding-estate IO tests")
class CodingEstateNativeIoTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        coding_estate._engine_digest_cache.clear()
        coding_estate._runtime_blocks_cache.clear()

        self.sentinel = self.directory / "estate_io_sentinel.py"
        self.sentinel.write_text(SENTINEL_SOURCE, encoding="utf-8")

        self.probe_dir = self.directory / "probe_root"
        self.probe_dir.mkdir(parents=True, exist_ok=True)
        for subdir in ("src", "channels", "tests"):
            (self.probe_dir / subdir).symlink_to(ROOT / subdir)

        self.estate = self.directory / "estate"
        self.runtimes = self.estate / "memory" / "coding-runtimes"
        self.coding = self.estate / "memory" / "coding"
        self.runtimes.mkdir(parents=True)
        self.coding.mkdir(parents=True)

        self.engine_bytes = b"#!/bin/sh\necho fixture-engine\nexit 0\n"
        self.engine_hash = _sha256_bytes(self.engine_bytes)
        self.engine = self.directory / "cetta-engine"
        self.engine.write_bytes(self.engine_bytes)
        self.engine.chmod(0o755)

        self.bundle_current_digest = "aaaa1111" + "0" * 56
        self.bundle_current = self.runtimes / self.bundle_current_digest
        (self.bundle_current / "src").mkdir(parents=True)
        (self.bundle_current / "coding_main.metta").write_text("; current\n", encoding="utf-8")
        (self.bundle_current / "src" / "llm_ledger.metta").write_text("; native\n", encoding="utf-8")

        self.bundle_old_digest = "8acffdbd" + "0" * 56
        self.bundle_old = self.runtimes / self.bundle_old_digest
        (self.bundle_old / "src").mkdir(parents=True)
        (self.bundle_old / "coding_main.metta").write_text("; legacy sqlite\n", encoding="utf-8")

        self.fixed_ts = time.mktime(time.strptime("2026-10-23 12:00:00", "%Y-%m-%d %H:%M:%S"))
        self.pin_ts = time.mktime(time.strptime("2026-10-22 09:00:00", "%Y-%m-%d %H:%M:%S"))

        self.base_env = {
            **isolated_environ(),
            "PYTHONHOME": sys.prefix,
            "LD_LIBRARY_PATH": str(Path(sys.prefix) / "lib"),
            "PYTHONPATH": os.pathsep.join([
                str(self.directory),
                str(ROOT / "tests"),
                str(ROOT / "src"),
                str(ROOT / "channels"),
            ]),
        }
        self._seq = 0

    def make_session(
        self,
        name,
        *,
        root=None,
        status="stopped",
        updated="default",
        pin=True,
        bundle="default",
        digest="default",
        binary="default",
        binary_sha256="default",
        phase="ended",
        at="default",
        write_state=True,
        write_task=True,
    ):
        coding_dir = (self.estate if root is None else Path(root)) / "memory" / "coding"
        coding_dir.mkdir(parents=True, exist_ok=True)
        session = coding_dir / name
        session.mkdir(parents=True, exist_ok=True)
        if write_task:
            task_path = session / "task.json"
            task_path.write_text('{"task":"fixture"}\n', encoding="utf-8")
            os.utime(task_path, (self.fixed_ts, self.fixed_ts))
        if write_state:
            state_payload = {"status": status, "steps": 2}
            if updated == "default":
                state_payload["updated"] = self.fixed_ts
            elif updated is not None:
                state_payload["updated"] = updated
            state_path = session / "state.json"
            state_path.write_text(json.dumps(state_payload), encoding="utf-8")
            os.utime(state_path, (self.fixed_ts, self.fixed_ts))
        if pin:
            chosen_bundle = self.bundle_current if bundle == "default" else bundle
            runtime_payload = {}
            if chosen_bundle is not None:
                runtime_payload["root"] = str(chosen_bundle)
            if digest == "default":
                if chosen_bundle is not None:
                    runtime_payload["digest"] = Path(str(chosen_bundle)).name
            elif digest is not None:
                runtime_payload["digest"] = digest
            chosen_binary = str(self.engine) if binary == "default" else binary
            if chosen_binary is not None:
                runtime_payload["binary"] = chosen_binary
            chosen_sha = self.engine_hash if binary_sha256 == "default" else binary_sha256
            if chosen_sha is not None:
                runtime_payload["binary_sha256"] = chosen_sha
            if phase is not None:
                runtime_payload["phase"] = phase
            if at == "default":
                runtime_payload["at"] = self.pin_ts
            elif at is not None:
                runtime_payload["at"] = at
            rt_path = session / "runtime.json"
            rt_path.write_text(json.dumps(runtime_payload), encoding="utf-8")
            os.utime(rt_path, (self.pin_ts, self.pin_ts))
        os.utime(session, (self.fixed_ts, self.fixed_ts))
        return session

    def _metta_quote(self, value):
        return json.dumps(str(value), ensure_ascii=False)

    def run_native(self, body, *, socket_path="", cwd=None, extra_env=None, check=True, timeout=45):
        self._seq += 1
        probe = self.probe_dir / ("probe_%d.metta" % self._seq)
        script = (
            "!(import! &self (library lib_import))\n"
            "!(import! &self %s)\n" % self._metta_quote(self.sentinel)
            + "!(py-call (estate_io_sentinel.arm))\n"
            + "!(import! &self ./src/coding_estate_cetta.metta)\n"
            + body
        )
        probe.write_text(script, encoding="utf-8")
        env = dict(self.base_env)
        if socket_path:
            env["METTACLAW_INPUT_LEDGER_SOCKET"] = str(socket_path)
        else:
            env.pop("METTACLAW_INPUT_LEDGER_SOCKET", None)
        if extra_env:
            env.update(extra_env)
        try:
            result = subprocess.run(
                [os.environ["CETTA_BIN"], "--lang", "petta", "--import-mode", "ancestor-walk", str(probe)],
                cwd=self.estate if cwd is None else cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        finally:
            probe.unlink(missing_ok=True)
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def observe_both(self, root=None, *, today="2026-10-25", socket_path=""):
        target = self.estate if root is None else Path(root)
        self._seq += 1
        snap_file = self.directory / ("snap_%d.json" % self._seq)
        view_file = self.directory / ("view_%d.txt" % self._seq)
        load_file = self.directory / ("load_%d.txt" % self._seq)
        body = (
            '!(let $s (estate:snapshot %s %s)\n'
            '   (py-call (estate_io_sentinel.write_text %s\n'
            '     (json:stringify-python-with $s ((separators "," ":"))))))\n'
            % (self._metta_quote(target), self._metta_quote(today), self._metta_quote(snap_file))
            + '!(let $v (estate:context-view-root %s %s)\n'
            '   (py-call (estate_io_sentinel.write_text %s (if (== $v EstateMissing) "__MISSING__" $v))))\n'
            % (self._metta_quote(target), self._metta_quote(today), self._metta_quote(view_file))
            + "!(py-call (estate_io_sentinel.write_text %s (repr (estate:load))))\n"
            % self._metta_quote(load_file)
        )
        self.run_native(body, socket_path=socket_path, cwd=target)
        nat_snap = json.loads(snap_file.read_text(encoding="utf-8"))
        raw_view = view_file.read_text(encoding="utf-8")
        nat_view = None if raw_view == "__MISSING__" else raw_view
        nat_load = load_file.read_text(encoding="utf-8").strip()

        old_sock = os.environ.get("METTACLAW_INPUT_LEDGER_SOCKET")
        if socket_path:
            os.environ["METTACLAW_INPUT_LEDGER_SOCKET"] = str(socket_path)
        else:
            os.environ.pop("METTACLAW_INPUT_LEDGER_SOCKET", None)
        try:
            coding_estate._engine_digest_cache.clear()
            coding_estate._runtime_blocks_cache.clear()
            py_snap = json.loads(coding_estate_snapshot.snapshot(target, today))
            py_view = coding_estate.context_view(
                target,
                world=coding_estate.World(today=today if today else None),
            )
        finally:
            if old_sock is None:
                os.environ.pop("METTACLAW_INPUT_LEDGER_SOCKET", None)
            else:
                os.environ["METTACLAW_INPUT_LEDGER_SOCKET"] = old_sock

        return nat_snap, py_snap, nat_view, py_view, nat_load

    def _start_counting_socket(self, upstream=None, mode="relay"):
        """Wrap an upstream socket (or simulate hang/drop) and count requests."""
        self._seq += 1
        sock_path = self.directory / ("proxy_%d.sock" % self._seq)
        listener = sockets.socket(sockets.AF_UNIX)
        listener.bind(str(sock_path))
        listener.listen(16)
        calls = []

        def close_quietly(*ends):
            for end in ends:
                try:
                    end.shutdown(sockets.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    end.close()
                except OSError:
                    pass

        def handle(client):
            if mode == "timeout":
                try:
                    data = client.recv(65536)
                    if b"llm-selection" in data:
                        calls.append("llm-selection")
                    time.sleep(1.5)
                except OSError:
                    pass
                finally:
                    close_quietly(client)
                return
            if mode == "drop":
                calls.append("drop")
                close_quietly(client)
                return

            server_conn = sockets.socket(sockets.AF_UNIX)
            try:
                server_conn.connect(str(upstream))
            except OSError:
                close_quietly(client, server_conn)
                return

            def back():
                try:
                    while reply := server_conn.recv(65536):
                        client.sendall(reply)
                except OSError:
                    pass
                finally:
                    close_quietly(client, server_conn)

            threading.Thread(target=back, daemon=True).start()
            recorded = False
            buf = b""
            try:
                while chunk := client.recv(65536):
                    buf += chunk
                    if not recorded and b"llm-selection" in buf:
                        recorded = True
                        calls.append("llm-selection")
                    server_conn.sendall(chunk)
                try:
                    server_conn.shutdown(sockets.SHUT_WR)
                except OSError:
                    pass
            except OSError:
                close_quietly(client, server_conn)

        def accept_loop():
            while True:
                try:
                    client, _ = listener.accept()
                except OSError:
                    return
                threading.Thread(target=handle, args=(client,), daemon=True).start()

        threading.Thread(target=accept_loop, daemon=True).start()
        self.addCleanup(close_quietly, listener)
        return str(sock_path), calls

    def _start_custom_ledger_server(self, answer_expr):
        """Start a real ValueServiceV2 Unix socket server returning answer_expr for LedgerCall."""
        self._seq += 1
        srv_dir = self.directory / ("custom_srv_%d" % self._seq)
        srv_dir.mkdir(parents=True, exist_ok=True)
        store_dir = srv_dir / "store"
        sock_path = srv_dir / "s.sock"
        mod_path = self.probe_dir / ("custom_ledger_srv_%d.metta" % self._seq)
        mod_path.write_text(
            "!(import! &self ./src/input_ledger_server.metta)\n"
            "(= (custom-srv:call (ServiceCallV1 $cfg $peer $state $req))\n"
            "   (LedgerAnswersV1 (%s)))\n" % answer_expr
            + "(= (custom-srv:configuration)\n"
            + "   (let (ValueServiceV2 $sock $data $routes $limits $maint) (db-server:configuration)\n"
            + "     (ValueServiceV2 $sock $data\n"
            + "       (cons-atom (ServiceRouteV1 LedgerCall Write custom-srv:call)\n"
            + "         (filter-atom $routes $r\n"
            + "           (let (ServiceRouteV1 $name $mode $fn) $r (not (== $name LedgerCall)))))\n"
            + "       $limits $maint)))\n",
            encoding="utf-8",
        )
        self.addCleanup(lambda: mod_path.unlink(missing_ok=True))
        env = dict(os.environ, CETTA_DB_SOURCE=str(store_dir), CETTA_DB_SOCKET=str(sock_path))
        log_path = srv_dir / "server.log"
        with open(log_path, "wb") as log_stream:
            proc = subprocess.Popen(
                [
                    str(WORKER),
                    "--value-bytes",
                    "8388608",
                    "--serve",
                    "--lang",
                    "petta",
                    str(mod_path),
                    "custom-srv:configuration",
                ],
                cwd=self.probe_dir,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
            )

        def stop_proc():
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)

        self.addCleanup(stop_proc)
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise AssertionError("custom ledger server exited: %s" % log_path.read_text(encoding="utf-8", errors="replace"))
            if sock_path.exists():
                try:
                    probe = sockets.socket(sockets.AF_UNIX)
                    probe.connect(str(sock_path))
                    probe.close()
                    return str(sock_path)
                except OSError:
                    probe.close()
            time.sleep(0.02)
        raise AssertionError("custom ledger server timed out starting")

    def test_pre_import_and_profile_sentinel_catches_retained_python_callables(self):
        self.make_session("work-ok", status="stopped")
        # Positive control: even a callable captured BEFORE arm() is caught by sys.setprofile.
        caught = self.run_native(
            "!(println! (collapse (catch (py-call (estate_io_sentinel.call_captured_snapshot %s)))))\n"
            "!(println! (py-call (estate_io_sentinel.get_events)))\n"
            % self._metta_quote(self.estate)
        )
        self.assertIn("AssertionError", caught.stdout)
        self.assertIn("coding_estate_snapshot.py:snapshot", caught.stdout)

        # Native path itself triggers zero forbidden calls.
        nat_snap, py_snap, nat_view, py_view, nat_load = self.observe_both()
        self.assertEqual(nat_snap, py_snap)
        self.assertEqual(nat_view, py_view)
        self.assertTrue(nat_load.startswith('("value" '))

    def test_snapshot_and_view_parity_across_missing_malformed_and_corrupt_files(self):
        # 1. Missing memory/coding directory -> available=False, EstateMissing / ("missing")
        empty_root = self.directory / "no_coding_root"
        empty_root.mkdir()
        nat_snap, py_snap, nat_view, py_view, nat_load = self.observe_both(empty_root)
        self.assertEqual(nat_snap, py_snap)
        self.assertFalse(nat_snap["available"])
        self.assertIsNone(nat_view)
        self.assertIsNone(py_view)
        self.assertEqual(nat_load, '("missing")')

        # 2. memory/coding is a regular file instead of a directory
        file_root = self.directory / "file_coding_root"
        (file_root / "memory").mkdir(parents=True)
        (file_root / "memory" / "coding").write_text("not a dir\n", encoding="utf-8")
        nat_snap, py_snap, nat_view, py_view, nat_load = self.observe_both(file_root)
        self.assertEqual(nat_snap, py_snap)
        self.assertIsNone(nat_view)
        self.assertEqual(nat_load, '("missing")')

        # 3. Malformed JSON, non-dict JSON roots, duplicate keys, CRLF, invalid UTF-8
        (self.estate / "memory" / "coding-settings.json").write_bytes(b"\xff\xfe{\"context\":\"off\"}")
        (self.coding / ".hidden-session").mkdir()
        (self.runtimes / ".hidden-runtime").mkdir()

        s_bad_json = self.make_session("work-01-bad-json", status="stopped")
        (s_bad_json / "state.json").write_text("{corrupt", encoding="utf-8")
        (s_bad_json / "lifecycle.json").write_text("[1, 2, 3]", encoding="utf-8")

        s_non_dict = self.make_session("work-02-non-dict", status="stopped")
        (s_non_dict / "state.json").write_text('"just-a-string"\r\n', encoding="utf-8")
        (s_non_dict / "runtime.json").write_text("42", encoding="utf-8")
        (s_non_dict / "lifecycle.json").write_bytes(b'{"hold":{"why":"\xffbad-utf8"}}')

        s_dup = self.make_session("work-03-dup-keys", status="running")
        (s_dup / "state.json").write_text(
            '{"status":"running","status":"failed","updated":%s}\r\n' % json.dumps(self.fixed_ts),
            encoding="utf-8",
        )
        (s_dup / "lifecycle.json").write_text(
            '{"hold":{"why":"first","review":"2026-10-20"},"hold":{"why":"second","review":"2026-10-21"}}',
            encoding="utf-8",
        )

        s_nostate = self.make_session("work-04-nostate-notask", write_state=False, write_task=False, pin=False)
        os.utime(s_nostate, (self.fixed_ts, self.fixed_ts))

        nat_snap, py_snap, nat_view, py_view, _ = self.observe_both()
        self.assertEqual(nat_snap, py_snap)
        self.assertEqual(nat_view, py_view)

        # 4. Unreadable directories (when non-root):
        #    - 0o111 on runtimes: stat of pinned child succeeds, opendir fails -> runtimes=[]
        #    - 0o000 on runtimes while session references child: Python raises PermissionError -> ("unavailable")
        #    - 0o000 on coding: is_dir() is True, opendir fails -> available=False -> ("missing")
        if os.geteuid() != 0:
            os.chmod(self.runtimes, 0o111)
            self.addCleanup(lambda: os.chmod(self.runtimes, 0o755))
            nat_snap_r, py_snap_r, nat_view_r, py_view_r, _ = self.observe_both()
            self.assertEqual(nat_snap_r, py_snap_r)
            self.assertEqual(nat_snap_r["runtimes"], [])
            self.assertEqual(nat_view_r, py_view_r)

            os.chmod(self.runtimes, 0o000)
            with self.assertRaises(PermissionError):
                coding_estate_snapshot.snapshot(self.estate, "2026-10-25")
            unavail = self.run_native("!(println! (estate:load))\n", cwd=self.estate)
            self.assertIn('("unavailable")', unavail.stdout)
            os.chmod(self.runtimes, 0o755)

            os.chmod(self.coding, 0o000)
            self.addCleanup(lambda: os.chmod(self.coding, 0o755))
            nat_snap_c, py_snap_c, nat_view_c, py_view_c, nat_load_c = self.observe_both()
            self.assertEqual(nat_snap_c, py_snap_c)
            self.assertFalse(nat_snap_c["available"])
            self.assertIsNone(nat_view_c)
            self.assertIsNone(py_view_c)
            self.assertEqual(nat_load_c, '("missing")')
            os.chmod(self.coding, 0o755)

    def test_embedded_nul_path_cannot_observe_an_existing_prefix(self):
        self.make_session("work-nul-engine", binary=str(self.engine) + "\0suffix")
        # Python's hashing path rejects embedded NUL rather than hashing the
        # valid prefix. The native view must likewise become unavailable.
        with self.assertRaises(ValueError):
            coding_estate_snapshot.snapshot(self.estate, "2026-10-25")
        bad_file = '(str:concat %s (str:concat (str:from-codepoints (0)) "suffix"))' % self._metta_quote(self.engine)
        bad_dir = '(str:concat %s (str:concat (str:from-codepoints (0)) "suffix"))' % self._metta_quote(self.directory)
        result = self.run_native(
            '!(let $p %s (println! (if (or (eio:os-exists? $p) (eio:is-file? $p)) "NUL_PREFIX_BAD" "NUL_FILE_REFUSED")))\n' % bad_file
            + '!(println! (if (eio:is-dir? %s) "NUL_PREFIX_BAD" "NUL_DIR_REFUSED"))\n' % bad_dir
            + '!(println! (estate:load))\n', cwd=self.estate)
        self.assertIn("NUL_FILE_REFUSED", result.stdout)
        self.assertIn("NUL_DIR_REFUSED", result.stdout)
        self.assertNotIn("NUL_PREFIX_BAD", result.stdout)
        self.assertIn('(\"unavailable\")', result.stdout)

    def test_path_fast_route_preserves_posix_spelling_and_normalized_children(self):
        from pathlib import PurePosixPath
        cases = ['', '.', '..', '/', '//', '///', './a', 'a//b/./c/',
                 '/a/../b', '//a/./b/', 'café/λ', 'a/.hidden', 'a/..hidden']
        output = self.directory / 'path-results.json'
        values = '(' + ' '.join(self._metta_quote(value) for value in cases) + ')'
        self.run_native(
            '!(let $rows (map-atom %s $p\n'
            '    (let $normal (eio:path $p)\n'
            '      (JsonArray ($normal (eio:basename $p) (eio:child $normal "state.json")))))\n'
            '   (py-call (estate_io_sentinel.write_text %s (json:stringify-python (JsonArray $rows)))))\n'
            % (values, self._metta_quote(output)))
        expected = [[str(PurePosixPath(value)), PurePosixPath(value).name,
                     str(PurePosixPath(value) / 'state.json')] for value in cases]
        self.assertEqual(json.loads(output.read_text()), expected)

    def test_shared_world_facts_refresh_between_views_in_the_same_process(self):
        for index in range(3):
            self.make_session('work-shared-%d' % index)
        changed = 'changed engine bytes with a different size\n'
        output = self.directory / 'world-refresh.json'
        self.run_native(
            '!(let* (($first (eio:snapshot %s "2026-10-25"))\n'
            '         ($_ (py-call (estate_io_sentinel.write_text %s %s)))\n'
            '         ($second (eio:snapshot %s "2026-10-25")))\n'
            '   (py-call (estate_io_sentinel.write_text %s\n'
            '     (json:stringify-python (JsonArray ($first $second))))))\n'
            % (self._metta_quote(self.estate), self._metta_quote(self.engine),
               self._metta_quote(changed), self._metta_quote(self.estate), self._metta_quote(output)))
        before, after = json.loads(output.read_text())
        self.assertEqual([row['engine_hash'] for row in before['sessions']], [self.engine_hash] * 3)
        expected = hashlib.sha256(changed.encode()).hexdigest()
        self.assertEqual([row['engine_hash'] for row in after['sessions']], [expected] * 3)

    def test_runs_jsonl_torn_tails_non_dicts_invalid_utf8_and_repin_dates(self):
        repin_ts = time.mktime(time.strptime("2026-10-24 16:30:00", "%Y-%m-%d %H:%M:%S"))

        # Torn final line dropped; malformed middle line skipped; non-dict lines ignored.
        s1 = self.make_session("work-runs-torn", status="stopped")
        (s1 / "runs.jsonl").write_text(
            "\n"
            '{"run": 1, "at": %s}\r\n' % self.pin_ts
            + "[1, 2, 3]\n"
            + "{malformed middle line}\n"
            + "null\n"
            + "123\n"
            + '{"run": 2, "at": %s}\u2028' % repin_ts
            + '{"run": 3, "torn_tail": ',
            encoding="utf-8",
        )

        # Invalid UTF-8 inside string replaced via U+FFFD; missing/non-numeric "at" falls back to pin_date.
        s2 = self.make_session("work-runs-utf8-fallback", status="stopped")
        (s2 / "runs.jsonl").write_bytes(
            b'{"note": "bad\xffbyte", "at": "not-a-number"}\n'
        )

        # Boolean "at": True/False is an instance of int in Python (1.0 / 0.0 epoch seconds).
        s3 = self.make_session("work-runs-bool-at", status="stopped")
        (s3 / "runs.jsonl").write_text('{"at": true}\n', encoding="utf-8")

        # runs.jsonl is a directory -> treated as absent ([]).
        s4 = self.make_session("work-runs-dir", status="stopped")
        (s4 / "runs.jsonl").mkdir()

        nat_snap, py_snap, nat_view, py_view, _ = self.observe_both()
        self.assertEqual(nat_snap, py_snap)
        self.assertEqual(nat_view, py_view)
        by_id = {entry["id"]: entry for entry in nat_snap["sessions"]}
        self.assertEqual(by_id["work-runs-torn"]["runs_count"], 2)
        self.assertEqual(by_id["work-runs-torn"]["repin_date"], "2026-10-24")
        self.assertEqual(by_id["work-runs-utf8-fallback"]["runs_count"], 1)
        self.assertEqual(by_id["work-runs-utf8-fallback"]["repin_date"], "2026-10-22")

    @unittest.skipUnless(ledger_available(), "native ledger worker binary unavailable")
    def test_all_six_ledger_selection_outcomes_and_at_most_once_socket_rule(self):
        # A. Zero matched sessions -> 0 socket connections even when socket is configured.
        self.make_session("work-current-only", status="stopped", bundle=self.bundle_current)
        ledger_dir = self.directory / "ledger_native"
        ledger_dir.mkdir()
        db_server = DbServer(ledger_dir)
        db_server.start()
        self.addCleanup(db_server.stop)

        sock_zero, calls_zero = self._start_counting_socket(db_server.socket)
        nat_snap, py_snap, nat_view, py_view, _ = self.observe_both(socket_path=sock_zero)
        self.assertEqual(nat_snap, py_snap)
        self.assertEqual(nat_view, py_view)
        self.assertEqual(nat_snap["selection"], {})
        self.assertEqual(calls_zero, [])

        # B. Five matched sessions + "native" -> exactly 1 selection query per snapshot/view.
        for idx in range(5):
            self.make_session("work-old-%d" % idx, status="stopped", bundle=self.bundle_old)
        sock_nat, calls_nat = self._start_counting_socket(db_server.socket)
        single_out = self.directory / "single_nat_view.txt"
        self.run_native(
            '!(py-call (estate_io_sentinel.write_text %s (estate:context-view-root %s "2026-10-25")))\n'
            % (self._metta_quote(single_out), self._metta_quote(self.estate)),
            socket_path=sock_nat,
        )
        self.assertEqual(calls_nat, ["llm-selection"])
        calls_nat.clear()
        nat_snap, py_snap, nat_view, py_view, _ = self.observe_both(socket_path=sock_nat)
        self.assertEqual(nat_snap, py_snap)
        self.assertEqual(nat_view, py_view)
        self.assertEqual(nat_snap["selection"], {"llm-ledger-sqlite": "native"})
        # observe_both runs 3 native snapshots + 2 Python oracle snapshots = 5 total (1 per snapshot across 5 sessions).
        self.assertEqual(len(calls_nat), 5)
        self.assertIn("needs-current: it uses the retired SQLite LLM ledger", nat_view)

        # C. "legacy" over real ValueServiceV2 socket -> resumable!
        raw_legacy = self._start_custom_ledger_server("legacy")
        sock_leg, calls_leg = self._start_counting_socket(raw_legacy)
        nat_snap, py_snap, nat_view, py_view, _ = self.observe_both(socket_path=sock_leg)
        self.assertEqual(nat_snap, py_snap)
        self.assertEqual(nat_view, py_view)
        self.assertEqual(nat_snap["selection"], {"llm-ledger-sqlite": "legacy"})
        self.assertEqual(len(calls_leg), 5)
        self.assertIn("work-old-0 stopped 2026-10-23 · pin 8acffdbd 2026-10-22 · resumable", nat_view)

        # D. "importing" over real ValueServiceV2 socket -> ["unknown", "importing"] / unverified.
        raw_imp = self._start_custom_ledger_server(
            "(Error llm-ledger:selection (unsupported ((llm-ledger-importing 1762000000))))"
        )
        sock_imp, calls_imp = self._start_counting_socket(raw_imp)
        nat_snap, py_snap, nat_view, py_view, _ = self.observe_both(socket_path=sock_imp)
        self.assertEqual(nat_snap, py_snap)
        self.assertEqual(nat_view, py_view)
        self.assertEqual(nat_snap["selection"], {"llm-ledger-sqlite": ["unknown", "importing"]})
        self.assertEqual(len(calls_imp), 5)
        self.assertIn("unverified: the LLM ledger's backend selection is unavailable (importing)", nat_view)

        # E. "unsupported" over real ValueServiceV2 socket -> ["unknown", "unsupported"] / unverified.
        raw_unsup = self._start_custom_ledger_server(
            "(Error llm-ledger:selection (unsupported ((bogus-state))))"
        )
        sock_unsup, calls_unsup = self._start_counting_socket(raw_unsup)
        nat_snap, py_snap, nat_view, py_view, _ = self.observe_both(socket_path=sock_unsup)
        self.assertEqual(nat_snap, py_snap)
        self.assertEqual(nat_view, py_view)
        self.assertEqual(nat_snap["selection"], {"llm-ledger-sqlite": ["unknown", "unsupported"]})
        self.assertEqual(len(calls_unsup), 5)
        self.assertIn("unverified: the LLM ledger's backend selection is unavailable (unsupported)", nat_view)

        # F. "timeout" on hung AF_UNIX listener -> ["unknown", "timeout"] / unverified.
        sock_to, calls_to = self._start_counting_socket(mode="timeout")
        nat_snap, py_snap, nat_view, py_view, _ = self.observe_both(socket_path=sock_to)
        self.assertEqual(nat_snap, py_snap)
        self.assertEqual(nat_view, py_view)
        self.assertEqual(nat_snap["selection"], {"llm-ledger-sqlite": ["unknown", "timeout"]})
        self.assertGreaterEqual(len(calls_to), 1)
        self.assertIn("unverified: the LLM ledger's backend selection is unavailable (timeout)", nat_view)

        # G. "unreachable" across absent socket, regular file, dropped socket, and >107-byte path.
        regular_file_sock = self.directory / "not-a-socket.sock"
        regular_file_sock.write_text("", encoding="utf-8")
        overlong_sock = self.directory / ("x" * 120 + ".sock")
        overlong_sock.write_text("", encoding="utf-8")
        sock_drop, _ = self._start_counting_socket(mode="drop")

        for bad_sock in (
            str(self.directory / "missing.sock"),
            str(regular_file_sock),
            sock_drop,
            str(overlong_sock),
        ):
            nat_snap, py_snap, nat_view, py_view, _ = self.observe_both(socket_path=bad_sock)
            self.assertEqual(nat_snap, py_snap)
            self.assertEqual(nat_view, py_view)
            self.assertEqual(nat_snap["selection"], {"llm-ledger-sqlite": ["unknown", "unreachable"]})

    def test_engine_symlinks_nanosecond_mtime_rounding_negative_ts_and_unavailable_guard(self):
        # Symlinked engine binary (valid) and broken engine symlink + directory engine.
        engine_link = self.directory / "engine-symlink"
        engine_link.symlink_to(self.engine)
        broken_link = self.directory / "engine-broken"
        broken_link.symlink_to(self.directory / "does-not-exist")
        engine_dir = self.directory / "engine-is-dir"
        engine_dir.mkdir()

        self.make_session("work-01-symlink-engine", binary=str(engine_link))
        self.make_session("work-02-broken-engine", binary=str(broken_link))
        self.make_session("work-03-dir-engine", binary=str(engine_dir))
        self.make_session("work-04-launching-at", status="stopped", phase="reserved",
                          at=time.mktime(time.strptime("2026-10-21 08:00:00", "%Y-%m-%d %H:%M:%S")))
        self.make_session("work-05-launching-fallback", status="stopped", phase="reserved", at=None)
        self.make_session("work-06-neg-ts", status="stopped", updated=-0.25)

        # Nanosecond mtime 23:59:59.999999900 local time (rounds in IEEE-754 float to next day).
        s_ns = self.make_session("work-07-ns-midnight", status="stopped", updated=None)
        almost_midnight_sec = int(time.mktime(time.strptime("2026-10-23 23:59:59", "%Y-%m-%d %H:%M:%S")))
        ns_stamp = almost_midnight_sec * 1_000_000_000 + 999_999_900
        os.utime(s_ns / "state.json", ns=(ns_stamp, ns_stamp))

        nat_snap, py_snap, nat_view, py_view, _ = self.observe_both()
        self.assertEqual(nat_snap, py_snap)
        self.assertEqual(nat_view, py_view)

        # Out-of-range timestamp in even an unlisted "done" session causes estate:load -> ("unavailable").
        bad_estate = self.directory / "bad_ts_estate"
        self.make_session("work-bad-ts", root=bad_estate, status="done", updated=10**20, pin=False)
        res = self.run_native(
            "!(println! (estate:load))\n",
            cwd=bad_estate,
        )
        self.assertIn('("unavailable")', res.stdout)

    def test_unicode_quotes_symlinks_hardlinks_and_runtime_block_accounting(self):
        # Populate runtime with real multi-KB payload, hardlink, internal symlink, external dir symlink,
        # leading-dash entry, and unreadable subdirectory.
        ext_dir = self.directory / "external_target_dir"
        ext_dir.mkdir()
        (ext_dir / "big_external.bin").write_bytes(b"E" * (512 * 1024))

        payload = self.bundle_current / "payload.bin"
        payload.write_bytes(b"P" * (1600 * 1024))
        os.link(payload, self.bundle_current / "payload-hardlink.bin")
        (self.bundle_current / "internal-link.bin").symlink_to(payload)
        (self.bundle_current / "external-dir-link").symlink_to(ext_dir)
        dash_dir = self.bundle_current / "-leading-dash-(dir)"
        dash_dir.mkdir()
        (dash_dir / "-dash-file.txt").write_text("dash\n", encoding="utf-8")

        locked_sub = self.bundle_current / "locked-sub"
        locked_sub.mkdir()
        (locked_sub / "secret.bin").write_bytes(b"S" * 65536)

        # Symlinked runtime root under memory/coding-runtimes
        real_rt = self.directory / "real_symlinked_runtime"
        (real_rt / "src").mkdir(parents=True)
        (real_rt / "src" / "llm_ledger.metta").write_text("; native\n", encoding="utf-8")
        (real_rt / "blob.bin").write_bytes(b"B" * (700 * 1024))
        sym_rt = self.runtimes / ("dddd4444" + "0" * 56)
        sym_rt.symlink_to(real_rt)

        # Unicode + quotes in session IDs, hold/archive reasons, and symlinked session dir.
        s_uni = self.make_session('work-café-λ-"quoted"', status="stopped", bundle=sym_rt)
        coding_estate.hold_session(s_uni, 'čekání na "překlad" · αβγ', "2026-10-20", now=self.fixed_ts)

        s_arch = self.make_session("work-archived-émoji-✨", status="failed")
        coding_estate.archive_session(s_arch, 'archived "reason" — Ω', now=self.fixed_ts)

        ext_session = self.directory / "external_session_target"
        ext_session.mkdir()
        (ext_session / "task.json").write_text('{"task":"sym"}\n', encoding="utf-8")
        (ext_session / "state.json").write_text(
            json.dumps({"status": "running", "updated": self.fixed_ts}), encoding="utf-8"
        )
        os.utime(ext_session, (self.fixed_ts, self.fixed_ts))
        (self.coding / "work-symlinked-session").symlink_to(ext_session)

        if os.geteuid() != 0:
            os.chmod(locked_sub, 0o000)
            self.addCleanup(lambda: os.chmod(locked_sub, 0o755))
        try:
            nat_snap, py_snap, nat_view, py_view, _ = self.observe_both()
        finally:
            if os.geteuid() != 0:
                os.chmod(locked_sub, 0o755)

        self.assertEqual(nat_snap, py_snap)
        self.assertEqual(nat_view, py_view)
        self.assertIn('work-café-λ-"quoted"', nat_view)
        self.assertIn('čekání na "překlad" · αβγ', nat_view)
        self.assertIn("work-symlinked-session running", nat_view)

    def test_read_only_filesystem_control_zero_mutations_and_chmod_readonly_tree(self):
        s_open = self.make_session("work-ro-1-open", status="stopped")
        (s_open / "runs.jsonl").write_text('{"run":1,"at":%s}\n' % self.fixed_ts, encoding="utf-8")
        s_hold = self.make_session("work-ro-2-held", status="stopped")
        coding_estate.hold_session(s_hold, "do not mutate", "2026-10-20", now=self.fixed_ts)
        s_arch = self.make_session("work-ro-3-arch", status="stopped")
        coding_estate.archive_session(s_arch, "frozen", now=self.fixed_ts)
        # Remove helper lockfiles created by setup's hold_session/archive_session before read-only check.
        for lock_file in self.estate.rglob(".lifecycle.lock"):
            lock_file.unlink()
        for session_dir in (s_open, s_hold, s_arch):
            os.utime(session_dir, (self.fixed_ts, self.fixed_ts))

        coding_estate._engine_digest_cache.clear()
        coding_estate._runtime_blocks_cache.clear()
        expected_snap = json.loads(coding_estate_snapshot.snapshot(self.estate, "2026-10-25"))
        expected_view = coding_estate.context_view(
            self.estate, world=coding_estate.World(today="2026-10-25")
        )

        # Part A: Writable tree must experience ZERO file creations, lockfiles, byte edits, or mtime changes.
        before = _tree_manifest(self.estate)
        snap_out = self.directory / "ro_snap.json"
        view_out = self.directory / "ro_view.txt"
        self.run_native(
            '!(py-call (estate_io_sentinel.write_text %s\n'
            '   (json:stringify-python-with (estate:snapshot %s "2026-10-25") ((separators "," ":")))))\n'
            '!(py-call (estate_io_sentinel.write_text %s (estate:context-view-root %s "2026-10-25")))\n'
            '!(println! (estate:load))\n'
            % (
                self._metta_quote(snap_out),
                self._metta_quote(self.estate),
                self._metta_quote(view_out),
                self._metta_quote(self.estate),
            ),
            cwd=self.estate,
        )
        after = _tree_manifest(self.estate)
        self.assertEqual(before, after)
        self.assertEqual(json.loads(snap_out.read_text(encoding="utf-8")), expected_snap)
        self.assertEqual(view_out.read_text(encoding="utf-8"), expected_view)

        # Part B: OS-enforced read-only mode (0555 dirs, 0444 files) still renders identically.
        if os.geteuid() != 0:
            chmod_targets = []
            for dirpath, dirnames, filenames in os.walk(self.estate, topdown=False):
                cur = Path(dirpath)
                for fname in filenames:
                    fpath = cur / fname
                    if not fpath.is_symlink():
                        chmod_targets.append((fpath, stat.S_IMODE(os.lstat(fpath).st_mode)))
                        os.chmod(fpath, 0o444)
                chmod_targets.append((cur, stat.S_IMODE(os.lstat(cur).st_mode)))
                os.chmod(cur, 0o555)

            def restore_modes():
                for path_obj, orig_mode in reversed(chmod_targets):
                    try:
                        os.chmod(path_obj, orig_mode)
                    except OSError:
                        pass

            self.addCleanup(restore_modes)
            view_ro_out = self.directory / "ro_enforced_view.txt"
            res_ro = self.run_native(
                '!(py-call (estate_io_sentinel.write_text %s (estate:context-view-root %s "2026-10-25")))\n'
                '!(println! (estate:load))\n'
                % (self._metta_quote(view_ro_out), self._metta_quote(self.estate)),
                cwd=self.estate,
            )
            self.assertEqual(view_ro_out.read_text(encoding="utf-8"), expected_view)
            self.assertIn('("value" ', res_ro.stdout)
            restore_modes()

    def test_in_process_cold_vs_warm_cache_tradeoff_and_invalidation(self):
        # Populate 4 MB engine binary and 3 runtime directories with 120 files (~6 MB)
        big_engine_bytes = os.urandom(4 * 1024 * 1024)
        self.engine.write_bytes(big_engine_bytes)
        self.engine_hash = _sha256_bytes(big_engine_bytes)

        extra_rt = self.runtimes / ("eeee5555" + "0" * 56)
        (extra_rt / "src").mkdir(parents=True)
        (extra_rt / "src" / "llm_ledger.metta").write_text("; native\n", encoding="utf-8")
        for rt_idx, rt_dir in enumerate((self.bundle_current, self.bundle_old, extra_rt)):
            sub = rt_dir / "pkg"
            sub.mkdir(exist_ok=True)
            for f_idx in range(40):
                (sub / ("mod_%d_%d.dat" % (rt_idx, f_idx))).write_bytes(b"X" * 16384)

        for idx in range(12):
            self.make_session(
                "work-bench-%02d" % idx,
                status="stopped" if idx % 2 == 0 else "running",
                bundle=self.bundle_current,
                binary_sha256=self.engine_hash,
            )

        bench_out = self.directory / "bench.json"
        mutator = self.directory / "bench_mutator.py"
        mutator.write_text(
            "from pathlib import Path\n"
            "import os, time\n"
            "def mutate(engine_path, rt_path):\n"
            "    p = Path(engine_path)\n"
            "    p.write_bytes(p.read_bytes() + b'CHANGED')\n"
            "    now = time.time() + 5.0\n"
            "    os.utime(p, (now, now))\n"
            "    added = Path(rt_path) / 'added_2mb.bin'\n"
            "    added.write_bytes(b'Z' * (2 * 1024 * 1024))\n"
            "    os.utime(rt_path, (now, now))\n"
            "    return 1\n",
            encoding="utf-8",
        )

        self.run_native(
            "!(import! &self %s)\n" % self._metta_quote(mutator)
            + "!(let* (($_ (eio:clear-caches!))\n"
            + "        ($t0 (system:monotonic-ns))\n"
            + '        ($cold (estate:context-view-root %s "2026-10-25"))\n' % self._metta_quote(self.estate)
            + "        ($t1 (system:monotonic-ns))\n"
            + '        ($w1 (estate:context-view-root %s "2026-10-25"))\n' % self._metta_quote(self.estate)
            + "        ($t2 (system:monotonic-ns))\n"
            + '        ($w2 (estate:context-view-root %s "2026-10-25"))\n' % self._metta_quote(self.estate)
            + "        ($t3 (system:monotonic-ns))\n"
            + '        ($w3 (estate:context-view-root %s "2026-10-25"))\n' % self._metta_quote(self.estate)
            + "        ($t4 (system:monotonic-ns))\n"
            + "        ($_m (py-call (bench_mutator.mutate %s %s)))\n"
            % (self._metta_quote(self.engine), self._metta_quote(self.bundle_current))
            + "        ($t5 (system:monotonic-ns))\n"
            + '        ($after (estate:context-view-root %s "2026-10-25"))\n' % self._metta_quote(self.estate)
            + "        ($t6 (system:monotonic-ns)))\n"
            + "   (py-call (estate_io_sentinel.write_text %s\n" % self._metta_quote(bench_out)
            + "     (json:stringify-python\n"
            + "       (JsonObject (\n"
            + '         (JsonPair "cold_ms" (/ (- $t1 $t0) 1000000.0))\n'
            + '         (JsonPair "warm1_ms" (/ (- $t2 $t1) 1000000.0))\n'
            + '         (JsonPair "warm2_ms" (/ (- $t3 $t2) 1000000.0))\n'
            + '         (JsonPair "warm3_ms" (/ (- $t4 $t3) 1000000.0))\n'
            + '         (JsonPair "invalidated_ms" (/ (- $t6 $t5) 1000000.0))\n'
            + '         (JsonPair "cold_view" $cold)\n'
            + '         (JsonPair "warm_equal" (and (== $cold $w1) (and (== $cold $w2) (== $cold $w3))))\n'
            + '         (JsonPair "after_view" $after)))))))\n',
            cwd=self.estate,
        )

        receipt = json.loads(bench_out.read_text(encoding="utf-8"))
        self.assertTrue(receipt["warm_equal"])
        self.assertIn("resumable 12", receipt["cold_view"])
        self.assertIn("needs-current 12", receipt["after_view"])
        self.assertIn("its engine binary has changed", receipt["after_view"])
        self.assertNotEqual(receipt["cold_view"], receipt["after_view"])
        warm_median = sorted([receipt["warm1_ms"], receipt["warm2_ms"], receipt["warm3_ms"]])[1]
        self.assertLess(warm_median, receipt["cold_ms"])

        receipt_target = os.environ.get("ESTATE_IO_BENCHMARK_OUT")
        if receipt_target:
            summary = {
                "sessions": 12,
                "runtimes": 3,
                "engine_bytes": len(big_engine_bytes),
                "cold_ms": round(receipt["cold_ms"], 3),
                "warm_ms_samples": [
                    round(receipt["warm1_ms"], 3),
                    round(receipt["warm2_ms"], 3),
                    round(receipt["warm3_ms"], 3),
                ],
                "warm_median_ms": round(warm_median, 3),
                "invalidated_refresh_ms": round(receipt["invalidated_ms"], 3),
                "speedup_cold_over_warm": round(receipt["cold_ms"] / max(warm_median, 0.001), 2),
            }
            Path(receipt_target).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    def test_find_dependency_missing_unsupported_and_timeout_propagate_unavailable_without_caching_zero(self):
        # Populate 2 MB runtime payload so valid observation yields "2 MB" (not 0 MB).
        (self.bundle_current / "two_mb.bin").write_bytes(b"M" * (2 * 1024 * 1024))
        os.utime(self.bundle_current, (self.pin_ts, self.pin_ts))
        self.make_session("work-find-ctrl", status="stopped", bundle=self.bundle_current)

        # 1. Missing find binary -> estate:load returns ("unavailable"), and context-bundle:with-views
        #    marks ONLY coding-sessions unavailable without starving other context sources.
        missing_find = str(self.directory / "absent-find-binary")
        res_missing = self.run_native(
            "!(println! (estate:load))\n",
            cwd=self.estate,
            extra_env={"METTACLAW_ESTATE_FIND_BIN": missing_find},
        )
        self.assertIn('("unavailable")', res_missing.stdout)

        from test_context_bundle_native import HEADER, guest
        from test_pins_context_native import q

        instructions = self.estate / "instructions.txt"
        instructions.write_text("Fixture instructions\n", encoding="utf-8")
        registry = self.estate / "registry.json"
        registry.write_text('{"schema":1,"projects":[]}', encoding="utf-8")
        (self.estate / "history.metta").write_text("", encoding="utf-8")
        (self.estate / "pins.txt").write_text("pinned note\n", encoding="utf-8")
        for subdir in ("src", "channels", "tests"):
            link = self.estate / subdir
            if not link.exists():
                link.symlink_to(ROOT / subdir)
        bundle_out = self.directory / "degraded-bundle.txt"
        guest(
            self.estate,
            HEADER + [
                "!(import! &self %s)" % self._metta_quote(self.sentinel),
                "!(py-call (estate_io_sentinel.arm))",
                "!(import! &self ./src/context_sources_cetta.metta)",
                "!(py-call (context_bundle_probe.note %s (context-bundle:with-views ())))" % q(bundle_out),
            ],
            engine="cetta",
            runtime_root=self.estate,
            METTACLAW_PROJECT_ROOT=str(self.estate),
            METTACLAW_PROMPT_PATH=str(instructions),
            METTACLAW_PROJECT_REGISTRY_PATH=str(registry),
            METTACLAW_ITER_PROCESS_DIR=str(self.estate / "transformations"),
            METTACLAW_OBSERVED_STRATEGY="0",
            METTACLAW_ESTATE_FIND_BIN=missing_find,
        )
        bundle_text = bundle_out.read_text(encoding="utf-8")
        self.assertIn("SOURCE[coding-sessions] status=unavailable", bundle_text)
        self.assertIn("SOURCE[project-capabilities] status=known", bundle_text)

        # 2. Unsupported non-GNU find (-printf rejected on exit 1 / exit 2 / non-numeric stdout) -> ("unavailable").
        fake_unsupported_1 = self.directory / "find_unsupported_exit1"
        fake_unsupported_1.write_text(
            "#!/bin/sh\necho \"find: unknown predicate '-printf'\" >&2\nexit 1\n",
            encoding="utf-8",
        )
        fake_unsupported_1.chmod(0o755)
        fake_unsupported_2 = self.directory / "find_unsupported_exit2"
        fake_unsupported_2.write_text(
            "#!/bin/sh\necho \"find: illegal option -- P\" >&2\nexit 2\n",
            encoding="utf-8",
        )
        fake_unsupported_2.chmod(0o755)
        fake_garbage = self.directory / "find_garbage"
        fake_garbage.write_text("#!/bin/sh\necho 'not-a-number'\nexit 0\n", encoding="utf-8")
        fake_garbage.chmod(0o755)

        for bad_find in (fake_unsupported_1, fake_unsupported_2, fake_garbage):
            res_bad = self.run_native(
                "!(println! (estate:load))\n",
                cwd=self.estate,
                extra_env={"METTACLAW_ESTATE_FIND_BIN": str(bad_find)},
            )
            self.assertIn('("unavailable")', res_bad.stdout)
            self.assertNotIn("0 MB", res_bad.stdout)

        # 3. Hung find exceeding timeout -> ("unavailable").
        fake_hang = self.directory / "find_hang"
        fake_hang.write_text(
            "#!%s\nimport time\ntime.sleep(2.0)\n" % sys.executable,
            encoding="utf-8",
        )
        fake_hang.chmod(0o755)
        res_hang = self.run_native(
            "!(println! (estate:load))\n",
            cwd=self.estate,
            extra_env={
                "METTACLAW_ESTATE_FIND_BIN": str(fake_hang),
                "METTACLAW_ESTATE_FIND_TIMEOUT_MS": "150",
            },
        )
        self.assertIn('("unavailable")', res_hang.stdout)

        # 4. Failed find MUST NOT cache an invented 0: within the same process and unchanged
        #    runtime directory mtime/inode, recovering find yields the true 2 MB count.
        toggle_flag = self.directory / "find_fail_once.flag"
        toggle_flag.write_text("fail\n", encoding="utf-8")
        flaky_find = self.directory / "find_flaky"
        real_find = "/usr/bin/find" if Path("/usr/bin/find").is_file() else "find"
        flaky_find.write_text(
            "#!%s\nimport os, pathlib, sys\n"
            "flag = pathlib.Path(%r)\n"
            "if flag.exists():\n"
            "    flag.unlink()\n"
            "    sys.stderr.write(\"find: unknown predicate '-printf'\\n\")\n"
            "    sys.exit(1)\n"
            "os.execvp(%r, [%r] + sys.argv[1:])\n"
            % (sys.executable, str(toggle_flag), real_find, real_find),
            encoding="utf-8",
        )
        flaky_find.chmod(0o755)
        res_recover = self.run_native(
            "!(let* (($first (estate:load))\n"
            "        ($second (estate:load)))\n"
            "   (py-call (estate_io_sentinel.write_text %s (str:join \"\\n===\\n\" ((repr $first) (repr $second))))))\n"
            % self._metta_quote(self.directory / "recover_out.txt"),
            cwd=self.estate,
            extra_env={"METTACLAW_ESTATE_FIND_BIN": str(flaky_find)},
        )
        self.assertEqual(res_recover.returncode, 0)
        first_load, second_load = (self.directory / "recover_out.txt").read_text(encoding="utf-8").split("\n===\n")
        self.assertEqual(first_load.strip(), '("unavailable")')
        self.assertIn("runtimes 2 (2 MB;", second_load)
        self.assertNotIn("(0 MB;", second_load)

        # 5. Child-env C locale isolation: non-English parent locale (LC_ALL/LC_MESSAGES/LANG/LANGUAGE)
        #    must neither break permission-partial stderr classification nor alter parent local-date TZ,
        #    while unsupported-utility failure still returns ("unavailable").
        locale_probe_find = self.directory / "find_locale_probe"
        locale_probe_find.write_text(
            "#!%s\nimport os, sys\n"
            "if (os.environ.get('LC_ALL') != 'C' or os.environ.get('LANG') != 'C'\n"
            "        or os.environ.get('LANGUAGE') != 'C' or 'LC_MESSAGES' in os.environ):\n"
            "    sys.stdout.write('4096\\n')\n"
            "    sys.stderr.write('find: Zugriff verweigert\\n')\n"
            "    sys.exit(1)\n"
            "os.execvp(%r, [%r] + sys.argv[1:])\n"
            % (sys.executable, real_find, real_find),
            encoding="utf-8",
        )
        locale_probe_find.chmod(0o755)
        non_c_locale_env = {
            "LANG": "de_DE.UTF-8",
            "LC_ALL": "de_DE.UTF-8",
            "LC_MESSAGES": "fr_FR.UTF-8",
            "LANGUAGE": "de:fr:cs",
            "TZ": "UTC",
        }
        if os.geteuid() != 0:
            locked = self.bundle_current / "locale-locked"
            locked.mkdir()
            os.chmod(locked, 0o000)
            self.addCleanup(lambda: os.chmod(locked, 0o755))
            try:
                res_locale_ok = self.run_native(
                    "!(println! (estate:load))\n"
                    "!(println! (eio:local-date 0))\n",
                    cwd=self.estate,
                    extra_env={**non_c_locale_env, "METTACLAW_ESTATE_FIND_BIN": str(locale_probe_find)},
                )
            finally:
                os.chmod(locked, 0o755)
            self.assertIn("runtimes 2 (2 MB;", res_locale_ok.stdout)
            self.assertIn('"1970-01-01"', res_locale_ok.stdout)

        res_locale_fail = self.run_native(
            "!(println! (estate:load))\n",
            cwd=self.estate,
            extra_env={**non_c_locale_env, "METTACLAW_ESTATE_FIND_BIN": str(fake_unsupported_1)},
        )
        self.assertIn('("unavailable")', res_locale_fail.stdout)


if __name__ == "__main__":
    unittest.main()
