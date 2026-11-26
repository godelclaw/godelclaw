; The coding executor's owned processes, with real delayed commands and their
; durable artifacts: output is visible while the command runs (a character
; split across reads arrives whole), the terminal receipt carries the exit
; code and artifact, poll and cancel never start the command again, and a new
; runtime reports an uncertain execution without running it.
!(import! &self ../native_test_support.metta)
!(import! &self (library list))

(= (sp:environment $dir)
   (list:append (("METTACLAW_ENGINE" "cetta") ("METTACLAW_ACTIVE_ENGINE" "cetta")
                 ("METTACLAW_STREAM_DIR" (path:join $dir "display")))
                (nt:environment $dir)))
; A program over coding_session.metta; arguments come from args.json in $dir.
(= (sp:program $repo $dir $forms)
   (let $program (path:join $dir "probe.metta")
     (progn (fs:write $program (str:join "" ("!(import! &self (library lib_import))\n!(import! &self "
                                             (path:join $repo "src/coding_session.metta") ")\n" $forms)))
            $program)))
(= (sp:argv $program) ((system:executable) "--lang" "petta" "--import-mode" "ancestor-walk" $program))
(= (sp:args $dir $pairs) (fs:write (path:join $dir "args.json") (json-stringify (JsonObject $pairs))))
(= (sp:quoted $text) (json-stringify (JsonString $text)))
; Every display record, in file order.
(= (sp:records $dir)
   (let $display (path:join $dir "display")
     (if (not (fs:exists $display)) ()
       (sp:flatten (map-atom (fs:entries $display) $name
         (map-atom (filter-atom (str:split "\n" (fs:read-text (path:join $display $name))) $l (not (== $l ""))) $line
           (json:parse $line)))))))
(= (sp:flatten $lists) (if (== $lists ()) () (list:append (car-atom $lists) (sp:flatten (cdr-atom $lists)))))
(= (sp:field $record $key) (case $record (((JsonObject $pairs) (sp:pair $pairs $key)) ($other ""))))
(= (sp:pair $pairs $key)
   (if (== $pairs ()) ""
     (let (JsonPair $k $v) (car-atom $pairs)
       (if (== $k $key) (case $v (((JsonString $s) $s) ((JsonNumber $n) $n) ($x $x))) (sp:pair (cdr-atom $pairs) $key)))))
(= (sp:output-text $dir)
   (str:join "" (map-atom (filter-atom (sp:records $dir) $r (== (sp:field $r "kind") "tool.output")) $r (sp:field $r "text"))))
(= (sp:wait-for $child $dir $needle $left)
   (if (nt:contains (sp:output-text $dir) $needle) True
     (if (<= $left 0) False (progn (proc:wait $child 20) (sp:wait-for $child $dir $needle (- $left 1))))))
(= (sp:last-finished $records $found)
   (if (== $records ()) $found
     (sp:last-finished (cdr-atom $records)
       (if (== (sp:field (car-atom $records) "kind") "tool.finished") (car-atom $records) $found))))
