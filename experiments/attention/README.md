# Small attention experiments

The running agent keeps its ordinary similarity/ECAN selector. Three optional
readouts test separate hypotheses: temporal recurrence, usefulness, and a fading
repetition penalty. None changes memory text, factual evidence, command execution
or the selected workspace.

```metta
(attention-studies)                          ; coverage, candidates, sources, limits
(attention-try "power-law" "current question" 10)
(attention-compare 10)                       ; same last pool, no new query
(attention-feedback "memory-id" 1)           ; optional usefulness observation
(attention-policy "study-arm" "gain-need")   ; persistent choice
(attention-policy "study-arm" "baseline")    ; ordinary selector
(attention-policy "study-mix" 0)             ; disable experimental readout
(settings "attention")                      ; values, defaults, domains and units
```

`attention-try` overrides the arm for that query; persistent choices are retained.
It performs a normal hybrid query, including normal recall exposure. Explicit
similarity queries always retain their similarity ordering. An unknown arm or
invalid numeric setting returns an error for that call.

`attention-compare` applies all four readouts to the last hybrid/attention query's
same candidates and frozen pre-query features. It does not query the embedding
service, stimulate attention, add feedback or save state. It is a comparison of
readouts on that pool, not an evaluation of every possible candidate generator.
It uses the currently selected `study-mix`.

## Scores and observations

Write `b` for the existing hybrid score and `m` for `study-mix`. An experimental
arm returns `(1-m)*b + m*a`:

| Arm | Alternative score `a` | Limitation |
|---|---|---|
| `power-law` | `x/(1+x)`, with `x=n/(1+age/scale)^decay` | Constant-space frequency/last-use proxy; not exact ACT-R history summation |
| `gain-need` | max-normalized finite PPR need times a smoothed usefulness mean | Optional ratings are not causal gain or PLN truth |
| `inhibition` | `b*(1-strength*2^(-age/half-life))` | A fading score penalty cannot by itself guarantee eventual selection |

Here `n` counts deliberate uses, including explicit reads with positive read
stimulus. Query exposure is not a deliberate use. Returned hits update a separate
shown count and timestamp even if the ECAN exposure amount is zero. Feedback keeps
positive and negative weighted totals; the gain proxy is
`(positive+prior)/(positive+negative+2*prior)`. With no observations and zero prior
it is `0.5`. Ignored hits receive no automatic negative rating.

One fixed-size `StudyTrace` per observed memory ID lives in the existing attention
snapshot. There is no timestamp history or additional service. These are
observations about an ID across edits, not revision-specific evidence. Forgetting
an ID removes its trace. Old snapshots acquire defaults without a migration or
qualification receipt.

All seven new settings register through the existing dynamic catalogue. A zero
mix disables the experimental effect; zero inhibition strength or half-life
disables the penalty; zero time scale or exponent disables temporal aging. Agent
changes are immediate and persistent.

The existing top-k certificate applies to the ordinary blended score. A changed
readout reports `ExperimentalUncertified` and uses the agent's existing finite
spreading budget. It does not claim the baseline certificate. The Lean results
about stochastic transport and discounted occupancy remain relevant to the linear
core; they do not establish that these new retrieval policies improve task results.

## Coverage

The machine-readable source is [attention_studies.json](../../src/attention_studies.json),
also available through `attention-studies`. The review's ten threads are kept
separate from additional speculative extensions.

| Thread | Small candidate | Current scope |
|---|---|---|
| Semantic random walks | Existing unit-seed PPR and similarity blend | Runtime, existing graph-oracle tests |
| Successor need and gain | Need times explicit usefulness estimate | Optional runtime arm |
| Recency/frequency | Last-use power-law proxy versus exact history sum and existing three horizons | Runtime arm and offline reference |
| Capacity/normalization | Unit seed, caller-selected top-k, temperature softmax | Runtime core and offline reference; token packing remains a separate context concern |
| Inhibition of return | Fading score multiplier | Optional runtime arm |
| Stable links | Once-per-turn co-use versus bounded moving average | Existing runtime and offline alternative; no automatic link-rule replacement |
| Adaptive gain | Agent-controlled restart/mixes; temperature sweep | Existing controls and offline reference; no automatic controller |
| Pattern completion | Small focus boost with explicit reset | Offline stickiness probe, not a Hopfield model |
| Graph diffusion | Truncated Poisson heat series on the same graph | Offline reference with missing mass reported |
| Retrieval diagnostics | Same-pool comparison, recent-repeat fraction and labelled failures | Runtime diagnostics and synthetic tests |

Still to evaluate: arm interactions, calibrated usefulness, real task-labelled
retrieval, partial-cue reconstruction beyond the stickiness probe, automatic
adaptation, amortized offline graph refresh and bounded forward lookahead. Compute
cost is a profiling concern separate from memory importance. No research thread is
marked beneficial merely because a formula runs.

The motivating sources include [ACT-R's reference manual](https://act-r.psy.cmu.edu/actr7.x/reference-manual.pdf),
[Mattar and Daw's replay-priority model](https://doi.org/10.1038/s41593-018-0232-z),
and [Reynolds and Heeger's normalization model](https://doi.org/10.1016/j.neuron.2009.01.002).
They motivate distinct experiments; the software composite is not a biological
claim.

## Reproduce

Set `CETTA_BIN`, `CETTA_SPACE_SERVICE`, `CETTA_VALUE_WORKER` and
`CETTA_CHANNEL_ROOT` as for the native memory tests, then run:

```sh
PYTHONPATH=tests python3 -m unittest test_attention_studies_engine -v
python3 experiments/attention/run.py --output attention-results.json
```

The developer runner creates a synthetic store; every selector and reference
operator runs in MeTTa. Python provides fixtures and writes measurements. An
optional `--baseline-root` compares against another source checkout using the same
engine and synthetic store. Warm-selector timing includes snapshot persistence,
and excludes embedding requests and model time.

The seven ranking probes contain three deliberately paired situations: the same
observations help in one task and hurt in another. They demonstrate a recency win
and an old-fact loss, a calibrated-gain win and a misleading-feedback loss, and a
repetition win and a necessary-revisit loss. They are mechanism tests, not an
estimate of real task success. Native tests also cover persistence, unchanged
memory evidence, scoped retrieval, disabled endpoints, temporary overrides,
passive comparison, Poisson-series coefficients, temperature, and focus reset.
