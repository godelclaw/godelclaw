"""Bounded, idempotently keyed receipt notices from the control responder.

A notice does not consume input, complete work, or make a provider request.
The service owns the input and the actual delivery receipts.
"""

import atexit
import hashlib
import json
import math
import os
import threading
import time
import uuid
import sqlite3
from contextlib import closing
import agent_checkpoint


TEXT = "Received. Your message is queued; /activity shows whether I'm waiting, retrying, or resting."
_pending = {}
_last_notice = {}
_cache_limit = 256
_scan_after = ""
_scan_seen = set()
_last_pump = 0.0
_input_view = None
_task_cache = {}
_active_tasks = set()
_changes = None


def parsed_task(task, body):
    task, body = str(task), str(body)
    entry = _task_cache.get(task)
    if entry is None or entry[0] != body:
        if len(_task_cache) >= _cache_limit + 1:
            _task_cache.pop(next(iter(_task_cache)))
        entry = (body, json.loads(body))
        _task_cache[task] = entry
    return entry[1]


def task_active(task):
    return int(str(task) in _active_tasks)


def telegram_service_state_dir():
    """The durable Telegram service's state directory (cetta-telegram-<instance>, or
    METTACLAW_TELEGRAM_SERVICE_STATE_DIR): what the change watch and the service share.
    It is NOT the agent's own state directory, where the native input ledger lives."""
    from pathlib import Path
    state = os.environ.get("METTACLAW_TELEGRAM_SERVICE_STATE_DIR")
    if not state:
        state = str(Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) /
                    ("cetta-telegram-" + os.environ.get("METTACLAW_INSTANCE", "agent")))
    return Path(state)


def ledger_paths(state):
    """(store directory, socket) of the native input ledger: <state>/input-ledger/store
    and <state>/input-ledger/ledger.sock, where `state` is the AGENT's own state
    directory, the one that holds its engine selection, observed-strategy.sqlite3 and
    modes/ (agent_checkpoint.state_dir(): the parent of METTACLAW_ENGINE_STATE_PATH,
    <XDG_STATE_HOME or ~/.local/state>/<instance> by run.sh). The caller passes it; there
    is no default. The unit pettaclaw-input-ledger@.service serves these paths;
    METTACLAW_INPUT_LEDGER_SOCKET carries the socket to the processes that use it."""
    from pathlib import Path
    root = Path(state) / "input-ledger"
    return root / "store", root / "ledger.sock"


def arm_changes():
    global _changes
    if _changes is None:
        import durable_telegram
        import mode_channel
        state = str(telegram_service_state_dir())
        outbox = mode_channel.state() / "channel-outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        _changes = durable_telegram.Changes([state, outbox], ignore_names=("pump.lock", "sequence"))
    return _changes.arm()


def wait_changes():
    # New tasks wake immediately; the deadline also reconciles delivery
    # receipts and delayed notices, even if a notification was unavailable.
    return _changes.wait(2) if _changes is not None else 0


def _store(timeout=10):
    if not (os.environ.get("METTACLAW_STRATEGY_PATH") or
            os.environ.get("METTACLAW_ENGINE_STATE_PATH")):
        return None
    # Without the managed ledger, cognition never creates one: no notice was
    # registered and no input needs a processed mark.
    if (os.environ.get("METTACLAW_OBSERVED_STRATEGY", "0") != "1"
            and not agent_checkpoint.path().exists()):
        return None
    # Schema/legacy migrations belong to cognition's boot. Notices must never
    # wait for its writer, nor repeatedly scan retained request history.
    store = agent_checkpoint.ledger(initialize=False, timeout=timeout)
    with store.transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS input_notices (
            task TEXT PRIMARY KEY, update_id TEXT NOT NULL, lane TEXT NOT NULL,
            send_key TEXT UNIQUE NOT NULL, created REAL NOT NULL,
            message_id INTEGER, processed INTEGER NOT NULL DEFAULT 0,
            delete_key TEXT, state TEXT NOT NULL DEFAULT 'registered', checked REAL NOT NULL DEFAULT 0)""")
        if "checked" not in {row[1] for row in db.execute("PRAGMA table_info(input_notices)")}:
            db.execute("ALTER TABLE input_notices ADD COLUMN checked REAL NOT NULL DEFAULT 0")
        db.execute("CREATE TABLE IF NOT EXISTS processed_inputs (id TEXT PRIMARY KEY, processed REAL NOT NULL)")
    return store


# ---- the native input ledger (METTACLAW_INPUT_LEDGER_SOCKET) ----
#
# With the variable set, the tables of this module live in the native db server
# on that socket, which runs src/input_ledger.metta next to its store; each verb is
# one LedgerCall made by src/ledger_client.metta in a persistent cetta-value-worker
# (the CSP1 client of execution_gateway, the same one the gateway queue uses). SQLite is not consulted then, and when the server
# cannot be reached these calls fail: there is no silent fallback to SQLite,
# which would split the ledger into two stores. Unset (or empty), nothing here runs.
LEDGER_SOCKET = "METTACLAW_INPUT_LEDGER_SOCKET"
# The value allowance of the ledger's server and of its client worker (the unit passes the same
# --value-bytes): a command output, a request or a working set is one value, and the largest
# command output on record is about 11 MB. The engine's default allowance is 1 MiB.
LEDGER_VALUE_BYTES = 256 * 1024 * 1024
WORKING_SET_NOTE = 1000000  # a failure of a value at least this large is explained
SOCKET_BYTES = 107  # sockaddr_un.sun_path holds 108 bytes on Linux, the last one a NUL
_ledger = None
_ledger_lock = threading.RLock()


class LedgerUnavailable(sqlite3.OperationalError):
    """The native ledger cannot answer: nobody listens, the answer was lost for
    good, or the store refused. What an unopenable SQLite file raises, and handled
    the same by callers (channels/telegram.py handover_ack re-raises it)."""


class LedgerBusy(LedgerUnavailable):
    """The write kept losing races to other writers; nothing was applied. Looks
    like a locked SQLite database to callers, who return 0 and retry later."""

    def __init__(self, message):
        super().__init__("database is locked: " + message)
        self.sqlite_errorcode = sqlite3.SQLITE_BUSY


def _native_socket():
    return os.environ.get(LEDGER_SOCKET, "")


def _managed():
    """Whether _store() would open a ledger at all: the same two conditions, without opening one."""
    if not (os.environ.get("METTACLAW_STRATEGY_PATH") or
            os.environ.get("METTACLAW_ENGINE_STATE_PATH")):
        return False
    return not (os.environ.get("METTACLAW_OBSERVED_STRATEGY", "0") != "1"
                and not agent_checkpoint.path().exists())


def _worker_binary():
    explicit = os.environ.get("CETTA_VALUE_WORKER_BIN")
    if explicit:
        return explicit
    engine = os.environ.get("CETTA_BIN")
    # The value worker sits next to cetta in the bundle (build_cetta.py writes both into runtime.env).
    from pathlib import Path
    return str(Path(engine).with_name("cetta-value-worker")) if engine else "cetta-value-worker"


def _ledger_worker():
    global _ledger
    from execution_gateway.cetta_worker import CettaValueWorker
    from pathlib import Path
    module = Path(__file__).resolve().with_name("ledger_client.metta")
    binary = _worker_binary()
    with _ledger_lock:
        if _ledger is not None and (_ledger[0] != os.getpid() or _ledger[1] != (binary, module)):
            if _ledger[0] == os.getpid():
                _ledger[2].close()
            else:  # a forked child gives up its copies of its parent's pipes and leaves that worker alone
                for stream in (getattr(_ledger[2].process, "stdin", None), getattr(_ledger[2].process, "stdout", None)):
                    if stream is not None:
                        stream.close()
            _ledger = None
        if _ledger is None or _ledger[2].closed:
            _ledger = (os.getpid(), (binary, module),
                       CettaValueWorker(binary, module, "ledger-client:run", timeout=10, language="petta",
                                        maximum_bytes=LEDGER_VALUE_BYTES))
        return _ledger[2]


def close_ledger():
    """Stop this process's ledger worker (it restarts on the next call)."""
    global _ledger
    with _ledger_lock:
        current, _ledger = _ledger, None
    if current is not None and current[0] == os.getpid():
        current[2].close()


