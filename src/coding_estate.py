"""Coding-session pin classification, lifecycle records, and turn-context view.

Invariants and unknown-state policy:
- Resume refusals implement `CodingSessionLifecycle.guard`, which is equivalent
  to `SafeOnPin` under the given world; failures outside the model (checkpoint
  decoding, dependency inventory, launch races) are not claimed.
- A legacy pin that recorded no engine hash (`binary_sha256`) is allowed to
  resume by policy and is labelled `unverified` ("engine not recorded"), never
  `resumable`.
- Pins frozen before run-token authority lack `"phase"` in `runtime.json` and
  are classified `needs-current` ("its runtime predates run tokens").
- A live backend selection that cannot be read (`importing`, `unsupported`,
  `unreachable`, or `timeout`) never claims retirement. When such an unknown
  selection meets a bundle that depends on the backend, the view and
  `work-status` show `unverified` ("the LLM ledger's backend selection is
  unavailable (<outcome>)"), while `work-resume` refuses retryably ("the LLM
  ledger's backend selection is unavailable (<outcome>); its pin cannot be
  checked now. Try again later.") because an active legacy backend and an
  unreachable native backend look the same. The display verdict and the resume
  permission are separate.
"""

from collections import Counter
import contextlib
from dataclasses import dataclass
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time


STATUS_ORDER = (
    "done",
    "failed",
    "stopped",
    "interrupted",
    "running",
    "launching",
    "unknown",
    "held",
    "archived",
)
VERDICT_ORDER = ("needs-current", "unverified", "resumable", "unpinned")
VERBS_LINE = (
    'verbs: (work-status "id") (work-resume "id" ["current"]) '
    '(work-hold "id" "why" ["YYYY-MM-DD"]) (work-archive "id" "why")'
)
VIEW_MAX_CHARS = 2000
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VALID_PHASES = ("reserved", "started", "ended")

_engine_digest_cache = {}
_runtime_blocks_cache = {}


@dataclass(frozen=True)
class World:
    """Optional overrides for pure testing and per-listing snapshots.

    A None field means read the live system fact. `selection` is a map from
    retirement name to its outcome, e.g. `{"llm-ledger-sqlite": "native"}`.
    """
    selection: dict[str, object] | None = None
    engines: object = None
    today: str | None = None


@dataclass(frozen=True)
class Retirement:
    name: str
    text: str
    live_fact: object
    bundle_test: object


def _uses_sqlite_llm_ledger(bundle):
    """Runtimes before godelclaw 0417b092e lack src/llm_ledger.metta and call the SQLite ledger directly. Runtimes from 0417b092e on carry it and choose the backend through the selection."""
    return not (Path(bundle) / "src/llm_ledger.metta").is_file()


def _parse_selection_answer(raw):
    import input_ack
    text = raw if isinstance(raw, str) else input_ack._show(raw)
    stripped = text.strip()
    if stripped == "legacy":
        return "legacy"
    if stripped == "native":
        return "native"
    return ("unknown", "unsupported")


def _parse_selection_exception(exc):
    msg = str(exc)
    lower = msg.lower()
    if "llm-ledger-importing" in msg:
        return ("unknown", "importing")
    if isinstance(exc, TimeoutError) or "timed out" in lower or "timeout" in lower:
        return ("unknown", "timeout")
    if "unsupported" in lower:
        return ("unknown", "unsupported")
    return ("unknown", "unreachable")


def llm_ledger_selection(socket=None, timeout=1.0):
    """Ask the agent's ledger server for llm-selection.

    Explicit outcomes from llm_ledger.metta:72-77:
    - "legacy" -> not retired;
    - "native" -> retired;
    - ("unknown", outcome) for importing, unsupported, unreachable, or timeout.
    """
    import input_ack
    target = input_ack._native_socket() if socket is None else str(socket)
    if not target or not os.path.exists(target):
        return ("unknown", "unreachable")
    try:
        answer = input_ack._ledger_call("llm-selection", (), timeout, target)
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return _parse_selection_exception(exc)
    return _parse_selection_answer(answer)


RETIREMENTS = (
    Retirement(
        name="llm-ledger-sqlite",
        text="the retired SQLite LLM ledger",
        live_fact=lambda: llm_ledger_selection(),
        bundle_test=_uses_sqlite_llm_ledger,
    ),
)


