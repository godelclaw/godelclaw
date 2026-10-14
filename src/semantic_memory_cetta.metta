; Agent-owned semantic memory. Collection policy and vector kernels are CeTTa
; libraries; embeddings come from the existing model daemon over native HTTP.
; Write intents live beside their records and survive an evaluator restart.
!(import! &self ./src/hierarchy_store)
!(import! &self (library lib_memory_collection))
!(import! &self (library fs))
!(import! &self (library json))
!(import! &self (library system))
!(import! &self (library io))
!(import! &self (library math))
!(bind! &semantic-cache (new-state Missing))
!(bind! &semantic-http (new-space))

(= (sm:env $name $default) (system:getenv-or-default $name $default))

(= (sm:config-path)
   (sm:env
     "METTACLAW_MEMORY_BACKEND_CONFIG"
     (str:join
       "/"
       ((sm:env "METTACLAW_REPOS_DIR" "./repos") "petta_lib_chromadb/memory_backend.json"))))

(= (sm:config) (catch (json-parse (fs:read-text (sm:config-path)))))

(= (sm:field $object $key $default)
   (case
     (json-object-get $object $key)
     (((JsonString $s) $s) ((JsonNumber $n) (str:string->number $n)) ($other $default))))

(= (semantic:native?) (== (sm:field (sm:config) "mode" "chroma") "native"))

(= (sm:socket) (sm:field (sm:config) "socket" ""))

(= (sm:name) (sm:field (sm:config) "name" ""))

(= (sm:now) (system:wall-time))

(= (sm:time) (system:format-time "%Y-%m-%d %H:%M:%S" (sm:now) "local"))

(= (sm:seconds $key $default) (str:string->number (sm:env $key $default)))

(= (sm:retry-seconds) (sm:seconds "METTACLAW_MEMORY_RETRY_SECONDS" "30"))

(= (sm:embedding-timeout) (* 1000 (sm:seconds "METTACLAW_MEMORY_EMBED_TIMEOUT_SECONDS" "180")))

(= (sm:open)
   (case
     (space:open (sm:socket) (sm:name) native)
     (((reply $rid ok $space)
       (case
         (hm:one
           (sm:socket)
           $space
           (MemoryProfile $profile $description)
           (MemoryProfile $profile $description))
         (((MemoryProfile $profile $description)
           (let
             $dim
             (sm:field (json-parse $description) "embedding_output_dim" 0)
             (if
               (> $dim 0)
               (MStore $space $profile $dim)
               (MemoryUnavailable invalid-embedding-profile))))
          ($error (MemoryUnavailable profile $error)))))
      ($error (MemoryUnavailable store $error)))))

(= (sm:snapshot $space $pattern $template)
   (case
     (space:count (sm:socket) $space $pattern)
     (((reply $r ok $count)
       (case
         (space:query (sm:socket) $space $pattern $template (max 1 $count) 30000)
         (((reply $q ok (snapshot $boot $cursor $source $rev $total $lease))
           (let
             $snapshot
             (snapshot $boot $cursor $source $rev $total $lease)
             (let
               $rows
               (hm:pages (sm:socket) $snapshot 0 $total 32)
               (let $closed (space:close (sm:socket) $snapshot) (MSnapshot $rev $rows)))))
          ((reply $q error query-row-limit) (sm:snapshot $space $pattern $template))
          ($error (MemoryUnavailable query $error)))))
      ($error (MemoryUnavailable count $error)))))

(= (sm:one $space $id)
   (case
     (hm:one (sm:socket) $space (Memory $id $r $p $v $d $m) (Memory $id $r $p $v $d $m))
     (((HFailure missing-record $pattern) (MemoryMissing $id))
      ((HFailure $why $at) (MemoryUnavailable record $why))
      ($row $row))))

(= (sm:pending $space)
   (sm:all
     $space
     (MemoryPending 1 $op $id $action $text $time $attempts $due $error)
     (MemoryPending 1 $op $id $action $text $time $attempts $due $error)))

(= (sm:pending-id $space $id)
   (sm:all
     $space
     (MemoryPending 1 $op $id $a $t $time $n $due $error)
     (MemoryPending 1 $op $id $a $t $time $n $due $error)))

(= (sm:removes $rows)
   (case $rows
     (((MemoryUnavailable $where $why) $rows)
      ($rows (if (== $rows ()) ()
        (cons-atom (remove (car-atom $rows)) (sm:removes (cdr-atom $rows))))))))

(= (sm:commit $space $ops)
   (case $ops
     (((MemoryUnavailable $where $why) $ops)
      ($ops (sm:commit-ops $space $ops)))))

