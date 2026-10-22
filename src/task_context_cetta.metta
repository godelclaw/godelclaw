; Task policy over the original SQLite records. Sources and identities remain
; recoverable; summaries and excerpts do not replace the original records.
!(import! &self ./src/state_sqlite)
!(import! &self (library math))

(= (task:contains $items $value)
   (if (== $items ()) False
     (if (== (car-atom $items) $value) True (task:contains (cdr-atom $items) $value))))
(= (task:root $request) (task:root-walk $request ()))
(= (task:root-walk $request $seen)
   (if (task:contains $seen $request) (Error task:root cyclic-task-ancestry)
     (let $rows (state:rows (state:query "SELECT parent FROM task_frames WHERE request=?" ($request)))
       (if (== $rows ()) $request
         (let $parent (car-atom (car-atom $rows))
           (if (or (== $parent sqlite:null) (== $parent "")) $request
             (task:root-walk $parent (cons-atom $request $seen))))))))
(= (task:family-sql $suffix)
   (str:concat "WITH RECURSIVE family(id) AS (SELECT ? UNION SELECT f.request FROM task_frames f JOIN family ON f.parent=family.id) " $suffix))
(= (task:statement $sql $root)
   (let $source (task:family-sql $sql) (sqlite:statement $source ($root))))
(= (task:objects $result)
   (case $result (((sqlite:result $names $rows $_ $_) (state:objects $names $rows))
                 ($error (Error task:objects $error)))))
(= (task:member $record $key $default)
   (case (json-object-get $record $key) ((JsonNull $default) ($value $value))))
(= (task:string $record $key)
   (state:text (state:json-field $record $key)))
(= (task:result $record)
   (let $decoded (catch (json-parse $record))
     (case $decoded
       (((JsonObject $_) $decoded)
        ($error (JsonObject ((JsonPair "disposition" (JsonString "recorded"))
                            (JsonPair "result_raw" (JsonString $record))
                            (JsonPair "decode_error" (JsonString (repr $error))))))))))

; SQLite joins source records once; the MeTTa view performs no repeated
; scans of the complete action/attempt lists. Materialize the issued selection:
; otherwise SQLite may re-run its grouped actions scan for every joined call.
(= (task:pairs-sql)
   (task:scoped-pairs-sql "SELECT ? UNION SELECT f.request FROM task_frames f JOIN family ON f.parent=family.id" "t.result" ""))
(= (task:scoped-pairs-sql $family $result $tail)
   (let* (($exists (state:rows (state:query "SELECT 1 FROM sqlite_master WHERE name='command_attempts'" ())))
          ($attempts (if (== $exists ()) "SELECT '' AS ticket,0 AS position,NULL AS result WHERE 0" "SELECT ticket,position,result FROM command_attempts")))
     (str:join "" ("WITH RECURSIVE family(id) AS (" $family "), "
       "issued AS MATERIALIZED (SELECT serial,request,ticket,command FROM actions WHERE rowid IN (SELECT MAX(rowid) FROM actions WHERE request IN family GROUP BY serial)), "
       "proposed AS (SELECT serial,request,commands,disposition FROM task_proposals WHERE request IN family UNION ALL "
       "SELECT serial,request,CASE WHEN json_type(command)='object' THEN COALESCE(json_extract(command,'$.commands'),'[]') ELSE json_array(json(command)) END,'legacy' FROM issued WHERE serial NOT IN (SELECT serial FROM task_proposals)), "
       "attempted AS (" $attempts ") "
       "SELECT p.serial,p.request,CAST(j.key AS INTEGER),CASE WHEN j.type IN ('array','object') THEN j.value WHEN j.type IN ('true','false') THEN j.type ELSE json_quote(j.value) END," $result ",t.ticket,p.disposition "
       "FROM proposed p JOIN json_each(p.commands) j LEFT JOIN issued a ON a.serial=p.serial "
       "LEFT JOIN attempted t ON t.ticket=a.ticket AND t.position=j.key "
       "WHERE p.disposition<>'legacy' OR t.ticket IS NOT NULL ORDER BY p.serial,CAST(j.key AS INTEGER)" $tail))))

; Clip presentation text before it crosses the SQLite boundary. Original rows
; and identities stay untouched; task-read still reads the complete result.
(= (task:limited-result-sql $limit)
   (if (<= $limit 0) "t.result"
     (let* (($half (repr (math:floor (/ $limit 2)))) ($size (repr $limit)))
       (str:join "" ("CASE WHEN json_valid(t.result) THEN CASE WHEN json_type(t.result,'$.result')='text' AND length(json_extract(t.result,'$.result'))>" $size
         " THEN json_set(t.result,'$.result',substr(json_extract(t.result,'$.result'),1," $half ")||char(10)||'…'||CAST((length(json_extract(t.result,'$.result'))-" $size "+3)/4 AS INTEGER)||' tokens truncated; task-read shows the whole result…'||char(10)||substr(json_extract(t.result,'$.result'),-" $half "),'$.result_truncated',json('true')) ELSE t.result END ELSE t.result END")))))
