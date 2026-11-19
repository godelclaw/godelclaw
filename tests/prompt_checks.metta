; Checks of a rendered request, for the runtime probes that read one: the same
; rules as the Python check modules they called before that host was retired.
(= (pc:has? $text $part) (case (str:find $text $part) (((TextNotFound $_) False) ($at True))))
(= (pc:count $text $part) (- (size-atom (str:split $part $text)) 1))
(= (pc:sum $numbers) (if (== $numbers ()) 0 (+ (car-atom $numbers) (pc:sum (cdr-atom $numbers)))))
(= (pc:member? $x $items) (if (== $items ()) False (if (== (car-atom $items) $x) True (pc:member? $x (cdr-atom $items)))))
(= (pc:text $value) (if (== (get-type $value) String) $value (utils:py-str ($value))))

; A role-context packet's messages as (role content) pairs, or Plain.
(= (pc:packet $text)
   (if (not (str:starts-with $text "{")) Plain
     (case (catch (json-parse $text))
       (((JsonObject $pairs)
         (if (== (json:get (JsonObject $pairs) "kind") (JsonString "role-context-v1"))
           (case (json:get (JsonObject $pairs) "messages")
             (((JsonArray $messages) (Packet (map-atom $messages $m (pc:message $m))))
              ($other Plain)))
           Plain))
        ($other Plain)))))
(= (pc:message $m)
   ((case (json:get $m "role") (((JsonString $r) $r) ($o "")))
    (case (json:get $m "content") (((JsonString $c) $c) ($o "")))))
(= (pc:with-roles $messages $roles) (filter-atom $messages $m (pc:member? (car-atom $m) $roles)))
(= (pc:contents $messages) (map-atom $messages $m (index-atom $m 1)))

; The advertisement separates every skill by exactly one line break.
(= (pc:one-skill-a-line $prompt $skills)
   (let $text (case (pc:packet $prompt)
                (((Packet $messages) (str:join "\n\n" (pc:contents (pc:with-roles $messages ("system")))))
                 (Plain $prompt)))
     (case (str:find $text "ADVERTISED_CAPABILITIES:")
       (((TextNotFound $_) False)
        ($start
          ; str:find answers a byte offset.
          (let* (($rest (str:byte-slice $text $start (str:byte-length $text)))
                 ($section (case (str:find $rest "\n\n") (((TextNotFound $_) $rest) ($end (str:byte-slice $rest 0 $end)))))
                 ($names (map-atom $skills $s (pc:text $s)))
                 ($inner (pc:sum (map-atom $names $s (pc:count (string-safe $s) "_newline_")))))
            (and (> (size-atom $names) 1)
                 (== (pc:count $section "_newline_") (+ (- (size-atom $names) 1) $inner)))))))))

; Two consecutive requests: the second shows the pins unchanged for exactly
; one turn, and the first shows no streak yet.
(= (pc:pins-unchanged-one-turn $first $second)
   (and (pc:has? $second "GODEL_NATIVE_ITER_PIN")
        (and (not (pc:has? $first "(unchanged for"))
             (and (pc:has? $second "(unchanged for 1 turn)") (not (pc:has? $second "(unchanged for 2"))))))

; The prompt-layout invariant that makes provider prefix caching work: two
; requests from consecutive turns agree on everything before the last cache
; boundary, and nothing that changes every turn appears there. The answer is
; the list of violations, empty when sound.
(= (pc:boundary) "_cache_boundary_")
(= (pc:head-marks) ("PROMPT:" "OUTPUT_FORMAT:" "CONTEXT_ORDER:"))
(= (pc:skill-marks) ("SKILLS:" "ADVERTISED_CAPABILITIES:"))
(= (pc:per-turn-marks)
   ("LOOPS_LEFT:" "SLEEP_INTERVAL_SECONDS:" "ACTIVE_MODE:" "ACTIVE_MODEL:" "ATTENTION_DAG:"
    "LAST_SKILL_USE_RESULTS:" "WAKE:" "TIME:" "NEW_ACTIVITY:" "CONTEXT_CERTIFICATE"))
(= (pc:missing $text $marks $label) (map-atom (filter-atom $marks $m (not (pc:has? $text $m))) $m (str:concat $label $m)))
(= (pc:present $text $marks $label) (map-atom (filter-atom $marks $m (pc:has? $text $m)) $m (str:concat "per-turn " (str:concat $m $label))))
(= (pc:any? $text $marks) (not (== (filter-atom $marks $m (pc:has? $text $m)) ())))
(= (pc:layout-violations $first $second)
   (case ((pc:packet $first) (pc:packet $second))
     ((((Packet $a) (Packet $b))
       (let* (($trusted-a (pc:with-roles $a ("system" "developer")))
              ($trusted-b (pc:with-roles $b ("system" "developer")))
              ($head (str:join "\n" (pc:contents $trusted-a))))
         (list:append (if (== $trusted-a $trusted-b) () ("authored instruction prefix differs"))
           (list:append (pc:missing $head (pc:head-marks) "instruction prefix lacks ")
             (list:append (if (pc:any? $head (pc:skill-marks)) () ("instruction prefix lacks skills"))
               (pc:present $head (pc:per-turn-marks) " in instructions"))))))
      ($plain
        (let* (($counts (map-atom ($first $second) $t (pc:count $t (pc:boundary)))))
          (if (not (== $counts (3 3))) ((boundaries $counts))
            (let* (($a (str:split (pc:boundary) $first)) ($b (str:split (pc:boundary) $second))
                   ($stable (str:join (pc:boundary) (list:take 3 $a)))
                   ($head (car-atom $a)))
              (list:append (if (== $stable (str:join (pc:boundary) (list:take 3 $b))) ()
                              ("the prefix before the last boundary differs"))
                (list:append (pc:missing $head (pc:head-marks) "head lacks ")
                  (list:append (if (pc:any? $head (pc:skill-marks)) () ("head lacks the skills"))
                    (pc:present $stable (pc:per-turn-marks) " before the last boundary")))))))))))
(= (pc:layout-sound? $first $second)
   (let $problems (pc:layout-violations $first $second)
     (if (== $problems ()) True (let $_ (println! (PROMPT_LAYOUT_VIOLATIONS $problems)) False))))
