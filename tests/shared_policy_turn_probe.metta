!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)
!(import! &self ./tests/scripted_provider.metta)

(= (assertEqual $A $B) (assert (== $A $B)))

;; Every named mode uses the same process path.  Selection changes policy data;
;; it does not select a different kernel or revoke any part of the periphery.
;; A mode is selected at boot, as an agent runs it after the recycle that a
;; switch requests: the runner boots this probe once in each mode.
!(py-eval "(__import__('helper').__dict__.__setitem__('_working_boot_cache', {}), __import__('helper').__dict__.__setitem__('working_boot', lambda: 1))")
!(scripted:default "()")
!(initMemory)
!(configure commchannel telegram)

;; The selection is read as its mode's name: an earlier name selects the
;; mode it now names.
(= (probe:policy "godelclaw") godelclaw)
(= (probe:policy "coding") coding)

!(let* (($mode (loop-mode:current))
         ($before (initLoop))
         ($after (process-step $before
                   (turnProcess (process-periphery $before))))
         ($_ (cut))
         ($_ (println! (POLICY_PROBE $mode
                       (process-root $after)
                       (loop-active-policy (process-periphery $after)))))
         ($_ (assertEqual (process-root $after) weak-process-core-v1))
         ($_ (assertEqual
               (loop-active-policy (process-periphery $after)) (probe:policy $mode)))
         ;; The turn's call reached the model.
         ($_ (assertEqual (scripted:calls) 1)))
     (println! SHARED_POLICY_TURN_OK))
