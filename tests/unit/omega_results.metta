!(import! &self ../native_test_support.metta)
!(import! &self ../../modes/omega/native/bootstrap.metta)
!(import! &self ../../src/history_pending_cetta.metta)

(= (results:lines $payload)
   (filter-atom (str:split "\n" $payload) $line (str:starts-with $line "(HistoryResultV1 ")))
(= (results:digest $record) (str:byte-slice (str:sha256 $record) 0 16))

; One result line per call, after the record and in its append: equal calls
; with different outputs stay distinct by their place in the record, and every
; line names the record by its digest.
!(let* (($record "(\"2026-11-20 12:00:00\" \"\n\" ((shell \"wc -c < f\") (shell \"wc -c < f\") (read-file \"x\") (shell \"y\")) \"\n\")")
        ($_ (change-state! &omega-results ()))
        ($_ (omega-host:note-result "SUCCESS, RETURN: 1"))
        ($_ (omega-host:note-result "SUCCESS, RETURN: 2"))
        ($_ (omega-host:note-result "SUCCESS, RETURN: "))
        ($_ (omega-host:note-result "ALERT_FAILED(Error (shell \"y\") \"no such\")"))
        ($payload (omega-host:with-results $record ((shell "wc -c < f") (shell "wc -c < f") (read-file "x") (shell "y"))))
        ($lines (results:lines $payload))
        ($of (str:join "" ("(of \"" (results:digest $record) "\")")))
        ($_ (nt:require record-bytes-unchanged-first (str:starts-with $payload (str:concat $record "\n"))))
        ($_ (nt:require one-line-per-call (== (size-atom $lines) 4)))
        ($_ (nt:require every-line-names-the-record (== (filter-atom $lines $line (not (nt:contains $line $of))) ())))
        ($_ (nt:require equal-calls-first (str:ends-with (index-atom $lines 0) " 0 returned full \"1\")")))
        ($_ (nt:require equal-calls-second (str:ends-with (index-atom $lines 1) " 1 returned full \"2\")")))
        ($_ (nt:require empty-return-is-full-and-empty (str:ends-with (index-atom $lines 2) " 2 returned full \"\")")))
        ($_ (nt:require raised-is-its-own-disposition
              (str:ends-with (index-atom $lines 3) " 3 raised full \"(Error (shell \\\"y\\\") \\\"no such\\\")\")")))
        ($_ (nt:require notes-consumed (== (get-state &omega-results) ()))))
   (println! UNIT_OMEGA_RESULT_LINES_OK))

; A call with no noted outcome has none kept; a long text is clipped with its
; length; text with newlines stays on one line and reads back whole; a record
; without calls is written alone.
!(let* (($record "(\"t\" \"\n\" ((shell \"a\") (shell \"b\")) \"\n\")")
        ($_ (change-state! &omega-results ()))
        ($_ (omega-host:note-result "SUCCESS, RETURN: first\nsecond"))
        ($missing (results:lines (omega-host:with-results $record ((shell "a") (shell "b")))))
        ($_ (nt:require text-on-one-line (== (size-atom $missing) 2)))
        ($_ (nt:require newline-escaped (str:ends-with (index-atom $missing 0) " 0 returned full \"first\\nsecond\")")))
        ($_ (nt:require unescaped-whole
              (== (str:unescape metta "first\\nsecond") "first\nsecond")))
        ($_ (nt:require missing-outcome-is-none (str:ends-with (index-atom $missing 1) " 1 unknown none \"\")")))
        ($long (str:join "" (map-atom (1 2 3 4 5) $i (str:join "" (map-atom (1 2 3 4 5 6 7 8 9 10) $j "abcdefghijklmnopqrst")))))
        ($_ (nt:require long-fixture (== (str:char-length $long) 1000)))
        ($longer (str:join "" ($long $long $long $long $long)))
        ($_ (omega-host:note-result (str:concat "SUCCESS, RETURN: " $longer)))
        ($clipped (results:lines (omega-host:with-results $record ((shell "a")))))
        ($_ (nt:require long-text-clipped-with-length (nt:contains (index-atom $clipped 0) " 0 returned (clipped 5000) \"")))
        ($_ (nt:require clipped-to-the-cap
              (str:ends-with (index-atom $clipped 0) (str:concat (str:char-slice $longer 3980 4000) "\")"))))
        ($_ (nt:require nothing-after-a-record-without-calls
              (== (omega-host:with-results $record "ALERT_FAILED(Error parse)") $record))))
   (println! UNIT_OMEGA_RESULT_STATES_OK))

; Omega's HISTORY is the same bytes with or without result lines, even when a
; large result sits within the budget, and the same as the plain tail of a
; history without them.
(= (results:records $n)
   (if (== $n 0) ()
     (cons-atom (str:join "" ("(\"2026-11-20 12:00:" (repr (+ 10 $n)) "\" \"\n\" ((shell \"step " (repr $n) "\")) \"\n\")\n"))
                (results:records (- $n 1)))))
(= (results:with-lines $records)
   (map-atom $records $record
     (str:join "" ($record "(HistoryResultV1 omega \"t\" (turn \"b\" 1) (of \"0\") 0 returned (clipped 9000) \""
                   (str:join "" (map-atom (1 2 3 4 5 6 7 8 9 10) $k "result text that is long ")) "\")\n"))))
