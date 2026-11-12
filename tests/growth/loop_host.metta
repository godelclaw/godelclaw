; Growth workload: one durable journal append of n lines.
!(import! &self ../growth_support.metta)
!(import! &self ../../src/loop_host_cetta.metta)
(= (loop-growth:lines $n $acc)
   (if (== $n 0) $acc
     (loop-growth:lines (- $n 1) (cons-atom "(2026-11-12 turn (RESPONSE ((send \"a line of the journal\"))))" $acc))))
!(let* (($text (str:join "\n" (loop-growth:lines (growth:size) ())))
        ($path (nt:join (growth:directory) (format-args "journal-{}" ((system:uuid4-hex))))))
   (growth:measure (loop-host:append-line $path $text)))
