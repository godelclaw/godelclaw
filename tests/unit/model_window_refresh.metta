; End-to-end native unit test of Anthropic model-window observation refresh
; (src/model_menu_cetta.metta -> detached src/provider_observation_main.metta)
; against tests/fixtures/native_http_peer.c with a fixture credential, plus
; fail-closed unknown-window preparation semantics (src/context_compaction.metta,
; src/provider_rejection_cetta.metta). No Python, no live state, no network.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/state_io.metta)
!(import! &self ../../src/utils_cetta.metta)

(= (mw:home) (nt:required-env "HOME"))
(= (mw:peer-dir) (path:join (mw:home) "peer"))
(= (mw:peer-file $name) (path:join (mw:peer-dir) $name))
(= (mw:peer-write $name $text) (fs:write (mw:peer-file $name) $text))
(= (mw:peer-count)
   (case (catch (str:string->number (str:trim (fs:read-text (mw:peer-file "count")))))
     (((Error $w $y) 0) ($n $n))))
(= (mw:wait-file $path $left)
   (if (fs:exists $path) True
     (if (<= $left 0) False (let $_ (sleep 0.05) (mw:wait-file $path (- $left 1))))))
(= (mw:wait-ledger $server $socket $left)
   (if (fs:exists $socket) True
     (if (<= $left 0) False (let $_ (proc:wait $server 20) (mw:wait-ledger $server $socket (- $left 1))))))
(= (mw:wait-window $id $expected-ctx $left)
   (let* (($record (model:record (model:observations) "model-window"))
          ($value (model:object (json:get $record "value")))
          ($obs-id (json:get $value "id"))
          ($obs-ctx (json:get $value "context"))
          ($obs-err (json:get $record "last_error")))
     (if (and (== $obs-id $id) (and (== $obs-ctx $expected-ctx) (== $obs-err JsonNull)))
       True
       (if (<= $left 0) False (let $_ (sleep 0.05) (mw:wait-window $id $expected-ctx (- $left 1)))))))
(= (mw:wait-error $expected-err $expected-model $left)
   (let* (($record (model:record (model:observations) "model-window"))
          ($err (json:get $record "last_error"))
          ($who (json:get $record "attempted_model")))
     (if (and (== $err $expected-err) (== $who $expected-model))
       True
       (if (<= $left 0) False (let $_ (sleep 0.05) (mw:wait-error $expected-err $expected-model (- $left 1)))))))
(= (mw:repeat $chunk $n $acc)
   (if (<= $n 0) $acc (mw:repeat $chunk (- $n 1) (str:concat $acc $chunk))))
(= (mw:run-script $env $source)
   (let* (($path (path:join (mw:home) (str:concat "probe-" (str:concat (system:uuid4-hex) ".metta"))))
          ($menu (repr (path:join (system:cwd) "src/model_menu_cetta.metta")))
          ($_ (fs:write $path (str:join "" ("!(import! &self " $menu ")\n" $source))))
          ($out (nt:exit-ok (proc:run ((system:executable) "--lang" "petta" "--import-mode" "ancestor-walk" "--quiet" $path)
                                      (system:cwd) $env 20000 1048576))))
     $out))
(= (mw:seed-observations $text)
   (let* (($path (model:observation-path))
          ($_ (fs:make-directories (path:dirname $path) 448)))
     (fs:write $path $text)))

