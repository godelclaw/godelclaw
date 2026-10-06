"""Start one agent's durable Telegram service without starting cognition."""
import argparse
import os
from pathlib import Path


def command(repo, state, credential):
    def required(name):
        value = os.environ.get(name, "").strip()
        if not value:
            raise ValueError("missing setting " + name)
        return value
    binary = required("CETTA_TELEGRAM_SERVICE_BIN")
    root = required("CETTA_TELEGRAM_SERVICE_ROOT")
    chats = {int(x.strip()) for name in ("METTACLAW_TELEGRAM_PRIMARY_CHAT_ID", "METTACLAW_TELEGRAM_ALLOWED_CHAT_IDS")
             for x in os.environ.get(name, "").split(",") if x.strip()}
    operators = {int(x.strip()) for x in required("METTACLAW_TELEGRAM_OPERATOR_IDS").split(",") if x.strip()}
    if not chats or 0 in chats or not operators or min(operators) <= 0:
        raise ValueError("invalid channel allowlist")
    argv = [binary, "--run", "--root", root, "--state-dir", str(state),
            "--worker", os.environ.get("METTACLAW_INSTANCE", "agent"), "--program", "channel",
            "--listener-fd", "3", "--operator-fd", "4", "--credential-file", str(credential),
            "--commands", str(repo / "channels/telegram_commands.metta"), "--command-deadline-ms", "3000"]
    for chat in sorted(chats):
        argv += ["--chat", str(chat)]
    for operator in sorted(operators):
        argv += ["--operator", str(operator)]
    offset = os.environ.get("METTACLAW_TELEGRAM_INITIAL_OFFSET", "")
    if offset:
        argv += ["--initial-offset", str(int(offset))]
    poll_limit = os.environ.get("METTACLAW_TELEGRAM_POLL_LIMIT", "").strip()
    if poll_limit:
        limit = int(poll_limit)
        if not 1 <= limit <= 100:
            raise ValueError("invalid poll limit")
        argv += ["--poll-limit", str(limit)]
    return argv


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--credential", type=Path, required=True)
    args = parser.parse_args()
    try:
        argv = command(args.repo, args.state, args.credential)
    except (KeyError, ValueError):
        # Configuration values and credentials are deliberately not logged.
        raise SystemExit("invalid durable Telegram service configuration") from None
    os.execv(argv[0], argv)


if __name__ == "__main__":
    main()
