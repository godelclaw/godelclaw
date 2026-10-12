!(import! &self ./src/topic_jobs.py)
!(import! &self ./src/mailbox_agent.py)
(= (getSkills)
   (;INTERNAL:
    "- Remember a particular string such as skills and memories: (remember \"string\")"
    "- Query long-term embedding memory for skills and memories with short phrases only: (query \"short phrase\")"
    "- Pin a working-memory line — persists across restarts, shown every turn under PINNED, cap 12: (pin \"string\")"
    "- Remove pins containing a fragment: (unpin \"fragment\")"
    "- Goal stack + mood (kernel decays STI, consolidates LTI, flags stuck/cull-review at each wake): (goals)"
    "- Add/update one goal (liberal format, see (help \"goal-write\")): (goal-write (goal name Area sti:0.8 lti:0.3 vibes:(a b) blocked-by:none last-verified:never note:why it matters))"
    "- Retire a goal with a reason, or close it with reason solved once last-verified is set; it goes to the compost log and goal-write can bring it back: (goal-drop \"name\" \"reason\")"
    "- Rest now, ending this burst until the next human message: (rest)"
    "- Rest now and poll more slowly while idle: (rest seconds)"
    "- Rest with a stated intention shown at your next waking: (rest seconds \"why\")"
    "- Rest, then run one quoted command without another model call: (rest seconds (quote (command ...)))"
    "- Finish this fast burst without suspending autonomous renewal: (nop)"
    "- Inspect/select the persistent cognitive loop policy: (mode) | (modes) | (mode-set \"name\")"
    "- Retained inputs and work: (requests) | (request-page \"offset\") | (request-status \"id\") | (request-read \"id\" \"offset\"); use (request-disposition \"id\" \"keep|waiting-user|defer|addressed|reopen\" \"reason and closing condition\")"
    ;ITER REPROGRAMMING:
    "- List active/disabled hot-reloaded MeTTa Iter request transformations: (iter-transformations)"
    "- Directly install or replace a mutable MeTTa transformation for the next model turn; failures stutter locally: (iter-transform-write \"10_name.metta\" \"(= (iter-transform $visible) (success $visible))\")"
    "- Reversibly disable or enable one Iter transformation using the leading-underscore convention: (iter-transform-disable \"10_name.metta\") | (iter-transform-enable \"10_name.metta\")"
    "- Inspect authoritative command receipts: (command-receipts), then (command-receipts \"NEXT_CURSOR\") for more"
    "- Separate topic notes, always keeping operator messages reachable: (topics) | (topic-select \"name\") | (topic-note \"notes\")"
    "- Start a concurrent read-only model strand: (background-model \"topic\" \"question\") | (background-model \"topic\" \"question\" \"JSON dependency names\")"
    "- Model strand evidence: (model-jobs) | (model-result \"id\"); inspect its receipt before (model-adopt \"id\") or (model-retire \"id\")"
    "- Wait for a configured mailbox without blocking or polling: (mailbox-wait \"stable-ticket\" \"channel\" \"correlation\"). An arrival wakes you with inert data. (mailbox-status) lists pending waits; (mailbox-read \"ticket\") reads a reply, (mailbox-read \"ticket\" offset count) pages it; (mailbox-cancel \"ticket\") cancels an unmatched wait."
    "- Subscribe to an existing Codex-room turn: (mailbox-codex \"stable-ticket\" \"thread-id\" \"turn-id\"). It returns immediately and wakes you with the terminal transcript; no model call or thread polling is started."
    "- Write to your sibling agent's mailbox, not through Telegram: (mailbox-send \"name\" \"text\"). The note carries your name and arrives as peer mail; it wakes them at most 6 times an hour from you, later notes wait for their next turn. Their notes to you arrive as [mailbox] data with \"from\"."
    ;SHELL AND FILE I/O:
    "- Execute shell command, it returns the command output to you: (shell \"command string\")"
    "- Run a long command in the background; you are woken when it ends, with its exit status and output tail: (background \"command string\")"
    "- Background jobs and their state: (jobs) | the end of a job's output: (job-output \"id\") | stop one: (job-stop \"id\")"
    "- The machine and you right now, on demand: uptime, load, memory and its stalls, swap, heaviest processes: (vitals)"
    "- Independent execution gateway, when configured: (gateway-tools) lists named tools and input schemas; (gateway-start \"stable-id\" \"tool\" \"json-object\") returns immediately; inspect completion with (gateway-status \"stable-id\") or request cancellation with (gateway-cancel \"stable-id\")."
    "- Read file to string: (read-file \"filename\")"
    "- Replace a file with exactly this content; the previous version is kept as a backup: (write-file \"filename\" \"string\")"
    "- Append one line to an existing file: (append-file \"filename\" \"string\")"
    "- Read a numbered window of a file: (read-lines \"filename\" start count)"
    "- Replace a string that occurs exactly once in a file: (edit-file \"filename\" \"old-string\" \"new-string\")"
    "- Compact directory tree, dirs first with file sizes: (ls-tree \"dir\" depth)"
    "- Search text across files under a dir, bounded output: (grep-files \"pattern\" \"dir\")",
	    ;COMMUNICATION CHANNELS:
	    "- Send message to user: (send \"string\")"
	    "- Send Telegram message to a specific chat id: (send-telegram-chat \"chat_id\" \"string\")"
	    "- Send a FILE to the chat as an attachment: (send-file \"/path/to/file.md\" \"caption\")"
	    "- Send an IMAGE so it previews inline; .svg is auto-converted to png: (send-image \"/path/diagram.svg\" \"caption\")"
	    "- Clean up after a test send: delete your own last k messages to a chat: (delete-my-recent \"chat_id\" k)"
	    "- Delete an exact known bot-owned Telegram message: (delete-message-exact \"chat_id\" message_id)"
	    "- Arming energy per sender (how many loops a message grants): (energy) to view, (energy-set \"default|Name|id\" \"full|mid|light\")"
	    "- Sibling claw control, watchdog-safe: (sibling-status) | (sibling-on) | (sibling-off \"reason\")"
	    "- Last k messages (both sides) from a chat by title or id: (recent-messages \"chat\" k)"
	    "- Search the web using the configured provider: (search \"string\")"
    "- Search the web using Tavily API: (search-tavily \"string\")"
    "- Search the web using DuckDuckGo HTML: (search-ddg \"string\")"
    ;LLM API:
    "- Check your embedding memory's health — how far the on-disk index trails your writes: (memory-health)"
    "- Attempt memory-index catch-up and report measured before/after drift: (memory-compact)"
    "- Check your LLM API budget — model in use, credits left, rate window: (llm-quota)"
    "- List the LLM models you can use: (llm-models)"
    "- Switch your LLM model for this session: (llm-set-model \"model-name\")"
    ;MCP:
    "- List configured MCP servers from .mcp.json: (mcp-servers)"
    "- List MCP tools exposed by a server: (mcp-tools \"server-name\")"
    "- Show one MCP tool's argument schema: (mcp-tool-info \"server-name\" \"tool-name\")"
    "- Call any MCP tool with JSON object arguments: (mcp-call \"server-name\" \"tool-name\" \"json-object-string\")"
    ;CODE EXECUTION:
    "- Execute MeTTa expression: (metta \"expression string\")"
    ;CLAUDE CODE AND TMUX:
    "- Ask Claude Code one headless question (needs the operator's authorization toggle): (claude-code \"prompt\")"
    "- List tmux windows: (tmux-windows)"
    "- Read the last n lines of any window on this user's tmux server: (tmux-peek \"session:window\" 30)"
    "- Type into any window on this user's tmux server: (tmux-send \"session:window\" \"message\")"
    ;WHEN UNSURE:
    "- Man page for any skill (usage, example, common failure): (help \"skill-name\")"
    "- Dry-run a command string through the real parser, nothing executes: (check \"(shell \\\"ls /tmp\\\")\")"))

