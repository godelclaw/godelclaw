; The IRC channel's own service: it keeps the connection to the server, so the
; server's PING is answered while the loop waits on a model call, and it
; carries messages both ways through the chat spool (channels/chat_spool.metta).
;
; - It registers with NICK and USER as METTACLAW_IRC_NICK and four digits
;   (others when the server says the nick is taken), and joins
;   METTACLAW_IRC_CHANNEL once the server welcomes it (001).
; - Every PRIVMSG to the channel or to the bot is decided by the access policy,
;   (ap:decide irc NICK GROUP MENTIONED): GROUP the channel in lowercase, or ""
;   for a direct message; MENTIONED when it is direct or names the bot. A
;   recorded one goes to the inbox as "irc:<uuid>".
; - A send of the outbox goes to the channel as PRIVMSG lines, one a line of
;   its text, each at most METTACLAW_IRC_LINE_BYTES (400) bytes, so no text can
;   end the command early or carry another. Its file goes once every line is
;   written; a connection lost before that sends it again whole.
; - A connection that has heard nothing for METTACLAW_IRC_QUIET_SECONDS (240) is
;   asked with a PING, and closed after twice that. A closed or failed
;   connection is opened again after METTACLAW_IRC_RECONNECT_SECONDS (10).
;
; The server is METTACLAW_IRC_SERVER (irc.quakenet.org) on METTACLAW_IRC_PORT
; (6667), over TLS when METTACLAW_IRC_TLS is set (1, true). Run by
; `run.sh chat-channel`; config/default.toml's [irc] sets these.
!(import! &self (library io))
!(import! &self (library str))
!(import! &self (library system))
!(import! &self ./channels/chat_spool.metta)
!(import! &self ./channels/access_policy.metta)
!(import! &self ./src/utils_cetta.metta)

!(bind! &irc-state (new-space))
(= (irc:get $kind $default)
   (case (collapse (match &irc-state (IrcState $kind $value) $value)) ((() $default) ($values (car-atom $values)))))
(= (irc:set $kind $value)
   (let $_ (collapse (match &irc-state (IrcState $kind $old) (remove-atom &irc-state (IrcState $kind $old))))
     (add-atom &irc-state (IrcState $kind $value))))

(= (irc:env $name $default) (system:getenv-or-default $name $default))
(= (irc:number $name $default)
   (case (catch (str:string->number (irc:env $name "")))
     (((Error $where $why) $default) ($n (if (> $n 0) $n $default)))))
(= (irc:url)
   (str:join "" ((if (irc:truthy? (irc:env "METTACLAW_IRC_TLS" "0")) "tls://" "tcp://")
                 (irc:env "METTACLAW_IRC_SERVER" "irc.quakenet.org") ":" (irc:env "METTACLAW_IRC_PORT" "6667"))))
(= (irc:truthy? $text) (case (str:lower (str:trim $text)) (("1" True) ("true" True) ("yes" True) ("on" True) ($other False))))
(= (irc:channel) (irc:env "METTACLAW_IRC_CHANNEL" "##metta"))
(= (irc:base-nick) (irc:env "METTACLAW_IRC_NICK" "maxbotnick"))
(= (irc:new-nick) (str:concat (irc:base-nick) (repr (+ 1000 (% (system:monotonic-ns) 9000)))))
(= (irc:line-bytes) (irc:number "METTACLAW_IRC_LINE_BYTES" 400))
(= (irc:tick-ms) 5000)
(= (irc:log $text) (system:write stderr (str:join "" ("[irc] " $text "\n"))))

;; ------------------------------------------------------------ the connection

(= (irc:connect)
   (case (catch (io:submit (tcp:connect (irc:url) 30000 (irc:number "METTACLAW_IRC_MAX_LINE_BYTES" 16384) lines)))
     (((io:pending $id)
       (case (io:wait $id 35000)
         (((io:event $done (tcp:connected $conn)) (IrcConnected $conn))
          ((io:idle) (let $_ (catch (io:cancel $id)) (IrcFailed "no connection within 35 s")))
          ($outcome (IrcFailed (repr $outcome))))))
      ($refused (IrcFailed (repr $refused))))))
(= (irc:receive $conn)
   (case (catch (io:submit (tcp:receive $conn))) (((io:pending $id) $id) ($refused none))))
