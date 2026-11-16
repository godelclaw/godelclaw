; Runtime events cross from Python through a durable spool, never a queue in
; Python's memory.
;
; - Configuration: the spool is runtime-events/ beside the Telegram health
;   record, fixed by the environment the process started with.
; - Producers (channels/telegram.py, for background jobs, retained model calls,
;   finished attachments and wake requests) write each event as one whole
;   JSON file, renamed into place and synced: <order>-<digest>.json, where
;   the order sorts as the events arrived and the digest names the event's
;   identity.
; - The consumer takes the files oldest first. A file is removed once its
;   item is taken: the removal is the durable receipt, so an event is handed
;   over once. A file left by a crash is taken again by the next process,
;   whose pending input began empty. A file whose removal fails is kept and
;   remembered, so this process never takes it twice.
; - Retained handover inputs (modes/channel-handover/<update>.json) are a
;   spool of their own, acknowledged when their turn commits; each is offered
;   once per process.
!(import! &self (library fs))
!(import! &self (library json))
!(import! &self (library str))
!(import! &self (library path))
!(import! &self (library system))
!(import! &self (library re))
!(import! &self ./effect_receipt_values_cetta.metta)

(= (rtev:spool $health-path) (path:join (path:dirname $health-path) "runtime-events"))

; The names a spool holds, oldest first: whole files only, never a sibling
; still being written.
(= (rtev:names $folder)
   (if (not (fs:exists $folder)) ()
     (sort-strings (rtev:json-names (fs:entries $folder)))))
(= (rtev:json-names $entries)
   (if (== $entries ()) ()
     (let* (($name (car-atom $entries)) ($rest (rtev:json-names (cdr-atom $entries))))
       (if (and (str:ends-with $name ".json") (not (str:starts-with $name ".")))
         (cons-atom $name $rest) $rest))))

; A spooled item: its JSON object, or RuntimeEventUnreadable.
(= (rtev:item $path)
   (case (fs:read-text-result $path 0 "raw")
     ((("ok" $text)
       (case (json-parse $text)
         (((JsonObject $pairs) (JsonObject $pairs))
          ($other RuntimeEventUnreadable))))
      ($missing RuntimeEventUnreadable))))

; Take every spooled event, oldest first, handing each to the handler (a
; function of one item) before its file is removed. Answers how many were
; handed over. An unreadable file is removed unhandled, and reported.
(: rtev:take (-> %Undefined% Atom %Undefined%))
(= (rtev:take $folder $handler)
   (rtev:take-names $folder (rtev:names $folder) $handler 0))
(: rtev:take-names (-> %Undefined% %Undefined% Atom %Undefined% %Undefined%))
(= (rtev:take-names $folder $names $handler $count)
   (if (== $names ()) $count
     (let* (($name (car-atom $names)) ($rest (cdr-atom $names)))
       (if (rtev:kept? $name) (rtev:take-names $folder $rest $handler $count)
         (let* (($path (path:join $folder $name))
                ($item (rtev:item $path))
                ($handled (if (== $item RuntimeEventUnreadable)
                            (system:write stderr (str:concat "[runtime-events] unreadable event removed: " (str:concat $name "\n")))
                            ($handler $item)))
                ($removed (rtev:remove $path $name)))
           (rtev:take-names $folder $rest $handler (if (== $item RuntimeEventUnreadable) $count (+ $count 1))))))))
(= (rtev:kept? $name) (not (== (collapse (match &self (RuntimeEventKeptV1 $name) True)) ())))
(= (rtev:remove $path $name)
   (case (fs:remove $path)
     (((fs:failed $reason $operation $file $code $progress $message)
       (let $kept (add-atom &self (RuntimeEventKeptV1 $name)) False))
      ($removed True))))

; Retained handover inputs not yet offered by this process, oldest first, as
; (update-id update) pairs; each is marked offered.
(= (rtev:handover $mode-root)
   (if (== $mode-root "") ()
     (rtev:handover-names (path:join $mode-root "channel-handover")
                          (rtev:names (path:join $mode-root "channel-handover")))))
(= (rtev:handover-names $folder $names)
   (if (== $names ()) ()
     (let* (($name (car-atom $names))
            ($id (str:char-slice $name 0 (- (str:char-length $name) 5)))
            ($rest (rtev:handover-names $folder (cdr-atom $names))))
       (if (rtev:offered? $id) $rest
         (case (rtev:item (path:join $folder $name))
           ((RuntimeEventUnreadable $rest)
            ($update (let $offered (add-atom &self (RuntimeHandoverOfferedV1 $id))
                       (cons-atom ($id $update) $rest)))))))))
(= (rtev:offered? $id) (not (== (collapse (match &self (RuntimeHandoverOfferedV1 $id) True)) ())))

; Native producers use the same durable spool and occurrence identities.
!(import! &self ./state_file_cetta.metta)
!(import! &self ./input_scope_cetta.metta)
!(bind! &runtime-event-order (new-state 0))
(= (rtev:spooled? $folder $suffix $names)
   (if (== $names ()) False
     (if (str:ends-with (car-atom $names) $suffix) True
       (rtev:spooled? $folder $suffix (cdr-atom $names)))))
(= (rtev:enqueue $health-path $identity $text $reason)
   (if (not (re:is-match (re:compile "^runtime:[A-Za-z0-9_.:-]{1,160}$") $identity))
     (Error rtev:enqueue invalid-runtime-occurrence)
     (case (input-scope:settled $identity)
       ((True True)
        (False (rtev:enqueue-new $health-path $identity $text $reason))
        ($unavailable (Error rtev:enqueue settlement-unavailable))))))
(= (rtev:enqueue-new $health-path $identity $text $reason)
   (let* (($folder (rtev:spool $health-path)) ($_ (fs:make-directories $folder 511))
          ($suffix (str:join "" ("-" (str:char-slice (str:sha256 $identity) 0 24) ".json"))))
     (if (or (erv:contains (input-scope:ids) $identity) (rtev:spooled? $folder $suffix (rtev:names $folder))) True
       (let* (($clock (max (system:wall-time-ns) (+ (get-state &runtime-event-order) 1)))
              ($_ (change-state! &runtime-event-order $clock))
              ($order (repr $clock))
              ($padded (str:concat (str:byte-slice "00000000000000000000" 0 (max 0 (- 20 (str:char-length $order)))) $order))
              ($path (path:join $folder (str:concat $padded $suffix)))
              ($item (JsonObject
                 ((JsonPair "chat" "runtime") (JsonPair "text" (str:concat "[runtime] " $text))
                  (JsonPair "from_bot" False) (JsonPair "tier" "light")
                  (JsonPair "update_id" $identity) (JsonPair "wake" $reason)))))
         (sf:replace $path (json:stringify-python-with $item ((ensure-ascii False))) 432 True)))))
