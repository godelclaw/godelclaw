# Evidence handoffs

A conversation checkpoint may retain model-authored evidence claims beside its
summary. Each claim names a source and the version that was observed. Resumption
shows that recorded version beside a newly observed version, or an explicit
unavailable result. The runtime does not label a claim trusted or true.

The optional compaction envelope is ordinary JSON:

```json
{
  "kind": "godelclaw-handoff-v1",
  "summary": "Continue the task, preserving its requirements and unresolved checks.",
  "claims": [
    {
      "claim": "The source was inspected at this version.",
      "source": {"kind": "file", "path": "/workspace/source.metta"},
      "version": "sha256:<64 hex digits observed from the file>"
    }
  ]
}
```

Plain historical handoffs remain readable, with no manufactured provenance.
Recognized structured envelopes must have a nonempty summary and at most 32
valid claims. Claim objects contain exactly `claim`, `source`, and `version`;
duplicate keys and extra authority-looking fields refuse.

Supported sources:

- `file`: an absolute `path`. A bounded regular-file read returns a SHA-256
  observation only if the metadata before and after the read agrees. Files over
  one MiB, changed files, missing paths and nonregular resources are unavailable.
  This is not an immutable filesystem snapshot.
- `request-head`: a `ledger` owner and request `id`. Owner and request metadata
  come from one captured native-store view. The fingerprint describes the request
  header, including its disposition; it is not a certificate of task completion
  or a digest of an external artifact. A different ledger refuses.

`hevidence:version` returns `HandoffVersionObservedV1` or
`HandoffVersionUnavailableV1`. The latter retains the producer diagnostic. The
bounded prompt projection uses a short unavailable reason; it never substitutes
an empty version or infers a negative finding.

One render observes each distinct source once, including unavailable sources,
while retaining claim order and duplicate occurrences. The cache ends with that
render. A subsequent render observes again. Cache-boundary markers inside the
JSON are escaped reversibly so claim text cannot split provider request regions.
Budget checks reserve the complete serialized evidence with a conservative
bound on producer version fields; the token conversion remains an estimate.

Checkpoint summary and claims publish together through the existing compare and
swap. Stale publication preserves the previous checkpoint. Workflow state and
pending deliveries continue to come from the ledger, independently of handoffs.

A matching version does not establish the original claim. The corresponding
Lean contract requires the claim's establishment separately, and does not assert
implementation refinement or a single global snapshot across different sources.
