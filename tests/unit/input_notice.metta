; Unit tests of src/input_notice_cetta.metta; no Python. The expected answers
; are those of the Python it replaces (input_ack.key, submitted, begin_scan,
; scan_after and input_view) for the same sequences, clocks and configuration;
; the keys are the values hashlib gives for the same names.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/input_notice_cetta.metta)

(= (in:config) (NoticeConfigV1 "" True "unit" 15))
(= (in:key $now $task $lane) (notice:key-at (in:config) $now $task $lane "message" "operator" (JsonObject ())))
(= (in:keys $from $to $now)
   (if (>= $from $to) done
     (let $_ (in:key $now (repr $from) "42.0") (in:keys (+ $from 1) $to $now))))
(= (in:page $from $to $tail)
   (if (>= $from $to) $tail
     (cons-atom ((repr $from) "[]") (in:page (+ $from 1) $to $tail))))
(= (in:forget-all)
   (let* (($_ (notice:set-scan "")) ($_ (notice:begin-scan-at 1.0 () ()))) (notice:pending-count)))
(= (in:message $task $date)
   ($task (str:join "" ("[\"input\",\"42.0\",\"message\",\"operator\",{\"update_id\":1,\"message\":{\"date\":" $date ",\"text\":\"private\"}}]"))))
(= (in:view-of $ordinary) (let $_ (notice:begin-scan-at 500.5 $ordinary $ordinary) (notice:input-view)))
(= (in:view $pending $newest $oldest)
   (JsonObject ((JsonPair "pending" (JsonNumber $pending)) (JsonPair "newestMessageAt" $newest)
                (JsonPair "oldestMessageAt" $oldest) (JsonPair "observedAt" (JsonNumber "500.5")))))
(= (in:wait-socket $server $socket $left)
   (if (fs:exists $socket) True
     (if (<= $left 0) False (let $_ (proc:wait $server 20) (in:wait-socket $server $socket (- $left 1))))))
(= (in:health $text)
   (let* (($path (health:path)) ($_ (fs:make-directories (path:dirname $path) 448))) (fs:write $path $text)))
; A block that ends without a result prints nothing and stops nothing: each
; block records its end, and the unit's marker needs all of them.
(= (in:passed $block) (let $_ (add-atom &self (in:block-passed $block)) (println! $block)))

; The delay setting, as float() and the bounds read it.
!(let* (($_ (nt:require delays
              (nt:equal (map-atom ("15" " 1.5 " "0.05" "0" "-1" "nan" "inf" "-Infinity" "1e400" "abc" "")
                                  $text (notice:delay $text))
                        (15 1.5 0.05 none none none none none none 15 15))))
        ($_ (nt:require update-ids
              (nt:equal (map-atom ("{\"update_id\":7}" "{\"update_id\":\"u1\"}" "{}" "{\"update_id\":null}" "[7]" "{\"update_id\":1,\"update_id\":2}")
                                  $text (notice:update-id (json-parse $text)))
                        ("7" "u1" "" "" "" "2"))))
        ($long (str:join "" (map-atom (1 2 3 4 5 6 7 8 9 10 11 12 13) $k "abcdefghij")))
        ($_ (nt:require names
              (nt:equal (map-atom ("" "a" (str:byte-slice $long 0 128) (str:byte-slice $long 0 129)) $name (notice:name? $name))
                        (False True True False)))))
   (in:passed UNIT_INPUT_NOTICE_VALUES_OK))

