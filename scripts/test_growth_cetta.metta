; Growth checks of native paths ported from Python (tests/growth_support.metta).
; Each registered check runs its workload at n, 4n and 16n on the core engine.
;   NATIVE_TEST_CORE=... NATIVE_TEST_ARTIFACTS=... [GROWTH_ONLY=name] \
;     $NATIVE_TEST_CORE/cetta --lang petta --import-mode ancestor-walk --quiet scripts/test_growth_cetta.metta
!(import! &self ../tests/growth_support.metta)

; (GrowthCheck name workload n bounds)
(= (growth:registered)
   ((GrowthCheck "coding-transcript-append" "tests/growth/coding_transcript_append.metta" 500 (growth:linear))
    (GrowthCheck "coding-settle-report" "tests/growth/coding_settle_report.metta" 64 (growth:linear))
    (GrowthCheck "coding-execution-result" "tests/growth/coding_execution_result.metta" 1024 (growth:bounded))))

(= (growth:run-all $root $core $artifacts $checks $only $ok)
   (if (== $checks ()) $ok
     (let* (((GrowthCheck $name $script $n $bounds) (car-atom $checks))
            ($selected (or (== $only "") (nt:contains $name $only)))
            ($passed (if $selected (growth:check $root $core $artifacts $name $script $n $bounds) True)))
       (growth:run-all $root $core $artifacts (cdr-atom $checks) $only (and $ok $passed)))))
!(nt:require growth-checks-within-bounds
   (growth:run-all (system:cwd) (nt:required-env "NATIVE_TEST_CORE") (nt:required-env "NATIVE_TEST_ARTIFACTS")
     (growth:registered) (system:getenv-or-default "GROWTH_ONLY" "") True))
!(println! NATIVE_GROWTH_CHECKS_PASSED)
