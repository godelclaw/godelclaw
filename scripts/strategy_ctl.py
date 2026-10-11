#!/usr/bin/env python3
"""Inspect and resolve an instance's protected request ledger."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from observed_strategy import Ledger


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, required=True)
    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser("status")
    accept = commands.add_parser("accept")
    accept.add_argument("request"); accept.add_argument("content")
    accept.add_argument("--checker", default="operator")
    finish = commands.add_parser("finish")
    finish.add_argument("request"); finish.add_argument("ticket")
    finish.add_argument("--confirm", action="store_true", required=True)
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("ticket"); reconcile.add_argument("result")
    reconcile.add_argument("--confirm", action="store_true", required=True)
    cancel = commands.add_parser("cancel")
    cancel.add_argument("request")
    cancel.add_argument("--confirm", action="store_true", required=True)
    retry = commands.add_parser("retry-command", help="release one quarantined command after repair")
    retry.add_argument("command", help="command as a JSON array")
    retry.add_argument("--reason", required=True)
    retry.add_argument("--confirm", action="store_true", required=True)
    args = parser.parse_args()
    store = Ledger(args.store)
    if args.operation == "status":
        result = store.protected(None)
    elif args.operation == "accept":
        result = store.accept(args.request, args.content, args.checker)
    elif args.operation == "cancel":
        result = store.cancel(args.request, operator_authorized=args.confirm)
    elif args.operation == "reconcile":
        result = store.report(args.ticket, args.result, domain="unknown")
    elif args.operation == "retry-command":
        try:
            command = json.loads(args.command)
        except ValueError:
            parser.error("command must be a JSON array")
        if not isinstance(command, list) or not command or not isinstance(command[0], str):
            parser.error("command must be a nonempty JSON array beginning with a tool name")
        result = store.release_command(command, args.reason)
    else:
        checker = lambda work, receipt: work["checker"] == "operator" and args.confirm
        result = (store.attest(args.request, args.ticket, checker)
                  and store.finish(args.request, args.ticket, {"operator": checker}))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result else 1


if __name__ == "__main__":
    sys.exit(main())
