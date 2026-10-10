# Model request lifetimes

Native model transport and attention have separate lifetimes. The MeTTa
client submits once, retains an owned `(llm:v1:pending …)` value, and polls
that handle. Other clients' completions stay in the native worker.

`llm:begin plan`, `llm:poll-once plan state` and
`llm:wait-once plan state milliseconds` support nonblocking callers. A bounded
wait returns the original pending value when no terminal answer is available.
`llm:run` is the foreground policy: it continues waiting for that same call.
The independent control responder and read-only topic workers remain runnable.
This does not add an autonomous second foreground tool loop.

Ordinary input queues without preempting a decision. Explicit steering and
changes to captured authority block adoption; returned evidence stays queryable
through `model-jobs` and `model-result`. Tools and outgoing messages still pass
through the existing observed strategy broker. Transport progress, heartbeats
and partial JSON never authorize an action.

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
Missing finish, missing `[DONE]`, mixed response identities, provider errors,
incomplete events and partial tool batches fail closed. There is no generation
POST retry or stream reconnect in the adapter.

The call journal records submission authority before networking, then the
handle, soft-wait and byte observations, terminal evidence and precise failure
reason. One foreground call is admitted at a time; background capacity and
budgets retain their existing limits. Routine recycling and pausing wait for
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

The checked `AsyncWait` model accompanies `TopicAsync` and `CooperativeInput`
in MeTTapedia. They establish model laws with explicit host assumptions;
the loopback tests supply implementation evidence, not a proof of libcurl or
the compiled host.