atexit.register(close_ledger)


def _show(value):
    from execution_gateway.cetta_worker import FloatBits, Symbol, scalar_float
    if isinstance(value, Symbol):
        return str(value.name)
    if isinstance(value, FloatBits):
        return repr(scalar_float(value))
    if isinstance(value, tuple):
        return "(" + " ".join(_show(item) for item in value) + ")"
    return repr(value)


def _ledger_call(verb, arguments, timeout=10, socket=None, page=None):
    """One verb of the ledger module on the native store; the verb's own value.

    The request names the socket and carries its arguments, $now among them,
    so a repeat of it is the same write with the same request ids. The module
    reconnects and repeats a request itself after an unavailable or uncertain
    outcome (input-ledger:session); the transport below is repeated once, on
    a restarted worker, with the same request. Raises LedgerBusy for a write
    that lost every race and LedgerUnavailable for any other failure. `socket`
    names another server than the configured one (the migration script). `page`, (start, count), asks
    for one page of a read (_ledger_read)."""
    from execution_gateway.cetta_worker import E, S, Symbol, WorkerEvaluationError, WorkerUnavailable
    if page is None and verb in PAGED_READS:
        return _ledger_read(verb, arguments, timeout, socket)
    socket_path = _native_socket() if socket is None else str(socket)
    if len(os.fsencode(socket_path)) > SOCKET_BYTES:
        raise LedgerUnavailable("input ledger socket path is %d bytes; a unix socket path may hold %d: %s"
                                % (len(os.fsencode(socket_path)), SOCKET_BYTES, socket_path))
    request = (E("ledger", socket_path, verb, tuple(arguments)) if page is None else
               E("ledger-page", socket_path, verb, tuple(arguments), int(page[0]), int(page[1])))
    problem = None
    for _ in range(2):
        try:
            worker = _ledger_worker()
            with worker.lock:
                worker.timeout = float(timeout) if timeout and timeout > 0 else 5.0
                reply = worker.call(request)
            break
        except WorkerEvaluationError as error:
            raise LedgerUnavailable("input ledger %s: %s" % (verb, _show(error.detail))) from error
        except (WorkerUnavailable, OSError) as error:
            problem = error
    else:
        raise LedgerUnavailable("input ledger %s: worker unavailable: %s" % (verb, problem)) from problem
    if not (isinstance(reply, tuple) and len(reply) == 2 and reply[0] == S("answer")):
        raise LedgerUnavailable("input ledger %s: unexpected reply %s" % (verb, _show(reply)))
    value = reply[1]
    if isinstance(value, tuple) and value[:1] == (S("Error"),):
        if value == (S("Error"), S("db:transaction"), (S("StoreRefused"), S("conflict"))):
            raise LedgerBusy("input ledger %s lost every race: %s" % (verb, _show(value)))
        raise LedgerUnavailable("input ledger %s: %s" % (verb, _show(value)))
    return {S("true"): True, S("false"): False}.get(value, value) if isinstance(value, Symbol) else value


PAGE_ROWS = 2000
# The reads input-ledger:pageable names: their answers grow with the ledger, and the worker passes each answer on
# as one value, so they are always read in pages.
PAGED_READS = frozenset({"request-rows", "actions", "attempts", "task-view", "task-rows", "conversation-rows",
                         "conversation-history", "turn-dump", "meta-prefix"})


def _ledger_read(verb, arguments, timeout=10, socket=None):
    """A read's answer however long it is, in pages that each fit one wire value (input-ledger:page):
    a list answer's rows joined in order, a record's expression fields joined so. A page too large for
    the wire is asked again with half its rows."""
    from execution_gateway.cetta_worker import S
    merged, start, count = None, 0, PAGE_ROWS
    while True:
        try:
            page = _ledger_call(verb, arguments, timeout, socket, page=(start, count))
        except LedgerUnavailable as error:
            if "reply-limit" in str(error) and count > 1:
                count //= 2
                continue
            raise
        if not (isinstance(page, tuple) and len(page) == 3 and page[0] == S("page")):
            raise LedgerUnavailable("input ledger %s: unexpected page %s" % (verb, _show(page)))
        merged = page[2] if merged is None else _joined(merged, page[2])
        if page[1] != S("more"):
            return merged
        start += count


def _joined(merged, value):
    if merged == () or isinstance(merged[0], tuple):
        return merged + value
    return merged[:1] + tuple(field + more if isinstance(field, tuple) else field
                              for field, more in zip(merged[1:], value[1:]))


def _ledger_flag(verb, arguments, timeout=10):
    """A verb that answers True (the write committed) or fails."""
    answer = _ledger_call(verb, arguments, timeout)
    if answer is not True:
        raise LedgerUnavailable("input ledger %s: unexpected answer %r" % (verb, answer))
    return True


