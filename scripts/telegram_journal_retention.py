"""Archive old received Telegram updates out of a channel journal, then remove them.

Run only while the channel service is stopped; the store has one writer.

1. Select host.received records except the newest --keep, and never one whose
   update still has a pending host.inbox entry.
2. Write them to a content-addressed .jsonl.gz archive (temporary file, fsync,
   rename), read it back and check its digest and record count.
3. Append one line to the archive manifest, including an explicit empty pass.
4. Remove exactly the archived records in one administrative commit through
   CeTTa's durable store, so its accounting stays consistent.

Received records are needed only while their update is being processed, and the
poll cursor keeps Telegram from delivering old updates again, so removing old
processed records changes no delivery. The archive keeps the raw stored values.
"""

import argparse
import base64
import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile


def candidates(journal, keep):
    with sqlite3.connect("file:%s?mode=ro" % journal, uri=True) as db:
        epoch, revision = db.execute("SELECT epoch, revision FROM meta").fetchone()
        rows = db.execute(
            "SELECT key, revision, position, value FROM records WHERE space='host.received' "
            "ORDER BY revision, position").fetchall()
        pending = [k for (k,) in db.execute("SELECT key FROM records WHERE space='host.inbox'")]
    old = rows[:max(0, len(rows) - keep)]
    if pending:
        # Keep everything from the oldest update that still waits for processing.
        first = min(int(k.rsplit("/", 1)[-1]) for k in pending if k.rsplit("/", 1)[-1].isdigit())
        old = [r for r in old if int(r[0].rsplit("/", 1)[-1]) < first]
    return epoch, revision, old


def write_archive(directory, records):
    lines = [json.dumps({"space": "host.received", "key": key, "revision": rev, "position": pos,
                         "value_b64": base64.b64encode(bytes(value)).decode("ascii")}, sort_keys=True)
             for key, rev, pos, value in records]
    body = ("\n".join(lines) + "\n").encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / ("received-%s.jsonl.gz" % digest[:16])
    fd, temporary = tempfile.mkstemp(prefix=".received-", dir=str(directory))
    with os.fdopen(fd, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as packed:
            packed.write(body)
        raw.flush()
        os.fsync(raw.fileno())
    os.replace(temporary, target)
    fd = os.open(str(directory), os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    with gzip.open(target, "rb") as check:
        back = check.read()
    if hashlib.sha256(back).hexdigest() != digest or back.count(b"\n") != len(records):
        raise RuntimeError("archive read-back does not match")
    return target, digest, len(body)


def append_manifest(directory, entry):
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / "MANIFEST.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def remove(cetta, journal, epoch, revision, keys):
    ops = " ".join('(durable:remove "host.received" "%s")' % key for key in keys)
    script = '!(import! &self durable)\n!(durable:open "%s")\n!(durable:commit "%s" %d (%s))\n!(durable:close)\n' % (
        journal.name, epoch, revision, ops)
    with tempfile.TemporaryDirectory(dir=str(journal.parent)) as work:
        path = Path(work) / "remove.metta"
        path.write_text(script, encoding="utf-8")
        # The open directive prints the whole snapshot; keep only the verdict.
        run = subprocess.run([cetta, "--durable-admin", str(path)], cwd=str(journal.parent),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
    out = run.stdout.decode("utf-8", "replace")
    if "(durable:committed" not in out:
        failure = out[out.find("(durable:failure"):][:120] if "(durable:failure" in out else "no verdict"
        raise RuntimeError("removal was not committed: %s (exit %d)" % (failure, run.returncode))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True, help="archive directory")
    parser.add_argument("--cetta", required=True, help="a CeTTa binary with durable support")
    parser.add_argument("--keep", type=int, default=150, help="newest received records to keep")
    parser.add_argument("--unit", help="refuse to run while this systemd user unit is active")
    args = parser.parse_args()
    if args.unit and subprocess.run(["systemctl", "--user", "is-active", "--quiet", args.unit]).returncode == 0:
        raise SystemExit("refusing: %s is active; stop it first" % args.unit)
    journal = args.journal.resolve()
    epoch, revision, old = candidates(journal, args.keep)
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    if not old:
        append_manifest(args.archive, {"time": now, "journal": journal.name, "records": 0, "empty": True})
        print("nothing to archive")
        return
    target, digest, size = write_archive(args.archive, old)
    keys = [key for key, _, _, _ in old]
    append_manifest(args.archive, {"time": now, "journal": journal.name, "file": target.name, "sha256": digest,
                                   "records": len(old), "bytes": size, "first": keys[0], "last": keys[-1],
                                   "revision": revision})
    remove(args.cetta, journal, epoch, revision, keys)
    with sqlite3.connect("file:%s?mode=ro" % journal, uri=True) as db:
        left = db.execute("SELECT COUNT(*) FROM records WHERE space='host.received'").fetchone()[0]
        live = db.execute("SELECT live_bytes FROM meta").fetchone()[0]
    print("archived and removed %d received records to %s; %d remain; live bytes now %d"
          % (len(old), target.name, left, live))


if __name__ == "__main__":
    main()
