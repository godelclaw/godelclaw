; The channel access policy on the core engine: facts read as data and
; checked, Telegram's own facts, layers, classes and named groups, wakes and
; labels, what Telegram's service must admit, and reloading the facts file.
;   NATIVE_TEST_ARTIFACTS=... $NATIVE_TEST_CORE/cetta --lang petta --import-mode ancestor-walk \
;     --quiet scripts/test_access_policy_cetta.metta
!(import! &self ../tests/native_test_support.metta)
!(import! &self ../channels/access_policy.metta)

(= (apt:refused? $text $why)
   (case (ap:parse-facts $text)
     (((AccessPolicyInvalidV1 $reason $fact) (nt:contains $reason $why))
      ($other False))))

(= (apt:reading)
   (let* (($_ (nt:require comment-cut
                (nt:equal (ap:code-of "(owner (sender telegram \"7\")) ; the operator") "(owner (sender telegram \"7\")) ")))
          ($_ (nt:require semicolon-in-string-kept
                (nt:equal (ap:code-of "(group-member (named-group \"a;b\") (sender telegram \"8\"))")
                          "(group-member (named-group \"a;b\") (sender telegram \"8\"))")))
          ($_ (nt:require comment-after-string
                (nt:equal (ap:code-of "(allow-named-group everywhere (named-group \"x;y\")) ; \"z\" ; w")
                          "(allow-named-group everywhere (named-group \"x;y\")) ")))
          ($_ (nt:require empty-file (nt:equal (ap:parse-facts "; nothing here\n\n") (AccessFactsV1 ()))))
          ($_ (nt:require example-file-valid
                (case (ap:parse-facts (fs:read-text "config/access.metta.example"))
                  (((AccessFactsV1 $facts) (== (size-atom $facts) 10)) ($other False)))))
          ($_ (nt:require facts-normalized
                (nt:equal (ap:parse-facts (str:join "\n"
                            ("; who may reach the agent"
                             "(owner (sender telegram \"7\"))"
                             "(allow-sender everywhere (sender slack \"T1:U2\")) ; a colleague"
                             "(allow-sender (on telegram) (sender telegram \"42\"))"
                             "(allow-named-group (on slack) (named-group \"lab\"))"
                             "(group-member (named-group \"lab\") (sender slack \"T1:U3\"))"
                             "(access (account telegram \"bot\") direct open)"
                             "(rule-for (group telegram \"-100\") groups stranger (rule record wake-never))"
                             "(named-rule generic groups (named-group \"lab\") (rule record wake-always (reason \"lab\") (tier \"mid\")))")))
                          (AccessFactsV1 ((ap-owner telegram "7" 1)
                                          (ap-allow-sender ApEverywhere slack "T1:U2" 1)
                                          (ap-allow-sender telegram telegram "42" 1)
                                          (ap-allow-named slack "lab" 1)
                                          (ap-member "lab" slack "T1:U3" 1)
                                          (ap-access (ApAccount telegram "bot") ApDirect ApOpen 1 5)
                                          (ap-rule (ApGroup telegram "-100") ApGroups ApStranger (ApRuleV1 ApRecord ApWakeNever "" "") 1 6)
                                          (ap-named-rule ApGeneric ApGroups "lab" (ApRuleV1 ApRecord ApWakeAlways "lab" "mid") 1 7))))))
          ($_ (nt:require unknown-fact-refused (apt:refused? "(foo bar)" "facts are")))
          ($_ (nt:require unknown-channel-refused (apt:refused? "(owner (sender twitter \"1\"))" "a sender is")))
          ($_ (nt:require numeric-id-refused (apt:refused? "(owner (sender telegram 7))" "a sender is")))
          ($_ (nt:require bad-wake-refused (apt:refused? "(rule-for generic direct listed (rule record sometimes))" "a rule is")))
          ($_ (nt:require bad-tier-refused
                (apt:refused? "(rule-for generic direct listed (rule record wake-always no-reason (tier \"huge\")))" "a rule is")))
          ($_ (nt:require foreign-sender-on-channel-list-refused
                (apt:refused? "(allow-sender (on slack) (sender telegram \"1\"))" "names that channel's senders")))
          ($_ (nt:require bad-layer-refused (apt:refused? "(access (group telegram) groups open)" "a layer is")))
          ($_ (nt:require bad-class-refused (apt:refused? "(rule-for generic direct nobody (rule drop wake-never))" "a class is")))
          ($_ (nt:require unparsable-refused (apt:refused? "(owner (sender telegram \"7\")" "do not parse")))
          ; A directive or a variable is refused, never run or bound.
          ($_ (nt:require directive-not-run (apt:refused? "!(system:exit 3)" "facts only")))
          ($_ (nt:require bare-call-not-run (apt:refused? "system:exit (3)" "facts are")))
          ($_ (nt:require variable-refused (apt:refused? "(owner (sender telegram $who))" "facts only")))
          ($_ (nt:require variable-head-refused (apt:refused? "($what (sender telegram \"7\"))" "facts only")))
          ($_ (nt:require marks-inside-strings-kept
                (nt:equal (ap:parse-facts "(owner (sender telegram \"!$;x\"))") (AccessFactsV1 ((ap-owner telegram "!$;x" 1)))))))
     True))

