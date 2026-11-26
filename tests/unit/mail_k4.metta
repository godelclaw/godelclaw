; Agent mail (K4): a crash between a reply and the acknowledgement redelivers
; the mail and the recipient still holds one reply; a different reply to a
; redelivered mail is refused as already answered; replies in one turn get
; distinct derived ids while unprompted mail gets fresh ones; automatic mail is
; marked, and the skill text says to answer it only when useful.
!(import! &self ../native_test_support.metta)
!(import! &self (library mailbox))
!(import! &self (library list))

; A reply's id: the first 32 hex digits of SHA-256 over the incoming id, the
; agent and the reply's ordinal, each string prefixed with its byte length.
(= (k4:reply-id $incoming $agent $ordinal)
   (str:byte-slice (str:sha256 (str:join "" ((repr (str:byte-length $incoming)) ":" $incoming ":"
                                             (repr (str:byte-length $agent)) ":" $agent ":" (repr $ordinal))))
                   0 32))
(= (k4:hex32? $id) (and (== (str:byte-length $id) 32) (== (filter-atom (str:python-codepoints $id) $p
                          (not (or (and (>= $p 48) (<= $p 57)) (and (>= $p 97) (<= $p 102))))) ())))
(= (k4:member? $x $xs) (if (== $xs ()) False (if (== (car-atom $xs) $x) True (k4:member? $x (cdr-atom $xs)))))
(= (k4:wait-file $owner $path $left)
   (if (fs:exists $path) True (if (<= $left 0) False (progn (proc:wait $owner 20) (k4:wait-file $owner $path (- $left 1))))))
(= (k4:send $socket $id $body)
   (nt:contains (repr (mailbox:send $socket "agent-mail" $id "receiver" "sender" $body)) "accepted"))
; Every delivery waiting for the sender, acknowledged as it is read: (id body) pairs.
(= (k4:drain $socket $acc)
   (case (mailbox:next $socket "agent-mail" "sender")
     (((mailbox:delivery $id $from $body)
       (let $_ (mailbox:ack $socket "agent-mail" "sender" $id) (k4:drain $socket (cons-atom ($id $body) $acc))))
      ($idle (list:reverse $acc)))))
(= (k4:environment $home $work $socket)
   (list:append (("XDG_STATE_HOME" (path:join $work "state")) ("OPENAI_API_KEY" "test-only-unused")
                 ("METTACLAW_ENGINE" "cetta") ("METTACLAW_ACTIVE_ENGINE" "cetta")
                 ("CETTA_BIN" (path:join (nt:required-env "NATIVE_TEST_CORE") "cetta"))
                 ("METTACLAW_SKIP_INITIALIZE" "1") ("METTACLAW_SKIP_CONFIG" "1") ("METTACLAW_TELEGRAM_CLIENT" "metta")
                 ("METTACLAW_OBSERVED_STRATEGY" "0") ("METTACLAW_MAIL_SOCKET" $socket) ("METTACLAW_MAIL_NAME" "receiver")
                 ("METTACLAW_MAIL_SPACE" "agent-mail") ("TMPDIR" $work))
                (nt:environment $home)))
(= (k4:header) "!(import! &self (library lib_import))\n!(import! &self ./lib_mettaclaw)\n!(remove-atom &self (= (commchannel) (empty)))\n!(add-atom &self (= (commchannel) telegram))\n")
; A program file in the checkout, which the agent imports from.
(= (k4:program $repo $code)
   (let* (($name (str:concat ".k4-probe-" (str:concat (str:byte-slice (system:uuid4-hex) 0 12) ".metta")))
          ($path (path:join $repo $name))
          ($written (fs:write $path (str:concat (k4:header) $code))))
     (if (fs:is-file $path) $path (progn (println! (K4_PROGRAM_UNWRITTEN $written (get-type $code))) $path))))
; Run an agent program to completion: its output, or a failure.
(= (k4:agent $repo $env $code)
   (let* (($path (k4:program $repo $code))
          ($result (proc:run ("bash" "./run.sh" $path) $repo $env 60000 4194304))
          ($_ (fs:remove $path)))
     (case $result (((proc:exited 0 $out $err) $out) ($bad (progn (println! (K4_AGENT_FAILED $bad)) (Failed $bad)))))))
(= (k4:quoted $text) (json-stringify (JsonString $text)))
(= (k4:service $core $work $socket)
   (proc:spawn ((path:join $core "cetta-space-service") "--socket" $socket "--data" (path:join $work "store"))
               ((env (nt:environment $work)) (max-bytes 65536))))
(= (k4:stop $service) (progn (proc:cancel $service) (proc:await $service) (proc:close $service)))
(= (k4:body-of $deliveries $body $default)
   (if (== $deliveries ()) $default
     (let ($id $b) (car-atom $deliveries) (if (== $b $body) $id (k4:body-of (cdr-atom $deliveries) $body $default)))))
