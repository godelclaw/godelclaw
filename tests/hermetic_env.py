"""A test process that cannot reach a live ledger, state directory or socket.

    import hermetic_env  # noqa: F401  (first import of a test module, before any src module)

Importing this module (once per process; later imports and calls change nothing)
makes the running process hermetic:

* every variable that names a state file, ledger, socket or runtime directory of
  an agent is REMOVED from os.environ: all METTACLAW_* and OMEGACLAW_*, the
  TELEGRAM_* MOLTBOOK_* CLAW_MAIL_* TAVILY_* (channel settings, credentials), the
  CETTA_DB_* / CETTA_KB_* / CETTA_SIWC_STORE / CETTA_SHARED_MAIL_SOCKET store
  settings, every name ending in _SOCKET _SOCK _STATE _STATE_DIR _STATE_PATH
  _STATE_HOME _STATE_ROOT _STORE _LEDGER _LEDGER_PATH _TOKEN _API_KEY _SECRET
  _PASSWORD, and the session handles
  NOTIFY_SOCKET SSH_AUTH_SOCK DBUS_SESSION_BUS_ADDRESS TMUX TMUX_PANE TMUX_TMPDIR
  MCP_CONFIG_PATH STATE_HOME (see SCRUBBED_EXACT / SCRUBBED_PREFIXES / SCRUBBED_SUFFIXES);
* HOME, XDG_STATE_HOME, XDG_CONFIG_HOME, XDG_CACHE_HOME, XDG_DATA_HOME,
  XDG_RUNTIME_DIR and TMPDIR (and tempfile.tempdir) point into a fresh directory
  that this process created under /tmp and removes at exit (a child process that
  imports this module again reuses its parent's directory and does not remove it);
* sqlite3.connect and AF_UNIX connect refuse (LiveStateRefused) a path that is, or
  lies under, the real home's state / data / config / cache directories, a value a
  removed variable had, or anywhere else in the real home outside this checkout; the
  checkout's own .runtime scratch tree is allowed wherever the checkout lies.

A test that forgets its own fixture therefore finds no ledger path (agent_checkpoint
raises RuntimeError, input_ack finds no socket) or writes into the temporary home.

Left alone on purpose: CETTA_BIN and the other engine locations (CETTA_ROOT,
CETTA_VALUE_WORKER_BIN, CETTA_TELEGRAM_SERVICE_ROOT/BIN, CETTA_DB_SERVER_MODULE,
PETTA_ROOT, PETTA_PY_ENV, PETTA_*), the two read-only test data locations
METTACLAW_LEAN_MODEL_DIR and METTACLAW_ATLAS_TEST_ROOT (the only METTACLAW_* a test reads from the invoking shell),
METTACLAW_MODE_CETTA_BIN (the modes' engine), PATH, LD_LIBRARY_PATH, LEAN_*, PYTHON*, LANG, TZ,
the *_BASE_URL and model-name settings, and XDG_DATA_DIRS/XDG_CONFIG_DIRS (read-only
search lists). tests/test_hermetic_guard.py lists every other variable the sources read.

The one deliberate way to give a test process a ledger socket is run_with_ledger_server.py,
which makes a 0700 directory /tmp/il-run-*, writes the marker file ".godel-test-ledger"
holding the socket path into it, and exports the path as GODEL_TEST_LEDGER_SOCKET (and
as METTACLAW_INPUT_LEDGER_SOCKET). This module keeps METTACLAW_INPUT_LEDGER_SOCKET only
when GODEL_TEST_LEDGER_SOCKET passes harness_socket(); the value is then the validated
one whatever the environment held, and without the harness variable it is removed.
"""
import atexit
import os
import pwd
import re
import shutil
import socket
import sqlite3
import stat
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import isolated_env  # first: it records the inherited METTACLAW_* before they are removed

LEDGER_SOCKET = "METTACLAW_INPUT_LEDGER_SOCKET"
HARNESS_SOCKET = "GODEL_TEST_LEDGER_SOCKET"
HARNESS_MARKER = ".godel-test-ledger"
ROOT_VARIABLE = "GODEL_TEST_HERMETIC_ROOT"
ROOT_MARKER = ".godel-hermetic-root"
ROOT_PREFIX = "gth-"

