; A recycle request: the operator's script puts the flag where the runtime
; reads it (under the instance name), and the runtime observes the flag
; without consuming it; no configured path, or a directory at the path, is
; no request. The runtime's own request is written whole, renamed into
; place, private (0600) and names its reason; the engine it runs is the
; configured CeTTa, and no other engine has a command.
!(import! &self ../native_test_support.metta)
!(import! &self (library list))

; The runtime's answer for one flag path, from a fresh process.
(= (rr:observed $repo $home $path)
   (let* (($program (path:join $home "observe.metta"))
          ($_ (fs:write $program (str:concat "!(import! &self " (str:concat (path:join $repo "src/boot_state_cetta.metta")
                                   ")\n!(println! (RECYCLE (boot-state:recycle)))\n"))))
          ($out (nt:exit-ok (proc:run ((system:executable) "--lang" "petta" "--profile" "extended" "--import-mode" "ancestor-walk"
                                       "--quiet" $program)
                                      $home (cons-atom ("METTACLAW_RECYCLE_REQUEST_PATH" $path) (nt:environment $home))
                                      30000 65536))))
     (if (nt:contains $out "(RECYCLE true)") True (if (nt:contains $out "(RECYCLE false)") False (Unexpected $out)))))
(= (rr:basename $path) (rr:last (str:split "/" $path)))
(= (rr:last $items) (if (== (cdr-atom $items) ()) (car-atom $items) (rr:last (cdr-atom $items))))
; Where the operator's script puts the flag, for an instance name or none.
(= (rr:requested $repo $home $state $extra)
   (let* (($_ (proc:run ("bash" (path:join $repo "scripts/request_recycle.sh")) $home
                        (list:append (("XDG_STATE_HOME" $state) ("METTACLAW_RECYCLE_WAIT_SECONDS" "0")) (list:append $extra (nt:environment $home)))
                        30000 65536)))
     True))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
         ($named (path:join $home "state-named")) ($default (path:join $home "state-default"))
         ($_ (rr:requested $repo $home $named (("METTACLAW_INSTANCE" "pettaclaw-probe"))))
         ($_ (nt:require the-script-uses-the-configured-instance-name
               (fs:is-file (path:join $named "pettaclaw-probe/recycle.requested"))))
         ($_ (rr:requested $repo $home $default ()))
         ($_ (nt:require the-script-defaults-to-the-checkout-name
               (fs:is-file (path:join (path:join $default (rr:basename $repo)) "recycle.requested"))))
         ($_ (nt:require no-configured-path-is-no-request (== (rr:observed $repo $home "") False)))
         ($flag (path:join $home "recycle.requested")) ($_ (fs:write $flag "request\n"))
         ($_ (nt:require a-flag-is-a-request (== (rr:observed $repo $home $flag) True)))
         ($_ (nt:require observing-does-not-consume-the-flag (fs:is-file $flag)))
         ($_ (nt:require a-directory-at-the-path-is-no-request (== (rr:observed $repo $home $home) False))))
   (println! UNIT_RECYCLE_REQUEST_CASES_OK))

; The runtime's own request and its engine command, from a fresh process.
(= (rr:runtime $repo $home $path)
   (let* (($program (path:join $home "request.metta"))
          ($_ (fs:write $program (str:join "" ("!(import! &self " (path:join $repo "src/engine_modes_cetta.metta") ")\n"
                                               "!(println! (REQUESTED (engine-native:recycle)))\n"
                                               "!(println! (CETTA (engine-native:command \"cetta\")))\n"
                                               "!(println! (PETTA (engine-native:command \"petta\")))\n")))))
     ; Under umask 0, so the mode is the runtime's own choice.
     (nt:exit-ok (proc:run ("sh" "-c" "umask 0; exec \"$@\"" "sh"
                            (system:executable) "--lang" "petta" "--profile" "extended" "--import-mode" "ancestor-walk" "--quiet" $program)
                           $repo (cons-atom ("METTACLAW_RECYCLE_REQUEST_PATH" $path)
                                   (cons-atom ("CETTA_BIN" (path:join $home "bundle/cetta")) (nt:environment $home)))
                           30000 65536))))
(= (rr:mode $path)
   (case (fs:metadata-v2 $path no-follow)
     (((fs:info-v2 $kind $size $identity $modified $changed (fs:permissions $mode) $blocks) $mode) ($other (NoMode $other)))))
!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
        ($flag (path:join $home "runtime-state/recycle.requested"))
        ($_ (fs:make-directories (path:join $home "runtime-state") 504))
        ($out (rr:runtime $repo $home $flag))
        ($_ (nt:require the-runtime-request-is-written
              (and (nt:contains $out "(REQUESTED true)")
              (and (nt:contains (fs:read-text $flag) "reason=mode-switch")
                   (str:starts-with (fs:read-text $flag) "requested_at=")))))
        ($_ (nt:require the-request-is-private (== (rr:mode $flag) 384)))
        ($_ (nt:require no-temporary-is-left
              (== (filter-atom (fs:entries (path:join $home "runtime-state")) $n (str:starts-with $n "recycle.requested.tmp")) ())))
        ($_ (nt:require the-engine-is-the-configured-cetta
              (and (nt:contains $out (str:join "" ("(CETTA (" (repr (path:join $home "bundle/cetta")) "))")))
                   (nt:contains $out "(PETTA ())"))))
        ($none (rr:runtime $repo $home "")))
   (nt:require no-path-requests-nothing (nt:contains $none "(REQUESTED false)")))
!(println! UNIT_RECYCLE_REQUEST_OK)
