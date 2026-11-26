; claw-mail: send mail, inspect an inbox, or wait for a reply through the
; shared mail service. scripts/shared_mail.metta makes the mailbox calls; this
; file reads the command line and mail.toml, retries transient failures and
; prints each result: summary lines unless --json, then one JSON line.
;
; Exit status: 0 for the expected result, 1 for a failure, 2 for a usage or
; configuration error, 3 when a wait ends without mail, 4 when a newer waiter
; for the same name took over.
!(import! &self ./shared_mail.metta)
!(import! &self (library system))
!(import! &self (library fs))
!(import! &self (library path))
!(import! &self (library toml))
!(import! &self (library host_io))
!(import! &self (library math))
!(import! &self (library re))

!(bind! &cm-name-re (new-state (re:compile "\\A[A-Za-z0-9_.-]+\\z")))
!(bind! &cm-int-re (new-state (re:compile "\\A[+-]?[0-9]+\\z")))
!(bind! &cm-float-re (new-state (re:compile "\\A[+-]?(?:[0-9]+(?:\\.[0-9]*)?|\\.[0-9]+)(?:[eE][+-]?[0-9]+)?\\z")))
!(bind! &cm-nonfinite-re (new-state (re:compile "\\A[+-]?(?:[iI][nN][fF](?:[iI][nN][iI][tT][yY])?|[nN][aA][nN])\\z")))
!(bind! &cm-negative-re (new-state (re:compile "\\A-(?:[0-9]+|[0-9]*\\.[0-9]+)\\z")))

;; ---- values ----
(= (cm:text $value) (if (== (get-type $value) String) $value (repr (quote $value))))
(= (cm:reverse $items $acc)
   (if (== $items ()) $acc (cm:reverse (cdr-atom $items) (cons-atom (car-atom $items) $acc))))
(= (cm:get $pairs $key $default)
   (if (== $pairs ()) $default
     (let ($k $v) (car-atom $pairs)
       (if (== $k $key) $v (cm:get (cdr-atom $pairs) $key $default)))))
(= (cm:has? $pairs $key) (!= (cm:get $pairs $key CmUnset) CmUnset))
(= (cm:member? $value $items)
   (if (== $items ()) False (if (== $value (car-atom $items)) True (cm:member? $value (cdr-atom $items)))))
(= (cm:contains? $text $part) (case (str:find $text $part) (((TextNotFound $_) False) ($_ True))))
(= (cm:name? $text) (re:is-match (get-state &cm-name-re) $text))
(= (cm:unset) "\u001fclaw-mail-unset\u001f")
(= (cm:env $name $default)
   (let $value (system:getenv-or-default $name (cm:unset)) (if (== $value (cm:unset)) $default $value)))

;; ---- output ----
(= (cm:out $text) (system:write stdout (str:concat $text "\n")))
(= (cm:err $text) (system:write stderr (str:concat $text "\n")))

; JSON in Python's json.dumps layout, non-ASCII text kept as is.
(= (cm:hex-digit $n) (str:char-slice "0123456789abcdef" $n (+ $n 1)))
(= (cm:escape-point $cp)
   (if (== $cp 34) "\\\""
   (if (== $cp 92) "\\\\"
   (if (== $cp 10) "\\n"
   (if (== $cp 13) "\\r"
   (if (== $cp 9) "\\t"
   (if (== $cp 8) "\\b"
   (if (== $cp 12) "\\f"
   (if (< $cp 32) (str:concat "\\u00" (str:concat (cm:hex-digit (math:floor (/ $cp 16))) (cm:hex-digit (% $cp 16))))
     (str:from-python-codepoints ($cp)))))))))))
(= (cm:plain-point? $cp) (and (>= $cp 32) (and (!= $cp 34) (!= $cp 92))))
(= (cm:all-plain? $points)
   (if (== $points ()) True
     (if (cm:plain-point? (car-atom $points)) (cm:all-plain? (cdr-atom $points)) False)))
(= (cm:quote $text)
   (let $points (str:python-codepoints $text)
     (str:concat "\""
       (str:concat (if (cm:all-plain? $points) $text (str:join "" (map-atom $points $cp (cm:escape-point $cp))))
         "\""))))
(= (cm:json (JsonString $text)) (cm:quote $text))
(= (cm:json (JsonNumber $number)) (cm:text $number))
(= (cm:json (JsonBool $value)) (if $value "true" "false"))
(= (cm:json JsonNull) "null")
(= (cm:json (JsonArray $items))
   (str:concat "[" (str:concat (str:join ", " (map-atom $items $item (cm:json $item))) "]")))
(= (cm:json (JsonObject $pairs))
   (str:concat "{" (str:concat (str:join ", " (map-atom $pairs $pair (cm:json-pair $pair))) "}")))
(= (cm:json-pair (JsonPair $key $value)) (str:concat (cm:quote $key) (str:concat ": " (cm:json $value))))

