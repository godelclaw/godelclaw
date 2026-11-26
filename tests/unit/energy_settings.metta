; Energy settings: edits keep unknown fields and the file's mode, a missing
; parent is created, sender names and negative ids resolve, an unwritable
; parent is a failed save that leaves the evidence alone, and concurrent
; whole-state writes leave one writer's state and no temporary file.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/state_file_cetta.metta)
!(import! &self (library list))

(= (en:environment $work $extra)
   (list:append (list:append (("METTACLAW_ENGINE" "cetta") ("METTACLAW_ACTIVE_ENGINE" "cetta") ("OPENAI_API_KEY" "test-only-unused")
                              ("METTACLAW_ENERGY_PATH" (path:join $work "memory/energy.json"))
                              ("XDG_STATE_HOME" (path:join $work "state")) ("METTACLAW_ENGINE_STATE_PATH" (path:join $work "engine")))
                             $extra)
                (nt:environment $work)))
(= (en:program $repo $work $expression $tag)
   (let $program (path:join $work (str:concat "energy-" (str:concat $tag ".metta")))
     (progn (fs:write $program (str:join "" ("!(import! &self (library lib_import))\n!(import! &self " (path:join $repo "channels/energy.metta")
                                             ")\n!(let $answers (collapse " $expression ") (println! (EnergyResult $answers)))\n"
                                             "!(println! ENERGY_NATIVE_OK)\n")))
            $program)))
(= (en:argv $program) ((system:executable) "--lang" "petta" "--import-mode" "ancestor-walk" $program))
(= (en:checked $result)
   (case $result
     (((proc:exited 0 $out $err)
       (if (and (nt:contains $out "ENERGY_NATIVE_OK") (not (nt:contains (str:concat $out $err) "Error"))) $out (Failed $out $err)))
      ((proc:result (proc:exited 0) $how $out $err $_)
       (if (and (nt:contains $out "ENERGY_NATIVE_OK") (not (nt:contains (str:concat $out $err) "Error"))) $out (Failed $out $err)))
      ($bad (Failed $bad)))))
(= (en:run $repo $work $expression $extra)
   (en:checked (proc:run (en:argv (en:program $repo $work $expression "run")) $repo (en:environment $work $extra) 25000 1048576)))
