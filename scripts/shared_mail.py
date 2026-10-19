#!/usr/bin/env python3
"""Mail the agents of another account through its space service's shared socket.

  shared_mail.py send AGENT TO TEXT   post TEXT from this account's AGENT to TO
  shared_mail.py next AGENT           show AGENT's next delivery (it stays ready)
  shared_mail.py ack AGENT ID         settle a delivery once it has been handled

The service authenticates this account itself; AGENT names the sender within
it. Sends need only an append grant on the agents' space. Replies arrive in
this account's inbox space, mail@LOGIN, which the service owner creates and
grants. The mailbox protocol itself runs in MeTTa through cetta.
"""
import argparse
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--socket", default=os.environ.get("CETTA_SHARED_MAIL_SOCKET"),
                        help="shared socket path (or CETTA_SHARED_MAIL_SOCKET)")
    parser.add_argument("--cetta", default=os.environ.get("CETTA_BIN"),
                        help="cetta engine whose library has mailbox:post (or CETTA_BIN)")
    parser.add_argument("--space", default="agent-mail", help="the agents' mail space")
    commands = parser.add_subparsers(dest="command", required=True)
    send = commands.add_parser("send")
    send.add_argument("agent"); send.add_argument("to"); send.add_argument("text")
    commands.add_parser("next").add_argument("agent")
    ack = commands.add_parser("ack")
    ack.add_argument("agent"); ack.add_argument("id")
    args = parser.parse_args()
    if not args.socket or not args.cetta:
        parser.error("choose the shared socket and the cetta engine")
    q = lambda value: json.dumps(value, ensure_ascii=False)
    inbox = q("mail@" + getpass.getuser())
    if args.command == "send":
        call = (f"(mailbox:post {q(args.socket)} {q(args.space)} {q(uuid.uuid4().hex)} "
                f"{q(args.to)} {q(args.agent)} {q(args.text)})")
    elif args.command == "next":
        call = f"(mailbox:next {q(args.socket)} {inbox} {q(args.agent)})"
    else:
        call = f"(mailbox:ack {q(args.socket)} {inbox} {q(args.agent)} {q(args.id)})"
    with tempfile.TemporaryDirectory(prefix="shared-mail-") as directory:
        program = Path(directory) / "mail.metta"
        program.write_text("!(import! &self mailbox)\n!" + call + "\n", encoding="utf-8")
        result = subprocess.run([args.cetta, "--lang", "petta", "--quiet", str(program)],
                                text=True, capture_output=True, timeout=60)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
