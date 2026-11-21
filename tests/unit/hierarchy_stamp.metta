!(import! &self ../native_test_support.metta)
!(import! &self ../../src/hierarchy_store.metta)
!(import! &self (library list))
!(import! &self (library str))

; The per-subtree definition the indexed stamp replaced, kept as the reference.
(= (href:stamp $nodes $id) (href:stamp-in $nodes $id () True))
(= (href:stamp-in $nodes $id $path $root)
   (if (hm:has $id $path) (HFailure cycle $id)
     (case (hm:find $nodes $id)
       (((HNode 1 $id $r (retired $original $reason) $t $cs $ss $q) ((HSource $id $r)))
        ((HNode 1 $id $r $k $t $cs $ss $q)
         (case (href:stamp-children $nodes $cs (cons-atom $id $path))
           (((HFailure $why $at) (HFailure $why $at))
            ($tail (let $own (if $root ((HSource $id $r)) ((HSource $id $r) (HSummarySource $id $q)))
              (hm:unique (hm:append $own (hm:append $ss $tail))))))))
        ($error $error)))))
(= (href:stamp-children $nodes $cs $path)
   (if (== $cs ()) ()
     (case (href:stamp-in $nodes (car-atom $cs) $path False)
       (((HFailure $why $at) (HFailure $why $at))
        ($head (case (href:stamp-children $nodes (cdr-atom $cs) $path)
          (((HFailure $why $at) (HFailure $why $at)) ($tail (hm:append $head $tail)))))))))

(= (unit:agrees $nodes $roots)
   (foldl-atom $roots True $ok $root
     (and $ok (let* (($new (hm:stamp $nodes $root)) ($old (href:stamp $nodes $root))) (== $new $old)))))

; Hand-built: shared children, sources, a retired node with children, a node
; reached twice, cycles, a self-loop, a missing child and a duplicate id.
(= (unit:shared)
   ((HNode 1 "p" 3 project "p" ("t1" "t2") ((Src "p")) 2)
    (HNode 1 "t1" 2 task "t1" ("e1" "e2" "s") ((Src "t1")) 0)
    (HNode 1 "t2" 1 task "t2" ("s" "e3" "old") () 1)
    (HNode 1 "s" 4 episode "s" ("e4") ((Src "p") (Src "s")) 1)
    (HNode 1 "e1" 1 episode "e1" () () 0)
    (HNode 1 "e2" 1 episode "e2" () ((Src "t1")) 0)
    (HNode 1 "e3" 2 episode "e3" () () 3)
    (HNode 1 "e4" 1 episode "e4" () () 0)
    (HNode 1 "old" 5 (retired task "done") "old" ("e1" "gone") () 2)))
(= (unit:cycle)
   ((HNode 1 "a" 1 task "a" ("b") () 0) (HNode 1 "b" 1 task "b" ("x" "c") () 0)
    (HNode 1 "x" 1 episode "x" () () 0) (HNode 1 "c" 1 task "c" ("a") () 0)))
; "d" is stamped clean through "y" first; reached again under "z" it must not
; hide the cycle z -> w -> z that follows it.
(= (unit:late-cycle)
   ((HNode 1 "r" 1 project "r" ("y" "z") () 0) (HNode 1 "y" 1 task "y" ("d") () 0)
    (HNode 1 "z" 1 task "z" ("d" "w") () 0) (HNode 1 "d" 1 episode "d" () () 0)
    (HNode 1 "w" 1 task "w" ("z") () 0)))
(= (unit:broken)
   ((HNode 1 "m" 1 task "m" ("ok" "missing") () 0) (HNode 1 "ok" 1 episode "ok" () () 0)
    (HNode 1 "loop" 1 task "loop" ("loop") () 0)
    (HNode 1 "dup-parent" 1 task "dup-parent" ("dup") () 0)
    (HNode 1 "dup" 1 episode "dup" () () 0) (HNode 1 "dup" 2 episode "dup" () () 0)))

; Generated: a tree of $n nodes with two children each, where one node in 97
; also shares a later node (the reference re-walks a shared subtree on every
; path, so sharing stays sparse), and sources on some nodes. $n = 2100 gives
; more than 4,096 entries.
(= (unit:child $i $n) (if (< $i $n) ((str:concat "g" (repr $i))) ()))
(= (unit:gen-node $i $n)
   (let* (($id (str:concat "g" (repr $i)))
          ($a (+ (* 2 $i) 1)) ($b (+ (* 2 $i) 2)) ($c (if (== (% $i 97) 5) (+ $i 40) $n))
          ($cs (unique-atom (list:append (unit:child $a $n) (list:append (unit:child $b $n) (unit:child $c $n)))))
          ($ss (if (== (% $i 5) 0) ((Src $id) (Src "shared")) ()))
          ($q (% $i 3)))
     (HNode 1 $id 1 task $id $cs $ss $q)))
(= (unit:generated $n) (map-atom (list:range 0 $n) $i (unit:gen-node $i $n)))

