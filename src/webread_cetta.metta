; Reading a web page as text. Lightpanda renders the page (its JavaScript runs;
; layout, images and fonts are skipped) in a memory-capped scope of its own and
; prints it as Markdown. It obeys robots.txt, never reaches private or local
; addresses, and sends no telemetry. When it refuses or fails, the reason is
; reported; nothing else fetches the page behind its back. The text is trimmed
; to a character budget; with a search phrase, only the passages around its
; matches are kept.
!(import! &self (library proc))
!(import! &self ./websearch_cetta.metta)

(= (web:setting $name $default) (system:getenv-or-default $name $default))
(= (web:budget) (str:string->number (web:setting "METTACLAW_READ_URL_CHARS" "12000")))

; The render command, in a scope of its own unless METTACLAW_READ_URL_SCOPE is 0.
(= (web:argv $url)
   (let* (($bin (web:setting "METTACLAW_LIGHTPANDA_BIN" "lightpanda"))
          ($memory (str:concat "MemoryMax=" (web:setting "METTACLAW_READ_URL_MEMORY" "768M")))
          ($fetch ("env" "LIGHTPANDA_DISABLE_TELEMETRY=true" $bin "fetch" "--obey-robots" "--block-private-networks"
                   "--dump" "markdown" "--log-level" "error" $url)))
     (if (== (web:setting "METTACLAW_READ_URL_SCOPE" "1") "0") $fetch
       (list:append ("systemd-run" "--user" "--scope" "-q" "-p" $memory "-p" "MemorySwapMax=0" "timeout" "45")
                    $fetch))))

; Lightpanda names why a page failed (err=RobotsBlocked, err=CouldntConnect, ...).
(= (web:failure-reason $code $stderr)
   (case (re:captures (re:compile "err=([A-Za-z]+)") $stderr)
     ((re:no-match (if (== $code 124) "timed out" (str:concat "render exit " (repr $code))))
      ($match (let $name (re:group-text $match $stderr 1)
                (case $name
                  (("RobotsBlocked" "robots.txt of the site disallows this page")
                   ("CouldntConnect" "could not connect (private and local addresses are never fetched)")
                   ("CouldntResolveHost" "unknown host")
                   ($other $other))))))))

(= (web:page $url)
   (case (proc:run (web:argv $url) (system:getcwd) inherit 60000 8388608)
     (((proc:exited 0 $text $_) (if (== (str:trim $text) "") (WebReadFailedV1 "empty page") (WebPageV1 $text)))
      ((proc:exited $code $_ $err) (WebReadFailedV1 (web:failure-reason $code $err)))
      ($other (WebReadFailedV1 "renderer unavailable; scripts/install_web_tools.sh installs it")))))

;; Presentation

; The first line that is a top-level heading, or -1.
(= (web:first-h1 $lines $index)
   (if (== $lines ()) -1
     (if (str:starts-with (car-atom $lines) "# ") $index (web:first-h1 (cdr-atom $lines) (+ $index 1)))))
(= (web:drop $n $lines) (if (or (== $n 0) (== $lines ())) $lines (web:drop (- $n 1) (cdr-atom $lines))))
; Navigation before the page's own heading goes, when that heading comes in
; the first 60% of the page.
(= (web:main $text)
   (let* (($lines (str:split "\n" $text)) ($count (size-atom $lines)) ($h1 (web:first-h1 $lines 0)))
     (if (and (> $h1 0) (< (* $h1 10) (* $count 6))) (str:join "\n" (web:drop $h1 $lines)) $text)))
(= (web:cut $text $budget)
   (let $length (str:char-length $text)
     (if (<= $length $budget) $text
       (str:concat (str:char-slice $text 0 $budget)
         (str:concat "\n[... " (str:concat (repr (- $length $budget))
           " more characters; (read-url URL \"words\") returns the passages around those words]"))))))

(= (web:matches? $line $needle)
   (case (str:find (str:lower $line) $needle) (((TextNotFound $_) False) ($_ True))))
; The indices of the lines that contain the phrase, at most `limit`.
(= (web:hits $lines $needle $index $limit)
   (if (or (== $lines ()) (== $limit 0)) ()
     (if (web:matches? (car-atom $lines) $needle)
       (cons-atom $index (web:hits (cdr-atom $lines) $needle (+ $index 1) (- $limit 1)))
       (web:hits (cdr-atom $lines) $needle (+ $index 1) $limit))))
; Windows of two lines around each hit, overlapping windows joined.
(= (web:windows $hits $start $end)
   (if (== $hits ()) ((WebWindowV1 $start $end))
     (let $hit (car-atom $hits)
       (if (<= (- $hit 2) (+ $end 1)) (web:windows (cdr-atom $hits) $start (+ $hit 2))
         (cons-atom (WebWindowV1 $start $end) (web:windows (cdr-atom $hits) (- $hit 2) (+ $hit 2)))))))
(= (web:slice $lines $start $end)
   (str:join "\n" (list:take (+ (- $end (max 0 $start)) 1) (web:drop (max 0 $start) $lines))))
(= (web:passages $text $phrase)
   (let* (($lines (str:split "\n" $text)) ($needle (str:lower (str:trim $phrase)))
          ($hits (web:hits $lines $needle 0 12)))
     (if (== $hits ()) ()
       (let* (($first (car-atom $hits))
              ($windows (web:windows (cdr-atom $hits) (- $first 2) (+ $first 2))))
         (map-atom $windows $w (case $w (((WebWindowV1 $s $e) (web:slice $lines $s $e)))))))))

(= (web:present $url $text $phrase)
   (if (== (str:trim $phrase) "")
     (str:concat (str:concat "URL: " (str:concat $url "\n\n")) (web:cut (web:main $text) (web:budget)))
     (let $passages (web:passages $text $phrase)
       (if (== $passages ())
         (str:join "" ("READ_URL: no line of " $url " contains \"" $phrase "\" ("
                       (repr (str:char-length $text)) " characters read)"))
         (str:concat (str:join "" ("URL: " $url "\nPASSAGES: " (repr (size-atom $passages)) " for \"" $phrase "\"\n\n"))
                     (web:cut (str:join "\n---\n" $passages) (web:budget)))))))

(= (web:read $url $phrase)
   (let $target (str:trim $url)
     (if (not (or (str:starts-with $target "https://") (str:starts-with $target "http://")))
       (str:concat "READ_URL_ERROR: not an http(s) URL: " $target)
       (case (web:page $target)
         (((WebPageV1 $text) (web:present $target $text $phrase))
          ((WebReadFailedV1 $why) (str:join "" ("READ_URL_ERROR: " $target " (" $why ")"))))))))
