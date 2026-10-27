# Iter and Omega modes

Iter and Omega are selectable cognitive loops in MeTTaClaw. Iter runs the
upstream MeTTa Iter loop (`iter.metta` and `utils.metta`) unchanged; Omega runs
its pinned core loop with host adapters, differentially qualified against its
reference evaluator. Each configured instance keeps its own identity,
credentials, process and mode state. A single instance can switch loops without
sharing its state with another instance. Omega's provider access supports
CeTTa's buffered `llm:v1:` library or the Python transport; supervised Iter
always uses the native provider.

Start with the [project README](../README.md) for installation and the
[service guide](../systemd/README.md) for supervised deployment. This guide
describes the mode interfaces, pinned sources, compatibility adapters and
verification scope on `main`.

## Standalone terminal launches

ITER needs only a CeTTa build with HTTP and the SWI-Prolog library: its loop,
model client, tools and channels are MeTTa, and no Python package is
installed. For the Omega core, install `omega/requirements.txt` (the
OpenAI-compatible SDK and PyYAML) in the Python environment used by a
Python-enabled CeTTa bundle. Neither profile loads Torch, Chroma,
sentence-transformers, MCP, or a Telegram service.

```sh
export CETTA_BIN=/path/to/cetta
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
even when invoked from a configured service's environment. ITER runs the
vendored loop as a checkout of it runs: the state directory holds its code,
`tools/`, `channels/` and `experience.json`, and the model request is the
vendored `iter_llm.metta`'s. Workers discard their output, as upstream's do, so
a terminal send shows in the loop's `Output>` line. Omega keeps its pinned core
loop and provider modules; its transport
is terminal-only and memory uses the existing history/text adapter. Its actual
NAL and PLN libraries are loaded. Upstream Chroma and arbitrary plugin startup
are not part of this profile. The runtime's `adapter-ledger.json` records these
differences.

State is outside the checkout and survives restart. Use `--state-dir PATH`
for a separate instance, and `--prompt FILE` to append your own instructions
when seeding new state. Existing prompts, tools, channels and memory files
are retained. Add-on experimentation should use a separate state directory.
Omega can also run on SWI-PeTTa with `--engine petta` and `PETTA_ROOT`, using
that engine's own Python bridge dependencies. Iter runs on CeTTa only.

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
MODE_TEST_CETTA_BIN=/path/to/cetta \
python3 -m unittest discover -s tests -p test_iter_native_channel.py
```

## Select and operate a mode

| Operator command | Purpose |
|---|---|
| `/modes` | List cognitive loops and the active selection. |
| `/mode iter` | Request the Iter loop. |
| `/mode omega` | Request the current qualified Omega loop. |
| `/mode godelclaw` | Return to the framework loop. |
| `/models`, `/model` | Inspect or change the LLM independently of the loop. |
| `/engines`, `/engine` | Inspect or change the MeTTa evaluator. |

Mode changes persist and take effect through a supervised cognition restart.
The operator UI distinguishes a requested mode from a running one. Older saved
names `agent`, `default`, `generic` and `claw23` map to `godelclaw`;
`iter-coding` maps to `coding`. The `iter` selection runs the vendored MeTTa
Iter loop.

The running process keeps its original mode until it checkpoints and exits;
a newer selection never becomes a policy inside the old loop. Startup consumes
only the previous restart request, before selecting its engine and mode.
Unexpected evaluator exits preserve the selected engine and report failure.
Automatic framework fallback to PeTTa is opt-in via
`METTACLAW_ENGINE_FALLBACK=petta`; an explicit `/engine petta` remains available.

Iter renews its work after an idle boundary and can make provider calls without
new input. It takes a stop or a switch before its next provider request, at most
one idle wait (about ten seconds) later; no tool runs in between, because tools
only follow a provider reply. Omega keeps its own wake/counter behavior. Framework fuel settings remain separate from these loops. Optional managed
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
input. Iter's Telegram channel is the MeTTa module `channels/telegram.metta`, and
its `send(channel, content)` tool takes `telegram` or a `telegram_CHAT_THREAD`
route; Omega advertises `send` and `send-channel` as ordinary structured tools.
The host supplies the current routes on every provider request. Reading a group
cannot redirect a default send.

Mode sends first record a keyed durable intent. The independent responder submits
it to the transport, which returns delivery receipts and retains unknown outcomes without replay.
Submission acceptance is not delivery success. The service command format includes
the required `plain` field. Model selection is reloaded from agent-wide persisted
state before each provider request, including in an already-running process.
Inputs retained during a transport handover are acknowledged only after the
consuming mode checkpoints them. Iter keeps no record of what it offered: an
input is acknowledged when a provider request starts whose saved history holds
it, after that history is synced to disk. Input lost to a failed save or to a
channel worker killed mid-receive is therefore offered again, and a restart
after a save does not offer it twice.

