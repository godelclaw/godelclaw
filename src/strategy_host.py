"""MeTTa adapters for the protected managed strategy.

Strict standalone modes do not pass through these hooks. Managed cognition
uses the existing provider, budgets, Iter preparation and interruptible wait.
"""

import json
import os
from pathlib import Path
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
_goal_view = None
_prepared_capture = None
_broker_feedback = {"reason": "no-current-capture"}

# These tools only inspect host state; their decisions commute without an
# intervening observation. Unknown tools and effects require a new model step.
INDEPENDENT_READS = frozenset({"help", "check", "vitals", "jobs", "goals",
                              "gateway-tools", "gateway-status", "llm-quota",
                              "llm-models", "energy", "modes", "tmux-windows",
                              "requests", "request-status"})


def enabled():
    return os.environ.get("METTACLAW_OBSERVED_STRATEGY", "0") == "1"


def enabled_flag():
    return int(enabled())


def fair_enabled():
    return enabled() and os.environ.get("METTACLAW_FAIR_GOALS", "0") == "1"


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


def guard():
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
        "goal_inventory": _file_identity(goals._goals_path()) if fair_enabled() else None,
    })


def _stimulus_free():
    import effect_frontier
    if effect_frontier.selected_backend() == "tmux-shadow":
        import effect_backend
        return effect_backend.turn_stimulus_free(_turn) == 1
    import telegram
    if telegram.metta_client():
        unread = telegram._metta_unread_chats()
        return unread is not None and not unread
    return telegram.effect_turn_stimulus_free(_turn) == 1


def begin(turn, source, message):
    global _turn, _sample, _ticket, _position, _focus, _input_turn, _goal_view, _prepared_capture
    if not enabled():
        return 1
    with _lock:
        _turn, _sample, _ticket, _position = str(turn), None, None, 0
        _goal_view, _prepared_capture = None, None
        # A multi-action foreground request keeps its observation/reconsider
        # steps. Autonomous scheduling resumes at the loop's existing wake
        # boundary, not merely because the next step has no new message.
        _input_turn = bool(str(message).strip() and str(source) in ("operator-event", "channel-event")) or (
            _input_turn and str(source) == "continuation-tick")
        store = ledger()
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
            store.accept(_focus, str(message))
        elif _focus is None:
            active = store.active()
            if active:
                _focus = active[-1]["id"]
            else:
                _focus = "autonomous"
                store.accept(_focus, "Ongoing autonomous work; rest and nop do not certify task completion.")
        return 1


def prepare(prompt):
    """Append protected work/results after replaceable transformations."""
    if not enabled():
        return str(prompt)
    global _focus, _goal_view
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
    return str(prompt) + "\n\nPROTECTED_STRATEGY " + payload + (
        "\nTask completion requires a configured checker and an observed successful receipt. "
        "nop, rest, advertisements, proposals and provider text do not complete work. "
        "Searches with incomplete coverage or stale scope cannot establish absence. "
        "Use one action unless all commands are independent host observations. "
        "Write commands as a list, for example ((pin \"note\") (send \"message\")). "
        "A withheld command did not run: inspect its reason and reconsider. "
        "Do not claim a repair or saved rule succeeded until its successful tool receipt is observed. "
        "Distinguish observed facts from causal or motive hypotheses. "
        "Outgoing prose uses ordinary apostrophes; do not imitate history escape tokens."
    )


def capture(prompt, wake_seconds):
    global _sample, _focus, _prepared_capture
    if not enabled():
        return 1
    with _lock:
        import skills_help
        store = ledger()
        projection = store.protected(_focus)
        # Observe only the exact projection that actually survived preparation.
        if strategy.encoded(projection) not in str(prompt):
            return 0
        if fair_enabled() and (_goal_view is None or
                "FAIR_GOALS " + strategy.encoded(_goal_projection()) not in str(prompt)):
            return 0
        presented = {r["ticket"]: [r] for r in projection["results"]}
        store.observe(presented, presented)
        store.finish_exact_results()
        if fair_enabled():
            import fair_goals
            fair_goals.finish_reviews(store, time.time())
        if not any(r["id"] == _focus for r in store.active()):
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


def provider_admitted():
    global _sample, _prepared_capture
    if not fair_enabled() or _prepared_capture is None:
        return
    with _lock:
        proposed = _prepared_capture
        _prepared_capture = None
        goal_snapshot()  # recheck source edits and newly eligible goals
        if proposed["guard"] != guard() or not _stimulus_free():
            raise RuntimeError("strategy changed before provider admission")
        _sample = ledger().capture(now=time.time(), **proposed)
        if _sample is None:
            raise RuntimeError("strategy not ready for provider admission")


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
    names = [str(c[0]) if isinstance(c, (list, tuple)) and c else "" for c in commands]
    return max(1, int(requested)) if names and all(t in INDEPENDENT_READS for t in names) else 1


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
        if not commands:
            ledger().decide(_sample["serial"], {"kind": "text"}, time.time(), guard())
            return 1
        import skills_help
        router = _sample["router"]
        for command in commands:
            if not isinstance(command, (list, tuple)) or not command:
                _broker_feedback = {"reason": "malformed-command"}
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
                if bounds:
                    _broker_feedback["arguments"] = list(bounds)
                ledger().decide(_sample["serial"], {"kind": "denied", "tool": name,
                    "request": _focus, "owner": _sample["owner"]}, time.time(), guard())
                return 0
        live_guard = guard() if _stimulus_free() else "new-input"
        # Batch authority is the intersection of every command's authority.
        _ticket = ledger().decide(_sample["serial"], {
            "kind": "command", "tool": str(commands[0][0]),
            "request": _focus, "owner": _sample["owner"],
            "command": {"commands": commands, "limit": int(limit)},
        }, time.time(), live_guard)
        _position = 0
        if _ticket in ("stale", "denied", "ignored", "failure", "text"):
            _broker_feedback = {"reason": _ticket}
            _ticket = None
            return 0
        _broker_feedback = {"reason": "admitted", "limit": int(limit)}
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
    return 1


def broker_end(turn, records):
    if enabled() and str(turn) == _turn and _ticket:
        ledger().report(_ticket, str(records))
    return records


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
    return strategy.encoded(ledger().protected(str(request)))


def focus_request(request):
    global _focus
    request = str(request)
    if not enabled() or not any(r["id"] == request for r in ledger().active()):
        return "REQUEST_FOCUS_WITHHELD_UNKNOWN_OR_FINISHED"
    _focus = request
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
    global _sample, _ticket, _prepared_capture
    if enabled():
        ledger().restore()
        ledger().recover_recorded_actions()
        _sample = None
        _ticket = None
        _prepared_capture = None
        import cognitive_health
        cognitive_health.turn_failed("host-transition-error")
    return 1
