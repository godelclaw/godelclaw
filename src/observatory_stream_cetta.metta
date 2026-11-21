; Unrestricted O(1)-memory streaming JSONL frame scanner for oversized
; observatory records (effect receipts and systemd journal entries).
; Ordinary lines fit inside the fast in-memory buffer and use json:parse-python.
; When a single line exceeds that buffer, this module streams chunks through a
; single-pass top-level JSON object extractor that skips multi-megabyte values
; ("result", huge "MESSAGE", huge "command" bodies) via compiled C-level regex
; jumps while retaining exact metadata fields, pattern hits, and balanced-JSON
; validation (so truncated final records are still rejected explicitly).
!(import! &self (library str))
!(import! &self (library re))
!(import! &self (library json))
!(import! &self (library list))

; Fast SIMD prefilter for string stops (" or \) plus single-step escape-run
; regex when backslashes are present (avoids deep recursion on escaped JSON).
!(bind! &obs-re-str-stop (new-state (re:compile "[\\\\\"]")))
!(bind! &obs-re-str-run (new-state (re:compile "(?s)^(?:[^\"\\\\]|\\\\.)*")))
!(bind! &obs-re-prim-stop (new-state (re:compile "[,}\\s]")))
!(bind! &obs-re-struct-stop (new-state (re:compile "[\"{}\\[\\]]")))
!(bind! &obs-re-ws-stop (new-state (re:compile "[^\\s]")))

; Pre-compiled observatory extraction patterns shared with observatory_cetta.metta.
!(bind! &obs-re-model (new-state (re:compile "\\(active-model ([^)\\s]+)\\)")))
!(bind! &obs-re-py-kind (new-state (re:compile "^\\[\\s*['\"]([A-Za-z_][\\w!-]*)['\"]")))
!(bind! &obs-re-metta-kind (new-state (re:compile "^\\(\\s*([A-Za-z_][\\w!-]*)(?:[\\s)]|$)")))
!(bind! &obs-re-systemd (new-state (re:compile "Started|Stopped|Failed|exited|Stopping")))
!(bind! &obs-re-usage (new-state (re:compile "\\[synthetic_llm\\] (\\w+) usage: in=(\\d+) cache_read=(\\d+) cache_write=(\\d+) out=(\\d+)")))
!(bind! &obs-re-failed (new-state (re:compile "engine '(\\w+)' failed \\((.*)\\)")))
!(bind! &obs-re-failed-raw (new-state (re:compile "engine '(\\w+)' failed \\(((?:[^\\r\\n\\\\]|\\\\[^nr])*)\\)")))

(= (oscan:contains? $hay $needle)
   (case (str:find $hay $needle) (((TextNotFound $_) False) ($at True))))

(= (oscan:new)
   ; (BigJson phase depth in-str esc key-buf cur-key val-buf val-kind val-trunc seen-open done bad fields msg-tail msg-hits)
   (BigJson before-obj 0 False False "" "" "" skip False False False False () "" (MsgHits False JsonNull False JsonNull False)))

(= (oscan:small-key? $key)
   (or (== $key "__CURSOR")
   (or (== $key "__REALTIME_TIMESTAMP")
   (or (== $key "_COMM")
   (or (== $key "SYSLOG_IDENTIFIER")
   (or (== $key "recorded_at")
   (or (== $key "turn")
   (or (== $key "disposition")
       (== $key "reason")))))))))

(= (oscan:put-field $fields $key $value)
   (cons-atom (JsonPair $key $value) $fields))

(= (oscan:tail-256 $text)
   (let $len (str:byte-length $text)
     (if (<= $len 256) $text (str:byte-slice $text (- $len 256) $len))))

; Decode a captured JSON string fragment (possibly truncated before closing quote).
; Drops a trailing incomplete escape sequence (including split \uXXXX) so
; json:parse-python decodes the intact prefix cleanly.
(= (oscan:trim-trailing-bs $raw)
   (let $len (str:byte-length $raw)
     (if (== $len 0) ""
       (if (== (str:byte-slice $raw (- $len 1) $len) "\\")
         (oscan:trim-trailing-bs (str:byte-slice $raw 0 (- $len 1)))
         $raw))))

(= (oscan:drop-incomplete-u $raw $left)
   (let $len (str:byte-length $raw)
     (if (or (<= $left 0) (<= $len 0)) ""
       (if (== (str:byte-slice $raw (- $len 1) $len) "\\")
         (oscan:trim-trailing-bs (str:byte-slice $raw 0 (- $len 1)))
         (oscan:drop-incomplete-u (str:byte-slice $raw 0 (- $len 1)) (- $left 1))))))

(= (oscan:decode-str-prefix $raw)
   (let* (($safe (oscan:trim-trailing-bs $raw))
          ($parsed (json:parse-python (str:join "" ("\"" $safe "\"")))))
     (if (== (get-type $parsed) String) $parsed
       (let* (($trimmed (oscan:drop-incomplete-u $safe 6))
              ($retry (json:parse-python (str:join "" ("\"" $trimmed "\"")))))
         (if (== (get-type $retry) String) $retry $safe)))))

(= (oscan:decode-json $raw)
   (let $parsed (json:parse-python $raw)
     (case $parsed
       (((JsonPythonErrorV1 $_1 $_2 $_3) JsonNull)
        ($ok $ok)))))

; Scan a MESSAGE chunk (with 256-byte carry) for all journal event patterns in O(1) memory.
(= (oscan:scan-msg-hits $window (MsgHits $sys $usage $trans $fail $restored))
   (let* (($sys2 (if $sys True (re:is-match (get-state &obs-re-systemd) $window)))
          ($usage2 (if (!= $usage JsonNull) $usage
                     (let $um (re:captures (get-state &obs-re-usage) $window)
                       (case $um
                         (((re:match $_u1 $_u2 $_u3)
                           (UsageHit (re:group-text $um $window 1)
                                     (re:group-text $um $window 2)
                                     (re:group-text $um $window 3)
                                     (re:group-text $um $window 4)
                                     (re:group-text $um $window 5)))
                          ($no-u JsonNull))))))
          ($trans2 (if $trans True (oscan:contains? $window "[synthetic_llm] transient")))
          ($fail2 (if (!= $fail JsonNull) $fail
                    (let $fm (re:captures (get-state &obs-re-failed-raw) $window)
                      (case $fm
                        (((re:match $_f1 $_f2 $_f3)
                          (FailHit (re:group-text $fm $window 1)
                                   (oscan:decode-str-prefix (re:group-text $fm $window 2))))
                         ($no-f JsonNull))))))
          ($rest2 (if $restored True (oscan:contains? $window "restoring stable engine"))))
     (MsgHits $sys2 $usage2 $trans2 $fail2 $rest2)))

(= (oscan:append-capped $buf $seg $cap $trunc)
   (if $trunc ($buf True)
     (let* (($cur (str:byte-length $buf))
            ($need (- $cap $cur)))
       (if (<= $need 0) ($buf True)
         (let $slen (str:byte-length $seg)
           (if (<= $slen $need) ((str:concat $buf $seg) False)
             ((str:concat $buf (str:byte-slice $seg 0 $need)) True)))))))

(= (oscan:find-nonws $chunk $pos $len)
   (if (>= $pos $len) -1
     (let $sub (str:byte-slice $chunk $pos $len)
       (case (re:find (get-state &obs-re-ws-stop) $sub)
         (((re:match $at $_1 $_2) (+ $pos $at))
          ($none -1))))))

(= (oscan:str-run-end $chunk $pos $len)
   (let $sub (str:byte-slice $chunk $pos $len)
     (case (re:find (get-state &obs-re-str-stop) $sub)
       (((re:match $rel $_1 $_2)
         (let $stop (+ $pos $rel)
           (if (== (str:byte-slice $chunk $stop (+ $stop 1)) "\"")
             $stop
             (case (re:find (get-state &obs-re-str-run) $sub)
               (((re:match $_s1 $end $_s2) (+ $pos $end))
                ($none $pos))))))
        ($none $len)))))

(= (oscan:finalize-field $cur-key $val-kind $val-buf $fields $msg-hits)
   (case $val-kind
     ((capture-scalar
       (oscan:put-field $fields $cur-key (oscan:decode-json $val-buf)))
      (capture-str-scalar
       (oscan:put-field $fields $cur-key (oscan:decode-str-prefix $val-buf)))
      (capture-cmd-str
       (oscan:put-field $fields "command" (oscan:decode-str-prefix $val-buf)))
      (capture-cmd-arr-elem
       (oscan:put-field $fields "command" (JsonArray ((oscan:decode-str-prefix $val-buf)))))
      (capture-message
       (let* (($prefix (oscan:decode-str-prefix $val-buf))
              ($f1 (oscan:put-field $fields "MESSAGE" $prefix)))
         (oscan:put-field $f1 "__OBS_MSG_HITS__" $msg-hits)))
      ($skip $fields))))

; Feed an arbitrary byte chunk belonging to ONE JSONL line (no '\n' inside $chunk).
(= (oscan:feed $state $chunk)
   (let $len (str:byte-length $chunk)
     (if (== $len 0) $state
       (oscan:step $state $chunk 0 $len))))

(= (oscan:step (BigJson $phase $depth $in-str $esc $key-buf $cur-key $val-buf $val-kind $val-trunc $seen-open $done $bad $fields $msg-tail $msg-hits) $chunk $pos $len)
   (if (or $bad (>= $pos $len))
     (BigJson $phase $depth $in-str $esc $key-buf $cur-key $val-buf $val-kind $val-trunc $seen-open $done $bad $fields $msg-tail $msg-hits)
     (case $phase
       ((before-obj
         (let $at (oscan:find-nonws $chunk $pos $len)
           (if (< $at 0)
             (BigJson before-obj $depth $in-str $esc $key-buf $cur-key $val-buf $val-kind $val-trunc $seen-open $done $bad $fields $msg-tail $msg-hits)
             (if (== (str:byte-slice $chunk $at (+ $at 1)) "{")
               (oscan:step (BigJson expect-key 1 False False "" "" "" skip False True False False $fields $msg-tail $msg-hits) $chunk (+ $at 1) $len)
               (BigJson before-obj 0 False False "" "" "" skip False False False True $fields $msg-tail $msg-hits)))))

        (after-obj
         (let $at (oscan:find-nonws $chunk $pos $len)
           (if (< $at 0)
             (BigJson after-obj 0 False False "" "" "" skip False True True False $fields $msg-tail $msg-hits)
             (BigJson after-obj 0 False False "" "" "" skip False True False True $fields $msg-tail $msg-hits))))

        (expect-key
         (let $at (oscan:find-nonws $chunk $pos $len)
           (if (< $at 0)
             (BigJson expect-key $depth $in-str $esc $key-buf $cur-key $val-buf $val-kind $val-trunc $seen-open $done $bad $fields $msg-tail $msg-hits)
             (let $ch (str:byte-slice $chunk $at (+ $at 1))
               (if (== $ch ",")
                 (oscan:step (BigJson expect-key 1 False False "" "" "" skip False True False False $fields $msg-tail $msg-hits) $chunk (+ $at 1) $len)
                 (if (== $ch "}")
                   (oscan:step (BigJson after-obj 0 False False "" "" "" skip False True True False $fields $msg-tail $msg-hits) $chunk (+ $at 1) $len)
                   (if (== $ch "\"")
                     (oscan:step (BigJson in-key 1 True False "" "" "" skip False True False False $fields $msg-tail $msg-hits) $chunk (+ $at 1) $len)
                     (BigJson expect-key 1 False False "" "" "" skip False True False True $fields $msg-tail $msg-hits))))))))

        (in-key
         (if $esc
           (let* (($c (str:byte-slice $chunk $pos (+ $pos 1)))
                  (($kb2 $kt2) (oscan:append-capped $key-buf (str:concat "\\" $c) 256 False)))
             (oscan:step (BigJson in-key 1 True False $kb2 $cur-key $val-buf $val-kind $val-trunc True False False $fields $msg-tail $msg-hits) $chunk (+ $pos 1) $len))
           (let* (($stop (oscan:str-run-end $chunk $pos $len))
                  ($seg (if (> $stop $pos) (str:byte-slice $chunk $pos $stop) ""))
                  (($kb2 $kt2) (if (> $stop $pos) (oscan:append-capped $key-buf $seg 256 False) ($key-buf False))))
             (if (< $stop $len)
               (if (== (str:byte-slice $chunk $stop (+ $stop 1)) "\\")
                 (BigJson in-key 1 True True $kb2 $cur-key $val-buf $val-kind $val-trunc True False False $fields $msg-tail $msg-hits)
                 (let $decoded (oscan:decode-str-prefix $kb2)
                   (oscan:step (BigJson expect-colon 1 False False "" $decoded "" skip False True False False $fields $msg-tail $msg-hits) $chunk (+ $stop 1) $len)))
               (BigJson in-key 1 True False $kb2 $cur-key $val-buf $val-kind $val-trunc True False False $fields $msg-tail $msg-hits)))))

        (expect-colon
         (let $at (oscan:find-nonws $chunk $pos $len)
           (if (< $at 0)
             (BigJson expect-colon 1 False False "" $cur-key "" skip False True False False $fields $msg-tail $msg-hits)
             (if (== (str:byte-slice $chunk $at (+ $at 1)) ":")
               (oscan:step (BigJson expect-val 1 False False "" $cur-key "" skip False True False False $fields $msg-tail $msg-hits) $chunk (+ $at 1) $len)
               (BigJson expect-colon 1 False False "" $cur-key "" skip False True False True $fields $msg-tail $msg-hits)))))

        (expect-val
         (let $at (oscan:find-nonws $chunk $pos $len)
           (if (< $at 0)
             (BigJson expect-val 1 False False "" $cur-key "" skip False True False False $fields $msg-tail $msg-hits)
             (let $ch (str:byte-slice $chunk $at (+ $at 1))
               (if (== $ch "\"")
                 (let $vk (if (oscan:small-key? $cur-key) capture-str-scalar
                            (if (== $cur-key "command") capture-cmd-str
                              (if (== $cur-key "MESSAGE") capture-message skip)))
                   (oscan:step (BigJson in-val-str 1 True False "" $cur-key "" $vk False True False False $fields "" $msg-hits) $chunk (+ $at 1) $len))
                 (if (or (== $ch "[") (== $ch "{"))
                   (let* (($f2 (if (== $cur-key "commands") (oscan:put-field $fields "commands" True) $fields))
                          ($phase2 (if (and (== $cur-key "command") (== $ch "[")) cmd-arr-start in-val-nested)))
                     (oscan:step (BigJson $phase2 2 False False "" $cur-key "" skip False True False False $f2 $msg-tail $msg-hits) $chunk (+ $at 1) $len))
                   (let* (($vk (if (oscan:small-key? $cur-key) capture-scalar skip))
                          ($f2 (if (and (== $cur-key "commands") (!= $ch "n")) (oscan:put-field $fields "commands" True) $fields)))
                     (oscan:step (BigJson in-val-prim 1 False False "" $cur-key "" $vk False True False False $f2 $msg-tail $msg-hits) $chunk $at $len))))))))

        (cmd-arr-start
         (let $at (oscan:find-nonws $chunk $pos $len)
           (if (< $at 0)
             (BigJson cmd-arr-start 2 False False "" $cur-key "" skip False True False False $fields $msg-tail $msg-hits)
             (let $ch (str:byte-slice $chunk $at (+ $at 1))
               (if (== $ch "\"")
                 (oscan:step (BigJson in-val-str 2 True False "" $cur-key "" capture-cmd-arr-elem False True False False $fields $msg-tail $msg-hits) $chunk (+ $at 1) $len)
                 (if (== $ch "]")
                   (let $f2 (oscan:put-field $fields "command" (JsonArray ()))
                     (oscan:step (BigJson expect-key 1 False False "" "" "" skip False True False False $f2 $msg-tail $msg-hits) $chunk (+ $at 1) $len))
                   (oscan:step (BigJson in-val-nested 2 False False "" $cur-key "" skip False True False False $fields $msg-tail $msg-hits) $chunk $at $len)))))))

        (in-val-str
         (oscan:step-str $depth $esc $cur-key $val-buf $val-kind $val-trunc $fields $msg-tail $msg-hits $chunk $pos $len))

        (in-val-prim
         (let $sub (str:byte-slice $chunk $pos $len)
           (case (re:find (get-state &obs-re-prim-stop) $sub)
             (((re:match $rel $_p1 $_p2)
               (let* (($stop (+ $pos $rel))
                      (($vb2 $vt2) (if (and (== $val-kind capture-scalar) (> $stop $pos))
                                     (oscan:append-capped $val-buf (str:byte-slice $chunk $pos $stop) 4096 $val-trunc)
                                     ($val-buf $val-trunc)))
                      ($f2 (oscan:finalize-field $cur-key $val-kind $vb2 $fields $msg-hits)))
                 (oscan:step (BigJson expect-key 1 False False "" "" "" skip False True False False $f2 $msg-tail $msg-hits) $chunk $stop $len)))
              ($none
               (let ($vb2 $vt2) (if (== $val-kind capture-scalar)
                                   (oscan:append-capped $val-buf $sub 4096 $val-trunc) ($val-buf $val-trunc))
                 (BigJson in-val-prim 1 False False "" $cur-key $vb2 $val-kind $vt2 True False False $fields $msg-tail $msg-hits)))))))

        (in-val-nested
         (oscan:step-nested $depth $in-str $esc $cur-key $fields $msg-tail $msg-hits $chunk $pos $len))))))

(= (oscan:str-cap $val-kind)
   (case $val-kind ((capture-str-scalar 4096) (capture-message 512) (capture-cmd-str 256) (capture-cmd-arr-elem 256) ($other 0))))

(= (oscan:step-str $depth $esc $cur-key $val-buf $val-kind $val-trunc $fields $msg-tail $msg-hits $chunk $pos $len)
   (if $esc
     (let* (($cap (oscan:str-cap $val-kind))
            ($need-seg (or (> $cap 0) (== $val-kind capture-message)))
            ($pair (if $need-seg (str:concat "\\" (str:byte-slice $chunk $pos (+ $pos 1))) ""))
            (($vb2 $vt2) (if (> $cap 0) (oscan:append-capped $val-buf $pair $cap $val-trunc) ($val-buf $val-trunc)))
            (($mt2 $mh2) (if (== $val-kind capture-message)
                           (let $w (str:concat $msg-tail $pair) ((oscan:tail-256 $w) (oscan:scan-msg-hits $w $msg-hits)))
                           ($msg-tail $msg-hits))))
       (oscan:step (BigJson in-val-str $depth True False "" $cur-key $vb2 $val-kind $vt2 True False False $fields $mt2 $mh2) $chunk (+ $pos 1) $len))
     (let* (($stop (oscan:str-run-end $chunk $pos $len))
            ($cap (oscan:str-cap $val-kind))
            ($has-seg (> $stop $pos))
            ($need-seg (and $has-seg (or (> $cap 0) (== $val-kind capture-message))))
            ($seg (if $need-seg (str:byte-slice $chunk $pos $stop) ""))
            (($vb2 $vt2) (if (and $has-seg (> $cap 0)) (oscan:append-capped $val-buf $seg $cap $val-trunc) ($val-buf $val-trunc)))
            (($mt2 $mh2) (if (and $has-seg (== $val-kind capture-message))
                           (let $w (str:concat $msg-tail $seg) ((oscan:tail-256 $w) (oscan:scan-msg-hits $w $msg-hits)))
                           ($msg-tail $msg-hits))))
       (if (< $stop $len)
         (if (== (str:byte-slice $chunk $stop (+ $stop 1)) "\\")
           (BigJson in-val-str $depth True True "" $cur-key $vb2 $val-kind $vt2 True False False $fields $mt2 $mh2)
           (let $f2 (oscan:finalize-field $cur-key $val-kind $vb2 $fields $mh2)
             (if (== $depth 1)
               (oscan:step (BigJson expect-key 1 False False "" "" "" skip False True False False $f2 "" $mh2) $chunk (+ $stop 1) $len)
               (oscan:step (BigJson in-val-nested $depth False False "" $cur-key "" skip False True False False $f2 "" $mh2) $chunk (+ $stop 1) $len))))
         (BigJson in-val-str $depth True False "" $cur-key $vb2 $val-kind $vt2 True False False $fields $mt2 $mh2)))))

(= (oscan:step-nested $depth $in-str $esc $cur-key $fields $msg-tail $msg-hits $chunk $pos $len)
   (if $in-str
     (if $esc
       (oscan:step (BigJson in-val-nested $depth True False "" $cur-key "" skip False True False False $fields $msg-tail $msg-hits) $chunk (+ $pos 1) $len)
       (let $stop (oscan:str-run-end $chunk $pos $len)
         (if (< $stop $len)
           (if (== (str:byte-slice $chunk $stop (+ $stop 1)) "\\")
             (BigJson in-val-nested $depth True True "" $cur-key "" skip False True False False $fields $msg-tail $msg-hits)
             (oscan:step (BigJson in-val-nested $depth False False "" $cur-key "" skip False True False False $fields $msg-tail $msg-hits) $chunk (+ $stop 1) $len))
           (BigJson in-val-nested $depth True False "" $cur-key "" skip False True False False $fields $msg-tail $msg-hits))))
     (let $sub (str:byte-slice $chunk $pos $len)
       (case (re:find (get-state &obs-re-struct-stop) $sub)
         (((re:match $rel $_n1 $_n2)
           (let* (($stop (+ $pos $rel))
                  ($ch (str:byte-slice $chunk $stop (+ $stop 1))))
             (if (== $ch "\"")
               (oscan:step (BigJson in-val-nested $depth True False "" $cur-key "" skip False True False False $fields $msg-tail $msg-hits) $chunk (+ $stop 1) $len)
               (if (or (== $ch "{") (== $ch "["))
                 (oscan:step (BigJson in-val-nested (+ $depth 1) False False "" $cur-key "" skip False True False False $fields $msg-tail $msg-hits) $chunk (+ $stop 1) $len)
                 (let $nd (- $depth 1)
                   (if (== $nd 1)
                     (oscan:step (BigJson expect-key 1 False False "" "" "" skip False True False False $fields $msg-tail $msg-hits) $chunk (+ $stop 1) $len)
                     (oscan:step (BigJson in-val-nested $nd False False "" $cur-key "" skip False True False False $fields $msg-tail $msg-hits) $chunk (+ $stop 1) $len)))))))
          ($none
           (BigJson in-val-nested $depth False False "" $cur-key "" skip False True False False $fields $msg-tail $msg-hits)))))))

(= (oscan:finish (BigJson $phase $depth $in-str $esc $key-buf $cur-key $val-buf $val-kind $val-trunc $seen-open $done $bad $fields $msg-tail $msg-hits))
   (if (and $seen-open (and $done (and (not $bad) (and (== $depth 0) (not $in-str)))))
     (BigJsonOk (JsonObject (list:reverse $fields)))
     BigJsonInvalid))
