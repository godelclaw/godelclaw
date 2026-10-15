; Receipt-driven cleanup of our own queued notices. Input ownership stays
; with cognition. Withdrawing a mistaken notice does not complete its input.
!(import! &self ./src/state_sqlite.metta)
!(import! &self cwp)

(= (nc:call $code $name $body)
   (cwp:call (system:getenv-or-default "METTACLAW_TELEGRAM_SERVICE_SOCKET" "")
             $code $name $body 5000))
(= (nc:ready?)
   (and (fs:exists (state:checkpoint))
     (case (state:query "SELECT 1 FROM sqlite_master WHERE type='table' AND name='input_notices'" ())
       (((sqlite:result $_ ((1)) $_ $_) True) ($other False)))))
(= (nc:eligible? $processed $state)
   (or (== $processed 1) (== $state "withdrawing")))
(= (nc:saved? $answer)
   (case $answer (((sqlite:result $_ $_ $_ $_) True) ($error False))))
(= (nc:withdraw $task)
   (if (nc:ready?)
     (nc:saved? (state:query "UPDATE input_notices SET state='withdrawing' WHERE task=? AND state IN ('registered','submitted')" ($task)))
     False))

; Persist each observation before acknowledging its service occurrence.
; A new delete identity is allowed only after definite non-delivery. The
; in-flight identity survives restarts and lost submission replies.
(= (nc:receipt $value)
   (if (not (nc:ready?)) False
     (case $value
       (((JsonArray ((JsonString "delivery") (JsonString $key) (JsonNumber "0") (JsonNumber "1") $outcome))
         (case (state:query "SELECT task,send_key,delete_key,message_id,processed,state FROM input_notices WHERE send_key=? OR delete_key=?" ($key $key))
           (((sqlite:result $_ (($task $send $delete $message $processed $state)) $_ $_)
             (nc:outcome $key $outcome $task $send $delete $message $processed $state))
            ($other False))))
        ($other False)))))
(= (nc:outcome $key $outcome $task $send $delete $message $processed $state)
   (case $outcome
     (((JsonArray ((JsonString "delivered") (JsonNumber $raw)))
       (let $id (parse $raw)
         (if (== $key $send)
           (if (and (> $id 0) (or (== $message sqlite:null) (== $message $id)))
             (nc:saved? (state:query "UPDATE input_notices SET message_id=? WHERE task=?" ($id $task))) False)
           (if (and (nc:eligible? $processed $state) (or (== $id 0) (== $id $message)))
             (nc:saved? (state:query "UPDATE input_notices SET state='deleted' WHERE task=? AND delete_key=?" ($task $key))) False))))
      ((JsonArray ((JsonString "not-sent")))
       (if (== $key $delete)
         (nc:saved? (state:query "UPDATE input_notices SET delete_key=NULL WHERE task=? AND delete_key=?" ($task $key))) True))
      ($other True))))

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
          ($candidate (str:join "" ($lane "." $padding $number)))
          ($_ (state:query "UPDATE input_notices SET delete_key=COALESCE(delete_key,?) WHERE task=? AND state IN ('registered','submitted','withdrawing')" ($candidate $task))))
     (case (state:query "SELECT delete_key FROM input_notices WHERE task=?" ($task))
       (((sqlite:result $_ (($key)) $_ $_) $key) ($other sqlite:null)))))
(= (nc:delete $row)
   (let ($task $lane $message) $row
     (let $key (nc:delete-key $task $lane)
       (if (== $key sqlite:null) 0
         (let* (($raw (repr $message))
                ($body (json-stringify (JsonArray ((JsonArray ((JsonString "delete") (JsonNumber $raw))))))))
           (case (nc:call 4 $key $body)
             (((cwp:reply 66 $_ $_) 1) ($other 0))))))))
(= (nc:delete-rows $rows)
   (if (== $rows ()) 0
     (let $done (nc:delete (car-atom $rows))
       (+ $done (nc:delete-rows (cdr-atom $rows))))))
(= (nc:pump)
   (if (not (nc:ready?)) 0
     (let $_ (nc:collect "")
       (case (state:query "SELECT task,lane,message_id FROM input_notices WHERE state IN ('registered','submitted','withdrawing') AND message_id IS NOT NULL AND (processed=1 OR state='withdrawing') ORDER BY created" ())
         (((sqlite:result $_ $rows $_ $_) (nc:delete-rows $rows))
          ($other 0))))))
