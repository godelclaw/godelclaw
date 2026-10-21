; Project existing provider rows into the finite model without repairing them.
; The ledger is opened read-only (sqlite:observe): a missing ledger is an
; error, never created, and nothing is reconciled, migrated or called. The
; result is diagnostics with no action authority.
!(import! &self (library fs))
!(import! &self (library json))
!(import! &self (library path))
!(import! &self (library sqlite))
!(import! &self (library str))
!(import! &self (library system))
!(import! &self ./RetryWakeModel.metta)

; src/native_lifetime.py TERMINAL_RECONCILE_GRACE_SECONDS; a test keeps them equal.
(= (gc:grace-seconds) 2)
(= (gc:scope) "read-only terminal-request diagnostics; no action authority")

(= (gc:float-text $number)
   (let $text (format-args "{}" ($number))
     (if (or (== (get-type (str:find $text ".")) Number) (== (get-type (str:find $text "e")) Number)) $text
       (str:concat $text ".0"))))
(= (gc:state $value) (if (== (get-type $value) String) (str:string->symbol $value) unrecorded))
(= (gc:text-json $value) (if (== (get-type $value) String) (JsonString $value) JsonNull))
(= (gc:terminal-state $request) (if (gc:known-request $request) (== (gc:terminal $request) terminal) False))
(= (gc:active-state $call) (if (gc:known-call $call) (== (gc:active $call) active) False))

(= (gc:observation ($id $call-text $updated $request-text) $now)
   (let* (($request (gc:state $request-text))
          ($call (gc:state $call-text))
          ($decision (gc:decide $request $call))
          ($result (case $decision (((GcModelDecisionV2 $r $c $outcome) (JsonString (str:symbol->string $outcome)))
                                    ($inapplicable JsonNull))))
          ($terminal (gc:terminal-state $request))
          ($active (gc:active-state $call))
          ($age (max 0 (- $now $updated)))
          ($settling (and $terminal (and $active (< $age (gc:grace-seconds))))))
     (JsonObject ((JsonPair "age_seconds" (JsonNumber (gc:float-text $age)))
                  (JsonPair "call" (gc:text-json $call-text))
                  (JsonPair "contradiction" (JsonBool (and $terminal (and $active (not $settling)))))
                  (JsonPair "id" (gc:text-json $id))
                  (JsonPair "model_result" $result)
                  (JsonPair "modelled" (JsonBool (!= $result JsonNull)))
                  (JsonPair "request" (gc:text-json $request-text))
                  (JsonPair "settlement_window" (JsonBool $settling))
                  (JsonPair "terminal_request" (JsonBool $terminal))))))
(= (gc:observations $rows $now)
   (if (== $rows ()) ()
     (let* (($head (gc:observation (car-atom $rows) $now)) ($rest (gc:observations (cdr-atom $rows) $now)))
       (cons-atom $head $rest))))

; (GcInspection json) or (GcInspectionError reason).
(= (gc:inspect $ledger $now)
   (let $module (path:join (system:cwd) "verification/RetryWake.module")
     (if (!= (str:sha256 (fs:read-text $module)) (gc:module-digest))
       (GcInspectionError "the generated model does not match the module source")
       (case (sqlite:observe $ledger
               "SELECT n.id,n.state,n.updated,r.status FROM native_calls n LEFT JOIN requests r ON r.id=n.id WHERE n.state IN ('claimed','submitted') ORDER BY n.created"
               () 1000)
         (((sqlite:result $columns $rows $changes $rowid)
           (GcInspection (JsonObject ((JsonPair "module_sha256" (JsonString (gc:module-digest)))
                                      (JsonPair "observations" (JsonArray (gc:observations $rows $now)))
                                      (JsonPair "scope" (JsonString (gc:scope)))))))
          ($failure (GcInspectionError (format-args "{}" ($failure)))))))))
