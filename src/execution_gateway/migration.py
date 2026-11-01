"""Operator CLI for coherent SQLite snapshot, SQLite->CeTTa import, and CeTTa->SQLite reverse export."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .sqlite_store import snapshot_sqlite
from .store import Store


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="Take a coherent SQLite backup without touching the source")
    snap.add_argument("--source", type=Path, required=True)
    snap.add_argument("--destination", type=Path, required=True)

    imp = sub.add_parser("import", help="Import SQLite queue into a new CeTTa store directory")
    imp.add_argument("--sqlite", type=Path, required=True)
    imp.add_argument("--store", type=Path, required=True)
    imp.add_argument("--agent")
    imp.add_argument("--backup", type=Path)
    imp.add_argument("--worker", type=Path)

    exp = sub.add_parser("export", help="Reverse-export CeTTa store directory back to SQLite")
    exp.add_argument("--store", type=Path, required=True)
    exp.add_argument("--sqlite", type=Path, required=True)
    exp.add_argument("--agent")
    exp.add_argument("--worker", type=Path)

    args = parser.parse_args(argv)
    if args.command == "snapshot":
        out = snapshot_sqlite(args.source, args.destination)
        print(json.dumps({"snapshot": str(out), "source": str(args.source.resolve())}))
    elif args.command == "import":
        report = Store.import_sqlite(
            args.sqlite,
            args.store,
            agent=args.agent,
            backup_path=args.backup,
            worker_binary=args.worker,
        )
        print(json.dumps(report, indent=2))
    elif args.command == "export":
        report = Store.export_sqlite(
            args.store,
            args.sqlite,
            agent=args.agent,
            worker_binary=args.worker,
        )
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
