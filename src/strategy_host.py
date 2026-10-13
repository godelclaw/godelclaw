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
import admission_frontier


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
_goal_view = None
_prepared_capture = None
_runtime_projection = None
_broker_feedback = {"reason": "no-current-capture"}
_context_trigger = "continuation-tick"
_task_projection = None

def enabled():
    return os.environ.get("METTACLAW_OBSERVED_STRATEGY", "0") == "1"


def enabled_flag():
    return int(enabled())


def fair_enabled():
    return enabled() and os.environ.get("METTACLAW_FAIR_GOALS", "0") == "1"


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


def goal_snapshot():
    import fair_goals
    import goals
    store = ledger()
    now = time.time()
    fair_goals.synchronize(store, fair_goals.definitions(goals._goals_path()), now)
    return fair_goals.snapshot(store, now)


def _goal_projection():
    return dict(inventory_digest=_goal_view["inventory_digest"],
                selected=_goal_view["selected"] if not _input_turn else None,
                queue=_goal_view["queue"], input_preempts=_input_turn)


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


def _file_identity(path):
    if not path:
        return "unset"
    try:
        value = Path(path)
        stat = value.stat()
        return [stat.st_mtime_ns, stat.st_ino, strategy.digest(value.read_bytes().hex())]
    except FileNotFoundError:
        return "missing"


def guard(include_topic=True):
    import lifecycle
    import loop_modes
    import skills_help
    import goals
    return strategy.digest({
        "mode": loop_modes.process_mode(),
        "selection": _file_identity(loop_modes._path()),
        "lifecycle": _file_identity(str(lifecycle._path())),
        "running": lifecycle.cognition_enabled(),
        "router": skills_help.ARITY,
        "permission": os.environ.get("METTACLAW_PERMITTED_TOOLS", "*"),
        "implementation": _file_identity(str(Path(__file__).with_name("skills.pl"))),
        "admission": _file_identity(str(Path(__file__).with_name("admission_frontier.py"))),
        "goal_inventory": _file_identity(goals._goals_path()) if fair_enabled() else None,
        "attention": __import__("attention").revision(),
        "topic": __import__("topic_jobs").foreground_revision() if enabled() and include_topic else None,
    })


def _stimulus_free():
    import effect_frontier
    if effect_frontier.selected_backend() == "tmux-shadow":
        import effect_backend
        return effect_backend.turn_stimulus_free(_turn) == 1
    import telegram
    if telegram.metta_client():
        unread = telegram._metta_unread_chats()
        import attention
        if attention.enabled():
            return unread is not None and attention.revision() == telegram._effect_attention_revision
        return unread is not None and not unread
    return telegram.effect_turn_stimulus_free(_turn) == 1


def begin(turn, source, message):
    global _turn, _sample, _ticket, _position, _focus, _input_turn, _message_focus, _goal_view, _prepared_capture, _runtime_projection, _context_trigger, _task_projection
    if not enabled():
        return 1
    with _lock:
        _turn, _sample, _ticket, _position = str(turn), None, None, 0
        _goal_view, _prepared_capture = None, None
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
        _focus = task_context.resume(store, previous)
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
                    previous = arrivals[0]["task"]
        elif _focus is None:
            active = store.active()
            if active:
                _focus = active[-1]["id"]
            else:
                _focus = "autonomous"
                store.accept(_focus, "Ongoing autonomous work; rest and nop do not certify task completion.")
        task_context.begin(store, _focus, source, previous,
                           getattr(telegram, "_reply_chat_id", ""))
        return 1


