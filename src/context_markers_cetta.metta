; One process-local owner for context change streaks. Restart resets them.
!(import! &self ./state_ledger.metta)
!(bind! &context-marks (new-state ()))
(= (mark:find $rows $name)
   (if (== $rows ()) none
     (let (($key $digest $count) $rest) (decons-atom $rows)
       (if (== $key $name) (MarkV1 $digest $count) (mark:find $rest $name)))))
(= (mark:without $rows $name)
   (if (== $rows ()) ()
     (let (($key $digest $count) $rest) (decons-atom $rows)
       (let $tail (mark:without $rest $name)
         (if (== $key $name) $tail (cons-atom ($key $digest $count) $tail))))))
(= (mark:context $name $text)
   (let* (($digest (str:sha256 $text)) ($rows (get-state &context-marks))
          ($old (mark:find $rows $name))
          ($count (case $old (((MarkV1 $same $n) (if (== $same $digest) (+ $n 1) 0)) ($none 0))))
          ($_ (change-state! &context-marks
                (cons-atom ($name $digest $count) (mark:without $rows $name)))))
     (if (> $count 0)
       (str:join "" (" (unchanged for " (repr $count) " turn" (if (== $count 1) "" "s") ")"))
       (if (== $old none) "" " (changed)"))))
(= (mark:pins $body)
   (if (== $body "(no pins)") $body
     (str:join "" ((mark:context "pins-context" $body) " " $body))))
