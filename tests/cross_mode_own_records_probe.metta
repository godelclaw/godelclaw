!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)
!(import! &self ./tests/scripted_provider.metta)

(= (assertEqual $a $b) (assert (== $a $b)))
; This loop's own records are never shown back to it as another loop's: not
; after a death between a record's write and the cursor's move, and not when
; the history cannot be read for a turn while a record is still unchecked.
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
          ((AgentHistoryCursorV1 $_ $_ $_ $at $_) (agent-history:parse (agent-history:cursor-raw)))
          ((AgentHistoryTextV1 $shown $text) (agent-history:text $at $size))
          ($content (str:concat (agent-history:header $at $shown $size) $text)))
     (state:ledger "request-accept" ((agent-history:id $file $at) $content "operator" "notification"
                                     "interrupted import" (str:sha256 $content) (state:request-id)))))
(= (probe:size) (let (AgentHistoryFileV1 $_ $_ $size) (agent-history:file) $size))
; A death after this loop's own record from <from> and before its cursor moved.
(= (probe:interrupted-own $from)
   (let* (((AgentHistoryFileV1 $device $inode $end) (agent-history:file))
          ($text (agent-history:read-at (agent-history:path) $from (- $end $from))))
     (agent-history:set (agent-history:cursor-raw)
       (agent-history:record-own (AgentHistoryFileV1 $device $inode $from) (((- $end $from) (str:sha256 $text)))))))
(= (probe:cursor-clean-at-end)
   (case (agent-history:parse (agent-history:cursor-raw))
     (((AgentHistoryCursorV1 $_ $_ $_ $at none) (== $at (probe:size))) ($other False))))

!(initMemory)
!(configure commchannel telegram)
!(let* (($_ (loop-mode:set "godelclaw"))
        ($s0 (initLoop))
        ($s1 (probe:turn $s0 1 "own turn one"))
        ; A death between this loop's own record and its cursor's move: the
        ; record is passed over, never shown back as another loop's.
        ($two (probe:size))
        ($s2 (probe:turn $s1 2 "own turn two"))
        ($three (probe:size))
        ($_ (assertEqual (probe:interrupted-own $two) True))
        ($s3 (probe:turn $s2 3 "own turn three"))
        ($_ (assertEqual (state:ledger "request-get" ((agent-history:id (agent-history:file) $two) head)) none))
        ($_ (assertEqual (probe:count 3 "own turn two") (probe:count 2 "own turn one")))
        ($_ (assertEqual (probe:cursor-clean-at-end) True))
        ; A whole own record still named, and a history that cannot be read for
        ; a turn: the name stays, that turn's record joins it, and once the
        ; file reads again both are passed over, neither shown back.
        ($_ (assertEqual (probe:interrupted-own $three) True))
        ($_ (probe:chmod "0200"))
        ($s4 (probe:turn $s3 4 "own turn four"))
        ($_ (probe:chmod "0600"))
        ($_ (assertEqual (probe:cursor-clean-at-end) False))
        ($s5 (probe:turn $s4 5 "own turn five"))
        ($_ (assertEqual (state:ledger "request-get" ((agent-history:id (agent-history:file) $three) head)) none))
        ($_ (assertEqual (probe:count 5 "own turn four") (probe:count 2 "own turn one")))
        ($_ (assertEqual (probe:cursor-clean-at-end) True))
        ($_ (assertEqual (scripted:calls) 5)))
   (println! CROSS_MODE_OWN_RECORDS_OK))
