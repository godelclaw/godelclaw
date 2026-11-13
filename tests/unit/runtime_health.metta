; Unit tests of src/runtime_health_cetta.metta, the responder's /health and
; /activity; no Python. The cases are those of the Python they replace
; (runtime_health.report, activity_report and the status they formatted), with
; the same records and clocks, and the same expected text.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/runtime_health_cetta.metta)

(= (rhu:obj $text) (json:parse $text))
(= (rhu:none) (JsonObject ()))
(= (rhu:home) (nt:required-env "HOME"))
(= (rhu:put $name $text)
   (let $path (path:join (rhu:home) $name)
     (let $_ (fs:make-directories (path:dirname $path) 448) (fs:write $path $text))))
(= (rhu:remove $name)
   (let $path (path:join (rhu:home) $name) (if (fs:exists $path) (fs:remove $path) True)))
(= (rhu:has $text $part) (nt:contains $text $part))
(= (rhu:lacks $text $part) (not (nt:contains $text $part)))
(= (rhu:passed $block) (let $_ (add-atom &self (rhu:block-passed $block)) (println! $block)))

; The activity line at 1000 for a checkpoint, a turn record, the client's
; publication and a progress state, with cognition enabled.
(= (rhu:activity $working $cognition $client $progress)
   (rh:activity-text 1000 (rhu:obj $working) (rhu:obj $cognition) True $client $progress ("decay" "godelclaw" "fixture")))
(= (rhu:awake) (RhClientV1 0 0 0))
(= (rhu:resting $until) (RhClientV1 $until 0 0))
; The status at 1000, enabled, memory native and reachable.
(= (rhu:status $channel $working $mode $cognition)
   (rh:status 1000 (rhu:obj $channel) (rhu:obj $working) (rhu:obj $mode) (rhu:obj $cognition) True (rhu:none)
              (RhMemoryV1 "native" True)))
(= (rhu:problems (RhStatusV1 $problems $poll $control $control-age $loop $working $turn $pending $memory)) $problems)
(= (rhu:ages (RhStatusV1 $problems $poll $control $control-age $loop $working $turn $pending $memory))
   ($control-age $working $turn $pending))
(= (rhu:member? $item $items) (rh:member? $item $items))
; A progress receipt of this process, alive, at AT with the monotonic deadline WAIT.
(= (rhu:receipt $at $wait)
   (rhu:put "progress.json"
     (str:join "" ("{\"pid\": " (repr (system:pid)) ", \"owner\": \"" (process:owner) "\", \"progress_at\": " (repr $at)
                   ", \"wait_until\": " (repr $wait) ", \"phase\": \"x\"}"))))
; A scripted git on the unit's PATH: it prints COMMIT, or fails for "".
(= (rhu:git $commit)
   (let* (($path (path:join (rhu:home) "bin/git"))
          ($_ (fs:make-directories (path:dirname $path) 448))
          ($_ (fs:write $path (if (== $commit "") "#!/bin/sh\nexit 128\n" (str:join "" ("#!/bin/sh\necho " $commit "\n"))))))
     (fs:set-mode $path 448)))

;; ------------------------------------------------------------- /activity

