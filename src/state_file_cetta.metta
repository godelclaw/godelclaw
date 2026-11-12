; Small state files, written whole: a JSON object read or updated, a file
; replaced at once by a renamed sibling, and a body run while holding a lock
; file. Coding sessions keep their state this way, and so does the Telegram
; client for what it publishes and its health facts. The engine's json and fs
; libraries read object fields and open, lock and rename files, but have no
; object update, whole-file replacement or lock scope of their own yet.
!(import! &self (library fs))
!(import! &self (library json))
!(import! &self (library str))
!(import! &self (library path))
!(import! &self (library system))

(= (sf:failed? $value)
   (case $value (((fs:failed $reason $operation $path $code $progress $message) True) ($other False))))

; The JSON object a file holds; the empty object when the file is missing or
; holds anything else. A parse failure is a value, not an error.
(= (sf:object $path)
   (if (fs:is-file $path)
     (case (catch (json-parse (fs:read-text $path)))
       (((JsonObject $pairs) (JsonObject $pairs))
        ($other (JsonObject ()))))
     (JsonObject ())))

; The object with key set to value: replaced where it stands, else added last.
(= (sf:put $object $key $value)
   (case $object
     (((JsonObject $pairs) (JsonObject (sf:put-pairs $pairs $key $value)))
      ($other (JsonObject ((JsonPair $key $value)))))))
(= (sf:put-pairs $pairs $key $value)
   (if (== $pairs ()) ((JsonPair $key $value))
     (let (JsonPair $name $old) (car-atom $pairs)
       (if (== $name $key) (cons-atom (JsonPair $key $value) (cdr-atom $pairs))
         (cons-atom (JsonPair $name $old) (sf:put-pairs (cdr-atom $pairs) $key $value))))))
(= (sf:put-all $object $pairs)
   (if (== $pairs ()) $object
     (let (JsonPair $key $value) (car-atom $pairs)
       (sf:put-all (sf:put $object $key $value) (cdr-atom $pairs)))))

; Replace a file whole: the text goes to a fresh sibling with the given mode,
; which is then renamed over the path. Durable also syncs the file before the
; rename and the directory after it. True; (SfUncertain failure) when the file
; is in place but the directory sync failed; otherwise the failure, with the
; sibling removed.
(= (sf:replace $path $text $mode $durable)
   (let* (($dir (path:dirname $path))
          ($temporary (path:join $dir (str:join "" ("." (sf:basename $path) "-" (system:uuid4-hex)))))
          ($writer (fs:open-write $temporary ("create" "exclusive") $mode)))
     (if (sf:failed? $writer) $writer
       (let* (($moded (fs:set-mode $writer $mode))
              ($wrote (fs:write-all $writer $text))
              ($synced (if (sf:failed? $wrote) $wrote (if $durable (fs:sync $writer) $wrote)))
              ($closed (fs:close $writer)))
         (if (sf:failed? $synced) (let $_ (fs:remove $temporary) $synced)
           (let $renamed (fs:rename $temporary $path)
             (if (sf:failed? $renamed) (let $_ (fs:remove $temporary) $renamed)
               (if (not $durable) True
                 (let $flushed (sf:sync-directory $dir)
                   (if (== $flushed True) True (SfUncertain $flushed)))))))))))
(= (sf:basename $path) (sf:last (str:split "/" $path)))
(= (sf:last $items) (if (== (cdr-atom $items) ()) (car-atom $items) (sf:last (cdr-atom $items))))
(= (sf:sync-directory $dir)
   (let $reader (fs:open-read $dir)
     (if (sf:failed? $reader) $reader
       (let* (($synced (fs:sync $reader)) ($_ (fs:close $reader)))
         (if (sf:failed? $synced) $synced True)))))

; Run body while holding an exclusive lock on lock-path, created with mode and
; kept at that mode whatever the umask; the lock is released whatever the body
; answers. The body's first answer, (SfNoAnswer), or (SfLockFailed failure).
(: sf:locked (-> %Undefined% %Undefined% Atom %Undefined%))
(= (sf:locked $lock-path $mode $body)
   (let $lock (fs:open-write $lock-path ("create" "append") $mode)
     (if (sf:failed? $lock) (SfLockFailed $lock)
       (let* (($moded (fs:set-mode $lock $mode))
              ($held (fs:lock $lock exclusive))
              ($result (collapse (catch (eval $body))))
              ($released (fs:unlock $lock))
              ($closed (fs:close $lock)))
         (if (== $result ()) (SfNoAnswer) (car-atom $result))))))
