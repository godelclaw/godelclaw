; Unit tests of channels/telegram_failure_notice.metta and the outbox writer it
; uses (channels/telegram_control_io.metta), against the native ledger's own
; server; no Python. The cases are those of the Python they replace
; (cognitive_health.notify_failure, durable_telegram.Channel.key and
; mode_channel.store): one notice per failure episode whatever is repeated or
; restarted, the stored intent published after a failed enqueue, nothing held
; or published while the ledger is down, and a new notice after a recovery.
; Run twice: with a ledger socket, and without one (no notice at all).
!(import! &self ../native_test_support.metta)
!(import! &self ../../channels/telegram_failure_notice.metta)

(= (fnu:home) (nt:required-env "HOME"))
(= (fnu:socket) (system:getenv-or-default "METTACLAW_INPUT_LEDGER_SOCKET" ""))
(= (fnu:store) (path:join (fnu:home) "store"))
(= (fnu:outbox) (tio:outbox))
(= (fnu:passed $block) (let $_ (add-atom &self (fnu:block-passed $block)) (println! $block)))

; Model turns failing as the provider transport records them: the call starts
; at NOW and fails a second later with a rate limit.
(= (fnu:fail $now)
   (let $started (health:write (health:started (health:read) "fixture" "chosen-model" $now))
     (health:write (health:failed (health:read) "provider-rejected-429" (+ $now 1) 0 (system:uuid4-hex)))))
(= (fnu:recover $now)
   (let $started (health:write (health:started (health:read) "fixture" "chosen-model" $now))
     (health:write (health:completed (health:read) 10 (+ $now 1)))))
(= (fnu:episode) (sf:get (sf:get (health:read) "failure_episode" (JsonObject ())) "id" ""))
(= (fnu:intents)
   (if (fs:exists (fnu:outbox)) (filter-atom (fs:entries (fnu:outbox)) $name (str:ends-with $name ".json")) ()))
(= (fnu:intent-text $name) (fs:read-text (path:join (fnu:outbox) $name)))
(= (fnu:notice-text $name)
   (case (json:parse (fnu:intent-text $name))
     (((JsonObject $pairs)
       (case (sf:get (JsonObject $pairs) "commands" JsonNull)
         (((JsonArray ((JsonArray ("send" $text "plain")))) $text) ($other ""))))
      ($other ""))))
(= (fnu:clear-outbox)
   (let $names (fnu:intents) (map-atom $names $name (fs:remove (path:join (fnu:outbox) $name)))))
; A restart: the process forgets the queued episodes it has seen.
(= (fnu:restart) (collapse (match &fnote-queued (FnoteQueuedV1 $id) (remove-atom &fnote-queued (FnoteQueuedV1 $id)))))
(= (fnu:stored $id) (ledger-client:call (fnu:socket) "provider-notice" ($id)))
(= (fnu:wait-socket $server $socket $left)
   (if (fs:exists $socket) True
     (if (<= $left 0) False (let $_ (proc:wait $server 20) (fnu:wait-socket $server $socket (- $left 1))))))
(= (fnu:start-ledger)
   (let* (($root (system:cwd)) ($socket (fnu:socket))
          ($_ (if (fs:exists $socket) (fs:remove $socket) True))
          ($worker (path:join (nt:required-env "NATIVE_TEST_CORE") "cetta-value-worker"))
          ($env (cons-atom ("CETTA_DB_SOURCE" (fnu:store)) (cons-atom ("CETTA_DB_SOCKET" $socket) (nt:environment (fnu:home)))))
          ($server (proc:spawn ($worker "--value-bytes" "268435456" "--serve" "--lang" "petta"
                                (path:join $root "src/input_ledger_server.metta") "db-server:configuration")
                               ((cwd $root) (env $env) (max-bytes 65536))))
          ($_ (nt:require ledger-ready (fnu:wait-socket $server $socket 500))))
     $server))
(= (fnu:stop-ledger $server)
   (let* (($_ (proc:cancel $server)) ($_ (proc:await $server)) ($_ (proc:close $server))) True))
(= (fnu:ms-since $started) (/ (- (system:monotonic-ns) $started) 1000000))