(= (task:row-pair $row $owner)
   (let* ((($serial $request $position $command $record $ticket $disposition) $row)
          ($result (if (== $ticket sqlite:null)
                     (JsonObject ((JsonPair "disposition" (JsonString "not_run"))
                                  (JsonPair "result" (JsonString $disposition))))
                     (if (== $record sqlite:null)
                       (JsonObject ((JsonPair "disposition" (JsonString "uncertain"))
                         (JsonPair "result" (JsonString "Interrupted; outcome unknown. This call was not automatically replayed."))))
                       (task:result $record))))
          ($identity (if (== $serial -1) (str:concat "task-input:" $request)
                       (str:join ":" ($owner (repr $serial) (repr $position)))))
          ($with-id (state:json-set $result "id" (JsonString $identity)))
          ($with-request (state:json-set $with-id "request" (JsonString $request)))
          ($decoded (json-parse $command)))
     (state:json-set $with-request "command" $decoded)))
(= (task:row-pairs $rows $owner)
   (if (== $rows ()) ()
     (cons-atom (task:row-pair (car-atom $rows) $owner) (task:row-pairs (cdr-atom $rows) $owner))))
(= (task:projection $request $trigger)
   (if (str:starts-with $request "conversation:")
     (if (== $request (task:conversation-root))
       (task:conversation-projection (state:text (state:meta "context_focus")) $trigger 0 0)
       (Error task:projection another-ledger))
     (task:family-projection $request $trigger)))
(= (task:family-projection $request $trigger)
   (let* (($root (task:root $request))
          ($requests (task:statement "SELECT r.id,r.content,r.status,c.kind,c.note FROM requests r JOIN request_context c ON c.request=r.id WHERE r.id IN family ORDER BY r.rowid" $root))
          ($notes (task:statement "SELECT * FROM task_notes WHERE request IN family ORDER BY sequence" $root))
          ($source (task:pairs-sql))
          ($pairs (sqlite:statement $source ($root)))
          ($plans (cons-atom $requests (cons-atom $notes (cons-atom $pairs ()))))
          ((sqlite:committed ($requested $noted $paired)) (state:transaction deferred $plans))
          ($owner (state:text (state:meta "owner")))
          ($transcript (task:row-pairs (state:rows $paired) $owner))
          ($request-objects (task:objects $requested)) ($note-objects (task:objects $noted)))
     (JsonObject ((JsonPair "root" (JsonString $root)) (JsonPair "focus" (JsonString $request))
       (JsonPair "trigger" (JsonString $trigger)) (JsonPair "requests" (JsonArray $request-objects))
       (JsonPair "notes" (JsonArray $note-objects)) (JsonPair "transcript" (JsonArray $transcript))
       (JsonPair "archive" JsonNull)))))

(= (task:conversation-root)
   (str:concat "conversation:" (state:text (state:meta "owner"))))
; Interleave immutable input deliveries with the calls captured after them.
; Both orderings come from the original event journal. A serial fallback keeps
; older, unframed call records readable without inventing new occurrences.
(= (task:conversation-sql $result $input-limit $tail)
   (str:join "" ((task:conversation-base-sql $result $input-limit)
     "SELECT serial,request,position,command,result,ticket,disposition FROM ordered ORDER BY ordering,category,ordinal" $tail)))
(= (task:conversation-base-sql $result $input-limit)
   (let* (($calls (task:scoped-pairs-sql "SELECT id FROM requests" $result ""))
          ($content (if (== $input-limit -2) "min(length(CAST(r.content AS BLOB)),160000)"
                        (if (< $input-limit 0) "NULL" (task:input-text-sql $input-limit))))
          ($record (str:join "" ("json_object('disposition','input','result'," $content ")"))))
     (str:join "" ("WITH calls(serial,request,position,command,result,ticket,disposition) AS (" $calls "), "
       "ordered AS (SELECT -1 AS serial,r.id AS request,-1 AS position,'null' AS command," $record " AS result,'input' AS ticket,'input' AS disposition,COALESCE((SELECT min(e.sequence) FROM events e WHERE json_extract(e.event,'$.kind')='deliver' AND json_extract(e.event,'$.request')=+r.id),0) AS ordering,0 AS category,r.rowid AS ordinal "
       "FROM requests r UNION ALL "
       "SELECT c.*,COALESCE((SELECT min(e.sequence) FROM events e WHERE json_extract(e.event,'$.kind')='capture' AND json_extract(e.event,'$.serial')=+c.serial),(SELECT COALESCE(max(sequence),0) FROM events)+c.serial+1),1,c.position FROM calls c) "))))
