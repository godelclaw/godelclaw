!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)
!(import! &self ./tests/scripted_provider.metta)

(= (assertEqual $A $B) (assert (== $A $B)))

;; Four ordinary turns of coding mode, which the runner boots, through the
;; actual loop/request path.  The probe supplies only a scripted model and
;; disposable transformation files; Iter execution and request folding remain
;; native.
!(scripted:answer 1 "((help \"send\"))")
!(scripted:answer 2 "((help \"pin\"))")
!(scripted:answer 3 "((help \"mode\"))")
!(scripted:default "((nop))")
!(initMemory)
!(configure commchannel telegram)

;; A stage's transformations replace the previous stage's.
(= (iter-stage:directory) (system:getenv-or-default "METTACLAW_ITER_PROCESS_DIR" ""))
(= (iter-stage:append $message)
   (format-args "(= (iter-transform\n      (coding-view $messages $advertised))\n   (success\n     (coding-view\n       (coding-message-append-one\n         $messages (coding-message user {}))\n       $advertised)))\n" ($message)))
(= (iter-stage:files "first") (("10_first.metta" (iter-stage:append "iter-stage-first"))))
(= (iter-stage:files "failure")
   (("10_failure.metta" "(= (iter-transform $visible) failure)\n")
    ("20_after_failure.metta" (iter-stage:append "iter-stage-second"))))
(= (iter-stage:files "timeout")
   (("10_timeout.metta" "(= (native-iter-scenario-spin) (native-iter-scenario-spin))\n(= (iter-transform $visible) (native-iter-scenario-spin))\n")))
(= (iter-stage:files "fourth") (("10_fourth.metta" (iter-stage:append "iter-stage-fourth"))))
(= (iter-stage:clear $names)
   (if (== $names ()) True
     (let* (($name (car-atom $names))
            ($_ (if (str:ends-with $name ".metta") (fs:remove (str:concat (iter-stage:directory) (str:concat "/" $name))) True)))
       (iter-stage:clear (cdr-atom $names)))))
(= (iter-stage:write $files)
   (if (== $files ()) True
     (let* ((($name $source) (car-atom $files))
            ($_ (fs:write (str:concat (iter-stage:directory) (str:concat "/" $name)) $source)))
       (iter-stage:write (cdr-atom $files)))))
(= (iter-stage:select $stage)
   (let* (($_ (fs:make-directories (iter-stage:directory) 448))
          ($_ (iter-stage:clear (fs:entries (iter-stage:directory)))))
     (iter-stage:write (iter-stage:files $stage))))

;; Whether each of the first $n prompts holds $text.
(= (iter-stage:each-prompt-has $n $text)
   (if (== $n 0) True
     (and (== (scripted:prompt-has $n $text) 1) (iter-stage:each-prompt-has (- $n 1) $text))))

!(let* (($_ (assertEqual (loop-mode:current) (loop-mode:canonical (system:getenv-or-default "NATIVE_PROBE_MODE" ""))))
         ($_ (iter-stage:select "first"))
         ($initial (initLoop))
         ($after-first
          (process-step $initial
            (turnProcess (process-periphery $initial))))
         ($_ (iter-stage:select "failure"))
         ($after-second
          (process-step $after-first
            (turnProcess (process-periphery $after-first))))
         ($_ (iter-stage:select "timeout"))
         ($after-third
          (process-step $after-second
            (turnProcess (process-periphery $after-second))))
         ($_ (iter-stage:select "fourth"))
         ($after-fourth
          (process-step $after-third
            (turnProcess (process-periphery $after-third))))
         ($_ (assertEqual (process-root $after-fourth) weak-process-core-v1))
         ($_ (cut))
         ($_ (assertEqual (scripted:calls) 4))
         ($_ (assertEqual (iter-stage:each-prompt-has 4 "GODEL_NATIVE_ITER_PERSONA") True))
         ($_ (assertEqual (iter-stage:each-prompt-has 4 "GODEL_NATIVE_ITER_PIN") True))
         ($_ (assertEqual (scripted:prompt-has 1 "ITER_PROCESS_OBSERVATIONS") 1))
         ;; Each stage's message is visible to its turn only.
         ($_ (assertEqual (scripted:user-message? 1 "iter-stage-first") True))
         ($_ (assertEqual (scripted:prompt-has 1 "iter-stage-second") 0))
         ($_ (assertEqual (scripted:user-message? 2 "iter-stage-second") True))
         ($_ (assertEqual (scripted:prompt-has 2 "iter-stage-first") 0))
         ($_ (assertEqual (scripted:user-message? 4 "iter-stage-fourth") True))
         ($_ (assertEqual (scripted:prompt-has 4 "iter-stage-first") 0))
         ($_ (assertEqual (scripted:prompt-has 4 "iter-stage-second") 0))
         ;; A failed transformation is observed, and the turn after it holds the
         ;; previous turn's command result.
         ($_ (assertEqual (scripted:prompt-has 2 "\"10_failure.metta\" failure") 1))
         ($_ (assertEqual (scripted:prompt-has 2 "usage: (send") 1))
         ;; A transformation that times out stutters: no stage's message shows.
         ($_ (assertEqual (scripted:prompt-has 3 "iter-stage-first") 0))
         ($_ (assertEqual (scripted:prompt-has 3 "iter-stage-second") 0))
         ($_ (assertEqual (scripted:prompt-has 3 "iter-stage-fourth") 0))
         ($_ (assertEqual (scripted:prompt-has 3 "\"10_timeout.metta\" failure \"timeout\"") 1))
         ($_ (assertEqual (scripted:prompt-has 3 "usage: (pin") 1)))
     (println! NATIVE_ITER_MULTITURN_OK))
