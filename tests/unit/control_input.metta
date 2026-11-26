; The Telegram control responder's input handling, each case in a clean home:
; control commands are recognized from their declarations; only ordinary
; operator input counts as awaiting cognition; a parse is cached only while
; its task is ordinary input on the page; a ledger that does not answer gives
; its error, no notice key and no delay; the Claude Code authorization command
; turns its flag over; /model shows the recorded choice, else the configured one.
!(import! &self ../native_test_support.metta)
!(import! &self (library list))

(= (ci:quote $text) (json-stringify (JsonString $text)))
; Settings may name the case's directory as {folder}.
(= (ci:expand $value $dir) (str:join $dir (str:split "{folder}" $value)))
(= (ci:probe $repo $home $tag $body $settings)
   (let* (($dir (path:join $home $tag)) ($_ (fs:make-directories $dir 448))
          ($program (path:join $dir "probe.metta"))
          ($_ (fs:write $program (str:join "" ("!(import! &self " (ci:quote (path:join $repo "channels/telegram_control.metta")) ")\n" $body))))
          ($env (list:append (map-atom $settings $pair (let ($name $value) $pair ($name (ci:expand $value $dir))))
                  (list:append (("METTACLAW_ENGINE" "cetta") ("METTACLAW_ACTIVE_ENGINE" "cetta") ("OPENAI_API_KEY" "test-only-unused")
                                ("METTACLAW_ENGINE_STATE_PATH" (path:join $dir "engine")))
                               (nt:environment $dir)))))
     (case (proc:run ((system:executable) "--lang" "petta" "--import-mode" "ancestor-walk" $program) $repo $env 30000 1048576)
       (((proc:exited 0 $out $err) (if (nt:contains $out "(Error ") (Failed $out) $out))
        ($bad (Failed $bad))))))
