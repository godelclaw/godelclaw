; Provider usage accounting on the native ledger, read back through the
; native usage report. Calls accumulate and stay attributable; a completion
; counts once however often it is offered; a missing counter is unknown, not
; zero; a call that failed before submission used nothing, one that failed
; after it is unknown; late usage is recorded without completing a failed
; call; usage outlives the admission window; a dead owner's call stays
; unknown; a local denial is not another provider call; only numeric usage is
; kept; cache reads and writes count once; a bad or huge counter never strands
; a completed reply; the report writes nothing, needs a ledger, prices
; nothing, and adds across windows; old untracked rows weaken coverage only in
; a window that holds them.
;
; Every case runs in its own window of admission time, so the cases share one
; ledger without reading each other's calls.
!(import! &self ../native_test_support.metta)
!(import! &self ../native_ledger_support.metta)
!(import! &self ../../src/llm_policy_cetta.metta)
!(import! &self ../../src/usage_report_cetta.metta)
!(import! &self ../../src/input_ledger.metta)
!(import! &self (library json))

(= (uh:socket) (nls:socket))
!(bind! &uh-ledger (new-state none))
!(bind! &uh-tickets (new-state 0))
(= (uh:start-ledger)
   (let $started (nls:start) (let $_ (change-state! &uh-ledger $started) (nls:up? $started))))

; A provider reply's usage object, from its JSON text.
(= (uh:usage $text) (json:parse $text))
(= (uh:reply $incoming $cached $outgoing)
   (uh:usage (str:join "" ("{\"prompt_tokens\": " (repr $incoming) ", \"completion_tokens\": " (repr $outgoing)
                           ", \"prompt_tokens_details\": {\"cached_tokens\": " (repr $cached) "}}"))))
(= (uh:ticket)
   (let $n (+ 1 (get-state &uh-tickets))
     (let $_ (change-state! &uh-tickets $n) (str:concat "ticket-" (repr $n)))))
(= (uh:call-as $model $provider $purpose $now)
   (let* (($ticket (uh:ticket))
          ($admitted (lp:admit $ticket $provider 100 "" $now)))
     (case $admitted
       (((LedgerAdmitted $t)
         (let $_ (lp:describe $ticket $model "chat-completions" $purpose "direct" $now) $ticket))
        ($refused (Refused $refused))))))
(= (uh:call $now) (uh:call-as "fixture-model" "synthetic" "turn" $now))
(= (uh:report-window $since $until)
   (case (ur:report (uh:socket) $since $until "UTC")
     (((UrOk $value) $value) ($other (Unreported $other)))))
; Case k owns admission times [k * 10^6, (k + 1) * 10^6).
(= (uh:at $case $offset) (+ (* $case 1000000) $offset))
(= (uh:report $case) (uh:report-window (uh:at $case 0) (uh:at (+ $case 1) 0)))
(= (uh:field $object $key $default)
   (let $found (filter-atom (json:object-pairs $object) $pair (case $pair (((JsonPair $k $v) (== $k $key)) ($o False))))
     (if (== $found ()) $default (case (car-atom $found) (((JsonPair $k $v) $v))))))
(= (uh:keys $object) (map-atom (json:object-pairs $object) $pair (case $pair (((JsonPair $k $v) $k)))))
(= (uh:total $report $key) (uh:field (json:get $report "totals") $key Absent))
(= (uh:has-total? $report $key) (not (== (uh:total $report $key) Absent)))
(= (uh:changed? $answer) (== $answer (LedgerChanged True)))
(= (uh:records $kind) (lp:native "llm-records" ($kind)))
(= (uh:request $ticket)
   (let $rows (filter-atom (uh:records "requests") $row
                (case $row (((llm:request $id $a $b $c $d $e $f $g $h $i) (== $id $ticket)) ($o False))))
     (if (== $rows ()) none (car-atom $rows))))
(= (uh:status-and-output $ticket)
   (case (uh:request $ticket) (((llm:request $id $a $b $status $output $e $f $g $h $i) ($status $output)) ($o $o))))
(= (uh:counts $evidence) (json:stringify-python-with (json:get $evidence "counts") ((sort-keys True))))

!(nt:require native-ledger-ready (uh:start-ledger))