;; ---- results ----
(= (cm:field-in $pairs $key)
   (if (== $pairs ()) CmAbsent
     (let (JsonPair $k $v) (car-atom $pairs)
       (if (== $k $key) $v (cm:field-in (cdr-atom $pairs) $key)))))
(= (cm:field (JsonObject $pairs) $key) (cm:field-in $pairs $key))
(= (cm:string-field $result $key)
   (case (cm:field $result $key) (((JsonString $text) $text) ($other ""))))
(= (cm:status $result) (cm:string-field $result "status"))
(= (cm:reason $result) (cm:string-field $result "reason"))
(= (cm:retryable? $result)
   (case (cm:field $result "retryable") (((JsonBool $value) (if $value True False)) ($other False))))
(= (cm:with-field-in $pairs $key $value)
   (if (== $pairs ()) ((JsonPair $key $value))
     (let (JsonPair $k $v) (car-atom $pairs)
       (if (== $k $key) (cons-atom (JsonPair $key $value) (cdr-atom $pairs))
         (cons-atom (JsonPair $k $v) (cm:with-field-in (cdr-atom $pairs) $key $value))))))
(= (cm:with-field (JsonObject $pairs) $key $value) (JsonObject (cm:with-field-in $pairs $key $value)))
(= (cm:error-result $reason)
   (JsonObject ((JsonPair "status" (JsonString "error")) (JsonPair "reason" (JsonString $reason)))))
; One call, one mailbox result.
(= (cm:one $results)
   (if (== (size-atom $results) 1)
     (let $result (car-atom $results)
       (case $result
         (((JsonObject $pairs)
           (if (== (cm:field $result "status") CmAbsent)
             (cm:error-result (str:concat "missing or ambiguous mailbox result: " (repr $result)))
             $result))
          ($other (cm:error-result (repr $other))))))
     (cm:error-result (str:concat "missing or ambiguous mailbox result: " (repr $results)))))

;; ---- the command line ----
(= (cm:commands) ("whoami" "send" "next" "inbox" "wait" "ack"))
(= (cm:options "global")
   (("--config" value) ("--socket" value) ("--cetta" value) ("--space" value) ("--json" flag)
    ("--help" help) ("-h" help)))
(= (cm:options "whoami") (("--json" flag) ("--space" value) ("--help" help) ("-h" help)))
(= (cm:options "send") (("--id" value) ("--json" flag) ("--space" value) ("--help" help) ("-h" help)))
(= (cm:options "next") (("--json" flag) ("--space" value) ("--help" help) ("-h" help)))
(= (cm:options "inbox")
   (("--limit" value) ("--count" flag) ("--notice" flag) ("--json" flag) ("--space" value)
    ("--help" help) ("-h" help)))
(= (cm:options "wait")
   (("--timeout" value) ("--forever" flag) ("--retry-limit" value) ("--json" flag) ("--space" value)
    ("--help" help) ("-h" help)))
(= (cm:options "ack") (("--json" flag) ("--space" value) ("--help" help) ("-h" help)))

(= (cm:usage "global")
   "usage: claw-mail [-h] [--config CONFIG] [--socket SOCKET] [--cetta CETTA] [--space SPACE] [--json] {whoami,send,next,inbox,wait,ack} ...")
(= (cm:usage "whoami") "usage: claw-mail whoami [-h]")
(= (cm:usage "send") "usage: claw-mail send [-h] [--id ID] ARG [ARG ...]")
(= (cm:usage "next") "usage: claw-mail next [-h] [agent]")
(= (cm:usage "inbox") "usage: claw-mail inbox [-h] [--limit 1..100] [--count | --notice] [agent]")
(= (cm:usage "wait") "usage: claw-mail wait [-h] [--timeout TIMEOUT | --forever] [--retry-limit RETRY_LIMIT] [agent]")
(= (cm:usage "ack") "usage: claw-mail ack [-h] ARG [ARG ...]")
(= (cm:prog $scope) (if (== $scope "global") "claw-mail" (str:concat "claw-mail " $scope)))

(= (cm:help-text "global")
   (str:concat (cm:usage "global") "\n\nSend mail, inspect an inbox, or wait for a reply through a CeTTa shared socket.\n\ncommands:\n  whoami   show the identity authenticated by the service\n  send     send [AGENT] TO TEXT; acceptance is not a reply\n  next     read without acknowledging\n  inbox    inspect pending and ready mail without taking it\n  wait     wait without acknowledging\n  ack      ack [AGENT] ID after handling a delivery\n\noptions:\n  -h, --help       show this help message and exit\n  --config CONFIG  mail.toml (default $CLAW_MAIL_CONFIG or ~/.config/godelclaw/mail.toml)\n  --socket SOCKET  the mail service socket\n  --cetta CETTA    accepted for older configurations; the client runs on its own engine\n  --space SPACE    send namespace (default agent-mail); an explicit empty value selects the owner inbox\n  --json           JSON only, without delivery summaries"))