; Pacing without a ledger: the notice waits out the delay, keeps its key
; across a lost reply and a restart, and is not sent twice.
!(let* (($_ (nt:require first-call-starts-the-delay (nt:equal (in:key 100.0 "input-1" "42.0") "")))
        ($_ (nt:require before-the-delay (nt:equal (in:key 114.9 "input-1" "42.0") "")))
        ($_ (nt:require due-after-the-delay (nt:equal (in:key 115.0 "input-1" "42.0") "42.0.08479484631796438864")))
        ($_ (nt:require same-key-until-submitted (nt:equal (in:key 116.0 "input-1" "42.0") "42.0.08479484631796438864")))
        ($_ (nt:require submitted (nt:equal (notice:submitted-at (in:config) 116.0 "input-1") 1)))
        ($_ (nt:require silent-after-submission (nt:equal (in:key 300.0 "input-1" "42.0") "")))
        ; A restart: the memory is gone, the key is not.
        ($_ (nt:require restart-forgets (nt:equal (in:forget-all) 0)))
        ($_ (nt:require restart-waits-again (nt:equal (in:key 300.0 "input-1" "42.0") "")))
        ($_ (nt:require restart-keeps-the-key (nt:equal (in:key 315.0 "input-1" "42.0") "42.0.08479484631796438864")))
        ($_ (nt:require non-ascii-names (nt:equal (input-ledger:send-key "unit" "42.0" "žluť 日本") "42.0.01627942418857948592")))
        ($_ (in:forget-all))
        ; Only an operator's message under observed strategy, with usable names and a delay.
        ($_ (nt:require other-senders-and-kinds
              (nt:equal (map-atom (("message" "peer") ("message" "bot") ("callback" "operator")) $pair
                          (let ($kind $role) $pair (notice:key-at (in:config) 100.0 "t" "42.0" $kind $role (JsonObject ()))))
                        ("" "" ""))))
        ($_ (nt:require observed-strategy-off
              (nt:equal (notice:key-at (NoticeConfigV1 "" False "unit" 15) 100.0 "t" "42.0" "message" "operator" (JsonObject ())) "")))
        ($_ (nt:require no-delay-no-notice
              (nt:equal (notice:key-at (NoticeConfigV1 "" True "unit" none) 100.0 "t" "42.0" "message" "operator" (JsonObject ())) "")))
        ($_ (nt:require unusable-names (nt:equal ((in:key 100.0 "" "42.0") (in:key 100.0 "t" "")) ("" ""))))
        ($_ (nt:require refusals-remember-nothing (nt:equal (notice:pending-count) 0)))
        ; The lane is captured with the first sight of the task.
        ($_ (in:key 100.0 "t" "42.0"))
        ($_ (nt:require lane-cannot-change (nt:equal (in:key 115.0 "t" "84.0") "")))
        ($_ (in:forget-all))
        ; One notice per lane a minute; other lanes are not held back.
        ($_ (map-atom (("a" "42.0") ("b" "42.0") ("c" "84.0")) $pair (let ($task $lane) $pair (in:key 100.0 $task $lane))))
        ($_ (nt:require first-of-the-lane (not (== (in:key 115.0 "a" "42.0") ""))))
        ($_ (notice:submitted-at (in:config) 115.0 "a"))
        ($_ (nt:require lane-limited (nt:equal (in:key 115.0 "b" "42.0") "")))
        ($_ (nt:require other-lane-free (nt:equal (in:key 115.0 "c" "84.0") "84.0.03909223822263505970")))
        ($_ (nt:require lane-still-limited (nt:equal (in:key 174.9 "b" "42.0") "")))
        ($_ (nt:require lane-free-after-a-minute (not (== (in:key 175.0 "b" "42.0") ""))))
        ; A lane whose tasks are all gone forgets its limit.
        ($_ (notice:begin-scan-at 1.0 () (("b" "[]") ("c" "[]"))))
        ($_ (nt:require lane-with-a-task-keeps-its-limit (nt:equal (notice:last "42.0") 115.0)))
        ($_ (notice:begin-scan-at 1.0 () (("c" "[]"))))
        ($_ (nt:require idle-lane-forgets (nt:equal (notice:last "42.0") none)))
        ($_ (in:forget-all)))
   (in:passed UNIT_INPUT_NOTICE_PACING_OK))

