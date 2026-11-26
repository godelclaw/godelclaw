; Settings over the native memory world (loaded by tests/cognitive_memory.metta
; after tests/memory_world.metta): described values that persist, providers
; that register on import without editing any table, attention keys that
; outlive their provider, the seed blend's endpoints, inspection that writes
; nothing, a failed provider that hides no other control, and the context
; provider and prompt index through the bootstrap.

(= (msets:case $world $service $case)
   (let (MemoryCase $server $socket $data $config) (mc:store $world $service $case)
     ($server (mc:case-env $world $config $case ".05"))))
(= (msets:has-all $text $parts)
   (if (== $parts ()) True (if (nt:contains $text (car-atom $parts)) (msets:has-all $text (cdr-atom $parts)) False)))
(= (msets:before $text $marker)
   (case (str:find $text $marker) (((TextNotFound $_) $text) ($at (str:byte-slice $text 0 $at)))))

(= (msets:values $world $service $root)
   (let* ((($server $env) (msets:case $world $service "settings-values"))
          ($out (mc:guest $world $root "
!(println! (SCHEMA (settings \"attention\")))
!(println! (SET (setting \"attention\" \"sti-half-life\" \"900\")))
!(println! (GET (setting \"attention\" \"sti-half-life\")))
!(println! (SET_MODE (setting \"attention\" \"mode\" \"similarity\")))
!(println! (ZERO_RESTART (setting \"attention\" \"restart\" \"0\")))
!(println! (UNKNOWN (setting \"attention\" \"sti-halflife\" 5)))
!(println! (LATER (semantic:query \"alpha\" 1)))" $env))
          ($_ (nt:require settings-are-described-and-set-from-numeric-text
                (msets:has-all $out ("(Default 300) (Unit \"seconds\") (Domain Nonnegative)" "(Current 900)"
                                     "(AttentionConfigured \"restart\" 0 true)"
                                     "(SettingUnknown \"attention\" \"sti-halflife\")"
                                     "(LATER (MemoryResults ((MemoryHit \"a\""))))
          ($again (mc:guest $world $root "!(println! (setting \"attention\" \"sti-half-life\"))" $env))
          ($_ (nt:require a-setting-persists-into-a-fresh-evaluator (nt:contains $again "(Current 900)"))))
     (mc:stop $server)))

(= (msets:imported $world $service $root)
   (let* ((($server $env) (msets:case $world $service "settings-import"))
          ($provider (nq:path $world "new-provider.metta"))
          ($_ (fs:write $provider "
!(bind! &fixture-gain (new-state 2))
(= (fixture:read $key) (get-state &fixture-gain))
(= (fixture:write $key $value) (change-state! &fixture-gain $value))
(= (fixture:register)
 (settings:register (SettingSpec 1 \"fixture\" \"gain\" 2 \"multiplier\" Nonnegative \"session\" \"A newly imported provider.\" fixture:read fixture:write)))
(SettingSpec 1 \"fixture\" \"gain\" 2 \"multiplier\" Nonnegative \"session\" \"A newly imported provider.\" fixture:read fixture:write)
"))
          ($out (mc:guest $world $root (str:join "" ("
!(println! (BEFORE (settings:context)))
!(import! &self " (repr $provider) ")
!(println! (AFTER (settings:context)))
!(println! (VALUE (setting \"fixture\" \"gain\")))
!(let $before (settings:revision)
   (let $_ (fixture:register) (println! (REVISION_KEPT (== $before (settings:revision))))))
!(setting \"fixture\" \"gain\" \"7\")
!(println! (UPDATED (setting \"fixture\" \"gain\")))
!(println! (BAD (setting \"fixture\" \"gain\" -1)))
!(println! (STILL (setting \"fixture\" \"gain\")))")) $env))
          ($_ (nt:require an-imported-provider-joins-the-index-without-a-table-edit
                (and (not (nt:contains (msets:before $out "(AFTER ") "SettingsGroup \"fixture\""))
                     (msets:has-all $out ("(SettingsGroup \"fixture\" (\"gain\"))" "(Current 2)" "(Current 7)"
                                          "(REVISION_KEPT true)" "(SettingError \"fixture\" \"gain\""))))))
     (mc:stop $server)))

(= (msets:new-key $world $service $root)
   (let* ((($server $env) (msets:case $world $service "settings-new-key"))
          ($out (mc:guest $world $root "
!(settings:register (SettingSpec 1 \"attention\" \"future-gain\" 2 \"multiplier\" Nonnegative \"restart\" \"New provider field.\" attention-policy attention-policy))
!(println! (DEFAULT (attention-policy \"future-gain\")))
!(println! (SET (setting \"attention\" \"future-gain\" 7)))
!(attention-reload)
!(println! (RELOADED (attention-policy \"future-gain\")))" $env))
          ($_ (nt:require a-new-attention-key-takes-effect (msets:has-all $out ("(DEFAULT 2)" "(RELOADED 7)"))))
          ; Its provider is not registered in a fresh evaluator: attention must not freeze.
          ($later (mc:guest $world $root "!(println! (semantic:query \"alpha\" 1))\n!(println! (attention-policy))" $env))
          ($_ (nt:require a-key-outlives-its-absent-provider
                (and (msets:has-all $later ("(MemoryHit \"a\"" "(AttentionSetting \"future-gain\" 7)"))
                     (not (nt:contains $later "AttentionUnavailable"))))))
     (mc:stop $server)))

(= (msets:seed-blend $world $service $root)
   (let* ((($server $env) (msets:case $world $service "settings-seed"))
          ($out (mc:guest $world $root "
!(attention-policy \"exposure\" 0)
!(attention-policy \"spread-rounds\" 0)
!(attention-use \"b\" 100)
!(setting \"attention\" \"seed-similarity-weight\" 1)
!(println! (SIM_SEED (semantic:query \"alpha\" 1 \"attention\")))
!(setting \"attention\" \"seed-similarity-weight\" 0)
!(println! (ATTN_SEED (semantic:query \"alpha\" 1 \"attention\")))
!(forget \"b\")
!(forget \"a\")
!(println! (EMPTY (semantic:query \"alpha\" 1 \"attention\")))" $env))
          ($_ (nt:require the-seed-blend-reaches-both-endpoints-and-zero-rounds
                (msets:has-all $out ("(SIM_SEED (MemoryResults ((MemoryHit \"a\"" "(ATTN_SEED (MemoryResults ((MemoryHit \"b\""
                                     "(EMPTY (MemoryResults ()")))))
     (mc:stop $server)))

(= (msets:passive $world $service $root)
   (let* ((($server $env) (msets:case $world $service "settings-passive"))
          ($snapshot (path:join (mc:case-dir $world "settings-passive") "attention.metta"))
          ($_ (mc:guest $world $root "!(attention-use \"a\")" $env))
          ($_ (nt:require attention-use-writes-the-case-snapshot (fs:exists $snapshot)))
          ($before (mapi:digest $snapshot))
          ($out (mc:guest $world $root "!(println! (settings))\n!(println! (settings:context))" $env))
          ($_ (nt:require inspecting-settings-neither-stimulates-nor-rewrites
                (and (nt:contains $out "(Setting \"attention\" \"seed-similarity-weight\"")
                     (== (mapi:digest $snapshot) $before)))))
     (mc:stop $server)))

(= (msets:failed-provider $world $service $root)
   (let* ((($server $env) (msets:case $world $service "settings-failed"))
          ($out (mc:guest $world $root "
(= (fixture:failed-provider) (Error fixture offline))
(SettingsProvider \"unavailable\" fixture:failed-provider)
!(println! (settings \"attention\"))
!(println! (settings:context))
!(println! (LATER (semantic:query \"alpha\" 1)))" $env))
          ($_ (nt:require a-failed-provider-is-visible-and-hides-nothing-else
                (msets:has-all $out ("(Setting \"attention\" \"mode\""
                                     "(SettingProviderUnavailable fixture:failed-provider offline)"
                                     "(LATER (MemoryResults ((MemoryHit \"a\"")))))
     (mc:stop $server)))

(= (msets:bootstrap $world $service $root $sdk)
   (let* ((($server $env) (msets:case $world $service "settings-bootstrap"))
          ($probe (nq:path $world "context-catalogue.metta"))
          ($_ (fs:write $probe "
!(println! (INDEX (settings:context)))
!(println! (SETTING_CONTEXT (setting \"context\" \"task_tokens\" \"24000\")))
!(println! (CURRENT (setting \"context\" \"task_tokens\")))
!(println! (HELP (help \"settings\")))
"))
          ($out (mc:bootstrap $world $root $sdk $probe $env))
          ($policy (path:join (mc:case-dir $world "settings-bootstrap") "context-policy.json"))
          ($_ (nt:require the-context-provider-and-prompt-index-work-through-the-bootstrap
                (and (msets:has-all $out ("(SettingsGroup \"context\" (\"task_tokens\"))" "(PrologAvailable "
                                          "(Current 24000)" "(ProviderErrors ())"))
                     (and (fs:exists $policy)
                          (== (preq:field (preq:parse (fs:read-text $policy)) "task_tokens") (JsonNumber "24000")))))))
     (mc:stop $server)))

(= (msets:run $world $service $root $sdk)
   (let* (($_ (msets:values $world $service $root))
          ($_ (msets:imported $world $service $root))
          ($_ (msets:new-key $world $service $root))
          ($_ (msets:seed-blend $world $service $root))
          ($_ (msets:passive $world $service $root))
          ($_ (msets:failed-provider $world $service $root))
          ($_ (msets:bootstrap $world $service $root $sdk)))
     True))
