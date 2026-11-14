; Through the real turn: a parked agent with a large bank stays dormant for
; quiet group messages below the count, wakes for a mention or the Nth message
; with at most its sender's allowance, returns to waiting once that is spent,
; banks its heartbeat, keeps resting through a recycle request and a restart,
; and wakes fully for the operator.
; The runner supplies the MeTTa client, a counted group -100 (every 3), the
; bot name GodelTestBot and operator 777 (nr:probe-settings in tests/runtime_probes_native.metta).
!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)
!(import! &self ./tests/scripted_provider.metta)

(= (assertEqual $A $B) (assert (== $A $B)))

;; The model answers ((nop)) unless the next requests' answers are scripted.
!(scripted:default "((nop))")
(= (aw:reply $text $count) (aw:script (+ (scripted:calls) 1) $count $text))
(= (aw:script $n $count $text)
   (if (== $count 0) 1
     (let $_ (scripted:answer $n $text) (aw:script (+ $n 1) (- $count 1) $text))))
;; Unscript the next $count requests' answers.
(= (aw:unscript $count) (aw:unscript-from (+ (scripted:calls) 1) $count))
(= (aw:unscript-from $n $count)
   (if (== $count 0) 1
     (let* (($path (scripted:path (format-args "response-{}.body" ($n))))
            ($_ (if (fs:exists $path) (fs:remove $path) True)))
       (aw:unscript-from (+ $n 1) (- $count 1)))))
!(initMemory)
!(configure commchannel telegram)

(= (aw:turn $p)
   (let* (($_ (working:turn-commit-reset))
          ((success $next) (turnProcess $p))
          ($_ (persistProcessState (rooted weak-process-core-v1 $next)))
          ($_ (if (== (working:turn-commit-ready) 1) (settleActivityBatch) 0))
          ($_ (working:turn-commit-reset)))
     $next))
(= (aw:turns $p $n) (if (== $n 0) $p (aw:turns (aw:turn $p) (- $n 1))))
(= (aw:quiet $uid $text)
   (tg:activity-add-kind "message" "" "-100" $text False "light" $uid False))
(= (aw:mention $uid $text)
   (tg:activity-add-kind (tg:count-kind "message" (json-parse "{}") $text)
                         "" "-100" $text False "light" $uid False))
(= (aw:bank $p) (coordinate-get-or banked-loops 0 $p))
(= (aw:waiting $p) (coordinate-get-or rest-await-input 0 $p))