def turn_checkpoint(working_text, ids, request, now, request_id=None):
    """The native store's half of observed_strategy.Ledger.checkpoint: the working set
    (its JSON text), the processed input ids, the request's processed status and
    the notices' processed flags, as ONE transaction. Raises when it did not
    commit (LedgerBusy: it lost every race; LedgerUnavailable: anything else,
    and then it may or may not have committed: see below).

    `request_id` names this checkpoint. It is made once here, before the first try, and
    every repeat of the request (the module's sessions, the transport's second try)
    carries it, so a repeat replays the commit instead of applying it twice. A new call
    makes a new one: two checkpoints with equal arguments are two checkpoints, and the
    second must apply even when another one came between them (A, B, A)."""
    request_id = request_id or "ck1." + uuid.uuid4().hex
    try:
        return _ledger_flag("checkpoint", (str(working_text), tuple(str(i) for i in ids if str(i)),
                                           str(request or ""), float(now), str(request_id)))
    except LedgerUnavailable as error:
        size = len(str(working_text).encode("utf-8", "surrogatepass"))
        if type(error) is LedgerUnavailable and size >= WORKING_SET_NOTE:
            # A value beyond the server's allowance is refused (an Error, or no answer at all): say so.
            raise LedgerUnavailable("%s; the working set is %d bytes, beyond what the native store's server "
                                    "accepts (nothing was written)" % (error, size)) from error
        raise


def working_set():
    """The JSON text of the working set the last checkpoint stored, or None when none
    was stored yet. An unreachable store raises: it is never an empty working set."""
    from execution_gateway.cetta_worker import S
    value = _ledger_call("working-set", ())
    if value == S("none"):
        return None
    if not isinstance(value, str):
        raise LedgerUnavailable("input ledger working-set: unexpected answer %s" % _show(value))
    return value


# ---- the requests in the native store (observed_strategy.Ledger on METTACLAW_INPUT_LEDGER_SOCKET) ----
#
# Thin transport: each function is one verb of src/input_ledger.metta (see "the requests"
# there for the records) and returns Python values; the rules (what a disposition
# refuses, what settles a request) live in the verbs, and observed_strategy.Ledger maps
# the values to what its callers expect. A write makes its request id ONCE here, before
# the first try: every repeat of the call (the module's sessions, the transport's second
# try) carries it and replays the first answer; a new call is a new transition.

def _rid():
    return "rq1." + uuid.uuid4().hex


def _name(value, what):
    from execution_gateway.cetta_worker import Symbol
    if isinstance(value, Symbol):
        return str(value.name)
    raise LedgerUnavailable("input ledger: unexpected %s %s" % (what, _show(value)))


def _checked_text(value, what):
    if not isinstance(value, str):
        raise LedgerUnavailable("input ledger: unexpected %s %s" % (what, _show(value)))
    return value


def _flag_of(value):
    if type(value) is not int or value not in (0, 1):
        raise LedgerUnavailable("input ledger: unexpected flag %s" % _show(value))
    return value


def _request_write(verb, arguments, size_of=None):
    """A write of the requests; explains the refusal of a value beyond the server's allowance."""
    try:
        return _ledger_call(verb, arguments)
    except LedgerUnavailable as error:
        size = len(str(size_of).encode("utf-8", "surrogatepass")) if size_of is not None else 0
        if type(error) is LedgerUnavailable and size >= WORKING_SET_NOTE:
            raise LedgerUnavailable("%s; the content is %d bytes, beyond what the native store's server "
                                    "accepts (nothing was written)" % (error, size)) from error
        raise


def request_accept(key, content, checker, kind, summary, digest, request_id=None):
    """"created", "exists" (same content and checker) or "conflict" (the id names another intent)."""
    answer = _request_write("request-accept", (str(key), str(content), str(checker), str(kind), str(summary),
                                               str(digest), str(request_id or _rid())), size_of=content)
    name = _name(answer, "answer")
    if name not in ("created", "exists", "conflict"):
        raise LedgerUnavailable("input ledger request-accept: unexpected answer %s" % name)
    return name


def request_settle(key, now, request_id=None):
    """Ledger._processed: True when the request was settled by this call."""
    answer = _ledger_call("request-settle", (str(key), float(now), str(request_id or _rid())))
    if type(answer) is not bool:
        raise LedgerUnavailable("input ledger request-settle: unexpected answer %s" % _show(answer))
    return answer


def request_disposition(key, disposition, note, request_id=None):
    """None when recorded, else the refusal (a text)."""
    from execution_gateway.cetta_worker import S
    answer = _ledger_call("request-disposition", (str(key), str(disposition), str(note), str(request_id or _rid())))
    if answer == S("ok"):
        return None
    if isinstance(answer, tuple) and len(answer) == 2 and answer[0] == S("refused") and isinstance(answer[1], str) and answer[1]:
        return answer[1]
    raise LedgerUnavailable("input ledger request-disposition: unexpected answer %s" % _show(answer))


def _request_move(verb, key, request_id):
    answer = _ledger_call(verb, (str(key), str(request_id or _rid())))
    if type(answer) is not bool:
        raise LedgerUnavailable("input ledger %s: unexpected answer %s" % (verb, _show(answer)))
    return answer


def request_cancel(key, request_id=None):
    """active -> cancelled: True when it moved."""
    return _request_move("request-cancel", key, request_id)


def request_finish(key, request_id=None):
    """active -> achieved: True when it moved."""
    return _request_move("request-finish", key, request_id)


def request_answered(key, request_id=None):
    """active or processed -> addressed: True when it moved."""
    return _request_move("request-answered", key, request_id)


def request_retire(prefix, note, request_id=None):
    """The ids (oldest first) of the open requests under the prefix that were cancelled."""
    from execution_gateway.cetta_worker import S
    answer = _ledger_call("request-retire", (str(prefix), str(note), str(request_id or _rid())))
    if not (isinstance(answer, tuple) and len(answer) == 2 and answer[0] == S("retired")
            and isinstance(answer[1], tuple) and all(isinstance(item, str) for item in answer[1])):
        raise LedgerUnavailable("input ledger request-retire: unexpected answer %s" % _show(answer))
    return list(answer[1])


def request_get(key, text=False):
    """None for an unknown request, else {id, kind, checker, status, processed, note, content} (content only with text=True)."""
    from execution_gateway.cetta_worker import S
    answer = _ledger_call("request-get", (str(key), S("text" if text else "head")))
    if answer == S("none"):
        return None
    if not (isinstance(answer, tuple) and len(answer) == 8 and answer[0] == S("got")):
        raise LedgerUnavailable("input ledger request-get: unexpected answer %s" % _show(answer))
    _, identity, kind, checker, status, processed, note, content = answer
    return dict(id=_checked_text(identity, "id"), kind=_checked_text(kind, "kind"), checker=_checked_text(checker, "checker"),
                status=_checked_text(status, "status"), processed=_flag_of(processed), note=_checked_text(note, "note"),
                content=_checked_text(content, "content"))


