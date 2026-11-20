!(import! &self ../native_test_support.metta)
!(import! &self ../../src/agent_history_cetta.metta)
; The framework loop's import of a long gap reads the newest bytes up to the
; settled end it is given, never past it: the last settled record is shown,
; a pending suffix after it is not.
(= (bounds:filler $n)
   (if (== $n 0) ()
     (cons-atom (str:join "" ("(\"2026-11-20 05:00:00\" \"\n\" \"FILLER " (repr $n) " " (str:join "" (map-atom (1 2 3 4 5 6 7 8 9 10) $i "padding padding ")) "\" \"\n\")\n"))
                (bounds:filler (- $n 1)))))
!(let* (($history (agent-history:path))
        ($_ (nt:require history-configured (!= $history "./memory/history.metta")))
        ($_ (fs:write $history (str:join "" (list:append (bounds:filler 250) ("(\"2026-11-20 05:00:01\" \"\n\" \"LAST_SETTLED_RECORD\" \"\n\")\n")))))
        ($end (case (agent-history:file) (((AgentHistoryFileV1 $_ $_ $size) $size))))
        ($_ (nt:require gap-is-long (> $end (agent-history:limit))))
        ($owner (fs:open-write $history ("append") 384))
        ($_ (fs:write-all $owner "(\"2026-11-20 05:00:02\" \"\n\" \"PENDING_SUFFIX_RECORD\" \"\n\")\n"))
        ($_ (fs:close $owner))
        ($shown (agent-history:text 0 $end))
        ($_ (nt:require long-gap-read
              (case $shown (((AgentHistoryTextV1 $from $text)
                             (and (nt:contains $text "LAST_SETTLED_RECORD") (not (nt:contains $text "PENDING_SUFFIX"))))
                            ($bad False)))))
        ($_ (nt:require long-gap-starts-at-a-record
              (case $shown (((AgentHistoryTextV1 $at $text) (str:starts-with $text "(\"2026-11-20 05:00:00\"")) ($bad False))))))
   (println! UNIT_AGENT_HISTORY_BOUNDS_OK))