; The automatic-mail program: two marked mails, the unmarked ones, the skill text.
(= (k4:send-form $socket $id $body)
   (str:join "" ("!(mailbox:send " (k4:quoted $socket) " \"agent-mail\" " (k4:quoted $id) " \"receiver\" \"sender\" " (k4:quoted $body) ")\n")))
(= (k4:receive-form $label $id $body)
   (str:join "" ("!(assert (== (receive) " (k4:quoted (str:join "" ("[" $label " from sender; id=" $id "] " $body))) "))\n")))
(= (k4:unmarked-forms $socket $cases)
   (if (== $cases ()) ""
     (let ($id $body) (car-atom $cases)
       (str:join "" ((k4:send-form $socket $id $body) (k4:receive-form "mail" $id $body)
                     "!(assert (== (settleActivityBatch) 1))\n" (k4:unmarked-forms $socket (cdr-atom $cases)))))))
(= (k4:auto-code $socket $gateway $nostr $unmarked)
   (str:join "" ("!(import! &self (library mailbox))\n"
                 (k4:send-form $socket "mark-gw-1" $gateway) (k4:receive-form "automatic mail" "mark-gw-1" $gateway)
                 "!(assert (== (settleActivityBatch) 1))\n"
                 (k4:send-form $socket "mark-nostr-1" $nostr) (k4:receive-form "automatic mail" "mark-nostr-1" $nostr)
                 "!(println! (AUTO_REPLY (mail-send \"sender\" \"useful follow-up\")))\n"
                 "!(assert (== (settleActivityBatch) 1))\n"
                 (k4:unmarked-forms $socket $unmarked)
                 "!(println! (SKILLS_TEXT (repr (getLegacySkills))))\n!(println! (MAIL_HELP (help \"mail-send\")))\n!(println! K4_MARK_OK)\n")))