def request_record(key, offset=0):
    """None for an unknown request, else {id, kind, checker, status, processed, note, slice, length, digest}."""
    from execution_gateway.cetta_worker import S
    answer = _ledger_call("request-record", (str(key), int(offset)))
    if answer == S("none"):
        return None
    if not (isinstance(answer, tuple) and len(answer) == 10 and answer[0] == S("record")):
        raise LedgerUnavailable("input ledger request-record: unexpected answer %s" % _show(answer))
    _, identity, kind, checker, status, processed, note, piece, length, digest = answer
    if type(length) is not int:
        raise LedgerUnavailable("input ledger request-record: unexpected length %s" % _show(length))
    return dict(id=_checked_text(identity, "id"), kind=_checked_text(kind, "kind"), checker=_checked_text(checker, "checker"),
                status=_checked_text(status, "status"), processed=_flag_of(processed), note=_checked_text(note, "note"),
                slice=_checked_text(piece, "content"), length=length, digest=_checked_text(digest, "digest"))


def request_page(offset, limit):
    """(total, rows) with rows as dicts id, status, checker, kind, processed, summary, note, newest first."""
    from execution_gateway.cetta_worker import S
    answer = _ledger_call("request-page", (int(offset), int(limit)))
    if not (isinstance(answer, tuple) and len(answer) == 3 and answer[0] == S("page") and type(answer[1]) is int
            and isinstance(answer[2], tuple)):
        raise LedgerUnavailable("input ledger request-page: unexpected answer %s" % _show(answer))
    rows = []
    for row in answer[2]:
        if not (isinstance(row, tuple) and len(row) == 7):
            raise LedgerUnavailable("input ledger request-page: unexpected row %s" % _show(row))
        identity, status, checker, kind, processed, summary, note = row
        rows.append(dict(id=_checked_text(identity, "id"), status=_checked_text(status, "status"),
                         checker=_checked_text(checker, "checker"), kind=_checked_text(kind, "kind"),
                         processed=_flag_of(processed), summary=_checked_text(summary, "summary"),
                         note=_checked_text(note, "note")))
    return answer[1], rows


def request_protected(focus):
    """What Ledger.protected reads of the requests, from one snapshot: {counts, total, unfinished, focus}.
    unfinished: dicts id, summary, content_digest, checker (at most 8); focus: None or (first 4000 characters, length)."""
    from execution_gateway.cetta_worker import S
    answer = _ledger_call("request-protected", (str(focus) if focus is not None else "",))
    if not (isinstance(answer, tuple) and len(answer) == 5 and answer[0] == S("protected")
            and isinstance(answer[1], tuple) and type(answer[2]) is int and isinstance(answer[3], tuple)):
        raise LedgerUnavailable("input ledger request-protected: unexpected answer %s" % _show(answer))
    counts = {}
    for item in answer[1]:
        if not (isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str) and type(item[1]) is int):
            raise LedgerUnavailable("input ledger request-protected: unexpected count %s" % _show(item))
        counts[item[0]] = item[1]
    if sum(counts.values()) != answer[2]:
        raise LedgerUnavailable("input ledger request-protected: the counts %r do not add up to the %d requests "
                                "(a status this code does not know)" % (counts, answer[2]))
    unfinished = []
    for item in answer[3]:
        if not (isinstance(item, tuple) and len(item) == 4 and all(isinstance(part, str) for part in item)):
            raise LedgerUnavailable("input ledger request-protected: unexpected request %s" % _show(item))
        unfinished.append(dict(id=item[0], summary=item[1], content_digest=item[2], checker=item[3]))
    found = answer[4]
    if found == S("none"):
        piece = None
    elif (isinstance(found, tuple) and len(found) == 3 and found[0] == S("slice") and isinstance(found[1], str)
          and type(found[2]) is int):
        piece = (found[1], found[2])
    else:
        raise LedgerUnavailable("input ledger request-protected: unexpected focus %s" % _show(found))
    return dict(counts=counts, total=answer[2], unfinished=unfinished, focus=piece)


def request_rows(which="all"):
    """Requests whole, oldest first: dicts id, content, status, checker, kind, processed, note.
    `which`: "all", "active" or a list of ids."""
    from execution_gateway.cetta_worker import S
    selector = S(which) if which in ("all", "active") else tuple(str(item) for item in dict.fromkeys(which))
    answer = _ledger_call("request-rows", (selector,), WHOLE_READ_SECONDS if which == "all" else 10)
    if not isinstance(answer, tuple):
        raise LedgerUnavailable("input ledger request-rows: unexpected answer %s" % _show(answer))
    return [_request_row(row) for row in answer]


def _request_row(row):
    from execution_gateway.cetta_worker import S
    if not (isinstance(row, tuple) and len(row) == 8 and row[0] == S("row")):
        raise LedgerUnavailable("input ledger request-rows: unexpected row %s" % _show(row))
    _, identity, content, status, checker, kind, processed, note = row
    return dict(id=_checked_text(identity, "id"), content=_checked_text(content, "content"),
                status=_checked_text(status, "status"), checker=_checked_text(checker, "checker"),
                kind=_checked_text(kind, "kind"), processed=_flag_of(processed), note=_checked_text(note, "note"))


# ---- the turns and their actions: observed_strategy.Ledger's turn and effect methods ----
# The same records hold the meta keys, the captured turns, the actions with their receipts and
# strategy_host's command attempts (see "the turns and their actions" in input_ledger.metta), so
# that a finish checks the action and moves the request in one native transaction.

def _symbol_none(value):
    from execution_gateway.cetta_worker import S
    return S("none") if value is None else value


def _is_none(value):
    from execution_gateway.cetta_worker import S
    return value == S("none")


def _entries(answer, verb):
    if not (isinstance(answer, tuple) and all(isinstance(item, tuple) for item in answer)):
        raise LedgerUnavailable("input ledger %s: unexpected answer %s" % (verb, _show(answer)))
    return answer


def ledger_state():
    """The stored meta keys, {key: value}: pending None or a serial, router_revision its JSON text. A key
    never written is absent; observed_strategy supplies its initial value."""
    state = {}
    for entry in _entries(_ledger_call("state", ()), "state"):
        if len(entry) != 2 or not isinstance(entry[0], str):
            raise LedgerUnavailable("input ledger state: unexpected entry %s" % _show(entry))
        state[entry[0]] = None if _is_none(entry[1]) else entry[1]
    return state


def ledger_control(mode, paused, router_text, request_id=None):
    """True when the mode, the pause or the router revision changed (the revision advanced)."""
    answer = _ledger_call("control", (_symbol_none(mode), _symbol_none(paused), _symbol_none(router_text),
                                      str(request_id or _rid())))
    if type(answer) is not bool:
        raise LedgerUnavailable("input ledger control: unexpected answer %s" % _show(answer))
    return answer


