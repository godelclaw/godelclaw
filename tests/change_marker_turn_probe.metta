!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)
!(import! &self ./tests/prompt_checks.metta)

(= (assertEqual $A $B) (assert (== $A $B)))

;; A change marker counts turns: two consecutive requests show the pins block
;; unchanged for exactly one turn, in the plain (agent) and the structured
;; (coding) request alike.
!(initMemory)
!(configure commchannel telegram)

(= (marker-counts-turns $mode)
   (let* (($_ (loop-mode:set $mode))
          ($_ (change-state! &context-marks ()))
          ($turn (process-periphery (initLoop)))
          ($first (coding-request-prompt (turnRequest $turn (NEW_ACTIVITY: first))))
          ($second (coding-request-prompt (turnRequest $turn (NEW_ACTIVITY: second))))
          ($_ (cut)))
     (pc:pins-unchanged-one-turn (request:render $first) (request:render $second))))

!(let* (($_ (assertEqual (marker-counts-turns "godelclaw") True))
        ($_ (assertEqual (marker-counts-turns "coding") True)))
    (println! CHANGE_MARKER_TURN_OK))
