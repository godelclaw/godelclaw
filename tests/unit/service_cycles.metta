; Operational cycles commit one outcome per cycle without changing search
; answer bags. The real recursive drivers (the Telegram command responder and
; the agent loop) run with synthetic cycles: 500 cycles finish, no cycle
; replays an alternative, and the choice depth stays bounded.
!(import! &self ../native_test_support.metta)
!(import! &self (library list))

; The definition that starts at $head in $source: up to its balancing parenthesis.
(= (sc:definition $source $head)
   (case (str:find $source $head)
     (((TextNotFound $_) "")
      ($at (let $rest (str:python-codepoints (str:byte-slice $source $at (str:byte-length $source)))
             (str:from-python-codepoints (sc:balanced $rest 0 ())))))))
(= (sc:balanced $points $depth $taken)
   (if (== $points ()) (list:reverse $taken)
     (let* (($p (car-atom $points))
            ($next (if (== $p 40) (+ $depth 1) (if (== $p 41) (- $depth 1) $depth)))
            ($kept (cons-atom $p $taken)))
       (if (and (== $next 0) (== $p 41)) (list:reverse $kept) (sc:balanced (cdr-atom $points) $next $kept)))))
(= (sc:choice-depths $text)
   (map-atom (filter-atom (str:split "\n" $text) $line (nt:contains $line "[petta-choice-kind]")) $line
     (sc:depth-of $line)))
(= (sc:depth-of $line)
   (case (str:find $line "depth=")
     (((TextNotFound $_) 0)
      ($at (let $tail (str:byte-slice $line (+ $at 6) (str:byte-length $line))
             (sc:leading-number (str:python-codepoints $tail) 0))))))
(= (sc:leading-number $points $n)
   (if (== $points ()) $n
     (let $p (car-atom $points)
       (if (and (>= $p 48) (<= $p 57)) (sc:leading-number (cdr-atom $points) (+ (* $n 10) (- $p 48))) $n))))
(= (sc:max $numbers $best) (if (== $numbers ()) $best (sc:max (cdr-atom $numbers) (max $best (car-atom $numbers)))))

; Run the driver from $module with $body; the program's output, error and exit code.
(= (sc:probe $repo $home $module $head $body $tag)
   (let* (($dir (path:join $home $tag)) ($_ (fs:make-directories $dir 448))
          ($definition (sc:definition (fs:read-text (path:join $repo $module)) $head))
          ($program (path:join $dir "probe.metta"))
          ($_ (fs:write $program (str:join "" ("!(import! &self system)\n" $definition "\n" $body))))
          ($env (list:append (("METTACLAW_ENGINE" "cetta") ("METTACLAW_ACTIVE_ENGINE" "cetta")
                              ("METTACLAW_ENGINE_STATE_PATH" (path:join $dir "engine")) ("METTACLAW_SKIP_INITIALIZE" "1")
                              ("METTACLAW_RECYCLE_REQUEST_PATH" (path:join $dir "recycle"))
                              ("METTACLAW_WORKING_SET_PATH" (path:join $dir "working.json"))
                              ("METTACLAW_STRATEGY_PATH" (path:join $dir "strategy.db"))
                              ("METTACLAW_HISTORY_PATH" (path:join $dir "history.metta"))
                              ("CETTA_PETTA_CHOICE_KIND_TRACE" "1"))
                             (nt:environment $dir)))
          ($result (proc:run ((system:executable) "--lang" "petta" "--import-mode" "ancestor-walk" $program) $repo $env 30000 8388608)))
     (case $result (((proc:exited $code $out $err) ($definition $code $out $err)) ($bad ("" -1 "" (repr $bad)))))))
(= (sc:committed? $run)
   (let ($definition $code $out $err) $run
     (and (and (!= $definition "") (== $code 0))
          (and (nt:contains $out "CYCLES_DONE")
               (and (not (nt:contains $out "REPLAYED_ALTERNATIVE"))
                    (<= (sc:max (sc:choice-depths $err) 0) 16))))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
         ($responder (sc:probe $repo $home "channels/telegram_control.metta" "(= (tc:serve)"
           (str:join "\n" ("!(bind! &cycle-count (new-state 0))"
                           "(= (fixture:choice) first)"
                           "(= (fixture:choice) second)"
                           "(= (tc:cycle)"
                           "   (if (>= (get-state &cycle-count) 500)"
                           "     (progn (println! CYCLES_DONE) (system:exit 0))"
                           "     (let $next (+ (get-state &cycle-count) 1)"
                           "       (let $_ (change-state! &cycle-count $next)"
                           "         (and True (case (fixture:choice)"
                           "           ((first True) ($other (progn (println! REPLAYED_ALTERNATIVE) False)))))))))"
                           "!(tc:serve)" ""))
           "responder"))
         ($_ (nt:require the-command-responder-commits-each-cycle (sc:committed? $responder)))
         ($loop (sc:probe $repo $home "src/loop.metta" "(= (processLoop "
           (str:join "\n" ("(= (fixture:choice) first)"
                           "(= (fixture:choice) second)"
                           "(= (processCycle $n)"
                           "   (if (>= $n 500)"
                           "     (progn (println! CYCLES_DONE) (system:exit 0))"
                           "     (let $found (collapse (superpose (evidence-a evidence-b evidence-a)))"
                           "       (let $_ (and True (case (fixture:choice)"
                           "                   ((first True) ($other (progn (println! REPLAYED_ALTERNATIVE) False)))))"
                           "         (if (== (size-atom $found) 3) (+ $n 1) (system:exit 2))))))"
                           "!(processLoop 0)" ""))
           "loop"))
         ($_ (nt:require the-agent-loop-commits-its-successor-and-keeps-the-search-bag (sc:committed? $loop))))
   (println! UNIT_SERVICE_CYCLES_OK))