(= (cm:help-text "inbox")
   (str:concat (cm:usage "inbox") "\n\noptions:\n  -h, --help      show this help message and exit\n  --limit 1..100  previews to show (default 20)\n  --count         print only the total number\n  --notice        print a notice only when mail is waiting"))
(= (cm:help-text "wait")
   (str:concat (cm:usage "wait") "\n\noptions:\n  -h, --help            show this help message and exit\n  --timeout TIMEOUT     seconds; 0 means forever (default 120)\n  --forever             wait until mail or persistent failure\n  --retry-limit RETRY_LIMIT\n                        consecutive transient failures before exit (default 8)"))
(= (cm:help-text "send")
   (str:concat (cm:usage "send") "\n\noptions:\n  -h, --help  show this help message and exit\n  --id ID     retry the same occurrence with identical content"))
(= (cm:help $scope)
   (if (cm:member? $scope ("global" "inbox" "wait" "send")) (cm:help-text $scope)
     (str:concat (cm:usage $scope) "\n\noptions:\n  -h, --help  show this help message and exit")))

; An option token: "--name", "--name=value" or "-h". A lone "-" and negative
; numbers are values.
(= (cm:option-token? $token)
   (and (str:starts-with $token "-")
     (and (!= $token "-") (not (re:is-match (get-state &cm-negative-re) $token)))))
(= (cm:split-inline $token)
   (if (str:starts-with $token "--")
     (case (str:find $token "=")
       (((TextNotFound $_) ($token CmNoValue))
        ($at (cm:split-at $token $at))))
     ($token CmNoValue)))
(= (cm:split-at $token $at)
   ((str:char-slice $token 0 $at) (str:char-slice $token (+ $at 1) (str:char-length $token))))
(= (cm:prefix-matches $name $table)
   (if (== $table ()) ()
     (let ($full $kind) (car-atom $table)
       (let $rest (cm:prefix-matches $name (cdr-atom $table))
         (if (and (str:starts-with $full "--") (str:starts-with $full $name)) (cons-atom ($full $kind) $rest) $rest)))))
(= (cm:resolve $name $table)
   (let $exact (cm:get $table $name CmUnset)
     (if (!= $exact CmUnset) (CmOption $name $exact)
       (if (or (not (str:starts-with $name "--")) (< (str:char-length $name) 3)) CmUnknown
         (let $matches (cm:prefix-matches $name $table)
           (if (== $matches ()) CmUnknown
             (if (== (size-atom $matches) 1)
               (let ($full $kind) (car-atom $matches) (CmOption $full $kind))
               (CmAmbiguous (map-atom $matches $m (let ($f $k) $m $f))))))))))

; (CmParsed pairs positionals rest), (CmUsageError scope message) or (CmHelp scope).
; In the global scope the first positional is the command: it and every later
; token are left in rest.
(= (cm:parse $scope $tokens $table $pairs $positionals)
   (if (== $tokens ()) (CmParsed $pairs (cm:reverse $positionals ()) ())
     (let* (($token (car-atom $tokens)) ($more (cdr-atom $tokens)))
       (if (== $token "--")
         (if (== $scope "global") (CmParsed $pairs (cm:reverse $positionals ()) $more)
           (CmParsed $pairs (cm:reverse (cm:reverse $more $positionals) ()) ()))
         (if (not (cm:option-token? $token))
           (if (== $scope "global") (CmParsed $pairs (cm:reverse $positionals ()) $tokens)
             (cm:parse $scope $more $table $pairs (cons-atom $token $positionals)))
           (let ($name $inline) (cm:split-inline $token)
             (case (cm:resolve $name $table)
               ((CmUnknown (CmUsageError $scope (str:concat "unrecognized arguments: " $token)))
                ((CmAmbiguous $names)
                 (CmUsageError $scope (str:concat "ambiguous option: " (str:concat $name
                   (str:concat " could match " (str:join ", " $names))))))
                ((CmOption $full help) (CmHelp $scope))
                ((CmOption $full flag)
                 (if (!= $inline CmNoValue)
                   (CmUsageError $scope (str:concat "argument " (str:concat $full
                     (str:concat ": ignored explicit argument '" (str:concat $inline "'")))))
                   (cm:parse $scope $more $table (cons-atom ($full True) $pairs) $positionals)))
                ((CmOption $full value)
                 (if (!= $inline CmNoValue)
                   (cm:parse $scope $more $table (cons-atom ($full $inline) $pairs) $positionals)
                   (if (or (== $more ()) (cm:option-token? (car-atom $more)))
                     (CmUsageError $scope (str:concat "argument " (str:concat $full ": expected one argument")))
                     (cm:parse $scope (cdr-atom $more) $table (cons-atom ($full (car-atom $more)) $pairs)
                       $positionals))))))))))))

