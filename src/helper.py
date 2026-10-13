import hashlib
import json
import os
import re
import sys
import tempfile
import time


def reverse_lines_open(path):
    """Read a stable file prefix backwards, retaining one block and one line.

    The consumer chooses when to stop. No history window or line-count cutoff
    is imposed, and appends after opening do not change the query's snapshot.
    """
    stream = open(str(path), "rb")
    size = stream.seek(0, os.SEEK_END)

    def lines():
        try:
            position, fragments, first = size, [], True
            while position:
                count = min(position, 65536)
                position -= count
                stream.seek(position)
                pieces = stream.read(count).split(b"\n")
                if len(pieces) == 1:
                    fragments.append(pieces[0])
                    continue
                tail = b"".join([pieces[-1], *reversed(fragments)])
                fragments = [pieces[0]]
                if not first or tail:
                    yield tail.rstrip(b"\r").decode("utf-8", errors="replace")
                first = False
                for part in reversed(pieces[1:-1]):
                    yield part.rstrip(b"\r").decode("utf-8", errors="replace")
            if size:
                yield b"".join(reversed(fragments)).rstrip(b"\r").decode("utf-8", errors="replace")
        finally:
            stream.close()

    return lines()


def reverse_lines_next(reader):
    try:
        return ("line", next(reader))
    except StopIteration:
        return ("end",)


def reverse_lines_close(reader):
    reader.close()
    return 1


SAFE_TEXT_REPLACEMENTS = (
    ("_newline_", "\n"),
    ("_quote_", '"'),
    ("_apostrophe_", "'"),
)


def decode_safe_text(value):
    text = str(value)
    for old, new in SAFE_TEXT_REPLACEMENTS:
        text = text.replace(old, new)
    return text



def balance_parentheses(s):
    s = s.strip()
    left = 0
    while left < len(s) and s[left] == '(':
        left += 1
    right = 0
    while right < len(s) and s[len(s) - 1 - right] == ')':
        right += 1
    core = s[left:len(s) - right if right else len(s)].strip()
    normalized = f"(({core}))"
    forms, clean = _top_level_forms(s)
    # Prose is not a command: text with no parenthesized form whose first
    # word names no skill runs nothing (undelivered_prose reports it). A
    # command written without its parentheses still runs.
    if not forms and not s.startswith("(") and not _is_bare_command(s):
        return "()"
    if _is_one_form(normalized) and (s.startswith("(") or not forms):
        return normalized
    # A stray ')' or text around the commands used to make the whole batch
    # unreadable, so nothing ran.  Every complete (command) still runs; text
    # outside parentheses and unmatched ')' are ignored.  An unclosed string
    # or form still fails the batch, because reading it would mean guessing.
    commands = [c for form in forms for c in _commands_in(form)]
    if not clean or not commands:
        return normalized
    print("[helper] recovered %d command(s) from a malformed batch"
          % len(commands), file=sys.stderr)
    return "(" + " ".join(commands) + ")"


def _is_bare_command(text):
    """A command written without its parentheses: its first word is a skill."""
    words = str(text).strip().split(None, 1)
    if not words:
        return False
    import skills_help
    return words[0] in skills_help.ARITY


_AFFECT_TRACE = re.compile(r"⋄⟨[^⟩]*⟩")


def undelivered_prose(response):
    """The text of a model response that no command carries: prose written
    instead of the command list, or around it. It reached no one. The affect
    trace is a self-appraisal, not communication, and is not counted."""
    if not isinstance(response, str):
        return ""
    s = response.strip()
    if not s:
        return ""
    forms, _ = _top_level_forms(s)
    if not forms and not s.startswith("(") and _is_bare_command(s):
        return ""
    outside = s
    for form in forms:
        outside = outside.replace(form, " ", 1)
    outside = _AFFECT_TRACE.sub(" ", outside)
    outside = "\n".join(" ".join(line.split()) for line in outside.replace(")", " ").splitlines())
    outside = re.sub(r"\n{3,}", "\n\n", outside).strip()
    return outside if any(c.isalnum() for c in outside) else ""


def _is_one_form(text):
    """True when text is exactly one balanced parenthesized form."""
    forms, clean = _top_level_forms(text)
    return clean and forms == [text]


