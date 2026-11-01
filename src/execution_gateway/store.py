"""An agent-owned execution-gateway queue backed by CeTTa's native durable Space."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid

from .cetta_worker import (
    CettaValueWorker,
    E,
    FloatBits,
    MAX_BYTES,
    S,
    Symbol,
    WorkerReplyUncertain,
    WorkerUnavailable,
    resolve_gateway_worker,
    scalar_float,
)
from .sqlite_store import SqliteStore, snapshot_sqlite

TERMINAL = {"succeeded", "failed", "cancelled", "expired", "uncertain"}

HERE = Path(__file__).resolve().parent
DEFAULT_MODULE = HERE / "metta/gateway_queue.metta"
DEFAULT_ENTRY = "gw:run"
CANCEL_CACHE_LIMIT = 4096


class Conflict(ValueError):
    pass


class StoreUnavailable(RuntimeError):
    """Durable CeTTa store is unavailable or refused an operation for I/O or integrity reasons."""


class StoreReplyUncertain(StoreUnavailable):
    """Worker died or timed out after request bytes were written; inspect durable receipt via resolve()."""

    def __init__(self, message: str, *, request_id: str, operation_id: str | None = None):
        super().__init__(message)
        self.request_id = request_id
        self.operation_id = operation_id


def _encode_bool(flag: bool):
    return S("true") if flag else S("false")


def _decode_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value in (S("true"), S("True")):
        return True
    if value in (S("false"), S("False")):
        return False
    raise StoreUnavailable(f"malformed boolean field in queue record: {value!r}")


def _encode_option(value, *, encoded: bool = True):
    if value is None:
        return S("NoValueV1")
    return E("SomeValueV1", json.dumps(value) if encoded else str(value))


def _decode_option(value, *, encoded: bool = True):
    if value == S("NoValueV1"):
        return None
    if not isinstance(value, tuple) or len(value) != 2 or value[0] != S("SomeValueV1"):
        raise StoreUnavailable(f"malformed optional field in queue record: {value!r}")
    return json.loads(value[1]) if encoded else value[1]


def _encode_detail(detail: dict):
    if not detail:
        return E("GatewayDetailV1")
    if set(detail) == {"dispatched"} and isinstance(detail["dispatched"], bool):
        return E("GatewayDispatchedV1", _encode_bool(detail["dispatched"]))
    if set(detail) == {"dispatch_attempt"} and type(detail["dispatch_attempt"]) is int:
        return E("GatewayAttemptV1", detail["dispatch_attempt"])
    if set(detail) == {"error"} and isinstance(detail["error"], str):
        if detail["error"] == "":
            return E("GatewayDetailV1")
        return E("GatewayErrorDetailV1", detail["error"])
    raise ValueError(f"unsupported gateway receipt detail shape: {detail!r}")


def _decode_detail(detail) -> dict:
    if detail == E("GatewayDetailV1"):
        return {}
    if isinstance(detail, tuple) and len(detail) == 2:
        if detail[0] == S("GatewayDispatchedV1"):
            return {"dispatched": _decode_bool(detail[1])}
        if detail[0] == S("GatewayAttemptV1"):
            return {"dispatch_attempt": int(detail[1])}
        if detail[0] == S("GatewayErrorDetailV1"):
            return {"error": str(detail[1])}
    raise StoreUnavailable(f"malformed receipt detail in queue record: {detail!r}")


def _fsync_directory(directory: Path):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _raise_refused(reason):
    name = reason.name if isinstance(reason, Symbol) else str(reason)
    if name == "owner-conflict":
        raise Conflict("queue belongs to another agent")
    if name == "fingerprint-conflict":
        raise Conflict("request ID already names a different operation")
    if name == "not-running":
        raise Conflict("only a running attempt can finish")
    if name == "terminal-state":
        raise Conflict("invalid terminal state")
    raise Conflict(name)


class Store:
    def __init__(
        self,
        path,
        agent,
        *,
        worker_binary: str | Path | None = None,
        module: str | Path | None = None,
        entry: str = DEFAULT_ENTRY,
        maximum_bytes: int = MAX_BYTES,
        cache_cancellation: bool = True,
        min_compact_bytes: int = 1024 * 1024,
        min_reclaim_bytes: int = 512 * 1024,
        compact_ratio: float = 2.5,
        low_headroom_bytes: int = 64 * 1024 * 1024,
        low_headroom_ratio: float = 1.5,
        _auto_import: bool = True,
    ):
        self.path = Path(path)
        self.agent = agent
        self._worker_binary = resolve_gateway_worker(worker_binary)
        self._module = Path(module) if module is not None else DEFAULT_MODULE
        self._entry = entry
        self._maximum_bytes = maximum_bytes
        self._cache_cancellation = cache_cancellation
        self.min_compact_bytes = int(min_compact_bytes)
        self.min_reclaim_bytes = int(min_reclaim_bytes)
        self.compact_ratio = float(compact_ratio)
        self.low_headroom_bytes = int(low_headroom_bytes)
        self.low_headroom_ratio = float(low_headroom_ratio)
        self.last_maintenance: dict | None = None
        self.last_envelope = None

        self._lock = threading.RLock()
        self._cancel_lock = threading.Lock()
        self._cancel_cache: dict[str, bool] = {}
        self._opened_generation: int | None = None
        self._next_check_physical: int = self.min_compact_bytes

        if self.path.exists() and not self.path.is_dir():
            raise ValueError(
                f"queue path {self.path} is a file, not a CeTTa store directory; "
                "open queue.db or use Store.import_sqlite()"
            )

        if _auto_import and not self.path.exists() and self.path.name == "queue.db":
            legacy_sqlite = self.path.parent / "queue.sqlite3"
            if legacy_sqlite.is_file():
                self.import_sqlite(
                    legacy_sqlite,
                    self.path,
                    agent=self.agent,
                    worker_binary=self._worker_binary,
                    maximum_bytes=self._maximum_bytes,
                )

        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.mkdir(mode=0o700)
        os.chmod(self.path, 0o700)

        self._worker = CettaValueWorker(
            self._worker_binary,
            self._module,
            self._entry,
            language="petta",
            maximum_bytes=self._maximum_bytes,
        )
        try:
            with self._lock:
                self._ensure_open()
        except BaseException:
            self._worker.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        with self._lock:
            self._clear_cancel_cache()
            if getattr(self, "_worker", None) is None or self._worker.closed:
                return
            try:
                if self._worker.process is not None and self._worker.process.poll() is None:
                    self._worker.call(E("Close"))
            except Exception:
                pass
            finally:
                self._opened_generation = None
                self._worker.close()

    def _clear_cancel_cache(self):
        with self._cancel_lock:
            self._cancel_cache.clear()

    def _set_cancel_cache(self, request_id: str, flag: bool):
        if not self._cache_cancellation:
            return
        with self._cancel_lock:
            if len(self._cancel_cache) >= CANCEL_CACHE_LIMIT and request_id not in self._cancel_cache:
                self._cancel_cache.clear()
            self._cancel_cache[request_id] = bool(flag)

    def _ensure_open(self):
        gen = self._worker.ensure_alive()
        if self._opened_generation == gen:
            return
        self._clear_cancel_cache()
        answer = self._worker.call(E("Open", str(self.path), self.agent))
        if isinstance(answer, tuple) and answer and answer[0] == S("GatewayRefusedV1"):
            _raise_refused(answer[1])
        if answer != S("GatewayReadyV1"):
            raise StoreUnavailable(f"failed to open CeTTa queue store: {answer!r}")
        self._opened_generation = gen

    def _call(self, expr, *, operation_id: str | None = None):
        with self._lock:
            self._ensure_open()
            try:
                return self._worker.call(expr)
            except WorkerReplyUncertain as err:
                self._opened_generation = None
                self._clear_cancel_cache()
                raise StoreReplyUncertain(
                    str(err), request_id=err.request_id, operation_id=operation_id
                ) from err
            except WorkerUnavailable as err:
                self._opened_generation = None
                self._clear_cancel_cache()
                raise StoreUnavailable(str(err)) from err

    def _apply(self, command, *, operation_id: str | None = None, allow_replay: bool = True):
        op_id = operation_id or ("queue-op-" + uuid.uuid4().hex)
        answer = self._call(E("ApplyOpen", op_id, command), operation_id=op_id)
        if isinstance(answer, tuple) and answer:
            if answer[0] == S("GatewayRefusedV1"):
                _raise_refused(answer[1])
            if answer[0] in (S("GatewayCommittedV1"), S("GatewayReplayedV1")):
                replayed = answer[0] == S("GatewayReplayedV1")
                if replayed and not allow_replay:
                    raise Conflict("replayed claim grants no dispatch right")
                self.last_envelope = answer
                return answer[1], replayed
            if answer == E("StoreRefused", S("recovery-required")):
                self._call(E("RecoverOwner"))
        self._clear_cancel_cache()
        raise StoreUnavailable(f"durable store refused operation: {answer!r}")

    def _inspect_raw(self, request_id: str):
        answer = self._call(E("InspectOpen", request_id))
        if not isinstance(answer, tuple) or len(answer) != 3 or answer[0] != S("GatewayInspectionV1"):
            raise StoreUnavailable(f"unexpected inspection response: {answer!r}")
        found, events = answer[1], answer[2]
        if not isinstance(events, tuple) or len(events) != 3 or events[0] != S("GatewayEventsV1"):
            raise StoreUnavailable(f"unexpected events response: {events!r}")
        return found, events[1]

    def submit(self, request, fingerprint, effect, *, operation_id: str | None = None):
        request_id = request["id"]
        command = E(
            "GatewaySubmitV1",
            request_id,
            fingerprint,
            json.dumps(request),
            S(effect),
            float(request["deadline"]),
            float(time.time()),
        )
        self._apply(command, operation_id=operation_id)
        return self.get(request_id)

    def get(self, request_id):
        found, receipts = self._inspect_raw(request_id)
        if found == S("SpaceProbeAbsentV1"):
            return None
        if not isinstance(found, tuple) or len(found) != 3 or found[0] != S("SpaceProbeFoundV1"):
            raise StoreUnavailable(f"unexpected probe response: {found!r}")
        job = found[1]
        fields = job[7]
        cancel_flag = _decode_bool(fields[4])
        self._set_cancel_cache(request_id, cancel_flag)
        events = []
        for wrapped in receipts:
            row = wrapped[1]
            events.append({
                "sequence": int(row[2]),
                "state": row[3].name,
                "at": scalar_float(row[4]),
                "detail": _decode_detail(row[5]),
            })
        return {
            "agent": self.agent,
            "id": request_id,
            "state": job[2].name,
            "result": _decode_option(fields[5], encoded=True),
            "error": _decode_option(fields[6], encoded=False),
            "outputs": _decode_option(fields[7], encoded=True),
            "receipts": events,
        }

    def claim(self, *, operation_id: str | None = None):
        command = E("GatewayClaimV1", float(time.time()))
        # If the caller explicitly passes operation_id to inspect/replay in a
        # controlled qualification harness, allow_replay remained False because
        # check_job_queue's contract forbids granting dispatch from a replay;
        # use resolve() to inspect lost-reply claim outcomes safely.
        answer, _ = self._apply(command, operation_id=operation_id, allow_replay=False)
        if answer == S("GatewayEmptyV1"):
            return None
        if not isinstance(answer, tuple) or len(answer) != 2 or answer[0] != S("GatewayClaimedV1"):
            raise StoreUnavailable(f"unexpected claim outcome: {answer!r}")
        job_id = answer[1]
        self._set_cancel_cache(job_id, False)
        found, _ = self._inspect_raw(job_id)
        if not isinstance(found, tuple) or found[0] != S("SpaceProbeFoundV1"):
            raise StoreUnavailable(f"claimed job missing on inspection: {job_id!r}")
        job = found[1]
        fields = job[7]
        return {
            "id": job[1],
            "fingerprint": fields[1],
            "request": json.loads(fields[2]),
            "effect": fields[3].name,
            "state": "queued",
            "cancel": 0,
            "result": None,
            "error": None,
            "sequence": int(job[3]),
            "outputs": None,
        }

    def cancellation_requested(self, request_id, *, durable: bool = False) -> bool:
        if self._cache_cancellation and not durable:
            with self._cancel_lock:
                cached = self._cancel_cache.get(request_id)
            if cached is not None:
                return cached
        found = self._call(E("FindOpen", request_id))
        if found == S("SpaceProbeAbsentV1"):
            return False
        if not isinstance(found, tuple) or len(found) != 3 or found[0] != S("SpaceProbeFoundV1"):
            raise StoreUnavailable(f"unexpected FindOpen response: {found!r}")
        flag = _decode_bool(found[1][7][4])
        self._set_cancel_cache(request_id, flag)
        return flag

    def cancel(self, request_id, *, operation_id: str | None = None):
        command = E("GatewayCancelV1", request_id, float(time.time()))
        answer, _ = self._apply(command, operation_id=operation_id)
        if isinstance(answer, tuple) and answer and answer[0] in (
            S("GatewayCancelledV1"),
            S("GatewayCancellationRequestedV1"),
        ):
            self._set_cancel_cache(request_id, True)
        return self.get(request_id)

    def finish(
        self,
        request_id,
        state,
        *,
        result=None,
        error=None,
        outputs=None,
        operation_id: str | None = None,
    ):
        command = E(
            "GatewayFinishV1",
            request_id,
            S(state),
            _encode_option(result, encoded=True),
            _encode_option(error, encoded=False),
            _encode_option(outputs, encoded=True),
            float(time.time()),
        )
        self._apply(command, operation_id=operation_id)

    def recover(self, *, operation_id: str | None = None):
        """Never silently replay a dispatch whose receipt was lost."""
        command = E("GatewayRecoverV1", float(time.time()))
        self._apply(command, operation_id=operation_id)

    def resolve_command(self, operation_id: str, command):
        """Inspect whether operation_id was durably published before a lost reply."""
        answer = self._call(E("ResolveOpen", operation_id, command))
        if answer == S("GatewayUnresolvedV1"):
            return None
        if isinstance(answer, tuple) and answer and answer[0] == S("GatewayResolvedV1"):
            return answer[1]
        if isinstance(answer, tuple) and answer and answer[0] == S("GatewayRefusedV1"):
            _raise_refused(answer[1])
        raise StoreUnavailable(f"unexpected resolve response: {answer!r}")

    def physical_bytes(self) -> int:
        total = 0
        try:
            with os.scandir(self.path) as entries:
                for entry in entries:
                    try:
                        st = entry.stat(follow_symlinks=False)
                        total += st.st_size
                    except FileNotFoundError:
                        continue
        except FileNotFoundError:
            return 0
        return total

    def _headroom_bytes(self) -> int:
        st = os.statvfs(self.path)
        return int(st.f_bavail * st.f_frsize)

    def live_stats(self) -> dict:
        with self._lock:
            started = self._call(E("LiveStatsStart"))
            if (
                not isinstance(started, tuple)
                or len(started) != 3
                or started[0] != S("GatewayLiveScanStartedV1")
            ):
                raise StoreUnavailable(f"failed to start live byte scan: {started!r}")
            epoch, revision = str(started[1]), int(started[2])
            for _ in range(100_000):
                step = self._call(E("LiveStatsStep", 256))
                if isinstance(step, tuple) and len(step) == 4:
                    if step[0] == S("GatewayLiveStatsMoreV1"):
                        continue
                    if step[0] == S("GatewayLiveStatsDoneV1"):
                        live_keys, key_bytes, val_bytes = int(step[1]), int(step[2]), int(step[3])
                        return {
                            "epoch": epoch,
                            "revision": revision,
                            "live_keys": live_keys,
                            "key_bytes": key_bytes,
                            "value_bytes": val_bytes,
                            "live_bytes": key_bytes + val_bytes,
                            "physical_bytes": self.physical_bytes(),
                            "headroom_bytes": self._headroom_bytes(),
                        }
                raise StoreUnavailable(f"unexpected live stats step response: {step!r}")
            raise StoreUnavailable("live stats scan exceeded iteration ceiling")

    def compact(self) -> dict:
        with self._lock:
            before_physical = self.physical_bytes()
            headroom = self._headroom_bytes()
            answer = self._call(E("CompactOpen"))
            if (
                not isinstance(answer, tuple)
                or len(answer) != 4
                or answer[0] != S("StoreCompacted")
            ):
                raise StoreUnavailable(f"compaction refused: {answer!r}")
            collected = answer[3]
            removed = (
                int(collected[1])
                if isinstance(collected, tuple)
                and len(collected) == 2
                and collected[0] == S("StoreCollected")
                else 0
            )
            after_physical = self.physical_bytes()
            summary = {
                "epoch": str(answer[1]),
                "revision": int(answer[2]),
                "before_physical_bytes": before_physical,
                "after_physical_bytes": after_physical,
                "reclaimed_bytes": max(0, before_physical - after_physical),
                "removed_segments": removed,
                "headroom_bytes": headroom,
            }
            self.last_maintenance = summary
            return summary

    def maintain_if_needed(self) -> dict | None:
        """Measurement-driven compaction intended for quiescent owner boundaries."""
        with self._lock:
            physical = self.physical_bytes()
            headroom = self._headroom_bytes()
            low_space = headroom < self.low_headroom_bytes
            floor = min(self.min_compact_bytes, 256 * 1024) if low_space else self.min_compact_bytes
            reclaim_floor = (
                min(self.min_reclaim_bytes, 128 * 1024) if low_space else self.min_reclaim_bytes
            )
            target_ratio = self.low_headroom_ratio if low_space else self.compact_ratio

            if physical < floor or (not low_space and physical < self._next_check_physical):
                return None

            stats = self.live_stats()
            physical = stats["physical_bytes"]
            headroom = stats["headroom_bytes"]
            # Account for B-tree leaf/node framing and segment header so a tiny
            # freshly compacted store does not report artificial amplification.
            accounted_live = max(stats["live_bytes"] + stats["live_keys"] * 48 + 4096, 4096)
            dead_bytes = physical - accounted_live
            ratio = physical / accounted_live
            safe_headroom = accounted_live * 2 + 65536

            if dead_bytes >= reclaim_floor and ratio >= target_ratio and headroom >= safe_headroom:
                compacted = self.compact()
                compacted["measured_live_bytes"] = stats["live_bytes"]
                compacted["accounted_live_bytes"] = accounted_live
                compacted["live_keys"] = stats["live_keys"]
                compacted["ratio_before"] = round(ratio, 4)
                compacted["ratio_after"] = round(
                    compacted["after_physical_bytes"] / accounted_live, 4
                )
                self._next_check_physical = max(
                    floor, int(compacted["after_physical_bytes"] * target_ratio)
                )
                self.last_maintenance = compacted
                return compacted

            self._next_check_physical = max(
                physical + max(reclaim_floor // 2, 16384),
                int(accounted_live * target_ratio),
            )
            return None

    @classmethod
    def import_sqlite(
        cls,
        sqlite_path: str | Path,
        store_dir: str | Path,
        agent: str | None = None,
        *,
        backup_path: str | Path | None = None,
        worker_binary: str | Path | None = None,
        maximum_bytes: int = MAX_BYTES,
    ) -> dict:
        """Snapshot SQLite via sqlite3.backup (leaving source untouched), import to CeTTa, and verify 100% parity."""
        source = Path(sqlite_path).resolve(strict=True)
        target = Path(store_dir)
        if target.exists():
            raise ValueError(f"destination store directory already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)

        temp_backup_dir = None
        if backup_path is not None:
            snap_file = Path(backup_path)
            snapshot_sqlite(source, snap_file)
        else:
            temp_backup_dir = tempfile.TemporaryDirectory(
                prefix=".sqlite-backup-", dir=target.parent
            )
            os.chmod(temp_backup_dir.name, 0o700)
            snap_file = Path(temp_backup_dir.name) / "snapshot.sqlite3"
            snapshot_sqlite(source, snap_file)

        staging_dir = target.parent / f".{target.name}.importing-{uuid.uuid4().hex}"
        try:
            conn = sqlite3.connect(f"file:{snap_file}?mode=ro", uri=True, timeout=5)
            try:
                conn.row_factory = sqlite3.Row
                owners = conn.execute("SELECT agent FROM owner").fetchall()
                if len(owners) != 1:
                    raise ValueError(f"SQLite queue must have exactly 1 owner row, found {len(owners)}")
                stored_agent = str(owners[0]["agent"])
                if agent is not None and stored_agent != agent:
                    raise Conflict("queue belongs to another agent")
                agent = stored_agent

                job_cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
                has_outputs = "outputs" in job_cols
                job_rows = conn.execute(
                    "SELECT * FROM jobs ORDER BY sequence ASC, id ASC"
                ).fetchall()
                receipt_rows = conn.execute(
                    "SELECT sequence, id, state, at, detail FROM receipts ORDER BY sequence ASC"
                ).fetchall()
                seq_table_exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
                ).fetchone()
                sqlite_seq = 0
                if seq_table_exists:
                    seq_row = conn.execute(
                        "SELECT seq FROM sqlite_sequence WHERE name='receipts'"
                    ).fetchone()
                    if seq_row and seq_row[0] is not None:
                        sqlite_seq = int(seq_row[0])
            finally:
                conn.close()

            max_seq = max(
                [
                    0,
                    sqlite_seq,
                    *(int(r["sequence"]) for r in job_rows),
                    *(int(r["sequence"]) for r in receipt_rows),
                ]
            )

            edits = []
            for row in job_rows:
                req_text = str(row["request"])
                req_obj = json.loads(req_text)
                deadline = float(req_obj["deadline"])
                seq = int(row["sequence"])
                state_str = str(row["state"])
                effect_str = str(row["effect"])
                if effect_str not in ("read", "write"):
                    raise ValueError(f"invalid job effect in SQLite: {effect_str!r}")
                ready = seq if state_str == "queued" else S("GatewayInactiveV1")
                running = seq if state_str == "running" else S("GatewayInactiveV1")
                raw_res = row["result"]
                if raw_res:
                    json.loads(raw_res)
                    val_opt = E("SomeValueV1", str(raw_res))
                else:
                    val_opt = S("NoValueV1")
                err_opt = (
                    S("NoValueV1")
                    if row["error"] is None
                    else E("SomeValueV1", str(row["error"]))
                )
                raw_out = row["outputs"] if has_outputs else None
                if raw_out:
                    json.loads(raw_out)
                    out_opt = E("SomeValueV1", str(raw_out))
                else:
                    out_opt = S("NoValueV1")
                fields = E(
                    "GatewayFieldsV1",
                    str(row["fingerprint"]),
                    req_text,
                    S(effect_str),
                    _encode_bool(bool(row["cancel"])),
                    val_opt,
                    err_opt,
                    out_opt,
                    FloatBits.of(deadline),
                )
                job_atom = E(
                    "GatewayJobV1",
                    str(row["id"]),
                    S(state_str),
                    seq,
                    ready,
                    running,
                    S(effect_str),
                    fields,
                )
                edits.append(E("SpaceInsertV1", E("import-job", str(row["id"])), job_atom))

            for row in receipt_rows:
                rseq = int(row["sequence"])
                detail_obj = json.loads(row["detail"])
                detail_atom = _encode_detail(detail_obj)
                event_atom = E(
                    "GatewayEventV1",
                    str(row["id"]),
                    rseq,
                    S(str(row["state"])),
                    FloatBits.of(float(row["at"])),
                    detail_atom,
                )
                edits.append(E("SpaceInsertV1", E("import-event", rseq), event_atom))

            staging_dir.mkdir(mode=0o700)
            os.chmod(staging_dir, 0o700)
            staging = cls(
                staging_dir,
                agent,
                worker_binary=worker_binary,
                maximum_bytes=maximum_bytes,
                _auto_import=False,
            )
            try:
                chunk_size = 128
                chunks = [
                    tuple(edits[i : i + chunk_size])
                    for i in range(0, len(edits), chunk_size)
                ]
                if not chunks and max_seq > 0:
                    chunks = [()]
                for idx, chunk in enumerate(chunks):
                    is_last = idx == len(chunks) - 1
                    seq_directive = (
                        E("SetSequenceV1", max_seq) if is_last else S("NoSequenceUpdateV1")
                    )
                    req_id = f"sqlite-import-chunk-{idx}"
                    ans = staging._call(
                        E("ImportChunkOpen", req_id, chunk, seq_directive),
                        operation_id=req_id,
                    )
                    if not isinstance(ans, tuple) or ans[0] not in (
                        S("KnowledgeCommittedV1"),
                        S("KnowledgeReplayedV1"),
                    ):
                        raise StoreUnavailable(f"SQLite import chunk {idx} failed: {ans!r}")

                staging.compact()

                reference = SqliteStore(snap_file, agent, conflict_cls=Conflict)
                for row in job_rows:
                    jid = str(row["id"])
                    got = staging.get(jid)
                    wanted = reference.get(jid)
                    if json.dumps(got, sort_keys=True) != json.dumps(wanted, sort_keys=True):
                        raise StoreUnavailable(f"post-import parity mismatch on job {jid!r}")
            finally:
                staging.close()

            _fsync_directory(staging_dir)
            os.rename(staging_dir, target)
            _fsync_directory(target.parent)
            return {
                "agent": agent,
                "source_sqlite": str(source),
                "backup_sqlite": str(snap_file) if backup_path is not None else None,
                "store_dir": str(target),
                "jobs": len(job_rows),
                "receipts": len(receipt_rows),
                "max_sequence": max_seq,
            }
        except BaseException:
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            raise
        finally:
            if temp_backup_dir is not None:
                temp_backup_dir.cleanup()

    @classmethod
    def export_sqlite(
        cls,
        store_dir: str | Path,
        sqlite_path: str | Path,
        agent: str | None = None,
        *,
        worker_binary: str | Path | None = None,
        maximum_bytes: int = MAX_BYTES,
    ) -> dict:
        """Export a consistent CeTTa store snapshot back into a full SQLite gateway database and verify parity."""
        source_dir = Path(store_dir).resolve(strict=True)
        target_sql = Path(sqlite_path)
        target_sql.parent.mkdir(parents=True, exist_ok=True)
        resolved_worker = resolve_gateway_worker(worker_binary)

        if agent is None:
            probe_worker = CettaValueWorker(
                resolved_worker,
                DEFAULT_MODULE,
                DEFAULT_ENTRY,
                language="petta",
                maximum_bytes=maximum_bytes,
            )
            try:
                probed = probe_worker.call(E("ReadExistingOwner", str(source_dir)))
            finally:
                probe_worker.close()
            if (
                not isinstance(probed, tuple)
                or len(probed) != 3
                or probed[0] != S("SpaceProbeFoundV1")
            ):
                raise StoreUnavailable(f"unable to read store owner from {source_dir}: {probed!r}")
            agent = str(probed[1])

        staging_sql = target_sql.parent / f".{target_sql.name}.exporting-{uuid.uuid4().hex}"
        with cls(
            source_dir,
            agent,
            worker_binary=resolved_worker,
            maximum_bytes=maximum_bytes,
            _auto_import=False,
        ) as store:
            with store._lock:
                opened = store._call(E("ExportOpen"))
                if (
                    not isinstance(opened, tuple)
                    or len(opened) != 5
                    or opened[0] != S("GatewayExportOpenedV1")
                ):
                    raise StoreUnavailable(f"failed to open export snapshot: {opened!r}")
                epoch, revision, stored_agent, max_seq = (
                    str(opened[1]),
                    int(opened[2]),
                    str(opened[3]),
                    int(opened[4]),
                )
                if stored_agent != agent:
                    store._call(E("ExportClose"))
                    raise Conflict("queue belongs to another agent")

                raw_jobs = []
                raw_events = []
                try:
                    for _ in range(100_000):
                        page = store._call(E("ExportJobsPage", 128))
                        if (
                            not isinstance(page, tuple)
                            or len(page) != 3
                            or page[0] != S("GatewayExportPageV1")
                        ):
                            raise StoreUnavailable(f"unexpected ExportJobsPage: {page!r}")
                        for wrapped in page[1]:
                            raw_jobs.append(wrapped[1])
                        if page[2] == S("Complete"):
                            break
                        if page[2] not in (S("More"), S("Paused")):
                            raise StoreUnavailable(f"unexpected ExportJobsPage status: {page!r}")
                    else:
                        raise StoreUnavailable("job export exceeded page ceiling")

                    for _ in range(100_000):
                        page = store._call(E("ExportEventsPage", 128))
                        if (
                            not isinstance(page, tuple)
                            or len(page) != 3
                            or page[0] != S("GatewayExportPageV1")
                        ):
                            raise StoreUnavailable(f"unexpected ExportEventsPage: {page!r}")
                        for wrapped in page[1]:
                            raw_events.append(wrapped[1])
                        if page[2] == S("Complete"):
                            break
                        if page[2] not in (S("More"), S("Paused")):
                            raise StoreUnavailable(f"unexpected ExportEventsPage status: {page!r}")
                    else:
                        raise StoreUnavailable("event export exceeded page ceiling")
                finally:
                    store._call(E("ExportClose"))

            jobs_decoded = []
            seen_job_ids = set()
            for job in raw_jobs:
                jid = str(job[1])
                if jid in seen_job_ids:
                    raise StoreUnavailable(f"duplicate job ID in CeTTa store: {jid!r}")
                seen_job_ids.add(jid)
                fields = job[7]
                cancel_int = 1 if _decode_bool(fields[4]) else 0
                result_str = (
                    None if fields[5] == S("NoValueV1") else str(fields[5][1])
                )
                error_str = (
                    None if fields[6] == S("NoValueV1") else str(fields[6][1])
                )
                outputs_str = (
                    None if fields[7] == S("NoValueV1") else str(fields[7][1])
                )
                jobs_decoded.append({
                    "id": jid,
                    "fingerprint": str(fields[1]),
                    "request": str(fields[2]),
                    "effect": fields[3].name,
                    "state": job[2].name,
                    "cancel": cancel_int,
                    "result": result_str,
                    "error": error_str,
                    "sequence": int(job[3]),
                    "outputs": outputs_str,
                })
            jobs_decoded.sort(key=lambda r: (r["sequence"], r["id"]))

            events_decoded = []
            seen_event_seqs = set()
            for ev in raw_events:
                eseq = int(ev[2])
                if eseq in seen_event_seqs:
                    raise StoreUnavailable(f"duplicate receipt sequence in CeTTa store: {eseq}")
                seen_event_seqs.add(eseq)
                events_decoded.append({
                    "sequence": eseq,
                    "id": str(ev[1]),
                    "state": ev[3].name,
                    "at": scalar_float(ev[4]),
                    "detail": json.dumps(_decode_detail(ev[5])),
                })
            events_decoded.sort(key=lambda r: r["sequence"])

            try:
                fd = os.open(staging_sql, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(fd)
                db = sqlite3.connect(staging_sql, timeout=5)
                try:
                    db.execute("PRAGMA synchronous=FULL")
                    with db:
                        db.executescript("""
                            CREATE TABLE owner (agent TEXT PRIMARY KEY);
                            CREATE TABLE jobs (
                                id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                                request TEXT NOT NULL, effect TEXT NOT NULL,
                                state TEXT NOT NULL, cancel INTEGER NOT NULL DEFAULT 0,
                                result TEXT, error TEXT, sequence INTEGER NOT NULL,
                                outputs TEXT
                            );
                            CREATE TABLE receipts (
                                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                                id TEXT NOT NULL, state TEXT NOT NULL,
                                at REAL NOT NULL, detail TEXT NOT NULL
                            );
                            CREATE INDEX gateway_jobs_ready ON jobs(state,sequence);
                        """)
                        db.execute("INSERT INTO owner(agent) VALUES (?)", (agent,))
                        for j in jobs_decoded:
                            db.execute(
                                "INSERT INTO jobs(id,fingerprint,request,effect,state,cancel,result,error,sequence,outputs)"
                                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                                (
                                    j["id"],
                                    j["fingerprint"],
                                    j["request"],
                                    j["effect"],
                                    j["state"],
                                    j["cancel"],
                                    j["result"],
                                    j["error"],
                                    j["sequence"],
                                    j["outputs"],
                                ),
                            )
                        for e in events_decoded:
                            db.execute(
                                "INSERT INTO receipts(sequence,id,state,at,detail) VALUES (?,?,?,?,?)",
                                (e["sequence"], e["id"], e["state"], e["at"], e["detail"]),
                            )
                        if max_seq > 0:
                            cur = db.execute(
                                "SELECT seq FROM sqlite_sequence WHERE name='receipts'"
                            ).fetchone()
                            if cur is None:
                                db.execute(
                                    "INSERT INTO sqlite_sequence(name,seq) VALUES ('receipts',?)",
                                    (max_seq,),
                                )
                            elif int(cur[0]) < max_seq:
                                db.execute(
                                    "UPDATE sqlite_sequence SET seq=? WHERE name='receipts'",
                                    (max_seq,),
                                )
                finally:
                    db.close()

                exported_ref = SqliteStore(staging_sql, agent, conflict_cls=Conflict)
                for j in jobs_decoded:
                    got = exported_ref.get(j["id"])
                    wanted = store.get(j["id"])
                    if json.dumps(got, sort_keys=True) != json.dumps(wanted, sort_keys=True):
                        raise StoreUnavailable(
                            f"post-export parity mismatch on job {j['id']!r}"
                        )

                os.chmod(staging_sql, 0o600)
                os.replace(staging_sql, target_sql)
                _fsync_directory(target_sql.parent)
                return {
                    "agent": agent,
                    "epoch": epoch,
                    "revision": revision,
                    "store_dir": str(source_dir),
                    "sqlite_path": str(target_sql),
                    "jobs": len(jobs_decoded),
                    "receipts": len(events_decoded),
                    "max_sequence": max_seq,
                }
            except BaseException:
                staging_sql.unlink(missing_ok=True)
                raise