def _fsync_dir(directory):
    fd = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextlib.contextmanager
def _lifecycle_lock(session_dir):
    lock_path = Path(session_dir) / ".lifecycle.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o660)
    try:
        with contextlib.suppress(OSError):
            os.fchmod(fd, 0o660)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _write_json_atomic(path, payload):
    path = Path(path)
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            with contextlib.suppress(OSError):
                os.fchmod(stream.fileno(), 0o660)
            json.dump(payload, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(tmp_path), str(path))
        _fsync_dir(parent)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise


def _read_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def read_runs(session_dir):
    """Read valid JSON object lines from runs.jsonl, dropping a torn last line."""
    runs_path = Path(session_dir) / "runs.jsonl"
    if not runs_path.is_file():
        return []
    try:
        raw_lines = runs_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    nonempty = [line.strip() for line in raw_lines if line.strip()]
    entries = []
    for index, line in enumerate(nonempty):
        try:
            item = json.loads(line)
        except ValueError:
            if index == len(nonempty) - 1:
                break
            continue
        if isinstance(item, dict):
            entries.append(item)
    return entries


def _engine_sha256(path):
    if not path:
        return None
    target = Path(path)
    try:
        st = target.stat()
    except OSError:
        return None
    if not target.is_file():
        return None
    key = str(target)
    stamp = (st.st_ino, st.st_size, st.st_mtime_ns)
    cached = _engine_digest_cache.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    hasher = hashlib.sha256()
    try:
        with open(target, "rb") as stream:
            for block in iter(lambda: stream.read(1 << 20), b""):
                hasher.update(block)
    except OSError:
        return None
    digest = hasher.hexdigest()
    _engine_digest_cache[key] = (stamp, digest)
    return digest


def _retirement_outcome(entry, world):
    if isinstance(world, World) and isinstance(world.selection, dict):
        if entry.name in world.selection:
            return world.selection[entry.name]
    return entry.live_fact()


def _resolve_world(world=None, matched_entries=None):
    base = world if isinstance(world, World) else World()
    existing = dict(base.selection) if isinstance(base.selection, dict) else {}
    entries = RETIREMENTS if matched_entries is None else matched_entries
    for entry in entries:
        if entry.name not in existing:
            existing[entry.name] = entry.live_fact()
    return World(selection=existing, engines=base.engines, today=base.today)


def _matched_retirements(session_path):
    runtime_path = Path(session_path) / "runtime.json"
    if not runtime_path.is_file():
        return ()
    runtime = _read_json(runtime_path)
    rt_root = runtime.get("root")
    if not isinstance(rt_root, str) or not rt_root:
        return ()
    bundle = Path(rt_root)
    if not bundle.is_dir():
        return ()
    return tuple(entry for entry in RETIREMENTS if entry.bundle_test(bundle))


def snapshot_session_world(session_dir, world=None):
    """Snapshot the world for one session, calling live selection at most once per matched retirement."""
    base = world if isinstance(world, World) else World()
    matched = _matched_retirements(session_dir)
    if matched:
        return _resolve_world(base, matched)
    return base


def snapshot_world(coding_dir=None, world=None):
    """Snapshot the world across a coding directory, calling live selection at most once per matched retirement."""
    base = world if isinstance(world, World) else World()
    target = Path(coding_dir) if coding_dir is not None else (Path(os.getcwd()) / "memory" / "coding")
    matched_by_name = {}
    if target.is_dir():
        try:
            sessions = sorted(
                (p for p in target.iterdir() if p.is_dir() and not p.name.startswith(".")),
                key=lambda p: p.name,
            )
        except OSError:
            sessions = []
        for session in sessions:
            for entry in _matched_retirements(session):
                matched_by_name[entry.name] = entry
    if matched_by_name:
        return _resolve_world(base, tuple(matched_by_name.values()))
    return base


def _coerce_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("true", "1", "active")


def is_launching(session_dir, *, scope_active=False):
    if _coerce_bool(scope_active):
        return False
    runtime_path = Path(session_dir) / "runtime.json"
    if not runtime_path.is_file():
        return False
    runtime = _read_json(runtime_path)
    return runtime.get("phase") == "reserved"