;; Background jobs: the shell skill's 5-second limit does not apply, and the
;; runtime wakes the agent when a job ends.  Vitals are read on demand.
(= (background $command)
   (py-call (jobs.start (py-call (helper.decode_safe_text $command)))))
(= (jobs) (py-call (jobs.status)))
(= (job-output $id) (py-call (jobs.output $id)))
(= (job-stop $id) (py-call (jobs.stop $id)))

;; Only the host checkpoint settles a mailbox reply. Worker content is data.
(= (mailbox-wait $ticket $channel $correlation)
   (py-call (mailbox_agent.wait $ticket $channel $correlation)))
(= (mailbox-status) (py-call (mailbox_agent.status)))
(= (mailbox-read $ticket) (py-call (mailbox_agent.read $ticket)))
(= (mailbox-read $ticket $offset) (py-call (mailbox_agent.read $ticket $offset)))
(= (mailbox-read $ticket $offset $count) (py-call (mailbox_agent.read $ticket $offset $count)))
(= (mailbox-cancel $ticket) (py-call (mailbox_agent.cancel $ticket)))
(= (mailbox-codex $ticket $thread $turn) (py-call (mailbox_agent.watch_codex $ticket $thread $turn)))
(= (mailbox-send $to $text)
   (let $safe (string-replace $text "\n" "\\n")
     (py-call (mailbox_agent.send $to (py-call (helper.decode_safe_text $safe))))))