!(let* (($line1 (rhu:activity "{\"saved_at\": 990, \"loops\": 50}"
                             "{\"schema\": 2, \"in_flight_since\": 980, \"local_commit_pending\": true}" (rhu:awake) "waiting"))
        ($_ (nt:require recording-result-state (rhu:has $line1 "activity: recording-result |")))
        ($_ (nt:require recording-result-wake (rhu:has $line1 "wake=local-storage")))
        ($_ (nt:require recording-result-turn (rhu:has $line1 "turn=recording-result:20s")))
        ($line2 (rhu:activity "{\"saved_at\": 100, \"loops\": 50}"
                             "{\"schema\": 2, \"last_outcome\": \"completed\", \"last_completed_at\": 110}" (rhu:awake) "stalled"))
        ($_ (nt:require stalled-command-shown (rhu:has $line2 "activity: stalled |")))
        ($_ (nt:require stalled-turn (rhu:has $line2 "turn=completed:890s-ago")))
        ($_ (nt:require stalled-wake (rhu:has $line2 "wake=supervisor")))
        ($line3 (rhu:activity "{\"saved_at\": 999, \"loops\": 50}"
                             "{\"schema\": 2, \"last_outcome\": \"failed\", \"last_failed_at\": 980, \"in_flight_since\": 0, \"retry_at\": 1100}"
                             (rhu:awake) "unknown"))
        ($_ (nt:require failed-retry-state (rhu:has $line3 "activity: retrying |")))
        ($_ (nt:require failed-retry-wake (rhu:has $line3 "wake=human-or-100s")))
        ($_ (nt:require failed-retry-never-succeeded (rhu:has $line3 "last-success=never")))
        ($_ (nt:require failed-retry-streak (rhu:has $line3 "failure-streak=0")))
        ($line4 (rhu:activity "{\"saved_at\": 999, \"loops\": 50}"
                             "{\"schema\": 2, \"last_outcome\": \"failed\", \"last_failed_at\": 980, \"retry_at\": 999}"
                             (rhu:awake) "unknown"))
        ($_ (nt:require due-retry-is-ready (rhu:has $line4 "wake=retry-ready")))
        ($line5 (rh:activity-text 1000 (rhu:obj "{\"saved_at\": 999, \"loops\": 50}") (rhu:obj "{\"pending_since\": 0}") False
                                 (rhu:awake) "fresh" ("decay" "godelclaw" "glm")))
        ($_ (nt:require stopped-state (rhu:has $line5 "activity: stopped |")))
        ($_ (nt:require stopped-steps (rhu:has $line5 "steps=0@operator-latch")))
        ($_ (nt:require stopped-wake (rhu:has $line5 "wake=/start"))))
   (rhu:passed UNIT_RUNTIME_HEALTH_ACTIVITY_STATES_OK))

!(let* (($line1 (rhu:activity "{\"saved_at\": 990, \"loops\": 12, \"continuation_pending\": true}"
                             "{\"last_completed_at\": 995, \"pending_since\": 0, \"last_outcome\": \"completed\"}" (rhu:awake) "fresh"))
        ($_ (nt:require receipts-working (rhu:has $line1 "activity: working |")))
        ($_ (nt:require receipts-steps (rhu:has $line1 "steps=12@checkpoint")))
        ($_ (nt:require receipts-continuation (rhu:has $line1 "continuation=pending")))
        ($_ (nt:require receipts-turn (rhu:has $line1 "turn=completed:5s-ago")))
        ($line2 (rhu:activity "{\"saved_at\": 999, \"loops\": 0, \"continuation_pending\": false}"
                             "{\"last_completed_at\": 995, \"pending_since\": 0, \"last_outcome\": \"completed\"}" (rhu:awake) "fresh"))
        ($_ (nt:require idle-not-resting (rhu:has $line2 "activity: idle |")))
        ($_ (nt:require idle-wake (rhu:has $line2 "wake=human-or-heartbeat")))
        ($_ (nt:require idle-no-rest-left (rhu:lacks $line2 "rest-left")))
        ($_ (nt:require idle-rest-none (rhu:has $line2 "rest=none")))
        ($line3 (rhu:activity "{\"saved_at\": 999, \"loops\": 0, \"continuation_pending\": false}"
                             "{\"last_completed_at\": 900, \"pending_since\": 998, \"budget_at_start\": 50, \"last_outcome\": \"completed\"}"
                             (rhu:awake) "fresh"))
        ($_ (nt:require armed-budget-working (rhu:has $line3 "activity: working |")))
        ($_ (nt:require armed-budget-steps (rhu:has $line3 "steps=50@turn-start")))
        ($_ (nt:require armed-budget-turn (rhu:has $line3 "turn=pending:2s")))
        ($line4 (rhu:activity "{\"saved_at\": 999, \"loops\": 0, \"continuation_pending\": false}"
                             "{\"schema\": 2, \"last_completed_at\": 900, \"last_failed_at\": 980, \"pending_since\": 600, \"in_flight_since\": 0, \"obligation_since\": 600, \"budget_at_start\": 50, \"last_outcome\": \"failed\"}"
                             (rhu:awake) "fresh"))
        ($_ (nt:require failed-request-not-in-flight (rhu:lacks $line4 "turn=pending")))
        ($_ (nt:require failed-request-turn (rhu:has $line4 "turn=failed:20s-ago")))
        ($_ (nt:require failed-request-obligation (rhu:has $line4 "obligation=retry:400s")))
        ($line5 (rhu:activity "{\"saved_at\": 999, \"loops\": 0, \"continuation_pending\": false}"
                             "{\"last_completed_at\": 900, \"last_failed_at\": 980, \"pending_since\": 600, \"budget_at_start\": 50, \"last_outcome\": \"failed\"}"
                             (rhu:awake) "fresh"))
        ($_ (nt:require legacy-failure-not-in-flight (rhu:lacks $line5 "turn=pending")))
        ($_ (nt:require legacy-failure-turn (rhu:has $line5 "turn=failed:20s-ago")))
        ($_ (nt:require legacy-failure-obligation (rhu:has $line5 "obligation=retry:400s")))
        ($line6 (rhu:activity "{\"saved_at\": 999, \"loops\": 0, \"continuation_pending\": false}"
                             "{\"schema\": 2, \"last_completed_at\": 900, \"last_failed_at\": 980, \"in_flight_since\": 0, \"obligation_since\": 600, \"last_outcome\": \"failed\"}"
                             (RhClientV1 0 2 0) "fresh"))
        ($_ (nt:require pending-input-shown (rhu:has $line6 "input=pending:2")))
        ($_ (nt:require pending-input-turn (rhu:has $line6 "turn=failed:20s-ago")))
        ($line7 (rhu:activity "{\"loops\": 0}" "{}" (RhClientV1 0 5 3) "fresh"))
        ($_ (nt:require prepared-input-shown (rhu:has $line7 "input=prepared:3/pending:5"))))
   (rhu:passed UNIT_RUNTIME_HEALTH_ACTIVITY_RECEIPTS_OK))