def launching_date(session_dir):
    session_path = Path(session_dir)
    runtime = _read_json(session_path / "runtime.json")
    at = runtime.get("at")
    if isinstance(at, (int, float)):
        return _local_date(at)
    state = _read_json(session_path / "state.json")
    return _status_date(session_path, state)


def effective_status(session_dir, scope_active=False):
    session_path = Path(session_dir)
    if is_launching(session_path, scope_active=_coerce_bool(scope_active)):
        return "launching since %s" % launching_date(session_path)
    state = _read_json(session_path / "state.json")
    return str(state.get("status") or "")


def classify(session_dir, world=None):
    """Classify a session's pin compatibility as (verdict, reason).

    Checks in exact order:
    1. No runtime.json -> ("unpinned", "a resume pins the current runtime").
    2. Bundle directory (runtime.json "root") missing ->
       ("needs-current", "its frozen runtime is gone").
    3. First retirement whose live fact holds and whose bundle test matches ->
       ("needs-current", "it uses <text>").
    4. Engine file (runtime.json "binary") missing ->
       ("needs-current", "its engine binary is gone").
    5. Engine hash recorded and differs from current ->
       ("needs-current", "its engine binary has changed").
    6. Manifest lacks "phase" (predates run tokens) ->
       ("needs-current", "its runtime predates run tokens").
    7. No binary_sha256 -> ("unverified", "engine not recorded"), unless a
       matched retirement's backend selection is unknown, which reports
       ("unverified", "the LLM ledger's backend selection is unavailable (<outcome>)").
    8. Matched retirement's backend selection is unknown ->
       ("unverified", "the LLM ledger's backend selection is unavailable (<outcome>)").
    9. Otherwise -> ("resumable", "").
    """
    session_path = Path(session_dir)
    runtime_path = session_path / "runtime.json"
    if not runtime_path.is_file():
        return ("unpinned", "a resume pins the current runtime")
    runtime = _read_json(runtime_path)
    root = runtime.get("root")
    bundle = Path(root) if isinstance(root, str) and root else None
    if bundle is None or not bundle.is_dir():
        return ("needs-current", "its frozen runtime is gone")

    unknown_outcome = None
    for entry in RETIREMENTS:
        if entry.bundle_test(bundle):
            outcome = _retirement_outcome(entry, world)
            if outcome == "native":
                return ("needs-current", "it uses %s" % entry.text)
            if isinstance(outcome, tuple) and len(outcome) == 2 and outcome[0] == "unknown":
                if unknown_outcome is None:
                    unknown_outcome = outcome[1]

    binary = runtime.get("binary")
    if isinstance(world, World) and world.engines is not None:
        current_hash = world.engines.get(str(binary)) if binary else None
    else:
        current_hash = _engine_sha256(binary) if isinstance(binary, str) and binary else None

    if current_hash is None:
        return ("needs-current", "its engine binary is gone")

    recorded_hash = runtime.get("binary_sha256")
    if isinstance(recorded_hash, str) and recorded_hash and current_hash != recorded_hash:
        return ("needs-current", "its engine binary has changed")

    if runtime.get("phase") not in _VALID_PHASES:
        return ("needs-current", "its runtime predates run tokens")

    if not isinstance(recorded_hash, str) or not recorded_hash:
        if unknown_outcome is not None:
            return ("unverified", "the LLM ledger's backend selection is unavailable (%s)" % unknown_outcome)
        return ("unverified", "engine not recorded")

    if unknown_outcome is not None:
        return ("unverified", "the LLM ledger's backend selection is unavailable (%s)" % unknown_outcome)

    return ("resumable", "")


def resume_problem(session_dir, world=None):
    """Return '' if the session may resume on its pin, else the refusal text."""
    verdict, reason = classify(session_dir, world=world)
    if verdict == "needs-current":
        return reason
    if verdict == "unverified" and reason.startswith("the LLM ledger's backend selection is unavailable ("):
        return "%s; its pin cannot be checked now. Try again later." % reason
    return ""


def read_lifecycle(session_dir):
    path = Path(session_dir) / "lifecycle.json"
    if not path.is_file():
        return {}
    return _read_json(path)


def _valid_review_date(value):
    if not isinstance(value, str) or not _DATE_RE.match(value):
        return False
    try:
        datetime.date.fromisoformat(value)
        return True
    except ValueError:
        return False


