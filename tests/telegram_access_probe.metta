; Telegram's intake under the access policy, with no channel service: what is
; dropped leaves only a note in the ledger; what is recorded carries its
; sender's provenance when the sender is not trusted, and its wake note and
; arming tier come from the rule. The runner starts this probe with operator
; 7, allowed chats 42 and -100, wake peer 8 and bot GodelTestBot.
!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)
!(configure commchannel telegram)

(= (assertEqual $A $B) (assert (== $A $B)))
(= (last-record)
   (let $lines (str:split "\n" (str:trim (fs:read-text (tg:ledger))))
     (json-parse (index-atom $lines (- (size-atom $lines) 1)))))
(= (message $id $chat $type $from $text)
   (json-parse (format-args "{\"update_id\": {}, \"message\": {\"message_id\": {}, \"date\": 1, \"chat\": {\"id\": {}, \"type\": \"{}\", \"title\": \"Lab\"}, \"from\": {\"id\": {}, \"first_name\": \"S{}\"}, \"text\": \"{}\"}}"
                            ($id $id $chat $type $from $from $text))))
(= (take $id $chat $type $from $text)
   (let $update (message $id $chat $type $from $text)
     (tg:intake (format-args "task-{}" ($id)) (format-args "{}.0" ($chat)) "message" "ordinary" $update)))
(= (pending $uid)
   (collapse (match &self (tg:pending $seq $task $chat $text $bot $tier $uid) ($text $tier))))
(= (note $uid) (collapse (match &self (tg:item-meta $uid $kind $note) $note)))
(= (contains? $text $needle) (case (str:find $text $needle) (((TextNotFound $n) False) ($at True))))
(= (facts $text) (case (ap:parse-facts $text) (((AccessFactsV1 $f) $f) ($other (refused $other)))))

; Today's behaviour: the channel's own settings.
(= (access:today)
   (let* (($_ (take 1 99 "private" 99 "hello"))
          ($_ (assertEqual (pending "1") ()))
          ($record (last-record))
          ($_ (assertEqual (json:get $record "note") (JsonString "access_dropped")))
          ($_ (assertEqual (json:get $record "allowed") (JsonBool False)))
          ($_ (assertEqual (json:get $record "text") JsonNull))
          ($_ (take 2 -100 "supergroup" 99 "hi all"))
          ((($text $tier)) (pending "2"))
          ($_ (assertEqual (contains? $text "provenance=\"untrusted stranger\"") True))
          ($_ (assertEqual (note "2") (quiet)))
          ($_ (assertEqual (json:get (last-record) "provenance") (JsonString "untrusted stranger")))
          ($_ (take 3 42 "private" 42 "a listed friend"))
          ((($text3 $tier3)) (pending "3"))
          ($_ (assertEqual (contains? $text3 "provenance") False))
          ($_ (assertEqual (note "3") (quiet)))
          ($_ (assertEqual (json:get (last-record) "provenance") JsonNull))
          ($_ (take 4 7 "private" 7 "from the operator"))
          ($_ (assertEqual (note "4") (sender-wake)))
          ($_ (take 5 -100 "supergroup" 8 "a peer"))
          ($_ (assertEqual (note "5") (sender-wake)))
          ($_ (take 6 -100 "supergroup" 42 "@GodelTestBot hello"))
          ($_ (assertEqual (note "6") (addressed)))
          ($_ (take 7 -200 "supergroup" 7 "an unlisted group"))
          ($_ (assertEqual (pending "7") ()))
          ($_ (assertEqual (json:get (last-record) "note") (JsonString "access_dropped")))
          ($_ (tg:forget-items (tg:pending-items))))
     (println! ACCESS_TODAY_OK)))

; A file's rules: the group records strangers silently, a listed member waits
; for a mention, and a named group's rule sets the arming tier.
(= (access:file-rules)
   (let* (($_ (ap:install (ap:append (ap:telegram-facts-from ("7") ("42" "-100") ("8") False)
                (facts (str:join "\n"
                  ("(access (group telegram \"-100\") groups allowlist)"
                   "(rule-for (group telegram \"-100\") groups stranger (rule record wake-never))"
                   "(rule-for (group telegram \"-100\") groups listed (rule record wake-on-mention))"
                   "(named-rule generic direct (named-group \"light\") (rule record wake-always no-reason (tier \"light\")))"
                   "(group-member (named-group \"light\") (sender telegram \"42\"))"))))))
          ($_ (take 11 -100 "supergroup" 99 "chatter"))
          ($_ (assertEqual (note "11") (silent)))
          ($_ (assertEqual (tg:quiet-batch?) True))
          ($_ (take 12 -100 "supergroup" 42 "more chatter"))
          ($_ (assertEqual (note "12") (silent)))
          ($_ (assertEqual (tg:quiet-batch?) True))
          ($_ (take 13 -100 "supergroup" 42 "@GodelTestBot a question"))
          ($_ (assertEqual (note "13") (addressed)))
          ($_ (assertEqual (tg:quiet-batch?) False))
          ($_ (take 14 42 "private" 42 "direct"))
          ((($text $tier)) (pending "14"))
          ($_ (assertEqual $tier "light"))
          ($_ (tg:forget-items (tg:pending-items))))
     (println! ACCESS_FILE_RULES_OK)))

; Every section must answer before the witness.
; A declared command's text that the service left as input is never a
; question for her: it is logged under its note and nothing is queued. A
; bare one from an owner (a service that does not take bare commands in
; groups), one addressed to another bot, one addressed to her by someone who
; may not command her. Anyone else's bare command is something they said.
(= (control-record $id $chat $type $from $text $expected)
   (let* (($_ (take $id $chat $type $from $text))
          ($record (last-record))
          ($_ (assertEqual (pending (format-args "{}" ($id))) ()))
          ($_ (assertEqual (json:get $record "note") (JsonString $expected)))
          ($_ (assertEqual (json:get $record "allowed") (JsonBool True)))
          ($_ (assertEqual (json:get $record "queued") (JsonBool False))))
     True))
(= (access:control-text)
   (let* (($_ (ap:install (ap:telegram-facts-from ("7") ("42" "-100") ("8") False)))
          ($_ (control-record 21 -100 "supergroup" 7 "/mode iter" "slash_command:/mode"))
          ($_ (control-record 22 -100 "supergroup" 7 "/Stop" "slash_command:/stop"))
          ($_ (control-record 23 -100 "supergroup" 42 "/mode@OtherBot iter" "slash_command_other_bot:otherbot"))
          ($_ (control-record 24 -100 "supergroup" 7 "/stop@OtherBot" "slash_command_other_bot:otherbot"))
          ($_ (control-record 25 -100 "supergroup" 42 "/mode@GodelTestBot iter" "slash_command_unknown"))
          ($_ (control-record 26 -100 "supergroup" 7 "/mode@godeltestbot iter" "slash_command:/mode"))
          ($_ (take 27 -100 "supergroup" 42 "/mode iter"))
          ((($text $tier)) (pending "27"))
          ($_ (assertEqual (contains? $text "/mode iter") True))
          ($_ (take 28 42 "private" 7 "/not_a_command now"))
          ($_ (assertEqual (size-atom (pending "28")) 1))
          ($_ (tg:forget-items (tg:pending-items))))
     (println! ACCESS_CONTROL_TEXT_OK)))

!(let* (($a (access:today)) ($c (access:control-text)) ($b (access:file-rules)))
   (println! TELEGRAM_ACCESS_OK))
