; Who reaches the agent through a chat channel, and how: the access policy of
; MeTTapedia's AccessPolicy module, decided here from facts.
;
; Two sources give the facts. A channel's own settings say how it has always
; behaved (for Telegram: the operators, the allowed chats, the wake peers and
; allow_private), and an optional facts file, METTACLAW_ACCESS_POLICY_PATH,
; states anything else, written as config/access.metta.example shows. Where
; both state the same setting of the same layer, the file's wins; where the
; file states one twice, its last statement wins.
;
; (ap:decide CHANNEL SENDER GROUP MENTIONED) decides one message, with GROUP
; "" for a direct message; (ap:decide CHANNEL SENDER GROUP MENTIONED ROLE)
; also takes the role the channel's service declared for the sender: its
; operators are owners and its wake peers are in the channel's wake peers'
; group, whatever else the facts say. The decision is
;   (AccessDecisionV1 VISIBILITY NOTE REASON TIER PROVENANCE)
; VISIBILITY is record or drop. NOTE is how the message may wake the agent:
; sender-wake (at once, with the full banked budget and REASON), addressed (at
; once), quiet (paced like any other message) or silent (never by itself; it
; waits for the next wake). TIER is an arming tier, or "" for the sender's
; own. PROVENANCE is "" for an owner or a listed sender, otherwise the label
; the agent's context shows with the message, such as "untrusted stranger".
;
; Facts are read as data and never evaluated: shapes are checked by their
; head symbols, and everything is kept in this module's own constructors.

!(import! &self str)
!(import! &self fs)
!(import! &self system)

;; ---------------------------------------------------------------- helpers

(= (ap:env $name $default) (system:getenv-or-default $name $default))

;; A list's first element and the rest, held in this module's own pair:
;; a term built as ($head $tail) is a call when $head is a function's name.
(= (ap:uncons $items)
   (let $head (car-atom $items)
     (let $tail (cdr-atom $items) (ApPair $head $tail))))

(= (ap:reverse $items $done)
   (if (== $items ()) $done
     (let (ApPair $head $tail) (ap:uncons $items)
       (ap:reverse $tail (cons-atom $head $done)))))

(= (ap:append $front $back) (ap:reverse (ap:reverse $front ()) $back))

(= (ap:member? $x $items)
   (if (== $items ()) False
     (let (ApPair $head $tail) (ap:uncons $items)
       (if (== $x $head) True (ap:member? $x $tail)))))

(= (ap:any? $pattern-results) (not (== $pattern-results ())))

(= (ap:get $name $default)
   (case (collapse (match &self (ap-state $name $value) $value))
     ((() $default)
      (($value) $value)
      ($many (car-atom $many)))))
(= (ap:set $name $value)
   (let $old (collapse (match &self (ap-state $name $v) (ap-state $name $v)))
     (let $gone (ap:remove-all $old)
       (let $added (add-atom &self (ap-state $name $value)) $value))))
(= (ap:remove-all $atoms)
   (if (== $atoms ()) True
     (let (ApPair $head $tail) (ap:uncons $atoms)
       (let $gone (remove-atom &self $head) (ap:remove-all $tail)))))

; Comma-separated identifiers, trimmed, empty ones left out.
(= (ap:ids $raw) (ap:nonempty (str:split "," $raw) ()))
(= (ap:nonempty $parts $kept)
   (if (== $parts ()) (ap:reverse $kept ())
     (let (ApPair $part $rest) (ap:uncons $parts)
       (let $id (str:trim $part)
         (ap:nonempty $rest (if (== $id "") $kept (cons-atom $id $kept)))))))

(= (ap:truthy? $text) (ap:member? (str:lower (str:trim $text)) ("1" "true" "yes" "on")))

;; ----------------------------------------------------------- fact shapes

