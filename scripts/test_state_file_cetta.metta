; Small state files on the core engine: JSON object reads and updates, whole
; replacement by a renamed sibling, and a body run under a lock file.
;   NATIVE_TEST_ARTIFACTS=... $NATIVE_TEST_CORE/cetta --lang petta --import-mode ancestor-walk \
;     --quiet scripts/test_state_file_cetta.metta
!(import! &self ../tests/native_test_support.metta)
!(import! &self ../src/state_file_cetta.metta)

(= (sft:mode $path)
   (case (fs:metadata $path follow-permissions) (((fs:info $k $s $i (fs:permissions $bits)) $bits) ($o $o))))
(= (sft:hidden $dir)
   (filter-atom (fs:entries $dir) $name (str:starts-with $name ".")))

(= (sft:objects $root)
   (let* (($file (path:join $root "object.json"))
          ($_ (nt:require missing-file-empty-object (nt:equal (sf:object $file) (JsonObject ()))))
          ($_ (fs:write $file "{torn"))
          ($_ (nt:require torn-file-empty-object (nt:equal (sf:object $file) (JsonObject ()))))
          ($_ (fs:write $file "[1, 2]"))
          ($_ (nt:require array-file-empty-object (nt:equal (sf:object $file) (JsonObject ()))))
          ($_ (fs:write $file "{\"a\": 1, \"b\": \"x\"}"))
          ($read (sf:object $file))
          ($_ (nt:require object-read (nt:equal (json:get $read "b") (JsonString "x"))))
          ($_ (nt:require put-replaces-in-place
                (nt:equal (sf:put $read "a" (JsonNumber "2"))
                          (JsonObject ((JsonPair "a" (JsonNumber "2")) (JsonPair "b" (JsonString "x")))))))
          ($_ (nt:require put-adds-last
                (nt:equal (sf:put (JsonObject ((JsonPair "a" 1))) "c" 3) (JsonObject ((JsonPair "a" 1) (JsonPair "c" 3))))))
          ($_ (nt:require put-on-non-object-starts-one (nt:equal (sf:put JsonNull "k" 1) (JsonObject ((JsonPair "k" 1))))))
          ($_ (nt:require put-all-in-order
                (nt:equal (sf:put-all (JsonObject ((JsonPair "a" 1))) ((JsonPair "b" 2) (JsonPair "a" 3) (JsonPair "b" 4)))
                          (JsonObject ((JsonPair "a" 3) (JsonPair "b" 4)))))))
     True))

(= (sft:replace $root)
   (let* (($dir (path:join $root "replace")) ($_ (fs:make-directories $dir 504))
          ($file (path:join $dir "state.json"))
          ($_ (nt:require replace-new (nt:equal (sf:replace $file "one" 384 False) True)))
          ($_ (nt:require replaced-text (nt:equal (fs:read-text $file) "one")))
          ($_ (nt:require replaced-mode-private (nt:equal (sft:mode $file) 384)))
          ($_ (nt:require replace-durable (nt:equal (sf:replace $file "two" 432 True) True)))
          ($_ (nt:require replaced-again (nt:equal (fs:read-text $file) "two")))
          ($_ (nt:require replaced-mode-group (nt:equal (sft:mode $file) 432)))
          ($_ (nt:require no-sibling-left (nt:equal (sft:hidden $dir) ())))
          ; A path whose directory does not exist fails before any rename.
          ($absent (path:join (path:join $root "absent-dir") "state.json"))
          ($_ (nt:require missing-directory-fails (sf:failed? (sf:replace $absent "x" 384 True))))
          ; Renaming over a directory fails and removes the sibling.
          ($blocked (path:join $dir "blocked")) ($_ (fs:make-directories (path:join $blocked "inner") 504))
          ($_ (nt:require rename-over-directory-fails (sf:failed? (sf:replace $blocked "x" 384 False))))
          ($_ (nt:require failed-replace-leaves-no-sibling (nt:equal (sft:hidden $dir) ())))
          ($_ (nt:require directory-syncs (nt:equal (sf:sync-directory $dir) True)))
          ($_ (nt:require missing-directory-sync-fails (sf:failed? (sf:sync-directory (path:join $root "nowhere"))))))
     True))

(= (sft:locked $root)
   (let* (($dir (path:join $root "locked")) ($_ (fs:make-directories $dir 504))
          ($lock (path:join $dir ".state.lock"))
          ($_ (nt:require body-answer (nt:equal (sf:locked $lock 432 (+ 2 3)) 5)))
          ($_ (nt:require lock-mode-kept (nt:equal (sft:mode $lock) 432)))
          ($_ (nt:require empty-body-no-answer (nt:equal (sf:locked $lock 432 (empty)) (SfNoAnswer))))
          ($_ (nt:require first-answer-of-many (nt:equal (sf:locked $lock 432 (superpose (7 8))) 7)))
          ($_ (nt:require failing-body-released
                (case (sf:locked $lock 432 (fs:read-text (path:join $dir "missing"))) (($any True)))))
          ; Released: the lock is taken again at once.
          ($_ (nt:require lock-taken-again (nt:equal (sf:locked $lock 432 taken) taken)))
          ($_ (nt:require unopenable-lock-fails
                (case (sf:locked (path:join (path:join $root "no-such-dir") ".lock") 432 never)
                  (((SfLockFailed $why) True) ($other False))))))
     True))

; Native leaves, last-wins lookup, removal and Python's json.dump bytes.
(= (sft:native $root)
   (let* (($file (path:join $root "native.json"))
          ($_ (nt:require native-missing-empty (nt:equal (sf:native-object $file) (JsonObject ()))))
          ($_ (fs:write $file "{\"b\": 1.5, \"a\": 2, \"a\": 3, \"t\": true}"))
          ($read (sf:native-object $file))
          ($_ (nt:require native-number (nt:equal (sf:get $read "b" none) 1.5)))
          ($_ (nt:require native-last-wins (nt:equal (sf:get $read "a" none) 3)))
          ($_ (nt:require native-default (nt:equal (sf:get $read "z" none) none)))
          ($_ (nt:require drop-every-occurrence (nt:equal (sf:get (sf:drop $read "a") "a" gone) gone)))
          ($_ (nt:require python-json-sorted
                (nt:equal (sf:python-json (sf:drop $read "a")) "{\"b\": 1.5, \"t\": true}\n")))
          ($_ (fs:write $file "[1]"))
          ($_ (nt:require native-array-empty (nt:equal (sf:native-object $file) (JsonObject ())))))
     True))

(= (sft:run)
   (let* (($root (nt:join (nt:required-env "NATIVE_TEST_ARTIFACTS") (str:concat "state-file-" (system:uuid4-hex))))
          ($_ (fs:make-directories $root 504))
          ($_ (sft:objects $root)) ($_ (sft:native $root)) ($_ (sft:replace $root)) ($_ (sft:locked $root)))
     StateFilePassed))
!(nt:require state-file-completed (nt:equal (collapse (sft:run)) (StateFilePassed)))
!(println! NATIVE_STATE_FILE_PASSED)
