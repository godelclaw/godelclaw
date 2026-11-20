!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)
!(import! &self ./tests/scripted_provider.metta)

(= (assertEqual $a $b) (assert (== $a $b)))
; A mode's append into the agent's history that has not settled may be torn:
; this loop imports only what lies before it. Settled, it joins once, with the
; result lines that follow its command record; a mode's record that cannot be
; read imports nothing until it can.
(= (pending:history) (system:getenv-or-default "METTACLAW_HISTORY_PATH" ""))
(= (pending:record $stamp $message $reply)
   (str:join "" ("(\"" $stamp "\" \"\n\" \"HUMAN_MESSAGE: \" \"" $message "\" \"\n\" ((shell \""
                 $reply "\")) \"\n\")\n")))
(= (pending:result $text)
   (str:join "" ("(HistoryResultV1 omega \"2026-11-20 03:00:00\" (turn \"probe\" 1) (of \"0\") 0 returned full \""
                 $text "\")\n")))
(= (pending:append $text)
   (let $owner (fs:open-write (pending:history) ("create" "append") 384)
     (let* (($_ (fs:write-all $owner $text)) ($_ (fs:close $owner))) True)))
(= (pending:size) (let (AgentHistoryFileV1 $_ $_ $size) (agent-history:file) $size))
(= (pending:omega-record) (path:join (path:join (agent-history:modes-root) "omega") "native-checkpoint-v1.json"))
(= (pending:mark $status $offset)
   (let* (((AgentHistoryFileV1 $quoted-device $quoted-inode $_) (agent-history:file))
          ; The modes' records name the file unquoted.
          ($device (history-pending:bare $quoted-device)) ($inode (history-pending:bare $quoted-inode)))
     (let* (($_ (fs:make-directories (path:dirname (pending:omega-record)) 448)))
       (sf:replace (pending:omega-record)
         (str:join "" ("{\"schema\": 1, \"mode\": \"omega\", \"operation\": \"append\", \"status\": \"" $status
                       "\", \"device\": \"" $device "\", \"inode\": \"" $inode "\", \"offset\": " (repr $offset) "}"))
         384 True))))
(= (pending:count $n $text)
   (- (size-atom (str:split (scripted:escaped $text) (fs:read-text (scripted:path (format-args "request-{}.body" ($n)))))) 1))
(= (pending:turn $state $n $note)
   (let $_ (scripted:answer $n (str:join "" ("((task-note \"" $note "\"))")))
     (process-step $state (turnProcess (process-periphery $state)))))

!(initMemory)
!(configure commchannel telegram)
!(let* (($_ (loop-mode:set "godelclaw"))
        ($_ (assertEqual (== (agent-history:modes-root) "") False))
        ($s0 (initLoop))
        ($_ (pending:append (pending:record "2026-11-20 03:00:00" "SETTLED_BEFORE" "zero")))
        ($s1 (pending:turn $s0 1 "own turn one"))
        ($_ (assertEqual (pending:count 1 "SETTLED_BEFORE") 1))
        ; Omega's append, its command record and a result line, not yet settled.
        ($at (pending:size))
        ($_ (pending:append (str:concat (pending:record "2026-11-20 03:00:01" "PENDING_RECORD" "one")
                                        (pending:result "PENDING_RESULT_TEXT"))))
        ($_ (assertEqual (pending:mark "pending" $at) True))
        ($s2 (pending:turn $s1 2 "own turn two"))
        ($_ (assertEqual (pending:count 2 "PENDING_RECORD") 0))
        ($_ (assertEqual (pending:count 2 "PENDING_RESULT_TEXT") 0))
        ; Settled: the record and its result join once.
        ($_ (assertEqual (pending:mark "settled" $at) True))
        ($s3 (pending:turn $s2 3 "own turn three"))
        ($_ (assertEqual (pending:count 3 "PENDING_RECORD") 1))
        ($_ (assertEqual (pending:count 3 "PENDING_RESULT_TEXT") 1))
        ; A mode's record that cannot be read: nothing new joins, nothing is lost.
        ($_ (pending:append (pending:record "2026-11-20 03:00:02" "AFTER_UNREADABLE" "two")))
        ($_ (assertEqual (sf:replace (pending:omega-record) "{not json" 384 True) True))
        ($s4 (pending:turn $s3 4 "own turn four"))
        ($_ (assertEqual (pending:count 4 "AFTER_UNREADABLE") 0))
        ($_ (assertEqual (pending:mark "settled" $at) True))
        ($s5 (pending:turn $s4 5 "own turn five"))
        ($_ (assertEqual (pending:count 5 "AFTER_UNREADABLE") 1))
        ($_ (assertEqual (pending:count 5 "PENDING_RECORD") 1))
        ($_ (assertEqual (pending:count 5 "PENDING_RESULT_TEXT") 1))
        ($_ (assertEqual (scripted:calls) 5)))
   (println! CROSS_MODE_PENDING_OK))