!(let* (($home (system:getenv-or-default "OMEGA_RESULTS_HOME" ""))
        ($_ (nt:require results-home (!= $home "")))
        ($records (results:records 40))
        ($plain (path:join $home "plain.metta"))
        ($mixed (path:join $home "mixed.metta"))
        ($_ (fs:write $plain (str:join "" $records)))
        ($_ (fs:write $mixed (str:join "" (results:with-lines $records))))
        ($_ (nt:require mixed-is-larger (> (str:byte-length (fs:read-text $mixed)) (* 3 (str:byte-length (fs:read-text $plain))))))
        ($view (omega-host:history-view $plain 600))
        ($_ (nt:require plain-view-is-the-tail
              (case (fs:tail-text $plain 600) (((fs:tail $_ $_ $text) (== $view $text)) ($bad False)))))
        ($_ (nt:require view-ignores-result-lines (== (omega-host:history-view $mixed 600) $view)))
        ($_ (nt:require whole-file-view (== (omega-host:history-view $mixed 100000) (fs:read-text $plain))))
        ($_ (nt:require missing-history-view (== (omega-host:history-view (path:join $home "none.metta") 600) "")))
        ; A cut inside a character reads as the old tail read decoded it: each
        ; byte of the cut character one U+FFFD; with or without result lines.
        ($wide (path:join $home "wide.metta"))
        ($wide-mixed (path:join $home "wide-mixed.metta"))
        ($euro (str:join "" (map-atom (1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20) $e "\u20ac")))
        ($wide-records (map-atom (1 2 3) $w (str:join "" ("(\"2026-11-20 12:01:0" (repr $w) "\" \"" $euro "\")\n"))))
        ($_ (fs:write $wide (str:join "" $wide-records)))
        ($_ (fs:write $wide-mixed (str:join "" (results:with-lines $wide-records))))
        ($old (case (fs:tail-text $wide 20) (((fs:tail $_ $_ $text) $text))))
        ($_ (nt:require unicode-cut-as-before (== (omega-host:history-view $wide 20) $old)))
        ($_ (nt:require unicode-cut-without-result-lines (== (omega-host:history-view $wide-mixed 20) $old)))
        ($_ (nt:require replacement-for-each-cut-byte (str:starts-with $old (str:from-codepoints (65533))))))
   (println! UNIT_OMEGA_HISTORY_VIEW_OK))

; Another loop reads the history only up to a mode's unsettled append; a
; settled one, one into another file and a modes-directory entry that is a
; file do not stop it; a record that cannot be read decides nothing.
(= (results:record $device $inode $status $offset)
   (str:join "" ("{\"status\": \"" $status "\", \"operation\": \"append\", \"device\": \"" $device
                 "\", \"inode\": \"" $inode "\", \"offset\": " (repr $offset) "}")))
!(let* (($home (system:getenv-or-default "OMEGA_RESULTS_HOME" ""))
        ($history (path:join $home "history.metta"))
        ($_ (fs:write $history "0123456789abcdefghij"))
        ((ModeFileV1 $size $device $inode) (mode-host:file-info $history))
        ($root (path:join $home "modes"))
        ($_ (fs:make-directories (path:join $root "omega") 448))
        ($_ (fs:make-directories (path:join $root "iter") 448))
        ($_ (fs:write (path:join $root "control-status.json") "{}"))
        ($_ (nt:require nothing-pending (== (history-pending:end $root $history $device $inode $size) 20)))
        ($omega (path:join $root "omega/native-checkpoint-v1.json"))
        ($_ (fs:write $omega (results:record $device $inode "pending" 12)))
        ($_ (nt:require pending-append-stops-reading (== (history-pending:end $root $history $device $inode $size) 12)))
        ($_ (fs:write (path:join $root "iter/agent-history-intent.json") (results:record $device $inode "pending" 7)))
        ($_ (nt:require earliest-pending-append (== (history-pending:end $root $history $device $inode $size) 7)))
        ($_ (fs:write (path:join $root "iter/agent-history-intent.json") (results:record $device $inode "settled" 7)))
        ($_ (fs:write $omega (results:record "0" $inode "pending" 3)))
        ($_ (nt:require other-file-does-not-stop (== (history-pending:end $root $history $device $inode $size) 20)))
        ($_ (fs:write $omega "{not json"))
        ($_ (nt:require unreadable-decides-nothing (== (history-pending:end $root $history $device $inode $size) unknown)))
        ($_ (nt:require no-modes-directory (== (history-pending:end (path:join $home "absent") $history $device $inode $size) 20)))
        ; A modes directory that cannot be observed authorizes no reading.
        ($locked (path:join $home "locked"))
        ($_ (fs:make-directories (path:join $locked "modes") 448))
        ($_ (proc:run ("/bin/chmod" "000" $locked) $home (("PATH" "/usr/bin:/bin")) 5000 1024))
        ($blind (history-pending:end (path:join $locked "modes") $history $device $inode $size))
        ($_ (proc:run ("/bin/chmod" "700" $locked) $home (("PATH" "/usr/bin:/bin")) 5000 1024))
        ($_ (nt:require unobservable-modes-decide-nothing (== $blind unknown))))
   (println! UNIT_HISTORY_PENDING_OK))
