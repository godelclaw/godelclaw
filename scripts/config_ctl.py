#!/usr/bin/env python3
"""Inspect or update a nonsecret setting and regenerate its environment."""
import argparse
import copy
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import tomllib


def atomic_write(path, text):
    fd, temporary = tempfile.mkstemp(prefix=".config-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def replace_setting(text, section, key, value):
    """Accept a text edit only if it changes exactly the requested value."""
    before = tomllib.loads(text)
    expected = copy.deepcopy(before)
    expected.setdefault(section, {})[key] = value
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines)
                  if re.fullmatch(r"\s*\[" + re.escape(section) + r"\]\s*(?:#.*)?\s*", line)), None)
    if start is None:
        candidate = text.rstrip() + f"\n\n[{section}]\n{key} = {encoded}\n"
    else:
        end = next((i for i in range(start + 1, len(lines))
                    if re.match(r"\s*\[", lines[i])), len(lines))
        existing = [i for i in range(start + 1, end)
                    if re.match(r"\s*" + re.escape(key) + r"\s*=", lines[i])]
        if existing:
            lines[existing[0]] = f"{key} = {encoded}\n"
        else:
            lines.insert(start + 1, f"{key} = {encoded}\n")
        candidate = "".join(lines)
    if tomllib.loads(candidate) != expected:
        raise ValueError("complex TOML setting requires a direct TOML edit")
    return candidate


def render(root, text):
    fd, temporary = tempfile.mkstemp(prefix=".config-check-", dir=root / "config")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        result = subprocess.run([sys.executable, str(root / "scripts/config_to_env.py"),
                                 temporary, str(root)], capture_output=True, text=True)
        if result.returncode:
            raise ValueError("configuration validation failed")
        return result.stdout
    finally:
        os.unlink(temporary)


def exports(environment):
    """The KEY -> value pairs of a rendered .env."""
    pairs = {}
    for line in environment.splitlines():
        if line.startswith("export "):
            for word in shlex.split(line[len("export "):]):
                key, _, value = word.partition("=")
                pairs[key] = value
    return pairs


def warn_conflicts(environment):
    """Say when the caller's environment (a systemd drop-in, a shell) sets a
    key the configuration also sets: the configuration silently wins."""
    for key, value in exports(environment).items():
        inherited = os.environ.get(key)
        if inherited is not None and inherited != value:
            secret = any(word in key.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
            shown = (lambda text: "(hidden)") if secret else repr
            print("config: %s=%s from the caller's environment is replaced by %s from config/local.toml "
                  "or config/default.toml; set it there instead" % (key, shown(inherited), shown(value)),
                  file=sys.stderr)


def show(root, current):
    """Every configured value with the file it comes from."""
    defaults = tomllib.loads((root / "config/default.toml").read_text())
    rows = []
    for section in sorted(set(defaults) | set(current)):
        given, base = current.get(section, {}), defaults.get(section, {})
        if not isinstance(given, dict) or not isinstance(base, dict):
            continue
        for key in sorted(set(base) | set(given)):
            origin = "local.toml" if key in given else "default.toml"
            if key in given and key not in base:
                origin = "local.toml (no default)"
            if any(word in key.lower() for word in ("secret", "password", "api_key")):
                continue
            rows.append({"setting": section + "." + key, "value": given.get(key, base.get(key)), "origin": origin})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("operation", choices=("get", "set", "check", "render", "show"))
    parser.add_argument("setting", nargs="?")
    parser.add_argument("value", nargs="?")
    args = parser.parse_args()
    root = args.root.resolve()
    lock = root / "config/.edit.lock"
    with lock.open("a+") as handle:
        os.chmod(lock, 0o600)
        fcntl.flock(handle, fcntl.LOCK_EX)
        path = root / "config/local.toml"
        text = path.read_text()
        current = tomllib.loads(text)
        if args.operation == "show":
            for row in show(root, current):
                print(json.dumps(row, ensure_ascii=False))
            return 0
        if args.operation in ("check", "render"):
            environment = render(root, text)
            if args.operation == "render":
                warn_conflicts(environment)
                if not args.dry_run:
                    atomic_write(root / ".env", environment)
            print(json.dumps({"valid": True}))
            return 0
        if not args.setting or not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", args.setting):
            raise ValueError("setting must be a section.key")
        section, key = args.setting.split(".")
        defaults = tomllib.loads((root / "config/default.toml").read_text())
        if key not in defaults.get(section, {}) or any(
                word in key.lower() for word in ("secret", "password", "api_key")):
            raise ValueError("setting is not an editable nonsecret configuration key")
        old = current.get(section, {}).get(key, defaults[section][key])
        if args.operation == "get":
            print(json.dumps({"setting": args.setting, "value": old}))
            return 0
        if args.value is None:
            raise ValueError("set requires a value")
        value = args.value if isinstance(defaults[section][key], str) else json.loads(args.value)
        if type(value) is not type(defaults[section][key]):
            if not (type(defaults[section][key]) is int and type(value) is float):
                raise ValueError("value has the wrong configuration type")
        candidate = replace_setting(text, section, key, value)
        environment = render(root, candidate)
        if not args.dry_run:
            if (root / ".runtime-update-hold").exists():
                raise ValueError("paired runtime update currently holds this configuration")
            # The source is durable first. Startup can regenerate the derived
            # file if a process stops between the two atomic replacements.
            atomic_write(path, candidate)
            atomic_write(root / ".env", environment)
        print(json.dumps({"setting": args.setting, "value": value,
                          "applied": not args.dry_run, "restart_required": not args.dry_run}))
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, TypeError, AttributeError, KeyError):
        print(json.dumps({"error": "configuration update rejected; check the key, type and TOML syntax"}),
              file=sys.stderr)
        raise SystemExit(2)
