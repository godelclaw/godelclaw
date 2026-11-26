; Existing state formats survive native writes and restarts: pins keep earlier
; evidence and long entries, an unreadable pin file is never replaced, a
; missing one is created; context-policy changes keep the other settings, and
; invalid existing settings are left as they are.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/state_file_cetta.metta)
!(import! &self (library list))

; Run $code over the native state module in $dir; its output.
(= (ns:guest $repo $dir $code)
   (let* (($program (path:join $dir "guest.metta"))
          ($_ (fs:write $program (str:join "" ("!(import! &self (library lib_import))\n!(import! &self "
                                               (path:join $repo "src/state_cetta.metta") ")\n"
                                               "(= (pin $text) (pins:cetta-pin $text))\n(= (unpin $text) (pins:cetta-unpin $text))\n"
                                               "(= (pins) (pins:cetta-view))\n" $code))))
          ($env (list:append (("METTACLAW_ENGINE" "cetta") ("METTACLAW_ACTIVE_ENGINE" "cetta") ("OPENAI_API_KEY" "test-only-unused")
                              ("METTACLAW_PINS_PATH" (path:join $dir "pins.txt")) ("METTACLAW_ENGINE_STATE_PATH" (path:join $dir "engine")))
                             (nt:environment $dir))))
     (case (proc:run ((system:executable) "--lang" "petta" "--import-mode" "ancestor-walk" $program) $repo $env 20000 1048576)
       (((proc:exited 0 $out $err) (if (and (nt:contains $out "STATE_OK") (not (nt:contains $err "Assertion failed"))) $out (Failed $out $err)))
        ($bad (Failed $bad))))))
(= (ns:lines $path) (filter-atom (str:split "\n" (fs:read-text $path)) $l (not (== $l ""))))
(= (ns:quoted $text) (json-stringify (JsonString $text)))
(= (ns:dir $home $tag) (let $d (path:join $home $tag) (progn (fs:make-directories $d 448) $d)))
(= (ns:canonical $text) (sf:python-json (json:parse $text)))
(= (ns:chmod $mode $path) (nt:exit-ok (proc:run ("chmod" $mode $path) "/" inherit 5000 64)))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
         ; Full pins and the agent's own removal survive restarts.
         ($full (ns:dir $home "full")) ($pins (path:join $full "pins.txt"))
         ($original "[01-01 00:00] old evidence λ")
         ($_ (fs:write $pins (str:concat $original "\n")))
         ($long (str:concat "retained " (str:join "" (list:repeat 700 "λ"))))
         ($_ (ns:guest $repo $full (str:join "" (list:append
                (map-atom (list:range 0 20) $i (str:join "" ("!(pin " (ns:quoted (str:concat (if (< $i 10) "pin-0" "pin-") (repr $i))) ")\n")))
                ((str:join "" ("!(pin " (ns:quoted $long) ")\n!(println! STATE_OK)\n")))))))
         ($written (ns:lines $pins))
         ($_ (nt:require pins-append-after-earlier-evidence
               (and (== (size-atom $written) 22) (and (== (car-atom $written) $original) (nt:contains (list:at $written 21) $long)))))
         ($view (ns:guest $repo $full "!(println! (pins))\n!(unpin \"pin-00\")\n!(println! STATE_OK)\n"))
         ($_ (nt:require a-restart-sees-the-long-pin (nt:contains $view $long)))
         ($after (ns:lines $pins))
         ($_ (nt:require removal-keeps-earlier-evidence (and (== (size-atom $after) 21) (== (car-atom $after) $original))))
         ; An unreadable pin file is never replaced, by pin or by unpin.
         ($locked (ns:dir $home "locked")) ($locked-pins (path:join $locked "pins.txt"))
         ($kept "[01-01 00:00] retain existing evidence\n")
         ($_ (nt:require an-unreadable-file-is-never-replaced
               (== (filter-atom (map-atom ("pin" "unpin") $command
                      (let* (($_ (fs:write $locked-pins $kept)) ($_ (ns:chmod "200" $locked-pins))
                             ($out (ns:guest $repo $locked (str:join "" ("!(println! (pins))\n!(println! (" $command " \"new note\"))\n!(println! STATE_OK)\n"))))
                             ($_ (ns:chmod "600" $locked-pins)))
                        (and (and (== (get-type $out) String) (nt:contains $out "pins unavailable:"))
                             (and (nt:contains $out (str:concat $command " failed:")) (== (fs:read-text $locked-pins) $kept)))))
                     $ok (not (== $ok True)))
                   ())))
         ; A missing pin file is created.
         ($empty (ns:dir $home "empty"))
         ($first (ns:guest $repo $empty "!(println! (pins))\n!(println! (pin \"first pin\"))\n!(println! STATE_OK)\n"))
         ($_ (nt:require a-missing-pin-file-is-created
               (and (and (nt:contains $first "(no pins)") (nt:contains $first "pinned 1"))
                    (nt:contains (fs:read-text (path:join $empty "pins.txt")) "first pin"))))
         ; Context-policy changes keep the other settings.
         ($policy-dir (ns:dir $home "policy")) ($policy (path:join $policy-dir "context-policy.json"))
         ($_ (fs:write $policy "{\"task_tokens\": 32768, \"retained_field\": \"λ\"}"))
         ($_ (ns:guest $repo $policy-dir "!(println! (context-policy 0))\n!(println! STATE_OK)\n"))
         ($_ (nt:require zero-returns-to-the-whole-window
               (== (ns:canonical (fs:read-text $policy)) (ns:canonical "{\"mode\": \"full\", \"retained_field\": \"λ\"}"))))
         ($_ (ns:guest $repo $policy-dir "!(println! (context-policy 120000))\n!(println! STATE_OK)\n"))
         ($_ (nt:require a-count-opts-into-a-budget
               (== (ns:canonical (fs:read-text $policy))
                   (ns:canonical "{\"mode\": \"budget\", \"task_tokens\": 120000, \"retained_field\": \"λ\"}"))))
         ($_ (ns:guest $repo $policy-dir "!(println! (context-policy \"full\"))\n!(println! STATE_OK)\n"))
         ($_ (nt:require full-returns-to-the-whole-window
               (== (ns:canonical (fs:read-text $policy)) (ns:canonical "{\"mode\": \"full\", \"retained_field\": \"λ\"}"))))
         ; Invalid existing settings are left as they are.
         ($invalid-dir (ns:dir $home "invalid")) ($invalid (path:join $invalid-dir "context-policy.json"))
         ($_ (fs:write $invalid "retained but invalid JSON"))
         ($_ (ns:guest $repo $invalid-dir "!(println! (catch (context-policy 42)))\n!(println! STATE_OK)\n"))
         ($_ (nt:require invalid-settings-are-preserved (== (fs:read-text $invalid) "retained but invalid JSON"))))
   (println! UNIT_NATIVE_STATE_OK))
