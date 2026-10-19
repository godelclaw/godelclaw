; Arming energy: how many loops a message from each sender grants. Lila's
; energy skills and the operator's /energy menu both edit one JSON file:
;   {"default": TIER, "senders": {ID: TIER}, "names": {ID: NAME},
;    "tier_loops": {TIER: N}}
; A tier is full, mid or light. Its loops come from tier_loops, else the
; environment, else 50, 30 and 10. Any other key in the file is kept.

!(import! &self str)
!(import! &self ./channels/string_compat.metta)
!(import! &self json)
!(import! &self fs)
!(import! &self system)
!(import! &self ./src/file_io)

(= (energy:env $name $default) (system:getenv-or-default $name $default))
(= (energy:path) (energy:env "METTACLAW_ENERGY_PATH" "memory/energy.json"))

;; Lists are walked with car-atom and cdr-atom and built with collapse, so no
;; list ever ends in a lazily computed tail.
(= (energy:uncons $items)
   (let $head (car-atom $items)
     (let $tail (cdr-atom $items) ($head $tail))))
(= (energy:items $items)
   (if (== $items ()) (empty)
     (superpose ((car-atom $items) (energy:items (cdr-atom $items))))))
(= (energy:items-then $items $last)
   (if (== $items ()) $last
     (superpose ((car-atom $items) (energy:items-then (cdr-atom $items) $last)))))

(= (energy:tiers) ("full" "mid" "light"))
(= (energy:tier? $tier) (or (== $tier "full") (or (== $tier "mid") (== $tier "light"))))

;; ------------------------------------------------------------------ JSON

(= (energy:pairs $object)
   (case $object
     (((JsonObject $pairs) $pairs)
      ($other ()))))
(= (energy:at $object $key) (energy:pair (energy:pairs $object) $key))
(= (energy:pair $pairs $key)
   (if (== $pairs ()) JsonNull
     (let ($head $tail) (energy:uncons $pairs)
       (case $head
         (((JsonPair $key $value) $value)
          ($other (energy:pair $tail $key)))))))
(= (energy:string $value)
   (case $value
     (((JsonString $s) $s)
      ($other ""))))
(= (energy:has? $pairs $key) (not (== (energy:pair $pairs $key) JsonNull)))

; OBJECT with KEY set to VALUE, in place if present, else last.
(= (energy:put $object $key $value)
   (let $pairs (energy:pairs $object)
     (if (energy:has? $pairs $key)
       (JsonObject (collapse (let $pair (energy:items $pairs)
                               (case $pair
                                 (((JsonPair $key $old) (JsonPair $key $value))
                                  ($other $other))))))
       (JsonObject (collapse (energy:items-then $pairs (JsonPair $key $value)))))))

;; ------------------------------------------------------------ the file

(= (energy:csv $raw)
   (collapse (let $part (energy:items (str:split "," $raw))
               (let $trimmed (str:trim $part)
                 (if (== $trimmed "") (empty) $trimmed)))))

; METTACLAW_TELEGRAM_SENDER_NAMES: "id:Name,id:Name", as (ID NAME) pairs.
(= (energy:env-names)
   (collapse (let $part (energy:items (energy:csv (energy:env "METTACLAW_TELEGRAM_SENDER_NAMES" "")))
               (case (str:find $part ":")
                 (((TextNotFound $needle) (empty))
                  ($at (let $id (str:trim (str-compat:byte-slice $part 0 $at))
                         (let $name (str:trim (str-compat:byte-slice $part (+ $at 1) (str-compat:byte-length $part)))
                           (if (or (== $id "") (== $name "")) (empty) ($id $name))))))))))

(= (energy:env-name $id)
   (case (collapse (let ($known $name) (energy:items (energy:env-names))
                     (if (== $known $id) $name (empty))))
     ((() "")
      ($found (car-atom $found)))))

; Before the file exists: everyone full, the legacy light-arm senders light.
(= (energy:seed)
   (let $senders (collapse (let $id (energy:items (energy:csv (energy:env "METTACLAW_TELEGRAM_LIGHT_ARM_IDS" "")))
                             (JsonPair $id (JsonString "light"))))
     (let $names (collapse (let ($id $name) (energy:items (energy:env-names))
                             (JsonPair $id (JsonString $name))))
       (JsonObject ((JsonPair "default" (JsonString "full"))
                    (JsonPair "senders" (JsonObject $senders))
                    (JsonPair "names" (JsonObject $names)))))))