;; ------------------------------------------------------------- without a ledger

!(if (not (== (fnu:socket) "")) ()
   (let* (($_ (fnu:fail 100))
          ($_ (nt:require no-ledger-no-notice (nt:equal (fnote:notify) 0)))
          ($_ (nt:require no-ledger-nothing-queued (nt:equal (fnu:intents) ()))))
     (println! UNIT_FAILURE_NOTICE_NO_LEDGER_OK)))

;; ------------------------------------------------------------- the text and the key

!(if (== (fnu:socket) "") ()
   (let* (($episode (json:parse "{\"id\": \"e1\", \"first_at\": 100.9, \"provider\": \"fixture\", \"model\": \"chosen-\\u017e\", \"kind\": \"provider-rejected-429\"}"))
          ($_ (nt:require rate-limit-text
                (nt:equal (fnote:text $episode)
                          "Turn failure observed at 1970-01-01 00:01:40 UTC: HTTP 429: the provider refused the request (rate or quota limit). Check its balance/limits, or choose a model with /models.\nAttempted configuration: {\"provider\": \"fixture\", \"model\": \"chosen-\\u017e\"}\nYour messages are retained. No model was switched automatically. /activity shows the current state.")))
          ($_ (nt:require authorization-text
                (nt:equal (map-atom ("provider-rejected-401" "provider-rejected-403") $kind
                            (str:starts-with (fnote:text (sf:put $episode "kind" $kind))
                                             "Turn failure observed at 1970-01-01 00:01:40 UTC: The provider refused authorization. Check its credentials/access, or choose a model with /models.\n"))
                          (True True))))
          ($_ (nt:require other-failure-text
                (str:starts-with (fnote:text (sf:put $episode "kind" "provider-timeout"))
                  "Turn failure observed at 1970-01-01 00:01:40 UTC: The turn failed: provider-timeout. /activity reports the retry or blocked state; /models remains available.\n")))
          ($_ (nt:require text-without-a-model
                (nt:contains (fnote:text (sf:drop $episode "model")) "\nAttempted configuration: {\"provider\": \"fixture\", \"model\": \"\"}\n")))
          ($_ (nt:require incomplete-episode-has-no-text
                (nt:equal (map-atom ("kind" "provider" "first_at") $key (fnote:text (sf:drop $episode $key))) (None None None))))
          ($_ (nt:require chat-numbers
                (nt:equal (map-atom ("42" "-1001234" "+7" "" "4x" "-") $text (tio:chat-number $text)) (42 -1001234 7 None None None))))
          ($_ (nt:require no-key-without-a-chat
                (nt:equal (map-atom ("" "0" "-0" "4x") $chat (case (tio:key $chat) (((Error tio:key $why) refused) ($key $key))))
                          (refused refused refused refused))))
          ($_ (nt:require group-chat-key (str:starts-with (tio:key "-1001234") "-1001234.0.")))
          ($_ (nt:require pad20 (nt:equal (map-atom (7 1794602052375295387) $n (tio:pad20 $n))
                                          ("00000000000000000007" "01794602052375295387"))))
          ; Fresh keys: the operator's chat, thread 0, a sequence above the
          ; stored one and the clock, written as Python writes it.
          ($first (tio:operator-key))
          ($second (tio:operator-key))
          ($_ (nt:require key-shape (and (str:starts-with $first "42.0.") (nt:equal (str:byte-length $first) 25))))
          ($_ (nt:require keys-increase (< (str:string->number (str:byte-slice $first 5 25)) (str:string->number (str:byte-slice $second 5 25)))))
          ($_ (nt:require key-above-the-clock (>= (str:string->number (str:byte-slice $first 5 25)) 1700000000000000000)))
          ($sequence (path:join (fnu:outbox) "sequence"))
          ($_ (nt:require sequence-as-python-writes-it
                (nt:equal (fs:read-text $sequence) (str:concat (repr (str:string->number (str:byte-slice $second 5 25))) "\n"))))
          ($_ (fs:write $sequence "9000000000000000000\n"))
          ($_ (nt:require key-above-a-stored-sequence (nt:equal (tio:operator-key) "42.0.09000000000000000001")))
          ($_ (fs:write $sequence "not a number"))
          ($_ (nt:require unreadable-sequence-counts-from-the-clock (str:starts-with (tio:operator-key) "42.0.01")))
          ; The intent is written as Python's json.dump writes it, text unescaped.
          ($_ (tio:store-intent "42.0.1" (JsonObject ((JsonPair "key" "42.0.1") (JsonPair "commands" (JsonArray ((JsonArray ("send" "žluť" "plain")))))))))
          ($_ (nt:require intent-bytes
                (nt:equal (fs:read-text (path:join (fnu:outbox) "42.0.1.json"))
                          "{\"key\": \"42.0.1\", \"commands\": [[\"send\", \"žluť\", \"plain\"]]}")))
          ($_ (fs:remove (path:join (fnu:outbox) "42.0.1.json"))))
     (fnu:passed UNIT_FAILURE_NOTICE_TEXT_OK)))