(= (ap:expression? $x) (== (get-metatype $x) Expression))
(= (ap:text? $x) (== (get-type $x) String))
(= (ap:form? $x $head $arity)
   (if (ap:expression? $x)
     (if (== (size-atom $x) (+ $arity 1))
       (let $first (car-atom $x) (if (== (get-metatype $first) Symbol) (== $first $head) False))
       False)
     False))
(= (ap:arg $x $i) (if (== $i 1) (car-atom (cdr-atom $x)) (ap:arg (cdr-atom $x) (- $i 1))))

(= (ap:channels) (telegram slack signal irc mattermost console))
(= (ap:channel? $c) (if (== (get-metatype $c) Symbol) (ap:member? $c (ap:channels)) False))

(= (ap:sender-of $x)
   (if (ap:form? $x sender 2)
     (let $c (ap:arg $x 1)
       (let $id (ap:arg $x 2)
         (if (and (ap:channel? $c) (ap:text? $id)) (ApOk (ApSender $c $id))
           (ApBad "a sender is (sender CHANNEL \"ID\")"))))
     (ApBad "a sender is (sender CHANNEL \"ID\")")))

(= (ap:named-of $x)
   (if (ap:form? $x named-group 1)
     (let $name (ap:arg $x 1)
       (if (ap:text? $name) (ApOk $name) (ApBad "a named group is (named-group \"NAME\")")))
     (ApBad "a named group is (named-group \"NAME\")")))

; The generic allowlist, or one channel's.
(= (ap:list-of $x)
   (if (== $x everywhere) (ApOk ApEverywhere)
     (if (ap:form? $x on 1)
       (let $c (ap:arg $x 1) (if (ap:channel? $c) (ApOk $c) (ApBad "an allowlist is everywhere or (on CHANNEL)")))
       (ApBad "an allowlist is everywhere or (on CHANNEL)"))))

(= (ap:scope-of $x)
   (if (== $x generic) (ApOk ApGeneric)
     (if (ap:form? $x channel 1)
       (let $c (ap:arg $x 1) (if (ap:channel? $c) (ApOk (ApChannel $c)) (ap:bad-scope)))
       (if (ap:form? $x account 2)
         (let $c (ap:arg $x 1)
           (let $name (ap:arg $x 2)
             (if (and (ap:channel? $c) (ap:text? $name)) (ApOk (ApAccount $c $name)) (ap:bad-scope))))
         (if (ap:form? $x group 2)
           (let $c (ap:arg $x 1)
             (let $id (ap:arg $x 2)
               (if (and (ap:channel? $c) (ap:text? $id)) (ApOk (ApGroup $c $id)) (ap:bad-scope))))
           (ap:bad-scope))))))
(= (ap:bad-scope) (ApBad "a layer is generic, (channel C), (account C \"NAME\") or (group C \"ID\")"))

(= (ap:kind-of $x)
   (if (== $x direct) (ApOk ApDirect)
     (if (== $x groups) (ApOk ApGroups) (ApBad "a kind is direct or groups"))))

(= (ap:access-of $x)
   (if (== $x open) (ApOk ApOpen)
     (if (== $x allowlist) (ApOk ApAllowlist)
       (if (== $x disabled) (ApOk ApDisabled) (ApBad "access is open, allowlist or disabled")))))

(= (ap:class-key-of $x)
   (if (== $x owner) (ApOk ApOwner)
     (if (== $x listed) (ApOk ApListed)
       (if (== $x stranger) (ApOk ApStranger) (ApBad "a class is owner, listed or stranger")))))

; (rule VISIBILITY WAKE) or (rule VISIBILITY WAKE REASON TIER).
(= (ap:rule-of $x)
   (if (ap:form? $x rule 2) (ap:rule-parts (ap:arg $x 1) (ap:arg $x 2) no-reason tier-default)
     (if (ap:form? $x rule 4) (ap:rule-parts (ap:arg $x 1) (ap:arg $x 2) (ap:arg $x 3) (ap:arg $x 4))
       (ap:bad-rule))))
