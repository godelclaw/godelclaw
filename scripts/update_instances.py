#!/usr/bin/env python3
"""Stage one reviewed runtime release into a group of independent instances.

The private plan supplies destinations, expected pre-update hashes, instance
overlays, service names and offline checks. All checks precede activation.
Backups cover both shared files and overlays; failures restore the whole group.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def relative(name):
    value = Path(name)
    if value.is_absolute() or ".." in value.parts:
        raise ValueError("release paths must be relative")
    return value


def install(source, target, mode=None):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".runtime-release-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(source.read_bytes())
            stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, mode if mode is not None else source.stat().st_mode & 0o777)
        os.replace(temporary, target)
        descriptor = os.open(target.parent, os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def service(name, operation):
    command = ["systemctl", "--user", operation, name]
    if operation == "show":
        command += ["--property=MainPID", "--value"]
    result = subprocess.run(command, capture_output=True,
                            text=True, timeout=20, check=True)
    return result.stdout.strip()


def running_settings(name, extra_keys=()):
    """Discover only nonsecret instance paths from the cognitive process."""
    group = subprocess.check_output(["systemctl", "--user", "show", name,
                                     "--property=ControlGroup", "--value"], text=True).strip()
    keys = {b"METTACLAW_ACTIVE_LOOP_MODE", b"METTACLAW_ENGINE_STATE_PATH",
            b"METTACLAW_RECYCLE_REQUEST_PATH", b"METTACLAW_WAKE_REQUEST_PATH"}
    allowed = {"METTACLAW_OBSERVED_STRATEGY", "METTACLAW_STRATEGY_PATH", "METTACLAW_FAIR_GOALS",
               "METTACLAW_ACTIVE_ENGINE", "CETTA_BIN", "METTACLAW_LLM_TRANSPORT",
               "METTACLAW_LLM_POLICY", "METTACLAW_COMMAND_BATCH_LIMIT", "METTACLAW_INSTANCE",
               "METTACLAW_INPUT_ACK_SECONDS", "METTACLAW_INPUT_POLICY",
               "METTACLAW_BACKGROUND_MODELS", "METTACLAW_BACKGROUND_TOKENS"}
    allowed.update(key.decode() for key in keys)
    if set(extra_keys) - allowed:
        raise ValueError("runtime checks may inspect only nonsecret settings")
    keys.update(k.encode() for k in extra_keys)
    candidates = []
    for pid in (Path("/sys/fs/cgroup") / group.lstrip("/") / "cgroup.procs").read_text().splitlines():
        try:
            environment = {}
            for entry in Path("/proc", pid, "environ").read_bytes().split(b"\0"):
                key, _, value = entry.partition(b"=")
                if key in keys:
                    environment[key.decode()] = value.decode()
            if "METTACLAW_ACTIVE_LOOP_MODE" in environment and "METTACLAW_ENGINE_STATE_PATH" in environment:
                stat = Path("/proc", pid, "stat").read_text().rsplit(")", 1)[1].split()
                candidates.append((int(stat[19]), environment))
        except (OSError, ValueError):
            continue
    if not candidates:
        raise RuntimeError("cannot discover the running instance's state paths")
    return min(candidates, key=lambda item: item[0])[1]


def await_boundaries(entries, pids, timeout):
    deadline = time.monotonic() + float(timeout)
    waiting = list(entries)
    while waiting:
        waiting = [entry for entry in waiting if
                   service(entry[0]["service"], "show") in ("0", pids[entry[0]["service"]])]
        if time.monotonic() > deadline:
            raise TimeoutError("an instance did not reach its update boundary")
        if waiting:
            time.sleep(1)


def recycle(instance, reason):
    state = Path(instance["state_dir"])
    state.mkdir(parents=True, exist_ok=True)
    Path(instance.get("recycle_path", state / "recycle.requested")).write_text(reason + "\n")
    Path(instance.get("wake_path", state / "wake.requested")).write_text(reason + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--receipt-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    release = args.release.resolve()
    manifest = json.loads((release / "manifest.json").read_text())
    plan = json.loads(args.plan.read_text())
    instances = plan["instances"]
    if len(instances) < 2:
        raise ValueError("a grouped update needs at least two instances")
    control_services = [name for instance in instances for name in instance.get("control_services", [])]
    if (any(not isinstance(instance.get("control_services", []), list) for instance in instances)
            or any(not isinstance(name, str) or not name or name.startswith("-") for name in control_services)
            or len(set(control_services)) != len(control_services)
            or set(control_services) & {instance["service"] for instance in instances}):
        raise ValueError("independent control services must be distinct from cognitive services")
    roots = [Path(i["root"]).resolve() for i in instances]
    if len(set(roots)) != len(roots):
        raise ValueError("instances must have separate roots")
    state_dirs = [Path(i["state_dir"]).resolve() for i in instances]
    if len(set(state_dirs)) != len(state_dirs):
        raise ValueError("instances must have separate state directories")
    files = {str(relative(n)): h for n, h in manifest["files"].items()}
    for name, expected in files.items():
        if sha(release / "files" / name) != expected:
            raise ValueError("release source hash mismatch: " + name)
        if name.startswith(("memory/", "config/secrets", "config/local")) or name in (".env", "AGENTS.md", "CLAUDE.md"):
            raise ValueError("instance data cannot be a shared release file")
    output = args.receipt_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    record = {"release": manifest["release"], "status": "preflight", "instances": []}
    entries = []
    for index, (instance, root) in enumerate(zip(instances, roots)):
        actual_settings = running_settings(instance["service"])
        expected_settings = {
            "METTACLAW_ENGINE_STATE_PATH": str(Path(instance["state_dir"]) / "engine"),
            "METTACLAW_RECYCLE_REQUEST_PATH": instance.get("recycle_path", str(Path(instance["state_dir"]) / "recycle.requested")),
            "METTACLAW_WAKE_REQUEST_PATH": instance.get("wake_path", str(Path(instance["state_dir"]) / "wake.requested")),
        }
        if any(actual_settings.get(key) != value for key, value in expected_settings.items()):
            raise ValueError("deployment paths differ from the running instance: " + instance["name"])
        overlays = {str(relative(n)): Path(p) for n, p in instance.get("overlays", {}).items()}
        if set(overlays) & set(files):
            raise ValueError("instance overlays cannot replace shared runtime files")
        names = list(files) + list(overlays)
        if set(instance["expected"]) != set(names):
            raise ValueError("every destination needs an expected pre-update hash")
        before = {}
        for name in names:
            actual = sha(root / name)
            if actual != instance["expected"][name]:
                raise ValueError("instance changed since review: " + name)
            if actual is not None:
                backup = output / "backups" / str(index) / name
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / name, backup)
            before[name] = actual
        pid = service(instance["service"], "show")
        entries.append((instance, root, overlays, before, pid))
        record["instances"].append({"name": instance["name"], "pid_before": pid, "before": before})
    def save():
        (output / "receipt.json").write_text(json.dumps(record, indent=2) + "\n")
    save()
    if not args.apply:
        record["status"] = "prepared"; save()
        print(json.dumps({"release": manifest["release"], "status": "prepared"}))
        return 0
    held = []
    released = False
    activated_controls = []
    try:
        if "run.sh" not in files:
            raise ValueError("paired cognitive activation requires the launcher gate")
        for instance, root, overlays, before, pid in entries:
            hold = root / ".runtime-update-hold"
            if hold.exists():
                raise ValueError("another runtime update holds this instance")
            hold.write_text(manifest["release"] + "\n"); held.append(hold)
            install(release / "files/run.sh", root / "run.sh")
        for instance, root, overlays, before, pid in entries:
            recycle(instance, "paired runtime update")
        await_boundaries(entries, {entry[0]["service"]: entry[4] for entry in entries},
                         plan.get("boundary_timeout", 180))
        record["status"] = "held"; save()
        # Recheck destinations after the old processes have finished writing.
        for instance, root, overlays, before, pid in entries:
            for name, expected in before.items():
                current = files[name] if name == "run.sh" else expected
                if sha(root / name) != current:
                    raise ValueError("destination changed while reaching boundary: " + name)
            for name in files:
                install(release / "files" / name, root / name)
            for name, source in overlays.items():
                install(source, root / name)
            for check_index, command in enumerate(instance["checks"]):
                env = dict(os.environ, **instance.get("environment", {}))
                result = subprocess.run(command, cwd=root, env=env, stdin=subprocess.DEVNULL,
                                        capture_output=True, text=True, timeout=90)
                (output / (instance["name"] + "-check-" + str(check_index) + ".log")).write_text(result.stdout + result.stderr)
                if result.returncode or any(marker not in result.stdout for marker in instance.get("markers", {}).get(str(check_index), [])):
                    raise RuntimeError("offline qualification failed for " + instance["name"])
            actual = {name: sha(root / name) for name in files}
            if actual != files:
                raise ValueError("shared runtime changed during qualification")
            record["instances"][roots.index(root)]["after"] = actual
        record["status"] = "qualified"; save()
        for name in control_services:
            activated_controls.append(name)
            service(name, "restart")
        # Mark activation before the first restart, so a partial cognitive
        # restart failure also acquires the candidate's rollback boundary.
        released = True
        for instance, root, overlays, before, pid in entries:
            service(instance["service"], "restart")
        for hold in held:
            hold.unlink()
        deadline = time.monotonic() + float(plan.get("boot_timeout", 60))
        while True:
            booted = True
            for instance, root, overlays, before, pid in entries:
                live_pid = service(instance["service"], "show")
                try:
                    active = json.loads((Path(instance["state_dir"]) / "active-loop.json").read_text())
                except (OSError, ValueError):
                    active = {}
                booted &= live_pid not in ("0", pid) and str(active.get("pid")) == live_pid
                required = instance.get("required_runtime", {})
                if required:
                    try:
                        actual_runtime = running_settings(instance["service"], required)
                        booted &= all(actual_runtime.get(key) == value for key, value in required.items())
                    except RuntimeError:
                        booted = False
            booted &= all(service(name, "show") != "0" for name in activated_controls)
            if booted:
                break
            if time.monotonic() > deadline:
                raise TimeoutError("paired runtime did not boot")
            time.sleep(1)
        for index, (instance, root, overlays, before, pid) in enumerate(entries):
            marker = {"schema": 1, "release": manifest["release"], "files": files,
                      "pid": service(instance["service"], "show")}
            path = Path(instance["state_dir"]) / "runtime-release.json"
            path.write_text(json.dumps(marker, sort_keys=True) + "\n")
            record["instances"][index]["pid_after"] = marker["pid"]
        record["status"] = "activated"; save()
        record["control_services"] = {name: service(name, "show") for name in activated_controls}
        save()
        # Advance the private plan's expected base only after every instance
        # booted. A subsequent edit to shared code still fails preflight.
        (output / "plan-before.json").write_text(json.dumps(plan, indent=2) + "\n")
        for instance, root, overlays, before, pid in entries:
            if instance.pop("overlays_once", False):
                instance.pop("overlays", None)
                instance["expected"] = {name: sha(root / name) for name in files}
            else:
                instance["expected"] = {name: sha(root / name) for name in list(files) + list(overlays)}
        prepared_plan = output / "next-plan.json"
        prepared_plan.write_text(json.dumps(plan, indent=2) + "\n")
        install(prepared_plan, args.plan, 0o600)
        print(json.dumps({"release": manifest["release"], "status": "activated", "instances": len(entries)}))
        return 0
    except BaseException as error:
        record["error"] = str(error)
        if released:
            # A candidate may have begun an action. Do not replace its source
            # or kill it halfway through: acquire both ordinary boundaries.
            candidate_pids = {entry[0]["service"]: service(entry[0]["service"], "show")
                              for entry in entries}
            for instance, root, overlays, before, pid in entries:
                (root / ".runtime-update-hold").write_text("rollback\n")
                recycle(instance, "rollback")
            try:
                await_boundaries(entries, candidate_pids, plan.get("boundary_timeout", 180))
            except TimeoutError:
                record["status"] = "rollback-pending"; save()
                raise RuntimeError("rollback is held pending a candidate's ordinary boundary") from error
        for index, (instance, root, overlays, before, pid) in enumerate(entries):
            if root / ".runtime-update-hold" not in held:
                continue
            for name, old in before.items():
                target = root / name
                if old is None:
                    target.unlink(missing_ok=True)
                else:
                    install(output / "backups" / str(index) / name, target)
            # Only the held launcher is restarted. The old cognitive process
            # is allowed to finish naturally if the boundary was not reached.
            current_pid = service(instance["service"], "show")
            if current_pid not in ("0", pid):
                service(instance["service"], "restart")
        for name in activated_controls:
            service(name, "restart")
        for hold in held:
            hold.unlink(missing_ok=True)
        record["status"] = "rolled-back"; save()
        raise


if __name__ == "__main__":
    raise SystemExit(main())
