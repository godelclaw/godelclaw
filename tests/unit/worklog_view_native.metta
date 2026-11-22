; Standalone native godel-worklog-view (B1/B2) contracts:
; - zero lifecycle noise and zero "Reconciled" notices (alias adoption)
; - verbatim copyable commands, multiline heredocs (even with blank lines), exit codes, artifact paths
; - decoded MeTTa commands (no raw Python-list command headers)
; - block-bounded --once (--limit N) preserving session header and never splitting heredocs, vs --full
; - recent-stream selection across many files without stalling
; - clean empty/disabled fallback message in formatted mode vs empty output in --json mode
!(import! &self (library list))
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/state_file_cetta.metta)

(= (wv:json-line $pairs)
   (str:concat (json:stringify-python (JsonObject $pairs)) "\n"))

(= (wv:run-with-env $args $env)
   (nt:exit-ok (proc:run
     (cons-atom (system:executable)
       (cons-atom "--no-results"
         (cons-atom "--lang"
           (cons-atom "petta"
             (cons-atom "--profile"
               (cons-atom "extended"
                 (cons-atom "--import-mode"
                   (cons-atom "ancestor-walk"
                     (cons-atom (path:join (nt:required-env "TEST_REPOSITORY") "scripts/worklog_view_cetta.metta")
                       $args)))))))))
     (system:getcwd) $env 15000 2097152)))

(= (wv:run $args) (wv:run-with-env $args inherit))

(= (wv:count $text $needle)
   (- (size-atom (str:split $needle $text)) 1))

(= (wv:write-old-files $dir $n)
   (if (<= $n 0) True
     (let* (($name (format-args "other.{}.jsonl" ($n)))
            ($path (path:join $dir $name))
            ($line (wv:json-line ((JsonPair "schema" 1) (JsonPair "epoch" "e1") (JsonPair "session" "other")
                    (JsonPair "parent_session" "") (JsonPair "producer" "p") (JsonPair "sequence" $n)
                    (JsonPair "time" 100) (JsonPair "kind" "text.delta") (JsonPair "channel" "commentary")
                    (JsonPair "attempt" "a0") (JsonPair "item" "i0") (JsonPair "text" "OLD_OTHER_NOISE\n")))))
       (progn (fs:write $path $line) (wv:write-old-files $dir (- $n 1))))))

; Build an older multi-chunk (>64 lines) godel stream to verify strict
; per-file chronological draining (no 64-line round-robin inversion after a newer file).
(= (wv:old-chunk-lines $i $max $acc)
   (if (> $i $max) (str:join "" (list:reverse $acc))
     (let $line (wv:json-line ((JsonPair "schema" 1) (JsonPair "epoch" "e1") (JsonPair "session" "godel")
                  (JsonPair "parent_session" "") (JsonPair "producer" "p-old") (JsonPair "sequence" $i)
                  (JsonPair "time" (+ 500 $i)) (JsonPair "kind" "tool.finished") (JsonPair "attempt" "a-old")
                  (JsonPair "item" (format-args "old-{}" ($i)))
                  (JsonPair "command" (if (== $i $max) "['old-tail-marker']" "['nop']"))
                  (JsonPair "text" (if (== $i $max) "OLDER_FILE_LAST_EVENT" "old-ok"))
                  (JsonPair "source" "executor") (JsonPair "status" "returned")))
       (wv:old-chunk-lines (+ $i 1) $max (cons-atom $line $acc)))))

(= (wv:index-of $haystack $needle)
   (case (str:find $haystack $needle)
     (((TextNotFound $_) -1)
      ($idx $idx))))

