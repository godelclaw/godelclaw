; The IRC channel end to end, natively: the channel's service process
; (channels/chat_service_main.metta) against a scripted IRC server
; (tests/fixtures/native_line_peer.c), and the loop's side of the chat spool
; (channels/chat_spool.metta) in this process. No Python.
!(import! &self ../native_test_support.metta)
!(import! &self ../../channels/chat_spool.metta)
!(import! &self ../../src/utils_cetta.metta)

(= (ic:home) (nt:required-env "HOME"))
(= (ic:peer $name) (path:join (path:join (ic:home) "peer") $name))
(= (ic:lines)
   (if (fs:exists (ic:peer "received"))
     (filter-atom (str:split "\n" (fs:read-text (ic:peer "received"))) $line (not (== $line ""))) ()))
(= (ic:say $line) (fs:append-text (ic:peer "inject") (str:concat $line "\n")))
(= (ic:find $lines $prefix)
   (if (== $lines ()) none
     (if (str:starts-with (car-atom $lines) $prefix) (car-atom $lines) (ic:find (cdr-atom $lines) $prefix))))
(= (ic:count $lines $prefix)
   (if (== $lines ()) 0 (+ (if (str:starts-with (car-atom $lines) $prefix) 1 0) (ic:count (cdr-atom $lines) $prefix))))
; Wait until the server has received at least COUNT lines beginning with PREFIX.
(= (ic:heard $prefix $count $left)
   (if (>= (ic:count (ic:lines) $prefix) $count) True
     (if (<= $left 0) False (let $_ (sleep 0.05) (ic:heard $prefix $count (- $left 1))))))
(= (ic:inbox-size) (size-atom (chat:names (chat:inbox irc))))
(= (ic:inbox-has $count $left)
   (if (>= (ic:inbox-size) $count) True
     (if (<= $left 0) False (let $_ (sleep 0.05) (ic:inbox-has $count (- $left 1))))))
(= (ic:file-waits $path $left)
   (if (fs:exists $path) True (if (<= $left 0) False (let $_ (sleep 0.05) (ic:file-waits $path (- $left 1))))))
(= (ic:nick $count) (let $line (ic:nth-nick (ic:lines) $count) (str:byte-slice $line 5 (str:byte-length $line))))
(= (ic:nth-nick $lines $count)
   (if (== $lines ()) "NICK ?"
     (if (str:starts-with (car-atom $lines) "NICK ")
       (if (<= $count 1) (car-atom $lines) (ic:nth-nick (cdr-atom $lines) (- $count 1)))
       (ic:nth-nick (cdr-atom $lines) $count))))
(= (ic:passed $block) (let $_ (add-atom &self (ic:block-passed $block)) (println! $block)))

!(let* (($home (ic:home)) ($root (system:cwd)) ($env (nt:environment $home))
        ($peer (path:join $home "native-line-peer"))
        ($_ (nt:exit-ok (proc:run ("cc" "-std=c11" "-O2" "-Wall" "-Wextra" "-Werror" "tests/fixtures/native_line_peer.c" "-o" $peer)
                                  $root $env 30000 65536)))
        ($_ (fs:make-directories (path:join $home "peer") 448))
        ($server (proc:spawn ($peer (path:join $home "peer")) ((cwd $root) (env $env) (max-bytes 65536))))
        ($_ (add-atom &self (ic:process $server)))
        ($_ (nt:require peer-listens (ic:file-waits (ic:peer "port") 200)))
        ($port (str:trim (fs:read-text (ic:peer "port"))))
        ($service-env (cons-atom ("METTACLAW_CHANNEL" "irc")
                       (cons-atom ("METTACLAW_IRC_SERVER" "127.0.0.1") (cons-atom ("METTACLAW_IRC_PORT" $port)
                       (cons-atom ("METTACLAW_IRC_CHANNEL" "#Test") (cons-atom ("METTACLAW_IRC_NICK" "claw")
                       (cons-atom ("METTACLAW_IRC_RECONNECT_SECONDS" "1")
                       (cons-atom ("METTACLAW_CHAT_SPOOL_ROOT" (path:join $home "chat"))
                       (cons-atom ("METTACLAW_WAKE_REQUEST_PATH" (path:join $home "wake"))
                       (cons-atom ("METTACLAW_ACCESS_POLICY_PATH" (path:join $home "access.metta")) $env))))))))))
        ($service (proc:spawn ((path:join (nt:required-env "NATIVE_TEST_CORE") "cetta") "--lang" "petta" "--import-mode" "ancestor-walk"
                               "--quiet" "channels/chat_service_main.metta")
                              ((cwd $root) (env $service-env) (stdout inherit) (stderr inherit))))
        ($_ (add-atom &self (ic:process $service)))
        ($_ (nt:require registers (and (ic:heard "NICK claw" 1 200) (ic:heard "USER claw" 1 200))))
        ($nick (ic:nick 1))
        ($_ (nt:require nick-has-four-digits (== (str:byte-length $nick) 8)))
        ($_ (nt:require no-join-before-welcome (nt:equal (ic:count (ic:lines) "JOIN") 0)))
        ($_ (ic:say (str:concat ":irc.test 001 " (str:concat $nick " :Welcome"))))
        ($_ (nt:require joins-after-welcome (ic:heard "JOIN #Test" 1 200)))
        ($_ (ic:say "PING :token-1"))
        ($_ (nt:require answers-ping (ic:heard "PONG :token-1" 1 200))))
   (ic:passed UNIT_IRC_SESSION_OK))

