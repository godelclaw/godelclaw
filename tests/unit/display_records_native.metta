; Native unit tests for audit #10 (MeTTa command records + legacy reader) and
; #11 (single attempt.open per provider call). No Python.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/effect_receipts_cetta.metta)
!(import! &self ../../src/console/presentation_cetta.metta)

(= (drn:display-file)
   (let ($producer $start) (display:producer)
     (path:join (display:root) (str:concat (display:safe (display:session)) (str:concat "." (str:concat $producer ".jsonl"))))))

(= (drn:run)
   (let* (($home (nt:required-env "HOME"))
          ($receipts-path (nt:required-env "METTACLAW_EFFECT_RECEIPT_PATH"))
          ($display-dir (display:root))
          ($_ (fs:make-directories $display-dir 448))
          ($_ (fs:write (path:join $display-dir "control.json") "{\"enabled\":true,\"epoch\":\"e1\"}"))

          ; 1. Native MeTTa command formatting on raw MeTTa, JsonArray, and nested suffix lists.
          ($_ (nt:require format-shell
                (nt:equal (erv:metta-command (quote (shell "ls -la"))) "(shell \"ls -la\")")))
          ($_ (nt:require format-utf8-quotes-newlines-bool
                (nt:equal (erv:metta-command (quote (send "héllo \"x\"\nline2" 3 True)))
                          "(send \"héllo \\\"x\\\"\\nline2\" 3 True)")))
          ($_ (nt:require format-json-array
                (nt:equal (erv:metta-command (JsonArray ("read-file" "a/b.txt")))
                          "(read-file \"a/b.txt\")")))
          ($_ (nt:require format-suffix-list
                (nt:equal (erv:metta-command (quote ((shell "a") (read-file "b"))))
                          "((shell \"a\") (read-file \"b\"))")))
          ($_ (nt:require format-plain-string-preserved
                (nt:equal (erv:metta-command "ls -l") "ls -l")))
          ($_ (nt:require format-metta-string-preserved
                (nt:equal (erv:metta-command "(shell \"ls -la\")") "(shell \"ls -la\")")))

          ; 2. Backward-compatible reader for historical Python-list & JSON-array command strings.
          ($_ (nt:require decode-legacy-py-single
                (nt:equal (erv:command-text "['shell', 'ls -la']") "(shell \"ls -la\")")))
          ($_ (nt:require decode-legacy-py-quotes-num
                (nt:equal (erv:command-text "['pin', \"a'b\", 3, 'true']") "(pin \"a'b\" 3 \"true\")")))
          ($_ (nt:require decode-legacy-py-escapes
                (nt:equal (erv:command-text "['send', 'line1\\nline2', '\\x41']") "(send \"line1\\nline2\" \"A\")")))
          ($_ (nt:require decode-legacy-py-suffix-list
                (nt:equal (erv:command-text "[['shell', 'a'], ['read-file', 'b']]")
                          "((shell \"a\") (read-file \"b\"))")))
          ($_ (nt:require decode-legacy-json-array
                (nt:equal (erv:command-text "[\"read-file\", \"notes.md\"]") "(read-file \"notes.md\")")))
          ($_ (nt:require decode-malformed-bracket-safe
                (nt:equal (erv:command-text "[unterminated") "[unterminated")))

          ; 3. er:tool extracts tool name uniformly across all 5 command representations.
          ($_ (nt:require tool-from-metta-expr (nt:equal (er:tool (quote (shell "ls"))) "shell")))
          ($_ (nt:require tool-from-metta-str (nt:equal (er:tool "(read-file \"a.txt\")") "read-file")))
          ($_ (nt:require tool-from-json-array (nt:equal (er:tool (JsonArray ("send" "hi"))) "send")))
          ($_ (nt:require tool-from-legacy-py (nt:equal (er:tool "['pin', 'note']") "pin")))
          ($_ (nt:require tool-from-legacy-json (nt:equal (er:tool "[\"grep-files\", \"foo\"]") "grep-files")))
          ($_ (nt:require tool-from-plain-text-null (nt:equal (er:tool "plain command") JsonNull)))

          ; 4. Durable receipts and display:receipt emit MeTTa form with ZERO "['" strings.
          ($_ (nt:require er-record-ok
                (nt:equal (er:record 1 returned (quote (shell "echo hi")) "hi" "occ-1") 1)))
          ($_ (nt:require er-record-utf8-ok
                (nt:equal (er:record 2 returned (quote (send "héllo \"x\"" 3 True)) "ok" "occ-2") 1)))
          ($_ (nt:require er-suffix-ok
                (nt:equal (er:suffix 3 withheld (quote ((shell "a") (read-file "b"))) "stopped") 1)))
          ($receipts-raw (fs:read-text $receipts-path))
          ($_ (nt:require receipts-have-metta-shell (nt:contains $receipts-raw "(shell \\\"echo hi\\\")")))
          ($_ (nt:require receipts-have-metta-suffix (nt:contains $receipts-raw "((shell \\\"a\\\") (read-file \\\"b\\\"))")))
          ($_ (nt:require receipts-have-no-py-list (not (nt:contains $receipts-raw "['"))))
          ($_ (nt:require display-file-exists (fs:exists (drn:display-file))))
          ($display-raw (fs:read-text (drn:display-file)))
          ($_ (nt:require display-receipt-has-metta (nt:contains $display-raw "(shell \\\"echo hi\\\")")))
          ($_ (nt:require display-receipt-has-no-py-list (not (nt:contains $display-raw "['"))))

          ; 5. er:view reads BOTH legacy Python-list receipt lines AND new MeTTa receipt lines cleanly.
          ($legacy-lines (str:join "\n"
            ("{\"command\": \"['shell', 'legacy-cmd']\", \"disposition\": \"returned\", \"occurrence\": \"old-1\", \"recorded_at\": 1700000000, \"result\": \"old-ok\", \"schema\": 1, \"turn\": 0}"
             "{\"commands\": \"[['shell', 'x'], ['read-file', 'y']]\", \"disposition\": \"withheld\", \"reason\": \"old\", \"recorded_at\": 1700000001, \"schema\": 1, \"turn\": 0}"
             "")))
          ($_ (fs:append-text $receipts-path $legacy-lines))
          ($viewed (er:view 10))
          ($_ (nt:require view-decodes-legacy-cmd (nt:contains $viewed "command=(shell \"legacy-cmd\")")))
          ($_ (nt:require view-decodes-legacy-suffix (nt:contains $viewed "commands=((shell \"x\") (read-file \"y\"))")))
          ($_ (nt:require view-keeps-new-cmd (nt:contains $viewed "command=(shell \"echo hi\")")))
          ($_ (nt:require view-has-no-py-list (not (nt:contains $viewed "['"))))

          ; 6. The console shows a finished legacy "['...']" command and a native one
          ;    by their shell text, and never as a Python list.
          ($buf (buffer:new 65536 bytes))
          ($_ (cc:set "events" $buf))
          ($_ (cc:set "stream-enabled" True))
          ($_ (cc:set "stream-epoch" "e1"))
          ($_ (cc:set "settings" (JsonObject ((JsonPair "agent" "drn") (JsonPair "view_all" True)))))
          ($ev-open (JsonObject ((JsonPair "producer" "p1") (JsonPair "sequence" 1) (JsonPair "epoch" "e1")
                        (JsonPair "time" 1001) (JsonPair "session" "drn") (JsonPair "parent_session" "")
                        (JsonPair "kind" "attempt.open") (JsonPair "attempt" "a1") (JsonPair "model" "m")
                        (JsonPair "status" "awaiting"))))
          ($ev-legacy (JsonObject ((JsonPair "producer" "p1") (JsonPair "sequence" 2) (JsonPair "epoch" "e1")
                        (JsonPair "time" 1002) (JsonPair "session" "drn") (JsonPair "parent_session" "")
                        (JsonPair "kind" "tool.finished") (JsonPair "attempt" "a1") (JsonPair "item" "i1")
                        (JsonPair "source" "executor") (JsonPair "status" "returned") (JsonPair "text" "ok")
                        (JsonPair "command" "['shell', 'old-ls']"))))
          ($ev-native (JsonObject ((JsonPair "producer" "p1") (JsonPair "sequence" 3) (JsonPair "epoch" "e1")
                        (JsonPair "time" 1003) (JsonPair "session" "drn") (JsonPair "parent_session" "")
                        (JsonPair "kind" "tool.finished") (JsonPair "attempt" "a1") (JsonPair "item" "i2")
                        (JsonPair "source" "executor") (JsonPair "status" "returned") (JsonPair "text" "ok")
                        (JsonPair "command" "(shell \"new-ls\")"))))
          ($_ (cc:event $ev-open False))
          ($_ (cc:event $ev-legacy False))
          ($_ (cc:event $ev-native False))
          ($presented (buffer:value $buf))
          ($_ (host:close $buf))
          ($_ (nt:require presentation-decodes-legacy (nt:contains $presented "old-ls")))
          ($_ (nt:require presentation-shows-native (nt:contains $presented "new-ls")))
          ($_ (nt:require presentation-no-py-list (not (nt:contains $presented "['"))))

          ; 7. Single attempt.open per provider call (#11): 5-arg begin + wire, 4-arg begin + wire, compaction.
          ($_ (nt:require open-5arg-ok (nt:equal (display:call-begin "call-1" False "openai" "turn" "gpt-6.2") 1)))
          ($_ (nt:require wire-5arg-noop (nt:equal (display:call-wire "call-1" "openai" "gpt-6.2") 0)))
          ($_ (nt:require open-4arg-ok (nt:equal (display:call-begin "call-2" False "anthropic" "coding") 1)))
          ($_ (nt:require wire-4arg-noop (nt:equal (display:call-wire "call-2" "anthropic" "claude-opus") 0)))
          ($_ (nt:require compaction-ok (nt:equal (display:call-begin "comp-1" True "openai" "compaction" "gpt-6.2") 1)))
          ($_ (nt:require compaction-wire-noop (nt:equal (display:call-wire "comp-1" "openai" "gpt-6.2") 0)))
          ($after-calls (fs:read-text (drn:display-file)))
          ; Exactly 2 attempt.open records total (1 for call-1, 1 for call-2, 0 for comp-1).
          ($_ (nt:require exactly-one-open-per-call (nt:equal (size-atom (str:split "\"kind\":\"attempt.open\"" $after-calls)) 3)))
          ($_ (nt:require call-1-open-carries-model (nt:contains $after-calls "\"model\":\"gpt-6.2\""))))
     True))

!(nt:require display-records-native-suite (drn:run))
!(println! UNIT_DISPLAY_RECORDS_NATIVE_OK)
