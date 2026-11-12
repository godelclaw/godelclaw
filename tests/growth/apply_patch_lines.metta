; Growth workload: one hunk near the end of a file of n lines (the search for
; its place scans the file).
!(import! &self ../growth_support.metta)
!(import! &self ../../src/apply_patch_cetta.metta)
!(let* (($dir (nt:join (growth:directory) (format-args "patch-{}" ((system:uuid4-hex)))))
        ($_ (fs:make-directories $dir 504))
        ($_ (nt:exit-ok (proc:run ("sh" "-c" (format-args "i=0; while [ $i -lt {} ]; do echo \"line $i of the file\"; i=$((i+1)); done > big.txt; echo 'the target line' >> big.txt; echo 'after it' >> big.txt" ((growth:size))))
                                  $dir (nt:environment $dir) 120000 4096)))
        ($patch "*** Begin Patch\n*** Update File: big.txt\n the target line\n-after it\n+changed after it\n*** End Patch"))
   (growth:measure (patch:apply $patch $dir)))
