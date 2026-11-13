; Unit tests of src/model_menu_cetta.metta; no Python, no provider. Two
; environments: "keyed" has the Anthropic and Kimi Code credentials, a
; selected ChatGPT account and a closed Synthetic port; "unkeyed" has none,
; and its model state file is in a directory that does not exist.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/model_menu_cetta.metta)

(= (mm:now) (system:wall-time))
(= (mm:write $path $text) (let $_ (fs:make-directories (path:dirname $path) 448) (fs:write $path $text)))
(= (mm:observations $text) (mm:write (model:observation-path) $text))
(= (mm:stamped $kind $age $value)
   (let $at (json:stringify-python (- (mm:now) $age))
     (str:join "" ("\"" $kind "\": {\"observed_at\": " $at ", \"attempted_at\": " $at ", \"value\": " $value "}"))))
; A record attempted just now and never observed: no refresh is due.
(= (mm:attempted $kind)
   (str:join "" ("\"" $kind "\": {\"attempted_at\": " (json:stringify-python (mm:now)) "}")))
(= (mm:keyed?) (model:env-set? "ANTHROPIC_API_KEY"))

;; ------------------------------------------------ pure parts, in both

!(nt:require valid-ids
   (nt:equal (map-atom ("syn:large:text" "hf:org/model-1.5+x@v" "a" "" " x" "x y" "-x" "λ")
                       $n (model:valid-id? $n))
             (True True True False False False False False)))
!(nt:require long-id-invalid (nt:equal (model:valid-id? (str:join "" (map-atom (1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21) $i "0123456789"))) False))
!(nt:require python-strings
   (nt:equal (map-atom ("text" 7 1.5 JsonNull True False) $v (model:python-str $v)) ("text" "7" "1.5" "None" "True" "False")))
!(nt:require truth
   (nt:equal (map-atom (JsonNull "" 0 False (JsonArray ()) (JsonObject ()) "x" 3 True) $v (model:truthy? $v))
             (False False False False False False True True True)))
!(nt:require tokens (nt:equal (map-atom (0 999 1000 1999 999999 1000000 1250000 "x") $v (model:tokens $v))
                              ("0" "999" "1k" "1k" "999k" "1.0M" "1.2M" "0")))
!(nt:require percents (nt:equal (map-atom (37.45 37.46 100 "12.34" "abc" JsonNull True) $v (model:percent $v))
                                ("37.5" "37.5" "100.0" "12.3" "?" "?" "1.0")))
!(nt:require lines (nt:equal (map-atom ("" "a" "a\n" "a\r\nb\n" "a\n\nb") $t (model:lines $t))
                             (() ("a") ("a") ("a" "b") ("a" "" "b"))))
!(nt:require other-lines
   (nt:equal (model:other-lines ("(pin a)" "  (active-model old)" "(active-modelx)" "x (active-model mid)" "(active-model\tz)"))
             ("(pin a)" "(active-modelx)" "x (active-model mid)")))
!(nt:require replace-refused (nt:equal (model:replace "/nonexistent-godelclaw-directory/state" "x") False))
; A catalog keeps each id once, trimmed, with its window; rows that are no
; objects or have no id are dropped; Kimi Code's ids carry kimi: once.
!(let $payload (json:parse-python "{\"data\": [{\"id\": \" m1 \", \"context_length\": 100}, {\"id\": \"m1\", \"context_length\": 5}, {\"id\": \"\"}, 7, {\"id\": \"m2\", \"context_length\": 0, \"context_window\": 50}, {\"id\": \"kimi:m3\"}, {\"name\": \"no id\"}]}")
   (let* (($_ (nt:require catalog-rows
                (nt:equal (json:stringify-python (model:catalog $payload ""))
                          "[{\"id\": \"m1\", \"context\": 100}, {\"id\": \"m2\", \"context\": 50}, {\"id\": \"kimi:m3\", \"context\": null}]"))))
     (nt:require kimi-prefixed-once
       (nt:equal (json:stringify-python (model:catalog $payload "kimi:"))
                 "[{\"id\": \"kimi:m1\", \"context\": 100}, {\"id\": \"kimi:m2\", \"context\": 50}, {\"id\": \"kimi:m3\", \"context\": null}]"))))
