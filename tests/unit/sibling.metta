; Unit tests of src/sibling_cetta.metta against a scripted systemctl on PATH;
; no Python. The bodies live in the unit's home. The expected answers are
; those of the Python it replaces (src/sibling.py), except that a directory
; that merely begins with a body's name is no longer taken for that body.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/sibling_cetta.metta)

(= (sb:home) (nt:required-env "HOME"))
(= (sb:in $name) (path:join (sb:home) $name))
(= (sb:log)
   (let $path (sb:in "systemctl.log")
     (if (fs:exists $path) (filter-atom (str:split "\n" (fs:read-text $path)) $line (not (== $line ""))) ())))
(= (sb:clear-log) (let $path (sb:in "systemctl.log") (if (fs:exists $path) (fs:remove $path) True)))
(= (sb:state $unit $state) (fs:write (sb:in (str:concat "state-" $unit)) (str:concat $state "\n")))
(= (sb:flag $name $on) (let $path (sb:in $name) (if $on (fs:write $path "") (if (fs:exists $path) (fs:remove $path) True))))
(= (sb:passed $block) (let $_ (add-atom &self (sb:block-passed $block)) (println! $block)))

; The scripted systemctl: logs each call, with "+marker" when the unit's body
; holds a pause marker at that moment, and keeps each unit's state in a file.
(= (sb:systemctl-script)
   "#!/bin/sh\nverb=$2; unit=$3; state=\"$HOME/state-$unit\"\nmarker=\"\"; [ -e \"$HOME/${unit%.service}/PAUSED-BY-OPERATOR.md\" ] && marker=\" +marker\"\necho \"$*$marker\" >> \"$HOME/systemctl.log\"\ncase \"$verb\" in\n  is-active) s=$(cat \"$state\" 2>/dev/null || echo inactive); echo \"$s\"; [ \"$s\" = active ] ;;\n  start) if [ -e \"$HOME/fail-start\" ]; then echo \"Failed to start $unit: Unit not found.\" >&2; exit 5; fi; echo active > \"$state\" ;;\n  stop) [ -e \"$HOME/stuck\" ] || echo inactive > \"$state\" ;;\nesac\n")

!(let* (($_ (fs:make-directories (sb:in "bin") 448))
        ($_ (fs:write (sb:in "bin/systemctl") (sb:systemctl-script)))
        ($_ (fs:set-mode (sb:in "bin/systemctl") 448))
        ($_ (fs:make-directories (sb:in "pettaclaw-godel/work") 448))
        ($_ (fs:make-directories (sb:in "pettaclaw-lila") 448))
        ($_ (fs:make-directories (sb:in "pettaclaw-godel-old") 448))
        ($_ (nt:require body-itself (nt:equal (sibling:me-in (sb:in "pettaclaw-godel")) godel)))
        ($_ (nt:require below-a-body (nt:equal (sibling:me-in (sb:in "pettaclaw-godel/work")) godel)))
        ($_ (nt:require other-body (nt:equal (sibling:me-in (sb:in "pettaclaw-lila")) lila)))
        ($_ (nt:require a-name-that-only-begins-like-a-body (nt:equal (sibling:me-in (sb:in "pettaclaw-godel-old")) none)))
        ($_ (nt:require outside-every-body (nt:equal (sibling:me-in (sb:home)) none)))
        ($_ (nt:require missing-directory (nt:equal (sibling:me-in (sb:in "nowhere")) none))))
   (sb:passed UNIT_SIBLING_BODIES_OK))

