"""MeTTa adapters for the protected managed strategy.

Strict standalone modes do not pass through these hooks. Managed cognition
uses the existing provider, budgets, Iter preparation and interruptible wait.
"""

import json
import os
from pathlib import Path
import re
import threading
import time

import observed_strategy as strategy


_lock = threading.RLock()
_ledger = None
_booted = False
_turn = None
_sample = None
_ticket = None
_position = 0
_focus = None
_input_turn = False
_message_focus = None
_runtime_projection = None
_broker_feedback = {"reason": "no-current-capture"}
_context_trigger = "continuation-tick"
_task_projection = None

def enabled():
    return os.environ.get("METTACLAW_OBSERVED_STRATEGY", "0") == "1"


def enabled_flag():
    return int(enabled())


def fair_enabled():
    # Goal choice belongs to the agent, not a rotating host scheduler.
    return False


def retire_goal(name, reason):
    """Close the open cycles of a goal the agent retired. Returns how many."""
    if not enabled():
        return 0
    store = ledger()
    with store.transaction() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='goal_accounts'").fetchone():
            return 0
        row = db.execute("SELECT id FROM goal_accounts WHERE name=?", (str(name),)).fetchone()
        owner = store.get(db, "owner")
    if not row:
        return 0
    return len(store.retire("goal:%s:%s:" % (owner, row[0]), "composted: " + str(reason)))








def ledger():
    global _ledger, _booted
    if _ledger is None:
        path = os.environ.get("METTACLAW_STRATEGY_PATH", "")
        if not path:
            engine = os.environ.get("METTACLAW_ENGINE_STATE_PATH", "")
            if not engine:
                raise RuntimeError("managed strategy requires an instance state directory")
            path = str(Path(engine).parent / "observed-strategy.sqlite3")
        _ledger = strategy.Ledger(path)
    if not _booted:
        _ledger.restore()
        _ledger.recover_recorded_actions()
        _booted = True
    return _ledger





def guard(include_topic=True):
    # Retained for old snapshot readers. State edits do not cancel proposals.
    return "sequential-tools-v1"


def begin(turn, source, message):
    global _turn, _sample, _ticket, _position, _focus, _input_turn, _message_focus, _runtime_projection, _context_trigger, _task_projection
    if not enabled():
        return 1
    with _lock:
        _turn, _sample, _ticket, _position = str(turn), None, None, 0
        _runtime_projection = None
        _message_focus = None
        # A multi-action foreground request keeps its observation/reconsider
        # steps. Autonomous scheduling resumes at the loop's existing wake
        # boundary, not merely because the next step has no new message.
        _input_turn = bool(str(message).strip() and str(source) in ("operator-event", "channel-event")) or (
            _input_turn and str(source) == "continuation-tick")
        store = ledger()
        import task_context
        previous = _focus or task_context.current(store)
        resume_parent = None
        _focus = task_context.resume(store, previous)
        if _focus is None and previous and str(source) not in ("operator-event", "channel-event"):
            with store.transaction() as db:
                state = db.execute("SELECT status FROM requests WHERE id=?", (previous,)).fetchone()
            if state and state[0] == "addressed":
                _focus = previous
        _context_trigger, _task_projection = str(source), None
        import loop_modes
        import lifecycle
        import telegram
        store.control(mode=loop_modes.process_mode(), paused=not lifecycle.cognition_enabled(),
                      router_revision=guard())
        if str(message).strip() and str(source) in ("operator-event", "channel-event"):
            ids = sorted(str(i) for i in telegram._current_batch_update_ids)
            if not ids:
                # A non-Telegram adapter supplies its stable event identity.
                # The message digest is a fallback only for offline adapters.
                ids = ["content:" + strategy.digest(str(message))]
            _focus = "input:" + strategy.digest([os.environ.get("METTACLAW_INSTANCE", "default"), ids])
            _message_focus = _focus
            store.accept(_focus, str(message), kind="message" if str(source) == "operator-event" else "notification")
            store.link_inputs(_focus, ids)
            import mailbox_agent
            arrivals = mailbox_agent.task_arrivals(ids)
            if arrivals:
                with store.transaction() as db:
                    for arrival in arrivals:
                        db.execute("UPDATE requests SET status='active' WHERE id=? AND status='waiting-mailbox'", (arrival["task"],))
                        store.event(db,"task-resume",**arrival)
                if len({a["task"] for a in arrivals}) == 1:
                    resume_parent = arrivals[0]["task"]
        elif _focus is None:
            _focus = "autonomous"
            store.accept(_focus, "Ongoing autonomous work; rest and nop do not certify task completion.")
        task_context.begin(store, _focus, source, previous,
                           getattr(telegram, "_reply_chat_id", ""), parent=resume_parent)
        return 1


