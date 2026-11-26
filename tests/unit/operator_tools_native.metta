; Comprehensive native qualification unit for the four R2 operator tools:
; config_ctl_cetta, usage_report_cetta, strategy_ctl_cetta, runtime_watch_cetta.
!(import! &self ../native_test_support.metta)
!(import! &self (library host_io))
!(import! &self ../../src/config_ctl_cetta.metta)
!(import! &self ../../src/usage_report_cetta.metta)
!(import! &self ../../src/strategy_ctl_cetta.metta)
!(import! &self ../../src/runtime_watch_cetta.metta)

(= (ot:dir $tag)
   (let* (($d (path:join (nt:required-env "HOME") $tag))
          ($mk (fs:make-directories $d 448)))
     $d))

(= (ot:entry $rel) (path:join (nt:required-env "TEST_REPOSITORY") $rel))

(= (ot:run-script $rel $args $env)
   (proc:run (list:append ((system:executable) "--no-results" "--lang" "petta" "--profile" "extended"
                           "--import-mode" "ancestor-walk" (ot:entry $rel)) $args)
             (nt:required-env "TEST_REPOSITORY") $env 20000 1048576))

(= (ot:test-config-ctl)
   (let* (($root (ot:dir "cfg-unit"))
          ($mk (fs:make-directories (path:join $root "config") 448))
          ($def-src (str:concat (fs:read-text (path:join (nt:required-env "TEST_REPOSITORY") "config/default.toml"))
                      "\n[secret_test]\napi_key = \"default-secret\"\n"))
          ($wdef (fs:write (path:join $root "config/default.toml") $def-src))
          ($orig "# Keep this comment\n[agent]\nsynthetic_model = \"fixture-model\"\n[channel]\nkind = \"telegram\"\n")
          ($wloc (fs:write (path:join $root "config/local.toml") $orig))
          ($wenv (fs:write (path:join $root ".env") "original environment\n"))
          ; 1) show reports origins and skips secret keys
          ($r-show (ot:run-script "scripts/config_ctl_cetta.metta" ("--root" $root "show") inherit))
          ($t1 (nt:require config-show-reports-origins
                 (case $r-show
                   (((proc:exited 0 $out1 $_e1)
                     (and (nt:contains $out1 "\"setting\": \"agent.synthetic_model\", \"value\": \"fixture-model\", \"origin\": \"local.toml\"")
                          (and (nt:contains $out1 "\"setting\": \"agent.shell_timeout_seconds\", \"value\": 120, \"origin\": \"default.toml\"")
                               (not (nt:contains $out1 "api_key")))))
                    ($bad1 False)))))
          ; 2) get returns default value
          ($r-get (ot:run-script "scripts/config_ctl_cetta.metta" ("--root" $root "get" "agent.shell_timeout_seconds") inherit))
          ($t2 (nt:require config-get-returns-default
                 (case $r-get
                   (((proc:exited 0 $out2 $_e2) (nt:contains $out2 "\"value\": 120"))
                    ($bad2 False)))))
          ; 3) render warns when caller environment is replaced by config
          ($r-ren (ot:run-script "scripts/config_ctl_cetta.metta" ("--root" $root "render")
                    (cons-atom ("METTACLAW_REASONING_EFFORT" "high") (nt:environment $root))))
          ($t3 (nt:require config-render-warns-env-override
                 (case $r-ren
                   (((proc:exited 0 $out3 $err3)
                     (and (nt:contains $out3 "\"valid\": true")
                          (and (nt:contains $err3 "METTACLAW_REASONING_EFFORT='high' from the caller's environment is replaced by 'default'")
                               (nt:contains (fs:read-text (path:join $root ".env")) "SYNTHETIC_MODEL='fixture-model'"))))
                    ($bad3 False)))))
          ; 4) invalid value, secret key, and --dry-run leave files untouched
          ($r-bad1 (ot:run-script "scripts/config_ctl_cetta.metta" ("--root" $root "set" "agent.llm_timeout_seconds" "0") inherit))
          ($r-bad2 (ot:run-script "scripts/config_ctl_cetta.metta" ("--root" $root "set" "secret_test.api_key" "secret") inherit))
          ($r-dry (ot:run-script "scripts/config_ctl_cetta.metta" ("--root" $root "--dry-run" "set" "agent.llm_timeout_seconds" "300") inherit))
          ($t4 (nt:require config-invalid-secret-dryrun-refused-cleanly
                 (and (case $r-bad1 (((proc:exited 2 $_o4 $_e4) True) ($b4 False)))
                 (and (case $r-bad2 (((proc:exited 2 $_o5 $_e5) True) ($b5 False)))
                 (and (case $r-dry (((proc:exited 0 $o6 $_e6) (nt:contains $o6 "\"applied\": false")) ($b6 False)))
                      (== (fs:read-text (path:join $root "config/local.toml")) $orig))))))
          ; 5) valid set preserves comments + updates .env + rejects shell metachar execution
          ($marker (path:join $root "must-not-exist"))
          ($evil (str:join "" ("$(touch " $marker "); `touch " $marker "`")))
          ($r-set1 (ot:run-script "scripts/config_ctl_cetta.metta" ("--root" $root "set" "agent.synthetic_model" $evil) inherit))
          ($r-sh (proc:run ("bash" "-c" "source \"$1\"" "cfg-test" (path:join $root ".env")) $root inherit 5000 4096))
          ($t5 (nt:require config-set-metachars-stay-data
                 (and (case $r-set1 (((proc:exited 0 $o7 $_e7) (nt:contains $o7 "\"applied\": true")) ($b7 False)))
                 (and (case $r-sh (((proc:exited 0 $_o8 $_e8) True) ($b8 False)))
                 (and (not (fs:exists $marker))
                      (nt:contains (fs:read-text (path:join $root "config/local.toml")) "# Keep this comment"))))))
          ; 6) concurrent two-process set preserves both updates under .edit.lock
          ($p1 (proc:spawn ((system:executable) "--no-results" "--lang" "petta" "--profile" "extended"
                  "--import-mode" "ancestor-walk" (ot:entry "scripts/config_ctl_cetta.metta")
                  "--root" $root "set" "agent.llm_timeout_seconds" "300")
                  ((mode stream) (max-bytes 65536) (timeout-ms 15000) (lifetime owner))))
          ($p2 (proc:spawn ((system:executable) "--no-results" "--lang" "petta" "--profile" "extended"
                  "--import-mode" "ancestor-walk" (ot:entry "scripts/config_ctl_cetta.metta")
                  "--root" $root "set" "agent.shell_timeout_seconds" "240")
                  ((mode stream) (max-bytes 65536) (timeout-ms 15000) (lifetime owner))))
          ($ci1 (proc:close-input $p1)) ($ci2 (proc:close-input $p2))
          ($a1 (proc:await $p1)) ($a2 (proc:await $p2))
          ($cl1 (proc:close $p1)) ($cl2 (proc:close $p2))
          ($env-after (fs:read-text (path:join $root ".env")))
          ($t6 (nt:require config-concurrent-sets-preserve-both
                 (and (nt:contains $env-after "SYNTHETIC_TIMEOUT='300'")
                      (nt:contains $env-after "METTACLAW_SHELL_TIMEOUT_SECONDS='240'"))))
          ; 7) .runtime-update-hold blocks live set
          ($whold (fs:write (path:join $root ".runtime-update-hold") "held\n"))
          ($r-hold (ot:run-script "scripts/config_ctl_cetta.metta" ("--root" $root "set" "agent.shell_timeout_seconds" "180") inherit))
          ($t7 (nt:require config-update-hold-blocks-set
                 (case $r-hold (((proc:exited 2 $_o9 $_e9) True) ($b9 False))))))
     True))

