; Unit tests of scripts/telegram_journal_retention.metta against journals this
; unit builds through the engine's durable store, and a scripted systemctl on
; PATH; no Python. The expected behaviour is that of the Python it replaces
; (scripts/telegram_journal_retention.py), which can no longer read the store.
!(import! &self ../native_test_support.metta)
!(import! &self ../../scripts/telegram_journal_retention.metta)

(= (rt:home) (nt:required-env "HOME"))
(= (rt:in $name) (path:join (rt:home) $name))
(= (rt:cetta) (path:join (nt:required-env "NATIVE_TEST_CORE") "cetta"))
(= (rt:passed $block) (let $_ (add-atom &self (rt:block-passed $block)) (println! $block)))

; A journal in DIR with received records for updates 100..(100+N-1), values
; with quotes and newlines, and host.inbox entries for the PENDING updates.
(= (rt:received $from $to)
   (if (>= $from $to) ()
     (cons-atom (format-args "(durable:insert \"host.received\" \"telegram-1/{}\" \"update \\\"{}\\\"\\nsecond line\")" ($from $from))
                (rt:received (+ $from 1) $to))))
(= (rt:inbox $pending)
   (map-atom $pending $n (format-args "(durable:insert \"host.inbox\" \"telegram-1/{}\" (host:inbox {}))" ($n $n))))
(= (rt:journal $dir $count $pending)
   (let* (($_ (fs:make-directories $dir 448))
          ($journal (path:join $dir "journal.db"))
          ($opened (jr:admin (rt:cetta) $journal ()))
          ($epoch (car-atom (str:split "\"" (car-atom (jr:results $opened)))))
          ($ops (str:join " " (jr:append (rt:received 100 (+ 100 $count)) (rt:inbox $pending))))
          ($committed (jr:admin (rt:cetta) $journal ((format-args "!(durable:commit {} 0 ({}))" ((repr $epoch) $ops))))))
     (if (nt:contains $committed "(durable:committed ") $journal (Error rt:journal $committed))))
(= (jr:append $front $back) (if (== $front ()) $back (cons-atom (car-atom $front) (jr:append (cdr-atom $front) $back))))
(= (rt:keys $journal) (case (jr:read (rt:cetta) $journal) (((JrRead $e $r $received $pending) (map-atom $received $x (car-atom $x))))))
(= (rt:numbers $from $to) (if (>= $from $to) () (cons-atom (format-args "telegram-1/{}" ($from)) (rt:numbers (+ $from 1) $to))))
(= (rt:manifest $archive)
   (map-atom (filter-atom (str:split "\n" (fs:read-text (path:join $archive "MANIFEST.jsonl"))) $l (not (== $l ""))) $line (json:parse $line)))
(= (rt:options $journal $archive $keep)
   ((JrOption "--journal" $journal) (JrOption "--archive" $archive) (JrOption "--keep" $keep) (JrOption "--cetta" (rt:cetta))))

; Which records go: all but the newest; never one at or after a waiting update.
!(let* (($journal (rt:journal (rt:in "a") 10 ()))
        ((JrRead $epoch $revision $received $pending) (jr:read (rt:cetta) $journal))
        ($_ (nt:require reads-in-mutation-order (nt:equal (map-atom $received $x (car-atom $x)) (rt:numbers 100 110))))
        ($_ (nt:require keeps-the-newest
              (nt:equal (map-atom (jr:candidates $received () 4) $x (car-atom $x)) (rt:numbers 100 106))))
        ($_ (nt:require keeps-from-the-oldest-waiting-update
              (nt:equal (map-atom (jr:candidates $received ("telegram-1/105" "telegram-1/103") 4) $x (car-atom $x)) (rt:numbers 100 103))))
        ($_ (nt:require keeps-all-when-fewer (nt:equal (jr:candidates $received () 20) ())))
        ($_ (nt:require a-record-is-the-store-text
              (nt:equal (car-atom (cdr-atom (car-atom $received)))
                        "(durable:record \"host.received\" \"telegram-1/100\" \"update \\\"100\\\"\nsecond line\")"))))
   (rt:passed UNIT_RETENTION_CANDIDATES_OK))

