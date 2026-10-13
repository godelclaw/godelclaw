"""A minimal tmux interface for the agent.

Every window on the default tmux server belongs to this dedicated agent Unix
user and is intentionally shared agent workspace. Sending is therefore no
more privileged than the shell skill the agent already has.
"""

import subprocess
import time


def _run(*args):
    try:
        r = subprocess.run(["tmux", *args], capture_output=True, text=True,
                           timeout=15)
        return r.returncode, (r.stdout or r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "%s: %s" % (type(exc).__name__, exc)


def windows():
    code, out = _run("list-windows", "-a",
                     "-F", "#{session_name}:#{window_index} #{window_name}")
    if code != 0:
        return "tmux-windows failed: %s" % out
    return out or "(no tmux windows)"


def peek(window, lines=25):
    try:
        n = max(1, int(lines))
    except (TypeError, ValueError):
        n = 25
    code, out = _run("capture-pane", "-p", "-S", str(-n), "-t", str(window))
    if code != 0:
        return ("tmux-peek failed: %s — name a window from (tmux-windows), "
                "e.g. \"session:window\"" % out)
    tail = [l for l in out.splitlines() if l.strip()][-n:]
    return "\n".join(tail) or "(pane is empty)"


def send(window, text):
    window = str(window)
    text = str(text).strip()
    if not text:
        return "tmux-send: empty message"
    target = window
    code, out = _run("send-keys", "-l", "-t", target, "--", text)
    if code != 0:
        return "tmux-send failed: %s" % out
    # Codex and similar TUIs treat an Enter delivered in the same input burst
    # as a composition newline. Let the literal paste settle, then submit it as
    # a distinct event.
    time.sleep(2.0)
    code, out = _run("send-keys", "-t", target, "Enter")
    if code != 0:
        return "tmux-send submit failed: %s" % out
    return ("sent to %s — the session will see it as user input; "
            "(tmux-peek \"%s\" 30) in a later turn to read the reply"
            % (target, target))
