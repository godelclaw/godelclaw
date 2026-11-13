; Growth checks without wall-clock thresholds.
;
; A workload runs at sizes n, 4n and 16n, each size in fresh processes on the
; Python-disabled core engine, or on the SDK engine for a check of the engine
; library the agents load. Only ratios between neighbouring sizes are
; asserted: a linear path may take at most about 6x the time and 4x the memory
; (plus a constant) per 4x of input, so a quadratic path (16x) fails while
; host noise, which scales both sizes alike, does not. Each size runs three
; times. Time keeps the fastest run: a busy host only ever adds to it. Memory
; keeps the middle reading: a resident peak can read low as well as high (the
; kernel reclaims pages under memory pressure, and the allocator reuses pages
; a run left resident), and the lowest of three is the run that undercounted.
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
; Linear memory a + b*n passes a 4x step when 3a + slack >= 0. Measured over
; 100-200 repeats, a reading's median absolute deviation is about 64 KiB at
; every size, and the linear control's intercept is about -235 KiB, so a
; middle-of-three reading keeps the control about 1.3 MiB inside the bound,
; and at least 0.78 MiB inside in 999 of 1000 resamples.
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
(= (growth:median3 $a $b $c) (max (min $a $b) (min (max $a $b) $c)))
; Three (time memory) readings of one size: the fastest time, the middle memory.
(= (growth:statistic ($t1 $m1) ($t2 $m2) ($t3 $m3))
   ((min $t1 (min $t2 $t3)) (growth:median3 $m1 $m2 $m3)))
(= (growth:sample $root $engine $script $size $dir)
   (let* (($first (growth:run-once $root $engine $script $size $dir 1))
          ($second (growth:run-once $root $engine $script $size $dir 2))
          ($third (growth:run-once $root $engine $script $size $dir 3)))
     (growth:statistic $first $second $third)))
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

;; ------------------------------------------------ the gate's own mutants

; Whether one 4x step breaks the bound DIMENSION names (time, memory or
; either), without failing the run.
(= (growth:breaks? $dimension $small $large $bounds)
   (let* (((GrowthBounds $tr $ts $mr $ms) $bounds)
          (($size-a $time-a $memory-a) $small) (($size-b $time-b $memory-b) $large)
          ($time (> $time-b (+ (* $tr $time-a) $ts))) ($memory (> $memory-b (+ (* $mr $memory-a) $ms))))
     (case $dimension ((time $time) (memory $memory) (either (or $time $memory))))))
; A registered mutant must break its bound in at least one step: this shows
; the gate can fail.
(= (growth:rejects $root $engine $artifacts $name $script $n $bounds $dimension)
   (let* (($dir (nt:join $artifacts (format-args "growth-{}-{}" ($name (system:uuid4-hex)))))
          ($_ (fs:make-directories $dir 448))
          (($t1 $m1) (growth:sample $root $engine $script $n $dir))
          (($t4 $m4) (growth:sample $root $engine $script (* 4 $n) $dir))
          (($t16 $m16) (growth:sample $root $engine $script (* 16 $n) $dir))
          ($_ (println! (GROWTH-MUTANT $name (size $n ns $t1 kib $m1) (size (* 4 $n) ns $t4 kib $m4)
                                       (size (* 16 $n) ns $t16 kib $m16)
                                       (time-ratios (/ $t4 (max 1 $t1)) (/ $t16 (max 1 $t4)))))))
     (or (growth:breaks? $dimension ($n $t1 $m1) ((* 4 $n) $t4 $m4) $bounds)
         (growth:breaks? $dimension ((* 4 $n) $t4 $m4) ((* 16 $n) $t16 $m16) $bounds))))
