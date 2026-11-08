; Receipt-driven cleanup of our own queued notices. Input ownership stays
; with cognition. Withdrawing a mistaken notice does not complete its input.
!(import! &self ./src/state_ledger.metta)
!(import! &self cwp)
!(import! &self system)

; With the native ledger the notices live in its store and each statement below is one of
; its notice-* verbs (src/input_ledger.metta), with the same rules; a nonce per call makes a
; lost reply repeatable and a later call new.
(= (nc:native?) (state:native-ledger))
(= (nc:native $verb $arguments) (state:ledger $verb $arguments))
; True from the ledger may arrive as the wire's boolean: truthy, not == to True.
(= (nc:native-flag $answer)
   (case (get-type $answer) ((Bool (if $answer True False)) ($other False))))

(= (nc:call $code $name $body)
   (cwp:call (system:getenv-or-default "METTACLAW_TELEGRAM_SERVICE_SOCKET" "")
             $code $name $body 5000))
(= (nc:ready?)
   True)
(= (nc:withdraw $task)
   (nc:native-flag (nc:native "notice-withdraw" ($task (system:uuid4-hex)))))

; Persist each observation before acknowledging its service occurrence.
; A new delete identity is allowed only after definite non-delivery. The
; in-flight identity survives restarts and lost submission replies.
(= (nc:receipt $value)
   (if (not (nc:ready?)) False
     (case $value
       (((JsonArray ((JsonString "delivery") (JsonString $key) (JsonNumber "0") (JsonNumber "1") $outcome))
         (nc:native-flag (nc:native "notice-delivery" ($key (nc:outcome-atom $outcome) (system:uuid4-hex)))))
        ($other False)))))

; A delivery outcome as the native verb takes it.
(= (nc:outcome-atom $outcome)
   (case $outcome
     (((JsonArray ((JsonString "delivered") (JsonNumber $raw))) (delivered (parse $raw)))
      ((JsonArray ((JsonString "not-sent"))) not-sent)
      ($other other))))

(= (nc:collect $after)
   (case (nc:call 1 $after "")
     (((cwp:reply 65 $task $body)
       (let $_ (if (nc:receipt (json-parse $body)) (nc:call 2 $task "[]") ())
         (nc:collect $task)))
      ($other ()))))

; The lane and target come only from the saved send and its delivery
; receipt. A competing cleanup process adopts the already-persisted key.
(= (nc:delete-key $task $lane)
   (let* (($number (repr (system:monotonic-ns)))
          ($padding (str:byte-slice "00000000000000000000" 0 (- 20 (str:byte-length $number))))
          ($candidate (str:join "" ($lane "." $padding $number))))
     (case (nc:native "notice-cleanup-key" ($task $candidate (system:uuid4-hex)))
         ((none none) ($key (if (== (get-type $key) String) $key none))))))
; Telegram lets a bot delete its own message for 48 hours. A notice older than that is
; expired once, by the ledger's check verb, as input_ack.pump expires it, and so leaves the
; deletable rows: a delete Telegram refused is not asked again every cycle. A delete key the
; notice already holds is used as it is; only a notice without one asks the ledger for one.
(= (nc:max-age) 172800)
(= (nc:delete $row $now)
   (let ($task $lane $message $stored $created) $row
     (if (> (- $now $created) (nc:max-age))
       (let $expired (nc:native "check" ($task $now (nc:max-age))) 0)
       (let $key (if (== (get-type $stored) String) $stored (nc:delete-key $task $lane))
         (if (== $key none) 0
           (let* (($raw (repr $message))
                  ($body (json-stringify (JsonArray ((JsonArray ((JsonString "delete") (JsonNumber $raw))))))))
             (case (nc:call 4 $key $body)
               (((cwp:reply 66 $_ $_) 1) ($other 0)))))))))
(= (nc:delete-rows $rows $now)
   (if (== $rows ()) 0
     (let $done (nc:delete (car-atom $rows) $now)
       (+ $done (nc:delete-rows (cdr-atom $rows) $now)))))
; The notices whose message may be deleted, oldest first: (task lane message-id delete-key
; created) rows.
(= (nc:deletable)
   (case (nc:native "notice-deletable" ())
       (((Error $where $why) ()) ($rows $rows))))
(= (nc:pump)
   (if (not (nc:ready?)) 0
     (let $_ (nc:collect "")
       (nc:delete-rows (nc:deletable) (system:wall-time)))))
