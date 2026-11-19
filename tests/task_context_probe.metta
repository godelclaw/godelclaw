!(import! &self (library lib_import))
!(import! &self ./lib_mettaclaw)
!(import! &self ./tests/scripted_provider.metta)

(= (assertEqual $a $b) (assert (== $a $b)))

;; A scripted model drives the actual task/context boundary: an early finding
;; survives a long batch of reads, a failed read and a write, a task note, and
;; a task-history query.
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
!(py-eval "(__import__('helper').__dict__.update(_working_boot_cache={}, working_boot=lambda: 8), __import__('strategy_host').__dict__.update(guard=lambda **kw: 'task-fixture-control', _stimulus_free=lambda: True), setattr(__import__('telegram'), '_metta_unread_chats', lambda: set()))")
!(py-eval "__import__('helper').__dict__.__setitem__('task_focus', lambda: __import__('strategy_host')._focus)")
!(py-eval "__import__('helper').__dict__.__setitem__('task_view_ok', lambda: (lambda view: int(len(view['transcript']) == 18 and view['transcript'][14]['disposition'] == 'returned' and 'pending' not in view))(__import__('task_context').projection(__import__('strategy_host').ledger(), __import__('strategy_host')._focus)))")
!(initMemory)
!(configure commchannel telegram)
!(let* (($_ (loop-mode:set "godelclaw"))
         ($initial (initLoop))
         ($first (process-step $initial (turnProcess (process-periphery $initial))))
         ($_ (assertEqual (fs:read-text (task:file "written")) "fixed content"))
         ($second (process-step $first (turnProcess (process-periphery $first))))
         ($_ (assertEqual (scripted:prompt-has 2 "EARLY_FINDING") 1))
         ($_ (assertEqual (scripted:prompt-has 2 "read failed") 1))
         ($_ (scripted:answer 3 (task:command ("task-history" (task:quoted (py-call (helper.task_focus)))))))
         ($third (process-step $second (turnProcess (process-periphery $second))))
         ;; Earlier evidence is not lost.
         ($_ (assertEqual (scripted:prompt-has 3 "EARLY_FINDING") 1))
         ($_ (assertEqual (scripted:prompt-has 3 "requires finite evidence; the later read failed") 1))
         ($_ (assertEqual (scripted:calls) 3))
         ($_ (assertEqual (py-call (helper.task_view_ok)) 1)))
   (println! TASK_CONTEXT_OK))
