; Optional modules load on their first use instead of at boot. A relative
; import! at run time resolves from the main script, which is not always in
; the code tree, so the path is resolved against the working directory: the
; runtime root, which the command arity scan reads too.
!(import! &self (library path))
!(import! &self (library system))
(= (lazy:import! $loaded $relative)
   (if (get-state $loaded) True
     (let* (($file (path:join (system:cwd) $relative))
            ($imported (collapse (catch (import! &self $file)))))
       (if (== $imported (True))
         (let $_ (change-state! $loaded True) True)
         (Error lazy:import! (ModuleUnavailable $relative))))))
