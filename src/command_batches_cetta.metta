; Text-only normalization of explicit command-batch envelopes. CeTTa owns
; the scanner; admission, acknowledgement and execution stay with the broker.
!(import! &self (library str))
!(import! &self (library system))
!(import! &self ./src/utils)

(= (command-batches:normalize $raw)
   (let $text (utils:py-str ($raw))
     (if (== (system:getenv-or-default "METTACLAW_OBSERVED_STRATEGY" "0") "1")
       (str:unwrap-envelopes $text "command-batch" 64)
       $text)))