!(let* (($line1 (rhu:activity "{\"saved_at\": 999, \"loops\": 0, \"continuation_pending\": true}"
                             "{\"last_completed_at\": 995, \"pending_since\": 0, \"last_outcome\": \"completed\"}" (rhu:awake) "fresh"))
        ($_ (nt:require continuation-scheduled (rhu:has $line1 "activity: scheduled |")))
        ($_ (nt:require continuation-wake (rhu:has $line1 "wake=timer-or-human")))
        ($_ (nt:require continuation-pending (rhu:has $line1 "continuation=pending")))
        ($line2 (rhu:activity "{\"saved_at\": 999, \"loops\": 0, \"continuation_pending\": false}"
                             "{\"last_completed_at\": 995, \"pending_since\": 0, \"last_outcome\": \"completed\"}" (rhu:resting 0) "fresh"))
        ($_ (nt:require no-rest-is-idle (rhu:has $line2 "activity: idle |")))
        ($_ (nt:require no-rest-none (rhu:has $line2 "rest=none")))
        ($_ (nt:require no-rest-no-human-wait (rhu:lacks $line2 "until-human")))
        ($line3 (rhu:activity "{\"saved_at\": 999, \"loops\": 0, \"continuation_pending\": false, \"intent\": \"quiet integration\"}"
                             "{\"last_completed_at\": 995, \"pending_since\": 0, \"last_outcome\": \"completed\"}" (rhu:resting 1416.2) "fresh"))
        ($_ (nt:require timed-rest-state (rhu:has $line3 "activity: resting |")))
        ($_ (nt:require timed-rest-wake (rhu:has $line3 "wake=timer-or-human")))
        ($_ (nt:require timed-rest-left (rhu:has $line3 "rest=417s-left:quiet integration")))
        ($line4 (rhu:activity "{\"saved_at\": 999, \"loops\": 0, \"continuation_pending\": false, \"banked_loops\": 43}"
                             "{\"last_completed_at\": 995, \"pending_since\": 0, \"last_outcome\": \"completed\"}" (rhu:resting 1020) "fresh"))
        ($_ (nt:require resting-banked (rhu:has $line4 "steps=43@banked")))
        ($_ (nt:require resting-not-zero (rhu:lacks $line4 "steps=0")))
        ($line5 (rhu:activity "{\"saved_at\": 999, \"loops\": 12, \"continuation_pending\": false, \"intent\": \"peek w7 for a reply\"}"
                             "{\"last_completed_at\": 995, \"pending_since\": 0, \"last_outcome\": \"completed\"}" (rhu:resting 0) "fresh"))
        ($_ (nt:require finished-rest-none (rhu:has $line5 "rest=none")))
        ($_ (nt:require finished-rest-no-intention (rhu:lacks $line5 "peek w7 for a reply")))
        ; The intention's whitespace runs read as single spaces, cut at 120 characters.
        ($line6 (rhu:activity "{\"loops\": 0, \"intent\": \"  peek\\u00a0 w7\\tfor\\n a reply \"}" "{}" (rhu:resting 1001) "fresh"))
        ($_ (nt:require intention-words (rhu:has $line6 "rest=1s-left:peek w7 for a reply |")))
        ($long (str:join "" (map-atom (1 2 3 4 5 6 7 8 9 10 11 12 13) $k "žluťoučký ")))
        ($line7 (rh:activity-text 1000 (JsonObject ((JsonPair "intent" $long))) (rhu:none) True (rhu:resting 1001) "fresh"
                                 ("decay" "godelclaw" "glm")))
        ($_ (nt:require intention-cut (rhu:has $line7 (str:concat ":" (str:concat (str:char-slice $long 0 120) " | continuation=")))))
        ($ideographic (str:from-python-codepoints (97 12288 133 98)))
        ($_ (nt:require words-alone (nt:equal (map-atom ("" "   " "a" " a  b " $ideographic) $t (rh:words $t)) ("" "" "a" "a b" "a b")))))
   (rhu:passed UNIT_RUNTIME_HEALTH_ACTIVITY_REST_OK))

