; Per-module unit tests of native ports. Each file runs in a fresh child of
; the Python-disabled core with its own scratch directory, and must print its
; marker. Run from the checkout root with NATIVE_TEST_CORE and
; NATIVE_TEST_ARTIFACTS; pass one unit name as the first argument to run only it.
!(import! &self ../tests/native_test_support.metta)

(= (units:all)
   (("tests/unit/model_selection.metta" "UNIT_MODEL_SELECTION_OK")
    ("tests/unit/loop_host.metta" "UNIT_LOOP_HOST_OK")
    ("tests/unit/commentary_contract.metta" "UNIT_COMMENTARY_ON_OK" (("METTACLAW_MODEL_COMMENTARY" "1")))
    ("tests/unit/commentary_contract.metta" "UNIT_COMMENTARY_OFF_OK" (("METTACLAW_MODEL_COMMENTARY" "0")))
    ("tests/unit/native_call.metta" "UNIT_NATIVE_CALL_KINDS_OK")
    ("tests/unit/structured_request.metta" "UNIT_STRUCTURED_REFUSALS_OK")
    ("tests/unit/completion_effects.metta" "UNIT_COMPLETION_EFFECTS_OK")
    ("tests/unit/rejection_gate.metta" "UNIT_REJECTION_GATE_OK")
    ("tests/unit/dispatch_stopped.metta" "UNIT_DISPATCH_STOPPED_OK")
    ("tests/unit/benchmark_statistics.metta" "UNIT_BENCHMARK_STATISTICS_OK")
    ("tests/unit/call_gate.metta" "UNIT_CALL_GATE_OK")
    ("tests/unit/call_gate.metta" "UNIT_CALL_GATE_RECYCLING_OK" (("METTACLAW_ACTIVE_LOOP_MODE" "coding")))
    ("tests/unit/strategy_broker.metta" "UNIT_STRATEGY_BROKER_OK")
    ("tests/unit/stream_cursors.metta" "UNIT_STREAM_CURSORS_OK")
    ("tests/unit/ledger_result.metta" "UNIT_LEDGER_RESULT_OK")
    ("tests/unit/coding_launch.metta" "UNIT_CODING_LAUNCH_OK")
    ("tests/unit/activity_source.metta" "UNIT_ACTIVITY_SOURCE_LEDGER_OK")
    ("tests/unit/native_transport.metta" "UNIT_NATIVE_TRANSPORT_OK")
    ("tests/unit/runtime_events.metta" "UNIT_RUNTIME_EVENTS_OK")
    ("tests/unit/input_notice.metta" "UNIT_INPUT_NOTICE_OK" (("METTACLAW_INSTANCE" "unit")))
    ("tests/unit/display.metta" "UNIT_DISPLAY_OK"
     (("METTACLAW_STREAM_DIR" "{home}/display") ("METTACLAW_INSTANCE" "unit")
      ("METTACLAW_ACTIVITY_CALL_SCOPE" "coding a/b") ("METTACLAW_STREAM_PARENT" "parent-1")))
    ("tests/unit/display.metta" "UNIT_DISPLAY_OFF_OK")))

(= (units:env $directory)
   (let $base (nt:environment $directory)
     (preq-free:append
       (("SYNTHETIC_MODEL" "unit-configured-model")
        ("METTACLAW_MODEL_STATE_PATH" (nt:join $directory "model-state"))
        ("METTACLAW_HEARTBEAT_INTERVAL_SECONDS" " 45 ") ("METTACLAW_COMMAND_BATCH_LIMIT" "0")
        ("METTACLAW_HISTORY_PATH" (nt:join $directory "journal/history.metta"))
        ("NATIVE_TEST_CORE" (nt:required-env "NATIVE_TEST_CORE")))
       $base)))
; A setting may name a path in the unit's own scratch directory as "{home}/...".
(= (units:homed $settings $directory)
   (map-atom $settings $setting
     (let ($name $value) $setting
       ($name (if (str:starts-with $value "{home}/")
                (nt:join $directory (str:byte-slice $value 7 (str:byte-length $value))) $value)))))
(= (preq-free:append $front $back)
   (if (== $front ()) $back (cons-atom (car-atom $front) (preq-free:append (cdr-atom $front) $back))))

(= (units:one $root $engine $artifacts ($file $marker))
   (units:one $root $engine $artifacts ($file $marker ())))
(= (units:one $root $engine $artifacts ($file $marker $extra))
   (let* (($directory (nt:join $artifacts (str:concat "unit-" (system:uuid4-hex))))
          ($_ (fs:make-directories $directory 448))
          ($output (nt:exit-ok (proc:run ($engine "--lang" "petta" "--profile" "extended" "--import-mode" "ancestor-walk" "--quiet" $file)
                                         $root (preq-free:append (units:homed $extra $directory) (units:env $directory)) 120000 1048576)))
          ($_ (nt:require unit-marker (nt:contains $output $marker)))
          ($_ (println! (UNIT_PASSED $file)))) 1))

(= (units:selected $all $only)
   (if (== $all ()) ()
     (let ($case $rest) ((car-atom $all) (cdr-atom $all))
       (if (or (== $only "") (nt:contains (car-atom $case) $only))
         (cons-atom $case (units:selected $rest $only))
         (units:selected $rest $only)))))

(= (units:run $root $engine $artifacts $cases $count)
   (if (== $cases ()) $count
     (let $_ (units:one $root $engine $artifacts (car-atom $cases))
       (units:run $root $engine $artifacts (cdr-atom $cases) (+ $count 1)))))

(= (units:main)
   (let* (($root (system:cwd)) ($engine (nt:join (nt:required-env "NATIVE_TEST_CORE") "cetta"))
          ($artifacts (nt:required-env "NATIVE_TEST_ARTIFACTS"))
          ($only (system:getenv-or-default "NATIVE_UNIT" ""))
          ($cases (units:selected (units:all) $only))
          ($_ (nt:require unit-selection-nonempty (not (== $cases ())))))
     (units:run $root $engine $artifacts $cases 0)))
!(let $count (units:main) (println! (NATIVE_UNITS_PASSED $count)))
