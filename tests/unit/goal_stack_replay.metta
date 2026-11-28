; The goal stack answers as the Python writer it replaced did, byte for byte.
; tests/fixtures/goal_stack_reference.json.gz holds that writer's answers to
; every call its own tests and the native comparison suites made: kernel
; passes and 100-pass tracks, human wakes, goal writes and drops, the goals
; and affect views, over legacy and duplicate records, flags, budgets, mood
; traces of every width, byte offsets into growing, shrinking and invalid
; logs, non-finite and overflow-sized numbers, invalid UTF-8 and NUL bytes.
; For each call it has the goal directory's files before it, its result, and
; each file's digest after it. Here every call runs on the native writer from
; the same files, with the same batch remembered as applied; its result and
; every file it leaves must match. A call that starts from the files the
; previous call left runs on in that directory. Compost lines begin with the
; local time, compared as STAMP. Calls under injected faults are not in the
; record; the native fault cases are in goal_stack.metta.
!(import! &self ../native_test_support.metta)

(= (gr:lit $text) (json-stringify (JsonString $text)))
(= (gr:get $object $key)
   (case $object
     (((JsonObject $pairs)
       (case (filter-atom $pairs $pair (let (JsonPair $name $_) $pair (== $name $key)))
         ((((JsonPair $_ $value)) $value) ($_ GrAbsent))))
      ($_ GrAbsent))))
(= (gr:items $array) (case $array (((JsonArray $items) $items) ($_ ()))))
(= (gr:pairs $object) (case $object (((JsonObject $pairs) $pairs) ($_ ()))))
(= (gr:nth $items $n) (if (== $n 0) (car-atom $items) (gr:nth (cdr-atom $items) (- $n 1))))
(= (gr:number $items $index)
   (if (== $items ()) () (cons-atom ($index (car-atom $items)) (gr:number (cdr-atom $items) (+ $index 1)))))

; The distinct lines, by number.
!(bind! &gr-lines (new-space))
(= (gr:line $n) (match &gr-lines (gr-line $n $text) $text))

; A file restored: its text lines written as they are, or its bytes, when
; they are not text, decoded from base64 by the shell.
(= (gr:restore-file $dir ($name $stored))
   (case $stored
     (((JsonArray $ids) (fs:write (path:join $dir $name) (str:join "" (map-atom $ids $n (gr:line $n)))))
      ($bytes (let* (($_ (fs:write (path:join $dir (str:concat $name ".b64")) (gr:get $bytes "b64"))))
        (nt:exit-ok (proc:run ("sh" "-c" "base64 -d \"$0.b64\" > \"$0\" && rm \"$0.b64\"" $name) $dir
                              (("PATH" (system:getenv-or-default "PATH" "/usr/bin:/bin"))) 30000 65536)))))))
(= (gr:restore $dir $before)
   (let $_ (fs:make-directories $dir 448)
     (map-atom (gr:pairs $before) $pair (let (JsonPair $name $stored) $pair (gr:restore-file $dir ($name $stored))))))

; Each call's directory: its own for a call that stores its files, the one
; before it for a call that continues.
(= (gr:places $numbered $home $current)
   (if (== $numbered ()) ()
     (let* ((($i $record) (car-atom $numbered))
            ($dir (if (== (gr:get $record "continues") True) $current (path:join $home (str:concat "r-" (repr $i))))))
       (cons-atom ($i $record $dir) (gr:places (cdr-atom $numbered) $home $dir)))))

; The call as a guest expression on its directory.
(= (gr:token $value) (if (== $value JsonNull) "GkNoWake" (gr:lit $value)))
(= (gr:call $record $dir)
   (let* (($args (gr:items (gr:get $record "args")))
          ($goals (gr:lit (path:join $dir (gr:get $record "goals"))))
          ($log (if (== (gr:get $record "log") "") "\"\"" (gr:lit (path:join $dir (gr:get $record "log"))))))
     (case (gr:get $record "op")
       (("kernel_pass" (str:join " " ("(gk:kernel-status" $goals $log (str:concat (gr:token (gr:nth $args 0)) ")"))))
        ("human_wake" (str:join " " ("(let $remembered (change-state! &gk-last-wake" (gr:token (gr:nth $args 1)) ")"
                                     "(gk:human-wake" (gr:lit (gr:nth $args 0)) $goals (str:concat $log "))"))))
        ("goal_write" (str:join " " ("(gk:goal-write" (gr:lit (gr:nth $args 0)) (str:concat $goals ")"))))
        ("goal_drop" (str:join " " ("(gk:goal-drop" (gr:lit (gr:nth $args 0)) (gr:lit (gr:nth $args 1)) (str:concat $goals ")"))))
        ("affect_view" (str:join "" ("(gv:affect " $goals ")")))
        ("goals_view" (str:join "" ("(gv:goals " $goals " True)")))))))

