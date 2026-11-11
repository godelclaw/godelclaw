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

This pilot covers point lookup and observation composition. Native bounded-range
continuations are a separate extension and are not exposed by this version.
