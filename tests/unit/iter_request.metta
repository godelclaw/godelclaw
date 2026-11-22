!(import! &self ../native_test_support.metta)
!(import! &self ../../modes/iter/core.metta)
!(import! &self ../../src/provider_request_cetta.metta)
!(import! &self (library llm))
; Iter's provider request meets the native request contract that every
; provider path, the agents' ChatGPT models among them, checks before sending.
!(let* (($message (JsonObject ((JsonPair "role" "user") (JsonPair "content" "unit"))))
        ($body (ih:request-body "chatgpt:unit-model" (JsonArray ($message)) (JsonArray ())))
        ; As llm:structured-plan hands it to the provider path: tagged JSON.
        ($tagged (preq:tag $body))
        ($_ (nt:require iter-request-fields-admitted (== (llm:v1:request-fields $tagged) True)))
        ($_ (nt:require iter-request-valid (== (llm:v1:validate-request $tagged) True)))
        ($_ (nt:require iter-reasoning-is-an-effort
              (== (sf:get (sf:get $body "reasoning" JsonNull) "effort" "") "medium")))
        ($_ (nt:require iter-default-max-tokens-5000
              (== (sf:get $body "max_tokens" 0) 5000)))
        ($_ (nt:require iter-default-tool-choice-required
              (== (sf:get $body "tool_choice" "") "required")))
        ; Malformed JSON tool arguments must fail decoding rather than falling through.
        ($bad-json (ih:decode "{"))
        ($_ (nt:require bad-json-decode-fails (not (ih:flag $bad-json "ok"))))
        ($_ (nt:require bad-json-decode-message (str:starts-with (ih:get $bad-json "error") "Invalid tool arguments from model")))
        ($good-obj (ih:decode "{\"a\":1}"))
        ($_ (nt:require good-json-decode-ok (and (ih:flag $good-obj "ok") (ih:flag $good-obj "object"))))
        ($good-arr (ih:decode "[]"))
        ($_ (nt:require array-json-not-object (and (ih:flag $good-arr "ok") (not (ih:flag $good-arr "object")))))
        ; LLM_BACKUP_MODEL is strictly opt-in (off by default when unset).
        ($_ (nt:require backup-model-off-by-default (== (ih:backup-model "openai:primary") "")))
        ; Refusal classification matches upstream utils.py:is_refusal.
        ($ref-fin (ih:box (JsonObject ((JsonPair "finish" "refusal") (JsonPair "message" (JsonObject ((JsonPair "content" "no"))))))))
        ($ref-cf (ih:box (JsonObject ((JsonPair "finish" "content_filter") (JsonPair "message" (JsonObject ((JsonPair "content" "no"))))))))
        ($ref-fld (ih:box (JsonObject ((JsonPair "finish" "stop") (JsonPair "message" (JsonObject ((JsonPair "refusal" "policy") (JsonPair "content" "hi"))))))))
        ($ref-empty (ih:box (JsonObject ((JsonPair "finish" "stop") (JsonPair "message" (JsonObject ((JsonPair "content" ""))))))))
        ($ref-null (ih:box (JsonObject ((JsonPair "finish" "stop") (JsonPair "message" JsonNull)))))
        ($ok-call (ih:box (JsonObject ((JsonPair "finish" "tool_calls") (JsonPair "message" (JsonObject ((JsonPair "tool_calls" (JsonArray ((JsonObject ((JsonPair "id" "1")))))))))))))
        ($ok-txt (ih:box (JsonObject ((JsonPair "finish" "stop") (JsonPair "message" (JsonObject ((JsonPair "content" "hello"))))))))
        ($_ (nt:require refusal-on-finish (ih:reply-refusal? $ref-fin)))
        ($_ (nt:require refusal-on-content-filter (ih:reply-refusal? $ref-cf)))
        ($_ (nt:require refusal-on-message-field (ih:reply-refusal? $ref-fld)))
        ($_ (nt:require refusal-on-empty-message (ih:reply-refusal? $ref-empty)))
        ($_ (nt:require refusal-on-null-message (ih:reply-refusal? $ref-null)))
        ($_ (nt:require non-refusal-on-tool-call (not (ih:reply-refusal? $ok-call))))
        ($_ (nt:require non-refusal-on-text (not (ih:reply-refusal? $ok-txt)))))
   (println! UNIT_ITER_REQUEST_OK))