; The later of two exclusive options names itself first, as argparse does.
(= (cm:exclusive $command $pairs $a $b)
   (if (and (cm:has? $pairs $a) (cm:has? $pairs $b))
     (let $later (cm:later $pairs $a $b)
       (CmUsageError $command (str:concat "argument " (str:concat $later
         (str:concat ": not allowed with argument " (if (== $later $a) $b $a))))))
     CmOk))
(= (cm:later $pairs $a $b)
   (let ($k $v) (car-atom $pairs) (if (or (== $k $a) (== $k $b)) $k (cm:later (cdr-atom $pairs) $a $b))))

(= (cm:integer $text)
   (if (re:is-match (get-state &cm-int-re) $text) (CmNumber (parse (if (str:starts-with $text "+") (str:char-slice $text 1 (str:char-length $text)) $text))) CmNotNumber))
; Decimal text as Python's float() reads it: ".5", "5." and "5.e3" too.
(= (cm:exponent-at $text)
   (case (str:find $text "e")
     (((TextNotFound $_) (case (str:find $text "E") (((TextNotFound $__) (str:char-length $text)) ($upper $upper))))
      ($lower $lower))))
(= (cm:decimal $text)
   (let* (($unsigned (if (str:starts-with $text "+") (str:char-slice $text 1 (str:char-length $text)) $text))
          ($negative (str:starts-with $unsigned "-"))
          ($body (if $negative (str:char-slice $unsigned 1 (str:char-length $unsigned)) $unsigned))
          ($cut (cm:exponent-at $body))
          ($mantissa (str:char-slice $body 0 $cut))
          ($exponent (str:char-slice $body $cut (str:char-length $body)))
          ($leading (if (str:starts-with $mantissa ".") (str:concat "0" $mantissa) $mantissa))
          ($whole (if (str:ends-with $leading ".") (str:concat $leading "0") $leading)))
     (parse (str:concat (if $negative "-" "") (str:concat $whole $exponent)))))
(= (cm:duration $text)
   (if (re:is-match (get-state &cm-nonfinite-re) $text) CmNotFinite
     (if (re:is-match (get-state &cm-float-re) $text)
       (let $number (cm:decimal $text)
         (if (!= (get-type $number) Number) CmNotNumber
           (if (< $number 0) CmNotFinite (CmNumber $number))))
       CmNotNumber)))

; Type and choice checks for one command's options and positionals.
(= (cm:check-command $command $pairs $positionals)
   (let $count (size-atom $positionals)
     (if (and (cm:member? $command ("send" "ack")) (== $count 0))
       (CmUsageError $command "the following arguments are required: ARG")
       (if (and (== $command "whoami") (> $count 0))
         (CmUsageError $command (str:concat "unrecognized arguments: " (str:join " " $positionals)))
         (if (and (cm:member? $command ("next" "inbox" "wait")) (> $count 1))
           (CmUsageError $command (str:concat "unrecognized arguments: " (str:join " " (cdr-atom $positionals))))
           (if (== $command "inbox") (cm:check-inbox $pairs)
             (if (== $command "wait") (cm:check-wait $pairs) CmOk)))))))
(= (cm:check-inbox $pairs)
   (let $exclusive (cm:exclusive "inbox" $pairs "--count" "--notice")
     (if (!= $exclusive CmOk) $exclusive
       (if (not (cm:has? $pairs "--limit")) CmOk
         (let $text (cm:get $pairs "--limit" "")
           (case (cm:integer $text)
             ((CmNotNumber (CmUsageError "inbox" (str:concat "argument --limit: invalid int value: '" (str:concat $text "'"))))
              ((CmNumber $n)
               (if (and (>= $n 1) (<= $n 100)) CmOk
                 (CmUsageError "inbox" (str:concat "argument --limit: invalid choice: " (str:concat (repr $n) " (choose from 1..100)"))))))))))))
(= (cm:check-wait $pairs)
   (let $exclusive (cm:exclusive "wait" $pairs "--timeout" "--forever")
     (if (!= $exclusive CmOk) $exclusive
       (let $timeout (if (cm:has? $pairs "--timeout") (cm:duration (cm:get $pairs "--timeout" "")) (CmNumber 120))
         (case $timeout
           ((CmNotNumber (CmUsageError "wait" (str:concat "argument --timeout: invalid duration value: '"
               (str:concat (cm:get $pairs "--timeout" "") "'"))))
            (CmNotFinite (CmUsageError "wait" "argument --timeout: timeout must be a finite nonnegative number of seconds"))
            ((CmNumber $n)
             (if (not (cm:has? $pairs "--retry-limit")) CmOk
               (let $text (cm:get $pairs "--retry-limit" "")
                 (case (cm:integer $text)
                   ((CmNotNumber (CmUsageError "wait" (str:concat "argument --retry-limit: invalid int value: '" (str:concat $text "'"))))
                    ($ok CmOk))))))))))))

