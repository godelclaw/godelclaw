!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)
!(import! &self ./tests/prompt_checks.metta)

(= (assertEqual $A $B) (assert (== $A $B)))
!(bind! &help-effects (new-state 0))
(= (help-effect-witness) (change-state! &help-effects 1))
!(help help-effect-witness)
!(assertEqual (get-state &help-effects) 0)
!(assertEqual (help (help-effect-witness)) "help: expected a skill name")
!(assertEqual (get-state &help-effects) 0)
!(assertEqual (str:starts-with (help vitals) "usage: (vitals)") True)

;; A structured request advertises one skill a line, in the agent and the
;; coding mode alike.
!(initMemory)
!(configure commchannel telegram)

(= (skills-a-line $mode)
   (let* (($_ (loop-mode:set $mode))
          ($turn (process-periphery (initLoop)))
          ($prompt (request:render (coding-request-prompt (turnRequest $turn (NEW_ACTIVITY: first)))))
          ($_ (cut)))
     (pc:one-skill-a-line $prompt (getSkills))))

!(let* (($_ (assertEqual (skills-a-line "godelclaw") True))
        ($_ (assertEqual (skills-a-line "coding") True)))
    (println! ADVERTISED_SKILLS_OK))
