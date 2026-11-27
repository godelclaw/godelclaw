; Print the evidence-based metrics of a replay file as one JSON object.
;   cetta --lang petta --profile extended scripts/replay_evaluation_cetta.metta REPLAY
!(import! &self ../src/replay_evaluation_cetta.metta)
!(import! &self (library system))
!(let $args (system:args)
   (if (not (== (size-atom $args) 1))
       (let $_ (system:write stderr "usage: replay_evaluation_cetta.metta REPLAY\n") (system:exit 2))
       (case (rpl:evaluate-file (str:symbol->string (car-atom $args)))
         (((ReplayInvalid $why) (let $_ (system:write stderr (str:concat $why "\n")) (system:exit 2)))
          ($metrics (let $_ (system:write stdout (str:concat (json:stringify-python $metrics) "\n")) (system:exit 0)))))))
