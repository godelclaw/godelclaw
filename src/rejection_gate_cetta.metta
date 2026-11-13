; The provider-rejection gate's native fast path.
;
; A deterministic rejection (an invalid request, HTTP 400, 404, 405, 406, 410,
; 413, 415 or 422) is retained in the cognitive health record until observed
; corrective state changes; the host decides that (provider_rejection.blocked).
; A request needs that decision only when a block is possible: the record holds
; one, or this process saw such a rejection, whose block the host may hold in
; memory alone when the record could not be written. Otherwise the request is
; allowed here, without the host.
!(import! &self (library json))
!(import! &self (library system))
!(import! &self (library path))
!(import! &self (library fs))
!(import! &self ./turn_health_cetta.metta)

; The record holds a block: its provider_rejection member is an object. An
; unreadable record holds none, as cognitive_health._read() reads it.
; A structured read: a missing or unreadable file is a value in both dialects.
(= (gate:recorded? $path)
   (case (fs:read-text-result $path 0 "raw")
     ((("ok" $text)
       (case (json:parse $text)
         (((JsonObject $pairs) (gate:object-member? $pairs "provider_rejection" False))
          ($other False))))
      ($unreadable False))))
; The last occurrence of a key wins, as json.loads keeps it.
(= (gate:object-member? $pairs $key $found)
   (if (== $pairs ()) $found
     (let* (($head (car-atom $pairs)) ($tail (cdr-atom $pairs)) ((JsonPair $name $value) $head)
            ($next (if (== $name $key) (case $value (((JsonObject $members) True) ($scalar False))) $found)))
       (gate:object-member? $tail $key $next))))

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
