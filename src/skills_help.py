"""Man pages + command checking. Progressive disclosure, kept deliberately
small: the catalog stays one line per skill; the page arrives only on the
turn that asks for it, and the checker answers format questions for free
(no LLM, it inspects what the loop's own reader produced).
"""

from command_arity import ARITY, _split_top  # noqa: E402


PAGES = {
    "work": 'usage: (work "task" ["workdir" ["model" ["effort"]]])\nStart an independent coding session with its own transcript and plan. It runs until a complete reply calls no tools, or you stop it, and mails its report back. You can continue talking while it works. It keeps the model and effort it starts with: yours and "high" unless you name others; switching your own model later does not change it.',
    "work-status": 'usage: (work-status ["id"])\nList sessions or inspect one session\'s progress, plan and final report.',
    "work-steer": 'usage: (work-steer "id" "guidance")\nAppend guidance to the same coding transcript. Guidance arriving during a final reply continues the task before it is marked done.',
    "work-resume": 'usage: (work-resume "id" ["pinned"|"current"])\nExplicitly resume an inactive session with its existing task, transcript, plan and inbox. "pinned" (default, or configured via (setting "work" "resume" ...)) resumes on its pinned runtime; "current" checks a copy of the checkpoint against the current runtime and re-pins before launching. An active session is not launched again. Interrupted tool effects remain uncertain and are never automatically replayed.',
    "work-stop": 'usage: (work-stop "id")\nStop the session, keeping its evidence and plan. Only explicit work-resume clears its stop request.',
    "work-hold": 'usage: (work-hold "id" "why" ["YYYY-MM-DD"])\nRecord a hold on the session in lifecycle.json, either until the local review date YYYY-MM-DD or indefinitely when omitted. Held sessions stay out of the open context list until due; work-resume clears the hold.',
    "work-archive": 'usage: (work-archive "id" "why")\nMark the session archived in lifecycle.json so it is excluded from the open context list while keeping its folder and evidence intact. Explicit work-resume clears the archive.',
    "settings": 'usage: (settings ["namespace"])\nLive native catalogue of module-registered settings, including current/default value, units, domain, description and persistence. New registrations appear immediately. An empty namespace lists all.',
    "setting": 'usage: (setting "namespace" "key" [value])\nInspect one setting or invoke its registered writer. Numeric strings are accepted for numeric domains. The module retains ownership of validation and storage.',
    "remember": ('usage: (remember "text")\n'
                 'Native mode stores the write intent first and returns MemoryQueued with its ID.\n'
                 'The existing daemon embeds it asynchronously; retries and restart recovery are automatic.\n'
                 'MemoryQueued means durably retained, not yet searchable. memory-read shows its current state.'),
    "memory-edit": 'usage: (memory-edit "id" "replacement text")\nRevise semantic memory under the same ID and re-embed it. Earlier versions remain in memory-history; the write survives an embedding outage.',
    "forget": 'usage: (forget "id" ["reason"])\nRemove semantic memory from retrieval and cancel its queued edits. Late embeddings cannot recreate it. History remains accessible; memory-edit can deliberately restore this ID.',
    "memory-read": 'usage: (memory-read "id")\nRead the current semantic record and any queued replacement without embedding a query.',
    "memory-history": 'usage: (memory-history "id")\nRead retained semantic revisions and write/forget intentions. This is separate from hierarchical memory-original.',
    "memory-pending": 'usage: (memory-pending)\nInspect durable embedding writes, attempts, next retry time and last error. No settlement chore is required.',
    "memory-retry": 'usage: (memory-retry)\nStart pending semantic writes now. Normal background retries already run during idle waits and survive restart.',
    "memory-retire": 'usage: (memory-retire "hierarchical-id" ["reason"])\nRetire a hierarchical node from search and prompt context, preserving originals. Capture and restart do not reactivate it; memory-node with an active kind deliberately does.',
    "memory-node": ('usage: (memory-node "id" kind "title" "text" [children] [sources])\n'
                    'Native CeTTa memory: episode, task, topic or project are conventional kinds.\n'
                    'Children and sources are expression lists, e.g. ("child-id").\n'
                    'Omitted lists are empty; this replaces current membership. Earlier versions stay readable.'),
    "memory-open": ('usage: (memory-open "id" [depth])\n'
                    'Expand current memory and its original bodies. Depth defaults to 1 and is adjustable.\n'
                    'Depth 0 shows this node and child IDs. Fresh/stale describes source revisions, not text accuracy.'),
    "memory-summary": ('usage: (memory-summary "id" "text")\n'
                       'Save your interpretation with the current source/membership revisions.\n'
                       'Original episodes remain available. No extra model call is made.'),
    "memory-link": 'usage: (memory-link "parent" "child")\nLink existing nodes; repeated links are harmless. Cycles and missing sources return explicit errors.',
    "memory-find": 'usage: (memory-find "phrase" ["root"])\nLiteral search across titles, summaries and full original bodies. Empty root searches all current nodes; this is not vector search.',
    "memory-original": 'usage: (memory-original "id" revision)\nRead the exact retained body and metadata at that version.',
    "memory-focus": 'usage: (memory-focus "id" [depth])\nPersist prompt focus and the default expansion depth. Empty ID follows the active task; interruptions do not erase it.',
    "memory-window": 'usage: (memory-window characters)\nSet the prompt excerpt size, default 8000 characters, 0 unlimited. This does not limit stored memory or memory-open.',
    "mail-send": ('usage: (mail-send "name" "text" ["stable-id"])\n'
                  'Native CeTTa mail, delivered automatically as peer input.\n'
                  'Mail labelled [automatic mail from ...] is information; answer it only when\n'
                  'an answer is useful. Reply with mail-send. Reusing an ID with the same content\n'
                  'is a duplicate; different content is a conflict. IDs are optional; no filing commands.'),
    "mail-receive": ('usage: (mail-receive)\n'
                     'Check native mail now. Arrivals also enter turns automatically, labelled\n'
                     '[automatic mail from ...] when marked Auto-Submitted; acknowledgement follows\n'
                     'a committed turn. CeTTa only.'),
    "task-read": 'usage: (task-read "record-id" offset [page-bytes])\nRead an exact retained input or call/result pair, including archived evidence. Offsets count characters; next=null ends it.',
    "task-history": 'usage: (task-history "request-id|conversation:owner" offset [count]); count=0 reads all entries\nA request gives its task-family call index. The conversation root shown in TASK_CONTEXT indexes all input and call occurrences across frames, including compacted history.',
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
        'usage: (background "command string") or (background "command string" timeout_seconds)\n'
        'CeTTa accepts a selected deadline; zero means none. Runs under bash and returns at once with a job\n'
        'id and its output file. When it ends you are woken by a [runtime]\n'
        'activity line with the exit status, duration and output tail; no\n'
        'polling needed. (jobs count) selects the list window (zero means all), '
        '(job-output "id" bytes) selects the output tail, '
        '(job-stop "id" signal_number) selects the signal on CeTTa. '
        'All have defaults when the optional argument is omitted. '
        'A service restart ends jobs; saved output remains readable.'),
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
        'Updates the inventory; it does not create a timer or schedule a review.\n'
        'Use (rest seconds) to choose your next wake.\n'
        'Notes and timestamps are self-reports, not verified completion.'),

    "goal-drop": (
        'usage: (goal-drop "name" "reason")\n'
        'Retires the goal with your reason: it leaves the inventory, goes to the\n'
        'compost log, and its open cycles close without counting as achieved.\n'
        'Reason "solved" claims achievement and needs a last-verified date.\n'
        'goal-write brings a retired goal back.'),
    "pin": (
        'usage: (pin "short state line")  |  (unpin "fragment")\n'
        'Persists to disk, survives restarts, shown every turn under PINNED.\n'
        'CeTTa retains the full text and every pin until you remove it. (pins) reads them all.\n'
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
        'usage: (query "phrase" [count] ["similarity|attention|hybrid"])\n'
        'CeTTa uses the persistent attention-policy mode when omitted. Distances remain cosine distances; attention-last explains the ranking.\n'
        'Semantic results include stable IDs, revisions, timestamps, distance and text.\n'
        'Count defaults to maxRecallItems; native mode accepts 0 for all records.\n'
        'Unindexed reports queued writes. MemoryUnavailable means retrieval failed, not that memories are absent.'),
    "memory-health": 'usage: (memory-health)\nRun the actual embedding and native retrieval path, returning structural health without memory text. An empty working collection is healthy.',
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


def command_error(command):
    if not isinstance(command, (list,tuple)) or not command or not isinstance(command[0],str):
        return "COMMAND_ERROR: expected (skill arguments...)"
    bounds = ARITY.get(command[0])
    if bounds and (len(command)-1 < bounds[0] or bounds[1] is not None and len(command)-1 > bounds[1]):
        return "COMMAND_ERROR: wrong number of arguments. " + help(command[0])
    return ""


PAGES['context-policy'] = 'usage: (context-policy), (context-policy task_tokens) or (context-policy "full")\nThe task view uses the whole model window by default, like Codex: steps accumulate and the oldest are archived at 90% of the window. A number opts into a token budget; "full" or 0 returns to the whole window. Older evidence remains in task-history/task-read.'
PAGES['task-resolve'] = 'No resolution bookkeeping is required. Calls remain in task-history.'

def command_names_text():
    """Snapshot the bare-command vocabulary for native response parsing."""
    import command_arity
    return command_arity.names_text()
