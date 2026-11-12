"""MeTTa adapters for the protected managed strategy.

Strict standalone modes do not pass through these hooks. Managed cognition
uses the existing provider, budgets, Iter preparation and interruptible wait.
"""

import json
import os
from pathlib import Path
import re
import sys
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
_context_preparation = None
# The calls the renderer carried with their results in the prompt it is preparing, as (ticket, position);
# bound to that prompt's digest by context_prepared, kept by capture for the exact prompt sent, and marked
# observed once the model's answer to it is recorded (broker_begin).
_context_presented = None
_turn_input_ids = None

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
    # Goal accounts belonged to the retired rotating-goal scheduler; the native ledger has none,
    # so no goal cycle is open to close.
    return 0


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


def begin(turn, source, message, delivery_ids=None):
    global _turn, _sample, _ticket, _position, _focus, _input_turn, _message_focus, _runtime_projection, _context_trigger, _task_projection, _context_preparation, _context_presented
    global _turn_input_ids
    if delivery_ids is None:
        import telegram
        captured_ids = tuple(sorted(str(i) for i in telegram._current_batch_update_ids))
    else:
        captured_ids = json.loads(str(delivery_ids))
        if not isinstance(captured_ids, list) or any(type(i) is not str or not i for i in captured_ids):
            raise ValueError("input scope requires nonempty delivery identities")
        captured_ids = tuple(sorted(set(captured_ids)))
    _turn_input_ids = captured_ids
    if not enabled():
        return 1
    with _lock:
        _turn, _sample, _ticket, _position = str(turn), None, None, 0
        _runtime_projection = None
        _context_preparation = None
        _context_presented = None
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
            import input_ack
            state = store.status_of(previous)
            state = None if state is None else (state,)
            if state and state[0] == "addressed":
                _focus = previous
        _context_trigger, _task_projection = str(source), None
        import loop_modes
        import lifecycle
        import telegram
        store.control(mode=loop_modes.process_mode(), paused=not lifecycle.cognition_enabled(),
                      router_revision=guard())
        if str(message).strip() and str(source) in ("operator-event", "channel-event"):
            ids = list(captured_ids)
            if not ids:
                raise ValueError("input delivery identity was not prepared by its transport")
            _focus = "input:" + strategy.digest([os.environ.get("METTACLAW_INSTANCE", "default"), ids])
            _message_focus = _focus
            try:
                store.accept(_focus, str(message), kind="message" if str(source) == "operator-event" else "notification")
                store.link_inputs(_focus, ids)
            except strategy.IdentityConflict:
                import cognitive_health
                cognitive_health.turn_failed("input-identity-conflict")
                raise
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
    global _focus, _runtime_projection, _task_projection, _context_presented
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
    view = task_context.conversation_projection(ledger(), _focus, _context_trigger)
    import context_budget
    capacity = context_capacity(output_tokens)
    available = context_budget.available(prompt, capacity)
    view = task_context.compact(view, available, measure=context_budget.tokens)
    _task_projection = "TASK_CONTEXT " + strategy.encoded(view)
    rendered = prompt + "\n\n" + _task_projection
    import hashlib
    with _lock:
        _context_presented = presented_calls(view.get("transcript", []))
    context_prepared(hashlib.sha256(rendered.encode()).hexdigest(), int(bool(view.get("archive"))))
    return rendered


def lifetime_context():
    """Native call-lifetime evidence for MeTTa's own context assembly, which
    appends the runtime facts itself; empty unless the transport is native."""
    if not enabled() or os.environ.get("METTACLAW_LLM_TRANSPORT") != "native":
        return ""
    import native_lifetime
    native_lifetime.notify()
    return "\n" + native_lifetime.evidence_context()


def context_input(key, output_tokens=32768):
    """Read host metadata for MeTTa's native context policy."""
    if key == "enabled":
        return enabled_flag()
    if key == "focus":
        return _focus or "autonomous"
    if key == "trigger":
        return _context_trigger
    if key == "capacity":
        capacity = context_capacity(output_tokens)
        return "" if capacity is None else str(capacity)
    if key == "model":
        import synthetic_llm
        return str(synthetic_llm.current_model())
    raise KeyError(key)


def context_capacity(output_tokens=32768):
    """Input tokens a request may fill: the model's window less its output.

    Providers report the window; a configured one never exceeds it."""
    import synthetic_llm
    model = synthetic_llm.current_model()
    catalog = "kimi-models" if str(model).startswith("kimi:") else "models"
    rows = synthetic_llm._cached_observation(catalog).get("value")
    rows = rows if isinstance(rows,list) else []
    reported = next((r.get("context") for r in rows if isinstance(r,dict) and r.get("id") == model), None)
    if reported is None:
        metadata = synthetic_llm._cached_observation("model-window").get("value") or {}
        if isinstance(metadata,dict) and metadata.get("id") == model:
            reported = metadata.get("context")
        synthetic_llm.refresh_provider_observations_async(
            ("models",) if catalog == "kimi-models" else ("model-window",))
    configured = os.environ.get("METTACLAW_CONTEXT_WINDOW_TOKENS")
    windows = [int(value) for value in (configured, reported) if value]
    if not windows:
        return None
    return max(0, min(windows) - int(output_tokens))


