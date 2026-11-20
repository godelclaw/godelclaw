!(import! &self ../native_test_support.metta)
!(import! &self ../../modes/history_cetta.metta)
; The episodes tool of Iter and Omega, with the mode history module loaded by
; itself: the lines around the record nearest a time, numbered, with CRLF read
; as LF; a time that is not a timestamp is refused.
(= (episodes:path) (path:join (system:getenv-or-default "EPISODES_HOME" "") "history.metta"))
!(let* (($path (episodes:path))
        ($_ (fs:write $path (str:join "" ("(\"2026-11-20 10:00:00\" \"\n\" \"HUMAN_MESSAGE: \" \"FIRST_EPISODE\" \"\n\")\n"
                                          "(\"2026-11-20 12:00:00\" \"\r\n\" \"HUMAN_MESSAGE: \" \"SECOND_EPISODE\" \"\r\n\")\r\n"
                                          "(\"2026-11-20 14:00:00\" \"\n\" \"HUMAN_MESSAGE: \" \"THIRD_EPISODE\" \"\n\")\n"))))
        ($found (mode-history:episodes $path "2026-11-20 12:05:00" 1))
        ($_ (nt:require episodes-returns-text (== (get-type $found) String)))
        ($_ (nt:require episodes-centres-on-nearest
              (and (nt:contains $found "2026-11-20 12:00:00")
                   (and (not (nt:contains $found "FIRST_EPISODE")) (not (nt:contains $found "THIRD_EPISODE"))))))
        ($_ (nt:require episodes-numbers-lines (str:starts-with $found "3:")))
        ($_ (nt:require episodes-reads-crlf-as-lf (not (nt:contains $found "\r"))))
        ($_ (nt:require episodes-refuses-non-timestamp
              (case (mode-history:episodes $path "noon" 1) (((Error episodes $_) True) ($other False))))))
   (println! UNIT_MODE_EPISODES_OK))