;; ------------------------------------------------------------- one notice an episode

!(if (== (fnu:socket) "") ()
   (let* (($server (fnu:start-ledger))
          ($_ (nt:require nothing-before-a-failure (nt:equal (fnote:notify) 0)))
          ($_ (fnu:fail 100))
          ($episode (fnu:episode))
          ($_ (nt:require first-failure-notified (nt:equal (fnote:notify) 1)))
          ($names (fnu:intents))
          ($_ (nt:require one-intent (nt:equal (size-atom $names) 1)))
          ($name (car-atom $names))
          ($original (fnu:intent-text $name))
          ($text (fnu:notice-text $name))
          ($_ (nt:require actionable-text
                (and (nt:contains $text "HTTP 429") (and (nt:contains $text "/models")
                  (and (nt:contains $text "retained") (nt:contains $text "chosen-model"))))))
          ($_ (nt:require intent-file-as-python-stores-it
                (nt:equal $original
                  (json:stringify-python-with
                    (JsonObject ((JsonPair "key" (str:byte-slice $name 0 (- (str:byte-length $name) 5)))
                                 (JsonPair "commands" (JsonArray ((JsonArray ("send" $text "plain")))))))
                    ((ensure-ascii False))))))
          ($_ (nt:require marked-queued (case (fnu:stored $episode) ((($intent 1) (== $intent $original)) ($other False)))))
          ($_ (nt:require queued-remembered (fnote:queued? $episode)))
          ; Failures of the same provider and model belong to the episode.
          ($_ (nt:require repeats-are-silent
                (nt:equal (map-atom (101 102 103 104 105 106 107 108 109) $n
                            (let $_ (fnu:fail $n) (if (== (fnu:episode) $episode) (fnote:notify) episode-changed)))
                          (0 0 0 0 0 0 0 0 0))))
          ($_ (nt:require bytes-unchanged (nt:equal (fnu:intent-text $name) $original)))
          ; Delivery consumed the outbox file; a restart must not publish it again.
          ($_ (fnu:clear-outbox))
          ($_ (nt:require consumed-stays-consumed (nt:equal (fnote:notify) 0)))
          ($_ (fnu:restart))
          ($_ (nt:require restart-forgets (not (fnote:queued? $episode))))
          ($_ (nt:require restart-asks-the-ledger (nt:equal (fnote:notify) 0)))
          ($_ (nt:require restart-learns-queued (fnote:queued? $episode)))
          ($_ (nt:require restart-publishes-nothing (nt:equal (fnu:intents) ())))
          ; Recovery ends the episode; the next outage gets a notice of its own.
          ($_ (fnu:recover 200))
          ($_ (nt:require recovered-is-silent (nt:equal (fnote:notify) 0)))
          ($_ (fnu:fail 300))
          ($_ (nt:require new-episode (not (== (fnu:episode) $episode))))
          ($_ (nt:require next-outage-notified (nt:equal (fnote:notify) 1)))
          ($_ (nt:require next-outage-one-intent (nt:equal (size-atom (fnu:intents)) 1)))
          ($_ (nt:require model-kept (nt:equal (sf:get (health:read) "model" "") "chosen-model")))
          ($_ (fnu:clear-outbox))
          ($_ (fnu:stop-ledger $server)))
     (fnu:passed UNIT_FAILURE_NOTICE_EPISODES_OK)))