def conversation_mode():
    return "chat" if not enabled() or _context_trigger == "operator-event" else "task"


def task_read(identity, offset=0):
    import task_context
    try:
        return strategy.encoded(task_context.read(ledger(), str(identity), int(offset)))
    except (ValueError, TypeError) as error:
        return "TASK_READ_FAILED: " + str(error)


def task_history(request, offset=0, count=20):
    import task_context
    return strategy.encoded(task_context.history(ledger(), str(request), int(offset), int(count)))


def task_note(text, sources="[]"):
    import task_context
    return task_context.note(ledger(), _focus, str(text), str(sources))


def description():
    return ("TASK_CONTEXT contains the request, earlier calls and their results. Resume the task after interruptions. "
            "task-history and task-read retrieve older evidence; task-note is an optional scratchpad. "
            "No disposition or resolution bookkeeping is required. context-policy adjusts the task context target. ")


def context_presented(calls):
    """The renderer's list of the calls it is carrying with their results, a JSON array of call ids
    (owner:serial:position); an empty array starts a preparation."""
    global _context_presented
    found = []
    for identity in json.loads(str(calls)):
        ticket, separator, position = str(identity).rpartition(":")
        if separator and ticket and position.isdigit():
            found.append((ticket, int(position)))
    with _lock:
        _context_presented = tuple(found)
    return 1


def presented_calls(rows):
    """The calls among transcript rows that carry their result: (ticket, position) of each row whose id is a
    call id and whose disposition is not not_run or uncertain. A cut, repeated or collapsed result still
    carries its content."""
    found = []
    for row in rows:
        identity = str(row.get("id", ""))
        ticket, separator, position = identity.rpartition(":")
        if (separator and ticket and position.isdigit() and not identity.startswith("task-input:")
                and row.get("disposition") not in ("not_run", "uncertain", "input")):
            found.append((ticket, int(position)))
    return tuple(found)


def context_prepared(prompt_digest, incomplete):
    """A renderer-owned coverage receipt, never inferred from prompt prose; it binds the calls the
    renderer reported carrying to this prompt."""
    global _context_preparation, _context_presented
    digest = str(prompt_digest)
    value = int(incomplete)
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest) or value not in (0, 1):
        raise ValueError("invalid prepared context receipt")
    with _lock:
        _context_preparation = (digest, bool(value), _context_presented or ())
        _context_presented = None
    return 1


def capture(prompt, wake_seconds):
    global _sample, _context_preparation
    if not enabled():
        return 1
    with _lock:
        import lifecycle
        import hashlib
        ledger().control(paused=not lifecycle.cognition_enabled())
        _sample = ledger().capture(_focus, str(prompt), {}, time.time())
        if _sample is not None:
            observed = hashlib.sha256(str(prompt).encode()).hexdigest()
            receipt = _context_preparation
            exact = receipt is not None and receipt[0] == observed
            _sample["context_incomplete"] = receipt[1] if exact else True
            # Only calls the renderer carried in this exact prompt; marked once its answer is recorded.
            _sample["presented"] = receipt[2] if exact else ()
        _context_preparation = None
        return int(_sample is not None)


def provider_admitted(ticket=None):
    return None


def accounting_origin():
    """Small observed wake facts, without input text or a new ledger write."""
    with _lock:
        source = _context_trigger if _turn is not None else "unspecified"
        if source not in {"operator-event", "channel-event", "heartbeat-event",
                          "autonomous-trigger", "continuation-tick", "scheduled-command"}:
            source = "unspecified"
        return dict(trigger=source, turn_id=str(_turn) if source != "unspecified" else "")


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


def preparation_failed(reason):
    """Retain this input and use the ordinary retry pacing before admission."""
    import synthetic_llm
    from admission_frontier import ContextPreparationFailure
    return synthetic_llm._empty_action(ContextPreparationFailure(reason))


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
        store = ledger()
        if not store.decision_pending(_sample["serial"]):
            _ticket = None
            _broker_feedback = {"reason": "capture-already-settled"}
            return 0
        _turn = str(turn)
        import task_context
        task_context.proposal(store, _sample["serial"], _focus, commands, "not run: batch interrupted")
        _position = 0
        if not commands:
            store.decide(_sample["serial"], {"kind": "text"}, time.time())
            _ticket = None
            _observe_answered(store)
            return 1
        _ticket = store.decide(_sample["serial"], {
            "kind": "command", "tool": "batch", "request": _focus,
            "owner": _sample["owner"], "command": {"commands": commands, "limit": len(commands)},
        }, time.time())
        if _ticket == "ignored":
            _ticket = None
            _broker_feedback = {"reason": "capture-already-settled"}
            return 0
        _broker_feedback = {"reason": "recorded"}
        _observe_answered(store)
        return 1


