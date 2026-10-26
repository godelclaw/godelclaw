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
!(import! &self ./src/mode_values_cetta.metta)
!(import! &self ./src/mode_io_cetta.metta)

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
   (erv:str (erv:cross $value)))
(= (fuel:known $name)
   (if (== (get-type $name) String)
     (let $clean (mode-text:name $name)
       (if (fuel:member $clean (fuel:names)) $clean (fuel:default)))
     ; Parsed JSON non-strings stringify to None/booleans/numbers/containers,
     ; none of which is a member of this finite ASCII fuel-name domain.
     (fuel:default)))
(= (fuel:stored $value)
   (case $value
     (((JsonObject $_) (fuel:known (erv:get $value "fuel" (fuel:default))))
      ($other (fuel:default)))))
; A missing, unreadable or malformed file, or an unknown name, means the default.
(= (fuel:current-result)
   (let $value (mio:load-object (fuel:path))
     (if (mv:failed? $value) $value (fuel:stored $value))))
(= (fuel:current) (mv:raise (fuel:current-result)))

; Actual runtime crossings carry integer loop budgets. Text probes also use
; int(str)'s Unicode Nd/underscore/whitespace grammar and decimal-digit limit.
; TypeError/ValueError become zero; OverflowError is not caught by the oracle.
(= (fuel:count $value) (mv:raise (mv:budget $value)))
(= (fuel:integer $value)
   (let $integer (mv:int (erv:cross $value))
     (case $integer (((ErFailure TypeError $_ $_) 0) ((ErFailure ValueError $_ $_) 0)
       ($other (mv:raise $other))))))
(= (fuel:integer-text $text) (fuel:integer $text))
(= (fuel:three-quarters $held)
   (let $scaled (* $held 3) (math:trunc (/ (- $scaled (% $scaled 4)) 4))))

; A renewal never returns below the grant, so no discipline starves it.
(= (fuel:refuel $loops $grant)
   (let $held (mv:budget $loops)
     (if (mv:failed? $held) (mv:raise $held)
       (let $offered (mv:budget $grant)
         (if (mv:failed? $offered) (mv:raise $offered)
           (let $name (fuel:current-result)
             (if (mv:failed? $name) (mv:raise $name)
               (case $name
                 (("unlimited" (max $held $offered))
                  ("accumulate" (+ $held $offered))
                  ("carry" (min (* (fuel:carry) $offered) (+ $held $offered)))
                  ("decay" (+ (fuel:three-quarters $held) $offered))
                  ($other (max $held $offered)))))))))))
; The bound this discipline guarantees, or 0 when there is none.
(= (fuel:ceiling $grant)
   (let $offered (mv:budget $grant)
     (if (mv:failed? $offered) (mv:raise $offered)
       (let $name (fuel:current-result)
         (if (mv:failed? $name) (mv:raise $name)
           (case $name
             (("accumulate" 0) ("unlimited" 0)
              ("carry" (* (fuel:carry) $offered)) ("decay" (* (fuel:carry) $offered))
              ($other $offered))))))))
; One model turn spends one loop, except in the agent-ended discipline.
(= (fuel:spend $loops)
   (let $held (mv:budget $loops)
     (if (mv:failed? $held) (mv:raise $held)
       (let $name (fuel:current-result)
         (if (mv:failed? $name) (mv:raise $name)
           (if (== $name "unlimited") $held (max 0 (- $held 1))))))))

(= (fuel:view)
   (let $name (fuel:current) (str:join "" ("active fuel: " $name " — " (fuel:description $name)))))
(= (fuel:context-view $loops)
   (let $name (fuel:current-result)
     (if (mv:failed? $name) (mv:raise $name)
       (if (== $name "unlimited") "FUEL: unlimited"
         (let $remaining (mv:budget $loops)
           (if (mv:failed? $remaining) (mv:raise $remaining)
             (format-args "LOOPS_LEFT: {}" ($remaining))))))))
(= (fuel:list-lines $names $current)
   (if (== $names ()) ()
     (let* (($name (car-atom $names))
            ($line (str:join "" ((if (== $name $current) "● " "  ") $name " — " (fuel:description $name))))
            ($rest (fuel:list-lines (cdr-atom $names) $current)))
       (cons-atom $line $rest))))
(= (fuel:list-view) (str:join "\n" (fuel:list-lines (fuel:names) (fuel:current))))

; The file holds exactly {"fuel": name}, written atomically as Python writes it.
(= (fuel:set $name)
   (let $requested (mv:requested $name)
     (if (mv:failed? $requested) (mv:raise $requested)
       (if (fuel:member $requested (fuel:names))
         (let $previous (fuel:current-result)
           (if (mv:failed? $previous) (mv:raise $previous)
             (if (mio:save-json (fuel:path) (JsonObject ((JsonPair "fuel" $requested))) "fuel")
               (str:join "" ("fuel set to '" $requested "'; persists across restarts"))
               "fuel-set failed: could not persist")))
         (str:concat "fuel-set failed: choose one of " (str:join ", " (fuel:names)))))))