!(let* (($shared (unit:shared))
        ($_ (nt:require shared-agrees (unit:agrees $shared ("p" "t1" "t2" "s" "old" "e1"))))
        ($_ (nt:require shared-root-value
              (== (hm:stamp $shared "t2")
                  ((HSource "t2" 1) (HSource "s" 4) (HSummarySource "s" 1) (Src "p") (Src "s")
                   (HSource "e4" 1) (HSummarySource "e4" 0) (HSource "e3" 2) (HSummarySource "e3" 3)
                   (HSource "old" 5)))))
        ($_ (nt:require cycle-agrees (unit:agrees (unit:cycle) ("a" "b" "c"))))
        ($_ (nt:require cycle-value (== (hm:stamp (unit:cycle) "a") (HFailure cycle "a"))))
        ($_ (nt:require late-cycle-agrees (unit:agrees (unit:late-cycle) ("r" "z" "y"))))
        ($_ (nt:require late-cycle-found (== (hm:stamp (unit:late-cycle) "r") (HFailure cycle "z"))))
        ($_ (nt:require broken-agrees (unit:agrees (unit:broken) ("m" "loop" "dup-parent" "dup" "absent"))))
        ($_ (nt:require missing-value (== (hm:stamp (unit:broken) "m") (HFailure missing-node "missing"))))
        ($_ (nt:require duplicate-value (== (hm:stamp (unit:broken) "dup-parent") (HFailure duplicate-node "dup"))))
        ($big (unit:generated 2100))
        ($stamp (hm:stamp $big "g0"))
        ($_ (nt:require generated-past-4096 (> (size-atom $stamp) 4096)))
        ($_ (nt:require generated-agrees (unit:agrees $big ("g0" "g1" "g17" "g999")))))
   (println! UNIT_HIERARCHY_STAMP_OK))

; A write checks only the edges it adds.
(= (unit:edges $before $after $ids) (hm:added-edges-sound $before $after $ids))
!(let* (($before (unit:shared))
        ; A recorded turn: a new leaf episode under t1.
        ($turn (hm:replace (cons-atom (HNode 1 "e9" 1 episode "e9" () () 0) $before) "t1"
                 (HNode 1 "t1" 3 task "t1" ("e1" "e2" "s" "e9") ((Src "t1")) 0)))
        ($_ (nt:require new-leaf-sound (== (unit:edges $before $turn ("e9" "t1" "p")) True)))
        ($_ (nt:require unchanged-sound (== (unit:edges $before $before ("p" "t1" "t2")) True)))
        ; e4 (under s, under t1) now holds t1: a cycle through the new edge.
        ($back (hm:replace $before "e4" (HNode 1 "e4" 2 episode "e4" ("t1") () 0)))
        ($_ (nt:require cycle-refused (== (unit:edges $before $back ("e4")) (HFailure cycle "e4"))))
        ($self (hm:replace $before "e1" (HNode 1 "e1" 2 episode "e1" ("e1") () 0)))
        ($_ (nt:require self-loop-refused (== (unit:edges $before $self ("e1")) (HFailure cycle "e1"))))
        ($dangling (hm:replace $before "t2" (HNode 1 "t2" 2 task "t2" ("s" "e3" "old" "nowhere") () 1)))
        ($_ (nt:require missing-child-refused (== (unit:edges $before $dangling ("t2")) (HFailure missing-node "nowhere"))))
        ($twice (cons-atom (HNode 1 "e1" 9 episode "e1" () () 0)
                  (hm:replace $before "t2" (HNode 1 "t2" 2 task "t2" ("s" "e3" "old" "e1") () 1))))
        ($_ (nt:require duplicate-child-refused (== (unit:edges $before $twice ("t2")) (HFailure duplicate-node "e1"))))
        ; A new child with children of its own is walked: sound when acyclic.
        ($linked (hm:replace $before "t2" (HNode 1 "t2" 2 task "t2" ("s" "e3" "old" "t1") () 1)))
        ($_ (nt:require walked-child-sound (== (unit:edges $before $linked ("t2")) True)))
        ; A cycle already stored elsewhere does not block an unrelated write.
        ($stored (list:append (unit:cycle) $before))
        ($elsewhere (hm:replace (cons-atom (HNode 1 "e9" 1 episode "e9" () () 0) $stored) "t1"
                      (HNode 1 "t1" 3 task "t1" ("e1" "e2" "s" "e9") ((Src "t1")) 0)))
        ($_ (nt:require stored-cycle-does-not-block (== (unit:edges $stored $elsewhere ("e9" "t1" "p")) True)))
        ; A retired node's children are not followed, as in the stamp.
        ($retired (hm:replace $before "old" (HNode 1 "old" 6 (retired task "done") "old" ("e1" "gone" "p") () 2)))
        ($_ (nt:require retired-not-followed (== (unit:edges $before $retired ("old")) True))))
   (println! UNIT_HIERARCHY_EDGES_OK))
