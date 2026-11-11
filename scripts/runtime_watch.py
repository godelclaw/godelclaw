#!/usr/bin/env python3
"""Observe a deployed PettaClaw generation and manage its probation.

The watcher is outside the agent process. It combines process health with
channel, cognitive-boundary, and memory continuity observations. A candidate
that fails repeatedly during probation is atomically returned to the recorded
known-good Git generation; private runtime state is never switched or reset.
"""

import argparse
import ast
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import runtime_health  # noqa: E402


def _run(command, cwd=None, check=False):
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                          timeout=30, check=check)


def _read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, TypeError, ValueError):
        return {}


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".deployment-", dir=path.parent,
                                     text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _head(root):
    result = _run(["git", "rev-parse", "HEAD"], cwd=root, check=True)
    return result.stdout.strip()


def _service_active(service):
    return _run(["systemctl", "--user", "is-active", "--quiet", service]).returncode == 0


def memory_trend(previous, sample, window, growth_mib_per_minute, minimum_growth_mib):
    """Observe one service invocation; swapping is not memory reclamation.

    This diagnostic never restarts a service or changes agent budgets. A new
    invocation starts a new history, so a restart cannot produce a false slope.
    """
    identity = sample.get("invocation")
    history = [row for row in previous.get("history", [])
               if identity and row.get("invocation") == identity
               and row["at"] < sample["at"]]
    cutoff = sample["at"] - window
    older = [row for row in history if row["at"] <= cutoff]
    history = (older[-1:] if older else []) + [row for row in history if row["at"] > cutoff]
    history.append(sample)
    first = history[0]
    elapsed = sample["at"] - first["at"]
    growth = sample["total_bytes"] - first["total_bytes"]
    rate = growth / (1024 * 1024) * 60 / elapsed if elapsed > 0 else 0
    alarm = (bool(identity) and growth_mib_per_minute > 0 and elapsed >= window
             and rate >= growth_mib_per_minute
             and growth >= minimum_growth_mib * 1024 * 1024)
    return {"history": history, "sample": sample,
            "window_seconds": elapsed, "growth_mib_per_minute": rate,
            "alarm": alarm}


def observe_memory(args, now=None):
    """Standalone resource observation, separate from probation management."""
    now = time.time() if now is None else float(now)
    previous = _read_json(args.state).get("services", {})
    services = {}
    for unit in args.units:
        result = _run(["systemctl", "--user", "show", unit,
            "--property=InvocationID", "--property=MainPID",
            "--property=LoadState",
            "--property=ActiveState", "--property=MemoryCurrent",
            "--property=MemorySwapCurrent"])
        props = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        load_state = props.get("LoadState", "unknown")
        restart_unavailable = (props.get("ActiveState") == "active"
                               and load_state not in ("loaded", "unknown"))
        try:
            ram = int(props["MemoryCurrent"])
            swap = int(props["MemorySwapCurrent"])
            sample = {"at": now, "invocation": props.get("InvocationID", ""),
                      "pid": int(props.get("MainPID", 0)),
                      "active": props.get("ActiveState"),
                      "load_state": props.get("LoadState", "unknown"),
                      "ram_bytes": ram, "swap_bytes": swap,
                      "total_bytes": ram + swap}
        except (KeyError, ValueError):
            services[unit] = {"history": [], "alarm": False, "unavailable": True,
                              "load_state": load_state, "restart_unavailable": restart_unavailable}
            continue
        services[unit] = memory_trend(previous.get(unit, {}), sample, args.window,
                                     args.growth_mib_per_minute, args.minimum_growth_mib)
        services[unit].update(load_state=load_state, restart_unavailable=restart_unavailable)
    alarms = [unit for unit, facts in services.items() if facts.get("alarm")]
    _write_json(args.state, {"observed_at": now, "services": services,
                             "alarms": alarms,
                             "restart_unavailable": [unit for unit, facts in services.items()
                                                     if facts.get("restart_unavailable")]})
    for unit, facts in services.items():
        if facts.get("restart_unavailable"):
            print("WARNING restart-unavailable: " + unit + " load=" + facts["load_state"])
        if "sample" not in facts:
            print("memory-observation unavailable: " + unit)
            continue
        sample = facts["sample"]
        print("%s %s: total=%.1f MiB ram=%.1f MiB swap=%.1f MiB growth=%.1f MiB/min over=%.0fs" % (
            "WARNING memory-growth" if facts["alarm"] else "memory-observation", unit,
            sample["total_bytes"] / 2**20, sample["ram_bytes"] / 2**20,
            sample["swap_bytes"] / 2**20, facts["growth_mib_per_minute"], facts["window_seconds"]))
    # Warnings are observations, not a trigger for service recovery actions.
    return 0


