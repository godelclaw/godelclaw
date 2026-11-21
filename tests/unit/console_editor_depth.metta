!(import! &self ../native_test_support.metta)
!(import! &self ../../src/console/editor_cetta.metta)

(= (edp:type-chars $text $i $n)
   (if (>= $i $n) True
     (let* (($ch (str:char-slice $text $i (+ $i 1)))
            ($k (cc:key "text" $ch)))
       (edp:type-chars $text (+ $i 1) $n))))
(= (edp:type $text) (edp:type-chars $text 0 (str:char-length $text)))
(= (edp:val) (buffer:value (cc:get "editor" ConsoleNoEditorV1)))
(= (edp:cur) (let (HostBufferInfo $s $c) (buffer:info (cc:get "editor" ConsoleNoEditorV1)) $c))
(= (edp:pump-edits $n)
   (if (<= $n 0) True
     (let* (($k1 (cc:key "paste" "x"))
            ($k2 (edp:pump-edits (- $n 1))))
       True)))

!(let* (($home (nt:required-env "HOME"))
        ($client (path:join $home "client"))
        ($mk (fs:make-directories $client 448))
        ($settings (JsonObject ((JsonPair "client" $client) (JsonPair "agent" "ed-test"))))
        ($s1 (cc:set "settings" $settings))
        ($s2 (cc:set "editor" (buffer:new 1048576 text)))
        ($s3 (cc:set "events" (buffer:new 1048576 bytes)))
        ($s4 (cc:set "color" False))
        ($r0 (cc:editor-reset))

        ; --- 1. Undo/redo with word/space coalescing and exact cursor restoration ---
        ($t1 (edp:type "hello world"))
        ($c1 (nt:require typed-text (== (edp:val) "hello world")))
        ($c2 (nt:require undo-coalesced-word (progn (cc:key "ctrl-z" "") (and (== (edp:val) "hello ") (== (edp:cur) 6)))))
        ($c3 (nt:require undo-coalesced-space (progn (cc:key "ctrl-_" "") (and (== (edp:val) "hello") (== (edp:cur) 5)))))
        ($raw0x1f (buffer:new 64 bytes))
        ($app0x1f (buffer:append $raw0x1f (str:from-python-codepoints (31))))
        ($c4 (nt:require undo-raw-0x1f (progn (cc:keys $raw0x1f flush 8) (and (== (edp:val) "") (== (edp:cur) 0)))))
        ($cls0x1f (host:close $raw0x1f))
        ($c5 (nt:require undo-empty-idempotent (progn (cc:key "ctrl-z" "") (and (== (edp:val) "") (== (edp:cur) 0)))))
        ($c6 (nt:require redo-ctrl-x-r (progn (cc:key "ctrl-x" "") (cc:key "text" "r") (and (== (edp:val) "hello") (== (edp:cur) 5)))))
        ($c7 (nt:require redo-alt-u (progn (cc:key "escape" "") (cc:key "text" "u") (and (== (edp:val) "hello ") (== (edp:cur) 6)))))
        ($c8 (nt:require redo-ctrl-x-ctrl-r (progn (cc:key "ctrl-x" "") (cc:key "ctrl-r" "") (and (== (edp:val) "hello world") (== (edp:cur) 11)))))
        ($c9 (nt:require redo-empty-idempotent (progn (cc:key "escape" "") (cc:key "text" "r") (and (== (edp:val) "hello world") (== (edp:cur) 11)))))

        ; --- 2. Emacs C-x u / C-x C-u undo, bare ESC passthrough, branch invalidation ---
        ($c10 (nt:require undo-ctrl-x-u (progn (cc:key "ctrl-x" "") (cc:key "text" "u") (== (edp:val) "hello "))))
        ($c11 (nt:require noop-delete-preserves-redo (progn (cc:key "delete" "") (> (size-atom (cc:get "redo-stack" ())) 0))))
        ($c12 (nt:require bare-esc-does-not-eat-char (progn (cc:key "escape" "") (cc:key "text" "z") (== (edp:val) "hello z"))))
        ($c13 (nt:require forward-edit-clears-redo (== (cc:get "redo-stack" ()) ())))
        ($c14 (nt:require undo-ctrl-x-ctrl-u (progn (cc:key "ctrl-x" "") (cc:key "ctrl-u" "") (== (edp:val) "hello "))))

        ; --- 3. Discrete edits (kill/yank/paste/UTF-8 cursor) undo/redo cleanly ---
        ($u1 (cc:editor-reset))
        ($u2 (cc:replace-draft ""))
        ($u3 (cc:key "paste" "ab雪🌿cd"))
        ($u4 (cc:key "left" ""))
        ($u5 (cc:key "left" ""))
        ($cur-mid (edp:cur))
        ($c15 (nt:require utf8-cursor-before-kill (== $cur-mid 9)))
        ($u6 (cc:key "ctrl-k" ""))
        ($c16 (nt:require kill-line-applied (== (edp:val) "ab雪🌿")))
        ($c17 (nt:require kill-line-undone-with-utf8-cursor (progn (cc:key "ctrl-z" "") (and (== (edp:val) "ab雪🌿cd") (== (edp:cur) 9)))))
        ($c18 (nt:require kill-line-redone (progn (cc:key "ctrl-x" "") (cc:key "ctrl-z" "") (and (== (edp:val) "ab雪🌿") (== (edp:cur) 9)))))
        ($u7 (cc:key "ctrl-y" ""))
        ($c19 (nt:require yank-undone (progn (cc:key "ctrl-z" "") (== (edp:val) "ab雪🌿"))))

        ; --- 4. Bounded undo stack (<= 128) ---
        ($b1 (cc:editor-reset))
        ($b2 (cc:replace-draft ""))
        ($b3 (edp:pump-edits 150))
        ($c20 (nt:require undo-stack-bounded (== (size-atom (cc:get "undo-stack" ())) 128)))

        ; --- 5. Incremental Ctrl-R / Ctrl-S reverse & forward history search ---
        ($h1 (cc:history-put "alpha first"))
        ($h2 (cc:history-put "Deploy roll-11"))
        ($h3 (cc:history-put "beta 雪 snow"))
        ($h4 (cc:history-put "deploy check"))
        ($h5 (cc:history-put "gamma latest"))
        ($h6 (cc:set "history-index" (cc:get "history-count" 0)))
        ($d1 (cc:editor-reset))
        ($d2 (cc:replace-draft "my unsent draft"))
        ($d3 (cc:key "left" ""))
        ($d4 (cc:key "left" ""))
        ($saved-cur (edp:cur))
        ($c21 (nt:require saved-draft-cursor (== $saved-cur 13)))

        ; Enter reverse-i-search: previews newest entry
        ($sr1 (cc:key "ctrl-r" ""))
        ($c22 (nt:require search-active-on-ctrl-r (== (cc:get "search-active" False) True)))
        ($c23 (nt:require search-initial-preview (== (edp:val) "gamma latest")))
        ($c24 (nt:require search-initial-bar (nt:contains (cc:search-bar 120) "(reverse-i-search)`': [1/5")))

        ; Draft-save while searching MUST preserve the pre-search unsent draft!
        ($sv1 (cc:save-draft))
        ($stored (sf:native-object (path:join $client "draft.json")))
        ($c25 (nt:require save-draft-during-search-keeps-unsent
                (and (== (sf:get $stored "text" "") "my unsent draft")
                     (== (sf:get $stored "cursor_bytes" -1) 13))))

        ; Smart-case incremental search: lowercase "deploy" matches both "deploy check" and "Deploy roll-11"
        ($sr2 (edp:type "deploy"))
        ($c26 (nt:require search-lowercase-matches-newest (== (edp:val) "deploy check")))
        ($c27 (nt:require search-lowercase-stats (nt:contains (cc:search-bar 120) "[1/2")))

        ; Ctrl-R steps to older match; Ctrl-S steps forward to newer match
        ($sr3 (cc:key "ctrl-r" ""))
        ($c28 (nt:require search-step-older (== (edp:val) "Deploy roll-11")))
        ($c29 (nt:require search-step-older-stats (nt:contains (cc:search-bar 120) "[2/2")))
        ($sr4 (cc:key "ctrl-s" ""))
        ($c30 (nt:require search-step-forward (== (edp:val) "deploy check")))
        ($c31 (nt:require search-forward-label (nt:contains (cc:search-bar 120) "(i-search)`deploy': [1/2")))

        ; Typing non-matching UTF-8 character fails search, UTF-8 backspace recovers it
        ($sr5 (cc:key "text" "雪"))
        ($c32 (nt:require search-no-match-state (== (cc:get "search-match" 0) -1)))
        ($c33 (nt:require search-failed-label (nt:contains (cc:search-bar 120) "(failed i-search)`deploy雪': [no match")))
        ($sr6 (cc:key "backspace" ""))
        ($c34 (nt:require search-utf8-backspace-recovers (and (== (cc:get "search-query" "") "deploy") (>= (cc:get "search-match" -1) 0))))

        ; Narrow width clipping and control sanitization
        ($c35 (nt:require search-bar-clipped (<= (terminal:cells (cc:search-bar 28)) 28)))

        ; Ctrl-G cancels and restores exact pre-search draft and cursor!
        ($sr7 (cc:key "ctrl-g" ""))
        ($c36 (nt:require cancel-exits-search (== (cc:get "search-active" True) False)))
        ($c37 (nt:require cancel-restores-draft-and-cursor (and (== (edp:val) "my unsent draft") (== (edp:cur) 13))))

        ; Uppercase query is case-sensitive ("Deploy" matches only "Deploy roll-11")
        ($sr8 (cc:key "ctrl-r" ""))
        ($sr9 (edp:type "Deploy"))
        ($c38 (nt:require smart-case-uppercase-exact (and (== (edp:val) "Deploy roll-11") (nt:contains (cc:search-bar 120) "[1/1"))))

        ; Enter accepts match into editor AND records undo so Ctrl-Z restores pre-search draft!
        ($sr10 (cc:key "enter" ""))
        ($c39 (nt:require accept-exits-search-keeps-match (and (== (cc:get "search-active" True) False) (== (edp:val) "Deploy roll-11"))))
        ($c40 (nt:require undo-after-search-accept-restores-draft (progn (cc:key "ctrl-z" "") (and (== (edp:val) "my unsent draft") (== (edp:cur) 13)))))

        ; Motion key during search accepts match AND applies motion
        ($sr11 (cc:key "ctrl-r" ""))
        ($sr12 (edp:type "雪"))
        ($c41 (nt:require utf8-query-matches-history (== (edp:val) "beta 雪 snow")))
        ($sr13 (cc:key "ctrl-a" ""))
        ($c42 (nt:require motion-accepts-and-moves
                (and (== (cc:get "search-active" True) False)
                     (and (== (edp:val) "beta 雪 snow") (== (edp:cur) 0)))))

        ; Cleanup owned native buffers
        ($cl1 (host:close (cc:get "editor" ConsoleNoEditorV1)))
        ($cl2 (host:close (cc:get "events" ConsoleNoBufferV1))))
   (println! UNIT_CONSOLE_EDITOR_DEPTH_OK))
