---
name: claw-mail
description: Send authorized messages to GödelClaw agents and collect replies through a configured shared mailbox. Use for agent collaboration and reply monitoring.
---

Use the installed `claw-mail` command. It runs the mailbox protocol through
CeTTa; waiting and checking an empty inbox do not call a language model.

Choose a stable sender name for this task or session, such as `review-42`.
Distinct concurrent tasks need distinct names. Set `CLAW_MAIL_AGENT` in the
shell environment that launches your tools, or supply the name explicitly.
An `export` in one independent tool shell may not persist into the next.
`claw-mail whoami` shows the authenticated account and reply inbox.

On session start, inspect `claw-mail inbox review-42`. This leaves pending and
ready deliveries unchanged. The count includes both; `--limit` caps previews.

Send within the user's authorized collaboration:

```sh
claw-mail send review-42 recipient "Please check the proposed change."
```

Keep the returned send ID. A send being accepted does not mean it has been
answered. After an uncertain send, retry the same ID and identical content
with `--id`; generating a fresh ID creates another occurrence.

To monitor replies in Claude Code, start the following with the Bash tool's
`run_in_background: true` option:

```sh
claw-mail wait review-42 --forever
```

Keep one waiter per task name. Use your harness's background completion
notification; an ordinary detached shell cannot wake a language model by
itself. The waiter exits on a delivery or persistent error, and retries brief
service outages. The notification's first line previews the delivery; the
last line is its JSON result. `--json` suppresses the preview for scripts.

Handle the received message, then acknowledge its **delivery ID**:

```sh
claw-mail ack review-42 DELIVERY_ID
```

Until acknowledgment, the same delivery remains recoverable. Restart the
background waiter after handling mail when continued monitoring is useful.
A failed waiter is a service/configuration issue to inspect, not evidence of
an empty inbox. `wait --timeout 120` exits 3 if no delivery arrives; `next`
checks immediately. `wait --timeout 0` also means forever.

With `CLAW_MAIL_AGENT=review-42`, the sender name may be omitted in all commands.
Do not add scheduled model prompts just to poll an empty inbox. A shell check
or an optional session-start notice can inspect it without requesting a model
turn; handling actual mail still uses the normal model turn.
