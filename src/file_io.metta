; One presentation layer for native CeTTa and SWI-PeTTa file reads.
; Select I/O once at startup, never on the file-read hot path.
!(import! &self ./src/engine_modes.py)
(= (file-io:cetta? $engine)
   (case $engine ((cetta True) ("cetta" True) ($other False))))
!(if (file-io:cetta? (py-call (engine_modes.active_engine)))
     (import! &self ./src/file_io_cetta)
     (import! &self ./src/file_io_python))

(= (read-file $path)
   (file-io:present $path (file-io:read $path 2000000)))
(= (read-file $path $max-bytes)
   (file-io:present $path (file-io:read $path $max-bytes)))

(= (file-io:present $path $result)
   (case $result
     ((("ok" $text)
       (if (== $text "")
           (file-io:join ("READ_OK_EMPTY_FILE: " $path " exists and holds zero bytes"))
           $text))
      (("limit" $limit)
       (let $number (file-io:number $limit)
         (file-io:join ("read failed: " $path " exceeds " $number
                       " bytes — use read-lines for a window, or supply a larger max_bytes (0 means unlimited)"))))
      (("missing" $cwd)
       (file-io:join ("read failed: no such file: " $path " (cwd " $cwd ")")))
      (("directory" $_)
       (let $quoted (repr $path)
         (file-io:join ("read failed: " $path " is a directory — use (ls-tree " $quoted " 1)"))))
      (("denied" $_)
       (file-io:join ("read failed: permission denied: " $path)))
      (("error" $class $detail)
       (file-io:join ("read failed (" $class "): " $detail)))
      ($other $other))))