def ledger_restore(request_id=None):
    if _ledger_call("restore", (str(request_id or _rid()),)) is not True:
        raise LedgerUnavailable("input ledger restore: unexpected answer")
    return True


def turn_capture(focus, boundary_digest, router_text, guard, owner, request_id=None):
    """None while paused, else (serial, revision, mode, owner) of the captured turn. A store without an
    owner takes `owner` in this capture; the tickets it issues carry its owner."""
    from execution_gateway.cetta_worker import S
    answer = _ledger_call("capture", (str(focus), str(boundary_digest), str(router_text), str(guard), str(owner),
                                      str(request_id or _rid())))
    if answer == S("paused"):
        return None
    if not (isinstance(answer, tuple) and len(answer) == 5 and answer[0] == S("captured")
            and type(answer[1]) is int and type(answer[2]) is int):
        raise LedgerUnavailable("input ledger capture: unexpected answer %s" % _show(answer))
    return answer[1], answer[2], answer[3], _checked_text(answer[4], "owner")


def turn_decide(serial, kind, tool, command_text, request_id=None):
    """"ignored" (not the pending turn), the ticket of an issued command, or the kind of the reply."""
    from execution_gateway.cetta_worker import S
    answer = _ledger_call("decide", (int(serial), str(kind), str(tool), str(command_text), str(request_id or _rid())))
    if answer == S("ignored"):
        return "ignored"
    if isinstance(answer, tuple) and len(answer) == 2 and answer[0] in (S("issued"), S("decided")):
        return _checked_text(answer[1], "ticket")
    raise LedgerUnavailable("input ledger decide: unexpected answer %s" % _show(answer))


def _turn_flag(verb, arguments):
    answer = _ledger_call(verb, arguments)
    if type(answer) is not bool:
        raise LedgerUnavailable("input ledger %s: unexpected answer %s" % (verb, _show(answer)))
    return answer


def action_report(ticket, payload, disposition, domain, payload_digest, request_id=None):
    """True when this call recorded the action's receipt (once, for an issued or uncertain action)."""
    payload = str(payload)
    return _turn_flag("report", (str(ticket), payload, str(disposition), str(domain), str(payload_digest),
                                 len(payload), str(request_id or _rid())))


def action_observe(pairs, request_id=None):
    """Mark observed the actions whose receipt payload digest is the presented one: pairs of (ticket, digest)."""
    pairs = tuple((str(ticket), str(digest)) for ticket, digest in pairs)
    if pairs:
        _turn_flag("observe", (pairs, str(request_id or _rid())))


def action_attest(request, ticket, request_id=None):
    return _turn_flag("attest", (str(request), str(ticket), str(request_id or _rid())))


def action_finish(request, ticket, request_id=None):
    return _turn_flag("finish", (str(request), str(ticket), str(request_id or _rid())))


def _action_of(row):
    from execution_gateway.cetta_worker import S
    if not (isinstance(row, tuple) and len(row) == 10 and row[0] == S("action")):
        raise LedgerUnavailable("input ledger actions: unexpected row %s" % _show(row))
    _, ticket, request, serial, tool, command, status, observed, receipt, payload = row
    if _is_none(receipt):
        receipt = None
    elif isinstance(receipt, tuple) and len(receipt) == 3 and receipt[0] == S("receipt"):
        receipt = dict(ticket=ticket, payload=payload, disposition=receipt[1], domain=receipt[2])
    else:
        raise LedgerUnavailable("input ledger actions: unexpected receipt %s" % _show(receipt))
    return dict(ticket=ticket, request=request, serial=serial, tool=tool, command=command,
                status=status, observed=_flag_of(observed), receipt=receipt)


# A read of every record of a family (the diagnostic projection, the conversation of the SQLite engine's
# context) can carry the whole history; it gets this deadline instead of a call's usual one.
WHOLE_READ_SECONDS = 900


def actions(which="all"):
    """The actions, oldest first: "all", "unobserved" (with a receipt), ("ticket", t), ("status", s),
    ("request", r) or ("protected", focus) (the protected view's two receipts). Each a dict whose receipt is None or {ticket, payload, disposition, domain}."""
    from execution_gateway.cetta_worker import S
    selection = which if isinstance(which, str) else (S(which[0]), str(which[1]))
    answer = _ledger_call("actions", (selection,), WHOLE_READ_SECONDS if which == "all" else 10)
    return [_action_of(row) for row in _entries(answer, "actions")]


def attempt_begin(ticket, position, command_text, request_id=None):
    """True when recorded; False when the (ticket, position) already has an attempt."""
    from execution_gateway.cetta_worker import S
    answer = _ledger_call("attempt-begin", (str(ticket), int(position), str(command_text), str(request_id or _rid())))
    if answer not in (S("created"), S("exists")):
        raise LedgerUnavailable("input ledger attempt-begin: unexpected answer %s" % _show(answer))
    return answer == S("created")


def attempt_result(ticket, position, command_text, result_text, request_id=None):
    """True when the result was recorded (the attempt exists for that command and had none)."""
    return _turn_flag("attempt-result", (str(ticket), int(position), str(command_text), str(result_text),
                                         str(request_id or _rid())))


def attempts(ticket=None, recent=None):
    """The attempts of a ticket by position, or (with recent=(bound, limit)) the newest `limit` whose order
    is at most `bound`, newest first: dicts {ticket, position, command, result (None before it), order}."""
    from execution_gateway.cetta_worker import S
    selection = (S("ticket"), str(ticket)) if recent is None else (S("recent"), int(recent[0]), int(recent[1]))
    rows = []
    for row in _entries(_ledger_call("attempts", (selection,)), "attempts"):
        if not (len(row) == 6 and row[0] == S("attempt")):
            raise LedgerUnavailable("input ledger attempts: unexpected row %s" % _show(row))
        rows.append(dict(ticket=row[1], position=row[2], command=row[3],
                         result=None if _is_none(row[4]) else row[4], order=row[5]))
    return rows


def turn_dump():
    """(meta keys, turns, arrivals): every turn as a dict and every request's arrival position."""
    from execution_gateway.cetta_worker import S
    answer = _ledger_call("turn-dump", (), WHOLE_READ_SECONDS)
    if not (isinstance(answer, tuple) and len(answer) == 4 and answer[0] == S("dump")):
        raise LedgerUnavailable("input ledger turn-dump: unexpected answer %s" % _show(answer))
    state = {entry[0]: None if _is_none(entry[1]) else entry[1] for entry in _entries(answer[1], "turn-dump")}
    turns = [dict(serial=t[1], status=t[2], revision=t[3], mode=t[4], owner=t[5], focus=t[6],
                  boundary_digest=t[7], router=t[8], guard=t[9], position=t[10])
             for t in _entries(answer[2], "turn-dump")]
    arrivals = {entry[0]: entry[1] for entry in _entries(answer[3], "turn-dump")}
    return state, sorted(turns, key=lambda turn: turn["serial"]), arrivals