def _top_level_forms(text):
    """Complete top-level (...) forms of text, and whether nothing was left
    open.  Strings are read as in the S-expression reader; a ')' with
    nothing open is skipped."""
    forms = []
    depth = 0
    start = 0
    in_string = False
    i = 0
    while i < len(text):
        c = text[i]
        if in_string:
            if c == '\\':
                i += 2
                continue
            if c == '"':
                in_string = False
        elif c == '"':
            in_string = True
        elif c == '(':
            if depth == 0:
                start = i
            depth += 1
        elif c == ')' and depth > 0:
            depth -= 1
            if depth == 0:
                forms.append(text[start:i + 1])
        i += 1
    return forms, depth == 0 and not in_string


def _commands_in(form):
    """A command is (head ...); a form opening with '(' is a list of them."""
    inner = form[1:-1].strip()
    if not inner:
        return []
    if inner[0] != '(':
        return [form]
    return [c for sub in _top_level_forms(inner)[0] for c in _commands_in(sub)]


def normalize_command_batches(raw):
    """Unwrap explicit batch envelopes without reading or running commands.

    Argument strings and ordinary command arguments remain byte-for-byte
    intact. Permission, freshness and pacing checks belong to the broker.
    An incomplete envelope is left for the normal parser to reject.
    """
    text = str(raw)
    forms, clean = _top_level_forms(text)
    if not clean:
        return text
    changed = False

    def children_only(inner):
        children, complete = _top_level_forms(inner)
        rest = inner
        for child in children:
            rest = rest.replace(child, "", 1)
        return children if complete and not rest.strip() else None

    def unwrap(form, depth=0):
        nonlocal changed
        if depth >= 64:
            return [form]
        inner = form[1:-1].strip()
        if inner.startswith("("):
            children = children_only(inner)
        elif inner.split(None, 1)[:1] == ["command-batch"]:
            children = children_only(inner[len("command-batch"):].strip())
            if children is None:
                return [form]
            changed = True
        else:
            return [form]
        if children is None:
            return [form]
        return [command for child in children for command in unwrap(child, depth + 1)]

    commands = [command for form in forms for command in unwrap(form)]
    return "(" + " ".join(commands) + ")" if changed else text


# ---- wake vitals -------------------------------------------------------------
# Interoception measured by the runtime, never inferred by the model: how long
# since the previous turn, and how loaded the machine is.  The prompt is built
# more than once per turn, so the view is fixed per iteration.

_WAKE = {"iteration": None, "at": None, "view": ""}
_HISTORY_STAMP = re.compile(r"(20\d\d-\d\d-\d\d \d\d:\d\d:\d\d)")