def hold_session(session_dir, why, review="", *, now=None):
    session_path = Path(session_dir)
    session_id = session_path.name
    if not (session_path / "task.json").is_file():
        return "work-hold: no session %s" % session_id
    review_date = None if review is None or review == "" else str(review)
    if review_date is not None and not _valid_review_date(review_date):
        return 'work-hold: invalid review date "%s"; use YYYY-MM-DD' % review_date
    timestamp = time.time() if now is None else float(now)
    payload = {"hold": {"why": str(why), "review": review_date, "at": timestamp}}
    with _lifecycle_lock(session_path):
        _write_json_atomic(session_path / "lifecycle.json", payload)
    if review_date is not None:
        return "WORK_HELD_UNTIL %s %s" % (review_date, session_id)
    return "WORK_HELD %s" % session_id


def archive_session(session_dir, why, *, now=None):
    session_path = Path(session_dir)
    session_id = session_path.name
    if not (session_path / "task.json").is_file():
        return "work-archive: no session %s" % session_id
    timestamp = time.time() if now is None else float(now)
    payload = {"archive": {"why": str(why), "at": timestamp}}
    with _lifecycle_lock(session_path):
        _write_json_atomic(session_path / "lifecycle.json", payload)
    return "WORK_ARCHIVED %s" % session_id


def clear_lifecycle(session_dir):
    session_path = Path(session_dir)
    lifecycle_path = session_path / "lifecycle.json"
    previous_path = session_path / "lifecycle.previous.json"
    with _lifecycle_lock(session_path):
        if not lifecycle_path.is_file():
            return 0
        os.replace(str(lifecycle_path), str(previous_path))
        _fsync_dir(session_path)
    return 1


def _local_date(timestamp):
    return time.strftime("%Y-%m-%d", time.localtime(float(timestamp)))


def _today_str(world=None):
    if isinstance(world, World) and world.today is not None:
        return str(world.today)
    return _local_date(time.time())


def listed(session_dir, world=None):
    """Whether a session appears in the open-session list of context_view()."""
    session_path = Path(session_dir)
    if is_launching(session_path):
        return True
    state = _read_json(session_path / "state.json")
    status = str(state.get("status") or "unknown")
    if status == "running":
        return True
    if status == "done":
        return False
    lifecycle = read_lifecycle(session_path)
    if isinstance(lifecycle.get("archive"), dict):
        return False
    hold = lifecycle.get("hold")
    if isinstance(hold, dict):
        review = hold.get("review")
        if not review:
            return False
        return str(review) <= _today_str(world=world)
    return True


def _label_suffix(lifecycle):
    hold = lifecycle.get("hold")
    if isinstance(hold, dict):
        why = str(hold.get("why", ""))
        review = hold.get("review")
        if review:
            return " · held until %s: %s" % (review, why)
        return " · held: %s" % why
    archive = lifecycle.get("archive")
    if isinstance(archive, dict):
        return " · archived: %s" % str(archive.get("why", ""))
    return ""


def _status_date(session_path, state):
    updated = state.get("updated")
    if isinstance(updated, (int, float)):
        return _local_date(updated)
    for candidate in (session_path / "state.json", session_path / "task.json", session_path):
        try:
            return _local_date(candidate.stat().st_mtime)
        except OSError:
            continue
    return _local_date(time.time())


def _repin_summary_part(session_path, default_date):
    runs = read_runs(session_path)
    if not runs:
        return ""
    last_at = runs[-1].get("at")
    repin_date = _local_date(last_at) if isinstance(last_at, (int, float)) else default_date
    return " · run %d · re-pinned %s" % (len(runs) + 1, repin_date)


def _pin_summary(session_path, verdict, reason, *, unpinned_reason=True):
    runtime_path = session_path / "runtime.json"
    if not runtime_path.is_file():
        return ("unpinned: %s" % reason) if unpinned_reason and reason else "unpinned"
    runtime = _read_json(runtime_path)
    digest = str(runtime.get("digest") or Path(str(runtime.get("root", ""))).name)[:8]
    try:
        pin_date = _local_date(runtime_path.stat().st_mtime)
    except OSError:
        pin_date = _local_date(time.time())
    repin_part = _repin_summary_part(session_path, pin_date)
    verdict_text = "%s: %s" % (verdict, reason) if reason else verdict
    return "pin %s %s%s · %s" % (digest, pin_date, repin_part, verdict_text)


