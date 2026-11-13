; Growth workload: deciding messages from senders the facts do not name, with
; n listed senders, n members of eight named groups that carry rules, and n
; group layers in force. A decision looks through what it must, so its cost
; may follow n, but no faster.
!(import! &self ../growth_support.metta)
!(import! &self ../../channels/access_policy.metta)
(= (apg:facts $i $n $kept)
   (if (== $i $n) $kept
     (let* (($id (repr $i))
            ($member (repr (+ 1000000 $i)))
            ($name (str:concat "g" (repr (% $i 8))))
            ($group (str:concat "-" $id))
            ($one (ap-allow-sender telegram telegram $id 1))
            ($two (ap-member $name telegram $member 1))
            ($three (ap-access (ApGroup telegram $group) ApGroups ApOpen 1 $i)))
       (apg:facts (+ $i 1) $n (cons-atom $one (cons-atom $two (cons-atom $three $kept)))))))
(= (apg:rules $i $kept)
   (if (== $i 8) $kept
     (let* (($name (str:concat "g" (repr $i)))
            ($rule (ap-named-rule ApGeneric ApGroups $name (ApRuleV1 ApRecord ApWakeOnMention "" "") 1 $i)))
       (apg:rules (+ $i 1) (cons-atom $rule $kept)))))
(= (apg:decisions $k)
   (if (== $k 0) (Decided)
     (let* (($a (ap:decide telegram "stranger" "" False))
            ($b (ap:decide telegram "stranger" "-1" True)))
       (apg:decisions (- $k 1)))))
!(let $installed (ap:install (apg:rules 0 (apg:facts 0 (growth:size) ())))
   (growth:measure (apg:decisions 10)))