!(let* (($_ (py-call (loop_modes.set_mode "godelclaw")))
         ($boot (process-periphery (initLoop)))
         ($parked (coordinate-set iteration 5
                    (coordinate-set loops 0
                      (coordinate-set banked-loops 197
                        (coordinate-set rest-await-input 1
                          (coordinate-set sleep-interval 90
                            (coordinate-set last-heartbeat (get_time) $boot)))))))
         ;; A quiet message below the count: no model call, bank untouched.
         ($_ (aw:quiet "9001" "QUIET_ONE"))
         ($p1 (aw:turn $parked))
         ($_ (assertEqual (scripted:calls) 0))
         ($_ (assertEqual (loop-budget $p1) 0))
         ($_ (assertEqual (aw:bank $p1) 197))
         ($_ (assertEqual (tg:pending-count) 1))
         ;; A mention wakes with the light allowance; the batch arrives whole.
         ($_ (aw:mention "9002" "@GodelTestBot MENTION_ONE"))
         ($p2 (aw:turn $p1))
         ($_ (assertEqual (scripted:calls) 1))
         ($_ (assertEqual (scripted:last-prompt-has "QUIET_ONE") 1))
         ($_ (assertEqual (scripted:last-prompt-has "MENTION_ONE") 1))
         ($_ (assertEqual (tg:pending-count) 0))
         ($_ (assertEqual (aw:bank $p2) 187))
         ($_ (assertEqual (aw:waiting $p2) 1))
         ($_ (assertEqual (loop-budget $p2) 0))
         ;; Nothing new: still dormant.
         ($p3 (aw:turn $p2))
         ($_ (assertEqual (scripted:calls) 1))
         ;; A mention that keeps working spends its allowance, then waits again.
         ($_ (aw:reply "((mode))" 20))
         ($_ (aw:mention "9003" "@GodelTestBot MENTION_TWO"))
         ($p4 (aw:turns $p3 14))
         ($used (- (scripted:calls) 1))
         ($_ (println! (ALLOWANCE_TURNS $used)))
         ($_ (assertEqual (<= $used 10) True))
         ($_ (assertEqual (>= $used 9) True))
         ($_ (assertEqual (aw:bank $p4) 177))
         ($_ (assertEqual (loop-budget $p4) 0))
         ($_ (assertEqual (aw:waiting $p4) 1))
         ($_ (assertEqual (>= (loop-wait-sleep $p4) (policy-idle-wait godelclaw)) True))
         ($_ (aw:unscript 20))
         ;; The Nth quiet message wakes with the allowance and brings all N.
         ($before (scripted:calls))
         ($_ (aw:quiet "9004" "QUIET_TWO"))
         ($_ (aw:quiet "9005" "QUIET_THREE"))
         ($p5 (aw:turn $p4))
         ($_ (assertEqual (scripted:calls) $before))
         ($_ (aw:quiet "9006" "QUIET_FOUR"))
         ($p6 (aw:turn $p5))
         ($_ (assertEqual (scripted:calls) (+ $before 1)))
         ($_ (assertEqual (scripted:last-prompt-has "QUIET_TWO") 1))
         ($_ (assertEqual (scripted:last-prompt-has "QUIET_FOUR") 1))
         ($_ (assertEqual (aw:bank $p6) 167))
         ;; The heartbeat banks its renewal without a model call.
         ($count (scripted:calls))
         ($p7 (aw:turn (coordinate-set last-heartbeat 0 $p6)))
         ($_ (assertEqual (scripted:calls) $count))
         ($_ (assertEqual (loop-budget $p7) 0))
         ($_ (assertEqual (aw:waiting $p7) 1))
         ($renewed (aw:bank $p7))
         ($_ (println! (HEARTBEAT_BANK $renewed)))
         ;; A recycle request ends the wait without waking: nothing is handed
         ;; back, so the next process boots still resting.
         ($_ (py-eval "open(__import__('os').environ['METTACLAW_RECYCLE_REQUEST_PATH'], 'w').write('probe')"))
         ((success $p7r) (waitProcess $p7))
         ($_ (persistProcessState (rooted weak-process-core-v1 $p7r)))
         ($_ (py-call (helper.recycle_ack)))
         ($_ (assertEqual (loop-budget $p7r) 0))
         ($_ (assertEqual (aw:bank $p7r) $renewed))
         ($_ (assertEqual (aw:waiting $p7r) 1))
         ;; Across a restart the bank and the activity wait survive. A new
         ;; process boots from the saved working set; initLoop's settings are
         ;; already configured in this one.
         ($_ (working:boot))
         ($restored (coordinate-set iteration 5 (initialPeriphery)))
         ($_ (assertEqual (aw:bank $restored) $renewed))
         ($_ (assertEqual (aw:waiting $restored) 1))
         ($_ (aw:mention "9007" "@GodelTestBot MENTION_THREE"))
         ($p8 (aw:turn $restored))
         ($_ (assertEqual (scripted:calls) (+ $count 1)))
         ($_ (assertEqual (aw:bank $p8) (- $renewed 10)))
         ;; The operator's message hands the whole bank back.
         ($_ (aw:reply "((mode))" 1))
         ($_ (tg:activity-add-kind "message" "" "42" "OPERATOR_ONE" False "full" "9008" "operator message"))
         ($p9 (aw:turn $p8))
         ($_ (assertEqual (scripted:last-prompt-has "OPERATOR_ONE") 1))
         ($_ (assertEqual (aw:bank $p9) 0))
         ($_ (assertEqual (aw:waiting $p9) 0))
         ($_ (assertEqual (>= (loop-budget $p9) (- $renewed 12)) True)))
   (println! ALLOWANCE_WAKE_LOOP_OK))