# ---- the task context: task_context's frames, proposals, notes, coverage and request inputs ----

def _answer_name(verb, answer, names):
    from execution_gateway.cetta_worker import Symbol
    if isinstance(answer, Symbol) and answer.name in names:
        return answer.name
    raise LedgerUnavailable("input ledger %s: unexpected answer %s" % (verb, _show(answer)))


def link_inputs(request, inputs, request_id=None):
    """Ledger.link_inputs: False when an input: envelope already names another set of inputs."""
    inputs = tuple(sorted({str(i) for i in inputs}))
    answer = _ledger_call("link-inputs", (str(request), inputs, str(request_id or _rid())))
    return _answer_name("link-inputs", answer, ("ok", "conflict")) == "ok"


def linked(input_id):
    """Ledger.requests_with_input: [{request, inputs (sorted)}] in linking order."""
    return [dict(request=_checked_text(entry[0], "request"), inputs=list(entry[1]))
            for entry in _entries(_ledger_call("linked", (str(input_id),)), "linked")]


def task_begin(request, trigger, previous, chat, parent, request_id=None):
    """task_context.begin: None, or the refusal text."""
    from execution_gateway.cetta_worker import S
    answer = _ledger_call("task-begin", (str(request), str(trigger), _symbol_none(None if previous is None else str(previous)),
                                         str(chat or ""), _symbol_none(None if parent is None else str(parent)),
                                         str(request_id or _rid())))
    if answer == S("ok"):
        return None
    if isinstance(answer, tuple) and len(answer) == 2 and answer[0] == S("refused") and isinstance(answer[1], str):
        return answer[1]
    raise LedgerUnavailable("input ledger task-begin: unexpected answer %s" % _show(answer))


def task_focus(request, request_id=None):
    _answer_name("task-focus", _ledger_call("task-focus", (_symbol_none(request), str(request_id or _rid()))), ("ok",))


def task_proposal(serial, request, commands_text, disposition, request_id=None):
    """False when the serial already names another request or other commands."""
    answer = _ledger_call("task-proposal", (int(serial), str(request), str(commands_text), str(disposition),
                                            str(request_id or _rid())))
    return _answer_name("task-proposal", answer, ("ok", "conflict")) == "ok"


def task_note(request, text, sources_text, request_id=None):
    """False when the request is unknown."""
    answer = _ledger_call("task-note", (str(request), str(text), str(sources_text), str(request_id or _rid())))
    return _answer_name("task-note", answer, ("ok", "unknown")) == "ok"


def task_resume(request):
    answer = _ledger_call("task-resume", (_symbol_none(request),))
    return None if _is_none(answer) else _checked_text(answer, "request")


def task_family(request):
    """(root, members) of a task; ValueError on cyclic ancestry."""
    from execution_gateway.cetta_worker import S
    answer = _ledger_call("task-family", (str(request),))
    if answer == S("cyclic"):
        raise ValueError("cyclic task ancestry")
    if isinstance(answer, tuple) and len(answer) == 3 and answer[0] == S("family"):
        return answer[1], list(answer[2])
    raise LedgerUnavailable("input ledger task-family: unexpected answer %s" % _show(answer))


def meta_prefix(prefix):
    """The meta keys other modules keep under a prefix, by key: [(key, JSON text)]."""
    return [(_checked_text(entry[0], "key"), _checked_text(entry[1], "value"))
            for entry in _entries(_ledger_call("meta-prefix", (str(prefix),)), "meta-prefix")]


def request_context(request):
    """(the chat of the request's frame or None, its inputs)."""
    from execution_gateway.cetta_worker import S
    answer = _ledger_call("request-context", (str(request),))
    if not (isinstance(answer, tuple) and len(answer) == 3 and answer[0] == S("context") and isinstance(answer[2], tuple)):
        raise LedgerUnavailable("input ledger request-context: unexpected answer %s" % _show(answer))
    return (None if _is_none(answer[1]) else answer[1]), list(answer[2])


def task_call(serial, ticket):
    """The request of a call's serial (its proposal's, else its action's), or None."""
    answer = _ledger_call("task-call", (int(serial), str(ticket)))
    return None if _is_none(answer) else _checked_text(answer, "request")


def task_view(which):
    """Everything a task projection reads, for a list of requests or "all", from one snapshot."""
    from execution_gateway.cetta_worker import S
    selection = S("all") if which == "all" else tuple(str(item) for item in dict.fromkeys(which))
    answer = _ledger_call("task-view", (selection,), WHOLE_READ_SECONDS if which == "all" else 10)
    if not (isinstance(answer, tuple) and len(answer) == 10 and answer[0] == S("view")):
        raise LedgerUnavailable("input ledger task-view: unexpected answer %s" % _show(answer))
    _, owner, requests, coverings, notes, proposals, actions, attempts, arrivals, captures = answer
    attempts_by_ticket = {}
    for ticket, position, result in _entries(attempts, "task-view"):
        attempts_by_ticket.setdefault(ticket, {})[position] = None if _is_none(result) else result
    return dict(owner=None if owner == S("absent") else owner,
                requests=[_request_row(row) for row in requests],
                coverings={entry[0]: entry[1] for entry in _entries(coverings, "task-view")},
                notes=sorted((dict(sequence=n[1], request=n[0], text=n[2], sources=n[3])
                              for n in _entries(notes, "task-view")), key=lambda note: note["sequence"]),
                proposals={p[0]: dict(serial=p[0], request=p[1], commands=p[2], disposition=p[3])
                           for p in _entries(proposals, "task-view")},
                actions={a[0]: dict(serial=a[0], ticket=a[1], request=a[2], command=a[3])
                         for a in _entries(actions, "task-view")},
                attempts=attempts_by_ticket,
                arrivals={entry[0]: entry[1] for entry in _entries(arrivals, "task-view")},
                captures={entry[0]: entry[1] for entry in _entries(captures, "task-view")})


def processed_update_ids(ids):
    """Called only after successful cognition, saved history and durable input ack."""
    values = [str(value) for value in ids if str(value)]
    if not values:
        return 1
    if _native_socket():
        if _managed():
            # One $now for the whole batch and for every retry of it.
            _ledger_flag("mark-processed", (time.time(), tuple(values)))
        return 1
    store = _store()
    if store is None:
        return 1
    with store.transaction() as db:
        for identity in values:
            db.execute("INSERT OR IGNORE INTO processed_inputs VALUES (?,?)", (identity, time.time()))
            db.execute("UPDATE input_notices SET processed=1 WHERE update_id=?", (identity,))
    return 1


