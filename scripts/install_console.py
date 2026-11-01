#!/usr/bin/env python3
"""Inspect a console installation by default; explicitly apply or roll it back."""
import argparse
import base64
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile

from build_console_release import digest, encoded, inventory, verify


def absolute(path):
    path = Path(os.path.abspath(path.expanduser()))
    if any(c in str(path) for c in ("\n", "\r", "\0")):
        raise ValueError("paths cannot contain line breaks or NUL")
    return path


def regular_parents(path):
    for parent in path.parents:
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise ValueError("destination parent is not a regular directory: " + str(parent))


def snapshot(path):
    regular_parents(path)
    if path.is_symlink():
        return {"kind": "link", "target": os.readlink(path)}
    if not path.exists():
        return {"kind": "absent"}
    if not path.is_file():
        raise ValueError("destination is not a file or symlink: " + str(path))
    body = path.read_bytes()
    return {"kind": "file", "body": base64.b64encode(body).decode(), "sha256": digest(body),
            "mode": stat.S_IMODE(path.stat().st_mode)}


def file_snapshot(body, mode=0o755):
    return {"kind": "file", "body": base64.b64encode(body).decode(), "sha256": digest(body), "mode": mode}


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def replace(path, value):
    """One atomic, durable launcher replacement; no shell evaluation of paths."""
    regular_parents(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if value["kind"] == "absent":
        path.unlink(missing_ok=True)
    else:
        with tempfile.TemporaryDirectory(prefix=".console-write-", dir=path.parent) as temporary:
            stage = Path(temporary) / "entry"
            if value["kind"] == "link":
                stage.symlink_to(value["target"])
            elif value["kind"] == "file":
                body = base64.b64decode(value["body"], validate=True)
                if digest(body) != value["sha256"]:
                    raise ValueError("backup byte identity mismatch")
                with stage.open("wb") as stream:
                    stream.write(body)
                    os.fchmod(stream.fileno(), value["mode"])
                    stream.flush()
                    os.fsync(stream.fileno())
            else:
                raise ValueError("unknown launcher snapshot kind")
            os.replace(stage, path)
    sync_directory(path.parent)


def locations(prefix):
    home = prefix / "share/godelclaw"
    return home, [home / "console-current", prefix / "bin/agent-console", prefix / "bin/godel-cli"]


@contextmanager
def locked(prefix):
    home, _ = locations(prefix)
    regular_parents(home / ".console-install.lock")
    home.mkdir(parents=True, exist_ok=True)
    lock = home / ".console-install.lock"
    if lock.is_symlink():
        raise ValueError("installation lock cannot be a symlink")
    with lock.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def check_published(target, files, manifest):
    regular_parents(target)
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_dir() or files != inventory(target):
            raise ValueError("immutable release content differs: " + str(target))
        path = target / "manifest.json"
        if path.read_bytes() != manifest or stat.S_IMODE(path.stat().st_mode) != 0o444:
            raise ValueError("immutable manifest differs: " + str(target))


def publish(source, target):
    """Never repair or merge an existing immutable directory."""
    files, manifest = inventory(source), (source / "manifest.json").read_bytes()
    check_published(target, files, manifest)
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".console-stage-", dir=target.parent) as temporary:
        stage = Path(temporary) / "release"
        shutil.copytree(source, stage)
        check_published(stage, files, manifest)
        for path in stage.rglob("*"):
            if path.is_file():
                with path.open("rb") as stream:
                    os.fsync(stream.fileno())
        for path in sorted((p for p in stage.rglob("*") if p.is_dir()), reverse=True):
            sync_directory(path)
        sync_directory(stage)
        if target.exists() or target.is_symlink():
            raise ValueError("immutable destination appeared during installation")
        os.rename(stage, target)
        sync_directory(target.parent)


def prepare(release, prefix, python, mail_script=None, replace_existing=False):
    release, prefix, python = map(absolute, (release, prefix, python))
    manifest = verify(release)
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("Python executable is missing or not executable")
    if mail_script is not None:
        mail_script = absolute(mail_script)
        if not mail_script.is_file():
            raise ValueError("configured mail script is missing")
    # Isolated Python cannot accidentally satisfy missing bundle imports from the
    # source checkout, user site, PYTHONPATH or the selected interpreter's site.
    entry = release / "files/scripts/console_entry.py"
    probe = subprocess.run([str(python), "-I", "-S", "-B", str(entry), "--check-console-imports"],
                           capture_output=True, text=True, timeout=30)
    if probe.returncode or probe.stdout.strip() != "console imports OK":
        raise ValueError("isolated console import check failed: " + probe.stderr.strip())
    version = subprocess.run([str(python), "-I", "-S", "-c",
                              "import json,sys; print(json.dumps(list(sys.version_info[:2])))"],
                             capture_output=True, text=True, timeout=30)
    if version.returncode or json.loads(version.stdout) != manifest["python"]:
        raise ValueError("selected Python major/minor differs from the build interpreter")
    home, destinations = locations(prefix)
    target = home / "releases" / ("console-" + manifest["release"])
    if release == target or release.is_relative_to(prefix):
        raise ValueError("build the input release outside the installation prefix")
    bindings = {}
    for name in ("agent-console", "godel-cli"):
        command = [str(python), "-I", "-S", "-B", str(target / "files/scripts/console_entry.py")]
        if name == "godel-cli":
            command += ["--agent", "godel"]
        if mail_script is not None:
            command += ["--mail-script", str(mail_script)]
        bindings[name] = ("#!/bin/sh\nexec " + shlex.join(command) + ' "$@"\n').encode()
    binding_payload = {"release": manifest["release"], "python_sha256": digest(python.read_bytes()),
                       "files": {name: digest(body) for name, body in bindings.items()}}
    binding_id = digest(encoded(binding_payload))
    binding = home / "console-bindings" / binding_id
    check_published(target, manifest["files"], (release / "manifest.json").read_bytes())
    check_published(binding, {name: {"sha256": digest(body), "mode": 0o555}
                              for name, body in bindings.items()}, encoded(binding_payload))
    after = [{"kind": "link", "target": str(binding)}]
    for name in ("agent-console", "godel-cli"):
        body = ('#!/bin/sh\nexec ' + shlex.quote(str(home / "console-current" / name)) + ' "$@"\n').encode()
        after.append(file_snapshot(body))
    before = [snapshot(path) for path in destinations]
    for path, old, new in zip(destinations, before, after):
        if old != new and old["kind"] != "absent" and not replace_existing:
            raise ValueError("existing launcher/selection requires --replace-existing and backup: " + str(path))
    return {"prefix": str(prefix), "release_source": str(release), "release_target": str(target),
            "release": manifest["release"], "binding": str(binding), "bindings": bindings,
            "binding_payload": binding_payload,
            "entries": [{"path": str(path), "before": old, "after": new}
                        for path, old, new in zip(destinations, before, after)]}