; (CmArgs global command options positionals), (CmUsageError scope message) or (CmHelp scope).
(= (cm:parse-args $tokens)
   (case (cm:parse "global" $tokens (cm:options "global") () ())
     (((CmParsed $global $none $rest)
       (if (== $rest ()) (CmUsageError "global" "the following arguments are required: command")
         (let $command (car-atom $rest)
           (if (not (cm:member? $command (cm:commands)))
             (CmUsageError "global" (str:concat "argument command: invalid choice: '" (str:concat $command
               "' (choose from whoami, send, next, inbox, wait, ack)")))
             (case (cm:parse $command (cdr-atom $rest) (cm:options $command) () ())
               (((CmParsed $options $positionals $tail)
                 (let $checked (cm:check-command $command $options $positionals)
                   (if (== $checked CmOk) (CmArgs $global $command $options $positionals) $checked)))
                ($stop $stop)))))))
      ($stop $stop))))

;; ---- mail.toml ----
(= (cm:config-arg $tokens)
   (if (== $tokens ()) CmUnset
     (let* (($token (car-atom $tokens)) ($more (cdr-atom $tokens)))
       (if (== $token "--") CmUnset
         (if (and (== $token "--config") (!= $more ())) (car-atom $more)
           (if (str:starts-with $token "--config=") (str:char-slice $token 9 (str:char-length $token))
             (cm:config-arg $more)))))))
(= (cm:absolute $path) (if (path:is-absolute $path) $path (path:join (system:getcwd) $path)))
(= (cm:resolved $path)
   (let $absolute (cm:absolute (system:expanduser $path))
     (case (fs:try-canonical-path $absolute)
       (((FS:CanonicalPath $canonical) $canonical) ($fault $absolute)))))
(= (cm:config-path $tokens)
   (let $given (cm:config-arg $tokens)
     (cm:resolved
       (if (!= $given CmUnset) $given
         (let $named (cm:env "CLAW_MAIL_CONFIG" "")
           (if (!= $named "") $named
             (let $home (cm:env "XDG_CONFIG_HOME" "")
               (path:join (if (== $home "") (system:expanduser "~/.config") $home) "godelclaw/mail.toml"))))))))

; $NAME and ${NAME} name environment variables; unset ones stay as written.
(= (cm:word-point? $cp)
   (or (and (>= $cp 48) (<= $cp 57)) (or (and (>= $cp 65) (<= $cp 90)) (or (and (>= $cp 97) (<= $cp 122)) (== $cp 95)))))
(= (cm:take-word $points $acc)
   (if (and (!= $points ()) (cm:word-point? (car-atom $points)))
     (cm:take-word (cdr-atom $points) (cons-atom (car-atom $points) $acc))
     ((cm:reverse $acc ()) $points)))
(= (cm:take-brace $points $acc)
   (if (== $points ()) CmNoBrace
     (if (== (car-atom $points) 125) ((cm:reverse $acc ()) (cdr-atom $points))
       (cm:take-brace (cdr-atom $points) (cons-atom (car-atom $points) $acc)))))
(= (cm:expand-name $name $written $rest $out)
   (let $value (cm:env (str:from-python-codepoints $name) (cm:unset))
     (if (== $value (cm:unset))
       (cm:expand-points $rest (cm:reverse $written $out))
       (cm:expand-points $rest (cm:reverse (str:python-codepoints $value) $out)))))
(= (cm:expand-points $points $out)
   (if (== $points ()) (cm:reverse $out ())
     (let* (($cp (car-atom $points)) ($more (cdr-atom $points)))
       (if (or (!= $cp 36) (== $more ())) (cm:expand-points $more (cons-atom $cp $out))
         (if (== (car-atom $more) 123)
           (case (cm:take-brace (cdr-atom $more) ())
             ((CmNoBrace (cm:expand-points $more (cons-atom $cp $out)))
              (($braced $after-brace)
               (cm:expand-name $braced (cons-atom 36 (cons-atom 123 (cm:reverse (cons-atom 125 (cm:reverse $braced ())) ())))
                 $after-brace $out))))
           (let ($word $after-word) (cm:take-word $more ())
             (if (== $word ()) (cm:expand-points $more (cons-atom $cp $out))
               (cm:expand-name $word (cons-atom 36 $word) $after-word $out))))))))
(= (cm:expandvars $text)
   (if (cm:contains? $text "$") (str:from-python-codepoints (cm:expand-points (str:python-codepoints $text) ())) $text))

(= (cm:config-pairs $pairs $directory $acc)
   (if (== $pairs ()) (CmConfig $acc)
     (let (JsonPair $key $value) (car-atom $pairs)
       (if (or (not (cm:member? $key ("socket" "cetta" "agent" "space"))) (!= (get-type $value) String))
         (CmConfigError "mail config accepts only string socket, cetta, agent and space fields")
         (let $expanded (if (cm:member? $key ("socket" "cetta")) (cm:expandvars (system:expanduser $value)) $value)
           (let $placed
             (if (and (cm:member? $key ("socket" "cetta"))
                   (and (or (== $key "socket") (cm:contains? $expanded "/")) (not (path:is-absolute $expanded))))
               (path:join $directory $expanded) $expanded)
             (cm:config-pairs (cdr-atom $pairs) $directory (cons-atom ($key $placed) $acc))))))))
