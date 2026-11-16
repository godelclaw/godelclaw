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
import sys
import tempfile

from update_instances import install


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from execution_gateway.cetta_worker import (WorkerUnavailable, describe_gateway_worker,
                                          validate_gateway_worker_bundle)


def prepare(instance, runtime, config_dir, unit_dir, worker=None):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", instance):
        raise ValueError("instance must be a simple service name")
    runtime = runtime.expanduser().resolve(strict=True)
    source = runtime / "src"
    if any(c in str(source) for c in ("\n", "\r", os.pathsep)):
        raise ValueError("runtime path cannot contain a line break or path separator")
    for name in (
        "execution_gateway/host_cetta.metta",
        "execution_gateway/protocol_cetta.metta",
        "execution_gateway/native_queue_cetta.metta",
        "execution_gateway/read_tools_cetta.metta",
        "execution_gateway/GATEWAY_CETTA_REF",
        "execution_gateway/metta/gateway_queue.metta",
        "execution_gateway/metta/native_kb_server.metta",
        "execution_gateway/metta/native_space_query_server.metta",
        "execution_gateway/metta/kb/space_job_queue.metta",
        "file_io.metta",
        "tool_output_cetta.metta",
    ):
        if not (source / name).is_file():
            raise ValueError("deployed gateway module is missing: " + name)
    config_path = config_dir / f"gateway-{instance}.json"
    env_path = config_dir / f"gateway-{instance}.env"
    if config_path.is_symlink() or env_path.is_symlink():
        raise ValueError("gateway configuration must be regular files")
    config = json.loads(config_path.read_text())
    if config["agent"] != instance:
        raise ValueError("configuration belongs to another instance")
    reference = (source / "execution_gateway/GATEWAY_CETTA_REF").read_text().strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", reference):
        raise ValueError("runtime has an invalid gateway engine pin")
    selected = worker if worker is not None else config.get("cetta_worker")
    try:
        if selected is None:
            selected = describe_gateway_worker()["path"]
        verified = validate_gateway_worker_bundle(selected, expected_ref=reference)
    except WorkerUnavailable as error:
        raise ValueError("gateway needs a verified worker for its runtime pin; supply --worker") from error
    config["cetta_worker"] = str(verified["path"])
    # Tool descriptors, including legacy PYTHONPATH compatibility data, remain
    # byte-for-byte values in fingerprints. The native host binds supplied
    # read descriptors to native handlers without executing their old modules.
    engine = Path(verified["path"]).parent / "cetta"
    engine_info = json.loads((engine.parent / "build-manifest.json").read_text())
    if engine_info.get("python_enabled") is not False or engine_info.get("runtime_profile") != "native-agent":
        raise ValueError("gateway host requires the Python-disabled native engine")
    entry = runtime / "scripts/execution_gateway_cetta.metta"
    if not entry.is_file() or not (runtime / "scripts/gateway_read_cetta.metta").is_file():
        raise ValueError("native gateway entrypoint or read adapters are missing")
    lines = env_path.read_text().splitlines()
    lines = [line for line in lines if not line.startswith(("PYTHONPATH=", "PYTHONHOME=", "GATEWAY_CETTA_BIN=", "GATEWAY_CETTA_ENTRY="))]
    lines.extend(("GATEWAY_CETTA_BIN=" + json.dumps(str(engine), ensure_ascii=False),
                  "GATEWAY_CETTA_ENTRY=" + json.dumps(str(entry), ensure_ascii=False)))
    template = (ROOT / "systemd/pettaclaw-gateway@.service").read_bytes()
    native_exec = (b"ExecStart=/usr/bin/env ${GATEWAY_CETTA_BIN} --no-results --lang petta --profile extended "
                   b"--import-mode ancestor-walk ${GATEWAY_CETTA_ENTRY} --config %h/.config/pettaclaw/gateway-%i.json")
    legacy = template.replace(native_exec, b"ExecStart=/usr/bin/python3 -m execution_gateway --config %h/.config/pettaclaw/gateway-%i.json")
    unit = unit_dir / "pettaclaw-gateway@.service"
    if unit.is_file() and unit.read_bytes() not in (template, legacy):
        raise ValueError("custom gateway unit differs; preserve it and adapt its paths explicitly")
    links = {unit_dir / f"pettaclaw-gateway@{instance}.service": "pettaclaw-gateway@.service"}
    enabled = unit_dir / f"default.target.wants/pettaclaw-gateway@{instance}.service"
    # Installation preserves whether the operator enabled this instance.
    if enabled.exists() or enabled.is_symlink():
        links[enabled] = "../pettaclaw-gateway@.service"
    for path in links:
        if path.exists() and (not path.is_symlink() or path.read_bytes() not in (template, legacy)):
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
    parser.add_argument("--worker", type=Path, help="verified worker matching the deployed runtime's engine pin")
    args = parser.parse_args()
    try:
        plan = prepare(args.instance, args.runtime_root, args.config_dir, args.unit_dir, args.worker)
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
