"""The bare-command vocabulary: each command's (min, max) argument count.

skills_help checks commands and helper recognizes a bare command against this
one table. Equation heads in src/ and channels/ refine the arity of every
command named here, so a command's arity follows its definition.
"""
import re
from pathlib import Path

# command -> (min_args, max_args). None = variadic tail.
ARITY = {
    "work": (1, 4), "work-status": (0, 1), "work-steer": (2, 2),
    "work-resume": (1, 2), "work-stop": (1, 1),
    "work-hold": (2, 3), "work-archive": (2, 2),
    "memory-node": (4, 6), "memory-link": (2, 2), "memory-summary": (2, 2),
    "memory-open": (1, 2), "memory-find": (1, 2), "memory-original": (2, 2),
    "memory-focus": (1, 2), "memory-window": (1, 1),
    "task-read": (1, 3), "task-history": (1, 3), "task-note": (1, 2),
    "requests": (0, 0), "request-status": (1, 1), "request-focus": (1, 1),
    "request-page": (1, 1), "request-read": (2, 2), "request-disposition": (3, 3),
    "command-receipts": (0, 1), "topics": (0, 0), "topic-select": (1, 1),
    "topic-note": (1, 1),  "model-jobs": (0, 0),
    "model-result": (1, 1),
    "settings": (0, 1), "setting": (2, 3),
    "remember": (1, 1), "query": (1, 3), "forget": (1, 2),
    "memory-edit": (2, 2), "memory-read": (1, 1), "memory-history": (1, 1),
    "memory-pending": (0, 0), "memory-retry": (0, 0), "memory-retire": (1, 2),
    "pin": (1, 1), "unpin": (1, 1), "pins": (0, 0),
    "goals": (0, 0), "goal-write": (1, 1), "goal-drop": (2, 2),
    "rest": (0, 2),
    "nop": (0, 0),
    "mode": (0, 0),
    "modes": (0, 0),
    "mode-set": (1, 1),
    "iter-transformations": (0, 0), "iter-transform-write": (2, 2),
    "iter-transform-disable": (1, 1), "iter-transform-enable": (1, 1),
    "shell": (1, 2), "background": (1, 2), "jobs": (0, 1),
    "job-output": (1, 2), "job-stop": (1, 2), "vitals": (0, 0),


    "read-file": (1, 2), "write-file": (2, 2),
    "mail-send": (2, 3), "mail-receive": (0, 0),
    "append-file": (2, 2), "read-lines": (1, 3), "edit-file": (3, 3),
    "ls-tree": (1, 4), "grep-files": (2, 4),
    "send": (1, 1), "send-telegram-chat": (2, 2),
    "send-file": (1, 2), "send-image": (1, 2),
    "delete-my-recent": (2, 2), "delete-message-exact": (2, 2),
    "recent-messages": (2, 2),
    "search": (1, 1), "search-tavily": (1, 1), "search-ddg": (1, 1),
    "llm-quota": (0, 0), "llm-models": (0, 0), "llm-set-model": (1, 1),
    "energy": (0, 0), "energy-set": (2, 2),
    "sibling-status": (0, 0), "sibling-on": (0, 0), "sibling-off": (1, 1),
    "mcp-servers": (0, 0), "mcp-tools": (1, 1), "mcp-tool-info": (2, 2),
    "mcp-call": (3, 3),
    "metta": (1, 1), "help": (0, 1), "check": (1, 1),
    "claude-code": (1, 1), "tmux-windows": (0, 0),
    "tmux-peek": (2, 2), "tmux-send": (2, 2),
}

# Commands named only by their equations.
EQUATION_COMMANDS = ("memory-health", "context-policy")


def _split_top(text):
    """Top-level tokens of one rendered s-expression: '(a "b c" (d e))' ->
    ['a', '"b c"', '(d e)']. Works on repr output, which is regular."""
    text = text.strip()
    if not (text.startswith("(") and text.endswith(")")):
        return None
    body, out, buf, depth, instr = text[1:-1], [], "", 0, False
    for ch in body:
        if instr:
            buf += ch
            if ch == '"':
                instr = False
        elif ch == '"':
            instr = True; buf += ch
        elif ch == "(":
            depth += 1; buf += ch
        elif ch == ")":
            depth -= 1; buf += ch
        elif ch.isspace() and depth == 0:
            if buf:
                out.append(buf); buf = ""
        else:
            buf += ch
    if buf:
        out.append(buf)
    return out


def equation_arities():
    result = {}
    for directory in (Path(__file__).parent, Path(__file__).resolve().parents[1] / "channels"):
        for path in sorted(directory.glob("*.metta")):
            text = path.read_text()
            for match in re.finditer(r"^\(=\s+\(", text, re.M):
                start = match.end()-1
                depth, quoted, escaped, end = 0, False, False, start
                for end in range(start,len(text)):
                    c=text[end]
                    if escaped:
                        escaped=False
                    elif c == "\\" and quoted:
                        escaped=True
                    elif c == '"':
                        quoted=not quoted
                    elif not quoted:
                        if c == '(':
                            depth+=1
                        elif c == ')':
                            depth-=1
                            if depth == 0:
                                break
                forms=[text[start:end+1]]
                # _top_level_forms scans the remaining file, including bodies;
                # only the first balanced form is the equation's left side.
                if not forms:
                    continue
                fields = _split_top(forms[0])
                if fields and not fields[0].startswith('$'):
                    result.setdefault(fields[0],set()).add(len(fields)-1)
    return result


_equations = equation_arities()
ARITY.update({name: (min(counts), max(counts)) for name, counts in _equations.items()
              if name in ARITY or name in EQUATION_COMMANDS})
ARITY.pop('rest-command', None)
ARITY['shell'] = (1, 2)  # native CeTTa or SWI-PeTTa process backend
for _retired in ("background-model", "model-adopt", "model-retire", "goal-review-save", "task-resolve"):
    ARITY.pop(_retired, None)



def names_text():
    """The bare-command vocabulary, one name per line, for native response parsing."""
    return "\n".join(sorted(ARITY))