(= (sm:commit-ops $space $ops)
   (case
     (hm:commit (sm:socket) $space $ops)
     (((reply $rid ok $answer)
       (let $invalidated (change-state! &semantic-cache Missing) (MemoryCommitted)))
      ((reply $rid error stale-revision) (MemoryChanged))
      ($error (MemoryUnavailable commit $error)))))

(= (sm:refresh $space $profile $dim)
   (case
     $space
     (((space $uuid $sid $sn $backend $kind $rev $count)
       (if
         (== (get-state &semantic-cache) (MCache $uuid $sid $rev $profile $dim))
         True
         (case
           (sm:snapshot $space (Memory $id $r $p $v $doc $meta) (Memory $id $r $p $v $doc $meta))
           (((MSnapshot $source-rev $rows)
             (case
               $rows
               (((HFailure $why $at) (MemoryUnavailable snapshot $why))
                ($rows
                  (let
                    $_
                    (memory:dispatch (reset))
                    (case
                      (memory:dispatch (load $profile $dim $rows))
                      (((Loaded $n)
                        (let
                          $_
                          (change-state!
                            &semantic-cache
                            (MCache $uuid $sid $source-rev $profile $dim))
                          True))
                       ($error (MemoryUnavailable load $error)))))))))
            ($error $error))))))))

(= (sm:embed-submit $kind $text)
   (let
     $endpoint
     (sm:env "METTACLAW_EMBED_ENDPOINT" "")
     (if
       (== $endpoint "")
       (MemoryUnavailable embedding endpoint-not-configured)
       (let
         $body
         (json-stringify
           (JsonObject
             ((JsonPair "kind" (JsonString $kind))
              (JsonPair "texts" (JsonArray ((JsonString $text)))))))
         (catch
           (io:submit
             (http:request
               "POST"
               (str:join "" ($endpoint "/embed"))
               ((http:header "Content-Type" "application/json"))
               $body
               (sm:embedding-timeout)
               (sm:seconds "METTACLAW_MEMORY_EMBED_RESPONSE_BYTES" "8388608"))))))))

(= (sm:numbers $items)
   (if
     (== $items ())
     ()
     (case
       (car-atom $items)
       (((JsonNumber $n)
         (let
           $value
           (str:string->number $n)
           (if
             (or (isnan-math $value) (isinf-math $value))
             (MemoryUnavailable embedding nonfinite-vector)
             (case
               (sm:numbers (cdr-atom $items))
               (((MemoryUnavailable $where $why) (MemoryUnavailable $where $why))
                ($tail (cons-atom $value $tail)))))))
        ($bad (MemoryUnavailable embedding invalid-vector))))))

(= (sm:embed-result $result $dim)
   (case
     $result
     (((http:response 200 $body)
       (let
         $value
         (catch (json-parse $body))
         (if
           (==
             (sm:field $value "protocol" "")
             (sm:env "METTACLAW_EMBED_PROTOCOL" "qwen3-embedding-8b-1024-v1"))
           (sm:embed-json $value $dim)
           (MemoryUnavailable embedding protocol-mismatch))))
      ((http:response $status $body) (MemoryUnavailable embedding (http-status $status)))
      ($error (MemoryUnavailable embedding $error)))))

(= (sm:embed-wait $id $dim)
   (case
     (io:wait $id 1000)
     (((io:idle) (sm:embed-wait $id $dim))
      ((io:event $id $result) (sm:embed-result $result $dim))
      ($error (MemoryUnavailable embedding $error)))))

(= (sm:embed $kind $text $dim)
   (case
     (sm:embed-submit $kind $text)
     (((io:pending $id) (sm:embed-wait $id $dim)) ($error (MemoryUnavailable embedding $error)))))

(= (sm:hits $rows)
   (if
     (== $rows ())
     ()
     (let
       ($score (MemoryInfo $id $rev $doc $meta))
       (car-atom $rows)
       (cons-atom
         (MemoryHit $id $rev (memory:field $meta "time") (- 1.0 $score) $doc)
         (sm:hits (cdr-atom $rows))))))

(= (semantic:query $text $count)
   (if
     (semantic:native?)
     (let
       $_
       (semantic:maintain)
       (case
         (sm:open)
         (((MStore $space $profile $dim)
           (case
             (sm:embed "query" $text $dim)
             (((MemoryVector $vector) (sm:query-vector $vector $count)) ($error $error))))
          ($error $error))))
     (legacy-query $text $count)))

