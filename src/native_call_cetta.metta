; The native owner of a prepared provider call: its immutable call facts, the
; seal of its prepared transport, and its owned phase. Decisions and ledger
; metadata are computed here. The host plan remains only as the handle that
; existing Python observers (display, health, completion effects) take; no
; decision reads its fields.
;
; Phase: ready -> claiming -> claimed -> submitted, or -> refused, kept as one
; NativeCallPhaseV1 atom per live call, keyed by its correlation. Delivery
; releases it; a released owner is spent: it never begins again and a replay
; answers duplicate, never a second request. Publication is marked ready at
; most once.
!(import! &self (library json))
!(import! &self (library system))
!(import! &self ./llm_policy_cetta.metta)

(: NativeCallOwnerV1 (-> %Undefined% %Undefined% %Undefined% %Undefined%))
(: CallPhaseV1 (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))

; $facts is the json:parse value of the seed facts plus the bound fingerprint,
; wire model, prepared stream flag and cache layout.
; Constructed values stay inert in this dialect: every field is computed and
; bound before a record is built.
(= (call:owner $host $facts $seal)
   (let* (($id (lp:field $facts "correlation" ""))
          ($forgotten (call:forget $id))
          ($added (add-atom &self (NativeCallPhaseV1 $id (CallPhaseV1 ready () () () () JsonNull none)))))
     (NativeCallOwnerV1 $host $facts $seal)))
; Rebuild each atom from its matched field: an atom taken whole from a
; collapsed match is not found again by remove-atom in this engine.
(= (call:forget $id)
   (collapse (match &self (NativeCallPhaseV1 $id $phase) (remove-atom &self (NativeCallPhaseV1 $id $phase)))))
(= (call:release $owner) (call:forget (call:correlation $owner)))

(= (call:native? $owner) (case $owner (((NativeCallOwnerV1 $h $f $s) True) ($other False))))
; The handle Python observers take, for a native owner or a legacy plan.
(= (call:host $owner) (case $owner (((NativeCallOwnerV1 $host $f $s) $host) ($legacy $legacy))))
(= (call:fact (NativeCallOwnerV1 $host $facts $seal) $key $default) (lp:field $facts $key $default))
(= (call:phase $owner)
   (let* (($id (call:correlation $owner))
          ($found (collapse (match &self (NativeCallPhaseV1 $id $phase) $phase))))
     (if (== $found ()) (CallPhaseV1 spent () () () () JsonNull published) (car-atom $found))))
(= (call:set-phase $owner $value)
   (let* (($id (call:correlation $owner)) ($forgotten (call:forget $id)))
     (add-atom &self (NativeCallPhaseV1 $id $value))))

(= (call:seal-matches? (NativeCallOwnerV1 $host $facts $seal) $candidate) (== $seal $candidate))
(= (call:correlation $owner) (call:fact $owner "correlation" ""))
(= (call:protocol $owner) (call:fact $owner "protocol" ""))
(= (call:compaction? $owner) (== (call:fact $owner "purpose" "turn") "compaction"))
; The decoder's (stream cap) facts, as response_decode_config gave them.
(= (call:decode-config $owner)
   (let* (($stream (if (== (call:fact $owner "prepared_stream" false) true) "true" "false"))
          ($cap (call:fact $owner "cap" 0)))
     ($stream $cap)))

; Not refused: the host's ready flag before this owner existed.
(= (call:ready? $owner)
   (let (CallPhaseV1 $status $t $s $r $soft $retry $publication) (call:phase $owner)
     (not (== $status refused))))
(= (call:startable? $owner)
   (let (CallPhaseV1 $status $t $s $r $soft $retry $publication) (call:phase $owner) (== $status ready)))
; One claim: the first begin moves ready to claiming; any other begin refuses.
(= (call:begin-claim $owner)
   (let (CallPhaseV1 $status $t $s $r $soft $retry $publication) (call:phase $owner)
     (if (== $status ready)
       (let $_ (call:set-phase $owner (CallPhaseV1 claiming $t $s $r $soft $retry $publication)) True)
       False)))
(= (call:claimed $owner)
   (let (CallPhaseV1 $status $t $s $r $soft $retry $publication) (call:phase $owner)
     (call:set-phase $owner (CallPhaseV1 claimed $t $s $r $soft $retry $publication))))
(= (call:refused $owner)
   (let (CallPhaseV1 $status $t $s $r $soft $retry $publication) (call:phase $owner)
     (call:set-phase $owner (CallPhaseV1 refused $t $s $r $soft $retry $publication))))
(= (call:submitted $owner $transport)
   (let* (((CallPhaseV1 $status $t $s $r $soft $retry $publication) (call:phase $owner))
          ($now (system:monotonic-ns)))
     (call:set-phase $owner (CallPhaseV1 submitted $transport $now $r $soft $retry $publication))))