(= (vitals) (py-call (helper.vitals)))

;; Shared execution code, separate agent-owned queues. These requests never
;; start a gateway and do not replace any existing skill implicitly.
(= (gateway-tools) (py-call (gateway_bridge.tools)))
(= (gateway-start $id $tool $json)
   (py-call (gateway_bridge.start $id $tool
             (py-call (helper.decode_safe_text $json)))))
(= (gateway-status $id) (py-call (gateway_bridge.status $id)))
(= (gateway-cancel $id) (py-call (gateway_bridge.cancel $id)))

;; read-file explains failure instead of silently returning nothing: the old
;; Prolog chain FAILED (empty result, no reason) on a missing/unreadable path,
;; which cost real time — the file existed but a relative path did not resolve
;; from the loop's cwd, and nothing said so.
(= (read-file $file)
   (py-call (fsops.read_file $file)))

(= (write-file $file $str)
   (py-call (file_tools.write_file $file $str)))

(= (edit-file $file $old $new)
   (py-call (file_tools.edit_file $file $old $new)))

(= (append-file $file $str)
   (py-call (file_tools.append_file $file $str)))

!(import_prolog_functions_from_file ./src/skills.pl
                                    (shell first_char run-command-batch-once command-batch-errors))

(= (metta $str)
   (let $code (sread $str)
        (eval $code)))

;; This is Iter's mutable developmental seam, deliberately distinct from
;; proposal-bound edits to stable source.  A captured in-flight request is
;; never rewritten.
(= (iter-transformations)
   (py-call (iter_authoring.list_transformations)))

(= (iter-transform-write $name $source)
   (py-call (iter_authoring.write_transformation $name $source)))

(= (iter-transform-disable $name)
   (py-call (iter_authoring.disable_transformation $name)))

(= (iter-transform-enable $name)
   (py-call (iter_authoring.enable_transformation $name)))

;; Rest is a pause, not a spend. The budget still held is banked by the loop
;; and handed back when the rest ends, so a short nap no longer costs a whole
;; breath. The loop does the banking because only it can see the periphery.
(= (rest)
   (progn (loop-directive-set rest-request 1)
          (loop-directive-set pending-continuation (quote no-continuation))
          (RESTING budget-banked)))

(= (rest $seconds)
   ;; Rest duration remains the agent's decision. An authenticated operator
   ;; message or /wake may deliberately end it; other activity queues.
   (let $n (max 1 (py-call (int $seconds)))
        (progn (loop-directive-set rest-request 1)
               (loop-directive-set sleep-interval $n)
               (loop-directive-set pending-continuation
                                   (quote no-continuation))
               (RESTING budget-banked sleep-interval-seconds $n))))

(= (rest $seconds (quote $continuation))
   ;; The opaque constructor keeps the command inert when it is read from the
   ;; generic coordinate store.  The wait adapter is the sole consumer that
   ;; unwraps it after the timer expires.
   (let $n (max 1 (py-call (int $seconds)))
        (progn (loop-directive-set rest-request 1)
               (loop-directive-set sleep-interval $n)
               (loop-directive-set pending-continuation
                                   (continuation-value $continuation))
               (RESTING budget-banked
                        sleep-interval-seconds $n
                        continuation (quote $continuation)))))