(= (sp:run $repo $dir $program)
   (case (proc:run (sp:argv $program) $repo (sp:environment $dir) 60000 1048576)
     (((proc:exited $code $out $err) ($code $out)) ($bad (-1 (repr $bad))))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
         ; 1. Output while running, a split character whole, the terminal receipt.
         ($live (path:join $home "live")) ($_ (fs:make-directories $live 448))
         ($_ (sp:args $live ((JsonPair "cmd" (JsonString "printf 'first '; printf '\\360\\237'; sleep .2; printf '\\220\\210\\n'; sleep 1; printf 'last\\n' >&2; exit 7")))))
         ($program (sp:program $repo $live (str:join "" ("!(println! (coding:exec-owned " (sp:quoted $live) " " (sp:quoted $live)
                                                         " (json-parse (fs:read-text " (sp:quoted (path:join $live "args.json")) "))))\n"))))
         ($child (proc:spawn (sp:argv $program) ((cwd $repo) (env (sp:environment $live)) (mode stream) (max-bytes 1048576)
                                                (timeout-ms 30000) (lifetime owner))))
         ($_ (proc:close-input $child))
         ($_ (nt:require output-is-visible-while-the-command-runs
               (and (sp:wait-for $child $live "🐈" 150) (not (== (proc:status $child) proc:ready)))))
         ($_ (nt:require a-split-character-arrives-whole (nt:contains (sp:output-text $live) "first 🐈\n")))
         ($result (proc:await $child)) ($_ (proc:close $child))
         ($_ (nt:require the-report-carries-the-exit-code
               (case $result (((proc:result (proc:exited 0) $how $out $err $_) (nt:contains $out "Exit code: 7")) ($bad False)))))
         ($final (sp:last-finished (sp:records $live) ()))
         ($_ (nt:require the-terminal-receipt-has-the-exit-code-and-artifact
               (and (== (sp:field $final "exit_code") 7)
                    (== (fs:read-text (sp:field $final "artifact")) "first 🐈\nlast\n"))))
         ; 2. Yield, poll, repeat and cancel never start the command again.
         ($held (path:join $home "held")) ($_ (fs:make-directories $held 448))
         ($_ (sp:args $held ((JsonPair "cmd" (JsonString "echo once >> count; printf start; sleep 4; printf finish"))
                             (JsonPair "yield_time_ms" (JsonNumber "0")))))
         ($root (sp:quoted $held))
         (($code2 $out2) (sp:run $repo $held (sp:program $repo $held (str:join "" (
           "!(let* (($report (coding:exec-owned " $root " " $root " (json-parse (fs:read-text " (sp:quoted (path:join $held "args.json")) "))))\n"
           "        ($id (str:trim (car-atom (cdr-atom (str:split \":\" (car-atom (str:split \"\\n\" $report)))))))\n"
           "        ($args (JsonObject ((JsonPair \"execution_id\" (JsonString $id)) (JsonPair \"yield_time_ms\" (JsonNumber \"1500\"))))))\n"
           "   (progn (println! (INITIAL $report))\n"
           "     (println! (POLL (coding:poll " $root " $args)))\n"
           "     (println! (CANCEL (coding:cancel " $root " $args)))\n"
           "     (println! (AGAIN (coding:poll " $root " $args)))))\n")))))
         ($_ (nt:require a-yielded-command-reports-still-running (and (== $code2 0) (nt:contains $out2 "Still running"))))
         ($_ (nt:require cancel-signals-the-command (nt:contains $out2 "signaled")))
         ($_ (nt:require the-command-ran-once (== (fs:read-text (path:join $held "count")) "once\n")))
         ($_ (nt:require one-execution-log
               (== (size-atom (filter-atom (fs:entries (path:join $held "executions")) $n (str:ends-with $n ".log"))) 1)))
         ; 3. A new runtime reports an uncertain execution without running it.
         ($fresh (path:join $home "fresh")) ($executions (path:join $fresh "executions"))
         ($_ (fs:make-directories $executions 448))
         ($id "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
         ($_ (fs:write (path:join $executions (str:concat $id ".json"))
               (json-stringify (JsonObject ((JsonPair "id" (JsonString $id)) (JsonPair "command" (JsonString "do not replay"))
                                            (JsonPair "status" (JsonString "dispatching")) (JsonPair "started" (JsonNumber "0.0")))))))
         ($log (path:join $executions (str:concat $id ".log")))
         ($_ (nt:exit-ok (proc:run ("sh" "-c" "printf 'partial\\377' > \"$1\"; cp \"$1\" \"$1.expected\"" "log" $log) $fresh inherit 5000 64)))
         ($_ (sp:args $fresh ((JsonPair "execution_id" (JsonString $id)))))
         (($code3 $out3) (sp:run $repo $fresh (sp:program $repo $fresh (str:join "" ("!(println! (coding:poll " (sp:quoted $fresh)
                                                " (json-parse (fs:read-text " (sp:quoted (path:join $fresh "args.json")) "))))\n")))))
         ($_ (nt:require an-unknown-outcome-is-reported-uncertain
               (and (== $code3 0) (and (nt:contains $out3 "uncertain") (nt:contains $out3 "not replayed")))))
         ($_ (nt:require the-partial-log-is-untouched
               (case (proc:run ("cmp" $log (str:concat $log ".expected")) $fresh inherit 5000 64)
                 (((proc:exited 0 $_o $_e) True) ($bad False))))))
   (println! UNIT_STREAMING_PROCESS_OK))