; A tool that fails, by raising or by returning an Error, is the step's output.
!(let* (($none (ih:object))
        ($failed (iter:tool-run $none "query" (ih:object)))
        ($_ (nt:require failing-tool-is-its-output (str:starts-with $failed "Tool execution failed: ")))
        ($fault (iter:tool-outcome ((Error component-call (Fault "deliberate failure")))))
        ($_ (nt:require fault-is-a-failure (str:starts-with $fault "Tool execution failed: ")))
        ($_ (nt:require failure-is-bounded (<= (str:char-length $fault) 700)))
        ($uncertain (iter:tool-outcome ((Error component-call (Incomplete (worker-result-unknown died))))))
        ($_ (nt:require incomplete-is-uncertain (str:starts-with $uncertain "Tool outcome uncertain: ")))
        ($ordinary (iter:tool-outcome ((MemoryUnavailable embedding endpoint-not-configured))))
        ($_ (nt:require ordinary-value-kept (== $ordinary "(MemoryUnavailable embedding endpoint-not-configured)")))
        ($_ (nt:require text-kept (== (iter:tool-outcome ("SUCCESS")) "SUCCESS")))
        ($_ (nt:require nothing-is-a-failure (== (iter:tool-outcome ()) "Tool execution failed: no result")))
        ($_ (nt:require backoff-schedule
              (== (map-atom (1 3 4 5 8 9 10 20) $n (iter:failure-wait $n)) (1 1 15 30 240 480 600 600)))))
   (println! UNIT_ITER_TOOL_FAILURE_OK))

; A refusal is how the provider call reports a request that would be refused
; again (host-native:structured-failed); a failure that may pass later is not.
(= (iter-unit:type $cause $kind)
   (case (failure:of $cause $kind 0 -1) (((CallFailureV1 $code $type $status $transient $retry) $type))))
(= (iter-unit:reported $cause $kind) (host-native:structured-failed 0 (iter-unit:type $cause $kind)))
!(let* (($_ (nt:require provider-rejection-refuses (ih:refusal? (host-native:structured-failed 400 "RuntimeError"))))
        ($_ (nt:require contract-refusal-refuses
              (ih:refusal? (iter-unit:reported (CallPreparation "unsupported-request-field") admission-error))))
        ($_ (nt:require seed-refusal-refuses (ih:refusal? (iter-unit:reported (CallError "ValueError") admission-error))))
        ($_ (nt:require lost-receipt-refuses (ih:refusal? (iter-unit:reported CallConflict local-completion-conflict))))
        ($_ (nt:require rate-limit-may-pass (not (ih:refusal? (host-native:structured-failed 429 "RuntimeError")))))
        ($_ (nt:require timeout-status-may-pass (not (ih:refusal? (host-native:structured-failed 408 "RuntimeError")))))
        ($_ (nt:require server-error-may-pass (not (ih:refusal? (host-native:structured-failed 503 "RuntimeError")))))
        ($_ (nt:require timeout-may-pass (not (ih:refusal? (iter-unit:reported (CallTransport "timeout" 0) transport-error)))))
        ($_ (nt:require denial-may-pass (not (ih:refusal? (iter-unit:reported (CallDenied "provider-cooldown" 5) admission-error)))))
        ($_ (nt:require submission-limit-may-pass (not (ih:refusal? (iter-unit:reported none submission-limit)))))
        ($_ (nt:require unavailable-session-may-pass (not (ih:refusal? (Error llm:structured-plan no-session)))))
        ; A failed call ends the step; only a refusal is kept, by its request's key.
        ($refused (ih:provider-failed ((Error provider (Fault "native provider rejected the request: HTTP 400"))) "key-1"))
        ($_ (nt:require refusal-ends-step (turn:failed? $refused)))
        ($_ (nt:require refusal-kept (== (get-state &iter-refused) "key-1")))
        ($_ (nt:require refusal-reported (nt:contains (diagnostic:text $refused 600) "not sent again unchanged")))
        ($passing (ih:provider-failed ((Error provider (Fault "native provider failed: TimeoutError"))) "key-2"))
        ($_ (nt:require transient-ends-step (turn:failed? $passing)))
        ($_ (nt:require transient-not-kept (== (get-state &iter-refused) "key-1")))
        ($_ (nt:require no-result-ends-step (turn:failed? (ih:provider-failed () "key-3")))))
   (println! UNIT_ITER_REFUSAL_OK))

