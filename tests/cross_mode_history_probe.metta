!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)
!(import! &self ./tests/scripted_provider.metta)

(= (assertEqual $a $b) (assert (== $a $b)))
; What a supervised mode writes to the agent's history while this loop is not
; running enters this loop's next prompt, once, each occurrence of it; and
; still once after an import was interrupted or the history could not be read.
(= (probe:history) (system:getenv-or-default "METTACLAW_HISTORY_PATH" ""))
; A mode's turn as Omega records it: the input, then its commands.
(= (probe:record $stamp $message $reply)
   (str:join "" ("(\"" $stamp "\" \"\n\" \"HUMAN_MESSAGE: \" \"" $message "\" \"\n\" ((send-telegram-chat \"42\" \""
                 $reply "\")) \"\n\")\n")))
(= (probe:append $text)
   (let $owner (fs:open-write (probe:history) ("create" "append") 384)
     (let* (($_ (fs:write-all $owner $text)) ($_ (fs:close $owner))) True)))
(= (probe:chmod $mode)
   (proc:run ("/bin/chmod" $mode (probe:history)) (system:cwd) (("PATH" "/usr/bin:/bin")) 5000 1024))
; How often request $n's prompt holds $text, as JSON writes it.
(= (probe:count $n $text)
   (- (size-atom (str:split (scripted:escaped $text) (fs:read-text (scripted:path (format-args "request-{}.body" ($n)))))) 1))
(= (probe:turn $state $n $note)
   (let $_ (scripted:answer $n (str:join "" ("((task-note \"" $note "\"))")))
     (process-step $state (turnProcess (process-periphery $state)))))
; An import whose request was written and whose cursor did not move.
(= (probe:interrupted-import)
   (let* (($file (agent-history:file)) ((AgentHistoryFileV1 $_ $_ $size) $file)
          ((AgentHistoryCursorV1 $_ $_ $_ $at) (agent-history:parse (agent-history:cursor-raw)))
          ((AgentHistoryTextV1 $shown $text) (agent-history:text $at $size))
          ($content (str:concat (agent-history:header $at $shown $size) $text)))
     (state:ledger "request-accept" ((agent-history:id $file $at) $content "operator" "notification"
                                     "interrupted import" (str:sha256 $content) (state:request-id)))))

!(initMemory)
!(configure commchannel telegram)
!(let* (($_ (loop-mode:set "godelclaw"))
        ($s0 (initLoop))
        ; A mode wrote before this loop's first turn, to a history that did not
        ; exist when the loop started: all of it is the mode's.
        ($_ (probe:append (probe:record "2026-11-20 01:00:00" "BEFORE_FIRST_TURN_RECORD" "zero")))
        ($s1 (probe:turn $s0 1 "own turn one"))
        ($_ (assertEqual (probe:count 1 "BEFORE_FIRST_TURN_RECORD") 1))
        ; A mode ran: a request and its reply, then one record written twice.
        ($_ (probe:append (probe:record "2026-11-20 01:21:44" "MODE_ERA_REQUEST add the models" "MODE_ERA_REPLY on it")))
        ($twice (probe:record "2026-11-20 01:22:05" "EQUAL_OCCURRENCE yes" "EQUAL_REPLY ok"))
        ($_ (probe:append $twice))
        ($_ (probe:append $twice))
        ($s2 (probe:turn $s1 2 "own turn two"))
        ($_ (assertEqual (probe:count 2 "MODE_ERA_REQUEST add the models") 1))
        ($_ (assertEqual (probe:count 2 "MODE_ERA_REPLY on it") 1))
        ($_ (assertEqual (probe:count 2 "EQUAL_OCCURRENCE yes") 2))
        ($_ (assertEqual (probe:count 2 "EQUAL_REPLY ok") 2))
        ; The next turn keeps them, and imports nothing again.
        ($s3 (probe:turn $s2 3 "own turn three"))
        ($_ (assertEqual (probe:count 3 "MODE_ERA_REQUEST add the models") 1))
        ($_ (assertEqual (probe:count 3 "EQUAL_OCCURRENCE yes") 2))
        ; An interrupted import, and a record after it: each once.
        ($_ (probe:append (probe:record "2026-11-20 02:00:00" "INTERRUPTED_RECORD" "first")))
        ($_ (assertEqual (probe:interrupted-import) created))
        ($_ (probe:append (probe:record "2026-11-20 02:00:10" "AFTER_INTERRUPTION_RECORD" "second")))
        ($s4 (probe:turn $s3 4 "own turn four"))
        ($_ (assertEqual (probe:count 4 "INTERRUPTED_RECORD") 1))
        ($_ (assertEqual (probe:count 4 "AFTER_INTERRUPTION_RECORD") 1))
        ($_ (assertEqual (probe:count 4 "MODE_ERA_REQUEST add the models") 1))
        ; An unreadable history: nothing joins, and nothing is lost.
        ($_ (probe:append (probe:record "2026-11-20 02:10:00" "UNREADABLE_THEN_READ" "third")))
        ($_ (probe:chmod "0200"))
        ($s5 (probe:turn $s4 5 "own turn five"))
        ($_ (probe:chmod "0600"))
        ($_ (assertEqual (probe:count 5 "UNREADABLE_THEN_READ") 0))
        ($s6 (probe:turn $s5 6 "own turn six"))
        ($_ (assertEqual (probe:count 6 "UNREADABLE_THEN_READ") 1))
        ($_ (assertEqual (probe:count 6 "INTERRUPTED_RECORD") 1))
        ($_ (assertEqual (probe:count 6 "BEFORE_FIRST_TURN_RECORD") 1))
        ($_ (assertEqual (scripted:calls) 6)))
   (println! CROSS_MODE_HISTORY_OK))