def status_line_suffix(session_dir, world=None):
    """Suffix for a session line in (work-status)."""
    session_path = Path(session_dir)
    verdict, reason = classify(session_path, world=world)
    pin_text = _pin_summary(session_path, verdict, reason, unpinned_reason=False)
    label_text = _label_suffix(read_lifecycle(session_path))
    return " · %s%s" % (pin_text, label_text)


def status_detail(session_dir, world=None):
    """Detail lines for (work-status "id"): bundle digest, engine basename & hashes, lifecycle."""
    session_path = Path(session_dir)
    verdict, reason = classify(session_path, world=world)
    runtime_path = session_path / "runtime.json"
    if runtime_path.is_file():
        runtime = _read_json(runtime_path)
        bundle_digest = str(runtime.get("digest") or Path(str(runtime.get("root", ""))).name or "unknown")
        binary = str(runtime.get("binary") or "")
        basename = Path(binary).name if binary else "unknown"
        recorded = str(runtime.get("binary_sha256") or "unrecorded")
        if isinstance(world, World) and world.engines is not None:
            current = world.engines.get(str(binary)) if binary else None
            current = current or "missing"
        else:
            current = _engine_sha256(binary) or "missing"
        try:
            pin_date = _local_date(runtime_path.stat().st_mtime)
        except OSError:
            pin_date = _local_date(time.time())
        repin_part = _repin_summary_part(session_path, pin_date)
        verdict_text = "%s: %s" % (verdict, reason) if reason else verdict
        pin_line = "pin: bundle %s · engine %s (recorded %s, current %s)%s · %s" % (
            bundle_digest, basename, recorded, current, repin_part, verdict_text)
    else:
        pin_line = "pin: unpinned (%s)" % reason

    lifecycle = read_lifecycle(session_path)
    if isinstance(lifecycle.get("hold"), dict):
        hold = lifecycle["hold"]
        review = hold.get("review")
        prefix = ("hold until %s: %s" % (review, hold.get("why", ""))
                  if review else "hold: %s" % hold.get("why", ""))
        lifecycle_line = "lifecycle: %s (%s)" % (prefix, json.dumps(lifecycle, sort_keys=True))
    elif isinstance(lifecycle.get("archive"), dict):
        archive = lifecycle["archive"]
        lifecycle_line = "lifecycle: archive: %s (%s)" % (
            archive.get("why", ""), json.dumps(lifecycle, sort_keys=True))
    else:
        lifecycle_line = "lifecycle: none"
    return "\n%s\n%s" % (pin_line, lifecycle_line)


def settings_path(root=None):
    base = Path(root) if root is not None else Path(os.getcwd())
    return base / "memory" / "coding-settings.json"


def work_setting(root=None, key="context"):
    data = _read_json(settings_path(root))
    key_str = str(key)
    if key_str == "context":
        value = data.get("context", "open")
        return str(value) if value in ("open", "summary", "off") else "open"
    if key_str == "resume":
        value = data.get("resume", "pinned")
        return str(value) if value in ("pinned", "current") else "pinned"
    return str(data.get(key_str, ""))


def set_work_setting(root=None, key="context", value="open"):
    path = settings_path(root)
    data = _read_json(path)
    data[str(key)] = str(value)
    _write_json_atomic(path, data)
    return str(path)


def _dir_blocks(path):
    target = str(path)
    try:
        st = os.lstat(target)
    except OSError:
        return 0
    stamp = (st.st_ino, st.st_mtime_ns)
    cached = _runtime_blocks_cache.get(target)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    total = st.st_blocks
    stack = [target]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        est = entry.stat(follow_symlinks=False)
                        total += est.st_blocks
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                    except OSError:
                        pass
        except OSError:
            pass
    _runtime_blocks_cache[target] = (stamp, total)
    return total


def _runtimes_summary(runtimes_dir, open_runtime_ids):
    if not runtimes_dir.is_dir():
        return 0, 0, 0
    try:
        dirs = sorted(
            (p for p in runtimes_dir.iterdir() if p.is_dir() and not p.name.startswith(".")),
            key=lambda p: p.name,
        )
    except OSError:
        return 0, 0, 0
    total_blocks = sum(_dir_blocks(d) for d in dirs)
    mb = round(total_blocks * 512 / (1024 * 1024))
    unused = sum(1 for d in dirs if d.name not in open_runtime_ids)
    return len(dirs), mb, unused


