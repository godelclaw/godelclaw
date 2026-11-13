; The command responder's own I/O between its answers: the mode outbox it
; submits and writes to, the readiness it publishes, and its wait for the next
; cycle.
;
; - Queueing a send: a fresh key from the outbox's sequence file, kept under
;   its lock file, strictly above every key handed out before and never below
;   the wall clock's nanoseconds (as Python's durable_telegram.Channel.key
;   gives them, so both may write the same outbox), and the intent written
;   whole as KEY.json.
; - The mode outbox (<mode state>/channel-outbox) holds sends that other
;   processes queued for the channel service: each a whole JSON file
;   {"key": KEY, "commands": [...]}, named KEY.json. A cycle submits at most
;   one, the first by name, under its own key, so a lost reply repeats the
;   same key with the same bytes. Stored removes the file. A refusal is a
;   terminal receipt, KEY.refused beside it with the service's code, never a
;   reason to try another recipient or a new key. An unavailable or full
;   service leaves the file for a later cycle. A file that is not an intent
;   (unreadable, no key, no commands) is set aside as KEY.unreadable, so the
;   intents after it still go. A lock file keeps two pumps from submitting
;   the same intent at once; a pump that finds it held submits nothing.
; - The readiness record (<mode state>/control-status.json), at most every
;   five seconds: this process, when, whether the service answered a NEXT,
;   and, while it did, the operator inbox view of the last complete scan.
; - The wait: until the service publishes work after the token taken before
;   the cycle read anything, the outbox changes, or two seconds pass. Work
;   that arrives while a cycle runs ends the next wait at once. When the
;   service cannot wait (unreachable, or it refused the token), the cycle
;   still waits out the two seconds.
;
; The mode state root is METTACLAW_MODE_STATE_ROOT, else "modes" beside
; METTACLAW_ENGINE_STATE_PATH; with neither, there is no outbox and no record.
!(import! &self (library cwp))
!(import! &self (library fs))
!(import! &self (library io))
!(import! &self (library json))
!(import! &self (library path))
!(import! &self (library str))
!(import! &self (library system))
!(import! &self ./src/state_file_cetta.metta)
!(import! &self ./src/utils_cetta.metta)

!(bind! &tio-state (new-space))

(= (tio:socket) (system:getenv-or-default "METTACLAW_TELEGRAM_SERVICE_SOCKET" ""))
(= (tio:mode-root)
   (let $configured (system:getenv-or-default "METTACLAW_MODE_STATE_ROOT" "")
     (if (not (== $configured "")) $configured
       (let $engine (system:getenv-or-default "METTACLAW_ENGINE_STATE_PATH" "")
         (if (== $engine "") "" (path:join (path:dirname $engine) "modes"))))))
(= (tio:outbox)
   (let $root (tio:mode-root) (if (== $root "") "" (path:join $root "channel-outbox"))))

; One remembered value of a kind, or the default.
(= (tio:get $kind $default)
   (case (collapse (match &tio-state (TioState $kind $value) $value))
     ((() $default) ($values (car-atom $values)))))
(= (tio:set $kind $value)
   (let $_ (collapse (match &tio-state (TioState $kind $old) (remove-atom &tio-state (TioState $kind $old))))
     (add-atom &tio-state (TioState $kind $value))))

;; ------------------------------------------------------------ the outbox

; The intents, oldest name first: whole files only, never a sibling still
; being written.
(= (tio:intents $folder)
   (if (not (fs:exists $folder)) ()
     (sort-strings (tio:json-names (fs:entries $folder)))))
(= (tio:json-names $entries)
   (if (== $entries ()) ()
     (let* (($name (car-atom $entries)) ($rest (tio:json-names (cdr-atom $entries))))
       (if (and (str:ends-with $name ".json") (not (str:starts-with $name ".")))
         (cons-atom $name $rest) $rest))))
