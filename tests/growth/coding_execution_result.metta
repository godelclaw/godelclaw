; Growth workload: the result of a finished command whose log has n KiB. Only
; the bytes shown are read, so its cost is bounded whatever the log's size.
!(import! &self ../growth_support.metta)
!(import! &self ../../src/coding_io_cetta.metta)
!(let* (($dir (nt:join (growth:directory) (format-args "work-exec-{}" ((system:uuid4-hex)))))
        ($id (system:uuid4-hex))
        ($_ (cio:execution-create $dir $id "yes"))
        ($_ (nt:exit-ok (proc:run ("sh" "-c" (format-args "head -c {} /dev/zero | tr '\\0' o > {}.log" ((* 1024 (growth:size)) $id)))
                                  (path:join $dir "executions") (nt:environment $dir) 60000 4096)))
        ($_ (cio:execution-finish $dir $id "exited" 0 1.0)))
   (growth:measure (str:byte-length (cio:execution-result $dir $id))))