def prepare(prompt, output_tokens=32768):
    """Append protected work/results after replaceable transformations."""
    if not enabled():
        return str(prompt)
    global _focus, _runtime_projection, _task_projection
    import topic_jobs
    prompt = str(prompt) + "\n" + topic_jobs.context()
    topic_jobs.notify()
    if os.environ.get("METTACLAW_LLM_TRANSPORT") == "native":
        import native_lifetime
        native_lifetime.notify()
        prompt += "\n" + native_lifetime.evidence_context()
    import runtime_context
    _runtime_projection = runtime_context.render()
    prompt += "\n" + _runtime_projection
    import task_context
    task_context.focus(ledger(), _focus)
    view = task_context.projection(ledger(), _focus, _context_trigger)
    import context_budget
    capacity = context_capacity(output_tokens)
    available = context_budget.available(prompt, capacity)
    view = task_context.compact(view, available, measure=context_budget.tokens)
    _task_projection = "TASK_CONTEXT " + strategy.encoded(view)
    return prompt + "\n\n" + _task_projection



def context_capacity(output_tokens=32768):
    import synthetic_llm
    configured = os.environ.get("METTACLAW_CONTEXT_WINDOW_TOKENS")
    if configured:
        window = int(configured)
    else:
        model = synthetic_llm.current_model()
        rows = synthetic_llm._cached_observation("models").get("value")
        rows = rows if isinstance(rows,list) else []
        window = next((r.get("context") for r in rows if isinstance(r,dict) and r.get("id") == model), None)
        if window is None:
            metadata = synthetic_llm._cached_observation("model-window").get("value") or {}
            if isinstance(metadata,dict) and metadata.get("id") == model:
                window = metadata.get("context")
            synthetic_llm.refresh_provider_observations_async(("model-window",))
        if window is None:
            return None
        window = int(window)
    return max(0, window - int(output_tokens))


def conversation_mode():
    return "chat" if not enabled() or _context_trigger == "operator-event" else "task"


def waiting_for_mailbox():
    if not enabled() or not _focus:
        return 0
    with ledger().transaction() as db:
        row = db.execute("SELECT status FROM requests WHERE id=?", (_focus,)).fetchone()
    return int(bool(row and row[0] == "waiting-mailbox"))


def task_read(identity, offset=0):
    import task_context
    try:
        return strategy.encoded(task_context.read(ledger(), str(identity), int(offset)))
    except (ValueError, TypeError) as error:
        return "TASK_READ_FAILED: " + str(error)


def task_history(request, offset=0):
    import task_context
    return strategy.encoded(task_context.history(ledger(), str(request), int(offset)))


def task_note(text, sources="[]"):
    import task_context
    return task_context.note(ledger(), _focus, str(text), str(sources))


def task_resolve(identity, reason):
    return "No resolution bookkeeping is required. Calls and results remain in task-history."


def description():
    return ("TASK_CONTEXT contains the request, earlier calls and their results. Resume the task after interruptions. "
            "task-history and task-read retrieve older evidence; task-note is an optional scratchpad. "
            "No disposition or resolution bookkeeping is required. context-policy adjusts the task context target. ")


def capture(prompt, wake_seconds):
    global _sample
    if not enabled():
        return 1
    with _lock:
        import lifecycle
        ledger().control(paused=not lifecycle.cognition_enabled())
        _sample = ledger().capture(_focus, str(prompt), {}, time.time())
        return int(_sample is not None)


