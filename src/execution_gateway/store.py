"""An agent-owned execution-gateway queue backed by CeTTa's native durable Space."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import threading
import time
import uuid

from .cetta_worker import (
    CettaValueWorker,
    E,
    MAX_BYTES,
    S,
    Symbol,
    WorkerReplyUncertain,
    WorkerUnavailable,
    resolve_gateway_worker,
    scalar_float,
)

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


_ACTIVE_STORE_LOCKS_GUARD = threading.Lock()
_ACTIVE_STORE_LOCKS: set[str] = set()


def _fsync_directory(directory: Path):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _canonical_store_key(store_dir: Path) -> tuple[Path, str]:
    parent = store_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    resolved_parent = parent.resolve(strict=True)
    return resolved_parent, str(resolved_parent / store_dir.name)


def _owner_lock_path(store_dir: Path) -> Path:
    resolved_parent, _ = _canonical_store_key(store_dir)
    return resolved_parent / f".{store_dir.name}.owner.lock"


def _activation_marker_path(store_dir: Path) -> Path:
    resolved_parent, _ = _canonical_store_key(store_dir)
    return resolved_parent / f".{store_dir.name}.activated.json"


def _acquire_store_lock(store_dir: Path) -> tuple[str, int, Path]:
    resolved_parent, key = _canonical_store_key(store_dir)
    lock_path = resolved_parent / f".{store_dir.name}.owner.lock"
    with _ACTIVE_STORE_LOCKS_GUARD:
        if key in _ACTIVE_STORE_LOCKS:
            raise StoreUnavailable(
                f"queue store {store_dir} already has an active owner in this process"
            )
        if lock_path.is_symlink():
            raise StoreUnavailable(f"owner lock file must not be a symlink: {lock_path}")
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as err:
            os.close(fd)
            raise StoreUnavailable(
                f"queue store {store_dir} is locked by another active owner"
            ) from err
        _ACTIVE_STORE_LOCKS.add(key)
        return (key, fd, lock_path)


def _release_store_lock(handle: tuple[str, int, Path] | None):
    if handle is None:
        return
    key, fd, _ = handle
    with _ACTIVE_STORE_LOCKS_GUARD:
        _ACTIVE_STORE_LOCKS.discard(key)
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            try:
                os.close(fd)
            except OSError:
                pass


def _read_activation_marker(marker_path: Path) -> dict:
    if marker_path.is_symlink():
        raise StoreUnavailable(f"activation marker must not be a symlink: {marker_path}")
    try:
        data = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise StoreUnavailable(f"corrupt store activation marker {marker_path}: {err}") from err
    if (
        not isinstance(data, dict)
        or data.get("schema") != 1
        or not isinstance(data.get("agent"), str)
        or not data["agent"]
    ):
        raise StoreUnavailable(f"invalid store activation marker format: {marker_path}")
    return data


def _write_activation_marker(marker_path: Path, payload: dict):
    if marker_path.is_symlink():
        raise StoreUnavailable(f"activation marker must not be a symlink: {marker_path}")
    parent = marker_path.parent
    tmp_path = parent / f".{marker_path.name}.tmp-{uuid.uuid4().hex}"
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(tmp_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        written = 0
        while written < len(encoded):
            written += os.write(fd, encoded[written:])
        os.fchmod(fd, 0o600)
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        tmp_path.unlink(missing_ok=True)
        raise
    os.close(fd)
    os.replace(tmp_path, marker_path)
    _fsync_directory(parent)


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
        _write_marker_on_open: bool = True,
        _prelocked_owner: tuple[str, int, Path] | None = None,
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
        self._write_marker_on_open = bool(_write_marker_on_open)
        self._owner_lock: tuple[str, int, Path] | None = None
        self._worker: CettaValueWorker | None = None

        if self.path.exists() and not self.path.is_dir():
            if _prelocked_owner is not None:
                _release_store_lock(_prelocked_owner)
            raise ValueError(
                f"queue path {self.path} is a file, not a CeTTa store directory; "
                "open queue.db"
            )

        self._owner_lock = (
            _prelocked_owner if _prelocked_owner is not None else _acquire_store_lock(self.path)
        )
        try:
            self._marker_path = _activation_marker_path(self.path)
            marker_present = self._marker_path.exists() or self._marker_path.is_symlink()
            root_csl = self.path / "root.csl"

            if marker_present:
                marker = _read_activation_marker(self._marker_path)
                if marker["agent"] != self.agent:
                    raise Conflict("queue belongs to another agent")
                if not self.path.is_dir() or not root_csl.is_file():
                    raise StoreUnavailable(
                        f"activated CeTTa queue store is missing or incomplete at {self.path}; "
                        "refusing empty reinitialization"
                    )
                self._require_existing = True
            else:
                legacy_sqlite = (
                    self.path.parent / "queue.sqlite3"
                    if self.path.name == "queue.db"
                    else None
                )
                if (
                    legacy_sqlite is not None
                    and (legacy_sqlite.exists() or legacy_sqlite.is_symlink())
                    and not root_csl.is_file()
                ):
                    raise StoreUnavailable(
                        f"an old SQLite queue {legacy_sqlite} exists without an activated CeTTa "
                        f"store at {self.path}; it was never migrated, and this release cannot import it"
                    )
                if root_csl.is_file():
                    self._require_existing = True
                elif self.path.exists() and any(self.path.iterdir()):
                    raise StoreUnavailable(
                        f"CeTTa store directory {self.path} is non-empty but missing root.csl"
                    )
                else:
                    self.path.mkdir(mode=0o700, exist_ok=True)
                    self._require_existing = False

            os.chmod(self.path, 0o700)
            self._worker = CettaValueWorker(
                self._worker_binary,
                self._module,
                self._entry,
                language="petta",
                maximum_bytes=self._maximum_bytes,
            )
            with self._lock:
                self._ensure_open()
        except BaseException:
            if self._worker is not None:
                self._worker.close()
            owner_lock, self._owner_lock = self._owner_lock, None
            _release_store_lock(owner_lock)
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        with self._lock:
            self._clear_cancel_cache()
            try:
                if self._worker is not None and not self._worker.closed:
                    try:
                        if self._worker.process is not None and self._worker.process.poll() is None:
                            self._worker.call(E("Close"))
                    except Exception:
                        pass
                    finally:
                        self._opened_generation = None
                        self._worker.close()
            finally:
                owner_lock, self._owner_lock = self._owner_lock, None
                _release_store_lock(owner_lock)

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
        if self._owner_lock is None or self._worker is None:
            raise StoreUnavailable("CeTTa queue store is closed")
        gen = self._worker.ensure_alive()
        if self._opened_generation == gen:
            return
        self._clear_cancel_cache()
        verb = "OpenExisting" if self._require_existing else "Open"
        answer = self._worker.call(E(verb, str(self.path), self.agent))
        if isinstance(answer, tuple) and answer and answer[0] == S("GatewayRefusedV1"):
            _raise_refused(answer[1])
        if answer != S("GatewayReadyV1"):
            raise StoreUnavailable(f"failed to open CeTTa queue store ({verb}): {answer!r}")
        self._require_existing = True
        if self._write_marker_on_open and not self._marker_path.exists():
            _write_activation_marker(
                self._marker_path,
                {
                    "schema": 1,
                    "agent": self.agent,
                    "mode": "initialized",
                    "activated_at": time.time(),
                },
            )
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
            if answer[0] == S("GatewayEmptyReadOnlyV1"):
                self.last_envelope = answer
                return S("GatewayEmptyV1"), False
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