(= (cm:load-config $path)
   (if (not (fs:is-file $path)) (CmConfig ())
     (case (toml:parse (fs:read-text $path))
       (((toml:failed $kind $detail) (CmConfigError (cm:text $detail)))
        ((JsonObject $pairs) (cm:config-pairs $pairs (path:dirname $path) ()))
        ($other (CmConfigError "mail config is not a TOML table"))))))

;; ---- calls ----
(= (cm:whoami $socket) (cm:one (collapse (catch (shared-mail:json (shared-mail:identity $socket))))))
(= (cm:post $socket $space $id $to $agent $text)
   (cm:one (collapse (catch (shared-mail:json (shared-mail:post $socket $space $id $to $agent $text))))))
(= (cm:ack $socket $agent $id) (cm:one (collapse (catch (shared-mail:json (shared-mail:ack $socket $agent $id))))))
(= (cm:inbox $socket $agent $limit $tries)
   (let $result (cm:one (collapse (catch (shared-mail:json (shared-mail:inbox $socket $agent $limit)))))
     (if (and (cm:retryable? $result) (> $tries 1)) (cm:inbox $socket $agent $limit (- $tries 1)) $result)))
(= (cm:next $socket $agent $milliseconds $generation $named)
   (if $named
     (cm:one (collapse (catch (shared-mail:json (shared-mail:next-as $socket $agent $milliseconds $generation)))))
     (cm:one (collapse (catch (shared-mail:json (shared-mail:next $socket $agent $milliseconds)))))))

; Deadlines are monotonic nanoseconds, or CmForever.
; The waiter counts in whole milliseconds (budgets and sleeps), so less than one
; left cannot be waited on: that is the deadline.
(= (cm:expired? $deadline) (if (== $deadline CmForever) False (>= (+ (system:monotonic-ns) 1000000) $deadline)))
(= (cm:remaining-s $deadline)
   (if (== $deadline CmForever) 8 (max 0 (/ (- $deadline (system:monotonic-ns)) 1000000000.0))))
(= (cm:budget-ms $deadline)
   (if (== $deadline CmForever) 30000
     (math:floor (min 30000 (max 0 (/ (- $deadline (system:monotonic-ns)) 1000000))))))
(= (cm:backoff $failures) (* 0.5 (math:pow 2 (min (- $failures 1) 4))))
; A delay in the shortest form that keeps six significant digits.
(= (cm:places $value $places)
   (if (or (>= (* $value (math:pow 10 $places)) 100000) (>= $places 12)) $places (cm:places $value (+ $places 1))))
(= (cm:trim-zeros $text)
   (if (str:ends-with $text "0") (cm:trim-zeros (str:char-slice $text 0 (- (str:char-length $text) 1)))
     (if (str:ends-with $text ".") (str:char-slice $text 0 (- (str:char-length $text) 1)) $text)))
(= (cm:seconds-text $value)
   (if (== $value (math:floor $value)) (repr (math:floor $value))
     (cm:trim-zeros (str:format-fixed $value (cm:places $value 0)))))

; One wait: a named waiter falls back to an unnamed wait on an engine that
; lacks named waits, stops when a newer waiter takes its name, and retries
; transient failures with backoff until the retry limit or the deadline.
(= (cm:wait-loop $socket $agent $waiting $deadline $generation $named $failures $limit)
   (let* (($result (cm:next $socket $agent (cm:budget-ms $deadline) $generation $named))
          ($status (cm:status $result)) ($reason (cm:reason $result)))
     (if (and $named (and (== $status "error") (str:starts-with $reason "(mailbox:watch-as ")))
       (cm:wait-loop $socket $agent $waiting $deadline $generation False $failures $limit)
       (if (and (== $status "error") (cm:contains? $reason "error superseded"))
         (JsonObject ((JsonPair "status" (JsonString "superseded"))
                      (JsonPair "reason" (JsonString "a newer waiter for this name holds it"))))
         (let $checked
           (if (and (== $status "error")
                 (or (cm:contains? $reason "error watch-capacity") (cm:contains? $reason "error account-watch-limit")))
             (cm:with-field $result "retryable" (JsonBool True)) $result)
           (if (and (cm:retryable? $checked) $waiting)
             (let $tried (+ $failures 1)
               (if (or (> $tried $limit) (cm:expired? $deadline)) $checked
                 (let $delay (min 8 (min (cm:backoff $tried) (cm:remaining-s $deadline)))
                   (progn
                     (cm:err (str:concat "mail temporarily unavailable; retry " (str:concat (repr $tried)
                       (str:concat "/" (str:concat (repr $limit) (str:concat " in " (str:concat (cm:seconds-text $delay) "s")))))))
                     ; Rounded up: a wait cut short to the deadline must reach it, or the
                     ; sub-millisecond rest would start a burst of zero-length retries.
                     (host:wait () (math:ceil (* $delay 1000)))
                     (cm:wait-loop $socket $agent $waiting $deadline $generation $named $tried $limit)))))
             (if (or (!= (cm:status $checked) "idle") (cm:expired? $deadline)) $checked
               (cm:wait-loop $socket $agent $waiting $deadline $generation $named 0 $limit))))))))

