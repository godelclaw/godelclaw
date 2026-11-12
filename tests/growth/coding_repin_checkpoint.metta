; Growth workload: what re-pinning does with a transcript of n KiB: the
; checkpoint revision (streamed hashes) and the copy the check reads.
!(import! &self ../growth_support.metta)
!(import! &self ../../src/coding_io_cetta.metta)
!(let* (($dir (nt:join (growth:directory) (format-args "work-repin-{}" ((system:uuid4-hex)))))
        ($_ (fs:make-directories (path:join $dir "executions") 504))
        ($_ (fs:write (path:join $dir "task.json") "{\"task\":\"t\"}"))
        ($_ (fs:write (path:join $dir "executions/0123456789abcdef0123456789abcdef.json") "{\"status\":\"exited\"}"))
        ($_ (nt:exit-ok (proc:run ("sh" "-c" (format-args "head -c {} /dev/zero | tr '\\0' m > messages.jsonl" ((* 1024 (growth:size)))))
                                  $dir (nt:environment $dir) 60000 4096))))
   (growth:measure (let* (($revision (cio:checkpoint-revision $dir))
                          ($copied (cio:copy-tree $dir (str:concat $dir "-copy") ("tmp" "run.log" ".ending.lock"))))
                     ($revision $copied))))
