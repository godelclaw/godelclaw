; The native libraries the runtime ships are the qualified ones: each is
; selected for the release and its bytes match the SHA-256 the manifest
; recorded, the manifest itself ships, and so do the knowledge store modules.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/state_file_cetta.metta)

(= (nl:member? $x $xs) (if (== $xs ()) False (if (== (car-atom $xs) $x) True (nl:member? $x (cdr-atom $xs)))))
(= (nl:digest $path) (case (fs:sha256-file $path) (((fs:hashed-v1 $digest $_) $digest) ($bad (Unhashed $bad)))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY"))
         ($manifest (sf:native-object (path:join $repo "config/native-libraries.json")))
         ((JsonArray $selected) (sf:get (sf:native-object (path:join $repo "config/runtime-files.json")) "files" (JsonArray ())))
         ((JsonArray $libraries) (sf:get $manifest "libraries" (JsonArray ())))
         ($_ (nt:require the-manifest-names-libraries (and (== (sf:get $manifest "schema" 0) 1) (> (size-atom $libraries) 0))))
         ($_ (nt:require every-library-ships-as-qualified
               (== (filter-atom $libraries $library
                     (let $runtime (sf:get $library "runtime" "")
                       (not (and (nl:member? $runtime $selected)
                                 (== (nl:digest (path:join $repo $runtime)) (sf:get $library "sha256" ""))))))
                   ())))
         ($_ (nt:require the-manifest-ships (nl:member? "config/native-libraries.json" $selected)))
         ($_ (nt:require the-knowledge-store-ships
               (and (nl:member? "src/knowledge_store_cetta.metta" $selected) (nl:member? "src/knowledge_observation_cetta.metta" $selected)))))
   (println! UNIT_NATIVE_LIBRARIES_OK))