def _duration(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "%ds" % seconds
    if seconds < 3600:
        return "%dm%02ds" % (seconds // 60, seconds % 60)
    return "%dh%02dm" % (seconds // 3600, seconds % 3600 // 60)


def _last_turn_from_history():
    """Time of the newest turn in history, for the first wake after start."""
    path = path_from_env("METTACLAW_HISTORY_PATH", "./memory/history.metta")
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 65536))
            stamps = _HISTORY_STAMP.findall(
                fh.read().decode("utf-8", "replace"))
        return time.mktime(time.strptime(stamps[-1], "%Y-%m-%d %H:%M:%S"))
    except (OSError, IndexError, ValueError, OverflowError):
        return None


def _machine_view():
    parts = []
    try:
        with open("/proc/loadavg", encoding="ascii") as fh:
            load = float(fh.read().split()[0])
        parts.append("load %g/%d" % (round(load, 1), os.cpu_count() or 1))
    except (OSError, ValueError, IndexError):
        pass
    try:
        info = {}
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                key, _, value = line.partition(":")
                info[key] = int(value.split()[0])
        used = 100 - 100 * info["MemAvailable"] // info["MemTotal"]
        parts.append("mem %d%%" % used)
    except (OSError, ValueError, KeyError, ZeroDivisionError):
        pass
    return parts


def wake_view(iteration):
    """Compact vitals for one turn: time since the previous turn, machine
    load against its CPUs, and memory in use."""
    if _WAKE["iteration"] == str(iteration) and _WAKE["view"]:
        return _WAKE["view"]
    now = time.time()
    previous = (_WAKE["at"] if _WAKE["at"] is not None
                else _last_turn_from_history())
    parts = (["+%s since last turn" % _duration(now - previous)]
             if previous is not None else [])
    view = " | ".join(parts + _machine_view()) or "unavailable"
    _WAKE.update(iteration=str(iteration), at=now, view=view)
    return view


def _gigabytes(kib):
    return "%.1fG" % (kib / 1048576.0)


def vitals():
    """The machine and this agent now, on demand: uptime, load, memory and
    its pressure stalls, swap, the heaviest processes, background jobs."""
    lines = []
    try:
        with open("/proc/uptime", encoding="ascii") as fh:
            lines.append("up %s" % _duration(float(fh.read().split()[0])))
    except (OSError, ValueError, IndexError):
        pass
    try:
        with open("/proc/loadavg", encoding="ascii") as fh:
            one, five, fifteen = fh.read().split()[:3]
        lines.append("load %s %s %s (1, 5, 15 min) over %d cpus"
                     % (one, five, fifteen, os.cpu_count() or 1))
    except (OSError, ValueError):
        pass
    try:
        info = {}
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                key, _, value = line.partition(":")
                info[key] = int(value.split()[0])
        used = 100 - 100 * info["MemAvailable"] // info["MemTotal"]
        lines.append("memory %d%% used, %s available of %s; swap %s used"
                     % (used, _gigabytes(info["MemAvailable"]),
                        _gigabytes(info["MemTotal"]),
                        _gigabytes(info["SwapTotal"] - info["SwapFree"])))
    except (OSError, ValueError, KeyError, ZeroDivisionError):
        pass
    try:
        with open("/proc/pressure/memory", encoding="ascii") as fh:
            stalls = {line.split()[0]: line.split()[1].split("=")[1]
                      for line in fh if line.strip()}
        lines.append("memory stalls over 10s: some tasks %s%%, all tasks %s%%"
                     % (stalls.get("some", "?"), stalls.get("full", "?")))
    except (OSError, ValueError, IndexError):
        pass
    try:
        import subprocess
        listing = subprocess.run(
            ["ps", "-eo", "user:16,rss,pcpu,etimes,comm", "--sort=-rss"],
            capture_output=True, text=True, timeout=5).stdout.splitlines()[1:6]
        heavy = []
        for row in listing:
            user, rss, cpu, age, command = row.split(None, 4)
            heavy.append("%s %s %s cpu %s%% for %s" % (
                user, command.strip(), _gigabytes(int(rss)), cpu,
                _duration(int(age))))
        if heavy:
            lines.append("heaviest: " + "; ".join(heavy))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    try:
        import jobs
        lines.append("background jobs running: %d" % jobs.running_count())
    except Exception:  # noqa: BLE001 - vitals must never fail
        pass
    return "\n".join(lines) or "vitals unavailable"


_CREDENTIAL_PATTERNS = (
    (re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}"), "<redacted>"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 <redacted>"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "<redacted>"),
    (re.compile(r"(?i)\b((?:api[_-]?)?key|token|secret|password|passwd)"
                r"(['\"]?\s*[:=]\s*['\"]?)[^\s'\"&,;)]{6,}"), r"\1\2<redacted>"),
)


def exception_text(value):
    """A raised Python error as an operator reads it: its type and message,
    with credentials screened out."""
    try:
        if isinstance(value, BaseException):
            text = "%s: %s" % (type(value).__name__, value)
        else:
            text = str(value)
        for pattern, replacement in _CREDENTIAL_PATTERNS:
            text = pattern.sub(replacement, text)
        return text
    except Exception:
        return "an error that could not be shown"


def normalize_string(value):
    """Return Janus/tool output as valid UTF-8 text without raising."""
    try:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value).encode("utf-8", errors="replace").decode("utf-8")
    except Exception:
        return str(value)


def path_from_env(name, default):
    return os.environ.get(str(name), str(default))


def int_env(name, default):
    try:
        return int(os.environ.get(str(name)) or default)
    except (TypeError, ValueError):
        return int(default)


def command_batch_limit(default):
    """Managed-loop admission limit; each composite command counts once."""
    limit = int_env("METTACLAW_COMMAND_BATCH_LIMIT", default)
    return limit if limit > 0 else int(default)


def text_nonempty(value):
    """Return numeric truth for text crossing the Python/MeTTa boundary."""
    return 1 if str(value or "") else 0


