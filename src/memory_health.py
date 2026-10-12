"""Measure the gap between Chroma's metadata and persisted vector index.

`report()` is cheap and read-only. `compact()` asks Chroma to load the
collection, then measures drift again and reports only observed progress. It
never equates a successful API call with a durable flush.
"""

import json
import os
import sqlite3


def _chroma_dir():
    return (os.environ.get("METTACLAW_CHROMA_DIR")
            or os.environ.get("OMEGACLAW_CHROMA_DIR")
            or "./chroma_db")


def _collection():
    return os.environ.get("METTACLAW_CHROMA_COLLECTION", "memories")


def drift():
    """Return (documents, indexed, behind) without loading the vector index."""
    path = os.path.join(_chroma_dir(), "chroma.sqlite3")
    if not os.path.exists(path):
        return (0, 0, 0)
    con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    try:
        row = con.execute(
            "select id from collections where name=?", (_collection(),)
        ).fetchone()
        if not row:
            return (0, 0, 0)
        cid = row[0]
        segs = dict((scope, sid) for sid, scope in con.execute(
            "select id, scope from segments where collection=?", (cid,)))
        meta_seg, vec_seg = segs.get("METADATA"), segs.get("VECTOR")
        docs = con.execute(
            "select count(*) from embeddings where segment_id=?", (meta_seg,)
        ).fetchone()[0] if meta_seg else 0
        seqs = dict(con.execute("select segment_id, seq_id from max_seq_id"))
        behind = max(0, int(seqs.get(meta_seg, 0)) - int(seqs.get(vec_seg, 0)))
        return (docs, max(0, docs - behind), behind)
    finally:
        con.close()


def _threshold_from_value(value):
    if isinstance(value, dict):
        direct = value.get("sync_threshold")
        if isinstance(direct, (int, float)):
            return int(direct)
        for child in value.values():
            found = _threshold_from_value(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _threshold_from_value(child)
            if found is not None:
                return found
    return None


def _stored_threshold():
    """Read Chroma's persisted configuration without loading its index."""
    path = os.path.join(_chroma_dir(), "chroma.sqlite3")
    if not os.path.exists(path):
        return None
    con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    try:
        row = con.execute(
            "select config_json_str, schema_str, id from collections "
            "where name=?", (_collection(),)).fetchone()
        if not row:
            return None
        for raw in row[:2]:
            if not raw:
                continue
            try:
                found = _threshold_from_value(json.loads(raw))
            except (TypeError, ValueError):
                found = None
            if found is not None:
                return found
        legacy = con.execute(
            "select int_value, float_value, str_value "
            "from collection_metadata where collection_id=? and key=?",
            (row[2], "hnsw:sync_threshold"),
        ).fetchone()
        if legacy:
            for value in legacy:
                if value is not None:
                    return int(value)
        return None
    except sqlite3.Error:
        return None
    finally:
        con.close()


def threshold():
    stored = _stored_threshold()
    if stored is not None:
        return stored
    return int(os.environ.get("METTACLAW_CHROMA_SYNC_THRESHOLD", "20"))


def backend():
    """The memory backend lib_chromadb is configured to use: its mode and,
    for the native service, the collection name and socket."""
    path = os.environ.get("METTACLAW_MEMORY_BACKEND_CONFIG") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "repos", "petta_lib_chromadb", "memory_backend.json")
    try:
        with open(path, encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, ValueError):
        return {"mode": "chroma"}
    return {"mode": config.get("mode", "chroma"), "name": config.get("name"),
            "socket": config.get("socket")}


def native_reachable(socket_path):
    """Whether the native memory service accepts a connection (read-only)."""
    import socket
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(2)
    try:
        probe.connect(str(socket_path))
        return True
    except (OSError, TypeError):
        return False
    finally:
        probe.close()


def report():
    configured = backend()
    if configured["mode"] == "native":
        state = "reachable" if native_reachable(configured["socket"]) else "UNREACHABLE"
        return "memory-health: native backend %s — service %s" % (configured["name"], state)
    docs, _indexed, behind = drift()
    limit = threshold()
    if docs == 0:
        return "memory-health: no chroma store found at %s" % _chroma_dir()
    verdict = ("healthy" if behind <= limit
               else ("DRIFTING — consider a measured (memory-compact) attempt"
                     if behind < 5 * limit
                     else "DANGEROUS — compact before querying"))
    return ("memory-health: %d memories, index ~%d behind (flush every %d) — %s"
            % (docs, behind, limit, verdict))


def compact():
    """Ask Chroma to catch up and report measured before/after drift."""
    _docs, _indexed, before = drift()
    if before == 0:
        return "memory-compact: nothing to do — " + report()
    try:
        import lib_chromadb
        lib_chromadb.COLLECTION.count()
        client = getattr(lib_chromadb, "CLIENT", None)
        if client is not None and hasattr(client, "_producer"):
            pass  # compatibility touch only; durability is measured below
    except Exception as exc:  # noqa: BLE001 - never raise across the boundary
        return "memory-compact FAILED: %s: %s" % (type(exc).__name__, exc)
    _docs, _indexed, after = drift()
    if after < before:
        return ("memory-compact: measured progress %d -> %d behind — %s"
                % (before, after, report()))
    return ("memory-compact: NO VERIFIED PROGRESS (%d -> %d behind) — %s"
            % (before, after, report()))


def engine_query_probe(text="memory index health probe"):
    """Run one real embed+Chroma query and disclose only structural success."""
    try:
        import qwen_embed
        import lib_chromadb
        vector = qwen_embed.embed_query(str(text))
        rows = lib_chromadb.query_with_ids(vector, 3)
    except Exception as exc:  # noqa: BLE001 - diagnostic boundary
        return "memory-engine-probe FAILED: %s: %s" % (type(exc).__name__, exc)
    if not isinstance(rows, list) or not rows:
        return "memory-engine-probe FAILED: query returned no rows"
    ids = [row[0] for row in rows if isinstance(row, list) and len(row) >= 3]
    if len(ids) != len(rows) or len(ids) != len(set(ids)):
        return "memory-engine-probe FAILED: malformed or duplicate result ids"
    return "memory-engine-probe: ok (%d unique results)" % len(rows)


def boot_check():
    """Print a service-start diagnostic and attempt only hard-drift recovery.

    Output goes to the service journal, not the model prompt. The caller still
    decides whether a failed recovery should block startup.
    """
    line = report()
    print("[memory_health] " + line, flush=True)
    _, _, behind = drift()
    if behind >= 5 * threshold():
        print("[memory_health] drift over hard limit — attempting catch-up "
              "before launch", flush=True)
        print("[memory_health] " + compact(), flush=True)
    return line


if __name__ == "__main__":
    boot_check()