!(nt:require catalog-without-list (nt:equal (model:catalog (json:parse-python "{\"data\": {\"id\": \"x\"}}") "") (JsonArray ())))
!(nt:require quota-kept-fields
   (nt:equal (json:stringify-python-with
               (model:quota-value (json:parse-python "{\"planWeeklyLimit\": {\"percentRemaining\": 40, \"secret\": 1}, \"rollingFiveHourLimit\": {\"remaining\": 3}, \"weeklyTokenLimit\": 9, \"other\": 2}"))
               ((sort-keys True)))
             "{\"planWeeklyLimit\": {\"nextRegenAt\": null, \"nextRegenPercent\": null, \"percentRemaining\": 40}, \"rollingFiveHourLimit\": {\"limited\": null, \"max\": null, \"remaining\": 3}}"))
!(nt:require provider-replies
   (nt:equal (map-atom ((http:response 401 "{}") (http:response 200 "{\"error\": \"rate limited\"}") (http:response 200 "[1]")
                        (http:response 200 "{\"x\"") (NativeHttpFailureV1 transport 7)
                        (http:response-with-metadata 503 "" (http:retry-after 3)))
                       $r (model:received $r))
             ((ModelObservationFailed "HTTP 401") (ModelObservationFailed "rate limited") (ModelObservationFailed "unexpected response")
              (ModelObservationFailed "bad response") (ModelObservationFailed "transport error") (ModelObservationFailed "HTTP 503"))))
!(nt:require provider-object-kept (nt:equal (model:received (http:response 200 "{\"data\": []}")) (JsonObject ((JsonPair "data" (JsonArray ()))))))
; Due: stale (never observed, or older than its time to live) and not
; attempted within the retry interval.
!(let $now 10000.0
   (nt:require refresh-due
     (nt:equal (map-atom (((JsonObject ()) "models")
                          ((JsonObject ((JsonPair "observed_at" 9701.0))) "models")
                          ((JsonObject ((JsonPair "observed_at" 9700.0))) "models")
                          ((JsonObject ((JsonPair "observed_at" 9950.0))) "quota")
                          ((JsonObject ((JsonPair "observed_at" 9930.0))) "quota")
                          ((JsonObject ((JsonPair "observed_at" 9000.0) (JsonPair "attempted_at" 9971.0))) "models")
                          ((JsonObject ((JsonPair "attempted_at" 9970.0))) "quota"))
                         $case (let ($record $kind) $case (model:refresh-due? $record $kind $now)))
               (True False True False True False True))))

;; ------------------------------------------------ keyed