; An unresolved turn failure silences the notice and starts no delay. The
; record is read at each scan.
!(let* (($open "{\"failure_episode\": {\"kind\": \"provider-rejected-429\", \"first_at\": 5.0}}")
        ($_ (in:health $open))
        ($_ (nt:require failure-pending (health:failure-pending)))
        ($_ (nt:require the-last-scan-saw-no-failure (not (notice:failure?))))
        ($_ (notice:begin-scan-at 1.0 () ()))
        ($_ (nt:require silent-during-failure (nt:equal ((in:key 100.0 "f" "42.0") (in:key 200.0 "f" "42.0")) ("" ""))))
        ($_ (nt:require failure-starts-no-delay (nt:equal (notice:pending-count) 0)))
        ($_ (in:health "{\"failure_episode\": {\"kind\": \"provider-rejected-429\", \"resolved_at\": 6.5}}"))
        ($_ (nt:require failure-resolved (not (health:failure-pending))))
        ($_ (in:health "{\"failure_episode\": {}}"))
        ($_ (nt:require empty-episode-is-none (not (health:failure-pending))))
        ($_ (in:health "not json"))
        ($_ (nt:require unreadable-record-is-none (not (health:failure-pending))))
        ($_ (nt:require silent-until-the-next-scan (nt:equal (in:key 250.0 "f" "42.0") "")))
        ($_ (notice:begin-scan-at 1.0 () ()))
        ($_ (nt:require notice-after-resolution (nt:equal ((in:key 300.0 "f" "42.0") (in:key 315.0 "f" "42.0"))
                                                          ("" (input-ledger:send-key "unit" "42.0" "f")))))
        ($_ (in:forget-all)))
   (in:passed UNIT_INPUT_NOTICE_FAILURE_OK))

; The scan: a complete one forgets what it did not see; pages of an incomplete
; one accumulate, and the cursor is the last task of the page.
!(let* (($_ (in:keys 0 300 100.0))
        ($_ (nt:require remembered (nt:equal (notice:pending-count) 300)))
        ($_ (notice:begin-scan-at 1.0 (("0" "body")) (("0" "body"))))
        ($_ (nt:require consumed-tasks-forgotten
              (nt:equal ((notice:pending-count) (notice:pending "0") (notice:pending "1")) (1 (100.0 False "42.0") none))))
        ($_ (in:forget-all))
        ($_ (in:keys 0 600 100.0))
        ($first (in:page 0 256 (("runtime-scan-unavailable" "[]"))))
        ($_ (notice:begin-scan-at 1.0 $first $first))
        ($_ (nt:require cursor-after-first-page (nt:equal ((notice:scan-after) (notice:pending-count)) ("255" 600))))
        ($_ (nt:require incomplete-scan-has-no-view (nt:equal (notice:input-view) JsonNull)))
        ($second (in:page 256 512 (("runtime-scan-unavailable" "[]"))))
        ($_ (notice:begin-scan-at 1.0 $second $second))
        ($_ (nt:require cursor-after-second-page (nt:equal ((notice:scan-after) (notice:pending-count)) ("511" 600))))
        ($last (in:page 512 600 ()))
        ($_ (notice:begin-scan-at 1.0 $last $last))
        ($_ (nt:require all-pages-seen-nothing-forgotten (nt:equal ((notice:scan-after) (notice:pending-count)) ("" 600))))
        ($_ (nt:require continued-scan-has-no-view (nt:equal (notice:input-view) JsonNull)))
        ; The next scan starts anew: its one complete page is all that was seen.
        ($_ (notice:begin-scan-at 1.0 (in:page 0 5 ()) (in:page 0 5 ())))
        ($_ (nt:require new-scan-forgets-the-rest (nt:equal (notice:pending-count) 5)))
        ; An unavailable scan with no task keeps everything and has no cursor.
        ($_ (notice:begin-scan-at 1.0 (("runtime-scan-unavailable" "[]")) (("runtime-scan-unavailable" "[]"))))
        ($_ (nt:require failed-scan-keeps-memory (nt:equal ((notice:scan-after) (notice:pending-count)) ("" 5))))
        ($_ (in:forget-all)))
   (in:passed UNIT_INPUT_NOTICE_SCAN_OK))

