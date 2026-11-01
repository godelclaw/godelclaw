!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)

(= (assertEqual $A $B) (assert (== $A $B)))

;; Two turns that differ only in per-turn state must send requests whose text
;; before the last cache boundary is identical, since that prefix is what a
;; provider may reuse.  Both the structured (iter) and the plain (agent)
;; request are built through the same turnRequest the loop sends.
!(py-eval "__import__('sys').path.insert(0, 'tests')")
!(py-eval "(__import__('helper').__dict__.__setitem__('_working_boot_cache', {}), __import__('helper').__dict__.__setitem__('working_boot', lambda: 3))")
!(initMemory)
!(configure commchannel telegram)

(= (layout-sound $mode)
   (let* (($_ (py-call (loop_modes.set_mode $mode)))
          ($first-turn (process-periphery (initLoop)))
          ($second-turn
            (coordinate-set loops 7
              (coordinate-set last-results "a different result"
                $first-turn)))
          ($first
            (coding-request-prompt
              (turnRequest $first-turn (NEW_ACTIVITY: first))))
          ($second
            (coding-request-prompt
              (turnRequest $second-turn (NEW_ACTIVITY: second))))
          ;; As in the loop, a cut ends the renderers' residual choice points.
          ($_ (cut)))
     (py-call (prompt_layout_check.ok (request:render $first) (request:render $second)))))

!(let* (($_ (assertEqual (layout-sound "godelclaw") 1))
        ($_ (assertEqual (layout-sound "coding") 1)))
    (println! PROMPT_CACHE_LAYOUT_OK))