Boundary checks: `python3 -m unittest discover -s tests -p test_mode_control_boundary.py`.
The real-service test `tests/test_telegram_metta_client.py` also checks controls with
no cognition running and a mode send whose delivery takes six seconds. Use its
documented `CETTA_CHANNEL_ROOT`, `CETTA_SERVICE_BIN` and `CETTA_BIN` variables.

**Recorded qualification (2026-10-20):** both standalone cores ran; the fixed
twelve-scenario matrix per mode, six semantic mutants, relevant upstream tests,
the `ToolCalls.module` schema, Lean models and generated observed-trace checks
passed. Iter's half of that matrix qualified the earlier port of `iter.py` and
was retired with it; the vendored Iter is checked by its own worker tests and
by the process tests below. Omega uses an
explicit loader adapter for its pinned PeTTa v1.0.4 utility semantics. Vendored
files and existing upstream test files remain unchanged. The production loader
uses current evaluator semantics instead of the old utility reevaluation shim.

## Production modes

The existing `/modes` menu exposes `godelclaw`, `coding`, `iter` and `omega`.
`/mode iter` selects the vendored MeTTa Iter loop; `iter` is no longer an alias.
`/mode omega` selects the current pinned native-tool loop. Mode changes persist
and request a supervised recycle.
The UI distinguishes the requested selection from the currently running loop.
`/mode godelclaw` returns to the framework loop.

`launch.py` prepares private state and launches the selected MeTTa entrypoint.
It freezes the agent-wide selection path before changing CWD. The installed
CeTTa bundle includes its library directory beside the binary. Tests exercise
this installed layout with implicit selection-path defaults.

The PeTTa runner is invoked through Bash, including upstream scripts without a
shebang. Omega's Boolean configuration and filesystem predicates use explicit
MeTTa atoms: a Python `True` alone crosses Janus as `(@ true)`, which is not
SWI-PeTTa's Boolean `true`. Its host text concatenation has an exclusive empty-list
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

