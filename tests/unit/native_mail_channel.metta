; The agent's mail channel on a live mail service, through the agent's own
; dispatcher: pending mail, the received form, batch settlement, mail-send,
; waking from a sleep when mail arrives, and pacing when the service is gone.
!(import! &self ../native_test_support.metta)
!(import! &self (library mailbox))
!(import! &self (library list))

(= (mc:wait-file $owner $path $left)
   (if (fs:exists $path) True
     (if (<= $left 0) False
       (progn (proc:wait $owner 10) (mc:wait-file $owner $path (- $left 1))))))
(= (mc:send $socket $id $body)
   (case (mailbox:send $socket "agent-mail" $id "receiver" "sender" $body)
     (($answer (nt:contains (repr $answer) "accepted")))))

(= (mc:program $signal) (str:join "\n" (
  "!(import! &self (library lib_import))"
  "!(import! &self ./lib_mettaclaw)"
  "!(remove-atom &self (= (commchannel) (empty)))"
  "!(add-atom &self (= (commchannel) telegram))"
  "!(assert (mail:pending?))"
  "!(assert (== (channel:sleep-until-message 0 False) \"rested 0s\"))"
  "!(assert (mail:pending?))"
  "!(tg:set last-human 1)"
  "!(assert (== (receive) \"[mail from sender; id=incoming] native λ\"))"
  "!(assert (not (lastMessageIsHuman)))"
  "!(assert (mail:active?))"
  "!(mail:reset)"
  "!(assert (== (settleActivityBatch) 1))"
  "!(assert (mail:pending?))"
  "!(assert (== (receive) \"[mail from sender; id=incoming] native λ\"))"
  "!(println! (run-command-batch-once 717171 99 (quote ((mail-send \"sender\" \"native reply\" \"outgoing\") (mail-receive)))))"
  "!(assert (== (settleActivityBatch) 1))"
  "!(assert (not (mail:active?)))"
  "!(assert (not (mail:pending?)))"
  "!(assert (== (receive) \"\"))"
  (str:concat "!(fs:update-text " (str:concat (json-stringify $signal) " \"write\" \"ready\" \"\" \"\")"))
  "!(assert (== (channel:sleep-until-message 30 True) \"woken by mail\"))"
  "!(assert (== (receive) \"[mail from sender; id=wakeup] native wake\"))"
  "!(assert (== (settleActivityBatch) 1))"
  "!(println! NATIVE_WAKE_OK)"
  "!(remove-atom &self (= (mail:socket) (system:getenv-or-default \"METTACLAW_MAIL_SOCKET\" \"\")))"
  "!(add-atom &self (= (mail:socket) \"missing-native-mail-fixture.socket\"))"
  "!(let* (($before (get_time)) ($_ (mail:idle-wait 25)) ($after (get_time)))"
  "   (progn (println! (NATIVE_PAUSE $before $after (- $after $before))) (assert (>= (- $after $before) 0.020))))"
  "!(println! NATIVE_MAIL_INSTANCE_OK)"
  "")))

!(let* (($home (nt:required-env "HOME")) ($repo (nt:required-env "TEST_REPOSITORY"))
         ($core (nt:required-env "NATIVE_TEST_CORE"))
         ($work (path:join $home "mail")) ($_ (fs:make-directories $work 448))
         ($socket (path:join $work "s.sock"))
         ($service (proc:spawn ((path:join $core "cetta-space-service") "--socket" $socket "--data" (path:join $work "store"))
                               ((env (nt:environment $home)) (max-bytes 65536))))
         ($_ (nt:require mail-service-started (mc:wait-file $service $socket 500)))
         ($_ (nt:require first-mail-accepted (mc:send $socket "incoming" "native λ")))
         ; The program imports the agent from the checkout, so it lives there for the run.
         ($signal (path:join $work "waiting"))
         ($program (path:join $repo (str:concat ".native-mail-channel-" (str:concat (str:byte-slice (system:uuid4-hex) 0 10) ".metta"))))
         ($_ (fs:write $program (mc:program $signal)))
         ($environment (list:append (nt:environment $home)
           (("XDG_STATE_HOME" (path:join $work "state")) ("OPENAI_API_KEY" "test-only-unused")
            ("METTACLAW_ENGINE" "cetta") ("METTACLAW_ACTIVE_ENGINE" "cetta") ("CETTA_BIN" (path:join $core "cetta"))
            ("METTACLAW_SKIP_INITIALIZE" "1") ("METTACLAW_TELEGRAM_CLIENT" "metta") ("METTACLAW_OBSERVED_STRATEGY" "0")
            ("METTACLAW_MAIL_SOCKET" $socket) ("METTACLAW_MAIL_NAME" "receiver") ("METTACLAW_MAIL_SPACE" "agent-mail")
            ("TMPDIR" $work))))
         ($agent (proc:spawn ("bash" "./run.sh" $program)
                             ((cwd $repo) (env $environment) (mode stream) (max-bytes 1048576) (timeout-ms 75000) (lifetime owner))))
         ($_ (proc:close-input $agent))
         ; Mail that arrives while the agent sleeps wakes it.
         ($waiting (mc:wait-file $agent $signal 3000))
         ($_ (proc:wait $agent 150))
         ($woke-sent (if $waiting (mc:send $socket "wakeup" "native wake") False))
         ($result (proc:await $agent)) ($_ (proc:close $agent))
         ($_ (fs:remove $program))
         ($_ (proc:cancel $service)) ($_ (proc:await $service)) ($_ (proc:close $service))
         ($output (case $result (((proc:result (proc:exited $code) $how $out $err $_) (str:concat $out $err)) ($bad (repr $bad)))))
         ($code (case $result (((proc:result (proc:exited $c) $how $out $err $_) $c) ($bad -1))))
         ($_ (if (== $code 0) True (println! (AGENT_OUTPUT $output))))
         ($_ (nt:require the-agent-waited-for-mail $waiting))
         ($_ (nt:require wake-mail-accepted $woke-sent))
         ($_ (nt:require agent-program-completed (== $code 0)))
         ($_ (nt:require mail-sent-through-the-dispatcher (nt:contains $output "MAILED sender id=outgoing")))
         ($_ (nt:require woken-by-mail-during-a-sleep (nt:contains $output "NATIVE_WAKE_OK")))
         ($_ (nt:require every-channel-claim-held (nt:contains $output "NATIVE_MAIL_INSTANCE_OK")))
         ($_ (nt:require no-command-errors
               (and (not (nt:contains $output "COMMAND_ERROR:"))
                    (and (not (nt:contains $output "COMMAND_EXCEPTION:")) (not (nt:contains $output "Assertion failed")))))))
   (println! UNIT_NATIVE_MAIL_CHANNEL_OK))
