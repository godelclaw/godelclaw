# Coding-mode working guidance

These are working rules for the coding task. They do not define a persona or
replace the identity of the agent that requested the work.

- Establish the objective and acceptance checks before implementing. Read the
  relevant source, callers, sibling entrypoints and applicable local guidance.
- Treat scope limits as constraints on the work, not permission to leave a
  correctness defect hidden. If correctness requires crossing a boundary,
  explain the conflict and the smallest required change to the requester.
- Check failure paths, cleanup, cancellation, restart and unwinding when they
  matter. Inspect the final diff and challenge the change before delivery.
  Test or disclose each concrete unresolved risk; do not write controls that
  avoid the disputed behavior or weaken expectations to obtain a pass.
- Report evidence precisely. Distinguish observed behavior from assumptions,
  baseline failures from regressions, and locally tested, committed, deployed
  and verified-live changes. Complete authorized integration and rollout.
- Preserve other work in shared checkouts. Stage explicit files and review
  the staged diff. Do not push or create a PR without authorization.
- Be considerate on shared machines: check available capacity before heavy
  jobs, use low priority, and avoid overlapping heavy builds from this task.
  Throttle this task instead of asking another user or lane to yield.
- Keep credentials and private material out of output, code, commits and
  public reports. Check generated artifacts and comments before publication.
  Do not read private SSH keys. Follow the project's privacy instructions.
- Keep valuable artifacts in the project's designated scratch or reporting
  locations. Temporary directories are disposable and are not handoff storage.
- Use the native coding tools. Wait substantially for a running build rather
  than repeatedly asking the model to poll; explicit short waits remain useful
  when steering or independent work requires them. Verify uncertain effects
  before retrying after an interruption.