SCRUBBED_PREFIXES = ("METTACLAW_", "OMEGACLAW_", "CETTA_DB_", "CETTA_KB_", "TELEGRAM_", "MOLTBOOK_", "CLAW_MAIL_", "TAVILY_")
SCRUBBED_EXACT = frozenset({
    "NOTIFY_SOCKET", "SSH_AUTH_SOCK", "DBUS_SESSION_BUS_ADDRESS", "TMUX", "TMUX_PANE", "TMUX_TMPDIR",
    "MCP_CONFIG_PATH", "STATE_HOME", "CETTA_SIWC_STORE", "CETTA_SHARED_MAIL_SOCKET", "CETTA_JOB_AGENT",
})
SCRUBBED_SUFFIXES = re.compile(r"_(SOCKET|SOCK|STATE|STATE_DIR|STATE_PATH|STATE_HOME|STATE_ROOT|STORE|LEDGER|LEDGER_PATH|TOKEN|API_KEY|SECRET|PASSWORD)$")
KEPT = frozenset({"CETTA_DB_SERVER_MODULE", HARNESS_SOCKET, "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME",
                  "XDG_CACHE_HOME", "XDG_RUNTIME_DIR",    # a code location; the harness opt-in; XDG_* are replaced, not removed
                  "METTACLAW_LEAN_MODEL_DIR", "METTACLAW_ATLAS_TEST_ROOT",  # read-only locations a test opts in to (verify() refuses a live one)
                  "METTACLAW_MODE_CETTA_BIN"})  # the modes' engine, an engine location like CETTA_BIN
LEAVES = (("HOME", "h"), ("XDG_STATE_HOME", "s"), ("XDG_CONFIG_HOME", "c"), ("XDG_CACHE_HOME", "k"),
          ("XDG_DATA_HOME", "d"), ("XDG_RUNTIME_DIR", "r"), ("TMPDIR", "t"))

REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)  # from the account database, never from $HOME
CHECKOUT = Path(__file__).resolve().parents[1]


class LiveStateRefused(RuntimeError):
    """A test process tried to open a state path that belongs to a real agent."""


SCRUBBED = []        # names removed by this process (names only; the values are never printed or logged)
ROOT = None          # the temporary home of this process tree
_live_values = set()
_captured_zones = []
_creator = None


def _under(path, base):
    return path == base or base in path.parents


def _real(path):
    return Path(os.path.realpath(path))


def is_state_name(name):
    if name in KEPT:
        return False
    return name in SCRUBBED_EXACT or name.startswith(SCRUBBED_PREFIXES) or bool(SCRUBBED_SUFFIXES.search(name))