; Status, stop and start of the sibling, from each body.
!(let* (($godel (sb:in "pettaclaw-godel")) ($lila (sb:in "pettaclaw-lila"))
        ($_ (sb:state "pettaclaw-godel.service" "active"))
        ($_ (sb:state "pettaclaw-lila.service" "active"))
        ($_ (nt:require lila-sees-godel
              (nt:equal (sibling:status-in $lila) "Gödel: active | watchdog revives within 30 min if down")))
        ($_ (nt:require status-asks-only-is-active (nt:equal (sb:log) ("--user is-active pettaclaw-godel.service"))))
        ($_ (sb:clear-log))
        ($_ (nt:require godel-stops-lila
              (nt:equal (sibling:off-in $godel "maintenance")
                        "Lila paused: service stopped, pause marker set — the watchdog will leave them down")))
        ($marker (fs:read-text (path:join $lila "PAUSED-BY-OPERATOR.md")))
        ($_ (nt:require marker-names-who-and-why
              (and (nt:contains $marker "Paused by: Gödel via (sibling-off)") (nt:contains $marker "Reason: maintenance\n"))))
        ($_ (nt:require marker-before-stop
              (nt:equal (sb:log) ("--user stop pettaclaw-lila.service +marker" "--user is-active pettaclaw-lila.service +marker"))))
        ($_ (nt:require paused-status
              (nt:equal (sibling:status-in $godel) "Lila: inactive | watchdog will NOT revive (pause marker present)")))
        ($_ (sibling:off-in $godel ""))
        ($_ (nt:require no-reason-given (nt:contains (fs:read-text (path:join $lila "PAUSED-BY-OPERATOR.md")) "Reason: (none given)\n")))
        ($_ (nt:require godel-resumes-lila
              (nt:equal (sibling:on-in $godel)
                        "Lila resumed: pause marker cleared, service active — they will boot, read their inbox, and start their loop")))
        ($_ (nt:require resume-clears-marker (not (fs:exists (path:join $lila "PAUSED-BY-OPERATOR.md")))))
        ($_ (nt:require marker-gone-before-start
              (nt:contains (str:join "\n" (sb:log)) "--user start pettaclaw-lila.service\n")))
        ($_ (sb:flag "stuck" True))
        ($_ (nt:require a-stop-that-does-not-stop
              (nt:equal (sibling:off-in $godel "x") "sibling-off incomplete: marker set but service is 'active' ()")))
        ($_ (sb:flag "stuck" False))
        ($_ (sb:state "pettaclaw-lila.service" "inactive"))
        ($_ (sb:flag "fail-start" True))
        ($_ (nt:require a-start-that-does-not-start
              (nt:equal (sibling:on-in $godel)
                        "sibling-on incomplete: marker cleared but service is 'inactive' (Failed to start pettaclaw-lila.service: Unit not found.) — the watchdog will retry within 30 min")))
        ($_ (sb:flag "fail-start" False))
        ($_ (nt:require outside-a-body
              (nt:equal ((sibling:status-in (sb:home)) (sibling:on-in (sb:home)) (sibling:off-in (sb:home) ""))
                        ("sibling-status failed: cannot tell which body this is" "sibling-on failed: cannot tell which body this is"
                         "sibling-off failed: cannot tell which body this is"))))
        ($_ (sb:clear-log)))
   (sb:passed UNIT_SIBLING_CONTROL_OK))

; The operator's start of this body.
!(let* (($godel (sb:in "pettaclaw-godel"))
        ($_ (fs:write (path:join $godel "PAUSED-BY-OPERATOR.md") "paused by sibling\n"))
        ($_ (sb:state "pettaclaw-godel.service" "inactive"))
        ($_ (nt:require start-clears-own-pause-and-starts
              (nt:equal (sibling:resume-self-in (path:join $godel "work")) "pause marker cleared; service active")))
        ($_ (nt:require own-marker-gone (not (fs:exists (path:join $godel "PAUSED-BY-OPERATOR.md")))))
        ($_ (nt:require own-marker-gone-before-start (nt:contains (str:join "\n" (sb:log)) "--user start pettaclaw-godel.service\n")))
        ($_ (sb:clear-log))
        ($_ (nt:require running-agent-left-alone (nt:equal (sibling:resume-self-in $godel) "service already active")))
        ($_ (nt:require only-asked-whether-active (nt:equal (sb:log) ("--user is-active pettaclaw-godel.service"))))
        ($_ (sb:state "pettaclaw-godel.service" "inactive"))
        ($_ (sb:flag "fail-start" True))
        ($_ (nt:require failed-start-reported
              (nt:equal (sibling:resume-self-in $godel) "service start failed (Failed to start pettaclaw-godel.service: Unit not found.)")))
        ($_ (sb:flag "fail-start" False))
        ($_ (nt:require resume-outside-a-body (nt:equal (sibling:resume-self-in (sb:home)) "resume: cannot tell which body this is"))))
   (sb:passed UNIT_SIBLING_RESUME_OK))

!(nt:require every-block-ended
   (nt:equal (size-atom (collapse (match &self (sb:block-passed $block) $block))) 3))
!(println! UNIT_SIBLING_OK)