(= (ap:bad-rule)
   (ApBad "a rule is (rule record|drop wake-never|wake-on-mention|wake-always [no-reason|(reason \"TEXT\") tier-default|(tier \"full|mid|light\")])"))
(= (ap:rule-parts $v $w $r $t)
   (let $visibility (if (== $v record) ApRecord (if (== $v drop) ApDrop ApInvalid))
     (let $wake (if (== $w wake-never) ApWakeNever
                  (if (== $w wake-on-mention) ApWakeOnMention
                    (if (== $w wake-always) ApWakeAlways ApInvalid)))
       (let $reason (if (== $r no-reason) ""
                      (if (ap:form? $r reason 1)
                        (let $text (ap:arg $r 1) (if (ap:text? $text) (if (== $text "") ApInvalid $text) ApInvalid))
                        ApInvalid))
         (let $tier (if (== $t tier-default) ""
                      (if (ap:form? $t tier 1)
                        (let $name (ap:arg $t 1) (if (ap:member? $name ("full" "mid" "light")) $name ApInvalid))
                        ApInvalid))
           (if (ap:member? ApInvalid ($visibility $wake $reason $tier)) (ap:bad-rule)
             (ApOk (ApRuleV1 $visibility $wake $reason $tier))))))))

; The first refusal among RESULTS, or ApNone.
(= (ap:first-bad $results)
   (if (== $results ()) ApNone
     (let (ApPair $head $tail) (ap:uncons $results)
       (case $head
         (((ApBad $why) (ApBad $why))
          ($ok (ap:first-bad $tail)))))))

; One fact of the file, as this module keeps it, or why it is refused.
(= (ap:fact-of $x $rank $seq)
   (if (ap:form? $x owner 1)
     (case (ap:sender-of (ap:arg $x 1))
       (((ApOk (ApSender $c $id)) (ApOk (ap-owner $c $id $rank)))
        ($bad $bad)))
     (if (ap:form? $x allow-sender 2)
       (case ((ap:list-of (ap:arg $x 1)) (ap:sender-of (ap:arg $x 2)))
         ((((ApOk $list) (ApOk (ApSender $c $id)))
           (if (or (== $list ApEverywhere) (== $list $c)) (ApOk (ap-allow-sender $list $c $id $rank))
             (ApBad "a channel's allowlist names that channel's senders")))
          ($both (ap:first-bad $both))))
       (if (ap:form? $x allow-named-group 2)
         (case ((ap:list-of (ap:arg $x 1)) (ap:named-of (ap:arg $x 2)))
           ((((ApOk $list) (ApOk $name)) (ApOk (ap-allow-named $list $name $rank)))
            ($both (ap:first-bad $both))))
         (if (ap:form? $x group-member 2)
           (case ((ap:named-of (ap:arg $x 1)) (ap:sender-of (ap:arg $x 2)))
             ((((ApOk $name) (ApOk (ApSender $c $id))) (ApOk (ap-member $name $c $id $rank)))
              ($both (ap:first-bad $both))))
           (if (ap:form? $x access 3)
             (case ((ap:scope-of (ap:arg $x 1)) (ap:kind-of (ap:arg $x 2)) (ap:access-of (ap:arg $x 3)))
               ((((ApOk $scope) (ApOk $kind) (ApOk $access)) (ApOk (ap-access $scope $kind $access $rank $seq)))
                ($all (ap:first-bad $all))))
             (if (ap:form? $x rule-for 4)
               (case ((ap:scope-of (ap:arg $x 1)) (ap:kind-of (ap:arg $x 2)) (ap:class-key-of (ap:arg $x 3)) (ap:rule-of (ap:arg $x 4)))
                 ((((ApOk $scope) (ApOk $kind) (ApOk $key) (ApOk $rule)) (ApOk (ap-rule $scope $kind $key $rule $rank $seq)))
                  ($all (ap:first-bad $all))))
               (if (ap:form? $x named-rule 4)
                 (case ((ap:scope-of (ap:arg $x 1)) (ap:kind-of (ap:arg $x 2)) (ap:named-of (ap:arg $x 3)) (ap:rule-of (ap:arg $x 4)))
                   ((((ApOk $scope) (ApOk $kind) (ApOk $name) (ApOk $rule)) (ApOk (ap-named-rule $scope $kind $name $rule $rank $seq)))
                    ($all (ap:first-bad $all))))
                 (ApBad "facts are owner, allow-sender, allow-named-group, group-member, access, rule-for and named-rule")))))))))