(= (energy:data)
   (let $path (energy:path)
     (if (fs:exists $path)
       (case (json-parse (fs:read-text $path))
         (((JsonObject $pairs) (JsonObject $pairs))
          ($other (energy:seed))))
       (energy:seed))))

; Atomic state replacement uses unique temporary files and creates parents.
(= (energy:save $data)
   (case (file-io:update (energy:path) "replace" (json-stringify $data) "")
     ((("written" $_ $_ $_ $_) True)
      ($error False))))

;; ----------------------------------------------------------- reading it

(= (energy:default $data)
   (let $tier (energy:string (energy:at $data "default"))
     (if (energy:tier? $tier) $tier "full")))

; (ID TIER) for every sender with a valid tier, in file order.
(= (energy:senders $data)
   (collapse (let $pair (energy:items (energy:pairs (energy:at $data "senders")))
               (case $pair
                 (((JsonPair $id (JsonString $tier)) (if (energy:tier? $tier) ($id $tier) (empty)))
                  ($other (empty)))))))

(= (energy:tier-of $data $id)
   (let $tier (energy:string (energy:at (energy:at $data "senders") $id))
     (if (energy:tier? $tier) $tier (energy:default $data))))

(= (energy:label $data $id)
   (let $name (energy:string (energy:at (energy:at $data "names") $id))
     (if (not (== $name "")) $name
       (let $env (energy:env-name $id) (if (== $env "") $id $env)))))

(= (energy:at-least-one $n) (if (< $n 1) 1 $n))

; The number TEXT reads as, or DEFAULT when it reads as no number: arithmetic
; on anything else raises, and a misspelt setting must not stop the loop.
(= (energy:number $text $default)
   (case (catch (+ 0 (parse $text)))
     (((Error $call $why) $default)
      ($n $n))))

(= (energy:loops $data $tier)
   (let $default (case $tier (("mid" 30) ("light" 10) ($f 50)))
     (case (energy:at (energy:at $data "tier_loops") $tier)
       (((JsonNumber $n) (energy:at-least-one (energy:number $n $default)))
        ($other
         (let $name (case $tier (("mid" "METTACLAW_LOOPS_MID") ("light" "METTACLAW_LOOPS_LIGHT") ($f "METTACLAW_LOOPS_FULL")))
           (energy:at-least-one (energy:number (energy:env $name "") $default))))))))

(= (energy:tier-for $id) (energy:tier-of (energy:data) $id))
(= (energy:tier-loops $tier) (energy:loops (energy:data) $tier))

(= (energy:view)
   (let $data (energy:data)
     (let $default (energy:default $data)
       (let $first (format-args "default: {}({})" ($default (energy:loops $data $default)))
         (let $rest (collapse (let ($id $tier) (energy:items (energy:senders $data))
                                (format-args "{}: {}({})" ((energy:label $data $id) $tier (energy:loops $data $tier)))))
           (if (== $rest ()) $first (format-args "{} | {}" ($first (str:join " | " $rest)))))))))

;; ----------------------------------------------------------- changing it

(= (energy:strip $text $digits)
   (if (== $digits ()) $text
     (let ($digit $rest) (energy:uncons $digits)
       (energy:strip (str:join "" (str:split $digit $text)) $rest))))
(= (energy:id? $text)
   (let $body (if (str:starts-with $text "-") (str-compat:byte-slice $text 1 (str-compat:byte-length $text)) $text)
     (and (> (str-compat:byte-length $body) 0)
          (== (energy:strip $body ("0" "1" "2" "3" "4" "5" "6" "7" "8" "9")) ""))))

; A sender named WHO, case aside: from the file's names, else the
; environment's.
(= (energy:named $data $who)
   (let $wanted (str:lower $who)
     (let $in-file (collapse (let $pair (energy:items (energy:pairs (energy:at $data "names")))
                               (case $pair
                                 (((JsonPair $id (JsonString $name)) (if (== (str:lower $name) $wanted) $id (empty)))
                                  ($other (empty))))))
       (if (not (== $in-file ())) (car-atom $in-file)
         (let $in-env (collapse (let ($id $name) (energy:items (energy:env-names))
                                  (if (== (str:lower $name) $wanted) $id (empty))))
           (if (== $in-env ()) "" (car-atom $in-env)))))))

