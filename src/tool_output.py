"""Retain full tool output while returning an explicitly bounded preview."""
import hashlib
import os
from pathlib import Path
import tempfile
import errno
import time
import math


class StorageExhausted(OSError):
    def __init__(self):
        super().__init__(errno.ENOSPC, "tool output reached the storage reserve")


class ProcessOutput:
    """Backpressured output capture with a physical-space reserve.

    Children receive pipes, never the spool descriptors. Publication links the
    completed spool to its content address without allocating a second copy.
    External writers may still cause ENOSPC; callers retain the partial result
    and report exhaustion rather than treating it as success or empty output.
    """
    def __init__(self, root, reserve=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        configured = os.environ.get("METTACLAW_TOOL_STORAGE_RESERVE_BYTES")
        if reserve is None and configured is None:
            volume = os.statvfs(self.root)
            fraction = float(os.environ.get("METTACLAW_TOOL_STORAGE_RESERVE_FRACTION", "0.01"))
            if not math.isfinite(fraction) or not 0 <= fraction <= 1:
                raise ValueError("storage reserve fraction must be between zero and one")
            self.reserve = max(64 * 1024 * 1024, int(volume.f_blocks * volume.f_frsize * fraction))
        else:
            self.reserve = int(configured if reserve is None else reserve)
        if self.reserve < 0:
            raise ValueError("storage reserve must be nonnegative")
        self.records = {name: dict(file=(self.root / (name + ".bin")).open("w+b"),
                                  digest=hashlib.sha256(), bytes=0, preview=bytearray())
                        for name in ("stdout", "stderr")}
        self.streams = {}

    def attach(self, process):
        self.streams = {name: getattr(process, name) for name in self.records
                        if getattr(process, name) is not None}
        for stream in self.streams.values():
            os.set_blocking(stream.fileno(), False)

    @property
    def complete(self):
        return not self.streams

    def pump(self, seconds=.01):
        volume = os.statvfs(self.root)
        if volume.f_bavail * volume.f_frsize < self.reserve:
            raise StorageExhausted()
        deadline = time.monotonic() + seconds
        while self.streams and time.monotonic() < deadline:
            changed = False
            for name, stream in list(self.streams.items()):
                try:
                    block = os.read(stream.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not block:
                    stream.close()
                    del self.streams[name]
                    continue
                changed = True
                row = self.records[name]
                volume = os.fstatvfs(row["file"].fileno())
                if volume.f_bavail * volume.f_frsize < self.reserve + len(block):
                    raise StorageExhausted()
                try:
                    row["file"].write(block)
                    row["file"].flush()
                except OSError as error:
                    # Only the previously accounted prefix can be published
                    # under its hash after a failed/partial filesystem write.
                    row["file"].seek(row["bytes"])
                    row["file"].truncate()
                    if error.errno in (errno.ENOSPC, errno.EDQUOT):
                        raise StorageExhausted() from error
                    raise
                row["digest"].update(block)
                row["bytes"] += len(block)
                row["preview"].extend(block[:max(0, 6000 - len(row["preview"]))])
            if not changed:
                break

    def artifacts(self, root, *, partial=False):
        root = Path(root); root.mkdir(parents=True, exist_ok=True)
        values = {}
        for name, row in self.records.items():
            stream = row["file"]; stream.flush(); os.fsync(stream.fileno())
            digest = row["digest"].hexdigest()
            path = root / (digest + ".txt")
            try:
                os.link(stream.name, path)
            except FileExistsError:
                pass
            values[name] = (row["preview"].decode("utf-8", "replace"), {
                "path": str(path.resolve()), "sha256": digest, "bytes": row["bytes"],
                "preview_truncated": row["bytes"] > len(row["preview"]),
                "complete": not partial})
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return values

    def close(self):
        for stream in self.streams.values():
            stream.close()
        self.streams.clear()
        for row in self.records.values():
            row["file"].close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def directory():
    selected = os.environ.get("METTACLAW_TOOL_OUTPUT_DIR")
    if selected:
        return Path(selected)
    state = os.environ.get("METTACLAW_ENGINE_STATE_PATH") or os.environ.get("METTACLAW_STRATEGY_PATH")
    if state:
        return Path(state).parent / "tool-results"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "godelclaw" / os.environ.get("METTACLAW_INSTANCE", "agent") / "tool-results"


def retain_stream(stream, root=None, preview_bytes=16384):
    """Return preview and a durable reference; output length never stops work.

    The caller owns the source stream. Storage failures raise so they cannot
    masquerade as a complete retained result.
    """
    root = Path(root) if root is not None else directory()
    root.mkdir(parents=True, exist_ok=True)
    stream.seek(0, os.SEEK_END)
    remaining = stream.tell()
    stream.seek(0)
    fd, temporary = tempfile.mkstemp(prefix=".output-", dir=root)
    digest = hashlib.sha256()
    size = 0
    preview = bytearray()
    try:
        with os.fdopen(fd, "wb") as target:
            while remaining:
                block = stream.read(min(remaining, 256 * 1024))
                if not block:
                    raise OSError("output changed during capture")
                remaining -= len(block)
                digest.update(block)
                size += len(block)
                if len(preview) < preview_bytes:
                    preview.extend(block[:preview_bytes - len(preview)])
                target.write(block)
            target.flush()
            os.fsync(target.fileno())
        path = root / (digest.hexdigest() + ".txt")
        os.replace(temporary, path)
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        return preview.decode("utf-8", "replace"), {
            "path": str(path.resolve()), "sha256": digest.hexdigest(),
            "bytes": size, "preview_truncated": size > len(preview),
        }
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def retain_text(text, root=None):
    import io
    return retain_stream(io.BytesIO(str(text).encode("utf-8")), root)[1]


def text_view(text, chars):
    text = str(text)
    if chars <= 0 or len(text) <= chars:
        return text
    artifact = retain_text(text)
    return text[:chars] + "\n[preview; full output: %s; %d bytes; sha256 %s]" % (
        artifact["path"], artifact["bytes"], artifact["sha256"])
