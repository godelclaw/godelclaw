; Python-entry census: the migration's progress metric.
;
; Each scenario runs as the runtime probes run (tests/runtime_probes_native.metta),
; but its Python environment carries tests/support/python_entry_census.py as
; sitecustomize. Every process of the scenario, spawned workers included, then
; records each call that entered Python from native code, until Python's
; shutdown begins: exit-time work is not program work. The project count
; must stay at or below the scenario's ceiling in tests/python_entry_census.json;
; a port lowers the ceiling. Run from the checkout root with the core engine:
;   NATIVE_TEST_SDK=... NATIVE_TEST_CORE=... NATIVE_TEST_ARTIFACTS=... \
;     $NATIVE_TEST_CORE/cetta --lang petta --import-mode ancestor-walk --quiet \
;     scripts/census_python_entries_cetta.metta
!(import! &self ../tests/runtime_probes_native.metta)

;; A disposable environment whose site-packages holds the census hook. run.sh
;; puts a pyvenv's site-packages on PYTHONPATH, so the agent's own launch path
;; loads the hook without any change to the runtime.
(= (census:environment $root $sdk $artifacts)
   (let* (($manifest (preq:parse (fs:read-text (nt:join $sdk "build-manifest.json"))))
          ($prefix (pcall:fact $manifest "python_prefix"))
          ($version (pcall:fact $manifest "python_version"))
          ($venv (nt:join $artifacts "census-python"))
          ($bin (nt:join $venv "bin"))
          ($site (nt:join $venv (format-args "lib/python{}/site-packages" ($version))))
          ($_ (fs:make-directories $bin 448)) ($_ (fs:make-directories $site 448))
          ($_ (fs:write (nt:join $venv "pyvenv.cfg")
                (format-args "home = {}/bin\ninclude-system-site-packages = true\nversion = {}\n" ($prefix $version))))
          ($python (nt:join $bin "python3"))
          ($_ (if (fs:exists $python) True
                (nt:exit-ok (proc:run ("ln" "-s" (nt:join $prefix "bin/python3") $python) $root (nt:environment $artifacts) 5000 4096))))
          ($_ (fs:write (nt:join $site "sitecustomize.py") (fs:read-text "tests/support/python_entry_census.py"))))
     $venv))

;; Census records: one line "kind<TAB>file<TAB>function" per entry.
(= (census:records $directory)
   (census:records-of $directory (fs:entries $directory) ()))
(= (census:records-of $directory $names $records)
   (if (nt:equal $names ()) $records
     (let* (($name (preq:head $names))
            ($lines (str:split "\n" (fs:read-text (nt:join $directory $name)))))
       (census:records-of $directory (preq:tail $names) (census:parse-lines $lines $records)))))
(= (census:parse-lines $lines $records)
   (if (nt:equal $lines ()) $records
     (let $line (preq:head $lines)
       (census:parse-lines (preq:tail $lines)
         (if (nt:equal $line "") $records (preq:cons (str:split "\t" $line) $records))))))

;; Counts per kind and per file, as an association list ((key count) ...).
(= (census:tally $records $kind $counts)
   (if (nt:equal $records ()) $counts
     (let ($found $file $function) (preq:head $records)
       (census:tally (preq:tail $records) $kind
         (if (nt:equal $found $kind) (census:bump $file $counts) $counts)))))
(= (census:bump $key $counts)
   (if (nt:equal $counts ()) (($key 1))
     (let ($head-key $head-count) (preq:head $counts)
       (if (nt:equal $head-key $key) (preq:cons ($key (+ $head-count 1)) (preq:tail $counts))
         (preq:cons ($head-key $head-count) (census:bump $key (preq:tail $counts)))))))
(= (census:total $counts)
   (if (nt:equal $counts ()) 0
     (let ($key $count) (preq:head $counts) (+ $count (census:total (preq:tail $counts))))))