(= (ap:facts-of $atoms $rank $seq $kept)
   (if (== $atoms ()) (AccessFactsV1 (ap:reverse $kept ()))
     (let (ApPair $atom $rest) (ap:uncons $atoms)
       (case (ap:fact-of $atom $rank $seq)
         (((ApOk $fact) (ap:facts-of $rest $rank (+ $seq 1) (cons-atom $fact $kept)))
          ((ApBad $why) (AccessPolicyInvalidV1 $why (repr $atom))))))))

;; ------------------------------------------------------- the facts file

; LINE up to its comment: a ; outside a string starts one. Strings do not
; span lines, and a backslash before a quote keeps the string open.
(= (ap:code-of $line)
   (case (str:find $line ";")
     (((TextNotFound $n) $line)
      ($at (ap:code-parts (str:split "\"" $line) False ())))))
(= (ap:code-parts $segments $inside $kept)
   (if (== $segments ()) (str:join "\"" (ap:reverse $kept ()))
     (let (ApPair $segment $rest) (ap:uncons $segments)
       (if $inside
         (ap:code-parts $rest (str:ends-with $segment "\\") (cons-atom $segment $kept))
         (case (str:find $segment ";")
           (((TextNotFound $n) (ap:code-parts $rest True (cons-atom $segment $kept)))
            ($at (str:join "\"" (ap:reverse (cons-atom (str:byte-slice $segment 0 $at) $kept) ())))))))))
(= (ap:code-lines $lines $kept)
   (if (== $lines ()) (ap:reverse $kept ())
     (let (ApPair $line $rest) (ap:uncons $lines)
       (ap:code-lines $rest (cons-atom (ap:code-of $line) $kept)))))

; The text of CODE outside its strings.
(= (ap:bare $segments $inside $kept)
   (if (== $segments ()) (str:join "" (ap:reverse $kept ()))
     (let (ApPair $segment $rest) (ap:uncons $segments)
       (if $inside
         (ap:bare $rest (str:ends-with $segment "\\") $kept)
         (ap:bare $rest True (cons-atom $segment $kept))))))
(= (ap:found? $text $needle)
   (case (str:find $text $needle) (((TextNotFound $n) False) ($at True))))

; The facts TEXT states, each checked. Nothing in it is evaluated: a file
; with a directive (!) or a variable ($) outside its strings is refused
; before it is read as atoms.
(= (ap:parse-facts $text)
   (let $code (str:join "\n" (ap:code-lines (str:split "\n" $text) ()))
     (let $bare (ap:bare (str:split "\"" $code) False ())
       (if (or (ap:found? $bare "!") (ap:found? $bare "$"))
         (AccessPolicyInvalidV1 "a facts file states facts only: no ! or $ outside strings" "")
         (case (catch (parse (str:concat "(" (str:concat $code "\n)"))))
           (((Error $call $why) (AccessPolicyInvalidV1 "the facts do not parse" (repr $why)))
            ($atoms (if (ap:expression? $atoms) (ap:facts-of $atoms 1 0 ())
                      (AccessPolicyInvalidV1 "the facts do not parse" (repr $atoms))))))))))

(= (ap:path) (ap:env "METTACLAW_ACCESS_POLICY_PATH" ""))

; What identifies the facts file's present state: a change reloads it.
(= (ap:file-stamp $path)
   (if (== $path "") ApNoFile
     (case (catch (fs:metadata $path follow))
       (((fs:info regular $size $identity) (ApFile $size $identity))
        ($other ApUnreadable)))))

