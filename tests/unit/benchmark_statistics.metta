; Unit tests of the paired benchmark's statistics (tests/provider_native_benchmark.metta).
!(import! &self ../native_test_support.metta)
!(import! &self ../provider_native_benchmark.metta)
; 21 pairs whose differences, candidate less baseline, are the given numbers.
(= (bench-test:samples $values $rss)
   (if (nt:equal $values ()) ()
     (preq:cons (NativeBenchSampleV1 (preq:head $values) $rss) (bench-test:samples (preq:tail $values) $rss))))
(= (bench-test:zeros) (0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0))
(= (bench-test:number $object $key) (preq:field $object $key))
!(let* (; Differences -9 .. 11 in a scrambled order: sorted, the 6th is -4, the
        ; 11th (median) is 1 and the 16th is 6; eleven are positive.
        ($differences (3 -9 11 0 -4 7 1 -8 10 -1 6 -7 9 2 -5 8 -3 5 -6 4 -2))
        ($paired (nb:paired (bench-test:samples (bench-test:zeros) 1) (bench-test:samples $differences 1)))
        ($_ (nt:require paired-median (nt:equal (bench-test:number $paired "median_difference_ns") (JsonNumber "1"))))
        ($_ (nt:require paired-interval
              (nt:equal (bench-test:number $paired "interval_ns") (JsonArray ((JsonNumber "-4") (JsonNumber "6"))))))
        ($_ (nt:require paired-slower-count (nt:equal (bench-test:number $paired "candidate_slower_pairs") (JsonNumber "11"))))
        ; Identical samples: no difference, an interval of zero width, none slower.
        ($same (nb:paired (bench-test:samples (bench-test:zeros) 1) (bench-test:samples (bench-test:zeros) 1)))
        ($_ (nt:require control-of-identical-samples
              (nt:equal ((bench-test:number $same "median_difference_ns") (bench-test:number $same "interval_ns")
                         (bench-test:number $same "candidate_slower_pairs"))
                        ((JsonNumber "0") (JsonArray ((JsonNumber "0") (JsonNumber "0"))) (JsonNumber "0"))))))
   (println! UNIT_BENCHMARK_STATISTICS_OK))