def context_view(root=None, *, world=None):
    """Render the coding-sessions context source, or None when absent/off."""
    base = Path(root) if root is not None else Path(os.getcwd())
    mode = work_setting(base, "context")
    if mode == "off":
        return None
    coding_dir = base / "memory" / "coding"
    if not coding_dir.is_dir():
        return None
    try:
        sessions = sorted(
            (p for p in coding_dir.iterdir() if p.is_dir() and not p.name.startswith(".")),
            key=lambda p: p.name,
        )
    except OSError:
        return None

    status_counts = Counter()
    verdict_counts = Counter()
    open_runtime_ids = set()
    session_lines = []
    # resolved_world makes at most one live selection call per matched retirement
    # across the view, and only when some session's frozen bundle matches.
    base_world = world if isinstance(world, World) else World()
    resolved_selection = dict(base_world.selection) if isinstance(base_world.selection, dict) else {}

    for session in sessions:
        state = _read_json(session / "state.json")
        launching = is_launching(session)
        status = "launching" if launching else str(state.get("status") or "unknown")
        lifecycle = read_lifecycle(session)
        is_archived = isinstance(lifecycle.get("archive"), dict)
        is_held = isinstance(lifecycle.get("hold"), dict)

        if is_archived and not launching:
            status_counts["archived"] += 1
        elif is_held and not launching:
            status_counts["held"] += 1
        else:
            status_counts[status] += 1

        is_open = launching or (status != "done" and not is_archived)
        runtime_path = session / "runtime.json"
        if runtime_path.is_file():
            runtime = _read_json(runtime_path)
            if is_open:
                digest = runtime.get("digest")
                if isinstance(digest, str) and digest:
                    open_runtime_ids.add(digest)
                rt_root = runtime.get("root")
                if isinstance(rt_root, str) and rt_root:
                    open_runtime_ids.add(Path(rt_root).name)
            for entry in _matched_retirements(session):
                if entry.name not in resolved_selection:
                    resolved_selection[entry.name] = entry.live_fact()

        session_world = World(
            selection=resolved_selection if resolved_selection else base_world.selection,
            engines=base_world.engines,
            today=base_world.today,
        )
        verdict, reason = classify(session, world=session_world)
        verdict_counts[verdict] += 1

        if mode == "open" and listed(session, world=session_world):
            pin_part = _pin_summary(session, verdict, reason, unpinned_reason=True)
            label_part = _label_suffix(lifecycle)
            if launching:
                l_date = launching_date(session)
                session_lines.append("%s launching since %s · %s%s" % (
                    session.name, l_date, pin_part, label_part))
            else:
                s_date = _status_date(session, state)
                session_lines.append("%s %s %s · %s%s" % (
                    session.name, status, s_date, pin_part, label_part))

    rt_count, rt_mb, rt_unused = _runtimes_summary(base / "memory" / "coding-runtimes", open_runtime_ids)
    parts = ["%d %s" % (status_counts[name], name) for name in STATUS_ORDER if status_counts[name] > 0]
    parts.extend("%s %d" % (verdict, verdict_counts[verdict])
                 for verdict in VERDICT_ORDER if verdict_counts[verdict] > 0)
    parts.append("runtimes %d (%d MB; %d used by no open session)" % (rt_count, rt_mb, rt_unused))
    summary_line = "CODING_SESSIONS %d: %s" % (len(sessions), " · ".join(parts))

    if mode == "summary" or not session_lines:
        return "%s\n%s" % (summary_line, VERBS_LINE)

    full = "\n".join([summary_line, *session_lines, VERBS_LINE])
    if len(full) <= VIEW_MAX_CHARS:
        return full

    kept = len(session_lines) - 1
    while kept >= 0:
        omitted = len(session_lines) - kept
        more_line = "… %d more: (work-status)" % omitted
        candidate = "\n".join([summary_line, *session_lines[:kept], more_line, VERBS_LINE])
        if len(candidate) <= VIEW_MAX_CHARS:
            return candidate
        kept -= 1

    return "%s\n%s" % (summary_line, VERBS_LINE)