(= (en:file $work) (path:join $work "memory/energy.json"))
; Canonical text of a JSON value, so key order never matters.
(= (en:canonical $text) (sf:python-json (json:parse $text)))
(= (en:same-json? $path $expected) (== (en:canonical (fs:read-text $path)) (en:canonical $expected)))
(= (en:senders $work) (sf:python-json (sf:get (json:parse (fs:read-text (en:file $work))) "senders" JsonNull)))
(= (en:mode $path) (str:strip-python (nt:exit-ok (proc:run ("stat" "-c" "%a" $path) "/" inherit 5000 64))))
(= (en:only-the-file? $work) (== (fs:entries (path:join $work "memory")) ("energy.json")))
(= (en:work $home $tag) (let $w (path:join $home $tag) (progn (fs:make-directories $w 448) $w)))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
         ; Unknown fields and the file's mode survive edits across restarts.
         ($keep (en:work $home "keep")) ($_ (fs:make-directories (path:join $keep "memory") 448))
         ($original "{\"default\": \"full\", \"senders\": {\"7\": \"light\"}, \"names\": {\"7\": \"λ 🌸\"}, \"tier_loops\": {\"mid\": 37}, \"extension\": {\"kept\": [1, 2]}}")
         ($_ (fs:write (en:file $keep) $original))
         ($_ (nt:exit-ok (proc:run ("chmod" "640" (en:file $keep)) $keep inherit 5000 64)))
         ($_ (nt:require a-default-edit-uses-the-files-own-tiers
               (nt:contains (en:run $repo $keep "(energy:set \"default\" \"mid\")" ()) "(EnergyResult (\"default arming set to mid(37)\"))")))
         ($_ (nt:require unknown-fields-survive-a-default-edit
               (en:same-json? (en:file $keep) "{\"default\": \"mid\", \"senders\": {\"7\": \"light\"}, \"names\": {\"7\": \"λ 🌸\"}, \"tier_loops\": {\"mid\": 37}, \"extension\": {\"kept\": [1, 2]}}")))
         ($_ (nt:require a-sender-edit-answers-with-the-senders-name
               (nt:contains (en:run $repo $keep "(energy:set \"7\" \"full\")" ()) "(EnergyResult (\"λ 🌸 arming set to full(50)\"))")))
         ($_ (nt:require unknown-fields-survive-a-sender-edit
               (en:same-json? (en:file $keep) "{\"default\": \"mid\", \"senders\": {\"7\": \"full\"}, \"names\": {\"7\": \"λ 🌸\"}, \"tier_loops\": {\"mid\": 37}, \"extension\": {\"kept\": [1, 2]}}")))
         ($_ (nt:require the-files-mode-is-kept (== (en:mode (en:file $keep)) "640")))
         ($_ (nt:require no-temporary-or-state-files-remain
               (and (en:only-the-file? $keep) (not (fs:exists (path:join $keep "state"))))))
         ; A missing parent is created.
         ($fresh (en:work $home "fresh"))
         ($_ (nt:require a-missing-parent-is-created
               (and (nt:contains (en:run $repo $fresh "(energy:set \"default\" \"mid\")" ()) "(EnergyResult (\"default arming set to mid(30)\"))")
                    (and (== (sf:get (json:parse (fs:read-text (en:file $fresh))) "default" "") "mid") (en:only-the-file? $fresh)))))
         ; Sender names and negative ids resolve to the same sender.
         ($named (en:work $home "named"))
         ($names (("METTACLAW_TELEGRAM_SENDER_NAMES" "-7:λ 🌸")))
         ($_ (nt:require a-sender-is-found-by-name
               (and (nt:contains (en:run $repo $named "(energy:set \"λ 🌸\" \"light\")" $names) "(EnergyResult (\"λ 🌸 arming set to light(10)\"))")
                    (== (en:senders $named) (en:canonical "{\"-7\": \"light\"}")))))
         ($_ (nt:require a-sender-is-found-by-negative-id
               (and (nt:contains (en:run $repo $named "(energy:set \"-7\" \"mid\")" $names) "(EnergyResult (\"λ 🌸 arming set to mid(30)\"))")
                    (== (en:senders $named) (en:canonical "{\"-7\": \"mid\"}")))))
         ; An unwritable parent is a failed save.
         ($blocked (en:work $home "blocked")) ($_ (fs:write (path:join $blocked "memory") "retained evidence"))
         ($_ (nt:require an-unwritable-parent-is-a-failed-save
               (and (nt:contains (en:run $repo $blocked "(energy:set \"default\" \"mid\")" ()) "(EnergyResult (\"energy-set failed: could not persist\"))")
                    (== (fs:read-text (path:join $blocked "memory")) "retained evidence"))))
         ; Concurrent whole-state writes: one writer's state, no shared temporary file.
         ($race (en:work $home "race"))
         ($payloads (map-atom (0 1 2 3) $i
                      (str:join "" ("{\"writer\": " (repr $i) ", \"retained\": \"" (str:join "" (list:repeat 4096 "λ")) "\"}"))))
         ($children (map-atom $payloads $payload
                      (proc:spawn (en:argv (en:program $repo $race (str:join "" ("(energy:save (json-parse " (json-stringify (JsonString $payload)) "))"))
                                                         (str:concat "w" (str:byte-slice (system:uuid4-hex) 0 8))))
                                  ((cwd $repo) (env (en:environment $race ())) (mode stream) (max-bytes 1048576) (timeout-ms 25000) (lifetime owner)))))
         ($outputs (map-atom $children $child
                     (let* (($_ (proc:close-input $child)) ($r (proc:await $child)) ($_ (proc:close $child))) (en:checked $r))))
         ($_ (nt:require every-concurrent-save-succeeds
               (== (filter-atom $outputs $out (not (and (== (get-type $out) String) (nt:contains $out "(EnergyResult (true))")))) ())))
         ($final (en:canonical (fs:read-text (en:file $race))))
         ($_ (nt:require the-file-holds-one-writers-state
               (not (== (filter-atom $payloads $p (== (en:canonical $p) $final)) ()))))
         ($_ (nt:require no-shared-temporary-file-remains
               (== (filter-atom (fs:entries (path:join $race "memory")) $n (not (== $n "energy.json"))) ()))))
   (println! UNIT_ENERGY_SETTINGS_OK))
