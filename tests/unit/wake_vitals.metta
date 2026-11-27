; What the runtime measures so the model need not infer it: each turn's view
; carries the time since the previous turn (the history's newest turn for
; the first wake after a start, read as local time the way Python's mktime
; read it), the machine's load and memory, one view per turn; long gaps read
; in hours; each message header carries Telegram's send time.
!(import! &self ../native_test_support.metta)

; Each case in a fresh engine: the view remembers the previous turn.
(= (wv:guest $name $tz $forms)
   (let* (($root (nt:required-env "TEST_REPOSITORY")) ($home (path:join (nt:required-env "HOME") $name))
          ($_ (fs:make-directories $home 448))
          ($history (path:join $home "history.metta"))
          ($_ (fs:write $history "(\"2026-09-24 16:04:10\" ((pin \"a\")))\n(\"2026-01-15 08:00:00\" ((pin \"winter\")))\n(\"2026-09-24 16:05:00\" ((shell \"b\")))\n"))
          ($program (path:join $home "case.metta"))
          ($_ (fs:write $program (str:join "\n" (cons-atom
                (str:join "" ("!(import! &self " (repr (path:join $root "src/runtime_support_cetta.metta")) ")")) $forms)))))
     (case (proc:run ((path:join (nt:required-env "NATIVE_TEST_CORE") "cetta") "--lang" "petta" "--import-mode" "ancestor-walk" "--quiet" $program)
                     $root (cons-atom ("TZ" $tz) (cons-atom ("METTACLAW_HISTORY_PATH" (if (== $name "no-history") (path:join $home "absent") $history))
                             (nt:environment $home)))
                     60000 65536)
       (((proc:exited 0 $out $err) $out) ($other (repr $other))))))

; Python's mktime of "2026-09-24 16:05:00" and "2026-01-15 08:00:00".
!(let $out (wv:guest "prague" "Europe/Prague"
             ("!(println! (LAST (runtime-support:last-turn)))"
              "!(println! (WINTER (runtime-support:local-time 2026 1 15 8 0 0)))"
              "!(println! (VIEW (runtime-support:wake-view-at 75 (+ 1790258700 2475) \"load 309/16 | mem 94%\")))"))
   (nt:require the-first-wake-measures-from-the-history-as-mktime-does
     (and (nt:contains $out "(LAST 1790258700)")
     (and (nt:contains $out "(WINTER 1768460400)")
          (nt:contains $out "(VIEW \"+41m15s since last turn | load 309/16 | mem 94%\")")))))
!(let $out (wv:guest "new-york" "America/New_York"
             ("!(println! (LAST (runtime-support:last-turn)))"
              "!(println! (WINTER (runtime-support:local-time 2026 1 15 8 0 0)))"))
   (nt:require local-time-follows-the-zone-and-its-daylight-saving
     (and (nt:contains $out "(LAST 1790280300)") (nt:contains $out "(WINTER 1768482000)"))))
!(let $out (wv:guest "no-history" "Europe/Prague"
             ("!(println! (FIRST (runtime-support:wake-view-at 7 1000 \"load 309/16 | mem 94%\")))"
              "!(println! (AGAIN (runtime-support:wake-view-at 7 1030 \"load 309/16 | mem 94%\")))"
              "!(println! (NEXT (runtime-support:wake-view-at 8 1042 \"load 309/16 | mem 94%\")))"
              "!(println! (HOURS (runtime-support:wake-view-at 9 8542 \"load 309/16 | mem 94%\")))"
              "!(println! (MACHINE (runtime-support:machine)))"))
   (nt:require one-view-per-turn-then-the-gap-to-the-next
     (and (nt:contains $out "(FIRST \"load 309/16 | mem 94%\")")
     (and (nt:contains $out "(AGAIN \"load 309/16 | mem 94%\")")
     (and (nt:contains $out "(NEXT \"+42s since last turn | load 309/16 | mem 94%\")")
     (and (nt:contains $out "(HOURS \"+2h05m since last turn | load 309/16 | mem 94%\")")
          (nt:contains $out "(MACHINE \"load ")))))))
!(let $out (wv:guest "send-time" "Europe/Prague"
             ((str:join "" ("!(import! &self " (repr (path:join (nt:required-env "TEST_REPOSITORY") "channels/telegram.metta")) ")"))
              "!(println! (SENT (tg:clock \"1790000000\")))"))
   (nt:require a-message-carries-its-send-time (nt:contains $out "(SENT \"2026-09-21 16:13:20\")")))

!(println! UNIT_WAKE_VITALS_OK)
