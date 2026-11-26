; git-import!'s second argument is a build command, not a commit: (git-import!
; URL BUILD BASE) clones URL into BASE and runs BUILD in the clone. A commit
; hash given there runs as a command, fails, and the half-made clone is
; removed. The agent's boot fetches nothing. Offline: the repository is local.
!(import! &self ../native_test_support.metta)
!(import! &self (library list))

(= (gi:git $origin $args)
   (nt:exit-ok (proc:run (list:append ("git" "-C" $origin "-c" "user.name=t" "-c" "user.email=t@example.invalid") $args)
                         $origin (nt:environment (nt:required-env "HOME")) 15000 65536)))
; A boot that imports the probe library with the given build argument: its
; output and the clone it leaves.
(= (gi:boot $home $origin $build $label)
   (let* (($base (path:join $home (str:concat "repos-" $label)))
          ($program (path:join $home "boot.metta"))
          ($_ (fs:write $program (str:join "" ("!(import! &self (library lib_import))\n"
                                               "!(git-import! \"file://" $origin "\" \"" $build "\" \"" $base "\")\n"
                                               "!(import! &self (library petta_lib_probe lib_probe))\n"
                                               "!(probe-loaded)\n"))))
          ($result (proc:run ((system:executable) "--lang" "petta" $program) $home
                             (nt:environment $home) 120000 1048576 "merged")))
     ((case $result (((proc:exited $_c $out $_e) $out) ($bad (repr $bad)))) (path:join $base "petta_lib_probe"))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY")) ($home (nt:required-env "HOME"))
         ($boot-source (fs:read-text (path:join $repo "lib_mettaclaw.metta")))
         ($_ (nt:require boot-fetches-nothing-with-git
               (and (not (nt:contains $boot-source "!(git-import! \"")) (not (nt:contains $boot-source "(library petta_lib_chromadb")))))
         ($origin (path:join $home "petta_lib_probe")) ($_ (fs:make-directories $origin 448))
         ($_ (fs:write (path:join $origin "lib_probe.metta") "(= (probe-loaded) yes)\n"))
         ($_ (gi:git $origin ("init" "-q")))
         ($_ (gi:git $origin ("add" "lib_probe.metta")))
         ($_ (gi:git $origin ("commit" "-qm" "probe")))
         ($commit (str:strip-python (gi:git $origin ("rev-parse" "HEAD"))))
         (($failed $failed-clone) (gi:boot $home $origin $commit "commit"))
         ($_ (nt:require a-commit-given-as-build-runs-as-a-command
               (and (nt:contains $failed (str:concat "Running build: " $commit)) (nt:contains $failed "build step failed"))))
         ($_ (nt:require the-failed-clone-is-removed (not (fs:exists $failed-clone))))
         (($loaded $clone) (gi:boot $home $origin "" "empty"))
         ($_ (nt:require an-empty-build-runs-nothing (not (nt:contains $loaded "Running build"))))
         ($_ (nt:require the-clone-is-kept-and-loads
               (and (fs:is-file (path:join $clone "lib_probe.metta")) (nt:contains $loaded "yes")))))
   (println! UNIT_GIT_IMPORT_ARGUMENTS_OK))