def provider_admitted(ticket=None):
    return None


def goal_review_save(inventory_digest, entries):
    return "Goal inventory reviews are no longer required. Use goals and goal-write as needed."


def normalize_response(response):
    import helper
    return helper.normalize_command_batches(str(response)) if enabled() else str(response)


def provider_failed():
    if enabled() and _sample is not None:
        import cognitive_health
        ledger().decide(_sample["serial"], {"kind": "failure"}, time.time(), guard(),
                        max(1, cognitive_health.retry_wait_seconds()))
    return 1


def batch_limit(requested, commands):
    return len(commands)


def batch_description(requested):
    return ("Output a ((skillName args) ...) S-expression. Each call runs in order and returns its own result. "
            "Use another model step when choosing an action requires a result, or express conditional "
            "work in a script. ")


def broker_turn(turn):
    if not enabled() or _sample is None:
        return int(turn)
    return int(_sample["serial"]) + 1


def broker_begin(turn, commands, limit=1):
    global _ticket, _position, _turn, _broker_feedback
    if not enabled():
        return 1
    with _lock:
        if _sample is None or int(turn) != int(_sample["serial"]) + 1:
            _broker_feedback = {"reason": "no-current-capture"}
            return 0
        _turn = str(turn)
        import task_context
        task_context.proposal(ledger(), _sample["serial"], _focus, commands, "not run: batch interrupted")
        _position = 0
        if not commands:
            ledger().decide(_sample["serial"], {"kind": "text"}, time.time())
            _ticket = None
            return 1
        _ticket = ledger().decide(_sample["serial"], {
            "kind": "command", "tool": "batch", "request": _focus,
            "owner": _sample["owner"], "command": {"commands": commands, "limit": len(commands)},
        }, time.time())
        _broker_feedback = {"reason": "recorded"}
        return 1


def begin_continuation(turn, commands):
    """Record an agent-scheduled command without a provider round trip."""
    global _sample
    if not enabled():
        return int(turn)
    begin(turn, "scheduled-command", "")
    _sample = ledger().capture(_focus, "agent scheduled continuation", {}, time.time())
    if _sample is None:
        return int(turn)
    actual = int(_sample["serial"]) + 1
    broker_begin(actual, commands)
    return actual


def stopped():
    import lifecycle
    return int(not lifecycle.cognition_enabled())


def broker_feedback():
    """Explain admission without changing or granting tool authority."""
    with _lock:
        return strategy.encoded(_broker_feedback)


def reserve_command(turn, command):
    global _position
    if not enabled():
        return 1
    with _lock:
        if str(turn) != _turn or not _ticket:
            return 0
        store = ledger()
        with store.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS command_attempts (ticket TEXT, position INTEGER, command TEXT, result TEXT, PRIMARY KEY(ticket,position))")
            db.execute("INSERT INTO command_attempts VALUES (?,?,?,NULL)", (_ticket, _position, strategy.encoded(command)))
        _position += 1
        return 1


def command_result(turn, disposition, command, result):
    if enabled() and str(turn) == _turn and _ticket:
        store = ledger()
        with store.transaction() as db:
            db.execute("UPDATE command_attempts SET result=? WHERE ticket=? AND position=? AND command=? AND result IS NULL",
                       (strategy.encoded({"disposition": str(disposition), "result": str(result)}), _ticket,
                        _position - 1, strategy.encoded(command)))
            store.event(db, "command-result", ticket=_ticket, position=_position - 1)
        _close_answered_message(command, str(result))
        import process_supervision
        process_supervision.progress("command-receipt")
    return 1