(= (rest $seconds $why)
   ;; Preserve the original intention-bearing rest.  Unlike a quoted command,
   ;; text is context for the next waking rather than executable data.
   (let $n (max 1 (py-call (int $seconds)))
        (progn (loop-directive-set rest-request 1)
               (loop-directive-set sleep-interval $n)
               (loop-directive-set pending-continuation
                                   (quote no-continuation))
               (py-call (helper.intent_save
                          (py-call (helper.decode_safe_text $why))))
               (RESTING budget-banked
                        sleep-interval-seconds $n
                        intent (py-call (helper.decode_safe_text $why))))))

;; A burst ends at its step bound or via nop. Ending a burst is a pacing
;; decision; protected work remains active until its task checker accepts.
(= (nop)
   (progn (loop-directive-set loops 0)
          (loop-directive-set burst-end nop)
          (BURST_COMPLETE loops-left 0)))

(= (mode) (py-call (loop_modes.mode_view)))
(= (modes) (py-call (loop_modes.modes_view)))
(= (requests) (py-call (strategy_host.view)))
(= (request-status $request) (py-call (strategy_host.request_status $request)))
(= (request-focus $request) (py-call (strategy_host.focus_request $request)))
(= (request-page $offset) (py-call (strategy_host.request_page $offset)))
(= (request-read $request $offset) (py-call (strategy_host.request_read $request $offset)))
(= (request-disposition $request $disposition $reason)
   (py-call (strategy_host.request_disposition $request $disposition $reason)))
(= (mode-set $name) (py-call (loop_modes.set_mode $name)))

;; Telegram file/image delivery. The result string says what happened —
;; "sent <name> (<n> bytes) to chat <id>" or the actual failure reason.
;; send-image previews inline; SVG is rasterized to PNG when a converter
;; exists, otherwise delivered as a file (Telegram cannot preview SVG).
(= (send-file $path) (py-call (telegram.send_file $path "")))
(= (send-file $path $caption)
   (py-call (telegram.send_file $path (py-call (helper.decode_safe_text $caption)))))
(= (send-image $path) (py-call (telegram.send_image $path "")))
(= (send-image $path $caption)
   (py-call (telegram.send_image $path (py-call (helper.decode_safe_text $caption)))))

;; Filesystem awareness: line-numbered reads, exact-string edits with
;; uniqueness enforced, bounded tree/search. Result strings are the
;; verification — no blind writes, no re-reading to check what landed.
(= (read-lines $f $start $count) (py-call (fsops.read_lines $f $start $count)))
(= (ls-tree $dir $depth) (py-call (fsops.ls_tree $dir $depth)))
(= (grep-files $pat $dir)
   (py-call (fsops.grep_files (py-call (helper.decode_safe_text $pat)) $dir)))

;; Autonomic goal-stack (to-mama spec v0.2): kernel math runs at wake events;
;; these skills are the semantic interface (appraisal, birth, death-review).
(= (goals) (py-call (goals.goals_view)))
(= (goal-write $atom)
   (py-call (goals.goal_write (py-call (helper.decode_safe_text $atom)))))
(= (goal-drop $name $reason) (py-call (goals.goal_drop $name $reason)))
(= (goal-review-save $digest $entries)
   (py-call (strategy_host.goal_review_save $digest (py-call (helper.decode_safe_text $entries)))))

;; Claude Code bridge + tmux interface. Headless claude-code remains gated by
;; the operator toggle. Every window on this Unix user's tmux server is shared
;; agent workspace, so tmux-send needs no separate authorization layer.
(= (claude-code $prompt)
   (py-call (claude_bridge.ask (py-call (helper.decode_safe_text $prompt)))))
(= (tmux-windows) (py-call (tmux_bridge.windows)))
(= (tmux-peek $win $n) (py-call (tmux_bridge.peek $win $n)))
(= (tmux-send $win $text)
   (py-call (tmux_bridge.send $win (py-call (helper.decode_safe_text $text)))))

