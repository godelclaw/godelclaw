; Growth checks of native paths ported from Python (tests/growth_support.metta).
; Each registered check runs its workload at n, 4n and 16n: a GrowthCheck on
; the core engine, an SdkGrowthCheck on the SDK engine, for the engine
; library the agents load.
;   NATIVE_TEST_CORE=... NATIVE_TEST_SDK=... NATIVE_TEST_ARTIFACTS=... [GROWTH_ONLY=name] \
;     $NATIVE_TEST_CORE/cetta --lang petta --import-mode ancestor-walk --quiet scripts/test_growth_cetta.metta
!(import! &self ../tests/growth_support.metta)

; (GrowthCheck name workload n bounds), (SdkGrowthCheck name workload n bounds)
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
    (GrowthCheck "command-request" "tests/growth/command_request.metta" 1000 (growth:linear))
    (GrowthCheck "activity-source-ack" "tests/growth/activity_source.metta" 500 (growth:linear))
    (GrowthCheck "completion-effects" "tests/growth/completion_effects.metta" 2000 (growth:linear))
    ; Streamed attempts leave no atoms: the workload asserts none remain at
    ; every size; evaluation temporaries keep memory linear, not bounded.
    (GrowthCheck "stream-cursors" "tests/growth/stream_cursors.metta" 2000 (growth:linear))
    (GrowthCheck "apply-patch-lines" "tests/growth/apply_patch_lines.metta" 500 (growth:linear))
    (GrowthCheck "access-policy-decide" "tests/growth/access_policy_decide.metta" 200 (growth:linear))
    (GrowthCheck "turn-health" "tests/growth/turn_health.metta" 8000 (growth:linear))
    (GrowthCheck "input-notice-scan" "tests/growth/input_notice.metta" 250 (growth:linear))
    (GrowthCheck "display-records" "tests/growth/display.metta" 500 (growth:linear))
    (GrowthCheck "commentary-observer-events" "tests/growth/commentary_observer_events.metta" 500 (growth:linear))
    ; The field stays within the observer's 16 KiB bound at 16n.
    (GrowthCheck "commentary-observer-prefix" "tests/growth/commentary_observer_prefix.metta" 1000 (growth:linear))
    (SdkGrowthCheck "responses-encoder" "tests/growth/responses_encoder.metta" 100 (growth:linear))
    (SdkGrowthCheck "anthropic-encoder" "tests/growth/anthropic_encoder.metta" 200 (growth:linear))))
; Mutants the gate must reject, run whenever no single check is selected.
(= (growth:mutants)
   ((GrowthCheck "quadratic-control" "tests/growth/quadratic_control.metta" 1000 (growth:linear))))
; Engine needs: bounds the SDK engine does not meet yet, held as strict
; expected failures. Each must still break its bounds; when an engine roll
; meets one, the gate fails until the check moves to the registered list.
; None is open: anthropic-encoder moved to the registered list when the
; engine's Anthropic conversation encoder became linear (engine 39e38c7f).
(= (growth:engine-needs) ())
; The engine a check's children run on, and the check's own fields.
(= (growth:on (GrowthCheck $name $script $n $bounds) $core $sdk) ($core $name $script $n $bounds))
(= (growth:on (SdkGrowthCheck $name $script $n $bounds) $core $sdk) ($sdk $name $script $n $bounds))
; Every check must break its bounds; $label names the requirement.
(= (growth:reject-all $root $core $sdk $artifacts $checks $label)
   (if (== $checks ()) True
     (let* ((($engine $name $script $n $bounds) (growth:on (car-atom $checks) $core $sdk))
            ($_ (nt:require ($label $name) (growth:rejects $root $engine $artifacts $name $script $n $bounds))))
       (growth:reject-all $root $core $sdk $artifacts (cdr-atom $checks) $label))))

(= (growth:run-all $root $core $sdk $artifacts $checks $only $ok)
   (if (== $checks ()) $ok
     (let* ((($engine $name $script $n $bounds) (growth:on (car-atom $checks) $core $sdk))
            ($selected (or (== $only "") (nt:contains $name $only)))
            ($passed (if $selected (growth:check $root $engine $artifacts $name $script $n $bounds) True)))
       (growth:run-all $root $core $sdk $artifacts (cdr-atom $checks) $only (and $ok $passed)))))
!(nt:require growth-checks-within-bounds
   (growth:run-all (system:cwd) (nt:required-env "NATIVE_TEST_CORE") (nt:required-env "NATIVE_TEST_SDK")
     (nt:required-env "NATIVE_TEST_ARTIFACTS") (growth:registered) (system:getenv-or-default "GROWTH_ONLY" "") True))
!(if (== (system:getenv-or-default "GROWTH_ONLY" "") "")
   (let* (($root (system:cwd)) ($core (nt:required-env "NATIVE_TEST_CORE")) ($sdk (nt:required-env "NATIVE_TEST_SDK"))
          ($artifacts (nt:required-env "NATIVE_TEST_ARTIFACTS"))
          ($_ (growth:reject-all $root $core $sdk $artifacts (growth:mutants) GrowthMutantRejected)))
     (growth:reject-all $root $core $sdk $artifacts (growth:engine-needs) GrowthEngineNeedOpen))
   True)
!(println! NATIVE_GROWTH_CHECKS_PASSED)
