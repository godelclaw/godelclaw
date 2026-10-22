"""Append-only session files for coding mode: one line per record, no backups."""
import json
import hashlib
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import time


def execution_path(directory, identity, suffix):
    if not re.fullmatch(r"[a-f0-9]{32}", str(identity)):
        raise ValueError("invalid execution identity")
    return Path(str(directory)) / "executions" / (str(identity) + suffix)


def execution_create(directory, identity, command):
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


def freeze_runtime(root, directory):
    """Pin imports and instructions for a session, including across resumes."""
    root, directory = Path(str(root)), Path(str(directory))
    if not (root/"config/runtime-files.json").is_file():
        root = Path(__file__).resolve().parents[1]
    record = directory / "runtime.json"
    if record.exists():
        value = json.loads(record.read_text())
        if not (Path(value["root"])/"coding_main.metta").is_file():
            raise RuntimeError("session runtime is missing; restore it before resuming")
        return value["root"]
    selection = root / "config/runtime-files.json"
    names = json.loads(selection.read_text())
    if isinstance(names, dict): names = names["files"]
    files = {}
    for name in names:
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("runtime selection must be relative")
        files[name] = (root/path).read_bytes()
    digest = hashlib.sha256(json.dumps({name:hashlib.sha256(data).hexdigest()
        for name,data in files.items()},sort_keys=True).encode()).hexdigest()
    cache = directory.parent.parent / "coding-runtimes"
    cache.mkdir(parents=True,exist_ok=True)
    target = cache/digest
    if not target.exists():
        temporary = Path(tempfile.mkdtemp(prefix=".stage-",dir=cache))
        try:
            for name,data in files.items():
                path=temporary/name;path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(data);shutil.copymode(root/name,path)
            try: os.rename(temporary,target)
            except FileExistsError: pass
        finally:
            if temporary.exists():shutil.rmtree(temporary)
    record.write_text(json.dumps(dict(root=str(target),digest=digest,
        binary=os.environ.get("CETTA_BIN","cetta"))))
    return str(target)


def runtime_binary(directory):
    return json.loads((Path(str(directory))/"runtime.json").read_text())["binary"]


def runtime_pythonpath(runtime, original):
    # Native source imports resolve inside this snapshot. External dependencies
    # retain their paths; runtime source entries are replaced, not prepended.
    return os.pathsep.join([str(Path(str(runtime))/p) for p in ("src","channels","modes")] +
        [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep)
         if p and p not in [str(Path(str(original))/n) for n in ("src","channels","modes")]])


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
