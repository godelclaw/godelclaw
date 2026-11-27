; The file skills on a tree with mixed line endings, invalid UTF-8, NUL, a
; long line, an ignored .git and file, directory and broken links
; (tests/fixtures/file_tools_tree.sh): read-lines windows, ls-tree pages,
; grep-files patterns and file-io:exists answer exactly as the Python file
; skills did (tests/fixtures/file_tools_reference.json). Writes replace a
; file at once, keep its mode and keep the previous version as a backup;
; an edit needs exactly one occurrence; a new file is private; a state
; replace creates its parents and keeps no backup; an edit of invalid UTF-8
; leaves the file as it was; nothing temporary is left beside a file. An
; append needs an existing file, and a replace refused at the rename keeps
; what was there and cleans up its temporary.
!(import! &self ../native_test_support.metta)
!(import! &self (library list))
!(import! &self (library json))

(= (ft:root) (nt:required-env "TEST_REPOSITORY"))
(= (ft:home) (nt:required-env "HOME"))
(= (ft:work) (path:join (ft:home) "work"))
(= (ft:engine) (path:join (nt:required-env "NATIVE_TEST_CORE") "cetta"))
(= (ft:fill $text) (str:join (ft:work) (str:split "{work}" $text)))
(= (ft:env)
   (list:append (("METTACLAW_ACTIVE_ENGINE" "cetta") ("METTACLAW_INSTANCE" "test-agent")
                 ("METTACLAW_ENGINE_STATE_PATH" (path:join (ft:work) "engine"))
                 ("XDG_STATE_HOME" (path:join (ft:work) "state")))
                (nt:environment (ft:home))))
(= (ft:prelude)
   ("!(import! &self (library fs))"
    (str:concat "!(import! &self " (str:concat (repr (path:join (ft:root) "src/file_io.metta")) ")"))))
; Each call's single answer, written to answer-N by one guest.
(= (ft:answers $tag $calls)
   (let* (($dir (path:join (ft:home) $tag)) ($_ (fs:make-directories $dir 448))
          ($forms (map-atom (list:range 0 (size-atom $calls)) $i
                    (str:join "" ("!(let $answers (collapse " (list:at $calls $i) ") (fs:write "
                                  (repr (path:join $dir (str:concat "answer-" (repr $i))))
                                  " (if (== (size-atom $answers) 1) (car-atom $answers) \"WRONG_ANSWER_COUNT\")))"))))
          ($source (path:join $dir "calls.metta"))
          ($_ (fs:write $source (str:concat (str:join "\n" (list:append (ft:prelude) $forms)) "\n")))
          ($run (proc:run ((ft:engine) "--lang" "petta" "--import-mode" "ancestor-walk" $source)
                          (ft:root) (ft:env) 120000 1048576)))
     (case $run
       (((proc:exited 0 $out $err)
         (map-atom (list:range 0 (size-atom $calls)) $j
           (let $answer (path:join $dir (str:concat "answer-" (repr $j)))
             (if (fs:exists $answer) (fs:read-text $answer) (NoAnswer $j)))))
        ($other (let $_ (println! (FILE_TOOLS_GUEST_FAILED $tag $other)) $other))))))
; An answer as the reference recorded it; a boolean is written true or false.
(= (ft:same $answer $expected)
   (if (ft:member? $expected ("True" "False")) (== (str:lower $answer) (str:lower $expected)) (== $answer $expected)))
(= (ft:member? $x $xs) (if (== $xs ()) False (if (== (car-atom $xs) $x) True (ft:member? $x (cdr-atom $xs)))))
(= (ft:digest $path) (case (fs:sha256-file $path) (((fs:hashed-v1 $hex $info) $hex) ($other (NoDigest $other)))))
(= (ft:mode $path)
   (case (fs:metadata-v2 $path no-follow)
     (((fs:info-v2 $kind $size $identity $modified $changed (fs:permissions $mode) $blocks) $mode) ($other (NoMode $other)))))
(= (ft:after $text $marker)
   (case (str:find $text $marker)
     (((TextNotFound $m) "") ($at (str:byte-slice $text (+ $at (str:byte-length $marker)) (str:byte-length $text))))))
; No ".NAME.*" temporary beside the file.
(= (ft:no-temporary $path)
   (let* (($parent (path:dirname $path)) ($name (ut-last (path:parts $path)))
          ($names (fs:entries $parent)))
     (== (filter-atom $names $n (str:starts-with (if (== (get-type $n) String) $n (repr $n)) (str:concat "." (str:concat $name ".")))) ())))
