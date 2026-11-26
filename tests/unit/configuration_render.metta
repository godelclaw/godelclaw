; What the native configuration renderer writes and refuses. It renders an
; instance's environment from config/default.toml and config/local.toml
; (scripts/config_to_env_cetta.metta) and publishes it to .env
; (scripts/render_config_native.sh).
!(import! &self ../native_test_support.metta)
!(import! &self (library list))
!(import! &self (library re))

(= (cr:repo) (nt:required-env "TEST_REPOSITORY"))
(= (cr:root $tag)
   (let* (($root (path:join (nt:required-env "HOME") $tag))
          ($_ (fs:make-directories (path:join $root "config") 448))
          ($_ (fs:write (path:join $root "config/default.toml") (fs:read-text (path:join (cr:repo) "config/default.toml")))))
     $root))
(= (cr:environment $root $extra)
   (list:append (("PATH" (system:getenv-or-default "PATH" "/usr/bin:/bin")) ("HOME" $root) ("USER" "fixture-user")
                 ("XDG_STATE_HOME" (path:join $root "state")) ("METTACLAW_INSTANCE" "fixture-instance")
                 ("LANG" "C.UTF-8"))
                $extra))
(= (cr:render $root $text $extra)
   (let* (($local (path:join $root "config/local.toml")) ($_ (fs:write $local $text)))
     (proc:run ((system:executable) "--no-results" "--lang" "petta" "--profile" "extended" "--import-mode" "relative"
                (path:join (cr:repo) "scripts/config_to_env_cetta.metta"))
               (cr:repo)
               (cr:environment $root (list:append (("METTACLAW_CONFIG_FILE" $local) ("METTACLAW_CONFIG_ROOT" $root)) $extra))
               30000 1048576)))
(= (cr:publish $root $extra)
   (proc:run ("bash" (path:join (cr:repo) "scripts/render_config_native.sh") $root) (cr:repo)
             (cr:environment $root (cons-atom ("CETTA_BIN" (system:executable)) $extra)) 30000 1048576))
(= (cr:code $result) (case $result (((proc:exited $code $_o $_e) $code) ($bad -1))))
(= (cr:out $result) (case $result (((proc:exited $_c $out $_e) $out) ($bad ""))))
(= (cr:err $result) (case $result (((proc:exited $_c $_o $err) $err) ($bad ""))))
(= (cr:ok $result) (if (== (cr:code $result) 0) (cr:out $result) ""))
(= (cr:all $values) (== (filter-atom $values $value (not (== $value True))) ()))
(= (cr:has $text $needles) (cr:all (map-atom $needles $needle (nt:contains $text $needle))))
(= (cr:lacks $text $needles) (cr:all (map-atom $needles $needle (not (nt:contains $text $needle)))))
; Refused: exit 2, nothing on standard output, and no private value echoed.
(= (cr:refused? $result)
   (and (== (cr:code $result) 2) (and (== (cr:out $result) "") (not (nt:contains (cr:err $result) "private-value")))))
; The rendered text without the generator's leading comment lines.
(= (cr:body $text) (str:join "\n" (cr:drop-comments (str:split "\n" $text))))
(= (cr:drop-comments $lines)
   (if (== $lines ()) () (if (str:starts-with (car-atom $lines) "#") (cr:drop-comments (cdr-atom $lines)) $lines)))
(= (cr:leftovers $root)
   (filter-atom (fs:entries $root) $name (str:starts-with $name ".env.native.")))