;; ------------------------------------------------------------- crashes and a dead ledger

!(if (== (fnu:socket) "") ()
   (let* (($server (fnu:start-ledger))
          ($_ (fnu:recover 400))
          ($_ (fnu:fail 500))
          ($episode (fnu:episode))
          ; The enqueue fails after the reservation: a directory stands where
          ; the intent's file goes (the key is known from the stored sequence).
          ($_ (fs:make-directories (fnu:outbox) 448))
          ($_ (fs:write (path:join (fnu:outbox) "sequence") "8000000000000000000\n"))
          ($blocked (path:join (fnu:outbox) "42.0.08000000000000000001.json"))
          ($_ (fs:make-directories $blocked 448))
          ($_ (nt:require failed-enqueue-publishes-nothing (nt:equal (fnote:notify) 0)))
          ($reserved (case (fnu:stored $episode) ((($intent 0) $intent) ($other (ReservationMissing $other)))))
          ($_ (nt:require reserved-not-queued (== (get-type $reserved) String)))
          ($_ (nt:require reserved-under-the-known-key (nt:contains $reserved "\"key\": \"42.0.08000000000000000001\"")))
          ($_ (fs:rename $blocked (path:join (fnu:outbox) "blocked.dir")))
          ; A later failure of the episode publishes the stored intent, its key and bytes.
          ($_ (fnu:fail 550))
          ($_ (nt:require retry-publishes-the-reservation (nt:equal (fnote:notify) 1)))
          ($_ (nt:require same-key-same-bytes (nt:equal (fnu:intent-text "42.0.08000000000000000001.json") $reserved)))
          ($_ (nt:require only-the-reserved-intent (nt:equal (fnu:intents) ("42.0.08000000000000000001.json"))))
          ($_ (fnu:clear-outbox))
          ; A reservation another process made first wins over this one's.
          ($_ (fnu:recover 600))
          ($_ (fnu:fail 700))
          ($other-episode (fnu:episode))
          ($theirs "{\"key\": \"42.0.00000000000000000777\", \"commands\": [[\"send\", \"theirs\", \"plain\"]]}")
          ($_ (ledger-client:call (fnu:socket) "provider-notice-reserve" ($other-episode $theirs (system:uuid4-hex))))
          ($_ (nt:require stored-intent-wins (nt:equal (fnote:notify) 1)))
          ($_ (nt:require theirs-published (nt:equal (fnu:intent-text "42.0.00000000000000000777.json") $theirs)))
          ($_ (fnu:clear-outbox))
          ; The ledger stops: nothing is published, and the cycle is not held.
          ($_ (fnu:stop-ledger $server))
          ($_ (fnu:recover 800))
          ($_ (fnu:fail 900))
          ($started (system:monotonic-ns))
          ($_ (nt:require dead-ledger-publishes-nothing (nt:equal (fnote:notify) 0)))
          ($_ (nt:require dead-ledger-does-not-hold (< (fnu:ms-since $started) 10000)))
          ($_ (nt:require dead-ledger-queues-nothing (nt:equal (fnu:intents) ())))
          ; That episode ends before the ledger is back: it is never told.
          ($_ (fnu:recover 950))
          ($again (fnu:start-ledger))
          ($_ (nt:require resolved-episode-is-silent (nt:equal (fnote:notify) 0)))
          ; The next outage, with the same store: published once, then silent.
          ($_ (fnu:fail 1000))
          ($_ (nt:require revived-ledger-notified (nt:equal (fnote:notify) 1)))
          ($_ (nt:require revived-ledger-then-silent (nt:equal (fnote:notify) 0)))
          ($_ (fnu:stop-ledger $again)))
     (fnu:passed UNIT_FAILURE_NOTICE_FAULTS_OK)))

!(if (== (fnu:socket) "") ()
   (let $blocks (collapse (match &self (fnu:block-passed $block) $block))
     (if (== (size-atom $blocks) 3) (println! UNIT_FAILURE_NOTICE_OK)
       (println! (UNIT_FAILURE_NOTICE_INCOMPLETE $blocks)))))
