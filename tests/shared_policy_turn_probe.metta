!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)

(= (assertEqual $A $B) (assert (== $A $B)))

;; Every named mode uses the same process path.  Selection changes policy data;
;; it does not select a different kernel or revoke any part of the periphery.
!(py-eval "(__import__('helper').__dict__.__setitem__('_working_boot_cache', {}), __import__('helper').__dict__.__setitem__('working_boot', lambda: 1))")
!(py-eval "__import__('synthetic_llm').__dict__.__setitem__('chat', lambda *args: (__import__('cognitive_health').turn_started('probe'), __import__('cognitive_health').turn_completed(2), '()')[-1])")
!(initMemory)
!(configure commchannel telegram)

!(let* (($_ (py-call (loop_modes.set_mode "godelclaw")))
         ($before (initLoop))
         ($after (process-step $before
                   (turnProcess (process-periphery $before))))
         ($_ (cut))
         ($_ (println! (POLICY_PROBE godelclaw
                       (process-root $after)
                       (loop-active-policy (process-periphery $after)))))
         ($_ (assertEqual (process-root $after) weak-process-core-v1))
         ($_ (assertEqual
               (loop-active-policy (process-periphery $after)) godelclaw)))
     (empty))

!(let* (($_ (py-call (loop_modes.set_mode "coding")))
         ($before (initLoop))
         ($after (process-step $before
                   (turnProcess (process-periphery $before))))
         ($_ (cut))
         ($_ (println! (POLICY_PROBE coding
                       (process-root $after)
                       (loop-active-policy (process-periphery $after)))))
         ($_ (assertEqual (process-root $after) weak-process-core-v1))
         ($_ (assertEqual
               (loop-active-policy (process-periphery $after)) coding)))
     (empty))

;; An earlier name selects the mode it now names.
!(let* (($_ (py-call (loop_modes.set_mode "iter")))
         ($before (initLoop))
         ($after (process-step $before
                   (turnProcess (process-periphery $before))))
         ($_ (cut))
         ($_ (println! (POLICY_PROBE iter
                       (process-root $after)
                       (loop-active-policy (process-periphery $after)))))
         ($_ (assertEqual (process-root $after) weak-process-core-v1))
         ($_ (assertEqual
               (loop-active-policy (process-periphery $after)) godelclaw)))
     (empty))

!(println! SHARED_POLICY_TURN_OK)