(= (mm:keyed)
   (let* (($_ (mm:write (system:getenv-or-default "METTACLAW_CHATGPT_POLICY_PATH" "") "{\"account\": \"unit-account\"}"))
          ($_ (mm:observations (str:join "" ("{\"schema\": 1, "
                 (mm:stamped "models" 100 "[{\"id\": \"syn-a\", \"context\": 128000}, {\"id\": \"syn-b\", \"context\": 0}, {\"id\": \"syn-a\"}, 3]") ", "
                 (mm:stamped "kimi-models" 7 "[{\"id\": \"kimi:k1\", \"context\": 256000}]") ", "
                 (mm:stamped "quota" 1 "{\"planWeeklyLimit\": {\"percentRemaining\": 37.46}, \"weeklyTokenLimit\": {\"remainingCredits\": 5}, \"rollingFiveHourLimit\": {\"remaining\": 12, \"max\": 40, \"limited\": true}}")
                 "}"))))
          ((ModelMenuV1 $ids $current $unavailable $status) (model:menu-state))
          ($_ (nt:require menu-ids
                (nt:equal $ids ("syn-a" "syn-b" "kimi:k1" "chatgpt:gpt-5.6-sol" "chatgpt:gpt-6-sol" "chatgpt:gpt-6.1-sol"
                                "chatgpt:gpt-6-astra" "unit-configured-model" "claude-unit-a" "claude-unit-b"))))
          ($_ (nt:require menu-current (nt:equal $current "unit-configured-model")))
          ; Pinned subscription choices are not observed, so not unavailable.
          ($_ (nt:require menu-unavailable (nt:equal $unavailable ("unit-configured-model"))))
          ($_ (nt:require menu-status
                (model:member? $status
                  (map-atom (100 101 102) $a
                    (str:join "" ("Synthetic catalog observed " (repr $a) "s ago; Kimi Code catalog observed " (repr (- $a 93))
                                  "s ago; pinned ChatGPT subscription choices (availability unverified)"))))))
          ($_ (nt:require models-listing
                (nt:equal (model:models) "((syn-a ctx 128000 provider synthetic) (syn-b ctx ? provider synthetic) (syn-a ctx ? provider synthetic) (kimi:k1 ctx 256000 provider kimi) (claude-unit-a provider anthropic) (claude-unit-b provider anthropic))")))
          ; The plan's figure, not the weekly one, and the five-hour window.
          ($_ (nt:require quota-line
                (nt:equal (model:quota) "model=unit-configured-model (Synthetic) | synthetic_weekly=37.5% remaining | 5h=12/40 LIMITED")))
          ; A switch keeps the file's other lines and its one choice line.
          ($state (model:state-path))
          ($_ (fs:write $state "(pin a)\n(active-model old)\n(note b)\n"))
          ($_ (nt:require switch-listed (nt:equal (model:set "  syn-b ") "model set to 'syn-b' (Synthetic); persists across restarts")))
          ($_ (nt:require switch-persisted (nt:equal (fs:read-text $state) "(pin a)\n(note b)\n(active-model syn-b)\n")))
          ($_ (nt:require switch-read-back (nt:equal (model:current syn:large:text) "syn-b")))
          ($_ (nt:require switch-unlisted
                (nt:equal (model:set "syn-new") "model set to 'syn-new' (Synthetic); persists across restarts; warning: absent from the latest provider catalog, so the provider may reject it")))
          ($_ (nt:require switch-kimi (nt:equal (model:set kimi:k1) "model set to 'kimi:k1' (Kimi Code API); persists across restarts")))
          ($_ (nt:require switch-chatgpt (nt:equal (model:set "chatgpt:gpt-6-sol") "model set to 'chatgpt:gpt-6-sol' (ChatGPT subscription); persists across restarts")))
          ($_ (nt:require switch-anthropic (nt:equal (model:set "claude-unit-b") "model set to 'claude-unit-b' (anthropic); persists across restarts")))
          ($_ (nt:require anthropic-unknown
                (nt:equal (model:set "claude-other") "unknown anthropic model 'claude-other'; available: claude-unit-a, claude-unit-b")))
          ($_ (nt:require invalid-unchanged (nt:equal (model:set "bad name") "invalid model id 'bad name'; model unchanged")))
          ($_ (nt:require refused-not-persisted (nt:equal (model:current syn:large:text) "claude-unit-b")))
          ; On an Anthropic model, today's tokens; no quota observation says nothing more.
          ($_ (mm:write (model:usage-path) "{\"today\": \"x\", \"in\": 1250000, \"out\": 3400}"))
          ($_ (nt:require quota-anthropic-today
                (nt:equal (model:quota) "model=claude-unit-b (Anthropic API) | today=1.2M in / 3k out | synthetic_weekly=37.5% remaining | 5h=12/40 LIMITED")))
          ($_ (nt:require session-problems
                (nt:equal (map-atom ("claude-unit-a" "claude-x" "ok:model" "no good") $n (model:session-problem $n))
                          ("" "unknown anthropic model 'claude-x'; available: claude-unit-a, claude-unit-b" "" "invalid model id 'no good'"))))
          ; Fresh observations start no refresh; an attempt is stamped once.
          ($_ (nt:require fresh-no-refresh (nt:equal (model:refresh ("models" "quota")) ())))
          ($_ (nt:require stamped-attempt (model:update "quota" (("attempted_at" 5.0) ("last_error" "x")) False)))
          ($record (model:record (model:observations) "quota"))
          ($_ (nt:require stamp-merged (nt:equal ((json:get $record "attempted_at") (json:get $record "last_error")
                                                  (== (json:get $record "value") JsonNull)) (5.0 "x" False))))
          ($_ (nt:require owner-only
                (case (fs:metadata (model:observation-path) follow-permissions)
                  (((fs:info regular $size $identity (fs:permissions $bits)) (== $bits 384)) ($other False)))))
          ($_ (nt:require replaced-record (model:update "quota" (("observed_at" 6.0)) True)))
          ($_ (nt:require record-replaced (nt:equal (json:stringify-python (model:record (model:observations) "quota")) "{\"observed_at\": 6.0}"))))
     (println! UNIT_MODEL_MENU_KEYED_OK)))

