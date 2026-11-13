; Growth checks without wall-clock thresholds.
;
; A workload runs at sizes n, 4n and 16n, each size in fresh processes on the
; Python-disabled core engine, or on the SDK engine for a check of the engine
; library the agents load. Only ratios between neighbouring sizes are
; asserted: a linear path may take at most about 6x the time and 4x the memory
; (plus a constant) per 4x of input, so a quadratic path (16x) fails while
; host noise, which scales both sizes alike, does not. Each size runs three
; times and keeps its minimum, which discards a run that a busy host delayed.
!(import! &self ./native_test_support.metta)

;; ------------------------------------------------- in the measured child

(= (growth:status-kib $key)
   (growth:status-line (str:split "\n" (fs:read-text "/proc/self/status")) $key))
(= (growth:status-line $lines $key)
   (if (== $lines ()) 0
     (let $line (car-atom $lines)
       (if (str:starts-with $line $key)
         (str:string->number (car-atom (str:split-whitespace (str:char-slice $line (str:char-length $key) (str:char-length $line)))))
         (growth:status-line (cdr-atom $lines) $key)))))
; Peak memory restarts at the current resident size, so setup does not count.
(= (growth:reset-peak)
   (let $writer (fs:open-write "/proc/self/clear_refs" () 384)
     (let* (($_ (fs:write-all $writer "5")) ($_ (fs:close $writer))) True)))
(= (growth:size) (str:string->number (nt:required-env "GROWTH_SIZE")))
(= (growth:directory) (nt:required-env "GROWTH_DIR"))
; Measure the workload: monotonic time, and the peak resident memory it added.
(: growth:measure (-> Atom %Undefined%))
(= (growth:measure $workload)
   (let* (($_ (growth:reset-peak)) ($before (growth:status-kib "VmRSS:"))
          ($start (system:monotonic-ns))
          ($answers (collapse (eval $workload)))
          ($elapsed (- (system:monotonic-ns) $start))
          ($peak (growth:status-kib "VmHWM:"))
          ($added (if (> $peak $before) (- $peak $before) 0)))
     (fs:write (nt:required-env "GROWTH_RESULT") (format-args "{} {} {}" ($elapsed $added (size-atom $answers))))))

;; ------------------------------------------------------ in the driver

(= (growth:append $left $right)
   (if (== $left ()) $right (cons-atom (car-atom $left) (growth:append (cdr-atom $left) $right))))

; (GrowthBounds time-ratio time-slack-ns memory-ratio memory-slack-kib)
(= (growth:linear) (GrowthBounds 6 2000000 4 2048))
; A bounded path: its cost may not follow its input at all.
(= (growth:bounded) (GrowthBounds 2 2000000 2 2048))

(= (growth:run-once $root $engine $script $size $dir $index)
   (let* (($result (nt:join $dir (format-args "result-{}-{}" ($size $index))))
          ($env (growth:append (nt:environment $dir)
                  (("GROWTH_SIZE" (repr $size)) ("GROWTH_DIR" $dir) ("GROWTH_RESULT" $result))))
          ($run (proc:run ((nt:join $engine "cetta") "--lang" "petta" "--import-mode" "ancestor-walk" "--quiet"
                           (nt:join $root $script)) $root $env 600000 1048576))
          ($_ (nt:exit-ok $run))
          ($fields (str:split " " (fs:read-text $result))))
     ((str:string->number (car-atom $fields)) (str:string->number (car-atom (cdr-atom $fields))))))
(= (growth:minimum $a $b) (if (< $a $b) $a $b))
(= (growth:sample $root $engine $script $size $dir)
   (let* ((($t1 $m1) (growth:run-once $root $engine $script $size $dir 1))
          (($t2 $m2) (growth:run-once $root $engine $script $size $dir 2))
          (($t3 $m3) (growth:run-once $root $engine $script $size $dir 3)))
     ((growth:minimum $t1 (growth:minimum $t2 $t3)) (growth:minimum $m1 (growth:minimum $m2 $m3)))))
(= (growth:within $name $small $large $bounds)
   (let* (((GrowthBounds $tr $ts $mr $ms) $bounds)
          (($size-a $time-a $memory-a) $small) (($size-b $time-b $memory-b) $large)
          ($_ (nt:require (GrowthTime $name $size-a $size-b) (<= $time-b (+ (* $tr $time-a) $ts))))
          ($_ (nt:require (GrowthMemory $name $size-a $size-b) (<= $memory-b (+ (* $mr $memory-a) $ms)))))
     True))
; Run one registered check at n, 4n and 16n and assert both neighbouring ratios.
(= (growth:check $root $engine $artifacts $name $script $n $bounds)
   (let* (($dir (nt:join $artifacts (format-args "growth-{}-{}" ($name (system:uuid4-hex)))))
          ($_ (fs:make-directories $dir 448))
          (($t1 $m1) (growth:sample $root $engine $script $n $dir))
          (($t4 $m4) (growth:sample $root $engine $script (* 4 $n) $dir))
          (($t16 $m16) (growth:sample $root $engine $script (* 16 $n) $dir))
          ($_ (println! (GROWTH $name (size $n ns $t1 kib $m1) (size (* 4 $n) ns $t4 kib $m4)
                                (size (* 16 $n) ns $t16 kib $m16)
                                (time-ratios (/ $t4 (max 1 $t1)) (/ $t16 (max 1 $t4)))))))
     (and (growth:within $name ($n $t1 $m1) ((* 4 $n) $t4 $m4) $bounds)
          (growth:within $name ((* 4 $n) $t4 $m4) ((* 16 $n) $t16 $m16) $bounds))))

;; ------------------------------------------------- the gate's own mutant

; Whether one 4x step stays within bounds, without failing the run.
(= (growth:holds? $small $large $bounds)
   (let* (((GrowthBounds $tr $ts $mr $ms) $bounds)
          (($size-a $time-a $memory-a) $small) (($size-b $time-b $memory-b) $large))
     (and (<= $time-b (+ (* $tr $time-a) $ts)) (<= $memory-b (+ (* $mr $memory-a) $ms)))))
; A registered mutant must break the bounds in at least one step: this shows
; the gate can fail.
(= (growth:rejects $root $engine $artifacts $name $script $n $bounds)
   (let* (($dir (nt:join $artifacts (format-args "growth-{}-{}" ($name (system:uuid4-hex)))))
          ($_ (fs:make-directories $dir 448))
          (($t1 $m1) (growth:sample $root $engine $script $n $dir))
          (($t4 $m4) (growth:sample $root $engine $script (* 4 $n) $dir))
          (($t16 $m16) (growth:sample $root $engine $script (* 16 $n) $dir))
          ($_ (println! (GROWTH-MUTANT $name (size $n ns $t1 kib $m1) (size (* 4 $n) ns $t4 kib $m4)
                                       (size (* 16 $n) ns $t16 kib $m16)
                                       (time-ratios (/ $t4 (max 1 $t1)) (/ $t16 (max 1 $t4)))))))
     (not (and (growth:holds? ($n $t1 $m1) ((* 4 $n) $t4 $m4) $bounds)
               (growth:holds? ((* 4 $n) $t4 $m4) ((* 16 $n) $t16 $m16) $bounds)))))