; 1. Distinct calls accumulate and stay attributable; nothing is priced.
!(let* (($first (uh:call-as "first" "synthetic" "turn" (uh:at 1 100)))
        ($second (uh:call-as "second" "chatgpt" "coding" (uh:at 1 100)))
        ($_ (lp:complete $second (uh:reply 24 8 6) "stop" (uh:at 1 101)))
        ($_ (lp:complete $first (uh:reply 12 4 3) "stop" (uh:at 1 101)))
        ($report (uh:report 1))
        ($groups (map-atom (json:array-items (json:get $report "groups")) $g
                   ((json:get $g "provider") (json:get $g "model") (json:get $g "purpose")))))
   (nt:require distinct-calls-accumulate-and-remain-attributable
     (and (== (uh:total $report "calls") 2)
     (and (== (uh:total $report "known_input_tokens") 36)
     (and (== (uh:total $report "known_cached_tokens") 12)
     (and (== (uh:total $report "known_output_tokens") 9)
     (and (== (sort-atom $groups) (sort-atom (("chatgpt" "second" "coding") ("synthetic" "first" "turn"))))
     (and (== (json:get $report "monetary_cost") JsonNull)
          (== (json:get $report "billing_reconciled") False)))))))))

; 2. A completion offered again, or with other usage, adds nothing.
!(let* (($ticket (uh:call (uh:at 2 100)))
        ($answers (map-atom (1 2 3) $n (uh:changed? (lp:complete $ticket (uh:reply 12 4 3) "stop" (uh:at 2 101)))))
        ($again (lp:complete $ticket (uh:reply 900 800 700) "stop" (uh:at 2 102)))
        ($report (uh:report 2)))
   (nt:require a-repeated-completion-never-adds-usage-twice
     (and (== $answers (True False False))
     (and (not (uh:changed? $again))
     (and (== (uh:total $report "known_input_tokens") 12)
          (== (uh:total $report "completed_calls") 1))))))

; 3. A missing cache counter leaves the other counts known.
!(let* (($ticket (uh:call (uh:at 3 100)))
        ($_ (lp:complete $ticket (uh:usage "{\"prompt_tokens\": 12, \"completion_tokens\": 3}") "stop" (uh:at 3 101)))
        ($report (uh:report 3)))
   (nt:require a-missing-cache-counter-does-not-erase-known-input
     (and (== (uh:total $report "known_input_tokens") 12)
     (and (== (uh:total $report "known_output_tokens") 3)
          (== (uh:total $report "cached_tokens_unknown_calls") 1)))))

; 4. Failed after submission: unknown. Failed before it: nothing used.
!(let* (($_ (lp:fail (uh:call (uh:at 4 100)) True JsonNull JsonNull (uh:at 4 101)))
        ($_ (lp:fail (uh:call (uh:at 4 100)) False JsonNull JsonNull (uh:at 4 101)))
        ($report (uh:report 4)))
   (nt:require a-timeout-is-unknown-but-unsent-is-zero
     (and (== (uh:total $report "calls") 2)
     (and (== (uh:total $report "not_submitted_calls") 1)
     (and (== (uh:total $report "input_tokens_unknown_calls") 1)
          (== (uh:total $report "known_input_tokens") 0))))))

; 5. Usage that arrives after the call failed is recorded; the call stays failed.
!(let* (($ticket (uh:call (uh:at 5 100)))
        ($_ (lp:fail $ticket True JsonNull JsonNull (uh:at 5 101)))
        ($late (lp:complete $ticket (uh:reply 12 4 3) "stop" (uh:at 5 102)))
        ($report (uh:report 5)))
   (nt:require late-usage-is-recorded-without-completing-a-failed-call
     (and (not (uh:changed? $late))
     (and (== (uh:total $report "failed_calls") 1)
     (and (== (uh:total $report "known_input_tokens") 12)
          (not (uh:has-total? $report "completed_calls")))))))

; 6. A call admitted long after another leaves the first one's usage reported.
!(let* (($old (uh:call (uh:at 6 100)))
        ($_ (lp:complete $old (uh:reply 12 4 3) "stop" (uh:at 6 101)))
        ($fresh (uh:call (uh:at 6 300000)))
        ($_ (lp:complete $fresh (uh:reply 12 4 3) "stop" (uh:at 6 300001))))
   (nt:require usage-survives-the-admission-window
     (== (uh:total (uh:report 6) "known_input_tokens") 24)))