def memory_parser():
    result = argparse.ArgumentParser(description="Observe RAM plus swap without restarting agents")
    result.add_argument("--units", nargs="+", required=True)
    result.add_argument("--state", type=pathlib.Path, required=True)
    result.add_argument("--window", type=float, default=180,
                        help="observation window in seconds")
    result.add_argument("--growth-mib-per-minute", type=float, default=16,
                        help="warning threshold; zero disables warnings")
    result.add_argument("--minimum-growth-mib", type=float, default=32,
                        help="minimum absolute growth before warning")
    return result


def _python_lifecycle_definitions(source, names):
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return False
    definitions = {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    return set(names) <= definitions


def _python_lifecycle_commands(source):
    """Recognize the actual cmd dispatch, not comments or documentation."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Compare)
                and isinstance(node.left, ast.Name) and node.left.id == "cmd"
                and len(node.ops) == 1 and isinstance(node.ops[0], ast.In)):
            continue
        values = set()
        for comparator in node.comparators:
            if isinstance(comparator, (ast.Tuple, ast.List, ast.Set)):
                values.update(element.value for element in comparator.elts
                              if isinstance(element, ast.Constant)
                              and isinstance(element.value, str))
        if {"/start", "/stop"} <= values:
            return True
    return False


def _metta_code(source):
    """Blank comments and strings while retaining executable source layout."""
    output = []
    quoted = escaped = comment = False
    for character in source:
        if comment:
            if character == "\n":
                comment = False
                output.append(character)
            else:
                output.append(" ")
        elif quoted:
            if character == "\n":
                output.append(character)
            else:
                output.append(" ")
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == ";":
            comment = True
            output.append(" ")
        elif character == '"':
            quoted = True
            output.append(" ")
        else:
            output.append(character)
    return "".join(output)


def _metta_definition(source, name):
    match = re.search(r"(?m)^\(=\s+\(" + re.escape(name) + r"(?=[\s)])",
                      source)
    if not match:
        return None
    # End at this form's closing parenthesis, not at the next definition.
    # A neighbouring top-level form cannot supply this definition's gate.
    depth = 0
    for at in range(match.start(), len(source)):
        if source[at] == "(":
            depth += 1
        elif source[at] == ")":
            depth -= 1
            if depth == 0:
                return source[match.start():at + 1]
    return None


def _metta_calls(source, definition, marker):
    body = _metta_definition(source, definition)
    return body is not None and marker in " ".join(body.split())


def _generation_has_lifecycle_authority(root, revision):
    """Refuse rollback across the operator-authority safety boundary."""
    paths = (
        "channels/telegram.py", "src/lifecycle.py", "src/lifecycle_cetta.metta",
        "src/loop.metta", "src/mode_state.metta",
    )
    sources = {}
    try:
        for relative in paths:
            result = _run(
                ["git", "show", "%s:%s" % (revision, relative)], cwd=root
            )
            sources[relative] = result.stdout if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return False

    commands = sources["channels/telegram.py"]
    if commands is None or not _python_lifecycle_commands(commands):
        return False
    loop = sources["src/loop.metta"]
    if loop is None:
        return False
    loop = _metta_code(loop)
    gates = ("turnCandidate", "waitCandidate")

    legacy = sources["src/lifecycle.py"]
    if (legacy is not None
            and _python_lifecycle_definitions(
                legacy, ("cognition_enabled", "stop", "start"))
            and all(_metta_calls(loop, gate, "(lifecycle.cognition_enabled)")
                    for gate in gates)):
        return True

    native = sources["src/lifecycle_cetta.metta"]
    facade = sources["src/mode_state.metta"]
    if native is None or facade is None:
        return False
    native = _metta_code(native)
    facade = " ".join(_metta_code(facade).split())
    native_names = ("enabled", "start", "stop")
    if any(_metta_definition(native, "lifecycle-native:" + name) is None
           for name in native_names):
        return False
    if any("(= (lifecycle:%s) (lifecycle-native:%s))" % (name, name)
           not in facade for name in native_names):
        return False
    return all(_metta_calls(loop, gate, "(lifecycle:enabled)")
               for gate in gates)


def observe(args, deployment, now=None):
    now = time.time() if now is None else float(now)
    os.environ["METTACLAW_TELEGRAM_HEALTH_PATH"] = str(args.health)
    os.environ["METTACLAW_WORKING_SET_PATH"] = str(args.working_set)
    if getattr(args, "cognitive_health", None):
        os.environ["METTACLAW_COGNITIVE_HEALTH_PATH"] = str(
            args.cognitive_health)
    facts = runtime_health.status(now=now)
    problems = list(facts["problems"])
    active = _service_active(args.service)
    if not active:
        problems.append("service-inactive")
    try:
        head = _head(args.root)
    except (OSError, subprocess.SubprocessError):
        head = "unknown"
        problems.append("generation-unreadable")
    if deployment.get("status") == "probation" and head != deployment.get("candidate"):
        problems.append("candidate-generation-mismatch")
    canonical_bytes = (args.canonical_memory.stat().st_size
                       if args.canonical_memory.exists() else 0)
    if canonical_bytes < int(deployment.get("baseline_canonical_bytes", 0)):
        problems.append("canonical-memory-regressed")
    return {
        "observed_at": now, "active": active, "head": head,
        "canonical_memory_bytes": canonical_bytes,
        "runtime": facts, "problems": sorted(set(problems)),
    }


def _rollback(args, deployment, observation):
    root = args.root
    if observation["head"] != deployment.get("candidate"):
        return False, "rollback-refused-generation-mismatch"
    if not _generation_has_lifecycle_authority(
            root, deployment.get("previous", "")):
        return False, "rollback-refused-lifecycle-regression"
    if (_run(["git", "diff", "--quiet"], cwd=root).returncode != 0
            or _run(["git", "diff", "--cached", "--quiet"], cwd=root).returncode != 0):
        return False, "rollback-refused-dirty-tree"
    _run(["systemctl", "--user", "stop", args.service], check=True)
    try:
        _run(["git", "switch", "--detach", deployment["previous"]],
             cwd=root, check=True)
    except Exception:
        _run(["systemctl", "--user", "start", args.service])
        raise
    _run(["systemctl", "--user", "start", args.service], check=True)
    return True, "rolled-back-to-known-good"


def manage(args, now=None):
    now = time.time() if now is None else float(now)
    deployment = _read_json(args.deployment)
    if not deployment:
        raise RuntimeError("deployment state is missing or invalid")
    observation = observe(args, deployment, now=now)
    problems = observation["problems"]
    action = "none"
    if problems:
        deployment["consecutive_failures"] = int(
            deployment.get("consecutive_failures", 0)) + 1
        if not observation["active"]:
            _run(["systemctl", "--user", "restart", args.service])
            action = "service-restarted"
        if (deployment.get("status") == "probation"
                and now < float(deployment.get("probation_until", 0))
                and deployment["consecutive_failures"] >= args.failures):
            success, action = _rollback(args, deployment, observation)
            if success:
                deployment["status"] = "rolled_back"
                deployment["rolled_back_at"] = now
                deployment["rollback_problems"] = problems
                deployment["consecutive_failures"] = 0
        elif deployment["consecutive_failures"] >= args.failures:
            _run(["systemctl", "--user", "restart", args.service])
            action = "service-restarted-after-repeated-failure"
            deployment["consecutive_failures"] = 0
    else:
        deployment["consecutive_failures"] = 0
        if (deployment.get("status") == "probation"
                and now >= float(deployment.get("probation_until", 0))):
            deployment["status"] = "accepted"
            deployment["accepted_at"] = now
            deployment["last_good"] = observation["head"]
            action = "candidate-accepted"
    deployment["last_observation"] = observation
    deployment["last_action"] = action
    _write_json(args.deployment, deployment)
    line = {
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "state": "ok" if not problems else "problem",
        "problems": problems, "action": action,
        "head": observation["head"][:12],
    }
    args.log.parent.mkdir(parents=True, exist_ok=True)
    with args.log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(line, sort_keys=True) + "\n")
    print(json.dumps(line, sort_keys=True))
    return 0 if not problems else 1


def parser():
    result = argparse.ArgumentParser()
    result.add_argument("--root", required=True, type=pathlib.Path)
    result.add_argument("--service", required=True)
    result.add_argument("--health", required=True, type=pathlib.Path)
    result.add_argument("--working-set", required=True, type=pathlib.Path)
    result.add_argument("--cognitive-health", type=pathlib.Path)
    result.add_argument("--canonical-memory", required=True, type=pathlib.Path)
    result.add_argument("--chroma", type=pathlib.Path, help=argparse.SUPPRESS)  # unused: installed units still pass it
    result.add_argument("--deployment", required=True, type=pathlib.Path)
    result.add_argument("--log", required=True, type=pathlib.Path)
    result.add_argument("--failures", type=int, default=3)
    return result


if __name__ == "__main__":
    if sys.argv[1:2] == ["memory"]:
        raise SystemExit(observe_memory(memory_parser().parse_args(sys.argv[2:])))
    raise SystemExit(manage(parser().parse_args()))
