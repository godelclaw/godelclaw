!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)
!(import! &self ./tests/scripted_provider.metta)
(= (assertEqual $a $b) (assert (== $a $b)))
; The control responder's status file, written as the responder writes it:
; ready with one operator message 40 s old, or failed.
(= (rc:status $ready)
   (let* (($now (system:wall-time)) ($old (- $now 40))
          ($dir (case (rt:mode-state) (((Val $d) $d) ($other (Error rc:status no-mode-state)))))
          ($_ (fs:make-directories $dir 448)))
     (fs:write (path:join $dir "control-status.json")
       (str:join "" ("{\"observed_at\": " (repr $now) ", \"channel_ready\": " (if $ready "true" "false")
                     ", \"operator_inbox\": {\"pending\": 1, \"newestMessageAt\": " (repr $old)
                     ", \"oldestMessageAt\": " (repr $old) ", \"observedAt\": " (repr $now) "}}")))))

;; Exercise the real protected preparation/capture boundary: the runtime facts
;; each prompt carries, a channel that stops reporting, and their omission.
!(scripted:default "((help vitals))")
;; The control responder's status: ready with one operator message 40 s old,
;; or failed.
!(rc:status True)
!(initMemory)
!(configure commchannel telegram)

;; The RUNTIME_STATE lines of request $n's messages.
(= (rc:lines $n)
   (let (JsonArray $messages)
        (json:get (json:parse (fs:read-text (scripted:path (format-args "request-{}.body" ($n))))) "messages")
     (rc:message-lines $messages)))
(= (rc:message-lines $messages)
   (if (== $messages ()) ()
     (let $content (json:get (car-atom $messages) "content")
       (rc:append (rc:state-lines (str:split "\n" $content)) (rc:message-lines (cdr-atom $messages))))))
(= (rc:state-lines $lines)
   (if (== $lines ()) ()
     (let $rest (rc:state-lines (cdr-atom $lines))
       (if (str:starts-with (car-atom $lines) "RUNTIME_STATE ") (cons-atom (car-atom $lines) $rest) $rest))))
(= (rc:append $front $back) (if (== $front ()) $back (cons-atom (car-atom $front) (rc:append (cdr-atom $front) $back))))
;; Request $n's facts: its one RUNTIME_STATE line, read as JSON.
(= (rc:facts $n)
   (let ($line) (rc:lines $n)
     (json:parse (str:byte-slice $line 14 (str:byte-length $line)))))
;; After a call, the prompt's facts show it returned and the turn pending.
(= (rc:returned $facts)
   (and (== (json:get (json:get $facts "call") "phase") "returned") (== (json:get $facts "turnPending") true)))

!(let* (($initial (initLoop))
         ($first (process-step $initial (turnProcess (process-periphery $initial))))
         ($facts1 (rc:facts 1))
         ($_ (assertEqual (json:get $facts1 "localChannelReady") true))
         ;; No provider call precedes the first prompt.
         ($_ (assertEqual (json:get (json:get $facts1 "call") "phase") "unknown"))
         ($_ (assertEqual (json:get (json:get $facts1 "humanInbox") "pending") 1))
         ($_ (assertEqual (>= (json:get (json:get $facts1 "humanInbox") "newestAgeSeconds") 40) True))
         ($_ (rc:status False))
         ($second (process-step $first (turnProcess (process-periphery $first))))
         ;; A failed channel gives no inbox assurance.
         ($facts2 (rc:facts 2))
         ($_ (assertEqual (json:get $facts2 "localChannelReady") false))
         ($_ (assertEqual (rc:returned $facts2) True))
         ($_ (assertEqual (json:get $facts2 "humanInbox") JsonNull))
         ;; Omitted, the facts are absent.
         ($_ (collapse (match &self (= (runtime:projection) $body)
                  (remove-atom &self (= (runtime:projection) $body)))))
         ($_ (add-atom &self (= (runtime:projection) "")))
         ($third (process-step $second (turnProcess (process-periphery $second))))
         ($_ (assertEqual (rc:lines 3) ()))
         ($_ (assertEqual (scripted:calls) 3))
         ;; Observations do not complete retained work.
         ($_ (assertEqual (size-atom (state:ledger "actions" ("all"))) 3))
         ($_ (assertEqual (> (size-atom (state:ledger "request-rows" (active))) 0) True)))
   (println! RUNTIME_CONTEXT_OK))
