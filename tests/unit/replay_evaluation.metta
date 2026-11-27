; Replay metrics distinguish the historical failure pattern from its
; certified repair, on the redacted reconstructions in tests/fixtures; and
; the edges: an effect that does not depend on a stimulus is never stale,
; a reopened phase is lost once, an unsupported claim counts even with a
; receipt elsewhere, and a replay that is neither a list nor an object of
; events is refused.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/replay_evaluation_cetta.metta)

(= (rv:fixture $name) (path:join (nt:required-env "TEST_REPOSITORY") (str:concat "tests/fixtures/" $name)))
(= (rv:metric $metrics $key) (sf:get $metrics $key JsonNull))
(= (rv:events $text) (rpl:evaluate (let (JsonArray $events) (json:parse $text) $events)))

!(let* (($before (rpl:evaluate-file (rv:fixture "godel_tmux_history_before.json")))
        ($repaired (rpl:evaluate-file (rv:fixture "godel_tmux_history_certified.json")))
        ($_ (nt:require historical-pattern-shows-each-failure-once
              (and (== (rv:metric $before "redundant_effects") 1)
              (and (== (rv:metric $before "stale_state_actions") 1)
              (and (== (rv:metric $before "lost_commitments") 1)
              (and (== (rv:metric $before "unsupported_claims") 1)
              (and (== (rv:metric $before "evidence_recovery_success") (/ 1.0 3))
                   (== (rv:metric $before "operator_interruption_latency_ms") 150.0))))))))
        ($_ (nt:require certified-replay-shows-none
              (and (== (rv:metric $repaired "redundant_effects") 0)
              (and (== (rv:metric $repaired "stale_state_actions") 0)
              (and (== (rv:metric $repaired "lost_commitments") 0)
              (and (== (rv:metric $repaired "unsupported_claims") 0)
              (and (== (rv:metric $repaired "evidence_recovery_success") 1.0)
                   (== (rv:metric $repaired "operator_interruption_latency_ms") 2.0))))))))
        ($_ (nt:require certified-replay-needs-less-context
              (< (rv:metric $repaired "context_chars_max") (rv:metric $before "context_chars_max"))))
        ($edges (rv:events "[{\"type\": \"context\", \"evidence_revision\": \"r1\", \"context_chars\": -5},
                             {\"type\": \"stimulus\", \"evidence_revision\": \"r2\", \"at_ms\": 10},
                             {\"type\": \"effect\", \"effect_key\": \"k\", \"issued_revision\": \"r1\", \"depends_on_stimulus\": false, \"result\": \"success\", \"receipt\": \"e1\"},
                             {\"type\": \"effect\", \"effect_key\": \"k\", \"issued_revision\": \"r1\", \"result\": \"failure\"},
                             {\"type\": \"phase\", \"phase\": \"p\", \"status\": \"completed\"},
                             {\"type\": \"phase\", \"phase\": \"p\", \"status\": \"active\"},
                             {\"type\": \"phase\", \"phase\": \"p\"},
                             {\"type\": \"claim\", \"claim\": \"completed\", \"supported_by\": \"e9\"},
                             {\"type\": \"claim\", \"claim\": \"completed\", \"supported_by\": \"e1\"},
                             {\"type\": \"interrupt\", \"at_ms\": 4}]"))
        ($_ (nt:require edges-follow-the-definitions
              (and (== (rv:metric $edges "stale_state_actions") 1)
              (and (== (rv:metric $edges "redundant_effects") 0)
              (and (== (rv:metric $edges "lost_commitments") 1)
              (and (== (rv:metric $edges "unsupported_claims") 1)
              (and (== (rv:metric $edges "context_chars_total") 0)
              (and (== (rv:metric $edges "operator_interruption_latency_ms") 0.0)
                   (== (rv:metric $edges "evidence_recovery_success") JsonNull)))))))))
        ($bad (path:join (nt:required-env "HOME") "bad-replay.json"))
        ($_ (fs:write $bad "{\"events\": 3}"))
        ($_ (nt:require a-replay-without-events-is-refused
              (== (rpl:evaluate-file $bad) (ReplayInvalid "replay must be a list or an object with events")))))
   (println! UNIT_REPLAY_EVALUATION_OK))