def prepare(prompt, output_tokens=32768):
    """Append protected work/results after replaceable transformations."""
    if not enabled():
        return str(prompt)
    global _focus, _goal_view, _runtime_projection, _task_projection
    import topic_jobs
    prompt = str(prompt) + "\n" + topic_jobs.context()
    import llm_policy
    prompt += "\n" + llm_policy.unknown_context()
    topic_jobs.notify()
    if os.environ.get("METTACLAW_LLM_TRANSPORT") == "native":
        import native_lifetime
        native_lifetime.notify()
        prompt += "\n" + native_lifetime.evidence_context()
    import runtime_context
    _runtime_projection = runtime_context.render()
    prompt += "\n" + _runtime_projection
    if fair_enabled():
        import fair_goals
        _goal_view = goal_snapshot()
        if not _input_turn:
            focus = fair_goals.accept_selected(ledger(), _goal_view)
            if focus is not None:
                _focus = focus
            elif _focus is None or _focus.startswith("goal:"):
                active = [r for r in ledger().active() if not r["id"].startswith("goal:")]
                if active:
                    _focus = active[-1]["id"]
                else:
                    _focus = "autonomous"
                    ledger().accept(_focus, "Ongoing autonomous work; rest and nop do not certify task completion.")
        prompt = str(prompt) + "\nFAIR_GOALS " + strategy.encoded(_goal_projection()) + (
            "\nFor autonomous work, attend to the selected goal for this turn. First check its current "
            "constraints: dormant, waiting, completed or superseded notes do not authorize restarting work. "
            "Selection grants no new permissions. Preserve unfinished user work. A provider attempt consumes "
            "one service unit even on failure; it does not complete the goal. (goals) shows the inventory. "
            "For an inventory-review goal, save a complete review with (goal-review-save digest json-array); "
            "each entry needs goal, disposition (keep/defer/propose-close), assessment, evidence and next_action. "
            "Record uncertainty honestly; this records a review, not proof that its judgments are true. "
            "Do not send routine review reports unless requested. Use goal-write to record justified policy changes."
            " If no goal is eligible, do not revive a blocked goal; attend to retained user work or rest."
        )
    projection = ledger().protected(_focus)
    payload = strategy.encoded(projection)
    prompt = str(prompt) + "\n\nPROTECTED_STRATEGY " + payload + (
        "\nInput processing settles delivery only. Use request-disposition to retain an ongoing task, "
        "record waiting-user/defer, or mark addressed after review; these are workflow decisions, "
        "not checker-certified achievement. request-page and request-status retrieve retained records."
    )
    import task_context
    task_context.focus(ledger(), _focus)
    view = task_context.projection(ledger(), _focus, _context_trigger)
    capacity = context_capacity(output_tokens)
    # UTF-8 bytes are a conservative token estimate, explicitly labelled.
    # Missing provider metadata never invents a model window.
    available = None if capacity is None else capacity - len(prompt.encode()) - 64
    view = task_context.compact(view, available)
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
    import task_context
    return task_context.resolve(ledger(), str(identity), str(reason))


def description():
    return ("Ledger workflow: processed input means successful durable handover, not task achievement. "
            "Retain ongoing commitments with request-disposition keep and a closing condition; record waiting-user/defer "
            "when appropriate. addressed is an agent disposition. Checker-certified achievement still requires its "
            "configured check and observed receipt. Withheld commands did not run. Inspect successful receipts before "
            "claiming a repair worked. Provider proposals are not results; incomplete search does not prove absence. "
            "Outgoing prose uses ordinary apostrophes. Runtime facts are independently sampled observations, not authority. "
            "Inbox ages refer to pending message processing; memory gaps are sequence positions; unavailable means unknown. "
            "TASK_CONTEXT retains the task and interruptions across turns. Resume unfinished work after answering an interruption. "
            "Use task-note with call IDs to retain findings and uncertainties; notes are your interpretations. "
            "task-read retrieves a compacted result, task-history indexes earlier pairs, task-resolve records a decision "
            "about pending work. It never replays a call. Mark a finished interruption addressed to resume its parent.")