(= (sm:queue $id $action $text)
   (case
     (sm:open)
     (((MStore $space $profile $dim)
       (let*
         (($old (sm:pending-id $space $id))
          ($op (space:request-id))
          ($time (sm:time))
          ($pending (MemoryPending 1 $op $id $action $text $time 0 0 ""))
          ($ops
            (sm:append-parts
              ((sm:removes $old)
               ((add $pending) (add (MemoryChange 1 $op $id $action $time $text)))))))
         (case
           (sm:commit $space $ops)
           (((MemoryCommitted) (let $started (semantic:maintain) (MemoryQueued $id $op)))
            ((MemoryChanged) (sm:queue $id $action $text))
            ($error $error)))))
      ($error $error))))

(= (semantic:remember $text)
   (if (semantic:native?) (sm:queue (space:request-id) remember $text) (legacy-remember $text)))

(= (memory-edit $id $text) (sm:queue $id edit $text))

(= (memory-read $id)
   (case
     (sm:open)
     (((MStore $space $p $dim)
       (let
         $pending
         (sm:pending-id $space $id)
         (case
           (sm:one $space $id)
           (((Memory $id $rev $profile $vector $doc $meta)
             (MemoryCurrent $id $rev $doc $meta $pending))
            ((MemoryMissing $id) (MemoryCurrent $id absent "" () $pending))
            ($error $error)))))
      ($error $error))))

(= (sm:archive $space $row)
   (case
     $row
     (((Memory $id $r $p $v $d $m)
       (case
         (hm:one
           (sm:socket)
           $space
           (MemoryVersion 1 $id $r $vp $vv $vd $vm)
           (MemoryVersion 1 $id $r $vp $vv $vd $vm))
         (((HFailure missing-record $pattern) ((add (MemoryVersion 1 $id $r $p $v $d $m))))
          ((HFailure $why $at) (MemoryUnavailable archive $why))
          ($already ()))))
      ((MemoryUnavailable $where $why) $row)
      ($absent ()))))

(= (sm:tombstones $space $id)
   (sm:all $space (MemoryRetired 1 $id $op $time $reason) (MemoryRetired 1 $id $op $time $reason)))

(= (forget $id) (forget $id ""))

(= (forget $id $reason)
   (case
     (sm:open)
     (((MStore $space $p $dim)
       (let*
         (($row (sm:one $space $id))
          ($pending (sm:pending-id $space $id))
          ($markers (sm:tombstones $space $id))
          ($op (space:request-id))
          ($time (sm:time))
          ($remove-row (case $row (((Memory $i $r $p $v $d $m) ((remove $row))) ($other ()))))
          ($ops
            (sm:append-parts
              ($remove-row
                (sm:archive $space $row)
                (sm:removes $pending)
                (sm:removes $markers)
                ((add (MemoryRetired 1 $id $op $time $reason))
                 (add (MemoryChange 1 $op $id forget $time $reason)))))))
         (case
           (sm:commit $space $ops)
           (((MemoryCommitted) (let $_ (sm:unmirrored) (MemoryForgotten $id)))
            ((MemoryChanged) (forget $id $reason))
            ($error $error)))))
      ($error $error))))

(= (memory-history $id)
   (case
     (sm:open)
     (((MStore $space $p $dim)
       (MemoryHistory
         (sm:all $space (MemoryVersion 1 $id $r $p $v $d $m) (MemoryRevision $id $r $d $m))
         (sm:all $space (MemoryChange 1 $op $id $a $t $d) (MemoryChange 1 $op $id $a $t $d))))
      ($error $error))))

(= (memory-pending)
   (case (sm:open) (((MStore $space $p $dim) (sm:pending $space)) ($error $error))))

(= (sm:unmirrored)
   (let
     $directory
     (sm:field (sm:config) "state_dir" "")
     (if
       (== $directory "")
       True
       (catch
         (fs:write
           (str:join "/" ($directory "fallback.json"))
           (json-stringify
             (JsonObject
               ((JsonPair "current" (JsonBool False))
                (JsonPair "writer" (JsonString "native-metta"))))))))))

(= (semantic:io-ids) (collapse (match &semantic-http (MHTTP $op $id $handle) $handle)))

(= (sm:live? $op) (not (== (collapse (match &semantic-http (MHTTP $op $id $handle) True)) ())))

(= (sm:maintain-body)
   (if
     (semantic:native?)
     (let
       $_
       (sm:poll-all
         (collapse (match &semantic-http (MHTTP $op $id $handle) (MHTTP $op $id $handle))))
       (case
         (sm:open)
         (((MStore $space $profile $dim) (sm:start-pending (sm:pending $space))) ($error False))))
     True))

