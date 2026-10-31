# Attaching a terminal to an agent

The initial console attaches to an existing agent through authenticated mail
backed by the Space service. Controls reuse Telegram's MeTTa handlers, and work
output follows the existing observational display. It starts no cognitive loop
and never uses the display cache as an input queue.

```sh
python scripts/agent_console.py --agent NAME
python scripts/agent_console.py --agent NAME --status --json
python scripts/agent_console.py --agent NAME --command /models --json
python scripts/agent_console.py --agent NAME --submit-file INPUT_FILE --json
```

A configured instance supplies its existing root, state and qualified interpreter.
`--root`, `--state` and `--mail-script` support other local installations. Standard
input is accepted with `--submit -`, avoiding command-line size limits. Install
the declared Python dependencies for the interactive composer.

`/detach` or Ctrl-D leaves cognition running. `/stop` explicitly pauses it.
`/retry` resubmits retained input with its original identity; it does not repeat
model-selected external actions. `/replies` reads retained local replies.

The structured status is a versioned set of timestamped runtime observations.
Unavailable facts stay unknown. A completed provider call is not evidence that
a task or goal is complete.

The default terminal should combine live work output with an input composer:

```text
Agent · provider backoff · input retained
14:02  input accepted · request 8ad...
14:03  tool finished · artifact 41b...

> /models
1  current model [selected]
2  another available model
   next page / previous page / cancel

> Continue the existing task, preserving its earlier constraints.
accepted · request b52... · waiting for provider
```

Controls reuse the same handlers and menu data as Telegram: models, modes,
engines, fuel, activity, health, start, stop and wake. Menu selection is explicit;
opening a menu never changes a setting. Provider failures do not prevent typing,
submitting input or using controls. Display errors do not consume input.

Normal output belongs in terminal scrollback. Multiline paste, Unicode, keyboard
selection and tmux copy mode should work without mouse capture or an alternate
screen. Rich rendering can remain an optional display mode. Raw tool output
cannot emit terminal control sequences; complete output remains retrievable.

Interactive and scripted clients share these contracts:

- Attach to a named existing session; detaching leaves the agent running.
- Submit with a stable occurrence ID; report acceptance only after durable storage.
- Poll versioned events by cursor; distinguish accepted, running, waiting for
  external input, provider backoff, failed, cancelled and completed outcomes.
- Resume using session, input, action and receipt identities. A lost reply does
  not justify repeating an external action.
- Keep input cancellation, cancellation of a running tool and stopping cognition
  distinct. Preserve the resulting receipts and any uncertain effects.
- Authenticate the local principal at the service boundary. Display text,
  mailbox names and model-generated summaries cannot confer operator authority.
- Offer plain text and structured output over the same operations, so a test
  harness needs no special model prompt to determine runtime state.

The work display remains a replaceable projection. Authoritative inputs,
permissions, snapshots and receipts belong to the shared runtime and ordinary
persistent MeTTa Space interfaces. A terminal adapter must not create a second
knowledge store or depend on fabricated Telegram updates.

Qualification covers native shared menus without inference, controls during a
blocked input publication, multiline bracketed paste in a real terminal, lost
acceptance replies, and reply persistence before acknowledgement.

The backend may refuse input beyond its advertised storage/transport capabilities.
The client reports the actual refusal, retains the original for retry and does
not announce acceptance or truncate the text. Removing remaining backend bounds
and providing a common session service remain native Space work.

Further qualification must cover actual agent replies after live attachment,
detach/reconnect, lost acceptance replies, restart, duplicate submissions,
interleaved output during a multiline paste, large catalogues, and copying text
from tmux scrollback. The same logical session must remain usable through each
frontend independently.