def recycle_requested():
    """Return PeTTa's integer truth value for the boundary flag.

    Python bools marshal into PeTTa as (@ true)/(@ false), which the
    language if does not treat as truth — the 1/0 integer convention is
    the reliable boundary type (bug found live 2026-07-18/19)."""
    if __package__:
        from . import engine_modes
    else:
        import engine_modes
    import process_supervision
    if process_supervision.recycle_due():
        engine_modes.request_recycle()
    return int(engine_modes.recycle_requested())


def recycle_ack():
    """Consume the boundary flag: the NEW process clears it at boot, so a
    pending request can fire at most one process exit. Before this
    existed, the flag was immortal — nothing anywhere removed it."""
    path = os.environ.get("METTACLAW_RECYCLE_REQUEST_PATH", "")
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass
    return 1


def recycle_exit():
    """Hard process exit for the recycle boundary. Merely returning from
    the loop recursion lands in engine backtracking, which re-enters the
    loop from scratch inside the same process — a boot-loop that re-arms
    a fresh budget every cycle (the 2026-08-06 storm). The service
    manager (Restart=always) starts the fresh heap."""
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(0)


# ---- present-moment continuity (PresentMoment.lean) ------------------------
# The working self — last results, energy, idle pacing, rhythm phase, and
# any stated rest intention — is snapshotted at every turn boundary
# (persist) and restored at boot (restore), so a recycle or restart is
# sleep, not a fresh boot. The continuity invariant: restore ∘ persist =
# identity on this projection, within the feedback window.

_WORKING_CAP = 200000  # mirrors (maxFeedback)
_working_boot_cache = {}


def _working_set_path():
    return os.environ.get("METTACLAW_WORKING_SET_PATH",
                          "./memory/working_set.json")