def capture(prompt, wake_seconds):
    global _sample, _focus, _prepared_capture
    if not enabled():
        return 1
    with _lock:
        import skills_help
        store = ledger()
        projection = store.protected(_focus)
        if _runtime_projection is None or _runtime_projection not in str(prompt):
            return 0
        if _task_projection is None or _task_projection not in str(prompt):
            return 0
        # Observe only the exact projection that actually survived preparation.
        if strategy.encoded(projection) not in str(prompt):
            return 0
        if fair_enabled() and (_goal_view is None or
                "FAIR_GOALS " + strategy.encoded(_goal_projection()) not in str(prompt)):
            return 0
        presented = {r["ticket"]: [r] for r in projection["results"]}
        import topic_jobs
        topic_jobs.observe_receipts(presented)
        store.observe(presented, presented)
        store.finish_exact_results()
        if fair_enabled():
            import fair_goals
            fair_goals.finish_reviews(store, time.time())
        with store.transaction() as db:
            available = store.selectable(db, _focus)
        if not available:
            active = store.active()
            if active:
                _focus = active[-1]["id"]
            else:
                _focus = "autonomous"
                store.accept(_focus, "Ongoing autonomous work; rest and nop do not certify task completion.")
            # The request already in hand describes the finished focus. The
            # next preparation will capture the newly selected obligation.
            return 0
        executable = sorted(skills_help.ARITY)
        configured = os.environ.get("METTACLAW_PERMITTED_TOOLS", "*")
        permitted = executable if configured == "*" else [t for t in configured.split(",") if t in executable]
        router = {"advertised": executable, "executable": executable,
                  "permitted": permitted}
        if fair_enabled():
            if _prepared_capture is not None or _sample is not None:
                return 0
            lease = None
            if not _input_turn and _goal_view and _goal_view["selected"]:
                lease = {k: _goal_view["selected"][k] for k in ("id", "round", "service")}
                lease["inventory_digest"] = _goal_view["inventory_digest"]
            _prepared_capture = dict(focus=_focus, boundary=str(prompt), router=router,
                                     guard=guard(), wake_seconds=float(wake_seconds), goal_lease=lease)
            # Both Python and native providers pass llm_policy.admit. Capture
            # and goal charging happen there, only after budget admission.
            return 1
        _sample = store.capture(_focus, str(prompt), router, time.time(), guard(), float(wake_seconds))
        return int(_sample is not None)


def provider_admitted(ticket=None):
    global _sample, _prepared_capture
    if os.environ.get("METTACLAW_TOPIC_WORKER_ID"):
        import topic_jobs
        topic_jobs.provider_admitted(os.environ["METTACLAW_TOPIC_WORKER_ID"], ticket)
        return
    if not fair_enabled() or _prepared_capture is None:
        return
    with _lock:
        proposed = _prepared_capture
        _prepared_capture = None
        goal_snapshot()  # recheck source edits and newly eligible goals
        if proposed["guard"] != guard() or not _stimulus_free():
            raise admission_frontier.AdmissionDenied("strategy-changed")
        _sample = ledger().capture(now=time.time(), **proposed)
        if _sample is None:
            code = ledger().admission_denial(proposed["focus"], time.time())
            raise admission_frontier.AdmissionDenied(code or "goal-not-selectable")


def goal_review_save(inventory_digest, entries):
    if not fair_enabled():
        return "GOAL_REVIEW_DISABLED"
    try:
        import fair_goals
        goal_snapshot()
        return fair_goals.save_review(ledger(), _ticket, str(inventory_digest), str(entries), time.time())
    except (ValueError, TypeError) as error:
        return "GOAL_REVIEW_WITHHELD " + str(error)


def normalize_response(response):
    import fair_goals
    import helper
    text = fair_goals.quote_review_json(response) if fair_enabled() else str(response)
    return helper.normalize_command_batches(text) if enabled() else text


def provider_failed():
    if enabled() and _sample is not None:
        import cognitive_health
        ledger().decide(_sample["serial"], {"kind": "failure"}, time.time(), guard(),
                        max(1, cognitive_health.retry_wait_seconds()))
    return 1


def batch_limit(requested, commands):
    if not enabled():
        return max(1, int(requested))
    return max(1, len(commands))


def batch_description(requested):
    if enabled():
        return ("Output a ((skillName args) ...) S-expression. Independent reads and host-declared "
                "independent effects may run together without a count cap. A call that needs an earlier "
                "result waits for your next observation. Failure stops the remaining proposal; every "
                "unexecuted call is retained with its reason. Scripts may express conditional follow-up "
                "work explicitly. Inspect receipts before reporting success. ")
    return "Output a ((skillName args) ...) S-expression containing at most %s command(s). " % requested