; Telegram as it has always behaved: operator 7, allowed private chats 7 and
; 42, allowed group -100, wake peer 8.
(= (apt:today $allow-private)
   (ap:install (ap:telegram-facts-from ("7") ("7" "42" "-100") ("8") $allow-private)))

(= (apt:telegram-defaults)
   (let* (($_ (apt:today False))
          ($_ (nt:require operator-direct
                (nt:equal (ap:decide telegram "7" "" False) (AccessDecisionV1 record sender-wake "operator message" "" ""))))
          ($_ (nt:require listed-direct-quiet
                (nt:equal (ap:decide telegram "42" "" False) (AccessDecisionV1 record quiet "" "" ""))))
          ($_ (nt:require listed-direct-addressed
                (nt:equal (ap:decide telegram "42" "" True) (AccessDecisionV1 record addressed "" "" ""))))
          ($_ (nt:require peer-direct
                (nt:equal (ap:decide telegram "8" "" False) (AccessDecisionV1 record sender-wake "peer message" "" ""))))
          ($_ (nt:require stranger-direct-dropped
                (nt:equal (ap:decide telegram "99" "" True) (AccessDecisionV1 drop silent "" "" ""))))
          ($_ (nt:require stranger-in-allowed-group-labelled
                (nt:equal (ap:decide telegram "99" "-100" False) (AccessDecisionV1 record quiet "" "" "untrusted stranger"))))
          ($_ (nt:require listed-in-allowed-group
                (nt:equal (ap:decide telegram "42" "-100" False) (AccessDecisionV1 record quiet "" "" ""))))
          ($_ (nt:require operator-in-group
                (nt:equal (ap:decide telegram "7" "-100" False) (AccessDecisionV1 record sender-wake "operator message" "" ""))))
          ($_ (nt:require peer-in-group
                (nt:equal (ap:decide telegram "8" "-100" False) (AccessDecisionV1 record sender-wake "peer message" "" ""))))
          ($_ (nt:require unlisted-group-dropped-even-for-operator
                (nt:equal (ap:decide telegram "7" "-200" True) (AccessDecisionV1 drop silent "" "" ""))))
          ($_ (nt:require channel-post-in-allowed-group
                (nt:equal (ap:decide telegram "" "-100" False) (AccessDecisionV1 record quiet "" "" "untrusted stranger"))))
          ($_ (nt:require owners-only-operators (nt:equal (ap:owner? telegram "7" "") True)))
          ($_ (nt:require peer-not-owner (nt:equal (ap:owner? telegram "8" "") False)))
          ; The service's declarations hold, whatever the client's settings say.
          ($_ (nt:require declared-peer-wakes
                (nt:equal (ap:decide telegram "77" "" False "wake-peer") (AccessDecisionV1 record sender-wake "peer message" "" ""))))
          ($_ (nt:require declared-operator-is-owner
                (nt:equal (ap:decide telegram "78" "-100" False "operator") (AccessDecisionV1 record sender-wake "operator message" "" ""))))
          ($_ (nt:require ordinary-role-adds-nothing
                (nt:equal (ap:decide telegram "79" "" False "ordinary") (AccessDecisionV1 drop silent "" "" ""))))
          ($_ (apt:today True))
          ($_ (nt:require allow-private-opens-direct
                (nt:equal (ap:decide telegram "99" "" False) (AccessDecisionV1 record quiet "" "" "untrusted stranger"))))
          ($_ (nt:require allow-private-keeps-groups-closed
                (nt:equal (ap:decide telegram "99" "-200" True) (AccessDecisionV1 drop silent "" "" "")))))
     True))