(= (iter-unit:chmod $mode $path)
   (proc:run ("/bin/chmod" $mode $path) (system:cwd) (("PATH" "/usr/bin:/bin")) 5000 1024))
; An operator's submission limit (submission-limit.json beside the health
; record): each submission of its purpose takes one, a call finding none left
; is refused, and a limit that cannot be read refuses.
!(let $health (system:getenv-or-default "METTACLAW_COGNITIVE_HEALTH_PATH" "")
   (if (== $health "") (println! UNIT_SUBMISSION_LIMIT_SKIPPED)
     (let* (($limit (path:join (path:dirname $health) "submission-limit.json"))
            ($coding (HostCallV1 (JsonObject ((JsonPair "purpose" "coding")))))
            ($turn (HostCallV1 (JsonObject ((JsonPair "purpose" "turn")))))
            ($_ (nt:require no-limit-allows (llm:submission-allowed $coding)))
            ($_ (fs:write $limit "{\"remaining\": 2, \"purpose\": \"coding\"}"))
            ($_ (nt:require other-purpose-not-counted (llm:submission-allowed $turn)))
            ($_ (nt:require first-allowed (llm:submission-allowed $coding)))
            ($_ (nt:require second-allowed (llm:submission-allowed $coding)))
            ($_ (nt:require third-refused (not (llm:submission-allowed $coding))))
            ($_ (nt:require count-spent (== (sf:get (sf:native-object $limit) "remaining" -1) 0)))
            ($_ (fs:write $limit "{\"remaining\": 1}"))
            ($_ (nt:require every-purpose-counted (llm:submission-allowed $turn)))
            ($_ (nt:require every-purpose-refused (not (llm:submission-allowed $coding))))
            ($_ (fs:write $limit "not a limit"))
            ($_ (nt:require unreadable-limit-refuses (not (llm:submission-allowed $coding))))
            ($_ (fs:write $limit "{\"remaining\": \"3\"}"))
            ($_ (nt:require non-count-refuses (not (llm:submission-allowed $coding))))
            ($_ (fs:write $limit "{\"remaining\": 3, \"purpose\": null}"))
            ($_ (nt:require non-purpose-refuses (not (llm:submission-allowed $coding))))
            ($_ (fs:write $limit "{\"remaining\": 1.5}"))
            ($_ (nt:require fractional-count-refuses (not (llm:submission-allowed $coding))))
            ($_ (fs:write $limit "{\"remaining\": -1}"))
            ($_ (nt:require negative-count-refuses (not (llm:submission-allowed $coding))))
            ($_ (fs:remove $limit))
            ($_ (nt:require removed-limit-allows (llm:submission-allowed $coding)))
            ; Only a limit known to be missing is no limit: a lookup or a lock
            ; that fails refuses, and the count stays.
            ($home (path:dirname $health))
            ($_ (nt:require missing-directory-allows (llm:submission-allowed-in $coding (path:join $home "nowhere"))))
            ($file (path:join $home "a-file")) ($_ (fs:write $file "not a directory"))
            ($_ (nt:require path-through-a-file-allows (llm:submission-allowed-in $coding $file)))
            ($denied (path:join $home "denied")) ($_ (fs:make-directories $denied 448))
            ($_ (fs:write (path:join $denied "submission-limit.json") "{\"remaining\": 5}"))
            ($_ (iter-unit:chmod "000" $denied))
            ($blind (llm:submission-allowed-in $coding $denied))
            ($_ (iter-unit:chmod "700" $denied))
            ($_ (nt:require failed-lookup-refuses (not $blind)))
            ($unlockable (path:join $home "unlockable"))
            ($_ (fs:make-directories (path:join $unlockable "submission-limit.json.lock") 448))
            ($_ (fs:write (path:join $unlockable "submission-limit.json") "{\"remaining\": 5}"))
            ($_ (nt:require failed-lock-refuses (not (llm:submission-allowed-in $coding $unlockable))))
            ($_ (nt:require failed-lock-keeps-count
                  (== (sf:get (sf:native-object (path:join $unlockable "submission-limit.json")) "remaining" -1) 5))))
       (println! UNIT_SUBMISSION_LIMIT_OK))))