def broker_turn(turn):
    if not enabled() or _sample is None:
        return int(turn)
    return int(_sample["serial"]) + 1


def broker_begin(turn, commands, limit=1):
    global _ticket, _position, _turn, _broker_feedback
    if not enabled():
        return 1
    with _lock:
        _broker_feedback = {"reason": "no-current-capture"}
        if _sample is None or int(turn) != int(_sample["serial"]) + 1:
            return 0
        _turn = str(turn)
        import task_context
        task_context.proposal(ledger(), _sample["serial"], _focus, commands, "awaiting admission")
        if not commands:
            ledger().decide(_sample["serial"], {"kind": "text"}, time.time(), guard())
            return 1
        import skills_help
        router = _sample["router"]
        for command in commands:
            if not isinstance(command, (list, tuple)) or not command:
                _broker_feedback = {"reason": "malformed-command"}
                task_context.proposal(ledger(), _sample["serial"], _focus, commands, "malformed-command")
                provider_failed()
                return 0
            name = str(command[0])
            bounds = skills_help.ARITY.get(name)
            if (not bounds or name not in router["executable"] or name not in router["permitted"]
                    or len(command) - 1 < bounds[0]
                    or (bounds[1] is not None and len(command) - 1 > bounds[1])):
                reason = ("unknown-tool" if not bounds else
                          "tool-not-executable" if name not in router["executable"] else
                          "tool-not-permitted" if name not in router["permitted"] else
                          "wrong-arity")
                _broker_feedback = {"reason": reason, "tool": name[:80],
                                    "given_arguments": len(command) - 1}
                task_context.proposal(ledger(), _sample["serial"], _focus, commands, reason)
                if bounds:
                    _broker_feedback["arguments"] = list(bounds)
                ledger().decide(_sample["serial"], {"kind": "denied", "tool": name,
                    "request": _focus, "owner": _sample["owner"]}, time.time(), guard())
                return 0
        live_guard = guard() if _stimulus_free() else "new-input"
        store = ledger()
        with store.transaction() as db:
            for command in commands[:int(limit)]:
                denied = store.command_denial(db, command)
                if denied:
                    _broker_feedback = denied
                    break
            else:
                denied = None
        if denied:
            task_context.proposal(ledger(), _sample["serial"], _focus, commands, str(denied))
            _ticket = None
            store.decide(_sample["serial"], {"kind": "denied", "tool": str(command[0]),
                "request": _focus, "owner": _sample["owner"]}, time.time(), live_guard)
            return 0
        # Batch authority is the intersection of every command's authority.
        _ticket = ledger().decide(_sample["serial"], {
            "kind": "command", "tool": str(commands[0][0]),
            "request": _focus, "owner": _sample["owner"],
            "command": {"commands": commands, "limit": int(limit)},
        }, time.time(), live_guard)
        _position = 0
        if _ticket in ("stale", "denied", "ignored", "failure", "text", "blocked-effects"):
            task_context.proposal(ledger(), _sample["serial"], _focus, commands, str(_ticket))
            _broker_feedback = {"reason": _ticket}
            _ticket = None
            return 0
        _broker_feedback = {"reason": "admitted", "limit": int(limit)}
        task_context.proposal(ledger(), _sample["serial"], _focus, commands, "not executed; reconsider this proposal")
        return 1


def broker_feedback():
    """Explain admission without changing or granting tool authority."""
    with _lock:
        return strategy.encoded(_broker_feedback)


