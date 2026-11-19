!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)
!(import! &self ./tests/scripted_provider.metta)

(= (assertEqual $a $b) (assert (== $a $b)))
; The task view the model's task-history reads, from the native ledger: its
; transcript holds one entry per command.
(= (probe:focus) (state:text (state:meta "context_focus")))
(= (probe:view) (task:projection (probe:focus) "continuation-tick"))
(= (probe:transcript) (task:items (probe:view) "transcript"))
(= (probe:field $n $key) (json-object-get (index-atom (probe:transcript) $n) $key))
(= (probe:text-has $value $text) (case (str:find $value $text) (((TextNotFound $t) False) ($at True))))
(= (probe:result-has $n $text)
   (case (probe:field $n "result") (((JsonString $s) (probe:text-has $s $text)) ($other (probe:text-has (repr $other) $text)))))
(= (probe:pending-free)
   (let (JsonObject $pairs) (probe:view)
     (== (collapse (case (superpose $pairs) (((JsonPair "pending" $v) True) ($other (empty))))) ())))

(= (task:file $name) (str:concat (system:getenv-or-default "TMPDIR" "") (str:concat "/task-context-" $name)))
(= (task:command $words) (str:join "" ("(" (str:join " " $words) ")")))
(= (task:quoted $text) (json:stringify (JsonString $text)))
(= (task:reads $n) (if (== $n 0) () (cons-atom (task:command ("read-file" (task:quoted (task:file "source.txt")))) (task:reads (- $n 1)))))
!(fs:write (task:file "source.txt") "EARLY_FINDING: this API requires finite evidence.")
!(scripted:answer 1
   (str:join "" ("(" (str:join " " (task:reads 13)) " "
                 (task:command ("read-file" (task:quoted (task:file "missing")))) " "
                 (task:command ("read-file" (task:quoted (task:file "source.txt")))) " "
                 (task:command ("write-file" (task:quoted (task:file "written")) (task:quoted "fixed content"))) ")")))
!(scripted:answer 2 "((task-note \"The source requires finite evidence; the later read failed.\"))")
!(initMemory)
!(configure commchannel telegram)
!(let* (($_ (loop-mode:set "godelclaw"))
         ($initial (initLoop))
         ($first (process-step $initial (turnProcess (process-periphery $initial))))
         ($_ (assertEqual (fs:read-text (task:file "written")) "fixed content"))
         ($second (process-step $first (turnProcess (process-periphery $first))))
         ($_ (assertEqual (scripted:prompt-has 2 "EARLY_FINDING") 1))
         ($_ (assertEqual (scripted:prompt-has 2 "read failed") 1))
         ($_ (scripted:answer 3 (task:command ("task-history" (task:quoted (probe:focus))))))
         ($third (process-step $second (turnProcess (process-periphery $second))))
         ;; Earlier evidence is not lost.
         ($_ (assertEqual (scripted:prompt-has 3 "EARLY_FINDING") 1))
         ($_ (assertEqual (scripted:prompt-has 3 "requires finite evidence; the later read failed") 1))
         ($_ (assertEqual (scripted:calls) 3))
         ($_ (assertEqual (length (probe:transcript)) 18))
         ($_ (assertEqual (probe:field 14 "disposition") (JsonString "returned")))
         ($_ (assertEqual (probe:pending-free) True)))
   (println! TASK_CONTEXT_OK))