; The progress receipt as a supervisor reads it, and the provider's retained rejection.
!(let* (($working "{\"saved_at\": 100, \"loops\": 50}")
        ($cognition "{\"schema\": 2, \"last_outcome\": \"completed\", \"last_completed_at\": 110}")
        ($_ (nt:require waiting-provider (rhu:has (rhu:activity $working $cognition (rhu:awake) "waiting") "activity: waiting | ")))
        ($_ (nt:require pressure-to-supervisor
              (rhu:has (rhu:activity $working $cognition (rhu:awake) "memory-pressure") "activity: memory-pressure |")))
        ($_ (nt:require unknown-old-checkpoint-unconfirmed
              (rhu:has (rhu:activity $working $cognition (rhu:awake) "unknown") "activity: unconfirmed | ")))
        ($_ (nt:require fresh-keeps-working (rhu:has (rhu:activity $working $cognition (rhu:awake) "fresh") "activity: working | ")))
        ($_ (nt:require recent-checkpoint-stays-working
              (rhu:has (rhu:activity "{\"saved_at\": 900, \"loops\": 50}" $cognition (rhu:awake) "unknown") "activity: working | ")))
        ($line1 (rhu:activity "{\"loops\": 3}" "{\"provider_rejection\": {\"status\": 429, \"code\": \"rate\", \"message\": \"slow down\"}}"
                             (rhu:awake) "fresh"))
        ($_ (nt:require rejection-blocks (rhu:has $line1 "activity: blocked | steps=3@checkpoint")))
        ($_ (nt:require rejection-wake (rhu:has $line1 "wake=input-or-configuration")))
        ($_ (nt:require rejection-detail (str:ends-with $line1 " | provider-rejection=HTTP 429 rate: slow down")))
        ($long (str:join "" (map-atom (1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25) $k "ž123456789")))
        ($line2 (rh:activity-text 1000 (rhu:none) (JsonObject ((JsonPair "provider_rejection" (JsonObject ((JsonPair "message" $long))))))
                                 True (rhu:awake) "fresh" ("decay" "godelclaw" "glm")))
        ($_ (nt:require rejection-cut (str:ends-with $line2 (str:char-slice (str:concat " | provider-rejection=HTTP ? : " $long) 0 240))))
        ($_ (nt:require rejection-cut-length
              (nt:equal (str:char-length (str:char-slice (str:concat " | provider-rejection=HTTP ? : " $long) 0 240)) 240))))
   (rhu:passed UNIT_RUNTIME_HEALTH_ACTIVITY_PROGRESS_OK))

; The whole line, field by field, as runtime_health.activity_report formatted it.
!(let* (($line (rhu:activity "{\"saved_at\": 990, \"loops\": 12, \"continuation_pending\": true}"
                             "{\"last_completed_at\": 995, \"pending_since\": 0, \"last_outcome\": \"completed\", \"failure_streak\": 2}"
                             (rhu:awake) "fresh"))
        ($_ (nt:require activity-line
              (nt:equal $line "activity: working | steps=12@checkpoint | fuel=decay | mode=godelclaw | model=fixture | wake=now | rest=none | continuation=pending | checkpoint-age=10s | turn=completed:5s-ago | last-success=5s-ago | input=none | obligation=none | failure-streak=2 | last-outcome=completed | progress=fresh | background=use model-jobs for archived results"))))
   (rhu:passed UNIT_RUNTIME_HEALTH_ACTIVITY_LINE_OK))