(= (apt:file-facts $text)
   (case (ap:parse-facts $text)
     (((AccessFactsV1 $facts) $facts)
      ($other (let $_ (println! (UNEXPECTED_REFUSAL $other)) (system:exit 1))))))

(= (apt:with-file $text)
   (ap:install (ap:append (ap:telegram-facts-from ("7") ("7" "42" "-100") ("8") False) (apt:file-facts $text))))

(= (apt:layers)
   (let* (($_ (apt:with-file (str:join "\n"
                ("; protobots: everything recorded, strangers never wake the agent; the"
                 "; group is open by the channel's settings, so it becomes an allowlist"
                 "(access (group telegram \"-100\") groups allowlist)"
                 "(rule-for (group telegram \"-100\") groups stranger (rule record wake-never))"
                 "(rule-for (group telegram \"-100\") groups listed (rule record wake-on-mention))"
                 "(named-rule (group telegram \"-100\") groups (named-group \"bots\")"
                 "  (rule record wake-on-mention (reason \"bot mention\") (tier \"light\")))"
                 "(group-member (named-group \"bots\") (sender telegram \"8\"))"
                 "(access (channel telegram) direct open)"
                 "(allow-sender everywhere (sender telegram \"55\"))"
                 "(owner (sender telegram \"66\"))"
                 "(named-rule generic groups (named-group \"muted\") (rule drop wake-never))"
                 "(group-member (named-group \"muted\") (sender telegram \"77\"))"
                 "(group-member (named-group \"muted\") (sender telegram \"66\"))"))))
          ($_ (nt:require group-stranger-recorded-silently
                (nt:equal (ap:decide telegram "99" "-100" True) (AccessDecisionV1 record silent "" "" "untrusted stranger"))))
          ($_ (nt:require open-group-ignores-stranger-rule
                (let $saved (ap:stored)
                  (let $o (ap:install (ap:append (ap:telegram-facts-from ("7") ("7" "42" "-100") ("8") False)
                                        (apt:file-facts "(rule-for (group telegram \"-100\") groups stranger (rule drop wake-never))")))
                    (let $d (ap:decide telegram "99" "-100" False)
                      (let $r (ap:install $saved)
                        (nt:equal $d (AccessDecisionV1 record quiet "" "" "untrusted stranger"))))))))
          ($_ (nt:require group-listed-waits-for-mention
                (nt:equal (ap:decide telegram "42" "-100" False) (AccessDecisionV1 record silent "" "" ""))))
          ($_ (nt:require group-listed-mention-wakes
                (nt:equal (ap:decide telegram "42" "-100" True) (AccessDecisionV1 record addressed "" "" ""))))
          ; An inner layer's named rule comes before the channel's wake peers.
          ($_ (nt:require inner-named-rule-first
                (nt:equal (ap:decide telegram "8" "-100" False) (AccessDecisionV1 record silent "" "light" ""))))
          ($_ (nt:require named-rule-reason-and-tier
                (nt:equal (ap:decide telegram "8" "-100" True) (AccessDecisionV1 record sender-wake "bot mention" "light" ""))))
          ($_ (nt:require peer-rule-outside-that-group
                (nt:equal (ap:decide telegram "8" "" False) (AccessDecisionV1 record sender-wake "peer message" "" ""))))
          ($_ (nt:require file-overrides-channel-setting
                (nt:equal (ap:decide telegram "99" "" False) (AccessDecisionV1 record quiet "" "" "untrusted stranger"))))
          ($_ (nt:require generic-list-holds-on-telegram
                (nt:equal (ap:decide telegram "55" "" False) (AccessDecisionV1 record quiet "" "" ""))))
          ($_ (nt:require file-owner
                (nt:equal (ap:decide telegram "66" "" False) (AccessDecisionV1 record sender-wake "operator message" "" ""))))
          ($_ (nt:require named-group-drops-in-open-group
                (nt:equal (ap:decide telegram "77" "-100" True) (AccessDecisionV1 drop silent "" "" ""))))
          ($_ (nt:require owner-before-named-group
                (nt:equal (ap:decide telegram "66" "-100" False) (AccessDecisionV1 record sender-wake "operator message" "" ""))))
          ($_ (nt:require generic-entry-only-on-its-channel
                (nt:equal (ap:decide slack "55" "" False) (AccessDecisionV1 drop silent "" "" ""))))
          ($_ (apt:with-file (str:join "\n"
                ("(access (channel telegram) direct open)"
                 "(access (channel telegram) direct allowlist)"
                 "(named-rule (channel telegram) groups (named-group \"x\") (rule record wake-never))"
                 "(group-member (named-group \"x\") (sender telegram \"42\"))"
                 "(rule-for (group telegram \"-100\") groups listed (rule record wake-on-mention))"))))
          ($_ (nt:require last-statement-wins
                (nt:equal (ap:decide telegram "99" "" False) (AccessDecisionV1 drop silent "" "" ""))))
          ; The file's named rule comes before the channel's own at one layer.
          ($_ (nt:require file-named-rule-first
                (nt:equal (ap:decide telegram "42" "-100" True) (AccessDecisionV1 record silent "" "" ""))))
          ; An unlisted member of a named group with a recording rule is labelled.
          ($_ (apt:with-file (str:join "\n"
                ("(access (group telegram \"-300\") groups allowlist)"
                 "(named-rule generic groups (named-group \"guests\") (rule record wake-on-mention))"
                 "(group-member (named-group \"guests\") (sender telegram \"99\"))"))))
          ($_ (nt:require unlisted-member-labelled
                (nt:equal (ap:decide telegram "99" "-300" True)
                          (AccessDecisionV1 record addressed "" "" "untrusted, in named group guests"))))
          ($_ (nt:require group-allowlist-default-drops-strangers
                (nt:equal (ap:decide telegram "98" "-300" True) (AccessDecisionV1 drop silent "" "" ""))))
          ($_ (nt:require group-layer-inherits-channel-listed-rule
                (nt:equal (ap:decide telegram "42" "-300" False) (AccessDecisionV1 record quiet "" "" ""))))
          ; Without the channel's own settings, the generic group default holds:
          ; a listed sender wakes the agent by a mention.
          ($_ (ap:install (apt:file-facts (str:join "\n"
                ("(access (group slack \"C1\") groups allowlist)"
                 "(allow-sender (on slack) (sender slack \"U42\"))")))))
          ($_ (nt:require generic-group-default-listed-on-mention
                (nt:equal ((ap:decide slack "U42" "C1" False) (ap:decide slack "U42" "C1" True) (ap:decide slack "U42" "" False))
                          ((AccessDecisionV1 record silent "" "" "") (AccessDecisionV1 record addressed "" "" "")
                           (AccessDecisionV1 record quiet "" "" ""))))))
     True))

