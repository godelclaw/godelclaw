!(import! &self ../growth_support.metta)
!(import! &self ../../modes/iter/host_cetta.metta)
(= (iter-growth:append $items $index $end)
   (if (>= $index $end) $items
     (let $_ (ih:append $items $index) (iter-growth:append $items (+ $index 1) $end))))
!(let $items (ih:list)
   (growth:measure
     (let* (($_ (iter-growth:append $items 0 (growth:size)))
            ($_ (nt:require list-keeps-all-occurrences (== (ih:size $items) (growth:size))))
            ($values (ih:values $items))
            ($_ (nt:require enumeration-has-expected-size (== (size-atom $values) (growth:size))))
            ($_ (nt:require last-occurrence-has-its-index (== (ih:get $items (- (growth:size) 1)) (- (growth:size) 1))))) 1)))
