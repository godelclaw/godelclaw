; The tmux skills against a stand-in tmux (tests/fixtures/fake_tmux.sh) and
; a real, separate tmux server. Windows and pane text are trimmed and line
; counts read as before; empty and failed answers are distinct; a send pastes
; the decoded text literally, never through a shell, then submits it with its
; own Enter at least the pause later; an empty message touches nothing; no
; Enter follows a failed paste; a failed submit is reported apart from a
; failed paste. The expected answers are those of the Python bridge this
; replaced, on the same inputs. On a real tmux server of its own, a send
; arrives as literal input; the server is named by an explicit socket in the
; wrapper, so the default server is never touched.
!(import! &self ../native_test_support.metta)
!(import! &self (library list))

(= (tx:root) (nt:required-env "TEST_REPOSITORY"))
(= (tx:home) (nt:required-env "HOME"))
(= (tx:engine) (path:join (nt:required-env "NATIVE_TEST_CORE") "cetta"))
(= (tx:dir $name) (let $d (path:join (tx:home) $name) (let $_ (fs:make-directories $d 448) $d)))
; A scenario's directory: its stand-in tmux on PATH, answering from files.
(= (tx:scenario $name $files)
   (let* (($d (tx:dir $name)) ($bin (tx:dir (str:concat $name "-bin")))
          ($_ (fs:write (path:join $bin "tmux")
                (str:concat "#!/bin/sh\nexec /bin/sh " (str:concat (repr (path:join (tx:root) "tests/fixtures/fake_tmux.sh")) " \"$@\"\n"))))
          ($_ (proc:run ("chmod" "700" (path:join $bin "tmux")) $d (nt:environment (tx:home)) 10000 4096))
          ($_ (map-atom $files $file (let ($n $text) $file (fs:write (path:join $d $n) $text)))))
     (Scenario $d $bin)))
(= (tx:env $scenario)
   (let (Scenario $d $bin) $scenario
     (list:append (("TMUX_FIXTURE_DIR" $d)
                   ("PATH" (str:concat $bin (str:concat ":" (system:getenv-or-default "PATH" "/usr/bin:/bin")))))
       (filter-atom (nt:environment (tx:home)) $pair (not (== (car-atom $pair) "PATH"))))))
; Run calls in a guest; each answer is written to answer-N.
(= (tx:answers $scenario $calls $environment)
   (let* (((Scenario $d $bin) $scenario)
          ($forms (map-atom (list:range 0 (size-atom $calls)) $i
                    (str:join "" ("!(let $answers (collapse " (list:at $calls $i) ") (fs:write "
                                  (repr (path:join $d (str:concat "answer-" (repr $i))))
                                  " (if (== (size-atom $answers) 1) (car-atom $answers) \"WRONG_ANSWER_COUNT\")))"))))
          ($source (path:join $d "calls.metta"))
          ($_ (fs:write $source (str:concat (str:join "\n"
                (cons-atom "!(import! &self (library fs))"
                  (cons-atom (str:concat "!(import! &self " (str:concat (repr (path:join (tx:root) "src/tmux_cetta.metta")) ")")) $forms))) "\n")))
          ($run (proc:run ((tx:engine) "--lang" "petta" "--import-mode" "ancestor-walk" $source)
                          (tx:root) $environment 60000 1048576)))
     (case $run
       (((proc:exited 0 $out $err)
         (map-atom (list:range 0 (size-atom $calls)) $j (fs:read-text (path:join $d (str:concat "answer-" (repr $j))))))
        ($other (let $_ (println! (TMUX_GUEST_FAILED $other)) $other))))))
(= (tx:calls $scenario)
   (let (Scenario $d $bin) $scenario
     (if (fs:exists (path:join $d "calls"))
         (filter-atom (str:split "\n" (fs:read-text (path:join $d "calls"))) $l (not (== $l "")))
         ())))
(= (tx:argv $line) (cdr-atom (str:split "\t" $line)))
(= (tx:at $line) (str:string->number (car-atom (str:split "\t" $line))))
(= (tx:which $program)
   (case (proc:run ("/bin/sh" "-c" (str:concat "command -v " $program)) (tx:home) (nt:environment (tx:home)) 10000 4096)
     (((proc:exited 0 $out $err) (str:trim $out)) ($other ""))))
; Send to a real, separate server, read its pane, and always stop it.
(= (tx:real-server $tmux)
   (let* (($d (tx:dir "real")) ($bin (tx:dir "real-bin")) ($socket (path:join $d "separate.sock"))
          ($started (proc:run ($tmux "-f" "/dev/null" "-S" $socket "new-session" "-d" "-s" "fixture" "cat")
                              $d (nt:environment (tx:home)) 10000 4096))
          ($_ (fs:write (path:join $bin "tmux")
                (str:join "" ("#!/bin/sh\nexec " (repr $tmux) " -S " (repr $socket) " \"$@\"\n"))))
          ($_ (proc:run ("chmod" "700" (path:join $bin "tmux")) $d (nt:environment (tx:home)) 10000 4096))
          ($environment (cons-atom ("TMUX" (str:concat $socket ",0,0")) (tx:env (Scenario $d $bin))))
          ($sent (tx:answers (Scenario $d $bin) ("(tmux-send \"fixture:0\" \"native literal λ 🌸\")") $environment))
          ($pane (proc:run ($tmux "-S" $socket "capture-pane" "-p" "-t" "fixture:0") $d (nt:environment (tx:home)) 10000 65536))
          ($_ (proc:run ($tmux "-S" $socket "kill-server") $d (nt:environment (tx:home)) 10000 4096)))
     (and (== $sent ((tx:sent "fixture:0")))
          (case $pane (((proc:exited 0 $text $err) (nt:contains $text "native literal λ 🌸")) ($other False))))))