; The inbox view: counts and dates only, and only from a complete readable scan.
!(let* (($_ (nt:require empty-inbox (nt:equal (in:view-of ()) (in:view "0" JsonNull JsonNull))))
        ($_ (nt:require dated-messages
              (nt:equal (in:view-of ((in:message "a" "1700000200") (in:message "b" "1700000100.5") (in:message "c" "1700000300")))
                        (in:view "3" (JsonNumber "1700000300") (JsonNumber "1700000100.5")))))
        ($_ (nt:require view-holds-no-text
              (not (nt:contains (json-stringify (notice:input-view)) "private"))))
        ($_ (nt:require other-tasks-are-not-counted
              (nt:equal (in:view-of ((in:message "a" "1700000200")
                                     ("p" "[\"input\",\"42.0\",\"message\",\"peer\",{\"message\":{\"date\":5}}]")
                                     ("k" "[\"command\",\"k\",\"/health\",\"\",\"42.0\"]")
                                     ("d" "[\"delivery\",\"k\",0,1,[\"delivered\",5]]")))
                        (in:view "1" (JsonNumber "1700000200") (JsonNumber "1700000200")))))
        ($_ (nt:require an-undated-message-hides-the-dates
              (nt:equal (map-atom ("0" "-5" "true" "null" "\"7\"" "1000000000001") $date
                          (in:view-of ((in:message "a" "1700000200") (in:message "b" $date))))
                        ((in:view "2" JsonNull JsonNull) (in:view "2" JsonNull JsonNull) (in:view "2" JsonNull JsonNull)
                         (in:view "2" JsonNull JsonNull) (in:view "2" JsonNull JsonNull) (in:view "2" JsonNull JsonNull)))))
        ($_ (nt:require the-largest-date
              (nt:equal (in:view-of ((in:message "a" "1000000000000")))
                        (in:view "1" (JsonNumber "1000000000000") (JsonNumber "1000000000000")))))
        ($_ (nt:require edited-message-dates
              (nt:equal (in:view-of (("e" "[\"input\",\"42.0\",\"message\",\"operator\",{\"message\":null,\"edited_message\":{\"date\":77}}]")
                                     ("f" "[\"input\",\"42.0\",\"message\",\"operator\",{\"edited_message\":{\"date\":78}}]")))
                        (in:view "2" (JsonNumber "78") (JsonNumber "77")))))
        ($_ (nt:require a-message-that-is-no-object-is-undated
              (nt:equal (in:view-of (("g" "[\"input\",\"42.0\",\"message\",\"operator\",{\"message\":\"x\",\"edited_message\":{\"date\":78}}]")))
                        (in:view "1" JsonNull JsonNull))))
        ($_ (nt:require unreadable-tasks-leave-no-view
              (nt:equal (map-atom ((("u" "not json")) (("u" "{}")) (("u" "[]")) (("u" "[\"novel\"]"))
                                   (("u" "[\"input\",\"42.0\",\"message\"]"))
                                   (("u" "[\"input\",\"42.0\",\"message\",\"operator\",[1]]"))
                                   ((in:message "a" "5") ("runtime-scan-unavailable" "[]")))
                                  $ordinary (in:view-of $ordinary))
                        (JsonNull JsonNull JsonNull JsonNull JsonNull JsonNull JsonNull))))
        ; The unavailable scan above left its cursor: the page after it continues that scan.
        ($_ (nt:require cursor-of-the-incomplete-scan (nt:equal (notice:scan-after) "a")))
        ($_ (nt:require continued-scan-has-no-view (nt:equal (in:view-of ((in:message "a" "5"))) JsonNull)))
        ; The view is of the conversational inputs; the page decides completeness.
        ($_ (notice:begin-scan-at 500.5 ((in:message "a" "5")) ((in:message "a" "5") ("ctl" "[\"input\",\"42.0\",\"message\",\"operator\",{}]"))))
        ($_ (nt:require view-of-the-ordinary-inputs (nt:equal (notice:input-view) (in:view "1" (JsonNumber "5") (JsonNumber "5")))))
        ($_ (in:forget-all)))
   (in:passed UNIT_INPUT_NOTICE_VIEW_OK))

; The wait between two scans returns after its two seconds, no sooner.
!(let* (($start (system:monotonic-ns)) ($_ (notice:wait))
        ($elapsed (/ (- (system:monotonic-ns) $start) 1000000.0))
        ($_ (nt:require wait-lasts-two-seconds (and (>= $elapsed 2000) (< $elapsed 4000)))))
   (in:passed UNIT_INPUT_NOTICE_WAIT_OK))

