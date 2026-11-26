; World-mode probes: each runs on the SDK engine directly with its test world as
; the working directory, so every file it writes (and the programs on PATH in
; <world>/scope-bin) stays inside the world, and the world's loopback provider
; and ledger serve it. A probe with settings gets them last.
!(import! &self ./runtime_probes_native.metta)

(= (wp:cases)
   (("tests/coding_lifecycle_probe.metta" "CODING_LIFECYCLE_OK" ())
    ("tests/provider_command_census_probe.metta" "CENSUS_PROVIDER_OK"
     (("METTACLAW_MODEL_COMMENTARY" "1") ("METTACLAW_STREAM_DIR" "display") ("METTACLAW_PUBLIC_WORK_DIR" "worklog")))))

(= (wp:run-one $root $sdk $core $artifacts $fixture $bin $name $marker $extra $index)
   (let* (($world (nq:world $root $sdk $core $artifacts $fixture (format-args "world-{}" ($index))))
          ($cwd (nq:path $world ""))
          ($path (format-args "{}scope-bin:{}:{}" ($cwd $bin (system:getenv-or-default "PATH" "/usr/bin:/bin"))))
          ($env (nq:overlay-env
                  (nq:guest-env $world "fixture-model"
                    (("PATH" $path) ("METTACLAW_INSTANCE" "world")
                     ("METTACLAW_CODING_GUIDANCE_DIR" (nq:path $world "no-guidance"))
                     ("METTACLAW_MAIL_NAME" "world-requester")
                     ("METTACLAW_WORK_MAIL" (format-args "{}scope-bin/mailer" ($cwd)))))
                  $extra))
          ($result (proc:run ((nt:join $sdk "cetta") "--lang" "petta" "--import-mode" "ancestor-walk" (nt:join $root $name))
                             $cwd $env 300000 1048576))
          ($_ (fs:write (nq:path $world "probe-result.metta") (preq:repr $result)))
          ($output (nt:exit-ok $result))
          ($_ (nt:require world-probe-witness (preq:member? $marker (str:split "\n" $output))))
          ($_ (println! (NATIVE_WORLD_PROBE_PASSED $name)))
          ($_ (nq:close $world)))
     True))

(= (wp:run-all $root $sdk $core $artifacts $fixture $bin $cases $index)
   (if (== $cases ()) $index
     (let (($name $marker $extra) $rest) ((car-atom $cases) (cdr-atom $cases))
       (let $_ (wp:run-one $root $sdk $core $artifacts $fixture $bin $name $marker $extra $index)
         (wp:run-all $root $sdk $core $artifacts $fixture $bin $rest (+ $index 1))))))

(= (wp:run)
   (let* (($root (system:cwd)) ($sdk (nt:required-env "NATIVE_TEST_SDK"))
          ($core (nt:required-env "NATIVE_TEST_CORE")) ($artifacts (nt:required-env "NATIVE_TEST_ARTIFACTS"))
          ($fixture (nt:required-env "NATIVE_TEST_HTTP_FIXTURE"))
          ; The peer also answers as the user service manager (--user is-active).
          ($bin (nt:join $artifacts "world-bin")) ($_ (fs:make-directories $bin 448))
          ($_ (nt:exit-ok (proc:run ("ln" "-sfn" $fixture (nt:join $bin "systemctl")) $root (nq:base-env $root $artifacts) 5000 65536))))
     (wp:run-all $root $sdk $core $artifacts $fixture $bin (wp:cases) 0)))

!(nt:require every-world-probe-passed (== (wp:run) (size-atom (wp:cases))))
!(println! NATIVE_WORLD_PROBES_PASSED)