!(let* (($home (nt:required-env "HOME"))
        ($dir (path:join $home "wv-display"))
        ($_ (fs:make-directories $dir 448))
        ($control (path:join $dir "control.json"))
        ($_ (sf:replace $control "{\"enabled\":true,\"epoch\":\"e1\"}" 384 True))
        ; Empty display -> friendly waiting message in formatted mode, empty in --json
        ($empty-fmt (wv:run ("--dir" $dir "--session" "godel" "--once")))
        ($empty-json (wv:run ("--dir" $dir "--session" "godel" "--once" "--json")))
        ($_ (nt:require empty-formatted-waiting (nt:contains $empty-fmt "Waiting for agent or coding-session output")))
        ($_ (nt:require empty-json-silent (== $empty-json "")))
        ; Seed 12 unrelated historical files, one older >64-line godel stream, then (after a tick) one newer active godel stream
        ($_ (wv:write-old-files $dir 12))
        ($older-stream (path:join $dir "godel.older.jsonl"))
        ($_ (fs:write $older-stream (wv:old-chunk-lines 1 80 ())))
        ($_ (sleep 0.05))
        ; The body has a line after a blank line that reads like a block header.
        ($heredoc "cat > /srv/demo.metta <<'EOF'\n!(println! line1)\n\n✓ heading within data\n!(println! line2)\nEOF")
        ($stream (path:join $dir "godel.active.jsonl"))
        ($events (str:join "" (
          ; Session open + lifecycle noise (must NOT leak)
          (wv:json-line ((JsonPair "schema" 1) (JsonPair "epoch" "e1") (JsonPair "session" "godel")
            (JsonPair "parent_session" "") (JsonPair "producer" "p1") (JsonPair "sequence" 1)
            (JsonPair "time" 1001) (JsonPair "kind" "session.open") (JsonPair "model" "gpt-6.2-sol")
            (JsonPair "text" "godel")))
          (wv:json-line ((JsonPair "schema" 1) (JsonPair "epoch" "e1") (JsonPair "session" "godel")
            (JsonPair "parent_session" "") (JsonPair "producer" "p1") (JsonPair "sequence" 2)
            (JsonPair "time" 1002) (JsonPair "kind" "attempt.open") (JsonPair "attempt" "a1")
            (JsonPair "model" "gpt-6.2-sol")))
          ; B2 alias adoption: commentary streams on item "1", reconciles via text.snapshot on item "answer"
          (wv:json-line ((JsonPair "schema" 1) (JsonPair "epoch" "e1") (JsonPair "session" "godel")
            (JsonPair "parent_session" "") (JsonPair "producer" "p1") (JsonPair "sequence" 3)
            (JsonPair "time" 1003) (JsonPair "kind" "text.delta") (JsonPair "channel" "commentary")
            (JsonPair "attempt" "a1") (JsonPair "item" "1") (JsonPair "text" "Checking state. ")))
          (wv:json-line ((JsonPair "schema" 1) (JsonPair "epoch" "e1") (JsonPair "session" "godel")
            (JsonPair "parent_session" "") (JsonPair "producer" "p1") (JsonPair "sequence" 4)
            (JsonPair "time" 1004) (JsonPair "kind" "text.snapshot") (JsonPair "channel" "commentary")
            (JsonPair "attempt" "a1") (JsonPair "item" "answer") (JsonPair "text" "Checking state. Done.")))
          ; Earlier tool blocks to test --limit trimming
          (wv:json-line ((JsonPair "schema" 1) (JsonPair "epoch" "e1") (JsonPair "session" "godel")
            (JsonPair "parent_session" "") (JsonPair "producer" "p1") (JsonPair "sequence" 5)
            (JsonPair "time" 1005) (JsonPair "kind" "tool.dispatching") (JsonPair "attempt" "a1")
            (JsonPair "item" "t1") (JsonPair "command" "['nop']") (JsonPair "source" "executor") (JsonPair "status" "dispatching")))
          (wv:json-line ((JsonPair "schema" 1) (JsonPair "epoch" "e1") (JsonPair "session" "godel")
            (JsonPair "parent_session" "") (JsonPair "producer" "p1") (JsonPair "sequence" 6)
            (JsonPair "time" 1006) (JsonPair "kind" "tool.finished") (JsonPair "attempt" "a1")
            (JsonPair "item" "t1") (JsonPair "text" "EARLY_BLOCK_ONE") (JsonPair "source" "executor") (JsonPair "status" "returned")))
          (wv:json-line ((JsonPair "schema" 1) (JsonPair "epoch" "e1") (JsonPair "session" "godel")
            (JsonPair "parent_session" "") (JsonPair "producer" "p1") (JsonPair "sequence" 7)
            (JsonPair "time" 1007) (JsonPair "kind" "tool.dispatching") (JsonPair "attempt" "a1")
            (JsonPair "item" "t2") (JsonPair "command" "['rest']") (JsonPair "source" "executor") (JsonPair "status" "dispatching")))
          (wv:json-line ((JsonPair "schema" 1) (JsonPair "epoch" "e1") (JsonPair "session" "godel")
            (JsonPair "parent_session" "") (JsonPair "producer" "p1") (JsonPair "sequence" 8)
            (JsonPair "time" 1008) (JsonPair "kind" "tool.finished") (JsonPair "attempt" "a1")
            (JsonPair "item" "t2") (JsonPair "text" "EARLY_BLOCK_TWO") (JsonPair "source" "executor") (JsonPair "status" "returned")))
          ; Final tool block with multiline heredoc containing a blank line + exit 7 + full artifact path
          (wv:json-line ((JsonPair "schema" 1) (JsonPair "epoch" "e1") (JsonPair "session" "godel")
            (JsonPair "parent_session" "") (JsonPair "producer" "p1") (JsonPair "sequence" 9)
            (JsonPair "time" 1009) (JsonPair "kind" "tool.dispatching") (JsonPair "attempt" "a1")
            (JsonPair "item" "t3") (JsonPair "name" "exec_command") (JsonPair "command" $heredoc)
            (JsonPair "source" "executor") (JsonPair "status" "dispatching")))
          (wv:json-line ((JsonPair "schema" 1) (JsonPair "epoch" "e1") (JsonPair "session" "godel")
            (JsonPair "parent_session" "") (JsonPair "producer" "p1") (JsonPair "sequence" 10)
            (JsonPair "time" 1011) (JsonPair "kind" "tool.finished") (JsonPair "attempt" "a1")
            (JsonPair "item" "t3") (JsonPair "text" "FINAL_HEREDOC_OUT\n") (JsonPair "source" "executor")
            (JsonPair "status" "exited") (JsonPair "exit_code" 7) (JsonPair "artifact" "/srv/logs/final-heredoc.log")))
          (wv:json-line ((JsonPair "schema" 1) (JsonPair "epoch" "e1") (JsonPair "session" "godel")
            (JsonPair "parent_session" "") (JsonPair "producer" "p1") (JsonPair "sequence" 11)
            (JsonPair "time" 1012) (JsonPair "kind" "attempt.finished") (JsonPair "attempt" "a1") (JsonPair "status" "completed"))))))
        ($_ (fs:write $stream $events))
        ; 1. Full --once output
        ($full (wv:run ("--dir" $dir "--session" "godel" "--once" "--full")))
        ($_ (nt:require no-lifecycle-noise
              (and (not (nt:contains $full "attempt.open"))
                   (and (not (nt:contains $full "attempt.finished"))
                        (and (not (nt:contains $full "tool.dispatching"))
                             (not (nt:contains $full "interrupted")))))))
        ($_ (nt:require b2-no-reconciled-notice (not (nt:contains $full "Reconciled"))))
        ($_ (nt:require b2-commentary-once (== (wv:count $full "Checking state.") 1)))
        ($_ (nt:require b2-commentary-suffix (nt:contains $full "Done.")))
        ($_ (nt:require decoded-metta-commands
              (and (nt:contains $full "(nop)")
                   (and (nt:contains $full "(rest)")
                        (and (not (nt:contains $full "['nop']"))
                             (not (nt:contains $full "['rest']")))))))
        ($_ (nt:require heredoc-verbatim (nt:contains $full $heredoc)))
        ($_ (nt:require exit-and-artifact
              (and (nt:contains $full "exit 7")
                   (nt:contains $full "output: /srv/logs/final-heredoc.log"))))
        ($_ (nt:require scoped-excludes-other (not (nt:contains $full "OLD_OTHER_NOISE"))))
        ; Strict chronological multi-file drain: event #80 of older file MUST appear BEFORE event #1 of newer file
        ($idx-older (wv:index-of $full "OLDER_FILE_LAST_EVENT"))
        ($idx-newer (wv:index-of $full "EARLY_BLOCK_ONE"))
        ($_ (nt:require multi-file-chronological-order
              (and (>= $idx-older 0) (and (>= $idx-newer 0) (< $idx-older $idx-newer)))))
        ; 2. Bounded --once --limit 2 keeps session header, omission notice, and complete final heredoc block without splitting!
        ($bounded (wv:run ("--dir" $dir "--session" "godel" "--once" "--limit" "2")))
        ($_ (nt:require pipe-has-no-sgr (not (nt:contains $bounded "\u001b["))))
        ($_ (nt:require bounded-keeps-session-header (nt:contains $bounded "godel")))
        ($_ (nt:require bounded-notes-omission (nt:contains $bounded "earlier blocks omitted; pass --full")))
        ($_ (nt:require bounded-omits-early (not (nt:contains $bounded "EARLY_BLOCK_ONE"))))
        ($_ (nt:require bounded-keeps-heredoc-whole (nt:contains $bounded $heredoc)))
        ($_ (nt:require bounded-keeps-artifact (nt:contains $bounded "output: /srv/logs/final-heredoc.log")))
        ; 2b. Bounded --once --limit 1 must keep the entire final heredoc block intact (never split on internal blank line!)
        ($one (wv:run ("--dir" $dir "--session" "godel" "--once" "--limit" "1")))
        ($_ (nt:require limit-one-keeps-heredoc-atomic
              (and (nt:contains $one $heredoc)
                   (and (nt:contains $one "output: /srv/logs/final-heredoc.log")
                        (not (nt:contains $one "EARLY_BLOCK_TWO"))))))
        ; 3. FORCE_COLOR=1 keeps SGR styling and still trims blocks cleanly across SGR-prefixed headers
        ($colored-env (("HOME" $home) ("PATH" (system:getenv-or-default "PATH" "/usr/bin:/bin"))
                       ("LANG" "C.UTF-8") ("FORCE_COLOR" "1") ("TERM" "xterm-256color")))
        ($colored (wv:run-with-env ("--dir" $dir "--session" "godel" "--once" "--limit" "2") $colored-env))
        ($_ (nt:require force-color-has-sgr (nt:contains $colored "\u001b[")))
        ($_ (nt:require force-color-notes-omission (nt:contains $colored "earlier blocks omitted; pass --full")))
        ($_ (nt:require force-color-omits-early (not (nt:contains $colored "EARLY_BLOCK_ONE"))))
        ($_ (nt:require force-color-keeps-artifact
              (and (nt:contains $colored "output: ")
                   (nt:contains $colored "/srv/logs/final-heredoc.log")))))
   (println! UNIT_WORKLOG_VIEW_NATIVE_OK))

