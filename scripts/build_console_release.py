#!/usr/bin/env python3
"""Build an offline, content-addressed console snapshot from installed dependencies."""
import argparse
import base64
import hashlib
import importlib.metadata
import json
import os
import re
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


def commands(manifest):
    entry = "files/scripts/console_entry.py" if manifest["schema"] == 1 else manifest["entrypoint"]
    table = manifest.get("commands", {
        "agent-console": {"entrypoint": entry, "arguments": []},
        "godel-cli": {"entrypoint": entry, "arguments": ["--agent", "godel"]},
    })
    if not isinstance(table, dict) or not table:
        raise ValueError("console commands must be a nonempty mapping")
    for name, command in table.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name):
            raise ValueError("console command must be a simple executable name")
        if not isinstance(command, dict) or set(command) != {"entrypoint", "arguments"}:
            raise ValueError("console command requires an entrypoint and arguments")
        target = str(relative(command["entrypoint"]))
        if target not in manifest["files"] or (manifest["schema"] == 2 and not target.endswith(".metta")):
            raise ValueError("console command entrypoint is not in its release")
        if not isinstance(command["arguments"], list) or any(not isinstance(x, str) or "\0" in x for x in command["arguments"]):
            raise ValueError("console command arguments must be strings without NUL")
    return table


def verify(root):
    if root.is_symlink() or not root.is_dir() or (root / "manifest.json").is_symlink():
        raise ValueError("release must be a regular snapshot directory")
    manifest = json.loads((root / "manifest.json").read_text())
    if stat.S_IMODE((root / "manifest.json").stat().st_mode) != 0o444:
        raise ValueError("console manifest must have read-only file mode")
    payload = {key: value for key, value in manifest.items() if key != "release"}
    if manifest.get("schema") not in (1, 2) or manifest.get("kind") != "godelclaw-console":
        raise ValueError("not a console release manifest")
    if digest(encoded(payload)) != manifest.get("release"):
        raise ValueError("console manifest identity mismatch")
    if not manifest.get("files") or inventory(root) != manifest["files"]:
        raise ValueError("console release files or modes differ from manifest")
    for name in manifest["files"]:
        relative(name)
    entry = ("files/scripts/console_entry.py" if manifest["schema"] == 1 else manifest.get("entrypoint"))
    if entry not in manifest["files"]:
        raise ValueError("console entry is missing")
    commands(manifest)
    if manifest["schema"] == 2:
        engine = manifest.get("engine", {})
        if engine.get("runtime_profile") != "native-agent" or engine.get("python_enabled") is not False:
            raise ValueError("native console requires a Python-disabled engine")
        if not isinstance(engine.get("revision"), str) or len(engine["revision"]) != 40:
            raise ValueError("native console needs an exact engine revision")
        if manifest.get("dependencies") or any(name.endswith(".py") or name.startswith("vendor/") for name in manifest["files"]):
            raise ValueError("native console cannot contain Python modules or dependency vendoring")
    return manifest


def build(source, output, frontend="stable"):
    if sys.version_info < (3, 11):
        raise ValueError("console packaging requires Python 3.11 or newer")
    if output.exists() or output.is_symlink():
        raise ValueError("refusing to overwrite release output")
    if frontend not in ("stable", "native"):
        raise ValueError("unknown console frontend")
    selection_name = "console-native-files.json" if frontend == "native" else "console-files.json"
    selection = json.loads((source / "config" / selection_name).read_text())
    dependencies = {} if frontend == "native" else json.loads((source / "config/console-dependencies.json").read_text())
    native = "scripts/agent_console_cetta.metta" in selection
    if native != (frontend == "native"):
        raise ValueError("console selection does not match its declared frontend")
    if native and dependencies:
        raise ValueError("native console uses the engine libraries, not vendored Python packages")
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
        payload = {"schema": 2 if native else 1, "kind": "godelclaw-console",
                   "dependencies": dependencies, "files": inventory(stage)}
        if native:
            payload.update(entrypoint="files/scripts/agent_console_cetta.metta", engine={
                "revision": (source / "CETTA_REF").read_text().strip(),
                "runtime_profile": "native-agent", "python_enabled": False,
                "libraries": ["host_io", "fs", "proc", "json", "toml", "mailbox"]})
            command_source = source / "config/console-entrypoints.json"
            if command_source.is_file():
                payload["commands"] = json.loads(command_source.read_text())
        else:
            payload["python"] = list(sys.version_info[:2])
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
    parser.add_argument("--frontend", choices=("stable", "native"), default="stable",
                        help="stable preserves the established console; native is an experimental port")
    args = parser.parse_args()
    try:
        manifest = build(args.source, args.output, args.frontend)
        print(json.dumps({"release": manifest["release"], "files": len(manifest["files"])}))
    except (OSError, ValueError, KeyError, TypeError, importlib.metadata.PackageNotFoundError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