(= (ci:has-all $out $parts) (and (== (get-type $out) String) (== (filter-atom $parts $p (not (nt:contains $out $p))) ())))
(= (ci:input-task $identity $role $field $text)
   (let $body (json-stringify (JsonArray ((JsonString "input") (JsonString "42.0") (JsonString "message") (JsonString $role)
                (JsonObject ((JsonPair "update_id" (JsonString $identity))
                             (JsonPair "message" (JsonObject ((JsonPair "date" (JsonNumber "1790000000")) (JsonPair $field (JsonString $text))))))))))
     (str:join "" ("(" (ci:quote $identity) " " (ci:quote $body) ")"))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
         ($commands (ci:probe $repo $home "commands" (str:join "\n" (
           "!(println! (OTHER (ti:control-text? \"/activity@OtherBot\")))"
           "!(println! (CASE (ti:control-text? \"  /ACTIVITY@OtherBot  \")))"
           "!(println! (UNKNOWN (ti:control-text? \"/custom question\")))"
           "!(println! (ORDINARY (ti:control-text? \"please explain /activity\")))"
           "!(println! (EMPTY (ti:control-text? \"\")))"
           "(tg-cmd:command \"/new_control\" delegated \"fixture declaration\")"
           "!(println! (DYNAMIC (ti:control-text? \"/new_control@OtherBot\")))" "")) ()))
         ($_ (nt:require commands-are-recognized-from-their-declarations
               (ci:has-all $commands ("(OTHER true)" "(CASE true)" "(DYNAMIC true)" "(UNKNOWN false)" "(ORDINARY false)" "(EMPTY false)"))))
         ($tasks (str:join " " ((ci:input-task "foreign" "operator" "text" "/activity@OtherBot")
                                (ci:input-task "caption" "operator" "caption" " /ACTIVITY@OtherBot")
                                (ci:input-task "ordinary" "operator" "text" "a real question")
                                (ci:input-task "unknown" "operator" "text" "/custom explain")
                                (ci:input-task "peer" "peer" "text" "/activity"))))
         ($view (ci:probe $repo $home "awaiting" (str:join "" (
           "!(let $tasks (tc:notice-tasks (" $tasks "))\n"
           "   (let $_ (notice:begin-scan $tasks $tasks)\n"
           "     (println! (FILTERED (size-atom $tasks) (json-stringify (notice:input-view))))))\n")) ()))
         ($_ (nt:require only-ordinary-operator-input-awaits-cognition
               (and (ci:has-all $view ("(FILTERED 3 " "pending"))
                    (nt:contains (str:join "" (str:split "\\" $view)) "\"pending\":2"))))
         ($input (json-stringify (JsonArray ((JsonString "input") (JsonString "42.0") (JsonString "message") (JsonString "operator")
                    (JsonObject ((JsonPair "update_id" (JsonNumber "1")) (JsonPair "message" (JsonObject ((JsonPair "date" (JsonNumber "5")))))))))))
         ($edited (json-stringify (JsonArray ((JsonString "input") (JsonString "42.0") (JsonString "message") (JsonString "operator")
                    (JsonObject ((JsonPair "update_id" (JsonNumber "1")) (JsonPair "edited_message" (JsonObject ((JsonPair "date" (JsonNumber "6")))))))))))
         ($control (json-stringify (JsonArray ((JsonString "command") (JsonString "k") (JsonString "/health") (JsonString "") (JsonString "42.0")))))
         ($cache (ci:probe $repo $home "cache" (str:join "" (
           "(= (cached $task) (collapse (match &tc-parsed (tc:cached $task $body $value) $body)))\n"
           "!(let* (($_ (tc:parse \"t1\" " (ci:quote $input) "))\n"
           "        ($_ (tc:parse \"t1\" " (ci:quote $input) "))\n"
           "        ($_ (tc:parse \"c1\" " (ci:quote $control) "))\n"
           "        ($_ (println! (ONCE (size-atom (cached \"t1\")))))\n"
           "        ($_ (tc:prune ((\"t1\" " (ci:quote $input) "))))\n"
           "        ($_ (println! (ON-PAGE (size-atom (cached \"t1\")) (size-atom (cached \"c1\")))))\n"
           "        ($_ (tc:parse \"t1\" " (ci:quote $edited) "))\n"
           "        ($_ (println! (REPARSED (== (cached \"t1\") (" (ci:quote $edited) ")))))\n"
           "        ($_ (tc:prune ())))\n"
           "   (println! (OFF-PAGE (size-atom (cached \"t1\")))))\n")) ()))
         ($_ (nt:require a-parse-is-cached-only-while-its-task-is-on-the-page
               (ci:has-all $cache ("(ONCE 1)" "(ON-PAGE 1 0)" "(REPARSED true)" "(OFF-PAGE 0)"))))
         ($update (json-stringify (JsonObject ((JsonPair "update_id" (JsonNumber "7"))
                     (JsonPair "message" (JsonObject ((JsonPair "date" (JsonNumber "5")) (JsonPair "text" (JsonString "q")))))))))
         ($notice (ci:probe $repo $home "no-ledger" (str:join "" (
           "(= (kind $answer) (case $answer (((Error $where $why) error) ($other $other))))\n"
           "!(let* (($update (json-parse " (ci:quote $update) "))\n"
           "        ($first (tc:input-notice \"t\" \"42.0\" \"message\" \"operator\" $update))\n"
           "        ($_ (sleep 0.05))\n"
           "        ($second (tc:input-notice \"t\" \"42.0\" \"message\" \"operator\" $update)))\n"
           "   (println! (NOTICE (kind $first) (kind $second) (notice:pending \"t\"))))\n"))
           (("METTACLAW_OBSERVED_STRATEGY" "1") ("METTACLAW_INPUT_ACK_SECONDS" "0.01")
            ("METTACLAW_INPUT_LEDGER_SOCKET" "{folder}/no-ledger.sock") ("METTACLAW_TELEGRAM_SERVICE_SOCKET" "{folder}/no-service.sock"))))
         ($_ (nt:require an-unanswered-ledger-gives-its-error-and-no-notice (ci:has-all $notice ("(NOTICE error error none)"))))
         ($toggle (ci:probe $repo $home "authorization" (str:join "\n" (
           "!(let* (($first (tc:answer \"/claude_code_authorization\" \"\"))"
           "        ($flag (fs:read-text (tc:claude-code-flag)))"
           "        ($second (tc:answer \"/claude_code_authorization\" \"\")))"
           "   (println! (TOGGLE $first $flag $second (fs:read-text (tc:claude-code-flag)))))" "")) ()))
         ($_ (nt:require the-authorization-command-turns-its-flag-over
               (ci:has-all $toggle ("(TOGGLE \"claude-code authorization: ON\" \"1\" \"claude-code authorization: OFF\" \"0\")"))))
         ($recorded (ci:probe $repo $home "model-recorded"
           "!(fs:write (system:getenv-or-default \"METTACLAW_MODEL_STATE_PATH\" \"\") \"(other atom)\\n(active-model kimi:k2)\\n\")\n!(println! (MODEL (tc:answer \"/model\" \"\")))\n"
           (("METTACLAW_MODEL_STATE_PATH" "{folder}/model-state") ("SYNTHETIC_MODEL" "configured-model"))))
         ($configured (ci:probe $repo $home "model-configured" "!(println! (MODEL (tc:answer \"/model\" \"\")))\n"
           (("METTACLAW_MODEL_STATE_PATH" "{folder}/missing") ("SYNTHETIC_MODEL" "configured-model"))))
         ($_ (nt:require model-shows-the-recorded-choice-else-the-configured-one
               (and (ci:has-all $recorded ("selected model: kimi:k2 — applied at the next provider request"))
                    (ci:has-all $configured ("selected model: configured-model — applied at the next provider request"))))))
   (println! UNIT_CONTROL_INPUT_OK))
