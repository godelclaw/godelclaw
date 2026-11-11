
; Python text is already text; repr would quote user input and make an empty
; poll appear nonempty. The symbol path retains the SWI carrier's behavior.
(= (omega-host:receive-text $value)
   (if (== (get-type $value) String) $value (string-safe (repr $value))))
(= (py-str-helper $L $outp)
   (if (== $L ()) $outp
       (let* (($head (car-atom $L)) ($tail (cdr-atom $L))
              ($outp2 (py-call (operator.add $outp (py-call (str $head))))))
         (py-str-helper $tail $outp2))))
(= (py-str $L) (py-str-helper $L ""))
; Upstream Omega reads configuration as SWI-PeTTa's Janus hands it over, text
; as symbols: it compares (commchannel) with telegram and (embeddingprovider)
; with Local. On CeTTa py-call hands Python text back as a String; make the
; symbol again (str:string->symbol, from the str library the CeTTa entry loads).
(= (omega-host:atom $value)
   (if (== (get-type $value) String)
     (case (str:string->symbol $value) (((Error $call $why) $value) ($symbol $symbol)))
     $value))
(= (configure $key $default)
   (let $value (omega-host:atom (py-call (runtime_host.config $key $default)))
     (add-atom &self (= ($key) $value))))
(= (initConfig) (py-call (runtime_host.init_config)))
(= (initLogger) ())
(= (applySecurityPolicy) ())
(= (initPlugins) ())
(= (log $level $module $message) ())
(= (get_time) (py-call (time.time)))
(= (get_time_as_string) (py-call (time.strftime "%Y-%m-%d %H:%M:%S")))
(= (joinPath $parts) (py-call (runtime_host.join_path $parts)))
(= (sleep $seconds) (py-call (runtime_host.omega_sleep $seconds)))
(= (string-replace $text $separators $replacement) (py-call (runtime_host.string_replace $text $separators $replacement)))
; Host Booleans cross py-call as the text "true"/"false"; on CeTTa that text
; is a String, which `if` reads as false. Read it as a Boolean on both hosts.
(= (omega-host:true $value) (case $value ((True True) (true True) ("true" True) ($other False))))
(= (exists-file $path) (omega-host:true (py-call (runtime_host.path_exists $path "file"))))
(= (exists-directory $path) (omega-host:true (py-call (runtime_host.path_exists $path "directory"))))
