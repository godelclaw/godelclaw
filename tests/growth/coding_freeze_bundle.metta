; Growth workload: freezing a runtime selection of n files of 2 KiB each,
; digest and copy (the first freeze of a release).
!(import! &self ../growth_support.metta)
!(import! &self ../../src/coding_io_cetta.metta)
!(let* (($program (nt:join (growth:directory) (format-args "program-{}" ((system:uuid4-hex)))))
        ($_ (fs:make-directories $program 504))
        ($_ (nt:exit-ok (proc:run ("sh" "-c" (format-args (str:join "" (
              "mkdir -p config src && i=0; printf '[\"coding_main.metta\"' > config/runtime-files.json; "
              "printf '; fixture\\n' > coding_main.metta; "
              "while [ $i -lt {} ]; do head -c 2048 /dev/zero | tr '\\0' m > src/m$i.metta; "
              "printf ', \"src/m%d.metta\"' $i >> config/runtime-files.json; i=$((i+1)); done; "
              "printf ']\\n' >> config/runtime-files.json")) ((growth:size))))
              $program (nt:environment $program) 120000 4096))))
   (growth:measure (cio:freeze-bundle $program (nt:join $program "cache"))))