def _close_answered_message(command, result):
    """A delivered answer settles the current arrival without a filing chore.

    This is a delivery/workflow status, never a theorem of task achievement.
    The task transcript remains available when an ongoing burst continues.
    """
    if not command or not _focus:
        return
    name = command[0]
    if name not in ("send", "send-telegram-chat", "mailbox-reply"):
        return
    if not (result.startswith("sent message ") or result.startswith("MAILED ")):
        return
    store = ledger()
    with store.transaction() as db:
        row = db.execute("SELECT c.kind,f.chat FROM request_context c LEFT JOIN task_frames f ON f.request=c.request WHERE c.request=?", (_focus,)).fetchone()
        if not row or row[0] not in ("message", "notification"):
            return
        inputs = [r[0] for r in db.execute("SELECT input FROM request_inputs WHERE request=?", (_focus,))]
        if name == "mailbox-reply":
            suffix = ":" + str(command[1])
            if not inputs or not all(i.endswith(suffix) for i in inputs):
                return
        else:
            # One reply cannot settle an arrival batch with unrelated work.
            # The frame records one chat, not a destination for every input.
            # Ambiguous batches retain their transcript and workflow status;
            # this never withholds a command or requires a filing command.
            if len(inputs) != 1 or inputs[0].startswith("mailbox:"):
                return
            import telegram
            target = telegram._reply_target(command[1] if name == "send-telegram-chat" else "")
            if not row[1] or str(target) != str(row[1]):
                return
        db.execute("UPDATE requests SET status='addressed' WHERE id=? AND status IN ('active','processed')", (_focus,))
        store.event(db,"answer-delivered",request=_focus)


def result_permits_suffix(command, record):
    # Failure is a result for the agent to interpret, not a batch veto.
    return 1


def broker_end(turn, records):
    if enabled() and str(turn) == _turn and _ticket:
        store=ledger()
        store.report(_ticket, str(records))
        import mailbox_agent
        mailbox_agent.finish_task_dispatch(store,_focus)
    return records


def command_receipts(cursor=""):
    """Bounded pages over immutable receipts, including lossless large records."""
    if not enabled():
        return "command receipts require the managed strategy"
    cursor = str(cursor)
    if cursor.startswith('"'):
        try:
            cursor = json.loads(cursor)
        except ValueError:
            pass
    if cursor and (not isinstance(cursor, str) or not re.fullmatch(r"[0-9]{1,18}:[0-9]{1,18}", cursor)):
        return strategy.encoded({"error": "invalid receipt cursor"})
    bound, offset = map(int, cursor.split(":")) if cursor else (None, 0)
    budget = 8192
    page = {"receipts": [], "next_cursor": None}
    store = ledger()
    with store.transaction() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='command_attempts'").fetchone():
            return strategy.encoded(page)
        if bound is None:
            bound = db.execute("SELECT MAX(rowid) FROM command_attempts").fetchone()[0] or 0
        rows = db.execute("SELECT rowid,ticket,position,command,result FROM command_attempts WHERE rowid<=? ORDER BY rowid DESC LIMIT 32", (bound,))
        for row in rows:
            identity = row["ticket"] + ":" + str(row["position"])
            result = row["result"] or strategy.encoded({"disposition": "uncertain",
                "reason": "no terminal receipt recorded; do not replay"})
            raw = ('{"command":' + row["command"] + ',"id":' + strategy.encoded(identity)
                   + ',"position":' + str(row["position"]) + ',"result":' + result
                   + ',"ticket":' + strategy.encoded(row["ticket"]) + '}')
            if row["rowid"] != bound:
                offset = 0
            if offset > len(raw):
                return strategy.encoded({"error": "receipt cursor beyond record"})
            after = row["rowid"] - 1
            more = db.execute("SELECT 1 FROM command_attempts WHERE rowid<=? LIMIT 1", (after,)).fetchone()
            next_cursor = str(after) + ":0" if more else None
            if not offset and len(raw.encode()) < budget - 256:
                candidate = {"receipts": page["receipts"] + [json.loads(raw)], "next_cursor": next_cursor}
                if len(strategy.encoded(candidate).encode()) <= budget:
                    page = candidate
                    continue
            if page["receipts"]:
                page["next_cursor"] = str(row["rowid"]) + ":" + str(offset)
                break
            # A large record is sliced as text. Offsets count Unicode characters;
            # the actual page limit counts encoded bytes, including JSON escapes.
            def fragment(end):
                return {"receipts": [{"id": identity, "chunk": raw[offset:end],
                    "offset": offset, "total": len(raw)}], "next_cursor":
                    str(row["rowid"]) + ":" + str(end) if end < len(raw) else next_cursor}
            low, high = offset, min(len(raw), offset + budget)
            while low < high:
                middle = (low + high + 1) // 2
                if len(strategy.encoded(fragment(middle)).encode()) <= budget:
                    low = middle
                else:
                    high = middle - 1
            page = fragment(low)
            break
    return strategy.encoded(page)