def _working_read():
    if os.environ.get("METTACLAW_OBSERVED_STRATEGY", "0") == "1":
        import agent_checkpoint
        checkpoint = agent_checkpoint.ledger().working_checkpoint()
        if checkpoint is not None:
            return checkpoint
    try:
        with open(_working_set_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


_turn_commit = None


def turn_commit_reset():
    global _turn_commit
    _turn_commit = None
    return 1


def turn_commit_stage(provider_succeeded, history_saved, has_activity):
    global _turn_commit
    if int(provider_succeeded) == 1 and int(history_saved) == 1:
        import telegram
        import strategy_host
        _turn_commit = (tuple(telegram._current_batch_update_ids) if int(has_activity) else (),
                        strategy_host._message_focus if int(has_activity) else None)
    else:
        _turn_commit = None
    return 1


def turn_commit_ready():
    return int(_turn_commit is not None)


def _working_write(data, settle=False):
    committed = False
    if os.environ.get("METTACLAW_OBSERVED_STRATEGY", "0") == "1":
        import agent_checkpoint
        ids, request = _turn_commit if settle and _turn_commit is not None else ((), None)
        agent_checkpoint.ledger().checkpoint(data, ids, request)
        committed = True
    path = _working_set_path()
    parent = os.path.dirname(path) or "."
    tmp = None
    try:
        os.makedirs(parent, exist_ok=True)
        descriptor, tmp = tempfile.mkstemp(prefix=".working-set-", dir=parent)
        with os.fdopen(descriptor, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        directory = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        if not committed:
            raise
        # The file is an operator projection. Recovery uses the committed DB.
    finally:
        if tmp is not None and os.path.exists(tmp):
            os.unlink(tmp)


def working_set_save(loops, sleep, lastresults, hb_clock,
                     continuation_pending=0, banked_loops=0):
    """persist: snapshot the working self at a turn boundary.

    ``banked_loops`` is the budget a rest set aside; it belongs to the
    working self exactly like the live budget does, so a restart during a
    rest wakes owing the same energy rather than none."""
    try:
        data = _working_read()
        data.update({
            "loops": int(loops),
            "sleepInterval": int(sleep),
            "lastresults": str(lastresults)[-_WORKING_CAP:],
            "last_heartbeat": float(hb_clock),
            "continuation_pending": bool(int(continuation_pending)),
            "banked_loops": max(0, int(banked_loops)),
            "saved_at": time.time(),
        })
        _working_write(data, settle=True)
        import process_supervision
        process_supervision.progress("turn-checkpoint")
        return 1
    except Exception:
        return 0


def rest_schedule(seconds, command):
    """Persist the one rest continuation alongside the existing working set."""
    data = _working_read()
    data['rest_timer'] = dict(deadline=time.time()+max(0,float(seconds)),
                              command=command, status='waiting')
    _working_write(data)
    return 1


def rest_cancel():
    data = _working_read()
    data.pop('rest_timer', None)
    _working_write(data)
    return 1


def rest_pending():
    timer = _working_read().get('rest_timer', {})
    if timer.get('status') == 'waiting':
        return ['continuation-value', timer['command']]
    return 'no-continuation'


def rest_due(timed_out):
    timer = _working_read().get('rest_timer')
    return int(timer['status'] == 'waiting' and time.time() >= timer['deadline']) if timer else int(timed_out)


def rest_claim():
    data = _working_read()
    timer = data.get('rest_timer')
    if timer:
        timer['status'] = 'running'
        _working_write(data)
    return 1


def rest_finish():
    return rest_cancel()


def wait_seconds(requested, last_heartbeat, interval):
    """Wake for the earlier timer or life heartbeat without firing a timer early."""
    now = time.time()
    deadlines = [max(0, float(requested))]
    if float(interval) > 0:
        deadlines.append(max(0, float(last_heartbeat)+float(interval)-now))
    timer = _working_read().get('rest_timer', {})
    if timer.get('status') == 'waiting':
        deadlines.append(max(0, timer['deadline']-now))
    import math
    return math.ceil(min(deadlines))


def intent_save(why):
    """Store the agent's stated rest intention; it greets the next
    waking (WOKE_FROM_REST) and survives any boundary in between."""
    try:
        data = _working_read()
        data["intent"] = str(why)[:500]
        _working_write(data)
        return 1
    except Exception:
        return 0


def working_boot():
    """restore: the NEW process loads the snapshot, prefixes the waking
    lastresults with a continuity marker (plus the consumed rest
    intention when one was stored), and clears the intention so it fires
    once. First-ever boot (no snapshot) leaves fresh-start defaults."""
    global _working_boot_cache
    data = _working_read()
    cache = dict(data)
    if data:
        intent = data.pop("intent", None)
        cache.pop("intent", None)
        marker = "CONTINUITY: resumed " + time.strftime("%Y-%m-%d %H:%M:%S")
        if intent:
            marker += " | WOKE_FROM_REST: " + str(intent)
        timer = data.get('rest_timer', {})
        if timer.get('status') == 'waiting':
            import math
            cache['sleepInterval'] = max(0, math.ceil(timer['deadline']-time.time()))
        elif timer.get('status') == 'running':
            marker += ' | SCHEDULED_COMMAND_INTERRUPTED: outcome unknown; not replayed: ' + repr(timer.get('command'))
            data.pop('rest_timer', None)
            _working_write(data)
        prior = str(cache.get("lastresults", ""))
        # The marker lives only in the waking view; the snapshot keeps
        # pristine content so markers never stack across boots.
        cache["lastresults"] = (marker + "\n" + prior)[:_WORKING_CAP] \
            if prior else marker
        if intent is not None:
            try:
                _working_write(data)  # consume the intention on disk
            except OSError:
                pass
    _working_boot_cache = cache
    return 1


def boot_str(key, default):
    v = _working_boot_cache.get(str(key))
    return str(v) if v is not None else str(default)


def boot_int(key, default):
    v = _working_boot_cache.get(str(key))
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(default)
        except (TypeError, ValueError):
            return 0


def boot_num(key, default):
    v = _working_boot_cache.get(str(key))
    try:
        return float(v)
    except (TypeError, ValueError):
        try:
            return float(default)
        except (TypeError, ValueError):
            return 0.0



# ---- salience -------------------------------------------------------------
# An identical block reprinted every turn says nothing; the same block
# labelled "unchanged for 40 turns" is a signal — and it is exactly the
# signal that was missing on the night nothing moved. Codex diffs its
# context sections to save tokens; here the point is attention, not size.
# Held in memory only: a restart resets the streak to zero, which is honest,
# because after a boundary the agent genuinely has not been watching.

_change_marks = {}


def change_mark(name, text):
    """Return a short suffix describing how long this block has stood."""
    try:
        key = str(name)
        digest = hashlib.sha1(str(text).encode("utf-8", "replace")).hexdigest()
        previous, streak = _change_marks.get(key, (None, 0))
        if previous == digest:
            _change_marks[key] = (digest, streak + 1)
            n = streak + 1
            return " (unchanged for %d turn%s)" % (n, "" if n == 1 else "s")
        _change_marks[key] = (digest, 0)
        return "" if previous is None else " (changed)"
    except Exception:
        return ""


# ---- the running action window ------------------------------------------
# Contract (mirrors vericore's Verus heartbeat_context_policy, which solved
# this once already):
#   recent turns within limit        count <= limit
#   newest turn preserved            present -> included, always
#   no-op turns excluded             a (nop) must not consume a recency slot
#   oldest-first ordering            the window reads forwards in time
# The agent forgets what its own hands did after a single turn, which is how
# it spends bursts rediscovering files it wrote itself. This is the cheap
# half of the fix: keep the COMMANDS, drop the outputs — the outputs are
# bulky and already acted on, the commands are small and are what is missed.

_ACTION_TAIL_BYTES = 262144
_NOISE = ("(nop)", "(no_commands_parsed)", "()")


def _action_blocks(path):
    """Turn blocks from the journal, oldest last. Never raises."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - _ACTION_TAIL_BYTES))
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        return []
    blocks, current = [], None
    for line in text.splitlines():
        if line.startswith('("'):
            if current is not None:
                blocks.append(current)
            current = [line]
        elif current is not None:
            current.append(line)
    if current is not None:
        blocks.append(current)
    return blocks


def _commands_of(block):
    """The command s-expression a turn issued, without the inbound message.

    A block is `<time>` then an optional `HUMAN_MESSAGE: <envelope>` then the
    response. Only the response belongs here: the message is already carried,
    in better form, by the conversation window."""
    for index, line in enumerate(block[1:], 1):
        if line.lstrip().startswith("(("):
            return "\n".join(block[index:]).strip()
    return ""


def _is_noise(commands):
    """A turn that issued nothing worth a slot in the window."""
    if not commands:
        return True
    if len(commands) < 40 and any(part in commands for part in _NOISE):
        return True
    return not commands.replace("(", "").replace(")", "").strip()


def recent_actions(path, limit=8, max_chars=12000):
    """The last `limit` substantive proposals, oldest first, within max_chars.

    The newest substantive turn is never dropped: if it alone exceeds the
    budget it is truncated rather than omitted, because a window that
    silently loses the most recent proposal is worse than a short one.  This
    transcript is not evidence that any proposed command executed; broker
    receipts carry that distinction."""
    try:
        limit = max(1, int(limit))
        budget = max(500, int(max_chars))
        blocks = []
        for block in _action_blocks(str(path)):
            commands = _commands_of(block)
            if not _is_noise(commands):
                blocks.append("%s %s" % (block[0].strip(), commands))
        if not blocks:
            return "(no prior actions recorded)"
        chosen, used = [], 0
        for rendered in reversed(blocks[-limit:]):
            if not chosen and len(rendered) > budget:
                rendered = rendered[:budget - 1] + "…"
            cost = len(rendered) + 2
            if chosen and used + cost > budget:
                break
            chosen.append(rendered)
            used += cost
        chosen.reverse()
        return "\n\n".join(chosen)
    except Exception as exc:
        print("[helper] recent_actions error:", type(exc).__name__, exc)
        return "(proposal window unavailable)"


def relevant_files(path, limit=8, max_paths=25):
    """Paths the agent has touched recently, most recent first.

    Derived from the action window rather than maintained, so it needs no
    discipline to stay true and decays on its own as actions age out."""
    try:
        window = recent_actions(path, limit=limit, max_chars=60000)
        seen = []
        pattern = r"(?<![\w/.])((?:~/|\./|/)?(?:[\w.@+-]+/)+[\w.@+-]+)"
        for match in re.finditer(pattern, window):
            candidate = match.group(1).rstrip(".,;:'\"")
            if len(candidate) < 6 or "/" not in candidate:
                continue
            if candidate in ("/dev/null", "/tmp"):
                continue
            if candidate.startswith(("http", "//")) or candidate.count("/") > 12:
                continue
            # prose is full of word/word; a path is rooted, or lives under a
            # known tree, or its last segment carries a suffix.
            rooted = candidate.startswith(("/", "./", "~/"))
            known = candidate.startswith((
                "src/", "tests/", "memory/", "channels/", "scripts/",
                "packages/", "lean/", "repos/", "docs/"))
            suffixed = "." in candidate.rsplit("/", 1)[-1]
            if not (rooted or known or suffixed):
                continue
            if candidate not in seen:
                seen.append(candidate)
        seen.reverse()
        return " ".join(seen[:max(1, int(max_paths))]) or "(none)"
    except Exception as exc:
        print("[helper] relevant_files error:", type(exc).__name__)
        return "(unavailable)"


def history_append(addition):
    """Append one internal turn to the configured conversation transcript.

    This deliberately does not share the public ``append-file`` skill.  That
    skill may stage protected source mutations; the runtime journal is an
    observation sink, not a self-modification request.  The destination is
    fixed by deployment configuration, so callers cannot turn this helper
    into general file access.
    """
    path = os.environ.get("METTACLAW_HISTORY_PATH", "./memory/history.metta")
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        payload = normalize_string(addition)
        if not payload.endswith("\n"):
            payload += "\n"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                             0o600)
        try:
            with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
        return 1
    except Exception as exc:
        print("[helper] history append error:", type(exc).__name__)
        return 0


def local_clock(epoch):
    """Telegram's own send time, local, in the same format as TIME."""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(float(epoch))))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def pad20(number):
    return "%020d" % int(number)


def rest_notice_text(left):
    """The one notice an operator gets when writing to a resting agent."""
    left = max(0, int(float(left)))
    wake_at = time.strftime("%H:%M", time.localtime(time.time() + left))
    mins, secs = left // 60, left % 60
    span = ("%dm%02ds" % (mins, secs)) if mins else ("%ds" % secs)
    return ("\U0001F4A4 resting — wakes in %s (at %s). Your message is queued "
            "and will be read then; send /wake to end the rest now." % (span, wake_at))


def adopt_active_engine():
    """The command responder runs beside the loop: the engine that loop
    runs is the one it published, not this process's own start value."""
    import telegram
    active = telegram.metta_value("active_engine", "")
    if active:
        os.environ["METTACLAW_ACTIVE_ENGINE"] = str(active)
    return 1


def claude_code_toggle():
    import claude_bridge
    return claude_bridge.set_authorized(not claude_bridge.authorized())


def menu_state(kind):
    """What one operator settings menu offers, as JSON for the command
    responder, which lays out the buttons: the choices and which are
    current, selected or unavailable. Read locally, never from a provider."""
    if kind == "modes":
        import loop_modes
        state = {"text": loop_modes.mode_view(), "current": loop_modes.active_mode(),
                 "selected": loop_modes.current_mode(),
                 "names": list(loop_modes.MODES)}
    elif kind == "fuels":
        import fuel_modes
        state = {"text": fuel_modes.fuels_view(), "current": fuel_modes.current_fuel(),
                 "names": list(fuel_modes.FUELS)}
    elif kind == "engines":
        adopt_active_engine()
        import engine_modes
        state = {"text": engine_modes.engines_view(), "current": engine_modes.active_engine(),
                 "selected": engine_modes.selected_engine(), "names": list(engine_modes.ENGINES),
                 "unavailable": [n for n in engine_modes.ENGINES
                                 if not engine_modes.engine_available(n)]}
    elif kind == "models":
        import synthetic_llm
        names = synthetic_llm.model_ids()
        state = {"text": "tap to switch — " + synthetic_llm.model_catalog_status(),
                 "current": synthetic_llm.current_model(), "names": names,
                 "unavailable": [n for n in names
                                 if synthetic_llm.model_in_observed_catalog(n) is False]}
    else:
        state = {"text": "", "current": "", "names": []}
    return json.dumps(state, ensure_ascii=False)


def str_env(name, default=""):
    return os.environ.get(str(name), str(default))


def complete_command_prefix(text):
    """A length-cut response may contain complete calls before its cut tail."""
    text = str(text).strip()
    forms, complete = _top_level_forms(text)
    if complete:
        return text
    # For a command-list envelope, its children can be complete even when
    # the enclosing list and its final child were cut by the provider.
    children, _ = _top_level_forms(text[1:] if text.startswith("((") else text)
    print("[model-response] output truncated; retained %d complete calls" % len(children))
    return "(" + " ".join(children) + ")"
