; Rendering only: collapse consecutive identical, confirmed plain rests.
; The original ledger rows and identities remain unchanged and recoverable.
; Queries, continuations, unknown shapes and request changes break a run.
(= (task:idle-append $items $item)
   (if (== $items ()) ($item)
     (cons-atom (car-atom $items)
       (task:idle-append (cdr-atom $items) $item))))

(= (task:rest-result? $pair $seconds $intent)
   (let* (($suffix (if (== $intent "") ""
                      (str:join "" (" intent " $intent))))
          ($expected (str:join "" ("(RESTING budget-banked sleep-interval-seconds "
                                     $seconds $suffix ")"))))
     (== (state:json-field $pair "result") (JsonString $expected))))

(= (task:rest-shape? $pair)
   (if (== (state:json-field $pair "disposition")
           (JsonString "returned"))
     (case (state:json-field $pair "command")
       (((JsonArray ((JsonString "rest")))
          (== (state:json-field $pair "result")
              (JsonString "(RESTING budget-banked)")))
        ((JsonArray ((JsonString "rest") $seconds))
          (task:rest-result? $pair (state:text $seconds) ""))
        ((JsonArray ((JsonString "rest") $seconds $intent))
          (task:rest-result? $pair (state:text $seconds)
                                     (state:text $intent)))
        ($other False)))
     False))

; Extra or missing fields make a receipt ineligible. This keeps the display
; optimization from hiding any additional evidence.
(= (task:plain-rest? $pair)
   (case (state:json-field $pair "id")
     (((JsonString $id)
        (case (state:json-field $pair "request")
          (((JsonString $request)
             (and (not (== $id ""))
               (and (not (== $request ""))
                 (and (== (state:encoded $pair)
                   (state:encoded (JsonObject (
                     (JsonPair "id" (JsonString $id))
                     (JsonPair "request" (JsonString $request))
                     (JsonPair "command" (state:json-field $pair "command"))
                     (JsonPair "disposition" (state:json-field $pair "disposition"))
                     (JsonPair "result" (state:json-field $pair "result"))))))
                   (task:rest-shape? $pair)))))
           ($other False))))
      ($other False))))

(= (task:idle-same? $first $next)
   (and (task:plain-rest? $next)
     (and (== (state:json-field $first "request")
              (state:json-field $next "request"))
          (and (== (state:json-field $first "command")
                   (state:json-field $next "command"))
               (== (state:json-field $first "result")
                   (state:json-field $next "result"))))))

(= (task:idle-ids $pairs)
   (if (== $pairs ()) ()
     (cons-atom (state:json-field (car-atom $pairs) "id")
                (task:idle-ids (cdr-atom $pairs)))))

(= (task:idle-emit $run)
   (if (== (length $run) 1) (car-atom $run)
     (let $first (car-atom $run)
       (JsonObject (
         (JsonPair "compacted" (JsonString "successful-identical-rest-run"))
         (JsonPair "count" (task:number (length $run)))
         (JsonPair "id" (state:json-field $first "id"))
         (JsonPair "ids" (JsonArray (task:idle-ids $run)))
         (JsonPair "request" (state:json-field $first "request"))
         (JsonPair "command" (state:json-field $first "command"))
         (JsonPair "disposition" (JsonString "returned"))
         (JsonPair "result" (state:json-field $first "result"))
         (JsonPair "recovery"
           (JsonString "task-read each original id; ledger unchanged")))))))

(= (task:idle-run $first $run $remaining)
   (if (== $remaining ()) ((task:idle-emit $run))
     (if (task:idle-same? $first (car-atom $remaining))
       (task:idle-run $first
         (task:idle-append $run (car-atom $remaining))
         (cdr-atom $remaining))
       (cons-atom (task:idle-emit $run)
                  (task:idle-pairs $remaining)))))

(= (task:idle-pairs $pairs)
   (if (== $pairs ()) ()
     (let $first (car-atom $pairs)
       (if (task:plain-rest? $first)
         (task:idle-run $first ($first) (cdr-atom $pairs))
         (cons-atom $first (task:idle-pairs (cdr-atom $pairs)))))))