(= (irc:send-line $conn $line)
   (case (catch (io:submit (tcp:send $conn (str:concat $line "\r\n"))))
     (((io:pending $id) (case (io:wait $id 30000) (((io:event $done (tcp:sent $sent)) True) ($outcome False))))
      ($refused False))))
(= (irc:close $conn $why)
   (let* (($_ (irc:log $why))
          ($_ (case (catch (io:submit (tcp:close $conn))) (((io:pending $id) (io:wait $id 5000)) ($refused ())))))
     done))
(= (irc:ended $why) (let $_ (irc:log $why) done))

; Forever: a session, then a pause before the next.
(= (irc:serve)
   (let* (($_ (once (irc:session)))
          ($_ (sleep (irc:number "METTACLAW_IRC_RECONNECT_SECONDS" 10))))
     (irc:serve)))
(= (irc:session)
   (case (irc:connect)
     (((IrcConnected $conn)
       (let* (($nick (irc:new-nick))
              ($_ (irc:set nick $nick)) ($_ (irc:set joined False)) ($_ (irc:heard))
              ($_ (irc:send-line $conn (str:concat "NICK " $nick)))
              ($_ (irc:send-line $conn (format-args "USER {} 0 * :{}" ($nick $nick)))))
         (irc:run (irc:next $conn (irc:receive $conn)))))
      ((IrcFailed $why) (irc:ended (str:concat "connect failed: " $why))))))
(= (irc:next $conn $receive)
   (if (== $receive none) (irc:close $conn "the connection refused a receive") (IrcSession $conn $receive)))
(= (irc:run $session)
   (if (== $session done) done
     (irc:run (once (irc:step $session)))))

; One step: wait for a line, a change of the outbox or the tick; then act.
(= (irc:step (IrcSession $conn $receive))
   (let* (($watch (irc:watch))
          ($_ (io:wait-ready (cons-atom io:ids (irc:handles (cons-atom $receive (cons-atom $watch ())))) (irc:tick-ms)))
          ($_ (irc:finish-watch $watch)))
     (case (io:poll $receive)
       (((io:event $id (tcp:received $c $line))
         (let* (($_ (irc:heard)) ($kept (irc:line $conn $line)))
           (if (== $kept closed) (irc:close $conn "the server ended the session")
             (let $_ (irc:flush $conn) (irc:next $conn (irc:receive $conn))))))
        ((io:event $id (tcp:closed $c $reason)) (irc:ended (str:concat "connection closed: " $reason)))
        ((io:event $id (tcp:failed $c $code $text)) (irc:ended (str:concat "connection failed: " $text)))
        ((io:idle)
         (if (irc:dead? $conn) (let $_ (catch (io:cancel $receive)) (irc:close $conn "the server stopped answering"))
           (let $_ (irc:flush $conn) (IrcSession $conn $receive))))
        ($other (irc:ended (repr $other)))))))
(= (irc:handles $candidates)
   (if (== $candidates ()) ()
     (let* (($head (car-atom $candidates)) ($rest (irc:handles (cdr-atom $candidates))))
       (if (== $head none) $rest (cons-atom $head $rest)))))
(= (irc:watch)
   (let $folder (chat:outbox irc)
     (if (== $folder "") none
       (let $_ (fs:make-directories $folder 448)
         (case (catch (io:submit (fs:changed $folder))) (((io:pending $id) $id) ($refused none)))))))
(= (irc:finish-watch $watch)
   (if (== $watch none) ()
     (case (io:poll $watch) (((io:idle) (catch (io:cancel $watch))) ($done ())))))

; Liveness: the time of the last line heard; a PING after a quiet while.
(= (irc:heard) (let $_ (irc:set pinged False) (irc:set heard (system:monotonic-ns))))
(= (irc:dead? $conn)
   (let* (($quiet (/ (- (system:monotonic-ns) (irc:get heard 0)) 1000000000.0))
          ($limit (irc:number "METTACLAW_IRC_QUIET_SECONDS" 240)))
     (if (> $quiet (* 2 $limit)) True
       (let $_ (if (and (> $quiet $limit) (not (irc:get pinged False)))
                 (let $_ (irc:set pinged True) (irc:send-line $conn (str:concat "PING :" (irc:get nick ""))))
                 ())
         False))))

;; ------------------------------------------------------------ what the server says

