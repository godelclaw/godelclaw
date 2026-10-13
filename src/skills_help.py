"""Man pages + command checking. Progressive disclosure, kept deliberately
small: the catalog stays one line per skill; the page arrives only on the
turn that asks for it, and the checker answers format questions for free
(no LLM, it inspects what the loop's own reader produced).
"""

# skill -> (min_args, max_args). None = variadic tail.
ARITY = {
    "task-read": (1, 2), "task-history": (1, 2), "task-note": (1, 2),
    "requests": (0, 0), "request-status": (1, 1), "request-focus": (1, 1),
    "request-page": (1, 1), "request-read": (2, 2), "request-disposition": (3, 3),
    "command-receipts": (0, 1), "topics": (0, 0), "topic-select": (1, 1),
    "topic-note": (1, 1),  "model-jobs": (0, 0),
    "model-result": (1, 1),
    "remember": (1, 1), "query": (1, 1), "pin": (1, 1), "unpin": (1, 1),
    "goals": (0, 0), "goal-write": (1, 1), "goal-drop": (2, 2),
    "rest": (0, 2),
    "nop": (0, 0),
    "mode": (0, 0),
    "modes": (0, 0),
    "mode-set": (1, 1),
    "iter-transformations": (0, 0), "iter-transform-write": (2, 2),
    "iter-transform-disable": (1, 1), "iter-transform-enable": (1, 1),
    "shell": (1, 2), "background": (1, 1), "jobs": (0, 0),
    "job-output": (1, 1), "job-stop": (1, 1), "vitals": (0, 0),


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

PAGES = {
    "mail-send": ('usage: (mail-send "name" "text" ["stable-id"])\n'
                  'Native CeTTa mail, delivered automatically as peer input.\n'
                  'Reply with mail-send. Reusing an ID with the same content is a duplicate;\n'
                  'different content is a conflict. IDs are optional; no filing commands.'),
    "mail-receive": ('usage: (mail-receive)\n'
                     'Check native mail now. Arrivals also enter turns automatically;\n'
                     'acknowledgement follows a committed turn. CeTTa only.'),
    "task-read": 'usage: (task-read "call-id" "offset")\nRead an exact recorded call/result pair. Offsets count characters; next=null ends it.',
    "task-history": 'usage: (task-history "request-id" "offset")\nTask-scoped ordered call/result index, including compacted history.',
    "task-note": 'usage: (task-note "findings, uncertainties, next steps" "JSON list of call IDs")\nAppend a task note with evidence references. A note is an interpretation, not certified truth.',

    "command-receipts": 'usage: (command-receipts) or (command-receipts "NEXT_CURSOR")\nPages of attempted commands with stable IDs and recorded dispositions/results. Follow next_cursor until null; concatenate chunk records by ID and character offset. Uncertain is not success.',
    "topics": 'usage: (topics)\nNamed agent-owned working contexts and their revisions. Operator input is always visible.',
    "topic-select": 'usage: (topic-select "name")\nSelect or create named notes for subsequent model turns. Does not hide incoming messages.',
    "topic-note": 'usage: (topic-note "notes")\nReplace active topic notes and advance its revision. Captured background answers then become stale.',

    "model-jobs": 'usage: (model-jobs)\nBounded recent model jobs: owners, state, observation and adoption.',
    "model-result": 'usage: (model-result "id")\nRead a correlated result. Its returned tool receipt must reach a subsequent model step before adoption.',


    "requests": 'usage: (requests)\nCompact retained work and receipt view with counts. Input processing settles delivery, not task achievement. Use request-page for older records.',
    "request-page": 'usage: (request-page "offset")\nRead eight retained request headlines, including processed messages and deferred work. next_offset continues the listing.',
    "request-status": 'usage: (request-status "id")\nRead kind, workflow status, processing flag and original input excerpt. next_offset continues with request-read.',
    "request-read": 'usage: (request-read "id" "offset")\nRead the next 4000 characters of the retained original input.',
    "request-disposition": 'usage: (request-disposition "id" "keep|waiting-user|defer|addressed|reopen" "reason")\nkeep promotes an input to explicit ongoing work; include its closing condition. waiting-user and defer retain work for later. addressed is your reviewed workflow disposition, never checker-certified achievement. Goals and configured exact-output checkers retain their own completion rules.',
    "shell": ('usage: (shell "command") or (shell "command" timeout_seconds)\n'
              'Run under sh; default timeout is 120 seconds, or METTACLAW_SHELL_TIMEOUT_SECONDS. '
              'Zero disables the deadline. A timeout returns partial output. '
              'For immediate return with completion notification use background.'),
    "background": (
        'usage: (background "command string")\n'
        'Runs under bash with no time limit and returns at once with a job\n'
        'id and its output file. When it ends you are woken by a [runtime]\n'
        'activity line with the exit status, duration and output tail; no\n'
        'polling needed. (jobs) lists jobs, (job-output "id") reads more\n'
        'output, (job-stop "id") ends one. A service restart ends jobs.'),
    "vitals": (
        'usage: (vitals)\n'
        'Uptime, load against CPUs, memory used and available, memory\n'
        'stall percentages, swap, the five heaviest processes by memory, and\n'
        'running background jobs. Check it before explaining a gap.'),
    "goal-write": (
        'usage: (goal-write (goal <name> <Area> sti:<0-1> lti:<0-1>\n'
        '        vibes:(a b) blocked-by:none last-verified:never note:free text))\n'
        'Liberal: quoted string or bare atom, commas or spaces in vibes,\n'
        'vibes omitted entirely — all accepted and normalized.\n'
        'blocked-by and note: hyphenate-multi-word-values.\n'
        'Scheduling: (goal-write (goal-policy name share:1 every:once urgency:0 checker:operator))\n'
        'share is a nonnegative integer (0 disables autonomous service); every is once or seconds.\n'
        'Use checker:inventory-review only for a goal whose outcome is a saved inventory review.\n'
        'Policies preserve service history; notes and timestamps do not certify completion.'),

    "goal-drop": (
        'usage: (goal-drop "name" "reason")\n'
        'Retires the goal with your reason: it leaves scheduling, goes to the\n'
        'compost log, and its open cycles close without counting as achieved.\n'
        'Reason "solved" claims achievement and needs a last-verified date.\n'
        'goal-write brings a retired goal back.'),
    "pin": (
        'usage: (pin "short state line")  |  (unpin "fragment")\n'
        'Persists to disk, survives restarts, shown every turn under PINNED.\n'
        'Cap 12, oldest drops. Pin STATE ("BS v0.5 verified, hebbian next"),\n'
        'use (remember ...) for things future-you should be able to query.'),
    "metta": (
        'usage: (metta "expression string")\n'
        'Evaluates in the LIVE loop space. Definitions do not persist across\n'
        'separate (metta ...) calls — chain dependent steps in ONE call via\n'
        'nested let. repr of an unevaluated call stringifies the CALL: force\n'
        'results first: (let $r (f x) (repr $r)).'),
    "read-file": (
        'usage: (read-file "path" [max-bytes])\n'
        'Default: 2000000 bytes; 0 means unlimited. Oversized files return a reason, never cut text.\n'
        'Whole file, or a reason: "read failed: no such file: X (cwd Y)".\n'
        'Relative paths resolve from the loop cwd shown in that message —\n'
        'when in doubt use absolute paths.'),
    "edit-file": (
        'usage: (edit-file "path" "old-string" "new-string")\n'
        'Exact unique replacement. If old-string matches zero or many places\n'
        'the result says so — widen the snippet until unique.'),
    "iter-transform-write": (
        'usage: (iter-transform-write "10_name.metta" "MeTTa source")\n'
        'Writes one mutable MeTTa Iter transformation atomically. It becomes\n'
        'eligible at the next request capture, never halfway through the\n'
        'current turn.\n'
        'Syntax failure is reported but remains permissive: the transform is\n'
        'installed and Iter will stutter locally until you repair or disable it.\n'
        'This is not proposal-bound stable-source editing and not a sandbox.'),
    "iter-transformations": (
        'usage: (iter-transformations)\n'
        'Lists active and leading-underscore-disabled transformation entries\n'
        'without executing them.'),
    "iter-transform-disable": (
        'usage: (iter-transform-disable "10_name.metta") |\n'
        '       (iter-transform-enable "10_name.metta")\n'
        'Atomically toggles the upstream leading-underscore convention. The\n'
        'change is eligible at the next request capture and is reversible.'),
    "send": (
        'usage: (send "message")\n'
        'Sends to the configured primary operator chat when one is set; a\n'
        'cross-chat read cannot redirect it. End messages to humans/agents\n'
        'with the affect trace line. For every other audience use the exact\n'
        'address: (send-telegram-chat "id" "msg").'),
    "send-file": (
        'usage: (send-file "/abs/path" "caption")  |  (send-image ...)\n'
        'send-image previews inline; SVG is rasterized when possible.\n'
        'Result echoes the sent message id — that is what delete uses.'),
    "delete-my-recent": (
        'usage: (delete-my-recent "chat_id" k)\n'
        'Deletes your own last k messages to that chat (ids are recorded at\n'
        'send time). Only your messages, only within Telegram\'s 48h window;\n'
        'a forward of your message belongs to the forwarder, not you.'),
    "delete-message-exact": (
        'usage: (delete-message-exact "chat_id" message_id)\n'
        'Deletes one exact bot-owned Telegram message only when its own-send\n'
        'receipt is in the ledger. Legacy ids require the authenticated\n'
        'operator /delete fast path. Claim completion only after the returned\n'
        'delete receipt.'),
    "query": (
        'usage: (query "short phrase")\n'
        'Embedding search over long-term memory. EMBED_DAEMON_DOWN means the\n'
        'memory service is unreachable — it does NOT mean nothing is stored.\n'
        'Report it once and move on; do not retry-storm.'),
    "energy": (
        'usage: (energy)  |  (energy-set "default|Name|id" "full|mid|light")\n'
        'How many loops a sender\'s message arms: full=50 mid=30 light=10.\n'
        'Persisted; the operator has the same controls as /energy in chat.'),
    "sibling-off": (
        'usage: (sibling-status) | (sibling-on) | (sibling-off "reason")\n'
        'Watchdog-safe: off sets the pause marker BEFORE stopping (no revival\n'
        'race); on clears it before starting. The result reports the VERIFIED\n'
        'state, not the attempt.'),
    "mcp-call": (
        'usage: (mcp-call "server" "tool" "json-object-string")\n'
        'Discover first: (mcp-servers), (mcp-tools "server"),\n'
        '(mcp-tool-info "server" "tool") for the argument schema.'),
    "rest": (
        'usage: (rest) — until the next human message | (rest seconds)\n'
        'Ends the burst. The operator\'s message or /wake ends a timed rest;\n'
        'other activity queues. Rest remains yours to choose.'),
    "claude-code": (
        'usage: (claude-code "one clear question or task")\n'
        'Runs Claude Code headless; the reply comes back as the result.\n'
        'Gated by Zar\'s /claude_code_authorization toggle. Takes up to 300s\n'
        '— batch it last so faster commands are not stuck behind it.'),
    "tmux-send": (
        'usage: (tmux-send "session:window" "message")\n'
        'Types into any window on this Unix user\'s tmux server. All such\n'
        'windows are shared agent workspace; no separate allowlist or\n'
        'authorization toggle applies.\n'
        'Read the reply LATER with (tmux-peek "session:window" 30) — the\n'
        'session answers in its own time, not within your turn.'),
    "check": (
        'usage: (check "(shell \\"ls /tmp\\")")\n'
        'Dry-run: parses the string with the SAME reader the loop uses and\n'
        'reports head, arg count, and arity fit — without executing anything.\n'
        'Costs a command slot, not a turn: batch it beside real work.'),
}
# aliases so help finds pages under sibling names
for a, b in (("unpin", "pin"), ("write-file", "read-file"),
             ("append-file", "read-file"), ("send-image", "send-file"),
             ("send-telegram-chat", "send"), ("energy-set", "energy"),
             ("sibling-on", "sibling-off"), ("sibling-status", "sibling-off"),
             ("remember", "query"), ("mcp-servers", "mcp-call"),
             ("mcp-tools", "mcp-call"), ("mcp-tool-info", "mcp-call"),
             ("search-tavily", "search"), ("search-ddg", "search")):
    PAGES.setdefault(a, PAGES.get(b, ""))
PAGES.setdefault("search", 'usage: (search "phrase") — configured provider; '
                 'also (search-tavily ...), (search-ddg ...).')


def help(name=""):
    name = str(name).strip().strip('"')
    if not name:
        known = sorted(set(list(PAGES) + list(ARITY)))
        return ("help pages: " + " ".join(known)
                + " — (help \"name\") for one page")
    page = PAGES.get(name)
    if page:
        return page
    if name in ARITY:
        lo, hi = ARITY[name]
        return "%s takes %s args; no longer page yet." % (
            name, lo if lo == hi else "%d-%d" % (lo, hi))
    return "no help for %r — (help) lists everything" % name


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


def verdict(original, rendered):
    """Human verdict on a dry-run parse. `rendered` is repr of what the
    loop's own reader produced — a string, so it always crosses the bridge."""
    rendered = str(rendered).strip()
    low = rendered.lower()
    if low.startswith("(error") or "syntax_error" in low or "parse error" in low:
        return ("PARSE FAILED: %s — the loop would reject this batch. "
                "Common causes: unbalanced parentheses, an apostrophe or "
                "unescaped quote inside a string." % _compact(rendered))
    toks = _split_top(rendered)
    if not toks:
        return ("parses, but as a bare atom (%s) — a command must be "
                "(skill args...)" % _compact(rendered))
    head, argc = toks[0], len(toks) - 1
    if head not in ARITY:
        return ("parses as (%s … %d args) but %r is NOT a known skill — "
                "(help) lists them" % (head, argc, head))
    lo, hi = ARITY[head]
    if lo <= argc <= hi:
        return "OK: (%s …) with %d args — valid call, not executed" % (head, argc)
    want = str(lo) if lo == hi else "%d-%d" % (lo, hi)
    hint = ""
    if argc > hi and head == "shell":
        hint = " — quote the whole command as ONE string"
    return ("ARITY: (%s …) got %d args, takes %s%s — this is the error the "
            "loop reports as a format failure" % (head, argc, want, hint))


def _compact(v, limit=90):
    s = str(v)
    return s if len(s) <= limit else s[:limit] + "…"


# Equation heads supply the arities for runtime-defined skills, so new skills
# do not need a second hand-kept registry merely to be callable.
def equation_arities():
    import re
    from pathlib import Path
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


def command_error(command):
    if not isinstance(command, (list,tuple)) or not command or not isinstance(command[0],str):
        return "COMMAND_ERROR: expected (skill arguments...)"
    bounds = ARITY.get(command[0])
    if bounds and (len(command)-1 < bounds[0] or bounds[1] is not None and len(command)-1 > bounds[1]):
        return "COMMAND_ERROR: wrong number of arguments. " + help(command[0])
    return ""


_equations = equation_arities()
ARITY.update({name:(min(counts),max(counts)) for name,counts in _equations.items()
              if name in ARITY or name in PAGES or name in ("memory-health", "memory-compact", "context-policy")})
ARITY.pop('rest-command', None)
ARITY['shell'] = (1,2)  # native CeTTa or SWI-PeTTa process backend
PAGES['context-policy'] = 'usage: (context-policy) or (context-policy task_tokens)\nInspect or change the task context target; actual model capacity also applies. Older evidence remains in task-history/task-read.'
PAGES['task-resolve'] = 'No resolution bookkeeping is required. Calls remain in task-history.'

for _retired in ("background-model", "model-adopt", "model-retire", "goal-review-save", "task-resolve"):
    ARITY.pop(_retired, None)
