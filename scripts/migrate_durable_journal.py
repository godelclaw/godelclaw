#!/usr/bin/env python3
"""Convert a captured SQLite durable journal to a native CSL1 checkpoint.

The source is opened read-only in one SQLite snapshot. The destination must
not exist. Epoch, global revision, record versions, positions and opaque value
bytes are preserved; the original database retains its historical deltas.
Stop the owning service before capturing its final source for activation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import struct
import tempfile


def _frame(stream, payload):
    stream.write(b"CSL1" + struct.pack(">I", len(payload)) +
                 hashlib.sha256(payload).hexdigest().encode("ascii"))
    stream.write(payload)


def _header(kind, epoch, revision, count):
    return b"CDR1" + bytes([kind]) + epoch.encode("ascii") + struct.pack(">QQ", revision, count)


def convert(source, destination):
    source, destination = Path(source), Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError("destination already exists")
    digest = hashlib.sha256()
    temporary = None
    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("BEGIN")
        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("source integrity check failed")
        meta = db.execute("SELECT epoch,revision,live_count FROM meta WHERE id=1").fetchone()
        if not meta:
            raise ValueError("missing durable journal metadata")
        epoch, revision, count = meta
        if not isinstance(epoch, str) or not re.fullmatch("[0-9a-f]{32}", epoch):
            raise ValueError("invalid journal epoch")
        if type(revision) is not int or not 0 <= revision < 2**63:
            raise ValueError("invalid journal revision")
        if type(count) is not int or count < 0 or db.execute("SELECT count(*) FROM records").fetchone()[0] != count:
            raise ValueError("record count differs from metadata")
        try:
            fd, temporary = tempfile.mkstemp(prefix=".journal-conversion-", dir=destination.parent)
            with os.fdopen(fd, "wb") as stream:
                _frame(stream, _header(0, epoch, revision, count))
                copied = 0
                payload_bytes = 0
                maximum_record = 0
                for space, key, value, version, position in db.execute(
                        "SELECT space,key,value,revision,position FROM records "
                        "ORDER BY revision,position,space,key"):
                    if not isinstance(space, str) or not isinstance(key, str) or not space or not key or "\0" in space + key:
                        raise ValueError("invalid record identity")
                    if type(value) is not bytes or type(version) is not int or not 0 < version <= revision:
                        raise ValueError("invalid record value or version")
                    if type(position) is not int or not 0 <= position < 2**63:
                        raise ValueError("invalid record position")
                    space, key = space.encode("utf-8"), key.encode("utf-8")
                    row = (struct.pack(">BIIIQQ", 1, len(space), len(key), len(value), version, position) +
                           space + key + value)
                    digest.update(row)
                    _frame(stream, _header(2, epoch, revision, 1) + row)
                    copied += 1
                    payload_bytes += len(space) + len(key) + len(value)
                    maximum_record = max(maximum_record, len(value))
                if copied != count:
                    raise ValueError("source snapshot changed")
                stream.flush()
                os.fsync(stream.fileno())
            # Publish without overwriting a destination created concurrently.
            os.link(temporary, destination)
            os.unlink(temporary)
            temporary = None
            directory = os.open(destination.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary is not None:
                os.unlink(temporary)
    return dict(format="CSL1/CDR1", epoch=epoch, revision=revision, records=count,
                record_sha256=digest.hexdigest(), payload_bytes=payload_bytes,
                maximum_record_bytes=maximum_record, file_bytes=destination.stat().st_size)


def export_sqlite(source, destination):
    """Capture the latest native state for rollback, including later commits."""
    source, destination = Path(source), Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError("destination already exists")
    fd, temporary = tempfile.mkstemp(prefix=".journal-conversion-", dir=destination.parent)
    os.close(fd)
    try:
        with sqlite3.connect(temporary) as db, source.open("rb") as stream:
            db.executescript("""
                CREATE TABLE meta(id INTEGER PRIMARY KEY CHECK(id=1),epoch TEXT NOT NULL,
                    revision INTEGER NOT NULL,checkpoint_revision INTEGER NOT NULL,
                    live_bytes INTEGER NOT NULL,live_count INTEGER NOT NULL,history_bytes INTEGER NOT NULL);
                CREATE TABLE records(space TEXT NOT NULL,key TEXT NOT NULL,value BLOB NOT NULL,
                    revision INTEGER NOT NULL,position INTEGER NOT NULL,PRIMARY KEY(space,key)) WITHOUT ROWID;
                CREATE INDEX record_order ON records(space,revision,position);
                CREATE TABLE checkpoint(space TEXT NOT NULL,key TEXT NOT NULL,value BLOB NOT NULL,
                    revision INTEGER NOT NULL,position INTEGER NOT NULL,PRIMARY KEY(space,key)) WITHOUT ROWID;
                CREATE TABLE deltas(revision INTEGER NOT NULL,position INTEGER NOT NULL,kind INTEGER NOT NULL,
                    space TEXT NOT NULL,key TEXT NOT NULL,value BLOB NOT NULL,PRIMARY KEY(revision,position),
                    UNIQUE(revision,space,key)) WITHOUT ROWID;
                PRAGMA application_id=1129595986; PRAGMA user_version=1;
            """)
            epoch, revision, remaining = None, 0, 0
            while header := stream.read(72):
                if len(header) != 72 or header[:4] != b"CSL1":
                    raise ValueError("invalid journal frame")
                payload = stream.read(struct.unpack(">I", header[4:8])[0])
                if len(payload) != struct.unpack(">I", header[4:8])[0] or hashlib.sha256(payload).hexdigest().encode() != header[8:]:
                    raise ValueError("journal checksum mismatch")
                if len(payload) < 53 or payload[:4] != b"CDR1":
                    raise ValueError("invalid durable frame")
                kind, current_epoch = payload[4], payload[5:37].decode("ascii")
                current_revision, count = struct.unpack(">QQ", payload[37:53])
                if not re.fullmatch("[0-9a-f]{32}", current_epoch) or current_revision >= 2**63:
                    raise ValueError("invalid frame identity")
                if epoch is None:
                    if kind != 0 or len(payload) != 53:
                        raise ValueError("missing initial checkpoint")
                    epoch, revision, remaining = current_epoch, current_revision, count
                    continue
                if current_epoch != epoch or kind not in (1, 2):
                    raise ValueError("journal identity changed")
                if kind == 2 and (not remaining or count != 1 or current_revision != revision):
                    raise ValueError("invalid checkpoint sequence")
                if kind == 1 and (remaining or not count or current_revision != revision + 1):
                    raise ValueError("invalid delta revision")
                offset, seen = 53, set()
                for index in range(count):
                    if len(payload) - offset < 29:
                        raise ValueError("incomplete record")
                    operation, ns, nk, nv, version, position = struct.unpack_from(">BIIIQQ", payload, offset)
                    offset += 29
                    if not 0 < ns <= 255 or not 0 < nk <= 255 or ns + nk + nv > len(payload) - offset:
                        raise ValueError("record cannot be represented by the SQLite journal")
                    space = payload[offset:offset + ns].decode("utf-8")
                    key = payload[offset + ns:offset + ns + nk].decode("utf-8")
                    value = payload[offset + ns + nk:offset + ns + nk + nv]
                    offset += ns + nk + nv
                    if "\0" in space + key or (space, key) in seen or not 0 < version <= current_revision or position >= 2**63:
                        raise ValueError("invalid record identity or version")
                    seen.add((space, key))
                    if operation not in (1, 2, 3) or (kind == 2 and operation != 1) or (kind == 1 and (version != current_revision or position != index)) or (operation == 3 and nv):
                        raise ValueError("invalid record operation")
                    exists = db.execute("SELECT 1 FROM records WHERE space=? AND key=?", (space, key)).fetchone()
                    if (operation == 1) == bool(exists):
                        raise ValueError("record precondition failed")
                    db.execute("DELETE FROM records WHERE space=? AND key=?", (space, key))
                    if operation != 3:
                        db.execute("INSERT INTO records VALUES(?,?,?,?,?)", (space, key, value, version, position))
                if offset != len(payload):
                    raise ValueError("trailing frame bytes")
                if kind == 2:
                    remaining -= 1
                else:
                    revision = current_revision
            if epoch is None or remaining:
                raise ValueError("incomplete checkpoint")
            count, size = db.execute("SELECT count(*),coalesce(sum(length(value)+length(CAST(space AS BLOB))+length(CAST(key AS BLOB))),0) FROM records").fetchone()
            db.execute("INSERT INTO checkpoint SELECT * FROM records")
            db.execute("INSERT INTO meta VALUES(1,?,?,?,?,?,0)", (epoch, revision, revision, size, count))
        with open(temporary, "rb") as file:
            os.fsync(file.fileno())
        os.link(temporary, destination)
        os.unlink(temporary)
        temporary = None
        directory = os.open(destination.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return dict(format="SQLite/durable-v1", epoch=epoch, revision=revision, records=count,
                    payload_bytes=size, file_bytes=destination.stat().st_size)
    finally:
        if temporary is not None:
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--to-sqlite", action="store_true", help="export the current native state for an older runtime")
    args = parser.parse_args()
    receipt = (export_sqlite if args.to_sqlite else convert)(args.source, args.destination)
    if args.receipt:
        args.receipt.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