(= (ap:load-file $path)
   (case (catch (fs:read-text-result $path 0 "raw"))
     ((("ok" $text) (ap:parse-facts $text))
      ($error (AccessPolicyInvalidV1 "the facts file cannot be read" $path)))))

;; ------------------------------------------------- a channel's own facts

; Telegram as it has always behaved: the operators are owners, the allowed
; private chats are listed senders, the allowed groups are open, other groups
; are not heard, allow_private opens direct messages, and operators and wake
; peers wake the agent at once with their reasons. Pacing stays the channel's.
(= (ap:telegram-peers) "telegram-wake-peers")
(= (ap:telegram-facts)
   (ap:telegram-facts-from (ap:ids (ap:env "METTACLAW_TELEGRAM_OPERATOR_IDS" ""))
     (ap:append (ap:ids (ap:env "METTACLAW_TELEGRAM_PRIMARY_CHAT_ID" ""))
                (ap:ids (ap:env "METTACLAW_TELEGRAM_ALLOWED_CHAT_IDS" "")))
     (ap:ids (ap:env "METTACLAW_TELEGRAM_WAKE_IDS" ""))
     (ap:truthy? (ap:env "METTACLAW_TELEGRAM_ALLOW_PRIVATE" ""))))
(= (ap:telegram-facts-from $operators $chats $peers $allow-private)
         (let $direct (if $allow-private ApOpen ApAllowlist)
           (let $operator (ApRuleV1 ApRecord ApWakeAlways "operator message" "")
             (let $peer (ApRuleV1 ApRecord ApWakeAlways "peer message" "")
               (let $layer (ApChannel telegram)
                (let $group-name (ap:telegram-peers)
                 (ap:append (ap:telegram-owner-facts $operators ())
                   (ap:append (ap:telegram-chat-facts $chats ())
                     (ap:append (ap:telegram-peer-facts $peers $group-name ())
                       ((ap-access $layer ApDirect $direct 0 0)
                        (ap-access $layer ApGroups ApDisabled 0 0)
                        (ap-rule $layer ApDirect ApOwner $operator 0 0)
                        (ap-rule $layer ApGroups ApOwner $operator 0 0)
                        (ap-rule $layer ApGroups ApListed (ApRuleV1 ApRecord ApWakeAlways "" "") 0 0)
                        (ap-allow-named telegram $group-name 0)
                        (ap-named-rule $layer ApDirect $group-name $peer 0 0)
                        (ap-named-rule $layer ApGroups $group-name $peer 0 0)))))))))))
(= (ap:telegram-owner-facts $ids $kept)
   (if (== $ids ()) $kept
     (let (ApPair $id $rest) (ap:uncons $ids)
       (ap:telegram-owner-facts $rest (cons-atom (ap-owner telegram $id 0) $kept)))))
; A group's chat id is negative; a private chat's is its user's id.
(= (ap:telegram-chat-facts $ids $kept)
   (if (== $ids ()) $kept
     (let (ApPair $id $rest) (ap:uncons $ids)
       (ap:telegram-chat-facts $rest
         (cons-atom (if (str:starts-with $id "-") (ap-access (ApGroup telegram $id) ApGroups ApOpen 0 0)
                      (ap-allow-sender telegram telegram $id 0))
                    $kept)))))
(= (ap:telegram-peer-facts $ids $name $kept)
   (if (== $ids ()) $kept
     (let (ApPair $id $rest) (ap:uncons $ids)
       (ap:telegram-peer-facts $rest $name (cons-atom (ap-member $name telegram $id 0) $kept)))))

(= (ap:channel-facts) (ap:telegram-facts))

;; ------------------------------------------------------- facts in force

