"""Run Claude Code headless (claude -p) on the agent's behalf, gated by the
operator's authorization flag.

The flag is a file the operator flips from Telegram (/claude_code_authorization)
or the shell; the skill reads it fresh on every call, so revocation is
immediate. The subprocess runs plain `claude -p` — interactive permission
prompts are simply absent, so anything that would need one is declined by
Claude Code itself; this bridge adds no permission bypasses.
"""

import os
import shutil
import subprocess

FLAG = os.path.expanduser("~/.config/pettaclaw/claude_code_authorized")
_MAX_OUT = 6000


def _claude_bin():
    """Absolute path to the claude binary.

    The agent's systemd service PATH does not include ~/.local/bin, so a bare
    "claude" fails there while working from any interactive shell — verified
    the wrong way once (from a shell), reported broken by the agent, and she
    was right. Resolve absolutely; never trust the service PATH for this."""
    cand = os.environ.get("METTACLAW_CLAUDE_BIN", "")
    for c in (cand, os.path.expanduser("~/.local/bin/claude"),
              shutil.which("claude") or ""):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return ""


def authorized():
    try:
        with open(FLAG, encoding="utf-8") as f:
            return f.read().strip() == "1"
    except OSError:
        return False


def set_authorized(on):
    os.makedirs(os.path.dirname(FLAG), exist_ok=True)
    with open(FLAG, "w", encoding="utf-8") as f:
        f.write("1" if on else "0")
    return "claude-code authorization: %s" % ("ON" if on else "OFF")


def ask(prompt):
    """One headless Claude Code turn; returns its answer text."""
    if not authorized():
        return ("claude-code not authorized — Zar's /claude_code_authorization "
                "toggle is OFF")
    prompt = str(prompt).strip()
    if not prompt:
        return "claude-code: empty prompt"
    binpath = _claude_bin()
    if not binpath:
        return ("claude-code: binary not found — set METTACLAW_CLAUDE_BIN or "
                "install to ~/.local/bin/claude")
    try:
        r = subprocess.run(
            [binpath, "-p", prompt],
            capture_output=True, text=True, timeout=300,
            cwd=os.path.expanduser("~"),
        )
    except subprocess.TimeoutExpired:
        return "claude-code: timed out after 300s — ask a smaller question"
    except OSError as exc:
        return "claude-code failed to launch: %s" % exc
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.returncode != 0 and not out:
        return "claude-code error (exit %d): %s" % (r.returncode, err[:800])
    if len(out) > _MAX_OUT:
        out = out[:_MAX_OUT] + " …[truncated]"
    return out or "(claude-code returned nothing)"
