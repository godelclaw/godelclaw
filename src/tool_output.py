"""Retain full tool output while returning an explicitly bounded preview."""
import hashlib
import os
from pathlib import Path
import tempfile


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