(= (tio:stem $name) (str:byte-slice $name 0 (- (str:byte-length $name) 5)))

; An intent file: (TioIntent KEY COMMANDS ITEM), TioMissing when it is gone,
; or TioUnreadable.
(= (tio:intent $path)
   (case (fs:read-text-result $path 0 "raw")
     ((("ok" $text)
       (case (catch (json:parse $text))
         (((JsonObject $pairs)
           (let* (($item (JsonObject $pairs))
                  ($key (sf:get $item "key" JsonNull))
                  ($commands (sf:get $item "commands" JsonNull)))
             (if (and (== (get-type $key) String) (and (not (== $key "")) (not (== $commands JsonNull))))
               (TioIntent $key $commands $item)
               TioUnreadable)))
          ($other TioUnreadable))))
      ($failed (if (fs:exists $path) TioUnreadable TioMissing)))))

; A receipt beside the intent, written whole, as Python's json.dump writes it.
(= (tio:receipt $folder $stem $suffix $item)
   (sf:replace (path:join $folder (str:concat $stem $suffix))
               (json:stringify-python-with $item ((ensure-ascii False))) 384 True))

; Submit the first intent; 1 when the service stored it, else 0.
(= (tio:submit-first $socket $folder $name)
   (let $path (path:join $folder $name)
     (case (tio:intent $path)
       ((TioMissing 0)
        (TioUnreadable
         (let* (($renamed (fs:rename $path (path:join $folder (str:concat (tio:stem $name) ".unreadable"))))
                ($_ (system:write stderr (str:concat "[control-outbox] not an intent, set aside: " (str:concat $name "\n")))))
           0))
        ((TioIntent $key $commands $item)
         (let $body (json:stringify-python-with $commands ((ensure-ascii False) (separators "," ":")))
           (case (cwp:call $socket 4 $key $body 200)
             (((cwp:reply 66 $stored $answer) (let $_ (fs:remove $path) 1))
              ((cwp:reply 71 $unavailable $answer) 0)
              ((cwp:reply 72 $limit $answer) 0)
              ((cwp:reply $code $refused $answer)
               (let* (($_ (tio:receipt $folder (tio:stem $name) ".refused" (sf:put $item "code" $code)))
                      ($_ (fs:remove $path)))
                 0))
              ($unreachable 0)))))))))

; One bounded submission a cycle, independent of cognition.
(= (tio:pump)
   (let $socket (tio:socket)
     (if (== $socket "") 0
       (let $folder (tio:outbox)
         (if (== $folder "") 0
           (let $names (tio:intents $folder)
             (if (== $names ()) 0 (tio:pump-locked $socket $folder (car-atom $names)))))))))
(= (tio:pump-locked $socket $folder $name)
   (let $lock (fs:open-write (path:join $folder "pump.lock") ("create" "append") 438)
     (if (sf:failed? $lock) 0
       (let $held (fs:lock $lock exclusive-nowait)
         (let* (($done (if (== $held fs:locked) (tio:submit-first $socket $folder $name) 0))
                ($_ (if (== $held fs:locked) (fs:unlock $lock) ()))
                ($_ (fs:close $lock)))
           $done)))))

;; ------------------------------------------------------------ queueing a send

; The operator's chat: the first of METTACLAW_TELEGRAM_PRIMARY_CHAT_ID,
; METTACLAW_TELEGRAM_CHAT_ID or TELEGRAM_CHAT_ID that is set, its first entry.
(= (tio:primary-chat)
   (let* (($primary (system:getenv-or-default "METTACLAW_TELEGRAM_PRIMARY_CHAT_ID" ""))
          ($chat (system:getenv-or-default "METTACLAW_TELEGRAM_CHAT_ID" ""))
          ($legacy (system:getenv-or-default "TELEGRAM_CHAT_ID" ""))
          ($configured (if (not (== $primary "")) $primary (if (not (== $chat "")) $chat $legacy))))
     (str:trim (car-atom (str:split "," $configured)))))