Supervised Iter makes each model request through the agent's native provider
(`src/native_llm.metta`) in a CeTTa process of its own, with the selected
provider and model, credential aliases, admission and receipts. The loop
itself runs no Python. Anthropic messages translate native tool-use
IDs, argument objects, grouped tool results and token-limit stop reasons at
this boundary. The native generic codec preserves forced tool choice; the
imported-mode adapter explicitly selects `auto-for-thinking` when translating
Iter's `enable_thinking` to Anthropic adaptive thinking. Iter's loop keeps its
retry rule for responses with no tools. Omega uses the same canonical
`ToolSpecV1`/`ToolCallV1`/`ToolResultV1` theory at the provider boundary. Each
carrier is a MeTTa constructor around named `JsonObject`/`JsonPair` fields;
provider JSON is only a wire projection. Omega retains its own loop, message
history and dispatch policy. `metta` is one
ordinary tool whose `sexpression` argument is evaluated by Omega's existing
MeTTa implementation. Model selection and credential configuration remain host
adapters. The [project README](../README.md#services-and-execution)
describes the native and Python paths, their compatibility projections and limits.

Each configured agent uses a separate durable CWP Telegram service, with its own
client/operator sockets and independent command responder. Bot identity, model
selection and the operator lifecycle latch are instance-specific. Neither
transport nor controller is a child of the cognitive process.

Inputs are acknowledged after history checkpoints. Mode switches before a
checkpoint leave the input with its existing channel owner; an Iter switch also
leaves input it saved but never sent to the model. Iter experience,
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
| Iter | `godelclaw/iter` `metta-native` `a58e2ebbba8db237e4126e13bdfe9f7cb90a3aaa`, on `patham9/iter` `metta` `dc456de33c7ac18d1969b74186870d1834432603` |
| Omega | `singnet/Omega` `94d7b9a2e1ef6b5873aee764ca1ef16de8dcd943` |
| Canonical call schema | `modes/protocol/ToolCalls.module`; exact digest recorded in each runtime adapter ledger |
| Omega reference evaluator | PeTTa v1.0.4 `7037f4c2ad378c52fc328004fe216d5118b674f0` |
| Current CeTTa runtime | [CETTA_REF](../CETTA_REF), published on [cetta-ours](https://github.com/godelclaw/CeTTa/tree/cetta-ours); the installer builds this engine and its services together |
| Original CeTTa core qualification | `godelclaw/CeTTa` [`9a535530`](https://github.com/godelclaw/CeTTa/commit/9a5355309c57d94e99e5a279e088b69cbe4486d8), branch `qualify/upstream-core-tests-20260930`, with `src/library_proc.c` text-boundary support |
| Lean models | `godelclaw/MeTTapedia` [`214423a38f`](https://github.com/godelclaw/MeTTapedia/commit/214423a38f062b746907e2652883f0887a2bc39e), `lean/pettaclaw/` on `main`; upstream `main` is merged there while `goertzel/proofs` remains separate |
| Offline core implementation | `modes/omega/` and `modes/conformance/` in this repository |
| Iter host modules | `modes/iter/supervised/` in this repository |
| Production mode integration | Runtime `main` |

The per-mode `upstream-manifest.json` lists **every tracked upstream file**, its
classification, whether it was vendored, and its digest or exclusion reason.
All vendored bytes are unchanged and verified before conformance. MIT and
Apache licenses remain beside the respective upstream copies.

## Exact offline verification command

This qualifies Omega. From this repository, using the pinned reference and the
corresponding CeTTa and Lean branches above. Set the paths for your own
checkouts and interpreter:

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

Mutants: Omega duplicate input rearming, inclusive wake comparison, and no
decrement.
Each runs only the scenario that distinguishes it from the reference.

Omega's 6 current helper tests run unchanged with their fixture supplied
directly; no pytest install is required. Existing `src_skills.metta` and
`src_utils.metta` pass, with their complete output matching the pinned reference.
The native runtime copy uses the recorded legacy-semantics adapter below.
Focused controls also compare predicate reevaluation, ordered answers,
negation stopping at its first answer, computed skill names and held arguments.
UTF-8 locale is controlled on both evaluators. The focused `lib/proc` text
tests and original golden fixture pass. Iter's vendored `tests/worker.metta`
runs from `iter/upstream` (`test_iter_native_channel.py`).

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

Iter's entrypoint is the vendored `iter.metta`, whose `!(main)` starts the
loop. The launcher prepares a mode-private working directory as a checkout of
the fork is laid out: the vendored `iter.metta`, `utils.metta`,
`iter_worker.metta`, `lib_nal.metta` and `reprogramming.txt` are copied in at
every launch; `prompt.txt`, `tools/`, `channels/`, `memory/`, `transformations/`
and `experience.json` belong to the agent and are only seeded. Each tool,
channel and transformation call runs in a CeTTa process of its own
(`iter_worker.metta`).

Standalone launches use the fork's `iter_llm.metta`. Supervised launches put
`supervised/iter_llm.metta` in its place: the same `init_client`,
`chat_completion` and `exception_text`, over `godelclaw_host.metta`. Before each
provider request it checks the operator latch and the recycle witnesses of
`lifecycle.py` and `engine_modes.py` (a recycle exits; a stop is waited out in
`godelclaw_pause.metta`, a short process of its own, and the iteration then
restarts), records a turn boundary when the history was saved, acknowledges
saved input, and sends the request, with the route context first, to
`supervised/provider.metta`. `channels/telegram.metta` and the supervised
`tools/send.metta` use the same module for the durable service, routes and send
intents. CeTTa's JSON library also defines `json-parse`, which `utils.metta`
defines over SWI terms, so the native provider cannot share the loop's space;
the provider process keeps them apart.

Omega's entrypoint remains `(omega)` in the **unchanged** vendored
`src/loop.metta`. `omega/offline.py` loads the isolated core with explicit host
bindings. The host supplies configuration, provider, channel, clock, optional
startup facilities and a private `repos/Omega/memory/` directory. Runtime copies
may persist state; the vendor directory is never a state directory.

The production launcher uses distinct per-agent `modes/iter` and `modes/omega`
state roots. The fixture adapters remain isolated from production adapters.
Python tools, channels and transformations left in an Iter state directory by
the earlier port stay on disk but are not loaded; the loop loads `.metta`
modules.

## LLM boundary findings

Iter (`iter.metta`) uses OpenAI-style chat messages, `tool_choice="required"`,
native tool-call IDs, reasoning fields and finish reasons. Plain text is marked
undelivered; no-call replies trigger temporary retries. Calls are capped before
dispatch, and dispatch uses the loaded implementation snapshot, independently
of schemas modified by transformations. Budget exhaustion continues the current
task; `nop` anywhere in the batch starts a new burst after polling.

Omega sends structured messages, schemas, token and reasoning limits and
receives ID-bearing calls. The canonical module theory keeps provider JSON,
Omega's Python objects and MeTTa dispatch as explicit projections rather than
one format silently becoming the others. Provider exceptions are contained as
an empty response by current upstream. Context is assembled before receiving
input; the signed loop counter decrements even while idle; wake comparison is
strict.

## Divergence and add-on ledger

| Boundary | Treatment and evidence |
|---|---|
| Iter loop | Vendored `iter.metta` and `utils.metta` run unchanged, with the fork's worker, tools and channels; every file is verified against `upstream-manifest.json`. |
| Iter model request | Standalone: the fork's `iter_llm.metta` over CeTTa HTTP. Supervised: `supervised/iter_llm.metta` keeps its interface and sends the request through the native provider in a CeTTa process of its own; no key string becomes a MeTTa term. |
| Iter gate | Operator latch and recycle witnesses are read before each provider request, not at the loop's top or in its `sleep`; switches and stops take hold within one idle wait. |
| Iter Telegram | `channels/telegram.metta` (receive, send) and a route-aware `send` tool. Input is acknowledged when a provider request starts whose saved history holds it. Rendering follows `channels/telegram.py`'s header; an external reply's origin reads as JSON. |
| Iter terminal output | Standalone workers discard output as upstream's do; the loop's `Output>` line shows a send. |
| Iter on SWI-PeTTa | Not supported: the vendored worker and model client use CeTTa. |
| `lib/proc` text | Local minimal patch accepts Python/Prolog text atoms in PeTTa mode; HE retains grounded-string validation. `proc_text_boundary.metta` and original golden fixture pass. |
| Omega main launcher | The tiny upstream `run.metta`/`lib_omega.metta` aggregate is not used because it imports an absent `src/context.metta`; the unchanged current loop and its required modules are loaded explicitly. |
| Omega optional startup | Embedding initialization, security-policy application and plugin initialization are fixture no-ops. Real services/providers/plugins are not qualified here; each excluded source file is in the manifest. |
| Omega channels/provider | Fake qualification channels and canonical ID-bearing calls use the original current interfaces. Unknown tools, per-call errors, send suppression and history remain upstream control. TCP mocks are retained as source but not started. |
| Omega clock | Generated utility module substitutes only clock/sleep operations with controlled host bindings. Integer fixture timestamps model source comparisons; real-time/IO behavior remains a host assumption. |
| Omega v1.0.4 input annotations | Two declarations in generated runtime copies translate old held `Expression` inputs to current `Atom`, and old evaluated/unguarded `Atom` inputs to `%Undefined%`. Result types remain `Bool`. Source authority: PeTTa `7037f4c`, `src/translator.pl:349-369`. The reference keeps its original declarations. |
| Omega v1.0.4 Empty case | Only `empty-to-bool` receives a generated compatibility body: enumerate matching answers, then reevaluate its predicate under negation, stopping at the first answer. This preserves the old evaluator's observable second evaluation, including a predicate that becomes empty. Source authority: PeTTa `7037f4c`, `src/translator.pl:163-172`; `runtime-repros/legacy-input-demand.metta` checks effects and answer order against that oracle. Every translated form is recorded in `legacy-semantics-adapter.json`; no global evaluator policy changes. |
| Omega `joinPath` | Thin direct binding to unchanged `helper.joinPath`, replacing the source cross-`progn` variable binding at this host filesystem boundary. Both evaluators use this binding in core fixtures. |
| Omega prompt/history reads | CeTTa-only filesystem bindings preserve provider prompt fallback and byte-tail history reads. The reference executes the original memory functions. This avoids the native computed-library-path incompatibility. |
| Omega `string-replace` | CeTTa-only codec binding reproduces `split_string` separator-set semantics plus joining, including empty input. The oracle executes the original Prolog calls; `runtime-repros/empty-string-replace.metta` isolates the native failure. |
| Omega call codecs | `ToolCalls.module` names three MeTTa record carriers over a supplied JSON-value theory. `tool_protocol.py` maps provider JSON to `JsonObject`/`JsonPair` atoms and Omega objects; the adapter ledger records the schema digest and representation. |
| Omega restart/checkpoint | History persists; control resets. Test termination is a fixture-only exit at the upstream sleep boundary; provider failures follow current upstream containment. |
| Unadapted legacy probes | `runtime-repros/typed-predicate-test.metta` and `empty-string-replace.metta` retain the native incompatibility reproductions. The loader adapters preserve the required pinned behavior; this work does not claim that CeTTa generally implements the old dialect without them. |
| Other upstream material | Complete file-by-file manifest marks deployment, provider/channel, plugin, theory and ancillary material outside this isolated core. No agent add-ons are merged into either pinned source. |

## Lean boundary and source references

`IterArchitecture.lean` models the earlier port of `iter.py` (sections 3–4:
response capping, snapshot dispatch, checkpoint assignments and pacing); its
observed-trace replay was retired with that port.

`OmegaArchitecture.lean` models signed counters, input novelty, strict wake,
provider/dispatch order and history control. Selected properties prove duplicate
input does not rearm, deadline equality does not wake, provider failure is
contained without inventing a call, and dispatch preserves provider call IDs and
order. Source: `src/loop.metta`, `src/providers.py` and `src/memory.metta`.
Tool bodies, results and formatted history entries remain explicit opaque host
observations.

The generated replay checks observed control/action projections from both
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