(= (sm:start-rows $rows)
   (if
     (== $rows ())
     True
     (let
       $head
       (car-atom $rows)
       (let
         (MemoryPending 1 $op $id $action $text $time $attempts $due $error)
         $head
         (let
           $started
           (if
             (or (sm:live? $op) (> $due (sm:now)))
             True
             (case
               (sm:embed-submit "document" $text)
               (((io:pending $handle) (add-atom &semantic-http (MHTTP $op $id $handle)))
                ($failure (sm:retry-write $head $failure)))))
           (sm:start-pending (cdr-atom $rows)))))))

(= (sm:poll-all $jobs)
   (if
     (== $jobs ())
     True
     (let
       (MHTTP $op $id $handle)
       (car-atom $jobs)
       (let
         $done
         (case
           (io:poll $handle)
           (((io:idle) True)
            ((io:event $handle $result)
             (let
               $removed
               (remove-atom &semantic-http (MHTTP $op $id $handle))
               (sm:finish-write $op $id $result)))
            ($failure
              (let
                $removed
                (remove-atom &semantic-http (MHTTP $op $id $handle))
                (sm:finish-write $op $id $failure)))))
         (sm:poll-all (cdr-atom $jobs))))))

(= (sm:finish-write $op $id $result)
   (case
     (sm:open)
     (((MStore $space $profile $dim)
       (case
         (hm:one
           (sm:socket)
           $space
           (MemoryPending 1 $op $id $action $text $time $attempts $due $error)
           (MemoryPending 1 $op $id $action $text $time $attempts $due $error))
         (((MemoryPending 1 $op $id $action $text $time $attempts $due $error)
           (let
             $pending
             (MemoryPending 1 $op $id $action $text $time $attempts $due $error)
             (case
               (sm:embed-result $result $dim)
               (((MemoryVector $vector) (sm:apply-write $pending $vector))
                ($failure (sm:retry-write $pending $failure))))))
          ($gone True))))
      ($error False))))

(= (sm:retry-write $pending $failure)
   (case
     $pending
     (((MemoryPending 1 $op $id $action $text $time $attempts $due $old-error)
       (case
         (sm:open)
         (((MStore $space $p $dim)
           (if
             (== (sm:pending-id $space $id) ($pending))
             (let
               $replacement
               (MemoryPending
                 1
                 $op
                 $id
                 $action
                 $text
                 $time
                 (+ $attempts 1)
                 (+ (sm:now) (sm:retry-seconds))
                 (repr $failure))
               (sm:commit $space ((remove $pending) (add $replacement))))
             True))
          ($error False)))))))

(= (sm:apply-write $pending $vector)
   (case
     $pending
     (((MemoryPending 1 $op $id $action $text $time $attempts $due $error)
       (case
         (sm:open)
         (((MStore (space $uuid $sid $sn $b $kind $rev $count) $profile $dim)
           (let
             $space
             (space $uuid $sid $sn $b $kind $rev $count)
             (if
               (== (sm:pending-id $space $id) ($pending))
               (sm:apply-current $space $profile $pending $vector (+ $rev 1))
               True)))
          ($error False)))))))

(= (semantic:retry-wait)
   (if
     (semantic:native?)
     (case
       (sm:open)
       (((MStore $space $p $dim)
         (let*
           (($rows (sm:pending $space))
            ($dues
              (collapse
                (case
                  (superpose $rows)
                  (((MemoryPending 1 $op $id $a $text $time $n $due $error)
                    (if (sm:live? $op) (empty) $due)))))))
           (if
             (== $dues ())
             ()
             (let*
               (($due (car-atom (sort-atom $dues)))
                ($milliseconds (math:ceil (max 0 (* 1000 (- $due (sm:now)))))))
               (case
                 (io:submit (timer:after $milliseconds))
                 (((io:pending $handle) ((memory-retry-wake $handle))) ($error ())))))))
        ($error ())))
     ()))

(= (memory-retry)
   (case
     (sm:open)
     (((MStore $space $p $dim)
       (let
         $rows
         (sm:pending $space)
         (let
           $ops
           (collapse
             (case
               (superpose $rows)
               (((MemoryPending 1 $op $id $a $text $time $n $due $error)
                 (superpose
                   ((remove (MemoryPending 1 $op $id $a $text $time $n $due $error))
                    (add (MemoryPending 1 $op $id $a $text $time $n 0 $error))))))))
           (case (sm:commit $space $ops) (((MemoryCommitted) (semantic:maintain)) ($error $error))))))
      ($error $error))))