; A line from the server; closed when it ends the session.
(= (irc:line $conn $line)
   (if (str:starts-with $line "PING") (irc:send-line $conn (str:concat "PONG" (str:byte-slice $line 4 (str:byte-length $line))))
     (if (str:starts-with $line "ERROR") closed
       (if (not (str:starts-with $line ":")) ignored
         (case (irc:split-once (str:byte-slice $line 1 (str:byte-length $line)) " ")
           ((($prefix $rest)
             (case (irc:split-once $rest " ")
               ((($command $params) (irc:command $conn $prefix $command $params))
                ($bare ignored))))
            ($no-command ignored)))))))
; TEXT cut at the first SEPARATOR: (BEFORE AFTER), or none.
(= (irc:split-once $text $separator)
   (case (str:find $text $separator)
     (((TextNotFound $needle) none)
      ($at (let* (($before (str:byte-slice $text 0 $at))
                  ($after (str:byte-slice $text (+ $at (str:byte-length $separator)) (str:byte-length $text))))
             ($before $after))))))
(= (irc:command $conn $prefix $command $params)
   (case $command
     (("001" (let* (($_ (irc:set joined True))) (irc:send-line $conn (str:concat "JOIN " (irc:channel)))))
      ("433" (let $nick (irc:new-nick) (let $_ (irc:set nick $nick) (irc:send-line $conn (str:concat "NICK " $nick)))))
      ("PRIVMSG"
       (case (irc:split-once $params " :")
         ((($target $text) (irc:privmsg $prefix $target $text))
          ($malformed ignored))))
      ($other ignored))))

; A message for the channel or for the bot, decided and recorded.
(= (irc:privmsg $prefix $target $text)
   (let* (($nick (car-atom (str:split "!" $prefix)))
          ($me (irc:get nick ""))
          ($direct (== (str:lower $target) (str:lower $me)))
          ($group (if $direct "" (str:lower $target)))
          ($mentioned (or $direct (or (irc:names? $text $me) (irc:names? $text (irc:base-nick)))))
          ($decision (ap:decide irc $nick $group $mentioned)))
     (chat:record irc (str:concat "irc:" (system:uuid4-hex)) $nick $text $decision)))
(= (irc:names? $text $name)
   (if (== $name "") False
     (case (str:find (str:lower $text) (str:lower $name)) (((TextNotFound $needle) False) ($at True)))))

;; ------------------------------------------------------------ the outbox

; Once joined, every waiting send goes, oldest first, while the lines go out.
(= (irc:flush $conn)
   (if (not (irc:get joined False)) not-joined (irc:flush-sends $conn (chat:sends irc))))
(= (irc:flush-sends $conn $sends)
   (if (== $sends ()) flushed
     (let ($name $key $text) (car-atom $sends)
       (if (irc:send-lines $conn (irc:lines $text))
         (let $_ (chat:sent irc $name) (irc:flush-sends $conn (cdr-atom $sends)))
         interrupted))))
(= (irc:send-lines $conn $lines)
   (if (== $lines ()) True
     (if (irc:send-line $conn (format-args "PRIVMSG {} :{}" ((irc:channel) (car-atom $lines))))
       (irc:send-lines $conn (cdr-atom $lines))
       False)))
; The PRIVMSG texts of a send: its lines (a written "\n" counts as one), CR
; dropped, empty lines left out, each cut to at most the line bytes.
(= (irc:lines $text)
   (irc:pieces (str:split "\n" (string-replace (string-replace $text "\\n" "\n") "\r" ""))))
(= (irc:pieces $lines)
   (if (== $lines ()) ()
     (let* (($line (car-atom $lines)) ($rest (irc:pieces (cdr-atom $lines))))
       (irc:append (irc:chunks $line) $rest))))
(= (irc:append $front $back)
   (if (== $front ()) $back (cons-atom (car-atom $front) (irc:append (cdr-atom $front) $back))))
(= (irc:chunks $line)
   (if (== $line "") ()
     (if (<= (str:byte-length $line) (irc:line-bytes)) ($line)
       (let* (($n (irc:fit $line (min (str:char-length $line) (irc:line-bytes))))
              ($rest (irc:chunks (str:char-slice $line $n (str:char-length $line)))))
         (cons-atom (str:char-slice $line 0 $n) $rest)))))
(= (irc:fit $line $n)
   (if (<= (str:byte-length (str:char-slice $line 0 $n)) (irc:line-bytes)) $n (irc:fit $line (- $n 1))))
