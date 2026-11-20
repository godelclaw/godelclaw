!(import! &self (library system))
!(import! &self ../../src/history_pending_cetta.metta)
; Loaded alone, before any other module: the module reads a mode's pending
; record by its own imports.
!(change-state! &standalone-end
   (let* (($home (system:getenv-or-default "PENDING_HOME" ""))
          ($root (path:join $home "modes"))
          ($_ (fs:make-directories (path:join $root "iter") 448))
          ($_ (fs:write (path:join (path:join $root "iter") "native-checkpoint-v1.json")
                "{\"status\":\"pending\",\"operation\":\"append\",\"device\":\"7\",\"inode\":\"8\",\"offset\":12}")))
     (history-pending:end $root (path:join $home "history.metta") "7" "8" 42)))
!(import! &self ../native_test_support.metta)
!(let $_ (nt:require standalone-module-reads-pending (== (get-state &standalone-end) 12))
   (println! UNIT_HISTORY_PENDING_STANDALONE_OK))
