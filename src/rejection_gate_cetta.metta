; The provider-rejection gate's native fast path.
;
; A deterministic rejection (an invalid request, HTTP 400, 404, 405, 406, 410,
; 413, 415 or 422) is retained in the cognitive health record until observed
; corrective state changes; the host decides that (provider_rejection.blocked).
; A request needs that decision only when a block is possible: the record holds
; one, or this process saw such a rejection, whose block the host may hold in
; memory alone when the record could not be written. Otherwise the request is
; allowed here, without the host. The loop's retry wait asks the same way.
!(import! &self (library system))
!(import! &self ./turn_health_cetta.metta)
!(import! &self ./interop_flags_cetta.metta)

; The record holds a block: its provider_rejection member is an object, the
; last occurrence winning as json.loads keeps it. A missing, unreadable or
; torn record holds none, as cognitive_health._read() reads it.
(= (gate:holds? $record)
   (case (sf:get $record "provider_rejection" JsonNull)
     (((JsonObject $members) True)
      ($other False))))
(= (gate:recorded? $path) (gate:holds? (sf:native-object $path)))

; This process saw a deterministic rejection of a command turn.
(= (gate:latched?)
   (not (== (collapse (match &self (ProviderRejectionLatchV1) True)) ())))
(= (gate:invalid-request? $status)
   (if (== (get-type $status) Number)
     (or (== $status 400) (or (== $status 404) (or (== $status 405) (or (== $status 406)
       (or (== $status 410) (or (== $status 413) (or (== $status 415) (== $status 422)))))))) False))
(= (gate:latch)
   (if (gate:latched?) True (let $added (add-atom &self (ProviderRejectionLatchV1)) True)))
; Released once the host has allowed a request: corrective state changed.
(= (gate:release)
   (let $removed (collapse (match &self (ProviderRejectionLatchV1) (remove-atom &self (ProviderRejectionLatchV1)))) True))

(= (gate:possible?)
   (if (gate:latched?) True (gate:recorded? (health:path))))

; ------------------------------------------------------ the loop's retry wait

; retry_wait_seconds: the seconds the loop's wait honours after a failed turn,
; a pure function of the record, the time and the host's hold on a retained
; rejection (none when no block was possible, so the host was not asked).
; - A retained rejection is ended by corrective input, not time: 0 when a
;   context repair is pending, so the loop goes on to the repair; else 1, so
;   the bounded wait keeps polling for the correction. A hold the host could
;   not decide is kept: an unreadable observation never grants a retry.
; - Otherwise the backoff left until the record's retry_at, rounded up.
(= (gate:wait $record $now $hold)
   (case $hold
     ((repair 0)
      (blocked 1)
      (undecided 1)
      ($free (let $left (- (health:stamp $record "retry_at") $now)
               (if (> $left 0) (ceil-math $left) 0))))))
; The host decides a block (provider_rejection.blocked) only when one is
; possible, as for a request. Its "clear" may have released the block and its
; backoff in the record, which is read again; it also releases the latch, as
; an allowed request does.
(= (gate:retry-wait) (gate:retry-wait-at (health:path) (system:wall-time)))
(= (gate:retry-wait-at $path $now)
   (let $record (sf:native-object $path)
     (if (or (gate:latched?) (gate:holds? $record))
       (case (gate:host-hold)
         ((clear (let $released (gate:release) (gate:wait (sf:native-object $path) $now clear)))
          ($held (gate:wait $record $now $held))))
       (gate:wait $record $now none))))
; The host's text is normalized at ingress (host:text): it crosses as a
; String on CeTTa and as a Symbol on SWI-PeTTa.
(= (gate:host-hold)
   (case (host:text (py-call (native_llm_host.rejection_hold)))
     (("repair" repair)
      ("blocked" blocked)
      ("clear" clear)
      ($unknown undecided))))