def committed(ids):
    """Only a durable checkpoint can authorize managed input acknowledgement."""
    if os.environ.get("METTACLAW_OBSERVED_STRATEGY", "0") != "1":
        return 1
    values = [str(value) for value in (ids.split(",") if isinstance(ids, str) else ids) if str(value)]
    if not (os.environ.get("METTACLAW_STRATEGY_PATH") or os.environ.get("METTACLAW_ENGINE_STATE_PATH")):
        return 0
    if _native_socket():
        # An unreachable ledger raises: it must not mean "not processed" either.
        return int(bool(_ledger_call("committed", (tuple(values),))))
    # Read a committed snapshot without acquiring the cognitive writer. Errors
    # propagate: an unavailable checkpoint must not mean "not processed".
    with closing(sqlite3.connect(agent_checkpoint.path().resolve().as_uri() + '?mode=ro',
                                 uri=True, timeout=10)) as db:
        db.execute("BEGIN")
        return int(all(db.execute("SELECT 1 FROM processed_inputs WHERE id=?", (identity,)).fetchone()
                       for identity in values))


def already_committed(identity):
    if os.environ.get("METTACLAW_OBSERVED_STRATEGY", "0") != "1" or not str(identity):
        return 0
    return committed([str(identity)])


def text():
    return TEXT


def scan_after():
    return _scan_after


def begin_scan(tasks, source_tasks=None):
    global _input_view, _active_tasks, _scan_after, _scan_seen
    source = tasks if source_tasks is None else source_tasks
    started_from = _scan_after if source_tasks is not None else ""
    if not started_from:
        _scan_seen = set()
    page = [str(item[0]) for item in source
            if isinstance(item, (list, tuple)) and len(item) == 2
            and str(item[0]) != "runtime-scan-unavailable"]
    complete = not any(isinstance(item, (list, tuple)) and len(item) == 2
                       and str(item[0]) == "runtime-scan-unavailable" for item in source)
    _scan_seen.update(page)
    _scan_after = page[-1] if not complete and page else ""
    _input_view = None
    # This is an observation of the durable service's pending inputs. It
    # neither acknowledges them nor calls a model. Scan sentinels distinguish
    # a completed empty scan from a transport failure or a bounded prefix.
    dates, known = [], complete and not started_from
    for item in tasks:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            known = False
            continue
        task, body = item
        if str(task) == "runtime-scan-unavailable":
            known = False
            continue
        try:
            value = parsed_task(task, body)
            if not isinstance(value, list) or not value:
                known = False
            elif value[0] == "input":
                if len(value) != 5:
                    known = False
                    continue
                if value[2] == "message" and value[3] == "operator":
                    update = value[4]
                    message = update.get("message") or update.get("edited_message")
                    stamp = message.get("date") if isinstance(message, dict) else None
                    dates.append(stamp if type(stamp) in (int, float) and
                                 0 < stamp <= 10**12 and math.isfinite(stamp) else None)
            elif value[0] not in ("command", "callback", "delivery", "rejected", "released"):
                known = False
        except (ValueError, TypeError, AttributeError, RecursionError):
            known = False
    _input_view = ({"pending": len(dates), "newestMessageAt": max(dates) if dates and None not in dates else None,
                    "oldestMessageAt": min(dates) if dates and None not in dates else None,
                    "observedAt": time.time()} if known else None)
    active = {str(task[0]) for task in tasks
              if isinstance(task, (list, tuple)) and len(task) == 2}
    _active_tasks = active
    for task in list(_task_cache):
        if task not in active:
            del _task_cache[task]
    if complete:
        for task in list(_pending):
            if task not in _scan_seen:
                del _pending[task]
    lanes = {entry[2] for entry in _pending.values()}
    for lane in list(_last_notice):
        if lane not in lanes:
            del _last_notice[lane]
    return 1


def input_view():
    """A copy of the last complete scan, without text, identities or routes."""
    return dict(_input_view) if _input_view is not None else None


def key(task, lane, kind, role, update="{}"):
    if (os.environ.get("METTACLAW_OBSERVED_STRATEGY", "0") != "1"
            or str(kind) != "message" or str(role) != "operator"):
        return ""
    import cognitive_health
    if cognitive_health.failure_pending():
        return ""  # The failure episode supplies actionable feedback instead.
    try:
        delay = float(os.environ.get("METTACLAW_INPUT_ACK_SECONDS", "15"))
    except ValueError:
        delay = 15
    if not 0 < delay < float("inf"):
        return ""
    task, lane = str(task), str(lane)
    if not task or not lane or len(task) > 128 or len(lane) > 128:
        return ""
    import attention
    attention.ingress(task, role, update)
    value = json.loads(str(update)) if isinstance(update, str) else update
    update_id = str(value.get("update_id", "")) if isinstance(value, dict) else ""
    native = bool(_native_socket()) and _managed()
    if native:
        if update_id and _ledger_call("committed", ((update_id,),), 0):
            return ""
        store = None
    else:
        store = _store(timeout=0)
    if store is not None:
        with store.transaction() as db:
            if db.execute("SELECT 1 FROM processed_inputs WHERE id=?", (update_id,)).fetchone():
                return ""
    if task not in _pending:
        _pending[task] = [time.monotonic(), False, lane]
    since, submitted, captured_lane = _pending[task]
    now = time.monotonic()
    if (submitted or lane != captured_lane or now - since < delay
            or now - _last_notice.get(lane, -float("inf")) < 60):
        return ""
    # Lost submission replies and restarts retain the same key and immutable
    # bytes. The service reconciles duplicates and enforces lane permissions.
    identity = os.environ.get("METTACLAW_INSTANCE", "default") + ":input-receipt:" + task
    number = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big")
    send_key = lane + ".%020d" % number
    if native:
        # Registers atomically unless the update is processed or the notice conflicts; "" then.
        # $now is part of the request id and stays the same when the call is repeated.
        answer = _ledger_call("register", (task, update_id, lane, send_key, time.time()), 0)
        if answer not in ("", send_key):
            raise LedgerUnavailable("input ledger register: unexpected answer %r" % (answer,))
        return answer
    if store is not None:
        with store.transaction() as db:
            if db.execute("SELECT 1 FROM processed_inputs WHERE id=?", (update_id,)).fetchone():
                return ""
            old = db.execute("SELECT * FROM input_notices WHERE task=?", (task,)).fetchone()
            if old and (old["lane"] != lane or old["send_key"] != send_key or old["processed"]):
                return ""
            db.execute("INSERT OR IGNORE INTO input_notices(task,update_id,lane,send_key,created) VALUES (?,?,?,?,?)",
                       (task, update_id, lane, send_key, time.time()))
    return send_key


