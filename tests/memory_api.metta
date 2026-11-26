; The command API over the native memory world (loaded by
; tests/cognitive_memory.metta after tests/memory_world.metta): an imported
; command's help, prompt lines, checks and module fields, none of which runs
; it; complete attention metadata whose inspection writes nothing; and the
; same metadata shared by help, check and dispatch through the bootstrap.

(= (mapi:imported $world $service $root)
   (let* (((MemoryCase $server $socket $data $config) (mc:store $world $service "api-import"))
          ($component (nq:path $world "component.metta"))
          ($_ (fs:write $component "
!(bind! &api-calls (new-state 0))
(= (fixture-command $text) (change-state! &api-calls (+ 1 (get-state &api-calls))))
(CommandSpec 1 \"fixture\" \"fixture-command\" 1 1 \"(fixture-command text)\" \"An imported command — Ω.\" \"\")
"))
          ($out (mc:guest $world $root (str:join "" ("
!(println! (BEFORE (api:help \"fixture-command\")))
!(import! &self " (repr $component) ")
!(println! (HELP (api:help \"fixture-command\")))
!(println! (PROMPT (api:prompt-lines)))
!(println! (VALID (api:check (quote (fixture-command \"text\")))))
!(println! (BAD (api:check (quote (fixture-command)))))
!(println! (FIELDS (api:module-fields \"fixture\")))
!(println! (NOT_RUN (get-state &api-calls)))")) (mc:env $world $config ".05")))
          ($_ (nt:require an-imported-command-is-described-and-checked-but-never-run
                (and (nt:contains $out "(BEFORE (APINotRegistered \"fixture-command\"))")
                (and (nt:contains $out "(HELP (CommandHelp \"fixture\" \"fixture-command\"")
                (and (nt:contains $out "An imported command — Ω.")
                (and (nt:contains $out "valid call, not executed")
                (and (nt:contains $out "COMMAND_ERROR: wrong number of arguments. usage: (fixture-command text)")
                (and (nt:contains $out "(CommandField \"fixture\" \"fixture-command\" 1 1")
                     (nt:contains $out "(NOT_RUN 0)"))))))))))
     (mc:stop $server)))

(= (mapi:digest $path) (case (fs:sha256-file $path) (((fs:hashed-v1 $digest $_) $digest) ($bad (Unhashed $bad)))))
(= (mapi:attention $world $service $root)
   (let* (((MemoryCase $server $socket $data $config) (mc:store $world $service "api-attention"))
          ($snapshot (nq:path $world "api-attention.metta"))
          ($env (cons-atom ("METTACLAW_ATTENTION_PATH" $snapshot) (mc:env $world $config ".05")))
          ($_ (mc:guest $world $root "!(attention-use \"a\")" $env))
          ($_ (nt:require attention-use-writes-its-snapshot (fs:exists $snapshot)))
          ($before (mapi:digest $snapshot))
          ($out (mc:guest $world $root "
!(println! (COMMANDS (size-atom (api:command-fields \"attention\" (api:commands)))))
!(println! (FIELDS (size-atom (api:module-fields \"attention\"))))
!(println! (DERIVED (== (size-atom (api:module-fields \"attention\"))
                       (+ 6 (size-atom (settings:specs \"attention\"))))))
!(println! (HELP (api:help \"attention-policy\")))
!(println! (VALID (api:check (quote (attention-link \"a\" \"b\" 0)))))
!(println! (BAD (api:argument-error (quote (attention-last 1)))))
!(println! (LATER (api:check (quote (attention-last)))))" $env))
          ($_ (nt:require attention-metadata-is-complete
                (and (nt:contains $out "(COMMANDS 6)")
                (and (nt:contains $out "(DERIVED true)")
                (and (nt:contains $out "(Default 300) (Unit \"seconds\")")
                (and (nt:contains $out "COMMAND_ERROR: wrong number of arguments. usage: (attention-last)")
                     (nt:contains $out "(LATER \"OK: (attention-last)")))))))
          ($_ (nt:require inspecting-attention-writes-nothing (== (mapi:digest $snapshot) $before))))
     (mc:stop $server)))

(= (mapi:bootstrap $world $service $root $sdk)
   (let* (((MemoryCase $server $socket $data $config) (mc:store $world $service "api-bootstrap"))
          ($probe (nq:path $world "api-bootstrap.metta"))
          ($_ (fs:write $probe "
!(println! (HELP (help \"attention-use\")))
!(println! (INDEX (help)))
!(println! (PROMPT (getSkills)))
!(println! (CHECK (check \"(attention-link \\\"a\\\" \\\"b\\\" 0)\")))
!(println! (BAD_CHECK (check \"(attention-last 1)\")))
!(println! (RUN (run-command-batch-once 910001 0
  (quote ((attention-last 1) (attention-last))))))
"))
          ($frontier (nq:path $world "frontier")) ($_ (fs:write $frontier "1 910001 0 0\n"))
          ($out (mc:bootstrap $world $root $sdk $probe
                  (preq:append (mc:env $world $config ".05")
                    (("METTACLAW_EFFECT_RECEIPT_PATH" (nq:path $world "receipts.jsonl"))
                     ("METTACLAW_STIMULUS_FRONTIER_PATH" $frontier)))))
          ($_ (nt:require help-check-and-dispatch-share-the-metadata
                (and (nt:contains $out "(CommandHelp \"attention\" \"attention-use\"")
                (and (nt:contains $out "valid call, not executed")
                (and (nt:contains $out "COMMAND_ERROR: wrong number of arguments. usage: (attention-last)")
                (and (nt:contains $out "AttentionQuery none")
                     (== (size-atom (str:split "COMMAND_RETURN:" $out)) 3))))))))
     (mc:stop $server)))

(= (mapi:run $world $service $root $sdk)
   (let* (($_ (mapi:imported $world $service $root))
          ($_ (mapi:attention $world $service $root))
          ($_ (mapi:bootstrap $world $service $root $sdk)))
     True))