def check_backup(backup, plan):
    backup = absolute(backup)
    prefix = Path(plan["prefix"])
    source = Path(plan["release_source"])
    if (backup.is_relative_to(prefix) or prefix.is_relative_to(backup) or
            backup.is_relative_to(source) or source.is_relative_to(backup)):
        raise ValueError("backup must be outside the prefix and release input")
    regular_parents(backup / "before.json")
    if backup.exists() or backup.is_symlink():
        raise FileExistsError("backup directory already exists: " + str(backup))
    return backup


def save_receipt(backup, plan):
    backup = check_backup(backup, plan)
    backup.mkdir(parents=True, exist_ok=False, mode=0o700)
    payload = {"schema": 1, "prefix": plan["prefix"], "release": plan["release"], "entries": plan["entries"]}
    replace(backup / "before.json", file_snapshot(encoded(dict(payload, sha256=digest(encoded(payload)))), 0o600))
    sync_directory(backup.parent)


def apply(plan, backup):
    if verify(Path(plan["release_source"]))["release"] != plan["release"]:
        raise ValueError("release input changed after inspection")
    for entry in plan["entries"]:
        if snapshot(Path(entry["path"])) != entry["before"]:
            raise ValueError("launcher changed after inspection: " + entry["path"])
    save_receipt(backup, plan)  # Durable recovery data precedes any launcher mutation.
    publish(Path(plan["release_source"]), Path(plan["release_target"]))
    with tempfile.TemporaryDirectory(prefix=".console-binding-", dir=Path(plan["binding"]).parent.parent) as temporary:
        stage = Path(temporary)
        for name, body in plan["bindings"].items():
            (stage / name).write_bytes(body)
            (stage / name).chmod(0o555)
        (stage / "manifest.json").write_bytes(encoded(plan["binding_payload"]))
        (stage / "manifest.json").chmod(0o444)
        publish(stage, Path(plan["binding"]))
    for entry in plan["entries"]:
        path = Path(entry["path"])
        if snapshot(path) != entry["before"]:
            raise ValueError("launcher changed during install; use saved rollback: " + str(path))
        if entry["before"] != entry["after"]:
            replace(path, entry["after"])


def rollback(backup, apply_changes=False):
    document = json.loads((absolute(backup) / "before.json").read_text())
    payload = {key: value for key, value in document.items() if key != "sha256"}
    if document.get("schema") != 1 or digest(encoded(payload)) != document.get("sha256"):
        raise ValueError("backup receipt identity mismatch")
    prefix = absolute(Path(document["prefix"]))
    _, expected = locations(prefix)
    if [entry["path"] for entry in document["entries"]] != list(map(str, expected)):
        raise ValueError("backup destinations do not match its prefix")

    def restore():
        # Preflight all entries before restoring any; allow already-restored or
        # never-replaced entries after interruption. Refuse later manual edits.
        for entry in document["entries"]:
            if snapshot(Path(entry["path"])) not in (entry["before"], entry["after"]):
                raise ValueError("launcher changed since install; rollback refused: " + entry["path"])
        if apply_changes:
            for entry in reversed(document["entries"]):
                path = Path(entry["path"])
                if snapshot(path) != entry["before"]:
                    replace(path, entry["before"])
    if apply_changes:
        with locked(prefix):
            restore()
    else:
        restore()
    return {"rollback": str(backup), "applied": apply_changes, "entries": document["entries"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path)
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local")
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--mail-script", type=Path)
    parser.add_argument("--replace-existing", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--rollback", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--dry-run", action="store_true", help="explicit spelling of the default inspect mode")
    args = parser.parse_args()
    try:
        if args.rollback:
            if args.release or args.backup_dir or args.mail_script or args.replace_existing:
                raise ValueError("--rollback cannot be combined with installation options")
            result = rollback(args.rollback, args.apply)
        else:
            if args.release is None or (args.apply and args.backup_dir is None):
                raise ValueError("installation requires --release; --apply also requires a new --backup-dir")
            prefix = absolute(args.prefix)
            plan = prepare(args.release, prefix, args.python, args.mail_script, args.replace_existing)
            if args.backup_dir is not None:
                check_backup(args.backup_dir, plan)
            if args.apply:
                with locked(prefix):
                    apply(plan, args.backup_dir)
            result = {key: value for key, value in plan.items() if key != "bindings"}
            result["applied"] = args.apply
            result["backup_dir"] = str(absolute(args.backup_dir)) if args.backup_dir is not None else None
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
