"""Append-only session files for coding mode: one line per record, no backups."""
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time


def execution_path(directory, identity, suffix):
    if not re.fullmatch(r"[a-f0-9]{32}", str(identity)):
        raise ValueError("invalid execution identity")
    return Path(str(directory)) / "executions" / (str(identity) + suffix)


def execution_create(directory, identity, command):
    if os.environ.get("METTACLAW_CODING_CHECK") == "1":
        raise RuntimeError("execution_create refused under METTACLAW_CODING_CHECK=1")
    path = execution_path(directory, identity, ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(dict(id=str(identity), command=str(command), status="dispatching",
                       started=time.time()), stream)
    return str(path.with_suffix(".log"))


def execution_finish(directory, identity, status, code, seconds):
    path = execution_path(directory, identity, ".json")
    record = json.loads(path.read_text())
    record.update(status=str(status), code=int(code), seconds=float(seconds), ended=time.time())
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record))
    os.replace(temporary, path)
    return execution_result(directory, identity)


def execution_result(directory, identity):
    try:
        path = execution_path(directory, identity, ".json")
        record = json.loads(path.read_text())
        log = path.with_suffix(".log")
        size = log.stat().st_size if log.exists() else 0
        with log.open("rb") as stream:
            head = stream.read(20000)
            if size > 40000:
                stream.seek(-20000, 2)
                raw = head + b"\n... tokens truncated; full output in artifact ...\n" + stream.read(20000)
            else:
                raw = head + stream.read(20000)
        status = record["status"]
        outcome = str(record.get("code", "?")) if status == "exited" else status
        if status in ("dispatching", "running"):
            outcome = "uncertain: this runtime does not own the execution; it was not replayed"
        return (f"Exit code: {outcome}\nWall time: {record.get('seconds', '?')} seconds\n"
                f"Execution ID: {identity}\nOutput artifact: {log}\nOutput:\n" + raw.decode("utf-8", "replace"))
    except (OSError, ValueError, KeyError):
        return "Unknown execution ID; nothing was started."


def transient_root():
    root = Path(os.environ.get("TMPDIR", Path.home() / ".cache/godelclaw"))
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.mkdtemp(prefix="command-", dir=root)


@contextlib.contextmanager
def _ending_lock(directory):
    import fcntl
    directory = Path(str(directory))
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / ".ending.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o660)
    with os.fdopen(fd, "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _fsync_dir(directory):
    try:
        dfd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dfd)
    except OSError:
        pass
    finally:
        os.close(dfd)


