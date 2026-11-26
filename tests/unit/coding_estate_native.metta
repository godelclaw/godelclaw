; Coding-estate observation, called in-process, against an independent count:
; the root's own 512-byte blocks (lstat) plus every entry below it found by
; find (one count per name, never through a symlink below the root, unreadable
; entries skipped). Hard links and internal symlinks are counted, not
; followed; a streamed hash and its cache see a same-size write with restored
; times; a resource failure is never cached as empty storage; a
; permission-limited walk keeps its partial count and closes what it opens;
; and a session snapshot skips a malformed run line and drops a torn tail.
!(import! &self ../native_test_support.metta)
!(import! &self ../support/coding_estate_observation_probe.metta)
!(import! &self (library json))

(= (ce:sh $script $args)
   (nt:exit-ok (proc:run (list:append ("sh" "-c" $script "estate") $args) "/" inherit 30000 1048576)))
(= (ce:number $text) (let $trimmed (str:strip-python $text) (parse $trimmed)))
(= (ce:oracle $path)
   (ce:number (ce:sh "t=$(stat -c %b -- \"$1\" 2>/dev/null) || { echo 0; exit 0; }
f=$(find \"$1/\" -mindepth 1 -printf '%b\\n' 2>/dev/null | awk '{s+=$1} END {print s+0}')
echo $((t + f))" ($path))))
(= (ce:blocks $path) (estate-probe:call (Blocks $path)))
(= (ce:blocks-ok $path) (== (ce:blocks $path) ((ce:oracle $path))))
(= (ce:sha256 $path) (car-atom (str:split " " (ce:sh "sha256sum -- \"$1\"" ($path)))))
; The engine's own open descriptors, listed by the engine itself: no child
; process, whose handles might close later, is started to count them.
(= (ce:fds) (let $d (fs:open-directory "/proc/self/fd") (let $n (ce:entries $d 0) (let $_ (fs:close $d) $n))))
(= (ce:entries $d $n) (case (fs:next-entry $d) (((fs:entry $name $kind $i) (ce:entries $d (+ $n 1))) ($end $n))))
(= (ce:wait-file $owner $path $left)
   (if (fs:exists $path) True (if (<= $left 0) False (let $_ (proc:wait $owner 20) (ce:wait-file $owner $path (- $left 1))))))
(= (ce:get (JsonObject $pairs) $key)
   (if (== $pairs ()) (Missing $key) (let (JsonPair $k $v) (car-atom $pairs) (if (== $k $key) $v (ce:get (JsonObject (cdr-atom $pairs)) $key)))))

!(let* (($home (nt:required-env "HOME")) ($_ (estate-probe:call Clear))
         ; Hard links, an internal loop, a link outside, a dangling link, a linked root.
         ($_ (ce:sh "set -e; r=$1
mkdir -p \"$r/tree/nested\" \"$r/outside\"
head -c 16384 /dev/zero | tr '\\0' A > \"$r/tree/nested/data\"
ln \"$r/tree/nested/data\" \"$r/tree/copy\"
ln -s \"$r/tree\" \"$r/tree/loop\"
head -c 262144 /dev/zero | tr '\\0' B > \"$r/outside/big\"
ln -s \"$r/outside\" \"$r/tree/outside-link\"
ln -s \"$r/missing\" \"$r/tree/dangling\"
ln -s \"$r/tree\" \"$r/linked-root\"" ($home)))
         ($_ (nt:require blocks-count-hard-links-and-never-follow-internal-symlinks
               (and (ce:blocks-ok (path:join $home "tree"))
               (and (ce:blocks-ok (path:join $home "linked-root"))
               (and (ce:blocks-ok (path:join $home "tree/copy"))
               (and (ce:blocks-ok (path:join $home "tree/dangling"))
                    (== (ce:blocks (path:join $home "absent")) (0))))))))
         ($_ (nt:require the-tree-is-not-empty (> (ce:oracle (path:join $home "tree")) 0)))
         ; A same-size write with restored times is still seen.
         ($engine (path:join $home "engine"))
         ($_ (ce:sh "head -c 65792 /dev/urandom > \"$1\"; cp -p \"$1\" \"$1.times\"" ($engine)))
         ($first (estate-probe:call (Hash $engine)))
         ($_ (nt:require a-streamed-hash-matches-sha256sum
               (and (== $first (ce:sha256 $engine)) (== (estate-probe:call (Hash $engine)) $first))))
         ($_ (ce:sh "head -c 65792 /dev/zero | tr '\\0' X > \"$1\"; touch -r \"$1.times\" \"$1\"" ($engine)))
         ($_ (nt:require the-hash-cache-sees-a-same-size-write-with-restored-times
               (let $again (estate-probe:call (Hash $engine))
                 (and (== $again (ce:sha256 $engine)) (not (== $again $first))))))
         ($_ (nt:require missing-and-directory-hashes-are-null
               (and (== (estate-probe:call (Hash (path:join $home "absent"))) JsonNull)
                    (== (estate-probe:call (Hash $home)) JsonNull))))
         ; Out of descriptors: no answer, and nothing cached. A helper lowers
         ; the engine's limit and, three seconds later, restores it by itself:
         ; a starved engine cannot be relied on to ask. The starved call comes
         ; at once; had it missed the window, it would not answer empty.
         ($limited (path:join $home "limited"))
         ($_ (ce:sh "mkdir -p \"$1\"; head -c 16384 /dev/zero | tr '\\0' X > \"$1/data\"" ($limited)))
         ($helper (proc:spawn ("sh" "-c" "saved=$(prlimit --pid \"$PPID\" --nofile --raw --noheadings -o SOFT,HARD | awk '{print $1\":\"$2}')
cat /proc/$PPID/comm > \"$1/parent\"
read go; prlimit --pid \"$PPID\" --nofile=0:${saved#*:} && : > \"$1/lowered\"
sleep 3; prlimit --pid \"$PPID\" --nofile=\"$saved\" && : > \"$1/restored\"" "limits" $home)
                       ((mode stream) (max-bytes 4096))))
         ($_ (proc:write $helper "go\n"))
         ($lowered (ce:wait-file $helper (path:join $home "lowered") 250))
         ($starved (ce:blocks $limited))
         ($restored (ce:wait-file $helper (path:join $home "restored") 500))
         ($_ (proc:close-input $helper)) ($_ (proc:await $helper)) ($_ (proc:close $helper))
         ($_ (nt:require the-helper-limits-the-engine-itself
               (and $lowered (and $restored (nt:contains (fs:read-text (path:join $home "parent")) "cetta")))))
         ($_ (nt:require a-resource-failure-is-not-cached-as-empty-storage
               (and (== $starved ())
                    (and (ce:blocks-ok $limited) (> (ce:oracle $limited) 0)))))
         ; A permission-limited walk keeps its partial count and closes what it opens.
         ($partial (path:join $home "partial"))
         ($_ (ce:sh "mkdir -p \"$1/denied\"; head -c 16384 /dev/zero | tr '\\0' X > \"$1/denied/private\"; chmod 0 \"$1/denied\"" ($partial)))
         ; The count is taken once, outside the measured window, which holds
         ; nothing but the walks.
         ($expected ((ce:oracle $partial)))
         ($before (ce:fds))
         ($walks (filter-atom (1 2 3 4 5 6 7 8 9 10 11 12) $i
                   (let $_ (estate-probe:call Clear) (not (== (ce:blocks $partial) $expected)))))
         ($after (ce:fds))
         ($_ (ce:sh "chmod 700 \"$1/denied\"" ($partial)))
         ($_ (if (and (== $walks ()) (== $after $before)) True (println! (PARTIAL_WALK $walks DESCRIPTORS $before $after))))
         ($_ (nt:require a-permission-limited-walk-keeps-its-count-and-its-descriptors
               (and (== $walks ()) (== $after $before))))
         ; A session with a malformed run line, a torn tail and a runtime.
         ($_ (ce:sh "set -e; r=$1
mkdir -p \"$r/memory/coding/s1\" \"$r/memory/coding-runtimes/r1\"
head -c 12288 /dev/zero | tr '\\0' R > \"$r/memory/coding-runtimes/r1/data\"
printf '%s' '{\"status\":\"stopped\",\"steps\":2,\"updated\":1794000000}' > \"$r/memory/coding/s1/state.json\"
printf '%s' '{\"task\":\"fixture\"}' > \"$r/memory/coding/s1/task.json\"
printf '%s\\n%s\\n%s\\n%s' '{\"id\":\"old\"}' 'malformed' '{\"id\":\"current\"}' '{' > \"$r/memory/coding/s1/runs.jsonl\"" ($home)))
         ($snapshot (json:parse-python (estate-probe:call (Snapshot $home "2026-11-12"))))
         ((JsonArray ($session)) (ce:get $snapshot "sessions"))
         ($state (ce:get $session "state"))
         ($status-date (str:strip-python (ce:sh "date -d @1794000000 +%Y-%m-%d" ())))
         ($_ (nt:require the-snapshot-reads-the-estate
               (and (== (ce:get $snapshot "today") "2026-11-12")
               (and (== (ce:get $snapshot "available") True)
               (and (== (ce:get $snapshot "mode") "open")
               (and (== (ce:get $snapshot "selection") (JsonObject ()))
                    (== (ce:get $snapshot "runtimes")
                        (JsonArray ((JsonObject ((JsonPair "id" "r1")
                                                 (JsonPair "blocks" (ce:oracle (path:join $home "memory/coding-runtimes/r1"))))))))))))))
         ($_ (nt:require a-session-keeps-its-state-and-skips-torn-runs
               (and (== (ce:get $state "status") "stopped")
               (and (== (ce:get $state "steps") 2)
               (and (== (ce:get $state "updated") 1794000000)
               (and (== (ce:get $session "runs_count") 2)
               (and (== (ce:get $session "runtime_exists") False)
               (and (== (ce:get $session "runtime") (JsonObject ()))
               (and (== (ce:get $session "lifecycle") (JsonObject ()))
               (and (== (ce:get $session "pin_date") "")
               (and (== (ce:get $session "engine_hash") JsonNull)
               (and (== (ce:get $session "matched") (JsonArray ()))
                    (== (ce:get $session "status_date") $status-date))))))))))))))
   (println! UNIT_CODING_ESTATE_NATIVE_OK))
