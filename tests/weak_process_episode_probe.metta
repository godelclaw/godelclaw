!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)
!(import! &self ./tests/scripted_provider.metta)

(= (assertEqual $A $B) (assert (== $A $B)))

;; One real policy episode through the production MeTTa loop.  Isolate the
;; boot snapshot; the scripted model gives a deterministic no-command reply,
;; so the probe is independent of a running agent's rest.
!(scripted:default "()")
!(initMemory)
!(configure commchannel telegram)
!(let* (($before (initLoop))
         ($outcome (turnProcess (process-periphery $before)))
         ($after (process-step $before $outcome))
         ($_ (assertEqual (process-root $after) weak-process-core-v1))
         ($_ (assertEqual (loop-iteration (process-periphery $after)) 2))
         ($_ (assertEqual (loop-budget (process-periphery $after)) 50))
         ($_ (assertEqual
               (loop-active-policy (process-periphery $after)) godelclaw))
         ($_ (assertEqual (scripted:calls) 1)))
     (println! WEAK_PROCESS_EPISODE_OK))
