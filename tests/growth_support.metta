; Growth checks without wall-clock thresholds. A workload runs in a fresh
; child at sizes 0, n, 4n and 16n, three times each, keeping the median.
; Time is measured around the workload inside the child, so start-up is
; excluded; memory is the child's peak resident set less the size-0 peak.
; A linear path keeps each 4x step within 6x the time and 4x the memory plus
; a constant (4.5x, for allocator table doubling). Ratios tolerate machine noise and catch a quadratic step at
; small sizes. A workload returns a small value derived from all its output.
!(import! &self ./native_test_support.metta)

(: growth:read-number (-> %Undefined% Number))
(= (growth:read-number $path) (json:parse (str:strip-python (fs:read-text $path))))

(: growth:field (-> %Undefined% %Undefined% %Undefined%))
(= (growth:field $pairs $key)
   (let (JsonPair $name $value) (car-atom $pairs)
     (if (== $name $key) $value (growth:field (cdr-atom $pairs) $key))))

(: growth:median3 (-> Number Number Number Number))
(= (growth:median3 $a $b $c)
   (- (+ $a (+ $b $c)) (+ (min-atom ($a $b $c)) (max-atom ($a $b $c)))))

(: growth:source (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (growth:source $imports $workload $size $timing)
   (format-args "!(import! &self (library system))\n!(import! &self (library fs))\n{}\n!(let* (($start (system:monotonic-ns)) ($value ({} {})) ($end (system:monotonic-ns))) (let $_ (fs:write {} (repr (- $end $start))) (println! (GROWTH_VALUE $value))))\n"
     ($imports $workload $size (repr $timing))))

; One child run: (elapsed-ns peak-rss-kib).
(: growth:sample (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (growth:sample $root $engine $env $fixture $directory $imports $workload $size $index)
   (let* (($stem (nt:join $directory (format-args "{}-{}-{}" ($workload $size $index))))
          ($entry (str:concat $stem ".metta")) ($timing (str:concat $stem ".ns"))
          ($report (str:concat $stem ".json"))
          ($_ (fs:write $entry (growth:source $imports $workload $size $timing)))
          ($output (nt:exit-ok (proc:run ($fixture "--measure" $report "--" $engine "--lang" "petta"
                                         "--profile" "extended" "--import-mode" "ancestor-walk" "--quiet" $entry)
                                        $root $env 600000 1048576)))
          ($_ (nt:require growth-workload-value (nt:contains $output "GROWTH_VALUE")))
          ((JsonObject $fields) (json:parse (fs:read-text $report))))
     ((growth:read-number $timing) (growth:field $fields "peak_rss_kib"))))

(: growth:point (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (growth:point $root $engine $env $fixture $directory $imports $workload $size)
   (let* ((($t1 $m1) (growth:sample $root $engine $env $fixture $directory $imports $workload $size 1))
          (($t2 $m2) (growth:sample $root $engine $env $fixture $directory $imports $workload $size 2))
          (($t3 $m3) (growth:sample $root $engine $env $fixture $directory $imports $workload $size 3)))
     ((growth:median3 $t1 $t2 $t3) (growth:median3 $m1 $m2 $m3))))

; Ratios of one 4x step, as (time-ratio memory-delta-ratio-check).
(: growth:step-ok (-> Number Number Number Number Number Number Bool))
(= (growth:step-ok $t-small $t-large $m-small $m-large $m-zero $constant)
   (and (<= $t-large (* 6 (max-atom ($t-small 1))))
        (<= (- $m-large $m-zero) (+ (* 4.5 (max-atom ((- $m-small $m-zero) 0))) $constant))))

; Measures the workload and answers True when both 4x steps stay linear.
; $memory-constant is in KiB and absorbs allocator granularity.
(: growth:linear? (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (growth:linear? $root $engine $env $fixture $directory $imports $workload $n $memory-constant)
   (let* (($_ (fs:make-directories $directory 448))
          (($t0 $m0) (growth:point $root $engine $env $fixture $directory $imports $workload 0))
          (($t1 $m1) (growth:point $root $engine $env $fixture $directory $imports $workload $n))
          (($t4 $m4) (growth:point $root $engine $env $fixture $directory $imports $workload (* 4 $n)))
          (($t16 $m16) (growth:point $root $engine $env $fixture $directory $imports $workload (* 16 $n)))
          ($_ (println! (GROWTH $workload (sizes 0 $n (* 4 $n) (* 16 $n))
                          (time-ns $t0 $t1 $t4 $t16) (peak-rss-kib $m0 $m1 $m4 $m16)
                          (time-ratios (/ $t4 (max-atom ($t1 1))) (/ $t16 (max-atom ($t4 1))))))))
     (and (growth:step-ok $t1 $t4 $m1 $m4 $m0 $memory-constant)
          (growth:step-ok $t4 $t16 $m4 $m16 $m0 $memory-constant))))