; What a render writes: defaults reach an instance, edits survive a render with
; the rest of the file, secrets stay out, and coding guidance keeps its lists.
(= (ot:cfg $root $args) (ot:run-script "scripts/config_ctl_cetta.metta" (cons-atom "--root" (cons-atom $root $args)) inherit))
(= (ot:code $result) (case $result (((proc:exited $code $_o $_e) $code) ($bad -1))))
(= (ot:out $result) (case $result (((proc:exited $_c $out $_e) $out) ($bad ""))))
(= (ot:test-config-render)
   (let* (($root (ot:dir "cfg-render"))
          ($_ (fs:make-directories (path:join $root "config") 448))
          ($defaults (re:replace-literal (re:compile "shell_timeout_seconds = 120")
                       (fs:read-text (path:join (nt:required-env "TEST_REPOSITORY") "config/default.toml"))
                       "shell_timeout_seconds = 77"))
          ($_ (fs:write (path:join $root "config/default.toml") $defaults))
          ($orig "# Keep this comment\n[agent]\nsynthetic_model = \"fixture-model\"\n[channel]\nkind = \"telegram\"\n")
          ($local (path:join $root "config/local.toml")) ($env (path:join $root ".env"))
          ($_ (fs:write $local $orig))
          ($_ (fs:write (path:join $root "config/secrets.env") "SECRET=fixture-secret\n"))
          ($_ (fs:write $env "original environment\n"))
          ; The cache TTL is validated before anything is replaced.
          ($forever (ot:cfg $root ("set" "context.anthropic_cache_ttl" "forever")))
          ($_ (nt:require config-invalid-cache-ttl-refused-before-replacing
                (and (== (ot:code $forever) 2)
                     (and (== (fs:read-text $local) $orig) (== (fs:read-text $env) "original environment\n")))))
          ($hour (ot:cfg $root ("set" "context.anthropic_cache_ttl" "1h")))
          ($_ (nt:require config-valid-cache-ttl-reaches-the-environment
                (and (== (ot:code $hour) 0) (nt:contains (fs:read-text $env) "METTACLAW_ANTHROPIC_CACHE_TTL='1h'"))))
          ; Defaults reach an instance unless it overrides them.
          ($_ (nt:require config-render-ok (== (ot:code (ot:cfg $root ("render"))) 0)))
          ($_ (nt:require config-defaults-reach-the-instance
                (and (nt:contains (fs:read-text $env) "METTACLAW_SHELL_TIMEOUT_SECONDS='77'")
                     (nt:contains (fs:read-text $env) "METTACLAW_LIFECYCLE_PATH="))))
          ($_ (nt:require config-an-override-replaces-the-default
                (and (== (ot:code (ot:cfg $root ("set" "agent.shell_timeout_seconds" "99"))) 0)
                     (nt:contains (fs:read-text $env) "METTACLAW_SHELL_TIMEOUT_SECONDS='99'"))))
          ; Coding guidance keeps its discovery lists and paths, and refuses a path out.
          ($_ (nt:require config-coding-guidance-rendered
                (let $text (fs:read-text $env)
                  (and (nt:contains $text (path:join $root "identity"))
                       (and (nt:contains $text "METTACLAW_CODING_ROOT_MARKERS=")
                            (nt:contains $text "METTACLAW_CODING_DOC_FALLBACKS="))))))
          ($before (fs:read-text $local))
          ($_ (nt:require config-coding-lists-refuse-a-path-out
                (and (== (ot:code (ot:cfg $root ("set" "coding.project_root_markers" "[\"../outside\"]"))) 2)
                     (and (== (ot:code (ot:cfg $root ("set" "coding.project_doc_fallback_filenames" "[\"../outside\"]"))) 2)
                          (== (fs:read-text $local) $before)))))
          ($_ (nt:require config-coding-list-edit-reaches-the-environment
                (and (== (ot:code (ot:cfg $root ("set" "coding.project_doc_fallback_filenames" "[\"TEAM_GUIDE.txt\"]"))) 0)
                     (nt:contains (fs:read-text $env) "TEAM_GUIDE.txt"))))
          ; A set survives a later render, the rest of the file is kept, and no secret leaks.
          ($set (ot:cfg $root ("set" "agent.llm_timeout_seconds" "300")))
          ($_ (nt:require config-set-asks-for-a-restart
                (and (== (ot:code $set) 0) (nt:contains (ot:out $set) "\"restart_required\": true"))))
          ($_ (nt:require config-set-keeps-comments-and-other-values
                (and (nt:contains (fs:read-text $local) "# Keep this comment")
                     (nt:contains (fs:read-text $local) "synthetic_model = \"fixture-model\""))))
          ($_ (fs:write $env "stale environment\n"))
          ($render (ot:cfg $root ("render")))
          ($_ (nt:require config-edits-survive-a-render
                (let $text (fs:read-text $env)
                  (and (== (ot:code $render) 0)
                       (and (nt:contains $text "SYNTHETIC_TIMEOUT='300'") (nt:contains $text "SYNTHETIC_MODEL='fixture-model'"))))))
          ($_ (nt:require config-secrets-stay-out-and-unchanged
                (and (not (nt:contains (str:concat (fs:read-text $env) (str:concat (ot:out $set) (ot:out $render))) "fixture-secret"))
                     (== (fs:read-text (path:join $root "config/secrets.env")) "SECRET=fixture-secret\n"))))
          ($_ (nt:require config-environment-is-private
                (== (str:strip-python (nt:exit-ok (proc:run ("stat" "-c" "%a" $env) $root inherit 5000 64))) "600")))
          ; The short command works from any directory on the named engine.
          ($short (proc:run ("sh" (ot:entry "scripts/config_ctl") "--root" $root "get" "agent.shell_timeout_seconds")
                            (nt:required-env "HOME") (cons-atom ("CETTA_BIN" (system:executable)) (nt:environment $root)) 20000 65536))
          ($_ (nt:require config-short-command-reads-the-setting
                (and (== (ot:code $short) 0) (nt:contains (ot:out $short) "\"value\": 99")))))
     True))