!(let* (($root (cr:root "render")) ($state (path:join $root "state/fixture-instance"))
         ; Defaults: transport state lives under the instance's state directory.
         ($defaults (cr:ok (cr:render $root "" ())))
         ($_ (nt:require defaults-place-transport-state-outside-the-checkout
               (and (cr:has $defaults
                      ((str:concat "METTACLAW_TELEGRAM_OFFSET_PATH='" (str:concat $state "/telegram/offset'"))
                       (str:concat "METTACLAW_TELEGRAM_LOG_PATH='" (str:concat $state "/telegram/updates.jsonl'"))
                       (str:concat "CETTA_ROOT='" (str:concat $root "/.runtime/cetta'"))
                       "METTACLAW_RECENT_ACTION_TURNS='12'" "METTACLAW_CONVERSATION_EVENTS='64'" "SYNTHETIC_TIMEOUT='120'"
                       "METTACLAW_TELEGRAM_BARE_GROUP_COMMANDS='1'"))
                    (cr:lacks $defaults ("METTACLAW_REPORTS_ROOT" "METTACLAW_PROJECT_REGISTRY_PATH"
                                         (path:join $root "telegram_offset.txt"))))))
         ; Local overrides replace only what they name.
         ($overrides (cr:ok (cr:render $root
                        "[agent]\nsynthetic_model=\"fixture-model\"\nllm_stream=false\n[paths]\nstream_dir=\"$STATE_HOME/streams\"\n[coding]\nproject_root_markers=[\".git\", \"project.json\"]\nproject_doc_fallback_filenames=[\"NOTES.md\", \"é.txt\"]\n" ())))
         ($_ (nt:require local-overrides-keep-the-other-defaults
               (cr:has $overrides
                 ("SYNTHETIC_MODEL='fixture-model'" "SYNTHETIC_TIMEOUT='120'" "METTACLAW_LLM_STREAM='0'"
                  (str:concat "METTACLAW_STREAM_DIR='" (str:concat $root "/state/streams'"))
                  "METTACLAW_CODING_ROOT_MARKERS='[\".git\", \"project.json\"]'"
                  "METTACLAW_CODING_DOC_FALLBACKS='[\"NOTES.md\", \"\\u00e9.txt\"]'"))))
         ; Expansion is one pass: undefined names stay literal, and an expanded
         ; value is never expanded again.
         ($expanded (cr:ok (cr:render $root "[paths]\nrepos_dir=\"$ROOT/${INSTANCE}/$UNDEFINED/${}/$EXTRA/~\"\npublic_work_dir=\"~/public\"\n"
                         (("EXTRA" "$ROOT")))))
         ($_ (nt:require expansion-is-one-pass-and-undefined-names-stay-literal
               (cr:has $expanded
                 ((str:concat "METTACLAW_REPOS_DIR='" (str:concat $root "/fixture-instance/$UNDEFINED/${}/$ROOT/~'"))
                  (str:concat "METTACLAW_PUBLIC_WORK_DIR='" (str:concat $root "/public'"))))))
         ; Quotes, newlines and shell-looking text are data when the file is sourced.
         ($quoted (cr:ok (cr:render $root "[agent]\nsynthetic_model=\"quote ' and \\\" $(touch SHOULD_NOT_EXIST) `false`\\nline\"\n" ())))
         ($_ (fs:write (path:join $root "quoted.env") $quoted))
         ($sourced (proc:run ("bash" "-c" "set -e; . ./quoted.env; printf '%s' \"$SYNTHETIC_MODEL\"") $root
                             (cr:environment $root ()) 10000 65536))
         ($_ (nt:require quoted-text-stays-data-when-sourced
               (and (nt:contains $quoted "'\"'\"'")
                    (and (== (cr:out $sourced) "quote ' and \" $(touch SHOULD_NOT_EXIST) `false`\nline")
                         (not (fs:exists (path:join $root "SHOULD_NOT_EXIST")))))))
         ; Identities never enter the environment; the private-chat switch does.
         ($identities (cr:ok (cr:render $root
                         "[channel]\nkind = \"telegram\"\n[telegram]\nallow_private = true\noperator_ids = [\"700001\"]\nlight_arm_ids = [\"700002\"]\nsender_names = [\"700001:private-name\"]\nallowed_chat_ids = [\"-700003\"]\n" ())))
         ($_ (nt:require sender-identities-never-enter-the-environment
               (and (nt:contains $identities "METTACLAW_TELEGRAM_ALLOW_PRIVATE")
                    (cr:lacks $identities ("700001" "700002" "700003" "private-name" "METTACLAW_TELEGRAM_OPERATOR_IDS"
                                           "METTACLAW_TELEGRAM_LIGHT_ARM_IDS" "METTACLAW_TELEGRAM_SENDER_NAMES"
                                           "METTACLAW_TELEGRAM_ALLOWED_CHAT_IDS")))))
         ; Invalid values are refused whole: no partial environment, no private value.
         ($_ (nt:require invalid-values-are-refused-without-output-or-private-values
               (cr:all (map-atom ("llm_timeout_seconds=0" "llm_timeout_seconds=-1" "llm_timeout_seconds=nan"
                                  "llm_timeout_seconds=inf" "llm_timeout_seconds=true" "llm_timeout_seconds=\"private-value\""
                                  "llm_hard_seconds=1" "llm_connect_seconds=0" "input_ack_seconds=-1" "input_ack_seconds=nan"
                                  "input_ack_seconds=inf" "input_ack_seconds=true" "input_ack_seconds=\"private-value\""
                                  "llm_stream=\"private-value\"" "model_commentary=1" "model_commentary=\"yes\""
                                  "public_tool_receipts=\"private-value\"" "public_tool_receipts=1"
                                  "recycle_bytes=0.5" "recycle_bytes=-1") $line
                         (cr:refused? (cr:render $root (str:concat "[agent]\n" (str:concat $line "\n")) ()))))))
         ($_ (nt:require a-zero-receipt-delay-is-valid
               (nt:contains (cr:ok (cr:render $root "[agent]\ninput_ack_seconds = 0\n" ())) "METTACLAW_INPUT_ACK_SECONDS='0'")))
         ($_ (nt:require display-switches-render-as-one-and-zero
               (cr:all (map-atom (("model_commentary" "METTACLAW_MODEL_COMMENTARY") ("public_tool_receipts" "METTACLAW_PUBLIC_TOOL_RECEIPTS")) $pair
                         (let ($setting $key) $pair
                           (and (nt:contains (cr:ok (cr:render $root (str:concat "[agent]\n" (str:concat $setting " = true\n")) ()))
                                             (str:concat $key "='1'"))
                                (nt:contains (cr:ok (cr:render $root (str:concat "[agent]\n" (str:concat $setting " = false\n")) ()))
                                             (str:concat $key "='0'"))))))))
         ($_ (nt:require names-paths-and-cache-lifetimes-are-validated
               (cr:all (map-atom ("[coding]\nproject_root_markers=[\"..\"]\n" "[coding]\nproject_doc_fallback_filenames=[\"a/b\"]\n"
                                  "[paths]\ncoding_guidance_dir=false\n" "[context]\nanthropic_cache_ttl=\"private-value\"\n") $text
                         (cr:refused? (cr:render $root $text ()))))))
         ($_ (nt:require unused-nonfinite-values-do-not-break-configuration
               (== (cr:code (cr:render $root "[unused]\nmeasurement=nan\n" ())) 0)))
         ($_ (nt:require dates-and-times-render-as-text
               (and (nt:contains (cr:ok (cr:render $root "[agent]\nsynthetic_model=2026-11-12\n" ())) "SYNTHETIC_MODEL='2026-11-12'")
                    (and (nt:contains (cr:ok (cr:render $root "[agent]\nsynthetic_model=01:02:03.123456\n" ())) "SYNTHETIC_MODEL='01:02:03.123456'")
                         (nt:contains (cr:ok (cr:render $root "[agent]\nsynthetic_model=2026-11-12T01:02:03Z\n" ()))
                                      "SYNTHETIC_MODEL='2026-11-12 01:02:03+00:00'")))))
         ; Without default.toml, the renderer's own fallbacks still apply.
         ($bare (path:join $root "bare")) ($_ (fs:make-directories (path:join $bare "config") 448))
         ($fallback (cr:ok (cr:render $bare "[agent]\ninput_ack_seconds=0\n" ())))
         ($_ (nt:require missing-defaults-keep-the-explicit-fallbacks
               (cr:has $fallback ("METTACLAW_INPUT_ACK_SECONDS='0'" "METTACLAW_LLM_HARD_SECONDS='1800'"
                                  "METTACLAW_SHELL_TIMEOUT_SECONDS='120'" "METTACLAW_INPUT_POLICY='cooperative'"
                                  (str:concat "METTACLAW_CODING_GUIDANCE_DIR='" (str:concat $bare "/identity'"))))))
         ; Channel templates name the channel module and nothing else.
         ($_ (nt:require channel-templates-name-the-channel-module
               (cr:all (map-atom ("telegram" "irc" "mattermost") $kind
                 (== (cr:out (proc:run ("bash" (path:join (cr:repo) "scripts/render_config_native.sh") $root "channel" $kind)
                                       (cr:repo) (cr:environment $root (("CETTA_BIN" (system:executable)))) 30000 65536))
                     (str:concat ";; Generated from config/local.toml [channel] kind. Do not edit.\n(= (configureChannel) (configure commchannel "
                       (str:concat $kind "))\n")))))))
         ($_ (nt:require an-unknown-channel-kind-is-refused
               (cr:refused? (proc:run ("bash" (path:join (cr:repo) "scripts/render_config_native.sh") $root "channel" "untrusted) syntax")
                                      (cr:repo) (cr:environment $root (("CETTA_BIN" (system:executable)))) 30000 65536))))
         ; Publication is private, atomic, and keeps secrets in their own file.
         ($pub (cr:root "publish"))
         ($_ (fs:write (path:join $pub "config/local.toml") "[agent]\nsynthetic_model=\"fixture-model\"\n"))
         ($_ (fs:write (path:join $pub "config/secrets.env") "PRIVATE_TOKEN=fixture-secret\n"))
         ($published (cr:publish $pub ()))
         ($env (path:join $pub ".env"))
         ($_ (nt:require publication-is-private-atomic-and-keeps-secrets-apart
               (and (== (cr:code $published) 0)
                    (and (== (cr:body (fs:read-text $env)) (cr:body (cr:ok (cr:render $pub "[agent]\nsynthetic_model=\"fixture-model\"\n" ()))))
                         (and (== (str:strip-python (nt:exit-ok (proc:run ("stat" "-c" "%a" $env) $pub inherit 5000 64))) "600")
                              (and (not (nt:contains (fs:read-text $env) "fixture-secret"))
                                   (and (== (fs:read-text (path:join $pub "config/secrets.env")) "PRIVATE_TOKEN=fixture-secret\n")
                                        (== (cr:leftovers $pub) ()))))))))
         ($_ (fs:write (path:join $pub "config/local.toml") "[agent]\nllm_timeout_seconds=-1\n"))
         ($_ (fs:write $env "previous complete environment\n"))
         ($_ (nt:require failed-validation-keeps-the-published-file
               (and (== (cr:code (cr:publish $pub ())) 2)
                    (and (== (fs:read-text $env) "previous complete environment\n") (== (cr:leftovers $pub) ())))))
         ($_ (fs:write (path:join $pub "config/local.toml") ""))
         ($_ (nt:require publication-reports-a-replaced-caller-setting
               (let* (($changed (cr:publish $pub (("METTACLAW_REASONING_EFFORT" "high"))))
                      ($same (cr:publish $pub (("METTACLAW_REASONING_EFFORT" "default")))))
                 (and (and (== (cr:code $changed) 0) (nt:contains (cr:err $changed) "METTACLAW_REASONING_EFFORT"))
                      (and (== (cr:code $same) 0) (not (nt:contains (cr:err $same) "METTACLAW_REASONING_EFFORT")))))))
         ; Concurrent publishers finish cleanly with no Python on the path.
         ($_ (fs:write (path:join $pub "config/local.toml") "[agent]\nsynthetic_model=\"fixture-model\"\n"))
         ($shims (path:join $pub "bin")) ($_ (fs:make-directories $shims 448))
         ($_ (map-atom ("python" "python3") $name
               (let $shim (path:join $shims $name)
                 (progn (fs:write $shim "#!/bin/sh\necho forbidden-python >&2\nexit 99\n")
                        (nt:exit-ok (proc:run ("chmod" "755" $shim) $pub inherit 5000 64))))))
         ($path (str:concat $shims (str:concat ":" (system:getenv-or-default "PATH" "/usr/bin:/bin"))))
         ($environment (cons-atom ("PATH" $path) (filter-atom (cr:environment $pub (("CETTA_BIN" (system:executable)))) $pair
                         (not (== (car-atom $pair) "PATH")))))
         ($children (map-atom (1 2 3 4) $n
                      (proc:spawn ("bash" (path:join (cr:repo) "scripts/render_config_native.sh") $pub)
                                  ((cwd (cr:repo)) (env $environment) (mode stream) (max-bytes 65536) (timeout-ms 30000) (lifetime owner)))))
         ($results (map-atom $children $child
                     (let* (($_ (proc:close-input $child)) ($result (proc:await $child)) ($_ (proc:close $child))) $result)))
         ($_ (nt:require concurrent-publishers-need-no-python
               (and (cr:all (map-atom $results $result
                      (case $result
                        (((proc:result (proc:exited 0) proc:normal $out $err $_) (not (nt:contains (str:concat $out $err) "forbidden-python")))
                         ($bad False)))))
                    (and (== (cr:leftovers $pub) ())
                         (nt:contains (fs:read-text $env) "SYNTHETIC_MODEL='fixture-model'"))))))
   (println! UNIT_CONFIGURATION_RENDER_OK))
