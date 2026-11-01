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

## Versioned console installation

The console has a separate offline build/install path. It does not deploy a
cognitive runtime, start a service, or change an instance. Use Python 3.11 or
newer with the exact distributions in `config/console-dependencies.json` already
installed (the builder never downloads anything):

```sh
python3 scripts/build_console_release.py --output "$BUILD/console-release"
python3 scripts/install_console.py --release "$BUILD/console-release" \
  --prefix "$PREFIX" --python "$PYTHON" --dry-run
python3 scripts/install_console.py --release "$BUILD/console-release" \
  --prefix "$PREFIX" --python "$PYTHON" --apply --backup-dir "$BACKUPS/console-before"
```

`BUILD`, `PREFIX`, `PYTHON` (an executable file, not a shell command), and `BACKUPS`
are operator-selected paths. The default prefix is `~/.local`. Build outside the
prefix; use a new backup directory outside both the prefix and build input.
Omitting `--apply` is non-mutating inspection, including isolated import checks.
An existing launcher or selection that differs requires `--replace-existing`
explicitly, even if it is a symlink. Inspection reports prior content as base64,
its hash, file mode or exact link target; keep this output private if a custom
launcher contains sensitive settings. No custom shell is parsed or discarded
without that consent and a durable backup.

For migration, carry any embedded `--mail-script` binding forward explicitly:
add `--mail-script "$MAIL_SCRIPT"` to both inspect and apply. Otherwise the
unchanged frontend discovers the installed `claw-mail` as before. Other custom
launcher flags must be supplied explicitly when launching; arbitrary shell
configuration is not automatically translated. Instance profiles, transport
configuration, display paths, client state, history and retry identities are
not copied, moved or rewritten.

Both `bin/agent-console` and `bin/godel-cli` delegate through one
`share/godelclaw/console-current` selection. `agent-console` passes generic
frontend flags unchanged; `godel-cli` prepends `--agent godel`. Explicit arguments
come last, so `godel-cli --agent other` and `godel-cli --agent=other` override that
default. A bound mail script can likewise be overridden with `--mail-script`.
All argument boundaries, including spaces and multiline input, are preserved.

The release contains the reviewed files in `config/console-files.json` and the
import closure of Rich and prompt_toolkit: Pygments, markdown-it-py, mdurl and
wcwidth. Installed distribution RECORD hashes are checked before copying;
package metadata and licenses are retained, while bytecode, console scripts and
installation-local metadata are omitted. No symlinks into a checkout, environment
or qualification run are used. Local client helpers also need `mode_channel`,
`durable_telegram`, `stream_sink` and `stream_lifecycle`; the frontend uses only
their storage/display interfaces, not their service entry points.

The SHA-256 manifest identifies every bundled file and its mode. Identical source
and installed distribution bytes produce the same release identity. Installation
verifies the manifest, stages a complete copy, and refuses to repair, merge or
overwrite changed content at an existing release identity. Launcher bindings
have a separate identity covering the release, interpreter bytes and shell
content. The external interpreter must match the builder's Python major/minor;
it is not bundled. Launch uses `-I -S -B` so imports cannot silently fall back to
user sites, environment sites, mutable source or bytecode caches. The interpreter,
standard library and existing mail/runtime deployment remain prerequisites;
this is not a Python or cognitive runtime updater.

Rollback uses the exact prior launcher bytes/modes or symlink targets, including
dangling links and previously absent paths:

```sh
python3 scripts/install_console.py --rollback "$BACKUPS/console-before"
python3 scripts/install_console.py --rollback "$BACKUPS/console-before" --apply
```

The backup receipt is durable before any selection or launcher replacement.
Each replacement is atomic; subsequent installs switch the shared selection in
one operation. First migration from unrelated launchers necessarily replaces
two separate entry points, so do not invoke them during that short migration.
A failed/interrupted apply can be rolled back with the same receipt; it is not
automatically retried. Rollback preflights all destinations and refuses later
manual edits rather than destroying them. Cooperating installs/rollbacks use a
prefix-local lock. Trusted local directories/receipts and no concurrent manual
filesystem edits are assumed; manifests provide byte integrity, not signatures
against a malicious local writer. Published snapshots and old releases remain
after rollback for inspection; no garbage collection or live-session restart is
performed. A running frontend is unaffected by switching launchers.

Packaging regression checks (no live attachment):

```sh
python3 -B -m unittest discover -s tests -p test_console_installation.py -v
```

## Session behavior

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
