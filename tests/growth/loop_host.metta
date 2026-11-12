; Growth workload: one durable journal append of n lines.
!(import! &self ../../src/loop_host_cetta.metta)
(: loop-growth:lines (-> Number %Undefined% %Undefined%))
(= (loop-growth:lines $n $acc)
   (if (== $n 0) $acc
     (loop-growth:lines (- $n 1) (cons-atom "(2026-11-12 turn (RESPONSE ((send \"a line of the journal\"))))" $acc))))
(= (loop-growth:workload $n)
   (let* (($path (str:concat (system:getenv-or-default "HOME" ".") (format-args "/growth-journal-{}-{}" ($n (system:uuid4-hex)))))
          ($answer (loop-host:append-line $path (str:join "\n" (loop-growth:lines $n ()))))
          ($size (str:byte-length (fs:read-text $path)))
          ($_ (fs:remove $path)))
     (+ $answer $size)))
