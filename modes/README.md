# Iter and Omega modes

Iter is a selectable, differentially qualified cognitive loop in MeTTaClaw.
Omega remains visible but unavailable in the production selector until its
new native-tool core is qualified; its pinned core can still be run explicitly
for development. Each configured instance keeps its own identity, credentials,
process and mode state. A single instance can switch qualified loops without
sharing its state with another instance.
Provider access supports CeTTa's buffered `llm:v1:` library or the
Python transport, preserving each core's command-text or structured-call protocol.

Start with the [project README](../README.md) for installation and the
[service guide](../systemd/README.md) for supervised deployment. This guide
describes the mode interfaces, pinned sources, compatibility adapters and
verification scope on `main`.

## Standalone terminal launches

From a clean checkout, install `iter/requirements.txt` for ITER, or
`omega/requirements.txt` for the Omega core, in the Python environment used
by a Python-enabled CeTTa bundle. ITER needs the OpenAI-compatible SDK;
Omega additionally needs PyYAML. Neither profile loads Torch, Chroma,
sentence-transformers, MCP, or a Telegram service.

```sh
export CETTA_BIN=/path/to/python-enabled-cetta/cetta
./run.sh --mode iter
./run.sh --mode omega
```

Configure the model endpoint before starting:

| Mode | Upstream configuration retained |
|---|---|
| ITER | `LLM_MODEL`, `BASE_URL`, `AI_API_KEY`; the pinned defaults apply when absent. |
| Omega core | `OMEGA_provider`, `OMEGA_model`, and upstream provider settings. `OpenAIAPI` uses `OMEGA_openaiapi_url`, `OMEGA_openaiapi_model`, and the key variable named by `OMEGA_api_token_var` (default `OPENAIAPI_API_KEY`). The vendored modules also register `ASICloud`, `Anthropic`, and `OpenAI`. |

The two commands select the standalone profile explicitly. They do not read
an instance's identity, model-selection file, pause latch, or Telegram inbox,
even when invoked from a configured service's environment. ITER keeps the
upstream SDK request and tool-return protocol. Its terminal channel forwards
worker output at the foreground checkpoint because upstream workers suppress
stdout. Omega keeps its pinned core loop and provider modules; its transport
is terminal-only and memory uses the existing history/text adapter. Its actual
NAL and PLN libraries are loaded. Upstream Chroma and arbitrary plugin startup
are not part of this profile. The runtime's `adapter-ledger.json` records these
differences.

State is outside the checkout and survives restart. Use `--state-dir PATH`
for a separate instance, and `--prompt FILE` to append your own instructions
when seeding new state. Existing prompts, tools, channels and memory files
are retained. Add-on experimentation should use a separate state directory.
An optional SWI-PeTTa engine remains available with `--engine petta` and
`PETTA_ROOT`, using that engine's own Python bridge dependencies.

Direct launcher calls accept `--profile standalone` or `--profile supervised`.
Without an explicit profile, a configured Telegram socket selects supervised
operation; otherwise startup selects the terminal. The existing agent service
launcher requests the supervised profile explicitly.

Qualification uses a local HTTP peer and actual terminal input, checks replies,
upstream request fields and saved history, and verifies that instance controls
cannot leak into standalone startup:

```sh
MODE_TEST_CETTA_BIN=/path/to/python-enabled-cetta/cetta \
python3 -m unittest discover -s tests -p test_standalone_modes.py
```

## Select and operate a mode

| Operator command | Purpose |
|---|---|
| `/modes` | List cognitive loops and the active selection. |
| `/mode iter` | Request the Iter loop. |
| `/mode omega` | Report why Omega is unavailable until current-core qualification passes. |
| `/mode godelclaw` | Return to the framework loop. |
| `/models`, `/model` | Inspect or change the LLM independently of the loop. |
| `/engines`, `/engine` | Inspect or change the MeTTa evaluator. |

