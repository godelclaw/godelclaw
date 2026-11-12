"""Native coding-estate observation: corrupt and unreadable estates, ledger
selections, read-only observation, and caches."""

import hashlib
import json
import os
from pathlib import Path
import shlex
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

from estate_blocks import allocated_blocks  # noqa: E402
from test_input_ledger_remote import DbServer, WORKER, available as ledger_available  # noqa: E402


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _day(timestamp):
    """The local calendar date of a POSIX timestamp."""
    return time.strftime("%Y-%m-%d", time.localtime(timestamp))


def _mtime_day(path):
    """The local date a file's modification time names, read as a float."""
    return _day(os.stat(path).st_mtime)


VERBS = ('verbs: (work-status "id") (work-resume "id" ["current"]) '
         '(work-hold "id" "why" ["YYYY-MM-DD"]) (work-archive "id" "why")')


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

    def label(self, session, kind, why, review=None):
        """A hold or archive label as (work-hold) and (work-archive) record it."""
        payload = {"why": why, "at": self.fixed_ts}
        if kind == "hold":
            payload["review"] = review
        (Path(session) / "lifecycle.json").write_text(json.dumps({kind: payload}, sort_keys=True) + "\n",
                                                      encoding="utf-8")

    def _metta_quote(self, value):
        return json.dumps(str(value), ensure_ascii=False)

    def run_native(self, body, *, socket_path="", cwd=None, extra_env=None, check=True, timeout=45):
        self._seq += 1
        probe = self.probe_dir / ("probe_%d.metta" % self._seq)
        script = "!(import! &self ./src/coding_estate_cetta.metta)\n" + body
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

    def observe(self, root=None, *, today="2026-10-25", socket_path=""):
        """The native snapshot, context view (None when absent) and source load."""
        target = self.estate if root is None else Path(root)
        self._seq += 1
        snap_file = self.directory / ("snap_%d.json" % self._seq)
        view_file = self.directory / ("view_%d.txt" % self._seq)
        load_file = self.directory / ("load_%d.txt" % self._seq)
        body = (
            '!(fs:write %s (json:stringify-python-with (estate:snapshot %s %s) ((separators "," ":"))))\n'
            % (self._metta_quote(snap_file), self._metta_quote(target), self._metta_quote(today))
            + '!(let $v (estate:context-view-root %s %s)\n'
            '   (fs:write %s (if (== $v EstateMissing) "__MISSING__" $v)))\n'
            % (self._metta_quote(target), self._metta_quote(today), self._metta_quote(view_file))
            + "!(fs:write %s (repr (estate:load)))\n" % self._metta_quote(load_file)
        )
        self.run_native(body, socket_path=socket_path, cwd=target)
        snapshot = json.loads(snap_file.read_text(encoding="utf-8"))
        view = view_file.read_text(encoding="utf-8")
        return snapshot, None if view == "__MISSING__" else view, load_file.read_text(encoding="utf-8").strip()

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

    def test_missing_malformed_corrupt_and_unreadable_estates(self):
        # A missing memory/coding, or a file in its place: no view, a missing source.
        empty_root = self.directory / "no_coding_root"
        empty_root.mkdir()
        file_root = self.directory / "file_coding_root"
        (file_root / "memory").mkdir(parents=True)
        (file_root / "memory" / "coding").write_text("not a dir\n", encoding="utf-8")
        for root in (empty_root, file_root):
            snapshot, view, load = self.observe(root)
            self.assertFalse(snapshot["available"])
            self.assertEqual((snapshot["sessions"], view, load), ([], None, '("missing")'))

        # Malformed JSON, non-object roots, duplicate keys, CRLF, invalid UTF-8,
        # hidden entries, and a session without state or task.
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

        snapshot, view, load = self.observe()
        self.assertEqual(snapshot["mode"], "open")
        by_id = {entry["id"]: entry for entry in snapshot["sessions"]}
        self.assertEqual(sorted(by_id), ["work-01-bad-json", "work-02-non-dict", "work-03-dup-keys",
                                         "work-04-nostate-notask"])
        self.assertEqual([entry["id"] for entry in snapshot["runtimes"]],
                         [self.bundle_old_digest, self.bundle_current_digest])
        self.assertEqual((by_id["work-01-bad-json"]["state"], by_id["work-01-bad-json"]["lifecycle"]), ({}, {}))
        self.assertEqual((by_id["work-02-non-dict"]["state"], by_id["work-02-non-dict"]["runtime"]), ({}, {}))
        self.assertTrue(by_id["work-02-non-dict"]["runtime_exists"])
        self.assertEqual(by_id["work-03-dup-keys"]["state"]["status"], "failed")
        self.assertEqual(by_id["work-03-dup-keys"]["lifecycle"], {"hold": {"why": "second", "review": "2026-10-21"}})
        bad_day, odd_day = _mtime_day(s_bad_json / "state.json"), _mtime_day(s_non_dict / "state.json")
        lines = [
            "work-01-bad-json unknown %s · pin aaaa1111 2026-10-22 · resumable" % bad_day,
            "work-02-non-dict unknown %s · pin  %s · needs-current: its frozen runtime is gone"
            % (odd_day, _mtime_day(s_non_dict / "runtime.json")),
            "work-03-dup-keys failed 2026-10-23 · pin aaaa1111 2026-10-22 · resumable · held until 2026-10-21: second",
            "work-04-nostate-notask unknown 2026-10-23 · unpinned: a resume pins the current runtime",
            VERBS,
        ]
        head = "CODING_SESSIONS 4: 3 unknown · 1 held · needs-current 1 · resumable 2 · unpinned 1 · runtimes %s"
        self.assertEqual(view, "\n".join([head % "2 (0 MB; 1 used by no open session)"] + lines))
        self.assertTrue(load.startswith('("value" '))

        # Unreadable directories (when not root): a runtimes directory that can
        # be searched but not listed counts no runtimes; one that cannot be
        # searched makes the source unavailable; an unlistable memory/coding
        # is missing.
        if os.geteuid() != 0:
            os.chmod(self.runtimes, 0o111)
            self.addCleanup(lambda: os.chmod(self.runtimes, 0o755))
            snapshot, view, _ = self.observe()
            self.assertEqual(snapshot["runtimes"], [])
            self.assertEqual(view, "\n".join([head % "0 (0 MB; 0 used by no open session)"] + lines))

            os.chmod(self.runtimes, 0o000)
            unavailable = self.run_native("!(println! (estate:load))\n", cwd=self.estate)
            self.assertIn('("unavailable")', unavailable.stdout)
            os.chmod(self.runtimes, 0o755)

            os.chmod(self.coding, 0o000)
            self.addCleanup(lambda: os.chmod(self.coding, 0o755))
            snapshot, view, load = self.observe()
            self.assertFalse(snapshot["available"])
            self.assertEqual((view, load), (None, '("missing")'))
            os.chmod(self.coding, 0o755)

    def test_embedded_nul_path_cannot_observe_an_existing_prefix(self):
        # An engine path with an embedded NUL is never hashed by its valid
        # prefix: the view becomes unavailable.
        self.make_session("work-nul-engine", binary=str(self.engine) + "\0suffix")
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
            '   (fs:write %s (json:stringify-python (JsonArray $rows))))\n'
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
            '         ($_ (fs:write %s %s))\n'
            '         ($second (eio:snapshot %s "2026-10-25")))\n'
            '   (fs:write %s (json:stringify-python (JsonArray ($first $second)))))\n'
            % (self._metta_quote(self.estate), self._metta_quote(self.engine),
               self._metta_quote(changed), self._metta_quote(self.estate), self._metta_quote(output)))
        before, after = json.loads(output.read_text())
        self.assertEqual([row['engine_hash'] for row in before['sessions']], [self.engine_hash] * 3)
        expected = hashlib.sha256(changed.encode()).hexdigest()
        self.assertEqual([row['engine_hash'] for row in after['sessions']], [expected] * 3)

    def test_runs_jsonl_torn_tails_non_dicts_invalid_utf8_and_repin_dates(self):
        repin_ts = time.mktime(time.strptime("2026-10-24 16:30:00", "%Y-%m-%d %H:%M:%S"))
        # Torn final line dropped; malformed middle line skipped; non-object lines ignored.
        s1 = self.make_session("work-runs-torn", status="stopped")
        (s1 / "runs.jsonl").write_text(
            "\n"
            '{"run": 1, "at": %s}\r\n' % self.pin_ts
            + "[1, 2, 3]\n"
            + "{malformed middle line}\n"
            + "null\n"
            + "123\n"
            + '{"run": 2, "at": %s} ' % repin_ts
            + '{"run": 3, "torn_tail": ',
            encoding="utf-8",
        )
        # Invalid UTF-8 inside a string is replaced; a non-numeric "at" falls back to the pin date.
        s2 = self.make_session("work-runs-utf8-fallback", status="stopped")
        (s2 / "runs.jsonl").write_bytes(b'{"note": "bad\xffbyte", "at": "not-a-number"}\n')
        # A boolean "at" reads as 1 or 0 epoch seconds.
        s3 = self.make_session("work-runs-bool-at", status="stopped")
        (s3 / "runs.jsonl").write_text('{"at": true}\n', encoding="utf-8")
        # runs.jsonl as a directory reads as no runs.
        s4 = self.make_session("work-runs-dir", status="stopped")
        (s4 / "runs.jsonl").mkdir()

        snapshot, view, _ = self.observe()
        by_id = {entry["id"]: entry for entry in snapshot["sessions"]}
        self.assertEqual((by_id["work-runs-torn"]["runs_count"], by_id["work-runs-torn"]["repin_date"]), (2, "2026-10-24"))
        self.assertEqual((by_id["work-runs-utf8-fallback"]["runs_count"], by_id["work-runs-utf8-fallback"]["repin_date"]),
                         (1, "2026-10-22"))
        self.assertEqual((by_id["work-runs-bool-at"]["runs_count"], by_id["work-runs-bool-at"]["repin_date"]), (1, _day(1.0)))
        self.assertEqual((by_id["work-runs-dir"]["runs_count"], by_id["work-runs-dir"]["repin_date"]), (0, None))
        self.assertEqual(view, "\n".join([
            "CODING_SESSIONS 4: 4 stopped · resumable 4 · runtimes 2 (0 MB; 1 used by no open session)",
            "work-runs-bool-at stopped 2026-10-23 · pin aaaa1111 2026-10-22 · run 2 · re-pinned %s · resumable" % _day(1.0),
            "work-runs-dir stopped 2026-10-23 · pin aaaa1111 2026-10-22 · resumable",
            "work-runs-torn stopped 2026-10-23 · pin aaaa1111 2026-10-22 · run 3 · re-pinned 2026-10-24 · resumable",
            "work-runs-utf8-fallback stopped 2026-10-23 · pin aaaa1111 2026-10-22 · run 2 · re-pinned 2026-10-22 · resumable",
            VERBS]))

    @unittest.skipUnless(ledger_available(), "native ledger worker binary unavailable")
    def test_all_six_ledger_selection_outcomes_and_at_most_once_socket_rule(self):
        # A. No session on a pre-ledger runtime: no query, even with a socket.
        self.make_session("work-current-only", status="stopped", bundle=self.bundle_current)
        ledger_dir = self.directory / "ledger_native"
        ledger_dir.mkdir()
        db_server = DbServer(ledger_dir)
        db_server.start()
        self.addCleanup(db_server.stop)

        sock_zero, calls_zero = self._start_counting_socket(db_server.socket)
        snapshot, view, _ = self.observe(socket_path=sock_zero)
        self.assertEqual(snapshot["selection"], {})
        self.assertIn("work-current-only stopped 2026-10-23 · pin aaaa1111 2026-10-22 · resumable", view)
        self.assertEqual(calls_zero, [])

        # B. Five sessions on a pre-ledger runtime and a "native" ledger: one
        # query per observation, whatever the number of sessions.
        for idx in range(5):
            self.make_session("work-old-%d" % idx, status="stopped", bundle=self.bundle_old)
        sock_nat, calls_nat = self._start_counting_socket(db_server.socket)
        single_out = self.directory / "single_nat_view.txt"
        self.run_native(
            '!(fs:write %s (estate:context-view-root %s "2026-10-25"))\n'
            % (self._metta_quote(single_out), self._metta_quote(self.estate)),
            socket_path=sock_nat,
        )
        self.assertEqual(calls_nat, ["llm-selection"])
        calls_nat.clear()
        snapshot, view, _ = self.observe(socket_path=sock_nat)
        self.assertEqual(snapshot["selection"], {"llm-ledger-sqlite": "native"})
        # The snapshot, the view and the source load each ask once.
        self.assertEqual(len(calls_nat), 3)
        self.assertIn("work-old-0 stopped 2026-10-23 · pin 8acffdbd 2026-10-22 · needs-current: it uses the retired SQLite LLM ledger", view)
        self.assertEqual(single_out.read_text(encoding="utf-8"), view)

        # C. "legacy" over a real ValueServiceV2 socket: resumable.
        raw_legacy = self._start_custom_ledger_server("legacy")
        sock_leg, calls_leg = self._start_counting_socket(raw_legacy)
        snapshot, view, _ = self.observe(socket_path=sock_leg)
        self.assertEqual(snapshot["selection"], {"llm-ledger-sqlite": "legacy"})
        self.assertEqual(len(calls_leg), 3)
        self.assertIn("work-old-0 stopped 2026-10-23 · pin 8acffdbd 2026-10-22 · resumable", view)

        # D. "importing": unknown, the view says unverified.
        raw_imp = self._start_custom_ledger_server(
            "(Error llm-ledger:selection (unsupported ((llm-ledger-importing 1762000000))))"
        )
        sock_imp, calls_imp = self._start_counting_socket(raw_imp)
        snapshot, view, _ = self.observe(socket_path=sock_imp)
        self.assertEqual(snapshot["selection"], {"llm-ledger-sqlite": ["unknown", "importing"]})
        self.assertEqual(len(calls_imp), 3)
        self.assertIn("unverified: the LLM ledger's backend selection is unavailable (importing)", view)

        # E. An unrecognized state: unknown, unsupported.
        raw_unsup = self._start_custom_ledger_server(
            "(Error llm-ledger:selection (unsupported ((bogus-state))))"
        )
        sock_unsup, calls_unsup = self._start_counting_socket(raw_unsup)
        snapshot, view, _ = self.observe(socket_path=sock_unsup)
        self.assertEqual(snapshot["selection"], {"llm-ledger-sqlite": ["unknown", "unsupported"]})
        self.assertEqual(len(calls_unsup), 3)
        self.assertIn("unverified: the LLM ledger's backend selection is unavailable (unsupported)", view)

        # F. A listener that never answers: unknown, timeout.
        sock_to, calls_to = self._start_counting_socket(mode="timeout")
        snapshot, view, _ = self.observe(socket_path=sock_to)
        self.assertEqual(snapshot["selection"], {"llm-ledger-sqlite": ["unknown", "timeout"]})
        self.assertGreaterEqual(len(calls_to), 1)
        self.assertIn("unverified: the LLM ledger's backend selection is unavailable (timeout)", view)

        # G. Unreachable: an absent socket, a regular file, a dropped
        # connection, and a path too long for a Unix socket.
        regular_file_sock = self.directory / "not-a-socket.sock"
        regular_file_sock.write_text("", encoding="utf-8")
        overlong_sock = self.directory / ("x" * 120 + ".sock")
        overlong_sock.write_text("", encoding="utf-8")
        sock_drop, _ = self._start_counting_socket(mode="drop")
        for bad_sock in (str(self.directory / "missing.sock"), str(regular_file_sock), sock_drop, str(overlong_sock)):
            snapshot, view, _ = self.observe(socket_path=bad_sock)
            self.assertEqual(snapshot["selection"], {"llm-ledger-sqlite": ["unknown", "unreachable"]})
            self.assertIn("unverified: the LLM ledger's backend selection is unavailable (unreachable)", view)

    def test_engine_symlinks_nanosecond_mtime_rounding_negative_ts_and_unavailable_guard(self):
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
        # 23:59:59.999999900 local time reads as the next day once the
        # modification time is a float, as it always has.
        s_ns = self.make_session("work-07-ns-midnight", status="stopped", updated=None)
        almost_midnight_sec = int(time.mktime(time.strptime("2026-10-23 23:59:59", "%Y-%m-%d %H:%M:%S")))
        ns_stamp = almost_midnight_sec * 1_000_000_000 + 999_999_900
        os.utime(s_ns / "state.json", ns=(ns_stamp, ns_stamp))

        snapshot, view, _ = self.observe()
        hashes = {entry["id"]: entry["engine_hash"] for entry in snapshot["sessions"]}
        self.assertEqual(hashes["work-01-symlink-engine"], self.engine_hash)
        self.assertEqual((hashes["work-02-broken-engine"], hashes["work-03-dir-engine"]), (None, None))
        self.assertEqual(view, "\n".join([
            "CODING_SESSIONS 7: 5 stopped · 2 launching · needs-current 2 · resumable 5 · runtimes 2 (0 MB; 1 used by no open session)",
            "work-01-symlink-engine stopped 2026-10-23 · pin aaaa1111 2026-10-22 · resumable",
            "work-02-broken-engine stopped 2026-10-23 · pin aaaa1111 2026-10-22 · needs-current: its engine binary is gone",
            "work-03-dir-engine stopped 2026-10-23 · pin aaaa1111 2026-10-22 · needs-current: its engine binary is gone",
            "work-04-launching-at launching since 2026-10-21 · pin aaaa1111 2026-10-22 · resumable",
            "work-05-launching-fallback launching since 2026-10-23 · pin aaaa1111 2026-10-22 · resumable",
            "work-06-neg-ts stopped %s · pin aaaa1111 2026-10-22 · resumable" % _day(-0.25),
            "work-07-ns-midnight stopped %s · pin aaaa1111 2026-10-22 · resumable" % _mtime_day(s_ns / "state.json"),
            VERBS]))
        self.assertEqual(_mtime_day(s_ns / "state.json"), "2026-10-24")

        # An out-of-range timestamp, even in an unlisted done session, makes the source unavailable.
        bad_estate = self.directory / "bad_ts_estate"
        self.make_session("work-bad-ts", root=bad_estate, status="done", updated=10**20, pin=False)
        res = self.run_native("!(println! (estate:load))\n", cwd=bad_estate)
        self.assertIn('("unavailable")', res.stdout)

    def test_unicode_quotes_symlinks_hardlinks_and_runtime_block_accounting(self):
        # A runtime with a multi-megabyte payload, a hard link, an internal
        # symlink, an external directory symlink, a leading-dash entry and an
        # unreadable subdirectory.
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

        # A symlinked runtime root under memory/coding-runtimes.
        real_rt = self.directory / "real_symlinked_runtime"
        (real_rt / "src").mkdir(parents=True)
        (real_rt / "src" / "llm_ledger.metta").write_text("; native\n", encoding="utf-8")
        (real_rt / "blob.bin").write_bytes(b"B" * (700 * 1024))
        sym_rt = self.runtimes / ("dddd4444" + "0" * 56)
        sym_rt.symlink_to(real_rt)

        # Unicode and quotes in session ids and labels, and a symlinked session.
        s_uni = self.make_session('work-café-λ-"quoted"', status="stopped", bundle=sym_rt)
        self.label(s_uni, "hold", 'čekání na "překlad" · αβγ', "2026-10-20")
        s_arch = self.make_session("work-archived-émoji-✨", status="failed")
        self.label(s_arch, "archive", 'archived "reason" — Ω')
        ext_session = self.directory / "external_session_target"
        ext_session.mkdir()
        (ext_session / "task.json").write_text('{"task":"sym"}\n', encoding="utf-8")
        (ext_session / "state.json").write_text(json.dumps({"status": "running", "updated": self.fixed_ts}), encoding="utf-8")
        os.utime(ext_session, (self.fixed_ts, self.fixed_ts))
        (self.coding / "work-symlinked-session").symlink_to(ext_session)

        if os.geteuid() != 0:
            os.chmod(locked_sub, 0o000)
            self.addCleanup(lambda: os.chmod(locked_sub, 0o755))
        try:
            snapshot, view, _ = self.observe()
            blocks = {path.name: allocated_blocks(path) for path in (self.bundle_old, self.bundle_current, sym_rt)}
        finally:
            if os.geteuid() != 0:
                os.chmod(locked_sub, 0o755)

        self.assertEqual({entry["id"]: entry["blocks"] for entry in snapshot["runtimes"]}, blocks)
        megabytes = round(sum(blocks.values()) * 512 / (1024 * 1024))
        self.assertEqual(view, "\n".join([
            "CODING_SESSIONS 3: 1 running · 1 held · 1 archived · resumable 2 · unpinned 1 · "
            "runtimes 3 (%d MB; 2 used by no open session)" % megabytes,
            'work-café-λ-"quoted" stopped 2026-10-23 · pin dddd4444 2026-10-22 · resumable · '
            'held until 2026-10-20: čekání na "překlad" · αβγ',
            "work-symlinked-session running 2026-10-23 · unpinned: a resume pins the current runtime",
            VERBS]))
        archived = {entry["id"]: entry for entry in snapshot["sessions"]}["work-archived-émoji-✨"]
        self.assertEqual(archived["lifecycle"], {"archive": {"why": 'archived "reason" — Ω', "at": self.fixed_ts}})

    def test_read_only_filesystem_control_zero_mutations_and_chmod_readonly_tree(self):
        s_open = self.make_session("work-ro-1-open", status="stopped")
        (s_open / "runs.jsonl").write_text('{"run":1,"at":%s}\n' % self.fixed_ts, encoding="utf-8")
        s_hold = self.make_session("work-ro-2-held", status="stopped")
        self.label(s_hold, "hold", "do not mutate", "2026-10-20")
        s_arch = self.make_session("work-ro-3-arch", status="stopped")
        self.label(s_arch, "archive", "frozen")
        for session_dir in (s_open, s_hold, s_arch):
            os.utime(session_dir, (self.fixed_ts, self.fixed_ts))
        expected_view = "\n".join([
            "CODING_SESSIONS 3: 1 stopped · 1 held · 1 archived · resumable 3 · runtimes 2 (0 MB; 1 used by no open session)",
            "work-ro-1-open stopped 2026-10-23 · pin aaaa1111 2026-10-22 · run 2 · re-pinned 2026-10-23 · resumable",
            "work-ro-2-held stopped 2026-10-23 · pin aaaa1111 2026-10-22 · resumable · held until 2026-10-20: do not mutate",
            VERBS])

        # A. Observing a writable tree creates, edits and touches nothing.
        before = _tree_manifest(self.estate)
        snap_out = self.directory / "ro_snap.json"
        view_out = self.directory / "ro_view.txt"
        self.run_native(
            '!(fs:write %s (json:stringify-python-with (estate:snapshot %s "2026-10-25") ((separators "," ":"))))\n'
            '!(fs:write %s (estate:context-view-root %s "2026-10-25"))\n'
            '!(println! (estate:load))\n'
            % (self._metta_quote(snap_out), self._metta_quote(self.estate),
               self._metta_quote(view_out), self._metta_quote(self.estate)),
            cwd=self.estate,
        )
        self.assertEqual(_tree_manifest(self.estate), before)
        self.assertEqual(view_out.read_text(encoding="utf-8"), expected_view)
        writable_snapshot = json.loads(snap_out.read_text(encoding="utf-8"))

        # B. A tree the OS keeps read-only (0555 directories, 0444 files) shows the same.
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
            snap_ro_out = self.directory / "ro_enforced_snap.json"
            res_ro = self.run_native(
                '!(fs:write %s (estate:context-view-root %s "2026-10-25"))\n'
                '!(fs:write %s (json:stringify-python-with (estate:snapshot %s "2026-10-25") ((separators "," ":"))))\n'
                '!(println! (estate:load))\n'
                % (self._metta_quote(view_ro_out), self._metta_quote(self.estate),
                   self._metta_quote(snap_ro_out), self._metta_quote(self.estate)),
                cwd=self.estate,
            )
            self.assertEqual(view_ro_out.read_text(encoding="utf-8"), expected_view)
            self.assertEqual(json.loads(snap_ro_out.read_text(encoding="utf-8")), writable_snapshot)
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
        # Grow the engine and add 2 MB to a runtime, both dated five seconds ahead.
        mutate = ('printf CHANGED >> "$1" && head -c 2097152 /dev/zero | tr "\\000" Z > "$2/added_2mb.bin" '
                  '&& touch -d "@$(( $(date +%s) + 5 ))" "$1" "$2"')
        self.run_native(
            "!(import! &self (library proc))\n"
            "!(let* (($_ (eio:clear-caches!))\n"
            + "        ($t0 (system:monotonic-ns))\n"
            + '        ($cold (estate:context-view-root %s "2026-10-25"))\n' % self._metta_quote(self.estate)
            + "        ($t1 (system:monotonic-ns))\n"
            + '        ($w1 (estate:context-view-root %s "2026-10-25"))\n' % self._metta_quote(self.estate)
            + "        ($t2 (system:monotonic-ns))\n"
            + '        ($w2 (estate:context-view-root %s "2026-10-25"))\n' % self._metta_quote(self.estate)
            + "        ($t3 (system:monotonic-ns))\n"
            + '        ($w3 (estate:context-view-root %s "2026-10-25"))\n' % self._metta_quote(self.estate)
            + "        ($t4 (system:monotonic-ns))\n"
            + '        ($mutation (proc:run ("sh" "-c" %s "sh" %s %s) "/" inherit 60000 4096))\n'
            % (self._metta_quote(mutate), self._metta_quote(self.engine), self._metta_quote(self.bundle_current))
            + "        ($t5 (system:monotonic-ns))\n"
            + '        ($after (estate:context-view-root %s "2026-10-25"))\n' % self._metta_quote(self.estate)
            + "        ($t6 (system:monotonic-ns)))\n"
            + "   (fs:write %s\n" % self._metta_quote(bench_out)
            + "     (json:stringify-python\n"
            + "       (JsonObject (\n"
            + '         (JsonPair "cold_ms" (/ (- $t1 $t0) 1000000.0))\n'
            + '         (JsonPair "warm1_ms" (/ (- $t2 $t1) 1000000.0))\n'
            + '         (JsonPair "warm2_ms" (/ (- $t3 $t2) 1000000.0))\n'
            + '         (JsonPair "warm3_ms" (/ (- $t4 $t3) 1000000.0))\n'
            + '         (JsonPair "invalidated_ms" (/ (- $t6 $t5) 1000000.0))\n'
            + '         (JsonPair "cold_view" $cold)\n'
            + '         (JsonPair "mutated" (case $mutation (((proc:exited 0 $o $e) True) ($other False))))\n'
            + '         (JsonPair "warm_equal" (and (== $cold $w1) (and (== $cold $w2) (== $cold $w3))))\n'
            + '         (JsonPair "after_view" $after))))))\n',
            cwd=self.estate,
        )

        receipt = json.loads(bench_out.read_text(encoding="utf-8"))
        self.assertTrue(receipt["warm_equal"])
        self.assertIn("resumable 12", receipt["cold_view"])
        self.assertTrue(receipt["mutated"])
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

    def test_native_blocks_no_longer_depend_on_an_external_find_or_its_locale(self):
        (self.bundle_current / "two_mb.bin").write_bytes(b"M" * (2 * 1024 * 1024))
        self.make_session("work-native-blocks", status="stopped", bundle=self.bundle_current)
        marker = self.directory / "find-was-executed"
        forbidden = self.directory / "find"
        forbidden.write_text("#!/bin/sh\n: > " + shlex.quote(str(marker)) + "\nexit 99\n")
        forbidden.chmod(0o755)
        positive = subprocess.run([str(forbidden)], capture_output=True)
        self.assertEqual(positive.returncode, 99)
        self.assertTrue(marker.exists())
        marker.unlink()
        for old_override in (str(self.directory / "missing-find"), str(forbidden)):
            result = self.run_native(
                '!(println! (estate:load))\n!(println! (eio:local-date 0))\n',
                cwd=self.estate,
                extra_env={"METTACLAW_ESTATE_FIND_BIN": old_override,
                           "METTACLAW_ESTATE_FIND_TIMEOUT_MS": "1",
                           "PATH": str(self.directory) + os.pathsep + self.base_env.get("PATH", "/usr/bin:/bin"),
                           "LANG": "de_DE.UTF-8", "LC_MESSAGES": "fr_FR.UTF-8", "TZ": "UTC"},
            )
            self.assertIn('("value" ', result.stdout)
            self.assertIn("runtimes 2 (2 MB;", result.stdout)
            self.assertIn('"1970-01-01"', result.stdout)
            self.assertFalse(marker.exists())
        # Native descriptor refusal/recovery and no-false-zero behavior are
        # exercised without guest Python in test_coding_estate_native_core.


if __name__ == "__main__":
    unittest.main()