;; ------------------------------------------------ unkeyed

(= (mm:unkeyed)
   (let* (($_ (nt:require no-file-no-observation (nt:equal (model:observations) (JsonObject ()))))
          ($_ (mm:observations "{\"schema\": 2, \"models\": {\"observed_at\": 1, \"value\": [{\"id\": \"x\"}]}}"))
          ($_ (nt:require other-schema-ignored (nt:equal (model:observations) (JsonObject ()))))
          ($_ (mm:observations (str:join "" ("{\"schema\": 1, " (mm:stamped "models" 0 "[{\"id\": \"syn-a\"}]") ", "
                                             (mm:attempted "quota") "}"))))
          ((ModelMenuV1 $ids $current $unavailable $status) (model:menu-state))
          ($_ (nt:require unkeyed-ids (nt:equal $ids ("syn-a" "unit-configured-model"))))
          ($_ (nt:require unkeyed-status (model:member? $status ("Synthetic catalog observed 0s ago" "Synthetic catalog observed 1s ago"))))
          ($_ (nt:require quota-without-observation
                (nt:equal (model:quota) "model=unit-configured-model (Synthetic) | quota refreshing; no cached observation")))
          ($_ (nt:require no-anthropic-key
                (nt:equal (model:set "claude-unit-a") "anthropic provider not configured (ANTHROPIC_API_KEY missing); model unchanged")))
          ($_ (nt:require session-no-anthropic-key
                (nt:equal (model:session-problem claude-unit-a) "anthropic provider not configured (ANTHROPIC_API_KEY missing)")))
          ($_ (nt:require kimi-unconfigured
                (nt:equal (model:in-catalog (model:observations) "kimi:k1") None)))
          ($_ (nt:require not-persisted
                (nt:equal (model:set "syn-a") "switch to 'syn-a' NOT applied: could not durably persist the choice")))
          ($_ (nt:require missing-catalog-unknown (nt:equal (model:in-catalog (JsonObject ()) "syn-z") None)))
          ($_ (mm:observations (str:join "" ("{\"schema\": 1, " (mm:attempted "models") ", " (mm:attempted "quota") "}"))))
          ($_ (nt:require models-refreshing (nt:equal (model:models) "(catalog refreshing)")))
          ($_ (nt:require attempted-no-refresh (nt:equal (model:refresh ("models" "quota")) ()))))
     (println! UNIT_MODEL_MENU_UNKEYED_OK)))

; Two writers that update their own kinds at once both land every update:
; each read, change and write is one step under the file's lock.
(= (mm:writer-source $menu)
   (str:join "" ("!(import! &self " (repr $menu) ")\n"
                 "(= (w:loop $kind $n) (if (> $n 30) True (let $_ (model:update $kind (((str:concat \"n\" (repr $n)) True)) False) (w:loop $kind (+ $n 1)))))\n"
                 "!(w:loop (format-args \"{}\" ((car-atom (system:args)))) 1)\n")))
(= (mm:fields $record) (case $record (((JsonObject $pairs) (size-atom $pairs)) ($other 0))))
(= (mm:concurrent-writers)
   (let* (($script (path:join (path:dirname (model:observation-path)) "writer.metta"))
          ($_ (mm:write $script (mm:writer-source (path:join (system:cwd) "src/model_menu_cetta.metta"))))
          ($_ (mm:observations "{\"schema\": 1}"))
          ($engine (system:executable)) ($cwd (system:cwd))
          ($first (proc:spawn ($engine "--lang" "petta" "--import-mode" "ancestor-walk" "--quiet" $script "one") ((cwd $cwd))))
          ($second (proc:spawn ($engine "--lang" "petta" "--import-mode" "ancestor-walk" "--quiet" $script "two") ((cwd $cwd))))
          ($done (map-atom ($first $second) $job (proc:await $job)))
          ($observations (model:observations)))
     (nt:require concurrent-updates-all-land
       (nt:equal ((mm:fields (model:record $observations "one")) (mm:fields (model:record $observations "two"))) (30 30)))))

!(if (mm:keyed?) (let $_ (mm:keyed) (mm:concurrent-writers)) (mm:unkeyed))