; A run archives the old records, records the pass, removes exactly them and
; leaves the newest; a second run has nothing to do.
!(let* (($journal (rt:journal (rt:in "b") 10 ("telegram-1/108")))
        ($archive (rt:in "b/archive"))
        ($said (jr:run (rt:options $journal $archive "4")))
        ($_ (nt:require reports-the-pass (nt:contains $said "archived and removed 6 received records to received-")))
        ($_ (nt:require removes-exactly-the-archived (nt:equal (rt:keys $journal) (rt:numbers 106 110))))
        ($entry (car-atom (rt:manifest $archive)))
        ($_ (nt:require manifest-names-the-pass
              (nt:equal ((sf:get $entry "records" 0) (sf:get $entry "first" "") (sf:get $entry "last" "") (sf:get $entry "journal" ""))
                        (6 "telegram-1/100" "telegram-1/105" "journal.db"))))
        ($file (path:join $archive (sf:get $entry "file" "")))
        ($text (case (proc:run ("gzip" "-dc" $file) $archive inherit 30000 0) (((proc:exited 0 $out $err) $out))))
        ($_ (nt:require archive-is-content-addressed
              (nt:equal (sf:get $entry "file" "") (str:join "" ("received-" (str:byte-slice (str:sha256 $text) 0 16) ".jsonl.gz")))))
        ($_ (nt:require archive-digest-recorded (nt:equal (sf:get $entry "sha256" "") (str:sha256 $text))))
        ($lines (map-atom (filter-atom (str:split "\n" $text) $l (not (== $l ""))) $line (json:parse $line)))
        ($_ (nt:require archive-holds-each-record-once-in-order
              (nt:equal (map-atom $lines $l (sf:get $l "key" "")) (rt:numbers 100 106))))
        ($_ (nt:require archive-keeps-the-stored-record
              (nt:equal (sf:get (car-atom $lines) "record" "")
                        "(durable:record \"host.received\" \"telegram-1/100\" \"update \\\"100\\\"\nsecond line\")")))
        ($_ (nt:require nothing-more-to-do (nt:equal (jr:run (rt:options $journal $archive "4")) "nothing to archive")))
        ($_ (nt:require empty-pass-recorded (nt:equal (sf:get (jr:last (rt:manifest $archive)) "empty" False) True))))
   (rt:passed UNIT_RETENTION_RUN_OK))

; A waiting update stops the archive at it.
!(let* (($journal (rt:journal (rt:in "c") 10 ("telegram-1/102")))
        ($said (jr:run (rt:options $journal (rt:in "c/archive") "4")))
        ($_ (nt:require archives-only-before-the-waiting (nt:contains $said "archived and removed 2 received records")))
        ($_ (nt:require keeps-from-the-waiting-update (nt:equal (rt:keys $journal) (rt:numbers 102 110)))))
   (rt:passed UNIT_RETENTION_PENDING_OK))

; The scripted systemctl: logs each call; a unit is active while its state file holds "active".
(= (rt:systemctl)
   "#!/bin/sh\necho \"$*\" >> \"$HOME/systemctl.log\"\ncase \"$2\" in\n  is-active) [ \"$(cat \"$HOME/state-$4\" 2>/dev/null)\" = active ] ;;\n  stop) shift 2; for u in \"$@\"; do echo inactive > \"$HOME/state-$u\"; done ;;\n  start) shift 2; for u in \"$@\"; do echo active > \"$HOME/state-$u\"; done ;;\nesac\n")
(= (rt:log) (filter-atom (str:split "\n" (fs:read-text (rt:in "systemctl.log"))) $l (not (== $l ""))))

; The active units are stopped for the run and started again, also when it fails.
!(let* (($_ (fs:make-directories (rt:in "bin") 448))
        ($_ (fs:write (rt:in "bin/systemctl") (rt:systemctl)))
        ($_ (fs:set-mode (rt:in "bin/systemctl") 448))
        ($_ (fs:write (rt:in "state-svc.socket") "active\n"))
        ($_ (fs:write (rt:in "state-svc.service") "active\n"))
        ($journal (rt:journal (rt:in "d") 6 ()))
        ($said (jr:main ("--journal" $journal "--archive" (rt:in "d/archive") "--keep" "2" "--cetta" (rt:cetta)
                         "--unit" "svc.socket" "--unit" "svc.service" "--unit" "idle.service")))
        ($_ (nt:require runs-with-units (nt:contains $said "archived and removed 4 received records")))
        ($_ (nt:require stops-only-the-active-and-starts-them-again
              (nt:equal (filter-atom (rt:log) $l (not (str:starts-with $l "--user is-active")))
                        ("--user stop svc.socket svc.service" "--user start svc.socket svc.service"))))
        ($_ (fs:remove (rt:in "systemctl.log")))
        ($failed (jr:main ("--journal" (rt:in "d/missing/journal.db") "--archive" (rt:in "d/archive") "--cetta" "/nonexistent/cetta"
                           "--unit" "svc.socket")))
        ($_ (nt:require a-failed-run-is-an-error (case $failed (((Error $w $y) True) ($o False)))))
        ($_ (nt:require started-again-after-a-failure
              (nt:equal (filter-atom (rt:log) $l (not (str:starts-with $l "--user is-active")))
                        ("--user stop svc.socket" "--user start svc.socket"))))
        ($_ (nt:require an-option-without-value (case (jr:main ("--journal")) (((Error $w2 $y2) True) ($o2 False))))))
   (rt:passed UNIT_RETENTION_UNITS_OK))

!(nt:require every-block-ended
   (nt:equal (size-atom (collapse (match &self (rt:block-passed $block) $block))) 4))
!(println! UNIT_JOURNAL_RETENTION_OK)
