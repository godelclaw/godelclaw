!(import! &self ../tests/native_source_mutation.metta)
(= (handoff-mutant:run)
   (let* (($cache (im:case "handoff-uncached-unknown" "src/handoff_evidence_cetta.metta"
              "((HandoffObservationFoundV1 $known) $known)" "((HandoffObservationFoundV1 $known) ($observe $source))"
              "scripts/test_handoff_evidence_cetta.metta" "FAIL shared-source-observed-once"))
          ($version (im:case "handoff-recorded-as-current" "src/handoff_evidence_cetta.metta"
              "(JsonPair \"current_version\" $current)" "(JsonPair \"current_version\" $recorded)"
              "scripts/test_handoff_evidence_cetta.metta" "FAIL current-version-observes-change"))
          ($publication (im:case "handoff-dropped-claims" "src/context_compaction.metta"
              "(JsonPair \"claims\" (JsonArray $claims))" "(JsonPair \"claims\" (JsonArray ()))"
              "scripts/test_handoff_evidence_cetta.metta" "FAIL exact-claims-stored"))
          ($budget (im:case "handoff-uncharged-evidence" "src/context_compaction.metta"
              "($next (state:json-set $with-summary \"claims\" (JsonArray $claims)))" "($next $with-summary)"
              "scripts/test_handoff_evidence_cetta.metta" "FAIL oversized-evidence-does-not-fit"))) NativeHandoffMutantsPassedV1))
!(nt:require handoff-mutants-completed (nt:equal (collapse (handoff-mutant:run)) (NativeHandoffMutantsPassedV1)))
!(println! NATIVE_HANDOFF_MUTANTS_PASSED)