(= (census:print-counts $scenario $counts)
   (if (nt:equal $counts ()) True
     (let* ((($key $count) (preq:head $counts))
            ($_ (println! (PYTHON_ENTRIES_BY_FILE $scenario $key $count))))
       (census:print-counts $scenario (preq:tail $counts)))))

;; A probe scenario runs through ./run.sh like the runtime probes. A world
;; scenario runs on the SDK engine directly, with the test world as working
;; directory: everything it creates (coding sessions, frozen runtimes, fixture
;; programs on PATH in <world>/scope-bin) stays inside the world, and the
;; world's loopback provider and ledger serve it.
; $extra is the scenario's optional environment, applied last. A relative path
; in it names a file inside the world, the working directory of world mode.
(= (census:command $root $sdk $world $bin $venv $directory $name probe $extra)
   (CensusCommand ("./run.sh" $name) $root
     (nq:overlay-env (nq:overlay-env (nr:probe-env $world $name $bin $name)
       (("PETTA_PY_ENV" $venv) ("PYTHON_ENTRY_CENSUS_DIR" $directory)
        ("PYTHON_ENTRY_CENSUS_ROOT" $root))) $extra)))
(= (census:command $root $sdk $world $bin $venv $directory $name world $extra)
   (let* (($cwd (nq:path $world ""))
          ($manifest (preq:parse (fs:read-text (nt:join $sdk "build-manifest.json"))))
          ($site (nt:join $venv (format-args "lib/python{}/site-packages" ((pcall:fact $manifest "python_version")))))
          ($path (format-args "{}scope-bin:{}:{}" ($cwd $bin (system:getenv-or-default "PATH" "/usr/bin:/bin")))))
     (CensusCommand ((nt:join $sdk "cetta") "--lang" "petta" "--import-mode" "ancestor-walk" (nt:join $root $name)) $cwd
       (nq:overlay-env (nq:guest-env $world "fixture-model"
         (("PYTHONPATH" (format-args "{}/src:{}/modes:{}/modes/iter:{}/channels:{}" ($root $root $root $root $site)))
          ("PATH" $path) ("METTACLAW_INSTANCE" "census")
          ("METTACLAW_CODING_GUIDANCE_DIR" (nq:path $world "no-guidance"))
          ("METTACLAW_MAIL_NAME" "census-requester")
          ("METTACLAW_WORK_MAIL" (format-args "{}scope-bin/mailer" ($cwd)))
          ("PYTHON_ENTRY_CENSUS_DIR" $directory) ("PYTHON_ENTRY_CENSUS_ROOT" $root))) $extra))))

(= (census:scenario $root $sdk $core $artifacts $fixture $bin $venv $name $marker $ceiling $mode $extra $index)
   (let* (($world (nq:world $root $sdk $core $artifacts $fixture (format-args "census-{}" ($index))))
          ($directory (nq:path $world "census")) ($_ (fs:make-directories $directory 448))
          ((CensusCommand $argv $cwd $env) (census:command $root $sdk $world $bin $venv $directory $name $mode $extra))
          ($start (system:monotonic-ns))
          ($result (proc:run $argv $cwd $env 300000 1048576))
          ($elapsed (/ (- (system:monotonic-ns) $start) 1000000))
          ($_ (fs:write (nq:path $world "probe-result.metta") (preq:repr $result)))
          ($output (nt:exit-ok $result))
          ($_ (nt:require census-scenario-witness (preq:member? $marker (str:split "\n" $output))))
          ($records (census:records $directory))
          ($project (census:tally $records "project" ()))
          ($other (census:tally $records "other" ()))
          ($count (census:total $project))
          ($_ (census:print-counts $name $project))
          ($_ (println! (PYTHON_ENTRY_CENSUS $name project $count ceiling $ceiling other (census:total $other) ms $elapsed)))
          ($_ (nq:close $world)))
     (if (<= $count $ceiling) True
       (let $_ (println! (PYTHON_ENTRY_CEILING_EXCEEDED $name $count $ceiling)) False))))