(= (ap:stored)
   (collapse (superpose ((match &self (ap-owner $a $b $c) (ap-owner $a $b $c))
                         (match &self (ap-allow-sender $a $b $c $d) (ap-allow-sender $a $b $c $d))
                         (match &self (ap-allow-named $a $b $c) (ap-allow-named $a $b $c))
                         (match &self (ap-member $a $b $c $d) (ap-member $a $b $c $d))
                         (match &self (ap-access $a $b $c $d $e) (ap-access $a $b $c $d $e))
                         (match &self (ap-rule $a $b $c $d $e $f) (ap-rule $a $b $c $d $e $f))
                         (match &self (ap-named-rule $a $b $c $d $e $f) (ap-named-rule $a $b $c $d $e $f))))))
(= (ap:store $facts)
   (let $gone (ap:remove-all (ap:stored)) (ap:add-all $facts)))
(= (ap:add-all $facts)
   (if (== $facts ()) True
     (let (ApPair $fact $rest) (ap:uncons $facts)
       (let $added (add-atom &self $fact) (ap:add-all $rest)))))

; The facts in force, loaded on first use and again whenever the facts file
; changes. A file that is not valid is reported once; the facts in force stay
; as they were, which at first are the channels' own.
(= (ap:ensure) (ap:ensure-from (ap:path)))
(= (ap:ensure-from $path)
   (let $stamp (ap:file-stamp $path)
     (if (== $stamp (ap:get loaded none)) True
       (if (== $stamp (ap:get refused none)) True
         (let $loaded (if (== $stamp ApNoFile) (AccessFactsV1 ()) (ap:load-file $path))
           (case $loaded
             (((AccessFactsV1 $file)
               (let $stored (ap:store (ap:append (ap:channel-facts) $file))
                 (let $s (ap:set loaded $stamp) True)))
              ($refusal
               (let $s (ap:set refused $stamp)
                 (let $said (println! (ACCESS_POLICY_REFUSED $refusal))
                   (if (== (ap:get loaded none) none)
                     (let $stored (ap:store (ap:channel-facts)) (let $l (ap:set loaded ApChannelOnly) True))
                     True)))))))))))

;; Install FACTS as the ones in force, as if the facts file had stated them.
(= (ap:install $facts)
   (let $stored (ap:store $facts)
     (let $l (ap:set loaded (ap:file-stamp (ap:path)))
       (let $r (ap:set refused none) True))))

;; ------------------------------------------------------------- deciding

(= (ap:account $channel)
   (if (== $channel telegram) (ap:env "METTACLAW_TELEGRAM_BOT_USERNAME" "") ""))

; The layers that hold for a message, innermost first.
(= (ap:scopes $channel $group)
   (let $account (ap:account $channel)
     (let $outer (if (== $account "") ((ApChannel $channel) ApGeneric)
                   ((ApAccount $channel $account) (ApChannel $channel) ApGeneric))
       (if (== $group "") $outer (cons-atom (ApGroup $channel $group) $outer)))))

; Of statements of one field at one layer, the file's, and its last.
(= (ap:preferred $found) (ap:preferred-from $found -1 -1 ApNone))
(= (ap:preferred-from $found $rank $seq $best)
   (if (== $found ()) $best
     (let (ApPair (ApStated $r $s $value) $rest) (ap:uncons $found)
       (if (or (> $r $rank) (and (== $r $rank) (>= $s $seq)))
         (ap:preferred-from $rest $r $s $value)
         (ap:preferred-from $rest $rank $seq $best)))))

(= (ap:access-in $scopes $kind)
   (if (== $scopes ()) ApAllowlist
     (let (ApPair $scope $rest) (ap:uncons $scopes)
       (let $found (collapse (match &self (ap-access $scope $kind $access $rank $seq) (ApStated $rank $seq $access)))
         (if (== $found ()) (ap:access-in $rest $kind) (ap:preferred $found))))))

(= (ap:default-rule $kind $key)
   (if (== $key ApStranger) (ApRuleV1 ApDrop ApWakeNever "" "")
     (if (and (== $key ApListed) (== $kind ApGroups)) (ApRuleV1 ApRecord ApWakeOnMention "" "")
       (ApRuleV1 ApRecord ApWakeAlways "" ""))))