def submitted(task):
    entry = _pending.get(str(task))
    if entry is not None:
        entry[1] = True
        _last_notice[entry[2]] = time.monotonic()
    if _native_socket():
        if _managed():
            _ledger_flag("submitted", (str(task),), 0)
        return 1
    store = _store(timeout=0)
    if store is not None:
        with store.transaction() as db:
            db.execute("UPDATE input_notices SET state='submitted' WHERE task=? AND state='registered'", (str(task),))
    return 1


def receipt(observation, timeout=10):
    """Save a correlated channel delivery before any reader acknowledges it."""
    value = json.loads(str(observation)) if isinstance(observation, str) else observation
    if (not isinstance(value, list) or len(value) != 5 or value[0] != "delivery"
            or value[2] != 0 or value[3] != 1):
        return 0
    if _native_socket():
        return _native_receipt(value, timeout) if _managed() else 0
    store = _store(timeout=timeout)
    if store is None:
        return 0
    key, outcome = value[1], value[4]
    with store.transaction() as db:
        row = db.execute("SELECT * FROM input_notices WHERE send_key=? OR delete_key=?", (key, key)).fetchone()
        if not row:
            return 0
        if (not isinstance(outcome, list) or len(outcome) != 2 or outcome[0] != "delivered"
                or type(outcome[1]) is not int):
            return 1  # known failed/uncertain evidence grants no deletion
        if key == row["send_key"] and outcome[1] > 0:
            if row["message_id"] is not None and row["message_id"] != outcome[1]:
                return 0
            db.execute("UPDATE input_notices SET message_id=? WHERE task=?", (outcome[1], row["task"]))
        elif key == row["delete_key"] and row["processed"] and outcome[1] in (0, row["message_id"]):
            db.execute("UPDATE input_notices SET state='deleted' WHERE task=?", (row["task"],))
        else:
            return 0
    return 1


def _native_receipt(value, timeout):
    """The body of receipt() on the native ledger, after its envelope checks."""
    from execution_gateway.cetta_worker import E, S
    key, outcome = value[1], value[4]
    if not isinstance(key, str):
        return 0  # a key that is no text names no notice (SQLite would raise for a list or object)
    if (isinstance(outcome, list) and len(outcome) == 2 and outcome[0] == "delivered"
            and type(outcome[1]) is int):
        if not -2**63 <= outcome[1] < 2**63:
            raise OverflowError("message id out of range")
        outcome = E("delivered", outcome[1])
    else:
        outcome = S("failed")  # known failed/uncertain evidence grants no deletion
    answer = _ledger_call("receipt", (key, outcome), timeout)
    if type(answer) is not int or answer not in (0, 1):
        raise LedgerUnavailable("input ledger receipt: unexpected answer %r" % (answer,))
    return answer


def _pump_native(channel):
    """pump() on the native ledger: the same steps, one check per due notice."""
    from execution_gateway.cetta_worker import S
    if not _managed():
        return 0
    _ledger_call("ensure", (), 0)  # as _store() opens the ledger first: an unreachable one fails here
    import durable_telegram as protocol
    if channel is None:
        path = os.environ.get("METTACLAW_TELEGRAM_SERVICE_SOCKET")
        if not path:
            return 0
        channel = protocol.Channel(path, os.devnull, timeout=1)
    def collect():
        for task, observation in channel.pending(64):
            if receipt(observation, timeout=0):
                channel.acknowledge(task)
    count = 0
    try:
        collect()
        rows = _ledger_call("due", (32,), 0)
        for row in rows:
            if not (isinstance(row, tuple) and len(row) == 11 and isinstance(row[1], str)):
                raise LedgerUnavailable("input ledger due: unexpected row %s" % _show(row))
            # One step in one transaction: checked, expiry or the delete key. $now stays the same on repeats.
            answer = _ledger_call("check", (row[1], time.time(), 172800), 0)
            if answer in (S("wait"), S("expired")):
                continue
            if not (isinstance(answer, tuple) and len(answer) == 3 and answer[0] == S("delete")
                    and isinstance(answer[1], str) and type(answer[2]) is int):
                raise LedgerUnavailable("input ledger check: unexpected answer %s" % _show(answer))
            code, _, _ = channel.rpc(protocol.SUBMIT, answer[1],
                                      json.dumps([["delete", answer[2]]], separators=(",", ":")))
            if code == protocol.STORED:
                count += 1
        collect()
    except (OSError, ValueError, protocol.ChannelError):
        pass  # durable intentions and unacknowledged receipt tasks remain
    return count


def pump(channel=None):
    """Reconcile owned delivery tasks and keyed deletion intents independently."""
    global _last_pump
    now = time.monotonic()
    if channel is None and now - _last_pump < 2:
        return 0
    _last_pump = now
    if _native_socket():
        return _pump_native(channel)
    store = _store(timeout=0)
    if store is None:
        return 0
    import durable_telegram as protocol
    if channel is None:
        path = os.environ.get("METTACLAW_TELEGRAM_SERVICE_SOCKET")
        if not path:
            return 0
        channel = protocol.Channel(path, os.devnull, timeout=1)
    def collect():
        for task, observation in channel.pending(64):
            if receipt(observation, timeout=0):
                channel.acknowledge(task)
    count = 0
    try:
        collect()
        with store.transaction() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM input_notices WHERE state IN ('registered','submitted') ORDER BY processed DESC,checked,created LIMIT 32")]
        for row in rows:
            with store.transaction() as db:
                db.execute("UPDATE input_notices SET checked=? WHERE task=?", (time.time(), row["task"]))
            if time.time() - row["created"] > 172800:
                with store.transaction() as db:
                    db.execute("UPDATE input_notices SET state='expired' WHERE task=?", (row["task"],))
                continue
            if row["message_id"] is None or not row["processed"]:
                continue
            identity = "notice-delete:" + row["send_key"]
            number = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big")
            delete_key = row["lane"] + ".%020d" % number
            with store.transaction() as db:
                db.execute("UPDATE input_notices SET delete_key=? WHERE task=?", (delete_key, row["task"]))
            code, _, _ = channel.rpc(protocol.SUBMIT, delete_key,
                                      json.dumps([["delete", row["message_id"]]], separators=(",", ":")))
            if code == protocol.STORED:
                count += 1
        collect()
    except (OSError, ValueError, protocol.ChannelError):
        pass  # durable intentions and unacknowledged receipt tasks remain
    return count
