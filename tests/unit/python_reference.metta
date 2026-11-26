; Every tracked Python file is delineated. config/python-reference.json names
; each one's fate and the phase that retires it, and names no file that is
; gone, so no Python appears unlisted and the list shrinks to empty. The old
; code stays reachable at the reference tag the list names.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/state_file_cetta.metta)
!(import! &self (library list))

(= (pr:lines $text) (filter-atom (str:split "\n" $text) $line (not (== $line ""))))
(= (pr:git-files $root $pattern)
   (pr:lines (nt:exit-ok (proc:run ("git" "ls-files" "--" $pattern) $root
                                   (nt:environment (nt:required-env "HOME")) 15000 4194304))))
(= (pr:member? $item $items)
   (not (== (list:len (remove-all-atom $item $items)) (list:len $items))))
; The items of $from that $in lacks, in $from's order.
(= (pr:missing $from $in) (filter-atom $from $item (not (pr:member? $item $in))))

(= (pr:fates) ("port-test" "delete-with-native-coverage" "port-tool" "dead" "vendored-external"))
(= (pr:entry-ok? $entry)
   (and (pr:member? (sf:get $entry "fate" "") (pr:fates))
        (case (sf:get $entry "phase" 0)
          (($phase (and (== (get-type $phase) Number) (and (>= $phase 1) (<= $phase 8))))))))
(= (pr:bad-entries $pairs)
   (filter-atom $pairs $pair (let (JsonPair $file $entry) $pair (not (pr:entry-ok? $entry)))))
(= (pr:report $tag $items) (if (== $items ()) True (println! ($tag $items))))

!(let* (($root (nt:required-env "TEST_REPOSITORY"))
         ($reference (sf:native-object (path:join $root "config/python-reference.json")))
         ($_ (nt:require reference-is-tracked
               (== (pr:git-files $root "config/python-reference.json") ("config/python-reference.json"))))
         ($_ (nt:require reference-names-its-tag
               (str:starts-with (sf:get $reference "reference_tag" "") "python-reference-")))
         ((JsonObject $pairs) (sf:get $reference "files" (JsonObject ())))
         ($listed (map-atom $pairs $pair (let (JsonPair $file $entry) $pair $file)))
         ($tracked (pr:git-files $root "*.py"))
         ($unlisted (pr:missing $tracked $listed))
         ($gone (pr:missing $listed $tracked))
         ($bad (pr:bad-entries $pairs))
         ($_ (pr:report UNLISTED_PYTHON $unlisted))
         ($_ (pr:report LISTED_BUT_GONE $gone))
         ($_ (pr:report UNKNOWN_FATE_OR_PHASE (map-atom $bad $pair (let (JsonPair $file $entry) $pair $file))))
         ($_ (nt:require every-tracked-python-file-is-listed (== $unlisted ())))
         ($_ (nt:require every-listed-file-still-exists (== $gone ())))
         ($_ (nt:require every-entry-has-a-known-fate-and-phase (== $bad ())))
         ; The comparison sees both directions on a small case.
         ($_ (nt:require checker-finds-an-unlisted-file
               (== (pr:missing ("a.py" "b.py") ("b.py" "c.py")) ("a.py"))))
         ($_ (nt:require checker-finds-a-gone-entry
               (== (pr:missing ("b.py" "c.py") ("a.py" "b.py")) ("c.py"))))
         ($_ (nt:require checker-refuses-an-unknown-fate
               (not (pr:entry-ok? (JsonObject ((JsonPair "fate" "keep") (JsonPair "phase" 1))))))))
   (println! UNIT_PYTHON_REFERENCE_OK))