; With the native ledger: a processed update gets no notice, a due notice is
; registered under its key, and submission is recorded.
!(let* (($home (nt:required-env "HOME")) ($root (system:cwd))
        ($socket (path:join $home "l.sock")) ($store (path:join $home "store"))
        ($worker (path:join (nt:required-env "NATIVE_TEST_CORE") "cetta-value-worker"))
        ($module (path:join $root "src/input_ledger_server.metta"))
        ($server-env (cons-atom ("CETTA_DB_SOURCE" $store) (cons-atom ("CETTA_DB_SOCKET" $socket) (nt:environment $home))))
        ($server (proc:spawn ($worker "--value-bytes" "268435456" "--serve" "--lang" "petta" $module "db-server:configuration")
                             ((cwd $root) (env $server-env) (max-bytes 65536))))
        ($_ (nt:require ledger-ready (in:wait-socket $server $socket 500)))
        ($config (NoticeConfigV1 $socket True "unit" 15))
        ($seven (JsonObject ((JsonPair "update_id" (JsonNumber "7")))))
        ($eight (JsonObject ((JsonPair "update_id" (JsonNumber "8")))))
        ($_ (nt:require processed-in-ledger (nt:equal (ledger-client:call $socket "mark-processed" (50.0 ("7"))) True)))
        ($_ (nt:require processed-update-gets-no-notice
              (nt:equal ((notice:key-at $config 100.0 "late" "42.0" "message" "operator" $seven)
                         (notice:key-at $config 200.0 "late" "42.0" "message" "operator" $seven)) ("" ""))))
        ($_ (nt:require processed-update-starts-no-delay (nt:equal (notice:pending-count) 0)))
        ($_ (nt:require nothing-registered-yet (nt:equal (ledger-client:call $socket "due" (32)) ())))
        ($_ (nt:require pending-update-waits (nt:equal (notice:key-at $config 100.0 "input-1" "42.0" "message" "operator" $eight) "")))
        ($key (notice:key-at $config 115.0 "input-1" "42.0" "message" "operator" $eight))
        ($_ (nt:require registered-key (nt:equal $key "42.0.08479484631796438864")))
        ($_ (nt:require repeated-key (nt:equal (notice:key-at $config 116.0 "input-1" "42.0" "message" "operator" $eight) $key)))
        ($_ (nt:require submission-recorded (nt:equal (notice:submitted-at $config 116.0 "input-1") 1)))
        ; The delivery receipt and the processed update make the notice due for cleanup: it is in the ledger under this key.
        ($_ (ledger-client:call $socket "receipt" ($key (delivered 500))))
        ($_ (ledger-client:call $socket "mark-processed" (120.0 ("8"))))
        ($_ (nt:require notice-is-in-the-ledger
              (case (ledger-client:call $socket "due" (32))
                ((((input-notice "input-1" "8" "42.0" $sent $created 500 $p $d $state $changed)) (== $sent $key))
                 ($other False)))))
        ; After a restart the ledger still refuses a notice for the processed update.
        ($_ (in:forget-all))
        ($_ (nt:require restart-after-processing
              (nt:equal ((notice:key-at $config 300.0 "input-1" "42.0" "message" "operator" $eight)
                         (notice:key-at $config 315.0 "input-1" "42.0" "message" "operator" $eight)) ("" ""))))
        ($_ (proc:cancel $server)) ($_ (proc:await $server)) ($_ (proc:close $server))
        ; A ledger that does not answer: an Error, never a key; submission is remembered first.
        ($nine (JsonObject ((JsonPair "update_id" (JsonNumber "9")))))
        ($_ (nt:require dead-ledger-gives-no-key
              (case (notice:key-at $config 400.0 "input-2" "42.0" "message" "operator" $nine)
                (((Error $where1 $why1) True) ($other1 False)))))
        ($_ (nt:require dead-ledger-starts-no-delay (nt:equal (notice:pending "input-2") none)))
        ($_ (notice:key-at $config 400.0 "input-3" "42.0" "message" "operator" (JsonObject ())))
        ($_ (nt:require dead-ledger-refuses-registration
              (case (notice:key-at $config 415.0 "input-3" "42.0" "message" "operator" (JsonObject ()))
                (((Error $where2 $why2) True) ($other2 False)))))
        ($_ (nt:require dead-ledger-submission-is-an-error
              (case (notice:submitted-at $config 416.0 "input-3")
                (((Error $where3 $why3) True) ($other3 False)))))
        ($_ (nt:require submission-remembered-first (nt:equal (notice:pending "input-3") (400.0 True "42.0")))))
   (in:passed UNIT_INPUT_NOTICE_LEDGER_OK))

!(nt:require every-block-ended
   (nt:equal (size-atom (collapse (match &self (in:block-passed $block) $block))) 7))
!(println! UNIT_INPUT_NOTICE_OK)