def _write_json_atomic(path, payload):
    path = Path(str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o660)
            json.dump(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_dir(path.parent)
    finally:
        if os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _read_json(path):
    try:
        value = json.loads(Path(str(path)).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _owner_alive(owner):
    text = str(owner or "").strip()
    parts = text.split(":")
    if len(parts) != 2 or not parts[0].isdigit():
        return False
    pid, birth = int(parts[0]), parts[1]
    if pid <= 0 or not birth:
        return False
    if birth == "portable":
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
    try:
        import native_lifetime
        return native_lifetime.process_start(pid) == birth
    except Exception:
        return False


def _clip_text(text, limit):
    text = str(text)
    return text if len(text) <= limit else text[:limit] + " …"


def _mark_state_unlocked(directory, status, report=None, identity=None, expected_owner=None):
    directory = Path(str(directory))
    path = directory / "state.json"
    runtime_path = directory / "runtime.json"
    state = _read_json(path)
    runtime = _read_json(runtime_path) if runtime_path.is_file() else {}
    if expected_owner is not None:
        if state.get("status") != "running":
            return 0
        if str(state.get("worker_owner") or "") != str(expected_owner):
            return 0
        if runtime.get("phase") is not None:
            if runtime.get("phase") != "started":
                return 0
            if state.get("run") and runtime.get("run") != state.get("run"):
                return 0
    else:
        if state.get("status") == str(status) and state.get("delivery_id"):
            return 1
    state.update(status=str(status), delivery="pending", updated=time.time())
    if report is not None:
        state.update(progress=str(report), delivery_version=2 if identity else 1)
        (directory / "report.txt").write_text(str(report), encoding="utf-8")
    if identity:
        state["delivery_id"] = str(identity)
    _write_json_atomic(path, state)
    if runtime_path.is_file() and runtime:
        if not state.get("run") or runtime.get("run") == state.get("run"):
            runtime["phase"] = "ended"
            _write_json_atomic(runtime_path, runtime)
    return 1


def _settle_ending_unlocked(directory, expected_owner=None, *, require_dead_owner=False):
    directory = Path(str(directory))
    state = _read_json(directory / "state.json")
    if state.get("status") != "running":
        return ""
    owner = str(state.get("worker_owner") or "")
    if expected_owner is None:
        expected_owner = owner
    elif owner != str(expected_owner):
        return ""
    if require_dead_owner and _owner_alive(owner):
        return ""
    ending = "stopped" if (directory / "stop").is_file() else "interrupted"
    why = (
        "Stopped by the requester."
        if ending == "stopped"
        else "The session ended without finishing its recorded ending: its process is gone (it was stopped, killed or crashed)."
    )
    progress = str(state.get("progress") or "")
    plan_path = directory / "plan.json"
    plan = _clip_text(plan_path.read_text(encoding="utf-8"), 2000) if plan_path.is_file() else "none recorded"
    report_path = directory / "report.txt"
    saved = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
    report = (
        f"{why}\n\nLast progress:\n{progress or 'none recorded'}"
        + (f"\n\nSaved report:\n{saved}" if saved else "")
        + f"\n\nPlan:\n{plan}\n\nResume without replaying uncertain effects:\n(work-resume \"{directory.name}\")"
    )
    updated = state.get("updated")
    steps = state.get("steps")
    identity = hashlib.sha256(
        json.dumps([directory.name, ending, "", updated, steps], separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]
    if not _mark_state_unlocked(directory, ending, report, identity, expected_owner=expected_owner):
        return ""
    return ending


def settle_ending(directory, expected_owner=None):
    """Settle an interrupted or stopped running session under .ending.lock.

    Shared by work:reap-one, work:ended, freeze_runtime, and repin_session so the
    handover report and stopped/interrupted decision have one implementation.
    Note that work-stop on a session that is not 'running' records no ending here
    (because conditional mark_state returns 0 when status != 'running'), while
    stop_session revokes any 'reserved' launch token.
    """
    with _ending_lock(directory):
        return _settle_ending_unlocked(directory, expected_owner=expected_owner)


def stop_session(directory):
    """Write the stop flag and revoke any reserved launch token under .ending.lock."""
    directory = Path(str(directory))
    runtime_path = directory / "runtime.json"
    with _ending_lock(directory):
        (directory / "stop").write_text("", encoding="utf-8")
        if runtime_path.is_file():
            runtime = _read_json(runtime_path)
            if runtime.get("phase") == "reserved":
                runtime["phase"] = "ended"
                _write_json_atomic(runtime_path, runtime)
    return 1


def _resolve_engine_binary():
    binary = os.environ.get("CETTA_BIN", "cetta")
    binary = os.path.realpath(shutil.which(binary) or binary)
    try:
        engine = _file_digest(binary)
    except OSError:
        engine = None
    return binary, engine


def freeze_bundle(root, cache=None):
    """Freeze the runtime files listed in config/runtime-files.json into cache/<digest>."""
    root = Path(str(root))
    if not (root / "config/runtime-files.json").is_file():
        root = Path(__file__).resolve().parents[1]
    selection = root / "config/runtime-files.json"
    names = json.loads(selection.read_text(encoding="utf-8"))
    if isinstance(names, dict):
        names = names["files"]
    files = {}
    for name in names:
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("runtime selection must be relative")
        files[name] = (root / path).read_bytes()
    digest = hashlib.sha256(json.dumps({
        name: hashlib.sha256(data).hexdigest()
        for name, data in files.items()
    }, sort_keys=True).encode()).hexdigest()
    if cache is None:
        cache = root / "memory" / "coding-runtimes"
    else:
        cache = Path(str(cache))
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / digest
    if not target.exists():
        temporary = Path(tempfile.mkdtemp(prefix=".stage-", dir=cache))
        try:
            for name, data in files.items():
                path = temporary / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                shutil.copymode(root / name, path)
            try:
                os.rename(temporary, target)
            except FileExistsError:
                pass
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    return str(target), digest


def freeze_runtime(root, directory):
    """Pin imports and instructions for a session, including across resumes."""
    root, directory = Path(str(root)), Path(str(directory))
    if not (root / "config/runtime-files.json").is_file():
        root = Path(__file__).resolve().parents[1]
    record = directory / "runtime.json"
    with _ending_lock(directory):
        _settle_ending_unlocked(directory, require_dead_owner=True)
        if record.exists():
            value = json.loads(record.read_text(encoding="utf-8"))
            if not (Path(value["root"]) / "coding_main.metta").is_file():
                raise RuntimeError("session runtime is missing; restore it before resuming")
            value.setdefault("checkpoint", None)
            value.setdefault("previous", None)
            value["run"] = secrets.token_hex(16)
            value["phase"] = "reserved"
            value["at"] = time.time()
            _write_json_atomic(record, value)
            return str(value["root"])
        target, digest = freeze_bundle(root, directory.parent.parent / "coding-runtimes")
        binary, engine = _resolve_engine_binary()
        # The engine is pinned by its resolved file and content: a resume after
        # an upgrade or a link flip must not run a different engine.
        manifest = dict(
            root=str(target),
            digest=digest,
            binary=binary,
            binary_sha256=engine,
            checkpoint=None,
            previous=None,
            run=secrets.token_hex(16),
            phase="reserved",
            at=time.time(),
        )
        _write_json_atomic(record, manifest)
        return str(target)


def current_run_token(directory):
    return str(_read_json(Path(str(directory)) / "runtime.json").get("run") or "")


def _file_digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def engine_problem(directory):
    """Why a session cannot resume on the engine it started on, or ""."""
    record = Path(str(directory)) / "runtime.json"
    if not record.is_file():
        return ""
    value = json.loads(record.read_text())
    if not value.get("binary_sha256"):
        return ""
    try:
        current = _file_digest(value["binary"])
    except OSError:
        return "the engine this session started on is gone (%s)" % value["binary"]
    if current != value["binary_sha256"]:
        return "the engine this session started on has changed since (%s)" % value["binary"]
    return ""


def mark_state(directory, status, report=None, identity=None, expected_owner=None):
    """Settle one ending; duplicate or stale recovery observers are rejected."""
    with _ending_lock(directory):
        return _mark_state_unlocked(directory, status, report=report, identity=identity, expected_owner=expected_owner)


def consume_run(directory, token, owner):
    """Consume a single-use reserved run token at worker start under .ending.lock."""
    directory = Path(str(directory))
    token = str(token or "")
    owner = str(owner or "")
    runtime_path = directory / "runtime.json"
    state_path = directory / "state.json"
    with _ending_lock(directory):
        runtime = _read_json(runtime_path)
        if not token or runtime.get("run") != token or runtime.get("phase") != "reserved":
            append_line(
                directory / "run.log",
                f"refused run token {token or '<empty>'}: manifest run={runtime.get('run')} phase={runtime.get('phase')}",
            )
            return 0
        runtime["phase"] = "started"
        _write_json_atomic(runtime_path, runtime)
        prior = _read_json(state_path)
        steps = prior.get("steps", 0)
        inbox = prior.get("inbox", 0)
        state = dict(
            status="running",
            steps=steps if isinstance(steps, int) else 0,
            progress="started",
            delivery="none",
            delivery_version=2,
            worker_owner=owner,
            run=token,
            inbox=inbox if isinstance(inbox, int) else 0,
            updated=time.time(),
        )
        _write_json_atomic(state_path, state)
        return 1


def write_worker_state(directory, token, owner, status, steps, progress, inbox, delivery_id="", report=None):
    """Conditional worker state write under .ending.lock; terminal writes set phase='ended'."""
    directory = Path(str(directory))
    token = str(token or "")
    owner = str(owner or "")
    status = str(status)
    runtime_path = directory / "runtime.json"
    state_path = directory / "state.json"
    with _ending_lock(directory):
        runtime = _read_json(runtime_path) if runtime_path.is_file() else {}
        state = _read_json(state_path)
        if runtime_path.is_file() or token:
            if (
                not token
                or runtime.get("run") != token
                or runtime.get("phase") != "started"
                or state.get("status") != "running"
                or str(state.get("worker_owner") or "") != owner
            ):
                return 0
        if report is not None:
            (directory / "report.txt").write_text(str(report), encoding="utf-8")
        updated = time.time()
        next_state = dict(
            status=status,
            steps=int(steps),
            progress=str(progress),
            delivery="none" if status == "running" else "pending",
            delivery_version=2,
            worker_owner=owner,
            run=token,
            inbox=int(inbox),
            updated=updated,
        )
        if status != "running":
            resolved_id = str(delivery_id or "")
            if not resolved_id:
                resolved_id = hashlib.sha256(
                    json.dumps([directory.name, status, "", updated, int(steps)]).encode("utf-8")
                ).hexdigest()[:32]
            next_state["delivery_id"] = resolved_id
        _write_json_atomic(state_path, next_state)
        if status != "running" and runtime_path.is_file() and runtime:
            runtime["phase"] = "ended"
            _write_json_atomic(runtime_path, runtime)
        return 1


def checkpoint_revision(directory):
    """One sha256 over task.json, messages.jsonl, plan.json, guidance.json and executions/*.json."""
    directory = Path(str(directory))
    digest = hashlib.sha256()
    rel_paths = []
    for name in (" guidance.json".strip(), "messages.jsonl", "plan.json", "task.json"):
        candidate = directory / name
        if candidate.is_file():
            rel_paths.append(name)
    exec_dir = directory / "executions"
    if exec_dir.is_dir():
        for path in sorted(exec_dir.glob("*.json")):
            if path.is_file():
                rel_paths.append(f"executions/{path.name}")
    for rel in sorted(rel_paths):
        data = (directory / rel).read_bytes()
        digest.update(rel.encode("utf-8") + b"\0" + str(len(data)).encode("ascii") + b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def check_checkpoint_copy(directory, bundle_root, binary=None, timeout=120):
    """Copy session checkpoint to memory/coding-checks/<id>-<hex> and run coding_check.metta."""
    directory = Path(str(directory))
    bundle_root = Path(str(bundle_root))
    if binary is None:
        binary, _ = _resolve_engine_binary()
    checks_root = directory.parent.parent / "coding-checks"
    checks_root.mkdir(parents=True, exist_ok=True)
    copy_dir = checks_root / f"{directory.name}-{secrets.token_hex(8)}"
    repo_root = Path(__file__).resolve().parents[1]
    try:
        copy_dir.mkdir(parents=True, exist_ok=False)
        for item in sorted(directory.iterdir()):
            if item.name in ("tmp", "run.log", ".ending.lock"):
                continue
            if item.name == "executions" and item.is_dir():
                target_exec = copy_dir / "executions"
                target_exec.mkdir(parents=True, exist_ok=True)
                for sub in sorted(item.iterdir()):
                    if sub.is_file() and sub.suffix == ".json":
                        shutil.copy2(sub, target_exec / sub.name)
                continue
            if item.is_dir():
                shutil.copytree(item, copy_dir / item.name)
            elif item.is_file():
                shutil.copy2(item, copy_dir / item.name)
        check_tmp = copy_dir / "tmp"
        check_tmp.mkdir(parents=True, exist_ok=True)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", str(Path.home())),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "TMPDIR": str(check_tmp),
            "PYTHONHOME": sys.prefix,
            "LD_LIBRARY_PATH": str(Path(sys.prefix) / "lib"),
            "PYTHONPATH": runtime_pythonpath(bundle_root, repo_root),
            "METTACLAW_CODING_CHECK": "1",
        }
        if os.environ.get("CETTA_ROOT"):
            env["CETTA_ROOT"] = os.environ["CETTA_ROOT"]
        if os.environ.get("PETTA_PY_ENV"):
            env["PETTA_PY_ENV"] = os.environ["PETTA_PY_ENV"]
        if os.environ.get("PETTA_ROOT"):
            env["PETTA_ROOT"] = os.environ["PETTA_ROOT"]
        entry = bundle_root / "coding_check.metta"
        if not entry.is_file():
            return False, "coding_check.metta is missing from the target runtime"
        result = subprocess.run(
            [str(binary), "--lang", "petta", "--import-mode", "ancestor-walk", str(entry), "--", str(copy_dir)],
            cwd=bundle_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        combined = "\n".join(part for part in (result.stdout, result.stderr) if part)
        for raw_line in combined.splitlines():
            line = raw_line.strip()
            if "CheckpointReady" in line:
                return True, line
            if "CheckpointFailed" in line:
                if line.startswith("(CheckpointFailed ") and line.endswith(")"):
                    inner = line[len("(CheckpointFailed "):-1].strip()
                    if inner.startswith('"') and inner.endswith('"'):
                        try:
                            inner = json.loads(inner)
                        except ValueError:
                            inner = inner.strip('"')
                    return False, inner
                idx = line.find("CheckpointFailed")
                return False, line[idx + len("CheckpointFailed"):].strip(" ()\"")
        if result.returncode != 0:
            return False, (combined.strip() or f"check exited {result.returncode}")
        return False, (combined.strip() or "check produced no verdict")
    except subprocess.TimeoutExpired:
        return False, "checkpoint check timed out"
    except OSError as exc:
        return False, str(exc)
    finally:
        shutil.rmtree(copy_dir, ignore_errors=True)


def _append_run_record_unlocked(directory, record):
    runs_path = Path(str(directory)) / "runs.jsonl"
    valid_lines = []
    if runs_path.is_file():
        try:
            raw_lines = runs_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            raw_lines = []
        for idx, raw in enumerate(raw_lines):
            text = raw.strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    valid_lines.append(json.dumps(parsed))
            except ValueError:
                if idx == len(raw_lines) - 1:
                    break
    valid_lines.append(json.dumps(record))
    payload = "\n".join(valid_lines) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=".runs-", dir=runs_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o660)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, runs_path)
        _fsync_dir(runs_path.parent)
    finally:
        if os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass


def repin_session(root, directory, *, scope_active=False, check_hook=None, before_rename_hook=None):
    """Re-pin a session to the current runtime after checking a copy of its checkpoint."""
    root, directory = Path(str(root)), Path(str(directory))
    if not (root / "config/runtime-files.json").is_file():
        root = Path(__file__).resolve().parents[1]
    target, digest = freeze_bundle(root, directory.parent.parent / "coding-runtimes")
    binary, engine = _resolve_engine_binary()
    record_path = directory / "runtime.json"

    with _ending_lock(directory):
        old_manifest = _read_json(record_path) if record_path.is_file() else {}
        if (
            old_manifest
            and old_manifest.get("digest") == digest
            and old_manifest.get("binary") == binary
            and old_manifest.get("binary_sha256") == engine
            and old_manifest.get("phase") in ("reserved", "started", "ended")
            and (Path(str(old_manifest.get("root") or "")) / "coding_main.metta").is_file()
        ):
            _settle_ending_unlocked(directory, require_dead_owner=True)
            token = secrets.token_hex(16)
            old_manifest.setdefault("checkpoint", None)
            old_manifest.setdefault("previous", None)
            old_manifest["run"] = token
            old_manifest["phase"] = "reserved"
            old_manifest["at"] = time.time()
            _write_json_atomic(record_path, old_manifest)
            return "same", token
        seen_rev = checkpoint_revision(directory)
        seen_manifest_state = (old_manifest.get("run"), old_manifest.get("phase"))

    ok, detail = check_checkpoint_copy(directory, target, binary=binary)
    if check_hook is not None:
        check_hook(directory)
    if not ok:
        return "failed", detail

    with _ending_lock(directory):
        current_old = _read_json(record_path) if record_path.is_file() else {}
        if (
            scope_active
            or checkpoint_revision(directory) != seen_rev
            or (current_old.get("run"), current_old.get("phase")) != seen_manifest_state
        ):
            return "changed", "the session changed during the check; try again."
        _settle_ending_unlocked(directory, require_dead_owner=True)
        current_old = _read_json(record_path) if record_path.is_file() else {}
        previous = {k: v for k, v in current_old.items() if k != "previous"} if current_old else None
        token = secrets.token_hex(16)
        now = time.time()
        manifest = dict(
            root=str(target),
            digest=digest,
            binary=binary,
            binary_sha256=engine,
            checkpoint=seen_rev,
            previous=previous,
            run=token,
            phase="reserved",
            at=now,
        )
        if before_rename_hook is not None:
            before_rename_hook(directory)
        _write_json_atomic(record_path, manifest)
        _append_run_record_unlocked(
            directory,
            dict(
                at=now,
                **{"from": current_old.get("digest") if current_old else None},
                to=digest,
                run=token,
            ),
        )
        return "repinned", token


def repin_session_json(root, directory):
    status, detail = repin_session(root, directory)
    bundle = runtime_root(directory) if status in ("same", "repinned") else ""
    return json.dumps({"status": status, "detail": detail, "root": bundle})


def session_model(directory, fallback):
    """The model a session keeps, written to its own state file so the
    agent's model file never reaches the session. It is the model in
    task.json; for a session from before models were recorded, the one its
    state file already names, or else the fallback. task.json is never
    rewritten: resuming keeps the session's record as it was."""
    directory = Path(str(directory))
    model = str(json.loads((directory / "task.json").read_text()).get("model") or "")
    state = directory / "model.metta"
    if not model and state.is_file():
        found = re.search(r"\(active-model\s+([^\s()]+)\s*\)", state.read_text())
        model = found.group(1) if found else ""
    model = model or str(fallback)
    text = "(active-model %s)\n" % model
    if not state.is_file() or state.read_text() != text:
        state.write_text(text)
    return model


def runtime_binary(directory):
    return json.loads((Path(str(directory)) / "runtime.json").read_text())["binary"]


def runtime_root(directory):
    return json.loads((Path(str(directory)) / "runtime.json").read_text())["root"]


def runtime_pythonpath(runtime, original):
    # Native source imports resolve inside this snapshot. External dependencies
    # retain their paths; runtime source entries are replaced, not prepended.
    return os.pathsep.join([str(Path(str(runtime)) / p) for p in ("src", "channels", "modes")] +
        [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep)
         if p and p not in [str(Path(str(original)) / n) for n in ("src", "channels", "modes")]])


def append_line(path, text):
    with open(str(path), "a", encoding="utf-8") as stream:
        stream.write(str(text) + "\n")
    return 1


def _reported_input(usage):
    import provider_protocol
    observed = provider_protocol.cache_observation(usage)
    if observed.get("status") == "reported":
        return observed["input_tokens"]
    total = usage.get("prompt_tokens", usage.get("input_tokens")) if isinstance(usage, dict) else None
    return total if type(total) is int and total > 0 else None


def context_observe(directory, request_bytes):
    """Remember what the provider counted for the request just answered."""
    directory = Path(str(directory))
    try:
        usage = json.loads((directory / "reply.json").read_text()).get("usage")
    except (OSError, ValueError, AttributeError):
        return 0
    tokens = _reported_input(usage)
    if tokens is None or int(request_bytes) <= 0:
        return 0
    temporary = directory / "context.tmp"
    temporary.write_text(json.dumps(dict(bytes=int(request_bytes), tokens=tokens)))
    os.replace(temporary, directory / "context.json")
    return 1


def context_tokens(directory, request_bytes):
    """Input tokens for a request of this many bytes: four bytes each, as
    Codex estimates new items, or the provider's own rate for this session's
    last request when it counted more."""
    size = int(request_bytes)
    try:
        record = json.loads((Path(str(directory)) / "context.json").read_text())
        rate = int(record["tokens"]) / int(record["bytes"])
    except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError):
        rate = 0
    return max(size // 4, math.ceil(size * rate))
