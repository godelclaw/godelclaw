; Evidence-based metrics for historical and simulated agent replays. A
; replay is a list of events (or an object holding one under "events"):
;
;   context   evidence_revision, context_chars: the evidence an action sees
;   stimulus  evidence_revision, at_ms: new evidence arrives
;   interrupt at_ms: the operator interrupts; answers the oldest stimulus
;   effect    effect_key or action_id, issued_revision, depends_on_stimulus
;             (default true), result, receipt
;   phase     phase, status (default "pending")
;   claim     claim "completed", supported_by: a receipt
;   recovery  required_sources, recovered_sources
;
; The metrics: an effect issued against an older evidence revision than the
; current one is stale; a second successful effect with the same key is
; redundant; a completed phase that is reopened is a lost commitment; a
; completion claim no successful receipt supports is unsupported; recovery
; success is the share of required sources recovered; interruption latency
; is the mean time from a stimulus to the interrupt answering it.
!(import! &self (library json))
!(import! &self (library fs))
!(import! &self (library list))
!(import! &self ./state_file_cetta.metta)

(= (rpl:member? $x $xs)
   (if (== $xs ()) False (if (== (car-atom $xs) $x) True (rpl:member? $x (cdr-atom $xs)))))
(= (rpl:adjoin $x $xs) (if (rpl:member? $x $xs) $xs (cons-atom $x $xs)))
(= (rpl:max $a $b) (if (> $a $b) $a $b))
(= (rpl:sum $xs) (foldl-atom $xs 0 $acc $x (+ $acc $x)))
(= (rpl:largest $xs) (foldl-atom $xs 0 $acc $x (rpl:max $acc $x)))
; A JSON value's truth as Python reads it.
(= (rpl:truthy? $value)
   (not (rpl:member? $value (false JsonNull 0 0.0 ""))))
; A field as text the way Python's str() writes the values a replay holds.
(= (rpl:text $value)
   (if (== (get-type $value) String) $value
     (case $value ((JsonNull "None") (true "True") (false "False") ($other (repr $other))))))
(= (rpl:field $event $key) (sf:get $event $key JsonNull))
(= (rpl:has? $event $key)
   (let (JsonObject $pairs) $event
     (not (== (collapse (let (JsonPair $k $v) (superpose $pairs) (if (== $k $key) $k (empty)))) ()))))
(= (rpl:texts $value)
   (case $value (((JsonArray $items) (map-atom $items $item (rpl:text $item))) ($other ()))))
(= (rpl:distinct $xs) (foldl-atom $xs () $acc $x (rpl:adjoin $x $acc)))
(= (rpl:float $x) (* 1.0 $x))