; A fresh mail world: (work socket service env).
(= (k4:world $home $tag)
   (let* (($work (path:join $home $tag)) ($_ (fs:make-directories $work 448)) ($socket (path:join $work "s.sock"))
          ($service (k4:service (nt:required-env "NATIVE_TEST_CORE") $work $socket))
          ($_ (k4:wait-file $service $socket 500)))
     ($work $socket $service (k4:environment $home $work $socket))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
         ; A crash between the reply and the acknowledgement.
         (($crash $crash-socket $crash-service $crash-env) (k4:world $home "crash"))
         ($_ (nt:require crash-mail-accepted (k4:send $crash-socket "inc-crash-1" "please inspect")))
         ($flag (path:join $crash "replied.flag")) ($pidfile (path:join $crash "agent.pid"))
         ($crash-program (k4:program $repo (str:join "" (
           "!(let $msg (receive)\n"
           "   (let $_ (println! (RECEIVED_FIRST $msg))\n"
           "     (let $sent (run-command-batch-once 101 1 (quote ((mail-send \"sender\" \"deterministic answer\"))))\n"
           "       (let $_ (println! (SENT_FIRST $sent))\n"
           "         (let $_ (fs:update-text " (k4:quoted $flag) " \"write\" \"1\" \"\" \"\")\n"
           "           (mail:pause 30000))))))\n"))))
         ($crashing (proc:spawn ("sh" "-c" "echo $$ > \"$1\"; exec bash ./run.sh \"$2\"" "agent" $pidfile $crash-program)
                                ((cwd $repo) (env $crash-env) (mode stream) (max-bytes 1048576) (timeout-ms 60000) (lifetime owner))))
         ($_ (proc:close-input $crashing))
         ($replied (k4:wait-file $crashing $flag 1500))
         ($_ (proc:run ("sh" "-c" "kill -KILL -- -\"$(cat \"$1\")\"" "kill" $pidfile) $crash inherit 5000 64))
         ($_ (proc:await $crashing)) ($_ (proc:close $crashing)) ($_ (fs:remove $crash-program))
         ($_ (nt:require the-first-reply-was-sent-before-the-crash $replied))
         ($second (k4:agent $repo $crash-env (str:join "" (
           "!(let $msg (receive)\n"
           "   (let $_ (println! (RECEIVED_SECOND $msg))\n"
           "     (let $sent (run-command-batch-once 102 1 (quote ((mail-send \"sender\" \"deterministic answer\"))))\n"
           "       (let $_ (println! (SENT_SECOND $sent))\n"
           "         (assert (== (settleActivityBatch) 1))))))\n"
           "!(assert (not (mail:pending?)))\n!(println! CRASH_RECOVERY_OK)\n"))))
         ($_ (nt:require the-unacknowledged-mail-is-redelivered-and-settled
               (and (== (get-type $second) String) (and (nt:contains $second "CRASH_RECOVERY_OK") (nt:contains $second "id=inc-crash-1")))))
         ($crash-replies (k4:drain $crash-socket ()))
         ($_ (k4:stop $crash-service))
         ($_ (nt:require the-recipient-holds-exactly-one-reply
               (== $crash-replies (((k4:reply-id "inc-crash-1" "receiver" 1) "deterministic answer")))))
         ; A different reply to a redelivered mail is refused.
         (($conflict $conflict-socket $conflict-service $conflict-env) (k4:world $home "conflict"))
         ($_ (nt:require conflict-mail-accepted (k4:send $conflict-socket "inc-conflict-1" "question?")))
         ($first (k4:agent $repo $conflict-env "!(let $msg (receive)\n   (println! (FIRST_SEND (mail-send \"sender\" \"first wording\"))))\n"))
         ($_ (nt:require the-first-wording-is-mailed (and (== (get-type $first) String) (nt:contains $first "MAILED sender id="))))
         ($again (k4:agent $repo $conflict-env (str:join "" (
           "!(let $msg (receive)\n"
           "   (let $res (mail-send \"sender\" \"different wording after crash\")\n"
           "     (let $_ (println! (SECOND_RESULT $res))\n"
           "       (assert (== (settleActivityBatch) 1)))))\n"))))
         ($_ (nt:require a-different-reply-is-already-answered (and (== (get-type $again) String) (nt:contains $again "already answered"))))
         ($conflict-replies (k4:drain $conflict-socket ()))
         ($_ (k4:stop $conflict-service))
         ($_ (nt:require the-first-reply-stands
               (and (== (size-atom $conflict-replies) 1) (let ($cid $cbody) (car-atom $conflict-replies) (== $cbody "first wording")))))
         ; Ordinals: two replies in one turn, and fresh ids for unprompted mail.
         (($ordinal $ordinal-socket $ordinal-service $ordinal-env) (k4:world $home "ordinal"))
         ($_ (nt:require ordinal-mail-accepted (k4:send $ordinal-socket "inc-ord-1" "two-part request")))
         ($_ (k4:agent $repo $ordinal-env (str:join "" (
           "!(let $msg (receive)\n"
           "   (let $r1 (mail-send \"sender\" \"part one\")\n"
           "     (let $r2 (mail-send \"sender\" \"part two\")\n"
           "       (let $_ (println! (ORDINAL_REPLIES $r1 $r2))\n"
           "         (assert (== (settleActivityBatch) 1))))))\n"
           "!(let $u1 (mail-send \"sender\" \"unprompted one\")\n"
           "   (let $u2 (mail-send \"sender\" \"unprompted two\")\n"
           "     (println! (UNPROMPTED_SENDS $u1 $u2))))\n"))))
         ($sent (k4:drain $ordinal-socket ()))
         ($_ (k4:stop $ordinal-service))
         ($id1 (k4:reply-id "inc-ord-1" "receiver" 1)) ($id2 (k4:reply-id "inc-ord-1" "receiver" 2))
         ($u1 (k4:body-of $sent "unprompted one" "")) ($u2 (k4:body-of $sent "unprompted two" ""))
         ($_ (nt:require replies-in-one-turn-get-their-ordinal-ids
               (and (== (size-atom $sent) 4)
                    (and (== (k4:body-of $sent "part one" "") $id1) (and (== (k4:body-of $sent "part two" "") $id2) (not (== $id1 $id2)))))))
         ($_ (nt:require unprompted-mail-gets-fresh-ids
               (and (and (k4:hex32? $u1) (k4:hex32? $u2))
                    (and (not (== $u1 $u2)) (and (not (k4:member? $u1 ($id1 $id2))) (not (k4:member? $u2 ($id1 $id2))))))))
         ; Automatic mail is marked, and only a header block marks it.
         (($auto $auto-socket $auto-service $auto-env) (k4:world $home "auto"))
         ($gateway "Auto-Submitted: auto-generated\n\nSent to alpha (<id@nostr>).")
         ($nostr (str:join "" ("From: alpha <" (str:join "" (list:repeat 64 "a")) "@nostr>\r\n"
                               "Auto-Submitted: Auto-Replied; reason=status\r\nSubject: Re: status\r\n\r\nAll relays connected.")))
         ($unmarked (("unmark-no" "Auto-Submitted: NO\n\nHuman mail") ("unmark-nonhdr" "Hello there\nAuto-Submitted: auto-generated\n\nBody")
                     ("unmark-space" "Quick note: check this\nAuto-Submitted: auto-generated\n\nBody")
                     ("unmark-body" "Subject: hi\n\nAuto-Submitted: auto-generated\n\nBody") ("unmark-noblank" "Auto-Submitted: auto-generated")))
         ($marked (k4:agent $repo $auto-env (k4:auto-code $auto-socket $gateway $nostr $unmarked)))
         ($auto-replies (k4:drain $auto-socket ()))
         ($_ (k4:stop $auto-service))
         ($_ (nt:require automatic-mail-is-marked-and-only-by-a-header-block
               (and (== (get-type $marked) String) (nt:contains $marked "K4_MARK_OK"))))
         ($_ (nt:require the-skills-say-to-answer-automatic-mail-only-when-useful
               (and (nt:contains $marked "[automatic mail from ...]") (nt:contains $marked "information; answer it only when"))))
         ($_ (nt:require automatic-mail-stays-answerable
               (== $auto-replies (((k4:reply-id "mark-nostr-1" "receiver" 1) "useful follow-up"))))))
   (println! UNIT_MAIL_K4_OK))