!(let* (($home (mw:home))
        ($root (system:cwd))
        ($base-env (nt:environment $home))
        ($peer-bin (path:join $home "native-http-peer"))
        ($_ (nt:exit-ok (proc:run ("cc" "-std=c11" "-O2" "-Wall" "-Wextra" "-Werror" "tests/fixtures/native_http_peer.c" "-o" $peer-bin)
                                  $root $base-env 30000 65536)))
        ($_ (fs:make-directories (mw:peer-dir) 448))
        ($_ (mw:peer-write "status" "200"))
        ($_ (mw:peer-write "delay-ms" "0"))
        ($_ (mw:peer-write "response-1.body" "{\"id\":\"claude-sonnet-5-5\",\"max_input_tokens\":1000000}"))
        ($_ (mw:peer-write "response-2.body" "{\"id\":\"claude-sonnet-5-5\",\"max_input_tokens\":1000000}"))
        ($_ (mw:peer-write "response-3.body" "{\"id\":\"claude-opus-5-5\",\"max_input_tokens\":500000}"))
        ($_ (mw:peer-write "response-4.body" "{\"error\":\"service unavailable\"}"))
        ($peer (proc:spawn ($peer-bin (mw:peer-dir)) ((cwd $root) (env $base-env) (max-bytes 65536) (lifetime owner))))
        ($_ (nt:require peer-ready (mw:wait-file (mw:peer-file "port") 200)))
        ($port (str:trim (fs:read-text (mw:peer-file "port"))))
        ($base-url (str:join "" ("http://127.0.0.1:" $port "/v1")))
        ($obs-path (model:observation-path))
        ($model-path (model:state-path))
        ($child-env (list:append (("ANTHROPIC_API_KEY" "fixture-anthropic-key")
                                  ("ANTHROPIC_BASE_URL" $base-url)
                                  ("ANTHROPIC_MODELS" "claude-sonnet-5-5,claude-opus-5-5")
                                  ("METTACLAW_PROVIDER_OBSERVATION_PATH" $obs-path)
                                  ("METTACLAW_MODEL_STATE_PATH" $model-path)
                                  ("METTACLAW_RUNTIME_ROOT" $root)
                                  ("SYNTHETIC_INTROSPECT_TIMEOUT" "5"))
                      $base-env))

        ;; 1. Seed a 1-second-old ChatGPT model-window observation (exact live transition).
        ;;    Switching to claude-sonnet-5-5 must NOT be suppressed by ChatGPT's 300s TTL or
        ;;    30s retry window: it must immediately spawn provider_observation_main.metta and
        ;;    record the Anthropic 1M window.
        ($now (system:wall-time))
        ($_ (mw:seed-observations
              (str:join "" ("{\"schema\": 1, \"model-window\": {\"observed_at\": " (repr (- $now 1.0))
                            ", \"attempted_at\": " (repr (- $now 1.0))
                            ", \"value\": {\"id\": \"chatgpt:gpt-6.2-sol\", \"context\": 872000, \"source\": \"chatgpt-models\"}}}"))))
        ($set1 (mw:run-script $child-env
                 "!(println! (SET1 (model:set \"claude-sonnet-5-5\")))\n"))
        ($_ (nt:require switch-to-sonnet-applied (nt:contains $set1 "model set to 'claude-sonnet-5-5' (anthropic)")))
        ($_ (nt:require sonnet-window-recorded (mw:wait-window "claude-sonnet-5-5" 1000000 300)))
        ($rec1 (model:record (model:observations) "model-window"))
        ($val1 (model:object (json:get $rec1 "value")))
        ($_ (nt:require sonnet-source-metadata (nt:equal (json:get $val1 "source") "provider-model-metadata")))
        ($_ (nt:require peer-got-one-request (nt:equal (mw:peer-count) 1)))
        ($hdr1 (fs:read-text (mw:peer-file "request-1.headers")))
        ($_ (nt:require sonnet-request-path (nt:contains $hdr1 "GET /v1/models/claude-sonnet-5-5 ")))
        ($_ (nt:require sonnet-request-key (nt:contains $hdr1 "x-api-key: fixture-anthropic-key")))
        ($_ (nt:require sonnet-request-version (nt:contains $hdr1 "anthropic-version: 2023-06-01")))
        ($_ (nt:require sonnet-capacity-uses-1m (nt:equal (turn:context-capacity 32768) 967232)))

        ;; 1b. Direct turn:context-capacity trigger (without model:set, e.g. after restart
        ;;     with an empty observations file) also spawns the detached child and records 1M.
        ($_ (mw:seed-observations "{\"schema\": 1}"))
        ($budget-path (repr (path:join $root "src/context_budget_cetta.metta")))
        ($cap-kick (mw:run-script $child-env
                     (str:join "" ("!(import! &self " $budget-path ")\n!(println! (CAP_KICK (turn:context-capacity 32768)))\n"))))
        ($_ (nt:require cap-kick-starts-unknown (nt:contains $cap-kick "(CAP_KICK \"\")")))
        ($_ (nt:require sonnet-window-recorded-via-capacity (mw:wait-window "claude-sonnet-5-5" 1000000 300)))
        ($_ (nt:require peer-got-two-sonnet-requests (nt:equal (mw:peer-count) 2)))
        ($_ (nt:require capacity-now-known-after-kick (nt:equal (turn:context-capacity 32768) 967232)))

        ;; 2. Immediate repeat refresh for the same freshly observed Anthropic model starts nothing.
        ($dup (mw:run-script $child-env
                "!(println! (DUP (model:refresh (\"model-window\"))))\n"))
        ($_ (nt:require same-model-fresh-no-repeat (nt:contains $dup "(DUP ())")))
        ($_ (nt:require still-two-sonnet-requests (nt:equal (mw:peer-count) 2)))

        ;; 3. Switching from freshly observed claude-sonnet-5-5 to claude-opus-5-5 refreshes immediately.
        ($set2 (mw:run-script $child-env
                 "!(println! (SET2 (model:set \"claude-opus-5-5\")))\n"))
        ($_ (nt:require switch-to-opus-applied (nt:contains $set2 "model set to 'claude-opus-5-5' (anthropic)")))
        ($_ (nt:require opus-window-recorded (mw:wait-window "claude-opus-5-5" 500000 300)))
        ($_ (nt:require peer-got-three-requests (nt:equal (mw:peer-count) 3)))
        ($hdr3 (fs:read-text (mw:peer-file "request-3.headers")))
        ($_ (nt:require opus-request-path (nt:contains $hdr3 "GET /v1/models/claude-opus-5-5 ")))
        ($_ (nt:require opus-capacity-uses-500k (nt:equal (turn:context-capacity 32768) 467232)))

        ;; 4. Non-Anthropic models never spawn the Anthropic model-window child.
        ($non-anth (mw:run-script $child-env
                     "!(let $_ (model:set \"chatgpt:gpt-6.2-sol\") (println! (CHATGPT_REF (model:refresh (\"model-window\")))))\n"))
        ($_ (nt:require non-anthropic-skips-window-child (nt:contains $non-anth "(CHATGPT_REF ())")))
        ($_ (nt:require still-three-requests (nt:equal (mw:peer-count) 3)))

        ;; 5. Failed child lookup (HTTP 503) preserves prior value, records last_error + attempted_model,
        ;;    and causes context:prepare to fail closed with the lookup reason.
        ($_ (mw:peer-write "status" "503"))
        ($set3 (mw:run-script $child-env
                 "!(println! (SET3 (model:set \"claude-sonnet-5-5\")))\n"))
        ($_ (nt:require switch-back-sonnet (nt:contains $set3 "model set to 'claude-sonnet-5-5' (anthropic)")))
        ($_ (nt:require http-503-error-recorded (mw:wait-error "HTTP 503" "claude-sonnet-5-5" 300)))
        ($_ (nt:require unknown-capacity-after-503 (nt:equal (turn:context-capacity 32768) "")))

        ;; 6. Start a local native ledger to verify context:prepare semantics end-to-end.
        ($socket (nt:required-env "METTACLAW_INPUT_LEDGER_SOCKET"))
        ($store (path:join $home "store"))
        ($worker (path:join (nt:required-env "NATIVE_TEST_CORE") "cetta-value-worker"))
        ($ledger-env (cons-atom ("CETTA_DB_SOURCE" $store) (cons-atom ("CETTA_DB_SOCKET" $socket) $base-env)))
        ($ledger (proc:spawn ($worker "--value-bytes" "268435456" "--serve" "--lang" "petta"
                                      (path:join $root "src/input_ledger_server.metta") "db-server:configuration")
                             ((cwd $root) (env $ledger-env) (max-bytes 65536) (lifetime owner))))
        ($_ (nt:require ledger-ready (mw:wait-ledger $ledger $socket 500)))
        ($_own (state:ledger "owner" ("mw-owner" (state:request-id))))
        ($_acc (state:ledger "request-accept" ("req-1" "Check model window" "" "message" "s1" "d1" (state:request-id))))
        ($_beg (state:ledger "task-begin" ("req-1" "operator-event" "" "" "" (state:request-id))))
        ($_foc (state:ledger "task-focus" ("req-1" (state:request-id))))
        ($small-base (RoleContext coding-messages-empty "skills" "obs" () () "data" "inst"))

        ;; 6a. Failed live child lookup ("HTTP 503") surfaces as context-window-unknown: HTTP 503.
        ($prep-503 (context:prepare $small-base "req-1" "continuation-tick" "" "claude-sonnet-5-5" 32768 "default"))
        ($_ (nt:require failed-503-surfaces-reason
              (nt:equal $prep-503 (ContextPreparationFailed "context-window-unknown: HTTP 503"))))

        ;; 6b. Exact legacy outage file shape (chatgpt value, last_error: submit-error, no attempted_model)
        ;;     surfaces as context-window-unknown: submit-error, never exceeds-window.
        ($_ (mw:seed-observations
              (str:join "" ("{\"schema\": 1, \"model-window\": {\"observed_at\": " (repr (- $now 10.0))
                            ", \"attempted_at\": " (repr (- $now 1.0))
                            ", \"last_error\": \"submit-error\""
                            ", \"value\": {\"id\": \"chatgpt:gpt-6.2-sol\", \"context\": 872000, \"source\": \"chatgpt-models\"}}}"))))
        ($prep-submit (context:prepare $small-base "req-1" "continuation-tick" "" "claude-sonnet-5-5" 32768 "default"))
        ($_ (nt:require submit-error-surfaces-reason
              (nt:equal $prep-submit (ContextPreparationFailed "context-window-unknown: submit-error"))))
        ;; End-to-end state:prepare-context also surfaces context-window-unknown: submit-error.
        ($state-submit (state:prepare-context "PROMPT" 32768))
        ($_ (nt:require state-prepare-surfaces-submit-error
              (nt:equal $state-submit (ContextPreparationFailed "context-window-unknown: submit-error"))))

        ;; 6c. Explicit configured/known capacity still works even if last_error was recorded.
        ($prep-known (context:prepare $small-base "req-1" "continuation-tick" "967232" "claude-sonnet-5-5" 32768 "default"))
        ($_ (nt:require known-capacity-overrides-error (nt:equal (get-type $prep-known) String)))

        ;; 6d. Unknown capacity with no recorded error yet: small prompt passes without compaction,
        ;;     but a prompt exceeding 90% of the fallback window (WinCompactV1) or the full fallback
        ;;     fails closed with context-window-unknown rather than compacting or claiming exceeds-window.
        ($_ (mw:seed-observations "{\"schema\": 1}"))
        ($prep-small-ok (context:prepare $small-base "req-1" "continuation-tick" "" "claude-sonnet-5-5" 32768 "default"))
        ($_ (nt:require unknown-window-small-passes (nt:equal (get-type $prep-small-ok) String)))
        ($kib (mw:repeat "0123456789abcdef" 64 ""))
        ($chunk16k (mw:repeat $kib 16 ""))
        ;; Add 2 transcript steps (~20k bytes each) + 464 KiB base so available > 0 (~9.8k tokens)
        ;; while the transcript (~16.4k tokens) crosses the compaction threshold under 200k fallback
        ;; (must fail closed with context-window-unknown and NEVER compact!).
        ($step-body (mw:repeat $kib 20 ""))
        ($step-res (state:encoded (JsonObject ((JsonPair "disposition" (JsonString "returned")) (JsonPair "result" (JsonString $step-body))))))
        ($cap1 (state:ledger "capture" ("req-1" "0000000000000000000000000000000000000000000000000000000000000000" "{}" "" "mw-owner" (state:request-id))))
        ((captured $ser1 $_r1 $_m1 $_o1) $cap1)
        ($_p1 (state:ledger "task-proposal" ($ser1 "req-1" "[[\"shell\",\"s1\"]]" "not run" (state:request-id))))
        ((issued $tkt1) (state:ledger "decide" ($ser1 "command" "batch" "{\"commands\":[[\"shell\",\"s1\"]],\"limit\":1}" (state:request-id))))
        ($_ab1 (state:ledger "attempt-begin" ($tkt1 0 "[\"shell\",\"s1\"]" (state:request-id))))
        ($_ar1 (state:ledger "attempt-result" ($tkt1 0 "[\"shell\",\"s1\"]" $step-res (state:request-id))))
        ($_rep1 (state:ledger "report" ($tkt1 "(COMMAND_RETURN: ok)" "returned" "unknown" "abcd" 32 (state:request-id))))
        ($cap2 (state:ledger "capture" ("req-1" "0000000000000000000000000000000000000000000000000000000000000000" "{}" "" "mw-owner" (state:request-id))))
        ((captured $ser2 $_r2 $_m2 $_o2) $cap2)
        ($_p2 (state:ledger "task-proposal" ($ser2 "req-1" "[[\"shell\",\"s2\"]]" "not run" (state:request-id))))
        ((issued $tkt2) (state:ledger "decide" ($ser2 "command" "batch" "{\"commands\":[[\"shell\",\"s2\"]],\"limit\":1}" (state:request-id))))
        ($_ab2 (state:ledger "attempt-begin" ($tkt2 0 "[\"shell\",\"s2\"]" (state:request-id))))
        ($_ar2 (state:ledger "attempt-result" ($tkt2 0 "[\"shell\",\"s2\"]" $step-res (state:request-id))))
        ($_rep2 (state:ledger "report" ($tkt2 "(COMMAND_RETURN: ok)" "returned" "unknown" "abcd" 32 (state:request-id))))
        ($chk-before (state:meta-text "task_window:conversation:mw-owner"))
        ($mid-text (mw:repeat $chunk16k 29 ""))
        ($mid-base (RoleContext coding-messages-empty "skills" "obs" () () $mid-text "inst"))
        ($prep-compact-unknown (context:prepare $mid-base "req-1" "continuation-tick" "" "claude-sonnet-5-5" 32768 "default"))
        ($_ (if (nt:equal $prep-compact-unknown (ContextPreparationFailed "context-window-unknown")) True
               (println! (WINDOW_UNKNOWN_DIFFERS unknown-window-refuses-compaction $prep-compact-unknown (model:observations)))))
        ($_ (nt:require unknown-window-refuses-compaction
              (nt:equal $prep-compact-unknown (ContextPreparationFailed "context-window-unknown"))))
        ($_ (nt:require unknown-window-checkpoint-untouched
              (nt:equal (state:meta-text "task_window:conversation:mw-owner") $chk-before)))
        ($big-text (mw:repeat $chunk16k 40 ""))
        ($big-base (RoleContext coding-messages-empty "skills" "obs" () () $big-text "inst"))
        ($prep-big-unknown (context:prepare $big-base "req-1" "continuation-tick" "" "claude-sonnet-5-5" 32768 "default"))
        ($_ (if (nt:equal $prep-big-unknown (ContextPreparationFailed "context-window-unknown")) True
               (println! (WINDOW_UNKNOWN_DIFFERS unknown-window-oversized-fails-closed $prep-big-unknown (model:observations)))))
        ($_ (nt:require unknown-window-oversized-fails-closed
              (nt:equal $prep-big-unknown (ContextPreparationFailed "context-window-unknown"))))
        ($prep-big-1m (context:prepare $big-base "req-1" "continuation-tick" "967232" "claude-sonnet-5-5" 32768 "default"))
        ($_ (nt:require known-1m-handles-big-base (nt:equal (get-type $prep-big-1m) String)))

        ;; 6e. turn:preparation-failed preserves owned window-unknown failure codes safely
        ;;     and never reflects untrusted text into the health code.
        ($_ (nt:require health-code-submit-error
              (nt:equal (turn:preparation-failed "context-window-unknown: submit-error")
                        ("failed" "context-window-unknown-submit-error" 0 False -1.0 False True))))
        ($_ (nt:require health-code-http-503
              (nt:equal (turn:preparation-failed "context-window-unknown: HTTP 503")
                        ("failed" "context-window-unknown-http-503" 0 False -1.0 False True))))
        ($_ (nt:require health-code-bare-unknown
              (nt:equal (turn:preparation-failed "context-window-unknown")
                        ("failed" "context-window-unknown" 0 False -1.0 False True))))
        ($_ (nt:require health-code-never-reflects-untrusted
              (nt:equal (turn:preparation-failed "context-window-unknown: https://secret.example/token")
                        ("failed" "context-window-unknown" 0 False -1.0 False True)))))
   (println! UNIT_MODEL_WINDOW_REFRESH_OK))
