; Task policy over the original SQLite records. Sources and identities remain
; recoverable; summaries and excerpts do not replace the original records.
!(import! &self ./src/state_sqlite)

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
; scans of the complete action/attempt lists.
(= (task:pairs-sql)
   (let* (($exists (state:rows (state:query "SELECT 1 FROM sqlite_master WHERE name='command_attempts'" ())))
          ($attempts (if (== $exists ()) "SELECT '' AS ticket,0 AS position,NULL AS result WHERE 0" "SELECT ticket,position,result FROM command_attempts")))
     (str:join "" ("WITH RECURSIVE family(id) AS (SELECT ? UNION SELECT f.request FROM task_frames f JOIN family ON f.parent=family.id), "
       "issued AS (SELECT serial,request,ticket,command FROM actions WHERE rowid IN (SELECT MAX(rowid) FROM actions WHERE request IN family GROUP BY serial)), "
       "proposed AS (SELECT serial,request,commands,disposition FROM task_proposals WHERE request IN family UNION ALL "
       "SELECT serial,request,CASE WHEN json_type(command)='object' THEN COALESCE(json_extract(command,'$.commands'),'[]') ELSE json_array(json(command)) END,'legacy' FROM issued WHERE serial NOT IN (SELECT serial FROM task_proposals)), "
       "attempted AS (" $attempts ") "
       "SELECT p.serial,p.request,CAST(j.key AS INTEGER),CASE WHEN j.type IN ('array','object') THEN j.value WHEN j.type IN ('true','false') THEN j.type ELSE json_quote(j.value) END,t.result,t.ticket,p.disposition "
       "FROM proposed p JOIN json_each(p.commands) j LEFT JOIN issued a ON a.serial=p.serial "
       "LEFT JOIN attempted t ON t.ticket=a.ticket AND t.position=j.key "
       "WHERE p.disposition<>'legacy' OR t.ticket IS NOT NULL ORDER BY p.serial,CAST(j.key AS INTEGER)"))))
(= (task:row-pair $row $owner)
   (let* ((($serial $request $position $command $record $ticket $disposition) $row)
          ($result (if (== $ticket sqlite:null)
                     (JsonObject ((JsonPair "disposition" (JsonString "not_run"))
                                  (JsonPair "result" (JsonString $disposition))))
                     (if (== $record sqlite:null)
                       (JsonObject ((JsonPair "disposition" (JsonString "uncertain"))
                         (JsonPair "result" (JsonString "Interrupted; outcome unknown. This call was not automatically replayed."))))
                       (task:result $record))))
          ($identity (str:join ":" ($owner (repr $serial) (repr $position))))
          ($with-id (state:json-set $result "id" (JsonString $identity)))
          ($with-request (state:json-set $with-id "request" (JsonString $request)))
          ($decoded (json-parse $command)))
     (state:json-set $with-request "command" $decoded)))
(= (task:row-pairs $rows $owner)
   (if (== $rows ()) ()
     (cons-atom (task:row-pair (car-atom $rows) $owner) (task:row-pairs (cdr-atom $rows) $owner))))
(= (task:projection $request $trigger)
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
