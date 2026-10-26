; Identify these two evaluators using a pure core operation, not a Python call,
; environment guess, failing native operation, or an additional foreign API.
; CeTTa implements new-space as an owned grounded value; SWI-PeTTa leaves the
; exact (new-space) syntax inert. The temporary space does not escape this
; expression. Only the selected evaluator tag, NOT active_engine, is retained.
!(if (== (collapse (match &self (mode-runtime:loaded) True)) ())
   (let $probe (new-space)
     (case $probe
       (((new-space)
         (progn (add-atom &self (mode-runtime:loaded))
                (add-atom &self (= (mode-runtime:cetta?) False))))
        ((Error $where $why) (Error $where $why))
        ($space
          (if (== (get-metatype $space) Grounded)
            (progn (add-atom &self (mode-runtime:loaded))
                   (add-atom &self (= (mode-runtime:cetta?) True)))
            (Error mode-runtime unexpected-evaluator-space)))))) True)