;; ------------------------------------------------------------- /health

!(let* (($control (rhu:obj "{\"observed_at\": 995, \"channel_ready\": true}"))
        ($status (rh:status 1000 (rhu:obj "{\"last_poll_ok_at\": 1}") (rhu:obj "{\"saved_at\": 1}") (rhu:obj "{\"mode\": \"iter\"}")
                            (rhu:obj "{\"schema\": 2, \"last_completed_at\": 997, \"last_settled_at\": 998}") True $control
                            (RhMemoryV1 "native" True)))
        ($_ (nt:require control-and-upstream-checkpoint-healthy (nt:equal (rhu:problems $status) ())))
        ($_ (nt:require control-age-and-settled-checkpoint (nt:equal (rhu:ages $status) (5 2 3 None))))
        ($stale (rh:status 1000 (rhu:obj "{\"last_poll_ok_at\": 1}") (rhu:obj "{\"saved_at\": 1}") (rhu:obj "{\"mode\": \"iter\"}")
                           (rhu:obj "{\"schema\": 2, \"last_completed_at\": 997, \"last_settled_at\": 998}") True
                           (rhu:obj "{\"observed_at\": 950, \"channel_ready\": false}") (RhMemoryV1 "native" True)))
        ($_ (nt:require stale-control (nt:equal (rhu:problems $stale) ("operator-control-stale" "channel-service-unavailable"))))
        ($stopped (rh:status 1000 (rhu:obj "{\"last_poll_ok_at\": 999, \"loop_status\": \"awake\", \"waiting_until\": 0}")
                             (rhu:obj "{\"saved_at\": 1, \"loops\": 50}") (rhu:obj "{\"mode\": \"godelclaw\"}")
                             (rhu:obj "{\"pending_since\": 100}") False (rhu:none) (RhMemoryV1 "native" True)))
        ($_ (nt:require stopped-needs-channel-not-cognition (nt:equal (rhu:problems $stopped) ())))
        ($_ (nt:require healthy-wait
              (nt:equal (rhu:problems (rhu:status "{\"last_poll_ok_at\": 999, \"loop_status\": \"waiting\", \"waiting_until\": 1200}"
                                                  "{\"saved_at\": 1}" "{}" "{}")) ())))
        ($_ (nt:require stale-poll-and-awake-loop
              (nt:equal (rhu:problems (rhu:status "{\"last_poll_ok_at\": 100, \"loop_status\": \"awake\", \"waiting_until\": 0}"
                                                  "{\"saved_at\": 1}" "{}" "{}"))
                        ("telegram-poll-stale" "cognitive-boundary-stale" "model-turn-receipt-missing"))))
        ($fresh (rhu:status "{\"last_poll_ok_at\": 999, \"loop_status\": \"waiting\", \"waiting_until\": 1200}"
                            "{\"saved_at\": 999, \"loops\": 5}" "{}"
                            "{\"last_completed_at\": 995, \"completed_count\": 4, \"pending_since\": 0, \"last_outcome\": \"completed\"}"))
        ($_ (nt:require fresh-turn-healthy (nt:equal (rhu:problems $fresh) ())))
        ($_ (nt:require fresh-turn-age (nt:equal (rhu:ages $fresh) (None 1 5 None))))
        ($spinning (rhu:status "{\"last_poll_ok_at\": 999, \"loop_status\": \"waiting\", \"waiting_until\": 1200}"
                               "{\"saved_at\": 999, \"loops\": 42}" "{}"
                               "{\"pending_since\": 600, \"last_expected_at\": 999, \"expected_count\": 300, \"completed_count\": 0}"))
        ($_ (nt:require old-pending-overdue (nt:equal (rhu:problems $spinning) ("model-turn-overdue"))))
        ($_ (nt:require old-pending-age (nt:equal (rhu:ages $spinning) (None 1 None 400))))
        ($_ (nt:require recording-blocked
              (nt:equal (rhu:problems (rhu:status "{\"last_poll_ok_at\": 999}" "{\"saved_at\": 999, \"loops\": 1}" "{}"
                                                  "{\"schema\": 2, \"in_flight_since\": 100, \"local_commit_pending\": true}"))
                        ("local-completion-blocked"))))
        ($_ (nt:require new-pending-grace
              (nt:equal (rhu:problems (rhu:status "{\"last_poll_ok_at\": 999, \"loop_status\": \"awake\", \"waiting_until\": 0}"
                                                  "{\"saved_at\": 999, \"loops\": 5}" "{}"
                                                  "{\"pending_since\": 995, \"last_completed_at\": 100, \"expected_count\": 2, \"completed_count\": 1}"))
                        ())))
        ($_ (nt:require completed-long-ago-stale
              (nt:equal (rhu:problems (rhu:status "{\"last_poll_ok_at\": 999}" "{\"saved_at\": 999, \"loops\": 5}" "{}"
                                                  "{\"last_completed_at\": 100}"))
                        ("model-turn-stale"))))
        ($_ (nt:require no-receipt-long-after-start
              (nt:equal (rhu:problems (rhu:status "{\"last_poll_ok_at\": 999, \"started_at\": 1}" "{\"saved_at\": 999, \"loops\": 5}" "{}" "{}"))
                        ("model-turn-receipt-missing"))))
        ($_ (nt:require default-idle-needs-no-receipt
              (nt:equal (rhu:problems (rhu:status "{\"last_poll_ok_at\": 999, \"loop_status\": \"waiting\", \"waiting_until\": 1200, \"started_at\": 1}"
                                                  "{\"saved_at\": 999, \"loops\": 0}" "{\"mode\": \"default\", \"autonomy_paused\": false}" "{}"))
                        ())))
        ($_ (nt:require autonomous-rest-not-stale
              (nt:equal (map-atom ("godelclaw" "coding") $mode
                          (rhu:problems (rh:status 1000 (rhu:obj "{\"last_poll_ok_at\": 999, \"loop_status\": \"waiting\", \"waiting_until\": 1200}")
                                                   (rhu:obj "{\"saved_at\": 999, \"loops\": 0}") (JsonObject ((JsonPair "mode" $mode)))
                                                   (rhu:obj "{\"last_completed_at\": 1, \"pending_since\": 0, \"last_outcome\": \"completed\"}")
                                                   True (rhu:none) (RhMemoryV1 "native" True))))
                        (() ()))))
        ($_ (nt:require autonomous-awake-needs-a-turn
              (nt:equal (rhu:problems (rhu:status "{\"last_poll_ok_at\": 999, \"loop_status\": \"awake\"}" "{\"saved_at\": 999, \"loops\": 0}"
                                                  "{\"mode\": \"coding\"}" "{\"last_completed_at\": 1}"))
                        ("model-turn-stale"))))
        ($_ (nt:require stale-setting
              (nt:equal (map-atom ("300" "10" "abc" "1e3" " 45 " "" "29.9") $t (rh:stale-seconds $t)) (300 30 900 1000 45 900 30))))
        ; Python's float() and int() count a Boolean as 1 or 0.
        ($_ (nt:require booleans-as-python-counts-them
              (nt:equal ((rh:stamp (rhu:obj "{\"a\": true}") "a") (rh:count (rhu:obj "{\"a\": true}") "a")
                         (rh:stamp (rhu:obj "{\"a\": false}") "a") (rh:stamp (rhu:obj "{\"a\": \"995\"}") "a")
                         (rh:count (rhu:obj "{\"a\": -3.7}") "a") (rh:count-any (rhu:obj "{\"a\": -3.7}") "a"))
                        (1 1 0 0 0 -3))))
        ($_ (nt:require memory-problems
              (nt:equal (map-atom ((RhMemoryV1 "native" False) (RhMemoryV1 "chroma" False) (RhMemoryV1 "unconfigured" False)) $memory
                          (rhu:problems (rh:status 1000 (rhu:obj "{\"last_poll_ok_at\": 999}") (rhu:obj "{\"saved_at\": 999}") (rhu:none)
                                                   (rhu:none) False (rhu:none) $memory)))
                        (("memory-service-unreachable") ("memory-not-native") ("memory-not-native"))))))
   (rhu:passed UNIT_RUNTIME_HEALTH_STATUS_OK))

