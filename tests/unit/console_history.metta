!(import! &self ../native_test_support.metta)
!(import! &self ../../src/console/editor_cetta.metta)
; Recall reads a prompt_toolkit FileHistory exactly: an entry is a run of "+"
; lines joined by newlines, any other line ends it, and an empty first or last
; line is part of the entry. The file itself is never rewritten.
(= (hist:entries $at $count)
   (if (>= $at $count) ()
     (let $text (match &cc-history (ConsoleHistoryV1 $at $value) $value)
       (cons-atom $text (hist:entries (+ $at 1) $count)))))
!(let* (($client (path:join (system:getenv-or-default "CONSOLE_HISTORY_HOME" "") "client"))
        ($_ (fs:make-directories $client 448))
        ($file (path:join $client "history"))
        ($text (str:join "" ("\n# 2026-11-20 10:00:00\n+plain command\n"
                             "\n# 2026-11-20 10:01:00\n+first\n+雪 second\n+third\n"
                             "\n# 2026-11-20 10:02:00\n+\n+leading blank\n+\n+last\n"
                             "\n# 2026-11-20 10:03:00\n+trailing blank\n+\n"
                             "\n# 2026-11-20 10:04:00\n+\n"
                             "\n# 2026-11-20 10:05:00\n+a\n# 2026-11-20 10:06:00\n+b\n")))
        ($_ (fs:write $file $text))
        ($_ (cc:set "settings" (JsonObject ((JsonPair "client" $client)))))
        ($_ (cc:history-load))
        ($count (cc:get "history-count" 0))
        ($_ (nt:require entry-count (== $count 7)))
        ($_ (nt:require entries-exact
              (== (hist:entries 0 $count)
                  ("plain command" "first\n雪 second\nthird" "\nleading blank\n\nlast" "trailing blank\n" "" "a" "b"))))
        ($_ (nt:require file-unchanged (== (fs:read-text $file) $text))))
   (println! UNIT_CONSOLE_HISTORY_OK))
