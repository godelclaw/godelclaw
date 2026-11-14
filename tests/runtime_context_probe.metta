!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)
!(import! &self ./tests/scripted_provider.metta)
(= (assertEqual $a $b) (assert (== $a $b)))

;; Exercise the real protected preparation/capture boundary: the runtime facts
;; each prompt carries, a channel that stops reporting, and their omission.
!(scripted:default "((help vitals))")
!(py-eval "(__import__('helper').__dict__.update(_working_boot_cache={}, working_boot=lambda: 3), __import__('strategy_host').__dict__.__setitem__('guard', lambda **kwargs: 'runtime-view-control'))")
;; The control responder's status: ready with one operator message 40 s old,
;; or failed.
!(py-eval "__import__('helper').__dict__.__setitem__('runtime_status', lambda ready: (lambda now: (__import__('mode_channel').store(__import__('mode_channel').state() / 'control-status.json', {'observed_at': now, 'channel_ready': bool(ready), 'operator_inbox': {'pending': 1, 'newestMessageAt': now-40, 'oldestMessageAt': now-40, 'observedAt': now}}), 1)[-1])(__import__('time').time()))")
!(py-eval "__import__('helper').__dict__.__setitem__('runtime_ledger_ok', lambda: int(len(__import__('strategy_host').ledger().projection()['actions']) == 3 and bool(__import__('strategy_host').ledger().active())))")
!(py-eval "(__import__('cognitive_health').turn_started('previous-probe'), __import__('cognitive_health').turn_completed(10))")
!(py-call (helper.runtime_status True))
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
;; Each prompt with facts shows the call returned and the turn pending.
(= (rc:returned $facts)
   (and (== (json:get (json:get $facts "call") "phase") "returned") (== (json:get $facts "turnPending") true)))

!(let* (($initial (initLoop))
         ($first (process-step $initial (turnProcess (process-periphery $initial))))
         ($facts1 (rc:facts 1))
         ($_ (assertEqual (json:get $facts1 "localChannelReady") true))
         ($_ (assertEqual (rc:returned $facts1) True))
         ($_ (assertEqual (json:get (json:get $facts1 "humanInbox") "pending") 1))
         ($_ (assertEqual (>= (json:get (json:get $facts1 "humanInbox") "newestAgeSeconds") 40) True))
         ($_ (py-call (helper.runtime_status False)))
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
         ($_ (assertEqual (py-call (helper.runtime_ledger_ok)) 1)))
   (println! RUNTIME_CONTEXT_OK))
