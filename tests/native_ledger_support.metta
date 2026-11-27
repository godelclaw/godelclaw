; A real native ledger for a unit: the agent's ledger server on a store in
; the unit's home, listening on METTACLAW_INPUT_LEDGER_SOCKET. (nls:start)
; answers (LedgerUp PROCESS) once the socket exists; (nls:stop UP) ends the
; server and clears its socket, so the next start serves the same store.
!(import! &self ./native_test_support.metta)

(= (nls:socket) (nt:required-env "METTACLAW_INPUT_LEDGER_SOCKET"))
(= (nls:wait-socket $server $left)
   (if (fs:exists (nls:socket)) True
     (if (<= $left 0) False (let $_ (proc:wait $server 20) (nls:wait-socket $server (- $left 1))))))
(= (nls:start)
   (let* (($home (nt:required-env "HOME")) ($root (nt:required-env "TEST_REPOSITORY"))
          ($ledger (proc:spawn ((path:join (nt:required-env "NATIVE_TEST_CORE") "cetta-value-worker")
                                "--value-bytes" "268435456" "--serve" "--lang" "petta"
                                (path:join $root "src/input_ledger_server.metta") "db-server:configuration")
                     ((cwd $root)
                      (env (cons-atom ("CETTA_DB_SOURCE" (path:join $home "store"))
                             (cons-atom ("CETTA_DB_SOCKET" (nls:socket)) (nt:environment $home))))
                      (max-bytes 65536) (lifetime owner)))))
     (if (nls:wait-socket $ledger 500) (LedgerUp $ledger) (LedgerDown $ledger))))
(= (nls:up? $started) (case $started (((LedgerUp $ledger) True) ($other False))))
(= (nls:stop (LedgerUp $ledger))
   (let* (($_ (proc:cancel $ledger)) ($_ (proc:wait $ledger 10000)))
     (if (fs:exists (nls:socket)) (fs:remove (nls:socket)) True)))