; A message is never woken unless recorded, for every rule and message.
(= (apt:visibilities) (ApRecord ApDrop))
(= (apt:wakes) (ApWakeNever ApWakeOnMention ApWakeAlways))
(= (apt:truths) (True False))
(= (apt:wake-recorded)
   (let $broken
       (collapse
         (let* (($v (superpose (apt:visibilities))) ($w (superpose (apt:wakes))) ($r (superpose ("" "why")))
                ($m (superpose (apt:truths))) ($d (superpose (apt:truths))) ($t (superpose (apt:truths))))
           (let $decision (ap:decision (ApRuleV1 $v $w $r "") ApListedClass $t $m $d)
             (case $decision
               (((AccessDecisionV1 drop $note $reason $tier $label)
                 (if (and (== $v ApDrop) (== $note silent)) (empty) ($v $w $r $m $d)))
                ((AccessDecisionV1 record $note $reason $tier $label)
                 (if (== $v ApRecord) (empty) ($v $w $r $m $d)))
                ($other ($other)))))))
     (nt:require wake-only-when-recorded (nt:equal $broken ()))))

(= (apt:admission)
   (let* (($_ (apt:with-file (str:join "\n"
                ("(owner (sender telegram \"66\"))"
                 "(allow-sender everywhere (sender telegram \"55\"))"
                 "(group-member (named-group \"bots\") (sender telegram \"8\"))"
                 "(group-member (named-group \"bots\") (sender telegram \"88\"))"
                 "(rule-for (group telegram \"-100\") groups stranger (rule record wake-never))"
                 "(access (group telegram \"-400\") groups open)"
                 "(access (group telegram \"-300\") groups disabled)"
                 "(allow-sender (on slack) (sender slack \"U1\"))"))))
          ($_ (nt:require admission-adds-file-chats
                (nt:equal (ap:telegram-admission)
                          (TelegramAdmissionV1 ("-100" "-400" "55" "66" "8" "88") ("66") False False))))
          ($_ (apt:with-file "(access (channel telegram) direct open)\n(access (channel telegram) groups allowlist)"))
          ($_ (nt:require admission-reports-open-direct-and-all-groups
                (nt:equal (ap:telegram-admission) (TelegramAdmissionV1 () () True True))))
          ($_ (ap:install (ap:telegram-facts-from ("7") ("7" "42") () True)))
          ($_ (nt:require admission-from-channel-settings-adds-nothing
                (nt:equal (ap:telegram-admission) (TelegramAdmissionV1 () () True False)))))
     True))

