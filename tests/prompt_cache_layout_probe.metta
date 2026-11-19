!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)
!(import! &self ./tests/prompt_checks.metta)

(= (assertEqual $A $B) (assert (== $A $B)))

;; Two turns that differ only in per-turn state must send requests whose text
;; before the last cache boundary is identical, since that prefix is what a
;; provider may reuse.  Both the structured (iter) and the plain (agent)
;; request are built through the same turnRequest the loop sends.
!(initMemory)
!(configure commchannel telegram)

(= (layout-sound $mode)
   (let* (($_ (loop-mode:set $mode))
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
     (pc:layout-sound? (request:render $first) (request:render $second))))

!(let* (($_ (assertEqual (layout-sound "godelclaw") True))
        ($_ (assertEqual (layout-sound "coding") True)))
    (println! PROMPT_CACHE_LAYOUT_OK))