def _observe_answered(store):
    """The model answered the captured prompt: the results it carried have been seen. Marking never fails
    the turn: the results stay in the window, and the next answered prompt carries them again."""
    presented = _sample.pop("presented", ()) if _sample is not None else ()
    if not presented:
        return
    try:
        store.observe_presented(presented)
    except Exception as error:  # the ledger was unavailable or slow; the decision itself is recorded
        print("observe_presented failed: %s: %s" % (type(error).__name__, error), file=sys.stderr, flush=True)


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
    """Record the batch's next command as attempted before it runs (the dispatch hook)."""
    global _position
    if not enabled():
        return 1
    with _lock:
        if str(turn) != _turn or not _ticket:
            return 0
        import input_ack
        if not input_ack.attempt_begin(_ticket, _position, strategy.encoded(command)):
            import llm_policy
            raise llm_policy.IntegrityError("command attempt %s/%d is already recorded" % (_ticket, _position))
        _position += 1
        try:
            import stream_sink
            stream_sink.event("tool.dispatching", command_identity(turn), str(command), "dispatching")
        except Exception:
            pass
        return 1


def command_identity(turn):
    """The broker's occurrence, independent of equal command text."""
    with _lock:
        if enabled() and str(turn) == _turn and _ticket:
            return str(_ticket) + ":" + str(_position - 1)
    return None


def command_result(turn, disposition, command, result):
    if enabled() and str(turn) == _turn and _ticket:
        store = ledger()
        import input_ack
        outcome = strategy.encoded({"disposition": str(disposition), "result": str(result)})
        input_ack.attempt_result(_ticket, _position - 1, strategy.encoded(command), outcome)
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
    if _sample and _sample.get("context_incomplete"):
        return  # A delivered status/uncertainty message is not work closure.
    name = command[0]
    if name not in ("send", "send-telegram-chat"):
        return
    if not result.startswith("sent message "):
        return
    store = ledger()
    import input_ack
    found = store.native_request(_focus)
    if not found or found["kind"] not in ("message", "notification"):
        return
    chat, inputs = input_ack.request_context(_focus)
    frame = None if chat is None else (chat,)
    if len(inputs) != 1:
        return
    import telegram
    target = telegram._reply_target(command[1] if name == "send-telegram-chat" else "")
    if not (frame and frame[0]) or str(target) != str(frame[0]):
        return
    input_ack.request_answered(_focus)  # the answer-delivered event is not written natively
    return


def broker_end(turn, records):
    if enabled() and str(turn) == _turn and _ticket:
        store=ledger()
        store.report(_ticket, str(records))
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
    import input_ack
    if bound is None:
        newest = input_ack.attempts(recent=(2 ** 62, 1))
        if not newest:
            return strategy.encoded(page)
        bound = newest[0]["order"]
    fetched = [dict(rowid=a["order"], ticket=a["ticket"], position=a["position"], command=a["command"],
                    result=a["result"]) for a in input_ack.attempts(recent=(bound, 33))]
    rows = fetched[:32]
    older = lambda after: any(row["rowid"] <= after for row in fetched)
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
        more = older(after)
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


def view():
    if not enabled():
        return ""
    return strategy.encoded(ledger().protected(_focus))


def request_status(request):
    if not enabled():
        return "PROTECTED_STRATEGY_DISABLED"
    return strategy.encoded(ledger().request_record(str(request)))


def request_page(offset=0, limit=8):
    return strategy.encoded(ledger().request_page(int(offset), int(limit))) if enabled() else "PROTECTED_STRATEGY_DISABLED"


def request_read(request, offset):
    return strategy.encoded(ledger().request_record(str(request), int(offset))) if enabled() else "PROTECTED_STRATEGY_DISABLED"


def request_disposition(request, disposition, note):
    global _message_focus
    if not enabled():
        return "PROTECTED_STRATEGY_DISABLED"
    import helper
    store, request = ledger(), str(request)
    record = store.request_record(request)
    if record.get("status") == "unknown":
        return "REQUEST_DISPOSITION_WITHHELD: unknown ID %s; (requests) lists request IDs" % request
    try:
        if store.disposition(request, str(disposition), helper.decode_safe_text(note)):
            return "REQUEST_DISPOSITION_RECORDED"
        return "REQUEST_DISPOSITION_WITHHELD: %s (request %s)" % (store.refusal, request)
    except (ValueError, TypeError) as error:
        return "REQUEST_DISPOSITION_WITHHELD " + str(error)


def focus_request(request):
    global _focus
    request = str(request)
    if not enabled():
        return "PROTECTED_STRATEGY_DISABLED"
    available = ledger().selectable(None, request)
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
        # A requested recycle is not a failed turn; the engine mode acts on the request.
        if not engine_modes.recycle_requested():
            import cognitive_health
            cognitive_health.turn_failed("host-transition-error")
    return 1


def task_resolve(identity, reason):
    return "No resolution bookkeeping is required. Calls and results remain in task-history."


def result_permits_suffix(command, record):
    # Failure is a result for the agent to interpret, not a batch veto.
    return 1


def annotate_result(command, result):
    return result


def input_processed():
    if enabled() and _message_focus:
        ledger().processed(_message_focus)
    return 1
