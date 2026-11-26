; A recycle request: the operator's script puts the flag where the runtime
; reads it (under the instance name), and the runtime observes the flag
; without consuming it; no configured path, or a directory at the path, is
; no request.
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
   (println! UNIT_RECYCLE_REQUEST_OK))
