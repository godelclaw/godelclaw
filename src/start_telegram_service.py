"""Start one agent's durable Telegram service without starting cognition."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
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


BARE_IN_GROUPS = "(tg-cmd:bare-in-groups)"


def write_commands(repo, path, bare_in_groups=False):
    """Write this agent's command declarations: the shared file plus its name,
    and, when asked, that an operator's bare /command in a group is one."""
    text = (Path(repo) / "channels/telegram_commands.metta").read_text(encoding="utf-8")
    name = agent_name()
    if name is not None:
        text = text.rstrip("\n") + '\n\n(tg-cmd:agent "%s")\n' % name
    if bare_in_groups:
        text = text.rstrip("\n") + "\n" + BARE_IN_GROUPS + "\n"
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


def _truthy(value):
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _rendered_setting(repo, name):
    """A setting from the instance's generated .env, rendered from its TOML."""
    try:
        lines = (Path(repo) / ".env").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):]
        key, sep, value = line.partition("=")
        if sep and key.strip() == name:
            return value.strip().strip("'\"")
    return None


def allow_private(repo):
    """telegram.allow_private: every private chat reaches the agent, not only
    the listed ones. The channel environment may state it; otherwise it is
    the instance's own setting, as the agent reads it."""
    value = os.environ.get("METTACLAW_TELEGRAM_ALLOW_PRIVATE")
    if value is None:
        value = _rendered_setting(repo, "METTACLAW_TELEGRAM_ALLOW_PRIVATE")
    return _truthy(value)


def service_usage(binary):
    """What this service binary says it takes; "" when it cannot be asked."""
    try:
        result = subprocess.run([str(binary), "--help"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout + result.stderr


def service_admits_private(binary):
    """Whether this service binary takes --allow-private, as its own usage
    says. One that does not would refuse the flag and never start."""
    return "--allow-private" in service_usage(binary)


def bare_group_commands(repo):
    """telegram.bare_group_commands: an operator's bare /command in a group
    is a command for this agent, as for every other agent there. On unless
    the channel environment or the instance's own setting turns it off."""
    value = os.environ.get("METTACLAW_TELEGRAM_BARE_GROUP_COMMANDS")
    if value is None:
        value = _rendered_setting(repo, "METTACLAW_TELEGRAM_BARE_GROUP_COMMANDS")
    return value is None or str(value).strip() == "" or _truthy(value)


def service_takes_bare_commands(binary):
    """Whether this service binary knows the declaration, as its own usage
    says. One that does not would refuse the whole commands file."""
    return BARE_IN_GROUPS in service_usage(binary)


def policy_admission(repo, root, private):
    """What the access policy's facts file adds to the chats this service
    admits, as the policy itself computes it; None without a facts file."""
    if not os.environ.get("METTACLAW_ACCESS_POLICY_PATH", "").strip():
        return None
    environment = dict(os.environ, METTACLAW_TELEGRAM_ALLOW_PRIVATE="1" if private else "0")
    result = subprocess.run([str(Path(root) / "cetta"), "--lang", "petta", "--import-mode", "ancestor-walk",
                             "--quiet", str(Path(repo) / "scripts/telegram_admission.metta")],
                            cwd=str(repo), env=environment, capture_output=True, text=True, timeout=60)
    for line in result.stdout.splitlines():
        if result.returncode == 0 and line.startswith("TELEGRAM_ADMISSION "):
            return json.loads(line[len("TELEGRAM_ADMISSION "):])
    print("the access policy's facts file was refused: %s"
          % (result.stdout + result.stderr).strip()[-600:], file=sys.stderr, flush=True)
    raise ValueError("refused access policy")


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
    private = allow_private(repo)
    admission = policy_admission(repo, root, private)
    if admission is not None:
        chats |= {int(x) for x in admission["chats"]}
        operators |= {int(x) for x in admission["owners"]}
        private = bool(admission["private"])
        if admission["all_groups"]:
            print("the access policy hears every Telegram group; the channel service admits only "
                  "the groups it names", file=sys.stderr, flush=True)
    if not chats or 0 in chats or not operators or min(operators) <= 0 or len(chats) > 128 or len(operators) > 16:
        raise ValueError("invalid channel allowlist")
    if private and not service_admits_private(binary):
        # Turned on before the service can do it: keep the channel up on its
        # listed chats rather than start a service that refuses the flag.
        print("telegram.allow_private is on, but this channel service cannot admit every private chat; "
              "it keeps to the listed chats", file=sys.stderr, flush=True)
        private = False
    # Declared only to a service that knows it. An older one keeps a bare
    # command in a group as ordinary input, which the loop's client handles.
    bare = bare_group_commands(repo) and service_takes_bare_commands(binary)
    argv = [binary, "--run", "--root", root, "--state-dir", str(state),
            "--worker", os.environ.get("METTACLAW_INSTANCE", "agent"), "--program", "channel",
            "--listener-fd", "3", "--operator-fd", "4", "--credential-file", str(credential),
            "--commands", str(write_commands(repo, Path(state) / "commands.metta", bare)),
            "--command-deadline-ms", "3000"]
    for chat in sorted(chats):
        argv += ["--chat", str(chat)]
    for peer in sorted({int(x.strip()) for x in os.environ.get("METTACLAW_TELEGRAM_WAKE_IDS", "").split(",") if x.strip()}):
        argv += ["--peer", str(peer)]
    for operator in sorted(operators):
        argv += ["--operator", str(operator)]
    if private:
        argv.append("--allow-private")
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
