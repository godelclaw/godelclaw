!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)

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
!(py-eval "__import__('sys').path.insert(0, 'tests')")
!(py-eval "(__import__('helper').__dict__.__setitem__('_working_boot_cache', {}), __import__('helper').__dict__.__setitem__('working_boot', lambda: 3))")
!(initMemory)
!(configure commchannel telegram)

(= (skills-a-line $mode)
   (let* (($_ (py-call (loop_modes.set_mode $mode)))
          ($turn (process-periphery (initLoop)))
          ($prompt (request:render (coding-request-prompt (turnRequest $turn (NEW_ACTIVITY: first)))))
          ($_ (cut)))
     (py-call (advertised_skills_check.one_skill_a_line $prompt (getSkills)))))

!(let* (($_ (assertEqual (skills-a-line "godelclaw") 1))
        ($_ (assertEqual (skills-a-line "coding") 1)))
    (println! ADVERTISED_SKILLS_OK))
