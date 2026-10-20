# Model request lifetimes

Native model transport and attention have separate lifetimes. The MeTTa
client submits once, retains an owned `(llm:v1:pending …)` value, and polls
that handle. Other clients' completions stay in the native worker.

`llm:begin plan`, `llm:poll-once plan state` and
`llm:wait-once plan state milliseconds` support nonblocking callers. A bounded
wait returns the original pending value when no terminal answer is available.
`llm:run` is the foreground policy: it continues waiting for that same call.
The independent control responder remains runnable. The unused background
model execution and adoption paths have been removed; retained results can
still be read without starting another foreground tool loop.

Ordinary input queues for the next decision. Complete model output is executed
in order; changes to goals, source code or history do not cancel its calls.
An explicit stop interrupts unexecuted work. Historical background results stay
queryable through `model-jobs` and `model-result`; transport progress, heartbeats
and partial JSON are not complete model output.

Configure the nonsecret settings in `config/local.toml` under `[agent]`, using
`scripts/config_ctl.py set agent.<setting> <value>`. The launcher regenerates
its derived environment. Apply the same checked runtime release to all managed
instances; each instance retains its own credentials and state.

| Setting | Default seconds | Meaning |
| --- | ---: | --- |
| `llm_timeout_seconds` | 120 | Native soft wait; Python transport hard timeout |
| `llm_connect_seconds` | 30 | Connection establishment |
| `llm_first_byte_seconds` | 900 | Initial body wait, including connection time |
| `llm_idle_seconds` | 180 | Silence after body data starts |
| `llm_hard_seconds` | 1800 | Absolute transport backstop |

The native soft default can be overridden by `METTACLAW_LLM_SOFT_SECONDS`.
These defaults are engineering bounds, not measurements of provider liveness.
Post-start byte activity includes SSE comments and reasoning deltas; a silent
model can still be working. A hard transport timeout cannot recover a later
remote result without provider-supported remote job retrieval.

`llm_stream = true` enables Chat Completions SSE, including Synthetic. Other
protocols retain buffered decoding. The native C worker receives bounded bytes;
the host's pure protocol adapter assembles text, reasoning, indexed tool calls,
finish and usage before the native MeTTa codec validates the canonical result.
Streaming defaults to an 8 MiB body cap because token-delta envelopes can be
much larger than the final answer; buffered responses default to 1 MiB.
METTACLAW_LLM_RESPONSE_BYTES sets a checked override up to 16 MiB.
Missing finish, missing `[DONE]`, mixed response identities, provider errors,
incomplete events and partial tool batches fail closed. There is no generation
POST retry or stream reconnect in the adapter.
Compatible servers may close directly after the exact `[DONE]` sentinel;
that terminal marker need not have a trailing SSE blank line. A preceding
validated finish is still required, and JSON events truncated at EOF are refused.

The call journal records the request identity before networking, then the
handle, soft-wait and byte observations, terminal evidence and precise failure
reason. Routine recycling and pausing wait for
an admitted call to settle. The independent `/stop` still blocks effects at
the broker boundary. A forcibly terminated process leaves an uncertain
receipt with unknown remote outcome and unknown actual billing; its token
reservation remains accounted. Recovery does not replay the POST.

Full terminal evidence is retained for at most seven days and the newest 32
terminal calls; metadata for at most thirty days and the newest 256. Pending
calls are never pruned. `model-result` reports whether evidence remains; expired
evidence is not recoverable from this bounded cache. The independent budget
ledger retains its accounting policy.

Queue notices use exact update IDs. Notice cleanup follows successful provider
processing, durable history and durable channel acknowledgement. Malformed
foreign values are rejected as a whole rather than extracting apparent IDs
from error text. Only correlated bot-owned notices with confirmed message IDs
are eligible for deletion.

Offline checks use loopback peers and fixture credentials:

```sh
python -m unittest discover -s tests -p 'test_handover_ids.py'
python -m unittest discover -s tests -p 'test_provider_stream.py'
python -m unittest discover -s tests -p 'test_native_lifetime.py'
CETTA_BIN=/path/to/http-python-cetta python -m unittest discover -s tests -p 'test_native_llm*.py'
```

The repair contracts have a separate kernel conformance gate. Use
`godelclaw/MeTTapedia` `main`, whose agent models live in `lean/pettaclaw`,
install the repository's Lean toolchain, and use a CeTTa build with Python and
HTTP support:

