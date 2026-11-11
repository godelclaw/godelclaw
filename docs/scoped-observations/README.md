# Scoped observations

Import `src/knowledge_observation_cetta.metta` explicitly to use the native
observation API. It supports CeTTa's PeTTa and HE extended profiles and imports
no Python modules. Existing agent loops and checkpoint behavior are unchanged.

`(Empty k)` is one inspectable value reporting no findings at the boundary
named by `k`. It is not computational zero, an empty container, or a failed
search. Each dialect retains its existing meaning of bare `Empty`.

## Read a captured view

Given an existing native byte-store snapshot `$view`:

```metta
(let $report (obs:lookup $view "candidate" 1048576)
  (case (obs:status $report)
    (((Empty $index) (obs:explain $report))
     ((Incomplete $index $reason) (obs:explain $report))
     ((Faulted $index $reason) (obs:diagnostics $report))
     ($other (obs:values $report)))))
```

`obs:lookup` observes raw byte-store values. `kb:read-report` observes a typed
knowledge fact by identity and preserves its decoded value, including unit,
empty containers and ordinary error-shaped data.

The index contains the physical source domain, captured epoch and revision,
exact encoded query and read region, portable observer contract and observation
identity. `obs:describe-index` and `obs:explain` render those descriptors without
executing the described query. The native point contract is shared by both
supported dialects; it does not infer a language from an environment variable.

The byte allowance limits materialization, not the work of deciding whether a
key exists. An absent key can therefore produce `Empty` even with a zero-byte
allowance; a present value too large to read produces `Incomplete`.

## Values and diagnostics

```metta
(ObservationValuesV1 index coverage items)
(ObservationDiagnosticV1 operation reason encoded-detail)
```

`obs:values` returns the tagged projection above. The coverage field keeps a
partial value list visibly partial. This projection discards fault occurrences,
occurrence identities and negative dependencies; do not reconstruct an absence
claim from its list. A malformed occurrence or codec returns
`ObservationRefusedV1`, never extra values spliced into a successful list.

`obs:diagnostics` retains structured fault occurrences. `obs:diagnostic-text`
renders one diagnostic, including its original detail. Returned data shaped
like `(Error source reason)` remains a value and is not reclassified as a raised
fault. Resource limits, invalidated views, malformed native replies and native
refusals have distinct diagnostics. No helper automatically retries an effect.

## Compose observations

`obs:choice` is ordered choice: it retains answer multiplicity, qualifies the
occurrence identities by branch and keeps each branch's coverage. A completed
empty branch combined with an unfinished branch that found three values keeps
all three values and unfinished coverage.

`obs:reconcile` accepts an identical replay or a same-index prefix extension.
It refuses another scope, conflicting occurrences or a changed closed report.
It is not set union and does not merge separate worlds into a new search.

The dependencies field records negative assumptions. Positive observations also
retain their read region in the index; an empty negative-dependency list is not
permission to omit those reads from a later transaction.

## Keep publication bound to the native view

`kb:observe-report` retains a bound native observation. Obtain its inspectable
report with `kb:observation-report`. `kb:report-scopes-on` re-observes the exact
fact under the retained view before returning `KnowledgeReadScopesV2`.

A fabricated `Empty`, a serialized report, or a copied plausible payload grants
no execution authority. The existing broker must still authenticate the action
and publish through `store:commit-scoped`. An insertion after a negative read
can make that publication conflict; it does not make the historical report a
claim about the new snapshot.

## Bounded ranges and owned residuals

Import `src/observation_range_cetta.metta`. Open a captured range with
`(obs:open-range view lower upper maximum-cursors)`. Bounds are byte keys or
`NoBound`; the lower bound is inclusive and the upper bound is exclusive.
The explicit positive cursor allowance bounds this interpreter's outstanding
registered residuals. Each call returns:

```metta
(ObservationStepV1 report cursor-or-NoObservationContinuationV1)
```

Call `(obs:step cursor work-budget maximum-bytes)` to advance or
`(obs:cancel cursor)` to finish without claiming complete coverage. A step
consumes its cursor exactly once; use the returned cursor for the next step.
A report remains ordinary reusable data after its cursor is consumed.

```metta
(let (ObservationStepV1 $initial $cursor)
  (obs:open-range $view NoBound NoBound 8)
  (let (ObservationStepV1 $report $next) (obs:step $cursor 16 1048576)
    (case $next
      ((NoObservationContinuationV1 (obs:values $report))
       ($owned (let $cancelled (obs:cancel $owned)
                 (obs:values $report)))))))
```

Work counts native cursor offers, including the end-of-range check. Zero work
keeps the observation incomplete. A byte limit keeps the offered row pending,
so a larger allowance retries that row. Cancellation keeps earlier occurrences
and leaves coverage open. Invalid budgets refuse without consuming the cursor.
Snapshot clones and scans pin the captured root: later writes and compaction
cannot substitute newer values. Close or cancel residuals when finished.

Residual handles belong to their live process and are not durable restart
checkpoints. Only their reports are portable data.

Operations are serialized within one interpreter; this API does not offer
concurrent mutation of a residual registry. It uses existing native ownership
and revocation, and adds no evaluator, parser or C changes.

## Overlapping fragments

Import `src/observation_reconcile_cetta.metta` for `obs:reconcile-overlap`.
It weaves compatible same-index ordered fragments, coalesces replayed occurrence
identities and preserves distinct equal-valued occurrences. Both inputs must
have unique occurrence identities. Conflicting payloads, conflicting order and
new events beyond a supposedly closed observation are refused. Two unfinished
fragments remain unfinished; their combination does not invent exhaustive
coverage. Both negative-premise lists remain attached.

## Other observation boundaries

Import `src/observation_interfaces_cetta.metta`:

* `obs:regex-find` uses a captured native regex question and subject. A native
  no-match is complete absence; epsilon is one zero-consumption match; a
  compilation or execution failure remains a fault. The index records the native
  Unicode base version. Unencodable prepared handles refuse with descriptor-codec;
  use the ordinary encodable pattern definition.
* `obs:syntax` encodes a term as inert data. `obs:undemanded` produces a
  NotObserved report. Neither operation runs the described computation.
* `obs:negative-grade` retains the exact negative dependencies of completed
  Empty reports. It refuses positive, unfinished and fault reports. A grade is
  inspectable data; a proof consumer must discharge its indexed premises.

Type-level emptiness remains in the type system; no new evaluator primitive is
introduced. The Lean contract treats empty types, epsilon and result cardinality
at their respective interfaces.

## Qualification and cost

The contract modules are
`Mettapedia.Languages.FiniteChoice.ScopedObservation` and
`Mettapedia.Languages.FiniteChoice.ScopedObservationContracts`. They prove finite
producer, scan, ordered composition, demand-sensitive binding and negative-grade
properties; they do not constitute a C refinement proof. Native tests exercise
both extended profiles, real compaction, concurrent insertion conflicts and
codec/parser round trips. Existing dialect goldens are unchanged.

A scope-sharing wire prototype was rejected: its repeated-scope representation
was much smaller, but packing slowed down both that workload and a single-query
control. It is not part of the runtime. The normal API retains existing native
atom sharing and uses the existing CAV1 codec; richer reports have explicit
metadata and encoding costs. None of these helpers runs on the default agent
loop unless imported and called.