; Real skills/ and transformations/ directories under MAX_SKILLS and
; MAX_CONTEXT_TRANSFORMATIONS from the environment: verifies ih:skills,
; ih:skill-limit, ih:transformations, ih:transform-limit, and the resulting
; request body (including non-execution of omitted transformations).
!(let $case-dir (system:getenv-or-default "ITER_SKILLS_CASE" "")
   (if (== $case-dir "") (println! UNIT_ITER_SKILLS_LIMIT_SKIPPED)
     (let* (($root (system:getcwd))
            ($engine (nt:required-env "NATIVE_CORE_BIN"))
            ($_ (fs:make-directories (path:join $case-dir "memory") 448))
            ($_ (fs:make-directories (path:join $case-dir "tools") 448))
            ($_ (fs:make-directories (path:join $case-dir "transformations") 448))
            ($_ (fs:make-directories (path:join $case-dir "skills/sub") 448))
            ($_ (fs:make-directories (path:join $case-dir "skills/_hidden") 448))
            ($_ (fs:write (path:join $case-dir "prompt.txt") "Skills limit unit prompt.\n"))
            ($_ (fs:write (path:join $case-dir "skills/01-alpha.md") "DESCRIPTION=\"Alpha \\\"quoted\\\" skill\"\nBody\n"))
            ($_ (fs:write (path:join $case-dir "skills/02-missing.md") "# Missing first-line description\n"))
            ($_ (fs:write (path:join $case-dir "skills/sub/03-omitted.md") "DESCRIPTION=\"Third omitted skill\"\n"))
            ($_ (fs:write (path:join $case-dir "skills/_skip.md") "DESCRIPTION=\"Hidden file\"\n"))
            ($_ (fs:write (path:join $case-dir "skills/_hidden/secret.md") "DESCRIPTION=\"Hidden dir\"\n"))
            ($_ (fs:write (path:join $case-dir "skills/notes.txt") "DESCRIPTION=\"Not markdown\"\n"))
            ($_ (fs:write (path:join $case-dir "transformations/01-run.metta")
                  "(ComponentDeclarationV1 \"First transform\" ())\n(= (component:transform-request (JsonArray ((JsonArray $m) $t))) (JsonArray ((JsonArray (list:append $m ((JsonObject ((JsonPair \"role\" \"user\") (JsonPair \"content\" \"RAN-01\")))))) $t)))\n"))
            ($_ (fs:write (path:join $case-dir "transformations/02-omitted.metta")
                  "(ComponentDeclarationV1 \"Second transform\" ())\n(= (component:transform-request (JsonArray ((JsonArray $m) $t))) (JsonArray ((JsonArray (list:append $m ((JsonObject ((JsonPair \"role\" \"user\") (JsonPair \"content\" \"MUST-NOT-RUN-02\")))))) $t)))\n"))
            ($probe (path:join $case-dir "probe.metta"))
            ($_ (fs:write $probe (str:join "" (
                  "!(import! &self \"" (path:join $root "tests/native_test_support.metta") "\")\n"
                  "!(import! &self \"" (path:join $root "modes/iter/core.metta") "\")\n"
                  "!(let* (($skills (ih:skills))\n"
                  "        ($s-omitted (ih:get $skills \"omitted\"))\n"
                  "        ($s-surface (ih:get $skills \"surface\"))\n"
                  "        ($s-notice (ih:skill-limit $s-omitted))\n"
                  "        ($transforms (ih:transformations))\n"
                  "        ($t-omitted (ih:get $transforms \"omitted\"))\n"
                  "        ($t-notice (ih:transform-limit $t-omitted))\n"
                  "        ($_ (nt:require env-max-skills-omitted (== $s-omitted 1)))\n"
                  "        ($_ (nt:require env-skill-limit-text (== $s-notice \"[SKILL LIMIT REACHED: 1 Markdown skills are omitted. Consolidate or remove skills if they are needed.]\")))\n"
                  "        ($_ (nt:require env-skill-alpha-listed (nt:contains $s-surface \"./skills/01-alpha.md: DESCRIPTION=\\\"Alpha \\\\\\\"quoted\\\\\\\" skill\\\"\")))\n"
                  "        ($_ (nt:require env-skill-missing-sentinel (nt:contains $s-surface \"./skills/02-missing.md: [DESCRIPTION FIRST LINE MISSING]\")))\n"
                  "        ($_ (nt:require env-skill-third-omitted (not (nt:contains $s-surface \"03-omitted.md\"))))\n"
                  "        ($_ (nt:require env-skill-hidden-excluded (and (not (nt:contains $s-surface \"_skip.md\")) (not (nt:contains $s-surface \"_hidden\")))))\n"
                  "        ($_ (nt:require env-skill-non-md-excluded (not (nt:contains $s-surface \"notes.txt\"))))\n"
                  "        ($_ (nt:require env-max-transforms-omitted (== $t-omitted 1)))\n"
                  "        ($_ (nt:require env-transform-limit-text (== $t-notice \"[CONTEXT TRANSFORMATION LIMIT REACHED: 1 transformations are omitted and will not execute. Consolidate or remove transformations if they are needed.]\")))\n"
                  "        ($temp (ih:empty-list))\n"
                  "        ($_ (ih:append $temp (ih:message \"user\" $t-notice)))\n"
                  "        ($_ (ih:append $temp (ih:message \"user\" $s-notice)))\n"
                  "        ((IterRequestV1 $body $_ $_ $_) (ih:request-plan-full (ih:list) $temp (ih:tools) $transforms $s-surface \"[3000 CHARACTERS BELOW MAXIMUM]\\n./memory/:\\n\"))\n"
                  "        ($wire (turn:json $body))\n"
                  "        ($_ (nt:require wire-carries-skills-and-omitted-notice (and (nt:contains $wire \"./skills/01-alpha.md\") (nt:contains $wire \"SKILL LIMIT REACHED: 1 Markdown skills are omitted\"))))\n"
                  "        ($_ (nt:require wire-ran-first-transform-only (and (nt:contains $wire \"RAN-01\") (not (nt:contains $wire \"MUST-NOT-RUN-02\"))))))\n"
                  "   (println! CHILD_ITER_SKILLS_LIMIT_OK))\n"))))
            ($env (list:append (nt:environment $case-dir)
                    (("METTACLAW_RUNTIME_ROOT" $root) ("METTACLAW_MODE_RUNTIME" $case-dir)
                     ("METTACLAW_MODE_PROFILE" "standalone") ("METTACLAW_ACTIVE_LOOP_MODE" "iter")
                     ("METTACLAW_TOOL_OUTPUT_DIR" (path:join $case-dir "artifacts"))
                     ("MAX_SKILLS" "2") ("MAX_CONTEXT_TRANSFORMATIONS" "1"))))
            ($output (nt:exit-ok (proc:run ($engine "--lang" "petta" "--profile" "extended" "--import-mode" "ancestor-walk" "--quiet" $probe)
                                           $case-dir $env 30000 1048576)))
            ($_ (nt:require iter-skills-limit-child (nt:contains $output "CHILD_ITER_SKILLS_LIMIT_OK"))))
       (println! UNIT_ITER_SKILLS_LIMIT_OK))))