(= (ot:wait-socket $sock $left)
   (if (or (fs:exists $sock) (<= $left 0)) (fs:exists $sock)
     (progn (host:wait () 50) (ot:wait-socket $sock (- $left 1)))))

(= (ot:test-ledger-tools)
   (let* (
          ; Pure ISO-8601 parser checks
          ($iso-z (ur:parse-iso "2023-11-14T22:00:00Z"))
          ($iso-off (ur:parse-iso "2023-11-15T00:00:00+02:00"))
          ($iso-notz (ur:parse-iso "2023-11-14T22:00:00"))
          ($t-iso (nt:require usage-iso-parsing-exact
                    (and (== $iso-z (UrOk 1699999200.0))
                    (and (== $iso-off (UrOk 1699999200.0))
                         (case $iso-notz (((UrError $_m) True) ($b False)))))))
          ; Start owned disposable native input_ledger_server.metta
          ($ldir (ot:dir "ledger-unit"))
          ($sock (path:join $ldir "ledger.sock"))
          ($dbdir (path:join $ldir "db"))
          ($repo (nt:required-env "TEST_REPOSITORY"))
          ($worker (path:join (path:dirname (system:executable)) "cetta-value-worker"))
          ($srv-env (list:append (("CETTA_DB_SOCKET" $sock) ("CETTA_DB_SOURCE" $dbdir)) (nt:environment $ldir)))
          ($srv (proc:spawn ($worker "--value-bytes" "67108864" "--serve" "--lang" "petta"
                             (ot:entry "src/input_ledger_server.metta") "db-server:configuration")
                  ((cwd $repo) (env $srv-env) (max-bytes 65536) (lifetime owner))))
          ($ready (ot:wait-socket $sock 100))
          ($t-srv (nt:require disposable-ledger-started $ready))
          ; Exercise strategy_ctl_cetta
          ($env-s (cons-atom ("METTACLAW_INPUT_LEDGER_SOCKET" $sock) (nt:environment $ldir)))
          ($acc1 (ot:run-script "scripts/strategy_ctl_cetta.metta" ("accept" "task:1" "solve 1") $env-s))
          ($acc-dup (ot:run-script "scripts/strategy_ctl_cetta.metta" ("accept" "task:1" "solve 1") $env-s))
          ($acc-conf (ot:run-script "scripts/strategy_ctl_cetta.metta" ("accept" "task:1" "different") $env-s))
          ($acc2 (ot:run-script "scripts/strategy_ctl_cetta.metta" ("accept" "task:2" "solve 2") $env-s))
          ($can-noconf (ot:run-script "scripts/strategy_ctl_cetta.metta" ("cancel" "task:1") $env-s))
          ($can1 (ot:run-script "scripts/strategy_ctl_cetta.metta" ("cancel" "task:1" "--confirm") $env-s))
          ($cap (ledger-client:call $sock "capture" ("task:2" "d2" "null" "g1" "owner-u" "rc1")))
          ($dec (ledger-client:call $sock "decide" (0 "command" "shell" "{\"commands\":[\"(nop)\"]}" "rd1")))
          ($rec (ot:run-script "scripts/strategy_ctl_cetta.metta" ("reconcile" "owner-u:0" "ok-result" "--confirm") $env-s))
          ($st-mid (ot:run-script "scripts/strategy_ctl_cetta.metta" ("status") $env-s))
          ($obs (ledger-client:call $sock "observe" ((("owner-u:0" (sctl:digest "ok-result"))) "ro1")))
          ($fin (ot:run-script "scripts/strategy_ctl_cetta.metta" ("finish" "task:2" "owner-u:0" "--confirm") $env-s))
          ($st-end (ot:run-script "scripts/strategy_ctl_cetta.metta" ("status") $env-s))
          ($t-s1 (nt:require strategy-ctl-accept-dup-conflict
                   (and (case $acc1 (((proc:exited 0 $o1 $_e1) (== (str:trim $o1) "true")) ($b1 False)))
                   (and (case $acc-dup (((proc:exited 1 $o2 $_e2) (== (str:trim $o2) "false")) ($b2 False)))
                        (case $acc-conf (((proc:exited 2 $_o3 $_e3) True) ($b3 False)))))))
          ($t-s2 (nt:require strategy-ctl-cancel-requires-confirm
                   (and (case $can-noconf (((proc:exited 2 $_o4 $_e4) True) ($b4 False)))
                        (case $can1 (((proc:exited 0 $o5 $_e5) (== (str:trim $o5) "true")) ($b5 False))))))
          ($t-s3 (nt:require strategy-ctl-reconcile-status-finish
                   (and (case $rec (((proc:exited 0 $o6 $_e6) (== (str:trim $o6) "true")) ($b6 False)))
                   (and (case $st-mid (((proc:exited 0 $o7 $_e7) (nt:contains $o7 "\"payload\": \"ok-result\"")) ($b7 False)))
                   (and (case $fin (((proc:exited 0 $o8 $_e8) (== (str:trim $o8) "true")) ($b8 False)))
                        (case $st-end (((proc:exited 0 $o9 $_e9) (nt:contains $o9 "\"achieved\": 1")) ($b9 False))))))))
          ; Seed LLM usage calls crossing UTC vs Europe/Prague midnight boundary
          ($la1 (ledger-client:call $sock "llm-admit" ("c1" "anthropic" 1000 "fp1" (none none) "turn" "owner-u"
                  "{\"instance\":\"lila\",\"provider\":\"anthropic\",\"model\":\"claude\",\"purpose\":\"turn\",\"trigger\":\"user\"}" 1700000100.0 "la1")))
          ($lc1 (ledger-client:call $sock "llm-complete" ("c1" none
                  "{\"counts\":{\"input_tokens\":1000,\"cached_tokens\":800,\"output_tokens\":150},\"reported_usage\":{\"cache_creation_input_tokens\":200}}"
                  150 "end_turn" "{}" none none none none 1700000106.0 "lc1")))
          ($la2 (ledger-client:call $sock "llm-admit" ("c2" "anthropic" 1000 "fp2" (none none) "turn" "owner-u"
                  "{\"instance\":\"lila\",\"provider\":\"anthropic\",\"model\":\"claude\",\"purpose\":\"turn\",\"trigger\":\"user\"}" 1700000200.0 "la2")))
          ($lf2 (ledger-client:call $sock "llm-fail" ("c2" none "turn" 0 "{\"sent\":false}" none none none none none 1700000201.0 "lf2")))
          ($la3 (ledger-client:call $sock "llm-admit" ("c3" "kimi" 1000 "fp3" (none none) "turn" "owner-u"
                  "{\"instance\":\"lila\",\"provider\":\"kimi\",\"model\":\"k2\"}" 1700004600.0 "la3")))
          ($ur-utc (ot:run-script "scripts/usage_report_cetta.metta"
                     ("--socket" $sock "--since" "2023-11-14T22:00:00Z" "--until" "2023-11-16T00:00:00Z" "--timezone" "UTC") inherit))
          ($ur-prg (ot:run-script "scripts/usage_report_cetta.metta"
                     ("--socket" $sock "--since" "2023-11-14T22:00:00Z" "--until" "2023-11-16T00:00:00Z" "--timezone" "Europe/Prague") inherit))
          ($ur-badtz (ot:run-script "scripts/usage_report_cetta.metta"
                       ("--socket" $sock "--timezone" "Invalid/Nowhere") inherit))
          ($t-u1 (nt:require usage-report-utc-grades-all-buckets
                   (case $ur-utc
                     (((proc:exited 0 $ou1 $_eu1)
                       (and (nt:contains $ou1 "\"known_input_tokens\": 1000")
                       (and (nt:contains $ou1 "\"known_cache_write_tokens\": 200")
                       (and (nt:contains $ou1 "\"not_submitted_calls\": 1")
                            (nt:contains $ou1 "\"calls\": 3")))))
                      ($bu1 False)))))
          ($t-u2 (nt:require usage-report-prague-shifts-midnight-call-to-next-day
                   (case $ur-prg
                     (((proc:exited 0 $ou2 $_eu2)
                       (and (nt:contains $ou2 "\"timezone\": \"Europe/Prague\"")
                            (nt:contains $ou2 "\"day\": \"2023-11-15\"")))
                      ($bu2 False)))))
          ($t-u3 (nt:require usage-report-rejects-invalid-timezone
                   (case $ur-badtz (((proc:exited 1 $_ou3 $_eu3) True) ($bu3 False)))))
          ; Always stop, await, and close the owned ledger worker cleanly!
          ($cancel-srv (proc:cancel $srv))
          ($await-srv (proc:await $srv))
          ($close-srv (proc:close $srv)))
     True))