(= (task:input-text-sql $limit)
   (if (<= $limit 0) "r.content"
     (let $half (repr (math:floor (/ $limit 2)))
       (str:join "" ("CASE WHEN length(r.content)>" (repr $limit)
         " THEN substr(r.content,1," $half ")||char(10)||'[input excerpt; request-read retains the complete source]'||char(10)||substr(r.content,-" $half ") ELSE r.content END")))))
(= (task:conversation-projection $request $trigger $offset $limit)
   (let* (($root (task:conversation-root)) ($task-root (task:root $request))
          ($requests (if (<= $limit 0)
             (sqlite:statement "SELECT r.id,r.content,r.status,c.kind,c.note FROM requests r JOIN request_context c ON c.request=r.id ORDER BY r.rowid" ())
             (sqlite:statement "SELECT r.id,r.status,c.kind,c.note,substr(r.content,1,160) AS excerpt FROM requests r JOIN request_context c ON c.request=r.id WHERE (r.status IN ('active','waiting-user','deferred') OR (r.status='processed' AND c.kind IN ('message','notification'))) OR r.id=? ORDER BY r.rowid" ($request))))
          ($notes (if (<= $limit 0)
             (sqlite:statement "SELECT * FROM task_notes ORDER BY sequence" ())
             (sqlite:statement "SELECT n.* FROM task_notes n JOIN requests r ON r.id=n.request JOIN request_context c ON c.request=r.id WHERE (r.status IN ('active','waiting-user','deferred') OR (r.status='processed' AND c.kind IN ('message','notification'))) OR r.id=? ORDER BY n.sequence" ($request))))
          ($source (task:conversation-sql (task:limited-result-sql $limit) $limit " LIMIT -1 OFFSET ?"))
          ($pairs (sqlite:statement $source ($offset)))
          ((sqlite:committed ($requested $noted $paired)) (state:transaction deferred ($requests $notes $pairs)))
          ($owner (state:text (state:meta "owner")))
          ($transcript (task:row-pairs (state:rows $paired) $owner)))
     (JsonObject ((JsonPair "root" (JsonString $root)) (JsonPair "task_root" (JsonString $task-root))
       (JsonPair "focus" (JsonString $request)) (JsonPair "trigger" (JsonString $trigger))
       (JsonPair "offset" (JsonNumber (repr $offset)))
       (JsonPair "requests" (JsonArray (task:objects $requested)))
       (JsonPair "notes" (JsonArray (task:objects $noted)))
       (JsonPair "transcript" (JsonArray $transcript)) (JsonPair "archive" JsonNull)))))

; One-time first-start selection uses integer sizes inside SQLite. Only the
; selected window's text crosses into MeTTa; old bodies remain on disk.
(= (task:initial-start $capacity $ratio)
   (let* (($base (task:conversation-base-sql "json_object('bytes',min(length(CAST(t.result AS BLOB)),160000))" -2))
          ($sql (str:join "" ($base
            ", sized AS (SELECT row_number() OVER (ORDER BY ordering,category,ordinal)-1 AS n,"
            "COALESCE(CASE WHEN serial=-1 THEN json_extract(result,'$.result') ELSE json_extract(result,'$.bytes') END,0)+length(CAST(command AS BLOB))+length(request)+160 AS bytes FROM ordered), "
            "ranked AS (SELECT n,SUM(bytes) OVER (ORDER BY n DESC) AS tail_bytes FROM sized) "
            "SELECT CASE WHEN COALESCE((SELECT SUM(bytes) FROM sized),0)<=? THEN 0 "
            "ELSE COALESCE((SELECT min(n) FROM ranked WHERE tail_bytes<=?),(SELECT max(n) FROM sized),0) END")))
          ($rows (state:rows (state:query $sql ((* 0.9 (* $capacity $ratio)) (* 0.2 (* $capacity $ratio)))))))
     (car-atom (car-atom $rows))))
(= (task:initial-view $focus $trigger $capacity $ratio)
   (let $rows (state:rows (state:query "SELECT 1 FROM meta WHERE key=?" ((task:window-key (task:conversation-root)))))
     (if (== $rows ())
       (task:conversation-projection $focus $trigger (task:initial-start $capacity $ratio) (task:result-chars))
       (task:conversation-view $focus $trigger))))
