; Browsing: an agent opens a page, reads it as an accessibility snapshot whose
; elements carry references (@e1, @e2, ...), and clicks, fills, presses keys
; or goes back, like a person at a browser. agent-browser drives a Lightpanda
; CDP server of the agent's own. The server runs as a transient user service
; with a memory cap and a lifetime (RuntimeMaxSec), so a session nobody closes
; still ends; every agent-browser call runs in a memory-capped scope. The
; browser obeys robots.txt, spaces navigations to one host (1 s by default),
; never reaches private or local addresses and sends no telemetry.
!(import! &self (library proc))
!(import! &self (library list))
!(import! &self ./websearch_cetta.metta)

(= (browse:setting $name $default) (system:getenv-or-default $name $default))
(= (browse:instance) (browse:setting "METTACLAW_INSTANCE" "agent"))
(= (browse:unit) (str:concat "godelclaw-browse-" (browse:instance)))
; A port per instance, 9300..9499, from the codepoints of its name, unless
; METTACLAW_BROWSE_PORT names one.
(= (browse:sum $values) (if (== $values ()) 0 (+ (car-atom $values) (browse:sum (cdr-atom $values)))))
(= (browse:port-of $name) (repr (+ 9300 (% (browse:sum (str:python-codepoints $name)) 200))))
(= (browse:port) (browse:setting "METTACLAW_BROWSE_PORT" (browse:port-of (browse:instance))))
(= (browse:lifetime) (browse:setting "METTACLAW_BROWSE_SECONDS" "1800"))
(= (browse:budget) (str:string->number (browse:setting "METTACLAW_BROWSE_CHARS" "8000")))

(= (browse:run $argv $timeout)
   (case (proc:run $argv (system:getcwd) inherit $timeout 4194304)
     (((proc:exited 0 $out $_) (BrowseOkV1 $out))
      ((proc:exited $code $out $err) (BrowseFailedV1 (str:join "" ("exit " (repr $code) ": " (str:trim $err) " " (str:trim $out)))))
      ($other (BrowseFailedV1 "tool-unavailable")))))

(= (browse:server-running?)
   (case (proc:run ("systemctl" "--user" "is-active" "--quiet" (browse:unit)) "/" inherit 10000 4096)
     (((proc:exited 0 $_ $_) True) ($other False))))
(= (browse:start-server)
   (let* (($bin (browse:setting "METTACLAW_LIGHTPANDA_BIN" "lightpanda"))
          ($memory (str:concat "MemoryMax=" (browse:setting "METTACLAW_BROWSE_MEMORY" "768M")))
          ($lifetime (str:concat "RuntimeMaxSec=" (browse:lifetime)))
          ($port (browse:port)))
     ; The agent's own server is not running, so a listener on its port belongs
     ; to someone else: never attach to it.
     (if (browse:port-open? $port 1) (BrowseFailedV1 (str:join "" ("port " $port " is in use; set METTACLAW_BROWSE_PORT")))
       (browse:launch $bin $memory $lifetime $port))))
(= (browse:port-open? $port $tries)
   (case (browse:run ("bash" "-c" (str:join "" ("for i in $(seq 1 " (repr $tries) "); do (exec 3<>/dev/tcp/127.0.0.1/" $port
                                                 ") 2>/dev/null && exit 0; sleep 0.1; done; exit 1"))) 15000)
     (((BrowseOkV1 $_) True) ($other False))))
(= (browse:launch $bin $memory $lifetime $port)
   (let* (($started (browse:run ("systemd-run" "--user" "--quiet" "--collect" "--unit" (browse:unit)
                                 "-p" $memory "-p" "MemorySwapMax=0" "-p" $lifetime
                                 "-E" "LIGHTPANDA_DISABLE_TELEMETRY=true"
                                 $bin "serve" "--obey-robots" "--block-private-networks"
                                 "--http-nav-delay" (browse:setting "METTACLAW_BROWSE_HOST_DELAY_MS" "1000")
                                 "--host" "127.0.0.1" "--port" $port "--log-level" "fatal") 20000)))
     (case $started
       (((BrowseFailedV1 $why) (BrowseFailedV1 (str:concat "server: " $why)))
        ($_ (if (browse:port-open? $port 50) (BrowseOkV1 "started") (BrowseFailedV1 "server did not listen")))))))

; agent-browser in the agent's own session, in a capped scope.
(= (browse:ab $arguments)
   (let* (($bin (browse:setting "METTACLAW_AGENT_BROWSER_BIN" "agent-browser"))
          ($scope ("systemd-run" "--user" "--scope" "-q" "-p" "MemoryMax=512M" "-p" "MemorySwapMax=0" "timeout" "60"))
          ($idle (str:concat (browse:lifetime) "s")))
     (browse:run (list:append $scope (list:append ($bin "--session" (browse:instance) "--idle-timeout" $idle) $arguments)) 70000)))

(= (browse:ensure)
   (if (browse:server-running?) (BrowseOkV1 "running")
     (case (browse:start-server)
       (((BrowseFailedV1 $why) (BrowseFailedV1 $why))
        ($_ (browse:ab ("connect" (browse:port))))))))

(= (browse:cut $text)
   (let* (($budget (browse:budget)) ($length (str:char-length $text)))
     (if (<= $length $budget) $text
       (str:concat (str:char-slice $text 0 $budget)
         (str:join "" ("\n[... " (repr (- $length $budget)) " more characters; (browse \"text\") reads the page]"))))))
(= (browse:show $result)
   (case $result
     (((BrowseOkV1 $text) (browse:cut (str:trim $text)))
      ((BrowseFailedV1 $why) (str:concat "BROWSE_ERROR: " $why)))))
; After an action, the page as it now is: its interactive elements.
(= (browse:then-snapshot $result)
   (case $result
     (((BrowseOkV1 $_) (browse:show (browse:ab ("snapshot" "-i"))))
      ($failed (browse:show $failed)))))

(= (browse:open $url)
   (let $target (str:trim $url)
     (if (not (or (str:starts-with $target "https://") (str:starts-with $target "http://")))
       (str:concat "BROWSE_ERROR: not an http(s) URL: " $target)
       (case (browse:ensure)
         (((BrowseFailedV1 $why) (str:concat "BROWSE_ERROR: " $why))
          ($_ (browse:then-snapshot (browse:ab ("open" $target)))))))))
(= (browse:act $action $arguments)
   (if (not (browse:server-running?)) "BROWSE_ERROR: no page is open; (browse \"open\" URL) first"
     (browse:then-snapshot (browse:ab (cons-atom $action $arguments)))))
(= (browse:close)
   (let* (($_ (browse:ab ("close")))
          ($_ (proc:run ("systemctl" "--user" "stop" (browse:unit)) "/" inherit 20000 4096)))
     "BROWSE: closed"))

(= (browse:command $verb $first $second)
   (case $verb
     (("open" (browse:open $first))
      ("snapshot" (browse:show (browse:ab ("snapshot" "-i"))))
      ("text" (browse:show (browse:ab ("snapshot"))))
      ("click" (browse:act "click" ($first)))
      ("fill" (browse:act "fill" ($first $second)))
      ("press" (browse:act "press" ($first)))
      ("back" (browse:act "back" ()))
      ("scroll" (let $direction (if (== $first "") "down" $first) (browse:act "scroll" ($direction))))
      ("close" (browse:close))
      ($other (str:join "" ("BROWSE_ERROR: unknown action " (repr $verb)
                " (open, snapshot, text, click, fill, press, back, scroll, close)"))))))