(= (ut-last $xs) (if (== (cdr-atom $xs) ()) (car-atom $xs) (ut-last (cdr-atom $xs))))
(= (ft:hex16 $hex) (str:byte-slice $hex 0 16))
; One write step's receipt against what the Python skill left.
(= (ft:step-ok $step $receipt)
   (let $kind (json:get $step "kind")
     (case $kind
       (("check" (== $receipt (ft:fill (json:get $step "expected"))))
        ("failed" (and (str:starts-with $receipt (json:get $step "prefix"))
                       (== (ft:digest (ft:fill (json:get $step "path"))) (json:get $step "sha256"))))
        ("written"
         (let* (($path (ft:fill (json:get $step "path")))
                ($previous (json:get $step "previous_sha256"))
                ($backup (ft:after $receipt " previous=")))
           (and (str:starts-with $receipt (str:concat "WROTE " (str:concat $path " op=")))
           (and (nt:contains $receipt (str:join "" ("bytes=" (repr (json:get $step "bytes")) " sha256=" (ft:hex16 (json:get $step "sha256")))))
           (and (== (ft:digest $path) (json:get $step "sha256"))
           (and (== (ft:mode $path) (json:get $step "mode"))
           (and (if (== $previous JsonNull) (== $backup "") (== (ft:digest $backup) $previous))
                (ft:no-temporary $path))))))))))))
(= (ft:steps-failing $steps $index)
   (if (== $steps ()) ()
     (let* (($step (car-atom $steps))
            (($receipt) (ft:answers (str:concat "write-" (repr $index)) ((ft:fill (json:get $step "call")))))
            ($ok (ft:step-ok $step $receipt))
            ($rest (ft:steps-failing (cdr-atom $steps) (+ $index 1))))
       (if $ok $rest (cons-atom (StepDiffers $index (json:get $step "call") $receipt) $rest)))))

!(let* (($reference (json:parse (fs:read-text (path:join (ft:root) "tests/fixtures/file_tools_reference.json"))))
        ($built (proc:run ("/bin/sh" (path:join (ft:root) "tests/fixtures/file_tools_tree.sh") (ft:work))
                          (ft:home) (nt:environment (ft:home)) 30000 4096))
        ($_ (nt:require the-tree-is-built (case $built (((proc:exited 0 $o $e) True) ($other False)))))
        ($reads (json:array-items (json:get $reference "reads")))
        ($answers (ft:answers "reads" (map-atom $reads $read (ft:fill (json:get $read "call")))))
        ($differing (filter-atom (map-atom (list:range 0 (size-atom $reads)) $i
                                   (let $read (list:at $reads $i)
                                     (if (ft:same (list:at $answers $i) (ft:fill (json:get $read "expected"))) ()
                                       (ReadDiffers (json:get $read "call") (list:at $answers $i)))))
                      $d (not (== $d ()))))
        ($_ (if (== $differing ()) True (println! (READS_DIFFER (size-atom $differing) (list:take 3 $differing)))))
        ($_ (nt:require reads-answer-as-the-python-skills-did (== $differing ())))
        ($failing (ft:steps-failing (json:array-items (json:get $reference "writes")) 0))
        ($_ (if (== $failing ()) True (println! (WRITES_DIFFER $failing))))
        ($_ (nt:require writes-replace-keep-mode-and-back-up-as-before (== $failing ())))
        ($missing (path:join (ft:work) "missing.txt"))
        (($appended) (ft:answers "append-missing" ((str:join "" ("(append-file " (repr $missing) " \"x\")")))))
        ($_ (nt:require an-append-needs-an-existing-file
              (and (str:starts-with $appended "append failed:") (not (fs:exists $missing)))))
        ($blocked (path:join (ft:work) "energy.json"))
        ($_ (fs:make-directories (path:join $blocked "inner") 448))
        (($replaced) (ft:answers "replace-refused"
                       ((str:join "" ("(file-io:write-present \"replace\" (file-io:update " (repr $blocked) " \"replace\" \"next\" \"\"))")))))
        ($_ (nt:require a-refused-replace-keeps-what-was-there-and-cleans-up
              (and (str:starts-with $replaced "replace failed:")
              (and (fs:exists (path:join $blocked "inner"))
                   (ft:no-temporary $blocked))))))
   (println! UNIT_FILE_TOOLS_OK))