; The line, as runtime_health.report formatted it.
!(let* (($status (rh:status 1000 (rhu:obj "{\"last_poll_ok_at\": 995}") (rhu:obj "{\"saved_at\": 999}") (rhu:none)
                            (rhu:obj "{\"schema\": 2, \"last_completed_at\": 997, \"last_settled_at\": 998}") True (rhu:none)
                            (RhMemoryV1 "native" True)))
        ($_ (nt:require report-line
              (nt:equal (rh:report-text $status "running (operator latch)" "abcdef012345" "none")
                        "runtime-health: ok | lifecycle=running (operator latch) | generation=abcdef012345 | telegram=poll-age=5s | loop=unknown | working-set-age=1s | model-turn-age=3s | model-turn-pending=Nones | memory=native | engine-failure=none | problems=none")))
        ($down (rh:status 1000 (rhu:obj "{\"last_poll_ok_at\": 995}") (rhu:obj "{\"saved_at\": 999}") (rhu:none)
                          (rhu:obj "{\"schema\": 2, \"last_completed_at\": 997}") True
                          (rhu:obj "{\"observed_at\": 990, \"channel_ready\": false}") (RhMemoryV1 "native" False)))
        ($_ (nt:require report-line-with-control
              (nt:equal (rh:report-text $down "running (operator latch)" "abcdef012345" "CeTTa process exited (status 7)")
                        "runtime-health: problem | lifecycle=running (operator latch) | generation=abcdef012345 | telegram=service-ready=False, control-age=10s (polling owned by transport) | loop=unknown | working-set-age=1s | model-turn-age=3s | model-turn-pending=Nones | memory=native | engine-failure=CeTTa process exited (status 7) | problems=channel-service-unavailable,memory-service-unreachable"))))
   (rhu:passed UNIT_RUNTIME_HEALTH_REPORT_LINE_OK))