; Optional fields after the ceiling: a mode ("probe", the default, or
; "world"), then an object of environment settings for the scenario.
(= (census:mode ()) probe)
(= (census:mode ((JsonString "probe") $more)) probe)
(= (census:mode ((JsonString "world") $more)) world)
(= (census:mode ((JsonString "world"))) world)
(= (census:extra-env ()) ())
(= (census:extra-env ($mode)) ())
(= (census:extra-env ($mode (JsonObject $pairs))) (census:env-pairs $pairs))
(= (census:env-pairs $pairs)
   (if (nt:equal $pairs ()) ()
     (let (JsonPair $key (JsonString $value)) (preq:head $pairs)
       (preq:cons ($key $value) (census:env-pairs (preq:tail $pairs))))))
(= (census:run-scenarios $root $sdk $core $artifacts $fixture $bin $venv $scenarios $index $ok)
   (if (nt:equal $scenarios ()) $ok
     (let* (((JsonArray $fields) (preq:head $scenarios))
            (((JsonString $name) (JsonString $marker) (JsonNumber $ceiling)) (census:first-three $fields))
            ($optional (cdr-atom (cdr-atom (cdr-atom $fields))))
            ($mode (census:mode $optional)) ($extra (census:extra-env $optional))
            ($passed (census:scenario $root $sdk $core $artifacts $fixture $bin $venv $name $marker (parse $ceiling) $mode $extra $index)))
       (census:run-scenarios $root $sdk $core $artifacts $fixture $bin $venv (preq:tail $scenarios) (+ $index 1)
         (and $ok $passed)))))

(= (census:run)
   (let* (($root (system:cwd)) ($sdk (nt:required-env "NATIVE_TEST_SDK"))
          ($core (nt:required-env "NATIVE_TEST_CORE")) ($artifacts (nt:required-env "NATIVE_TEST_ARTIFACTS"))
          ($fixture (nt:join $artifacts "native-http-peer")) ($bin (nt:join $artifacts "bin"))
          ($_ (fs:make-directories $bin 448)) ($control (nt:join $bin "systemctl"))
          ($env (nq:base-env $root $artifacts))
          ($_ (nt:exit-ok (proc:run ("cc" "-std=c11" "-O2" "-Wall" "-Wextra" "-Werror"
             "tests/fixtures/native_http_peer.c" "-o" $fixture) $root $env 30000 65536)))
          ($_ (nt:exit-ok (proc:run ("ln" "-sfn" $fixture $control) $root $env 5000 65536)))
          ($venv (census:environment $root $sdk $artifacts))
          ($only (system:getenv-or-default "PYTHON_ENTRY_CENSUS_ONLY" ""))
          ((JsonObject $fields) (preq:parse (fs:read-text "tests/python_entry_census.json")))
          ((JsonArray $all) (preq:field (JsonObject $fields) "scenarios"))
          ($scenarios (if (nt:equal $only "") $all (census:select $all $only))))
     (census:run-scenarios $root $sdk $core $artifacts $fixture $bin $venv $scenarios 0 True)))
(= (census:first-three $fields)
   ((car-atom $fields) (car-atom (cdr-atom $fields)) (car-atom (cdr-atom (cdr-atom $fields)))))
(= (census:select $scenarios $only)
   (if (nt:equal $scenarios ()) ()
     (let* (((JsonArray $fields) (preq:head $scenarios))
            (((JsonString $name) $marker $ceiling) (census:first-three $fields))
            ($rest (census:select (preq:tail $scenarios) $only)))
       (if (nt:contains $name $only) (preq:cons (preq:head $scenarios) $rest) $rest))))

!(nt:require python-entry-census-within-ceilings (census:run))
!(println! PYTHON_ENTRY_CENSUS_PASSED)