```sh
python scripts/check_repair_contracts.py \
  --models ../MeTTapedia/lean/pettaclaw \
  --toolchain leanprover/lean4:v4.33.1 \
  --cetta "$CETTA_BIN" \
  --output-dir ./qualification/repair-contracts
```

`RepairArchitecture.lean` strengthens the coarse delivery and wait models with
the actual host's processing, promotion, goal/checker guards, correlated notice
receipts, deletion eligibility, call epochs and accounting preservation. It
also checks explicit counterexamples for the old permanent-backlog and
destructive-timeout policies. Inductive laws apply to arbitrary traces of the
specified workflow and call transitions. Conditional queue progress assumes
successful durable boundaries and service of pending heads.

`NoticeProtocol.lean` composes input settlement, captured chat/thread routes,
deletion intents and correlated confirmations. A visible reply alone cannot
authorize cleanup. Failed attempts retain their route, key and message target;
any finite number of failures followed by a confirming receipt closes the
notice. These progress laws require service of the notice and confirmation
before expiry. Expiry closes local tracking without asserting remote deletion.

The gate mutates isolated fixture databases, executes the extracted MeTTa turn
guard, and compares observed host outputs with the Lean functions. Every finite
comparison is checked with `by decide`; deliberate corrupted observations must
fail kernel checking. The receipt records source hashes, coverage and axioms.
A failed rerun replaces an older passing receipt with an incomplete status.
Source changes during a run invalidate qualification.

The channel integration regression also exercises group notices with the
real MeTTa client and a loopback Bot API: visible reply before processing,
control restart, refused handover, successful retry and correlated deletion
in the original group. Reproduce it with the CeTTa checkout's transport test
fixtures and a Telegram service build:

```sh
CETTA_CHANNEL_ROOT=../CeTTa CETTA_SERVICE_BIN=/path/to/cetta-telegram-service \
  CETTA_BIN=/path/to/http-python-cetta python tests/test_telegram_metta_client.py
```

This checks finite correspondence for these boundaries. It does not prove the
Python/C/SQLite implementation, all crash interleavings, remote exactly-once
execution, provider liveness, task completion, a fixed whole-prompt token
bound, or a cache hit rate. Content excerpts and backlog item counts are
bounded; identifiers, checker text and other prompt sources have separate
contracts. Cache-prefix laws concern stable-first layout and eligibility for
reuse. The provider decides whether to reuse a prefix.

The older `audit_strategy.py`/`StrategyTraceAudit.lean` monitor predates the new
workflow events and fails closed on them. It requires a workflow-aware adapter
before it can certify complete current ledgers. Passing this repair gate does
not constitute that complete-ledger certificate.

The checked `AsyncWait` model accompanies `TopicAsync` and `CooperativeInput`
in MeTTapedia. They establish model laws with explicit host assumptions;
the loopback tests supply implementation evidence, not a proof of libcurl or
the compiled host.

## Request workflow and compact context

A message is an input delivery record, not automatically a permanent task.
The managed loop marks it processed only after the provider turn succeeds,
its history is durable, and the input handover is acknowledged. Processing
does not certify fulfillment. The current message remains selectable for
continuation, and issued or uncertain actions and their receipts stay retained.

`request-disposition id keep reason` retains explicit ongoing work; the reason
should include its closing condition. `waiting-user` and `defer` retain work
outside the active queue. `addressed` records the agent's reviewed workflow
disposition, distinct from checker-certified `achieved`; `reopen` resumes it.
Goal lifecycle and configured exact-output checkers retain their own rules.

The prompt shows eight active headlines, counts and the current request, with
bounded receipt excerpts. `request-page offset` pages through all retained
records. `request-status id` and `request-read id offset` retrieve original
inputs. Omitting an old record from the working view never deletes it.
Old delivery records must be backfilled only from verified handover evidence.

Stable instructions precede changing context and the provider cache boundary.
The Chat Completions adapter, including Synthetic, sends no undocumented
cache-control hint. Completion metadata records prefix sizes and hashes,
and the provider's actual cache counters when supplied. Missing counters
remain unknown. These observations do not predict cache reuse or billing.
Synthetic's adapter sends the requested `low`, `medium` or `high` effort as
`reasoning_effort`, rather than the generic compatible-provider `reasoning`
object. Unsupported or conflicting effort options are refused. Correct effort
encoding does not guarantee a particular reasoning length or a completed answer.

Late or interrupted native calls appear in a durable context listing and
produce a local readiness wake; a wake can repeat after restart. `model-result`
provides a bounded excerpt, digest and retained size, with no action authority.
A newer decision must reconsider the evidence under current dependencies.