; 7. A call whose owner has exited is recovered as unknown.
!(let* (($ticket (uh:ticket))
        ($_ (lp:native "llm-admit" ($ticket "synthetic" 100 "" "managed" "godelclaw" "0:0" "{}" (uh:at 7 100) (lp:request-id))))
        ($_ (lp:native "llm-recover-requests" ((uh:at 7 101) (lp:request-id))))
        ($report (uh:report 7)))
   (nt:require a-dead-owner-remains-unknown-in-history
     (and (== (uh:total $report "unknown_calls") 1)
          (== (uh:total $report "input_tokens_unknown_calls") 1))))

; 8. A provider cooling down refuses locally, and that is no provider call.
!(let* (($_ (lp:fail (uh:call-as "fixture-model" "cooling" "turn" (uh:at 8 100)) True 429 10 (uh:at 8 100)))
        ($denied (uh:call-as "fixture-model" "cooling" "turn" (uh:at 8 101))))
   (nt:require a-local-denial-does-not-invent-another-provider-call
     (and (case $denied (((Refused (LedgerDenied $why $wait)) True) ($o False)))
          (== (uh:total (uh:report 8) "calls") 1))))

; 9. Only numeric usage fields reach the history.
!(let* (($ticket (uh:call (uh:at 9 100)))
        ($_ (lp:complete $ticket
              (uh:usage "{\"prompt_tokens\": 12, \"completion_tokens\": 3, \"api_key\": \"credential sentinel\", \"content\": \"private prompt sentinel\", \"prompt_tokens_details\": {\"cached_tokens\": 4, \"content\": \"private nested sentinel\"}}")
              "stop" (uh:at 9 101)))
        ($history (repr (uh:records "events"))))
   (nt:require only-numeric-usage-fields-are-retained
     (and (== (uh:total (uh:report 9) "known_input_tokens") 12)
     (and (not (nt:contains $history "credential sentinel"))
     (and (not (nt:contains $history "private prompt sentinel"))
          (not (nt:contains $history "private nested sentinel")))))))

; 10. Anthropic input counts cache reads and writes once each.
!(let* (($usage (uh:usage "{\"input_tokens\": 10, \"cache_read_input_tokens\": 20, \"cache_creation_input_tokens\": 30, \"output_tokens\": 4}"))
        ($evidence (ue:evidence $usage)))
   (nt:require anthropic-input-includes-cache-reads-and-writes-once
     (and (== (uh:counts $evidence) "{\"cached_tokens\": 20, \"input_tokens\": 60, \"output_tokens\": 4}")
          (== (json:stringify-python-with (json:get $evidence "reported_usage") ((sort-keys True)))
              (json:stringify-python-with $usage ((sort-keys True)))))))

; 11. The report keeps cache writes and the trigger apart, inventing no counts.
!(let* (($ticket (uh:ticket))
        ($_ (lp:admit $ticket "anthropic" 100 "" (uh:at 11 100)))
        ($_ (lp:describe-origin $ticket "fixture-model" "anthropic" "turn" "direct"
              (json:parse "{\"trigger\": \"heartbeat-event\", \"turn_id\": \"wake-7\"}") (uh:at 11 100)))
        ($_ (lp:complete $ticket (uh:usage "{\"input_tokens\": 10, \"cache_read_input_tokens\": 20, \"cache_creation_input_tokens\": 30, \"output_tokens\": 4}") "stop" (uh:at 11 101)))
        ($_ (lp:complete (uh:call (uh:at 11 100)) (uh:reply 12 4 3) "stop" (uh:at 11 101)))
        ($report (uh:report 11))
        ($triggers (map-atom (json:array-items (json:get $report "groups")) $g (json:get $g "trigger"))))
   (nt:require the-report-separates-cache-writes-and-trigger
     (and (== (uh:total $report "known_cache_write_tokens") 30)
     (and (== (uh:total $report "cache_write_tokens_unknown_calls") 1)
          (== (sort-atom (unique-atom $triggers)) (sort-atom ("heartbeat-event" "unrecorded")))))))

; 12. Partial usage keeps each known counter, with no cap on a counter.
!(nt:require partial-usage-retains-each-known-counter-without-a-cap
   (and (== (uh:counts (ue:evidence (uh:usage "{\"cache_read_input_tokens\": 20, \"output_tokens\": 4}")))
            "{\"cached_tokens\": 20, \"input_tokens\": null, \"output_tokens\": 4}")
        (== (uh:counts (ue:evidence (uh:usage "{\"prompt_tokens\": 1000000000000000, \"completion_tokens\": 0, \"prompt_tokens_details\": {\"cached_tokens\": 1000000000000000}}")))
            "{\"cached_tokens\": 1000000000000000, \"input_tokens\": 1000000000000000, \"output_tokens\": 0}")))