;; Receipt-bound terminal capabilities used by qualification and by a future
;; live broker only after it passes qualification. With the shadow effect
;; provider, the dispatcher intercepts these already-parsed commands before
;; eval and handles them on an isolated tmux socket. In ordinary operation the
;; equations are inert refusal values and grant no authority.
(= (tmux-new-shell-after $receipt $name)
   (SHADOW_EFFECT_REQUIRES_QUALIFICATION_BACKEND $receipt $name))
(= (tmux-send-observed $receipt $text)
   (SHADOW_EFFECT_REQUIRES_QUALIFICATION_BACKEND $receipt $text))
(= (shadow-finish $summary)
   (SHADOW_EFFECT_REQUIRES_QUALIFICATION_BACKEND $summary))

;; Progressive disclosure: one-line catalog above, full page on demand.
;; check parses with the loop's OWN reader (sread), so its verdict is the
;; loop's verdict — nothing is executed either way.
(= (help) (py-call (skills_help.help "")))
(= (help $name) (py-call (skills_help.help (py-call (helper.decode_safe_text $name)))))
(= (check $str)
   ;; repr BEFORE the bridge: a caught syntax_error compound cannot cross
   ;; janus (Domain error: py_term) — as a string it always can.
   (let $decoded (py-call (helper.decode_safe_text $str))
        (let $parsed (catch (sread $decoded))
             (let $rendered (repr $parsed)
                  (py-call (skills_help.verdict $decoded $rendered))))))

;; Pins: real, persisted working memory (previously catalog-only — the call
;; just echoed itself into results feedback and vanished on restart).
(= (pin $text) (py-call (pins.pin (py-call (helper.decode_safe_text $text)))))
(= (unpin $frag) (py-call (pins.unpin (py-call (helper.decode_safe_text $frag)))))

;; Arming energy (persisted per-sender loop tiers) and sibling-claw control.
;; sibling-on/off always handle the watchdog pause marker too, so a stop
;; STAYS stopped and a start is not raced by the reviver.
(= (energy) (if (tg:client-metta?) (energy:view) (py-call (telegram.energy_view))))
(= (energy-set $who $tier)
   (if (tg:client-metta?) (energy:set $who $tier) (py-call (telegram.energy_set $who $tier))))
(= (sibling-status) (py-call (sibling.sibling_status)))
(= (sibling-on) (py-call (sibling.sibling_on)))
(= (sibling-off $reason)
   (py-call (sibling.sibling_off (py-call (helper.decode_safe_text $reason)))))

;; LLM API introspection (synthetic.new): budget, model list, session model switch.
(= (memory-health) (py-call (memory_health.report)))
(= (memory-compact) (py-call (memory_health.compact)))
(= (llm-quota) (py-call (synthetic_llm.quota)))
(= (llm-models) (py-call (synthetic_llm.models)))
(= (llm-set-model $name) (py-call (synthetic_llm.set_model $name)))

;; Generic MCP bridge. Servers are configured in .mcp.json; arguments are JSON.
(= (mcp-servers) (py-call (mcp_bridge.servers)))
(= (mcp-tools $server) (py-call (mcp_bridge.tools $server)))
(= (mcp-tool-info $server $tool) (py-call (mcp_bridge.tool_info $server $tool)))
(= (mcp-call $server $tool $json)
   (let $args (py-call (helper.decode_safe_text $json))
        (py-call (mcp_bridge.call_tool $server $tool $args))))

(= (command-receipts) (py-call (strategy_host.command_receipts)))
(= (command-receipts $cursor) (py-call (strategy_host.command_receipts $cursor)))
(= (topics) (py-call (topic_jobs.topics)))
(= (topic-select $name) (py-call (topic_jobs.select $name)))
(= (topic-note $notes) (py-call (topic_jobs.note (py-call (helper.decode_safe_text $notes)))))
(= (background-model $topic $question) (py-call (topic_jobs.start $topic (py-call (helper.decode_safe_text $question)))))
(= (background-model $topic $question $dependencies)
   (py-call (topic_jobs.start $topic (py-call (helper.decode_safe_text $question)) (py-call (helper.decode_safe_text $dependencies)))))
(= (model-jobs) (py-call (topic_jobs.status)))
(= (model-result $id) (py-call (topic_jobs.results $id)))
(= (model-adopt $id) (py-call (topic_jobs.adopt $id)))
(= (model-retire $id) (py-call (topic_jobs.retire $id)))