(= (energy:unique $items $seen)
   (if (== $items ()) ()
     (let ($head $tail) (energy:uncons $items)
       (if (== (collapse (let $s (energy:items $seen) (if (== $s $head) True (empty)))) ())
         (let $rest (energy:unique $tail (collapse (superpose ($head (energy:items $seen)))))
           (collapse (superpose ($head (energy:items $rest)))))
         (energy:unique $tail $seen)))))

(= (energy:known-names $data)
   (let $names (collapse (superpose ((let $pair (energy:items (energy:pairs (energy:at $data "names")))
                                       (case $pair (((JsonPair $id (JsonString $name)) $name) ($other (empty)))))
                                     (let ($id $name) (energy:items (energy:env-names)) $name))))
     (if (== $names ()) "(none known)" (str:join ", " (energy:unique $names ())))))

(= (energy:tier-named $raw)
   (let $tier (str:lower (str:trim $raw))
     (case $tier (("50" "full") ("30" "mid") ("10" "light") ($other $other)))))

; Set the tier of "default" (or "everyone", "*"), a sender ID or a known
; name. The answer says what was set, or why nothing was.
(= (energy:set $who $raw-tier)
   (let $tier (energy:tier-named $raw-tier)
     (if (not (energy:tier? $tier))
       (format-args "energy-set failed: tier must be full, mid or light (got {})" ($tier))
       (let $target (str:trim $who)
         (let $data (energy:data)
           (if (or (== (str:lower $target) "default") (or (== (str:lower $target) "everyone") (== $target "*")))
             (if (energy:save (energy:put $data "default" (JsonString $tier)))
               (format-args "default arming set to {}({})" ($tier (energy:loops $data $tier)))
               "energy-set failed: could not persist")
             (let $id (if (energy:id? $target) $target (energy:named $data $target))
               (if (== $id "")
                 (format-args "energy-set failed: unknown name {} — use a numeric sender id, or one of: {}"
                   ($target (energy:known-names $data)))
                 (let $label (energy:label $data $id)
                   (let $senders (energy:put (energy:at $data "senders") $id (JsonString $tier))
                     (let $names (if (energy:has? (energy:pairs (energy:at $data "names")) $id)
                                   (energy:at $data "names")
                                   (energy:put (energy:at $data "names") $id (JsonString $label)))
                       (if (energy:save (energy:put (energy:put $data "senders" $senders) "names" $names))
                         (format-args "{} arming set to {}({})" ($label $tier (energy:loops $data $tier)))
                         "energy-set failed: could not persist"))))))))))))

;; ---------------------------------------------------------- the menu

(= (energy:cut $text) (if (> (str:char-length $text) 64) (str:char-slice $text 0 64) $text))
(= (energy:button $label $data) (JsonArray ((JsonString (energy:cut $label)) (JsonString $data))))

; Rows of buttons: everyone's default, then each known sender, one tier a
; button, the current one marked.
(= (energy:menu)
   (let $data (energy:data)
     (let $default (energy:default $data)
       (let $everyone (JsonArray (collapse (let $tier (energy:items (energy:tiers))
                                             (energy:button (format-args "{}everyone: {}({})"
                                                              ((if (== $tier $default) "● " "") $tier (energy:loops $data $tier)))
                                                            (format-args "energy:default:{}" ($tier))))))
         (let $filed (collapse (let ($id $tier) (energy:items (energy:senders $data)) $id))
           (let $known (collapse (superpose ((energy:items $filed)
                                             (let ($id $name) (energy:items (energy:env-names))
                                               (if (== (collapse (let $f (energy:items $filed) (if (== $f $id) True (empty)))) ())
                                                 $id (empty))))))
             (let $rows (collapse (let $id (energy:items $known)
                                    (let $current (energy:tier-of $data $id)
                                      (let $name (energy:label $data $id)
                                        (JsonArray (collapse (let $tier (energy:items (energy:tiers))
                                                               (let $cbd (format-args "energy:{}:{}" ($id $tier))
                                                                 (if (> (str-compat:byte-length $cbd) 64) (empty)
                                                                   (energy:button (format-args "{}{}: {}" ((if (== $tier $current) "● " "") $name $tier)) $cbd))))))))))
               (JsonArray (collapse (superpose ($everyone (energy:items $rows))))))))))))