; A one-shot view ends with the commands still in flight, shown as running
; under their session; a finished command before them stays visible.
(= (wv:tool-line $seq $kind $item $command $status)
   (wv:json-line ((JsonPair "schema" 1) (JsonPair "epoch" "e1") (JsonPair "session" "work-1")
     (JsonPair "parent_session" "godel") (JsonPair "producer" "p1") (JsonPair "sequence" $seq)
     (JsonPair "time" (+ 2000 $seq)) (JsonPair "kind" $kind) (JsonPair "attempt" "a1")
     (JsonPair "item" $item) (JsonPair "name" "exec_command") (JsonPair "command" $command)
     (JsonPair "source" "executor") (JsonPair "status" $status))))
!(let* (($dir (path:join (nt:required-env "HOME") "wv-in-flight"))
        ($_ (fs:make-directories $dir 448))
        ($_ (sf:replace (path:join $dir "control.json") "{\"enabled\":true,\"epoch\":\"e1\"}" 384 True))
        ($running "cd /srv/work && python3 -m unittest tests/test_ledger.py -v")
        ($_ (fs:write (path:join $dir "work-1.active.jsonl")
              (str:join "" ((wv:json-line ((JsonPair "schema" 1) (JsonPair "epoch" "e1") (JsonPair "session" "work-1")
                              (JsonPair "parent_session" "godel") (JsonPair "producer" "p1") (JsonPair "sequence" 0)
                              (JsonPair "time" 2000) (JsonPair "kind" "attempt.open") (JsonPair "attempt" "a1")
                              (JsonPair "item" "a1") (JsonPair "status" "awaiting")))
                            (wv:tool-line 1 "tool.dispatching" "x1" "ls /srv/work/finished-listing" "dispatching")
                            (wv:tool-line 2 "tool.finished" "x1" "ls /srv/work/finished-listing" "returned")
                            (wv:tool-line 3 "tool.dispatching" "x2" $running "dispatching")
                            (wv:tool-line 4 "tool.started" "x2" $running "running")))))
        ($out (wv:run ("--dir" $dir "--once")))
        ($_ (nt:require in-flight-command-shown (nt:contains $out $running)))
        ($_ (nt:require in-flight-marked-running (nt:contains $out "◌ shell")))
        ($_ (nt:require in-flight-after-finished
              (> (wv:index-of $out $running) (wv:index-of $out "ls /srv/work/finished-listing"))))
        ($_ (nt:require in-flight-under-session (nt:contains $out "work-1"))))
   (println! UNIT_WORKLOG_VIEW_IN_FLIGHT_OK))