Mode changes persist and take effect through a supervised cognition restart.
The operator UI distinguishes a requested mode from a running one. Older saved
names `agent`, `default`, `generic` and `claw23` map to `godelclaw`;
`iter-coding` maps to `coding`. The current `iter` selection runs the qualified
behavior-preserving MeTTa port of the pinned upstream core.

The running process keeps its original mode until it checkpoints and exits;
a newer selection never becomes a policy inside the old loop. Startup consumes
only the previous restart request, before selecting its engine and mode.
Unexpected evaluator exits preserve the selected engine and report failure.
Automatic framework fallback to PeTTa is opt-in via
`METTACLAW_ENGINE_FALLBACK=petta`; an explicit `/engine petta` remains available.

Iter renews its work after an idle boundary and can make provider calls without
new input. Omega keeps its own wake/counter behavior. Framework fuel settings remain separate from these loops. Optional managed
admission limits and quota cooldowns are shared across modes; repository
Iter/Omega defaults follow upstream without those added limits. See the
[provider and policy guide](../README.md#operator-admission-and-upstream-profiles).

## Channel and control boundary

Operator controls are independent of the cognitive loop. Every production mode
uses an independently supervised durable Telegram service and command responder;
the launcher never starts a bot poller inside cognition. Instance templates are
provided alongside the existing single-agent units. Each instance has its own
credential, allowed chats, operator IDs, inbox, outbox and model/mode selections.

The adapters preserve Telegram origin, sender, bot, chat, thread, reply and
forwarding metadata. `telegram` addresses the configured private operator.
Group/thread destinations are explicit named channels learned from service-accepted
input. Iter uses upstream `send(channel, content)`; Omega adds
`send-channel channel message` and its two-argument parser registration. The host
supplies the current routes on every provider request. Reading a group cannot
redirect a default send.

Mode sends first record a keyed durable intent. The independent responder submits
it to the transport, which returns delivery receipts and retains unknown outcomes without replay.
Submission acceptance is not delivery success. The service command format includes
the required `plain` field. Model selection is reloaded from agent-wide persisted
state before each provider request, including in an already-running process.
Inputs retained during a transport handover are acknowledged only after the
consuming mode checkpoints them.

Boundary checks: `python3 -m unittest discover -s tests -p test_mode_control_boundary.py`.
The real-service test `tests/test_telegram_metta_client.py` also checks controls with
no cognition running and a mode send whose delivery takes six seconds. Use its
documented `CETTA_CHANNEL_ROOT`, `CETTA_SERVICE_BIN` and `CETTA_BIN` variables.

**Recorded qualification (2026-09-30):** both standalone cores ran; the fixed
twelve-scenario matrix per mode, six semantic mutants, relevant upstream tests,
Lean models and 380 observed trace checks passed. Omega uses an
explicit loader adapter for its pinned PeTTa v1.0.4 utility semantics. Vendored
files and existing upstream test files remain unchanged. The production loader
uses current evaluator semantics instead of the old utility reevaluation shim.

## Production modes

The existing `/modes` menu exposes `godelclaw`, `coding`, `iter` and `omega`.
`/mode iter` selects the qualified Iter port; `iter` is no longer an alias.
Omega is marked with an open-circle unavailable state and `/mode omega` refuses
the switch until its current native-tool core passes qualification. Mode changes
persist and request a supervised recycle.
The UI distinguishes the requested selection from the currently running loop.
`/mode godelclaw` returns to the framework loop.

`launch.py` prepares private state and launches the selected MeTTa entrypoint.
It freezes the agent-wide selection path before changing CWD. The installed
CeTTa bundle includes its library directory beside the binary. Tests exercise
this installed layout with implicit selection-path defaults.

The PeTTa runner is invoked through Bash, including upstream scripts without a
shebang. Both evaluators use explicit MeTTa Boolean atoms at Iter's Python
predicate boundary. A Python `True` alone crosses Janus as `(@ true)`, which is
not SWI-PeTTa's Boolean `true`.
On PeTTa, tool subprocesses use Iter's pinned upstream Python helper; on CeTTa,
they use `lib/proc`. The loop's tool selection, ordering and result protocol stay
the same. Omega's Boolean configuration and filesystem predicates also use
explicit MeTTa atoms. Its host text concatenation has an exclusive empty-list
case: the inherited overlapping helper otherwise enumerates indefinitely when
asked for all answers. These adaptations leave the vendored loops unchanged.

To check startup, switching, checkpoints and restart with both installed engines:

```sh
MODE_TEST_CETTA_BIN=/path/to/cetta \
MODE_TEST_PETTA_ROOT=/path/to/PeTTa \
python3 -m unittest discover -s tests -p 'test_mode_launcher.py'
MODE_TEST_CETTA_BIN=/path/to/cetta \
MODE_TEST_PETTA_ROOT=/path/to/PeTTa \
python3 -m unittest discover -s tests -p 'test_upstream_mode_runtime.py'
```

Use the Python environment configured for the engines. These checks use local
fake providers and channels and temporary instance state. Omega runs through
100 boundaries per restart with a controlled host sleep, exercising evaluator
cleanup beyond a short startup smoke test.

For long-running Omega on CeTTa, use a build containing
[the suspended-cursor relocation fix](https://github.com/godelclaw/CeTTa/commit/b256939464c4b334016b8cc7500aa8933511d745).
Earlier builds can pass startup checks and then fail during evaluator cleanup.

Iter uses native tool-call JSON through the configured HTTP transport and
the configured provider/model. Anthropic messages translate native tool-use
IDs, argument objects, grouped tool results and token-limit stop reasons at
this boundary. The native generic codec preserves forced tool choice; the
imported-mode adapter explicitly selects `auto-for-thinking` when translating
Iter's `enable_thinking` to Anthropic adaptive thinking. Iter's existing loop
retains its retry rule for responses with no tools. Omega keeps its command-text
layout, including provider cache boundaries. Model selection and credential
configuration remain host adapters. The [project README](../README.md#services-and-execution)
describes the native and Python paths, their compatibility projections and limits.

Each configured agent uses a separate durable CWP Telegram service, with its own
client/operator sockets and independent command responder. Bot identity, model
selection and the operator lifecycle latch are instance-specific. Neither
transport nor controller is a child of the cognitive process.

Inputs are acknowledged after history checkpoints. Mode switches before a
checkpoint leave the input with its existing channel owner. Iter experience,
Omega history and transformations are mode-private. Omega loads the pinned
upstream NAL and PLN libraries. On supervised CeTTa it uses the agent's native
semantic-memory service; standalone and SWI-PeTTa launches retain mode-private
text recall. Optional plugins and upstream Chroma startup remain excluded. All
these differences are explicit in the generated `adapter-ledger.json`.

Production process verification, using scripted HTTP/CWP peers:

```sh
MODE_TEST_CETTA_BIN=/path/to/installed/cetta \
  python -m unittest discover -s tests -p test_upstream_mode_runtime.py
```

The test runs both actual loops, verifies provider protocol shapes, saved input,
restarts and cooperative exits. Runtime receipts contain event metadata, not
provider prompts or credentials.

Live verification on 2026-09-30 completed a real provider/tool turn, history
checkpoint and supervised restart in both modes on two independently configured
instances. Both returned to `godelclaw` afterward, including one retaining its
selected Claude model. This checked startup, turns and recovery, rather than
long-duration operation.

Signed thinking blocks are retained in Iter's existing `reasoning_details`
checkpoint field and passed back unchanged until its upstream context cleanup
prunes them. The Anthropic boundary tests exercise batch IDs, grouped results,
reasoning signatures, token stops and sanitized failure pacing:

```sh
python -m unittest discover -s tests -p test_iter_anthropic_provider.py
```

Instance-specific deployment receipts and original-file backups are kept in
operator-controlled storage outside the published source tree.

## Pins and review locations

| Component | Exact source |
|---|---|
| Iter | `patham9/iter` `f4064d97849ecaccac7939315a3f1a68de15c3ef` |
| Omega qualified baseline | `singnet/Omega` `ccb26015d7f07391bf58695e2abf541964e1e7ca` (escape-token injection fix included) |
| Omega current-core target (observed 2026-10-20) | `singnet/Omega` `94d7b9a2e1ef6b5873aee764ca1ef16de8dcd943`; native tool-call rewrite, deliberately unavailable until requalified |
| Omega reference evaluator | PeTTa v1.0.4 `7037f4c2ad378c52fc328004fe216d5118b674f0` |
| Current CeTTa runtime | [CETTA_REF](../CETTA_REF), published on [cetta-ours](https://github.com/godelclaw/CeTTa/tree/cetta-ours); the installer builds this engine and its services together |
| Original CeTTa core qualification | `godelclaw/CeTTa` [`9a535530`](https://github.com/godelclaw/CeTTa/commit/9a5355309c57d94e99e5a279e088b69cbe4486d8), branch `qualify/upstream-core-tests-20260930`, with `src/library_proc.c` text-boundary support |
| Lean models | `godelclaw/MeTTapedia` [`a4dafeb37`](https://github.com/godelclaw/MeTTapedia/commit/a4dafeb37243a9f8546fede9561321a2348077a5), `lean/pettaclaw/`, branch `pettaclaw/upstream-cores-20260930` |
| Offline core implementation | `modes/iter/`, `modes/omega/` and `modes/conformance/` in this repository |
| Production mode integration | Runtime `main` |

The per-mode `upstream-manifest.json` lists **every tracked upstream file**, its
classification, whether it was vendored, and its digest or exclusion reason.
All vendored bytes are unchanged and verified before conformance. MIT and
Apache licenses remain beside the respective upstream copies.

## Exact offline verification command

From this repository, using the pinned reference and the corresponding CeTTa
and Lean branches above. Set the paths for your own checkouts and interpreter:

```sh
python3 modes/conformance/qualify.py \
  --cetta "$CETTA_BIN" \
  --cetta-source "$CETTA_SOURCE" \
  --petta "$PETTA_REFERENCE_ROOT" \
  --python "$PETTA_PYTHON" \
  --formal-root "$FORMAL_ROOT" \
  --output "$QUALIFICATION_OUTPUT"
```

The output contains `qualification.json`, the binary digest and toolchain,
matrix summary, raw per-evaluator traces, mutant results and source-test logs.
Generated Lean replay and oleans remain in the formal worktree's `.build/`.
Lean checks run sequentially using the caller's environment.
No credentials, account settings, network providers or live channels are used.

`conformance/scenarios.json` freezes the twelve specifications per mode. The
memory specification has two threshold fixtures; restart specifications also
run a second fresh process against copied persisted state. Only fixture-root
paths are normalized. Roots have equal lengths, so source `CHARS_SENT` values
are compared intact. Clock and session IDs are explicitly controlled.

Mutants: Iter last-call-only `nop`, a 49-turn budget, and a nine-call cap;
Omega duplicate input rearming, inclusive wake comparison, and no decrement.
Each runs only the scenario that distinguishes it from the reference.

Omega's 23 existing parsing tests run unchanged with their fixture supplied
directly; no pytest install is required. Existing `src_skills.metta` and
`src_utils.metta` pass, with their complete output matching the pinned reference.
The native runtime copy uses the recorded legacy-semantics adapter below.
Focused controls also compare predicate reevaluation, ordered answers,
negation stopping at its first answer, computed skill names and held arguments.
UTF-8 locale is controlled on both evaluators. Iter has no upstream test suite
at this pin. The focused `lib/proc` text tests and original golden fixture pass.

Upstream audit (2026-09-30): freshly fetched trueagi-io/PeTTa main
`ae66fa8e41dcd5539d614706bd4e5cfb34f9608d` also fails Omega's unchanged
`src_utils.metta`. The input-demand migration was merged upstream in
[PR #212](https://github.com/trueagi-io/PeTTa/pull/212), on 2026-07-24.
Correcting only the old `Expression` annotation makes that suite pass on both
latest trueagi and in-house PeTTa. Empty-case reevaluation is a separate issue:
latest trueagi still reevaluates; in-house `5883475` changes it to once. The
Omega callers inspected do not demonstrate dependence on that second run;
the legacy side-effect probes here preserve an observed oracle behavior, not
an independently asserted Omega requirement. Machine-specific audit logs and
qualification receipts stay outside the published source tree.

## Entrypoints and host contracts

Iter's entrypoint is `(iter:start)` in `iter/core.metta`. The host selects an
isolated working directory containing `prompt.txt`, `tools/`, `channels/`,
`memory/`, `transformations/` and `experience.json`. MeTTa owns control flow,
retry selection, dispatch policy, cleanup boundaries, checkpointing and pacing.
`iter_host.py` boxes JSON values and reuses the pinned Python helper sections
for filesystem/component operations. Tool workers execute through `lib/proc`.

Omega's entrypoint remains `(omega)` in the **unchanged** vendored
`src/loop.metta`. `omega/offline.py` loads the isolated core with explicit host
bindings. The host supplies configuration, provider, channel, clock, optional
startup facilities and a private `repos/Omega/memory/` directory. Runtime copies
may persist state; the vendor directory is never a state directory.

The production launcher uses distinct per-agent `modes/iter` and `modes/omega`
state roots. The fixture adapters remain isolated from production adapters.

## LLM boundary findings

Iter uses OpenAI-style chat messages, `tool_choice="required"`, native tool-call
IDs, reasoning fields and finish reasons. Plain text is marked undelivered;
no-call replies trigger temporary retries. Calls are capped before dispatch,
and dispatch uses the loaded implementation snapshot, independently of schemas
modified by transformations. Budget exhaustion continues the current task;
`nop` anywhere in the batch starts a new burst after polling.

Omega sends one assembled prompt string with token and reasoning limits and
receives command text. Its pinned helper parses that text; there are no native
tool-call IDs or finish reasons at this interface. Provider errors escape the
loop. Context is assembled before receiving input; the signed loop counter
decrements even while idle; wake comparison is strict. These protocols must
remain distinct in the future native client.

## Divergence and add-on ledger

| Boundary | Treatment and evidence |
|---|---|
| Iter Python loop | Replaced by `core.metta`; the complete pinned Python loop is the offline oracle. Requests, ordered results, control and persistence match the fixed matrix. |
| Iter JSON representation | `Box` preserves opaque JSON/None; scalar comparisons use host JSON equality because PeTTa symbols and authored string literals have different native representations. |
| Iter helper functions and prompts | Pinned helper sections 0–3 and prompts reused unchanged. The offline oracle uses the pinned Python SDK interface; production selects the Python adapter or CeTTa's buffered `llm:v1:` library. |
| Iter worker launch | Existing `lib/proc` with the same five-second deadline, inherited environment and process group. A thin Python launcher redirects all streams to DEVNULL before exec, matching upstream worker streams. Embedded Python's `sys.executable` is replaced with the explicit host interpreter. |
| `lib/proc` text | Local minimal patch accepts Python/Prolog text atoms in PeTTa mode; HE retains grounded-string validation. `proc_text_boundary.metta` and original golden fixture pass. |
| Iter provider/channel/time | Scripted SDK-shaped replies, fake receive events and a fixed clock. The reference loop is observed at its existing loop boundary; test termination occurs after the requested iteration count. |
| Omega main launcher | Deliberately not loaded: its missing `src/context.metta` import and network/embedding/NAL/PLN add-ons are outside isolated core qualification. No replacement upstream module is fabricated. |
| Omega optional startup | Embedding initialization, security-policy application and plugin initialization are fixture no-ops. Real services/providers/plugins are not qualified here; each excluded source file is in the manifest. |
| Omega channels/provider | Fake channel plus scripted string provider at the original interface. Unknown commands, errors, send suppression and history remain upstream control. TCP mock implementations are retained as source but not started. |
| Omega clock | Generated utility module substitutes only clock/sleep operations with controlled host bindings. Integer fixture timestamps model source comparisons; real-time/IO behavior remains a host assumption. |
| Omega v1.0.4 input annotations | Two declarations in generated runtime copies translate old held `Expression` inputs to current `Atom`, and old evaluated/unguarded `Atom` inputs to `%Undefined%`. Result types remain `Bool`. Source authority: PeTTa `7037f4c`, `src/translator.pl:349-369`. The reference keeps its original declarations. |
| Omega v1.0.4 Empty case | Only `empty-to-bool` receives a generated compatibility body: enumerate matching answers, then reevaluate its predicate under negation, stopping at the first answer. This preserves the old evaluator's observable second evaluation, including a predicate that becomes empty. Source authority: PeTTa `7037f4c`, `src/translator.pl:163-172`; `runtime-repros/legacy-input-demand.metta` checks effects and answer order against that oracle. Every translated form is recorded in `legacy-semantics-adapter.json`; no global evaluator policy changes. |
| Omega `joinPath` | Thin direct binding to unchanged `helper.joinPath`, replacing the source cross-`progn` variable binding at this host filesystem boundary. Both evaluators use this binding in core fixtures. |
| Omega prompt/history reads | CeTTa-only filesystem bindings preserve provider prompt fallback and byte-tail history reads. The reference executes the original memory functions. This avoids the native computed-library-path incompatibility. |
| Omega `string-replace` | CeTTa-only codec binding reproduces `split_string` separator-set semantics plus joining, including empty input. The oracle executes the original Prolog calls; `runtime-repros/empty-string-replace.metta` isolates the native failure. |
| Omega configuration bool | The declared `spamShield` field converts its text spelling at the host configuration boundary; it does not change loop policy. |
| Omega restart/checkpoint | History persists; control resets. Test termination is a fixture-only exit at the upstream sleep boundary; provider failure exits before that boundary. |
| Unadapted legacy probes | `runtime-repros/typed-predicate-test.metta` and `empty-string-replace.metta` retain the native incompatibility reproductions. The loader adapters preserve the required pinned behavior; this work does not claim that CeTTa generally implements the old dialect without them. |
| Other upstream material | Complete file-by-file manifest marks deployment, provider/channel, plugin, theory and ancillary material outside this isolated core. No agent add-ons are merged into either pinned source. |

## Lean boundary and source references

`IterArchitecture.lean` reuses the transformation/snapshot/restart proofs and
adds selected properties for `nop` anywhere in a batch, budget continuation and
checkpoint rollback. `observedCycle` exposes requests, dispatch, save, wait and
recovery. Source: `iter.py` sections 3–4, particularly response capping,
snapshot dispatch, checkpoint assignments and final pacing branches.

`OmegaArchitecture.lean` models signed counters, input novelty, strict wake,
provider/dispatch order and history control. Selected properties prove duplicate
input does not rearm, deadline equality does not wake, and provider failure
halts before dispatch/history. Source: `src/loop.metta` initialization and
iteration body; `src/memory.metta` history conditions. The observed dispatch
action means selection of a parsed command; it does not assert that an Error
command executes a tool. Context, parsing, results and formatted history entries
are explicit opaque host observations.

The generated replay checks 380 observed control/action projections from both
evaluators, including restarts. New proofs contain no `sorry` or new declared
axioms; standard Lean logical dependencies are printed by the model files.
This proves properties of the executable models and tests correspondence at
that boundary. It is not a full verification of Python, MeTTa, real providers,
arbitrary tool bodies or OS/filesystem behavior.

## Verification scope

The recorded qualification command exited zero. Reproducing it creates a
`qualification.json` with each stage's result, the evaluator digest and the
toolchain. Keep that receipt with the tested source revisions. The claims apply
to the listed adapters and scenarios, with the host assumptions stated above.

Mode registration, real providers/channels/tools and supervised startup are
implemented by `launch.py`, `runtime_host.py` and the agent host hooks described
above. The buffered native CeTTa client is now available; streaming and automatic
lossless native replay in imported histories remain later work. Credentials use
scoped host aliases or the Python adapter. Keep generated traces, state and build
outputs in your own runtime or verification directories.