;; ---- printing ----
(= (cm:clean-point $cp) (if (str:starts-with (str:unicode-category $cp) "C") 32 $cp))
(= (cm:clean $text) (str:from-python-codepoints (map-atom (str:python-codepoints $text) $cp (cm:clean-point $cp))))
(= (cm:line-break? $cp) (cm:member? $cp (10 11 12 13 28 29 30 133 8232 8233)))
(= (cm:first-line $points $acc)
   (if (or (== $points ()) (cm:line-break? (car-atom $points))) (cm:reverse $acc ())
     (cm:first-line (cdr-atom $points) (cons-atom (car-atom $points) $acc))))
(= (cm:preview $text)
   (cm:clean (str:from-python-codepoints (cm:first-line (str:python-codepoints (str:char-slice $text 0 200)) ()))))
(= (cm:summary $item)
   (str:concat "MAIL id=" (str:concat (cm:clean (cm:string-field $item "id"))
     (str:concat " from=" (str:concat (cm:clean (cm:string-field $item "from"))
       (str:concat ": " (cm:preview (cm:string-field $item "text"))))))))
(= (cm:number-text $result $key) (case (cm:field $result $key) (((JsonNumber $n) (cm:text $n)) ($other "0"))))
(= (cm:items $result) (case (cm:field $result "items") (((JsonArray $items) $items) ($other ()))))
(= (cm:emit $command $flags $agent $result)
   (let $status (cm:status $result)
     (if (and (== $command "inbox") (and (cm:member? CmCountOnly $flags) (== $status "inbox")))
       (cm:out (cm:number-text $result "count"))
       (if (and (== $command "inbox") (cm:member? CmNoticeOnly $flags))
         (if (== $status "inbox")
           (if (!= (cm:number-text $result "count") "0")
             (cm:out (str:concat "MAIL: " (str:concat (cm:number-text $result "count")
               (str:concat " unacknowledged message(s) for " (str:concat $agent "; use claw-mail next.")))))
             ())
           (cm:err (let $why (cm:reason $result) (if (== $why "") "mail check failed" $why))))
         (progn
           (if (cm:member? CmJsonOnly $flags) ()
             (if (== $status "delivery") (cm:out (cm:summary $result))
               (if (== $status "inbox")
                 (progn
                   (cm:out (str:concat "INBOX " (str:concat (cm:number-text $result "count")
                     (str:concat " unacknowledged message(s); showing " (repr (size-atom (cm:items $result)))))))
                   (map-atom (cm:items $result) $item (cm:out (cm:summary $item))))
                 ())))
           (cm:out (cm:json $result)))))))

(= (cm:expected "whoami") ("identity"))
(= (cm:expected "send") ("accepted" "duplicate"))
(= (cm:expected "inbox") ("inbox"))
(= (cm:expected "ack") ("settled" "duplicate"))
(= (cm:expected "next") ("delivery" "idle"))
(= (cm:expected "wait") ("delivery"))
(= (cm:exit-code $command $result)
   (let $status (cm:status $result)
     (if (== $status "superseded") 4
       (if (cm:member? $status (cm:expected $command)) 0
         (if (and (== $command "wait") (== $status "idle")) 3 1)))))

;; ---- main ----
(= (cm:usage-error $scope $message)
   (progn (cm:err (cm:usage $scope)) (cm:err (str:concat (cm:prog $scope) (str:concat ": error: " $message))) 2))

; The sender for send and ack may come first or from the default; next, inbox
; and wait take an optional agent.
(= (cm:fields $command $positionals $default)
   (let $count (size-atom $positionals)
     (if (cm:member? $command ("send" "ack"))
       (let $needed (if (== $command "send") 2 1)
         (if (== $count (+ $needed 1)) (CmFields (car-atom $positionals) (cdr-atom $positionals))
           (if (and (== $count $needed) (!= $default "")) (CmFields $default $positionals)
             (CmFieldsError (str:concat $command " needs AGENT or a configured CLAW_MAIL_AGENT")))))
       (if (== $command "whoami") (CmFields "" ())
         (let $agent (if (and (== $count 1) (!= (car-atom $positionals) "")) (car-atom $positionals) $default)
           (if (== $agent "") (CmFieldsError "choose AGENT or set CLAW_MAIL_AGENT") (CmFields $agent ())))))))
