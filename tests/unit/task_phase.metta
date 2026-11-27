; The task-phase record the shadow flow keeps: advancing completes the phase
; left, with its evidence, and makes the next active; a phase completes only
; with evidence; a completed phase is never made active again.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/shadow_effect_cetta.metta)

(= (tp:path) (nt:required-env "METTACLAW_TASK_PHASE_PATH"))
(= (tp:phase $name) (sf:get (sf:get (sf:native-object (tp:path)) "phases" (JsonObject ())) $name (JsonObject ())))
(= (tp:status $name) (sf:get (tp:phase $name) "status" ""))
(= (tp:state) (JsonObject ((JsonPair "turn" 3) (JsonPair "effects" 1))))

!(let* (($_ (fs:write (tp:path) "{\"schema\": 1, \"task_id\": \"task-1\", \"revision\": 0, \"phases\": {\"inspect\": {\"status\": \"active\"}}}"))
        ($_ (se:advance-task-phase (tp:state) "inspect" "test" "receipt-1"))
        ($_ (nt:require advancing-completes-with-evidence-and-activates-the-next
              (and (== (tp:status "inspect") "completed")
              (and (== (sf:get (tp:phase "inspect") "evidence" "") "receipt-1")
                   (== (tp:status "test") "active")))))
        ($_ (se:advance-task-phase (tp:state) "test" "inspect" "receipt-2"))
        ($_ (nt:require a-completed-phase-cannot-silently-reopen
              (and (== (tp:status "inspect") "completed")
              (and (== (sf:get (tp:phase "inspect") "evidence" "") "receipt-1")
                   (== (tp:status "test") "completed")))))
        ($_ (fs:write (tp:path) "{\"schema\": 1, \"phases\": {\"build\": {\"status\": \"active\"}}}"))
        ($_ (se:advance-task-phase (tp:state) "build" "ship" "")))
   (nt:require completion-requires-evidence (== (tp:status "build") "active")))

!(println! UNIT_TASK_PHASE_OK)
