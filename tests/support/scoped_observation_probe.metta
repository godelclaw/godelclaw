!(import! &self ../../src/knowledge_observation_cetta.metta)
!(import! &self (library fs))

; Binary evidence avoids dialect-dependent printing and never executes data.
(= (observation-probe:save $path $value)
   (case (store:encode $value)
     (((StoreEncoded $bytes)
       (let $writer (fs:open-write $path ("truncate" "create") 384)
        (case $writer
         (((NativeHandle $kind $id)
           (let* (($_ (collapse (fs:write-all $writer $bytes)))
                  ($_ (collapse (fs:close $writer)))) True))
          ($error $error)))))
      ($error $error))))