def _zones():
    zones = []
    for variable in ("XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        value = os.environ.get(variable)
        if value and os.path.isabs(value):
            zones.append(Path(value))
    return zones


def refuse_if_live(path):
    """Raise LiveStateRefused when path is a live ledger/state path (see the module doc)."""
    resolved = _real(os.path.abspath(os.fsdecode(path)))
    if ROOT is not None and _under(resolved, _real(ROOT)):
        return
    if _under(resolved, _real(CHECKOUT / ".runtime")):
        return  # the tests' own scratch tree, also when the checkout lies inside a state directory
    for zone in _captured_zones:
        if _under(resolved, _real(zone)):
            raise LiveStateRefused("refusing %s: inside the real state, data, config or cache directory" % resolved)
    for value in _live_values:
        if resolved == _real(value):
            raise LiveStateRefused("refusing %s: it is the path of a removed state variable" % resolved)
    if _under(resolved, _real(REAL_HOME)) and not _under(resolved, CHECKOUT):
        raise LiveStateRefused("refusing %s: inside the real home, outside this checkout" % resolved)


def _sqlite_target(database):
    if isinstance(database, (str, bytes, os.PathLike)):
        text = os.fsdecode(database)
        if text in ("", ":memory:"):
            return None
        if text.startswith("file:"):
            parsed = urlparse(text)
            if "mode=memory" in parsed.query or not parsed.path:
                return None
            return unquote(parsed.path)
        return text
    return None


def _install_guards():
    if not getattr(sqlite3.connect, "_godel_guard", False):
        original = sqlite3.connect

        def connect(database, *args, **kwargs):
            target = _sqlite_target(database)
            if target is not None:
                refuse_if_live(target)
            return original(database, *args, **kwargs)
        connect._godel_guard = True
        sqlite3.connect = connect
    if not getattr(socket.socket.connect, "_godel_guard", False):
        def guarded(method):
            def wrapper(self, address):
                if self.family == getattr(socket, "AF_UNIX", None) and isinstance(address, (str, bytes)):
                    text = os.fsdecode(address)
                    if text and not text.startswith("\0"):
                        refuse_if_live(text)
                return method(self, address)
            wrapper._godel_guard = True
            return wrapper
        socket.socket.connect = guarded(socket.socket.connect)
        socket.socket.connect_ex = guarded(socket.socket.connect_ex)


def _valid_root(value):
    try:
        path = Path(value)
        info = path.lstat()
        return (stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077
                and path.name.startswith(ROOT_PREFIX) and (path / ROOT_MARKER).is_file()
                and not _under(_real(path), _real(REAL_HOME)))
    except OSError:
        return False


def _base():
    for candidate in (os.environ.get("TMPDIR"), "/tmp"):
        if candidate and os.path.isabs(candidate) and os.path.isdir(candidate) \
                and not _under(_real(candidate), _real(REAL_HOME)):
            return candidate
    raise LiveStateRefused("no temporary directory outside the real home")


def _cleanup(root):
    if os.getpid() == _creator and root.name.startswith(ROOT_PREFIX) and (root / ROOT_MARKER).is_file():
        shutil.rmtree(root, ignore_errors=True)


def harness_socket(value):
    """The socket path of a run_with_ledger_server.py run, or raise: it must sit in a 0700 /tmp/il-run-* directory of
    this user that holds the marker file with exactly this path, and must not be a live path."""
    path = Path(value)
    parent = path.parent
    try:
        info = parent.lstat()
        marker = (parent / HARNESS_MARKER).read_text()
    except OSError as error:
        raise LiveStateRefused("%s is not a harness socket: %s" % (HARNESS_SOCKET, error.strerror))
    if not (path.is_absolute() and path.name.endswith(".sock") and parent.name.startswith("il-run-")
            and _real(parent).parent == _real("/tmp")
            and stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077
            and marker == str(path)):
        raise LiveStateRefused("%s does not name a harness socket" % HARNESS_SOCKET)
    for zone in _captured_zones:
        if _under(_real(path), _real(zone)):
            raise LiveStateRefused("%s names a live state path" % HARNESS_SOCKET)
    return str(path)


def apply():
    """Make this process hermetic. Safe to call again and from every module: later calls only remove state variables
    that have appeared since (a value inside this process tree's temporary home is the test's own and stays), and
    never replace HOME and the other directories again."""
    global ROOT, _creator
    first = ROOT is None
    descendant = False
    if first:
        inherited = os.environ.get(ROOT_VARIABLE)
        descendant = bool(inherited and _valid_root(inherited))
        if descendant:
            ROOT = Path(inherited)  # a child of a hermetic process: its HOME, XDG_* and TMPDIR are the parent's or the test's own
        else:
            _captured_zones.extend(_zones())  # before anything is replaced: the invoking shell's real directories
            ROOT = Path(tempfile.mkdtemp(prefix=ROOT_PREFIX, dir=_base()))
            os.chmod(ROOT, 0o700)
            (ROOT / ROOT_MARKER).write_text("godel hermetic test root\n")
            _creator = os.getpid()
            atexit.register(_cleanup, ROOT)
            os.environ[ROOT_VARIABLE] = str(ROOT)
        _captured_zones.extend(z for z in (REAL_HOME / ".local/state", REAL_HOME / ".local/share", REAL_HOME / ".config",
                                           REAL_HOME / ".cache") if z not in _captured_zones)
    root = _real(ROOT)
    for name in list(os.environ):
        if not is_state_name(name):
            continue
        value = os.environ[name]
        if value.startswith("/") and _under(_real(value), root) or (not first or descendant) and not value.startswith("/"):
            continue  # the test's own (or a child's inherited) setting: inside the temporary home, or not a path at all
        del os.environ[name]
        if value.startswith("/"):
            _live_values.add(value)
        if name not in SCRUBBED:
            SCRUBBED.append(name)
    if first:
        for name, leaf in LEAVES:
            (ROOT / leaf).mkdir(mode=0o700, exist_ok=True)
            if not descendant or name not in os.environ:
                os.environ[name] = str(ROOT / leaf)
        if not descendant:
            tempfile.tempdir = str(ROOT / "t")
    harness = os.environ.get(HARNESS_SOCKET)
    if harness:
        validated = harness_socket(harness)
        os.environ[LEDGER_SOCKET] = validated
        isolated_env._INHERITED[LEDGER_SOCKET] = validated  # children made with isolated_environ still do not get it
    _install_guards()
    verify()


def verify():
    """Raise when the environment still names a live path (a single-path value; PATH-like lists are not looked at)."""
    for name, value in os.environ.items():
        if name in (ROOT_VARIABLE, HARNESS_SOCKET) or not value.startswith("/") or ":" in value:
            continue
        path = _real(value)
        if ROOT is not None and _under(path, _real(ROOT)):
            continue
        if name == LEDGER_SOCKET and value == os.environ.get(HARNESS_SOCKET):
            continue
        for zone in _captured_zones:
            if _under(path, _real(zone)):
                raise LiveStateRefused("%s still names a path inside the real state, data, config or cache directory" % name)
        if value in _live_values:
            raise LiveStateRefused("%s still holds the value of a removed state variable" % name)


apply()
