#!/usr/bin/env python3
"""Send mail and collect replies through a CeTTa shared mailbox socket.

Each command prints one JSON result. A delivery stays ready until explicit ack.
The service authenticates the OS account; AGENT is a name within that account.
"""
import argparse
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid


def agent_name(value):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise argparse.ArgumentTypeError("use a nonempty agent name with letters, digits, _, . or -")
    return value


def duration(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("timeout must be a finite nonnegative number of seconds")
    return number


def invoke(args, call, timeout=30):
    source = Path(__file__).with_suffix(".metta").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="shared-mail-") as directory:
        program = Path(directory) / "mail.metta"
        program.write_text(source + "\n!(shared-mail:print " + call + ")\n", encoding="utf-8")
        result = subprocess.run([args.cetta, "--lang", "petta", "--quiet", str(program)],
                                text=True, capture_output=True, timeout=timeout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.returncode:
        raise RuntimeError(f"cetta exited {result.returncode}: {result.stdout.strip()}")
    records = []
    for line in result.stdout.splitlines():
        if line.startswith(("{", '\"{')):
            value = json.loads(line)
            records.append(json.loads(value) if isinstance(value, str) else value)
    if len(records) != 1 or not isinstance(records[0], dict) or "status" not in records[0]:
        raise RuntimeError("missing or ambiguous mailbox result: " + result.stdout.strip())
    return records[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", default=os.environ.get("CETTA_SHARED_MAIL_SOCKET"),
                        help="shared socket path (or CETTA_SHARED_MAIL_SOCKET)")
    parser.add_argument("--cetta", default=os.environ.get("CETTA_BIN"),
                        help="cetta engine with mailbox:post (or CETTA_BIN)")
    parser.add_argument("--space", default="agent-mail", help="the agents' mail space")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("whoami", help="show the identity authenticated by the service")
    send = commands.add_parser("send", help="post a message; acceptance is not a reply")
    send.add_argument("agent", type=agent_name)
    send.add_argument("to", type=agent_name)
    send.add_argument("text")
    send.add_argument("--id", help="reuse this ID with identical content after an uncertain send")
    commands.add_parser("next", help="read without acknowledging").add_argument("agent", type=agent_name)
    wait = commands.add_parser("wait", help="wait for a delivery without acknowledging")
    wait.add_argument("agent", type=agent_name)
    wait.add_argument("--timeout", type=duration, default=120, help="seconds to wait (default 120)")
    ack = commands.add_parser("ack", help="acknowledge a handled delivery")
    ack.add_argument("agent", type=agent_name)
    ack.add_argument("id")
    args = parser.parse_args(argv)
    if not args.socket or not args.cetta:
        parser.error("choose the shared socket and the cetta engine")
    q = lambda value: json.dumps(value, ensure_ascii=False)
    message_id = None
    try:
        if args.command == "whoami":
            result = invoke(args, f"(shared-mail:identity {q(args.socket)})")
        elif args.command == "send":
            message_id = args.id if args.id is not None else uuid.uuid4().hex
            if not message_id or "\0" in message_id:
                parser.error("message ID must be nonempty and contain no NUL")
            # Make a generated ID recoverable even if the process loses the reply.
            print("message-id=" + json.dumps(message_id), file=sys.stderr, flush=True)
            result = invoke(args, f"(shared-mail:post {q(args.socket)} {q(args.space)} {q(message_id)} "
                                  f"{q(args.to)} {q(args.agent)} {q(args.text)})")
        elif args.command == "ack":
            result = invoke(args, f"(shared-mail:ack {q(args.socket)} {q(args.agent)} {q(args.id)})")
        else:
            deadline = time.monotonic() + (args.timeout if args.command == "wait" else 0)
            while True:
                milliseconds = min(30000, max(0, int((deadline - time.monotonic()) * 1000)))
                result = invoke(args, f"(shared-mail:next {q(args.socket)} {q(args.agent)} {milliseconds})",
                                timeout=milliseconds / 1000 + 30)
                if result["status"] != "idle" or time.monotonic() >= deadline:
                    break
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        result = {"status": "error", "reason": str(error)}
    if message_id:
        result.setdefault("id", message_id)
    print(json.dumps(result, ensure_ascii=False))
    expected = {"whoami": {"identity"}, "send": {"accepted", "duplicate"},
                "ack": {"settled", "duplicate"}, "next": {"delivery", "idle"}, "wait": {"delivery"}}
    if result["status"] in expected[args.command]:
        return 0
    return 3 if args.command == "wait" and result["status"] == "idle" else 1


if __name__ == "__main__":
    sys.exit(main())
