#!/usr/bin/env python3
"""Bind an existing gateway configuration to its deployed runtime root.

Show the affected paths by default. --apply saves a backup before replacing
them. This command neither starts services nor changes queues or tool policy.
"""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from update_instances import install


ROOT = Path(__file__).resolve().parents[1]


def prepare(instance, runtime, config_dir, unit_dir):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", instance):
        raise ValueError("instance must be a simple service name")
    runtime = runtime.expanduser().resolve(strict=True)
    source = runtime / "src"
    if any(c in str(source) for c in ("\n", "\r", os.pathsep)):
        raise ValueError("runtime path cannot contain a line break or path separator")
    for name in ("execution_gateway/__main__.py", "execution_gateway/read_tools.py", "fsops.py"):
        if not (source / name).is_file():
            raise ValueError("deployed gateway module is missing: " + name)
    config_path = config_dir / f"gateway-{instance}.json"
    env_path = config_dir / f"gateway-{instance}.env"
    if config_path.is_symlink() or env_path.is_symlink():
        raise ValueError("gateway configuration must be regular files")
    config = json.loads(config_path.read_text())
    if config["agent"] != instance:
        raise ValueError("configuration belongs to another instance")
    # Only the supplied read adapter imports this runtime's modules. Preserve
    # custom tools, identities, state paths, credentials and effect classes.
    for tool in config["tools"].values():
        command = tool["command"]
        if len(command) >= 3 and command[1:3] == ["-m", "execution_gateway.read_tools"]:
            tool.setdefault("environment", {})["PYTHONPATH"] = str(source)
    lines = env_path.read_text().splitlines()
    lines = [line for line in lines if not line.startswith("PYTHONPATH=")]
    lines.append("PYTHONPATH=" + json.dumps(str(source), ensure_ascii=False))
    template = (ROOT / "systemd/pettaclaw-gateway@.service").read_bytes()
    unit = unit_dir / "pettaclaw-gateway@.service"
    if unit.is_file() and unit.read_bytes() != template:
        raise ValueError("custom gateway unit differs; preserve it and adapt its paths explicitly")
    links = {unit_dir / f"pettaclaw-gateway@{instance}.service": "pettaclaw-gateway@.service"}
    enabled = unit_dir / f"default.target.wants/pettaclaw-gateway@{instance}.service"
    # Installation preserves whether the operator enabled this instance.
    if enabled.exists() or enabled.is_symlink():
        links[enabled] = "../pettaclaw-gateway@.service"
    for path in links:
        if path.exists() and (not path.is_symlink() or path.read_bytes() != template):
            raise ValueError("custom instance unit exists: " + str(path))
    return {
        "files": {config_path: (json.dumps(config, indent=2) + "\n").encode(),
                  env_path: ("\n".join(lines) + "\n").encode(), unit: template},
        "links": links,
    }


def apply(plan, backup):
    backup.mkdir(parents=True, exist_ok=False, mode=0o700)
    destinations = [*plan["files"], *plan["links"]]
    before = []
    for index, path in enumerate(destinations):
        entry = {"path": str(path), "link": os.readlink(path) if path.is_symlink() else None,
                 "existed": path.exists() or path.is_symlink()}
        if path.is_file():
            saved = backup / str(index)
            shutil.copy2(path, saved)
            saved.chmod(0o600)
            entry["contents"] = saved.name
        before.append(entry)
    (backup / "before.json").write_text(json.dumps(before, indent=2) + "\n")
    with tempfile.TemporaryDirectory(prefix="gateway-install-", dir=backup) as stage:
        for index, (path, value) in enumerate(plan["files"].items()):
            source = Path(stage) / str(index)
            source.write_bytes(value)
            install(source, path, 0o644 if path.suffix == ".service" else 0o600)
        for path, target in plan["links"].items():
            # Atomic replacement also repairs a dangling instance link.
            source = Path(stage) / "unit-link"
            source.symlink_to(target)
            os.replace(source, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    parser.add_argument("--config-dir", type=Path, default=config_home / "pettaclaw")
    parser.add_argument("--unit-dir", type=Path, default=config_home / "systemd/user")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    try:
        plan = prepare(args.instance, args.runtime_root, args.config_dir, args.unit_dir)
        if args.apply:
            if args.backup_dir is None:
                raise ValueError("--apply requires a new --backup-dir")
            apply(plan, args.backup_dir)
        print(json.dumps({"applied": args.apply,
                          "files": [str(path) for path in plan["files"]],
                          "links": [str(path) for path in plan["links"]]}))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