; Messages reach the inbox by the access policy; the loop reads and settles them.
!(let* (($nick (ic:nick 1))
        ($_ (ic:say ":alice!a@host PRIVMSG #test :hello there"))
        ($_ (ic:say ":mallory!m@host PRIVMSG #elsewhere :not for us"))
        ($_ (ic:say ":bob!b@host PRIVMSG #Test no colon here"))
        ($_ (ic:say (str:concat ":carol!c@host PRIVMSG " (str:concat $nick " :a direct word"))))
        ($_ (ic:say (str:concat ":dave!d@host PRIVMSG #test :ping " (str:concat $nick ", are you there?"))))
        ($_ (nt:require three-recorded (ic:inbox-has 3 200)))
        ($_ (sleep 0.3))
        ($_ (nt:require nothing-else-recorded (nt:equal (ic:inbox-size) 3)))
        ($pending (chat:pending irc))
        ($_ (nt:require notes-and-labels
              (nt:equal (map-atom $pending $m (let ($name $id $sender $text $note $provenance) $m ($sender $note $provenance)))
                        (("alice" "quiet" "untrusted stranger") ("carol" "addressed" "untrusted stranger")
                         ("dave" "addressed" "untrusted stranger")))))
        ($_ (nt:require delivery-identities
              (nt:equal (map-atom $pending $m (let ($name $id $sender $text $note $provenance) $m (str:starts-with $id "irc:"))) (True True True))))
        ($read (chat:receive irc))
        ($_ (nt:require the-turn-reads-every-message
              (nt:equal $read (str:join "" ("alice (untrusted stranger): hello there | carol (untrusted stranger): a direct word | dave (untrusted stranger): ping "
                                            $nick ", are you there?")))))
        ($_ (nt:require read-is-not-settled (nt:equal (ic:inbox-size) 3)))
        ($_ (nt:require read-again-until-settled (nt:equal (chat:receive irc) $read)))
        ($_ (nt:require settled (nt:equal ((chat:ack irc) (ic:inbox-size) (chat:presented)) (1 0 ()))))
        ($_ (nt:require nothing-to-read (nt:equal (chat:receive irc) ""))))
   (ic:passed UNIT_IRC_INBOX_OK))

; Sends: one PRIVMSG a line, long lines cut, the file gone once sent.
!(let* (($long (str:join "" (map-atom (1 2 3 4 5 6 7 8 9) $k "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmnopqrstuvwxyzABCD")))
        ($_ (nt:require queued (str:starts-with (chat:send irc "first line\nsecond line\\nthird\n\n") "durably queued: irc.")))
        ($_ (chat:send irc $long))
        ($_ (nt:require lines-sent
              (and (ic:heard "PRIVMSG #Test :first line" 1 200)
                   (and (ic:heard "PRIVMSG #Test :second line" 1 200) (ic:heard "PRIVMSG #Test :third" 1 200)))))
        ($_ (nt:require long-text-cut (ic:heard "PRIVMSG #Test :" 6 200)))
        ($_ (nt:require no-empty-line (nt:equal (filter-atom (ic:lines) $line (== $line "PRIVMSG #Test :")) ())))
        ($_ (nt:require every-piece-fits
              (nt:equal (filter-atom (ic:lines) $line (> (str:byte-length $line) (+ 400 (str:byte-length "PRIVMSG #Test :")))) ())))
        ($_ (sleep 0.3))
        ($_ (nt:require outbox-empty (nt:equal (chat:names (chat:outbox irc)) ()))))
   (ic:passed UNIT_IRC_SEND_OK))

; Wake notes are configurable: a facts file makes the channel's messages wake the agent.
!(let* (($_ (fs:write (path:join (ic:home) "access.metta")
              "(rule-for (channel irc) groups listed (rule record wake-always (reason \"irc message\") tier-default))\n"))
        ($_ (ic:say ":erin!e@host PRIVMSG #test :wake up"))
        ($_ (nt:require sender-wake-requested (ic:file-waits (path:join (ic:home) "wake") 200)))
        ($_ (nt:require with-its-reason (nt:equal (fs:read-text (path:join (ic:home) "wake")) "irc message")))
        ($_ (nt:require recorded-as-sender-wake
              (nt:equal (map-atom (chat:pending irc) $m (let ($name $id $sender $text $note $provenance) $m $note)) ("sender-wake"))))
        ($_ (chat:receive irc)) ($_ (chat:ack irc)))
   (ic:passed UNIT_IRC_WAKE_OK))

; A dropped connection: the service connects again and registers anew.
!(let* (($_ (fs:write (ic:peer "drop") ""))
        ($_ (nt:require registers-again (ic:heard "NICK claw" 2 400)))
        ($nick (ic:nick 2))
        ($_ (chat:send irc "after the reconnect"))
        ($_ (sleep 0.5))
        ($_ (nt:require holds-sends-until-joined (nt:equal (ic:count (ic:lines) "PRIVMSG #Test :after the reconnect") 0)))
        ($_ (ic:say (str:concat ":irc.test 001 " (str:concat $nick " :Welcome"))))
        ($_ (nt:require joins-again (ic:heard "JOIN #Test" 2 200)))
        ($_ (nt:require held-send-goes (ic:heard "PRIVMSG #Test :after the reconnect" 1 200))))
   (ic:passed UNIT_IRC_RECONNECT_OK))

!(let $processes (collapse (match &self (ic:process $p) $p))
   (map-atom $processes $p (let* (($_ (proc:cancel $p)) ($_ (proc:await $p))) (proc:close $p))))
!(nt:require every-block-ended
   (nt:equal (size-atom (collapse (match &self (ic:block-passed $block) $block))) 5))
!(println! UNIT_IRC_CHANNEL_OK)