(= (ap:class-rule $scopes $kind $key)
   (if (== $scopes ()) (ap:default-rule $kind $key)
     (let (ApPair $scope $rest) (ap:uncons $scopes)
       (let $found (collapse (match &self (ap-rule $scope $kind $key $rule $rank $seq) (ApStated $rank $seq $rule)))
         (if (== $found ()) (ap:class-rule $rest $kind $key) (ap:preferred $found))))))

; Named groups' rules in force, in order: an inner layer's first; within a
; layer, the file's first, each in the order written.
(= (ap:named-rules $scopes $kind)
   (if (== $scopes ()) ()
     (let (ApPair $scope $rest) (ap:uncons $scopes)
       (let $file (sort-atom (collapse (match &self (ap-named-rule $scope $kind $name $rule 1 $seq) (ApNamedRule $seq $name $rule))))
         (let $own (sort-atom (collapse (match &self (ap-named-rule $scope $kind $name $rule 0 $seq) (ApNamedRule $seq $name $rule))))
           (ap:append (ap:append $file $own) (ap:named-rules $rest $kind)))))))

; The wake peers' group a channel's service declares senders into.
(= (ap:peers-of $channel) (if (== $channel telegram) (ap:telegram-peers) ""))

; ROLE is what the channel's service declared for the sender: "operator",
; "wake-peer", or anything else for nothing.
(= (ap:owner? $channel $sender $role)
   (if (== $role "operator") True
     (ap:any? (collapse (match &self (ap-owner $channel $sender $rank) True)))))

(= (ap:member-of? $name $channel $sender $role)
   (if (and (== $role "wake-peer") (== $name (ap:peers-of $channel))) True
     (ap:any? (collapse (match &self (ap-member $name $channel $sender $rank) True)))))

(= (ap:any-member? $names $channel $sender $role)
   (if (== $names ()) False
     (let (ApPair $name $rest) (ap:uncons $names)
       (if (ap:member-of? $name $channel $sender $role) True (ap:any-member? $rest $channel $sender $role)))))

; Listed by the generic allowlist or the channel's, directly or through a
; named group.
(= (ap:listed? $channel $sender $role)
   (if (ap:any? (collapse (match &self (ap-allow-sender ApEverywhere $channel $sender $rank) True))) True
     (if (ap:any? (collapse (match &self (ap-allow-sender $channel $channel $sender $rank) True))) True
       (ap:any-member? (collapse (superpose ((match &self (ap-allow-named ApEverywhere $name $rank) $name)
                                             (match &self (ap-allow-named $channel $name $rank) $name))))
                       $channel $sender $role))))

(= (ap:named-class $rules $channel $sender $role)
   (if (== $rules ()) ApNoMatch
     (let (ApPair (ApNamedRule $seq $name $rule) $rest) (ap:uncons $rules)
       (if (ap:member-of? $name $channel $sender $role) (ApNamedClass $name $rule)
         (ap:named-class $rest $channel $sender $role)))))

(= (ap:class $channel $sender $scopes $kind $role)
   (if (ap:owner? $channel $sender $role) ApOwnerClass
     (case (ap:named-class (ap:named-rules $scopes $kind) $channel $sender $role)
       ((ApNoMatch (if (ap:listed? $channel $sender $role) ApListedClass ApStrangerClass))
        ($named $named)))))

; The rule a layer's access gives a class: disabled drops everything, and open
; treats a stranger as listed.
(= (ap:effective $access $class $scopes $kind)
   (if (== $access ApDisabled) (ApRuleV1 ApDrop ApWakeNever "" "")
     (case $class
       ((ApOwnerClass (ap:class-rule $scopes $kind ApOwner))
        ((ApNamedClass $name $rule) $rule)
        (ApListedClass (ap:class-rule $scopes $kind ApListed))
        (ApStrangerClass (ap:class-rule $scopes $kind (if (== $access ApOpen) ApListed ApStranger)))))))