; Only a submitted call owns a transport.
(= (call:owns? $owner $epoch $correlation $transport)
   (let (CallPhaseV1 $status $t $s $r $soft $retry $publication) (call:phase $owner)
     (if (== $t ()) False
       (and (== (lp:text $epoch) (call:fact $owner "epoch" ""))
            (and (== (lp:text $correlation) (call:correlation $owner)) (== $transport $t))))))
(= (call:retry-after $owner $seconds)
   (let* (((CallPhaseV1 $status $t $s $r $soft $retry $publication) (call:phase $owner))
          ($value (if (>= $seconds 0) $seconds JsonNull)))
     (call:set-phase $owner (CallPhaseV1 $status $t $s $r $soft $value $publication))))

; Whether the soft wait has elapsed since submission, as 0 or 1.
(= (call:soft-elapsed $owner $now)
   (let (CallPhaseV1 $status $t $submitted $r $soft $retry $publication) (call:phase $owner)
     (if (== $submitted ()) 0
       (if (>= (- $now $submitted) (* (call:fact $owner "soft" 0) 1000000)) 1 0))))
; A progress record is due every two seconds, or at once when the soft state
; changes. Answers (due soft) and records the due observation.
(= (call:progress-due $owner)
   (let* (($now (system:monotonic-ns)) ($soft (call:soft-elapsed $owner $now))
          ((CallPhaseV1 $status $t $s $last $last-soft $retry $publication) (call:phase $owner)))
     (if (if (== $last ()) False (and (< (- $now $last) 2000000000) (== $soft $last-soft)))
       (False $soft)
       (let $_ (call:set-phase $owner (CallPhaseV1 $status $t $s $now $soft $retry $publication))
         (True $soft)))))

; The committed completion is published once; a repeated commit is refused.
(= (call:publication-ready $owner)
   (let (CallPhaseV1 $status $t $s $r $soft $retry $publication) (call:phase $owner)
     (if (== $publication none)
       (let $_ (call:set-phase $owner (CallPhaseV1 $status $t $s $r $soft $retry ready)) True)
       False)))

; The ledger's call metadata, as native_llm_host.policy_metadata gave it.
(= (call:policy-metadata $owner)
   (let* (((CallPhaseV1 $status $t $s $r $last-soft $retry $publication) (call:phase $owner))
          ($sent (if (== $t ()) false true)) ($progress-soft (if (== $last-soft ()) 0 $last-soft))
          ($id (call:correlation $owner)) ($epoch (call:fact $owner "epoch" ""))
          ($provider (call:fact $owner "provider" "")) ($maximum (call:fact $owner "maximum" 0))
          ($fingerprint (call:fact $owner "fingerprint" "")) ($model (call:fact $owner "wire_model" ""))
          ($protocol (call:protocol $owner)) ($purpose (call:fact $owner "purpose" "turn"))
          ($activity (call:fact $owner "activity" "")) ($lane (call:fact $owner "lane" ""))
          ($guard (call:fact $owner "guard" JsonNull)) ($cap (call:fact $owner "cap" 0))
          ($soft-elapsed (call:soft-elapsed $owner (system:monotonic-ns)))
          ($origin (call:fact $owner "origin" (JsonObject ()))))
     (JsonObject
       ((JsonPair "id" $id) (JsonPair "epoch" $epoch) (JsonPair "provider" $provider)
        (JsonPair "maximum" $maximum) (JsonPair "fingerprint" $fingerprint) (JsonPair "model" $model)
        (JsonPair "protocol" $protocol) (JsonPair "purpose" $purpose) (JsonPair "activity" $activity)
        (JsonPair "lane" $lane) (JsonPair "guard" $guard) (JsonPair "cap" $cap) (JsonPair "sent" $sent)
        (JsonPair "soft_elapsed" $soft-elapsed) (JsonPair "progress_soft" $progress-soft)
        (JsonPair "retry_after" $retry) (JsonPair "origin" $origin)))))
; The request observation adds the Anthropic cache layout to the origin.
(= (call:request-metadata $owner)
   (let* (((JsonObject $pairs) (call:policy-metadata $owner))
          ($origin (call:request-origin $owner))
          ($with (call:with-origin $pairs $origin)))
     (JsonObject $with)))
(= (call:request-origin $owner)
   (let* (($layout (call:fact $owner "cache_layout" JsonNull))
          ((JsonObject $origin) (call:fact $owner "origin" (JsonObject ()))))
     (if (== $layout JsonNull) (JsonObject $origin)
       (let $extended (call:append $origin ((JsonPair "cache_layout" $layout))) (JsonObject $extended)))))
(= (call:with-origin $pairs $origin)
   (if (== $pairs ()) ()
     (let* (((JsonPair $key $value) (car-atom $pairs))
            ($pair (if (== $key "origin") (JsonPair "origin" $origin) (JsonPair $key $value)))
            ($rest (call:with-origin (cdr-atom $pairs) $origin)))
       (cons-atom $pair $rest))))
(= (call:append $front $back)
   (if (== $front ()) $back
     (let $rest (call:append (cdr-atom $front) $back) (cons-atom (car-atom $front) $rest))))