; A chat id as a whole number, or None: an optional sign and digits.
(= (tio:chat-number $text)
   (let $digits (if (or (str:starts-with $text "-") (str:starts-with $text "+")) (str:byte-slice $text 1 (str:byte-length $text)) $text)
     (if (and (not (== $digits "")) (str:is-all-in digit $digits)) (str:string->number $text) None)))

; "%020d": the sequence zero-padded to 20 digits.
(= (tio:pad20 $n) (tio:zeros (repr $n) 20))
(= (tio:zeros $digits $width)
   (if (< (str:byte-length $digits) $width) (tio:zeros (str:concat "0" $digits) $width) $digits))

; The outbox's sequence, as its file holds it: 0 when it is missing or holds
; anything but a whole number.
(= (tio:stored-sequence $path)
   (case (fs:read-text-result $path 0 "raw")
     ((("ok" $text)
       (let $trimmed (str:trim $text)
         (if (and (not (== $trimmed "")) (str:is-all-in digit $trimmed)) (str:string->number $trimmed) 0)))
      ($missing 0))))
; The next sequence: above the stored one and never below the wall clock's
; nanoseconds, kept under the sequence's lock file, so every process that
; queues sends here (Python's durable_telegram.Channel.key among them) gets a
; strictly larger one, across restarts too. The number, or the failure.
(= (tio:next-sequence $folder)
   (let $path (path:join $folder "sequence")
     (sf:locked (str:concat $path ".lock") 384
       (let* (($stored (tio:stored-sequence $path))
              ($now (system:wall-time-ns))
              ($next (if (> $now $stored) $now (+ $stored 1)))
              ($saved (sf:replace $path (str:concat (repr $next) "\n") 384 True)))
         (if (== $saved True) $next $saved)))))

; A fresh key CHAT.THREAD.SEQUENCE for a send to a chat's thread 0, or
; (Error tio:key WHY) when there is no outbox or the chat is no chat id.
(= (tio:operator-key) (tio:key (tio:primary-chat)))
(= (tio:key $chat-text)
   (let* (($folder (tio:outbox)) ($chat (tio:chat-number $chat-text)))
     (if (== $folder "") (Error tio:key "no mode state root")
       (if (or (== $chat None) (== $chat 0)) (Error tio:key "no chat id")
         (let* (($_ (fs:make-directories $folder 511))
                ($sequence (tio:next-sequence $folder)))
           (if (== (get-type $sequence) Number)
             (str:join "" ((repr $chat) ".0." (tio:pad20 $sequence)))
             (Error tio:key $sequence)))))))

; Write an intent, {"key": KEY, "commands": [...]}, whole into the outbox as
; KEY.json, synced, where the pump submits it. True, or the failure.
(= (tio:store-intent $key $intent)
   (let $folder (tio:outbox)
     (if (== $folder "") (Error tio:store-intent "no mode state root")
       (let $_ (fs:make-directories $folder 511)
         (sf:replace (path:join $folder (str:concat $key ".json"))
                     (json:stringify-python-with $intent ((ensure-ascii False))) 384 True)))))

;; ------------------------------------------------------------ the readiness record

(= (tio:health-interval-ns) 5000000000)
(= (tio:health-due? $now)
   (case (tio:get health-at none)
     ((none True) ($at (>= (- $now $at) (tio:health-interval-ns))))))

; Whether the service answers a NEXT now: idle or a task.
(= (tio:service-ready? $socket)
   (case (cwp:call $socket 1 "" "" 200)
     (((cwp:reply 64 $name $body) True)
      ((cwp:reply 65 $name $body) True)
      ($other False))))

(= (tio:status-record $ready $view)
   (JsonObject ((JsonPair "pid" (JsonNumber (repr (system:pid))))
                (JsonPair "observed_at" (JsonNumber (repr (system:wall-time))))
                (JsonPair "channel_ready" (JsonBool (if $ready true false)))
                (JsonPair "operator_inbox" (if $ready $view JsonNull)))))

; $view is the inbox view to publish: a JSON object, or JsonNull.
(= (tio:heartbeat $view)
   (let $socket (tio:socket)
     (if (== $socket "") 0
       (let $now (system:monotonic-ns)
         (if (not (tio:health-due? $now)) 1
           (let $root (tio:mode-root)
             (if (== $root "") 0
               (let* (($ready (tio:service-ready? $socket))
                      ($_ (fs:make-directories $root 511))
                      ($_ (sf:replace (path:join $root "control-status.json")
                                      (json-stringify (tio:status-record $ready $view)) 384 True))
                      ($_ (tio:set health-at (system:monotonic-ns))))
                 1))))))))

;; ------------------------------------------------------------ the wait

(= (tio:wait-ms) 2000)

; The service's revision token, asked for when none is held: an empty token
; answers at once with the current one, so a service that does not answer
; within 200 ms (as for NEXT and SUBMIT) gives none and costs the cycle no more.
(= (tio:token $socket)
   (if (== $socket "") ""
     (let $held (tio:get token "")
       (if (not (== $held "")) $held
         (case (cwp:call $socket 5 "" "" 200)
           (((cwp:reply 64 $token $body) (let $_ (tio:set token $token) $token))
            ((cwp:reply 67 $token $body) (let $_ (tio:set token $token) $token))
            ($other "")))))))

(= (tio:submit $request)
   (case (catch (io:submit $request))
     (((io:pending $id) $id)
      ($failed none))))

; Arm before the cycle reads anything.
(= (tio:arm)
   (let* (($socket (tio:socket))
          ($token (tio:token $socket))
          ($folder (tio:outbox))
          ($made (if (== $folder "") () (fs:make-directories $folder 511)))
          ($watch (if (== $folder "") none (tio:submit (fs:changed $folder)))))
     (TioArmed $socket $token $watch)))

(= (tio:handles $candidates)
   (if (== $candidates ()) ()
     (let* (($head (car-atom $candidates)) ($rest (tio:handles (cdr-atom $candidates))))
       (if (== $head none) $rest (cons-atom $head $rest)))))

; Whether the service reported new work; a refused or failed wait forgets
; the token, so the next cycle asks for a current one.
(= (tio:finish-service $handle)
   (if (== $handle none) False
     (case (io:poll $handle)
       (((io:event $id (cwp:reply 67 $token $body)) (let $_ (tio:set token $token) True))
        ((io:event $id (cwp:reply 64 $token $body)) (let $_ (tio:set token $token) False))
        ((io:event $id $failed) (let $_ (tio:set token "") False))
        ((io:idle) (let $_ (catch (io:cancel $handle)) False))
        ($unknown False)))))
(= (tio:finish-watch $handle)
   (if (== $handle none) False
     (case (io:poll $handle)
       (((io:event $id $changed) True)
        ((io:idle) (let $_ (catch (io:cancel $handle)) False))
        ($unknown False)))))

(= (tio:wait (TioArmed $socket $token $watch))
   (let* (($started (system:monotonic-ns))
          ($service (if (or (== $socket "") (== $token "")) none
                      (tio:submit (cwp:request $socket 5 $token (repr (tio:wait-ms)) (+ (tio:wait-ms) 1000)))))
          ($handles (tio:handles (cons-atom $watch (cons-atom $service ()))))
          ($_ (if (== $handles ()) () (io:wait-ready (cons-atom io:ids $handles) (tio:wait-ms))))
          ($woken (tio:finish-service $service))
          ($changed (tio:finish-watch $watch))
          ($left (- (tio:wait-ms) (/ (- (system:monotonic-ns) $started) 1000000))))
     (if (or $woken $changed) woken
       (if (> $left 0) (let $_ (sleep (/ $left 1000.0)) waited) waited))))