; The guest writes each answer with the digests of the files it left: a
; kernel pass's message, a wake's "fresh applied message", a refusal as
; "raises why", anything else as written; then "name digest" per file,
; compost times as STAMP.
(= (gr:guest-definitions)
   (str:join "\n" (
     "(= (gr:show $x) (case $x (((GkStatus $a $m) $m) ((GoalWake $f $a $m) (str:join \" \" ((repr $f) (repr $a) $m))) ((Error $k $why) (str:concat \"raises \" (if (== (get-type $why) String) $why (repr $why)))) ($other (if (== (get-type $other) String) $other (str:concat \"VALUE \" (repr $other)))))))"
     "!(bind! &gr-stamp (new-state (re:compile \"(?m)^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2} \")))"
     "(= (gr:digest $path $name) (str:byte-slice (if (== $name \"goals_compost.log\") (str:sha256 (re:replace-literal (get-state &gr-stamp) (fs:read-text $path) \"STAMP \")) (case (fs:sha256-file $path) (((fs:hashed-v1 $hex $_) $hex) ($other (repr $other))))) 0 16))"
     "(= (gr:files $dir) (map-atom (sort-atom (map-atom (filter-atom (fs:entries $dir) $name (fs:is-file (str:join \"/\" ($dir $name)))) $name ($name (gr:digest (str:join \"/\" ($dir $name)) $name)))) $pair (let ($name $hex) $pair (str:join \" \" ($name $hex)))))"
     "(= (gr:answer $x $dir) (str:join \"\\n\" (cons-atom (gr:show $x) (gr:files $dir))))"
     "")))
(= (gr:expected $record)
   (let* (($answer (case (gr:get $record "result")
            ((GrAbsent (str:concat "raises " (gr:nth (gr:items (gr:get $record "raises")) 1)))
             ((JsonArray ($applied $message)) $message)
             ((JsonArray ($fresh $applied $message)) (str:join " " ((repr $fresh) (repr $applied) $message)))
             ($text $text))))
          ($files (map-atom (sort-atom (map-atom (gr:pairs (gr:get $record "after")) $pair (let (JsonPair $name $hex) $pair ($name $hex))))
                    $pair (let ($name $hex) $pair (str:join " " ($name $hex))))))
     (str:join "\n" (cons-atom $answer $files))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
        ($fixture (path:join $repo "tests/fixtures/goal_stack_reference.json.gz"))
        ($document (case (proc:run ("gzip" "-dc" $fixture) $repo (("PATH" (system:getenv-or-default "PATH" "/usr/bin:/bin"))) 60000 16777216)
                     (((proc:exited 0 $decoded $_) (json:parse $decoded)) ($failed (Failed $failed)))))
        ($loaded (map-atom (gr:number (gr:items (gr:get $document "lines")) 0) $entry
                   (let ($number $line) $entry (add-atom &gr-lines (gr-line $number $line)))))
        ($records (gr:items (gr:get $document "records")))
        ($places (gr:places (gr:number $records 0) $home $home))
        ($restored (map-atom $places $place (let ($i $record $dir) $place
                     (if (== (gr:get $record "continues") True) True (gr:restore $dir (gr:get $record "before"))))))
        ($program (path:join $home "replay.metta"))
        ($written (fs:write $program (str:join "" (cons-atom
              (str:join "" ("!(import! &self " (path:join $repo "src/goal_bookkeeping_cetta.metta") ")\n" (gr:guest-definitions)))
              (map-atom $places $place (let ($i $record $dir) $place
                (str:join "" ("!(fs:write " (gr:lit (path:join $home (str:concat "out-" (repr $i))))
                              " (gr:answer " (gr:call $record $dir) " " (gr:lit $dir) "))\n"))))))))
        ($run (proc:run ((system:executable) "--lang" "petta" "--import-mode" "ancestor-walk" $program) $repo
                        (nt:environment $home) 600000 1048576))
        ($_ (nt:require the-replay-ran (case $run (((proc:exited 0 $_ $_) True) ($refused (let $_ (println! (REPLAY-RUN $refused)) False))))))
        ($wrong (filter-atom (map-atom $places $place (let ($i $record $dir) $place
                   (let* (($answer (fs:read-text (path:join $home (str:concat "out-" (repr $i)))))
                          ($expected (gr:expected $record)))
                     ($i (gr:get $record "op") (== $answer $expected) $answer $expected))))
                 $row (let ($_ $_ $same $_ $_) $row (not $same))))
        ($_ (map-atom $wrong $row (println! (MISMATCH $row)))))
   (nt:require every-call-answers-and-leaves-the-files-as-the-python-writer-did
     (and (== $wrong ()) (> (size-atom $places) 800))))

!(println! UNIT_GOAL_STACK_REPLAY_OK)
