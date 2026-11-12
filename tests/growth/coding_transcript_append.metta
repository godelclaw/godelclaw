; Growth workload: a session appends n transcript lines (coding:record's path).
!(import! &self ../growth_support.metta)
!(import! &self ../../src/coding_io_cetta.metta)
(= (gw:append $path $line $left)
   (if (<= $left 0) True (let $_ (cio:append-line $path $line) (gw:append $path $line (- $left 1)))))
!(let* (($path (nt:join (growth:directory) (format-args "messages-{}.jsonl" ((system:uuid4-hex)))))
        ($line "{\"role\":\"tool\",\"tool_call_id\":\"call-0001\",\"content\":\"Exit code: 0\\nWall time: 0.01 seconds\\nOutput:\\ncensus line of ordinary tool output\"}"))
   (growth:measure (gw:append $path $line (growth:size))))