; The recipient is checked before the sender, as the Python client did.
(= (cm:check-names $command $agent $rest)
   (let $recipient
     (if (== $command "send")
       (let $parts (str:split "/" (car-atom $rest))
         (if (> (size-atom $parts) 2) "use AGENT or ACCOUNT/AGENT"
           (if (cm:all-names? $parts) CmOk "use an agent name with letters, digits, _, . or -")))
       CmOk)
     (if (!= $recipient CmOk) $recipient
       (if (and (!= $command "whoami") (not (cm:name? $agent)))
         "use an agent name with letters, digits, _, . or -" CmOk))))
(= (cm:all-names? $parts)
   (if (== $parts ()) True (if (cm:name? (car-atom $parts)) (cm:all-names? (cdr-atom $parts)) False)))

(= (cm:run $command $socket $space $agent $rest $options)
   (if (== $command "whoami") (cm:whoami $socket)
     (if (== $command "send")
       (let $id (cm:get $options "--id" (system:uuid4-hex))
         (progn
           (system:write stderr (str:concat "message-id=" (str:concat (cm:quote $id) "\n")))
           (let $result (cm:post $socket $space $id (car-atom $rest) $agent (car-atom (cdr-atom $rest)))
             (if (== (cm:field $result "id") CmAbsent) (cm:with-field $result "id" (JsonString $id)) $result))))
       (if (== $command "ack") (cm:ack $socket $agent (car-atom $rest))
         (if (== $command "inbox")
           (cm:inbox $socket $agent
             (if (or (cm:has? $options "--count") (cm:has? $options "--notice")) 0
               (let (CmNumber $limit-n) (cm:integer (cm:get $options "--limit" "20")) $limit-n))
             3)
           (if (== $command "next")
             (cm:wait-loop $socket $agent False (system:monotonic-ns) 0 False 0 0)
             (let* (($timeout (case (cm:duration (cm:get $options "--timeout" "120")) (((CmNumber $timeout-n) $timeout-n) ($other 120))))
                    ($forever (or (cm:has? $options "--forever") (<= $timeout 0)))
                    ($deadline (if $forever CmForever (+ (system:monotonic-ns) (math:floor (* $timeout 1000000000)))))
                    ($limit (let (CmNumber $retry-n) (cm:integer (cm:get $options "--retry-limit" "8")) $retry-n)))
               (cm:wait-loop $socket $agent True $deadline (system:wall-time-ns) True 0 $limit))))))))

(= (cm:main $argv)
   (let* (($tokens (map-atom $argv $arg (cm:text $arg)))
          ($config (cm:load-config (cm:config-path $tokens))))
     (case $config
       (((CmConfigError $config-message) (cm:usage-error "global" $config-message))
        ((CmConfig $settings)
         (case (cm:parse-args $tokens)
           (((CmHelp $help-scope) (progn (cm:out (cm:help $help-scope)) 0))
            ((CmUsageError $usage-scope $usage-message) (cm:usage-error $usage-scope $usage-message))
            ((CmArgs $global $command $options $positionals)
             (let* (($socket (cm:get $global "--socket" (cm:env "CETTA_SHARED_MAIL_SOCKET" (cm:get $settings "socket" ""))))
                    ($space (cm:get $options "--space" (cm:get $global "--space" (cm:get $settings "space" "agent-mail"))))
                    ($flags (if (or (cm:has? $options "--json") (cm:has? $global "--json")) (CmJsonOnly) ()))
                    ($flags2 (if (cm:has? $options "--count") (cons-atom CmCountOnly $flags)
                               (if (cm:has? $options "--notice") (cons-atom CmNoticeOnly $flags) $flags)))
                    ($default (cm:env "CLAW_MAIL_AGENT" (cm:get $settings "agent" ""))))
               (if (== $socket "") (cm:usage-error "global" "choose a socket in arguments, environment, or mail.toml")
                 (case (cm:fields $command $positionals $default)
                   (((CmFieldsError $fields-message) (cm:usage-error "global" $fields-message))
                    ((CmFields $agent $rest)
                     (let $named (cm:check-names $command $agent $rest)
                       (if (!= $named CmOk) (cm:usage-error "global" $named)
                         (if (and (== $command "wait")
                               (< (let (CmNumber $retry-limit) (cm:integer (cm:get $options "--retry-limit" "8")) $retry-limit) 0))
                           (cm:usage-error "global" "--retry-limit must be nonnegative")
                           (if (and (== $command "send") (== (cm:get $options "--id" "x") ""))
                             (cm:usage-error "global" "message ID must be nonempty and contain no NUL")
                             (let $result (cm:run $command $socket $space $agent $rest $options)
                               (progn (cm:emit $command $flags2 $agent $result)
                                      (cm:exit-code $command $result))))))))))))))))))))

!(let $codes (collapse (cm:main (system:args)))
   (case $codes ((($code) (system:exit $code)) ($other (progn (cm:err (str:concat "claw-mail: internal error: " (repr $other))) (system:exit 1))))))
