# Native attach client

The native attach frontend is experimental. Its production rollout was withdrawn
after the presentation failed to preserve the established console: raw event
names, repeated reconciliation notices, missing block styling and reduced status
information made it unsuitable for everyday use. Functional markers and input
latency alone do not qualify its appearance or interaction behavior.

The standalone native viewer is also experimental. Its JSON path must apply
the same enabled/epoch/session boundary as formatted output. The established
viewer remains available while grouping, plans/timing and viewport behavior
are compared; a curated-note CLI test does not qualify those workflows.

Console builds select the established frontend by default. `--frontend native`
builds the port for continued development. A replacement must compare the same
event recordings with the established block renderer, including incremental
text, reconciliation, Markdown, tool grouping/folding and the activity/status
rows. Native performance must be measured on those complete interactions.

Use `install_console.py --attach-only` when replacing just the editor frontend.
This preserves independent worklog bindings and
cognitive services. A legacy frontend is a Python runtime role; it does not count
as completion of the full native-runtime migration.

The experimental native client runs on Python-disabled CeTTa with PeTTa extended. It keeps
one owned edit buffer and an observation-only display projection. The terminal
library supplies modes, readiness, Unicode boundaries and cell widths;
MeTTa supplies key bindings, menus, history, formatting and command policy.

`scripts/agent_console_cetta.metta` accepts the existing instance, state,
submission, command and status options. Enter submits; Alt-Enter or Ctrl-J
inserts a newline. Bracketed paste retains multiline text. Ctrl-C interrupts
the draft; `/detach` and Ctrl-D on an empty draft leave cognition running.
Drafts are saved separately from mail acceptance, and restored without sending.

Pending, accepted and received records retain their existing formats.
A send failure preserves the original ID for `/retry`. Replies are archived
before acknowledgement. Native mailbox clients and the existing native control
process have independent owners, so service delays leave the editor responsive.
An uncertain control request is reported without automatic replay.

Stream offsets advance by bytes read. Complete buffered records drain after
file EOF; a partial record waits for more bytes. Directory notifications
identify changed producers, and overflow schedules a rescan. Display text and
command descriptions remain data. Rendering strips their terminal controls,
then supplies its own cursor and colour sequences. Large values fold explicitly;
their source logs and reply archives remain available.

Frames retain their bytes through partial writes. The output offset advances
only by the host's physical write count. Editing continues while a frame is
pending, and the next frame uses the latest draft. Closing restores the terminal
and stops owned clients; accepted mail and cognition retain their owners.

Console manifest schema 2 names the native entrypoint and exact required engine.
The installer also accepts legacy schema 1 manifests for rollback. Explicit
native artifacts contain no Python modules or vendored Python dependencies;
the established frontend selection retains its Python runtime dependencies.