(= (ap:provenance $class $trusted)
   (if $trusted ""
     (case $class
       (((ApNamedClass $name $rule) (format-args "untrusted, in named group {}" ($name)))
        ($other "untrusted stranger")))))

(= (ap:decision $rule $class $trusted $mentioned $direct)
   (let (ApRuleV1 $visibility $wake $reason $tier) $rule
     (if (== $visibility ApDrop) (AccessDecisionV1 drop silent "" "" "")
       (let $eligible (case $wake
                        ((ApWakeNever False)
                         (ApWakeOnMention (or $direct $mentioned))
                         (ApWakeAlways True)))
         (let $note (if (not $eligible) silent
                      (if (not (== $reason "")) sender-wake (if $mentioned addressed quiet)))
           (AccessDecisionV1 record $note (if (== $note sender-wake) $reason "") $tier
             (ap:provenance $class $trusted)))))))

(= (ap:decide $channel $sender $group $mentioned) (ap:decide $channel $sender $group $mentioned ""))
(= (ap:decide $channel $sender $group $mentioned $role)
   (let $ready (ap:ensure)
     (let $kind (if (== $group "") ApDirect ApGroups)
       (let $scopes (ap:scopes $channel $group)
         (let $class (ap:class $channel $sender $scopes $kind $role)
           (let $trusted (if (ap:owner? $channel $sender $role) True (ap:listed? $channel $sender $role))
             (ap:decision (ap:effective (ap:access-in $scopes $kind) $class $scopes $kind)
                          $class $trusted $mentioned (== $group ""))))))))

(= (ap:owner-of? $channel $sender) (let $ready (ap:ensure) (ap:owner? $channel $sender "")))

;; ------------------------------------------- what Telegram's service admits

; What the facts file adds to the chats Telegram's channel service admits,
; beyond the ones its own settings already list: the file's owners and listed
; senders, the members of its named groups, and its groups that are heard;
; and whether direct messages are open. The service cannot admit every group
; yet, so a file that hears every group is reported as all_groups.
(= (ap:file-ids $pattern-results) (ap:unique (sort-atom $pattern-results) ()))
(= (ap:unique $sorted $kept)
   (if (== $sorted ()) (ap:reverse $kept ())
     (let (ApPair $x $rest) (ap:uncons $sorted)
       (ap:unique $rest (if (and (not (== $kept ())) (== (car-atom $kept) $x)) $kept (cons-atom $x $kept))))))

(= (ap:telegram-admission)
   (let $ready (ap:ensure)
     (let $owners (ap:file-ids (collapse (match &self (ap-owner telegram $id 1) $id)))
       (let $senders (collapse (superpose ((match &self (ap-allow-sender $list telegram $id 1) $id)
                                           (match &self (ap-member $name telegram $id 1) $id))))
         (let $groups (collapse (superpose ((match &self (ap-access (ApGroup telegram $id) $kind $access 1 $seq) $id)
                                            (match &self (ap-rule (ApGroup telegram $id) $kind $key $rule 1 $seq) $id)
                                            (match &self (ap-named-rule (ApGroup telegram $id) $kind $name $rule 1 $seq) $id))))
           (let $heard (ap:heard-groups (ap:file-ids $groups) ())
             (let $chats (ap:file-ids (ap:append $owners (ap:append $senders $heard)))
               (let $outer (ap:scopes telegram "")
                 (TelegramAdmissionV1 $chats $owners
                   (== (ap:access-in $outer ApDirect) ApOpen)
                   (not (== (ap:access-in $outer ApGroups) ApDisabled)))))))))))
(= (ap:heard-groups $ids $kept)
   (if (== $ids ()) (ap:reverse $kept ())
     (let (ApPair $id $rest) (ap:uncons $ids)
       (ap:heard-groups $rest
         (if (== (ap:access-in (ap:scopes telegram $id) ApGroups) ApDisabled) $kept (cons-atom $id $kept))))))
