; The installed mail client is a snapshot named by its content, not a link into
; the changing checkout; a reinstall replaces the command link only when asked,
; and the earlier snapshot stays intact.
!(import! &self ../native_test_support.metta)

(= (mi:install $root $target $extra)
   (proc:run (list:append ("sh" (path:join $root "scripts/install_claw_mail.sh") "--engine" "/bin/true"
                           "--bin-dir" $target "--no-skill") $extra)
             $root (nt:environment (nt:required-env "HOME")) 10000 65536))
(= (mi:code $result) (case $result (((proc:exited $code $_o $_e) $code) ($bad -1))))
; The snapshot directory the command link resolves into.
(= (mi:snapshot $target)
   (path:dirname (str:strip-python (nt:exit-ok (proc:run ("readlink" "-f" (path:join $target "claw-mail"))
                                                         $target inherit 5000 4096)))))
!(import! &self (library list))

!(let* (($home (nt:required-env "HOME")) ($repo (nt:required-env "TEST_REPOSITORY"))
         ($root (path:join $home "source")) ($scripts (path:join $root "scripts"))
         ($_ (fs:make-directories $scripts 448))
         ($_ (fs:write (path:join $scripts "install_claw_mail.sh") (fs:read-text (path:join $repo "scripts/install_claw_mail.sh"))))
         ($client (path:join $scripts "claw_mail_cetta.metta"))
         ($_ (fs:write $client "(original client)\n"))
         ($_ (fs:write (path:join $scripts "shared_mail.metta") "(original mailbox)\n"))
         ($target (path:join $home "installed/bin"))
         ($_ (nt:require first-install-succeeds (== (mi:code (mi:install $root $target ())) 0)))
         ($first (mi:snapshot $target))
         ($_ (fs:write $client "(new development work)\n"))
         ($_ (nt:require checkout-edits-do-not-reach-the-installed-client
               (== (fs:read-text (path:join $first "claw_mail_cetta.metta")) "(original client)\n")))
         ($_ (nt:require reinstall-refuses-to-replace-the-link-unasked
               (== (mi:code (mi:install $root $target ())) 2)))
         ($_ (nt:require forced-reinstall-succeeds (== (mi:code (mi:install $root $target ("--force-links"))) 0)))
         ($second (mi:snapshot $target))
         ($_ (nt:require new-content-gets-a-new-snapshot (not (== $first $second))))
         ($_ (nt:require the-new-snapshot-holds-the-new-client
               (== (fs:read-text (path:join $second "claw_mail_cetta.metta")) "(new development work)\n")))
         ($_ (nt:require the-earlier-snapshot-is-intact
               (== (fs:read-text (path:join $first "claw_mail_cetta.metta")) "(original client)\n")))
         ($_ (nt:require the-command-runs-the-new-snapshot
               (nt:contains (fs:read-text (path:join $target "claw-mail")) (path:join $second "claw_mail_cetta.metta")))))
   (println! UNIT_MAIL_INSTALL_OK))