(= (ot:py-cmds) "def dispatch(cmd):\n    if cmd in (\"/start\", \"/stop\"):\n        pass\n")
(= (ot:nat-cmds) "(= (tc:answer $name $args)\n   (case $name\n     ((\"/start\" (tc:lifecycle-start (lifecycle:start)))\n      (\"/stop\" (tc:lifecycle-stop (lifecycle:stop))))))\n")
(= (ot:nat-loop) "(= (turnCandidate $p)\n   (case (lifecycle:enabled) (($x $x))))\n(= (waitCandidate $p)\n   (case (lifecycle:enabled) (($x $x))))\n")
(= (ot:nat-life) "(= (lifecycle-native:enabled) 1)\n(= (lifecycle-native:start) \"started\")\n(= (lifecycle-native:stop) \"stopped\")\n")
(= (ot:nat-facade) "(add-atom &self (= (lifecycle:enabled) (lifecycle-native:enabled)))\n(add-atom &self (= (lifecycle:start) (lifecycle-native:start)))\n(add-atom &self (= (lifecycle:stop) (lifecycle-native:stop)))\n")
(= (ot:leg-loop) "(= (turnCandidate $p)\n   (case (py-call (lifecycle.cognition_enabled)) (($x $x))))\n(= (waitCandidate $p)\n   (case (py-call (lifecycle.cognition_enabled)) (($x $x))))\n")
(= (ot:leg-life) "def cognition_enabled():\n    return 1\ndef start():\n    return \"started\"\ndef stop():\n    return \"stopped\"\n")

