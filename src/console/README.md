# Native attach client

The attach client runs on Python-disabled CeTTa with PeTTa extended. It keeps
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
The installer also accepts legacy schema 1 manifests for rollback. New console
artifacts contain no Python modules or vendored Python dependencies.
