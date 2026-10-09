"""Cooperative input: receipt does not preempt; explicit steering invalidates.

The durable channel authenticates native input roles. Python ingress must pass
its own authenticated operator role. No inference of urgency from ordinary prose.
"""
import json
import os
import agent_checkpoint


def enabled():
    return (os.environ.get("METTACLAW_OBSERVED_STRATEGY") == "1" and
            os.environ.get("METTACLAW_INPUT_POLICY", "cooperative") == "cooperative")


def _store():
    store = agent_checkpoint.ledger()
    with store.transaction() as db:
        db.execute("CREATE TABLE IF NOT EXISTS attention_inputs (id TEXT PRIMARY KEY, revision INTEGER NOT NULL)")
        db.execute("INSERT OR IGNORE INTO meta VALUES ('attention_revision','0')")
    return store


def revision():
    if not enabled():
        return 0
    store = _store()
    with store.transaction() as db:
        return store.get(db, "attention_revision")


def ingress(identity, role, update):
    if not enabled() or str(role) != "operator":
        return 0
    value = json.loads(str(update)) if isinstance(update, str) else update
    if not isinstance(value, dict):
        return 0
    message = next((value.get(kind) for kind in ("message", "edited_message", "channel_post", "edited_channel_post")
                    if isinstance(value.get(kind), dict)), {})
    text = str(message.get("text") or message.get("caption") or "").strip()
    head = text.split(None, 1)[0] if text else ""
    command, _, target = head.partition("@")
    if command.lower() != "/steer":
        return 0
    if target and target.lower() != os.environ.get("METTACLAW_TELEGRAM_BOT_USERNAME", "").lstrip("@").lower():
        return 0
    identity = str(identity)
    if not identity or len(identity) > 128:
        raise ValueError("invalid steering identity")
    store = _store()
    with store.transaction() as db:
        if db.execute("SELECT 1 FROM attention_inputs WHERE id=?", (identity,)).fetchone():
            return 0
        revision = store.get(db, "attention_revision") + 1
        store.put(db, "attention_revision", revision)
        db.execute("INSERT INTO attention_inputs VALUES (?,?)", (identity, revision))
        store.event(db, "steer", input=identity, revision=revision)
    return 1


def observe_pending(tasks):
    for task, body in tasks:
        value = json.loads(body)
        if isinstance(value, list) and len(value) == 5 and value[0] == "input":
            ingress(str(task), value[3], value[4])
    return 1


def description():
    if enabled():
        return ("Ordinary messages queue until the next model-and-tools boundary and do not cancel this turn. "
                "Operator /steer invalidates an unexecuted decision; /stop suspends at an effect boundary. "
                "Do not rest through queued input. Read it before a dependent next action.")
    return ("A new inbound message withholds an unexecuted reply into its own chat, "
            "the commands after that reply, and any rest; everything else still runs.")