(= (ot:rev-native)
   (JsonObject ((JsonPair "channels/telegram.py" (ot:py-cmds))
                (JsonPair "src/lifecycle_cetta.metta" (ot:nat-life))
                (JsonPair "src/loop.metta" (ot:nat-loop))
                (JsonPair "src/mode_state.metta" (ot:nat-facade)))))

(= (ot:rev-legacy)
   (JsonObject ((JsonPair "channels/telegram.py" (ot:py-cmds))
                (JsonPair "src/lifecycle.py" (ot:leg-life))
                (JsonPair "src/loop.metta" (ot:leg-loop)))))

(= (ot:rev-responder)
   (JsonObject ((JsonPair "channels/telegram_control.metta" (ot:nat-cmds))
                (JsonPair "src/lifecycle_cetta.metta" (ot:nat-life))
                (JsonPair "src/loop.metta" (ot:nat-loop))
                (JsonPair "src/mode_state.metta" (ot:nat-facade)))))

(= (ot:test-runtime-watch)
   (let* (
          ; 3 positive lifecycle authority fixtures
          ($t1 (nt:require rw-accepts-native-legacy-and-responder
                 (and (rw:has-lifecycle-authority-sources (ot:rev-native))
                 (and (rw:has-lifecycle-authority-sources (ot:rev-legacy))
                      (rw:has-lifecycle-authority-sources (ot:rev-responder))))))
          ; 14 negative controls matching tests/test_runtime_watch.py
          ($stop "(\"/stop\" (tc:lifecycle-stop (lifecycle:stop)))")
          ($n1 (sf:put (ot:rev-responder) "channels/telegram_control.metta" (rw:replace (ot:nat-cmds) $stop (str:concat "; " (str:concat $stop "\n")))))
          ($n2 (sf:put (ot:rev-responder) "channels/telegram_control.metta" (rw:replace (ot:nat-cmds) $stop "")))
          ($n3 (sf:put (ot:rev-responder) "channels/telegram_control.metta"
                 (str:concat (rw:replace (ot:nat-cmds) $stop "") (str:concat "(= (tc:other $name) (case $name (" (str:concat $stop ")))\n")))))
          ($n4 (sf:put (ot:rev-native) "src/loop.metta"
                 "(= (turnCandidate $p) $p)\n!(lifecycle:enabled)\n(= (waitCandidate $p) (case (lifecycle:enabled) (($x $x))))\n"))
          ($n5 (sf:put (ot:rev-native) "channels/telegram.py" (rw:replace (ot:py-cmds) "cmd in" "cmd !=")))
          ($n6 (sf:put (ot:rev-native) "src/lifecycle_cetta.metta" (rw:replace (ot:nat-life) "(= (lifecycle-native:stop) \"stopped\")" "")))
          ($n7 (sf:put (ot:rev-native) "src/mode_state.metta" ""))
          ($n8 (sf:put (ot:rev-native) "src/loop.metta"
                 "(= (turnCandidate $p) (case (lifecycle:other) (($x $x))))\n(= (waitCandidate $p) (case (lifecycle:enabled) (($x $x))))\n"))
          ($n9 (sf:put (ot:rev-native) "src/loop.metta" (rw:replace (ot:nat-loop) "(case (lifecycle:enabled)" "; (case (lifecycle:enabled)")))
          ($n10 (sf:put (ot:rev-native) "src/loop.metta" (rw:replace (ot:nat-loop) "(case (lifecycle:enabled)" "\"(case (lifecycle:enabled)\"")))
          ($n11 (sf:put (ot:rev-legacy) "src/lifecycle.py" (rw:replace (ot:leg-life) "def stop():" "def retired_stop():")))
          ($n12 (sf:put (ot:rev-legacy) "src/loop.metta"
                  "(= (turnCandidate $p) (case (py-call (lifecycle.retired)) (($x $x))))\n(= (waitCandidate $p) (case (py-call (lifecycle.cognition_enabled)) (($x $x))))\n"))
          ($n13 (sf:put (ot:rev-native) "channels/telegram.py" "# if cmd in (\"/start\", \"/stop\"): pass\n"))
          ($n14 (sf:put (ot:rev-native) "src/loop.metta" JsonNull))
          ($negs ($n1 $n2 $n3 $n4 $n5 $n6 $n7 $n8 $n9 $n10 $n11 $n12 $n13 $n14))
          ($any-passed (foldl-atom $negs False $acc $case (or $acc (rw:has-lifecycle-authority-sources $case))))
          ($t2 (nt:require rw-rejects-all-14-negative-authority-controls (not $any-passed)))
          ; Current repo HEAD passes real git-backed authority check
          ($repo (nt:required-env "TEST_REPOSITORY"))
          ($head-ok (case (rw:head $repo)
                      (((RwOk $h) (rw:has-lifecycle-authority $repo $h))
                       ($err False))))
          ($t3 (nt:require rw-accepts-current-repo-head $head-ok))
          ; Probation state machine: candidate acceptance + rollback refusal
          ($wdir (ot:dir "rw-unit"))
          ($dep-path (path:join $wdir "deployment.json"))
          ($log-path (path:join $wdir "watch.jsonl"))
          ($opts (JsonObject ((JsonPair "--root" $wdir) (JsonPair "--service" "unit-test.service")
                              (JsonPair "--deployment" $dep-path) (JsonPair "--log" $log-path)
                              (JsonPair "--failures" "3"))))
          ($dep0 (JsonObject ((JsonPair "status" "probation") (JsonPair "candidate" "cand-sha")
                              (JsonPair "previous" "prev-sha") (JsonPair "probation_until" 2000)
                              (JsonPair "consecutive_failures" 2))))
          ; 3rd failure during probation with head mismatch refuses rollback cleanly
          ($obs-bad (JsonObject ((JsonPair "observed_at" 1500) (JsonPair "active" True)
                                 (JsonPair "head" "wrong-sha") (JsonPair "problems" (JsonArray ("telegram-poll-stale"))))))
          ($rc-bad (rw:manage-step $opts $dep0 $obs-bad 1500))
          ($saved-bad (sf:native-object $dep-path))
          ($t4 (nt:require rw-refuses-rollback-on-generation-mismatch
                 (and (== $rc-bad 1)
                 (and (== (sf:get $saved-bad "last_action" "") "rollback-refused-generation-mismatch")
                      (== (sf:get $saved-bad "status" "") "probation")))))
          ; Healthy observation past probation_until accepts candidate and logs JSONL
          ($obs-ok (JsonObject ((JsonPair "observed_at" 2500) (JsonPair "active" True)
                                (JsonPair "head" "cand-sha-123456") (JsonPair "problems" (JsonArray ())))))
          ($rc-ok (rw:manage-step $opts $saved-bad $obs-ok 2500))
          ($saved-ok (sf:native-object $dep-path))
          ($log-txt (fs:read-text $log-path))
          ($t5 (nt:require rw-accepts-candidate-after-probation
                 (and (== $rc-ok 0)
                 (and (== (sf:get $saved-ok "status" "") "accepted")
                 (and (== (sf:get $saved-ok "last_action" "") "candidate-accepted")
                 (and (== (sf:get $saved-ok "last_good" "") "cand-sha-123456")
                      (nt:contains $log-txt "\"action\": \"candidate-accepted\""))))))))
     True))

!(let* (($p1 (ot:test-config-ctl))
        ($p1r (ot:test-config-render))
        ($p2 (ot:test-ledger-tools))
        ($p3 (ot:test-runtime-watch)))
   (println! UNIT_OPERATOR_TOOLS_NATIVE_OK))
