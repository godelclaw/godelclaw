"""Observation and transport boundary for the native working-state machine.

MeTTa owns every successor, rest deadline, boot marker and wait calculation.
This module only samples the existing turn scope and carries the already
encoded checkpoint through the existing idempotent native-ledger transport.
"""
import input_ack


def ledger_read():
    """(0,) for no committed checkpoint, or (1, exact JSON text)."""
    text = input_ack.working_set()
    return (0,) if text is None else (1, text)


def commit_scope(has_activity):
    """Carry the identities captured at turn begin, with the request focus."""
    import strategy_host
    import telegram
    captured = strategy_host._turn_input_ids
    ids = (tuple(telegram.current_batch_update_ids()) if captured is None else captured) if int(has_activity) else ()
    request = strategy_host._message_focus
    return tuple(str(identity) for identity in ids), str(request) if request else ""


def checkpoint(working_text, ids, request, saved_at):
    """Commit through the existing request-ID replay/conflict machinery."""
    return input_ack.turn_checkpoint(working_text, ids, request, float(saved_at or 0.0))
