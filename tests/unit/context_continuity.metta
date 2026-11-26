; Context continuity in source: the agent's own history is appended through the
; loop host, never through the public append-file skill; each turn's prompt
; builds exactly one context bundle, from the conversation the native Telegram
; client renders, and no retired Python view is consulted.
!(import! &self ../native_test_support.metta)
!(import! &self (library list))

(= (cc:read $repo $file) (fs:read-text (path:join $repo $file)))
; The text of $source from $start up to (not including) $end.
(= (cc:between $source $start $end)
   (case (str:find $source $start)
     (((TextNotFound $_) "")
      ($from (let $rest (str:byte-slice $source $from (str:byte-length $source))
               (case (str:find $rest $end)
                 (((TextNotFound $_) $rest) ($to (str:byte-slice $rest 0 $to)))))))))
(= (cc:count $text $part) (- (size-atom (str:split $part $text)) 1))
(= (cc:none-of $text $parts) (== (filter-atom $parts $p (nt:contains $text $p)) ()))

!(let* (($repo (nt:required-env "TEST_REPOSITORY"))
         ($history (cc:read $repo "src/agent_history_cetta.metta"))
         ($memory (cc:read $repo "src/memory.metta"))
         ($_ (nt:require history-is-appended-through-the-loop-host (nt:contains $history "(loop-host:history-append")))
         ($_ (nt:require history-never-uses-the-public-append-skill
               (and (not (nt:contains $history "(append-file")) (not (nt:contains $memory "(append-file")))))
         ($context (cc:between (cc:read $repo "src/loop.metta") "(= (getContext" "(= (HandleError"))
         ($additions (cc:read $repo "src/turn_additions.metta"))
         ($definition (cc:between $additions "(= (addition-context-sources)" "\n\n"))
         ($_ (nt:require the-prompt-takes-the-context-sources-once
               (and (not (== $context "")) (== (cc:count $context "addition-context-sources") 1))))
         ($_ (nt:require one-bundle-per-turn-from-the-rendered-conversation
               (and (nt:contains $definition "(context-bundle:with-views $views $conversation)")
                    (and (== (cc:count $definition "context-bundle:with-views") 1)
                         (== (cc:count $additions "context-bundle:with-views") 1)))))
         ($_ (nt:require no-retired-python-view-is-consulted
               (and (cc:none-of $additions ("tg:client-metta?" "context_sources.bundle"))
                    (cc:none-of $context ("(getHistory)" "telegram.recent_activity" "telegram.conversation_window"))))))
   (println! UNIT_CONTEXT_CONTINUITY_OK))
