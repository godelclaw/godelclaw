!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)

(= (assertEqual $A $B) (assert (== $A $B)))

;; A change marker counts turns: two consecutive requests show the pins block
;; unchanged for exactly one turn, in the plain (agent) and the structured
;; (coding) request alike.
!(py-eval "__import__('sys').path.insert(0, 'tests')")
!(py-eval "(__import__('helper').__dict__.__setitem__('_working_boot_cache', {}), __import__('helper').__dict__.__setitem__('working_boot', lambda: 3))")
!(initMemory)
!(configure commchannel telegram)

(= (marker-counts-turns $mode)
   (let* (($_ (py-call (loop_modes.set_mode $mode)))
          ($_ (py-eval "__import__('helper')._change_marks.clear()"))
          ($turn (process-periphery (initLoop)))
          ($first (coding-request-prompt (turnRequest $turn (NEW_ACTIVITY: first))))
          ($second (coding-request-prompt (turnRequest $turn (NEW_ACTIVITY: second))))
          ($_ (cut)))
     (py-call (change_marker_check.one_turn $first $second))))

!(let* (($_ (assertEqual (marker-counts-turns "godelclaw") 1))
        ($_ (assertEqual (marker-counts-turns "coding") 1)))
    (println! CHANGE_MARKER_TURN_OK))