;; ------------------------------------------------------------- reading the records

; The settings and files, as the responder's process reads them: the unit
; runner points every path into this unit's home.
!(let* (($_ (rhu:put "telegram-health.json" "{\"last_poll_ok_at\": 995}"))
        ($_ (rhu:put "working.json" "{\"saved_at\": 999, \"loops\": 12, \"continuation_pending\": true}"))
        ($_ (rhu:put "cognitive.json" "{\"schema\": 2, \"last_completed_at\": 997, \"last_settled_at\": 998, \"last_outcome\": \"completed\"}"))
        ($_ (rhu:put "lifecycle.json" "{\"schema\":1,\"state\":\"running\"}\n"))
        ($_ (rhu:put "memory.json" "{\"mode\": \"native\", \"name\": \"m\", \"socket\": \"no-such-memory.sock\"}"))
        ($_ (rhu:git "0123456789abcdef0123456789abcdef01234567"))
        ($report (rh:report-at 1000))
        ($_ (nt:require report-reads-the-records
              (rhu:has $report "| telegram=poll-age=5s | loop=unknown | working-set-age=1s | model-turn-age=3s | model-turn-pending=Nones | memory=native |")))
        ($_ (nt:require report-unreachable-memory (rhu:has $report "problems=memory-service-unreachable")))
        ($_ (nt:require report-lifecycle (rhu:has $report "lifecycle=running (operator latch)")))
        ($_ (nt:require report-generation (rhu:has $report "generation=0123456789ab |")))
        ; Without a checkout to ask: the deployed commit, cut to 12.
        ($_ (rhu:git ""))
        ($_ (nt:require report-generation-fallback (rhu:has (rh:report-at 1000) "generation=abcdef012345 |")))
        ($_ (nt:require report-no-engine-failure (rhu:has $report "engine-failure=none |")))
        ($_ (rhu:put "engine-failure" "schema=1\nstatus=7\nreason=CeTTa process exited\nreason=CeTTa exited again\n"))
        ($_ (nt:require report-engine-failure (rhu:has (rh:report-at 1000) "engine-failure=CeTTa exited again (status 7) |")))
        ($_ (rhu:put "engine-failure" "status=3\nreason=\n"))
        ($_ (nt:require report-engine-failure-without-reason (rhu:has (rh:report-at 1000) "engine-failure=none |")))
        ($_ (rhu:put "engine-failure" "reason=a=b\r\n"))
        ($_ (nt:require report-engine-failure-default-status (rhu:has (rh:report-at 1000) "engine-failure=a=b (status unknown) |")))
        ; A readiness record beside the engine state replaces the poll.
        ($_ (rhu:put "modes/control-status.json" "{\"observed_at\": 998, \"channel_ready\": true}"))
        ($_ (nt:require report-reads-control
              (rhu:has (rh:report-at 1000) "telegram=service-ready=True, control-age=2s (polling owned by transport)")))
        ($_ (rhu:put "memory.json" "{\"mode\": \"chroma\"}"))
        ($_ (nt:require report-memory-not-native (rhu:has (rh:report-at 1000) "memory=chroma | engine-failure=a=b (status unknown) | problems=memory-not-native")))
        ($activity1 (rh:activity-at 1000 5.0))
        ($_ (nt:require activity-reads-the-records
              (rhu:has $activity1 "activity: working | steps=12@checkpoint | fuel=decay | mode=godelclaw | model=unit-configured-model | wake=now | rest=none | continuation=pending | checkpoint-age=1s | turn=completed:3s-ago")))
        ($_ (nt:require activity-no-progress-receipt (rhu:has $activity1 "progress=unknown")))
        ($_ (rhu:put "telegram-health.client.json" "{\"sleep_until\": 1416.5, \"pending_count\": 4, \"prepared_count\": 0}"))
        ($_ (rhu:put "working.json" "{\"saved_at\": 999, \"loops\": 0, \"intent\": \"quiet integration\"}"))
        ($_ (rhu:put "fuel.json" "{\"fuel\": \"carry\"}"))
        ($_ (rhu:put "mode.json" "{\"mode\": \"coding\"}"))
        ($_ (rhu:put "model-state" "(active-model chosen-model)\n"))
        ($activity2 (rh:activity-at 1000 5.0))
        ($_ (nt:require activity-reads-the-client
              (rhu:has $activity2 "activity: resting | steps=0@checkpoint | fuel=carry | mode=coding | model=chosen-model | wake=timer-or-human | rest=417s-left:quiet integration")))
        ($_ (nt:require activity-reads-the-inputs (rhu:has $activity2 "input=pending:4")))
        ($_ (rhu:put "lifecycle.json" "{\"schema\":1,\"state\":\"stopped\"}\n"))
        ($_ (nt:require activity-reads-the-latch (rhu:has (rh:activity-at 1000 5.0) "activity: stopped | steps=0@operator-latch")))
        ($_ (nt:require report-reads-the-latch (rhu:has (rh:report-at 1000) "lifecycle=stopped (operator latch)")))
        ($_ (rhu:put "lifecycle.json" "{\"schema\":1,\"state\":\"running\"}\n")))
   (rhu:passed UNIT_RUNTIME_HEALTH_RECORDS_OK))