; The state the events fold through.
(= (rpl:start) (ReplayState JsonNull () () () 0 0 0 0 () 0 0 () ()))
(= (rpl:step $state $event)
   (let (ReplayState $revision $effects $receipts $phases $redundant $stale $lost $unsupported
                     $contexts $recovered $required $pending $latencies) $state
     (case (rpl:field $event "type")
       (("context"
         (let $chars (rpl:max 0 (sf:get $event "context_chars" 0))
           (ReplayState (rpl:field $event "evidence_revision") $effects $receipts $phases $redundant $stale
                        $lost $unsupported (cons-atom $chars $contexts) $recovered $required $pending $latencies)))
        ("stimulus"
         (ReplayState (sf:get $event "evidence_revision" $revision) $effects $receipts $phases $redundant
                      $stale $lost $unsupported $contexts $recovered $required
                      (list:append $pending ((rpl:float (sf:get $event "at_ms" 0)))) $latencies))
        ("interrupt"
         (if (== $pending ()) $state
           (let* (($started (car-atom $pending))
                  ($latency (rpl:max 0.0 (- (rpl:float (sf:get $event "at_ms" $started)) $started))))
             (ReplayState $revision $effects $receipts $phases $redundant $stale $lost $unsupported
                          $contexts $recovered $required (cdr-atom $pending) (cons-atom $latency $latencies)))))
        ("effect"
         (let* (($stale-now (and (rpl:truthy? (sf:get $event "depends_on_stimulus" true))
                                 (and (not (== $revision JsonNull))
                                      (not (== (rpl:field $event "issued_revision") $revision)))))
                ($stale2 (if $stale-now (+ $stale 1) $stale)))
           (if (not (== (rpl:field $event "result") "success"))
               (ReplayState $revision $effects $receipts $phases $redundant $stale2 $lost $unsupported
                            $contexts $recovered $required $pending $latencies)
             (let* (($key (rpl:text (if (rpl:has? $event "effect_key") (rpl:field $event "effect_key")
                                      (sf:get $event "action_id" ""))))
                    ($redundant2 (if (and (not (== $key "")) (rpl:member? $key $effects)) (+ $redundant 1) $redundant))
                    ($effects2 (if (== $key "") $effects (rpl:adjoin $key $effects)))
                    ($receipt (rpl:field $event "receipt"))
                    ($receipts2 (if (rpl:truthy? $receipt) (rpl:adjoin (rpl:text $receipt) $receipts) $receipts)))
               (ReplayState $revision $effects2 $receipts2 $phases $redundant2 $stale2 $lost $unsupported
                            $contexts $recovered $required $pending $latencies)))))
        ("phase"
         (let* (($phase (rpl:text (sf:get $event "phase" "")))
                ($status (rpl:text (sf:get $event "status" "pending")))
                ($was (collapse (let (PhaseStatus $p $s) (superpose $phases) (if (== $p $phase) $s (empty)))))
                ($lost2 (if (and (== $was ("completed")) (not (== $status "completed"))) (+ $lost 1) $lost))
                ($phases2 (if (== $phase "") $phases
                            (cons-atom (PhaseStatus $phase $status)
                              (filter-atom $phases $entry (let (PhaseStatus $p2 $s2) $entry (not (== $p2 $phase))))))))
           (ReplayState $revision $effects $receipts $phases2 $redundant $stale $lost2 $unsupported
                        $contexts $recovered $required $pending $latencies)))
        ("claim"
         (if (not (== (rpl:field $event "claim") "completed")) $state
           (let* (($support (rpl:text (sf:get $event "supported_by" "")))
                  ($unsupported2 (if (or (== $support "") (not (rpl:member? $support $receipts)))
                                     (+ $unsupported 1) $unsupported)))
             (ReplayState $revision $effects $receipts $phases $redundant $stale $lost $unsupported2
                          $contexts $recovered $required $pending $latencies))))
        ("recovery"
         (let* (($needed (rpl:distinct (rpl:texts (sf:get $event "required_sources" (JsonArray ())))))
                ($found (rpl:distinct (rpl:texts (sf:get $event "recovered_sources" (JsonArray ())))))
                ($hits (size-atom (filter-atom $needed $source (rpl:member? $source $found)))))
           (ReplayState $revision $effects $receipts $phases $redundant $stale $lost $unsupported
                        $contexts (+ $recovered $hits) (+ $required (size-atom $needed)) $pending $latencies)))
        ($other $state)))))

; The metrics of a list of events, as a JSON object with sorted keys.
(= (rpl:evaluate $events)
   (let (ReplayState $revision $effects $receipts $phases $redundant $stale $lost $unsupported
                     $contexts $recovered $required $pending $latencies)
        (foldl-atom $events (rpl:start) $state $event (rpl:step $state $event))
     (JsonObject
       ((JsonPair "context_chars_max" (rpl:largest $contexts))
        (JsonPair "context_chars_total" (rpl:sum $contexts))
        (JsonPair "evidence_recovery_success"
          (if (== $required 0) JsonNull (/ (rpl:float $recovered) $required)))
        (JsonPair "lost_commitments" $lost)
        (JsonPair "operator_interruption_latency_ms"
          (if (== $latencies ()) JsonNull (/ (rpl:float (rpl:sum $latencies)) (size-atom $latencies))))
        (JsonPair "redundant_effects" $redundant)
        (JsonPair "stale_state_actions" $stale)
        (JsonPair "unsupported_claims" $unsupported)))))
; A replay file: a list of events, or an object with one under "events".
(= (rpl:evaluate-file $path)
   (let $value (json:parse (fs:read-text $path))
     (case $value
       (((JsonArray $events) (rpl:evaluate $events))
        ((JsonObject $pairs)
         (case (sf:get $value "events" JsonNull)
           (((JsonArray $events2) (rpl:evaluate $events2))
            ($none (ReplayInvalid "replay must be a list or an object with events")))))
        ($bad (ReplayInvalid "replay must be a list or an object with events"))))))
