; Iter transformation authoring through the agent: writing, disabling and
; enabling a transformation each answer with a receipt, a request prepared
; while it is active observes it and one prepared while it is disabled does
; not, and the file holds exactly the source written.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/state_file_cetta.metta)
!(import! &self (library list))

(= (ia:documents $out)
   (map-atom (filter-atom (str:split "\n" $out) $l (str:starts-with $l "\"{")) $l (json:parse (json:parse $l))))
(= (ia:last $items) (if (== (cdr-atom $items) ()) (car-atom $items) (ia:last (cdr-atom $items))))
(= (ia:steps $docs)
   (map-atom (filter-atom $docs $doc (not (== (sf:get $doc "operation" NoOperation) NoOperation))) $doc
     ((sf:get $doc "operation" "") (sf:get $doc "active" None))))
(= (ia:names $rows) (case $rows (((JsonArray $items) (map-atom $items $row (sf:get $row "name" ""))) ($o $o))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
         ($directory (path:join $home "transformations"))
         ($env (list:append (("CETTA_BIN" (path:join (nt:required-env "NATIVE_TEST_CORE") "cetta"))
                             ("METTACLAW_ITER_PROCESS_DIR" $directory) ("METTACLAW_SKIP_INITIALIZE" "1")
                             ("XDG_STATE_HOME" (path:join $home "state")))
                            (nt:environment $home)))
         ($out (case (proc:run ("bash" "./run.sh" "tests/iter_authoring_probe.metta") $repo $env 40000 4194304)
                 (((proc:exited 0 $o $e) $o) ($bad (repr $bad)))))
         ($_ (nt:require the-probe-completes (nt:contains $out "ITER_AUTHORING_OK")))
         ($docs (ia:documents $out))
         ($_ (nt:require each-change-answers-with-its-receipt
               (== (ia:steps $docs) (("write" True) ("disable" False) ("enable" True)))))
         ($listing (ia:last $docs))
         ($_ (nt:require the-listing-shows-the-active-transformation
               (and (== (ia:names (sf:get $listing "active" JsonNull)) ("10_probe.metta"))
                    (== (sf:get $listing "disabled" JsonNull) (JsonArray ())))))
         ($prepared (filter-atom (str:split "\n" $out) $l (str:starts-with $l "(iter-native-result ")))
         ($_ (nt:require an-active-transformation-is-observed-and-a-disabled-one-is-not
               (and (== (size-atom $prepared) 2)
                    (and (nt:contains (car-atom $prepared) "(iter-observation \"10_probe.metta\" success \"\")")
                         (str:ends-with (ia:last $prepared) " iter-observations-empty)")))))
         ($_ (nt:require the-file-holds-exactly-the-source
               (and (== (fs:read-text (path:join $directory "10_probe.metta")) "(= (iter-transform $visible) (success $visible))\n")
                    (not (fs:exists (path:join $directory "_10_probe.metta")))))))
   (println! UNIT_ITER_AUTHORING_OK))