def reserve_command(turn, command):
    global _position
    if not enabled():
        return 1
    with _lock:
        if str(turn) != _turn or not _ticket or guard() != _sample["guard"] or not _stimulus_free():
            return 0
        store = ledger()
        with store.transaction() as db:
            if (store.get(db, "revision") != _sample["revision"] or
                    store.get(db, "mode") != _sample["mode"] or store.get(db, "paused")):
                return 0
            action = db.execute("SELECT command,status FROM actions WHERE ticket=?", (_ticket,)).fetchone()
            if not action or action["status"] != "issued":
                return 0
            prefix = admission_frontier.admitted_prefix(action["command"])
            if prefix is None or _position >= len(prefix) or prefix[_position] != command:
                return 0
            if admission_frontier.needs_observation(command, prefix[:_position]):
                return 0
            if not admission_frontier.may_issue(list(db.execute("SELECT * FROM actions")),
                    command, exclude=_ticket):
                return 0
            if store.command_denial(db, command):
                return 0
            db.execute("UPDATE command_quarantine SET allowed=0 WHERE command=?", (strategy.encoded(command),))
            db.execute("CREATE TABLE IF NOT EXISTS command_attempts (ticket TEXT, position INTEGER, command TEXT, result TEXT, PRIMARY KEY(ticket,position))")
            db.execute("INSERT INTO command_attempts VALUES (?,?,?,NULL)", (_ticket, _position, strategy.encoded(command)))
            store.event(db, "command-reserved", ticket=_ticket, position=_position)
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
            if str(disposition) == "returned":
                db.execute("UPDATE command_quarantine SET allowed=1 WHERE command=?", (strategy.encoded(command),))
        import process_supervision
        process_supervision.progress("command-receipt")
    return 1


def result_permits_suffix(command, record):
    """Interpret the dispatcher's explicit failure/result boundary."""
    if not isinstance(record, (list, tuple)) or not record or record[0] != "COMMAND_RETURN:":
        return 0
    result = record[1][1]
    import action_graph
    if not action_graph.result_permits_dependent_suffix(command, result):
        return 0
    text = str(result).strip().lower()
    if command[0].startswith("mailbox-"):
        try:
            value = json.loads(str(result))
            if isinstance(value, dict) and value.get("state") in ("refused", "disabled", "error", "cancelled"):
                return 0
        except (TypeError, ValueError):
            pass
    failures = ("read failed", "read-lines failed", "edit-file failed", "write-file failed",
                "append-file failed", "ls-tree failed", "shell_error", "timeout_error",
                "mailbox_send_failed", "mailbox_send_refused", "error(", "(error ")
    if text.startswith(failures):
        return 0
    # An exception/no-result is authoritative even when its rendered body is unfamiliar.
    if enabled() and _ticket:
        with ledger().transaction() as db:
            row = db.execute("SELECT result FROM command_attempts WHERE ticket=? AND position=?",
                             (_ticket, _position - 1)).fetchone()
        if row and row[0] and json.loads(row[0])["disposition"] != "returned":
            return 0
    return 1


def broker_end(turn, records):
    if enabled() and str(turn) == _turn and _ticket:
        ledger().report(_ticket, str(records))
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
    if not enabled() or not isinstance(command, (list, tuple)) or not command:
        return result
    if str(command[0]) in {"search", "search-tavily", "search-ddg", "grep-files", "query"}:
        # These adapters do not provide a checked exhaustive scope certificate.
        # Keep their useful results, while making absence explicitly unproved.
        return strategy.encoded({"result": str(result), "scope": command[1:],
                                 "complete_coverage": False,
                                 "absence": "unavailable"})
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
    if not enabled():
        return 0
    with ledger().transaction() as db:
        return max(0, int(ledger().get(db, "cooldown") - time.time()) + 1)


def wake(now):
    return ledger().wake(float(now)) if enabled() else 0


def autonomous_ready(scheduled):
    if not enabled() or not int(scheduled):
        return int(scheduled)
    with ledger().transaction() as db:
        deadline = ledger().get(db, "next_wake")
        return int(deadline is None or time.time() > deadline)


def fault():
    """A failed host transition preserves work and reconciles saved results."""
    global _sample, _ticket, _prepared_capture, _runtime_projection
    if enabled():
        ledger().restore()
        ledger().recover_recorded_actions()
        _sample = None
        _ticket = None
        _prepared_capture = None
        _runtime_projection = None
        import engine_modes
        if engine_modes.recycle_requested():
            with ledger().transaction() as db:
                ledger().event(db, "recycle-requested")
        else:
            import cognitive_health
            cognitive_health.turn_failed("host-transition-error")
    return 1