; The progress receipt of a live owner (this process) against its clocks.
!(let* (($_ (rhu:receipt 990 0))
        ($_ (nt:require progress-fresh (nt:equal (rh:progress 1000 5.0) "fresh")))
        ($_ (rhu:receipt 700 0))
        ($_ (nt:require progress-stalled (nt:equal (rh:progress 1000 5.0) "stalled")))
        ($_ (rhu:receipt 700 9.0))
        ($_ (nt:require progress-waiting-on-the-monotonic-deadline (nt:equal (rh:progress 1000 5.0) "waiting")))
        ($_ (nt:require progress-deadline-passed (nt:equal (rh:progress 1000 10.0) "stalled")))
        ($_ (rhu:put "progress.json" "{\"pid\": 1, \"owner\": \"1:1\", \"progress_at\": 990, \"wait_until\": 0}"))
        ($_ (nt:require progress-dead-owner (nt:equal (rh:progress 1000 5.0) "unknown")))
        ($_ (rhu:put "progress.json" "[1]"))
        ($_ (nt:require progress-not-a-receipt (nt:equal (rh:progress 1000 5.0) "unknown")))
        ($_ (rhu:remove "progress.json"))
        ($_ (nt:require progress-missing (nt:equal (rh:progress 1000 5.0) "unknown")))
        ($_ (nt:require pressure-over-high (rh:pressed? (RhMemoryAccountV1 200 100 None))))
        ($_ (nt:require pressure-over-max-without-high (rh:pressed? (RhMemoryAccountV1 200 None 100))))
        ($_ (nt:require no-pressure-below (not (rh:pressed? (RhMemoryAccountV1 50 100 None)))))
        ($_ (nt:require no-pressure-without-limits (not (rh:pressed? (RhMemoryAccountV1 50 None None)))))
        ($_ (nt:require no-pressure-unread (not (rh:pressed? (RhMemoryAccountV1 None 100 100)))))
        ($_ (nt:require no-account-of-no-process (nt:equal (rh:account 0) (RhMemoryAccountV1 None None None)))))
   (rhu:passed UNIT_RUNTIME_HEALTH_PROGRESS_OK))

!(let $blocks (collapse (match &self (rhu:block-passed $block) $block))
   (if (== (size-atom $blocks) 9) (println! UNIT_RUNTIME_HEALTH_OK)
     (println! (UNIT_RUNTIME_HEALTH_INCOMPLETE $blocks))))