; 13. A malformed output counter is unknown and still completes the reply.
!(let $stranded
   (filter-atom ("\"abc\"" "\"5_0\"" "\"+5\"" "true" "1.5" "-1" "{}" "[]") $value
     (let* (($ticket (uh:call (uh:at 13 100)))
            ($done (lp:complete $ticket (uh:usage (str:join "" ("{\"prompt_tokens\": 12, \"completion_tokens\": " $value "}"))) "stop" (uh:at 13 101))))
       (not (and (uh:changed? $done) (== (uh:status-and-output $ticket) ("completed" none))))))
   (nt:require a-bad-output-counter-cannot-strand-a-completed-response (== $stranded ())))

; 14. Usage beyond the 64-bit request column is reported exactly.
!(let* (($ticket (uh:call (uh:at 14 100)))
        ($done (lp:complete $ticket (uh:reply 12 4 1000000000000000000000000000000) "stop" (uh:at 14 101))))
   (nt:require exact-usage-is-reported-beyond-the-64-bit-counter
     (and (uh:changed? $done)
     (and (== (repr (uh:total (uh:report 14) "known_output_tokens")) "1000000000000000000000000000000")
          (== (uh:status-and-output $ticket) ("completed" none))))))

; 15. The report writes nothing and needs a ledger.
!(let* (($_ (lp:complete (uh:call (uh:at 15 100)) (uh:reply 12 4 3) "stop" (uh:at 15 101)))
        ($kinds ("requests" "cooldowns" "calls" "results" "events"))
        ($before (map-atom $kinds $kind (uh:records $kind)))
        ($_ (uh:report 15))
        ($after (map-atom $kinds $kind (uh:records $kind))))
   (nt:require the-report-writes-nothing-and-needs-a-ledger
     (and (== $before $after)
          (case (ur:report "" 0 100 "UTC") (((UrError $why) True) ($o False))))))

; 16. Totals over two adjoining windows add to the totals over both.
!(let* (($_ (lp:complete (uh:call (uh:at 16 100)) (uh:reply 12 4 3) "stop" (uh:at 16 101)))
        ; Admitted on the boundary: it counts in exactly one window.
        ($_ (lp:complete (uh:call (uh:at 16 150)) (uh:reply 24 8 6) "stop" (uh:at 16 151)))
        ($first (json:get (uh:report-window (uh:at 16 0) (uh:at 16 150)) "totals"))
        ($second (json:get (uh:report-window (uh:at 16 150) (uh:at 16 300)) "totals"))
        ($whole (json:get (uh:report-window (uh:at 16 0) (uh:at 16 300)) "totals"))
        ($keys (uh:keys $whole)))
   (nt:require the-totals-add-across-time-windows
     (and (== (uh:field $whole "calls" 0) 2)
          (== (filter-atom $keys $key
                (not (== (+ (uh:field $first $key 0) (uh:field $second $key 0)) (json:get $whole $key))))
              ()))))

; 17. A request row without history (one migrated from an older ledger)
; weakens coverage only in a window that holds it: the ledger's own reader.
!(let* (($usage (new-space))
        ($_ (add-atom $usage (llm:event 1 "" "recording-started" 40 "{}")))
        ($_ (add-atom $usage (llm:request "legacy" 50 10 "completed" none "fixture" "godelclaw" none "" "")))
        ($_ (add-atom $usage (llm:request "new" 200 10 "completed" 2 "fixture" "godelclaw" none "" "")))
        ($_ (add-atom $usage (llm:event 2 "new" "admitted" 200 "{}"))))
   (nt:require older-untracked-calls-do-not-poison-a-new-window
     (and (== (repr (llm-ledger:usage-of $usage 150 300)) "(usage 40 0 ((\"new\" \"admitted\" \"{}\" 200)))")
          (== (repr (llm-ledger:usage-of $usage 0 300)) "(usage 40 1 ((\"new\" \"admitted\" \"{}\" 200)))"))))

!(println! UNIT_USAGE_HISTORY_OK)