; The facts file: loaded on first use, reloaded when it changes, a refused
; one leaves the facts in force as they were.
(= (apt:reloading $root)
   (let* (($file (path:join $root "access.metta"))
          ($_ (fs:write $file "(owner (sender telegram \"61\"))\n"))
          ($_ (nt:require file-loaded (nt:equal (ap:ensure-from $file) True)))
          ($_ (nt:require file-owner-in-force (nt:equal (ap:owner? telegram "61" "") True)))
          ($_ (fs:write $file "(owner (sender telegram \"62\"))\n(owner (sender telegram \"63\"))\n"))
          ($_ (ap:ensure-from $file))
          ($_ (nt:require changed-file-reloaded
                (nt:equal ((ap:owner? telegram "61" "") (ap:owner? telegram "62" "")) (False True))))
          ($_ (fs:write $file "(owner (sender telegram 64))\n"))
          ($_ (ap:ensure-from $file))
          ($_ (nt:require refused-file-keeps-facts
                (nt:equal ((ap:owner? telegram "62" "") (ap:owner? telegram "64" "")) (True False))))
          ($_ (fs:write $file "(owner (sender telegram \"65\"))\n"))
          ($_ (ap:ensure-from $file))
          ($_ (nt:require valid-again-reloaded (nt:equal (ap:owner? telegram "65" "") True)))
          ($_ (nt:require missing-file-refused
                (case (ap:load-file (path:join $root "absent.metta"))
                  (((AccessPolicyInvalidV1 $why $where) True) ($other False))))))
     True))

(= (apt:run)
   (let* (($root (nt:join (nt:required-env "NATIVE_TEST_ARTIFACTS") (str:concat "access-policy-" (system:uuid4-hex))))
          ($_ (fs:make-directories $root 504))
          ($_ (apt:reading)) ($_ (apt:telegram-defaults)) ($_ (apt:layers)) ($_ (apt:wake-recorded))
          ($_ (apt:admission)) ($_ (apt:reloading $root)))
     AccessPolicyPassed))
!(nt:require access-policy-completed (nt:equal (collapse (apt:run)) (AccessPolicyPassed)))
!(println! NATIVE_ACCESS_POLICY_PASSED)