(= (semantic:health)
   (if
     (semantic:native?)
     (case
       (semantic:query "memory retrieval health probe" 1)
       (((MemoryResults $hits (Unindexed $pending))
         (case
           (sm:open)
           (((MStore $space $profile $dim)
             (case
               (space:count (sm:socket) $space (Memory $id $r $p $v $doc $meta))
               (((reply $rid ok $count)
                 (if
                   (or (== $count 0) (> (size-atom $hits) 0))
                   (MemoryHealth healthy native (size-atom $hits) (Unindexed $pending))
                   (MemoryHealth unavailable retrieval-returned-no-hits)))
                ($error (MemoryHealth unavailable $error)))))
            ($error (MemoryHealth unavailable $error)))))
        ($error (MemoryHealth unavailable $error))))
     (py-call (memory_health.engine_query_probe))))

(= (sm:embed-json $value $dim)
   (if
     (== (sm:field $value "dim" 0) $dim)
     (case
       (json-object-get $value "embeddings")
       (((JsonArray ((JsonArray $items)))
         (if
           (== (size-atom $items) $dim)
           (case
             (sm:numbers $items)
             (((MemoryUnavailable $where $why) (MemoryUnavailable $where $why))
              ($numbers (sm:checked-vector (tensor:from f64 ($dim) $numbers)))))
           (MemoryUnavailable embedding dimension-mismatch)))
        ($bad (MemoryUnavailable embedding invalid-vector-batch))))
     (MemoryUnavailable embedding dimension-mismatch)))

(= (sm:checked-vector $vector)
   (case
     $vector
     (((TensorFailure $why) (MemoryUnavailable embedding $why))
      ($good
        (let
          $norm
          (tensor:norm $good)
          (if
            (and (> $norm 0) (not (or (isnan-math $norm) (isinf-math $norm))))
            (MemoryVector $good)
            (MemoryUnavailable embedding invalid-norm)))))))

(= (sm:query-vector $vector $count)
   (let
     $_
     (semantic:maintain)
     (case
       (sm:open)
       (((MStore $space $profile $dim)
         (case
           (sm:refresh $space $profile $dim)
           ((True
              (let
                $k
                (if (== $count 0) (size-atom (collapse (memory:matches All))) $count)
                (case
                  (memory:dispatch (MemoryQuery $profile $vector $k All))
                  (((MemoryFailure $at $why) (MemoryUnavailable retrieval $why))
                   ($rows
                     (MemoryResults (sm:hits $rows) (Unindexed (size-atom (sm:pending $space)))))))))
            ($error $error))))
        ($error $error)))))

(= (sm:append-parts $parts)
   (if (== $parts ()) ()
     (case (car-atom $parts)
       (((MemoryUnavailable $where $why) (MemoryUnavailable $where $why))
        ($head (case (sm:append-parts (cdr-atom $parts))
          (((MemoryUnavailable $where $why) (MemoryUnavailable $where $why))
           ($tail (hm:append $head $tail)))))))))

(= (sm:apply-current $space $profile $pending $vector $revision)
   (case
     $pending
     (((MemoryPending 1 $op $id $action $text $time $attempts $due $error)
       (let*
         (($old (sm:one $space $id))
          ($metadata
            (case
              $old
              (((Memory $i $r $p $v $d $m) (memory:patch $m (("updated_at" $time))))
               ($other (("time" $time))))))
          ($row (Memory $id $revision $profile $vector $text $metadata))
          ($remove (case $old (((Memory $i $r $p $v $d $m) ((remove $old))) ($absent ()))))
          ($markers (sm:tombstones $space $id))
          ($ops
            (sm:append-parts
              ($remove
                (sm:archive $space $old)
                (sm:removes $markers)
                ((remove $pending)
                 (add $row)
                 (add (MemoryVersion 1 $id $revision $profile $vector $text $metadata)))))))
         (case
           (sm:commit $space $ops)
           (((MemoryCommitted) (let $_ (sm:unmirrored) True))
            ((MemoryChanged) (sm:apply-write $pending $vector))
            ($failure (sm:retry-write $pending $failure)))))))))

(= (sm:all $space $pattern $template)
   (case
     (sm:snapshot $space $pattern $template)
     (((MSnapshot $revision (HFailure $why $at)) (MemoryUnavailable snapshot $why))
      ((MSnapshot $revision $rows) $rows) ($error $error))))

(= (semantic:maintain)
   (case (catch (sm:maintain-body)) (((Error $at $why) False) ($result $result))))

(= (sm:start-pending $rows)
   (case
     $rows
     (((MemoryUnavailable $where $why) False)
      ((HFailure $why $at) False)
      ($rows (sm:start-rows $rows)))))
