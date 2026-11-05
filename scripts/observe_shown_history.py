#!/usr/bin/env python3
"""Mark observed the receipts the conversation carried before its current window.

While answered prompts did not mark the results they carried, receipts reported in that time stayed
unobserved however often the model saw them: they could not be attested or finished, and two of them
held the protected view (lean/pettaclaw/ObservedReceipts.lean: unmarked_view_starves). An answered
prompt now marks what it carried, but entries before the conversation's saved window start are not
carried again.

The window only moves forward, and a compaction keeps its newest entries, so every entry before the
saved start was carried by an earlier prompt. This treats the calls of those entries as carried and
marks their actions by the rule an answered prompt uses (Ledger.observe_presented): the receipt exists
and every attempt has its result among the carried calls.

Without --apply it lists what it would mark. With --apply it marks them and lists them, so the change
can be undone.
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


CHUNK = 500


def window_start(input_ack):
    """The saved start of the conversation window, and its key."""
    for key, text in input_ack.meta_prefix("task_window:conversation:"):
        value = json.loads(text)
        if isinstance(value, dict) and isinstance(value.get("start"), int):
            return key, value["start"]
    return None, 0


def carried_before(input_ack, start):
    """The (ticket, position) calls with a recorded result among the entries before `start`."""
    if start <= 0:
        return []
    answer = input_ack._ledger_call("conversation-history", (0, int(start)), input_ack.WHOLE_READ_SECONDS)
    if not (isinstance(answer, tuple) and len(answer) == 3):
        raise RuntimeError("unexpected conversation history: %s" % input_ack._show(answer))
    calls = []
    for serial, request, position, command, record, ticket, disposition in answer[2]:
        if serial != -1 and not input_ack._is_none(ticket) and not input_ack._is_none(record):
            calls.append((str(ticket), int(position)))
    return calls


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--socket", help="the native ledger's socket (default: METTACLAW_INPUT_LEDGER_SOCKET)")
    parser.add_argument("--apply", action="store_true", help="mark them; without it, only list them")
    args = parser.parse_args(argv)
    import input_ack
    if args.socket:
        os.environ[input_ack.LEDGER_SOCKET] = args.socket
    if not input_ack._native_socket():
        parser.error("no native ledger: pass --socket or set %s" % input_ack.LEDGER_SOCKET)
    try:
        key, start = window_start(input_ack)
        calls = carried_before(input_ack, start)
        by_ticket = {}
        for ticket, position in calls:
            by_ticket.setdefault(ticket, []).append(position)
        tickets = sorted(by_ticket)
        pairs = []
        for begin in range(0, len(tickets), CHUNK):  # bounded reads: CHUNK actions each
            chunk = [(ticket, position) for ticket in tickets[begin:begin + CHUNK] for position in by_ticket[ticket]]
            pairs += input_ack.presented_receipts(chunk, input_ack.WHOLE_READ_SECONDS)
        if args.apply:
            for start_at in range(0, len(pairs), CHUNK):  # each chunk is one transaction
                input_ack.action_observe(pairs[start_at:start_at + CHUNK])
    finally:
        input_ack.close_ledger()
    print(json.dumps({"window": key, "start": start, "carried_calls": len(calls), "applied": args.apply,
                      "tickets": [ticket for ticket, _ in pairs]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
