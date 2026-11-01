#!/usr/bin/env python3
"""Build an offline, content-addressed console snapshot from installed dependencies."""
import argparse
import base64
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def digest(body):
    return hashlib.sha256(body).hexdigest()


def relative(name):
    if not isinstance(name, str):
        raise ValueError("release paths must be strings")
    path = Path(name)
    if not name or path.is_absolute() or any(
            part in (".", "..") for part in name.split("/")) or str(path) != name:
        raise ValueError("expected a normalized relative file path: " + str(name))
    return path


def inventory(root):
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError("release contains a link or special file: " + str(path))
        if path.is_file() and path != root / "manifest.json":
            files[path.relative_to(root).as_posix()] = {
                "sha256": digest(path.read_bytes()), "mode": stat.S_IMODE(path.stat().st_mode)}
    return files


def verify(root):
    if root.is_symlink() or not root.is_dir() or (root / "manifest.json").is_symlink():
        raise ValueError("release must be a regular snapshot directory")
    manifest = json.loads((root / "manifest.json").read_text())
    if stat.S_IMODE((root / "manifest.json").stat().st_mode) != 0o444:
        raise ValueError("console manifest must have read-only file mode")
    payload = {key: value for key, value in manifest.items() if key != "release"}
    if manifest.get("schema") != 1 or manifest.get("kind") != "godelclaw-console":
        raise ValueError("not a console release manifest")
    if digest(encoded(payload)) != manifest.get("release"):
        raise ValueError("console manifest identity mismatch")
    if not manifest.get("files") or inventory(root) != manifest["files"]:
        raise ValueError("console release files or modes differ from manifest")
    for name in manifest["files"]:
        relative(name)
    if "files/scripts/console_entry.py" not in manifest["files"]:
        raise ValueError("console entry is missing")
    return manifest


def build(source, output):
    if sys.version_info < (3, 11):
        raise ValueError("console packaging requires Python 3.11 or newer")
    if output.exists() or output.is_symlink():
        raise ValueError("refusing to overwrite release output")
    selection = json.loads((source / "config/console-files.json").read_text())
    dependencies = json.loads((source / "config/console-dependencies.json").read_text())
    if len(selection) != len(set(selection)):
        raise ValueError("duplicate console source selection")
    bodies = {}
    for name in selection:
        path = source / relative(name)
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(source.resolve()):
            raise ValueError("missing or linked console source: " + name)
        bodies["files/" + name] = path.read_bytes()
    for name, version in dependencies.items():
        distribution = importlib.metadata.distribution(name)
        if distribution.version != version:
            raise ValueError(f"dependency {name}: expected {version}, found {distribution.version}")
        if not distribution.files:
            raise ValueError("dependency has no installed RECORD: " + name)
        for item in distribution.files:
            # Console scripts and installation-local/generated metadata are not imports.
            if ".." in item.parts or "__pycache__" in item.parts or item.suffix == ".pyc" or item.name in (
                    "RECORD", "INSTALLER", "REQUESTED", "direct_url.json"):
                continue
            relative(str(item))
            path = Path(distribution.locate_file(item))
            if path.is_symlink() or not path.is_file() or not item.hash or item.hash.mode != "sha256":
                raise ValueError("dependency file lacks a regular SHA-256 RECORD entry: " + str(item))
            body = path.read_bytes()
            recorded = base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b"=").decode()
            if recorded != item.hash.value:
                raise ValueError("dependency RECORD mismatch: " + str(item))
            destination = "vendor/" + str(item)
            if destination in bodies:
                raise ValueError("overlapping dependency file: " + destination)
            bodies[destination] = body
    # Publish only a complete snapshot, never an incomplete output directory.
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".console-build-", dir=output.parent) as temporary:
        stage = Path(temporary) / "release"
        stage.mkdir()
        for name, body in bodies.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            path.chmod(0o444)
        payload = {"schema": 1, "kind": "godelclaw-console", "python": list(sys.version_info[:2]),
                   "dependencies": dependencies, "files": inventory(stage)}
        manifest = dict(payload, release=digest(encoded(payload)))
        (stage / "manifest.json").write_bytes(encoded(manifest))
        (stage / "manifest.json").chmod(0o444)
        verify(stage)
        # rename does not replace a populated directory; also reject an empty collision.
        if output.exists() or output.is_symlink():
            raise ValueError("release output appeared during build")
        os.rename(stage, output)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = build(args.source, args.output)
        print(json.dumps({"release": manifest["release"], "files": len(manifest["files"])}))
    except (OSError, ValueError, KeyError, TypeError, importlib.metadata.PackageNotFoundError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
