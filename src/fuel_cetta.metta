; Fuel discipline, natively: what a renewal does to the budget it finds.
; The same definitions as src/fuel_modes.py, which the SWI lane and the
; Python readers keep using; tests/test_fuel_cetta.py checks every operation
; against it, and lean/pettaclaw/FuelPolicy.lean proves their bounds.
;
;   saturate    max(loops, grant)               original behaviour, ceiling grant
;   accumulate  loops + grant                   unbounded; restraint compounds
;   carry       min(CARRY * grant, loops+grant) accumulate under a ceiling
;   decay       loops * 3 // 4 + grant          recency-weighted, same ceiling
;   unlimited   budget is not spent             nop, rest or stop ends work
;
; The selected discipline lives in METTACLAW_FUEL_MODE_PATH as {"fuel": name}.
!(import! &self (library fs))
!(import! &self (library json))
!(import! &self (library math))
!(import! &self (library str))
!(import! &self (library system))

(= (fuel:carry) 4)
(= (fuel:default) "decay")
(= (fuel:names) ("saturate" "accumulate" "carry" "decay" "unlimited"))
(= (fuel:description "saturate")
   "one fresh full burst per renewal; unspent budget is lost (the loop's original behaviour)")
(= (fuel:description "accumulate") "every grant adds; unspent budget compounds without limit")
(= (fuel:description "carry")
   (let $carry (fuel:carry) (format-args "grants add up to a ceiling of {} full bursts" ($carry))))
(= (fuel:description "decay")
   "three quarters of what is held carries over, then the grant; same ceiling as carry, approached gradually")
(= (fuel:description "unlimited") "turns do not spend the burst; nop, rest or operator stop ends it")

(= (fuel:path) (system:getenv-or-default "METTACLAW_FUEL_MODE_PATH" "memory/fuel_mode.json"))
(= (fuel:member $name $names)
   (if (== $names ()) False
     (if (== (car-atom $names) $name) True (fuel:member $name (cdr-atom $names)))))
(= (fuel:text $value)
   (if (== (get-type $value) String) $value (format-args "{}" ($value))))
(= (fuel:known $name)
   (let $clean (str:lower (str:trim (fuel:text $name)))
     (if (fuel:member $clean (fuel:names)) $clean (fuel:default))))
(= (fuel:stored $value)
   (case $value
     (((JsonObject $pairs) (fuel:stored-member $pairs))
      ($other (fuel:default)))))
(= (fuel:stored-member $pairs)
   (if (== $pairs ()) (fuel:default)
     (let (JsonPair $key $field) (car-atom $pairs)
       (if (== $key "fuel")
         (case $field (((JsonString $name) (fuel:known $name)) ($other (fuel:default))))
         (fuel:stored-member (cdr-atom $pairs))))))
; A missing, unreadable or malformed file, or an unknown name, means the default.
(= (fuel:current)
   (let $path (fuel:path)
     (if (fs:is-file $path)
       (let $parsed (catch (json-parse (fs:read-text $path)))
         (case $parsed (((Error $where $why) (fuel:default)) ($value (fuel:stored $value)))))
       (fuel:default))))

; Budgets are integers: numbers truncate, integer text parses, anything else is
; 0, and negatives become 0, as Python's max(0, int(value)).
(= (fuel:count $value) (max 0 (fuel:integer $value)))
(= (fuel:integer $value)
   (if (== (get-type $value) Number) (math:trunc $value)
     (if (== (get-type $value) String) (fuel:integer-text (str:trim $value)) 0)))
(= (fuel:integer-text $text)
   (let $digits (if (or (str:starts-with $text "-") (str:starts-with $text "+"))
                  (str:byte-slice $text 1 (str:byte-length $text)) $text)
     (if (and (!= $digits "") (str:is-all-in digit $digits)) (str:string->number $text) 0)))
(= (fuel:three-quarters $held)
   (let $scaled (* $held 3) (math:trunc (/ (- $scaled (% $scaled 4)) 4))))

; A renewal never returns below the grant, so no discipline starves it.
(= (fuel:refuel $loops $grant)
   (let* (($held (fuel:count $loops)) ($offered (fuel:count $grant)) ($name (fuel:current)))
     (case $name
       (("unlimited" (max $held $offered))
        ("accumulate" (+ $held $offered))
        ("carry" (min (* (fuel:carry) $offered) (+ $held $offered)))
        ("decay" (+ (fuel:three-quarters $held) $offered))
        ($other (max $held $offered))))))
; The bound this discipline guarantees, or 0 when there is none.
(= (fuel:ceiling $grant)
   (let* (($offered (fuel:count $grant)) ($name (fuel:current)))
     (case $name
       (("accumulate" 0) ("unlimited" 0)
        ("carry" (* (fuel:carry) $offered)) ("decay" (* (fuel:carry) $offered))
        ($other $offered)))))
; One model turn spends one loop, except in the agent-ended discipline.
(= (fuel:spend $loops)
   (let $held (fuel:count $loops)
     (if (== (fuel:current) "unlimited") $held (max 0 (- $held 1)))))

(= (fuel:view)
   (let $name (fuel:current) (str:join "" ("active fuel: " $name " — " (fuel:description $name)))))
(= (fuel:context-view $loops)
   (if (== (fuel:current) "unlimited") "FUEL: unlimited"
     (let $remaining (fuel:count $loops) (format-args "LOOPS_LEFT: {}" ($remaining)))))
(= (fuel:list-lines $names $current)
   (if (== $names ()) ()
     (let* (($name (car-atom $names))
            ($line (str:join "" ((if (== $name $current) "● " "  ") $name " — " (fuel:description $name))))
            ($rest (fuel:list-lines (cdr-atom $names) $current)))
       (cons-atom $line $rest))))
(= (fuel:list-view) (str:join "\n" (fuel:list-lines (fuel:names) (fuel:current))))

; The file holds exactly {"fuel": name}, written atomically as Python writes it.
(= (fuel:set $name)
   (let $requested (str:lower (str:trim (fuel:text $name)))
     (if (fuel:member $requested (fuel:names))
       (case (catch (fs:update-text (fuel:path) "replace" (str:join "" ("{\n  \"fuel\": \"" $requested "\"\n}\n")) "" ""))
         ((("written" $path $bytes $digest $backup) (str:join "" ("fuel set to '" $requested "'; persists across restarts")))
          ($other "fuel-set failed: could not persist")))
       (str:concat "fuel-set failed: choose one of " (str:join ", " (fuel:names))))))
