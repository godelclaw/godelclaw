; Growth checks of native paths ported from Python (tests/growth_support.metta).
; Each registered check runs its workload at n, 4n and 16n on the core engine.
;   NATIVE_TEST_CORE=... NATIVE_TEST_ARTIFACTS=... [GROWTH_ONLY=name] \
;     $NATIVE_TEST_CORE/cetta --lang petta --import-mode ancestor-walk --quiet scripts/test_growth_cetta.metta
!(import! &self ../tests/growth_support.metta)

; (GrowthCheck name workload n bounds)
(= (growth:registered)
   ((GrowthCheck "coding-transcript-append" "tests/growth/coding_transcript_append.metta" 500 (growth:linear))
    (GrowthCheck "coding-settle-report" "tests/growth/coding_settle_report.metta" 64 (growth:linear))
    (GrowthCheck "coding-execution-result" "tests/growth/coding_execution_result.metta" 1024 (growth:bounded))
    (GrowthCheck "coding-freeze-bundle" "tests/growth/coding_freeze_bundle.metta" 200 (growth:linear))
    (GrowthCheck "coding-repin-checkpoint" "tests/growth/coding_repin_checkpoint.metta" 256 (growth:linear))
    (GrowthCheck "coding-estate-listing" "tests/growth/coding_estate_listing.metta" 40 (growth:linear))
    (GrowthCheck "linear-control" "tests/growth/linear_control.metta" 20000 (growth:linear))
    (GrowthCheck "model-selection" "tests/growth/model_selection.metta" 20000 (growth:linear))
    (GrowthCheck "journal-append" "tests/growth/loop_host.metta" 20000 (growth:linear))
    ; Rewriting copies the prompt a fixed number of times: linear, with a larger constant.
    (GrowthCheck "commentary-rewrite" "tests/growth/commentary_contract.metta" 40000 (GrowthBounds 6 2000000 4 8192))
    (GrowthCheck "native-call-owners" "tests/growth/native_call.metta" 500 (growth:linear))
    (GrowthCheck "structured-request" "tests/growth/structured_request.metta" 100 (growth:linear))
    (GrowthCheck "completion-effects" "tests/growth/completion_effects.metta" 2000 (growth:linear))
    ; Streamed attempts leave no atoms: the workload asserts none remain at
    ; every size; evaluation temporaries keep memory linear, not bounded.
    (GrowthCheck "stream-cursors" "tests/growth/stream_cursors.metta" 2000 (growth:linear))
    (GrowthCheck "apply-patch-lines" "tests/growth/apply_patch_lines.metta" 500 (growth:linear))))
; Mutants the gate must reject, run whenever no single check is selected.
(= (growth:mutants)
   ((GrowthCheck "quadratic-control" "tests/growth/quadratic_control.metta" 1000 (growth:linear))))
(= (growth:reject-all $root $core $artifacts $mutants)
   (if (== $mutants ()) True
     (let* (((GrowthCheck $name $script $n $bounds) (car-atom $mutants))
            ($_ (nt:require (GrowthMutantRejected $name) (growth:rejects $root $core $artifacts $name $script $n $bounds))))
       (growth:reject-all $root $core $artifacts (cdr-atom $mutants)))))

(= (growth:run-all $root $core $artifacts $checks $only $ok)
   (if (== $checks ()) $ok
     (let* (((GrowthCheck $name $script $n $bounds) (car-atom $checks))
            ($selected (or (== $only "") (nt:contains $name $only)))
            ($passed (if $selected (growth:check $root $core $artifacts $name $script $n $bounds) True)))
       (growth:run-all $root $core $artifacts (cdr-atom $checks) $only (and $ok $passed)))))
!(nt:require growth-checks-within-bounds
   (growth:run-all (system:cwd) (nt:required-env "NATIVE_TEST_CORE") (nt:required-env "NATIVE_TEST_ARTIFACTS")
     (growth:registered) (system:getenv-or-default "GROWTH_ONLY" "") True))
!(if (== (system:getenv-or-default "GROWTH_ONLY" "") "")
   (growth:reject-all (system:cwd) (nt:required-env "NATIVE_TEST_CORE") (nt:required-env "NATIVE_TEST_ARTIFACTS") (growth:mutants))
   True)
!(println! NATIVE_GROWTH_CHECKS_PASSED)