(= (tx:sent $target) (str:join "" ("sent to " $target " — the session will see it as user input; (tmux-peek \"" $target "\" 30) in a later turn to read the reply")))

!(let* (($pane (tx:scenario "pane" (("list-windows.stdout" "  agent:5 λ 🌸\n") ("capture-pane.stdout" "  first\r\n\r\n   \nλ 🌸\rlast  \n"))))
        ($peeks ("1" "2" "0" "-3" "2.8" "\"3\"" "\"+2\"" "\" 2 \"" "\"bad\"" "\"2.0\""))
        ($answers (tx:answers $pane (cons-atom "(tmux-windows)" (map-atom $peeks $n (str:join "" ("(tmux-peek \"agent:5\" " $n ")"))))
                    (tx:env $pane)))
        ($_ (nt:require windows-and-pane-text-read-as-before
              (== $answers ("agent:5 λ 🌸" "last" "λ 🌸\nlast" "last" "last" "λ 🌸\nlast" "first\nλ 🌸\nlast"
                            "λ 🌸\nlast" "λ 🌸\nlast" "first\nλ 🌸\nlast" "first\nλ 🌸\nlast"))))
        ($_ (nt:require every-read-is-one-tmux-call-listing-all-windows
              (let $lines (tx:calls $pane)
                (and (== (size-atom $lines) 11) (== (list:take 3 (tx:argv (car-atom $lines))) ("list-windows" "-a" "-F"))))))
        ($empty (tx:scenario "empty" ()))
        ($_ (nt:require empty-answers-are-explicit
              (== (tx:answers $empty ("(tmux-windows)" "(tmux-peek \"agent:5\" 3)") (tx:env $empty))
                  ("(no tmux windows)" "(pane is empty)"))))
        ($failing (tx:scenario "failing" (("list-windows.code" "1") ("list-windows.stderr" "can't find server\n")
                                          ("capture-pane.code" "1") ("capture-pane.stderr" "can't find window\n"))))
        ($_ (nt:require failed-reads-say-so
              (== (tx:answers $failing ("(tmux-windows)" "(tmux-peek \"agent:5\" 3)") (tx:env $failing))
                  ("tmux-windows failed: can't find server"
                   "tmux-peek failed: can't find window — name a window from (tmux-windows), e.g. \"session:window\""))))
        ($send (tx:scenario "send" ()))
        ($marker (path:join (tx:home) "must-not-exist"))
        ($text (str:join "" ("λ _quote_x_quote__newline_$(touch " $marker "); _apostrophe_y_apostrophe_")))
        ($sent (tx:answers $send ((str:join "" ("(tmux-send \"agent:5\" " (repr $text) ")"))) (tx:env $send)))
        ($lines (tx:calls $send))
        ($_ (nt:require a-send-pastes-literally-then-submits-after-the-pause
              (and (== $sent ((tx:sent "agent:5")))
              (and (== (size-atom $lines) 2)
              (and (== (tx:argv (car-atom $lines))
                       ("send-keys" "-l" "-t" "agent:5" "--" (str:join "" ("λ \"x\"\\n$(touch " $marker "); 'y'"))))
              (and (== (tx:argv (list:at $lines 1)) ("send-keys" "-t" "agent:5" "Enter"))
                   (>= (- (tx:at (list:at $lines 1)) (tx:at (car-atom $lines))) 1.9)))))))
        ($_ (nt:require the-pasted-text-never-reaches-a-shell (not (fs:exists $marker))))
        ($bare (tx:scenario "bare" ()))
        ($_ (nt:require a-bare-window-target-stays-bare-and-empty-touches-nothing
              (and (== (tx:answers $bare ("(tmux-send \"godel\" \"hello\")" "(tmux-send \"agent:5\" \"   \")") (tx:env $bare))
                       ((tx:sent "godel") "tmux-send: empty message"))
                   (and (== (size-atom (tx:calls $bare)) 2)
                        (== (tx:argv (car-atom (tx:calls $bare))) ("send-keys" "-l" "-t" "godel" "--" "hello"))))))
        ($no-paste (tx:scenario "no-paste" (("send-keys.code" "1") ("send-keys.stderr" "can't find window"))))
        ($_ (nt:require no-enter-follows-a-failed-paste
              (and (== (tx:answers $no-paste ("(tmux-send \"agent:missing\" \"hello\")") (tx:env $no-paste))
                       ("tmux-send failed: can't find window"))
                   (== (size-atom (tx:calls $no-paste)) 1))))
        ($no-submit (tx:scenario "no-submit" (("submit.code" "1") ("submit.stderr" "can't submit"))))
        ($_ (nt:require a-failed-submit-is-reported-apart
              (== (tx:answers $no-submit ("(tmux-send \"agent:5\" \"hello\")") (tx:env $no-submit))
                  ("tmux-send submit failed: can't submit"))))
        ($tmux (tx:which "tmux"))
        ($_ (if (== $tmux "") (println! (SKIPPED a-real-separate-server-receives-literal-input "tmux is not installed"))
              (nt:require a-real-separate-server-receives-literal-input (tx:real-server $tmux)))))
   (println! UNIT_TMUX_OK))