def annotate_result(command, result):
    return result


def view():
    if not enabled():
        return ""
    return strategy.encoded(ledger().protected(_focus))


def request_status(request):
    if not enabled():
        return "PROTECTED_STRATEGY_DISABLED"
    return strategy.encoded(ledger().request_record(str(request)))


def request_page(offset=0):
    return strategy.encoded(ledger().request_page(int(offset))) if enabled() else "PROTECTED_STRATEGY_DISABLED"


def request_read(request, offset):
    return strategy.encoded(ledger().request_record(str(request), int(offset))) if enabled() else "PROTECTED_STRATEGY_DISABLED"


def _request_for_ticket(store, value):
    """A mailbox ticket names the request formed from that one arrival."""
    instance = os.environ.get("METTACLAW_INSTANCE", "")
    identity = value if value.startswith("mailbox:") else "mailbox:%s:%s" % (instance, value)
    matches = store.requests_with_input(identity)
    if not matches:
        return None, ("unknown ID %s: neither a request ID nor the mailbox ticket of a recorded "
                      "request; (requests) lists request IDs" % value)
    match = matches[-1]
    if len(match["inputs"]) > 1:
        return None, ("mailbox ticket %s arrived in request %s together with %d other input(s); a "
                      "disposition applies to the whole request, so use that ID if you mean all of it"
                      % (value, match["request"], len(match["inputs"]) - 1))
    return match["request"], None


def request_disposition(request, disposition, note):
    if not enabled():
        return "PROTECTED_STRATEGY_DISABLED"
    import helper
    store, request = ledger(), str(request)
    target = request
    if store.request_record(request).get("status") == "unknown":
        target, refusal = _request_for_ticket(store, request)
        if target is None:
            return "REQUEST_DISPOSITION_WITHHELD: " + refusal
    try:
        if store.disposition(target, str(disposition), helper.decode_safe_text(note)):
            return "REQUEST_DISPOSITION_RECORDED" + (
                "" if target == request else " for request %s (mailbox ticket %s)" % (target, request))
        return "REQUEST_DISPOSITION_WITHHELD: %s (request %s)" % (store.refusal, target)
    except (ValueError, TypeError) as error:
        return "REQUEST_DISPOSITION_WITHHELD " + str(error)


def input_processed():
    if enabled() and _message_focus:
        ledger().processed(_message_focus)
    return 1


def focus_request(request):
    global _focus
    request = str(request)
    if not enabled():
        return "PROTECTED_STRATEGY_DISABLED"
    with ledger().transaction() as db:
        available = ledger().selectable(db, request)
    if not available:
        return "REQUEST_FOCUS_WITHHELD_UNKNOWN_OR_FINISHED"
    _focus = request
    import task_context
    task_context.focus(ledger(), request)
    return "REQUEST_FOCUSED " + request


def retry_seconds():
    # Provider transport retry policy lives in llm_policy/cognitive_health.
    return 0


def wake(now):
    return 0


def autonomous_ready(scheduled):
    return int(scheduled)


def fault():
    """A failed host transition preserves work and reconciles saved results."""
    global _sample, _ticket, _runtime_projection
    if enabled():
        ledger().restore()
        ledger().recover_recorded_actions()
        _sample = None
        _ticket = None
        _runtime_projection = None
        import engine_modes
        if engine_modes.recycle_requested():
            with ledger().transaction() as db:
                ledger().event(db, "recycle-requested")
        else:
            import cognitive_health
            cognitive_health.turn_failed("host-transition-error")
    return 1
