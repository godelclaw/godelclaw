#!/usr/bin/env python3
"""Build a content-addressed release from an explicit reviewed file selection."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--overrides", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    selection = json.loads(args.selection.read_text())
    names = list(selection["files"]) if isinstance(selection, dict) else list(selection)
    overrides = json.loads(args.overrides.read_text()) if args.overrides else {}
    if set(overrides) - set(names):
        raise ValueError("overrides must belong to the reviewed selection")
    args.output.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for name in names:
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or name in hashes:
            raise ValueError("release paths must be distinct relative paths")
        source = Path(overrides[name]) if name in overrides else args.source / path
        destination = args.output / "files" / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        # Overrides supply content; the canonical source supplies executable
        # permissions. A freshly written override must not disable a launcher.
        canonical = args.source / path
        if canonical.is_file():
            destination.chmod(canonical.stat().st_mode & 0o777)
        hashes[name] = hashlib.sha256(destination.read_bytes()).hexdigest()
    identity = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    (args.output / "manifest.json").write_text(json.dumps(
        {"schema": 1, "release": identity, "files": hashes}, indent=2) + "\n")
    print(json.dumps({"release": identity, "files": len(hashes)}))


if __name__ == "__main__": main()
