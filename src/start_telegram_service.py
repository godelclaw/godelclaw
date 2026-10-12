"""Start one agent's durable Telegram service without starting cognition."""
import argparse
import json
import os
from pathlib import Path
import re
import urllib.request


def agent_name():
    """The agent's name for command replies, from METTACLAW_AGENT_NAME.

    The channel service accepts 1..32 bytes without control characters, quotes
    or backslashes; anything else leaves the name out, and replies then say
    "the agent".
    """
    name = os.environ.get("METTACLAW_AGENT_NAME", "").strip()
    data = name.encode("utf-8")
    if not 0 < len(data) <= 32 or any(b < 32 or b == 127 for b in data) or '"' in name or "\\" in name:
        return None
    return name


def write_commands(repo, path):
    """Write this agent's command declarations: the shared file plus its name."""
    text = (Path(repo) / "channels/telegram_commands.metta").read_text(encoding="utf-8")
    name = agent_name()
    if name is not None:
        text = text.rstrip("\n") + '\n\n(tg-cmd:agent "%s")\n' % name
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)
    return path


_DECLARATION = re.compile(r'^\(tg-cmd:command "/([a-z0-9_]{1,32})" [a-z]+ "([^"]{1,200})"\)', re.MULTILINE)


def menu_commands(text):
    """Telegram's slash menu, read from the same declarations the service uses."""
    return [{"command": name, "description": help_text[:256]}
            for name, help_text in _DECLARATION.findall(text)]


def register_menu(commands_path, credential, timeout=10):
    """Publish the declared commands to Telegram's menu. Best effort: a failure
    never stops the channel, and nothing about the credential is printed."""
    try:
        commands = menu_commands(Path(commands_path).read_text(encoding="utf-8"))
        token = Path(credential).read_text(encoding="utf-8").strip()
        request = urllib.request.Request(
            "https://api.telegram.org/bot%s/setMyCommands" % token,
            data=json.dumps({"commands": commands}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            ok = json.loads(response.read().decode("utf-8")).get("ok") is True
        print("command menu: %d commands %s" % (len(commands), "registered" if ok else "refused"), flush=True)
        return ok
    except Exception as error:
        print("command menu not registered: %s" % type(error).__name__, flush=True)
        return False


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
            "--commands", str(write_commands(repo, Path(state) / "commands.metta")),
            "--command-deadline-ms", "3000"]
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
    parser.add_argument("--state", type=Path)
    parser.add_argument("--credential", type=Path)
    parser.add_argument("--write-commands", type=Path,
                        help="only write this agent's command file to PATH and exit")
    parser.add_argument("--register-menu", action="store_true",
                        help="with --write-commands: also publish the commands to Telegram's menu")
    args = parser.parse_args()
    if args.write_commands:
        path = write_commands(args.repo, args.write_commands)
        if args.register_menu and args.credential is not None:
            register_menu(path, args.credential)
        return
    if args.state is None or args.credential is None:
        parser.error("--state and --credential are required to start the service")
    try:
        argv = command(args.repo, args.state, args.credential)
    except (KeyError, ValueError):
        # Configuration values and credentials are deliberately not logged.
        raise SystemExit("invalid durable Telegram service configuration") from None
    register_menu(argv[argv.index("--commands") + 1], args.credential)
    os.execv(argv[0], argv)


if __name__ == "__main__":
    main()
